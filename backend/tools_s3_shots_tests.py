#!/usr/bin/env python3
"""S3 免费验收：镜头 CRUD/排序 + 分镜版本与审核 + 单镜重做（隔离、零真实调用）。

覆盖：
1. 未确认脚本 → 不能加镜头（SCRIPT_NOT_CONFIRMED 持久化前置）
2. 镜头时长按模型原生区间严格校验（3/6/12 通过；1/13/5.5/"6" 拒绝）
3. 增删镜头 / 重排（只影响成片顺序，不使分镜确认失效）
4. 分镜报价不建任务；confirm=true 才建图片任务
5. 分镜多版本；通过绑定版本；驳回必须原因；重生成新版本不继承批准
6. 单镜重做只影响该镜
"""
from __future__ import annotations

import json
import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import config, db, director, projects, shots  # noqa: E402
from app.errors import AppError  # noqa: E402

RESULTS: list[dict] = []
OUT = ROOT / "docs" / "evidence" / "S3-20261026"
BRIEF = {"subject": "单瓶精华液", "quantity": 1, "target_duration_s": 20}
V10 = "doubao-seedance-1-0-pro-fast-251015"
FLASH = "doubao-seedream-5-0-flash-260915"


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
    tmp = Path(tempfile.mkdtemp(prefix="s3-"))
    config.DB_PATH = tmp / "app.db"
    config.ASSETS = tmp / "assets"
    config.ASSETS.mkdir(parents=True, exist_ok=True)
    db.init_db()
    return tmp


def expect(code: str, fn, http: int | None = None) -> dict:
    try:
        fn()
    except AppError as e:
        assert e.code == code, f"期望 {code}，实际 {e.code}: {e.message}"
        if http is not None:
            assert e.http == http, f"期望 HTTP {http}，实际 {e.http}"
        return {"code": e.code, "http": e.http}
    raise AssertionError(f"期望抛出 {code}，但没有报错")


def ready_project() -> tuple[str, str]:
    """建项目 + 确认脚本，返回 (project_id, script_id)。"""
    pid = projects.create_project("S3 项目", BRIEF)["project_id"]
    s = projects.add_script(pid, {"summary": "三段式"}, source="manual")
    projects.confirm_script(s["script_id"])
    return pid, s["script_id"]


