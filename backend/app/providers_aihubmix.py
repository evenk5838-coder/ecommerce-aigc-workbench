"""AiHubMix native media protocol. No paid POST retry or arbitrary URL proxy.

Only the reviewed models in aihubmix_models are executable. All remote IDs
must be persisted before polling, including image jobs. HTTP responses and
transport exceptions are not echoed: they can contain credentials or data URIs.
"""
from __future__ import annotations

import base64
import binascii
import json
import math
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from . import aihubmix_audio, aihubmix_models, config, db, media_policy, storage
from .errors import AppError, err

POLL_SECONDS = 15
MAX_POLL_SECONDS = 1800
ID_PATTERN = r"[A-Za-z0-9_-]{1,200}"
STATES = {"pending": "queued", "in_progress": "running", "completed": "succeeded",
          "failed": "failed", "cancelled": "canceled"}
USAGE_FIELDS = {"images", "input_tokens", "output_tokens", "total_tokens", "prompt_tokens",
                "completion_tokens", "input_image_tokens", "output_image_tokens", "cached_tokens"}


def _numeric_usage(value) -> dict:
    if not isinstance(value, dict):
        return {}
    return {k: v for k, v in value.items() if k in USAGE_FIELDS and type(v) in (int, float) and math.isfinite(v) and v >= 0}


def configured() -> bool:
    config._load_dotenv()
    return bool(os.getenv("AIHUBMIX_API_KEY", "").strip())


def api_key() -> str:
    if not configured():
        raise err("AUTH_MISSING", "请在本机 .env 配置 AIHUBMIX_API_KEY")
    return os.environ["AIHUBMIX_API_KEY"].strip()


def _base_url() -> str:
    # This release supports the official gateway only; no user-controlled proxy.
    url = os.getenv("AIHUBMIX_BASE_URL", "https://aihubmix.com").rstrip("/")
    if url != "https://aihubmix.com":
        raise err("PROVIDER_BAD_REQUEST", "AiHubMix 地址应为 https://aihubmix.com（不含 /v1）")
    return url


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward the API key to another location.


def _request(method: str, path: str, body: dict | None = None, *, binary: bool = False):
    try:
        timeout = float(os.getenv("AIHUBMIX_TIMEOUT", "120"))
        if not 1 <= timeout <= 300:
            raise ValueError("timeout")
    except ValueError:
        raise err("PROVIDER_BAD_REQUEST", "AIHUBMIX_TIMEOUT 应在 1 到 300 秒之间") from None
    req = urllib.request.Request(_base_url() + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + api_key(), "Content-Type": "application/json"})
    try:
        with urllib.request.build_opener(_NoRedirect()).open(req, timeout=timeout) as response:
            raw = response.read()
        if binary:
            return raw
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("not an object")
        return result
    except urllib.error.HTTPError as exc:
        # Only classify a known code; neither body, headers nor exception text
        # enter an AppError, persisted task, trace, or frontend response.
        code = ""
        try:
            payload = json.loads(exc.read())
            value = payload.get("error")
            if isinstance(value, dict) and isinstance(value.get("code"), str):
                code = value["code"].lower()
        except (ValueError, AttributeError):
            pass
        if code == "async_not_enabled":
            raise err("PROVIDER_ASYNC_NOT_ENABLED", "请先在 AiHubMix 控制台开启异步任务功能") from None
        if exc.code == 402 or code in ("insufficient_quota", "insufficient_balance"):
            raise err("PROVIDER_INSUFFICIENT_BALANCE", "AiHubMix 余额或密钥额度不足，请在控制台核对") from None
        if exc.code in (401, 403):
            raise err("AUTH_FAILED", "AiHubMix 密钥无效或没有该模型权限") from None
        if exc.code == 404:
            raise err("MODEL_NOT_OPENED", "AiHubMix 模型或任务不可访问，请核对权限和型号") from None
        if exc.code == 429:
            raise err("PROVIDER_RATE_LIMITED", "AiHubMix 请求限流，请稍后再试") from None
        if exc.code in (400, 422):
            raise err("PROVIDER_BAD_REQUEST", "AiHubMix 不接受当前生成参数，请调整设置") from None
        raise err("PROVIDER_UNAVAILABLE", "AiHubMix 服务暂不可用") from None
    except (urllib.error.URLError, OSError, TimeoutError):
        raise err("PROVIDER_UNAVAILABLE", "AiHubMix 网络请求未完成") from None
    except (ValueError, UnicodeError):
        raise err("PROVIDER_UNAVAILABLE", "AiHubMix 返回格式无法识别") from None


