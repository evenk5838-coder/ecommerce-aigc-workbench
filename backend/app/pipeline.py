"""生产链路回写：任务完成/失败/取消 → 分镜素材、镜头 take、镜头状态、full 模式自动合成。

设计要点（本轮收尾）：
- **由 worker 调用**（生产路径），不依赖测试脚本串联。
- **幂等**：重复完成不会重复写 take、不会重复合成成片（按 batch 去重）。
- **重启**：`reconcile()` 把中断任务对应的 take/分镜标记为 interrupted，不自动重跑。
- **取消**：take 标记 canceled。
- **失败**：take 标记 failed，镜头回 draft；**full 模式不合成**（缺镜）。
- 自动合成仅在：模式 `full` + 该 batch 全部任务 succeeded + 尚无同 batch 成片。
"""
from __future__ import annotations

import json

from . import db


def on_task_succeeded(task: dict) -> dict:
    """任务成功 → 回写分镜/镜头产物；必要时触发 full 自动合成。"""
    task_id = task["id"]
    out = {"storyboard": 0, "take": 0, "auto_film": None}

    # 1) 分镜图任务 → 挂素材
    sbs = db.query("SELECT * FROM storyboards WHERE task_id=?", (task_id,))
    for sb in sbs:
        if sb["asset_id"] == task.get("output_asset_id"):
            out["storyboard"] += 1
            continue
        db.update("storyboards", sb["id"], {"asset_id": task.get("output_asset_id")})
        out["storyboard"] += 1

    # 2) 镜头视频任务 → 更新 take 与镜头状态（幂等）
    takes = db.query("SELECT * FROM shot_takes WHERE task_id=?", (task_id,))
    for t in takes:
        if t["status"] == "succeeded" and t["asset_id"] == task.get("output_asset_id"):
            out["take"] += 1
            continue
        db.update("shot_takes", t["id"], {
            "status": "succeeded", "asset_id": task.get("output_asset_id"),
            "duration_ms": task.get("duration_ms"), "cost_cny": task.get("cost_cny")})
        if t["shot_id"]:
            db.update("shots", t["shot_id"], {"status": "ready", "updated_at": db.now()})
        out["take"] += 1

    # 3) full 模式：整组成功 → 自动合成
    if takes:
        out["auto_film"] = maybe_auto_compose(task.get("batch_id"))
    return out


def on_task_failed(task: dict, status: str = "failed") -> dict:
    """任务失败/取消/中断 → 回写 take 与镜头状态（不自动合成）。"""
    out = {"storyboard": 0, "take": 0}
    for sb in db.query("SELECT * FROM storyboards WHERE task_id=?", (task["id"],)):
        # 分镜失败：保持 draft，可重试该镜
        out["storyboard"] += 1
    for t in db.query("SELECT * FROM shot_takes WHERE task_id=?", (task["id"],)):
        if t["status"] in ("succeeded",):
            continue
        db.update("shot_takes", t["id"], {"status": status})
        if t["shot_id"] and status in ("failed", "canceled", "interrupted"):
            db.update("shots", t["shot_id"],
                      {"status": "storyboard_approved", "updated_at": db.now()})
        out["take"] += 1
    return out


def maybe_auto_compose(batch_id: str | None) -> dict | None:
    """full 模式：该 batch 全部任务成功且尚无成片 → 自动拼接。"""
    if not batch_id:
        return None
    receipt = db.query("SELECT * FROM submit_receipts WHERE batch_id=?", (batch_id,))
    if not receipt:
        return None
    r = receipt[0]
    if r["mode"] != "full":
        return None
    # 幂等：同一 batch 只自动合成一次
    if _has("films"):
        rows = db.query("SELECT * FROM films WHERE source_batch=?", (batch_id,))
        if rows:
            return {"skipped": "already_composed", "film_id": rows[0]["id"]}
    tasks = db.query("SELECT status FROM tasks WHERE batch_id=?", (batch_id,))
    if not tasks:
        return None
    if any(t["status"] != "succeeded" for t in tasks):
        return {"skipped": "not_all_succeeded",
                "pending": [t["status"] for t in tasks if t["status"] != "succeeded"]}
    from . import assembly
    try:
        film = assembly.compose(r["project_id"], source_batch=batch_id)
        return {"composed": True, **{k: film[k] for k in ("film_id", "version", "status")}}
    except Exception as e:  # noqa: BLE001  自动合成失败不掩盖任务成功
        db.insert("assembly_errors", {"id": db.new_id("aerr"), "project_id": r["project_id"],
                                      "batch_id": batch_id,
                                      "error": f"{type(e).__name__}: {e}"[:300],
                                      "created_at": db.now()})
        return {"composed": False, "error": f"{type(e).__name__}: {e}"[:200]}


def on_reconcile_interrupted(task_ids: list[str]) -> dict:
    """重启恢复：把中断任务对应的 take 标记 interrupted（不自动重跑）。"""
    n = 0
    for tid in task_ids:
        for t in db.query("SELECT * FROM shot_takes WHERE task_id=?", (tid,)):
            if t["status"] in ("queued", "running", "loading", "postprocess"):
                db.update("shot_takes", t["id"], {"status": "interrupted"})
                n += 1
    return {"takes_interrupted": n}


def _has(table: str) -> bool:
    return bool(db.query("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                         (table,)))
