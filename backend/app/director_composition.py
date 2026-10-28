"""Confirmed, durable composition of explicitly ordered existing video assets.

Preparation only probes local copies. The worker concatenates every confirmed
input, never silently drops a clip and never retries an interrupted render.
"""
from __future__ import annotations

import hashlib
import logging
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Any

from . import assembly, canvas, config, db, node_execution, projects, storage
from .errors import AppError, err

KIND = "composition"
MAX_SOURCES = 20
ACTION_TTL_S = 600
_stop = threading.Event()
_thread: threading.Thread | None = None
_thread_lock = threading.Lock()
_log = logging.getLogger(__name__)


def _project(connection: Any, project_id: str) -> None:
    row = connection.execute("SELECT deleted_at FROM projects WHERE id=?", (project_id,)).fetchone()
    if row is None:
        raise err("PROJECT_NOT_FOUND", "项目不存在")
    if row["deleted_at"] is not None:
        raise err("PROJECT_DELETED", "该项目已删除")


def _action(connection: Any, project_id: str, action_id: str) -> dict:
    _project(connection, project_id)
    row = connection.execute("SELECT * FROM director_actions WHERE id=? AND project_id=? AND kind=?",
                             (action_id, project_id, KIND)).fetchone()
    if row is None:
        raise err("DIRECTOR_ACTION_NOT_FOUND", "合成方案不存在")
    return dict(row)


def _source(connection: Any, project_id: str, source: dict) -> dict:
    """Resolve optional canvas/execution ownership from persisted records."""
    row = connection.execute("SELECT * FROM assets WHERE id=?", (source["asset_id"],)).fetchone()
    if row is None or row["kind"] != "video":
        raise err("CANVAS_INVALID_REFERENCE", "请选择已有的视频素材")
    asset = dict(row)
    node_id, execution_id = source.get("node_id"), source.get("execution_id")
    execution = None
    if execution_id:
        execution = connection.execute(
            "SELECT e.*,t.status,t.output_asset_id FROM canvas_node_executions e "
            "JOIN tasks t ON t.id=e.task_id WHERE e.id=? AND e.project_id=?",
            (execution_id, project_id)).fetchone()
        if (not execution or execution["status"] != "succeeded"
                or execution["output_asset_id"] != asset["id"]
                or (node_id and execution["node_id"] != node_id)):
            raise err("CANVAS_INVALID_REFERENCE", "视频必须对应本项目中已成功的生成记录")
        node_id = execution["node_id"]
    node = None
    if node_id:
        node = connection.execute("SELECT * FROM canvas_nodes WHERE project_id=? AND node_id=?",
                                  (project_id, node_id)).fetchone()
        if not node or (execution and execution["node_record_id"] != node["id"]):
            raise err("CANVAS_INVALID_REFERENCE", "视频来源节点不属于当前项目或已被替换")
        if not execution and (db.loads(node["binding_json"]) or {}).get("asset_id") != asset["id"]:
            execution = connection.execute(
                "SELECT e.*,t.status,t.output_asset_id FROM canvas_node_executions e "
                "JOIN tasks t ON t.id=e.task_id WHERE e.project_id=? AND e.node_record_id=? "
                "AND t.status='succeeded' AND t.output_asset_id=? ORDER BY e.created_at DESC LIMIT 1",
                (project_id, node["id"], asset["id"])).fetchone()
            if not execution:
                raise err("CANVAS_INVALID_REFERENCE", "视频与来源节点不匹配")
    return {"asset": asset, "asset_id": asset["id"], "node_id": node_id,
            "node_record_id": node["id"] if node else None,
            "execution_id": execution["id"] if execution else None}


