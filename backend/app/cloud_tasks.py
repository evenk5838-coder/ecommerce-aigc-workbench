"""云端任务执行：预算闸门 + 图片同步调用 + 视频异步任务（持久化 remote_task_id / 不盲目重发 / 官方取消语义）。"""
from __future__ import annotations

import base64
import json
import time

from . import config, db, providers_ark as ark, storage
from .errors import err


# ---------- 预算 ----------
def estimate_cost(kind: str, model_id: str, params: dict) -> float:
    """单次调用估算成本。

    图片：像素档/单一价（官方价格页 2026-10-23 核对）。
    视频：官方 token 公式 × 单价（已用本项目实测 token 反算验证）。
    注意：这是**估算**，不是账单；视频成功后按 usage.completion_tokens 重算。
    """
    if kind == "image":
        mode = (params.get("generation_mode") or "text_to_image").strip()
        refs = 1 if (mode == "image_to_image" and params.get("input_asset_id")) else 0
        return config.image_cost_cny(model_id, params.get("size"), reference_images=refs)
    return config.video_cost_cny(model_id, params)


def spent_today() -> float:
    rows = db.query(
        "SELECT COALESCE(SUM(cost_cny),0) c FROM tasks WHERE finished_at > ? AND cost_cny IS NOT NULL",
        (time.time() - (time.time() % 86400),),
    )
    return float(rows[0]["c"] or 0)


def budget_gate(cost: float) -> None:
    if config.BUDGET_CNY <= 0:
        raise err("BUDGET_NOT_APPROVED",
                  "尚未批准云端预算：请在 .env 设置 APP_BUDGET_CNY（元）后重启服务",
                  detail={"estimated_cost_cny": cost})
    if spent_today() + cost > config.BUDGET_CNY:
        raise err("BUDGET_EXCEEDED",
                  f"超出当日预算：已用 {spent_today():.2f} 元 + 本次 {cost:.2f} 元 > 上限 {config.BUDGET_CNY:.2f} 元",
                  detail={"spent_cny": spent_today(), "estimated_cost_cny": cost, "limit_cny": config.BUDGET_CNY})


# ---------- 图片（同步） ----------
def run_image(task_id: str, model_id: str, params: dict, token, on_progress=None) -> dict:
    cost = estimate_cost("image", model_id, params)
    budget_gate(cost)
    prompt = (params.get("prompt") or "").strip()
    if not prompt:
        raise err("VALIDATION_FAILED", "prompt 不能为空")

    mode = (params.get("generation_mode") or "text_to_image").strip()
    if mode not in ("text_to_image", "image_to_image"):
        raise err("VALIDATION_FAILED", "generation_mode 只能为 text_to_image 或 image_to_image",
                  detail={"allowed": ["text_to_image", "image_to_image"], "got": mode})

    size = params.get("size") or "1024x1024"
    ref_data_url = None
    ref_asset_id = params.get("input_asset_id")
    if mode == "image_to_image":
        if not ref_asset_id:
            raise err("REFERENCE_IMAGE_REQUIRED", "图生图必须提供 input_asset_id 作为参考图")
        ref = storage.load_asset(ref_asset_id)          # 不存在 → ASSET_NOT_FOUND
        ref_data_url = storage.asset_data_url(ref)      # 校验图片 + 真实 MIME
        if on_progress:
            on_progress(0.15)

    if on_progress:
        on_progress(0.3)
    # 关键：参考图真正进入请求体（image 字段），不是只存 ID
    res = ark.image_generate(model_id, prompt, size=size,
                            watermark=bool(params.get("watermark", False)),
                            response_format="b64_json",
                            image=ref_data_url)
    if on_progress:
        on_progress(0.8)
    if res.get("b64"):
        data = base64.b64decode(res["b64"])
    elif res.get("url"):
        data = ark.download(res["url"])
    else:
        raise err("PROVIDER_BAD_REQUEST", "云端返回既无 b64 也无 url")

    usage = {"images": 1, "generation_mode": mode,
             "reference_image": bool(ref_data_url), "raw_usage": res.get("usage")}
    return {"data": data, "kind": "image", "width": None, "height": None,
            "usage": usage, "cost_cny": cost, "request_id": res.get("request_id"),
            "load_ms": 0, "latency_ms": res.get("latency_ms")}


