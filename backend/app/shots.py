"""S3：镜头（shots）与分镜（storyboards）。

规则（PRD v0.10 §2.3–§2.5、导演台路线）：
- 镜头时长只能用**模型原生区间内的整数秒**（复用 `config.resolve_duration_s`，禁止截断）。
- 增删/编辑/排序都要求**上一阶段（脚本）确认有效**；排序只影响成片顺序，不使镜头产物失效。
- **分镜图生成前必须报价**（`estimate_cost`），用户确认后才建图片任务（复用现有 `/api/tasks` 全链路）。
- 分镜多版本；**审核通过绑定 `storyboard_id + version`**；重生成 → 新版本，旧批准不继承。
- 单镜重做只影响该镜；历史分镜与 take 保留。
"""
from __future__ import annotations

from typing import Any

from . import config, db, director, projects
from .errors import err

SHOT_STATUS = ("draft", "storyboard_pending", "storyboard_approved", "ready")


def _require_script_confirmed(project_id: str) -> dict:
    """镜头相关操作的前置条件：当前最新脚本**确认有效**。"""
    r = director.readiness(project_id)
    cur = r["current"]["confirmation"]
    if not cur.get("valid"):
        raise err("SCRIPT_NOT_CONFIRMED",
                  "脚本尚未确认或确认已失效，请先重新确认脚本",
                  detail={"reason": cur.get("reason"), "script": r["current"]["script"]})
    return r


def _duration_for(project_id: str, duration_s: Any, model_id: str | None) -> int:
    """按模型原生区间校验镜头时长（严格；拒绝小数/越界）。"""
    mid = model_id or _default_video_model()
    return config.resolve_duration_s(mid, duration_s, strict=True)


def _default_video_model() -> str:
    """挑一个**可提交**的视频模型作为时长校验基准（优先 verified）。"""
    for mid, meta in config.MODEL_REGISTRY.items():
        if meta.get("kind") != "video" or meta.get("provider") != "ark":
            continue
        if config.model_submittability(mid)["submittable"]:
            return mid
    raise err("MODEL_NOT_OPENED", "当前没有可用的视频模型，无法确定时长规则")


def validate_shot_input(project_id: str, shots: list[dict], model_id: str | None = None) -> list[dict]:
    if not isinstance(shots, list) or not shots:
        raise err("VALIDATION_FAILED", "shots 不能为空")
    out = []
    for i, sh in enumerate(shots, start=1):
        desc = str(sh.get("description") or "").strip()
        if not desc:
            raise err("VALIDATION_FAILED", f"第 {i} 个镜头缺少画面描述",
                      detail={"field": "description", "seq": i})
        dur = _duration_for(project_id, sh.get("duration_s"), model_id)
        out.append({"seq": int(sh.get("seq") or i), "duration_s": dur,
                    "description": desc,
                    "motion": (sh.get("motion") or "").strip() or None})
    return out


# ---------------- 镜头 ----------------
def add_shots(project_id: str, script_id: str, shots: list[dict],
              model_id: str | None = None) -> dict:
    _require_script_confirmed(project_id)
    script = projects.get_script(script_id)
    if script["project_id"] != project_id:
        raise err("VALIDATION_FAILED", "脚本不属于该项目")
    clean = validate_shot_input(project_id, shots, model_id)
    rows = []
    with db.transaction() as c:
        base = c.execute("SELECT COALESCE(MAX(seq),0) m FROM shots WHERE project_id=?",
                         (project_id,)).fetchone()["m"]
        for i, sh in enumerate(clean, start=1):
            sid = db.new_id("shot")
            c.execute(
                "INSERT INTO shots(id, project_id, script_version_id, seq, duration_s, "
                "description, motion, status, revision, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (sid, project_id, script_id, base + i, sh["duration_s"], sh["description"],
                 sh["motion"], "draft", 1, db.now(), db.now()))
            rows.append(sid)
    return {"project_id": project_id, "shot_ids": rows, "count": len(rows)}


def list_shots(project_id: str) -> list[dict]:
    projects.get_project(project_id)
    rows = db.query("SELECT * FROM shots WHERE project_id=? ORDER BY seq", (project_id,))
    out = []
    for r in rows:
        sb = db.query("SELECT * FROM storyboards WHERE shot_id=? ORDER BY version DESC",
                      (r["id"],))
        out.append(_shot_view(r, sb))
    return out


def get_shot(shot_id: str) -> dict:
    row = db.get("shots", shot_id)
    if not row:
        raise err("SHOT_NOT_FOUND", f"镜头不存在：{shot_id}")
    sb = db.query("SELECT * FROM storyboards WHERE shot_id=? ORDER BY version DESC",
                  (row["id"],))
    return _shot_view(row, sb)


