"""Reviewed AiHubMix subset, separate from Ark IDs, prices and acceptance.

Sources checked 2026-10-29: /call/schema/models/{model}/endpoints and
https://docs.aihubmix.com/cn/api/async-tasks. This is an intentionally small
application subset, not a claim to expose every vendor parameter.
"""
from __future__ import annotations

import math
import os
from copy import deepcopy
from decimal import Decimal, ROUND_UP

from .errors import err
from . import aihubmix_extra_images as extra_images
from . import aihubmix_extra_videos as extra_videos

# Small provider-contract modules share the existing transport and task lifecycle.
EXTENSIONS = {mid: module for module in (extra_images, extra_videos) for mid in module.MODELS}

PREFIX = "aihubmix:"
FLASH = PREFIX + "doubao-seedream-5-0-flash"
MINI = PREFIX + "doubao-seedance-2-0-mini-260615"
FAST = PREFIX + "doubao-seedance-2-0-fast-260128"
NANO = PREFIX + "gemini-nano-banana-2.1"
GPT_IMAGE = PREFIX + "gpt-image-2.5-flare"
QWEN = PREFIX + "qwen-image-3.0-pro"
MODELS = {
    NANO: {"name": "Nano Banana 2.1 · AiHubMix", "kind": "image"},
    GPT_IMAGE: {"name": "GPT Image 2.5 Flare · AiHubMix", "kind": "image"},
    QWEN: {"name": "Qwen Image 3.0 Pro · AiHubMix", "kind": "image"},
    FLASH: {"name": "Seedream 5.0 Flash · AiHubMix", "kind": "image"},
    MINI: {"name": "Seedance 2.0 Mini · AiHubMix", "kind": "video"},
    FAST: {"name": "Seedance 2.0 Fast · AiHubMix", "kind": "video"},
}
for _module in (extra_images, extra_videos):
    MODELS.update(deepcopy(_module.MODELS))

# USD list estimates, never supplier bills. Mini's public page and structured
# catalog disagree: reserve using the higher public-page rate until reconciled.
# Nano/GPT token billing has no verified request bound: do not invent flat prices.
USD_PRICING = {
    FLASH: {"per_image": "0.0186"},
    MINI: {"per_second": {"480p": "0.055", "720p": "0.113"},
           "conservative_upper_estimate": True,
           "pricing_discrepancy": {
               "catalog_per_second": {"480p": "0.0344", "720p": "0.0706"},
               "public_page_per_second": {"480p": "0.055", "720p": "0.113"},
               "policy": "higher_public_rate",
               "note": "目录与模型页报价不同，按较高模型页单价预留；非实扣账单。",
               "sources": ["https://aihubmix.com/api/v1/models",
                           "https://aihubmix.com/model/doubao-seedance-2-0-mini-260615"]}},
    QWEN: {"long_edge_tiers": [{"max": 1024, "per_image": "0.03572"},
                               {"max": 2560, "per_image": "0.07143"}],
           "per_input_image": "0.00286"},
    FAST: {"per_second": {"480p": "0.055", "720p": "0.113"}},
}

for _module in (extra_images, extra_videos):
    USD_PRICING.update(deepcopy(_module.USD_PRICING))

# These are the contracts covered by retained real synthetic-cup samples, not
# a claim that all supplier capabilities or every product have been evaluated.
SAMPLE_PROFILES = {
    extra_images.FLUX: [{"generation_mode": ["text_to_image"], "ratio": ["1:1"], "size": ["1024x1024"]}],
    extra_videos.MINIMAX_H3: [{"generation_mode": [mode], "duration": [5], "ratio": ["16:9"],
                             "resolution": ["768P"], "generate_audio": [False]}
                            for mode in ("text_to_video", "image_to_video")],
    extra_videos.WAN_3: [{"generation_mode": [mode], "duration": [5], "ratio": ["16:9"],
                         "resolution": ["480p"], "generate_audio": [False]}
                        for mode in ("text_to_video", "image_to_video")],
    MINI: [{"generation_mode": ["image_to_video"], "duration": [5], "ratio": ["16:9"],
            "resolution": ["480p"], "generate_audio": [False]}],
    QWEN: [{"generation_mode": [mode], "ratio": ["1:1"], "size": ["1024x1024"]}
           for mode in ("text_to_image", "image_to_image")],
    FLASH: [{"generation_mode": [mode], "ratio": ["1:1"], "size": ["1024x1024"]}
            for mode in ("text_to_image", "image_to_image")],
    FAST: [{"generation_mode": [mode], "duration": [5], "ratio": ["16:9"],
            "resolution": ["480p"], "generate_audio": [False]}
           for mode in ("text_to_video", "image_to_video")],
}


