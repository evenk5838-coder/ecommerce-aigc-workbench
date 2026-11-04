"""Product output policy: explicit no-watermark requests and uncropped inputs.

Reference preparation is local and deterministic. It preserves the entire
image and adds room around it; it does not erase marks or reconstruct pixels
already missing from an input. Model adherence still needs real acceptance.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import math
import re

from PIL import Image, ImageFilter, ImageOps

from .errors import err

VERSION = "no-watermark-contain-v1"
VIDEO_VERSION = "no-watermark-edge-gradient-v3"
MARGIN_PERCENT = 10
FRAMING_INSTRUCTION = (
    "【商品构图要求】以完整商品的中远景为准，全程保留顶部、底部和左右边缘，"
    "主体高度约占画面50%，不得超过60%，上下左右至少留15%的安全空间。"
    "起始帧和结束帧都必须保持完整轮廓，不得切掉商品边缘。"
    "保持参考画面的完整范围和物体比例，不做填满画面的裁切或拉伸。"
    "如需推进镜头，从更远的景别开始，整个片段放大幅度不超过5%，结束时仍符合上述占比与留白；"
    "不得推进成特写，运镜与完整展示冲突时优先完整展示。"
)


def specification(kind: str) -> dict:
    result = {"version": VERSION, "watermark": False}
    if kind == "video":
        result.update(version=VIDEO_VERSION, reference_fit="contain",
                      reference_background="edge_gradient_extend", safe_margin_percent=MARGIN_PERCENT,
                      framing="full_subject", max_subject_height_percent=60,
                      min_subject_edge_margin_percent=15, max_camera_scale_change_percent=5,
                      framing_is_model_guidance=True)
    return result


def parameter_problems(params: dict) -> list[dict]:
    if "watermark" in params and params["watermark"] is not False:
        return [{"field": "watermark", "got": params["watermark"], "allowed": [False],
                 "reason": "watermark_forbidden"}]
    if isinstance(params.get("prompt"), str) and re.search(
            r"--(?:wm|watermark)(?:\s+|=)(?:true|1)(?=\s|$)", params["prompt"], re.I):
        return [{"field": "prompt", "reason": "watermark_forbidden"}]
    return []


def require_no_watermark(value=False) -> None:
    if value is not False:
        raise err("MODEL_CAPABILITY_MISMATCH", "工作台仅允许无水印生成，请关闭水印后重新确认")


def require_safe_prompt(prompt: str) -> None:
    if parameter_problems({"prompt": prompt}):
        raise err("MODEL_CAPABILITY_MISMATCH", "提示词不能包含开启水印的参数")


def video_prompt(prompt: str) -> str:
    if not isinstance(prompt, str):
        raise err("VALIDATION_FAILED", "提示词格式不正确")
    return prompt if FRAMING_INSTRUCTION in prompt else prompt.rstrip() + "\n\n" + FRAMING_INSTRUCTION


def canvas_size(ratio: str) -> tuple[int, int]:
    if ratio not in ("1:1", "16:9", "9:16", "4:3", "3:4", "21:9"):
        raise err("MODEL_CAPABILITY_MISMATCH", "参考图暂不支持该视频比例")
    a, b = map(int, ratio.split(":"))
    divisor = math.gcd(a, b)
    a, b = a // divisor, b // divisor
    # Exact target ratio, both sides multiples of 16, long side around 1280px.
    unit = math.ceil(1280 / (max(a, b) * 16)) * 16
    return a * unit, b * unit


def _extend_background(fitted: Image.Image, target: tuple[int, int],
                       position: tuple[int, int]) -> Image.Image:
    """Extend edge colors, smoothing only the padding; keep the fitted ROI exact.

    No mirrored/resized copy of the whole product is used as a background.
    This is color extension, not semantic outpainting of missing scenery.
    """
    width, height = target
    left, top = position
    right, bottom = left + fitted.width, top + fitted.height
    canvas = Image.new("RGB", target)
    for box, point in [((0, 0, left, top), (0, 0)),
                       ((right, 0, width, top), (fitted.width - 1, 0)),
                       ((0, bottom, left, height), (0, fitted.height - 1)),
                       ((right, bottom, width, height), (fitted.width - 1, fitted.height - 1))]:
        if box[0] < box[2] and box[1] < box[3]:
            canvas.paste(fitted.getpixel(point), box)
    for box, size, at in [((0, 0, 1, fitted.height), (left, fitted.height), (0, top)),
                         ((fitted.width - 1, 0, fitted.width, fitted.height),
                          (width - right, fitted.height), (right, top)),
                         ((0, 0, fitted.width, 1), (fitted.width, top), (left, 0)),
                         ((0, fitted.height - 1, fitted.width, fitted.height),
                          (fitted.width, height - bottom), (left, bottom))]:
        if min(size) > 0:
            canvas.paste(fitted.crop(box).resize(size, Image.Resampling.BILINEAR), at)
    canvas.paste(fitted, position)
    radius = max(1, round(max(target) * 3 / 320))
    for _ in range(12):
        canvas = canvas.filter(ImageFilter.GaussianBlur(radius))
        canvas.paste(fitted, position)
    return _match_edge_gradient(canvas, fitted, position)


def _match_edge_gradient(canvas: Image.Image, fitted: Image.Image,
                         position: tuple[int, int]) -> Image.Image:
    """Match normal brightness slopes in an outside-only transition band.

    Slopes and total brightness changes are bounded, decay with distance and
    blend back into the smoothed background. Tangential smoothing grows away
    from the boundary so edge noise is not repeated as long strips. This does
    not reconstruct a physically correct shadow or modify original pixels.
    """
    (left, top) = position
    (right, bottom) = (left + fitted.width, top + fitted.height)
    (width, height) = canvas.size
    collar = max(2, round(max(canvas.size) * 0.075))
    decay = max(1, collar / 3)
    estimate = fitted.filter(ImageFilter.GaussianBlur(1))
    px = estimate.load()
    out = canvas.copy()
    pixels = out.load()

    def lum(color):
        return sum(color) / 3

    def slope(samples):
        n = len(samples)
        near = min(4, max(1, n // 4))
        far = min(8, max(1, n // 3))
        if n <= 1:
            return 0.0
        a = sum((lum(samples[i]) for i in range(near))) / near
        b = sum((lum(samples[i]) for i in range(n - far, n))) / far
        distance = n - far + (far - 1) / 2 - (near - 1) / 2
        return max(-1.25, min(1.25, (a - b) / max(1, distance)))
    band_x = min(24, fitted.width)
    band_y = min(24, fitted.height)
    gradients_left = [slope([px[x, y] for x in range(band_x)]) for y in range(fitted.height)]
    gradients_right = [slope([px[fitted.width - 1 - x, y] for x in range(band_x)]) for y in range(fitted.height)]
    gradients_top = [slope([px[x, y] for y in range(band_y)]) for x in range(fitted.width)]
    gradients_bottom = [slope([px[x, fitted.height - 1 - y] for y in range(band_y)]) for x in range(fitted.width)]

    def prefix(values):
        out = [0.0]
        for v in values:
            out.append(out[-1] + v)
        return out
    (glp, grp, gtp, gbp) = map(prefix, (gradients_left, gradients_right, gradients_top, gradients_bottom))

    def gradient(values, index, distance):
        radius = min(24, round(distance / 3))
        lo = max(0, index - radius)
        hi = min(len(values) - 1, index + radius + 1)
        return (values[hi] - values[lo]) / (hi - lo)
    levels = (0, 1, 2, 4, 8, 16, 24)

    def profiles(box):
        edge = fitted.crop(box)

        def values(im):
            return [im.getpixel((0, y)) for y in range(im.height)] if im.width == 1 else [im.getpixel((x, 0)) for x in range(im.width)]
        return [values(edge.filter(ImageFilter.GaussianBlur(level))) if level else values(edge) for level in levels]
    left_profiles = profiles((0, 0, 1, fitted.height))
    right_profiles = profiles((fitted.width - 1, 0, fitted.width, fitted.height))
    top_profiles = profiles((0, 0, fitted.width, 1))
    bottom_profiles = profiles((0, fitted.height - 1, fitted.width, fitted.height))

    def edge_color(values, index, distance):
        sigma = min(levels[-1], distance / 4)
        for n in range(1, len(levels)):
            if sigma <= levels[n]:
                factor = (sigma - levels[n - 1]) / (levels[n] - levels[n - 1])
                return tuple((a * (1 - factor) + b * factor for (a, b) in zip(values[n - 1][index], values[n][index])))
        return values[-1][index]
    for y in range(max(0, top - collar), min(height, bottom + collar)):
        sy = max(0, min(fitted.height - 1, y - top))
        dy = top - y if y < top else y - bottom + 1 if y >= bottom else 0
        vy = decay * (1 - math.exp(-dy / decay))
        gy = gtp if y < top else gbp
        for x in range(max(0, left - collar), min(width, right + collar)):
            if left <= x < right and top <= y < bottom:
                continue
            sx = max(0, min(fitted.width - 1, x - left))
            dx = left - x if x < left else x - right + 1 if x >= right else 0
            d = math.hypot(dx, dy)
            if d >= collar:
                continue
            gx = glp if x < left else grp
            delta = (gradient(gx, sy, dx) * decay * (1 - math.exp(-dx / decay)) if dx else 0) + (gradient(gy, sx, dy) * vy if dy else 0)
            delta = max(-48, min(48, delta))
            u = d / collar
            weight = 1 - 3 * u * u + 2 * u * u * u
            if dx and dy:
                horizontal = edge_color(left_profiles if x < left else right_profiles, sy, d)
                vertical = edge_color(top_profiles if y < top else bottom_profiles, sx, d)
                edge = tuple(((a + b) / 2 for (a, b) in zip(horizontal, vertical)))
            elif dx:
                edge = edge_color(left_profiles if x < left else right_profiles, sy, dx)
            else:
                edge = edge_color(top_profiles if y < top else bottom_profiles, sx, dy)
            base = pixels[x, y]
            predicted = tuple((max(0, min(255, c + delta)) for c in edge))
            pixels[x, y] = tuple((round(a * (1 - weight) + b * weight) for (a, b) in zip(base, predicted)))
    return out


def prepare_reference(reference: str, ratio: str) -> tuple[str, dict]:
    """EXIF-aware contain fit with edge-color extension and 10% safe room."""
    target = canvas_size(ratio)
    try:
        if not isinstance(reference, str) or not reference.startswith("data:image/"):
            raise ValueError("local image required")
        header, payload = reference.split(",", 1)
        if not header.endswith(";base64"):
            raise ValueError("base64 required")
        raw = base64.b64decode(payload, validate=True)
        with Image.open(io.BytesIO(raw)) as source:
            if getattr(source, "n_frames", 1) != 1:
                raise ValueError("single still frame required")
            source.load()
            image = ImageOps.exif_transpose(source).convert("RGBA")
    except (ValueError, TypeError, OSError, binascii.Error, Image.DecompressionBombError):
        raise err("INVALID_REFERENCE_IMAGE", "参考图片无法用于视频，请上传可读取的静态图片") from None
    width, height = image.size
    if not width or not height:
        raise err("INVALID_REFERENCE_IMAGE", "参考图片尺寸不正确")
    # Alpha pixels contribute white, rather than hidden RGB values. Nothing is
    # cropped and the original file/asset metadata are never modified.
    background = Image.new("RGBA", image.size, (255, 255, 255, 255))
    background.alpha_composite(image)
    image = background.convert("RGB")
    available = tuple(max(1, side * (100 - 2 * MARGIN_PERCENT) // 100) for side in target)
    fitted = ImageOps.contain(image, available, Image.Resampling.LANCZOS)
    position = ((target[0] - fitted.width) // 2, (target[1] - fitted.height) // 2)
    canvas = _extend_background(fitted, target, position)
    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG")
    data = buffer.getvalue()
    metadata = {"policy": VIDEO_VERSION, "source_sha256": hashlib.sha256(raw).hexdigest(),
                "prepared_sha256": hashlib.sha256(data).hexdigest(), "source_size": [width, height],
                "target_size": list(target), "content_box": [*position, position[0] + fitted.width,
                                                             position[1] + fitted.height],
                "fit": "contain", "background_fill": "edge_gradient_extend",
                "edge_gradient_transition": True,
                "safe_margin_percent": MARGIN_PERCENT}
    return "data:image/png;base64," + base64.b64encode(data).decode(), metadata
