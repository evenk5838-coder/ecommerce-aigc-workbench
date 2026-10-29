"""Lossless model-facing projection; persisted/API/UI records stay complete.

Only presentation fields and duplicate copies are removed. User messages,
briefs, prompts, settings, identities, revisions and execution errors survive.
This module cannot submit work, change a budget or call a model.
"""
from __future__ import annotations

from copy import deepcopy


def _pick(value: dict, keys: tuple[str, ...]) -> dict:
    return {key: deepcopy(value[key]) for key in keys if key in value}


def _execution(value: dict | None) -> dict | None:
    if not value:
        return value
    result = _pick(value, ("execution_id", "task_id", "node_id", "status", "stage", "active",
        "asset_id", "output_asset_id", "asset", "asset_available", "asset_error", "result_status",
        "error", "input", "input_hash", "config_revision", "estimated_cost_cny", "cost_cny",
        "storyboard_id", "take_id", "version", "duration_ms"))
    if value.get("last_successful_execution"):
        result["last_successful_execution"] = _execution(value["last_successful_execution"])
    return result


def execution_state(node: dict) -> str:
    """Describe only this saved revision; never infer confirmation from history."""
    execution = node.get("last_execution")
    if not execution:
        return "not_submitted"
    if execution.get("config_revision") is None:
        return "unknown"
    if execution["config_revision"] != node.get("config_revision"):
        return "settings_changed"
    if execution.get("active"):
        return "running"
    status = execution.get("status")
    if status == "succeeded":
        return "completed" if execution.get("asset_available") is True else "output_unavailable"
    return status if status in ("failed", "canceled", "interrupted") else "unknown"


def creation_for_model(value: dict) -> dict:
    result = _pick(value, ("project_id", "current_role", "selected_node_ids", "script",
                          "total_nodes", "nodes_truncated"))
    nodes = []
    for source in value.get("nodes", []):
        node = _pick(source, ("node_id", "type", "config", "binding", "config_revision", "asset",
                            "input_asset", "script_content", "content_truncated"))
        node["last_execution"] = _execution(source.get("last_execution"))
        if source.get("type") in ("image", "video"):
            node["execution_state"] = execution_state(source)
        # Script nodes expose the same summary as both editable text and content.
        # Keep the authoritative structured script, with an explicit reference.
        content, cfg = node.get("script_content") or {}, node.get("config") or {}
        if node.get("type") == "script" and "text" in cfg and cfg["text"] == content.get("summary"):
            cfg.pop("text")
            node["config_text_ref"] = "script_content.summary"
        nodes.append(node)
    result["nodes"] = nodes
    return result


def project_for_model(project: dict, creation: dict) -> dict:
    result = deepcopy(project)
    script = result.get("script")
    if script:
        # Only deduplicate the exact same version AND content. An older bound
        # node, a truncated script, or a deleted script is never substituted.
        match = next((node for node in creation.get("nodes", [])
            if (node.get("binding") or {}).get("script_id") == script.get("script_id")
            and (node.get("binding") or {}).get("script_version") == script.get("version")
            and node.get("script_content") == script.get("content")), None)
        if match:
            script.pop("content")
            script["content_ref"] = {"node_id": match["node_id"], "field": "script_content"}
    return result


def snapshot(creation: dict, project: dict) -> dict:
    return {"creation": creation_for_model(creation), "project": project_for_model(project, creation)}


def history_artifact(value: dict, current_nodes: list[dict]) -> dict:
    # A changed/deleted node's historical settings may be needed for a user
    # asking to revert. Only exact copies of a CURRENT revision become refs.
    result = deepcopy(value)
    result.pop("position", None)
    match = next((node for node in current_nodes
        if node.get("node_id") == value.get("node_id")
        and node.get("type") == value.get("type")
        and node.get("config_revision") == value.get("config_revision")
        and node.get("binding") == value.get("binding")
        and node.get("config") == value.get("config")
        and ("script_content" not in value or node.get("script_content") == value["script_content"])), None)
    if match:
        result.pop("config", None)
        result.pop("script_content", None)
        result["content_ref"] = {"node_id": match["node_id"], "config_revision": match["config_revision"],
                                 "source": "current_creation"}
    if result.get("last_execution"):
        result["last_execution"] = _execution(result["last_execution"])
    return result


def context_tool_reply(output: dict, initial: dict) -> dict:
    compact = snapshot(output["creation"], output["project"])
    if compact == initial:
        return {"creation": _pick(compact["creation"], ("project_id", "current_role", "selected_node_ids")),
                "unchanged": True, "context_ref": "initial_context",
                "note": "与本轮初始creation/project完全一致，请使用已提供内容。"}
    return compact