def list_models() -> list[str]:
    """Read-only token-scoped legacy directory, not generation verification."""
    data = _request("GET", "/v1/models").get("data")
    if not isinstance(data, list):
        raise err("PROVIDER_UNAVAILABLE", "AiHubMix 模型目录格式无法识别")
    return sorted({m["id"] for m in data if isinstance(m, dict) and isinstance(m.get("id"), str)})


def _collection(kind: str) -> str:
    if kind not in ("image", "video"):
        raise err("VALIDATION_FAILED", "不支持的媒体类型")
    return "/ai/v1/" + ("images" if kind == "image" else "videos")


def _task_path(kind: str, remote_id: str) -> str:
    if not isinstance(remote_id, str) or not re.fullmatch(ID_PATTERN, remote_id):
        raise err("PROVIDER_BAD_REQUEST", "AiHubMix 任务编号格式无法识别")
    return _collection(kind) + "/" + remote_id


def build_body(kind: str, model_id: str, params: dict, reference: str | None = None,
               *, preparation: dict | None = None) -> dict:
    if model_id not in aihubmix_models.MODELS or aihubmix_models.MODELS[model_id]["kind"] != kind:
        raise err("MODEL_CAPABILITY_MISMATCH", "该 AiHubMix 模型尚未适配")
    params = aihubmix_models.effective_candidate_params(model_id, kind, params)
    prompt = params.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise err("VALIDATION_FAILED", "请填写生成提示词")
    if config.validate_params_for_model(model_id, kind, params):
        raise err("MODEL_CAPABILITY_MISMATCH", "模型不支持所选生成参数")
    mode = params.get("generation_mode", "text_to_" + kind)
    if mode.startswith("image_") and not reference:
        raise err("REFERENCE_IMAGE_REQUIRED", "请提供参考图片")
    body = {"model": config.MODEL_REGISTRY[model_id]["upstream_model_id"], "prompt": prompt.strip()}
    if kind == "image":
        body.update(aihubmix_models.image_body_options(model_id, params))
        if mode == "image_to_image":
            body["image"] = reference
    else:
        body["prompt"] = media_policy.video_prompt(body["prompt"])
        extension = aihubmix_models.EXTENSIONS.get(model_id)
        if extension is not None:
            prepared = None
            if mode == "image_to_video":
                prepared, metadata = media_policy.prepare_reference(reference, params.get("ratio", "16:9"))
                if preparation is not None:
                    preparation.update(metadata)
            body.update(extension.body_options(model_id, params, prepared))
        else:
            body.update(duration=config.resolve_duration_s(model_id, params.get("duration"), strict=True, legacy=True),
                        aspect_ratio=params.get("ratio", "9:16"), resolution=params.get("resolution", "720p"),
                        generate_audio=params.get("generate_audio", False),
                        extra={"watermark": False})
            if params.get("seed") is not None and params["seed"] >= 0:
                raise err("MODEL_CAPABILITY_MISMATCH", "当前视频通道未接入随机种子选项")
            if mode == "image_to_video":
                prepared, metadata = media_policy.prepare_reference(reference, body["aspect_ratio"])
                if preparation is not None:
                    preparation.update(metadata)
                body["frame_images"] = [{"frame_type": "first_frame", "image_url": {"url": prepared}}]
    return body


