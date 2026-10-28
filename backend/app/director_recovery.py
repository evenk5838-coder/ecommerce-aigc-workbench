"""Reconcile conversations whose owning local process has demonstrably ended.

Reads never run recovery. Starting a second service must not take over a live
owner's conversation. Requests without ownership evidence remain unknown.
Recovery preserves durable tools, actions and media tasks and never calls a
model, submits a generation, or retries a render.
"""
from __future__ import annotations

import os
import subprocess

from . import db


def _process_start(pid: int) -> str | None:
    try:
        result = subprocess.run(["ps", "-p", str(pid), "-o", "lstart="],
                                capture_output=True, text=True, timeout=2,
                                env={**os.environ, "TZ": "UTC", "LC_ALL": "C"})
        value = result.stdout.strip() if result.returncode == 0 else ""
        return "utc-lstart-v1:" + value if value else None
    except (OSError, subprocess.SubprocessError):
        return None


_OWNER_PID = os.getpid()
_OWNER_START = _process_start(_OWNER_PID)


def request_context(body: dict, session_id: str) -> dict:
    return {"flow_version": 2, "message": body["message"], "session_id": session_id,
            "selected_node_ids": body.get("selected_node_ids", []),
            "owner_pid": _OWNER_PID, "owner_start": _OWNER_START}


def _owner_state(context: dict) -> str:
    pid = context.get("owner_pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return "unknown"
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "ended"
    except (PermissionError, OSError):
        return "unknown"
    # PID reuse does not confer ownership on an unrelated newer process.
    actual = _process_start(pid)
    stored = context.get("owner_start")
    if (isinstance(stored, str) and stored.startswith("utc-lstart-v1:")
            and actual and actual.startswith("utc-lstart-v1:") and stored != actual):
        return "ended"
    return "active"


def recover_interrupted_requests() -> dict:
    from . import director_chat as legacy, director_workflow as workflow

    counts = {"interrupted": 0, "active": 0, "unknown_owner": 0}
    rows = db.query("SELECT r.* FROM director_requests r JOIN projects p ON p.id=r.project_id "
                    "WHERE r.status='pending' AND r.id LIKE 'dreq2_%' AND p.deleted_at IS NULL")
    for row in rows:
        context = db.loads(row.get("request_json")) or {}
        owner = _owner_state(context)
        if owner != "ended":
            counts["active" if owner == "active" else "unknown_owner"] += 1
            continue
        with db.transaction() as connection:
            current = connection.execute("SELECT * FROM director_requests WHERE id=? AND status='pending'",
                                         (row["id"],)).fetchone()
            if current is None or current["request_json"] != row["request_json"]:
                continue
            operations = connection.execute("SELECT response_json FROM director_tool_operations "
                "WHERE project_id=? AND request_id=? ORDER BY created_at,id",
                (row["project_id"], row["client_request_id"])).fetchall()
            artifacts = [artifact for operation in operations for artifact in
                         (db.loads(operation["response_json"]) or {}).get("artifacts", [])]
            action_rows = connection.execute("SELECT id,kind FROM director_actions WHERE project_id=? AND request_id=? "
                "ORDER BY created_at,id", (row["project_id"], row["client_request_id"])).fetchall()
            actions = []
            for action in action_rows:
                try:
                    actions.append(workflow._action_view(row["project_id"], action["id"]))
                except Exception:
                    actions.append({"action_id": action["id"], "kind": action["kind"],
                                    "status": "unknown", "requires_user_action": False})
            error = {"code": "DIRECTOR_REQUEST_INTERRUPTED", "message": "上次对话被中断，已保留保存的内容；请核对后继续。"}
            outcome = {"status": "partial" if artifacts or actions else "failed", "error": error,
                       "node_ids": list(dict.fromkeys(a["node_id"] for a in artifacts if a.get("node_id")))}
            response = {"flow_version": 2, "request_id": row["client_request_id"], "session_id": context.get("session_id"),
                        "request_status": "failed", "reply_type": outcome["status"], "reply": error["message"],
                        "outcome": outcome, "artifacts": artifacts, "actions": actions,
                        "usage": {"has_unknown_cost": True}, "recovered_after_restart": True}
            turn_id, now = None, db.now()
            # A pre-upgrade request may have no stored original user text.
            # Never invent a user message just to fill the transcript.
            if context.get("message") and context.get("session_id"):
                turn_id = db.new_id("dturn")
                connection.execute("INSERT INTO director_turns(id,project_id,session_id,request_id,user_text,reply_text,"
                    "reply_type,structured_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)", (turn_id, row["project_id"],
                    context["session_id"], row["client_request_id"], context["message"], error["message"], outcome["status"],
                    db.dumps({"flow_version": 2, "artifacts": artifacts, "actions": actions, "outcome": outcome,
                              "recovered_after_restart": True}), now))
            response["workflow_revision"] = legacy._workflow_revision(row["project_id"], connection)
            connection.execute("UPDATE director_requests SET status='failed',turn_id=?,error_code=?,error_message=?,"
                "response_json=?,updated_at=? WHERE id=? AND status='pending'", (turn_id, error["code"], error["message"],
                db.dumps(response), now, row["id"]))
            counts["interrupted"] += 1
    return counts
