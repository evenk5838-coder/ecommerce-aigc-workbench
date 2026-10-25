#!/usr/bin/env python3
"""S1 复审修复：R1 未开通闸门 + R2 历史参数兼容（免费、隔离、零费用）。

隔离原则（按评审要求）：
- **新建临时库**（不复制、不打开业务库）；`config.ASSETS` 指向临时目录；
- **自制 32×32 测试图**，不依赖固定业务素材；
- `providers_ark._request` / `video_create` / `video_query` 全部替换为记录器；
- 不启动 worker、不调用真实模型、不产生费用。

用法：<venv>/bin/python backend/tools_s1_fix_tests.py
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
OUT = ROOT / "docs" / "evidence" / "S1-B10-20261025"
V10 = "doubao-seedance-1-0-pro-fast-251015"
MINI = "doubao-seedance-2-0-mini-260615"      # not_opened
FLASH = "doubao-seedream-5-0-flash-260915"    # verified
PRO = "doubao-seedream-5-0-pro-260628"        # not_opened


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


def setup() -> dict:
    """全新临时库 + 临时素材目录 + 自制测试图。"""
    tmp = Path(tempfile.mkdtemp(prefix="s1-fix-"))
    config.DB_PATH = tmp / "app.db"
    config.ASSETS = tmp / "assets"
    config.ASSETS.mkdir(parents=True, exist_ok=True)
    db.init_db()

    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (200, 210, 220)).save(buf, format="PNG")
    ref = storage.save_bytes(buf.getvalue(), "image", "upload", meta={"synthetic": True})

    def _guard(*a, **k):
        raise AssertionError("测试期间禁止真实云端调用")
    providers_ark._request = _guard  # type: ignore[assignment]
    return {"tmp": str(tmp), "ref": ref["id"]}


def n_tasks() -> int:
    return db.query("SELECT COUNT(*) c FROM tasks")[0]["c"]


def run() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    env = setup()
    REF = env["ref"]
    print(f"临时库：{env['tmp']}/app.db（业务库未打开）｜测试图：{REF}")

    print("\n=== R1：未开通模型提交闸门 ===")

    def gate_model_status():
        assert config.model_submittability(MINI)["reasons"] == ["MODEL_NOT_OPENED"]
        assert "MODEL_NOT_OPENED" in config.model_submittability(PRO)["reasons"]
        assert config.model_submittability(FLASH)["submittable"] is True
        return {"mini": "blocked", "pro": "blocked", "flash": "ok"}
    case("可提交性判定：not_opened 被标记（图片 + 视频）", gate_model_status)

    def block_unopened_video():
        before = n_tasks()
        try:
            main.create_task(main.TaskIn(
                kind="video", model_id=MINI, input_asset_id=REF,
                params={"prompt": "推近", "duration": 4, "ratio": "9:16",
                        "resolution": "720p"}))
            raise AssertionError("未开通视频模型仍被提交")
        except AppError as e:
            assert e.code == "MODEL_NOT_OPENED", e.code
        after = n_tasks()
        assert after == before, f"任务数变化 {before}→{after}"
        return {"code": "MODEL_NOT_OPENED", "tasks": f"{before}→{after}"}
    case("未开通视频模型（合法 4 秒）→ 拒绝，任务数不变", block_unopened_video)

    def block_unopened_image():
        before = n_tasks()
        try:
            main.create_task(main.TaskIn(
                kind="image", model_id=PRO, input_asset_id=REF,
                params={"prompt": "白底", "generation_mode": "image_to_image",
                        "size": "2048x2048"}))
            raise AssertionError("未开通图片模型仍被提交")
        except AppError as e:
            assert e.code == "MODEL_NOT_OPENED", e.code
        assert n_tasks() == before
        return {"code": "MODEL_NOT_OPENED", "tasks_unchanged": True}
    case("未开通图片模型 → 拒绝，任务数不变", block_unopened_image)

    def opened_model_ok():
        t = main.create_task(main.TaskIn(
            kind="image", model_id=FLASH, input_asset_id=REF,
            params={"prompt": "白底", "generation_mode": "image_to_image",
                    "size": "2048x2048"}))
        assert t["status"] == "queued"
        return {"task": t["id"][:14] + "…", "status": "queued"}
    case("已开通模型正常入队", opened_model_ok)

    def estimate_preview_allowed():
        before = n_tasks()
        est = main.estimate_task(main.TaskIn(
            kind="video", model_id=MINI, input_asset_id=REF,
            params={"prompt": "推近", "duration": 4, "ratio": "9:16", "resolution": "720p"}))
        assert est["submittable"] is False, est
        assert "MODEL_NOT_OPENED" in est["blocked_reasons"], est["blocked_reasons"]
        assert est["estimated_cost_cny"] > 0
        assert n_tasks() == before, "预估不应建任务"
        return {"submittable": False, "reasons": est["blocked_reasons"],
                "estimate": est["estimated_cost_cny"]}
    case("费用预估仍可返回，但标记 submittable=false 且不建任务", estimate_preview_allowed)

    def assistant_quote_blocked():
        from app.assistant import service
        s = service.ensure_session(None, "p-gate", "generate")
        code = None
        try:
            service.quote(session_id=s["id"], project_id="p-gate",
                          payload={"kind": "video", "model_id": MINI,
                                   "input_asset_id": REF,
                                   "template_id": "tpl_product_motion_5s",
                                   "template_fields": {"motion": "推近",
                                                       "duration": 4, "ratio": "9:16"}})
            raise AssertionError("助手报价未拦截未开通模型")
        except AppError as exc:
            code = exc.code
            assert code == "MODEL_NOT_OPENED", code
        return {"code": code}
    case("助手报价路径同样拦截未开通模型", assistant_quote_blocked)

    def idempotent_not_blocked():
        body = main.TaskIn(kind="video", model_id=V10, input_asset_id=REF,
                           params={"prompt": "推近", "duration": 5, "ratio": "9:16",
                                   "resolution": "720p"},
                           client_request_id="idem-legacy-1")
        t1 = main.create_task(body)
        # 之后把该模型标为未开通（模拟账号状态变化）
        saved = config.MODEL_ACTIVATION[V10]
        config.MODEL_ACTIVATION[V10] = "not_opened"
        try:
            t2 = main.create_task(body)
        finally:
            config.MODEL_ACTIVATION[V10] = saved
        assert t2["id"] == t1["id"], "幂等回放被新闸门误拦"
        assert t2.get("idempotent_replay") is True
        return {"same_task": True, "idempotent_replay": True}
    case("幂等查询已有任务不被未开通闸门误拦", idempotent_not_blocked)

    print("\n=== R2：历史参数兼容（10.0 / \"10\"） ===")

    def legacy_estimate_replay():
        rows = []
        for raw in (10.0, "10"):
            t = main.create_task(main.TaskIn(
                kind="video", model_id=V10, input_asset_id=REF,
                params={"prompt": "推近", "duration": 5, "ratio": "9:16",
                        "resolution": "720p"},
                client_request_id=f"legacy-{type(raw).__name__}-{raw}"))
            # 直接改写为历史形态（模拟旧版本落库），不改费用字段
            db.update("tasks", t["id"], {
                "params_json": db.dumps({"prompt": "推近", "duration": raw,
                                         "ratio": "9:16", "resolution": "720p"})})
            replay = main.create_task(main.TaskIn(
                kind="video", model_id=V10, input_asset_id=REF,
                params={"prompt": "x", "duration": 5},
                client_request_id=t["client_request_id"]))
            rows.append({"raw": repr(raw), "estimate": replay["estimated_cost_cny"]})
            assert abs(replay["estimated_cost_cny"] - 0.9072) < 0.01, \
                f"历史 {raw!r} 报价错为 {replay['estimated_cost_cny']}"
        return {"rows": rows}
    case("历史 10.0 / \"10\" 回放报价仍为 10 秒（≈0.9072，非 0.4536）", legacy_estimate_replay)

    def legacy_pending_execution():
        captured: dict = {}

        def fake_create(model_id, content, **kw):
            captured.update({"duration": kw.get("duration"), "model": model_id})
            raise AppError("PROVIDER_UNAVAILABLE", "测试中止", http=502)
        orig = providers_ark.video_create
        providers_ark.video_create = fake_create  # type: ignore[assignment]
        try:
            out = []
            for raw in (10.0, "10"):
                t = main.create_task(main.TaskIn(
                    kind="video", model_id=V10, input_asset_id=REF,
                    params={"prompt": "推近", "duration": 5, "ratio": "9:16",
                            "resolution": "720p"}))
                db.update("tasks", t["id"], {
                    "params_json": db.dumps({"prompt": "推近", "duration": raw,
                                             "ratio": "9:16", "resolution": "720p"})})
                stored = db.loads(db.get("tasks", t["id"])["params_json"])
                try:
                    cloud_tasks.run_video(t["id"], V10, stored,
                                          type("T", (), {"raise_if_canceled": lambda s: None})())
                except AppError:
                    pass
                out.append({"raw": repr(raw), "provider_duration": captured.get("duration")})
                assert captured.get("duration") == 10, f"历史 {raw!r} 请求时长错为 {captured}"
        finally:
            providers_ark.video_create = orig  # type: ignore[assignment]
        return {"rows": out}
    case("历史 10.0 / \"10\" 待执行任务执行时按 10 秒请求（不报 not_integer）",
         legacy_pending_execution)

    def legacy_retry():
        t = main.create_task(main.TaskIn(
            kind="video", model_id=V10, input_asset_id=REF,
            params={"prompt": "推近", "duration": 5, "ratio": "9:16", "resolution": "720p"}))
        db.update("tasks", t["id"], {
            "status": "failed", "error_code": "TEST",
            "params_json": db.dumps({"prompt": "推近", "duration": 10.0,
                                     "ratio": "9:16", "resolution": "720p"})})
        r = main.retry_task(t["id"])
        new = db.loads(db.get("tasks", r["task_id"])["params_json"])
        assert new["duration"] == 10 and isinstance(new["duration"], int), new["duration"]
        assert db.get("tasks", t["id"])["params_json"] == db.dumps(
            {"prompt": "推近", "duration": 10.0, "ratio": "9:16", "resolution": "720p"}), \
            "历史原始参数被改写"
        return {"new_duration": new["duration"], "history_untouched": True}
    case("历史任务重试：新任务归一为整数 10，历史原始参数不改", legacy_retry)

    def legacy_remote_resume_no_recreate():
        calls = {"create": 0, "query": 0}
        orig_create = providers_ark.video_create
        orig_query = providers_ark.video_query
        providers_ark.video_create = lambda *a, **k: calls.__setitem__("create", calls["create"] + 1)  # type: ignore[assignment]
        def fake_query(remote_id, timeout=None):  # noqa: ANN001
            calls["query"] += 1
            # 用不可重试错误退出轮询，避免测试挂起
            raise AppError("PROVIDER_BAD_REQUEST", "测试中止：已确认走续查", http=400)
        providers_ark.video_query = fake_query  # type: ignore[assignment]
        try:
            t = main.create_task(main.TaskIn(
                kind="video", model_id=V10, input_asset_id=REF,
                params={"prompt": "推近", "duration": 5, "ratio": "9:16", "resolution": "720p"}))
            db.update("tasks", t["id"], {
                "remote_task_id": "cgt-legacy-remote", "remote_status": "running",
                "params_json": db.dumps({"prompt": "推近", "duration": "10",
                                         "ratio": "9:16", "resolution": "720p"})})
            stored = db.loads(db.get("tasks", t["id"])["params_json"])
            try:
                cloud_tasks.run_video(t["id"], V10, stored,
                                      type("T", (), {"raise_if_canceled": lambda s: None})())
            except AppError:
                pass
        finally:
            providers_ark.video_create = orig_create  # type: ignore[assignment]
            providers_ark.video_query = orig_query  # type: ignore[assignment]
        assert calls["create"] == 0, "已有 remote_task_id 却重新创建了云端任务"
        assert calls["query"] >= 1, "未查询原远端任务"
        return {"create_calls": 0, "query_calls": calls["query"]}
    case("remote_task_id 续查：不重新创建，仅查询原任务", legacy_remote_resume_no_recreate)

    def no_five_second_fallback():
        # 真正无法解析的历史值，不得按 5 秒报价
        try:
            cloud_tasks.estimate_cost("video", V10,
                                      {"prompt": "x", "duration": "abc"}, legacy=True)
            raise AssertionError("无法解析时仍返回了报价（疑似 5 秒兜底）")
        except AppError as e:
            assert e.code == "MODEL_CAPABILITY_MISMATCH", e.code
            assert (e.detail or {}).get("reason") == "not_integer"
        return {"code": "MODEL_CAPABILITY_MISMATCH", "fallback_to_5": False}
    case("解析失败不再按 5 秒兜底（明确报错）", no_five_second_fallback)

    def new_requests_still_strict():
        for bad in (10.0, "10", True, 5.5):
            try:
                main.create_task(main.TaskIn(
                    kind="video", model_id=V10, input_asset_id=REF,
                    params={"prompt": "x", "duration": bad, "ratio": "9:16",
                            "resolution": "720p"}))
                raise AssertionError(f"新请求未拒绝 {bad!r}")
            except AppError as e:
                assert e.code == "MODEL_CAPABILITY_MISMATCH", e.code
        return {"10.0": "rejected", "'10'": "rejected", "True": "rejected", "5.5": "rejected"}
    case("新请求仍严格拒绝 10.0 / \"10\" / true / 5.5", new_requests_still_strict)

    passed = sum(1 for r in RESULTS if r["ok"])
    summary = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "scope": "S1 复审修复：R1 未开通闸门 + R2 历史参数兼容",
        "isolation": {"fresh_db": True, "assets_dir": "临时目录",
                      "synthetic_image": "32×32 PNG 自制", "business_db_opened": False,
                      "worker": "未启动", "network": "全拦截"},
        "paid_calls": 0, "real_llm_calls": 0, "generation_tasks_executed": 0,
        "total": len(RESULTS), "passed": passed, "failed": len(RESULTS) - passed,
        "cases": RESULTS,
        "disclaimer": "仅验证闸门与兼容逻辑；未执行真实生成。",
    }
    path = OUT / "复审修复结果.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{'=' * 60}\n结果：{passed}/{len(RESULTS)} 通过")
    print("付费 0｜真实模型 0｜真实生成 0｜业务库未打开")
    print(f"证据：{path}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(run())
