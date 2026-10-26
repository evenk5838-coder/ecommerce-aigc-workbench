"""配置：全部来自环境变量，密钥类内容本项目不需要。"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]          # 项目根目录


def _load_dotenv() -> None:
    """在读取任何配置前加载本机 .env（不覆盖已存在的环境变量）。"""
    p = ROOT / ".env"
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


_load_dotenv()
DATA = ROOT / "data"
ASSETS = DATA / "assets"
MODELS = DATA / "models"
DB_PATH = DATA / "app.db"

DATA.mkdir(parents=True, exist_ok=True)
ASSETS.mkdir(parents=True, exist_ok=True)
MODELS.mkdir(parents=True, exist_ok=True)

HOST = os.getenv("APP_HOST", "127.0.0.1")
PORT = int(os.getenv("APP_PORT", "8000"))
CONCURRENCY = int(os.getenv("APP_CONCURRENCY", "1"))     # 默认单并发；提高前必须实测内存
MAX_UPLOAD_MB = int(os.getenv("APP_MAX_UPLOAD_MB", "20"))
POLL_INTERVAL = float(os.getenv("APP_WORKER_POLL", "1.0"))
MPS_BUDGET_RATIO = float(os.getenv("APP_MPS_BUDGET_RATIO", "0.9"))

# 模型登记表：只声明"本机是否存在权重 + 元信息"，不代表已验证可用
MODEL_REGISTRY = {
    "sd15": {
        "name": "Stable Diffusion 1.5 (fp16, diffusers)",
        "kind": "image",
        "dir": str(MODELS / "sd15"),
        "license": "creativeml-openrail-m",
        "max_size": 768,
        "default_size": 512,
        "default_steps": 25,
        "note": "备选模型；本次用于链路验证与出图",
    },
    "sdxl-base-1.0": {
        "name": "SDXL Base 1.0 (fp16, diffusers)",
        "kind": "image",
        "dir": str(MODELS / "sdxl-base-1.0"),
        "license": "openrail++",
        "max_size": 1024,
        "default_size": 768,
        "default_steps": 25,
        "note": "首测候选；权重未就位时不可用",
    },
    "i2vgen-xl": {
        "name": "I2VGen-XL (fp16, diffusers, I2V)",
        "kind": "video",
        "dir": str(MODELS / "i2vgen-xl"),
        "license": "模型卡标注 MIT，另含研究/非商业说明 → 商用许可待核实",
        "max_size": 896,
        "default_size": 512,
        "default_steps": 25,
        "default_frames": 8,
        "note": "首测候选；本次仅学习验证",
    },
}

# ---------- 云端（火山方舟 Ark）模型登记：模型 ID 来自官方「模型列表」，是否已开通需账号核对 ----------
CLOUD = os.getenv("APP_PROVIDER", "ark") == "ark"

# 图片计价（2026-10-23 官方《模型价格》核对）：
#   flash  输出图 0.12 元/张（不区分像素档），输入图免费
#   pro    单图生成按输出像素档：≤261 万像素 0.30，>261 万像素 0.60；
#          输入图首张免费，第 2 张起 0.02 元/张
#   图层拆分场景单价不同（≤261 万 0.15 / >261 万 0.30），本项目未使用图层拆分。
IMAGE_PRICE_CNY = {  # 旧字段保留：单一价（不分像素档）的模型放这里
    "doubao-seedream-5-0-flash-260915": 0.12,
}
# 按输出像素档计价的模型：tiers 按 max_mp 升序，命中第一个 mp <= max_mp
IMAGE_PIXEL_TIERS = {
    "doubao-seedream-5-0-pro-260628": [
        {"max_mp": 2.61, "price": 0.30},
        {"max_mp": 999999.0, "price": 0.60},
    ],
    "doubao-seedream-5-0-260128": [
        {"max_mp": 2.61, "price": 0.30},
        {"max_mp": 999999.0, "price": 0.60},
    ],
}
# 图生图输入图加价（元/张，从第 2 张起）；缺省视为免费
IMAGE_INPUT_PRICE = {
    "doubao-seedream-5-0-pro-260628": {"free_first": True, "from_second": 0.02},
}

VIDEO_PRICE_PER_MTOKEN = {
    # 2026-10-22 17:29 官方《模型价格》核实：1.0-pro-fast = 4.20 元/百万 token（不含输入视频）
    # 2026-10-23 复核：2.0-mini = 23.00（输入不含视频）/ 14.00（输入含视频）
    # 历史记录（旧口径 15.0 估算）保留不重算，差异见 docs/交付/评测报告.md
    "doubao-seedance-1-0-pro-fast-251015": 4.20,
    "doubao-seedance-2-5-260628": 70.0,
    "doubao-seedance-2-0-260128": 46.0,
    "doubao-seedance-2-0-fast-260128": 37.0,
    "doubao-seedance-2-0-mini-260615": 23.0,
}
# 官方 token 估算公式：(输入时长+输出时长) × 输出宽 × 输出高 × 帧率 / 1024
# 已用本项目实测 704×1248 / 5.041667s / 24fps = 103,818 tokens 反算验证通过。
VIDEO_OUTPUT_FPS = 24
RESOLUTION_WH = {  # 分辨率档 → (宽, 高)，按常见横版；竖版比例自动交换
    "480p": (854, 480),
    "720p": (1280, 720),
    "1080p": (1920, 1080),
    "4k": (3840, 2160),
}
# 账号开通状态（来自真实调用探测，2026-10-22）：
#   verified  = 真实调用成功
#   not_opened = 真实调用返回 ModelNotOpen
MODEL_ACTIVATION = {
    "doubao-seedream-5-0-flash-260915": "verified",
    "doubao-seedance-1-0-pro-fast-251015": "verified",
    "doubao-seedream-5-0-pro-260628": "not_opened",
    "doubao-seedream-5-0-260128": "not_opened",
    "doubao-seedance-2-0-260128": "not_opened",
    "doubao-seedance-2-0-fast-260128": "not_opened",
    "doubao-seedance-2-0-mini-260615": "not_opened",
    "doubao-seedance-2-5-260628": "not_opened",
    "sd15": "local_history",
    "sdxl-base-1.0": "local_not_downloaded",
    "i2vgen-xl": "local_not_downloaded",
}

BUDGET_CNY = float(os.getenv("APP_BUDGET_CNY", "0"))   # 0 = 未批准预算，禁止调用云端

# ---------- 导演对话（LLM）预算闸门：**每次 LLM 请求前**检查 ----------
# 单次请求的预估上限（元）；0 = 不限制。默认给一个保守值，避免一次对话烧掉大量 token。
DIRECTOR_MAX_COST_PER_CALL_CNY = float(os.getenv("DIRECTOR_MAX_COST_PER_CALL_CNY", "0.05"))
# 单会话累计上限（元）；0 = 不限制
DIRECTOR_MAX_SESSION_COST_CNY = float(os.getenv("DIRECTOR_MAX_SESSION_COST_CNY", "0.30"))
# 单轮最多工具调用次数
DIRECTOR_MAX_TOOL_CALLS = int(os.getenv("DIRECTOR_MAX_TOOL_CALLS", "2"))

MODEL_REGISTRY.update({
    "doubao-seedream-5-0-pro-260628": {
        "name": "Seedream 5.0 pro（云端）", "kind": "image", "provider": "ark",
        "license": "火山方舟商用服务（按量计费）", "default_size": 1024, "max_size": 2048,
        "default_steps": None, "note": "云端推理；需账号已开通",
    },
    "doubao-seedream-5-0-flash-260915": {
        "name": "Seedream 5.0 flash（云端）", "kind": "image", "provider": "ark",
        "license": "火山方舟商用服务（按量计费）", "default_size": 1024, "max_size": 2048,
        "default_steps": None, "note": "更便宜（0.12 元/张）",
    },
    "doubao-seedance-2-0-260128": {
        "name": "Seedance 2.0（云端·图生视频）", "kind": "video", "provider": "ark",
        "license": "火山方舟商用服务（按量计费）", "default_size": 720, "max_size": 1080,
        "default_steps": None, "default_frames": None,
        "note": "云端推理；先跑 I2V；时长/分辨率档位以官方文档为准",
    },
    "doubao-seedance-1-0-pro-fast-251015": {
        "name": "Seedance 1.0 pro fast（云端·图生视频，账号已开通）", "kind": "video", "provider": "ark",
        "license": "火山方舟商用服务（按量计费）", "default_size": 720, "max_size": 1080,
        "default_steps": None, "default_frames": None,
        "note": "用户账号已开通；官方模型列表标注即将下线",
    },
    "doubao-seedance-2-0-mini-260615": {
        "name": "Seedance 2.0 mini（云端·图生视频）", "kind": "video", "provider": "ark",
        "license": "火山方舟商用服务（按量计费）", "default_size": 720, "max_size": 1080,
        "default_steps": None, "default_frames": None, "note": "最便宜档",
    },
    "doubao-seedance-2-5-260628": {
        "name": "Seedance 2.5（云端·图生视频）", "kind": "video", "provider": "ark",
        "license": "火山方舟商用服务（按量计费）", "default_size": 720, "max_size": 1080,
        "default_steps": None, "default_frames": None, "note": "最新档，单价最高",
    },
    "doubao-seedance-2-0-fast-260128": {
        "name": "Seedance 2.0 fast（云端·图生视频）", "kind": "video", "provider": "ark",
        "license": "火山方舟商用服务（按量计费）", "default_size": 720, "max_size": 1080,
        "default_steps": None, "default_frames": None, "note": "更快更便宜档",
    },
})

# 仅供流程测试的假模型（APP_TEST_FAKE=1 时注册）；不参与任何真实模型验收
if os.getenv("APP_TEST_FAKE") == "1":
    MODEL_REGISTRY["fake-image"] = {
        "name": "[测试] 模拟图片引擎（不推理、不做真实生成）",
        "kind": "image",
        "dir": str(MODELS / "_fake"),
        "license": "N/A",
        "max_size": 1024,
        "default_size": 512,
        "default_steps": 10,
        "note": "仅用于验证队列/取消/重试/恢复流程",
    }

# ---------- 模型能力元数据（2026-10-23 官方《模型列表》核对） ----------
# 用途：前端/调用方按能力过滤与校验参数，不靠复制各模型参数规则。
MODEL_CAPABILITIES = {
    "doubao-seedream-5-0-flash-260915": {
        "input_modes": ["text_to_image", "image_to_image"],
        "max_reference_images": 10,
        "ratios": ["1:1", "3:4", "4:3", "9:16", "16:9"],
        "resolutions": [],
        "sizes": ["1024x1024", "2048x2048", "1728x2304", "2304x1728", "1536x2688", "2688x1536"],
        "duration_s": [],
        "supports": ["图层拆分", "单图生成", "文生图", "单张图生图", "多参考图生图"],
        "max_ipm": 500,
        "pricing": {"kind": "per_image", "cny_per_image": 0.12, "input_image": "免费"},
    },
    "doubao-seedream-5-0-pro-260628": {
        "input_modes": ["text_to_image", "image_to_image"],
        "max_reference_images": 10,
        "ratios": ["1:1", "3:4", "4:3", "9:16", "16:9"],
        "resolutions": [],
        "sizes": ["1024x1024", "2048x2048", "1728x2304", "2304x1728", "1536x2688", "2688x1536"],
        "duration_s": [],
        "supports": ["图层拆分", "单图生成", "文生图", "单张图生图", "多参考图生图"],
        "max_ipm": 500,
        "pricing": {"kind": "per_pixel_tier",
                    "tiers": [{"max_mp": 2.61, "cny": 0.30}, {"max_mp": None, "cny": 0.60}],
                    "input_image": {"first_free": True, "from_second_cny": 0.02}},
        "note": "能力项与 flash 一致；画质/速度差异待同题实测",
    },
    "doubao-seedance-1-0-pro-fast-251015": {
        "input_modes": ["image_to_video", "text_to_video"],
        "max_reference_images": 1,
        "ratios": ["9:16", "16:9", "1:1"],
        "resolutions": ["480p", "720p", "1080p"],
        "sizes": [],
        "duration_s": [5, 10],
        # B10：原生能力 2–12 整数秒（range+step 优先于 duration_s 枚举）
        "duration_range_s": [2, 12],
        "duration_step_s": 1,
        "supports": ["首帧生视频", "文生视频"],
        "fps": 24,
        "pricing": {"kind": "per_mtoken", "cny_per_mtoken": 4.20, "billing": "仅成功生成计费"},
    },
    "doubao-seedance-2-0-mini-260615": {
        "input_modes": ["image_to_video", "text_to_video"],
        "max_reference_images": 1,
        "ratios": ["9:16", "16:9", "1:1"],
        "resolutions": ["480p", "720p"],
        "sizes": [],
        "duration_s": [5, 10],
        "duration_range_s": [4, 15],
        "duration_step_s": 1,
        "supports": ["全模态参考生视频", "参考生视频", "编辑视频", "延长视频",
                     "首尾帧生视频", "首帧生视频", "文生视频"],
        "fps": 24,
        "pricing": {"kind": "per_mtoken", "cny_per_mtoken": 23.00,
                    "cny_per_mtoken_with_input_video": 14.00, "billing": "仅成功生成计费"},
        "limits": {"rpm_personal": 180, "concurrency_personal": 3},
    },
}

# ---------- 模型生命周期（2026-10-23 官方《模型下线公告》核实） ----------
# 公告：https://www.volcengine.com/docs/82379/1350667（第十批模型下线说明）
#   通知 2026-09-22 10:00 (UTC+8)；EOM 2026-09-24 10:00 (UTC+8) 停止新购；
#   EOS 2026-11-24 14:00 (UTC+8) 模型服务下线。
# 官方明确：“生图生视频模型不会自动迁移到新模型”——因此本项目不静默替换。
MODEL_LIFECYCLE = {
    "doubao-seedance-1-0-pro-fast-251015": {
        "eom_at": "2026-09-24T10:00:00+08:00",
        "eos_at": "2026-11-24T14:00:00+08:00",
        "migrate_to": "doubao-seedance-2-0-mini-260615",
        "action": "关停",
        "announce_url": "https://www.volcengine.com/docs/82379/1350667",
    },
    "doubao-seedance-1-0-pro-250528": {
        "eom_at": "2026-09-24T10:00:00+08:00",
        "eos_at": "2026-11-24T14:00:00+08:00",
        "migrate_to": "doubao-seedance-2-0-mini-260615",
        "action": "关停",
        "announce_url": "https://www.volcengine.com/docs/82379/1350667",
    },
}

# 仅供流程测试的假模型（APP_TEST_FAKE=1 时注册）；不参与任何真实模型验收
if os.getenv("APP_TEST_FAKE") == "1":
    MODEL_REGISTRY["fake-image"] = {
        "name": "[测试] 模拟图片引擎（不推理、不做真实生成）",
        "kind": "image",
        "dir": str(MODELS / "_fake"),
        "license": "N/A",
        "max_size": 1024,
        "default_size": 512,
        "default_steps": 10,
        "note": "仅用于验证队列/取消/重试/恢复流程",
    }


# ---------- 价格计算 ----------
def parse_size_px(size: str | None) -> tuple[int, int]:
    """'2048x2048' → (2048, 2048)；无法解析时回退 1024×1024。"""
    try:
        w, h = str(size or "").lower().replace("*", "x").split("x")[:2]
        return int(w), int(h)
    except Exception:  # noqa: BLE001
        return 1024, 1024


def image_cost_cny(model_id: str, size: str | None, reference_images: int = 0) -> float:
    """图片单次调用估算价：支持单一价与像素档两种口径；ref 图从第 2 张起加价。"""
    cost = 0.0
    if model_id in IMAGE_PIXEL_TIERS:
        w, h = parse_size_px(size)
        mp = (w * h) / 1_000_000
        for tier in IMAGE_PIXEL_TIERS[model_id]:
            if mp <= tier["max_mp"]:
                cost = float(tier["price"])
                break
        priced = IMAGE_INPUT_PRICE.get(model_id)
        if priced and reference_images > 1:
            cost += priced["from_second"] * (reference_images - 1)
    else:
        cost = float(IMAGE_PRICE_CNY.get(model_id, 0.30))
    return round(cost, 4)


# ---------- B10：时长策略（range+step 优先，其次枚举，否则不可用） ----------
DEFAULT_VIDEO_DURATION_S = 5


def duration_policy(model_id: str) -> dict:
    """解析某视频模型的时长策略。

    优先级：
      1) `duration_range_s` + `duration_step_s` **同时完整有效** → kind="range"
      2) 否则退回 `duration_s` 枚举                             → kind="enum"
      3) 两者都缺/非法                                         → kind="unavailable"
    """
    caps = MODEL_CAPABILITIES.get(model_id) or {}
    rng = caps.get("duration_range_s")
    step = caps.get("duration_step_s")
    if (isinstance(rng, (list, tuple)) and len(rng) == 2
            and all(isinstance(x, int) and not isinstance(x, bool) for x in rng)
            and isinstance(step, int) and not isinstance(step, bool) and step > 0
            and rng[0] <= rng[1] and (rng[1] - rng[0]) % step == 0):
        return {"kind": "range", "min": rng[0], "max": rng[1], "step": step,
                "allowed": None}
    enum = caps.get("duration_s") or []
    enum = [d for d in enum if isinstance(d, int) and not isinstance(d, bool)]
    if enum:
        return {"kind": "enum", "min": None, "max": None, "step": None,
                "allowed": sorted(enum)}
    return {"kind": "unavailable", "min": None, "max": None, "step": None,
            "allowed": None}


def _duration_error(model_id: str, got, reason: str, policy: dict) -> "AppError":  # noqa: F821
    from .errors import err
    return err("MODEL_CAPABILITY_MISMATCH",
               f"模型 {model_id} 的时长不合法：got={got!r}（{reason}）",
               detail={"field": "duration", "policy": policy, "reason": reason,
                       "got": got, "got_type": type(got).__name__})


_LEGACY_INT_RE = None


def _legacy_to_int(value):
    """**仅用于历史记录**：把旧版本曾接受的 10.0 / "10" 归一为 10。

    绝不用于新请求；也不做四舍五入（10.5 仍视为非法）。
    """
    import re as _re
    global _LEGACY_INT_RE
    if _LEGACY_INT_RE is None:
        _LEGACY_INT_RE = _re.compile(r"^[+-]?\d+$")
    if isinstance(value, bool):
        return None
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        t = value.strip()
        if _LEGACY_INT_RE.match(t):
            return int(t)
        return None
    return None


def resolve_duration_s(model_id: str, value=None, *, strict: bool = True,
                       legacy: bool = False):
    """把时长解析为**唯一有效整数**（校验/估价/落库/供应商请求共用）。

    严格模式（默认）：拒绝 float（含 5.0）、bool、str、越界、不满足步长——
    **绝不 int()/round() 截断或静默修正**。
    `value is None` 时按模型默认值（5）处理，并校验默认值是否合法。
    """
    policy = duration_policy(model_id)
    if policy["kind"] == "unavailable":
        if strict:
            raise _duration_error(model_id, value, "policy_unavailable", policy)
        return None

    if value is None:
        value = DEFAULT_VIDEO_DURATION_S
        if policy["kind"] == "enum" and value not in policy["allowed"]:
            if strict:
                raise _duration_error(model_id, value, "missing", policy)
            return None

    # 类型：只接受真正的 int（bool 是 int 子类，必须排除）
    # legacy=True 时额外接受"旧版本曾接受"的 10.0 / "10"（仅限历史回放/执行/重试）
    if isinstance(value, bool) or not isinstance(value, int):
        if legacy:
            value = _legacy_to_int(value)
            if value is None:
                if strict:
                    raise _duration_error(model_id, value, "not_integer", policy)
                return None
        else:
            if strict:
                raise _duration_error(model_id, value, "not_integer", policy)
            return None

    if policy["kind"] == "range":
        if value < policy["min"] or value > policy["max"]:
            if strict:
                raise _duration_error(model_id, value, "out_of_range", policy)
            return None
        if (value - policy["min"]) % policy["step"] != 0:
            if strict:
                raise _duration_error(model_id, value, "step_mismatch", policy)
            return None
        return int(value)

    # enum
    if value not in policy["allowed"]:
        if strict:
            raise _duration_error(model_id, value, "out_of_range", policy)
        return None
    return int(value)


def video_token_estimate(duration_s: float, resolution: str, ratio: str) -> int:
    """按官方公式估算 token：(输出时长) × 宽 × 高 × 帧率 / 1024。"""
    w, h = RESOLUTION_WH.get(str(resolution or "720p"), (1280, 720))
    if str(ratio or "9:16").split(":")[0] < str(ratio or "9:16").split(":")[-1]:
        w, h = h, w
    return int(float(duration_s) * w * h * VIDEO_OUTPUT_FPS / 1024)


def video_cost_cny(model_id: str, params: dict, *, legacy: bool = False) -> float:
    """视频估算价：token 公式 × 单价。

    **不再在解析失败时按 5 秒兜底**（那会把历史 10 秒错算成 5 秒）。
    无法解析 → 直接报错，由调用方决定如何处理。
    """
    resolved = resolve_duration_s(model_id, params.get("duration"),
                                  strict=False, legacy=legacy)
    if resolved is None:
        raise _duration_error(model_id, params.get("duration"), "not_integer",
                              duration_policy(model_id))
    tokens = video_token_estimate(
        resolved,
        params.get("resolution") or "720p",
        params.get("ratio") or "9:16",
    )
    price = float(VIDEO_PRICE_PER_MTOKEN.get(model_id, 46.0))
    return round(tokens / 1e6 * price, 4)


# ---------- 模型生命周期（到期保护） ----------
def _iso_to_ts(value: str | None) -> float | None:
    if not value:
        return None
    try:
        from datetime import datetime
        return datetime.fromisoformat(value).timestamp()
    except ValueError:
        return None


def model_lifecycle_state(model_id: str, now_ts: float | None = None) -> dict:
    """**生命周期状态的唯一判定入口**（`/api/models` 列表与提交校验共用）。

    status:
      active        无下线计划
      eos_scheduled 已公告未到期 → **允许使用**，返回 eos_at/migrate_to 供前端提示
      eos_expired   已到期（now >= eos_at） → 提交路径必须拒绝（MODEL_EOS）

    `migrate_to_activation` 只**转述** `MODEL_ACTIVATION` 中目标模型的真实调用结果
    （`verified` / `not_opened` / `unknown`），**不表示迁移目标已开通或可用**；
    调用方需以目标模型自身的 `activation` 为准。
    """
    import time as _time

    life = MODEL_LIFECYCLE.get(model_id)
    if not life:
        return {"status": "active"}
    now = _time.time() if now_ts is None else float(now_ts)
    eos_ts = _iso_to_ts(life.get("eos_at"))
    eom_ts = _iso_to_ts(life.get("eom_at"))
    expired = eos_ts is not None and now >= eos_ts      # 到期时刻即视为到期
    migrate_to = life.get("migrate_to")
    return {
        "status": "eos_expired" if expired else "eos_scheduled",
        "eos_at": life.get("eos_at"),
        "eom_at": life.get("eom_at"),
        "migrate_to": migrate_to,
        # 仅转述目标模型的真实调用结果；不做“可用/已开通”的断言
        "migrate_to_activation": MODEL_ACTIVATION.get(migrate_to, "unknown") if migrate_to else None,
        "action": life.get("action"),
        "announce_url": life.get("announce_url"),
        "seconds_remaining": (int(eos_ts - now) if (eos_ts is not None and not expired) else 0),
        "eom_passed": (eom_ts is not None and now >= eom_ts),
    }


def model_submittability(model_id: str, now_ts: float | None = None) -> dict:
    """模型当前是否**允许新提交**（与前端是否可选一致）。

    同时检查：activation（账号开通）与 lifecycle（EOS）；再叠加时长策略是否可用。
    """
    activation = MODEL_ACTIVATION.get(model_id, "unknown")
    life = model_lifecycle_state(model_id, now_ts)
    policy = duration_policy(model_id)
    kind = (MODEL_REGISTRY.get(model_id) or {}).get("kind")
    reasons = []
    if activation == "not_opened":
        reasons.append("MODEL_NOT_OPENED")
    elif activation in ("local_not_downloaded",):
        reasons.append("MODEL_NOT_DOWNLOADED")
    if life.get("status") == "eos_expired":
        reasons.append("MODEL_EOS")
    if kind == "video" and policy["kind"] == "unavailable":
        reasons.append("DURATION_POLICY_UNAVAILABLE")
    return {
        "submittable": not reasons,
        "reasons": reasons,
        "activation": activation,
        "lifecycle_status": life.get("status"),
        "duration_policy_kind": policy["kind"] if kind == "video" else None,
    }


def validate_params_for_model(model_id: str, kind: str, params: dict) -> list[dict]:
    """对**最终生效参数**做模型能力校验；返回问题列表（空=通过）。"""
    caps = MODEL_CAPABILITIES.get(model_id)
    if not caps:
        return []
    problems: list[dict] = []
    if kind == "image":
        mode = params.get("generation_mode", "text_to_image")
        allowed_modes = caps.get("input_modes", [])
        if allowed_modes and mode not in allowed_modes:
            problems.append({"field": "generation_mode", "got": mode, "allowed": allowed_modes})
        size = params.get("size")
        allowed_sizes = caps.get("sizes") or []
        if size and allowed_sizes and size not in allowed_sizes:
            problems.append({"field": "size", "got": size, "allowed": allowed_sizes})
    else:
        # B10：时长走严格策略校验（拒绝小数/bool/字符串/越界/步长不符）
        try:
            resolve_duration_s(model_id, params.get("duration"), strict=True)
        except Exception as e:  # noqa: BLE001  AppError 转成 problems 结构
            problems.append({"field": "duration",
                             "got": params.get("duration"),
                             "reason": (getattr(e, "detail", {}) or {}).get("reason"),
                             "policy": (getattr(e, "detail", {}) or {}).get("policy")})
        ratio = params.get("ratio")
        allowed_ratios = caps.get("ratios") or []
        if ratio and allowed_ratios and ratio not in allowed_ratios:
            problems.append({"field": "ratio", "got": ratio, "allowed": allowed_ratios})
        resolution = params.get("resolution")
        allowed_res = caps.get("resolutions") or []
        if resolution and allowed_res and resolution not in allowed_res:
            problems.append({"field": "resolution", "got": resolution, "allowed": allowed_res})
    return problems


REQUIRED_FILES = {
    "image": ["model_index.json", "unet", "vae", "text_encoder", "tokenizer", "scheduler"],
    "video": ["model_index.json", "unet", "vae", "text_encoder", "tokenizer", "scheduler"],
}