# Retained acceptance metadata belongs to each model. A later model tested
# against a newer policy must not inherit Fast's older v2 sample or caveats.
SAMPLE_DETAILS = {
    extra_videos.MINIMAX_H3: {
        "tested_output_policy_version": "no-watermark-edge-gradient-v3",
        "tested_audio_processing_version": "h3-video-only-v1",
        "limitations": ["仅虚构白杯文生和图生视频，5秒768P横屏样例；未测更高规格",
                        "原生文件含近静音音轨；交付文件本地去音轨，视频流不重编码且逐帧校验一致，原件保留",
                        "样例主体完整、无可见水印；旋转幅度和背景效果并非确定性控制"],
        "evidence": "docs/evidence/v0.11-FLUX-H3-Wan真实验收-20261030/当前结果.md",
    },
    extra_videos.WAN_3: {
        "tested_output_policy_version": "no-watermark-edge-gradient-v3",
        "limitations": ["仅虚构白杯文生和图生视频，5秒480p横屏无声样例；未测更高规格",
                        "样例主体完整、无可见水印；构图比例、运动幅度和复杂商品一致性不保证精确控制"],
        "evidence": "docs/evidence/v0.11-FLUX-H3-Wan真实验收-20261030/当前结果.md",
    },
    extra_images.FLUX: {
        "tested_output_policy_version": "no-watermark-contain-v1",
        "limitations": ["仅虚构白杯、1024方图文生样例；未测图生图与更大规格",
                        "把手造型并非严格按提示控制，不保证复杂商品一致性"],
        "evidence": "docs/evidence/v0.11-FLUX-H3-Wan真实验收-20261030/当前结果.md",
    },
    MINI: {
        "tested_output_policy_version": "no-watermark-edge-gradient-v3",
        "limitations": ["仅本次虚构白杯首帧图生视频，5秒480p16:9无声；未测文生视频",
                        "样例主体完整，无可见水印；旋转幅度和背景细微色差仍有限制",
                        "不保证复杂商品一致性或定量运镜控制"],
        "evidence": "docs/evidence/v0.11-Qwen-Mini真实验收-20261030/当前结果.md",
    },
    QWEN: {
        "tested_output_policy_version": "no-watermark-contain-v1",
        "limitations": ["仅虚构白杯、1024方图、单参考换背景的两种模式样例，未测更大尺寸",
                        "杯身局部有轻微纵向纹理，不保证任意商品一致性或完美材质"],
        "evidence": "docs/evidence/v0.11-Qwen-Mini真实验收-20261030/当前结果.md",
    },
    FLASH: {
        "tested_output_policy_version": "no-watermark-contain-v1",
        "limitations": ["仅虚构白杯的已列规格样例，不保证任意商品一致性"],
        "evidence": "docs/evidence/v0.11-AiHubMix无水印复验-20261030/当前结果.md",
    },
    FAST: {
        "tested_output_policy_version": "no-watermark-edge-extend-v2",
        "limitations": [
            "v2样例主体完整、无可见水印；局部阴影仍有接痕",
            "数字运镜和留白目标未严格遵守，不是确定性控制",
            "当前补边策略若不同于已测v2，则该新版成片尚未付费复验",
        ],
        "evidence": "docs/evidence/v0.11-视频构图v2真实复验-20261030/当前结果.md",
    },
}

# Offline request contracts are separate from retained real samples and the
# operational allowlist. This first subset prepares acceptance, not activation.
# Pixel sizes remain the app representation; Nano translates them to tiers.
CANDIDATE_PROFILES = {
    NANO: [{"generation_mode": [mode], "size": [size], "ratio": ["1:1"]}
           for mode in ("text_to_image", "image_to_image")
           for size in ("1024x1024", "2048x2048", "4096x4096")],
    GPT_IMAGE: [{"generation_mode": [mode], "size": [size], "ratio": [ratio]}
                for mode in ("text_to_image", "image_to_image")
                for size, ratio in (("1024x1024", "1:1"), ("1536x1024", "3:2"), ("1024x1536", "2:3"))],
    QWEN: [{"generation_mode": [mode], "size": [size], "ratio": [ratio]}
           for mode in ("text_to_image", "image_to_image")
           for size, ratio in (("1024x1024", "1:1"), ("2048x2048", "1:1"),
                               ("1536x1024", "3:2"), ("1024x1536", "2:3"))],
    MINI: [{"generation_mode": [mode], "duration": [5], "ratio": ["16:9"],
            "resolution": ["480p"], "generate_audio": [False]}
           for mode in ("text_to_video", "image_to_video")],
}
NANO_TIERS = {"1024x1024": "1K", "2048x2048": "2K", "4096x4096": "4K"}
for _module in (extra_images, extra_videos):
    CANDIDATE_PROFILES.update(deepcopy(_module.PROFILES))



