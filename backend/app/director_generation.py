"""Durable director generation actions built on the canvas node execution contract.

Only ``prepare_generation`` belongs in the model's tool registry. Confirmation
comes from an explicit user action; neither quoting nor reading starts work.
All members are submitted together, and no failure triggers an automatic retry.
"""
from __future__ import annotations

import math
from typing import Any

from . import db, node_execution, projects
from .errors import AppError, err

MAX_NODES = 20
KIND = "generation"


def _project_in_tx(connection: Any, project_id: str) -> None:
    project = connection.execute("SELECT deleted_at FROM projects WHERE id=?", (project_id,)).fetchone()
    if project is None:
        raise err("PROJECT_NOT_FOUND", "项目不存在")
    if project["deleted_at"] is not None:
        raise err("PROJECT_DELETED", "该项目已删除，无法继续生成")


def _action_in_tx(connection: Any, project_id: str, action_id: str) -> dict:
    _project_in_tx(connection, project_id)
    row = connection.execute("SELECT * FROM director_actions WHERE id=? AND project_id=? AND kind=?",
                             (action_id, project_id, KIND)).fetchone()
    if row is None:
        raise err("DIRECTOR_ACTION_NOT_FOUND", "生成方案不存在，请重新准备")
    return dict(row)


def _validate_selection(node_ids: list[str], expected_revisions: dict[str, int],
                        request_id: str, output_mode: str) -> None:
    if output_mode not in ("clips", "film"):
        raise err("VALIDATION_FAILED", "请选择独立素材或完整成片")
    node_execution._required_string({"request_id": request_id}, "request_id")
    if (not isinstance(node_ids, list) or not 1 <= len(node_ids) <= MAX_NODES
            or any(not isinstance(node_id, str) or not node_id.strip() or len(node_id) > 200 for node_id in node_ids)
            or len(set(node_ids)) != len(node_ids)):
        raise err("VALIDATION_FAILED", f"请选择 1 至 {MAX_NODES} 个不同的生成节点")
    if (not isinstance(expected_revisions, dict) or set(expected_revisions) != set(node_ids)
            or any(isinstance(revision, bool) or not isinstance(revision, int) or revision < 1
                   for revision in expected_revisions.values())):
        raise err("VALIDATION_FAILED", "每个节点都需要当前配置修订号")


@projects.project_guard()
def prepare_generation(project_id: str, node_ids: list[str], expected_revisions: dict[str, int],
                       request_id: str, output_mode: str = "clips") -> dict:
    """Freeze one saved set of nodes and create all quotes plus its action atomically."""
    _validate_selection(node_ids, expected_revisions, request_id, output_mode)
    input_hash = node_execution._hash({"node_ids": node_ids, "expected_revisions": expected_revisions,
                                       "output_mode": output_mode})
    with db.transaction() as connection:
        _project_in_tx(connection, project_id)
        prior = connection.execute(
            "SELECT id FROM director_actions WHERE project_id=? AND request_id=? AND kind=? AND input_hash=?",
            (project_id, request_id, KIND, input_hash)).fetchone()
        if prior:
            action_id, replay = prior["id"], True
        else:
            quotes = [node_execution.quote_node_in_tx(connection, project_id, node_id,
                      {"expected_config_revision": expected_revisions[node_id]}) for node_id in node_ids]
            if output_mode == "film" and any(quote["input"]["kind"] != "video" for quote in quotes):
                raise err("VALIDATION_FAILED", "完整成片需要按镜头顺序选择视频生成节点")
            cost = round(sum(quote["estimated_cost_cny"] for quote in quotes), 4)
            if not math.isfinite(cost) or cost < 0:
                raise err("VALIDATION_FAILED", "生成费用估算不可用")
            now, action_id = db.now(), db.new_id("dact")
            frozen = {"node_ids": node_ids, "expected_revisions": expected_revisions,
                      "quotes": quotes, "output_mode": output_mode}
            connection.execute(
                "INSERT INTO director_actions(id,project_id,request_id,kind,input_hash,frozen_json,"
                "estimated_cost_cny,expires_at,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?, 'proposed',?,?)",
                (action_id, project_id, request_id, KIND, input_hash, db.dumps(frozen), cost,
                 min(quote["expires_at"] for quote in quotes), now, now))
            replay = False
    return {**get_action(project_id, action_id), "idempotent_replay": replay,
            "side_effects": {"tasks_created": 0, "model_called": False, "budget_deducted": False}}


