"""Local input continuity comparison against the retained v2 reference frame."""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import statistics
import sys

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app import media_policy


def metrics(image, box, rows):
    left, top, _, _ = box
    values, slopes = [], []
    for row in rows:
        y = top + row
        level = lambda x: sum(image.getpixel((x, y))) / 3
        boundary = level(left)
        values.append(abs(level(left-1) - boundary))
        slopes.append(abs((level(left-8)-boundary)/8 - (boundary-level(left+8))/8))
    return {"jump_mean": statistics.mean(values), "jump_p95": sorted(values)[int(len(values)*.95)],
            "slope_mean": statistics.mean(slopes), "slope_p95": sorted(slopes)[int(len(slopes)*.95)]}


def run(evidence):
    evidence.mkdir(parents=True, exist_ok=True)
    source = ROOT / "docs/evidence/v0.11-AiHubMix无水印复验-20261030/text_to_image.jpg"
    previous = ROOT / "docs/evidence/v0.11-视频构图v2真实复验-20261030/prepared-first-frame.png"
    report = {"model_requests": 0, "cost_cny": 0, "video_policy": media_policy.VIDEO_VERSION,
              "image_policy": media_policy.VERSION, "true_video_acceptance": "not_run"}
    if source.is_file() and previous.is_file():
        raw = source.read_bytes()
        before_sha = hashlib.sha256(raw).hexdigest()
        value, metadata = media_policy.prepare_reference(
            "data:image/jpeg;base64," + base64.b64encode(raw).decode(), "16:9")
        current = Image.open(io.BytesIO(base64.b64decode(value.split(",", 1)[1]))).convert("RGB")
        old = Image.open(previous).convert("RGB")
        box = metadata["content_box"]
        assert old.size == current.size
        assert old.crop(box).tobytes() == current.crop(box).tobytes()
        assert hashlib.sha256(source.read_bytes()).hexdigest() == before_sha
        left, top, _, bottom = box
        edge = [sum(old.getpixel((left, y)))/3 for y in range(top, bottom)]
        threshold = statistics.median(edge) - 12
        rows = [i for i, level in enumerate(edge) if 8 <= i < len(edge)-8 and level < threshold]
        assert rows, "No touching shadow detected in the retained sample"
        before, after = metrics(old, box, rows), metrics(current, box, rows)
        assert after["jump_mean"] < before["jump_mean"] * .2
        assert after["slope_mean"] < before["slope_mean"]
        assert after["jump_p95"] <= 1.5
        current.save(evidence / "新版阴影延展.png")
        old.save(evidence / "上一版真实输入.png")
        comparison = Image.new("RGB", (1280, 1488), "#151515")
        comparison.paste(old, (0, 24))
        comparison.paste(current, (0, 768))
        draw = ImageDraw.Draw(comparison)
        draw.text((12, 6), "BEFORE - retained v2 input frame", fill="white")
        draw.text((12, 750), "AFTER - bounded edge gradient / original pixels unchanged", fill="white")
        comparison.save(evidence / "阴影连续性对比.jpg", quality=94)
        mid = top + round(statistics.mean(rows))
        crop = (left-80, max(top, mid-90), left+80, min(bottom, mid+90))
        zoom = Image.new("RGB", (640, 400), "#151515")
        zoom.paste(old.crop(crop).resize((320, 360)), (0, 24))
        zoom.paste(current.crop(crop).resize((320, 360)), (320, 24))
        draw = ImageDraw.Draw(zoom)
        draw.text((12, 5), "BEFORE - input only", fill="white")
        draw.text((332, 5), "AFTER - input only", fill="white")
        zoom.save(evidence / "阴影交界局部对比.jpg", quality=95)
        report.update(source_unchanged=True, fitted_pixels_exact=True, source_sha256=before_sha,
                      shadow_rows=rows, shadow_before=before, shadow_after=after, preparation=metadata,
                      visual_limitations="Color extension cannot recover unseen physical shadows; faint texture may remain.")
        print("PASS: retained input shadow boundary and slope improve; fitted pixels/source unchanged")
    else:
        report["real_sample"] = "not_run: retained local sample missing; synthetic unittest coverage is separate"
        print("SKIP: retained real input sample unavailable; no real-sample improvement claim")
    (evidence / "offline-shadow.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence-dir", type=Path, required=True)
    run(parser.parse_args().evidence_dir)
