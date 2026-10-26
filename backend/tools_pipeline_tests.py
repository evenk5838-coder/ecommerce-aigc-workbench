#!/usr/bin/env python3
"""生产链路收尾验收（免费）：任务完成自动回写 + full 自动合成 + 重启/失败/取消/重复完成 + ZIP 导出。

关键：**不手动串联**——分镜/镜头/成片都通过 `worker.execute()`（生产消费路径）产生，
云端调用被替换为本地替身（返回真实 ffmpeg 生成的小 mp4 / PNG）。

用法：<venv>/bin/python backend/tools_pipeline_tests.py
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import assembly, cloud_tasks, config, db, pipeline, projects, scope, shots, storage, worker  # noqa: E402
from app.errors import AppError  # noqa: E402

RESULTS: list[dict] = []
OUT = ROOT / "docs" / "evidence" / "生产链路-20261026"
BRIEF = {"subject": "单瓶精华液", "quantity": 1}
V10 = "doubao-seedance-1-0-pro-fast-251015"
FLASH = "doubao-seedream-5-0-flash-260915"
SPECS = {"model_id": V10, "ratio": "9:16", "resolution": "720p"}
STATE = {"png": b"", "mp4": b"", "fail_video": False, "fail_image": False}


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


# ---------- 替身：模拟云端（返回真实媒体字节） ----------
def fake_run_image(task_id, model_id, params, token, on_progress=None):
    if STATE["fail_image"]:
        raise AppError("PROVIDER_UNAVAILABLE", "替身：图片失败", http=502)
    return {"data": STATE["png"], "kind": "image", "width": 32, "height": 32,
            "usage": {"images": 1}, "cost_cny": 0.12, "request_id": "fake",
            "load_ms": 0, "latency_ms": 10}


def fake_run_video(task_id, model_id, params, token, on_progress=None):
    if STATE["fail_video"]:
        raise AppError("PROVIDER_UNAVAILABLE", "替身：视频失败", http=502)
    return {"data": STATE["mp4"], "kind": "video", "width": 320, "height": 240,
            "fps": 24, "frames": None, "duration": 2.0,
            "usage": {"tokens": 1000}, "cost_cny": 0.436,
            "remote_task_id": f"fake-{task_id}", "load_ms": 0, "latency_ms": 10}


def setup() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="pipeline-"))
    config.DB_PATH = tmp / "app.db"
    config.ASSETS = tmp / "assets"
    config.ASSETS.mkdir(parents=True, exist_ok=True)
    db.init_db()

    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (205, 210, 220)).save(buf, format="PNG")
    STATE["png"] = buf.getvalue()

    import subprocess
    ff = assembly._ffmpeg()
    mp4 = tmp / "seed.mp4"
    subprocess.run([ff, "-y", "-f", "lavfi", "-i", "color=c=0x8fa3c8:s=320x240:d=2:r=24",
                    "-pix_fmt", "yuv420p", str(mp4)], capture_output=True, check=True)
    STATE["mp4"] = mp4.read_bytes()

    cloud_tasks.run_image = fake_run_image        # type: ignore[assignment]
    cloud_tasks.run_video = fake_run_video        # type: ignore[assignment]
    return tmp


def run_task(task_id: str) -> dict:
    """走生产消费路径（worker.execute），不手动串数据。"""
    worker.execute(db.get("tasks", task_id) or {})
    return db.get("tasks", task_id) or {}


def build_project(n: int = 2) -> tuple[str, list[str], dict]:
    pid = projects.create_project("生产链路", BRIEF)["project_id"]
    s = projects.add_script(pid, {"summary": "剧本"}, source="manual")
    projects.confirm_script(s["script_id"])
    out = shots.add_shots(pid, s["script_id"],
                          [{"description": f"镜{i}", "duration_s": 5} for i in range(1, n + 1)])
    storyboard_tasks = []
    for sid in out["shot_ids"]:
        g = shots.generate_storyboard(sid, FLASH, confirm=True)
        storyboard_tasks.append((sid, g["storyboard_id"], g["task_id"]))
    return pid, out["shot_ids"], {"storyboards": storyboard_tasks}


def run() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = setup()
    print(f"临时库：{tmp}/app.db（业务库未打开）| 替身返回真实 2s mp4\n")

    print("=== 1) 分镜任务完成 → 自动回写素材（生产路径）===")

    def storyboard_writeback():
        pid, shot_ids, info = build_project(2)
        sb_tasks = info["storyboards"]
        assert all(shots.get_storyboard(t[1])["asset_id"] is None for t in sb_tasks)
        for _, sb_id, task_id in sb_tasks:
            task = run_task(task_id)
            assert task["status"] == "succeeded", task["status"]
            sb = shots.get_storyboard(sb_id)
            assert sb["asset_id"] == task["output_asset_id"], "分镜素材未回写"
            shot = shots.get_shot(sb["shot_id"])
            assert shot["status"] == "storyboard_pending", shot["status"]
        return {"storyboards": len(sb_tasks), "attached": True}
    case("worker 完成分镜图任务 → storyboards.asset_id 自动回写", storyboard_writeback)

    print("\n=== 2) full 模式：全部成功 → 自动合成（无需手动 compose）===")

    def full_auto_compose():
        pid, shot_ids, info = build_project(2)
        for _, sb_id, task_id in info["storyboards"]:
            run_task(task_id)
            shots.review_storyboard(sb_id, approve=True)
        sc = scope.create_scope(pid, "full", shot_ids, SPECS)
        scope.confirm_scope(sc["scope_id"])
        sub = scope.submit_scope(sc["scope_id"])
        films_before = db.query("SELECT COUNT(*) c FROM films WHERE project_id=?", (pid,))[0]["c"]
        assert films_before == 0

        for t in sub["tasks"]:
            run_task(t["task_id"])

        films = db.query("SELECT * FROM films WHERE project_id=?", (pid,))
        assert len(films) == 1, f"应自动合成 1 条成片，实际 {len(films)}"
        f = films[0]
        assert f["status"] == "ready", f["status"]
        assert f["source_batch"] == sub["batch_id"], f["source_batch"]
        assert f["asset_id"], "成片未落素材库"
        dur = f["duration_ms"]
        assert dur and 3500 <= dur <= 4500, f"两段 2s 拼接应约 4000ms，实际 {dur}"
        takes = db.query("SELECT * FROM shot_takes WHERE shot_id IN (?,?)", tuple(shot_ids))
        assert all(t["status"] == "succeeded" and t["asset_id"] for t in takes)
        shot_status = {s["shot_id"]: s["status"] for s in shots.list_shots(pid)}
        assert all(v == "ready" for v in shot_status.values()), shot_status
        return {"films": 1, "status": f["status"], "duration_ms": dur,
                "shot_status": shot_status}
    case("full：逐镜任务完成 → 自动拼接成片（ready，实测约 4000ms）", full_auto_compose)

    def clips_no_auto_compose():
        pid, shot_ids, info = build_project(1)
        for _, sb_id, task_id in info["storyboards"]:
            run_task(task_id)
            shots.review_storyboard(sb_id, approve=True)
        sc = scope.create_scope(pid, "clips", shot_ids, SPECS)
        scope.confirm_scope(sc["scope_id"])
        sub = scope.submit_scope(sc["scope_id"])
        for t in sub["tasks"]:
            run_task(t["task_id"])
        films = db.query("SELECT COUNT(*) c FROM films WHERE project_id=?", (pid,))[0]["c"]
        assert films == 0, "clips 模式不应自动合成"
        return {"films": 0, "note": "clips 只交付独立片段"}
    case("clips：完成逐镜后**不**自动合成", clips_no_auto_compose)

    print("\n=== 3) 失败 / 取消 / 重复完成 ===")

    def partial_failure_no_film():
        pid, shot_ids, info = build_project(2)
        for _, sb_id, task_id in info["storyboards"]:
            run_task(task_id)
            shots.review_storyboard(sb_id, approve=True)
        sc = scope.create_scope(pid, "full", shot_ids, SPECS)
        scope.confirm_scope(sc["scope_id"])
        sub = scope.submit_scope(sc["scope_id"])
        run_task(sub["tasks"][0]["task_id"])            # 第 1 镜成功
        STATE["fail_video"] = True
        try:
            run_task(sub["tasks"][1]["task_id"])        # 第 2 镜失败
        finally:
            STATE["fail_video"] = False
        takes = db.query("SELECT * FROM shot_takes WHERE shot_id IN (?,?)", tuple(shot_ids))
        statuses = sorted(t["status"] for t in takes)
        assert statuses == ["failed", "succeeded"], statuses
        films = db.query("SELECT COUNT(*) c FROM films WHERE project_id=?", (pid,))[0]["c"]
        assert films == 0, "有失败镜时不应自动产出完整成片"
        return {"takes": statuses, "films": 0}
    case("失败镜 → take=failed，且 full 不自动出片", partial_failure_no_film)

    def cancel_updates_take():
        pid, shot_ids, info = build_project(1)
        for _, sb_id, task_id in info["storyboards"]:
            run_task(task_id)
            shots.review_storyboard(sb_id, approve=True)
        sc = scope.create_scope(pid, "full", shot_ids, SPECS)
        scope.confirm_scope(sc["scope_id"])
        sub = scope.submit_scope(sc["scope_id"])
        r = worker.cancel(sub["tasks"][0]["task_id"])   # 排队中取消
        assert r["result"] == "canceled"
        take = db.query("SELECT * FROM shot_takes WHERE task_id=?", (sub["tasks"][0]["task_id"],))[0]
        assert take["status"] == "canceled", take["status"]
        return {"cancel": r["result"], "take": take["status"]}
    case("排队中取消 → take 标记 canceled", cancel_updates_take)

    def duplicate_completion_idempotent():
        pid, shot_ids, info = build_project(1)
        for _, sb_id, task_id in info["storyboards"]:
            run_task(task_id)
            shots.review_storyboard(sb_id, approve=True)
        sc = scope.create_scope(pid, "full", shot_ids, SPECS)
        scope.confirm_scope(sc["scope_id"])
        sub = scope.submit_scope(sc["scope_id"])
        task_id = sub["tasks"][0]["task_id"]
        run_task(task_id)
        films_1 = db.query("SELECT COUNT(*) c FROM films WHERE project_id=?", (pid,))[0]["c"]
        takes_1 = db.query("SELECT COUNT(*) c FROM shot_takes WHERE shot_id=?", (shot_ids[0],))[0]["c"]
        # 重复完成（模拟重复回报/重放）
        pipeline.on_task_succeeded(db.get("tasks", task_id) or {})
        films_2 = db.query("SELECT COUNT(*) c FROM films WHERE project_id=?", (pid,))[0]["c"]
        takes_2 = db.query("SELECT COUNT(*) c FROM shot_takes WHERE shot_id=?", (shot_ids[0],))[0]["c"]
        assert films_1 == films_2 == 1, (films_1, films_2)
        assert takes_1 == takes_2, (takes_1, takes_2)
        return {"films": films_2, "takes": takes_2, "idempotent": True}
    case("重复完成回报 → 不重复建 take / 不重复合成", duplicate_completion_idempotent)

    def restart_reconcile_marks_interrupted():
        pid, shot_ids, info = build_project(1)
        for _, sb_id, task_id in info["storyboards"]:
            run_task(task_id)
            shots.review_storyboard(sb_id, approve=True)
        sc = scope.create_scope(pid, "full", shot_ids, SPECS)
        scope.confirm_scope(sc["scope_id"])
        sub = scope.submit_scope(sc["scope_id"])
        tid = sub["tasks"][0]["task_id"]
        db.update("tasks", tid, {"status": "running", "stage": "running"})
        rec = worker.reconcile()                        # 模拟重启
        take = db.query("SELECT * FROM shot_takes WHERE task_id=?", (tid,))[0]
        assert take["status"] == "interrupted", take["status"]
        assert rec["interrupted"] >= 1, rec
        assert db.query("SELECT COUNT(*) c FROM films WHERE project_id=?", (pid,))[0]["c"] == 0
        return {"interrupted": rec["interrupted"], "take": take["status"]}
    case("重启：中断任务的 take 标记 interrupted，且不自动合成", restart_reconcile_marks_interrupted)

    print("\n=== 4) ZIP 导出（含成片 MP4）===")

    def export_contains_film():
        pid, shot_ids, info = build_project(2)
        for _, sb_id, task_id in info["storyboards"]:
            run_task(task_id)
            shots.review_storyboard(sb_id, approve=True)
        sc = scope.create_scope(pid, "full", shot_ids, SPECS)
        scope.confirm_scope(sc["scope_id"])
        sub = scope.submit_scope(sc["scope_id"])
        for t in sub["tasks"]:
            run_task(t["task_id"])
        out = assembly.export_project(pid)
        import zipfile
        path, row = assembly.export_path(out["export_id"])
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            manifest = json.loads(zf.read("manifest.json"))
        films = [n for n in names if n.startswith("films/")]
        clips = [n for n in names if n.startswith("clips/")]
        sbs = [n for n in names if n.startswith("storyboards/")]
        assert films, f"ZIP 缺少成片 MP4：{names}"
        assert clips and len(clips) == 2, clips
        assert sbs and len(sbs) == 2, sbs
        assert "manifest.json" in names
        assert manifest["films"] and manifest["films"][0]["file"].startswith("films/")
        # 下载入口可用
        assert out["download_url"].endswith("/file")
        assert row["file_name"] == out["file_name"]
        return {"files": len(names), "films": films, "clips": len(clips),
                "storyboards": len(sbs), "bytes": out["bytes"],
                "download_url": out["download_url"]}
    case("导出 ZIP 含 films/ + clips/ + storyboards/ + manifest", export_contains_film)

    def export_download_route():
        pid, shot_ids, info = build_project(1)
        out = assembly.export_project(pid)
        from app.routers import creative
        resp = creative.download_export(out["export_id"])
        assert resp.media_type == "application/zip"
        assert Path(resp.path).exists()
        return {"media_type": resp.media_type, "file": Path(resp.path).name}
    case("下载入口 GET /api/exports/{id}/file 返回 zip", export_download_route)

    passed = sum(1 for r in RESULTS if r["ok"])
    summary = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "scope": "生产链路回写 + full 自动合成 + 失败/取消/重启/重复 + ZIP 导出",
        "isolation": {"fresh_db": True, "business_db_opened": False,
                      "provider": "替身（返回真实 ffmpeg 小 mp4 / PNG）",
                      "worker": "未启动线程（直接调用 worker.execute 生产路径）",
                      "real_model_calls": 0},
        "paid_calls": 0, "generation_tasks_executed": 0,
        "total": len(RESULTS), "passed": passed, "failed": len(RESULTS) - passed,
        "cases": RESULTS,
        "disclaimer": "云端调用为替身；验证的是生产链路状态机与文件产物，不是模型质量。",
    }
    (OUT / "免费验收结果.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{'=' * 60}\n结果：{passed}/{len(RESULTS)} 通过")
    print("付费 0｜真实模型 0｜业务库未打开")
    print(f"证据：{OUT / '免费验收结果.json'}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(run())
