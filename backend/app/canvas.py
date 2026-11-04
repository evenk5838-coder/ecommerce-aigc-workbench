"""自由画布节点持久化（D3）。

规则：
- **每个节点独立保存** prompt / 素材 / generation_mode / model_id / 规格 / 绑定。
- **布局改动 ≠ 生成输入改动**：只移动节点/缩放视图 → `layout_revision+1`，**不**使报价与确认失效。
- **配置改动** → `config_revision+1`，并使该项目的整组报价（scope）失效。
- 校验：引用必须属于本项目；模式与模型能力必须匹配（复用 config）。
- 执行结果（task/asset/费用）只从真实任务读取，**不接受客户端 PATCH**。
"""
from __future__ import annotations

import json
import math
from typing import Any

from . import config, db, projects
from .errors import err

NODE_TYPES = ("text", "image", "video", "storyboard", "shot", "asset", "script")
CONFIG_KEYS = ("generation_mode", "model_id", "prompt", "input_asset_id", "params", "title", "text")
NODE_KEYS = {"node_id", "type", "position", "config", "binding", "expected_config_revision"}
BINDING_KEYS = {"shot_id", "script_id", "script_version", "asset_id"}


def _asset_summary(asset_id: str | None) -> dict | None:
    asset = db.get("assets", asset_id) if asset_id else None
    return {key: asset.get(key) for key in (
        "id", "kind", "mime", "width", "height", "duration_ms")} if asset else None


def _finite_point(value: Any, name: str, *, viewport: bool = False) -> dict:
    if not isinstance(value, dict):
        raise err("VALIDATION_FAILED", f"{name} 必须是对象")
    defaults = {"x": 0, "y": 0, **({"zoom": 1} if viewport else {})}
    if set(value) - set(defaults):
        raise err("VALIDATION_FAILED", f"{name} 含未知字段")
    out = {**defaults, **value}
    for key, number in out.items():
        try:
            finite = isinstance(number, (int, float)) and not isinstance(number, bool) and math.isfinite(number)
        except OverflowError:
            finite = False
        if not finite or (key == "zoom" and number <= 0):
            raise err("VALIDATION_FAILED", f"{name}.{key} 必须是有限数值" + ("且大于零" if key == "zoom" else ""))
    return out


def _state(project_id: str, connection: Any) -> dict:
    row = connection.execute("SELECT * FROM canvas_states WHERE project_id=?", (project_id,)).fetchone()
    if row:
        return dict(row)
    return {"project_id": project_id, "schema_version": 1, "layout_revision": 0,
            "viewport_json": db.dumps({"x": 0, "y": 0, "zoom": 1}), "edges_json": "[]"}


def get_canvas(project_id: str) -> dict:
    projects.get_project(project_id)
    # Layout, current identities and deletion markers describe one read snapshot.
    # The bounded undo-history listing is not a complete source of deletion markers.
    with db.conn() as connection:
        connection.execute("BEGIN")
        st = _state(project_id, connection)
        nodes = [dict(row) for row in connection.execute(
            "SELECT * FROM canvas_nodes WHERE project_id=? ORDER BY created_at", (project_id,))]
        deletions = connection.execute(
            "SELECT deleted_nodes_json FROM canvas_deletions WHERE project_id=? AND status='deleted'",
            (project_id,)).fetchall()
        connection.execute("COMMIT")
    present_ids = {node["node_id"] for node in nodes}
    deleted_node_ids = sorted({
        node["node_id"] for operation in deletions
        for node in (db.loads(operation["deleted_nodes_json"]) or [])
        if node["node_id"] not in present_ids
    })
    out = []
    for n in nodes:
        cfg = db.loads(n["config_json"]) or {}
        binding = db.loads(n["binding_json"]) or {}
        # 最近一次执行：只读真实任务/产物
        last = None
        if binding.get("shot_id") and n["type"] in ("image", "storyboard"):
            boards = db.query("SELECT * FROM storyboards WHERE shot_id=? ORDER BY version DESC LIMIT 1", (binding["shot_id"],))
            if boards:
                last = {"storyboard_id": boards[0]["id"], "task_id": boards[0]["task_id"],
                        "status": boards[0]["status"], "asset_id": boards[0]["asset_id"], "version": boards[0]["version"]}
        elif binding.get("shot_id") and n["type"] in ("video", "shot"):
            take = db.query("SELECT * FROM shot_takes WHERE shot_id=? "
                            "ORDER BY created_at DESC LIMIT 1", (binding["shot_id"],))
            if take:
                last = {"take_id": take[0]["id"], "task_id": take[0]["task_id"],
                        "status": take[0]["status"], "asset_id": take[0]["asset_id"],
                        "duration_ms": take[0]["duration_ms"]}
        from . import node_execution
        last = node_execution.get_last_execution(project_id, n["node_id"]) or last
        output_asset_id = last.get("asset_id") if last else None
        if last and not output_asset_id:
            output_asset_id = (last.get("last_successful_execution") or {}).get("asset_id")
        out.append({"node_id": n["node_id"], "type": n["type"],
                    "position": db.loads(n["position_json"]) or {"x": 0, "y": 0},
                    "config": cfg, "binding": binding,
                    "config_revision": int(n["config_revision"] or 1),
                    "last_execution": last, "asset": _asset_summary(binding.get("asset_id") or output_asset_id)})
    return {"project_id": project_id, "schema_version": int(st["schema_version"] or 1),
            "layout_revision": int(st["layout_revision"] or 0),
            "viewport": db.loads(st["viewport_json"]) or {"x": 0, "y": 0, "zoom": 1},
            "nodes": out, "edges": db.loads(st.get("edges_json")) or [],
            "deleted_node_ids": deleted_node_ids,
            "note": "布局与生成配置分开；last_execution 只读，不可 PATCH"}


