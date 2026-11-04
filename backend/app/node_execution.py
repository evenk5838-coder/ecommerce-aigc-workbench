"""One-node generation: frozen quote, explicit user confirmation, atomic task link.

This service is shared by canvas controls and future director tools.  Quoting
never creates a task.  ``generate_node`` is a user-confirmation boundary and
must not be registered as an unrestricted LLM tool.  Workers consume the normal
durable ``tasks`` queue; execution views read that queue without caching status.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from . import cloud_tasks, config, db, projects, storage
from .errors import AppError, err
from .model_specs import image_ratio

QUOTE_TTL_S = 600
TERMINAL = ("succeeded", "failed", "canceled", "interrupted")
REMOTE_TERMINAL = ("succeeded", "success", "failed", "cancelled", "canceled")
PARAM_KEYS = {"image": {"size", "ratio", "watermark"},
              "video": {"duration", "ratio", "resolution", "generate_audio", "watermark", "seed"}}


def _body(body: dict | None, allowed: set[str]) -> dict:
    if body is None:
        return {}
    if not isinstance(body, dict) or set(body) - allowed:
        raise err("VALIDATION_FAILED", "请求包含未知字段或格式不正确")
    return body


def _required_string(body: dict, key: str, *, limit: int = 200) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise err("VALIDATION_FAILED", f"{key} 必须为非空字符串且不超过 {limit} 字")
    return value


def _node(project_id: str, node_id: str, connection: Any = None) -> dict:
    if connection is None:
        projects.get_project(project_id)
        rows = db.query("SELECT * FROM canvas_nodes WHERE project_id=? AND node_id=?",
                        (project_id, node_id))
        node = rows[0] if rows else None
    else:
        project = connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if project is None:
            raise err("PROJECT_NOT_FOUND", "项目不存在")
        if project["deleted_at"] is not None:
            raise err("PROJECT_DELETED", "该项目已删除，无法继续生成")
        row = connection.execute("SELECT * FROM canvas_nodes WHERE project_id=? AND node_id=?",
                                 (project_id, node_id)).fetchone()
        node = dict(row) if row else None
    if node is None:
        raise err("CANVAS_NODE_NOT_FOUND", "节点不存在，请重新打开画布")
    return node


def _hash(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()


def _freeze(node: dict) -> tuple[dict, dict]:
    """Resolve exactly the parameters that the existing worker actually consumes."""
    # Lazy import avoids a main -> router -> service import cycle.
    from .main import TaskIn, resolve_task

    kind = node["type"]
    if kind not in ("image", "video"):
        raise err("NODE_NOT_EXECUTABLE", "请选择图片或视频生成节点")
    cfg = db.loads(node.get("config_json")) or {}
    mode = cfg.get("generation_mode")
    if mode not in (("text_to_image", "image_to_image") if kind == "image" else
                    ("text_to_video", "image_to_video")):
        raise err("GENERATION_MODE_CONFLICT", "请选择与节点对应的生成方式")
    model_id = cfg.get("model_id")
    if not isinstance(model_id, str) or not model_id.strip():
        raise err("VALIDATION_FAILED", "请为节点选择模型")
    # Local historical engines do not implement the same four-mode contract.
    if model_id in config.MODEL_REGISTRY and config.MODEL_REGISTRY[model_id].get("provider") not in config.CLOUD_PROVIDERS:
        raise err("NODE_NOT_EXECUTABLE", "当前节点生成仅支持已接入的云端图片或视频模型")
    prompt = cfg.get("prompt", "")
    if not isinstance(prompt, str) or len(prompt) > 20000:
        raise err("VALIDATION_FAILED", "提示词格式不正确或超过长度限制")
    params = cfg.get("params") or {}
    if not isinstance(params, dict) or set(params) - PARAM_KEYS[kind]:
        raise err("VALIDATION_FAILED", "节点包含当前生成模型未接入的参数",
                  detail={"allowed": sorted(PARAM_KEYS[kind])})
    params = dict(params)
    for key in ("watermark", "generate_audio"):
        if key in params and not isinstance(params[key], bool):
            raise err("VALIDATION_FAILED", f"{key} 必须为布尔值")
    if "seed" in params and (isinstance(params["seed"], bool) or not isinstance(params["seed"], int)):
        raise err("VALIDATION_FAILED", "seed 必须为整数")
    if model_id in config.aihubmix_models.EXTENSIONS:
        params = config.aihubmix_models.effective_candidate_params(
            model_id, kind, {**params, "generation_mode": mode})
    if kind == "image":
        params.setdefault("size", "1024x1024")
        # These pixel-aligned presets use the same display ratios as the canvas
        # controls.  In particular 2688x1536 is intentionally labelled 16:9.
        size = params["size"]
        display_ratio = image_ratio(size)
        if display_ratio is None:
            raise err("MODEL_CAPABILITY_MISMATCH", "请选择模型支持的图片尺寸")
        params.setdefault("ratio", display_ratio)
        if params["ratio"] not in (config.MODEL_CAPABILITIES.get(model_id) or {}).get("ratios", []) or params["ratio"] != display_ratio:
            raise err("MODEL_CAPABILITY_MISMATCH", "画面比例与图片尺寸不匹配")
    else:
        params.setdefault("ratio", "9:16")
        params.setdefault("resolution", "720p")
        params.setdefault("generate_audio", False)
    params.setdefault("watermark", False)
    params.update(prompt=prompt.strip(), generation_mode=mode)
    reference_id = cfg.get("input_asset_id")
    if mode.startswith("text_") and reference_id:
        raise err("GENERATION_MODE_CONFLICT", "文生模式不能携带参考图片")
    resolved = resolve_task(TaskIn(kind=kind, model_id=model_id, params=params,
                                   input_asset_id=reference_id), now_ts=db.now())
    reference = None
    if reference_id:
        asset = storage.load_asset(reference_id)
        digest = hashlib.sha256(storage.asset_path(asset).read_bytes()).hexdigest()
        if asset.get("sha256") and asset["sha256"] != digest:
            raise err("INVALID_REFERENCE_IMAGE", "参考图片内容发生变化，请重新上传")
        reference = {"asset_id": reference_id, "sha256": digest, "mime": asset.get("mime")}
    frozen = {"kind": resolved["kind"], "model_id": resolved["model_id"],
              "params": resolved["params"], "input_asset_id": resolved["input_asset_id"],
              "reference": reference}
    if config.MODEL_REGISTRY.get(model_id, {}).get("provider") in config.CLOUD_PROVIDERS:
        from . import media_policy, providers_aihubmix
        frozen["output_policy"] = (providers_aihubmix.output_policy(kind, model_id)
                                   if config.MODEL_REGISTRY[model_id]["provider"] == "aihubmix"
                                   else media_policy.specification(kind))
    return frozen, resolved


def _quote_view(row: dict) -> dict:
    frozen = db.loads(row["frozen_json"]) or {}
    return {"quote_id": row["id"], "project_id": row["project_id"], "node_id": row["node_id"],
            "config_revision": row["config_revision"], "input_hash": row["input_hash"],
            "estimated_cost_cny": row["estimated_cost_cny"], "expires_at": row["expires_at"],
            "input": frozen,
            "side_effects": {"task_created": False, "model_called": False, "budget_deducted": False}}


def quote_node_in_tx(connection: Any, project_id: str, node_id: str, body: dict | None = None) -> dict:
    """Create a quote on the caller's transaction; never commit or create a task."""
    body = _body(body, {"expected_config_revision"})
    node = _node(project_id, node_id, connection)
    expected = body.get("expected_config_revision")
    if expected is not None and (isinstance(expected, bool) or not isinstance(expected, int)):
        raise err("VALIDATION_FAILED", "expected_config_revision 必须为整数")
    if expected is not None and expected != node["config_revision"]:
        raise err("CANVAS_REVISION_CONFLICT", "节点设置已更新，请重新读取后生成")
    frozen, resolved = _freeze(node)
    now = db.now()
    row = {"id": db.new_id("nquote"), "project_id": project_id, "node_id": node_id,
           "node_record_id": node["id"], "config_revision": node["config_revision"],
           "input_hash": _hash(frozen), "frozen_json": db.dumps(frozen),
           "estimated_cost_cny": resolved["estimated_cost_cny"],
           "expires_at": now + QUOTE_TTL_S, "created_at": now}
    columns = ",".join(row)
    connection.execute(f"INSERT INTO canvas_node_quotes({columns}) VALUES({','.join('?' for _ in row)})",
                       tuple(row.values()))
    return _quote_view(row)


