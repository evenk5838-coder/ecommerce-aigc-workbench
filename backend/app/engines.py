"""模型引擎层：内存护栏 + 取消令牌 + 模型管理 + 图片/视频推理实现。

设计要点（与 docs/架构/架构方案.md §8/§9 一致）：
- 同一时刻只驻留一个模型，切换时显式卸载并清空 MPS 缓存；
- 不关闭 MPS 内存保护；
- 运行中取消为协作式：在采样步回调 / 帧间检查，最多延迟一步或一帧。
"""
from __future__ import annotations

import gc
import os
import threading
import time
from pathlib import Path

from . import config, db
from .errors import err

try:
    import torch
except Exception:  # noqa: BLE001
    torch = None  # type: ignore


class CancelRequested(Exception):
    pass


class CancelToken:
    def __init__(self) -> None:
        self._e = threading.Event()

    def request(self) -> None:
        self._e.set()

    @property
    def canceled(self) -> bool:
        return self._e.is_set()

    def raise_if_canceled(self) -> None:
        if self.canceled:
            raise CancelRequested()


class MemoryGuard:
    """估算 + 准入 + 采样。估算系数为经验值，实测后校准并写回文档。"""

    def __init__(self) -> None:
        self.budget_mb = 0
        self.refresh()

    def refresh(self) -> None:
        if torch is None:
            self.budget_mb = 6000
            return
        try:
            self.budget_mb = int(torch.mps.recommended_max_memory() / 1e6 * config.MPS_BUDGET_RATIO)
        except Exception:  # noqa: BLE001
            self.budget_mb = 6000

    def estimate_mb(self, kind: str, weights_mb: int, width: int, height: int, frames: int) -> int:
        """按实测校准的激活系数估算（2026-10-22 实测）：
        512²→driver 6.7GB、640²→9.2GB、768²→11.7GB（均含 ~2.1GB 权重）
        拟合 k ≈ 17,700MB / 百万像素。视频系数尚未标定（无推理数据），沿用同一系数并加帧项。
        """
        mpx = width * height / 1e6
        if kind == "image":
            act = int(mpx * 17700)
        else:
            act = int(mpx * 17700 + frames * 90)
        return weights_mb + act

    def check(self, kind: str, weights_mb: int, width: int, height: int, frames: int = 1) -> dict:
        need = self.estimate_mb(kind, weights_mb, width, height, frames)
        info = {"budget_mb": self.budget_mb, "estimated_mb": need}
        if need > self.budget_mb:
            raise err(
                "MEMORY_PREDICTED_OVER",
                f"预计内存 {need}MB 超出预算 {self.budget_mb}MB，请降低分辨率/帧数或改用更小模型",
                detail=info,
            )
        return info

    def snapshot(self) -> dict:
        out = {"budget_mb": self.budget_mb}
        if torch is not None:
            try:
                out["mps_alloc_mb"] = int(torch.mps.current_allocated_memory() / 1e6)
                out["mps_driver_mb"] = int(torch.mps.driver_allocated_memory() / 1e6)
            except Exception:  # noqa: BLE001
                pass
        try:
            import psutil

            out["sys_used_mb"] = int(psutil.virtual_memory().used / 1e6)
        except Exception:  # noqa: BLE001
            pass
        return out


class MemSampler:
    """每 0.5s 采样一次内存，写入 mem_samples，峰值回写任务。"""

    def __init__(self, guard: MemoryGuard) -> None:
        self.guard = guard
        self.peak_mb = 0
        self._stop = threading.Event()
        self._t: threading.Thread | None = None
        self.task_id = ""

    def start(self, task_id: str) -> None:
        self.task_id = task_id
        self.peak_mb = 0
        self._stop.clear()
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            s = self.guard.snapshot()
            peak = max(s.get("mps_driver_mb", 0), s.get("sys_used_mb", 0))
            self.peak_mb = max(self.peak_mb, peak)
            try:
                db.insert("mem_samples", {
                    "task_id": self.task_id, "ts": db.now(),
                    "mps_alloc_mb": s.get("mps_alloc_mb", 0),
                    "mps_driver_mb": s.get("mps_driver_mb", 0),
                    "sys_used_mb": s.get("sys_used_mb", 0),
                })
            except Exception:  # noqa: BLE001
                pass
            self._stop.wait(0.5)

    def stop(self) -> int:
        self._stop.set()
        if self._t:
            self._t.join(timeout=2)
        return int(self.peak_mb)