def effective_candidate_params(model_id: str, kind: str, params: dict) -> dict:
    """Fill only missing new-contract fields; never rewrite explicit settings."""
    result = dict(params)
    if model_id not in EXTENSIONS:
        return result
    result.setdefault("generation_mode", "text_to_" + kind)
    profiles = CANDIDATE_PROFILES[model_id]
    profile = next((row for row in profiles if result["generation_mode"] in row.get("generation_mode", [])), profiles[0])
    for field, values in profile.items():
        if isinstance(values, list) and values:
            result.setdefault(field, deepcopy(values[0]))
    return result

def candidate_parameter_problems(model_id: str, params: dict) -> list[dict]:
    if model_id in EXTENSIONS:
        return EXTENSIONS[model_id].parameter_problems(model_id, params)
    if model_id not in CANDIDATE_PROFILES:
        return []
    issues = []
    seed = params.get("seed")
    if model_id == QWEN:
        if seed is not None and (type(seed) is not int or not -1 <= seed <= 2147483647):
            issues.append({"field": "seed", "reason": "unsupported_seed"})
        negative = params.get("negative_prompt")
        if negative is not None and not isinstance(negative, str):
            issues.append({"field": "negative_prompt", "reason": "not_string"})
    else:
        if seed is not None and (type(seed) is not int or seed != -1):
            issues.append({"field": "seed", "reason": "unsupported_seed"})
        if params.get("negative_prompt") not in (None, ""):
            issues.append({"field": "negative_prompt", "reason": "unsupported_parameter"})
    if model_id == GPT_IMAGE and isinstance(params.get("prompt"), str) and len(params["prompt"]) > 32000:
        issues.append({"field": "prompt", "reason": "too_long"})
    quality = params.get("quality")
    if quality is not None and (model_id != GPT_IMAGE or quality != "medium"):
        issues.append({"field": "quality", "reason": "unsupported_quality"})
    if model_id == NANO:
        size = params.get("size", "1024x1024")
        tier = NANO_TIERS.get(size) if isinstance(size, str) else None
        if "resolution" in params and params["resolution"] != tier:
            issues.append({"field": "resolution", "reason": "size_tier_mismatch"})
    elif MODELS[model_id]["kind"] == "image" and params.get("resolution") is not None:
        issues.append({"field": "resolution", "reason": "use_pixel_size"})
    # Vendor extras must be constructed here, not passed through by callers.
    for key in ("extra", "images", "mask", "output_format", "input_references", "frame_images",
                "prompt_extend", "enable_thinking", "prompt_extend_mode"):
        if key in params:
            issues.append({"field": key, "reason": "unsupported_parameter"})
    return issues


def image_body_options(model_id: str, params: dict) -> dict:
    if model_id in extra_images.MODELS:
        return extra_images.body_options(model_id, params)
    size = params.get("size", "1024x1024")
    result = {"n": 1, "async": True}
    if model_id == NANO:
        result.update(aspect_ratio="1:1", resolution=NANO_TIERS[size])
    elif model_id == GPT_IMAGE:
        result.update(size=size, extra={"quality": "medium"})
    else:
        result.update(size=size, extra={"watermark": False})
        if model_id == QWEN:
            result["extra"].update(prompt_extend=False, enable_thinking=False, prompt_extend_mode="direct")
            if params.get("seed", -1) is not None and params.get("seed", -1) >= 0:
                result["seed"] = params["seed"]
            if params.get("negative_prompt"):
                result["negative_prompt"] = params["negative_prompt"]
    return result


def enabled_models_config() -> dict:
    """Explicit server allowlist; unknown/unverified entries close the whole list.

    Accept the application ID or its exact upstream suffix. Never return the
    original invalid value: an operator could have pasted a secret by mistake.
    """
    raw = os.getenv("AIHUBMIX_ENABLED_MODELS", "").strip()
    if not raw:
        return {"valid": True, "models": []}
    values = [part.strip() for part in raw.split(",")]
    ids = [value if value.startswith(PREFIX) else PREFIX + value for value in values]
    if any(not value for value in values) or any(mid not in SAMPLE_PROFILES for mid in ids):
        return {"valid": False, "models": []}
    return {"valid": True, "models": sorted(set(ids))}