@projects.project_guard()
def quote_node(project_id: str, node_id: str, body: dict | None = None) -> dict:
    with db.transaction() as connection:
        return quote_node_in_tx(connection, project_id, node_id, body)


def _active(task: dict) -> bool:
    # A stopped local wait must not permit a second billed remote generation.
    return (task.get("status") not in TERMINAL or bool(task.get("remote_task_id") and
            task.get("status") != "succeeded" and task.get("remote_status") not in REMOTE_TERMINAL) or
            task.get("error_code") == "CREATE_RESULT_UNKNOWN")


def _task_cost_input(task: dict) -> dict:
    return {"kind": task.get("kind"), "model_id": task.get("model_id"),
            "params": db.loads(task.get("params_json")) or {},
            "input_asset_id": task.get("input_asset_id")}


def freeze_task_cost_reservation(task: dict, cost: float) -> dict:
    """Bind a direct task's positive estimate without duplicating its prompt."""
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost <= 0:
        raise err("BUDGET_RESERVATION_UNAVAILABLE", "生成费用预留无法确认，请重新报价")
    return {"version": 1, "kind": task["kind"], "model_id": task["model_id"],
            "input_hash": _hash(_task_cost_input(task)), "estimated_cost_cny": float(cost)}


def _reserved_cost_in_tx(connection: Any, task: dict) -> float:
    # A consumed, linked quote is the authoritative price for canvas/director
    # tasks, including after a capability or price-list change.
    quote = connection.execute(
        "SELECT q.* FROM canvas_node_executions e JOIN canvas_node_quotes q "
        "ON q.id=e.quote_id AND q.execution_id=e.id WHERE e.task_id=? AND q.consumed_at IS NOT NULL",
        (task["id"],)).fetchone()
    params = db.loads(task["params_json"]) or {}
    if quote is not None:
        frozen = db.loads(quote["frozen_json"]) or {}
        cost = quote["estimated_cost_cny"]
        if (frozen.get("kind") == task["kind"] and frozen.get("model_id") == task["model_id"]
                and frozen.get("params") == params and frozen.get("input_asset_id") == task.get("input_asset_id")
                and _hash(frozen) == quote["input_hash"]
                and not isinstance(cost, bool) and isinstance(cost, (int, float))
                and math.isfinite(cost) and cost > 0):
            return float(cost)
        raise err("BUDGET_RESERVATION_UNAVAILABLE", "有历史任务的费用记录需要核对，请核对后继续生成",
                  task_id=task["id"])
    if config.MODEL_REGISTRY.get(task["model_id"], {}).get("provider") == "aihubmix":
        usage = db.loads(task.get("usage_json")) or {}
        if not isinstance(usage, dict):
            raise err("BUDGET_RESERVATION_UNAVAILABLE", "历史费用记录格式需要核对", task_id=task["id"])
        if usage.get("cost_basis") == "unknown" or usage.get("cost_pending_verification") is True:
            raise err("BUDGET_RESERVATION_UNAVAILABLE", "已有生成费用待核对，请核对后继续生成",
                      task_id=task["id"])
        if "cost_reservation" in usage:
            reservation = usage["cost_reservation"]
            cost = reservation.get("estimated_cost_cny") if isinstance(reservation, dict) else None
            if (isinstance(reservation, dict) and type(reservation.get("version")) is int
                    and reservation["version"] == 1
                    and reservation.get("kind") == task["kind"]
                    and reservation.get("model_id") == task["model_id"]
                    and reservation.get("input_hash") == _hash(_task_cost_input(task))
                    and not isinstance(cost, bool) and isinstance(cost, (int, float))
                    and math.isfinite(cost) and cost > 0):
                return float(cost)
            raise err("BUDGET_RESERVATION_UNAVAILABLE", "费用预留与当前生成输入不一致，请先核对",
                      task_id=task["id"])
    try:
        if task["kind"] == "video" and config.MODEL_REGISTRY.get(task["model_id"], {}).get("provider") != "aihubmix":
            return config.historical_video_reservation_cny(task["model_id"], params)
        return cloud_tasks.estimate_cost(task["kind"], task["model_id"], params, legacy=True)
    except AppError as exc:
        raise err("BUDGET_RESERVATION_UNAVAILABLE", "有历史任务的费用尚无法核算，请先核对该任务后继续生成",
                  task_id=task["id"], detail={"cause": exc.code}) from exc