@projects.project_guard()
def confirm_generation(project_id: str, action_id: str, body: dict) -> dict:
    """Explicit user confirmation: all tasks, executions and the action receipt commit once."""
    body = node_execution._body(body, {"client_request_id", "user_action"})
    request_id = node_execution._required_string(body, "client_request_id")
    if body.get("user_action") != "confirm_generate":
        raise err("CONFIRM_NOT_ALLOWED", "请确认本次生成范围后开始")
    with db.transaction() as connection:
        action = _action_in_tx(connection, project_id, action_id)
        used = connection.execute("SELECT id FROM director_actions WHERE project_id=? AND submit_client_request_id=?",
                                  (project_id, request_id)).fetchone()
        if used and used["id"] != action_id:
            raise err("DIRECTOR_ACTION_CONFLICT", "同一请求标识不能提交不同的生成方案")
        if action["status"] == "submitted":
            if action["submit_client_request_id"] != request_id:
                raise err("DIRECTOR_ACTION_CONFLICT", "该方案已经提交，请查看原任务")
            if not action["result_json"]:
                raise err("DIRECTOR_ACTION_CONFLICT", "生成回执缺失，请核对原任务")
            replay = True
        else:
            if action["status"] != "proposed":
                raise err("DIRECTOR_ACTION_CONFLICT", "生成方案状态已变化，请重新读取")
            if action["expires_at"] <= db.now():
                raise err("DIRECTOR_ACTION_EXPIRED", "生成方案已过期，请重新报价并确认")
            frozen = db.loads(action["frozen_json"])
            if frozen["output_mode"] not in ("clips", "film"):
                raise err("VALIDATION_FAILED", "生成方式无效，请重新准备")
            if (frozen["output_mode"] == "film"
                    and any(item["input"]["kind"] != "video" for item in frozen["quotes"])):
                raise err("VALIDATION_FAILED", "完整成片只能包含视频生成节点")
            # Validate every member before the first queue insert. The write transaction
            # also prevents another process from editing nodes between validation and use.
            quotes = []
            for item in frozen["quotes"]:
                try:
                    _, quote = node_execution.validate_node_quote_in_tx(
                        connection, project_id, item["node_id"], item["quote_id"],
                        expected_config_revision=frozen["expected_revisions"][item["node_id"]])
                except AppError as exc:
                    if exc.code in ("NODE_QUOTE_STALE", "NODE_QUOTE_CONSUMED", "NODE_QUOTE_NOT_FOUND",
                                    "NODE_QUOTE_EXPIRED", "CANVAS_NODE_NOT_FOUND"):
                        raise err("DIRECTOR_ACTION_STALE", "方案中的节点或报价已变化，请重新准备",
                                  detail={"node_id": item["node_id"], "cause": exc.code}) from exc
                    raise
                quotes.append(quote)
            total = round(sum(quote["estimated_cost_cny"] for quote in quotes), 4)
            if total != action["estimated_cost_cny"]:
                raise err("DIRECTOR_ACTION_STALE", "生成费用已变化，请重新报价")
            node_execution._budget_in_tx(connection, total)
            batch_id, now = db.new_id("batch"), db.now()
            connection.execute("INSERT INTO batches(id,name,created_at,params_json) VALUES(?,?,?,?)",
                               (batch_id, "导演素材生成", now, db.dumps({"action_id": action_id,
                                "mode": frozen["output_mode"], "count": len(quotes)})))
            tasks = []
            for index, quote in enumerate(quotes):
                receipt = node_execution.generate_node_in_tx(connection, project_id, quote["node_id"], {
                    "quote_id": quote["id"], "client_request_id": f"director:{action_id}:{index}",
                    "user_action": "confirm_generate"}, check_budget=False)
                connection.execute("UPDATE tasks SET batch_id=?,caller_id='director' WHERE id=?",
                                   (batch_id, receipt["task_id"]))
                tasks.append({key: receipt[key] for key in ("node_id", "execution_id", "task_id", "quote_id")})
            result = {"batch_id": batch_id, "output_mode": frozen["output_mode"], "tasks": tasks,
                      "submitted_at": now, "scope": _scope(frozen)}
            connection.execute("UPDATE director_actions SET status='submitted',submit_client_request_id=?,"
                               "result_json=?,updated_at=? WHERE id=?", (request_id, db.dumps(result), now, action_id))
            replay = False
    return {**get_action(project_id, action_id), "idempotent_replay": replay}


