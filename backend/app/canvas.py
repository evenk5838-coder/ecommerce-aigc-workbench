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
from typing import Any

from . import config, db, projects
from .errors import err

NODE_TYPES = ("image", "video", "storyboard", "shot", "asset", "script")
CONFIG_KEYS = ("generation_mode", "model_id", "prompt", "input_asset_id", "params")


def _state(project_id: str) -> dict:
    rows = db.query("SELECT * FROM canvas_states WHERE project_id=?", (project_id,))
    if rows:
        return rows[0]
    return {"project_id": project_id, "schema_version": 1, "layout_revision": 0,
            "viewport_json": db.dumps({"x": 0, "y": 0, "zoom": 1}), "edges_json": "[]"}


def get_canvas(project_id: str) -> dict:
    projects.get_project(project_id)
    st = _state(project_id)
    nodes = db.query("SELECT * FROM canvas_nodes WHERE project_id=? ORDER BY created_at",
                     (project_id,))
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
        out.append({"node_id": n["node_id"], "type": n["type"],
                    "position": db.loads(n["position_json"]) or {"x": 0, "y": 0},
                    "config": cfg, "binding": binding,
                    "config_revision": int(n["config_revision"] or 1),
                    "last_execution": last})
    return {"schema_version": int(st["schema_version"] or 1),
            "layout_revision": int(st["layout_revision"] or 0),
            "viewport": db.loads(st["viewport_json"]) or {"x": 0, "y": 0, "zoom": 1},
            "nodes": out, "edges": db.loads(st.get("edges_json")) or [],
            "note": "布局与生成配置分开；last_execution 只读，不可 PATCH"}


def _validate_ref(project_id: str, binding: dict) -> None:
    if not isinstance(binding, dict):
        raise err("CANVAS_INVALID_REFERENCE", "binding 必须是对象")
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
    aid = binding.get("asset_id") or (binding.get("config") or {}).get("input_asset_id")
    if aid:
        if not db.get("assets", aid):
            raise err("CANVAS_INVALID_REFERENCE", f"素材不存在：{aid}")


