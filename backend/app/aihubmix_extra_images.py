"""Unactivated Wan/FLUX image contracts, checked against AiHubMix 2026-10-30.

Only the bounded application subset below is prepared for future acceptance.
These contracts never grant activation, change keys, or send a request.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_CEILING

from .errors import err

PREFIX = "aihubmix:"
WAN = PREFIX + "wan2.7-image-pro"
FLUX = PREFIX + "flux-2-pro"
MODELS = {
    WAN: {"name": "Wan 2.7 Image Pro · AiHubMix", "kind": "image"},
    FLUX: {"name": "FLUX.2 Pro · AiHubMix", "kind": "image"},
}
PROFILES = {
    mid: [{"generation_mode": [mode], "size": ["1024x1024"], "ratio": ["1:1"]}
          for mode in ("text_to_image", "image_to_image")]
    for mid in MODELS
}
CAPABILITIES = {
    mid: {
        "input_modes": ["text_to_image", "image_to_image"],
        "sizes": ["1024x1024"], "ratios": ["1:1"], "resolutions": [], "duration_s": [],
        "supports": ["文生图", "单张图生图"], "max_reference_images": 1,
        "parameter_combinations": PROFILES[mid],
        "limits": {"max_output_images": 1, "sizing": "pixels", "random_seed": "not_exposed"},
    } for mid in MODELS
}
WATERMARK_CONTROLS = {WAN: "explicit_false", FLUX: "not_exposed"}

# Directory lists only generate/megapixel for FLUX. The application rounds each
# output UP to a whole decimal megapixel for a conservative T2I reservation.
# Image-reference billing is not stated: this entry does not license I2I prices.
USD_PRICING = {
    FLUX: {"per_output_megapixel": "0.03", "generation_modes": ["text_to_image"],
           "megapixel_rounding": "ceil", "conservative_upper_estimate": True,
           "reference_image_price": "unconfirmed"},
}
PRICING_NOTES = {
    WAN: {
        "pending_reason": "token_and_reference_cost_bound_unconfirmed",
        "listed_output_per_image_usd": "0.0704",
        "page_input_tokens_per_million_usd": "2",
        "page_output_tokens_per_million_usd": "2",
        "note": "模型页同时列Token价与每张展示价；参考图与思考费用上界未核，不将展示价当整次价格。",
        "sources": ["https://aihubmix.com/api/v1/models",
                    "https://aihubmix.com/model/wan2.7-image-pro"],
    },
    FLUX: {
        "pending_reason": "reference_image_price_unconfirmed",
        "priced_generation_modes": ["text_to_image"],
        "listed_output_per_megapixel_usd": "0.03",
        "note": "仅无参考图文生图按输出MP向上整档预留；图生图输入计费与实际账单仍待核。",
        "sources": ["https://aihubmix.com/api/v1/models",
                    "https://aihubmix.com/model/flux-2-pro"],
    },
}
_ALLOWED_PARAMS = {"prompt", "generation_mode", "size", "ratio", "n", "watermark", "seed",
                   "reference_images", "input_asset_id"}


def parameter_problems(model_id: str, params: dict) -> list[dict]:
    """Validate the app subset; vendor extras cannot be passed through."""
    if model_id not in MODELS:
        return [{"field": "model_id", "reason": "unknown_model"}]
    if not isinstance(params, dict):
        return [{"field": "params", "reason": "not_object"}]
    problems = [{"field": key, "reason": "unsupported_parameter"} for key in params if key not in _ALLOWED_PARAMS]
    mode = params.get("generation_mode", "text_to_image")
    if mode not in ("text_to_image", "image_to_image"):
        problems.append({"field": "generation_mode", "reason": "unsupported_mode"})
    if params.get("size", "1024x1024") != "1024x1024":
        problems.append({"field": "size", "reason": "unsupported_size"})
    if params.get("ratio", "1:1") != "1:1":
        problems.append({"field": "ratio", "reason": "size_ratio_mismatch"})
    if "n" in params and (type(params["n"]) is not int or params["n"] != 1):
        problems.append({"field": "n", "reason": "single_output_only"})
    if "watermark" in params and params["watermark"] is not False:
        problems.append({"field": "watermark", "reason": "watermark_forbidden"})
    if params.get("seed") is not None and (type(params["seed"]) is not int or params["seed"] != -1):
        problems.append({"field": "seed", "reason": "seed_not_exposed"})
    refs = params.get("reference_images")
    if "reference_images" in params:
        if type(refs) is not int or refs not in (0, 1):
            problems.append({"field": "reference_images", "reason": "single_reference_only"})
        elif (mode == "text_to_image" and refs != 0) or (mode == "image_to_image" and refs != 1):
            problems.append({"field": "reference_images", "reason": "mode_reference_conflict"})
    if "input_asset_id" in params and params["input_asset_id"] is not None:
        aid = params["input_asset_id"]
        if not isinstance(aid, str) or not aid.strip():
            problems.append({"field": "input_asset_id", "reason": "invalid_reference_id"})
        elif mode == "text_to_image":
            problems.append({"field": "input_asset_id", "reason": "mode_reference_conflict"})
    if "prompt" in params and (not isinstance(params["prompt"], str) or not params["prompt"].strip()):
        problems.append({"field": "prompt", "reason": "prompt_required"})
    return problems


def body_options(model_id: str, params: dict) -> dict:
    """Return native options except model/prompt/image, which provider adds."""
    if parameter_problems(model_id, params):
        raise err("MODEL_CAPABILITY_MISMATCH", "模型不支持所选图片参数")
    options = {"size": "1024x1024", "n": 1, "async": True, "output_format": "png"}
    if model_id == WAN:
        # Even thinking_mode=False is forbidden with image by the native Schema.
        # Omit it for both modes; never enable a multi-output sequence.
        options["extra"] = {"watermark": False, "enable_sequential": False}
    else:
        # FLUX has no watermark control. False is not a supported extra field.
        options["extra"] = {"prompt_upsampling": False}
    return options


def estimate_usd(model_id: str, params: dict, *, legacy: bool = False) -> Decimal:
    """Bounded output-only FLUX T2I estimate; unconfirmed modes stay closed."""
    if model_id not in MODELS:
        raise err("MODEL_CAPABILITY_MISMATCH", "未知图片模型")
    if model_id == WAN:
        raise err("MODEL_PRICE_UNCONFIRMED", "Wan图片整次调用与参考图费用尚未核实")
    # Explicit image mode or ANY source marker prevents input charges being
    # silently omitted, even if an upstream caller forgot generation_mode.
    refs = params.get("reference_images", 0) if isinstance(params, dict) else None
    if (not isinstance(params, dict) or params.get("generation_mode", "text_to_image") != "text_to_image"
            or type(refs) is not int or refs != 0
            or params.get("input_asset_id") is not None
            or params.get("image") is not None or params.get("images") is not None):
        raise err("MODEL_PRICE_UNCONFIRMED", "FLUX参考图输入计费尚未核实，暂不支持图生图报价")
    if parameter_problems(model_id, params):
        raise err("MODEL_CAPABILITY_MISMATCH", "所选图片规格尚未适配")
    megapixels = Decimal(1024 * 1024) / Decimal(1_000_000)
    units = megapixels.to_integral_value(rounding=ROUND_CEILING)
    return units * Decimal(USD_PRICING[FLUX]["per_output_megapixel"])