def _read_asset(asset: dict) -> bytes:
    path = storage.asset_path(asset).resolve()
    if not path.is_relative_to(config.ASSETS.resolve()):
        raise err("ASSEMBLY_FAILED", "素材文件位置无效")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise err("ASSEMBLY_FAILED", "视频素材文件缺失或无法读取", detail={"asset_id": asset["id"]}) from exc
    digest = hashlib.sha256(data).hexdigest()
    if not data or digest != asset["sha256"]:
        raise err("DIRECTOR_ACTION_STALE", "视频素材内容发生变化，请重新导入并准备合成")
    return data


def _decode(path: Path) -> dict:
    """Validate the entire local video stream, not just its container header."""
    try:
        result = subprocess.run([storage.ffmpeg_exe(), "-nostdin", "-v", "error", "-xerror",
                                 "-protocol_whitelist", "file,pipe", "-i", str(path),
                                 "-map", "0:v:0", "-an", "-f", "null", "-"],
                                capture_output=True, timeout=120)
        info = storage.probe_video(path)
    except Exception as exc:
        raise err("ASSEMBLY_FAILED", "视频无法完成解码验证") from exc
    if (result.returncode or not info.get("width") or not info.get("height")
            or not info.get("duration_ms") or info["duration_ms"] <= 0):
        raise err("ASSEMBLY_FAILED", "视频存在损坏或没有有效画面与时长")
    return info


def _verify_frozen(connection: Any, project_id: str, source: dict) -> dict:
    try:
        resolved = _source(connection, project_id, source)
        if any(resolved[key] != source.get(key) for key in ("node_id", "node_record_id", "execution_id")):
            raise err("DIRECTOR_ACTION_STALE", "来源节点已变化，请重新准备合成")
        if resolved["asset"]["sha256"] != source["sha256"]:
            raise err("DIRECTOR_ACTION_STALE", "来源素材已变化，请重新准备合成")
        return resolved["asset"]
    except AppError as exc:
        if exc.code == "CANVAS_INVALID_REFERENCE":
            raise err("DIRECTOR_ACTION_STALE", "来源视频已变化，请重新准备合成") from exc
        raise


def _freeze_sources(resolved: list[dict]) -> list[dict]:
    """Read/check/probe local copies without holding a database transaction."""
    frozen_sources = []
    with tempfile.TemporaryDirectory(prefix="composition-prepare-") as tmp:
        for index, source in enumerate(resolved):
            data = _read_asset(source["asset"])
            path = Path(tmp) / f"clip-{index:03d}.mp4"
            path.write_bytes(data)
            info = _decode(path)
            frozen_sources.append({key: value for key, value in source.items() if key != "asset"} |
                                  {"sha256": hashlib.sha256(data).hexdigest(), "metadata": info})
    return frozen_sources


@projects.project_guard()
def prepare_composition(project_id: str, sources: list[dict], request_id: str,
                        title: str = "成片视频") -> dict:
    node_execution._required_string({"request_id": request_id}, "request_id")
    node_execution._required_string({"title": title}, "title")
    if not isinstance(sources, list) or not 1 <= len(sources) <= MAX_SOURCES:
        raise err("VALIDATION_FAILED", f"请按顺序选择 1 至 {MAX_SOURCES} 条视频")
    normalized = []
    for source in sources:
        source = node_execution._body(source, {"asset_id", "node_id", "execution_id"})
        node_execution._required_string(source, "asset_id")
        normalized.append({key: node_execution._required_string(source, key) for key in source})
    input_hash = node_execution._hash({"sources": normalized, "title": title})
    with db.conn() as connection:
        prior = connection.execute("SELECT id FROM director_actions WHERE project_id=? AND request_id=? "
                                   "AND kind=? AND input_hash=?", (project_id, request_id, KIND, input_hash)).fetchone()
        resolved = [] if prior else [_source(connection, project_id, source) for source in normalized]
    if prior:
        return {**get_composition_action(project_id, prior["id"]), "idempotent_replay": True}
    frozen_sources = _freeze_sources(resolved)
    frozen = {"sources": frozen_sources, "title": title, "no_trim": True}
    with db.transaction() as connection:
        _project(connection, project_id)
        # Another process may have completed the same preparation during probing.
        prior = connection.execute("SELECT id FROM director_actions WHERE project_id=? AND request_id=? "
                                   "AND kind=? AND input_hash=?", (project_id, request_id, KIND, input_hash)).fetchone()
        action_id, now = prior["id"] if prior else db.new_id("dact"), db.now()
        if not prior:
            for source in frozen_sources:
                _verify_frozen(connection, project_id, source)
            connection.execute("INSERT INTO director_actions(id,project_id,request_id,kind,input_hash,frozen_json,"
                               "estimated_cost_cny,expires_at,status,created_at,updated_at) "
                               "VALUES(?,?,?,?,?,?,0,?,'proposed',?,?)", (action_id, project_id, request_id, KIND,
                                input_hash, db.dumps(frozen), now + ACTION_TTL_S, now, now))
    return {**get_composition_action(project_id, action_id), "idempotent_replay": bool(prior)}


