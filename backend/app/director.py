"""导演台就绪度 + 确认持久化（S2 修复 / 渐进式导演台）。

核心规则（PRD v0.10 + 导演台路线 20261026）：
- **确认必须持久化**：`confirmations` 表记录 object_type/object_id/object_version/status；
  `confirmed_at` 只表示"曾经确认过"，**不能代表当前有效**。
- **确认绑定具体版本**：新版本不继承旧确认；上游变更使下游确认 `invalidated` 并记原因。
- **失效原因与允许动作由后端给出**，前端 `currentStep` 只控制查看位置，不构成执行授权。
- **打开项目/切换步骤/回看历史不调用任何模型**（本模块无模型依赖）。
"""
from __future__ import annotations

from typing import Any

from . import db, projects
from .errors import err

# 五组业务步骤（内部八阶段映射；对用户只暴露这五组）
STAGES: list[dict] = [
    {"key": "requirements", "label": "需求", "internal": ["requirements"]},
    {"key": "script", "label": "脚本与镜头计划", "internal": ["script", "shot_plan"]},
    {"key": "storyboard", "label": "分镜画面与审核", "internal": ["storyboard"]},
    {"key": "video", "label": "视频生成", "internal": ["generate"]},
    {"key": "delivery", "label": "成片交付", "internal": ["film", "export"]},
]

OBJECT_TYPES = ("script", "shot_plan", "storyboard", "scope", "film")


# ---------------- 确认 ----------------
def confirm_object(project_id: str, object_type: str, object_id: str,
                   object_version: int) -> dict:
    if object_type not in OBJECT_TYPES:
        raise err("VALIDATION_FAILED", f"未知确认对象类型：{object_type}",
                  detail={"allowed": list(OBJECT_TYPES)})
    projects.get_project(project_id)      # 校验项目存在
    existing = db.query(
        "SELECT * FROM confirmations WHERE object_type=? AND object_id=? AND object_version=?",
        (object_type, object_id, object_version))
    if existing:
        row = existing[0]
        if row["status"] != "active":
            # 重新确认：把旧的置为 superseded 再插新行（保留历史）
            db.update("confirmations", row["id"],
                      {"status": "superseded", "invalidated_at": db.now()})
        else:
            return {"confirmation_id": row["id"], "status": "active",
                    "object_type": object_type, "object_id": object_id,
                    "object_version": object_version, "reused": True}
    cid = db.new_id("cfm")
    db.insert("confirmations", {
        "id": cid, "project_id": project_id, "object_type": object_type,
        "object_id": object_id, "object_version": int(object_version),
        "status": "active", "reason": None,
        "created_at": db.now(), "invalidated_at": None})
    return {"confirmation_id": cid, "status": "active", "object_type": object_type,
            "object_id": object_id, "object_version": int(object_version), "reused": False}


def current_confirmation(object_type: str, object_id: str,
                         object_version: int | None) -> dict:
    """返回某对象**指定版本**的确认状态（不跨版本继承）。"""
    if object_version is None:
        return {"confirmed": False, "valid": False, "reason": "no_version"}
    rows = db.query(
        "SELECT * FROM confirmations WHERE object_type=? AND object_id=? AND object_version=? "
        "ORDER BY created_at DESC LIMIT 1",
        (object_type, object_id, int(object_version)))
    if not rows:
        return {"confirmed": False, "valid": False, "reason": "never_confirmed",
                "object_version": int(object_version)}
    row = rows[0]
    active = row["status"] == "active"
    return {"confirmed": True, "valid": active, "status": row["status"],
            "object_version": int(object_version),
            "confirmed_at": row["created_at"],
            "invalidated_at": row["invalidated_at"],
            "reason": row["reason"] if not active else None}


def invalidate(object_type: str, *, project_id: str | None = None,
               object_id: str | None = None, reason: str) -> int:
    """把匹配的**活动**确认置为 invalidated（持久化，不删除历史）。"""
    sql = "SELECT * FROM confirmations WHERE status='active' AND object_type=?"
    args: list[Any] = [object_type]
    if project_id:
        sql += " AND project_id=?"
        args.append(project_id)
    if object_id:
        sql += " AND object_id=?"
        args.append(object_id)
    rows = db.query(sql, args)
    now = db.now()
    for r in rows:
        db.update("confirmations", r["id"],
                  {"status": "invalidated", "reason": reason, "invalidated_at": now})
    return len(rows)


def invalidate_after_upstream_change(*, project_id: str, change: str) -> dict:
    """上游变更 → 下游确认失效（返回失效数量，便于前端提示）。"""
    mapping = {
        "brief": ["script", "shot_plan"],
        "script": ["shot_plan"],
        "shot_plan": ["storyboard", "scope"],
        "shot": ["storyboard", "scope"],
    }
    out = {}
    for ot in mapping.get(change, []):
        out[ot] = invalidate(ot, project_id=project_id,
                             reason=f"upstream_{change}_changed")
    return out


# ---------------- 就绪度 ----------------
def _shot_current_approved(shot: dict) -> bool:
    """当前镜头输入是否"有效通过"：最新分镜版本已批准 + 该版本确认有效 + revision 匹配。

    历史批准不授权当前输入（B1 核心规则）。
    """
    rows = db.query("SELECT * FROM storyboards WHERE shot_id=? ORDER BY version DESC LIMIT 1",
                    (shot["id"],))
    if not rows:
        return False
    sb = rows[0]
    if sb["status"] != "approved" or not sb["asset_id"]:
        return False
    if int(sb.get("shot_revision") or 0) != int(shot.get("revision") or 1):
        return False
    cfm = current_confirmation("storyboard", sb["id"], sb["version"])
    return bool(cfm.get("valid"))