def _budget_in_tx(connection: Any, cost: float) -> None:
    """Keep estimates reserved until remote success has a settled local charge.

    A download/encoding failure after remote success does not undo the provider
    generation.  Such a task stays reserved even though local execution ended.
    Remote failed/cancelled terminal results follow the current provider's
    success-only billing contract; unknown remote states remain active above.
    """
    reserved = 0.0
    rows = connection.execute("SELECT * FROM tasks").fetchall()
    for record in rows:
        task = dict(record)
        remote_charge_unsettled = bool(task.get("remote_task_id") and
            task.get("remote_status") in ("success", "succeeded") and task.get("cost_cny") is None)
        if (not _active(task) and not remote_charge_unsettled) or config.MODEL_REGISTRY.get(task["model_id"], {}).get("provider") not in config.CLOUD_PROVIDERS:
            continue
        reserved += _reserved_cost_in_tx(connection, task)
    if not math.isfinite(cost) or cost < 0 or not math.isfinite(reserved):
        raise err("VALIDATION_FAILED", "生成费用估算不可用")
    cloud_tasks.budget_gate(round(cost + reserved, 4))


def validate_node_quote_in_tx(connection: Any, project_id: str, node_id: str, quote_id: str,
                              *, expected_config_revision: int | None = None) -> tuple[dict, dict]:
    """Validate the frozen input and active-task guard without consuming the quote."""
    quote = connection.execute("SELECT * FROM canvas_node_quotes WHERE id=? AND project_id=? AND node_id=?",
                               (quote_id, project_id, node_id)).fetchone()
    if quote is None:
        raise err("NODE_QUOTE_NOT_FOUND", "生成报价不存在，请重新报价")
    quote = dict(quote)
    if quote.get("consumed_at") is not None:
        raise err("NODE_QUOTE_CONSUMED", "该报价已用于生成，请查看原任务")
    if quote["expires_at"] <= db.now():
        raise err("NODE_QUOTE_EXPIRED", "报价已过期，请重新确认生成")
    node = _node(project_id, node_id, connection)
    if node["id"] != quote["node_record_id"]:
        raise err("NODE_QUOTE_STALE", "节点已重新创建，请重新报价")
    if expected_config_revision is not None and node["config_revision"] != expected_config_revision:
        raise err("NODE_QUOTE_STALE", "节点设置已变化，请重新报价")
    try:
        current, resolved = _freeze(node)
    except AppError as exc:
        if exc.code in ("INVALID_REFERENCE_IMAGE", "ASSET_NOT_FOUND", "REFERENCE_IMAGE_REQUIRED",
                        "GENERATION_MODE_CONFLICT", "VALIDATION_FAILED", "NODE_NOT_EXECUTABLE"):
            raise err("NODE_QUOTE_STALE", "生成输入已变化，请重新报价", detail={"cause": exc.code}) from exc
        raise
    if _hash(current) != quote["input_hash"] or resolved["estimated_cost_cny"] != quote["estimated_cost_cny"]:
        raise err("NODE_QUOTE_STALE", "生成输入或费用已变化，请重新报价")
    tasks = connection.execute("SELECT t.* FROM canvas_node_executions e JOIN tasks t ON t.id=e.task_id "
                               "WHERE e.project_id=? AND e.node_record_id=?", (project_id, node["id"])).fetchall()
    if any(_active(dict(task)) for task in tasks):
        raise err("NODE_EXECUTION_ACTIVE", "该节点已有生成任务，请等待完成", detail={"node_id": node_id})
    return node, quote


