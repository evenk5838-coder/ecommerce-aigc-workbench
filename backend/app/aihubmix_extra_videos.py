"""Prepared AiHubMix video contracts; none are real-sample/activation records.

Sources: retained 2026-10-30 native endpoint schemas and pricing snapshots in
``docs/evidence/v0.11-八款模型扩充-后端-20261030``. The deliberately small app
subset excludes audio/video references, last frames, editing and automatic
length. An API field or a cost estimate is not evidence of media quality.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal

from . import media_policy
from .errors import err

PREFIX = "aihubmix:"
SEEDANCE_25 = PREFIX + "doubao-seedance-2-5-260628"
MINIMAX_H3 = PREFIX + "minimax-h3"
WAN_3 = PREFIX + "wan3.0-video"
KLING_V3 = PREFIX + "kling-v3-omni"

MODELS = {
    SEEDANCE_25: {"name": "Seedance 2.5 · AiHubMix", "kind": "video"},
    MINIMAX_H3: {"name": "MiniMax H3 · AiHubMix", "kind": "video"},
    WAN_3: {"name": "Wan 3.0 Video · AiHubMix", "kind": "video"},
    KLING_V3: {"name": "可灵 V3 Omni · AiHubMix", "kind": "video"},
}
DEFAULT_RESOLUTIONS = {SEEDANCE_25: "480p", MINIMAX_H3: "768P", WAN_3: "480p", KLING_V3: "720p"}
PROFILES = {
    mid: [{"generation_mode": [mode], "duration": [5], "ratio": ["16:9"],
           "resolution": [resolution], "generate_audio": [False]}
          for mode in ("text_to_video", "image_to_video")]
    for mid, resolution in DEFAULT_RESOLUTIONS.items()
}
WATERMARK_CONTROLS = {
    SEEDANCE_25: "explicit_false", MINIMAX_H3: "not_exposed",
    WAN_3: "explicit_false", KLING_V3: "watermark_info_enabled_false",
}

CAPABILITIES = {
    mid: {"input_modes": ["text_to_video", "image_to_video"],
          "ratios": ["16:9"], "resolutions": [resolution], "sizes": [],
          "duration_s": [5], "max_reference_images": 1,
          "supports": ["文生视频", "首帧生视频"],
          "parameter_combinations": deepcopy(PROFILES[mid]),
          "limits": {"default_resolution": resolution, "default_ratio": "16:9",
                     "default_duration": 5, "audio_control": "not_exposed" if mid == MINIMAX_H3 else "explicit_false",
                     "watermark_control": WATERMARK_CONTROLS[mid],
                     "candidate_contract_only": True}}
    for mid, resolution in DEFAULT_RESOLUTIONS.items()
}
CAPABILITIES[SEEDANCE_25]["limits"].update(
    first_frame_aspect_ratio="adaptive_from_prepared_16_9_reference",
    pricing_requirement="verified_video_token_bound")
CAPABILITIES[MINIMAX_H3]["limits"].update(
    native_silent_output_verified=False,
    audio_note="原生Schema未提供音轨开关；无声仅为候选意图，实际音轨待验，不承诺原生静音。",
    max_prompt_length=7000)
CAPABILITIES[KLING_V3]["limits"]["upstream_mode"] = "std"

# Catalog token-rate unit is ambiguous; the model page explicitly says per
# million video tokens. No token/pixel/frame upper bound is verified here.
# Never reinterpret this as dollars per second.
USD_PRICING = {
    MINIMAX_H3: {
        "per_second": {"768P": "0.07744"}, "per_input_image": "0.0282",
        "conservative_upper_estimate": True,
        "pricing_discrepancy": {
            "catalog_input_image": "0.0282",
            "page_input_image_free_count": 5,
            "policy": "reserve_first_reference_image_until_bill_verified"}},
    WAN_3: {"per_second": {"480p": "0.04225"}},
}
PRICING_NOTES = {
    SEEDANCE_25: {
        "status": "unconfirmed", "reason": "video_token_cost_bound_unconfirmed",
        "usd_per_million_video_tokens_without_video_input": "11.77",
        "scope": "本批480p、不输入视频；不能把token价当秒价，需先确认单次费用上界。",
        "source": "https://aihubmix.com/model/doubao-seedance-2-5-260628"},
    MINIMAX_H3: {
        "status": "conservative_estimate", "estimate_only": True,
        "reason": "input_image_free_threshold_disagrees_with_catalog",
        "audio_control": "not_exposed", "watermark_control": "not_exposed",
        "source": "https://aihubmix.com/model/minimax-h3"},
    WAN_3: {"status": "list_estimate", "estimate_only": True,
            "source": "https://aihubmix.com/model/wan3.0-video"},
    KLING_V3: {
        "status": "unconfirmed", "estimate_only": True,
        "reason": "input_token_and_generation_cost_bound_unconfirmed",
        "listed_std_generation_per_second_usd": "0.0845",
        "listed_std_reference_per_second_usd": "0.1268",
        "page_input_tokens_per_million_usd": "2",
        "note": "模型页同时列输入Token价与生成/参考秒价；llms.txt只指向模型页，未明确秒价是否含全部输入费用，暂不返回整次可执行报价。",
        "sources": ["https://aihubmix.com/api/v1/models",
                    "https://aihubmix.com/model/kling-v3-omni",
                    "https://aihubmix.com/model/kling-v3-omni/llms.txt"]},
}

# Shared app fields are accepted, but vendor escape hatches are never forwarded.
# Keeping the list explicit makes misspellings and unsupported settings fail
# before transport instead of silently disappearing from the actual request.
APP_FIELDS = {"prompt", "generation_mode", "duration", "ratio", "resolution", "generate_audio",
              "watermark", "seed", "input_asset_id", "reference_images"}


def parameter_problems(model_id: str, params: dict) -> list[dict]:
    if model_id not in MODELS:
        return [{"field": "model_id", "reason": "unknown_model"}]
    if not isinstance(params, dict):
        return [{"field": "params", "reason": "not_object"}]
    problems = [{"field": field, "reason": "unsupported_parameter"}
                for field in sorted(set(params) - APP_FIELDS)]
    mode = params.get("generation_mode", "text_to_video")
    if mode not in ("text_to_video", "image_to_video"):
        problems.append({"field": "generation_mode", "reason": "unsupported_mode"})
    if type(params.get("duration", 5)) is not int or params.get("duration", 5) != 5:
        problems.append({"field": "duration", "allowed": [5], "reason": "unsupported_duration"})
    if params.get("ratio", "16:9") != "16:9":
        problems.append({"field": "ratio", "allowed": ["16:9"], "reason": "unsupported_ratio"})
    if params.get("resolution", DEFAULT_RESOLUTIONS[model_id]) != DEFAULT_RESOLUTIONS[model_id]:
        problems.append({"field": "resolution", "allowed": [DEFAULT_RESOLUTIONS[model_id]],
                         "reason": "unsupported_resolution"})
    for flag in ("generate_audio", "watermark"):
        if flag in params and params[flag] is not False:
            problems.append({"field": flag, "allowed": [False], "reason": "unsupported_flag"})
    if params.get("seed") is not None and (type(params["seed"]) is not int or params["seed"] != -1):
        problems.append({"field": "seed", "allowed": [-1], "reason": "unsupported_seed"})
    refs = params.get("reference_images")
    if refs is not None and (type(refs) is not int or refs not in (0, 1)):
        problems.append({"field": "reference_images", "reason": "single_reference_only"})
    prompt = params.get("prompt")
    if prompt is not None and (not isinstance(prompt, str) or not prompt.strip()):
        problems.append({"field": "prompt", "reason": "invalid_prompt"})
    if model_id == MINIMAX_H3 and isinstance(prompt, str) and len(media_policy.video_prompt(prompt)) > 7000:
        problems.append({"field": "prompt", "reason": "too_long_after_framing_guidance"})
    return problems


def body_options(model_id: str, params: dict, reference: str | None = None) -> dict:
    if parameter_problems(model_id, params):
        raise err("MODEL_CAPABILITY_MISMATCH", "模型不支持所选视频参数")
    mode = params.get("generation_mode", "text_to_video")
    if mode == "image_to_video" and (not isinstance(reference, str) or not reference):
        raise err("REFERENCE_IMAGE_REQUIRED", "请提供首帧参考图片")
    body = {"duration": 5, "aspect_ratio": "16:9", "resolution": DEFAULT_RESOLUTIONS[model_id]}
    if model_id == MINIMAX_H3:
        # No documented audio or watermark field. Do not invent an extra to
        # claim that an output has no audio/watermark before real acceptance.
        pass
    elif model_id == KLING_V3:
        body.update(generate_audio=False,
                    extra={"mode": "std", "multi_shot": False, "watermark_info": {"enabled": False}})
    else:
        body.update(generate_audio=False, extra={"watermark": False})
        if model_id == WAN_3:
            body["extra"]["prompt_extend"] = False
    if mode == "image_to_video":
        body["frame_images"] = [{"frame_type": "first_frame", "image_url": {"url": reference}}]
        if model_id == SEEDANCE_25:
            # This model requires adaptive when frame_images is present. The
            # caller has prepared a complete, uncropped exact-16:9 reference.
            body["aspect_ratio"] = "adaptive"
    return body


def estimate_usd(model_id: str, params: dict, *, legacy: bool = False) -> Decimal:
    # legacy is reserved for a future retained task. None of these newly
    # prepared models has historical accepted contracts to relax today.
    del legacy
    if model_id not in MODELS:
        raise err("MODEL_CAPABILITY_MISMATCH", "未知的视频模型")
    if model_id not in USD_PRICING:
        raise err("MODEL_PRICE_UNCONFIRMED", "尚未确认该视频模型的单次费用上界")
    if parameter_problems(model_id, params):
        raise err("MODEL_PRICE_UNCONFIRMED", "所选视频参数没有已核实的费用规则")
    mode = params.get("generation_mode", "text_to_video")
    refs = params.get("reference_images", 1 if mode == "image_to_video" or params.get("input_asset_id") else 0)
    if type(refs) is not int or refs not in (0, 1):
        raise err("MODEL_PRICE_UNCONFIRMED", "当前仅支持单张首帧参考图的费用估算")
    if mode == "image_to_video" or params.get("input_asset_id"):
        refs = 1
    policy = USD_PRICING[model_id]
    resolution = params.get("resolution", DEFAULT_RESOLUTIONS[model_id])
    rate = policy.get("per_second_with_reference", {}).get(resolution) if refs else None
    rate = rate or policy["per_second"][resolution]
    return Decimal(rate) * 5 + Decimal(policy.get("per_input_image", "0")) * refs
