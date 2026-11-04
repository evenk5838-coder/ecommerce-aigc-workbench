"""Declarative parameter combinations; no provider, price or activation changes."""
from __future__ import annotations

from copy import deepcopy
import math
import re

FIELDS = {"generation_mode", "ratio", "resolution", "size", "duration", "generate_audio"}
IMAGE_PRESET_RATIOS = {"2048x2048": "1:1", "1728x2304": "3:4", "2304x1728": "4:3",
                       "1536x2688": "9:16", "2688x1536": "16:9"}


def image_ratio(size: object) -> str | None:
    """Use the same pixel-aligned display ratios as existing canvas controls."""
    match = re.fullmatch(r"([1-9][0-9]{0,5})x([1-9][0-9]{0,5})", size) if isinstance(size, str) else None
    if not match:
        return None
    width, height = map(int, match.groups())
    divisor = math.gcd(width, height)
    return IMAGE_PRESET_RATIOS.get(size, f"{width // divisor}:{height // divisor}")


def effective_params(kind: str, params: dict, *, mode: str | None = None) -> dict:
    """Copy actual execution defaults without coercing explicit invalid values."""
    result = dict(params)
    if mode is not None:
        result["generation_mode"] = mode
    if kind == "image":
        result.setdefault("generation_mode", "text_to_image")
        result.setdefault("size", "1024x1024")
        ratio = image_ratio(result["size"])
        if ratio is not None:
            result.setdefault("ratio", ratio)
    else:
        result.setdefault("ratio", "9:16")
        result.setdefault("resolution", "720p")
        result.setdefault("generate_audio", False)
    return result


def combinations_valid(caps: dict) -> bool:
    if "parameter_combinations" not in caps:
        return True  # Existing models keep their independent parameter policies.
    rules = caps["parameter_combinations"]
    if not isinstance(rules, list) or not 1 <= len(rules) <= 100:
        return False
    for rule in rules:
        if not isinstance(rule, dict) or not rule or set(rule) - FIELDS:
            return False
        for field, values in rule.items():
            if field == "duration" and isinstance(values, dict):
                if set(values) != {"min", "max", "step"}:
                    return False
                low, high, step = (values[key] for key in ("min", "max", "step"))
                if not all(type(value) is int for value in (low, high, step)):
                    return False
                if low <= 0 or high < low or step <= 0 or (high - low) % step:
                    return False
            else:
                if not isinstance(values, list) or not values:
                    return False
                if field == "duration":
                    if any(type(value) is not int or value <= 0 for value in values):
                        return False
                elif field == "generate_audio":
                    if any(type(value) is not bool for value in values):
                        return False
                elif any(not isinstance(value, str) or not value.strip() for value in values):
                    return False
    return True


def validate_combination(caps: dict, params: dict) -> list[dict]:
    if not combinations_valid(caps):
        return [{"field": "parameter_combinations", "reason": "configuration_invalid"}]
    if "parameter_combinations" not in caps:
        return []

    def matches(rule: dict) -> bool:
        for field, allowed in rule.items():
            value = params.get(field)
            if isinstance(allowed, dict):
                if type(value) is not int or not allowed["min"] <= value <= allowed["max"]:
                    return False
                if (value - allowed["min"]) % allowed["step"]:
                    return False
            elif not any(type(value) is type(item) and value == item for item in allowed):
                return False
        return True

    if any(matches(rule) for rule in caps["parameter_combinations"]):
        return []
    return [{"field": "parameter_combination", "reason": "unsupported_combination",
             "got": {key: params[key] for key in sorted(FIELDS) if key in params},
             "allowed": deepcopy(caps["parameter_combinations"])}]


def public_capabilities(caps: dict, duration: dict | None) -> dict:
    # Shared by the model API and Agent; preserve the existing response shape.
    result = {key: deepcopy(caps.get(key, [])) for key in (
        "input_modes", "ratios", "resolutions", "sizes", "duration_s", "supports")}
    result.update({key: deepcopy(caps.get(key)) for key in (
        "duration_range_s", "duration_step_s", "max_reference_images", "fps", "limits")})
    result["duration_policy"] = deepcopy(duration)
    if "parameter_combinations" in caps:
        result["parameter_combinations"] = deepcopy(caps["parameter_combinations"])
    return result