def generate_node_in_tx(connection: Any, project_id: str, node_id: str, body: dict,
                        *, check_budget: bool = True) -> dict:
    """Consume a quote and link its task atomically; the caller owns commit and receipt reads.

    Group callers may skip the per-node budget check only after reserving the complete
    group cost with ``_budget_in_tx`` on this same transaction.
    """
    body = _body(body, {"quote_id", "client_request_id", "user_action"})
    quote_id = _required_string(body, "quote_id")
    request_id = _required_string(body, "client_request_id")
    if body.get("user_action") != "confirm_generate":
        raise err("CONFIRM_NOT_ALLOWED", "请由用户确认后生成")
    payload_hash = _hash({"node_id": node_id, "quote_id": quote_id,
                          "user_action": "confirm_generate"})
    # Receipts remain readable after the node is removed; a deleted project stays closed.
    project = connection.execute("SELECT deleted_at FROM projects WHERE id=?", (project_id,)).fetchone()
    if project is None:
        raise err("PROJECT_NOT_FOUND", "项目不存在")
    if project["deleted_at"] is not None:
        raise err("PROJECT_DELETED", "该项目已删除，无法继续生成")
    prior = connection.execute("SELECT * FROM canvas_node_executions WHERE project_id=? AND client_request_id=?",
                               (project_id, request_id)).fetchone()
    if prior:
        if prior["payload_hash"] != payload_hash:
            raise err("NODE_EXECUTION_CONFLICT", "同一请求标识不能提交不同的生成内容")
        return {"execution_id": prior["id"], "node_id": node_id, "task_id": prior["task_id"],
                "quote_id": prior["quote_id"], "idempotent_replay": True}
    node, quote = validate_node_quote_in_tx(connection, project_id, node_id, quote_id)
    if check_budget:
        _budget_in_tx(connection, quote["estimated_cost_cny"])
    frozen = db.loads(quote["frozen_json"])
    now, task_id, execution_id = db.now(), db.new_id("task"), db.new_id("nexec")
    connection.execute(
        "INSERT INTO tasks(id,kind,status,params_json,model_id,model_revision,input_asset_id,progress,stage,"
        "attempts,cancel_requested,client_request_id,created_at,updated_at,external_request_id,caller_id,concept_mode) "
        "VALUES(?,?,'queued',?,?,?, ?,0,'queued',0,0,?,?,?,?, 'canvas',0)",
        (task_id, frozen["kind"], db.dumps(frozen["params"]), frozen["model_id"], "local-snapshot",
         frozen["input_asset_id"], "canvas:" + execution_id, now, now, project_id))
    connection.execute(
        "INSERT INTO canvas_node_executions(id,project_id,node_id,node_record_id,quote_id,client_request_id,"
        "payload_hash,task_id,input_hash,config_revision,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (execution_id, project_id, node_id, node["id"], quote_id, request_id, payload_hash, task_id,
         quote["input_hash"], quote["config_revision"], now))
    connection.execute("UPDATE canvas_node_quotes SET consumed_at=?,execution_id=? WHERE id=?",
                       (now, execution_id, quote_id))
    connection.execute("UPDATE projects SET updated_at=? WHERE id=?", (now, project_id))
    return {"execution_id": execution_id, "node_id": node_id, "task_id": task_id,
            "quote_id": quote_id, "idempotent_replay": False}