def _latest_script(project_id: str) -> dict | None:
    rows = db.query("SELECT * FROM script_versions WHERE project_id=? ORDER BY version DESC LIMIT 1",
                    (project_id,))
    return rows[0] if rows else None


def _table_exists(name: str) -> bool:
    return bool(db.query("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)))


def readiness(project_id: str, now_ts: float | None = None) -> dict:
    """项目当前阶段 / 确认是否有效 / 失效原因 / 允许动作。**不调用模型**。"""
    project = projects.get_project(project_id)
    brief = db.loads(project.get("brief_json")) or {}
    script = _latest_script(project_id)
    script_cfm = (current_confirmation("script", script["id"], script["version"])
                  if script else {"confirmed": False, "valid": False, "reason": "no_script"})

    facts_ready = bool((brief.get("subject") or "").strip())
    script_valid = bool(script_cfm.get("valid"))

    shots = []
    if _table_exists("shots"):
        shots = db.query("SELECT * FROM shots WHERE project_id=? ORDER BY seq", (project_id,))
    shots_defined = len(shots) > 0
    shots_valid = False
    shot_ready = []          # 每镜是否"当前有效通过"
    if shots_defined and _table_exists("storyboards"):
        for sh in shots:
            shot_ready.append(_shot_current_approved(sh))
        shots_valid = all(shot_ready)

    takes = []
    if _table_exists("shot_takes"):
        takes = db.query("SELECT * FROM shot_takes WHERE shot_id IN "
                         "(SELECT id FROM shots WHERE project_id=?)", (project_id,))
    takes_ok = bool(takes) and all(t["status"] in ("succeeded", "failed") for t in takes) \
        and any(t["status"] == "succeeded" for t in takes)
    films = []
    if _table_exists("films"):
        films = db.query("SELECT * FROM films WHERE project_id=? ORDER BY version DESC",
                         (project_id,))
    film_ready = any(f["status"] == "ready" for f in films)

    stage_status = {
        "requirements": "done" if facts_ready else "current",
        "script": "done" if script_valid else ("current" if facts_ready else "locked"),
        "storyboard": ("done" if shots_valid else
                       ("current" if script_valid else "locked")),
        "video": ("done" if takes_ok else
                  ("current" if shots_valid else "locked")),
        "delivery": "done" if film_ready else ("current" if takes_ok else "locked"),
    }

    films_ready = film_ready
    takes_any = any(t["status"] == "succeeded" for t in takes) if takes else False

    allowed: list[str] = []
    if not facts_ready:
        allowed += ["save_brief"]
    else:
        allowed += ["edit_brief"]
        allowed.append("generate_script" if not script else "regenerate_script")
        if script and not script_valid:
            allowed.append("confirm_script")
        if script_valid:
            allowed.append("edit_shot_plan")
            if not shots_defined:
                allowed.append("add_shots")
            elif not shots_valid:
                allowed += ["generate_storyboard", "review_storyboard"]
            else:
                allowed += ["quote_generation", "confirm_generation"]
                if takes_any:
                    allowed.append("compose_film")
                if films_ready:
                    allowed.append("export")

    stages = []
    for s in STAGES:
        st = stage_status.get(s["key"], "locked")
        stages.append({"key": s["key"], "label": s["label"], "status": st,
                       "visible": st != "locked",
                       "actions": [a for a in allowed
                                   if _action_stage(a) == s["key"]]})

    warnings = []
    if script and not script_cfm.get("valid"):
        warnings.append({"object": "script", "version": script["version"],
                         "reason": script_cfm.get("reason"),
                         "message": "脚本确认已失效或未确认，请重新核对"})
    if shots_defined and not shots_valid:
        warnings.append({"object": "shot_review", "reason": "storyboards_not_all_approved",
                         "message": "存在未通过的分镜，需审核后才能生成视频"})

    return {
        "project_id": project_id,
        "project": {"name": project["name"], "status": project.get("status")},
        "facts": {"brief": brief, "ready": facts_ready},
        "current": {
            "stage": next((s["key"] for s in stages if s["status"] == "current"),
                          "delivery" if film_ready else "requirements"),
            "script": ({"script_id": script["id"], "version": script["version"],
                        "source": script.get("source"),
                        "confirmed_at": script.get("confirmed_at")} if script else None),
            "confirmation": script_cfm,
        },
        "stages": stages,
        "allowed_actions": allowed,
        "warnings": warnings,
        "counts": {"scripts": db.query(
            "SELECT COUNT(*) c FROM script_versions WHERE project_id=?", (project_id,))[0]["c"],
            "shots": len(shots), "takes": len(takes), "films": len(films)},
    }


_STAGE_OF_ACTION = {
    "save_brief": "requirements", "edit_brief": "requirements",
    "generate_script": "script", "regenerate_script": "script", "confirm_script": "script",
    "edit_shot_plan": "script", "add_shots": "script",
    "generate_storyboard": "storyboard", "review_storyboard": "storyboard",
    "quote_generation": "video", "confirm_generation": "video",
    "compose_film": "delivery", "export": "delivery",
}


def _action_stage(action: str) -> str:
    return _STAGE_OF_ACTION.get(action, "requirements")