def update_shot(shot_id: str, *, description: str | None = None,
                duration_s: Any = None, motion: str | None = None,
                expected_revision: int | None = None) -> dict:
    row = db.get("shots", shot_id)
    if not row:
        raise err("SHOT_NOT_FOUND", f"镜头不存在：{shot_id}")
    if expected_revision is not None and int(expected_revision) != int(row["revision"]):
        raise err("SHOT_REVISION_CONFLICT",
                  f"镜头已被修改：当前 r{row['revision']}，提交 r{expected_revision}",
                  detail={"current_revision": row["revision"]})
    patch: dict[str, Any] = {"revision": int(row["revision"]) + 1, "updated_at": db.now()}
    invalidated = 0
    if description is not None:
        if not description.strip():
            raise err("VALIDATION_FAILED", "画面描述不能为空")
        patch["description"] = description.strip()
    if duration_s is not None:
        patch["duration_s"] = _duration_for(row["project_id"], duration_s, None)
    if motion is not None:
        patch["motion"] = motion.strip() or None
    db.update("shots", shot_id, patch)
    # 输入变化 → 该镜分镜确认与整组确认失效（不删除历史）
    if "description" in patch or "duration_s" in patch:
        director.invalidate("storyboard", project_id=row["project_id"],
                            object_id=shot_id, reason="shot_changed")
        director.invalidate("scope", project_id=row["project_id"],
                            reason="shot_changed")
        invalidated = 1
    return {**get_shot(shot_id), "invalidated": bool(invalidated)}


def delete_shot(shot_id: str) -> dict:
    row = db.get("shots", shot_id)
    if not row:
        raise err("SHOT_NOT_FOUND", f"镜头不存在：{shot_id}")
    db.query("DELETE FROM shots WHERE id=?", (shot_id,))
    director.invalidate("storyboard", project_id=row["project_id"],
                        object_id=shot_id, reason="shot_deleted")
    director.invalidate("scope", project_id=row["project_id"], reason="shot_deleted")
    return {"deleted": shot_id, "project_id": row["project_id"]}


def reorder_shots(project_id: str, order: list[str]) -> dict:
    """重排**只影响成片拼接顺序**；不使镜头/分镜产物失效；不触发生成。"""
    rows = db.query("SELECT id FROM shots WHERE project_id=?", (project_id,))
    existing = {r["id"] for r in rows}
    if set(order) != existing:
        raise err("VALIDATION_FAILED", "排序必须包含且仅包含该项目全部镜头",
                  detail={"expected": sorted(existing), "got": order})
    with db.transaction() as c:
        for i, sid in enumerate(order, start=1):
            c.execute("UPDATE shots SET seq=?, updated_at=? WHERE id=?",
                      (i, db.now(), sid))
        c.execute("UPDATE projects SET updated_at=? WHERE id=?", (db.now(), project_id))
    director.invalidate("film", project_id=project_id, reason="shot_reordered")
    return {"project_id": project_id, "order": order,
            "note": "仅影响成片顺序；镜头产物与分镜确认不受影响"}


def _shot_view(row: dict, storyboards: list[dict]) -> dict:
    approved = next((b for b in storyboards if b["status"] == "approved"), None)
    latest = storyboards[0] if storyboards else None
    return {
        "shot_id": row["id"], "project_id": row["project_id"],
        "script_version_id": row["script_version_id"], "seq": row["seq"],
        "duration_s": row["duration_s"], "description": row["description"],
        "motion": row["motion"], "status": row["status"],
        "revision": int(row["revision"] or 1),
        "storyboard": ({"storyboard_id": latest["id"], "version": latest["version"],
                        "asset_id": latest["asset_id"], "status": latest["status"],
                        "approved": bool(approved and approved["id"] == latest["id"])}
                       if latest else None),
        "storyboard_versions": len(storyboards),
    }


# ---------------- 分镜 ----------------
def quote_storyboard(shot_ids: list[str], model_id: str,
                     size: str = "2048x2048", prompt: str | None = None) -> dict:
    """分镜图**报价**：只算钱，不建任务、不调模型。"""
    from . import cloud_tasks

    if not shot_ids:
        raise err("VALIDATION_FAILED", "shot_ids 不能为空")
    items = []
    total = 0.0
    for sid in shot_ids:
        shot = db.get("shots", sid)
        if not shot:
            raise err("SHOT_NOT_FOUND", f"镜头不存在：{sid}")
        est = cloud_tasks.estimate_cost("image", model_id,
                                        {"size": size, "generation_mode": "text_to_image"})
        total += est
        items.append({"shot_id": sid, "estimated_cost_cny": est})
    return {"shots": items, "count": len(items), "total_estimate_cny": round(total, 4),
            "size": size, "model_id": model_id,
            "side_effects": {"tasks_created": 0, "model_called": False}}