@projects.project_guard()
def confirm_composition(project_id: str, action_id: str, body: dict) -> dict:
    body = node_execution._body(body, {"client_request_id", "user_action"})
    request_id = node_execution._required_string(body, "client_request_id")
    if body.get("user_action") != "confirm_compose":
        raise err("CONFIRM_NOT_ALLOWED", "请确认视频顺序后开始合成")
    with db.transaction() as connection:
        action = _action(connection, project_id, action_id)
        used = connection.execute("SELECT id FROM director_actions WHERE project_id=? AND submit_client_request_id=?",
                                  (project_id, request_id)).fetchone()
        if used and used["id"] != action_id:
            raise err("DIRECTOR_ACTION_CONFLICT", "同一请求标识不能提交不同的方案")
        if action["status"] == "submitted":
            if action["submit_client_request_id"] != request_id or not action["result_json"]:
                raise err("DIRECTOR_ACTION_CONFLICT", "该方案已提交，请查看原合成任务")
            replay = True
        else:
            if action["status"] != "proposed":
                raise err("DIRECTOR_ACTION_CONFLICT", "合成方案状态已变化")
            if action["expires_at"] <= db.now():
                raise err("DIRECTOR_ACTION_EXPIRED", "合成方案已过期，请重新准备")
            frozen = db.loads(action["frozen_json"])
            for source in frozen["sources"]:
                _read_asset(_verify_frozen(connection, project_id, source))
            job_id, now = db.new_id("comp"), db.now()
            connection.execute("INSERT INTO director_compositions(id,project_id,action_id,status,input_json,created_at,updated_at) "
                               "VALUES(?,?,?,'queued',?,?,?)", (job_id, project_id, action_id, db.dumps(frozen), now, now))
            connection.execute("UPDATE director_actions SET status='submitted',submit_client_request_id=?,"
                               "result_json=?,updated_at=? WHERE id=?", (request_id,
                                db.dumps({"composition_id": job_id, "submitted_at": now}), now, action_id))
            replay = False
    return {**get_composition_action(project_id, action_id), "idempotent_replay": replay}