def _validate_ref(project_id: str, binding: dict) -> None:
    if not isinstance(binding, dict):
        raise err("CANVAS_INVALID_REFERENCE", "binding 必须是对象")
    if set(binding) - BINDING_KEYS:
        raise err("CANVAS_INVALID_REFERENCE", "binding 含未知字段", detail={"allowed": sorted(BINDING_KEYS)})
    for key in ("shot_id", "script_id", "asset_id"):
        if key in binding and binding[key] is not None and (
                not isinstance(binding[key], str) or not binding[key].strip()):
            raise err("CANVAS_INVALID_REFERENCE", f"binding.{key} 必须为非空字符串")
    sid = binding.get("shot_id")
    if sid:
        shot = db.get("shots", sid)
        if not shot or shot["project_id"] != project_id:
            raise err("CANVAS_INVALID_REFERENCE", f"镜头不属于本项目：{sid}",
                      detail={"shot_id": sid})
    scid = binding.get("script_id")
    if scid:
        scr = db.get("script_versions", scid)
        if not scr or scr["project_id"] != project_id:
            raise err("CANVAS_INVALID_REFERENCE", f"脚本不属于本项目：{scid}")
        version = binding.get("script_version")
        if version is not None and (isinstance(version, bool) or not isinstance(version, int)
                                    or version != scr["version"]):
            raise err("CANVAS_INVALID_REFERENCE", "脚本版本与脚本引用不一致")
    elif binding.get("script_version") is not None:
        raise err("CANVAS_INVALID_REFERENCE", "script_version 需要同时指定 script_id")
    aid = binding.get("asset_id")
    if aid:
        asset = db.get("assets", aid)
        if not asset or asset.get("kind") not in ("image", "video"):
            raise err("CANVAS_INVALID_REFERENCE", f"素材不存在：{aid}")