def activation_state(model_id: str) -> str:
    state = enabled_models_config()
    return "verified" if state["valid"] and model_id in state["models"] else "pending_validation"


def operational_profiles(model_id: str) -> list[dict]:
    return deepcopy(SAMPLE_PROFILES.get(model_id, []))


def validation_info(model_id: str) -> dict:
    from . import aihubmix_audio, media_policy
    image = MODELS.get(model_id, {}).get("kind") == "image"
    verified = model_id in SAMPLE_PROFILES
    detail = SAMPLE_DETAILS.get(model_id, {}) if verified else {}
    tested_policy = detail.get("tested_output_policy_version")
    current = media_policy.specification("image" if image else "video")["version"]
    audio = aihubmix_audio.policy(model_id) if not image else None
    tested_audio = detail.get("tested_audio_processing_version")
    enabled = enabled_models_config()
    limitations = deepcopy(detail.get("limitations", [])) if verified and detail else (
        ["真实样例的策略与验收元信息尚未登记"] if verified else
        ["尚无本通道真实生成与无水印验收样例，暂不开放"])
    return {"sample_generation_verified": verified,
            "contract_verified": verified or model_id in CANDIDATE_PROFILES,
            "candidate_profiles": deepcopy(CANDIDATE_PROFILES.get(model_id, [])),
            "watermark_control": (EXTENSIONS[model_id].WATERMARK_CONTROLS.get(model_id, "not_exposed")
                                  if model_id in EXTENSIONS else
                                  "not_exposed" if model_id in (NANO, GPT_IMAGE) else "explicit_false"),
            "operational_enabled": enabled["valid"] and model_id in enabled["models"],
            "enabled_models_config_valid": enabled["valid"],
            "pricing_ready": pricing_ready(model_id),
            "status": ("sample_verified" if image else "sample_verified_with_limitations") if verified else "unverified",
            "tested_profiles": operational_profiles(model_id),
            "tested_output_policy_versions": [tested_policy] if tested_policy else [],
            "sample_evidence": detail.get("evidence"),
            "current_output_policy_version": current,
            "current_output_policy_sample_verified": verified and bool(tested_policy) and current == tested_policy,
            "tested_audio_processing_version": tested_audio,
            "current_audio_processing_version": audio["version"] if audio else None,
            "current_audio_processing_sample_verified": (bool(tested_audio) and tested_audio == audio["version"]) if audio else None,
            "limitations": limitations, "supplier_bill_verified": False}


def public_pricing(model_id: str) -> dict:
    return {"currency": "USD", "status": "ready_estimate" if pricing_ready(model_id) else
            "awaiting_exchange_bound" if model_id in USD_PRICING else "unconfirmed",
            "estimate_only": True,
            "pending_reason": "token_cost_bound_unconfirmed" if model_id in (NANO, GPT_IMAGE) else
                              "request_cost_bound_unconfirmed" if model_id not in USD_PRICING else None,
            "notes": deepcopy(EXTENSIONS[model_id].PRICING_NOTES.get(model_id)) if model_id in EXTENSIONS else None,
            **deepcopy(USD_PRICING.get(model_id, {}))}


def model_note(model_id: str) -> str:
    if model_id not in SAMPLE_PROFILES:
        return "暂未开放"
    state = enabled_models_config()
    if not state["valid"]:
        return "暂未开放"
    if model_id not in state["models"]:
        return "暂未开放"
    if not pricing_ready(model_id):
        return "费用暂未就绪"
    if not os.getenv("AIHUBMIX_API_KEY", "").strip():
        return "服务暂未就绪"
    modes = {mode for row in operational_profiles(model_id) for mode in row.get("generation_mode", [])}
    if MODELS[model_id]["kind"] == "image":
        if modes == {"text_to_image", "image_to_image"}:
            return "商品图片与参考图编辑"
        return "参考图编辑" if modes == {"image_to_image"} else "文字生成商品图片" if modes == {"text_to_image"} else "暂未开放"
    if modes == {"text_to_video", "image_to_video"}:
        return "短视频与首帧视频"
    return "首帧生成视频" if modes == {"image_to_video"} else "文字生成短视频" if modes == {"text_to_video"} else "暂未开放"


