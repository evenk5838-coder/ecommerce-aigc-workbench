"""Reproducible local padding evidence; no model/network or business DB access."""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import statistics
import sys
import time

from PIL import Image, ImageChops, ImageDraw, ImageOps

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app import media_policy as policy


def data_uri(image):
    output = io.BytesIO()
    image.save(output, "PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode()


def decode(uri):
    return Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1]))).convert("RGB")


def jumps(image, box):
    left, top, right, bottom = box
    pairs = [((left-1, y), (left, y)) for y in range(top, bottom)]
    pairs += [((right-1, y), (right, y)) for y in range(top, bottom)]
    pairs += [((x, top-1), (x, top)) for x in range(left, right)]
    pairs += [((x, bottom-1), (x, bottom)) for x in range(left, right)]
    values = [abs(a-b) for first, second in pairs
              for a, b in zip(image.getpixel(first), image.getpixel(second))]
    return {"mean": statistics.mean(values), "p95": sorted(values)[int(.95*len(values))],
            "max": max(values)}


def run(evidence):
    evidence.mkdir(parents=True, exist_ok=True)
    report = {"image_policy_version": policy.VERSION, "video_policy_version": policy.VIDEO_VERSION,
              "model_requests": 0, "cost_cny": 0, "ratios": []}
    for ratio in ("16:9", "9:16", "1:1", "4:3", "3:4", "21:9"):
        source = Image.new("RGB", (128, 128), (175, 181, 190))
        ImageDraw.Draw(source).rectangle((43, 38, 85, 94), fill=(0, 0, 240))
        value, metadata = policy.prepare_reference(data_uri(source), ratio)
        output = decode(value)
        left, top, right, bottom = metadata["content_box"]
        available = tuple(side * (100 - 2 * policy.MARGIN_PERCENT)//100 for side in output.size)
        fitted = ImageOps.contain(source, available, Image.Resampling.LANCZOS)
        assert output.crop((left, top, right, bottom)).tobytes() == fitted.tobytes()
        for outside in [(0, 0, left, output.height), (right, 0, output.width, output.height),
                        (left, 0, right, top), (left, bottom, right, output.height)]:
            red, green, blue = output.crop(outside).split()
            assert ImageChops.subtract(blue, red).getextrema()[1] < 80
            assert ImageChops.subtract(blue, green).getextrema()[1] < 80
        gradient = Image.new("RGB", (128, 128))
        for y in range(128):
            for x in range(128):
                value = round(155 + x * .2 + y * .3)
                gradient.putpixel((x, y), (value,)*3)
        value, gradient_metadata = policy.prepare_reference(data_uri(gradient), ratio)
        continuity = jumps(decode(value), gradient_metadata["content_box"])
        assert continuity["mean"] < 2 and continuity["p95"] <= 4
        report["ratios"].append({"ratio": ratio, "fitted_pixels_exact": True,
                                  "duplicate_subject_in_padding": False, "gradient_boundary": continuity})
    previous = ROOT / "docs/evidence/v0.11-AiHubMix无水印复验-20261030"
    source_path = previous / "text_to_image.jpg"
    if source_path.is_file() and (previous / "prepared-first-frame.png").is_file():
        raw = source_path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        started = time.monotonic()
        uri, metadata = policy.prepare_reference(
            "data:image/jpeg;base64," + base64.b64encode(raw).decode(), "16:9")
        elapsed = time.monotonic() - started
        output = decode(uri)
        old = Image.open(previous / "prepared-first-frame.png").convert("RGB")
        assert old.size == output.size
        box = metadata["content_box"]
        assert old.crop(box).tobytes() == output.crop(box).tobytes()
        before, after = jumps(old, box), jumps(output, box)
        assert after["mean"] < before["mean"] * .3
        assert hashlib.sha256(source_path.read_bytes()).hexdigest() == digest
        old.save(evidence / "旧版纯色补边.png")
        output.save(evidence / "新版边缘延展.png")
        comparison = Image.new("RGB", (1280, 1488), "#181818")
        draw = ImageDraw.Draw(comparison)
        draw.text((12, 6), "BEFORE - flat border / visible rectangle", fill="white")
        comparison.paste(old, (0, 24))
        draw.text((12, 750), "AFTER - edge extension / original product pixels preserved", fill="white")
        comparison.paste(output, (0, 768))
        comparison.save(evidence / "补边前后对比.jpg", quality=94)
        report["real_cup"] = {"source_sha256": digest, "source_unchanged": True,
                              "fitted_pixels_exact": True, "old_boundary": before, "new_boundary": after,
                              "elapsed_seconds": round(elapsed, 4), "preparation": metadata}
    else:
        report["real_cup"] = {"status": "not_run", "reason": "previous local acceptance sample absent"}
    report["limitations"] = ["Color extension is not semantic outpainting of complex backgrounds.",
                               "Free tests cannot prove future video model adherence to framing guidance."]
    (evidence / "offline-framing.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print("PASS: six ratios, unchanged fitted pixels, no duplicated subject and continuous gradient padding")
    print("Real sample:", json.dumps(report["real_cup"], ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-dir", type=Path, required=True)
    run(parser.parse_args().evidence_dir)