# ---------- 视频（异步） ----------
POLL_SCHEDULE = [3, 5, 10, 20, 30]
MAX_POLL_SECONDS = float(1800)   # 本地轮询上限；到点不判失败，只标记超时并保留 remote_task_id


def build_content(params: dict) -> list[dict]:
    content: list[dict] = []
    prompt = (params.get("prompt") or "").strip()
    if prompt:
        content.append({"type": "text", "text": prompt})
    asset_id = params.get("input_asset_id")
    if asset_id:
        a = storage.load_asset(asset_id)
        b64 = base64.b64encode(storage.asset_path(a).read_bytes()).decode()
        content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"},
                        "role": params.get("image_role", "first_frame")})
    if not content:
        raise err("VALIDATION_FAILED", "视频任务至少需要 prompt 或首帧图")
    return content


def run_video(task_id: str, model_id: str, params: dict, token, on_progress=None) -> dict:
    cost = estimate_cost("video", model_id, params)
    budget_gate(cost)

    task = db.get("tasks", task_id) or {}
    remote_id = task.get("remote_task_id")

    if not remote_id:
        content = build_content(params)
        created = ark.video_create(
            model_id, content,
            # B10：执行路径同样走严格解析，禁止 int()/默认值静默修正
            duration=config.resolve_duration_s(model_id, params.get("duration"), strict=True),
            ratio=params.get("ratio") or "9:16",
            resolution=params.get("resolution") or "720p",
            generate_audio=bool(params.get("generate_audio", False)),
            watermark=bool(params.get("watermark", False)),
            seed=params.get("seed"),
            safety_identifier=task_id,     # 用于创建超时后的人工对账
        )
        remote_id = created["remote_task_id"]
        db.update("tasks", task_id, {"remote_task_id": remote_id, "remote_status": "created",
                                     "status": "remote_queued", "stage": "remote_queued"})

    # 轮询（重启后从已持久化的 remote_id 继续，不重新创建）
    t0 = time.time()
    i = 0
    state: dict = {}
    while True:
        token.raise_if_canceled()
        if time.time() - t0 > MAX_POLL_SECONDS:
            raise err("TIMEOUT_LOCAL_POLL", "本地轮询超时；云端任务 ID 已保留，可稍后继续查询",
                      detail={"remote_task_id": remote_id})
        try:
            state = ark.video_query(remote_id)
        except Exception as e:  # noqa: BLE001
            if getattr(e, "code", "") in ("PROVIDER_UNAVAILABLE", "PROVIDER_RATE_LIMITED"):
                time.sleep(POLL_SCHEDULE[min(i, len(POLL_SCHEDULE) - 1)])
                i += 1
                continue
            raise
        st = (state.get("status") or "").lower()
        db.update("tasks", task_id, {"remote_status": st, "stage": f"remote_{st}"})
        if on_progress:
            on_progress(0.3 if st in ("queued", "running") else 0.7)
        if st in ("succeeded", "success"):
            break
        if st in ("failed", "cancelled", "canceled"):
            e = state.get("error") or {}
            raise err("CONTENT_REJECTED" if "sensitive" in str(e).lower() else "PROVIDER_UNAVAILABLE",
                      f"云端任务失败：{e.get('code') or st} {str(e.get('message') or '')[:200]}",
                      detail={"remote_task_id": remote_id, "remote_status": st})
        time.sleep(POLL_SCHEDULE[min(i, len(POLL_SCHEDULE) - 1)])
        i += 1

    url = state.get("video_url")
    if not url:
        raise err("RESULT_URL_EXPIRED", "云端任务成功但未返回 video_url（URL 24 小时有效，可能已过期）",
                  detail={"remote_task_id": remote_id})
    data = ark.download(url)
    usage = state.get("usage") or {}
    tokens = float(usage.get("completion_tokens") or usage.get("total_tokens") or 0)
    if tokens:
        cost = round(tokens / 1e6 * config.VIDEO_PRICE_PER_MTOKEN.get(model_id, 46.0), 4)
    return {"data": data, "kind": "video", "width": None, "height": None,
            "fps": state.get("fps"), "frames": None, "duration": state.get("duration"),
            "usage": {"tokens": tokens, "raw_usage": usage}, "cost_cny": cost,
            "remote_task_id": remote_id, "load_ms": 0, "latency_ms": int((time.time() - t0) * 1000)}