def _validate_config(cfg: dict) -> dict:
    if not isinstance(cfg, dict):
        raise err("VALIDATION_FAILED", "config 必须是对象")
    unknown = [k for k in cfg if k not in CONFIG_KEYS]
    if unknown:
        raise err("VALIDATION_FAILED", f"config 含未知字段：{unknown}",
                  detail={"allowed": list(CONFIG_KEYS)})
    try:
        out = json.loads(json.dumps({k: cfg[k] for k in CONFIG_KEYS if k in cfg}, allow_nan=False))
    except (ValueError, TypeError) as exc:
        raise err("VALIDATION_FAILED", "config 必须是有效 JSON 数据") from exc
    mode = out.get("generation_mode")
    model_id = out.get("model_id")
    if not isinstance(out.get("params", {}), dict):
        raise err("VALIDATION_FAILED", "params 必须是对象")
    for key, limit in (("prompt", 20000), ("title", 200), ("text", 100000)):
        if key in out and (not isinstance(out[key], str) or len(out[key]) > limit):
            raise err("VALIDATION_FAILED", f"{key} 必须是长度不超过 {limit} 的字符串")
    aid = out.get("input_asset_id")
    if "input_asset_id" in out and aid is not None and (not isinstance(aid, str) or not aid.strip()):
        raise err("CANVAS_INVALID_REFERENCE", "input_asset_id 必须是非空字符串")
    if aid:
        asset = db.get("assets", aid)
        if not asset or asset.get("kind") != "image":
            raise err("CANVAS_INVALID_REFERENCE", "参考素材不存在或不是图片")
    if mode is not None and mode not in ("text_to_image", "image_to_image",
                             "text_to_video", "image_to_video"):
        raise err("VALIDATION_FAILED", f"未知 generation_mode：{mode}")
    if mode in ("text_to_image", "text_to_video") and aid:
        raise err("GENERATION_MODE_CONFLICT", "文生节点不能携带参考素材", detail={"mode": mode})
    if mode in ("image_to_image", "image_to_video") and not aid:
        raise err("REFERENCE_IMAGE_REQUIRED", f"{mode} 节点需要参考素材")
    if "model_id" in out and model_id is not None and (not isinstance(model_id, str) or not model_id.strip()):
        raise err("VALIDATION_FAILED", "model_id 必须是非空字符串")
    if model_id:
        if model_id not in config.MODEL_REGISTRY:
            raise err("VALIDATION_FAILED", f"未知模型：{model_id}")
        if not config.model_submittability(model_id)["submittable"]:
            raise err("MODEL_NOT_OPENED", f"模型 {model_id} 不可提交")
        kind = config.MODEL_REGISTRY[model_id]["kind"]
        if mode and kind != ("image" if mode.endswith("_image") else "video"):
            raise err("MODEL_CAPABILITY_MISMATCH", "模型类型与生成模式不匹配")
        if mode:
            caps = config.MODEL_CAPABILITIES.get(model_id) or {}
            if caps.get("input_modes") and mode not in caps["input_modes"]:
                raise err("MODEL_CAPABILITY_MISMATCH",
                          f"模型 {model_id} 不支持 {mode}",
                          detail={"field": "generation_mode", "got": mode,
                                  "allowed": caps.get("input_modes")})
        params = out.get("params") or {}
        if kind == "video":
            if config.MODEL_REGISTRY[model_id].get("provider") == "aihubmix":
                # New node defaults follow the permitted application profile;
                # explicit user settings are still validated, never downgraded.
                operational = config.public_model_capabilities(model_id)
                for field, key in (("ratio", "ratios"), ("resolution", "resolutions")):
                    values = operational.get(key) or []
                    if field not in params and values:
                        params[field] = values[0]
                params.setdefault("generate_audio", False)
            if params.get("duration") is not None:
                params["duration"] = config.resolve_duration_s(
                    model_id, params["duration"], strict=True)
            from .model_specs import effective_params
            effective = effective_params("video", params, mode=mode)
            effective["duration"] = config.resolve_duration_s(
                model_id, params.get("duration"), strict=True)
            problems = config.validate_params_for_model(
                model_id, "video", effective)
            if problems:
                raise err("MODEL_CAPABILITY_MISMATCH", "节点参数不被模型支持",
                          detail={"problems": problems})
            out["params"] = params
        if kind == "image":
            from .model_specs import effective_params
            problems = config.validate_params_for_model(
                model_id, "image", effective_params("image", params, mode=mode))
            if problems:
                raise err("MODEL_CAPABILITY_MISMATCH", "节点参数不被模型支持", detail={"problems": problems})
        if mode in ("text_to_image", "text_to_video"):
            if out.get("input_asset_id"):
                raise err("GENERATION_MODE_CONFLICT",
                          "文生节点不能携带参考素材", detail={"mode": mode})
        if mode in ("image_to_image", "image_to_video") and not out.get("input_asset_id"):
            raise err("REFERENCE_IMAGE_REQUIRED", f"{mode} 节点需要参考素材")
    return out