def _require_ready(model_id: str) -> None:
    ready = config.model_submittability(model_id)
    if not ready["submittable"]:
        messages = {"MODEL_NOT_VERIFIED": "该模型尚未启用，暂不可生成",
                    "MODEL_PRICE_UNCONFIRMED": "该模型费用配置尚未就绪，暂不可生成",
                    "AUTH_MISSING": "尚未配置 AiHubMix 密钥",
                    "MODEL_EOS": "该模型已下线，暂不可生成"}
        code = next((code for code in messages if code in ready["reasons"]), "MODEL_NOT_VERIFIED")
        raise err(code, messages[code])


def output_policy(kind: str, model_id: str) -> dict:
    result = media_policy.specification(kind)
    audio = aihubmix_audio.policy(model_id) if kind == "video" else None
    if audio is not None:
        result["audio_processing"] = audio
    return result


def media_create(kind: str, model_id: str, params: dict, reference: str | None = None) -> dict:
    preparation = {}
    body = build_body(kind, model_id, params, reference, preparation=preparation)
    # Offline previews do not need activation; paid creates recheck it here.
    _require_ready(model_id)
    path = _collection(kind) + ("/generations" if kind == "image" else "")
    try:
        data = _request("POST", path, body)
    except AppError as exc:
        if exc.code == "PROVIDER_UNAVAILABLE":
            raise err("CREATE_RESULT_UNKNOWN", "生成请求结果未知，可能已计费；请核对 AiHubMix 任务记录，不会自动重发") from None
        raise
    rid = data.get("id")
    if not isinstance(rid, str) or not re.fullmatch(ID_PATTERN, rid):
        raise err("CREATE_RESULT_UNKNOWN", "未收到有效的生成任务编号，请核对 AiHubMix 任务记录，不会自动重发")
    return {"remote_task_id": rid, "output_policy": output_policy(kind, model_id),
            "prepared_reference": preparation or None}


def media_query(kind: str, remote_id: str) -> dict:
    data = _request("GET", _task_path(kind, remote_id))
    if data.get("id") != remote_id or data.get("object") != kind or data.get("status") not in STATES:
        raise err("PROVIDER_UNAVAILABLE", "AiHubMix 任务状态无法识别，原任务编号已保留")
    usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
    return {"status": STATES[data["status"]], "output": data.get("output"), "usage": _numeric_usage(usage)}


def download_result(kind: str, remote_id: str, output) -> bytes:
    if not isinstance(output, list) or len(output) != 1 or not isinstance(output[0], dict):
        raise err("DOWNLOAD_FAILED", "AiHubMix 未返回单个可下载结果，原任务编号已保留")
    item = output[0]
    if kind == "image" and item.get("b64_json"):
        try:
            return base64.b64decode(item["b64_json"], validate=True)
        except (ValueError, TypeError, binascii.Error):
            raise err("DOWNLOAD_FAILED", "AiHubMix 图片数据无法识别") from None
    # Result URLs must be the authenticated gateway proxy for THIS task. No
    # token is ever sent to external URLs or redirects returned by the model.
    value = item.get("content_url")
    base = _task_path(kind, remote_id) + "/content"
    if not isinstance(value, str):
        raise err("DOWNLOAD_FAILED", "AiHubMix 未返回下载地址")
    url = urllib.parse.urlsplit(value)
    if url.scheme != "https" or url.netloc != "aihubmix.com" or url.query or url.fragment:
        raise err("DOWNLOAD_FAILED", "AiHubMix 下载地址无法校验")
    pattern = re.escape(base) + ("/" + ID_PATTERN if kind == "image" else "")
    if not re.fullmatch(pattern, url.path):
        raise err("DOWNLOAD_FAILED", "AiHubMix 下载结果与当前任务不匹配")
    return _request("GET", url.path, binary=True)


