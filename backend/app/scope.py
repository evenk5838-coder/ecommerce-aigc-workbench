"""S4：整组生成范围（scope）→ 用户确认 → 提交（batch + tasks + takes + 收据，同事务）。

规则（PRD v0.10 §2.6–§2.7、技术方案 v6 §4）：
- 两种**平级**模式：`full`（生成完整视频，之后自动拼接）/ `clips`（只交付独立片段）。
- **一次确认整组范围与费用**；报价不建任务、不调模型。
- **冻结每镜完整输入**（description/prompt/分镜版本/规格/时长），任一变化 → `scope_hash` 变 → 旧 scope 失效。
- 提交在**单个事务**内：消费 scope + 建 batch + 建 tasks + 建 takes + 写收据；失败整组回滚。
- 重复提交同一 scope → 返回**原 batch**（幂等）。
- 逐镜状态；**只重试用户选定的失败镜头**，成功片段保留；单 worker 顺序执行（如实标注）。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from . import config, db, director, projects, shots
from .errors import AppError as AppErrorLike, err

MODES = ("full", "clips")
QUOTE_TTL_S = 600


# ---------------- 冻结输入 ----------------
def _storyboard_for(shot: dict) -> dict | None:
    """只返回**当前有效**的分镜：最新版本 + 已批准 + 确认有效 + 与镜头 revision 匹配。"""
    rows = db.query("SELECT * FROM storyboards WHERE shot_id=? ORDER BY version DESC LIMIT 1",
                    (shot["id"],))
    if not rows:
        return None
    sb = rows[0]
    if sb["status"] != "approved" or not sb["asset_id"]:
        return None
    if int(sb.get("shot_revision") or 0) != int(shot.get("revision") or 1):
        return None
    if not director.current_confirmation("storyboard", sb["id"], sb["version"]).get("valid"):
        return None
    return sb


def freeze(project_id: str, shot_ids: list[str], specs: dict) -> dict:
    """把每镜完整输入冻结成快照；缺分镜/未通过 → 拒绝。"""
    model_id = specs.get("model_id")
    if not model_id:
        raise err("VALIDATION_FAILED", "缺少 model_id")
    if not config.model_submittability(model_id)["submittable"]:
        raise err("MODEL_NOT_OPENED", f"模型 {model_id} 不可提交")
    ratio = specs.get("ratio")
    resolution = specs.get("resolution")
    params = {"ratio": ratio, "resolution": resolution, "generate_audio": False}
    problems = config.validate_params_for_model(model_id, "video", params)
    if problems:
        raise err("MODEL_CAPABILITY_MISMATCH", "生成规格不被模型支持",
                  detail={"problems": problems})

    items = []
    for sid in shot_ids:
        shot = db.get("shots", sid)
        if not shot or shot["project_id"] != project_id:
            raise err("SHOT_NOT_FOUND", f"镜头不存在或不属于该项目：{sid}")
        sb = _storyboard_for(shot)
        if not sb:
            raise err("SHOT_NOT_READY", f"镜头缺少已通过的分镜：{sid}",
                      detail={"shot_id": sid})
        dur = config.resolve_duration_s(model_id, shot["duration_s"], strict=True)
        items.append({
            "shot_id": sid, "seq": int(shot["seq"]),
            "description": shot["description"], "prompt": shot["description"],
            "storyboard_id": sb["id"], "storyboard_version": int(sb["version"]),
            "input_asset_id": sb["asset_id"],
            "model_id": model_id, "ratio": ratio, "resolution": resolution,
            "duration_s": dur,
        })
    return {"items": items, "model_id": model_id, "ratio": ratio,
            "resolution": resolution}


def scope_hash(frozen: dict, mode: str) -> str:
    payload = {"mode": mode, "items": frozen["items"], "model_id": frozen["model_id"],
               "ratio": frozen["ratio"], "resolution": frozen["resolution"]}
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()


# ---------------- 报价 ----------------
def create_scope(project_id: str, mode: str, shot_ids: list[str],
                 specs: dict) -> dict:
    from . import cloud_tasks

    if mode not in MODES:
        raise err("VALIDATION_FAILED", f"mode 只能为 {list(MODES)}",
                  detail={"allowed": list(MODES), "got": mode})
    if not shot_ids:
        raise err("VALIDATION_FAILED", "shot_ids 不能为空")
    projects.get_project(project_id)
    r = director.readiness(project_id)
    if not r["current"]["confirmation"].get("valid"):
        raise err("SCRIPT_NOT_CONFIRMED", "脚本确认已失效，请先重新确认")

    frozen = freeze(project_id, shot_ids, specs)
    rows = []
    total = 0.0
    for it in frozen["items"]:
        est = cloud_tasks.estimate_cost("video", frozen["model_id"], {
            "duration": it["duration_s"], "ratio": frozen["ratio"],
            "resolution": frozen["resolution"]})
        total += est
        rows.append({"shot_id": it["shot_id"], "duration_s": it["duration_s"],
                     "estimate_cny": est})

    h = scope_hash(frozen, mode)
    sid = db.new_id("scp")
    db.insert("generation_scopes", {
        "id": sid, "project_id": project_id, "mode": mode,
        "shot_ids_json": db.dumps([i["shot_id"] for i in frozen["items"]]),
        "frozen_json": db.dumps(frozen), "scope_hash": h,
        "estimated_cost_cny": round(total, 4),
        "expires_at": db.now() + QUOTE_TTL_S,
        "approved_at": None, "consumed_at": None, "status": "quoted",
        "created_at": db.now()})
    return {"scope_id": sid, "mode": mode, "shots": rows,
            "total_estimate_cny": round(total, 4),
            "planned_film_duration_s": sum(i["duration_s"] for i in frozen["items"]),
            "scope_hash": h, "expires_at": db.now() + QUOTE_TTL_S,
            "side_effects": {"tasks_created": 0, "model_called": False}}


def confirm_scope(scope_id: str, confirm: bool = True) -> dict:
    row = _get_scope(scope_id)
    if not confirm:
        raise err("VALIDATION_FAILED", "确认动作需 confirm=true")
    if row["status"] != "quoted":
        raise err("SCOPE_ALREADY_USED", f"范围已使用或失效：{row['status']}",
                  detail={"status": row["status"]})
    if float(row["expires_at"]) <= db.now():
        raise err("SCOPE_EXPIRED", "报价已过期，请重新报价")
    db.update("generation_scopes", scope_id,
              {"status": "approved", "approved_at": db.now()})
    director.confirm_object(row["project_id"], "scope", scope_id, 1)
    return {"scope_id": scope_id, "approved": True, "mode": row["mode"],
            "expires_in_s": max(0, int(float(row["expires_at"]) - db.now())),
            "max_uses": 1}


# ---------------- 提交（单事务） ----------------
def submit_scope(scope_id: str) -> dict:
    from . import main as core

    # 幂等：已提交 → 返回原 batch
    receipt = db.query("SELECT * FROM submit_receipts WHERE scope_id=?", (scope_id,))
    if receipt:
        r = receipt[0]
        return {"batch_id": r["batch_id"], "idempotent_replay": True,
                "tasks": db.loads(r["tasks_json"]), "mode": r["mode"],
                "sequential": True}

    row = _get_scope(scope_id)
    if row["status"] != "approved":
        raise err("SCOPE_REQUIRED", f"范围未确认（当前 {row['status']}），请先确认",
                  detail={"status": row["status"]})
    if float(row["expires_at"]) <= db.now():
        raise err("SCOPE_EXPIRED", "确认已过期，请重新报价并确认")

    # 提交阶段复查：脚本确认仍有效（不只信任报价时点）
    r = director.readiness(row["project_id"])
    if not r["current"]["confirmation"].get("valid"):
        raise err("SCRIPT_NOT_CONFIRMED",
                  "脚本确认已失效，请重新确认后再提交",
                  detail={"reason": r["current"]["confirmation"].get("reason")})

    frozen = db.loads(row["frozen_json"]) or {}
    try:
        current = freeze(row["project_id"], [i["shot_id"] for i in frozen["items"]],
                         {"model_id": frozen["model_id"], "ratio": frozen["ratio"],
                          "resolution": frozen["resolution"]})
    except AppErrorLike as e:  # once 报价时的镜头/分镜已不可用 → 明确 stale
        if e.code in ("SHOT_NOT_READY", "SHOT_NOT_FOUND", "REFERENCE_IMAGE_REQUIRED",
                      "STORYBOARD_STALE"):
            raise err("SCOPE_STALE",
                      "镜头或分镜自报价后已变更，请重新报价并确认",
                      detail={"cause": e.code}) from e
        raise
    if scope_hash(current, row["mode"]) != row["scope_hash"]:
        raise err("SCOPE_STALE", "镜头或分镜自报价后已变更，请重新报价并确认",
                  detail={"scope_hash": row["scope_hash"]})

    mode = row["mode"]
    batch_id = db.new_id("batch")
    tasks_out: list[dict] = []
    with db.transaction() as c:
        cur = c.execute("UPDATE generation_scopes SET status='consumed', consumed_at=? "
                        "WHERE id=? AND status='approved'", (db.now(), scope_id))
        if cur.rowcount != 1:
            raise err("SCOPE_ALREADY_USED", "该范围已被使用，请重新报价")
        c.execute("INSERT INTO batches(id, name, created_at, params_json) VALUES(?,?,?,?)",
                  (batch_id, f"{mode}-{row['project_id']}", db.now(),
                   db.dumps({"count": len(frozen["items"]), "mode": mode})))
        for it in frozen["items"]:
            tid = db.new_id("task")
            now = db.now()
            params = {"prompt": it["prompt"], "duration": it["duration_s"],
                      "ratio": it["ratio"], "resolution": it["resolution"],
                      "generate_audio": False}
            c.execute(
                "INSERT INTO tasks(id, kind, status, params_json, model_id, model_revision, "
                "input_asset_id, progress, stage, attempts, cancel_requested, batch_id, "
                "created_at, updated_at, template_id, template_version, external_request_id, "
                "caller_id, concept_mode) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tid, "video", "queued", db.dumps(params), it["model_id"],
                 "local-snapshot", it["input_asset_id"], 0.0, "queued", 0, 0, batch_id,
                 now, now, None, None, row["project_id"], "creative", 0))
            take_id = db.new_id("take")
            c.execute("INSERT INTO shot_takes(id, shot_id, task_id, asset_id, duration_ms, "
                      "spec_json, cost_cny, status, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                      (take_id, it["shot_id"], tid, None, None,
                       db.dumps({"duration_s": it["duration_s"], "ratio": it["ratio"],
                                 "resolution": it["resolution"],
                                 "model_id": it["model_id"],
                                 "prompt": it["prompt"],
                                 "input_asset_id": it["input_asset_id"],
                                 "storyboard_id": it["storyboard_id"],
                                 "storyboard_version": it["storyboard_version"]}),
                       None, "queued", now))
            tasks_out.append({"shot_id": it["shot_id"], "task_id": tid, "take_id": take_id})
        c.execute("INSERT INTO submit_receipts(id, scope_id, project_id, batch_id, mode, "
                  "tasks_json, scope_hash, created_at) VALUES(?,?,?,?,?,?,?,?)",
                  (db.new_id("rcp"), scope_id, row["project_id"], batch_id, mode,
                   db.dumps(tasks_out), row["scope_hash"], db.now()))
        c.execute("UPDATE projects SET updated_at=? WHERE id=?",
                  (db.now(), row["project_id"]))
    return {"batch_id": batch_id, "mode": mode, "tasks": tasks_out,
            "auto_film": mode == "full", "sequential": True,
            "note": "单 worker 顺序执行，非并行"}


# ---------------- 逐镜状态 / 定向重试 ----------------
def project_status(project_id: str) -> dict:
    """刷新恢复唯一入口：项目 + 镜头 + take + batch + 成片。**不调用模型**。"""
    projects.get_project(project_id)
    r = director.readiness(project_id)
    shot_rows = db.query("SELECT * FROM shots WHERE project_id=? ORDER BY seq", (project_id,))
    out = []
    for s in shot_rows:
        takes = db.query("SELECT * FROM shot_takes WHERE shot_id=? ORDER BY created_at DESC",
                         (s["id"],))
        latest = takes[0] if takes else None
        out.append({
            "shot_id": s["id"], "seq": s["seq"], "duration_s": s["duration_s"],
            "description": s["description"],
            "storyboard": shots.get_shot(s["id"])["storyboard"],
            "take": ({"take_id": latest["id"], "task_id": latest["task_id"],
                      "status": latest["status"], "asset_id": latest["asset_id"],
                      "actual_duration_ms": latest["duration_ms"]} if latest else None),
            "takes": len(takes),
        })
    films = db.query("SELECT * FROM films WHERE project_id=? ORDER BY version DESC",
                     (project_id,)) if _has("films") else []
    return {"project_id": project_id, "mode": r["stages"][3]["status"],
            "current_stage": r["current"]["stage"], "shots": out,
            "allowed_actions": r["allowed_actions"],
            "warnings": r["warnings"],
            "films": [{"film_id": f["id"], "version": f["version"], "status": f["status"],
                       "asset_id": f["asset_id"], "duration_ms": f["duration_ms"],
                       "partial": bool(f["partial"])} for f in films]}


def retry_take(take_id: str) -> dict:
    """**只重做该 take**（用户选定的失败镜头）；成功镜头不动，历史保留。"""
    take = db.get("shot_takes", take_id)
    if not take:
        raise err("TAKE_NOT_FOUND", f"镜头产物不存在：{take_id}")
    if take["status"] in ("queued", "running"):
        raise err("TAKE_STILL_RUNNING", "该镜头仍在生成中，不能重试")
    if take["status"] == "succeeded":
        raise err("VALIDATION_FAILED", "成功的镜头无需重试；如需重做请新建生成范围",
                  detail={"take_id": take_id})
    spec = db.loads(take.get("spec_json")) or {}
    from . import main as core
    new_task = core.create_task(core.TaskIn(
        kind="video", model_id=spec.get("model_id") or _model_of(take),
        input_asset_id=spec.get("input_asset_id"),
        params={"prompt": spec.get("prompt") or "", "duration": spec["duration_s"],
                "ratio": spec.get("ratio"), "resolution": spec.get("resolution"),
                "generate_audio": False}))
    new_take = db.new_id("take")
    db.insert("shot_takes", {"id": new_take, "shot_id": take["shot_id"],
                             "task_id": new_task["id"], "asset_id": None,
                             "duration_ms": None, "spec_json": take["spec_json"],
                             "cost_cny": None, "status": "queued", "created_at": db.now()})
    return {"take_id": new_take, "task_id": new_task["id"], "shot_id": take["shot_id"],
            "estimated_cost_cny": new_task.get("estimated_cost_cny"),
            "note": "只重试该镜；其它镜头与历史产物不变"}


def _model_of(take: dict) -> str:
    task = db.get("tasks", take["task_id"]) or {}
    return task.get("model_id") or "doubao-seedance-1-0-pro-fast-251015"


def _get_scope(scope_id: str) -> dict:
    row = db.get("generation_scopes", scope_id)
    if not row:
        raise err("SCOPE_NOT_FOUND", f"生成范围不存在：{scope_id}")
    return row


def _has(table: str) -> bool:
    return bool(db.query("SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                         (table,)))