def get_composition_action(project_id: str, action_id: str) -> dict:
    with db.conn() as connection:
        action = _action(connection, project_id, action_id)
        row = connection.execute("SELECT * FROM director_compositions WHERE action_id=? AND project_id=?",
                                 (action_id, project_id)).fetchone()
        job = dict(row) if row else None
    if action["status"] == "submitted" and job is None:
        raise err("DIRECTOR_ACTION_CONFLICT", "合成任务回执缺失")
    frozen = db.loads(action["frozen_json"])
    asset = db.get("assets", job["output_asset_id"]) if job and job["output_asset_id"] else None
    available, asset_error = node_execution._output_availability(
        {"status": job["status"] if job else "queued", "kind": "video",
         "output_asset_id": job["output_asset_id"] if job else None}, asset)
    status = "proposed" if not job else ("running" if job["status"] in ("queued", "running") else
               "completed" if job["status"] == "succeeded" and available else "failed")
    expired = action["expires_at"] <= db.now()
    receipt = db.loads(action["result_json"]) or {}
    return {"action_id": action_id, "project_id": project_id, "kind": KIND, "status": status,
            "action_status": action["status"], "sources": frozen["sources"], "title": frozen["title"],
            "request_id": action["request_id"], "expires_at": action["expires_at"], "expired": expired,
            "created_at": action["created_at"], "submitted_at": receipt.get("submitted_at"),
            "submit_client_request_id": action["submit_client_request_id"],
            "requires_user_action": action["status"] == "proposed" and not expired,
            "estimated_cost_cny": 0, "model_called": False, "no_trim": True,
            "asset_available": available, "asset_error": asset_error,
            "generation_action_id": frozen.get("generation_action_id"),
            "composition_id": job["id"] if job else None, "job": ({key: value for key, value in job.items()
                if key not in ("input_json", "error_json")} | {"error": db.loads(job["error_json"])}) if job else None,
            "asset": {key: asset.get(key) for key in ("id", "kind", "mime", "width", "height", "duration_ms")} if asset else None,
            "asset_id": asset["id"] if available else None, "result_node_id": job["result_node_id"] if job else None,
            "actual_duration_ms": asset["duration_ms"] if available else None,
            "planned_duration_ms": sum(source["metadata"]["duration_ms"] for source in frozen["sources"])}


def _film_inputs(connection: Any, project_id: str, action_id: str) -> tuple[dict, dict, list[dict]] | None:
    """Accept only the exact executions covered by the original film approval.

    A later successful execution on the same displayed node is not a substitute.
    Layout/config edits do not change already approved execution inputs, while a
    deleted/recreated node changes its record identity and cannot be substituted.
    """
    _project(connection, project_id)
    row = connection.execute("SELECT * FROM director_actions WHERE id=? AND project_id=? AND kind='generation'",
                             (action_id, project_id)).fetchone()
    if not row or row["status"] != "submitted":
        return None
    action = dict(row)
    frozen, receipt = db.loads(action["frozen_json"]), db.loads(action["result_json"]) or {}
    if frozen.get("output_mode") != "film" or receipt.get("composition_action_id") or receipt.get("composition_error"):
        return None
    expected_scope = {"generate": True, "compose": True, "ordered_node_ids": frozen["node_ids"],
                      "no_trim": True, "composition_cost_cny": 0}
    tasks, quotes = receipt.get("tasks", []), frozen.get("quotes", [])
    if (not action["submit_client_request_id"] or receipt.get("scope") != expected_scope
            or receipt.get("output_mode") != "film" or not 1 <= len(tasks) <= MAX_SOURCES
            or len(quotes) != len(tasks) or [item["node_id"] for item in tasks] != frozen["node_ids"]
            or [item["node_id"] for item in quotes] != frozen["node_ids"]):
        raise err("DIRECTOR_ACTION_CONFLICT", "成片授权范围与生成回执不一致，未开始合成")
    resolved = []
    for task, quote in zip(tasks, quotes):
        execution = connection.execute(
            "SELECT e.*,t.status,t.kind,t.output_asset_id,t.remote_task_id,t.remote_status,t.error_code,"
            "q.node_record_id AS quoted_record,q.config_revision AS quoted_revision,q.input_hash AS quoted_hash "
            "FROM canvas_node_executions e JOIN tasks t ON t.id=e.task_id "
            "JOIN canvas_node_quotes q ON q.id=e.quote_id WHERE e.id=? AND e.project_id=?",
            (task["execution_id"], project_id)).fetchone()
        if (not execution or execution["node_id"] != task["node_id"] or execution["task_id"] != task["task_id"]
                or execution["quote_id"] != task["quote_id"] or execution["quote_id"] != quote["quote_id"]
                or execution["node_record_id"] != execution["quoted_record"]
                or execution["quoted_revision"] != frozen["expected_revisions"][task["node_id"]]
                or execution["quoted_hash"] != quote["input_hash"] or execution["kind"] != "video"
                or quote["input"]["kind"] != "video"):
            raise err("DIRECTOR_ACTION_CONFLICT", "视频生成记录与已确认镜头不一致，未开始合成")
        if execution["status"] != "succeeded" or node_execution._active(dict(execution)):
            return None
        resolved.append(_source(connection, project_id, {
            "asset_id": execution["output_asset_id"], "node_id": task["node_id"], "execution_id": task["execution_id"]}))
    return action, receipt, resolved


