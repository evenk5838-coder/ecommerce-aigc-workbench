#!/usr/bin/env python3
"""S1 后端 B10 免费验收：时长严格校验 + 四处一致（校验/估价/落库/供应商请求）。

隔离：临时库副本；`providers_ark._request` 与 `video_create` 被替换为记录器；
不启动 worker、不调用真实模型、不写业务库、零费用。

用法：<venv>/bin/python backend/tools_s1_duration_tests.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import cloud_tasks, config, db, main, providers_ark  # noqa: E402
from app.errors import AppError  # noqa: E402

RESULTS: list[dict] = []
OUT = ROOT / "docs" / "evidence" / "S1-B10-20261025"
V10 = "doubao-seedance-1-0-pro-fast-251015"
MINI = "doubao-seedance-2-0-mini-260615"
FIRST_FRAME = "asset_cd91e2f9dda14123a622"


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
    tmp = Path(tempfile.mkdtemp(prefix="s1-b10-"))
    shutil.copy2(config.DB_PATH, tmp / "app.db")
    config.DB_PATH = tmp / "app.db"
    db.init_db()

    def _guard(*a, **k):
        raise AssertionError("测试期间禁止真实云端调用")
    providers_ark._request = _guard  # type: ignore[assignment]
    return tmp


def run() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = setup()
    print(f"隔离库：{tmp / 'app.db'}（业务库未改）\n")

    print("=== 1) 能力策略：range+step 优先 / 缺字段退回枚举 / 都没有=不可用 ===")

    def policy_priority():
        p10 = config.duration_policy(V10)
        assert p10 == {"kind": "range", "min": 2, "max": 12, "step": 1, "allowed": None}, p10
        pmini = config.duration_policy(MINI)
        assert pmini["kind"] == "range" and (pmini["min"], pmini["max"]) == (4, 15), pmini
        # 伪造：只有枚举
        config.MODEL_CAPABILITIES["_enum_only"] = {"input_modes": ["image_to_video"],
                                                   "duration_s": [5, 10]}
        try:
            pe = config.duration_policy("_enum_only")
            assert pe["kind"] == "enum" and pe["allowed"] == [5, 10], pe
        finally:
            config.MODEL_CAPABILITIES.pop("_enum_only", None)
        # 伪造：range 缺 step → 退回枚举；都没 → unavailable
        config.MODEL_CAPABILITIES["_broken"] = {"duration_range_s": [2, 12]}
        try:
            pb = config.duration_policy("_broken")
            assert pb["kind"] == "unavailable", pb
        finally:
            config.MODEL_CAPABILITIES.pop("_broken", None)
        return {"pro": p10, "mini": pmini, "enum_fallback": "ok", "broken": "unavailable"}
    case("range+step 优先；缺 step 不猜测 → unavailable", policy_priority)

    def models_endpoint_exposes_policy():
        items = {m["id"]: m for m in main.models()["items"]}
        cap = items[V10]["capabilities"]
        assert cap["duration_policy"]["kind"] == "range", cap.get("duration_policy")
        assert cap["duration_step_s"] == 1
        assert "duration_range_s" in cap
        return {"policy": cap["duration_policy"], "step": cap["duration_step_s"]}
    case("GET /api/models 暴露 duration_policy / range / step", models_endpoint_exposes_policy)

    print("\n=== 2) 合法值：3 / 6 / 12 秒 —— 校验、估价、落库、请求同一整数 ===")

    def legal_three_six_twelve():
        captured: dict = {}

        def fake_video_create(model_id, content, **kw):
            captured.update({"model_id": model_id, **kw})
            raise AppError("PROVIDER_UNAVAILABLE", "测试中止：已捕获请求参数", http=502)
        orig = providers_ark.video_create
        providers_ark.video_create = fake_video_create  # type: ignore[assignment]
        rows = []
        try:
            for d in (3, 6, 12):
                t = main.create_task(main.TaskIn(
                    kind="video", model_id=V10, input_asset_id=FIRST_FRAME,
                    params={"prompt": "推近", "duration": d, "ratio": "9:16",
                            "resolution": "720p", "generate_audio": False}))
                stored = db.loads(db.get("tasks", t["id"])["params_json"])
                est = cloud_tasks.estimate_cost("video", V10, stored)
                # 触发执行路径（会被 fake 中止，但参数已捕获）
                try:
                    cloud_tasks.run_video(t["id"], V10, stored, type("T", (), {
                        "raise_if_canceled": lambda self: None})())
                except AppError:
                    pass
                rows.append({"requested": d, "stored": stored["duration"],
                             "provider": captured.get("duration"), "estimate": est})
                assert stored["duration"] == d, f"落库值不一致：{stored['duration']}"
                assert captured.get("duration") == d, f"请求值不一致：{captured.get('duration')}"
                assert isinstance(captured.get("duration"), int)
                expected = round(config.video_token_estimate(d, "720p", "9:16") / 1e6 * 4.20, 4)
                assert abs(est - expected) < 1e-6, f"估价不一致：{est} vs {expected}"
        finally:
            providers_ark.video_create = orig  # type: ignore[assignment]
        return {"rows": rows}
    case("3/6/12 秒：校验=落库=估价=供应商请求", legal_three_six_twelve)

    print("\n=== 3) 非法值：1 / 13 / 5.5 / 5.0 / '5' / true 一律拒绝 ===")

    def illegal_values():
        rejected = {}
        for bad in (1, 13, 5.5, 5.0, "5", True, [5], {"d": 5}):
            try:
                main.create_task(main.TaskIn(
                    kind="video", model_id=V10, input_asset_id=FIRST_FRAME,
                    params={"prompt": "推近", "duration": bad, "ratio": "9:16",
                            "resolution": "720p"}))
                raise AssertionError(f"未拒绝：{bad!r}")
            except AppError as e:
                assert e.code in ("MODEL_CAPABILITY_MISMATCH", "VALIDATION_FAILED"), e.code
                det = e.detail or {}
                rejected[repr(bad)] = {"code": e.code, "reason": det.get("reason"),
                                       "got_type": det.get("got_type")}
        # 逐项核对 reason
        assert rejected["1"]["reason"] == "out_of_range", rejected
        assert rejected["13"]["reason"] == "out_of_range"
        assert rejected["5.5"]["reason"] == "not_integer"
        assert rejected["5.0"]["reason"] == "not_integer", "5.0 是 float，必须拒绝"
        assert rejected["'5'"]["reason"] == "not_integer"
        assert rejected["True"]["reason"] == "not_integer", "bool 必须拒绝"
        return {"rejected": rejected}
    case("1/13/5.5/5.0/'5'/true 全部拒绝，且 reason 正确", illegal_values)

    def no_silent_coercion():
        # 5.9 不能被 int() 改成 5 通过
        try:
            config.resolve_duration_s(V10, 5.9, strict=True)
            raise AssertionError("5.9 未被拒绝（发生了静默取整）")
        except AppError as e:
            assert (e.detail or {}).get("reason") == "not_integer"
        # 13 不能被夹取成 12
        try:
            config.resolve_duration_s(V10, 13, strict=True)
            raise AssertionError("13 未被拒绝（发生了夹取）")
        except AppError as e:
            assert (e.detail or {}).get("reason") == "out_of_range"
        return {"5.9": "rejected", "13": "rejected", "coercion": "none"}
    case("禁止 int()/round()/夹取：5.9 与 13 直接拒绝", no_silent_coercion)

    def legacy_still_works():
        for d in (5, 10):
            t = main.create_task(main.TaskIn(
                kind="video", model_id=V10, input_asset_id=FIRST_FRAME,
                params={"prompt": "推近", "duration": d, "ratio": "9:16", "resolution": "720p"}))
            assert db.loads(db.get("tasks", t["id"])["params_json"])["duration"] == d
        return {"5s": "ok", "10s": "ok", "note": "旧值仍合法，兼容不破坏"}
    case("旧任务接口兼容：5/10 秒仍可提交", legacy_still_works)

    print("\n=== 4) 估价接口与提交一致性 ===")

    def estimate_matches_submit():
        body = main.TaskIn(kind="video", model_id=V10, input_asset_id=FIRST_FRAME,
                           params={"prompt": "推近", "duration": 6, "ratio": "9:16",
                                   "resolution": "720p"})
        est = main.estimate_task(body)
        t = main.create_task(body)
        stored = db.loads(db.get("tasks", t["id"])["params_json"])
        assert est["effective"]["duration"] == stored["duration"] == 6
        assert abs(est["estimated_cost_cny"] - cloud_tasks.estimate_cost("video", V10, stored)) < 1e-9
        return {"estimate": est["estimated_cost_cny"], "effective": est["effective"]}
    case("estimate 与提交的有效时长/费用一致", estimate_matches_submit)

    def estimate_rejects_bad():
        for bad in (1, 5.5):
            try:
                main.estimate_task(main.TaskIn(
                    kind="video", model_id=V10, input_asset_id=FIRST_FRAME,
                    params={"prompt": "推近", "duration": bad, "ratio": "9:16",
                            "resolution": "720p"}))
                raise AssertionError(f"estimate 未拒绝 {bad!r}")
            except AppError as e:
                assert e.code == "MODEL_CAPABILITY_MISMATCH"
        return {"1": "rejected", "5.5": "rejected"}
    case("estimate 同样拒绝非法时长（不静默修正）", estimate_rejects_bad)

    def no_tasks_created_on_reject():
        before = db.query("SELECT COUNT(*) c FROM tasks")[0]["c"]
        for bad in (1, 13, 5.5, "5", True):
            try:
                main.create_task(main.TaskIn(
                    kind="video", model_id=V10, input_asset_id=FIRST_FRAME,
                    params={"prompt": "x", "duration": bad, "ratio": "9:16",
                            "resolution": "720p"}))
            except AppError:
                pass
        after = db.query("SELECT COUNT(*) c FROM tasks")[0]["c"]
        assert after == before, f"非法请求仍建了任务：{before}→{after}"
        return {"tasks_before": before, "tasks_after": after}
    case("非法时长不产生任务记录", no_tasks_created_on_reject)

    def unopened_model_rejected():
        # 2.0 mini 未开通：时长 4–15 合法策略，但提交应被 activation 层拦（此处仅验证策略解析）
        pol = config.duration_policy(MINI)
        assert pol["kind"] == "range" and pol["min"] == 4
        assert config.resolve_duration_s(MINI, 4) == 4
        try:
            config.resolve_duration_s(MINI, 3, strict=True)
            raise AssertionError("3s 超出 mini 的 4–15 未被拒绝")
        except AppError as e:
            assert (e.detail or {}).get("reason") == "out_of_range"
        return {"mini_policy": pol, "4s": "ok", "3s": "rejected"}
    case("2.0 mini 策略为 4–15（未开通仍不可提交由 activation 处理）", unopened_model_rejected)

    passed = sum(1 for r in RESULTS if r["ok"])
    summary = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "scope": "S1 后端 B10：时长严格校验与四处一致",
        "isolation": {"db": "临时副本", "real_db_untouched": True,
                      "provider_guard": "providers_ark.video_create 替换为记录器",
                      "worker": "未启动"},
        "paid_calls": 0, "real_llm_calls": 0, "generation_tasks_executed": 0,
        "total": len(RESULTS), "passed": passed, "failed": len(RESULTS) - passed,
        "cases": RESULTS,
        "disclaimer": "仅验证校验/估价/落库/请求参数一致性，未执行真实生成。",
    }
    path = OUT / "免费验收结果.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{'=' * 60}\n结果：{passed}/{len(RESULTS)} 通过")
    print("付费 0｜真实模型 0｜真实生成 0｜业务库未改动")
    print(f"证据：{path}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(run())
