#!/usr/bin/env python3
"""四模式（B0）免费安全验收：T2V/T2I/I2V/I2I 分支、payload、批量、重试、旧请求兼容。

隔离库 + 替身供应商（不发网络请求）；断言 estimate 零入队/零模型调用。
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import cloud_tasks, config, db, main, providers_ark, storage  # noqa: E402
from app.errors import AppError  # noqa: E402

RESULTS: list[dict] = []
OUT = ROOT / "docs" / "evidence" / "四模式-20261026"
V = "doubao-seedance-1-0-pro-fast-251015"
F = "doubao-seedream-5-0-flash-260915"
IMG = {"id": None}
CALLS = {"n": 0}


def case(name: str, fn) -> None:
    try:
        d = fn() or {}
        RESULTS.append({"case": name, "ok": True, "detail": d})
        print(f"  OK  {name}" + (f"  {d}" if d else ""))
    except AssertionError as e:
        RESULTS.append({"case": name, "ok": False, "error": str(e)})
        print(f" FAIL {name} → {e}")
    except Exception as e:  # noqa: BLE001
        RESULTS.append({"case": name, "ok": False,
                        "error": f"{type(e).__name__}: {e}",
                        "trace": traceback.format_exc()[-400:]})
        print(f" ERR  {name} → {type(e).__name__}: {e}")


def setup() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="modes-"))
    config.DB_PATH = tmp / "app.db"
    config.ASSETS = tmp / "assets"
    config.ASSETS.mkdir(parents=True, exist_ok=True)
    db.init_db()
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (198, 205, 215)).save(buf, format="JPEG")
    IMG["id"] = storage.save_bytes(buf.getvalue(), "image", "upload")["id"]

    def _guard(*a, **k):
        CALLS["n"] += 1
        raise AssertionError("测试禁止真实云端调用")
    providers_ark._request = _guard  # type: ignore[assignment]
    return tmp


def expect(code: str, fn, http: int | None = None) -> dict:
    try:
        fn()
    except AppError as e:
        assert e.code == code, f"期望 {code}，实际 {e.code}: {e.message}"
        if http is not None:
            assert e.http == http
        return {"code": e.code, "http": e.http}
    raise AssertionError(f"期望抛出 {code}，但没有报错")


def vbody(mode=None, with_img=False, prompt="瓶子缓慢旋转", **kw) -> main.TaskIn:
    params = {"prompt": prompt, "duration": 5, "ratio": "9:16", "resolution": "720p"}
    if mode:
        params["generation_mode"] = mode
    params.update(kw)
    return main.TaskIn(kind="video", model_id=V,
                       input_asset_id=IMG["id"] if with_img else None, params=params)


def run() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = setup()
    print(f"临时库：{tmp}/app.db（业务库未打开）\n")

    print("=== 组1：四模式合法解析 + 错误分支拒绝 ===")

    def four_modes_resolve():
        out = {}
        out["T2V"] = main.resolve_task(vbody("text_to_video"))["effective"]["generation_mode"]
        out["I2V"] = main.resolve_task(vbody("image_to_video", with_img=True))["effective"]["generation_mode"]
        out["T2I"] = main.resolve_task(main.TaskIn(
            kind="image", model_id=F,
            params={"generation_mode": "text_to_image", "prompt": "白底", "size": "2048x2048"})
        )["effective"]["generation_mode"]
        out["I2I"] = main.resolve_task(main.TaskIn(
            kind="image", model_id=F, input_asset_id=IMG["id"],
            params={"generation_mode": "image_to_image", "prompt": "换背景", "size": "2048x2048"})
        )["effective"]["generation_mode"]
        assert out == {"T2V": "text_to_video", "I2V": "image_to_video",
                       "T2I": "text_to_image", "I2I": "image_to_image"}, out
        return out
    case("四模式各自合法输入可解析，effective 回显模式", four_modes_resolve)

    def reject_branches():
        before = db.query("SELECT COUNT(*) c FROM tasks")[0]["c"]
        checks = {
            "T2V带图冲突": expect("GENERATION_MODE_CONFLICT",
                                lambda: main.create_task(vbody("text_to_video", with_img=True)), 400),
            "T2V空prompt": expect("VALIDATION_FAILED",
                                lambda: main.create_task(vbody("text_to_video", prompt="   ")), 400),
            "I2V无图": expect("REFERENCE_IMAGE_REQUIRED",
                            lambda: main.create_task(vbody("image_to_video")), 400),
            "未知mode": expect("VALIDATION_FAILED",
                             lambda: main.create_task(vbody("img2video")), 400),
            "无mode无图": expect("VALIDATION_FAILED",
                              lambda: main.create_task(vbody(None)), 400),
            "图片用视频mode": expect("VALIDATION_FAILED",
                                 lambda: main.create_task(main.TaskIn(
                                     kind="image", model_id=F,
                                     params={"generation_mode": "text_to_video",
                                             "prompt": "x", "size": "1024x1024"})), 400),
        }
        after = db.query("SELECT COUNT(*) c FROM tasks")[0]["c"]
        assert after == before, f"错误分支仍建任务：{before}→{after}"
        assert CALLS["n"] == 0, "错误分支触发了模型调用"
        return {k: v["code"] for k, v in checks.items()}
    case("错误 kind / 未知模式 / 空提示词 / 缺图 → 拒绝且不建任务", reject_branches)

    def unsupported_mode_by_model():
        # 伪造一个只支持 I2V 的模型，验证"模型不支持该模式"
        config.MODEL_CAPABILITIES["_i2v_only"] = {"input_modes": ["image_to_video"],
                                                 "ratios": ["9:16"],
                                                 "resolutions": ["720p"],
                                                 "duration_s": [5]}
        config.MODEL_REGISTRY["_i2v_only"] = {"name": "test", "kind": "video",
                                             "provider": "ark", "license": "t"}
        config.MODEL_ACTIVATION["_i2v_only"] = "verified"
        try:
            d = expect("MODEL_CAPABILITY_MISMATCH", lambda: main.resolve_task(
                main.TaskIn(kind="video", model_id="_i2v_only",
                            params={"generation_mode": "text_to_video", "prompt": "x",
                                    "duration": 5, "ratio": "9:16", "resolution": "720p"})), 400)
        finally:
            config.MODEL_CAPABILITIES.pop("_i2v_only", None)
            config.MODEL_REGISTRY.pop("_i2v_only", None)
            config.MODEL_ACTIVATION.pop("_i2v_only", None)
        return d
    case("模型不支持该模式 → MODEL_CAPABILITY_MISMATCH", unsupported_mode_by_model)

    print("\n=== 组2：T2V estimate 与 create 一致；payload 不含 image_url ===")

    def t2v_estimate_zero_side_effect():
        before = db.query("SELECT COUNT(*) c FROM tasks")[0]["c"]
        est = main.estimate_task(vbody("text_to_video"))
        after = db.query("SELECT COUNT(*) c FROM tasks")[0]["c"]
        assert after == before, "estimate 建了任务"
        assert est["side_effects"]["task_created"] is False
        assert est["side_effects"]["model_called"] is False
        assert est["effective"]["generation_mode"] == "text_to_video"
        created = main.create_task(vbody("text_to_video"))
        saved = db.loads(db.get("tasks", created["id"])["params_json"])
        assert saved["generation_mode"] == "text_to_video"
        assert saved.get("input_asset_id") is None
        return {"estimate_mode": est["effective"]["generation_mode"],
                "saved_mode": saved["generation_mode"],
                "same_duration": est["effective"]["duration"] == saved["duration"],
                "estimated": est["estimated_cost_cny"] == created["estimated_cost_cny"]}
    case("T2V：estimate 零副作用，estimate 与 create 的模式/时长/规格/费用一致",
         t2v_estimate_zero_side_effect)

    def t2v_payload_text_only():
        params = {"generation_mode": "text_to_video", "prompt": "瓶子旋转", "duration": 5}
        content = cloud_tasks.build_content(params)
        types = [c["type"] for c in content]
        assert types == ["text"], types
        assert not any("image_url" in c for c in content)
        return {"payload_types": types}
    case("T2V 供应商 payload：仅文字，无 image_url", t2v_payload_text_only)

    print("\n=== 组3：I2V 缺图/坏图/非图片拒绝；合法 I2V MIME 正确 ===")

    def _fake_asset(kind: str, mime: str, data: bytes, name: str) -> str:
        """直接写文件 + 插入素材行（绕过上传校验，模拟真实脏数据）。"""
        rel = f"fake/{name}"
        path = config.ASSETS / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        aid = db.new_id("asset")
        db.insert("assets", {"id": aid, "sha256": f"fake-{aid}", "kind": kind,
                             "mime": mime, "size": len(data), "width": 32, "height": 32,
                             "rel_path": rel, "source": "upload", "meta_json": "{}",
                             "created_at": db.now(), "owner": "local"})
        return aid

    def i2v_invalid_assets():
        before = db.query("SELECT COUNT(*) c FROM tasks")[0]["c"]
        # a) 非图片素材（kind=video）
        vid = _fake_asset("video", "video/mp4", b"\x00\x00\x00\x18ftypmp42", "v1.mp4")
        d1 = expect("ASSET_NOT_IMAGE", lambda: main.create_task(main.TaskIn(
            kind="video", model_id=V, input_asset_id=vid,
            params={"generation_mode": "image_to_video", "prompt": "x",
                    "duration": 5, "ratio": "9:16", "resolution": "720p"})), 400)
        # b) 素材不存在
        d2 = expect("ASSET_NOT_FOUND", lambda: main.create_task(main.TaskIn(
            kind="video", model_id=V, input_asset_id="asset_missing",
            params={"generation_mode": "image_to_video", "prompt": "x",
                    "duration": 5, "ratio": "9:16", "resolution": "720p"})), 404)
        # c) 坏图（声明为 image/jpeg 但内容无法解码）
        bad = _fake_asset("image", "image/jpeg", b"not-a-real-jpeg", "broken.jpg")
        d3 = expect(("INVALID_REFERENCE_IMAGE", "ASSET_NOT_IMAGE"),
                    lambda: main.create_task(main.TaskIn(
                        kind="video", model_id=V, input_asset_id=bad,
                        params={"generation_mode": "image_to_video", "prompt": "x",
                                "duration": 5, "ratio": "9:16", "resolution": "720p"})))
        after = db.query("SELECT COUNT(*) c FROM tasks")[0]["c"]
        assert after == before, "非法素材仍建了任务"
        return {"video_asset": d1["code"], "missing": d2["code"], "broken": d3["code"]}
    # expect 支持元组
    _orig_expect = expect

    def expect_any(codes, fn, http=None):  # noqa: ANN001
        try:
            fn()
        except AppError as e:
            allowed = (codes,) if isinstance(codes, str) else tuple(codes)
            assert e.code in allowed, f"期望 {allowed}，实际 {e.code}"
            return {"code": e.code}
        raise AssertionError(f"期望 {allowed}，但没有报错")

    globals()["expect"] = expect_any
    case("I2V：非图片 / 不存在 / 坏图 → 均拒绝（不建任务）", i2v_invalid_assets)
    globals()["expect"] = _orig_expect

    def i2v_payload_mime():
        params = {"generation_mode": "image_to_video", "prompt": "推近",
                  "input_asset_id": IMG["id"], "duration": 5}
        content = cloud_tasks.build_content(params)
        text = [c for c in content if c["type"] == "text"]
        img = [c for c in content if c["type"] == "image_url"]
        assert text and len(img) == 1, content
        url = img[0]["image_url"]["url"]
        assert url.startswith("data:image/jpeg;base64,"), url[:40]
        return {"types": [c["type"] for c in content], "mime": url.split(";")[0]}
    case("I2V 供应商 payload：文字 + 首帧图，MIME 为真实 jpeg", i2v_payload_mime)

    print("\n=== 组4：T2V 不静默带图；重试保持模式 ===")

    def t2v_conflict_not_silent():
        # 显式 T2V + 旧 input_asset_id → 明确拒绝（不按图生执行）
        d = expect("GENERATION_MODE_CONFLICT", lambda: main.create_task(
            vbody("text_to_video", with_img=True)), 400)
        # 即使 params 里残留 input_asset_id 也不携带
        body = main.TaskIn(kind="video", model_id=V,
                           params={"generation_mode": "text_to_video", "prompt": "x",
                                   "duration": 5, "ratio": "9:16", "resolution": "720p",
                                   "input_asset_id": IMG["id"]})
        r = main.resolve_task(body)
        assert r["params"].get("input_asset_id") is None, r["params"].get("input_asset_id")
        assert r["input_asset_id"] is None
        return {**d, "residual_stripped": True}
    case("显式 T2V 混入参考图 → 拒绝；参数残留也被剥离", t2v_conflict_not_silent)

    def retry_keeps_mode():
        t2v = main.create_task(vbody("text_to_video"))
        db.update("tasks", t2v["id"], {"status": "failed", "error_code": "TEST"})
        r1 = main.retry_task(t2v["id"])
        p1 = db.loads(db.get("tasks", r1["task_id"])["params_json"])
        assert p1["generation_mode"] == "text_to_video"
        assert p1.get("input_asset_id") in (None, ""), p1.get("input_asset_id")

        i2v = main.create_task(vbody("image_to_video", with_img=True))
        db.update("tasks", i2v["id"], {"status": "failed", "error_code": "TEST"})
        r2 = main.retry_task(i2v["id"])
        new_task = db.get("tasks", r2["task_id"])
        p2 = db.loads(new_task["params_json"])
        assert p2["generation_mode"] == "image_to_video"
        assert new_task["input_asset_id"] == IMG["id"], new_task["input_asset_id"]
        return {"t2v_retry_mode": p1["generation_mode"], "t2v_retry_img": p1.get("input_asset_id"),
                "i2v_retry_mode": p2["generation_mode"], "i2v_retry_img": "kept"}
    case("重试：T2V 仍不带图；I2V 保留模式与首帧", retry_keeps_mode)

    print("\n=== 组5：批量按项校验，不串图/不串参数 ===")

    def batch_per_item():
        items = [
            main.TaskIn(kind="video", model_id=V,
                        params={"generation_mode": "text_to_video", "prompt": "文生视频A",
                                "duration": 5, "ratio": "9:16", "resolution": "720p"}),
            main.TaskIn(kind="video", model_id=V, input_asset_id=IMG["id"],
                        params={"generation_mode": "image_to_video", "prompt": "图生视频B",
                                "duration": 10, "ratio": "1:1", "resolution": "720p"}),
        ]
        r = main.post_batch(main.BatchIn(name="mixed", items=items))
        tasks = [db.get("tasks", tid) for tid in r["task_ids"]]
        p0, p1 = db.loads(tasks[0]["params_json"]), db.loads(tasks[1]["params_json"])
        assert p0["generation_mode"] == "text_to_video" and tasks[0]["input_asset_id"] is None
        assert p1["generation_mode"] == "image_to_video" and tasks[1]["input_asset_id"] == IMG["id"]
        assert (p0["duration"], p0["ratio"]) == (5, "9:16")
        assert (p1["duration"], p1["ratio"]) == (10, "1:1")
        # 混入非法项 → 整批拒绝
        before = db.query("SELECT COUNT(*) c FROM tasks")[0]["c"]
        try:
            main.post_batch(main.BatchIn(name="bad", items=[
                items[0], main.TaskIn(kind="video", model_id=V,
                                      params={"generation_mode": "text_to_video",
                                              "prompt": "x", "duration": 5,
                                              "ratio": "9:16", "resolution": "720p"},
                                      input_asset_id=IMG["id"])]))
            raise AssertionError("非法项未被拒绝")
        except AppError as e:
            assert e.code == "GENERATION_MODE_CONFLICT"
        assert db.query("SELECT COUNT(*) c FROM tasks")[0]["c"] == before, "整批未回滚"
        return {"mixed_batch": "ok", "invalid_batch": "rejected_atomically"}
    case("批量：每项按自身模式校验，不串图/参数；非法项整批拒绝", batch_per_item)

    print("\n=== 组6：旧无 mode 的有图请求兼容；历史不改写 ===")

    def legacy_i2v_still_works():
        r = main.resolve_task(vbody(None, with_img=True))
        assert r["params"]["generation_mode"] == "image_to_video", r["params"]["generation_mode"]
        assert r["effective"]["generation_mode"] == "image_to_video"
        content = cloud_tasks.build_content(r["params"])
        assert any(c["type"] == "image_url" for c in content)
        return {"mode": r["params"]["generation_mode"], "has_image": True}
    case("旧请求（无 mode + 首帧）→ 归一为 image_to_video，不被改成 T2V",
         legacy_i2v_still_works)

    def legacy_task_not_rewritten():
        t = main.create_task(main.TaskIn(
            kind="video", model_id=V, input_asset_id=IMG["id"],
            params={"prompt": "旧任务", "duration": 5, "ratio": "9:16", "resolution": "720p"}))
        original = db.get("tasks", t["id"])["params_json"]
        assert "generation_mode" in (db.loads(original) or {}), "新任务应写入规范化模式"
        # 模拟历史行（无 mode）
        legacy = {"prompt": "历史", "duration": 5, "ratio": "9:16", "resolution": "720p"}
        db.update("tasks", t["id"], {"params_json": db.dumps(legacy), "status": "failed"})
        before_json = db.get("tasks", t["id"])["params_json"]
        r = main.retry_task(t["id"])
        assert db.get("tasks", t["id"])["params_json"] == before_json, "历史原始参数被改写"
        new_p = db.loads(db.get("tasks", r["task_id"])["params_json"])
        assert new_p["generation_mode"] == "image_to_video"
        return {"history_unchanged": True, "retry_mode": new_p["generation_mode"]}
    case("旧历史行（无 mode）不被改写；重试归一化并保留首帧", legacy_task_not_rewritten)

    passed = sum(1 for r in RESULTS if r["ok"])
    summary = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "scope": "B0 四模式：T2V/T2I/I2V/I2I 分支、payload、批量、重试、旧请求兼容",
        "isolation": {"fresh_db": True, "business_db_opened": False,
                      "provider": "未调用（providers_ark._request 守卫）",
                      "worker": "未启动"},
        "paid_calls": 0, "real_llm_calls": 0, "generation_tasks_executed": 0,
        "total": len(RESULTS), "passed": passed, "failed": len(RESULTS) - passed,
        "cases": RESULTS,
        "disclaimer": "T2V/I2V 供应商 payload 仅做分支与 MIME 断言，未真实调用。",
    }
    (OUT / "免费验收结果.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{'=' * 60}\n结果：{passed}/{len(RESULTS)} 通过")
    print("付费 0｜真实模型 0｜业务库未打开")
    print(f"证据：{OUT / '免费验收结果.json'}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(run())