def public_operational_capabilities(model_id: str, vendor_caps: dict, vendor_duration: dict | None) -> dict:
    from .model_specs import public_capabilities
    profiles = operational_profiles(model_id)
    image = MODELS.get(model_id, {}).get("kind") == "image"
    def values(field):
        return list(dict.fromkeys(value for row in profiles for value in row.get(field, [])))
    caps = {"input_modes": values("generation_mode"), "ratios": values("ratio"),
            "sizes": values("size"), "resolutions": values("resolution"),
            "duration_s": values("duration"),
            "max_reference_images": 1,
            "supports": [{"text_to_image": "文生图", "image_to_image": "单张图生图",
                          "text_to_video": "文生视频", "image_to_video": "首帧生视频"}[mode]
                         for mode in values("generation_mode")]}
    duration = None if image else {"kind": "enum" if profiles else "unavailable", "min": None,
                                   "max": None, "step": None, "allowed": values("duration") if profiles else None}
    if profiles:
        caps["parameter_combinations"] = profiles
    result = public_capabilities(caps, duration)
    result.update(operational_profiles=profiles,
                  vendor_capabilities=public_capabilities(vendor_caps, vendor_duration),
                  validation=validation_info(model_id), pricing=public_pricing(model_id))
    return result


def operational_problems(model_id: str, kind: str, params: dict) -> list[dict]:
    """Only for a new submission/body; legacy cost/recovery uses vendor rules."""
    from . import config
    from .model_specs import effective_params, validate_combination
    profiles = operational_profiles(model_id) or deepcopy(CANDIDATE_PROFILES.get(model_id, []))
    if not profiles:
        return [{"field": "model_id", "reason": "unverified_model"}]
    if MODELS[model_id]["kind"] != kind:
        return [{"field": "model_id", "reason": "kind_mismatch"}]
    effective = effective_params(kind, params)
    effective.setdefault("generation_mode", "text_to_" + kind)
    if kind == "video" and effective.get("duration") is None:
        effective["duration"] = config.resolve_duration_s(model_id, None, strict=True)
    issues = candidate_parameter_problems(model_id, params)
    issues.extend(validate_combination({"parameter_combinations": profiles}, effective))
    if kind == "image" and "n" in params and (type(params["n"]) is not int or params["n"] != 1):
        issues.append({"field": "n", "got": params["n"], "allowed": [1]})
    return issues


def register(registry: dict, capabilities: dict, activation: dict) -> None:
    for mid, item in MODELS.items():
        image = item["kind"] == "image"
        registry[mid] = {**item, "provider": "aihubmix", "upstream_model_id": mid[len(PREFIX):],
                         "license": "AiHubMix 按量服务；商用条款另行核对",
                         "default_size": 1024 if image else 480 if mid == FAST else 720,
                         "max_size": 2688 if image else 720,
                         "default_steps": None, "note": model_note(mid)}
        # A working Key or Ark acceptance must never enable a new provider.
        activation[mid] = activation_state(mid)
        caps = {
            "input_modes": ["text_to_image", "image_to_image"] if image else
                           ["text_to_video", "image_to_video"],
            "max_reference_images": 1,
            "ratios": ["1:1", "3:4", "4:3", "9:16", "16:9"] if image else
                      ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"],
            "sizes": ["1024x1024", "2048x2048", "1728x2304", "2304x1728", "1536x2688", "2688x1536"] if image else [],
            "resolutions": [] if image else ["480p", "720p"],
            "duration_s": [] if image else [5, 10],
            "supports": ["文生图", "单张图生图"] if image else ["文生视频", "首帧生视频"],
            "pricing": {"currency": "USD", "status": "awaiting_exchange_bound" if mid in USD_PRICING else "unconfirmed",
                        "estimate_only": True, **deepcopy(USD_PRICING.get(mid, {}))},
        }
        if not image:
            caps.update(duration_range_s=[4, 15], duration_step_s=1,
                        parameter_combinations=[{"duration": {"min": 4, "max": 15, "step": 1},
                          "resolution": ["480p", "720p"], "generate_audio": [False, True]}])
        if image and mid in CANDIDATE_PROFILES:
            profiles = CANDIDATE_PROFILES[mid]
            caps["sizes"] = list(dict.fromkeys(row["size"][0] for row in profiles))
            caps["ratios"] = list(dict.fromkeys(row["ratio"][0] for row in profiles))
            # App sizes are mapped to Nano's upstream tier; no exact-size API claim.
            caps["limits"] = {"sizing": "tier_mapping" if mid == NANO else "pixels",
                              "quality": ["medium"] if mid == GPT_IMAGE else []}
            registry[mid]["max_size"] = max(int(part) for size in caps["sizes"] for part in size.split("x"))
        if mid in EXTENSIONS:
            caps = deepcopy(EXTENSIONS[mid].CAPABILITIES[mid])
            caps["pricing"] = public_pricing(mid)
            if image:
                registry[mid]["max_size"] = max(int(part) for size in caps.get("sizes", ["1024x1024"]) for part in size.split("x"))
            else:
                resolutions = caps.get("resolutions") or []
                def pixels(value):
                    return {"1K": 1024, "2K": 2048, "4K": 4096}.get(value, int(value[:-1]) if value.lower().endswith("p") and value[:-1].isdigit() else 720)
                registry[mid]["default_size"] = pixels(resolutions[0]) if resolutions else 720
                registry[mid]["max_size"] = max(map(pixels, resolutions)) if resolutions else 720
        capabilities[mid] = caps


