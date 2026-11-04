"""火山方舟（Ark）云端推理适配层。

约束（与 PRD v0.5 一致）：
- Key 只从环境变量 / .env 读取，不落库、不进日志、不进前端；
- 创建视频任务后必须持久化 remote_task_id（由 worker 负责写库）；
- 创建请求超时不得盲目重发（返回 CREATE_UNKNOWN，由调用方决定对账）；
- 取消只对「排队中」任务有效，运行中无官方取消能力；
- 结果必须在 24 小时有效期内下载到本地素材库。
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

from .errors import err
from . import media_policy

BASE_URL = os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
TIMEOUT = float(os.getenv("ARK_TIMEOUT", "120"))


def _load_dotenv() -> None:
    """极简 .env 读取（不覆盖已存在的环境变量）。"""
    root = Path(__file__).resolve().parents[2]
    p = root / ".env"
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def api_key() -> str:
    _load_dotenv()
    key = os.getenv("ARK_API_KEY", "").strip()
    if not key:
        raise err("AUTH_MISSING", "未配置 ARK_API_KEY：请写入本机 .env（不要发到对话里）")
    return key


def configured() -> bool:
    _load_dotenv()
    return bool(os.getenv("ARK_API_KEY", "").strip())


def _request(method: str, path: str, body: dict | None = None, timeout: float | None = None) -> dict:
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key()}",
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout or TIMEOUT) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "ignore")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"error": {"message": raw[:300]}}
        code = ((payload.get("error") or {}).get("code") or "")
        msg = ((payload.get("error") or {}).get("message") or raw[:300])
        rid = (payload.get("error") or {}).get("request_id") or e.headers.get("X-Request-Id")
        if e.code in (401, 403):
            raise err("AUTH_FAILED", f"鉴权失败：{msg}", detail={"request_id": rid}) from e
        if e.code == 429:
            raise err("PROVIDER_RATE_LIMITED", f"云端限流：{msg}", detail={"request_id": rid}) from e
        if "SensitiveContent" in code or "SensitiveContent" in msg:
            raise err("CONTENT_REJECTED", f"内容审核未通过：{msg}", detail={"request_id": rid}) from e
        if e.code == 404 or "ModelNotOpen" in code:
            raise err("MODEL_NOT_OPENED",
                      f"账号未开通该模型：{msg[:160]}",
                      detail={"code": code, "request_id": rid}) from e
        if "InvalidParameter" in code or e.code == 400:
            raise err("PROVIDER_BAD_REQUEST", f"参数不合法：{msg}", detail={"code": code, "request_id": rid}) from e
        raise err("PROVIDER_UNAVAILABLE", f"云端错误 {e.code}：{msg}", detail={"code": code, "request_id": rid}) from e
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        # 网络类失败：可能是"请求已到达但响应丢失"，视频创建场景由调用方按 CREATE_UNKNOWN 处理
        raise err("PROVIDER_UNAVAILABLE", f"网络异常：{type(e).__name__}: {str(e)[:200]}") from e
    except json.JSONDecodeError as e:
        raise err("PROVIDER_UNAVAILABLE", "云端返回非 JSON") from e


# ---------------- 图片（Seedream，同步返回） ----------------
def image_generate(model_id: str, prompt: str, size: str = "1024x1024",
                   watermark: bool = False, response_format: str = "b64_json",
                   image: str | None = None, timeout: float | None = None) -> dict:
    media_policy.require_no_watermark(watermark)
    media_policy.require_safe_prompt(prompt)
    body: dict = {"model": model_id, "prompt": prompt, "size": size,
                  "response_format": response_format, "watermark": watermark}
    if image:
        body["image"] = image
    t0 = time.time()
    data = _request("POST", "/images/generations", body, timeout=timeout)
    items = data.get("data") or []
    if not items:
        raise err("PROVIDER_BAD_REQUEST", "云端未返回图片数据", detail={"raw_keys": list(data)})
    first = items[0]
    b64 = first.get("b64_json")
    url = first.get("url")
    return {"b64": b64, "url": url, "usage": data.get("usage") or {},
            "request_id": data.get("request_id"), "latency_ms": int((time.time() - t0) * 1000),
            "raw_meta": {"model": data.get("model"), "created": data.get("created")}}


# ---------------- 视频（Seedance，异步任务） ----------------
def video_create(model_id: str, content: list[dict], *, duration: int | None = None,
                 ratio: str | None = None, resolution: str | None = None,
                 generate_audio: bool = False, watermark: bool = False,
                 seed: int | None = None, safety_identifier: str | None = None,
                 output_format: str = "mp4", timeout: float | None = None) -> dict:
    media_policy.require_no_watermark(watermark)
    for item in content:
        if item.get("type") == "text":
            media_policy.require_safe_prompt(item.get("text", ""))
    body: dict = {"model": model_id, "content": content}
    if duration is not None:
        body["duration"] = duration
    if ratio:
        body["ratio"] = ratio
    if resolution:
        body["resolution"] = resolution
    body["generate_audio"] = generate_audio
    body["watermark"] = watermark
    if seed is not None and seed >= 0:
        body["seed"] = seed
    if safety_identifier:
        body["safety_identifier"] = safety_identifier
    body["output_format"] = output_format
    t0 = time.time()
    try:
        data = _request("POST", "/contents/generations/tasks", body, timeout=timeout)
    except Exception as e:  # noqa: BLE001
        # 超时/网络中断：请求可能已创建 → 绝不能自动重发
        if getattr(e, "code", "") in ("PROVIDER_UNAVAILABLE",):
            raise err("CREATE_RESULT_UNKNOWN",
                      "创建请求结果未知（可能已创建并计费）；不会自动重发，请用任务记录核对",
                      detail={"elapsed_ms": int((time.time() - t0) * 1000)}) from e
        raise
    tid = data.get("id")
    if not tid:
        raise err("CREATE_RESULT_UNKNOWN", "云端未返回任务 ID", detail={"raw": str(data)[:300]})
    return {"remote_task_id": tid, "latency_ms": int((time.time() - t0) * 1000), "raw": data}


def video_query(remote_task_id: str, timeout: float | None = None) -> dict:
    data = _request("GET", f"/contents/generations/tasks/{remote_task_id}", timeout=timeout)
    content = data.get("content") or {}
    return {
        "remote_task_id": data.get("id", remote_task_id),
        "status": data.get("status"),
        "video_url": content.get("video_url"),
        "error": data.get("error"),
        "usage": data.get("usage") or {},
        "duration": data.get("duration"),
        "ratio": data.get("ratio"),
        "resolution": data.get("resolution"),
        "fps": data.get("framespersecond"),
        "seed": data.get("seed"),
        "raw": data,
    }


def video_cancel(remote_task_id: str) -> dict:
    """官方 DELETE：仅能取消「排队中」任务，或删除任务记录。运行中任务无取消能力。"""
    t0 = time.time()
    _request("DELETE", f"/contents/generations/tasks/{remote_task_id}", timeout=30)
    return {"ok": True, "latency_ms": int((time.time() - t0) * 1000)}


def download(url: str, timeout: float = 300) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "ecom-workbench"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except Exception as e:  # noqa: BLE001
        raise err("DOWNLOAD_FAILED", f"结果下载失败：{type(e).__name__}: {str(e)[:200]}") from e
