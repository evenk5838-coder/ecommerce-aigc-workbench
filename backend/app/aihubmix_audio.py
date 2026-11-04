"""H3 video-only delivery, with an immutable native original and no generation.

H3 has no native audio switch. When the workbench requests a silent result we
remove audio streams locally, copy the video stream, and verify decoded frames.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile

from . import config, storage
from .errors import AppError, err

VERSION = "h3-video-only-v1"
H3 = "aihubmix:minimax-h3"
_ID = re.compile(r"[A-Za-z0-9_-]{1,200}")


def policy(model_id: str) -> dict | None:
    if model_id != H3:
        return None
    return {"version": VERSION, "silent_delivery": "local_video_stream_copy",
            "preserve_native_original": True, "native_audio_switch": False,
            "reencode_video": False}


def _save_original(remote_id: str, data: bytes) -> tuple[Path, dict, bool]:
    digest = hashlib.sha256(data).hexdigest()
    valid_id = isinstance(remote_id, str) and _ID.fullmatch(remote_id) is not None
    # Even an invalid ID cannot discard an already downloaded paid result. Its
    # quarantine name contains only a hash, never the untrusted identifier.
    safe_id = remote_id if valid_id else "invalid-id-" + hashlib.sha256(str(remote_id).encode()).hexdigest()[:32]
    if len(safe_id) > 150:
        safe_id = safe_id[:110] + "-" + hashlib.sha256(safe_id.encode()).hexdigest()[:32]
    root = Path(config.DATA).resolve()
    directory = root / "provider-originals" / "aihubmix"
    for component in (root / "provider-originals", directory):
        if component.is_symlink():
            raise ValueError("unsafe originals directory")
        component.mkdir(exist_ok=True)
    destination = directory / f"{safe_id}-{digest}.mp4"
    staged = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".native-", delete=False) as file:
            staged = Path(file.name)
            file.write(data)
            file.flush()
            os.fsync(file.fileno())
        try:
            # Atomic, exclusive publication: never replace an existing file.
            os.link(staged, destination)
        except FileExistsError:
            if destination.is_symlink() or not destination.is_file() or hashlib.sha256(destination.read_bytes()).hexdigest() != digest:
                raise ValueError("original collision") from None
    finally:
        if staged is not None:
            staged.unlink(missing_ok=True)
    return destination, {"version": VERSION,
                         "native_original_relpath": destination.relative_to(root).as_posix(),
                         "native_original_sha256": digest}, valid_id


def _inspect(path: Path) -> dict:
    """Decode the complete video and summarize frame hashes and input streams."""
    result = subprocess.run([storage.ffmpeg_exe(), "-nostdin", "-hide_banner", "-v", "info",
        "-protocol_whitelist", "file,pipe", "-i", str(path), "-map", "0:v:0", "-an",
        "-vsync", "0", "-f", "framemd5", "-"], capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise ValueError("video decode failed")
    dimensions = re.search(r"^#dimensions\s+0:\s*(\d+)x(\d+)\s*$", result.stdout, re.MULTILINE)
    hashes = []
    for line in result.stdout.splitlines():
        if line and not line.startswith("#"):
            fields = [field.strip() for field in line.split(",")]
            if len(fields) != 6 or not re.fullmatch(r"[0-9a-f]{32}", fields[-1]):
                raise ValueError("frame verification failed")
            hashes.append(fields[-1])
    if dimensions is None or not hashes:
        raise ValueError("missing video frames")
    streams = result.stderr.split("Stream mapping:", 1)[0]
    info = {"width": int(dimensions[1]), "height": int(dimensions[2]),
            "frames": len(hashes), "decoded_video_sha256": hashlib.sha256("\n".join(hashes).encode()).hexdigest(),
            "has_audio": bool(re.search(r"Stream #\S+.*Audio:", streams))}
    duration = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", streams)
    if duration:
        info["duration_ms"] = round((int(duration[1]) * 3600 + int(duration[2]) * 60 + float(duration[3])) * 1000)
    fps = re.search(r"Video:[^\n]*?\b(\d+(?:\.\d+)?) fps\b", streams)
    if fps:
        info["fps"] = float(fps[1])
    return info


def apply(model_id: str, params: dict, remote_id: str, data: bytes) -> tuple[bytes, dict | None]:
    """Return delivered bytes and safe provenance; never call a model or retry."""
    if model_id != H3 or params.get("generate_audio", False) is not False:
        return data, None
    metadata = {"version": VERSION}
    try:
        if not isinstance(data, bytes) or not data:
            raise ValueError("empty video")
        original, retained, valid_id = _save_original(remote_id, data)
        metadata.update(retained)
        if not valid_id:
            raise ValueError("invalid task identifier")
        native = _inspect(original)
        metadata.update(native_has_audio=native["has_audio"], native_video=native)
        if native["has_audio"]:
            with tempfile.TemporaryDirectory(dir=original.parent, prefix=".silent-") as temp:
                output = Path(temp) / "delivered.mp4"
                result = subprocess.run([storage.ffmpeg_exe(), "-nostdin", "-v", "error",
                    "-protocol_whitelist", "file,pipe", "-i", str(original), "-map", "0:v:0",
                    "-c:v", "copy", "-an", "-movflags", "+faststart", str(output)],
                    capture_output=True, timeout=120)
                if result.returncode or not output.is_file():
                    raise ValueError("silent remux failed")
                delivered = _inspect(output)
                delivered_data = output.read_bytes()
        else:
            delivered, delivered_data = dict(native), data
        if delivered["has_audio"] or any(delivered[key] != native[key]
                for key in ("width", "height", "frames", "decoded_video_sha256")):
            raise ValueError("delivered video differs")
        metadata.update(delivered_has_audio=False, delivered_video=delivered,
                        local_remux=native["has_audio"], video_frames_unchanged=True,
                        delivered_sha256=hashlib.sha256(delivered_data).hexdigest())
        return delivered_data, metadata
    except AppError:
        raise
    except Exception:
        # FFmpeg stderr and native filenames can contain private data. Retain
        # only relative, hashed provenance; the same remote task is resumable.
        raise err("MEDIA_ENCODE_FAILED", "视频静音交付处理失败，原任务编号已保留，可继续读取同一结果",
                  detail={"audio_processing": {**metadata, "delivery_verified": False}}) from None
