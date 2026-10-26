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

from . import config, db, director, projects
from .errors import err

NODE_TYPES = ("image", "video", "storyboard", "shot", "asset", "script")
CONFIG_KEYS = ("generation_mode", "model_id", "prompt", "input_asset_id", "params")


def _state(project_id: str) -> dict:
    rows = db.query("SELECT * FROM canvas_states WHERE project_id=?", (project_id,))
    if rows:
        return rows[0]
    db.insert("canvas_states", {"project_id": project_id, "schema_version": 1,
                                "layout_revision": 0, "viewport_json": db.dumps(
                                    {"x": 0, "y": 0, "zoom": 1}),
                                "updated_at": db.now()})
    return db.query("SELECT * FROM canvas_states WHERE project_id=?", (project_id,))[0]


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
        if binding.get("shot_id"):
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
            "nodes": out,
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
    out = {k: cfg[k] for k in CONFIG_KEYS if k in cfg}
    mode = out.get("generation_mode")
    model_id = out.get("model_id")
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
        if mode in ("text_to_video",):
            if out.get("input_asset_id"):
                raise err("GENERATION_MODE_CONFLICT",
                          "文生视频节点不能携带参考素材", detail={"mode": mode})
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


def patch_canvas(project_id: str, body: dict) -> dict:
    """并发校验后的布局/节点编辑。**不生成、不确认**。"""
    projects.get_project(project_id)
    st = _state(project_id)
    cur_layout = int(st["layout_revision"] or 0)
    expected = body.get("expected_layout_revision")
    if expected is not None and int(expected) != cur_layout:
        raise err("CANVAS_REVISION_CONFLICT",
                  f"画布已被更新：当前 r{cur_layout}，提交 r{expected}",
                  detail={"current_layout_revision": cur_layout})

    nodes_in = body.get("nodes") or []
    edges_in = body.get("edges") or []
    config_changed = False
    upserts = 0

    for n in nodes_in:
        node_id = str(n.get("node_id") or "").strip()
        if not node_id:
            raise err("VALIDATION_FAILED", "node_id 必填")
        ntype = n.get("type") or "video"
        if ntype not in NODE_TYPES:
            raise err("VALIDATION_FAILED", f"未知节点类型：{ntype}",
                      detail={"allowed": list(NODE_TYPES)})
        pos = n.get("position") or {}
        if not isinstance(pos, dict):
            raise err("VALIDATION_FAILED", "position 必须是对象")
        binding = n.get("binding") or {}
        _validate_ref(project_id, binding)

        existing = db.query("SELECT * FROM canvas_nodes WHERE project_id=? AND node_id=?",
                            (project_id, node_id))
        cfg_in = n.get("config")
        if existing:
            row = existing[0]
            patch: dict[str, Any] = {"updated_at": db.now(),
                                     "position_json": db.dumps(pos),
                                     "binding_json": db.dumps(binding),
                                     "type": ntype}
            if cfg_in is not None:
                new_cfg = _validate_config(cfg_in)
                old_cfg = db.loads(row["config_json"]) or {}
                if new_cfg != old_cfg:
                    patch["config_json"] = db.dumps(new_cfg)
                    patch["config_revision"] = int(row["config_revision"] or 1) + 1
                    config_changed = True
                expected_cfg = n.get("expected_config_revision")
                if expected_cfg is not None and int(expected_cfg) != int(row["config_revision"] or 1):
                    raise err("CANVAS_REVISION_CONFLICT",
                              f"节点 {node_id} 配置已被更新",
                              detail={"node_id": node_id,
                                      "current_config_revision": row["config_revision"]})
            db.update("canvas_nodes", row["id"], patch)
        else:
            cfg = _validate_config(cfg_in or {})
            db.insert("canvas_nodes", {"id": db.new_id("node"), "project_id": project_id,
                                       "node_id": node_id, "type": ntype,
                                       "position_json": db.dumps(pos),
                                       "config_json": db.dumps(cfg),
                                       "binding_json": db.dumps(binding),
                                       "config_revision": 1,
                                       "created_at": db.now(), "updated_at": db.now()})
            if cfg:
                config_changed = True
        upserts += 1

    # 删除未提交的节点（仅当显式传 nodes 时才做全量对齐）
    if body.get("prune", False):
        keep = {str(n.get("node_id")) for n in nodes_in}
        for row in db.query("SELECT node_id FROM canvas_nodes WHERE project_id=?", (project_id,)):
            if row["node_id"] not in keep:
                db.query("DELETE FROM canvas_nodes WHERE project_id=? AND node_id=?",
                         (project_id, row["node_id"]))

    # 连线校验：引用存在 + 无环
    known = {r["node_id"] for r in db.query(
        "SELECT node_id FROM canvas_nodes WHERE project_id=?", (project_id,))}
    for e in edges_in:
        if e.get("from") not in known or e.get("to") not in known:
            raise err("CANVAS_INVALID_REFERENCE", "连线引用了不存在的节点",
                      detail={"edge": e})
        if e.get("from") == e.get("to"):
            raise err("CANVAS_CYCLE", "节点不能连接自身", detail={"edge": e})
    if _has_cycle([{"node_id": k} for k in known], edges_in):
        raise err("CANVAS_CYCLE", "连线存在循环依赖", detail={"edges": edges_in})

    layout_revision = cur_layout + (1 if nodes_in or body.get("viewport") else 0)
    with db.conn() as c:
        c.execute("UPDATE canvas_states SET layout_revision=?, viewport_json=?, "
                  "edges_json=?, updated_at=? WHERE project_id=?",
                  (layout_revision,
                   db.dumps(body.get("viewport") or db.loads(st["viewport_json"]) or
                            {"x": 0, "y": 0, "zoom": 1}),
                   db.dumps(edges_in) if "edges" in body else st.get("edges_json"),
                   db.now(), project_id))

    invalidated: list[str] = []
    if config_changed:
        n = director.invalidate("scope", project_id=project_id, reason="node_config_changed")
        invalidated.append("scope")
        if n:
            invalidated.append(f"scope:{n}")

    return {"project_id": project_id, "layout_revision": layout_revision,
            "nodes_upserted": upserts,
            "config_changed": config_changed,
            "invalidated": invalidated,
            "canvas": get_canvas(project_id),
            "note": ("仅移动节点不使报价失效；生成配置变化已使整组报价失效"
                     if config_changed else "布局/位置更新，未影响生成确认与报价")}
