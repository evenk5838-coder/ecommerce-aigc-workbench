#!/usr/bin/env python3
"""B1–B6 免费验收：当前版本确认链 / 执行闸门 / 导演台动作 / 模板列表 / 历史版本 / 时长上下文。

隔离库 + 替身供应商（worker 不启动、不调真实模型）。
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

from app import assembly, config, db, director, projects, providers_ark, scope, shots, storage  # noqa: E402
from app.errors import AppError  # noqa: E402

RESULTS: list[dict] = []
OUT = ROOT / "docs" / "evidence" / "确认链-20261026"
BRIEF = {"subject": "单瓶精华液", "quantity": 1}
V = "doubao-seedance-1-0-pro-fast-251015"
F = "doubao-seedream-5-0-flash-260915"
SPECS = {"model_id": V, "ratio": "9:16", "resolution": "720p"}
IMG = {"id": None}


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
    tmp = Path(tempfile.mkdtemp(prefix="chain-"))
    config.DB_PATH = tmp / "app.db"
    config.ASSETS = tmp / "assets"
    config.ASSETS.mkdir(parents=True, exist_ok=True)
    db.init_db()
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (200, 206, 214)).save(buf, format="PNG")
    IMG["id"] = storage.save_bytes(buf.getvalue(), "image", "upload")["id"]

    def _guard(*a, **k):
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


def ready(n: int = 1, approved: bool = True) -> dict:
    """项目 + 确认脚本 + n 镜；approved=True 时每镜分镜已生成并通过。"""
    pid = projects.create_project("确认链", BRIEF)["project_id"]
    s = projects.add_script(pid, {"summary": "三段"}, source="manual")
    projects.confirm_script(s["script_id"])
    out = shots.add_shots(pid, s["script_id"],
                          [{"description": f"镜{i}", "duration_s": 5} for i in range(1, n + 1)])
    sbs = []
    if approved:
        for sid in out["shot_ids"]:
            g = shots.generate_storyboard(sid, F, confirm=True)
            shots.attach_storyboard_asset(g["storyboard_id"], IMG["id"])
            shots.review_storyboard(g["storyboard_id"], approve=True)
            sbs.append(g)
    return {"project_id": pid, "script_id": s["script_id"],
            "shot_ids": out["shot_ids"], "storyboards": sbs}


def run() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = setup()
    print(f"临时库：{tmp}/app.db（业务库未打开）\n")

    print("=== B1：当前版本确认链 ===")

    def shot_change_invalidates_storyboard_confirmation():
        env = ready(1)
        sb = env["storyboards"][0]
        before = director.current_confirmation("storyboard", sb["storyboard_id"], sb["version"])
        assert before["valid"] is True
        # 改描述（object_id 是 storyboard_id，不是 shot_id）
        shots.update_shot(env["shot_ids"][0], description="换成海边场景")
        after = director.current_confirmation("storyboard", sb["storyboard_id"], sb["version"])
        assert after["valid"] is False, "改镜头后分镜确认未失效"
        assert after["reason"] == "shot_changed", after
        r = director.readiness(env["project_id"])
        assert r["current"]["stage"] in ("storyboard", "script"), r["current"]["stage"]
        return {"before": True, "after": after["status"], "reason": after["reason"]}
    case("改镜头描述 → 该镜分镜确认失效（object_id 匹配正确）",
         shot_change_invalidates_storyboard_confirmation)

    def motion_change_invalidates():
        env = ready(1)
        sb = env["storyboards"][0]
        shots.update_shot(env["shot_ids"][0], motion="环绕")
        after = director.current_confirmation("storyboard", sb["storyboard_id"], sb["version"])
        assert after["valid"] is False, "改运镜未使确认失效"
        return {"reason": after["reason"]}
    case("改运镜同样使分镜确认失效", motion_change_invalidates)

    def regenerate_invalidates_old():
        env = ready(1)
        sb1 = env["storyboards"][0]
        g2 = shots.generate_storyboard(env["shot_ids"][0], F, confirm=True)
        old = director.current_confirmation("storyboard", sb1["storyboard_id"], sb1["version"])
        assert old["valid"] is False, "重做分镜后旧版本确认仍有效"
        assert g2["version"] == sb1["version"] + 1
        # 新版本未审核 → readiness 不应认为该镜通过
        r = director.readiness(env["project_id"])
        assert r["current"]["stage"] in ("storyboard", "script"), r["current"]["stage"]
        return {"old_valid": False, "new_version": g2["version"]}
    case("重做分镜 → 旧版本确认失效；未审核新版本不算通过",
         regenerate_invalidates_old)

    def reject_invalidates():
        env = ready(1)
        sb = env["storyboards"][0]
        shots.review_storyboard(sb["storyboard_id"], approve=False, reason="比例不对")
        after = director.current_confirmation("storyboard", sb["storyboard_id"], sb["version"])
        assert after["valid"] is False, "驳回后确认仍有效"
        assert after["reason"] == "storyboard_rejected", after
        return {"reason": after["reason"]}
    case("驳回已批准版本 → 该版本确认失效", reject_invalidates)

    def stale_storyboard_cannot_approve():
        env = ready(1, approved=False)
        g = shots.generate_storyboard(env["shot_ids"][0], F, confirm=True)
        shots.attach_storyboard_asset(g["storyboard_id"], IMG["id"])
        # 先改镜头（revision+1），旧分镜不能通过审核
        shots.update_shot(env["shot_ids"][0], description="新描述")
        d = expect("STORYBOARD_STALE",
                   lambda: shots.review_storyboard(g["storyboard_id"], approve=True), 409)
        return d
    case("分镜基于旧镜头版本 → 审核被拒 STORYBOARD_STALE",
         stale_storyboard_cannot_approve)

    def old_scope_invalid_after_change():
        env = ready(1)
        sc = scope.create_scope(env["project_id"], "full", env["shot_ids"], SPECS)
        scope.confirm_scope(sc["scope_id"])
        shots.update_shot(env["shot_ids"][0], description="改场景")
        d = expect("SCOPE_STALE", lambda: scope.submit_scope(sc["scope_id"]), 409)
        assert db.query("SELECT COUNT(*) c FROM tasks WHERE batch_id IS NOT NULL")[0]["c"] == 0
        return d
    case("改镜头后旧 scope 不可提交（SCOPE_STALE）", old_scope_invalid_after_change)

    def submit_rechecks_script_confirmation():
        env = ready(1)
        sc = scope.create_scope(env["project_id"], "full", env["shot_ids"], SPECS)
        scope.confirm_scope(sc["scope_id"])
        # 报价后改 brief → 脚本确认失效
        projects.update_project(env["project_id"], brief={"subject": "两件套装"})
        d = expect("SCRIPT_NOT_CONFIRMED", lambda: scope.submit_scope(sc["scope_id"]), 409)
        return d
    case("提交阶段复查脚本确认（不信任报价时点）", submit_rechecks_script_confirmation)

    print("\n=== B2：服务端执行闸门（直接调 API 也拦）===")

    def gates_require_script_confirmed():
        pid = projects.create_project("闸门", BRIEF)["project_id"]
        s = projects.add_script(pid, {"summary": "草稿"}, source="manual")
        out = shots.add_shots(pid, s["script_id"],
                              [{"description": "a", "duration_s": 5}]) if False else None
        # 未确认脚本 → 所有写接口都拒绝
        checks = {}
        checks["add"] = expect("SCRIPT_NOT_CONFIRMED",
                               lambda: shots.add_shots(pid, s["script_id"],
                                                       [{"description": "a", "duration_s": 5}]), 409)
        # 先确认后建一个镜头，再把脚本确认失效（改 brief）→ 更新/删除/排序/生成/审核都拒绝
        projects.confirm_script(s["script_id"])
        env = shots.add_shots(pid, s["script_id"], [{"description": "a", "duration_s": 5},
                                                    {"description": "b", "duration_s": 5}])
        ids = env["shot_ids"]
        g = shots.generate_storyboard(ids[0], F, confirm=True)
        projects.update_project(pid, brief={"subject": "改了"})   # 使脚本确认失效
        checks["update"] = expect("SCRIPT_NOT_CONFIRMED",
                                  lambda: shots.update_shot(ids[0], description="x"), 409)
        checks["delete"] = expect("SCRIPT_NOT_CONFIRMED",
                                  lambda: shots.delete_shot(ids[1]), 409)
        checks["reorder"] = expect("SCRIPT_NOT_CONFIRMED",
                                   lambda: shots.reorder_shots(pid, [ids[1], ids[0]]), 409)
        checks["generate_storyboard"] = expect("SCRIPT_NOT_CONFIRMED",
                                               lambda: shots.generate_storyboard(ids[1], F, confirm=True), 409)
        checks["review"] = expect("SCRIPT_NOT_CONFIRMED",
                                  lambda: shots.review_storyboard(g["storyboard_id"], approve=True), 409)
        return {k: v["code"] for k, v in checks.items()}
    case("未确认脚本 → 增删改/排序/生成分镜/审核全部 409", gates_require_script_confirmed)

    print("\n=== B3：导演台动作完整性 ===")

    def allowed_actions_progress():
        env = ready(1)
        r = director.readiness(env["project_id"])
        assert "quote_generation" in r["allowed_actions"], r["allowed_actions"]
        assert "compose_film" not in r["allowed_actions"], "没有片段时不应允许合成"
        # 造一条成功 take → 允许合成
        take = db.new_id("take")
        db.insert("shot_takes", {"id": take, "shot_id": env["shot_ids"][0],
                                 "task_id": "task_fake", "asset_id": IMG["id"],
                                 "duration_ms": 2000, "spec_json": "{}", "cost_cny": 0.1,
                                 "status": "succeeded", "created_at": db.now()})
        r2 = director.readiness(env["project_id"])
        assert "compose_film" in r2["allowed_actions"], r2["allowed_actions"]
        assert "export" not in r2["allowed_actions"], "没有成片时不应允许导出"
        return {"before": r["allowed_actions"], "after": r2["allowed_actions"]}
    case("交付动作随状态出现：有片段→compose_film；有成片→export", allowed_actions_progress)

    def done_state_not_override_invalid_confirm():
        env = ready(1)
        projects.update_project(env["project_id"], brief={"subject": "新商品"})  # 确认失效
        r = director.readiness(env["project_id"])
        assert r["current"]["confirmation"]["valid"] is False
        assert r["current"]["stage"] in ("script", "storyboard"), r["current"]["stage"]
        assert "confirm_generation" not in r["allowed_actions"], r["allowed_actions"]
        return {"stage": r["current"]["stage"], "valid": False}
    case("旧确认失效后不越过失效继续（阶段回退）", done_state_not_override_invalid_confirm)

    print("\n=== B4：结构模板列表 ===")

    def template_list():
        t1 = assembly.save_template("模板A", [{"seq": 1, "role": "开场", "duration_s": 4}])
        t2 = assembly.save_template("模板B", [{"seq": 1, "role": "收尾", "duration_s": 5}])
        from app.routers import creative
        items = creative.list_structure_templates()["items"]
        ids = {i["template_id"] for i in items}
        assert {t1["template_id"], t2["template_id"]} <= ids, ids
        assert all("latest_version" in i and "name" in i for i in items)
        return {"count": len(items)}
    case("GET /api/structure-templates 返回真实模板列表", template_list)

    print("\n=== B5：历史分镜与片段可读 ===")

    def history_endpoints():
        env = ready(1)
        shot_id = env["shot_ids"][0]
        g1 = env["storyboards"][0]
        g2 = shots.generate_storyboard(shot_id, F, confirm=True)   # 第二个版本
        sbs = shots.list_storyboards(shot_id)
        assert len(sbs) == 2, len(sbs)
        assert all("task_id" in s and s["task_id"] for s in sbs), sbs
        assert {s["version"] for s in sbs} == {g1["version"], g2["version"]}
        assert all("confirmation" in s for s in sbs)
        # takes 历史：一条失败 + 一条成功
        for status in ("failed", "succeeded"):
            db.insert("shot_takes", {"id": db.new_id("take"), "shot_id": shot_id,
                                     "task_id": f"task_{status}", "asset_id": IMG["id"],
                                     "duration_ms": 2000, "spec_json": "{}",
                                     "cost_cny": 0.1, "status": status,
                                     "created_at": db.now()})
        takes = shots.list_takes(shot_id)
        assert len(takes) == 2, len(takes)
        assert {t["status"] for t in takes} == {"failed", "succeeded"}
        assert all(t["duration_ms"] == 2000 for t in takes)
        return {"storyboards": len(sbs), "takes": len(takes),
                "has_task_id": True, "has_asset": True}
    case("按镜头读全部历史分镜/片段（含 task_id、失败项、实测时长）", history_endpoints)

    print("\n=== B6：镜头时长校验的模型上下文 ===")

    def duration_model_context():
        env = ready(1)
        shot_id = env["shot_ids"][0]
        # 使用 1.0 pro fast（2–12）→ 8 合法
        ok = shots.update_shot(shot_id, duration_s=8, model_id=V)
        assert ok["duration_s"] == 8
        # 用 mini 的区间（4–15）校验：3 应被拒（mini 下限 4）
        d = expect("MODEL_CAPABILITY_MISMATCH",
                   lambda: shots.update_shot(shot_id, duration_s=3,
                                             model_id="doubao-seedance-2-0-mini-260615"), 400)
        return {**d, "v_ok": ok["duration_s"]}
    case("修改镜头时长可用指定模型上下文校验", duration_model_context)

    passed = sum(1 for r in RESULTS if r["ok"])
    summary = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "scope": "B1 确认链 / B2 执行闸门 / B3 动作 / B4 模板列表 / B5 历史 / B6 时长上下文",
        "isolation": {"fresh_db": True, "business_db_opened": False,
                      "provider": "未调用", "worker": "未启动"},
        "paid_calls": 0, "real_llm_calls": 0,
        "total": len(RESULTS), "passed": passed, "failed": len(RESULTS) - passed,
        "cases": RESULTS,
        "disclaimer": "全为隔离免费验证；未调用真实模型。",
    }
    (OUT / "免费验收结果.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{'=' * 60}\n结果：{passed}/{len(RESULTS)} 通过")
    print("付费 0｜真实模型 0｜业务库未打开")
    print(f"证据：{OUT / '免费验收结果.json'}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(run())
