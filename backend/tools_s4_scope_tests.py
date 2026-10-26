#!/usr/bin/env python3
"""S4 免费验收：整组范围（两种平级模式）→ 确认 → 提交 → 逐镜状态 → 定向重试。

覆盖：
1. 两种模式（full / clips）报价：不建任务、冻结每镜完整输入
2. 未确认/过期/变更（description 或分镜版本变化）→ 拒绝
3. 确认后提交：单事务建 batch+tasks+takes+收据；重复提交幂等返回原 batch
4. 逐镜状态可查（status 接口）；单 worker 顺序标注
5. 只重试失败镜；成功镜不动；历史 take 保留
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

from app import config, db, director, projects, scope, shots, storage  # noqa: E402
from app.errors import AppError  # noqa: E402

RESULTS: list[dict] = []
OUT = ROOT / "docs" / "evidence" / "S4-20261026"
BRIEF = {"subject": "单瓶精华液", "quantity": 1}
V10 = "doubao-seedance-1-0-pro-fast-251015"
FLASH = "doubao-seedream-5-0-flash-260915"
SPECS = {"model_id": V10, "ratio": "9:16", "resolution": "720p"}


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


SYNTH_ASSET = {"id": None}


def setup() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="s4-"))
    config.DB_PATH = tmp / "app.db"
    config.ASSETS = tmp / "assets"
    config.ASSETS.mkdir(parents=True, exist_ok=True)
    db.init_db()
    import io

    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (210, 210, 220)).save(buf, format="PNG")
    SYNTH_ASSET["id"] = storage.save_bytes(buf.getvalue(), "image", "upload",
                                           meta={"synthetic": True})["id"]
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


def ready(n: int = 2) -> tuple[str, list[str]]:
    """建项目 → 确认脚本 → 加镜头 → 每镜生成并批准分镜。"""
    pid = projects.create_project("S4 项目", BRIEF)["project_id"]
    s = projects.add_script(pid, {"summary": "三段式"}, source="manual")
    projects.confirm_script(s["script_id"])
    out = shots.add_shots(pid, s["script_id"],
                          [{"description": f"镜{i}", "duration_s": 5} for i in range(1, n + 1)])
    for i, sid in enumerate(out["shot_ids"], start=1):
        g = shots.generate_storyboard(sid, FLASH, confirm=True)
        shots.attach_storyboard_asset(g["storyboard_id"], SYNTH_ASSET["id"])
        shots.review_storyboard(g["storyboard_id"], approve=True)
    return pid, out["shot_ids"]


def run() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = setup()
    print(f"临时库：{tmp}/app.db（业务库未打开）\n")

    print("=== 1) 两种模式的报价（不建任务）===")

    def both_modes_quote():
        pid, ids = ready(2)
        before = db.query("SELECT COUNT(*) c FROM tasks")[0]["c"]
        full = scope.create_scope(pid, "full", ids, SPECS)
        clips = scope.create_scope(pid, "clips", ids, SPECS)
        after = db.query("SELECT COUNT(*) c FROM tasks")[0]["c"]
        assert after == before, "报价阶段不应建任务"
        assert full["mode"] == "full" and clips["mode"] == "clips"
        assert full["total_estimate_cny"] > 0
        assert full["planned_film_duration_s"] == 10, full["planned_film_duration_s"]
        assert full["side_effects"]["model_called"] is False
        return {"full": full["total_estimate_cny"], "clips": clips["total_estimate_cny"],
                "planned_s": full["planned_film_duration_s"], "tasks": f"{before}→{after}"}
    case("full / clips 均可报价，且不建任务", both_modes_quote)

    def frozen_inputs():
        pid, ids = ready(2)
        sc = scope.create_scope(pid, "full", ids, SPECS)
        row = db.get("generation_scopes", sc["scope_id"])
        frozen = db.loads(row["frozen_json"])
        it = frozen["items"][0]
        for k in ("shot_id", "description", "prompt", "storyboard_id",
                  "storyboard_version", "input_asset_id", "duration_s",
                  "model_id", "ratio", "resolution"):
            assert k in it, f"冻结输入缺少 {k}"
        return {"fields": sorted(it.keys())[:6], "hash": sc["scope_hash"][:16] + "…"}
    case("冻结每镜完整输入（含分镜版本与素材）", frozen_inputs)

    print("\n=== 2) 确认与失效 ===")

    def submit_requires_confirm():
        pid, ids = ready(2)
        sc = scope.create_scope(pid, "full", ids, SPECS)
        d = expect("SCOPE_REQUIRED", lambda: scope.submit_scope(sc["scope_id"]), 409)
        return d
    case("未确认直接提交 → SCOPE_REQUIRED", submit_requires_confirm)

    def stale_after_change():
        pid, ids = ready(2)
        sc = scope.create_scope(pid, "full", ids, SPECS)
        scope.confirm_scope(sc["scope_id"])
        before = db.query("SELECT COUNT(*) c FROM tasks")[0]["c"]
        # 改某镜描述（属冻结输入）→ 提交时必须 SCOPE_STALE
        shots.update_shot(ids[0], description="改成海边场景")
        d = expect("SCOPE_STALE", lambda: scope.submit_scope(sc["scope_id"]), 409)
        after = db.query("SELECT COUNT(*) c FROM tasks")[0]["c"]
        assert after == before, f"失效范围仍建了任务：{before}→{after}"
        return d
    case("改镜头描述后提交 → SCOPE_STALE（不建任务）", stale_after_change)

    def expired_scope():
        pid, ids = ready(1)
        sc = scope.create_scope(pid, "clips", ids, SPECS)
        db.update("generation_scopes", sc["scope_id"], {"expires_at": db.now() - 1})
        d = expect("SCOPE_EXPIRED", lambda: scope.confirm_scope(sc["scope_id"]), 409)
        return d
    case("过期 scope 无法确认 → SCOPE_EXPIRED", expired_scope)

    print("\n=== 3) 提交（事务/幂等/逐镜）===")

    def submit_creates_all():
        pid, ids = ready(2)
        sc = scope.create_scope(pid, "full", ids, SPECS)
        scope.confirm_scope(sc["scope_id"])
        out = scope.submit_scope(sc["scope_id"])
        assert len(out["tasks"]) == 2
        assert out["sequential"] is True
        batch = db.get("batches", out["batch_id"])
        assert batch and json.loads(batch["params_json"])["mode"] == "full"
        takes = db.query("SELECT * FROM shot_takes WHERE shot_id IN (?,?)", tuple(ids))
        assert len(takes) == 2 and all(t["status"] == "queued" for t in takes)
        receipt = db.query("SELECT * FROM submit_receipts WHERE scope_id=?", (sc["scope_id"],))
        assert len(receipt) == 1
        return {"tasks": len(out["tasks"]), "takes": len(takes), "batch": out["batch_id"][:12] + "…"}
    case("确认后提交：batch + 2 tasks + 2 takes + 收据", submit_creates_all)

    def idempotent_submit():
        pid, ids = ready(2)
        sc = scope.create_scope(pid, "full", ids, SPECS)
        scope.confirm_scope(sc["scope_id"])
        a = scope.submit_scope(sc["scope_id"])
        n = db.query("SELECT COUNT(*) c FROM tasks")[0]["c"]
        b = scope.submit_scope(sc["scope_id"])
        assert b["batch_id"] == a["batch_id"] and b.get("idempotent_replay") is True
        assert db.query("SELECT COUNT(*) c FROM tasks")[0]["c"] == n, "重复提交新建了任务"
        return {"same_batch": True, "tasks_unchanged": True}
    case("重复提交同一 scope → 返回原 batch（不新建任务）", idempotent_submit)

    def clips_mode_delivers_clips():
        pid, ids = ready(2)
        sc = scope.create_scope(pid, "clips", ids, SPECS)
        scope.confirm_scope(sc["scope_id"])
        out = scope.submit_scope(sc["scope_id"])
        assert out["mode"] == "clips" and out["auto_film"] is False, out
        return {"mode": out["mode"], "auto_film": False}
    case("clips 模式：提交后不自动合成（auto_film=false）", clips_mode_delivers_clips)

    def status_endpoint():
        pid, ids = ready(2)
        sc = scope.create_scope(pid, "full", ids, SPECS)
        scope.confirm_scope(sc["scope_id"])
        scope.submit_scope(sc["scope_id"])
        st = scope.project_status(pid)
        assert len(st["shots"]) == 2
        assert all(s["take"]["status"] == "queued" for s in st["shots"])
        assert st["current_stage"] in ("video", "delivery"), st["current_stage"]
        return {"shots": len(st["shots"]), "stage": st["current_stage"]}
    case("逐镜状态可查（刷新恢复入口）", status_endpoint)

    print("\n=== 4) 定向重试 ===")

    def retry_only_failed():
        pid, ids = ready(2)
        sc = scope.create_scope(pid, "full", ids, SPECS)
        scope.confirm_scope(sc["scope_id"])
        out = scope.submit_scope(sc["scope_id"])
        takes = [t["take_id"] for t in out["tasks"]]
        # 模拟：第 1 镜成功、第 2 镜失败
        db.update("shot_takes", takes[0], {"status": "succeeded", "asset_id": "asset_ok"})
        db.update("shot_takes", takes[1], {"status": "failed"})
        n = db.query("SELECT COUNT(*) c FROM tasks")[0]["c"]
        r = scope.retry_take(takes[1])
        assert db.query("SELECT COUNT(*) c FROM tasks")[0]["c"] == n + 1
        assert db.get("shot_takes", takes[0])["status"] == "succeeded", "成功镜被改动"
        assert db.get("shot_takes", takes[1])["status"] == "failed", "历史 take 被改写"
        # 成功镜不允许重试
        d = expect("VALIDATION_FAILED", lambda: scope.retry_take(takes[0]), 400)
        return {**d, "new_take": r["take_id"][:10] + "…", "tasks_added": 1}
    case("只重试失败镜；成功镜与历史 take 保持不变", retry_only_failed)

    passed = sum(1 for r in RESULTS if r["ok"])
    summary = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "scope": "S4 整组范围：两模式 / 冻结输入 / 确认 / 事务提交 / 幂等 / 定向重试",
        "isolation": {"fresh_db": True, "business_db_opened": False,
                      "worker": "未启动", "real_model_calls": 0,
                      "note": "任务只入队不执行，无费用"},
        "paid_calls": 0, "generation_tasks_executed": 0,
        "total": len(RESULTS), "passed": passed, "failed": len(RESULTS) - passed,
        "cases": RESULTS,
        "disclaimer": "任务仅入队；未执行真实生成。",
    }
    (OUT / "免费验收结果.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{'=' * 60}\n结果：{passed}/{len(RESULTS)} 通过")
    print("付费 0｜真实模型 0｜真实生成 0｜业务库未打开")
    print(f"证据：{OUT / '免费验收结果.json'}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(run())