def _record_film_error(project_id: str, action_id: str, exc: Exception) -> None:
    error = ({"code": exc.code, "message": exc.message} if isinstance(exc, AppError) else
             {"code": "ASSEMBLY_FAILED", "message": "成片准备未完成，镜头素材已保留，请重新准备合成"})
    with db.transaction() as connection:
        row = connection.execute("SELECT result_json FROM director_actions WHERE id=? AND project_id=? "
                                 "AND status='submitted'", (action_id, project_id)).fetchone()
        if row:
            receipt = db.loads(row["result_json"]) or {}
            if not receipt.get("composition_action_id") and not receipt.get("composition_error"):
                receipt["composition_error"] = error
                connection.execute("UPDATE director_actions SET result_json=?,updated_at=? WHERE id=?",
                                   (db.dumps(receipt), db.now(), action_id))


def enqueue_ready_films() -> int:
    """Derive at most one local job from a durable, explicit film confirmation.

    This is worker-only, never a model tool or a GET side effect. Failed local
    preparation is recorded once; queued/running/interrupted jobs are never
    duplicated. The deterministic action ID and unique job action_id also
    protect cross-process scheduling. Probing runs outside the write transaction.
    """
    with db.conn() as connection:
        candidates = connection.execute(
            "SELECT a.id,a.project_id FROM director_actions a JOIN projects p ON p.id=a.project_id "
            "WHERE a.kind='generation' AND a.status='submitted' AND p.deleted_at IS NULL "
            "AND json_extract(a.frozen_json,'$.output_mode')='film' "
            "AND json_extract(a.result_json,'$.composition_action_id') IS NULL "
            "AND json_extract(a.result_json,'$.composition_error') IS NULL ORDER BY a.created_at,a.id").fetchall()
    for candidate in candidates:
        project_id, generation_id = candidate["project_id"], candidate["id"]
        with projects._project_lock(project_id):
            try:
                with db.conn() as connection:
                    ready = _film_inputs(connection, project_id, generation_id)
                if ready is None:
                    continue
                action, receipt, resolved = ready
                sources = _freeze_sources(resolved)
                frozen = {"sources": sources, "title": "成片视频", "no_trim": True,
                          "generation_action_id": generation_id,
                          "authorization": {"user_action": "confirm_generate",
                                            "client_request_id": action["submit_client_request_id"]}}
                with db.transaction() as connection:
                    current = _film_inputs(connection, project_id, generation_id)
                    if current is None:
                        continue
                    if current[0]["frozen_json"] != action["frozen_json"] or current[1] != receipt:
                        raise err("DIRECTOR_ACTION_CONFLICT", "成片授权回执已变化，未开始合成")
                    # Recheck exact output identity and SHA after probing; no FFmpeg
                    # is invoked while the database write lock is held.
                    for source in sources:
                        _read_asset(_verify_frozen(connection, project_id, source))
                    composition_action_id, job_id, now = "film_" + generation_id, db.new_id("comp"), db.now()
                    connection.execute(
                        "INSERT INTO director_actions(id,project_id,request_id,kind,input_hash,frozen_json,"
                        "estimated_cost_cny,expires_at,status,result_json,created_at,updated_at) "
                        "VALUES(?,?,?,?,?,?,0,?,'submitted',?,?,?)",
                        (composition_action_id, project_id, "film:" + generation_id, KIND,
                         node_execution._hash(frozen), db.dumps(frozen), now,
                         db.dumps({"composition_id": job_id, "submitted_at": now}), now, now))
                    connection.execute(
                        "INSERT INTO director_compositions(id,project_id,action_id,status,input_json,created_at,updated_at) "
                        "VALUES(?,?,?,'queued',?,?,?)", (job_id, project_id, composition_action_id, db.dumps(frozen), now, now))
                    receipt.update({"composition_action_id": composition_action_id, "composition_id": job_id})
                    connection.execute("UPDATE director_actions SET result_json=?,updated_at=? WHERE id=?",
                                       (db.dumps(receipt), now, generation_id))
                return 1
            except Exception as exc:
                # No task/generation API is called here. A bad source cannot cause
                # repeated probing or any automatic model charge on later polls.
                if isinstance(exc, AppError) and exc.code in ("PROJECT_DELETED", "PROJECT_NOT_FOUND"):
                    continue
                _record_film_error(project_id, generation_id, exc)
    return 0


