#!/usr/bin/env python3
"""真实验收（已批准）：2 张分镜图 + 2 条 5s 镜头视频 + 真实 ffmpeg 拼接 + 导出。

预算：**4 次真实模型调用，上限 ¥2.00**（用户 2026-10-26 批准）
- 图片：flash 2048² × 2 = ¥0.24
- 视频：1.0 pro fast 720p 9:16 5s × 2 ≈ ¥0.91
- 合计估算 ≈ ¥1.15

硬约束：每次调用前检查名额与预算（预占）；失败/超时也计数；**不自动重试**；超限立即停止。
隔离：**临时库 + 临时素材目录**（业务库不写入）；不启动 worker（任务由脚本同步执行）；
参考图从业务素材**只读复制**。
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import assembly, cloud_tasks, config, db, projects, scope, shots, storage, worker  # noqa: E402

OUT = ROOT / "docs" / "evidence" / "真实验收-20261026"
MAX_CALLS = 4
MAX_CNY = 2.00
FLASH = "doubao-seedream-5-0-flash-260915"
V10 = "doubao-seedance-1-0-pro-fast-251015"
REF_SRC = ROOT / "data" / "assets"            # 只读来源
REF_ASSET_ID = "asset_d73231ab405540b68eb6"   # 单瓶真实商品图（只读复制）

CALLS: list[dict] = []
SPENT = 0.0
RESERVED = 0.0


class BudgetStop(Exception):
    pass


def guard(kind: str, model_id: str, unit_estimate: float) -> None:
    """**调用前**检查名额与预算（含预占；失败也占名额）。"""
    global RESERVED
    if len(CALLS) >= MAX_CALLS:
        raise BudgetStop(f"请求名额已用尽（{MAX_CALLS}）")
    if RESERVED + unit_estimate > MAX_CNY:
        raise BudgetStop(f"预算预占将超上限：{RESERVED + unit_estimate:.2f} > {MAX_CNY:.2f}")
    RESERVED += unit_estimate
    CALLS.append({"kind": kind, "model": model_id, "reserved_cny": unit_estimate,
                  "started_at": time.time(), "ok": None})


def record(cost: float | None, **extra) -> None:
    global SPENT
    CALLS[-1].update(extra)
    if cost is not None:
        SPENT += float(cost)
        CALLS[-1]["cost_cny"] = float(cost)


def run_task(task_id: str, label: str) -> dict:
    """同步执行一个任务（等价于 worker 消费，但由脚本控制，不启动 worker 线程）。"""
    task = db.get("tasks", task_id) or {}
    t0 = time.time()
    worker.execute(task)
    row = db.get("tasks", task_id) or {}
    print(f"    {label}: {row.get('status')} | {row.get('latency_ms')}ms "
          f"| cost={row.get('cost_cny')} | out={row.get('output_asset_id')}")
    return {"task_id": task_id, "status": row.get("status"),
            "latency_ms": row.get("latency_ms"), "cost_cny": row.get("cost_cny"),
            "output_asset_id": row.get("output_asset_id"),
            "width": row.get("width"), "height": row.get("height"),
            "duration_ms": row.get("duration_ms"),
            "error_code": row.get("error_code"), "error_message": row.get("error_message"),
            "wall_ms": int((time.time() - t0) * 1000)}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="real-acc-"))
    config.DB_PATH = tmp / "app.db"
    config.ASSETS = tmp / "assets"
    config.ASSETS.mkdir(parents=True, exist_ok=True)
    db.init_db()

    # 只读复制一张真实商品图到临时素材库
    src = REF_SRC / "a5" / REF_ASSET_ID  # 内容寻址路径不一定如此，改用 DB 查询原文
    real_db = ROOT / "data" / "app.db"
    ref_file = None
    try:
        import sqlite3
        c = sqlite3.connect(f"file:{real_db}?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        row = c.execute("SELECT rel_path, mime FROM assets WHERE id=?", (REF_ASSET_ID,)).fetchone()
        c.close()
        if row:
            ref_file = REF_SRC / row["rel_path"]
    except Exception:  # noqa: BLE001
        pass
    if not ref_file or not ref_file.exists():
        print("❌ 找不到参考图，终止（不猜测替代素材）")
        return 2
    asset = storage.save_bytes(ref_file.read_bytes(), "image", "upload",
                               meta={"copied_from": REF_ASSET_ID, "real_acceptance": True})
    REF = asset["id"]
    print(f"临时库：{tmp}/app.db（业务库只读）｜参考图副本：{REF}")

    result: dict = {"date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "approved": {"max_calls": MAX_CALLS, "max_cny": MAX_CNY},
                    "isolation": {"temp_db": True, "business_db": "只读复制参考图",
                                  "worker": "未启动（脚本同步执行）"},
                    "steps": []}
    try:
        # 1) 项目 / 脚本 / 镜头
        pid = projects.create_project("真实验收-两镜", {
            "subject": "单瓶精华液", "quantity": 1,
            "forbidden_elements": "不要人物，不要添加其它商品"})["project_id"]
        script = projects.add_script(pid, {"summary": "瓶身特写转推近瓶盖"}, source="manual")
        projects.confirm_script(script["script_id"])
        shot_out = shots.add_shots(pid, script["script_id"], [
            {"description": "白色精华瓶置于浅灰台面，柔光，瓶身完整", "duration_s": 5},
            {"description": "镜头缓慢推近瓶盖与标签区域，保持瓶身不变形", "duration_s": 5}])
        shot_ids = shot_out["shot_ids"]
        result["steps"].append({"step": "project_script_shots", "project_id": pid,
                                "shots": len(shot_ids)})
        print(f"  项目 {pid[:14]}… 镜头 {len(shot_ids)} 个")

        # 2) 分镜图 × 2（真实调用）
        storyboards = []
        for i, sid in enumerate(shot_ids, start=1):
            guard("image", FLASH, 0.12)
            gen = shots.generate_storyboard(sid, FLASH, confirm=True,
                                            input_asset_id=REF, size="2048x2048")
            r = run_task(gen["task_id"], f"分镜图{i}")
            record(r["cost_cny"], **r)
            if r["status"] != "succeeded":
                raise RuntimeError(f"分镜图{i} 失败：{r['error_code']}")
            shots.attach_storyboard_asset(gen["storyboard_id"], r["output_asset_id"])
            shots.review_storyboard(gen["storyboard_id"], approve=True)
            storyboards.append({"shot_id": sid, "storyboard_id": gen["storyboard_id"],
                                "asset_id": r["output_asset_id"], "cost": r["cost_cny"]})
        result["steps"].append({"step": "storyboards", "items": storyboards})
        print(f"  分镜图 2 张完成")

        # 3) 整组范围（full）→ 确认 → 提交
        sc = scope.create_scope(pid, "full", shot_ids,
                                {"model_id": V10, "ratio": "9:16", "resolution": "720p"})
        print(f"  报价：¥{sc['total_estimate_cny']}（计划 {sc['planned_film_duration_s']}s）")
        scope.confirm_scope(sc["scope_id"])
        sub = scope.submit_scope(sc["scope_id"])
        result["steps"].append({"step": "scope", "mode": "full",
                                "estimate_cny": sc["total_estimate_cny"],
                                "batch_id": sub["batch_id"],
                                "tasks": len(sub["tasks"])})

        # 4) 逐镜视频 × 2（真实调用）
        clips = []
        for i, t in enumerate(sub["tasks"], start=1):
            guard("video", V10, 0.4536)
            r = run_task(t["task_id"], f"镜头{i}视频")
            record(r["cost_cny"], **r)
            if r["status"] == "succeeded":
                db.update("shot_takes", t["take_id"],
                          {"status": "succeeded", "asset_id": r["output_asset_id"],
                           "duration_ms": r["duration_ms"]})
            else:
                db.update("shot_takes", t["take_id"], {"status": "failed"})
            clips.append({"shot_id": t["shot_id"], "take_id": t["take_id"], **r})
        result["steps"].append({"step": "clips", "items": clips})

        # 5) 真实拼接（纯拼接）
        film = assembly.compose(pid)
        film_detail = assembly.get_film(film["film_id"])
        result["steps"].append({"step": "film", **film, "file": film_detail})
        print(f"  成片：{film['status']} v{film['version']} | 计划 {film['planned_duration_s']}s "
              f"| 实测 {film['actual_duration_ms']}ms | asset={film.get('asset_id')}")

        # 6) 导出
        exp = assembly.export_project(pid)
        result["steps"].append({"step": "export", **{k: exp[k] for k in
                                                    ("file_name", "bytes", "includes")}})
        print(f"  导出：{exp['file_name']} {exp['bytes']} bytes")

        # 7) 结论
        assert film["status"] == "ready", f"成片状态异常：{film['status']}"
        assert film["actual_duration_ms"] and film["actual_duration_ms"] > 8000, \
            f"成片时长异常：{film['actual_duration_ms']}"
        result["passed"] = True
    except BudgetStop as e:
        result.update({"passed": False, "stopped": str(e)})
        print(f"  ⛔ 预算/名额中止：{e}")
    except Exception as e:  # noqa: BLE001
        result.update({"passed": False, "error": f"{type(e).__name__}: {e}",
                       "trace": traceback.format_exc()[-800:]})
        print(f"  ❌ 失败：{type(e).__name__}: {e}")

    result["calls"] = CALLS
    result["usage"] = {"calls": len(CALLS), "calls_cap": MAX_CALLS,
                       "reserved_cny": round(RESERVED, 4),
                       "actual_cost_cny": round(SPENT, 4), "cap_cny": MAX_CNY}
    result["note"] = ("费用为应用侧估算，非供应商账单；未自动重试。")
    (OUT / "真实验收结果.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n调用 {len(CALLS)}/{MAX_CALLS}｜预占 ¥{RESERVED:.2f}/¥{MAX_CNY}"
          f"｜估算实付 ¥{SPENT:.2f}")
    print(f"结果：{'通过' if result.get('passed') else '未通过'}")
    print(f"证据：{OUT / '真实验收结果.json'}")
    return 0 if result.get("passed") else 1


if __name__ == "__main__":
    sys.exit(main())
