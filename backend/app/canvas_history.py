"""Persistent, scoped undo for removing canvas representations.

Only the deleted rows and incident edges are restored.  Assets, tasks, scripts,
other nodes, the viewport and subsequent edits are never rewound by this module.
"""
from __future__ import annotations

from typing import Any

from . import db, projects
from .errors import err


def record_deletion_in_tx(connection: Any, project_id: str, deleted_nodes: list[dict],
                          old_edges: list[dict], layout_revision: int) -> str | None:
    """Record a removal in the transaction that deletes the corresponding rows."""
    if not deleted_nodes:
        return None
    node_ids = {row["node_id"] for row in deleted_nodes}
    edges = [edge for edge in old_edges
             if edge.get("from") in node_ids or edge.get("to") in node_ids]
    operation_id = db.new_id("cdel")
    connection.execute(
        "INSERT INTO canvas_deletions(id,project_id,deleted_nodes_json,removed_edges_json,"
        "delete_layout_revision,status,created_at) VALUES(?,?,?,?,?,'deleted',?)",
        (operation_id, project_id, db.dumps(deleted_nodes), db.dumps(edges),
         layout_revision, db.now()))
    return operation_id


def _operation_summary(row: dict) -> dict:
    nodes = db.loads(row["deleted_nodes_json"]) or []
    return {"operation_id": row["id"], "status": row["status"],
            "node_ids": [node["node_id"] for node in nodes],
            "node_count": len(nodes), "delete_layout_revision": row["delete_layout_revision"],
            "created_at": row["created_at"], "restored_at": row.get("restored_at"),
            "restore_layout_revision": row.get("restore_layout_revision")}


def list_deletions(project_id: str) -> dict:
    """Provide persistent operation IDs after refresh, without exposing row internals."""
    projects.get_project(project_id)
    rows = db.query("SELECT * FROM canvas_deletions WHERE project_id=? "
                    "ORDER BY created_at DESC,id DESC LIMIT 100", (project_id,))
    return {"project_id": project_id, "operations": [_operation_summary(row) for row in rows]}


def _conflict(reason: str, node_ids: list[str] | None = None) -> None:
    raise err("CANVAS_REVISION_CONFLICT", "画布内容已变化，无法直接恢复，请先查看当前画布。",
              detail={"reason": reason, **({"node_ids": node_ids} if node_ids else {})})


def _merge_edges(existing: list[dict], removed: list[dict], all_node_ids: set[str]) -> list[dict]:
    """Merge incident edges; do not replace newer edits or create ambiguous duplicates."""
    merged = list(existing)
    for edge in removed:
        if edge.get("from") not in all_node_ids or edge.get("to") not in all_node_ids:
            _conflict("edge_endpoint_missing")
        if edge in merged:
            continue
        for current in merged:
            same_id = edge.get("id") is not None and current.get("id") == edge["id"]
            same_endpoints = (current.get("from"), current.get("to")) == (edge.get("from"), edge.get("to"))
            if same_id or same_endpoints:
                _conflict("edge_changed")
        merged.append(edge)
    return merged