def generate_storyboard(shot_id: str, model_id: str, confirm: bool,
                        size: str = "2048x2048", prompt: str | None = None,
                        input_asset_id: str | None = None) -> dict:
    """生成分镜图。**confirm=False 时只返回报价，不建任务**。"""
    from . import cloud_tasks, main as core

    shot = db.get("shots", shot_id)
    if not shot:
        raise err("SHOT_NOT_FOUND", f"镜头不存在：{shot_id}")
    if not confirm:
        return {**quote_storyboard([shot_id], model_id, size, prompt),
                "need_confirm": True,
                "hint": "确认后将创建 1 个图片任务（产生图片费用）"}
    if not config.model_submittability(model_id)["submittable"]:
        raise err("MODEL_NOT_OPENED", f"模型 {model_id} 不可提交")

    text = (prompt or "").strip() or shot["description"]
    task = core.create_task(core.TaskIn(
        kind="image", model_id=model_id, input_asset_id=input_asset_id,
        params={"prompt": text, "generation_mode":
                ("image_to_image" if input_asset_id else "text_to_image"), "size": size}))
    ver = int(db.query("SELECT COALESCE(MAX(version),0) v FROM storyboards WHERE shot_id=?",
                       (shot_id,))[0]["v"]) + 1
    sb = db.new_id("sb")
    db.insert("storyboards", {"id": sb, "shot_id": shot_id, "task_id": task["id"],
                              "asset_id": None, "version": ver, "status": "draft",
                              "reject_reason": None, "created_at": db.now()})
    db.update("shots", shot_id, {"status": "storyboard_pending", "updated_at": db.now()})
    return {"shot_id": shot_id, "storyboard_id": sb, "version": ver,
            "task_id": task["id"], "estimated_cost_cny": task.get("estimated_cost_cny"),
            "need_confirm": False}


def attach_storyboard_asset(storyboard_id: str, asset_id: str) -> dict:
    row = db.get("storyboards", storyboard_id)
    if not row:
        raise err("STORYBOARD_NOT_FOUND", f"分镜不存在：{storyboard_id}")
    db.update("storyboards", storyboard_id, {"asset_id": asset_id})
    return get_storyboard(storyboard_id)


def get_storyboard(storyboard_id: str) -> dict:
    row = db.get("storyboards", storyboard_id)
    if not row:
        raise err("STORYBOARD_NOT_FOUND", f"分镜不存在：{storyboard_id}")
    return {"storyboard_id": row["id"], "shot_id": row["shot_id"],
            "version": row["version"], "asset_id": row["asset_id"],
            "task_id": row["task_id"], "status": row["status"],
            "reject_reason": row["reject_reason"]}


def review_storyboard(storyboard_id: str, approve: bool,
                      reason: str | None = None) -> dict:
    row = db.get("storyboards", storyboard_id)
    if not row:
        raise err("STORYBOARD_NOT_FOUND", f"分镜不存在：{storyboard_id}")
    if not row["asset_id"]:
        raise err("STORYBOARD_NOT_READY", "分镜图尚未生成完成，无法审核",
                  detail={"storyboard_id": storyboard_id})
    shot = db.get("shots", row["shot_id"]) or {}
    if approve:
        db.update("storyboards", storyboard_id, {"status": "approved"})
        db.update("shots", row["shot_id"], {"status": "storyboard_approved",
                                           "updated_at": db.now()})
        confirmed = director.confirm_object(shot["project_id"], "storyboard",
                                            storyboard_id, int(row["version"]))
        return {"storyboard_id": storyboard_id, "status": "approved",
                "confirmation": confirmed}
    if not (reason or "").strip():
        raise err("VALIDATION_FAILED", "驳回必须填写原因", detail={"field": "reason"})
    db.update("storyboards", storyboard_id, {"status": "rejected",
                                            "reject_reason": reason.strip()})
    db.update("shots", row["shot_id"], {"status": "draft", "updated_at": db.now()})
    return {"storyboard_id": storyboard_id, "status": "rejected",
            "reason": reason.strip(),
            "hint": "只重做该镜分镜图即可，其它镜头不受影响"}