def _validate_node_config(ntype: str, cfg: dict, binding: dict) -> None:
    if "text" in cfg and ntype != "text":
        raise err("VALIDATION_FAILED", "正文仅适用于自由文本节点；脚本正文请编辑脚本版本")
    if ntype == "text" and any(key in cfg for key in ("model_id", "generation_mode", "input_asset_id", "params")):
        raise err("VALIDATION_FAILED", "自由文本节点不能设置媒体生成参数")
    expected_kind = {"image": "image", "storyboard": "image", "video": "video", "shot": "video"}.get(ntype)
    mode = cfg.get("generation_mode")
    model_id = cfg.get("model_id")
    if expected_kind and mode and expected_kind != ("image" if mode.endswith("_image") else "video"):
        raise err("MODEL_CAPABILITY_MISMATCH", "节点类型与生成模式不匹配")
    model_kind = (config.MODEL_REGISTRY.get(model_id) or {}).get("kind") if model_id else None
    if expected_kind and model_kind and model_kind != expected_kind:
        raise err("MODEL_CAPABILITY_MISMATCH", "节点类型与模型类型不匹配")
    if expected_kind and binding.get("asset_id"):
        asset = db.get("assets", binding["asset_id"])
        if asset["kind"] != expected_kind:
            raise err("CANVAS_INVALID_REFERENCE", "节点类型与绑定素材类型不匹配")


def _has_cycle(nodes: list[dict], edges: list[dict]) -> bool:
    graph: dict[str, list[str]] = {}
    for e in edges:
        a, b = e.get("from"), e.get("to")
        graph.setdefault(a, []).append(b)
    color: dict[str, int] = {}

    def dfs(n: str) -> bool:
        if color.get(n) == 1:
            return True
        if color.get(n) == 2:
            return False
        color[n] = 1
        for m in graph.get(n, []):
            if dfs(m):
                return True
        color[n] = 2
        return False

    return any(dfs(n) for n in list(graph))