def run() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = setup()
    print(f"临时库：{tmp}/app.db（业务库未打开）\n")

    print("=== 1) 前置条件：脚本确认 ===")

    def require_script_confirmed():
        pid = projects.create_project("未确认脚本", BRIEF)["project_id"]
        s = projects.add_script(pid, {"summary": "draft"}, source="manual")
        d = expect("SCRIPT_NOT_CONFIRMED",
                   lambda: shots.add_shots(pid, s["script_id"],
                                           [{"description": "瓶身", "duration_s": 5}]), 409)
        assert db.query("SELECT COUNT(*) c FROM shots")[0]["c"] == 0
        return d
    case("脚本未确认 → 拒绝加镜头（不产生镜头）", require_script_confirmed)

    def confirmed_allows_shots():
        pid, sid = ready_project()
        out = shots.add_shots(pid, sid, [
            {"description": "瓶身特写", "duration_s": 6},
            {"description": "推近瓶盖", "duration_s": 4}])
        assert out["count"] == 2
        rows = shots.list_shots(pid)
        assert [r["seq"] for r in rows] == [1, 2]
        return {"count": out["count"], "seqs": [r["seq"] for r in rows]}
    case("脚本确认后可加镜头（seq 自动递增）", confirmed_allows_shots)

    print("\n=== 2) 镜头时长严格校验（原生区间）===")

    def duration_strict():
        pid, sid = ready_project()
        ok = shots.add_shots(pid, sid, [{"description": "x", "duration_s": d}
                                        for d in (2, 3, 12)]) if False else None
        # 逐个验证
        accepted = []
        for d in (2, 3, 6, 12):
            out = shots.add_shots(pid, sid, [{"description": f"{d}s", "duration_s": d}])
            accepted.append(d)
        rejected = {}
        for bad in (1, 13, 5.5, "6", True):
            try:
                shots.add_shots(pid, sid, [{"description": "bad", "duration_s": bad}])
                raise AssertionError(f"未拒绝 {bad!r}")
            except AppError as e:
                assert e.code in ("MODEL_CAPABILITY_MISMATCH", "VALIDATION_FAILED"), e.code
                rejected[repr(bad)] = (e.detail or {}).get("reason")
        return {"accepted": accepted, "rejected": rejected}
    case("镜头时长 2/3/6/12 通过；1/13/5.5/\"6\"/true 拒绝", duration_strict)

    def edit_duration_strict():
        pid, sid = ready_project()
        out = shots.add_shots(pid, sid, [{"description": "a", "duration_s": 5}])
        shot_id = out["shot_ids"][0]
        d = expect("MODEL_CAPABILITY_MISMATCH",
                   lambda: shots.update_shot(shot_id, duration_s=5.5), 400)
        ok = shots.update_shot(shot_id, duration_s=8)
        assert ok["duration_s"] == 8
        return {**d, "updated_to": ok["duration_s"]}
    case("编辑镜头时长同样严格（5.5 拒绝 / 8 通过）", edit_duration_strict)

    print("\n=== 3) 增删与排序 ===")

    def reorder_only_affects_film():
        pid, sid = ready_project()
        out = shots.add_shots(pid, sid, [{"description": f"镜{i}", "duration_s": 5}
                                         for i in range(1, 4)])
        ids = out["shot_ids"]
        # 先给第一镜一个已批准分镜
        sb = shots.generate_storyboard(ids[0], FLASH, confirm=True)
        shots.attach_storyboard_asset(sb["storyboard_id"], "asset_fake")
        shots.review_storyboard(sb["storyboard_id"], approve=True)
        before = director.current_confirmation("storyboard", sb["storyboard_id"],
                                              sb["version"])
        assert before["valid"] is True
        r = shots.reorder_shots(pid, [ids[2], ids[0], ids[1]])
        after = director.current_confirmation("storyboard", sb["storyboard_id"],
                                             sb["version"])
        assert after["valid"] is True, "重排不应使分镜确认失效"
        seqs = [s["shot_id"] for s in shots.list_shots(pid)]
        assert seqs == [ids[2], ids[0], ids[1]], seqs
        return {"order": r["order"], "storyboard_still_valid": True}
    case("重排只影响成片顺序，不使分镜确认失效", reorder_only_affects_film)

    def delete_shot_invalidates():
        pid, sid = ready_project()
        out = shots.add_shots(pid, sid, [{"description": "a", "duration_s": 5},
                                         {"description": "b", "duration_s": 5}])
        shot_id = out["shot_ids"][0]
        shots.delete_shot(shot_id)
        remaining = [s["shot_id"] for s in shots.list_shots(pid)]
        assert remaining == [out["shot_ids"][1]], remaining
        return {"deleted": shot_id[:12] + "…", "remaining": 1}
    case("删除镜头（其余保留）", delete_shot_invalidates)

    print("\n=== 4) 分镜：报价 / 生成 / 版本 / 审核 ===")

    def quote_no_task():
        pid, sid = ready_project()
        out = shots.add_shots(pid, sid, [{"description": "a", "duration_s": 5}])
        before = db.query("SELECT COUNT(*) c FROM tasks")[0]["c"]
        q = shots.generate_storyboard(out["shot_ids"][0], FLASH, confirm=False)
        after = db.query("SELECT COUNT(*) c FROM tasks")[0]["c"]
        assert q["need_confirm"] is True
        assert after == before, "报价阶段不应建任务"
        assert q["total_estimate_cny"] > 0
        return {"estimate": q["total_estimate_cny"], "tasks": f"{before}→{after}"}
    case("分镜报价不建任务（confirm=false）", quote_no_task)

    def generate_creates_image_task():
        pid, sid = ready_project()
        out = shots.add_shots(pid, sid, [{"description": "a", "duration_s": 5}])
        shot_id = out["shot_ids"][0]
        gen = shots.generate_storyboard(shot_id, FLASH, confirm=True)
        task = db.get("tasks", gen["task_id"])
        assert task["kind"] == "image" and task["status"] == "queued"
        assert db.query("SELECT COUNT(*) c FROM storyboards WHERE shot_id=?",
                        (shot_id,))[0]["c"] == 1
        return {"task": gen["task_id"][:12] + "…", "version": gen["version"]}
    case("confirm=true → 建 1 个图片任务（复用现有任务系统）", generate_creates_image_task)

    def version_and_review():
        pid, sid = ready_project()
        out = shots.add_shots(pid, sid, [{"description": "a", "duration_s": 5}])
        shot_id = out["shot_ids"][0]
        g1 = shots.generate_storyboard(shot_id, FLASH, confirm=True)
        # 未附素材不能审核（不额外生成版本）
        expect("STORYBOARD_NOT_READY",
               lambda: shots.review_storyboard(g1["storyboard_id"], approve=True), 409)
        shots.attach_storyboard_asset(g1["storyboard_id"], "asset_fake_1")
        ok = shots.review_storyboard(g1["storyboard_id"], approve=True)
        assert ok["status"] == "approved"
        cur = director.current_confirmation("storyboard", g1["storyboard_id"], g1["version"])
        assert cur["valid"] is True
        # 重生成 → 新版本，旧批准仍属旧版本，新版本未确认
        g2 = shots.generate_storyboard(shot_id, FLASH, confirm=True)
        shots.attach_storyboard_asset(g2["storyboard_id"], "asset_fake_2")
        new_cur = director.current_confirmation("storyboard", g2["storyboard_id"], g2["version"])
        assert g2["version"] == g1["version"] + 1
        assert new_cur["valid"] is False, "新版本不应自动继承批准"
        # B1 新语义：重做分镜使**旧版本确认失效**（历史记录保留但不再授权）
        old_cur = director.current_confirmation("storyboard", g1["storyboard_id"], g1["version"])
        assert old_cur["valid"] is False, old_cur
        assert old_cur["reason"] == "storyboard_regenerated", old_cur
        assert old_cur["confirmed"] is True, "历史确认记录应保留"
        return {"v1": g1["version"], "v2": g2["version"],
                "new_unconfirmed": True, "old_invalidated": old_cur["reason"]}
    case("分镜多版本、审核绑定版本、新版本不继承批准", version_and_review)

    def reject_requires_reason():
        pid, sid = ready_project()
        out = shots.add_shots(pid, sid, [{"description": "a", "duration_s": 5}])
        g = shots.generate_storyboard(out["shot_ids"][0], FLASH, confirm=True)
        shots.attach_storyboard_asset(g["storyboard_id"], "asset_fake")
        expect("VALIDATION_FAILED",
               lambda: shots.review_storyboard(g["storyboard_id"], approve=False))
        r = shots.review_storyboard(g["storyboard_id"], approve=False, reason="瓶身比例不对")
        assert r["status"] == "rejected" and "重做" in r["hint"]
        shot = shots.get_shot(out["shot_ids"][0])
        assert shot["status"] == "draft", "驳回后应回到待重做"
        return {"status": r["status"], "shot_status": shot["status"]}
    case("驳回必须填原因；驳回后该镜回到待重做", reject_requires_reason)

    def redo_single_shot():
        pid, sid = ready_project()
        out = shots.add_shots(pid, sid, [{"description": f"镜{i}", "duration_s": 5}
                                         for i in (1, 2)])
        ids = out["shot_ids"]
        g1 = shots.generate_storyboard(ids[0], FLASH, confirm=True)
        g2 = shots.generate_storyboard(ids[1], FLASH, confirm=True)
        before = db.query("SELECT COUNT(*) c FROM tasks")[0]["c"]
        # 只重做第 1 镜
        g1b = shots.generate_storyboard(ids[0], FLASH, confirm=True)
        after = db.query("SELECT COUNT(*) c FROM tasks")[0]["c"]
        assert after == before + 1, "重做单镜应只新增 1 个任务"
        assert shots.get_storyboard(g2["storyboard_id"])["version"] == g2["version"]
        assert g1b["version"] == g1["version"] + 1
        return {"tasks_added": after - before, "shot2_version_unchanged": True}
    case("单镜重做只影响该镜（只新增 1 个任务）", redo_single_shot)

    def readiness_after_storyboard():
        pid, sid = ready_project()
        out = shots.add_shots(pid, sid, [{"description": "a", "duration_s": 5}])
        r1 = director.readiness(pid)
        assert r1["current"]["stage"] == "storyboard", r1["current"]["stage"]
        assert "generate_storyboard" in r1["allowed_actions"], r1["allowed_actions"]
        g = shots.generate_storyboard(out["shot_ids"][0], FLASH, confirm=True)
        shots.attach_storyboard_asset(g["storyboard_id"], "asset_fake")
        shots.review_storyboard(g["storyboard_id"], approve=True)
        r2 = director.readiness(pid)
        assert "quote_generation" in r2["allowed_actions"], r2["allowed_actions"]
        return {"before": r1["current"]["stage"], "after_actions": r2["allowed_actions"][:4]}
    case("就绪度随分镜通过推进到视频阶段", readiness_after_storyboard)

    passed = sum(1 for r in RESULTS if r["ok"])
    summary = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "scope": "S3 镜头 / 分镜（CRUD、排序、时长、版本、审核、单镜重做）",
        "isolation": {"fresh_db": True, "business_db_opened": False,
                      "worker": "未启动", "real_model_calls": 0,
                      "note": "图片任务仅入队，未执行（无 worker），无费用"},
        "paid_calls": 0, "generation_tasks_executed": 0,
        "total": len(RESULTS), "passed": passed, "failed": len(RESULTS) - passed,
        "cases": RESULTS,
        "disclaimer": "分镜任务只创建不执行；未调用任何真实模型。",
    }
    (OUT / "免费验收结果.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{'=' * 60}\n结果：{passed}/{len(RESULTS)} 通过")
    print("付费 0｜真实模型 0｜真实生成 0｜业务库未打开")
    print(f"证据：{OUT / '免费验收结果.json'}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(run())
