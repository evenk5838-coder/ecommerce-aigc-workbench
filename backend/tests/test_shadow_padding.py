"""Local shadow continuity and product-preservation regressions; no network."""
from __future__ import annotations

import base64
import hashlib
import io
import math
from pathlib import Path
import statistics
import sys
import unittest

from PIL import Image, ImageChops, ImageDraw, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import media_policy


def shadow_source(sigma=9, center=95):
    """Narrow soft shadow crossing the left edge, clear space around the product."""
    source = Image.new("RGB", (128, 128))
    pixels = source.load()
    for y in range(source.height):
        for x in range(source.width):
            shadow = 52 * math.exp(-((x-70)/95)**2 - ((y-center)/sigma)**2)
            level = round(205 + .08*x + .1*y - shadow)
            pixels[x, y] = (level,)*3
    ImageDraw.Draw(source).rectangle((48, 20, 80, 62), fill=(15, 20, 240))
    return source


def prepare(source, ratio="16:9"):
    stream = io.BytesIO()
    source.save(stream, "PNG")
    raw = stream.getvalue()
    value = "data:image/png;base64," + base64.b64encode(raw).decode()
    prepared, metadata = media_policy.prepare_reference(value, ratio)
    output = Image.open(io.BytesIO(base64.b64decode(prepared.split(",", 1)[1]))).convert("RGB")
    return output, metadata, value, raw


def edge_metrics(image, box, side, center, half_width):
    """C0 adjacent luminance jump; C1 difference between inward/outward 8px slopes."""
    left, top, right, bottom = box
    lo, hi = (top, bottom) if side in ("left", "right") else (left, right)
    values, slopes = [], []
    directions = {"left": (-1, 0), "right": (1, 0), "top": (0, -1), "bottom": (0, 1)}
    dx, dy = directions[side]
    for tangent in range(max(lo+8, round(center-half_width)), min(hi-8, round(center+half_width))):
        x, y = {"left": (left, tangent), "right": (right-1, tangent),
                "top": (tangent, top), "bottom": (tangent, bottom-1)}[side]
        luminance = lambda px, py: sum(image.getpixel((px, py))) / 3
        edge = luminance(x, y)
        values.append(abs(luminance(x+dx, y+dy) - edge))
        outside_slope = (luminance(x+dx*8, y+dy*8) - edge) / 8
        inside_slope = (edge - luminance(x-dx*8, y-dy*8)) / 8
        slopes.append(abs(outside_slope-inside_slope))
    assert values and slopes
    return {"jump_p95": sorted(values)[int(len(values)*.95)],
            "slope_mean": statistics.mean(slopes),
            "slope_p95": sorted(slopes)[int(len(slopes)*.95)]}


class ShadowPaddingTests(unittest.TestCase):
    def assert_original_preserved(self, source, output, metadata, value, raw):
        available = tuple(side * (100-2*media_policy.MARGIN_PERCENT)//100 for side in output.size)
        fitted = ImageOps.contain(source, available, Image.Resampling.LANCZOS)
        self.assertEqual(output.crop(metadata["content_box"]).tobytes(), fitted.tobytes())
        self.assertEqual(metadata["source_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(base64.b64decode(value.split(",", 1)[1]), raw)

    def assert_shadow_continuous(self, metrics):
        # Byte quantization can produce an adjacent step of one gray level.
        # Three gray levels over the 8px normal band are tolerated at P95.
        self.assertLessEqual(metrics["jump_p95"], 1.5, metrics)
        self.assertLessEqual(metrics["slope_p95"], .375, metrics)
        self.assertLessEqual(metrics["slope_mean"], .2, metrics)

    def test_touching_soft_shadows_preserve_original_and_local_value_and_normal_slope(self):
        for sigma, center in ((6, 87), (9, 95), (14, 92)):
            with self.subTest(sigma=sigma, center=center):
                source = shadow_source(sigma, center)
                original_pixels = source.tobytes()
                output, metadata, value, raw = prepare(source)
                self.assert_original_preserved(source, output, metadata, value, raw)
                self.assertEqual(source.tobytes(), original_pixels)
                left, top, right, bottom = metadata["content_box"]
                scale = (bottom-top) / source.height
                metrics = edge_metrics(output, (left, top, right, bottom), "left",
                                       top+center*scale, sigma*scale*2)
                self.assert_shadow_continuous(metrics)

    def test_soft_shadow_continuity_applies_at_each_border(self):
        source = shadow_source()
        variants = [(source, "left", 95),
                    (source.transpose(Image.Transpose.ROTATE_90), "bottom", 95),
                    (source.transpose(Image.Transpose.ROTATE_180), "right", 127-95),
                    (source.transpose(Image.Transpose.ROTATE_270), "top", 127-95)]
        for image, side, center in variants:
            with self.subTest(side=side):
                output, metadata, value, raw = prepare(image)
                self.assert_original_preserved(image, output, metadata, value, raw)
                left, top, right, bottom = metadata["content_box"]
                origin, length = (top, bottom-top) if side in ("left", "right") else (left, right-left)
                scale = length / 128
                self.assert_shadow_continuous(edge_metrics(
                    output, metadata["content_box"], side, origin+center*scale, 18*scale))

    def test_texture_and_contrast_edges_do_not_duplicate_product_or_create_extreme_bands(self):
        for pattern in ("texture", "contrast"):
            with self.subTest(pattern=pattern):
                source = Image.new("RGB", (128, 128))
                pixels = source.load()
                for y in range(128):
                    for x in range(128):
                        depth = min(x, y, 127-x, 127-y)
                        if pattern == "texture":
                            level = round(140+18*math.sin(x*.9)+16*math.cos(y*.8))
                        else:
                            level = 48 if depth < 4 else 208 if depth < 12 else 140
                        pixels[x, y] = (level,)*3
                ImageDraw.Draw(source).rectangle((45, 30, 83, 95), fill=(15, 20, 240))
                output, metadata, value, raw = prepare(source)
                self.assert_original_preserved(source, output, metadata, value, raw)
                box = metadata["content_box"]
                fitted = output.crop(box)
                edges = [fitted.crop(b) for b in ((0,0,1,fitted.height),
                         (fitted.width-1,0,fitted.width,fitted.height),
                         (0,0,fitted.width,1), (0,fitted.height-1,fitted.width,fitted.height))]
                edge_extrema = [(min(im.getextrema()[c][0] for im in edges),
                                 max(im.getextrema()[c][1] for im in edges)) for c in range(3)]
                left, top, right, bottom = box
                for outside in ((0,0,left,output.height), (right,0,output.width,output.height),
                                (left,0,right,top), (left,bottom,right,output.height)):
                    padding = output.crop(outside)
                    red, green, blue = padding.split()
                    self.assertLess(ImageChops.subtract(blue, red).getextrema()[1], 80)
                    self.assertLess(ImageChops.subtract(blue, green).getextrema()[1], 80)
                    for channel, (minimum, maximum) in enumerate(padding.getextrema()):
                        low, high = edge_extrema[channel]
                        # A small lighting continuation is acceptable; a new
                        # >32/255 halo/band beyond all edge colors is not.
                        self.assertGreaterEqual(minimum, max(0, low-32))
                        self.assertLessEqual(maximum, min(255, high+32))


if __name__ == "__main__":
    unittest.main()