def _validate_config(cfg: dict) -> dict:
    if not isinstance(cfg, dict):
        raise err("VALIDATION_FAILED", "config 必须是对象")
    unknown = [k for k in cfg if k not in CONFIG_KEYS]
    if unknown:
        raise err("VALIDATION_FAILED", f"config 含未知字段：{unknown}",
                  detail={"allowed": list(CONFIG_KEYS)})
    out = json.loads(json.dumps({k: cfg[k] for k in CONFIG_KEYS if k in cfg}))
    mode = out.get("generation_mode")
    model_id = out.get("model_id")
    if not isinstance(out.get("params", {}), dict):
        raise err("VALIDATION_FAILED", "params 必须是对象")
    if "prompt" in out and not isinstance(out["prompt"], str):
        raise err("VALIDATION_FAILED", "prompt 必须是字符串")
    aid = out.get("input_asset_id")
    if aid:
        asset = db.get("assets", aid)
        if not asset or asset.get("kind") != "image":
            raise err("CANVAS_INVALID_REFERENCE", "参考素材不存在或不是图片")
    if mode and mode not in ("text_to_image", "image_to_image",
                             "text_to_video", "image_to_video"):
        raise err("VALIDATION_FAILED", f"未知 generation_mode：{mode}")
    if model_id:
        if model_id not in config.MODEL_REGISTRY:
            raise err("VALIDATION_FAILED", f"未知模型：{model_id}")
        if not config.model_submittability(model_id)["submittable"]:
            raise err("MODEL_NOT_OPENED", f"模型 {model_id} 不可提交")
        kind = config.MODEL_REGISTRY[model_id]["kind"]
        if mode:
            caps = config.MODEL_CAPABILITIES.get(model_id) or {}
            if caps.get("input_modes") and mode not in caps["input_modes"]:
                raise err("MODEL_CAPABILITY_MISMATCH",
                          f"模型 {model_id} 不支持 {mode}",
                          detail={"field": "generation_mode", "got": mode,
                                  "allowed": caps.get("input_modes")})
        params = out.get("params") or {}
        if kind == "video":
            if params.get("duration") is not None:
                params["duration"] = config.resolve_duration_s(
                    model_id, params["duration"], strict=True)
            problems = config.validate_params_for_model(model_id, "video", params)
            if problems:
                raise err("MODEL_CAPABILITY_MISMATCH", "节点参数不被模型支持",
                          detail={"problems": problems})
            out["params"] = params
        if kind == "image":
            problems = config.validate_params_for_model(model_id, "image", {**params, "generation_mode": mode or "text_to_image"})
            if problems:
                raise err("MODEL_CAPABILITY_MISMATCH", "节点参数不被模型支持", detail={"problems": problems})
        if mode in ("text_to_image", "text_to_video"):
            if out.get("input_asset_id"):
                raise err("GENERATION_MODE_CONFLICT",
                          "文生节点不能携带参考素材", detail={"mode": mode})
        if mode in ("image_to_image", "image_to_video") and not out.get("input_asset_id"):
            raise err("REFERENCE_IMAGE_REQUIRED", f"{mode} 节点需要参考素材")
    return out


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
            node_id = str(n.get("node_id") or "").strip()
            if not node_id or node_id in seen:
                raise err("VALIDATION_FAILED", "node_id 必填且不可重复")
            seen.add(node_id)
            old = old_nodes.get(node_id)
            ntype = n.get("type", old["type"] if old else "video")
            if ntype not in NODE_TYPES:
                raise err("VALIDATION_FAILED", f"未知节点类型：{ntype}")
            pos = n.get("position", db.loads(old["position_json"]) if old else {"x": 0, "y": 0})
            binding = n.get("binding", db.loads(old["binding_json"]) if old else {})
            if not isinstance(pos, dict):
                raise err("VALIDATION_FAILED", "position 必须是对象")
            _validate_ref(project_id, binding)
            old_cfg = db.loads(old["config_json"]) or {} if old else {}
            cfg = _validate_config(n["config"]) if "config" in n else old_cfg
            cfg_rev = int(old["config_revision"] or 1) if old else 0
            expected_cfg = n.get("expected_config_revision")
            if expected_cfg is not None and _revision(expected_cfg, "expected_config_revision") != cfg_rev:
                raise err("CANVAS_REVISION_CONFLICT", f"节点 {node_id} 配置已被更新",
                          detail={"node_id": node_id, "current_config_revision": cfg_rev})
            changed = cfg != old_cfg or (old is not None and (
                binding != (db.loads(old["binding_json"]) or {}) or ntype != old["type"]))
            config_changed = config_changed or changed
            next_nodes[node_id] = {"id": old["id"] if old else db.new_id("node"),
                "node_id": node_id, "project_id": project_id, "type": ntype,
                "position_json": db.dumps(pos), "binding_json": db.dumps(binding),
                "config_json": db.dumps(cfg), "config_revision": cfg_rev + int(changed) if old else 1,
                "created_at": old["created_at"] if old else db.now(), "updated_at": db.now()}
        if body.get("prune"):
            removed = set(next_nodes) - seen
            config_changed = config_changed or bool(removed)
            next_nodes = {k: v for k, v in next_nodes.items() if k in seen}
        edges = body.get("edges", db.loads(st.get("edges_json")) or [])
        if not isinstance(edges, list):
            raise err("VALIDATION_FAILED", "edges 必须是数组")
        for edge in edges:
            if not isinstance(edge, dict) or edge.get("from") not in next_nodes or edge.get("to") not in next_nodes:
                raise err("CANVAS_INVALID_REFERENCE", "连线引用了不存在的节点", detail={"edge": edge})
            if edge["from"] == edge["to"]:
                raise err("CANVAS_CYCLE", "节点不能连接自身")
        if _has_cycle([], edges):
            raise err("CANVAS_CYCLE", "连线存在循环依赖")
        viewport = body.get("viewport", db.loads(st["viewport_json"]))
        if not isinstance(viewport, dict):
            raise err("VALIDATION_FAILED", "viewport 必须是对象")
        layout_changed = bool(nodes_in or body.get("prune") or "viewport" in body or "edges" in body)
        layout_revision = cur_layout + int(layout_changed)
        # No database writes above this line: failures leave nodes, revisions and confirmations intact.
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
            "invalidated": invalidated, "canvas": get_canvas(project_id),
            "note": "生成配置已变化，请重新报价" if config_changed else "布局已保存，生成确认不变"}