@projects.project_guard()
def generate_node(project_id: str, node_id: str, body: dict) -> dict:
    with db.transaction() as connection:
        receipt = generate_node_in_tx(connection, project_id, node_id, body)
    return {**get_execution(project_id, node_id, receipt["execution_id"]),
            "idempotent_replay": receipt["idempotent_replay"]}


def _output_availability(task: dict, asset: dict | None) -> tuple[bool, dict | None]:
    """Check the saved output without altering task history or scheduling repair.

    Asset metadata alone is not proof of a readable result. Stream the actual
    bytes so same-size replacements are detected without loading a video into
    memory. Errors intentionally omit local filesystem paths.
    """
    asset_id = task.get("output_asset_id")
    if not asset_id and task["status"] != "succeeded":
        return False, None

    def unavailable(reason: str, message: str, code: str = "OUTPUT_ASSET_UNAVAILABLE") -> tuple[bool, dict]:
        return False, {"code": code, "message": message, "asset_id": asset_id, "reason": reason}

    if not asset_id:
        return unavailable("output_not_recorded", "生成记录已完成，但没有可读取的产物")
    if not asset:
        return unavailable("asset_record_missing", "产物记录已不可用")
    if asset.get("kind") != task["kind"]:
        return unavailable("asset_kind_mismatch", "产物类型与生成任务不一致")
    if not asset.get("rel_path"):
        return unavailable("file_location_missing", "产物文件位置不可用")
    if not asset.get("sha256"):
        return unavailable("checksum_missing", "产物缺少完整性记录，暂时无法确认可用")
    try:
        path = storage.asset_path(asset).resolve()
        if not path.is_relative_to(config.ASSETS.resolve()):
            return unavailable("file_location_invalid", "产物文件位置无效")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except FileNotFoundError:
        return unavailable("file_missing", "产物文件已缺失")
    except (OSError, ValueError):
        return unavailable("file_unreadable", "产物文件暂时无法读取")
    if digest.hexdigest() != asset["sha256"]:
        return unavailable("content_changed", "产物文件内容已变化", "OUTPUT_ASSET_CHANGED")
    return True, None


