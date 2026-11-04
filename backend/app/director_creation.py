"""Project-bound, non-billed creation tools for the director conversation.

Each write commits its durable tool receipt with its artifacts.  These tools
never confirm a script, create a generation task, or grant media approval.
Image/video nodes use the same config validation as the manual canvas.  Their
executions do not fabricate legacy storyboard or take rows.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Callable

from . import canvas, config, db, node_execution, projects, storage
from .errors import err

MAX_NODES = 40
MAX_ITEMS = 24
SCRIPT_ACTION_TTL_S = 24 * 60 * 60
NO_GENERATION = {"task_created": False, "model_called": False, "budget_deducted": False}


def _object(value: Any, allowed: set[str], name: str) -> dict:
    if not isinstance(value, dict) or set(value) - allowed:
        raise err("VALIDATION_FAILED", f"{name} 格式不正确或含未知字段")
    return value


def _string(value: Any, name: str, limit: int = 200) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise err("VALIDATION_FAILED", f"{name} 必须是非空字符串且不超过 {limit} 字")
    return value.strip()


def _revision(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise err("VALIDATION_FAILED", f"{name} 必须为正整数")
    return value


def _items(value: Any, name: str) -> list:
    if not isinstance(value, list) or not 1 <= len(value) <= MAX_ITEMS:
        raise err("VALIDATION_FAILED", f"{name} 需要 1 至 {MAX_ITEMS} 项")
    return value


def _node(connection: Any, project_id: str, node_id: str, revision: Any = None) -> dict:
    row = connection.execute("SELECT * FROM canvas_nodes WHERE project_id=? AND node_id=?",
                             (project_id, node_id)).fetchone()
    if row is None:
        raise err("CANVAS_NODE_NOT_FOUND", "所选节点不在当前项目中")
    node = dict(row)
    if revision is not None and _revision(revision, "expected_config_revision") != node["config_revision"]:
        raise err("CANVAS_REVISION_CONFLICT", "源节点已更新，请重新读取后继续",
                  detail={"node_id": node_id, "current_config_revision": node["config_revision"]})
    return node


def _asset(asset_id: str) -> dict:
    asset = storage.load_asset(asset_id)
    if asset.get("kind") != "image":
        raise err("INVALID_REFERENCE_IMAGE", "请选择真实图片素材")
    try:
        digest = hashlib.sha256(storage.asset_path(asset).read_bytes()).hexdigest()
    except OSError as exc:
        raise err("INVALID_REFERENCE_IMAGE", "参考图片文件不可读取") from exc
    if not asset.get("sha256") or asset["sha256"] != digest:
        raise err("INVALID_REFERENCE_IMAGE", "参考图片内容已变化，请重新上传")
    return {key: asset.get(key) for key in ("id", "sha256", "kind", "mime", "width", "height")}


def _bounded(value: Any, limit: int = 6000) -> tuple[Any, bool]:
    """Keep all config keys; mark shortened content instead of hiding truncation."""
    truncated = False
    if isinstance(value, str):
        return value[:limit], len(value) > limit
    if isinstance(value, list):
        result = []
        for item in value[:MAX_NODES]:
            clean, cut = _bounded(item, limit)
            result.append(clean)
            truncated |= cut
        return result, truncated or len(value) > MAX_NODES
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            result[key], cut = _bounded(item, limit)
            truncated |= cut
        return result, truncated
    return value, False


def creation_context(project_id: str, selected_node_ids: list[str] | None = None) -> dict:
    """Read actual project nodes, including selected output identity and revisions."""
    projects.get_project(project_id)
    selected = [] if selected_node_ids is None else selected_node_ids
    if not isinstance(selected, list) or len(selected) > MAX_NODES:
        raise err("VALIDATION_FAILED", f"selected_node_ids 最多 {MAX_NODES} 项")
    selected = list(dict.fromkeys(_string(item, "selected_node_id") for item in selected))
    # Validate bindings before get_canvas follows any legacy shot/output links.
    for row in db.query("SELECT binding_json FROM canvas_nodes WHERE project_id=?", (project_id,)):
        canvas._validate_ref(project_id, db.loads(row["binding_json"]) or {})
    state = canvas.get_canvas(project_id)
    all_nodes = {node["node_id"]: node for node in state["nodes"]}
    if any(node_id not in all_nodes for node_id in selected):
        raise err("CANVAS_NODE_NOT_FOUND", "所选节点不在当前项目中")
    ordered = selected + [node_id for node_id in all_nodes if node_id not in selected]
    nodes = []
    for node_id in ordered[:MAX_NODES]:
        node, truncated = _bounded(all_nodes[node_id])
        binding = node.get("binding") or {}
        for key, asset_id in (("asset", (node.get("asset") or {}).get("id")),
                              ("input_asset", (node.get("config") or {}).get("input_asset_id"))):
            asset = db.get("assets", asset_id) if asset_id else None
            node[key] = {field: asset.get(field) for field in
                         ("id", "sha256", "kind", "mime", "width", "height", "duration_ms")} if asset else None
        if node["type"] == "script" and binding.get("script_id"):
            script = projects.get_script(binding["script_id"])
            if script["project_id"] != project_id:
                raise err("CANVAS_INVALID_REFERENCE", "脚本绑定不属于当前项目")
            node["script_content"], cut = _bounded(script["content"])
            truncated |= cut
        node["content_truncated"] = truncated
        nodes.append(node)
    latest = db.query("SELECT * FROM script_versions WHERE project_id=? ORDER BY version DESC LIMIT 1", (project_id,))
    confirmed = bool(latest and db.query(
        "SELECT id FROM confirmations WHERE project_id=? AND object_type='script' AND object_id=? "
        "AND object_version=? AND status='active'", (project_id, latest[0]["id"], latest[0]["version"])))
    focus = [all_nodes[node_id] for node_id in selected] if selected else list(all_nodes.values())
    role = "director" if confirmed else "writer"
    # Selection wins. Without selection, a newly revised draft keeps the writer
    # active even if an older production already has images or finished clips.
    media_focus = focus if selected or not latest or confirmed else []
    if any(node["type"] in ("video", "asset") and (node.get("asset") or {}).get("kind") == "video" for node in media_focus):
        role = "editor"
    elif any(node["type"] == "video" or
             node["type"] in ("image", "asset") and (node.get("asset") or {}).get("kind") == "image" for node in media_focus):
        role = "cinematographer"
    elif any(node["type"] == "image" for node in media_focus):
        role = "director"
    elif selected and any(node["type"] == "text" for node in focus):
        role = "writer"
    return {"project_id": project_id, "current_role": role,
            "selected_node_ids": selected, "layout_revision": state["layout_revision"],
            "nodes": nodes, "total_nodes": len(all_nodes), "nodes_truncated": len(all_nodes) > MAX_NODES,
            "script": {"script_id": latest[0]["id"], "version": latest[0]["version"], "confirmed": confirmed} if latest else None,
            "side_effects": dict(NO_GENERATION)}


def _operation(project_id: str, args: dict, ctx: dict, name: str,
               apply: Callable[[Any, str], dict]) -> dict:
    if not isinstance(ctx, dict) or ctx.get("project_id") != project_id:
        raise err("PROJECT_SESSION_MISMATCH", "工具上下文不属于当前项目")
    request_id = _string(ctx.get("request_id"), "request_id", 400)
    session_id = ctx.get("session_id")
    if session_id:
        session = db.get("assistant_sessions", session_id)
        if not session or session.get("project_id") != project_id:
            raise err("PROJECT_SESSION_MISMATCH", "会话不属于当前项目")
    try:
        payload = json.dumps(args, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise err("VALIDATION_FAILED", "工具参数必须是有效 JSON") from exc
    digest = "sha256:" + hashlib.sha256(payload.encode()).hexdigest()
    with db.transaction() as connection:
        prior = connection.execute("SELECT response_json FROM director_tool_operations WHERE project_id=? "
                                   "AND request_id=? AND tool_name=? AND input_hash=?",
                                   (project_id, request_id, name, digest)).fetchone()
        if prior:
            return {**db.loads(prior["response_json"]), "idempotent_replay": True}
        stable = hashlib.sha256(f"{project_id}:{request_id}:{name}:{digest}".encode()).hexdigest()[:20]
        result = {**apply(connection, stable), "side_effects": dict(NO_GENERATION), "idempotent_replay": False}
        connection.execute("INSERT INTO director_tool_operations(id,project_id,request_id,tool_name,input_hash,response_json,created_at) "
                           "VALUES(?,?,?,?,?,?,?)", (db.new_id("dtool"), project_id, request_id, name, digest, db.dumps(result), db.now()))
        return result


def _script_snapshot(connection: Any, project_id: str) -> dict:
    project = connection.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    script = connection.execute("SELECT * FROM script_versions WHERE project_id=? ORDER BY version DESC LIMIT 1", (project_id,)).fetchone()
    node = connection.execute("SELECT * FROM canvas_nodes WHERE project_id=? AND node_id='script'", (project_id,)).fetchone()
    if not project or project["deleted_at"] is not None or not script or not node or node["type"] != "script":
        raise err("DIRECTOR_ACTION_STALE", "当前脚本或节点已变化，请重新读取")
    binding = db.loads(node["binding_json"]) or {}
    if binding.get("script_id") != script["id"] or binding.get("script_version") != script["version"]:
        raise err("DIRECTOR_ACTION_STALE", "脚本节点已绑定其他版本，请重新读取")
    return {"script_id": script["id"], "script_version": script["version"],
            "content_hash": node_execution._hash(db.loads(script["content_json"]) or {}),
            "brief_hash": node_execution._hash(db.loads(project["brief_json"]) or {}),
            "node_id": "script", "node_record_id": node["id"]}


def _prepare_script_action_in_tx(connection: Any, project_id: str, request_id: str,
                                 script_id: str, version: int) -> str:
    """Prepare user approval beside the script; never grant approval here."""
    snapshot = _script_snapshot(connection, project_id)
    if snapshot["script_id"] != script_id or snapshot["script_version"] != version:
        raise err("DIRECTOR_ACTION_STALE", "脚本已更新，请查看最新版本")
    digest = node_execution._hash(snapshot)
    prior = connection.execute("SELECT id FROM director_actions WHERE project_id=? AND request_id=? "
        "AND kind='confirm_script' AND input_hash=?", (project_id, request_id, digest)).fetchone()
    if prior:
        return prior["id"]
    action_id, now = db.new_id("dact"), db.now()
    connection.execute("INSERT INTO director_actions(id,project_id,request_id,kind,input_hash,frozen_json,estimated_cost_cny,"
        "expires_at,status,created_at,updated_at) VALUES(?,?,?,'confirm_script',?,?,0,?,'proposed',?,?)",
        (action_id, project_id, request_id, digest, db.dumps(snapshot), now + SCRIPT_ACTION_TTL_S, now, now))
    return action_id


@projects.project_guard()
def prepare_script_confirmation(project_id: str, args: dict, ctx: dict) -> dict:
    """Explicitly renew/make approval for an existing current script, without rewriting it.

    Same request replays the same action even after expiry. A new request is an
    explicit fresh preparation; GET never renews approval scopes.
    """
    _object(args, {"script_id", "expected_version"}, "script confirmation")
    script_id = _string(args.get("script_id"), "script_id")
    version = _revision(args.get("expected_version"), "expected_version")

    def apply(connection: Any, _stable: str) -> dict:
        action_id = _prepare_script_action_in_tx(connection, project_id, ctx["request_id"], script_id, version)
        return {"action_ids": [action_id], "node_ids": ["script"], "artifacts": []}

    return _operation(project_id, args, ctx, "prepare_script_confirmation", apply)


@projects.project_guard()
def write_script(project_id: str, args: dict, ctx: dict) -> dict:
    from . import director_chat
    _object(args, {"summary", "shots", "base_version"}, "script")
    content = director_chat._clean_script({key: value for key, value in args.items() if key != "base_version"})

    def apply(connection: Any, _stable: str) -> dict:
        latest = connection.execute("SELECT COALESCE(MAX(version),0) v FROM script_versions WHERE project_id=?",
                                    (project_id,)).fetchone()["v"]
        if latest and args.get("base_version") is None:
            raise err("SCRIPT_VERSION_CONFLICT", "修改脚本需要提供当前 base_version", detail={"current_version": latest})
        script = projects._add_script_in_tx(connection, project_id, content, source="assistant",
            session_id=ctx.get("session_id"), base_version=args.get("base_version", 0))
        proposal = director_chat._save_proposal_in_tx(connection, project_id, "script", content,
            target_id=script["script_id"], target_version=script["version"],
            session_id=ctx.get("session_id"), request_id=ctx["request_id"])
        # Saving the artifact, preparing its user confirmation and the tool
        # receipt are one commit. A lost process cannot strand a saved script.
        action_id = _prepare_script_action_in_tx(connection, project_id, ctx["request_id"],
                                                script["script_id"], script["version"])
        artifact = {**proposal, "type": "script", "node_id": "script", "saved": True, "confirmed": False}
        return {**artifact, "node_ids": ["script"], "artifacts": [artifact], "action_ids": [action_id]}

    return _operation(project_id, args, ctx, "write_script", apply)


def _ratio(size: str) -> str:
    from .model_specs import image_ratio
    if not isinstance(size, str):
        raise err("MODEL_CAPABILITY_MISMATCH", "图片尺寸必须为字符串")
    ratio = image_ratio(size)
    if ratio is None:
        raise err("MODEL_CAPABILITY_MISMATCH", "图片尺寸不受支持")
    return ratio


def _media_config(kind: str, model_id: str, prompt: str, params: dict, *,
                  asset_id: str | None = None, title: str | None = None) -> dict:
    model = config.MODEL_REGISTRY.get(model_id) or {}
    caps = (config.public_model_capabilities(model_id) if model.get("provider") == "aihubmix"
            else config.MODEL_CAPABILITIES.get(model_id) or {})
    mode = ("image_to_" if asset_id else "text_to_") + kind
    if model.get("provider") not in config.CLOUD_PROVIDERS or model.get("kind") != kind or mode not in caps.get("input_modes", []):
        raise err("MODEL_CAPABILITY_MISMATCH", "请选择支持当前生成方式的已接入模型")
    params = dict(_object(params, node_execution.PARAM_KEYS[kind], "params"))
    if kind == "image":
        sizes = caps.get("sizes") or []
        if "size" not in params:
            available = [size for size in sizes if not params.get("ratio") or _ratio(size) == params["ratio"]]
            if not available:
                raise err("MODEL_CAPABILITY_MISMATCH", "模型没有支持该比例的图片尺寸")
            params["size"] = available[0]
        params.setdefault("ratio", _ratio(params["size"]))
    else:
        for key, cap, preferred in (("ratio", "ratios", "16:9"), ("resolution", "resolutions", "720p")):
            supported = caps.get(cap) or []
            if key not in params:
                if not supported:
                    raise err("MODEL_CAPABILITY_MISMATCH", f"模型缺少 {key} 能力")
                params[key] = preferred if preferred in supported else supported[0]
        params["duration"] = config.resolve_duration_s(model_id, params.get("duration"), strict=True)
    cfg = {"generation_mode": mode, "model_id": model_id, "prompt": prompt, "params": params}
    if asset_id:
        cfg["input_asset_id"] = asset_id
    if title:
        cfg["title"] = title
    cfg = canvas._validate_config(cfg)
    canvas._validate_node_config(kind, cfg, {})
    # Validation/resolution only: no quote, task, approval or network call.
    node_execution._freeze({"type": kind, "config_json": db.dumps(cfg)})
    return cfg


def _insert_node(connection: Any, project_id: str, node_id: str, kind: str, cfg: dict,
                 binding: dict, position: dict) -> dict:
    now = db.now()
    connection.execute("INSERT INTO canvas_nodes(id,project_id,node_id,type,position_json,config_json,binding_json,"
                       "config_revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,1,?,?)",
                       (db.new_id("node"), project_id, node_id, kind, db.dumps(position), db.dumps(cfg), db.dumps(binding), now, now))
    return {"type": kind, "node_id": node_id, "config_revision": 1, "config": cfg,
            "binding": binding, "position": position, "saved": True, "confirmed": False}


def _touch_layout(connection: Any, project_id: str) -> int:
    state = canvas._state(project_id, connection)
    revision = int(state["layout_revision"] or 0) + 1
    now = db.now()
    connection.execute("INSERT INTO canvas_states(project_id,schema_version,layout_revision,viewport_json,edges_json,updated_at) "
                       "VALUES(?,?,?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET layout_revision=excluded.layout_revision,updated_at=excluded.updated_at",
                       (project_id, state["schema_version"], revision, state["viewport_json"], state["edges_json"], now))
    connection.execute("UPDATE projects SET updated_at=? WHERE id=?", (now, project_id))
    return revision


def _text_source(connection: Any, project_id: str, args: dict) -> tuple[dict, dict]:
    source_id = _string(args.get("source_node_id"), "source_node_id")
    revision = _revision(args.get("expected_source_revision"), "expected_source_revision")
    source = _node(connection, project_id, source_id, revision)
    if source["type"] == "text":
        body = (db.loads(source["config_json"]) or {}).get("text")
        _string(body, "source.text", 100000)
        return source, {"node_id": source_id, "config_revision": revision, "type": "text",
                        "content_hash": node_execution._hash({"text": body})}
    if source_id != "script" or source["type"] != "script":
        raise err("CANVAS_INVALID_REFERENCE", "请选择当前脚本或自由文本节点")
    binding = db.loads(source["binding_json"]) or {}
    script = connection.execute("SELECT * FROM script_versions WHERE project_id=? ORDER BY version DESC LIMIT 1", (project_id,)).fetchone()
    if not script or binding.get("script_id") != script["id"] or binding.get("script_version") != script["version"]:
        raise err("SCRIPT_VERSION_CONFLICT", "脚本来源不是当前版本，请重新读取")
    confirmation = connection.execute("SELECT id FROM confirmations WHERE project_id=? AND object_type='script' "
        "AND object_id=? AND object_version=? AND status='active'", (project_id, script["id"], script["version"])).fetchone()
    if confirmation is None:
        raise err("SCRIPT_NOT_CONFIRMED", "请先确认当前脚本，再准备分镜图")
    return source, {"node_id": source_id, "config_revision": revision, "type": "script",
                    "script_id": script["id"], "script_version": script["version"],
                    "content_hash": node_execution._hash(db.loads(script["content_json"]))}


@projects.project_guard()
def prepare_storyboards(project_id: str, args: dict, ctx: dict) -> dict:
    _object(args, {"source_node_id", "expected_source_revision", "model_id", "params", "shots", "input_asset_id"}, "storyboards")
    model_id = _string(args.get("model_id"), "model_id")
    items = _items(args.get("shots"), "shots")

    def apply(connection: Any, stable: str) -> dict:
        source, source_info = _text_source(connection, project_id, args)
        shared_id = args.get("input_asset_id")
        shared_id = _string(shared_id, "input_asset_id") if shared_id is not None else None
        shared_reference = _asset(shared_id) if shared_id is not None else None
        origin = db.loads(source["position_json"]) or {"x": 0, "y": 0}
        base_seq = connection.execute("SELECT COALESCE(MAX(seq),0) seq FROM shots WHERE project_id=?", (project_id,)).fetchone()["seq"]
        artifacts = []
        for index, item in enumerate(items, 1):
            _object(item, {"prompt", "title", "duration_s", "motion", "input_asset_id", "params"}, "shot")
            prompt = _string(item.get("prompt"), "shot.prompt", 20000)
            title = _string(item.get("title", f"分镜 {base_seq + index}"), "shot.title")
            params = {**_object(args.get("params", {}), node_execution.PARAM_KEYS["image"], "params"),
                      **_object(item.get("params", {}), node_execution.PARAM_KEYS["image"], "shot.params")}
            # Omitted shot reference inherits this group's explicit reference;
            # null opts out, and a shot's own ID overrides only that shot.
            aid = item.get("input_asset_id", shared_id)
            aid = _string(aid, "shot.input_asset_id") if aid is not None else None
            reference = (shared_reference if aid == shared_id else _asset(aid)) if aid is not None else None
            cfg = _media_config("image", model_id, prompt, params, asset_id=aid, title=title)
            duration = item.get("duration_s")
            if duration is not None and (isinstance(duration, bool) or not isinstance(duration, int) or not 1 <= duration <= 3600):
                raise err("VALIDATION_FAILED", "duration_s 必须为 1 至 3600 的整数；视频生成时另按所选模型校验")
            motion = _string(item["motion"], "motion", 2000) if item.get("motion") is not None else None
            shot_id = f"shot_{stable}_{index}"
            connection.execute("INSERT INTO shots(id,project_id,script_version_id,seq,duration_s,description,motion,status,revision,created_at,updated_at) "
                               "VALUES(?,?,?,?,?,?,?,'draft',1,?,?)", (shot_id, project_id, source_info.get("script_id"),
                               base_seq + index, duration, prompt, motion, db.now(), db.now()))
            binding = {"shot_id": shot_id}
            if source_info.get("script_id"):
                binding.update(script_id=source_info["script_id"], script_version=source_info["script_version"])
            artifact = _insert_node(connection, project_id, f"director-image-{stable}-{index}", "image", cfg, binding,
                                    {"x": origin["x"] + 480, "y": origin["y"] + (index - 1) * 340})
            artifact.update(source=source_info, reference=reference, seq=base_seq + index)
            artifacts.append(artifact)
        return {"saved": True, "confirmed": False, "artifacts": artifacts,
                "node_ids": [item["node_id"] for item in artifacts], "source": source_info,
                "layout_revision": _touch_layout(connection, project_id), "legacy_media_created": False}

    return _operation(project_id, args, ctx, "prepare_storyboards", apply)


def _image_source(connection: Any, project_id: str, item: dict) -> tuple[dict, dict, dict]:
    asset_id = _string(item.get("asset_id"), "source.asset_id")
    asset = _asset(asset_id)
    node_id = item.get("node_id")
    if not node_id:
        if item.get("execution_id") is not None or item.get("expected_config_revision") is not None:
            raise err("CANVAS_INVALID_REFERENCE", "执行记录与节点版本必须同时指定源节点")
        return {"type": "library_asset", "asset": asset}, {}, {"x": 80, "y": 100}
    revision = _revision(item.get("expected_config_revision"), "source.expected_config_revision")
    node = _node(connection, project_id, _string(node_id, "source.node_id"), revision)
    binding = db.loads(node["binding_json"]) or {}
    source = {"node_id": node_id, "config_revision": revision, "asset": asset}
    if node["type"] == "asset":
        if binding.get("asset_id") != asset_id or item.get("execution_id") is not None:
            raise err("CANVAS_INVALID_REFERENCE", "素材与所选来源节点不一致")
        source["type"] = "imported_asset"
    elif node["type"] == "image":
        query = ("SELECT e.id,e.config_revision FROM canvas_node_executions e JOIN tasks t ON t.id=e.task_id "
                 "WHERE e.project_id=? AND e.node_record_id=? AND t.status='succeeded' AND t.output_asset_id=?")
        values = [project_id, node["id"], asset_id]
        if item.get("execution_id") is not None:
            query += " AND e.id=?"
            values.append(_string(item["execution_id"], "source.execution_id"))
        execution = connection.execute(query + " ORDER BY e.created_at DESC,e.id DESC LIMIT 1", values).fetchone()
        if execution is None:
            raise err("CANVAS_INVALID_REFERENCE", "请选择该图片节点真实生成成功的输出")
        source.update(type="image_execution", execution_id=execution["id"], execution_config_revision=execution["config_revision"])
    else:
        raise err("CANVAS_INVALID_REFERENCE", "视频来源需要图片节点或图片素材节点")
    # Preserve an actual shot identity only; this is not a legacy storyboard approval.
    target_binding = {}
    if binding.get("shot_id"):
        shot = connection.execute("SELECT id FROM shots WHERE id=? AND project_id=?", (binding["shot_id"], project_id)).fetchone()
        if shot is None:
            raise err("CANVAS_INVALID_REFERENCE", "源镜头不属于当前项目")
        target_binding["shot_id"] = shot["id"]
    return source, target_binding, db.loads(node["position_json"]) or {"x": 80, "y": 100}


@projects.project_guard()
def prepare_video_nodes(project_id: str, args: dict, ctx: dict) -> dict:
    _object(args, {"model_id", "params", "sources", "output_mode"}, "videos")
    if args.get("output_mode", "clips") not in ("clips", "film"):
        raise err("VALIDATION_FAILED", "output_mode 必须为 clips 或 film")
    model_id = _string(args.get("model_id"), "model_id")
    items = _items(args.get("sources"), "sources")

    def apply(connection: Any, stable: str) -> dict:
        artifacts = []
        for index, item in enumerate(items, 1):
            _object(item, {"node_id", "expected_config_revision", "asset_id", "execution_id", "prompt", "title", "params"}, "source")
            source, binding, origin = _image_source(connection, project_id, item)
            prompt = _string(item.get("prompt"), "source.prompt", 20000)
            title = _string(item.get("title", f"镜头视频 {index}"), "source.title")
            params = {**_object(args.get("params", {}), node_execution.PARAM_KEYS["video"], "params"),
                      **_object(item.get("params", {}), node_execution.PARAM_KEYS["video"], "source.params")}
            cfg = _media_config("video", model_id, prompt, params, asset_id=source["asset"]["id"], title=title)
            artifact = _insert_node(connection, project_id, f"director-video-{stable}-{index}", "video", cfg, binding,
                                    {"x": origin["x"] + 480, "y": origin["y"] + ((index - 1) * 340 if not item.get("node_id") else 0)})
            artifact["source"] = source
            artifacts.append(artifact)
        return {"saved": True, "confirmed": False, "artifacts": artifacts,
                "node_ids": [item["node_id"] for item in artifacts],
                "layout_revision": _touch_layout(connection, project_id), "legacy_media_created": False}

    return _operation(project_id, args, ctx, "prepare_video_nodes", apply)


@projects.project_guard()
def update_node(project_id: str, args: dict, ctx: dict) -> dict:
    """Merge a partial config into one node, preserving omitted settings.

    Top-level config fields overwrite only supplied keys. ``params`` is also
    merged by key, so a duration change preserves resolution and ratio. Clear
    an image reference explicitly with ``input_asset_id: null`` when changing
    to text input. Node identity, type, binding, position and shot order are
    immutable through this tool. Any quote/approval remains a separate step.
    """
    _object(args, {"node_id", "expected_config_revision", "config"}, "node update")
    node_id = _string(args.get("node_id"), "node_id")
    expected = _revision(args.get("expected_config_revision"), "expected_config_revision")
    change = _object(args.get("config"), set(canvas.CONFIG_KEYS), "config")

    def apply(connection: Any, _stable: str) -> dict:
        node = _node(connection, project_id, node_id, expected)
        if node["type"] not in ("text", "image", "video"):
            raise err("NODE_NOT_EDITABLE", "此工具只能修改文本、图片或视频节点；脚本请保存为新的脚本版本")
        binding = db.loads(node["binding_json"]) or {}
        canvas._validate_ref(project_id, binding)
        previous = db.loads(node["config_json"]) or {}
        merged = {**previous, **change}
        if "params" in change:
            allowed = node_execution.PARAM_KEYS.get(node["type"], set())
            params = _object(change["params"], allowed, "config.params")
            merged["params"] = {**(previous.get("params") or {}), **params}
        clean = canvas._validate_config(merged)
        canvas._validate_node_config(node["type"], clean, binding)
        if node["type"] in ("image", "video"):
            node_execution._freeze({**node, "config_json": db.dumps(clean)})
        changed = node_execution._hash(previous) != node_execution._hash(clean)
        revision = node["config_revision"] + int(changed)
        layout_revision = int(canvas._state(project_id, connection)["layout_revision"] or 0)
        if changed:
            connection.execute("UPDATE canvas_nodes SET config_json=?,config_revision=?,updated_at=? WHERE id=?",
                               (db.dumps(clean), revision, db.now(), node["id"]))
            connection.execute("UPDATE confirmations SET status='invalidated',reason='node_config_changed',invalidated_at=? "
                               "WHERE project_id=? AND object_type='scope' AND status='active'", (db.now(), project_id))
            layout_revision = _touch_layout(connection, project_id)
        artifact = {"type": node["type"], "node_id": node_id, "config": clean, "binding": binding,
                    "config_revision": revision, "position": db.loads(node["position_json"]), "saved": True}
        return {"saved": True, "node_id": node_id, "node_ids": [node_id], "artifacts": [artifact],
                "config_revision": revision, "config_changed": changed,
                "layout_revision": layout_revision, "merge_strategy": "partial_config_and_params"}

    return _operation(project_id, args, ctx, "update_node", apply)
