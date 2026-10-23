"""图生图链路自检（模拟）：校验矩阵 / 参考图是否真正进入请求体 / 批量隔离。
只证明调用链与错误处理，不代表模型效果。
运行：<venv>/bin/python backend/tools_img2img_checks.py
"""
from __future__ import annotations

import base64
import io
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from PIL import Image  # noqa: E402

from app import cloud_tasks, config, db, providers_ark, storage  # noqa: E402
from app.errors import AppError  # noqa: E402

CAPTURED: list[dict] = []


def fake_generate(model_id, prompt, size="1024x1024", watermark=False,
                  response_format="b64_json", image=None, timeout=None):
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (200, 120, 90)).save(buf, format="PNG")
    CAPTURED.append({"model_id": model_id, "prompt": prompt, "size": size,
                     "image": image, "image_len": len(image or "")})
    return {"b64": base64.b64encode(buf.getvalue()).decode(), "url": None,
            "usage": {"generated_images": 1}, "request_id": "sim-1", "latency_ms": 1}


def expect_error(code: str, fn):
    try:
        fn()
    except AppError as e:
        return {"case": code, "ok": e.code == code, "got": e.code, "message": e.message}
    except Exception as e:  # noqa: BLE001
        return {"case": code, "ok": False, "got": type(e).__name__, "message": str(e)[:120]}
    return {"case": code, "ok": False, "got": "no_error"}


def main() -> int:
    providers_ark.image_generate = fake_generate      # 拦截云端调用
    db.init_db()
    model = "doubao-seedream-5-0-flash-260915"

    # 找两张真实图片素材（不同内容）
    imgs = db.query("SELECT * FROM assets WHERE kind='image' ORDER BY created_at DESC LIMIT 2")
    if len(imgs) < 2:
        print("跳过：素材不足 2 张图片")
        return 1
    ref_a, ref_b = imgs[0], imgs[1]
    vid = db.query("SELECT * FROM assets WHERE kind='video' LIMIT 1")

    results = []
    # 1) 文生图：不带参考图 → image 必须为 None
    CAPTURED.clear()
    cloud_tasks.run_image("t-text", model, {"prompt": "白底商品图", "size": "1024x1024"}, None)
    results.append({"case": "text_to_image_no_ref", "ok": CAPTURED[-1]["image"] is None,
                    "image_is_none": CAPTURED[-1]["image"] is None})

    # 2) 图生图：参考图必须真的进入请求体，且 MIME 正确
    CAPTURED.clear()
    cloud_tasks.run_image("t-i2i-a", model, {"prompt": "保持主体，换背景", "size": "1024x1024",
                                             "generation_mode": "image_to_image",
                                             "input_asset_id": ref_a["id"]}, None)
    got = CAPTURED[-1]["image"] or ""
    expect_prefix = f"data:{ref_a['mime']};base64,"
    results.append({"case": "image_to_image_ref_passed", "ok": got.startswith(expect_prefix),
                    "mime": ref_a["mime"], "prefix_ok": got.startswith(expect_prefix),
                    "b64_len": len(got)})

    # 3) 批量隔离：两张不同参考图，请求体必须不同
    CAPTURED.clear()
    cloud_tasks.run_image("t-i2i-b", model, {"prompt": "换背景", "generation_mode": "image_to_image",
                                             "input_asset_id": ref_b["id"]}, None)
    url_b = CAPTURED[-1]["image"]
    results.append({"case": "batch_isolation_distinct_refs",
                    "ok": url_b != got and url_b.startswith(f"data:{ref_b['mime']};base64,"),
                    "different_from_a": url_b != got})

    # 4) 缺参考图
    results.append(expect_error("REFERENCE_IMAGE_REQUIRED", lambda: cloud_tasks.run_image(
        "t-i2i-missing", model, {"prompt": "x", "generation_mode": "image_to_image"}, None)))
    # 5) 参考素材不存在
    results.append(expect_error("ASSET_NOT_FOUND", lambda: cloud_tasks.run_image(
        "t-i2i-404", model, {"prompt": "x", "generation_mode": "image_to_image",
                             "input_asset_id": "asset_not_exist"}, None)))
    # 6) 参考素材不是图片
    if vid:
        results.append(expect_error("ASSET_NOT_IMAGE", lambda: cloud_tasks.run_image(
            "t-i2i-video", model, {"prompt": "x", "generation_mode": "image_to_image",
                                   "input_asset_id": vid[0]["id"]}, None)))
    # 7) 非法枚举
    results.append(expect_error("VALIDATION_FAILED", lambda: cloud_tasks.run_image(
        "t-bad-mode", model, {"prompt": "x", "generation_mode": "img2img",
                              "input_asset_id": ref_a["id"]}, None)))
    # 8) 文生图带参考图 → 不得把参考图传给模型（保持旧行为）
    CAPTURED.clear()
    cloud_tasks.run_image("t-text-with-ref", model, {"prompt": "白底", "generation_mode": "text_to_image",
                                                     "input_asset_id": ref_a["id"]}, None)
    results.append({"case": "text_to_image_ignores_ref", "ok": CAPTURED[-1]["image"] is None})

    ok = all(r["ok"] for r in results)
    out = {"all_ok": ok, "cases": results,
           "note": "模拟：ark.image_generate 被替换，未产生任何云端调用与费用",
           "refs": {"a": ref_a["id"], "b": ref_b["id"], "a_mime": ref_a["mime"], "b_mime": ref_b["mime"]}}
    Path("docs/evidence/阶段4").mkdir(parents=True, exist_ok=True)
    Path("docs/evidence/阶段4/img2img-sim-checks.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1))
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