def _scope(frozen: dict) -> dict:
    film = frozen["output_mode"] == "film"
    return {"generate": True, "compose": film, "ordered_node_ids": frozen["node_ids"],
            "no_trim": film, "composition_cost_cny": 0}


def get_action(project_id: str, action_id: str) -> dict:
    """Read the durable receipt and current task state; never retry or recreate anything."""
    with db.conn() as connection:
        action = _action_in_tx(connection, project_id, action_id)
    frozen = db.loads(action["frozen_json"])
    result = db.loads(action.get("result_json")) or {}
    executions = [node_execution.get_execution(project_id, item["node_id"], item["execution_id"])
                  for item in result.get("tasks", [])]
    status = "proposed"
    if action["status"] == "submitted":
        if len(executions) != len(frozen["node_ids"]):
            raise err("DIRECTOR_ACTION_CONFLICT", "生成回执中的任务数量不完整，请核对任务记录")
        complete = sum(item["status"] == "succeeded" and item["asset_available"] for item in executions)
        status = ("running" if any(item["active"] for item in executions) else
                  "completed" if complete == len(executions) else "partial" if complete else "failed")
    media_status, composition, error = status, None, None
    film = frozen["output_mode"] == "film"
    if film and action["status"] == "submitted":
        if result.get("composition_action_id"):
            from . import director_composition
            composition = director_composition.get_composition_action(project_id, result["composition_action_id"])
            status = composition["status"]
            if status == "failed":
                error = (composition.get("asset_error") or (composition.get("job") or {}).get("error")
                         or {"code": "ASSEMBLY_FAILED", "message": "成片未完成，已生成的镜头仍可使用"})
        elif media_status in ("partial", "failed"):
            error = {"code": "FILM_MEDIA_INCOMPLETE", "message": "部分镜头未完成或素材不可用，未合成不完整视频",
                     "node_ids": [item["node_id"] for item in executions
                                  if item["status"] != "succeeded" or not item["asset_available"]]}
            composition = {"status": "blocked", "reason": "media_incomplete"}
        elif result.get("composition_error"):
            status, error = "failed", result["composition_error"]
            composition = {"status": "failed", "error": error}
        else:
            status = "running"
            composition = {"status": "waiting", "reason": "media_pending" if media_status == "running" else "preparing"}
    expired = action["expires_at"] <= db.now()
    return {"action_id": action_id, "project_id": project_id, "kind": KIND,
            "status": status, "action_status": action["status"], "request_id": action["request_id"],
            "node_ids": frozen["node_ids"], "quotes": frozen["quotes"], "output_mode": frozen["output_mode"],
            "estimated_cost_cny": action["estimated_cost_cny"], "expires_at": action["expires_at"],
            "expired": expired, "requires_user_action": action["status"] == "proposed" and not expired,
            "batch_id": result.get("batch_id"), "tasks": result.get("tasks", []), "executions": executions,
            "submit_client_request_id": action.get("submit_client_request_id"), "auto_film": film,
            "media_status": media_status, "composition": composition, "error": error, "scope": _scope(frozen),
            "created_at": action["created_at"], "submitted_at": result.get("submitted_at")}