def _revision(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise err("VALIDATION_FAILED", f"{name} 必须为非负整数")
    return value


@projects.project_guard()
def patch_canvas(project_id: str, body: dict) -> dict:
    """Validate the complete next state before one atomic commit; omitted fields survive PATCH."""
    projects.get_project(project_id)
    if not isinstance(body, dict):
        raise err("VALIDATION_FAILED", "画布请求必须是对象")
    nodes_in = body.get("nodes", [])
    if not isinstance(nodes_in, list):
        raise err("VALIDATION_FAILED", "nodes 必须是数组")
    delete_ids = body.get("delete_node_ids", [])
    if (not isinstance(delete_ids, list) or any(not isinstance(node_id, str) or not node_id.strip()
                                               or len(node_id) > 200 for node_id in delete_ids)
            or len(set(delete_ids)) != len(delete_ids)):
        raise err("VALIDATION_FAILED", "delete_node_ids 必须是不重复的节点 ID 数组")
    if "prune" in body and not isinstance(body["prune"], bool):
        raise err("VALIDATION_FAILED", "prune 必须为布尔值")
    if body.get("prune") and delete_ids:
        raise err("VALIDATION_FAILED", "prune 与 delete_node_ids 不能同时使用")
    if body.get("prune") and "nodes" not in body:
        raise err("VALIDATION_FAILED", "prune 必须显式提供 nodes")
    with db.transaction() as c:
        old_state = c.execute("SELECT * FROM canvas_states WHERE project_id=?", (project_id,)).fetchone()
        st = dict(old_state) if old_state else {
            "schema_version": 1, "layout_revision": 0,
            "viewport_json": db.dumps({"x": 0, "y": 0, "zoom": 1}), "edges_json": "[]"}
        cur_layout = int(st["layout_revision"] or 0)
        expected = body.get("expected_layout_revision")
        if expected is not None and _revision(expected, "expected_layout_revision") != cur_layout:
            raise err("CANVAS_REVISION_CONFLICT", "画布已被更新", detail={"current_layout_revision": cur_layout})
        old_nodes = {r["node_id"]: dict(r) for r in c.execute(
            "SELECT * FROM canvas_nodes WHERE project_id=?", (project_id,))}
        next_nodes = dict(old_nodes)
        seen = set()
        config_changed = False
        for n in nodes_in:
            if not isinstance(n, dict):
                raise err("VALIDATION_FAILED", "节点必须是对象")
            if set(n) - NODE_KEYS:
                raise err("VALIDATION_FAILED", "节点含未知或只读字段", detail={"allowed": sorted(NODE_KEYS)})
            node_id = n.get("node_id")
            if (not isinstance(node_id, str) or not node_id.strip() or len(node_id) > 200
                    or node_id in seen or node_id in delete_ids):
                raise err("VALIDATION_FAILED", "node_id 必填且不可重复")
            seen.add(node_id)
            old = old_nodes.get(node_id)
            ntype = n.get("type", old["type"] if old else "video")
            if ntype not in NODE_TYPES:
                raise err("VALIDATION_FAILED", f"未知节点类型：{ntype}")
            if node_id == "script" and ntype != "script":
                raise err("VALIDATION_FAILED", "script 节点 ID 保留给项目脚本")
            pos = n.get("position", db.loads(old["position_json"]) if old else {"x": 0, "y": 0})
            binding = n.get("binding", db.loads(old["binding_json"]) if old else {})
            pos = _finite_point(pos, "position")
            _validate_ref(project_id, binding)
            old_cfg = db.loads(old["config_json"]) or {} if old else {}
            cfg = _validate_config(n["config"]) if "config" in n else old_cfg
            if not old or any(key in n for key in ("type", "config", "binding")):
                _validate_node_config(ntype, cfg, binding)
            cfg_rev = int(old["config_revision"] or 1) if old else 0
            expected_cfg = n.get("expected_config_revision")
            if expected_cfg is not None and _revision(expected_cfg, "expected_config_revision") != cfg_rev:
                raise err("CANVAS_REVISION_CONFLICT", f"节点 {node_id} 配置已被更新",
                          detail={"node_id": node_id, "current_config_revision": cfg_rev})
            changed = (cfg != old_cfg or binding != ((db.loads(old["binding_json"]) or {}) if old else {})
                       or (old is not None and ntype != old["type"]))
            config_changed = config_changed or changed
            next_nodes[node_id] = {"id": old["id"] if old else db.new_id("node"),
                "node_id": node_id, "project_id": project_id, "type": ntype,
                "position_json": db.dumps(pos), "binding_json": db.dumps(binding),
                "config_json": db.dumps(cfg), "config_revision": cfg_rev + int(changed) if old else 1,
                "created_at": old["created_at"] if old else db.now(), "updated_at": db.now()}
        if body.get("prune"):
            removed = set(next_nodes) - seen
            config_changed = config_changed or any(db.loads(next_nodes[k]["config_json"]) or
                                                    db.loads(next_nodes[k]["binding_json"]) for k in removed)
            next_nodes = {k: v for k, v in next_nodes.items() if k in seen}
        removed = set(delete_ids) & set(next_nodes)
        config_changed = config_changed or any(db.loads(next_nodes[k]["config_json"]) or
                                                db.loads(next_nodes[k]["binding_json"]) for k in removed)
        next_nodes = {k: v for k, v in next_nodes.items() if k not in removed}
        edges = body.get("edges", db.loads(st.get("edges_json")) or [])
        if not isinstance(edges, list):
            raise err("VALIDATION_FAILED", "edges 必须是数组")
        if removed and "edges" not in body:
            edges = [edge for edge in edges if edge.get("from") not in removed and edge.get("to") not in removed]
        for edge in edges:
            if not isinstance(edge, dict) or edge.get("from") not in next_nodes or edge.get("to") not in next_nodes:
                raise err("CANVAS_INVALID_REFERENCE", "连线引用了不存在的节点", detail={"edge": edge})
            if edge["from"] == edge["to"]:
                raise err("CANVAS_CYCLE", "节点不能连接自身")
        if _has_cycle([], edges):
            raise err("CANVAS_CYCLE", "连线存在循环依赖")
        viewport = body.get("viewport", db.loads(st["viewport_json"]))
        viewport = _finite_point(viewport, "viewport", viewport=True)
        layout_changed = bool(nodes_in or body.get("prune") or removed or "viewport" in body or "edges" in body)
        layout_revision = cur_layout + int(layout_changed)
        # No database writes above this line: failures leave nodes, revisions and confirmations intact.
        # The snapshot and row removal commit together.  Removing a representation never
        # deletes its asset/task/script, and undo does not rewind unrelated canvas work.
        from .canvas_history import record_deletion_in_tx
        deleted_node_ids = old_nodes.keys() - next_nodes.keys()
        deletion_operation_id = record_deletion_in_tx(
            c, project_id, [old_nodes[node_id] for node_id in sorted(deleted_node_ids)],
            db.loads(st.get("edges_json")) or [], layout_revision)
        for node_id in old_nodes.keys() - next_nodes.keys():
            c.execute("DELETE FROM canvas_nodes WHERE project_id=? AND node_id=?", (project_id, node_id))
        for node_id in seen:
            row = next_nodes[node_id]
            cols = list(row)
            c.execute("INSERT INTO canvas_nodes(" + ",".join(cols) + ") VALUES(" + ",".join("?" for _ in cols) + ") "
                      "ON CONFLICT(project_id,node_id) DO UPDATE SET " +
                      ",".join(k + "=excluded." + k for k in cols if k not in ("id", "project_id", "node_id", "created_at")),
                      [row[k] for k in cols])
        c.execute("INSERT INTO canvas_states(project_id,schema_version,layout_revision,viewport_json,edges_json,updated_at) "
                  "VALUES(?,1,?,?,?,?) ON CONFLICT(project_id) DO UPDATE SET "
                  "layout_revision=excluded.layout_revision,viewport_json=excluded.viewport_json,"
                  "edges_json=excluded.edges_json,updated_at=excluded.updated_at",
                  (project_id, layout_revision, db.dumps(viewport), db.dumps(edges), db.now()))
        invalidated = []
        if config_changed:
            c.execute("UPDATE confirmations SET status='invalidated',reason='node_config_changed',invalidated_at=? "
                      "WHERE project_id=? AND object_type='scope' AND status='active'", (db.now(), project_id))
            invalidated = ["scope"]
    return {"project_id": project_id, "layout_revision": layout_revision,
            "nodes_upserted": len(seen), "config_changed": config_changed,
            "nodes_deleted": len(old_nodes.keys() - next_nodes.keys()),
            "deletion_operation_id": deletion_operation_id,
            "invalidated": invalidated, "canvas": get_canvas(project_id),
            "note": "生成配置已变化，请重新报价" if config_changed else "布局已保存，生成确认不变"}


def upsert_script_node_in_tx(connection: Any, project_id: str, script_id: str, script_version: int) -> dict:
    """Bind the real script in the caller's transaction; never duplicate its body.

    The caller owns BEGIN/COMMIT and must have inserted script_versions already.
    Existing layout, unrelated bindings, node config, viewport and edges survive.
    """
    script = connection.execute("SELECT project_id,version FROM script_versions WHERE id=?", (script_id,)).fetchone()
    if (not script or script["project_id"] != project_id or isinstance(script_version, bool)
            or not isinstance(script_version, int) or script["version"] != script_version):
        raise err("CANVAS_INVALID_REFERENCE", "脚本节点必须引用本项目的真实脚本版本")
    old = connection.execute("SELECT * FROM canvas_nodes WHERE project_id=? AND node_id='script'",
                             (project_id,)).fetchone()
    state = connection.execute("SELECT * FROM canvas_states WHERE project_id=?", (project_id,)).fetchone()
    binding = {**((db.loads(old["binding_json"]) or {}) if old else {}),
               "script_id": script_id, "script_version": script_version}
    changed = not old or old["type"] != "script" or db.loads(old["binding_json"]) != binding
    config_revision = int(old["config_revision"] or 1) + int(changed) if old else 1
    layout_revision = int(state["layout_revision"] or 0) if state else 0
    if changed:
        now = db.now()
        if old:
            connection.execute("UPDATE canvas_nodes SET type='script',binding_json=?,config_revision=?,updated_at=? "
                               "WHERE id=?", (db.dumps(binding), config_revision, now, old["id"]))
        else:
            connection.execute("INSERT INTO canvas_nodes(id,project_id,node_id,type,position_json,config_json,"
                               "binding_json,config_revision,created_at,updated_at) VALUES(?,?,'script','script',?,? ,?,1,?,?)",
                               (db.new_id("node"), project_id, db.dumps({"x": 80, "y": 100}), "{}", db.dumps(binding), now, now))
        layout_revision += 1
        if state:
            connection.execute("UPDATE canvas_states SET layout_revision=?,updated_at=? WHERE project_id=?",
                               (layout_revision, now, project_id))
        else:
            connection.execute("INSERT INTO canvas_states(project_id,schema_version,layout_revision,viewport_json,"
                               "edges_json,updated_at) VALUES(?,1,?,?,?,?)",
                               (project_id, layout_revision, db.dumps({"x": 0, "y": 0, "zoom": 1}), "[]", now))
        connection.execute("UPDATE confirmations SET status='invalidated',reason='node_config_changed',invalidated_at=? "
                           "WHERE project_id=? AND object_type='scope' AND status='active'", (now, project_id))
    return {"node_id": "script", "type": "script", "binding": binding,
            "config_revision": config_revision, "layout_revision": layout_revision}