def _publish_result(job: dict, frozen: dict, asset: dict) -> None:
    """Publish a playable asset node and the successful receipt together."""
    with db.transaction() as connection:
        _project(connection, job["project_id"])
        current = connection.execute("SELECT status FROM director_compositions WHERE id=?", (job["id"],)).fetchone()
        if not current or current["status"] != "running":
            raise err("DIRECTOR_ACTION_CONFLICT", "合成任务状态已变化")
        nodes = connection.execute("SELECT node_id,position_json FROM canvas_nodes WHERE project_id=?",
                                   (job["project_id"],)).fetchall()
        positions = {node["node_id"]: db.loads(node["position_json"]) for node in nodes}
        related = [positions[source["node_id"]] for source in frozen["sources"] if source.get("node_id") in positions]
        position = {"x": max((point["x"] for point in related), default=0) + 560,
                    "y": min((point["y"] for point in related), default=100)}
        node_id, now = "composition_" + job["id"], db.now()
        connection.execute("INSERT INTO canvas_nodes(id,project_id,node_id,type,position_json,config_json,"
                           "binding_json,config_revision,created_at,updated_at) VALUES(?,?,?,'asset',?,?,?,1,?,?)",
                           (db.new_id("node"), job["project_id"], node_id, db.dumps(position),
                            db.dumps({"title": frozen["title"]}), db.dumps({"asset_id": asset["id"]}), now, now))
        state = canvas._state(job["project_id"], connection)
        edges = db.loads(state["edges_json"]) or []
        for source_id in dict.fromkeys(source.get("node_id") for source in frozen["sources"]):
            if source_id in positions:
                edges.append({"from": source_id, "to": node_id})
        connection.execute("INSERT INTO canvas_states(project_id,schema_version,layout_revision,viewport_json,edges_json,updated_at) "
                           "VALUES(?,1,?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET layout_revision=excluded.layout_revision,"
                           "edges_json=excluded.edges_json,updated_at=excluded.updated_at", (job["project_id"],
                            state["layout_revision"] + 1, state["viewport_json"], db.dumps(edges), now))
        connection.execute("UPDATE director_compositions SET status='succeeded',output_asset_id=?,result_node_id=?,"
                           "finished_at=?,updated_at=? WHERE id=?", (asset["id"], node_id, now, now, job["id"]))
        connection.execute("UPDATE projects SET updated_at=? WHERE id=?", (now, job["project_id"]))