def exchange_bound() -> Decimal | None:
    """Operator's CNY budget conversion bound, not a live exchange rate."""
    try:
        value = Decimal(os.getenv("AIHUBMIX_USD_TO_CNY", "0"))
        return value if value.is_finite() and value > 0 else None
    except ArithmeticError:
        return None


def pricing_ready(model_id: str) -> bool:
    return model_id in USD_PRICING and exchange_bound() is not None


def parameter_problems(kind: str, params: dict) -> list[dict]:
    problems = []
    if kind == "video" and params.get("seed") not in (None, -1):
        problems.append({"field": "seed", "got": params["seed"], "allowed": [-1]})
    for key in ("watermark", "generate_audio"):
        if key in params and type(params[key]) is not bool:
            problems.append({"field": key, "reason": "not_boolean"})
    return problems


def estimate_cost(kind: str, model_id: str, params: dict, *, legacy: bool = False) -> float:
    from . import config
    if model_id not in MODELS or MODELS[model_id]["kind"] != kind:
        raise err("MODEL_CAPABILITY_MISMATCH", "AiHubMix 模型与生成类型不匹配")
    if not pricing_ready(model_id):
        raise err("MODEL_PRICE_UNCONFIRMED", "该模型的价格或预算换算上界尚未核实，暂不可生成")
    policy = USD_PRICING[model_id]
    if model_id in EXTENSIONS:
        dollars = EXTENSIONS[model_id].estimate_usd(model_id, params, legacy=legacy)
    elif kind == "image":
        if model_id == QWEN:
            size = params.get("size") or "1024x1024"
            if not isinstance(size, str) or size not in {row["size"][0] for row in CANDIDATE_PROFILES[QWEN]}:
                raise err("MODEL_PRICE_UNCONFIRMED", "所选图片尺寸没有已核实的费用规则")
            edge = max(map(int, size.split("x")))
            rate = next(row["per_image"] for row in policy["long_edge_tiers"] if edge <= row["max"])
            # Quote the reference for image mode even if its asset is validated later.
            refs = params.get("reference_images", 1 if params.get("generation_mode") == "image_to_image" else 0)
            if type(refs) is not int or refs not in (0, 1):
                raise err("MODEL_PRICE_UNCONFIRMED", "当前仅支持单张参考图的费用估算")
            if params.get("generation_mode") == "image_to_image":
                refs = 1
            if params.get("n", 1) != 1 or type(params.get("n", 1)) is not int:
                raise err("MODEL_PRICE_UNCONFIRMED", "当前每次仅支持一张图片的费用估算")
            dollars = Decimal(rate) + Decimal(policy["per_input_image"]) * refs
        else:
            dollars = Decimal(policy["per_image"])
    else:
        duration = config.resolve_duration_s(model_id, params.get("duration"), strict=True, legacy=legacy)
        rate = policy["per_second"].get(params.get("resolution", "720p"))
        if rate is None:
            raise err("MODEL_PRICE_UNCONFIRMED", "所选清晰度没有已核实的价格")
        dollars = Decimal(rate) * duration
    value = float((dollars * exchange_bound()).quantize(Decimal("0.0001"), rounding=ROUND_UP))
    if not math.isfinite(value) or value <= 0:
        raise err("MODEL_PRICE_UNCONFIRMED", "生成费用估算不可用")
    return value