def run_media(task_id: str, model_id: str, params: dict, token, on_progress=None) -> dict:
    """Shared durable image/video lifecycle; an existing ID is only queried."""
    from . import cloud_tasks, node_execution
    kind = config.MODEL_REGISTRY[model_id]["kind"]
    task = db.get("tasks", task_id) or {}
    remote_id = task.get("remote_task_id")
    started = time.monotonic()
    local_usage = db.loads(task.get("usage_json")) or {}
    try:
        with db.conn() as connection:
            cost = node_execution._reserved_cost_in_tx(connection, task)
    except AppError as exc:
        if not remote_id or exc.code != "BUDGET_RESERVATION_UNAVAILABLE":
            raise
        # Fetching an already-created result cannot create a second charge.
        # Preserve unknown local cost instead of inventing zero or repricing it
        # later using a newly configured exchange bound.
        cost = None
        if not isinstance(local_usage, dict):
            local_usage = {}
        local_usage.update(cost_basis="unknown", cost_pending_verification=True)
        db.update("tasks", task_id, {"usage_json": db.dumps(local_usage)})
    if not remote_id:
        _require_ready(model_id)
        if cloud_tasks.estimate_cost(kind, model_id, params, legacy=True) != cost:
            raise err("NODE_QUOTE_STALE", "生成费用发生变化，请重新报价后生成")
        if "cost_reservation" not in local_usage:
            local_usage["cost_reservation"] = node_execution.freeze_task_cost_reservation(task, cost)
            db.update("tasks", task_id, {"usage_json": db.dumps(local_usage)})
        with db.conn() as connection:
            # The current queued/running task is included once, together with
            # all other outstanding charges, including unknown legacy costs.
            node_execution._budget_in_tx(connection, 0)
        reference = None
        if params.get("generation_mode", "").startswith("image_"):
            reference = storage.asset_data_url(storage.load_asset(params.get("input_asset_id")))
        token.raise_if_canceled()
        created = media_create(kind, model_id, params, reference)
        remote_id = created["remote_task_id"]
        local_usage = {**local_usage, "output_policy": created.get("output_policy"),
                       "prepared_reference": created.get("prepared_reference")}
        db.update("tasks", task_id, {"remote_task_id": remote_id, "remote_status": "created",
                                     "provider": "aihubmix", "stage": "remote_queued",
                                     "usage_json": db.dumps(local_usage)})
    while time.monotonic() - started <= MAX_POLL_SECONDS:
        token.raise_if_canceled()
        try:
            state = media_query(kind, remote_id)
        except AppError as exc:
            if exc.code not in ("PROVIDER_RATE_LIMITED", "PROVIDER_UNAVAILABLE"):
                raise
            time.sleep(POLL_SECONDS)
            continue
        status = state["status"]
        db.update("tasks", task_id, {"remote_status": status, "stage": "remote_" + status})
        if on_progress:
            on_progress(0.8 if status == "succeeded" else 0.3)
        if status == "succeeded":
            data = download_result(kind, remote_id, state["output"])
            try:
                data, audio = aihubmix_audio.apply(model_id, params, remote_id, data)
            except AppError as exc:
                if isinstance(exc.detail.get("audio_processing"), dict):
                    local_usage["audio_processing"] = exc.detail["audio_processing"]
                    db.update("tasks", task_id, {"usage_json": db.dumps(local_usage)})
                raise
            if audio is not None:
                local_usage["audio_processing"] = audio
                db.update("tasks", task_id, {"usage_json": db.dumps(local_usage)})
            return {"data": data, "kind": kind, "usage": {**_numeric_usage(state.get("usage")), **local_usage,
                    "cost_basis": "unknown" if cost is None else "estimate",
                    "provider": "aihubmix"}, "cost_cny": cost, "remote_task_id": remote_id,
                    "load_ms": 0, "latency_ms": int((time.monotonic() - started) * 1000)}
        if status in ("failed", "canceled"):
            raise err("PROVIDER_BAD_REQUEST", "AiHubMix 生成任务失败或已取消，请在控制台核对原因")
        time.sleep(POLL_SECONDS)
    raise err("TIMEOUT_LOCAL_POLL", "等待超时，AiHubMix 任务编号已保留，请核对原任务后继续")