def _execution_view(row: dict) -> dict:
    task = db.get("tasks", row["task_id"])
    if task is None:
        # Historical corruption is reported, never interpreted as a success.
        raise err("TASK_NOT_FOUND", "生成任务记录缺失", task_id=row["task_id"])
    asset = db.get("assets", task["output_asset_id"]) if task.get("output_asset_id") else None
    asset_available, asset_error = _output_availability(task, asset)
    quote = db.get("canvas_node_quotes", row["quote_id"])
    return {"execution_id": row["id"], "project_id": row["project_id"], "node_id": row["node_id"],
            "quote_id": row["quote_id"], "client_request_id": row["client_request_id"],
            "task_id": task["id"], "status": task["status"], "stage": task.get("stage"),
            "progress": task.get("progress"), "active": _active(task),
            "output_asset_id": task.get("output_asset_id"), "asset_id": task.get("output_asset_id"),
            "asset": {key: asset.get(key) for key in ("id", "kind", "mime", "width", "height", "duration_ms")} if asset else None,
            "asset_available": asset_available, "asset_error": asset_error,
            "result_status": "available" if asset_available else "pending" if _active(task) and not asset_error else "unavailable",
            "error": {"code": task["error_code"], "message": task.get("error_message")} if task.get("error_code") else None,
            "estimated_cost_cny": quote["estimated_cost_cny"] if quote else None,
            "input": db.loads(quote["frozen_json"]) if quote else None,
            "cost_cny": task.get("cost_cny"), "created_at": row["created_at"],
            "finished_at": task.get("finished_at"), "config_revision": row["config_revision"],
            "input_hash": row["input_hash"]}


def get_execution(project_id: str, node_id: str, execution_id: str) -> dict:
    projects.get_project(project_id)
    rows = db.query("SELECT * FROM canvas_node_executions WHERE id=? AND project_id=? AND node_id=?",
                    (execution_id, project_id, node_id))
    if not rows:
        raise err("NODE_EXECUTION_NOT_FOUND", "节点生成记录不存在")
    return _execution_view(rows[0])


def list_executions(project_id: str, node_id: str) -> dict:
    projects.get_project(project_id)
    rows = db.query("SELECT * FROM canvas_node_executions WHERE project_id=? AND node_id=? ORDER BY created_at DESC,id DESC LIMIT 100",
                    (project_id, node_id))
    return {"project_id": project_id, "node_id": node_id, "executions": [_execution_view(row) for row in rows]}


def get_last_execution(project_id: str, node_id: str) -> dict | None:
    node = _node(project_id, node_id)
    rows = db.query("SELECT * FROM canvas_node_executions WHERE project_id=? AND node_record_id=? ORDER BY created_at DESC,id DESC LIMIT 1",
                    (project_id, node["id"]))
    if not rows:
        return None
    latest = _execution_view(rows[0])
    successes = db.query(
        "SELECT e.* FROM canvas_node_executions e JOIN tasks t ON t.id=e.task_id "
        "WHERE e.project_id=? AND e.node_record_id=? AND t.status='succeeded' "
        "AND t.output_asset_id IS NOT NULL ORDER BY e.created_at DESC,e.id DESC LIMIT 1",
        (project_id, node["id"]))
    latest["last_successful_execution"] = _execution_view(successes[0]) if successes else None
    return latest
