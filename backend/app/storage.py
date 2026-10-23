"""本地内容寻址存储 + 上传校验 + 媒体探测/编码。"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

from . import config, db
from .errors import err

MAGIC = {
    b"\xff\xd8\xff": ("image/jpeg", "jpg"),
    b"\x89PNG\r\n\x1a\n": ("image/png", "png"),
    b"RIFF": ("image/webp", "webp"),
    b"GIF8": ("image/gif", "gif"),
}


def sniff(data: bytes) -> tuple[str, str]:
    for sig, (mime, ext) in MAGIC.items():
        if data.startswith(sig):
            return mime, ext
    raise err("UPLOAD_REJECTED", "不支持的文件类型（仅接受 JPG/PNG/WebP/GIF）")


def _rel(sha: str, ext: str) -> Path:
    return Path(sha[:2]) / sha[2:4] / f"{sha}.{ext}"


def save_bytes(data: bytes, kind: str, source: str, meta: dict | None = None) -> dict:
    """写入内容寻址文件并登记 assets；同内容不重复写。"""
    sha = hashlib.sha256(data).hexdigest()
    if kind == "image":
        mime, ext = sniff(data)
    else:
        mime, ext = "video/mp4", "mp4"
    rel = _rel(sha, ext)
    path = config.ASSETS / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(data)

    existing = db.query("SELECT * FROM assets WHERE sha256=?", (sha,))
    if existing:
        return existing[0]

    width = height = duration_ms = fps = frames = None
    meta = dict(meta or {})
    if kind == "image":
        with Image.open(io.BytesIO(data)) as im:
            width, height = im.size
            meta.setdefault("mode", im.mode)
    else:
        info = probe_video(path)
        width, height = info.get("width"), info.get("height")
        duration_ms, fps, frames = info.get("duration_ms"), info.get("fps"), info.get("frames")
        meta.update({k: v for k, v in info.items() if k not in meta})

    asset = {
        "id": db.new_id("asset"),
        "sha256": sha,
        "kind": kind,
        "mime": mime,
        "size": len(data),
        "width": width,
        "height": height,
        "duration_ms": duration_ms,
        "fps": fps,
        "frames": frames,
        "rel_path": str(rel),
        "source": source,
        "meta_json": db.dumps(meta),
        "created_at": db.now(),
        "owner": "local",
    }
    db.insert("assets", asset)
    return asset


def asset_path(asset: dict) -> Path:
    return config.ASSETS / asset["rel_path"]


def load_asset(asset_id: str) -> dict:
    a = db.get("assets", asset_id)
    if not a:
        raise err("ASSET_NOT_FOUND", f"素材不存在：{asset_id}")
    return a


MAX_REF_BYTES = 10 * 1024 * 1024   # 参考图经 base64 后体积上限保护


def validate_image_asset(asset: dict) -> None:
    """参考图必须是可解码的图片，且大小在限内；否则明确报错。"""
    if asset.get("kind") != "image":
        raise err("ASSET_NOT_IMAGE", f"参考素材不是图片（kind={asset.get('kind')}）",
                  detail={"asset_id": asset.get("id"), "kind": asset.get("kind")})
    path = asset_path(asset)
    if not path.exists():
        raise err("INVALID_REFERENCE_IMAGE", "参考素材文件缺失", detail={"asset_id": asset.get("id")})
    size = path.stat().st_size
    if size == 0 or size > MAX_REF_BYTES:
        raise err("INVALID_REFERENCE_IMAGE",
                  f"参考图体积不合法（{size} 字节，上限 {MAX_REF_BYTES}）",
                  detail={"asset_id": asset.get("id"), "bytes": size, "limit": MAX_REF_BYTES})
    try:
        with Image.open(path) as im:
            im.verify()
    except Exception as e:  # noqa: BLE001
        raise err("INVALID_REFERENCE_IMAGE", f"参考图无法解码：{type(e).__name__}",
                  detail={"asset_id": asset.get("id")}) from e


def asset_data_url(asset: dict) -> str:
    """按素材真实 MIME 生成 data URL（不把图片一律当成 PNG）。"""
    validate_image_asset(asset)
    mime = asset.get("mime") or "application/octet-stream"
    if not mime.startswith("image/"):
        raise err("ASSET_NOT_IMAGE", f"参考素材 MIME 非法：{mime}", detail={"asset_id": asset.get("id")})
    data = asset_path(asset).read_bytes()
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def open_image(asset: dict) -> Image.Image:
    return Image.open(asset_path(asset)).convert("RGB")


def encode_video(frames: list[Image.Image], fps: int) -> bytes:
    """把帧序列编码为 H.264 MP4（imageio-ffmpeg 自带二进制，不依赖系统 ffmpeg）。"""
    import numpy as np
    import imageio.v2 as imageio

    tmpdir = tempfile.mkdtemp(prefix="vid_")
    try:
        out = os.path.join(tmpdir, "out.mp4")
        writer = imageio.get_writer(out, fps=fps, codec="libx264", quality=8,
                                    macro_block_size=1, ffmpeg_log_level="error")
        try:
            for f in frames:
                writer.append_data(np.asarray(f.convert("RGB")))
        finally:
            writer.close()
        return Path(out).read_bytes()
    except Exception as e:  # noqa: BLE001
        raise err("MEDIA_ENCODE_FAILED", f"视频编码失败：{type(e).__name__}: {e}") from e
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def ffmpeg_exe() -> str:
    import imageio_ffmpeg

    return imageio_ffmpeg.get_ffmpeg_exe()


def probe_video(path: Path) -> dict:
    """用 imageio-ffmpeg 自带的 ffmpeg 读元信息（无系统 ffprobe 时的替代）。"""
    exe = ffmpeg_exe()
    try:
        p = subprocess.run([exe, "-i", str(path)], capture_output=True, text=True, timeout=30)
        text = p.stderr or ""
    except Exception:  # noqa: BLE001
        return {}
    info: dict = {}
    for line in text.splitlines():
        if "Stream #" in line and "Video:" in line:
            parts = line.split(",")
            for seg in parts:
                seg = seg.strip()
                if "x" in seg and seg.split("x")[0].isdigit():
                    w, _, h = seg.partition("x")
                    if h.isdigit():
                        info["width"], info["height"] = int(w), int(h)
                if seg.endswith("fps"):
                    try:
                        info["fps"] = round(float(seg.replace("fps", "").strip()))
                    except ValueError:
                        pass
        if "Duration:" in line:
            seg = line.split("Duration:")[1].split(",")[0].strip()
            try:
                h, m, s = seg.split(":")
                info["duration_ms"] = int((int(h) * 3600 + int(m) * 60 + float(s)) * 1000)
            except ValueError:
                pass
    return info