def execute_next() -> dict | None:
    enqueue_ready_films()
    with db.conn() as connection:
        row = connection.execute("SELECT c.* FROM director_compositions c JOIN projects p ON p.id=c.project_id "
                                 "WHERE c.status='queued' AND p.deleted_at IS NULL ORDER BY c.created_at,c.id LIMIT 1").fetchone()
        if not row:
            return None
        job = dict(row)
    # Match the project's deletion lock, including the time spent outside SQLite.
    with projects._project_lock(job["project_id"]):
        with db.transaction() as connection:
            # Recheck after acquiring the project lock and the cross-process write
            # lock. Multiple callers may have observed the same queued candidate.
            current = connection.execute("SELECT c.status,p.deleted_at FROM director_compositions c "
                                         "JOIN projects p ON p.id=c.project_id WHERE c.id=?", (job["id"],)).fetchone()
            if not current or current["status"] != "queued" or current["deleted_at"] is not None:
                return None
            now = db.now()
            connection.execute("UPDATE director_compositions SET status='running',started_at=?,updated_at=? WHERE id=?",
                               (now, now, job["id"]))
        try:
            frozen = db.loads(job["input_json"])
            with tempfile.TemporaryDirectory(prefix="director-compose-") as tmp:
                paths = []
                with db.conn() as connection:
                    _project(connection, job["project_id"])
                    assets = [_verify_frozen(connection, job["project_id"], source) for source in frozen["sources"]]
                for index, asset in enumerate(assets):
                    path = Path(tmp) / f"clip-{index:03d}.mp4"
                    path.write_bytes(_read_asset(asset))
                    _decode(path)
                    paths.append(path)
                output = Path(tmp) / "output.mp4"
                ok, _ = assembly.concat_clips(paths, output)
                if not ok:
                    raise err("ASSEMBLY_FAILED", "视频拼接未完成，请检查素材编码后重新准备")
                info = _decode(output)
                expected_ms = sum(source["metadata"]["duration_ms"] for source in frozen["sources"])
                if abs(info["duration_ms"] - expected_ms) > max(200, len(paths) * 100):
                    raise err("ASSEMBLY_FAILED", "成片时长与全部片段不一致，未发布不完整结果")
                asset = storage.save_bytes(output.read_bytes(), "video", "director_composition", {
                    "composition_id": job["id"], "project_id": job["project_id"], "sources": frozen["sources"], "no_trim": True})
                _publish_result(job, frozen, asset)
        except Exception as exc:
            error = {"code": exc.code, "message": exc.message} if isinstance(exc, AppError) else {
                "code": "ASSEMBLY_FAILED", "message": "合成未完成，请检查素材后重新准备"}
            # A failure here leaves 'running' for startup recovery; never requeue it.
            with db.transaction() as connection:
                connection.execute("UPDATE director_compositions SET status='failed',error_json=?,finished_at=?,updated_at=? "
                                   "WHERE id=? AND status='running'", (db.dumps(error), db.now(), db.now(), job["id"]))
    return db.get("director_compositions", job["id"])


def recover_interrupted() -> int:
    with db.transaction() as connection:
        now = db.now()
        result = connection.execute("UPDATE director_compositions SET status='interrupted',error_json=?,finished_at=?,updated_at=? "
                                    "WHERE status='running' AND project_id IN (SELECT id FROM projects WHERE deleted_at IS NULL)",
                                    (db.dumps({"code": "TASK_INTERRUPTED", "message": "合成过程被中断，请重新准备合成"}), now, now))
        return result.rowcount


def _loop() -> None:
    while not _stop.is_set():
        try:
            if execute_next() is not None:
                continue
        except Exception:
            _log.exception("Director composition worker stopped one failed operation")
        _stop.wait(0.5)


def start() -> None:
    global _thread
    with _thread_lock:
        if _thread and _thread.is_alive():
            return
        recover_interrupted()
        _stop.clear()
        _thread = threading.Thread(target=_loop, name="director-composition", daemon=True)
        _thread.start()


def stop() -> None:
    with _thread_lock:
        _stop.set()
        if _thread:
            _thread.join(timeout=2)