@projects.project_guard()
def undo_deletion(project_id: str, operation_id: str, body: dict) -> dict:
    """Restore one deletion atomically, preserving later unrelated work.

    An already-restored operation is a successful replay even when the caller's
    revision is now stale.  A still-pending operation must match the *current*
    layout revision.  A reused node ID, changed edge or newer canonical script
    is an explicit conflict rather than an overwrite.
    """
    from . import canvas

    projects.get_project(project_id)
    if not isinstance(body, dict) or set(body) - {"expected_layout_revision"}:
        raise err("VALIDATION_FAILED", "恢复请求只接受 expected_layout_revision")
    expected = canvas._revision(body.get("expected_layout_revision"), "expected_layout_revision")
    if not isinstance(operation_id, str) or not operation_id.strip() or len(operation_id) > 200:
        raise err("VALIDATION_FAILED", "删除操作 ID 无效")

    already_restored = False
    restored_node_ids: list[str] = []
    with db.transaction() as connection:
        found = connection.execute("SELECT * FROM canvas_deletions WHERE id=? AND project_id=?",
                                   (operation_id, project_id)).fetchone()
        if not found:
            raise err("CANVAS_DELETION_NOT_FOUND", "未找到可恢复的删除操作")
        operation = dict(found)
        deleted_nodes = db.loads(operation["deleted_nodes_json"]) or []
        restored_node_ids = [row["node_id"] for row in deleted_nodes]
        state = connection.execute("SELECT * FROM canvas_states WHERE project_id=?", (project_id,)).fetchone()
        layout_revision = int(state["layout_revision"] or 0) if state else 0
        if operation["status"] == "restored":
            already_restored = True
        else:
            if expected != layout_revision:
                raise err("CANVAS_REVISION_CONFLICT", "画布已被更新", detail={
                    "current_layout_revision": layout_revision})
            removed_edges = db.loads(operation["removed_edges_json"]) or []
            current_nodes = {row["node_id"]: dict(row) for row in connection.execute(
                "SELECT * FROM canvas_nodes WHERE project_id=?", (project_id,))}
            occupied = sorted(set(restored_node_ids) & set(current_nodes))
            if occupied:
                _conflict("node_id_reused", occupied)
            newer = connection.execute(
                "SELECT deleted_nodes_json FROM canvas_deletions WHERE project_id=? "
                "AND delete_layout_revision>? AND id<>?",
                (project_id, operation["delete_layout_revision"], operation_id))
            for row in newer:
                overlapping = set(restored_node_ids) & {
                    node["node_id"] for node in (db.loads(row["deleted_nodes_json"]) or [])}
                if overlapping:
                    _conflict("newer_deletion", sorted(overlapping))
            for row in deleted_nodes:
                binding = db.loads(row["binding_json"]) or {}
                canvas._validate_ref(project_id, binding)
                if row["node_id"] == "script":
                    latest = connection.execute(
                        "SELECT id,version FROM script_versions WHERE project_id=? ORDER BY version DESC LIMIT 1",
                        (project_id,)).fetchone()
                    if latest and (binding.get("script_id") != latest["id"] or
                                   binding.get("script_version") not in (None, latest["version"])):
                        _conflict("newer_script", ["script"])
            edges = _merge_edges(db.loads(state["edges_json"]) or [] if state else [],
                                 removed_edges, set(current_nodes) | set(restored_node_ids))
            if canvas._has_cycle([], edges):
                _conflict("edge_cycle")
            # All validation precedes writes, so a batch either fully restores or does nothing.
            now = db.now()
            for saved in deleted_nodes:
                row = {**saved, "project_id": project_id, "updated_at": now,
                       "config_revision": int(saved["config_revision"] or 1) + 1}
                columns = list(row)
                connection.execute("INSERT INTO canvas_nodes(" + ",".join(columns) + ") VALUES(" +
                                   ",".join("?" for _ in columns) + ")", [row[column] for column in columns])
            layout_revision += 1
            connection.execute("UPDATE canvas_states SET layout_revision=?,edges_json=?,updated_at=? "
                               "WHERE project_id=?", (layout_revision, db.dumps(edges), now, project_id))
            connection.execute("UPDATE canvas_deletions SET status='restored',restored_at=?,"
                               "restore_layout_revision=? WHERE id=? AND project_id=?",
                               (now, layout_revision, operation_id, project_id))
            if any((db.loads(node["config_json"]) or db.loads(node["binding_json"])) for node in deleted_nodes):
                connection.execute("UPDATE confirmations SET status='invalidated',reason='node_config_changed',"
                                   "invalidated_at=? WHERE project_id=? AND object_type='scope' AND status='active'",
                                   (now, project_id))
    return {"project_id": project_id, "operation_id": operation_id,
            "already_restored": already_restored, "restored_node_ids": restored_node_ids,
            "layout_revision": layout_revision, "canvas": canvas.get_canvas(project_id)}