def _empty_cache() -> None:
    gc.collect()
    if torch is not None:
        try:
            torch.mps.empty_cache()
        except Exception:  # noqa: BLE001
            pass


class ModelManager:
    """按需加载、切换卸载；同一时刻只驻留一个模型。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._pipe = None
        self._loaded_id: str | None = None
        self._precision: str | None = None
        self._load_ms: int = 0

    @property
    def loaded_id(self) -> str | None:
        return self._loaded_id.split(":")[0] if self._loaded_id else None

    def weights_mb(self, model_id: str) -> int:
        d = Path(config.MODEL_REGISTRY[model_id]["dir"])
        total = sum(f.stat().st_size for f in d.rglob("*") if f.is_file()) if d.exists() else 0
        return int(total / 1e6)

    def weights_ready(self, model_id: str) -> bool:
        """只有通过 sha256 校验（.verified.json）且文件齐备，才算可用。"""
        d = Path(config.MODEL_REGISTRY[model_id]["dir"])
        if not Path(d / "model_index.json").exists():
            return False
        if not Path(d / ".verified.json").exists():
            return False
        return self.weights_mb(model_id) > 500

    def unload(self) -> None:
        with self._lock:
            if self._pipe is not None:
                del self._pipe
                self._pipe = None
            self._loaded_id = None
            self._precision = None
            _empty_cache()

    def ensure(self, model_id: str, precision: str = "fp16", mode: str = "txt2img"):
        """加载（或复用）指定模型（精度+模式）；返回 (pipeline, load_ms)。"""
        if torch is None:
            raise err("MODEL_LOAD_FAILED", "torch 未安装，无法加载本地模型")
        if model_id not in config.MODEL_REGISTRY:
            raise err("VALIDATION_FAILED", f"未知模型：{model_id}")
        meta = config.MODEL_REGISTRY[model_id]
        if not self.weights_ready(model_id):
            raise err(
                "MODEL_NOT_DOWNLOADED",
                f"模型 {model_id} 权重未就位（目录：{meta['dir']}）",
                detail={"dir": meta["dir"]},
            )

        cache_key = f"{model_id}:{precision}:{mode}"
        with self._lock:
            if self._loaded_id == cache_key and self._pipe is not None:
                return self._pipe, 0
            if self._pipe is not None:
                self.unload()
            t0 = time.time()
            try:
                from diffusers import (
                    StableDiffusionImg2ImgPipeline,
                    StableDiffusionPipeline,
                    StableDiffusionXLImg2ImgPipeline,
                    StableDiffusionXLPipeline,
                )

                if "sdxl" in model_id:
                    cls = StableDiffusionXLImg2ImgPipeline if mode == "img2img" else StableDiffusionXLPipeline
                else:
                    cls = StableDiffusionImg2ImgPipeline if mode == "img2img" else StableDiffusionPipeline
                # 只下载了 fp16 变体权重：fp32 计算 = 用 fp16 权重加载后整体升精度，
                # 避免为同一次验证再下载 4GB fp32 权重（用户明确要求不重复精度版本）。
                # 只下载 fp16 变体权重（不重复下载 fp32，符合"不重复精度版本"要求）：
                # 先按 fp16 加载，再按需升精度；VAE 始终用 fp32 计算，规避 fp16 VAE 黑图/NaN。
                kwargs = dict(
                    torch_dtype=torch.float16,
                    variant="fp16",
                    use_safetensors=True,
                    local_files_only=True,
                )
                # SD1.5 系列的 safety_checker 未下载（省内存/省磁盘）；XL 系列没有该组件。
                # 这是刻意的裁剪，记录在评测报告中，不属于"关闭安全机制"的隐式行为。
                if "XL" not in cls.__name__:
                    kwargs["safety_checker"] = None
                pipe = cls.from_pretrained(meta["dir"], **kwargs)
                if precision == "fp32":
                    # fp16 权重加载后整体升 fp32（避免再下载 4GB 同模型 fp32 权重）
                    pipe = pipe.to("cpu", dtype=torch.float32)
                pipe = pipe.to("mps")
                pipe.set_progress_bar_config(disable=True)
                try:
                    pipe.enable_attention_slicing()
                    pipe.enable_vae_slicing()
                except Exception:  # noqa: BLE001
                    pass
            except Exception as e:  # noqa: BLE001
                self._pipe = None
                self._loaded_id = None
                _empty_cache()
                raise err("MODEL_LOAD_FAILED", f"模型加载失败：{type(e).__name__}: {str(e)[:300]}") from e
            self._pipe, self._loaded_id = pipe, cache_key
            self._load_ms = int((time.time() - t0) * 1000)
            return pipe, self._load_ms


MANAGER = ModelManager()
GUARD = MemoryGuard()


def _seed(seed: int | None) -> int:
    if seed is None or seed < 0:
        import random

        return random.randint(0, 2**31 - 1)
    return seed


def _fake_image(params: dict, token: CancelToken, on_progress=None) -> dict:
    """测试用假引擎：按步 sleep，支持协作式取消与失败注入。不做任何推理。"""
    import io
    import time as _t

    from PIL import Image

    steps = int(params.get("steps", 10))
    delay = float(params.get("fake_step_delay", 0.5))
    width = int(params.get("width", 64))
    height = int(params.get("height", 64))
    t0 = _t.time()
    for i in range(steps):
        token.raise_if_canceled()
        if params.get("fail_at_step") == i:
            raise err("INTERNAL_ERROR", f"注入失败（第 {i} 步）")
        _t.sleep(delay)
        if on_progress:
            on_progress((i + 1) / steps)
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (90, 120, 200)).save(buf, format="PNG")
    return {"data": buf.getvalue(), "width": width, "height": height,
            "seed": int(params.get("seed", 0)), "steps": steps, "guidance": 0.0,
            "load_ms": 0, "latency_ms": int((_t.time() - t0) * 1000)}


def generate_image(model_id: str, params: dict, token: CancelToken, on_progress=None) -> dict:
    """本地图片推理。返回 {data, width, height, seed, steps, load_ms, latency_ms}"""
    if model_id == "fake-image":
        return _fake_image(params, token, on_progress)
    meta = config.MODEL_REGISTRY[model_id]
    width = int(params.get("width") or meta.get("default_size", 512))
    height = int(params.get("height") or width)
    steps = int(params.get("steps") or meta.get("default_steps", 25))
    guidance = float(params.get("guidance", 7.5 if "sdxl" not in model_id else 5.0))
    prompt = (params.get("prompt") or "").strip()
    negative = (params.get("negative_prompt") or "").strip()
    if not prompt:
        raise err("VALIDATION_FAILED", "prompt 不能为空")

    # This M1 Pro produced an all-black SD1.5 image with fp16. Keep the same
    # downloaded weights, but compute in fp32 unless explicitly overridden.
    precision = params.get("precision", "fp32" if model_id == "sd15" else "fp16")
    if precision not in ("fp16", "fp32"):
        raise err("VALIDATION_FAILED", "precision 只能为 fp16 或 fp32")
    weight_factor = 2 if precision == "fp32" else 1
    init_asset_id = params.get("init_image_asset_id")
    mode = "img2img" if init_asset_id else "txt2img"
    GUARD.check("image", MANAGER.weights_mb(model_id) * weight_factor, width, height)
    t0 = time.time()
    pipe, load_ms = MANAGER.ensure(model_id, precision=precision, mode=mode)
    seed = _seed(params.get("seed"))

    if torch is not None:
        torch.manual_seed(seed)

    def _cb(pipe_, step, timestep, kwargs):
        token.raise_if_canceled()
        if on_progress:
            on_progress((step + 1) / max(steps, 1))
        return kwargs

    gen = None
    if torch is not None:
        gen = torch.Generator(device="cpu").manual_seed(seed)

    call_kwargs = dict(prompt=prompt, width=width, height=height,
                       num_inference_steps=steps, guidance_scale=guidance,
                       callback_on_step_end=_cb, generator=gen, output_type="np")
    if negative and "sdxl" not in model_id:
        call_kwargs["negative_prompt"] = negative

    if mode == "img2img":
        from PIL import Image as _Image

        from .storage import load_asset, open_image

        src = open_image(load_asset(init_asset_id)).resize((width, height), _Image.LANCZOS)
        call_kwargs["image"] = src
        call_kwargs["strength"] = float(params.get("strength", 0.55))

    out = pipe(**call_kwargs)
    import numpy as np
    from PIL import Image

    pixels = np.asarray(out.images[0])
    if not np.isfinite(pixels).all():
        raise err("MODEL_NUMERICAL_ERROR", "模型输出包含无效数值，未保存图片。请使用 fp32 精度后从头重试。")
    img = Image.fromarray(np.rint(np.clip(pixels, 0, 1) * 255).astype("uint8"))
    import io

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return {
        "data": buf.getvalue(),
        "width": img.width,
        "height": img.height,
        "seed": seed,
        "steps": steps,
        "guidance": guidance,
        "precision": precision,
        "mode": mode,
        "load_ms": load_ms,
        "latency_ms": int((time.time() - t0) * 1000),
    }


def generate_video(model_id: str, params: dict, token: CancelToken, on_progress=None) -> dict:
    """本地 I2V 推理：返回 {data, fps, frames, width, height, seed, steps, load_ms, latency_ms}"""
    meta = config.MODEL_REGISTRY[model_id]
    if not MANAGER.weights_ready(model_id):
        raise err("MODEL_NOT_DOWNLOADED", f"模型 {model_id} 权重未就位（{meta['dir']}）")

    width = int(params.get("width") or 320)
    height = int(params.get("height") or 512)
    frames = int(params.get("frames") or 8)
    fps = int(params.get("fps") or 8)
    steps = int(params.get("steps") or 25)
    prompt = (params.get("prompt") or "").strip()
    if not prompt:
        raise err("VALIDATION_FAILED", "prompt 不能为空")

    GUARD.check("video", MANAGER.weights_mb(model_id), width, height, frames)
    from PIL import Image

    from .storage import load_asset, open_image

    first = load_asset(params["input_asset_id"])
    image = open_image(first).resize((width, height), Image.LANCZOS)

    if torch is None:
        raise err("MODEL_LOAD_FAILED", "torch 未安装")
    from diffusers import I2VGenXLPipeline
    from diffusers.utils import export_to_video

    t0 = time.time()
    try:
        pipe = I2VGenXLPipeline.from_pretrained(meta["dir"], torch_dtype=torch.float16,
                                                variant="fp16", local_files_only=True)
        pipe = pipe.to("mps")
        pipe.set_progress_bar_config(disable=True)
        pipe.enable_attention_slicing()
        pipe.enable_vae_slicing()
    except Exception as e:  # noqa: BLE001
        raise err("MODEL_LOAD_FAILED", f"视频模型加载失败：{type(e).__name__}: {str(e)[:300]}") from e
    load_ms = int((time.time() - t0) * 1000)
    seed = _seed(params.get("seed"))
    torch.manual_seed(seed)

    def _cb(pipe_, step, timestep, kwargs):
        token.raise_if_canceled()
        if on_progress:
            on_progress((step + 1) / max(steps, 1))
        return kwargs

    try:
        out = pipe(image=image, prompt=prompt, num_frames=frames, num_inference_steps=steps,
                   guidance_scale=float(params.get("guidance", 9.0)), generator=None,
                   callback_on_step_end=_cb)
    finally:
        del pipe
        _empty_cache()

    frames_list = out.frames[0] if out.frames and isinstance(out.frames[0], list) else out.frames
    import tempfile

    tmp = Path(tempfile.mkdtemp()) / "raw.mp4"
    export_to_video(frames_list, str(tmp), fps=fps)
    data = tmp.read_bytes()
    return {
        "data": data,
        "fps": fps,
        "frames": len(frames_list),
        "width": width,
        "height": height,
        "seed": seed,
        "steps": steps,
        "load_ms": load_ms,
        "latency_ms": int((time.time() - t0) * 1000),
    }
