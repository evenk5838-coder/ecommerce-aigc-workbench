"""工具执行器：把现有能力薄封装给 LLM 使用。

**硬约束**：
- 白名单固定，不包含 `confirm`（用户批准）与 `submit`（建任务）——
  模型无法自己确认、也无法自己提交。
- 只读接口 + 报价（报价本身不建任务、不扣费）。
- 助手来源、额度、价格一律由后端决定，工具只返回事实。
"""
from __future__ import annotations

from typing import Callable

from .. import config, db, templates
from ..errors import err
from .provider import ToolSpec

# 单轮工具调用上限（可在 service 层用会话配置覆盖）
DEFAULT_MAX_TOOL_CALLS = 3

FORBIDDEN_TOOLS = {"confirm", "submit", "approve", "pay", "set_quota"}

_TOOL_SPECS: list[ToolSpec] = [
    ToolSpec("list_models", "列出可用生成模型及其能力、开通状态、下线信息",
             {"type": "object", "properties": {"kind": {"type": "string", "enum": ["image", "video"]}}}),
    ToolSpec("list_templates", "列出创作模板（含字段与默认参数）",
             {"type": "object", "properties": {
                 "kind": {"type": "string", "enum": ["image", "video"]},
                 "category": {"type": "string", "enum": ["work", "hot"]}}}),
    ToolSpec("get_template", "读取指定模板版本的完整定义",
             {"type": "object", "properties": {"template_id": {"type": "string"},
                                              "version": {"type": "integer"}},
              "required": ["template_id"]}),
    ToolSpec("list_assets", "列出已有素材的元数据（类型/尺寸/来源，不含图片内容）",
             {"type": "object", "properties": {
                 "kind": {"type": "string", "enum": ["image", "video"]},
                 "limit": {"type": "integer"}}}),
    ToolSpec("get_brief", "读取当前项目已确认的 brief",
             {"type": "object", "properties": {"project_id": {"type": "string"}},
              "required": ["project_id"]}),
    ToolSpec("save_brief", "保存/更新当前项目 brief（只存用户确认的信息）",
             {"type": "object", "properties": {"project_id": {"type": "string"},
                                              "brief": {"type": "object"}},
              "required": ["project_id", "brief"]}),
    ToolSpec("quote", "为一次生成请求报价（不建任务、不扣费；报价≠批准）",
             {"type": "object", "properties": {"payload": {"type": "object"}},
              "required": ["payload"]}),
    ToolSpec("get_task", "查询任务状态",
             {"type": "object", "properties": {"task_id": {"type": "string"}},
              "required": ["task_id"]}),
    ToolSpec("cancel_task", "请求取消任务（语义与手动取消一致）",
             {"type": "object", "properties": {"task_id": {"type": "string"}},
              "required": ["task_id"]}),
]


def tool_specs() -> list[ToolSpec]:
    return [t for t in _TOOL_SPECS if t.name not in FORBIDDEN_TOOLS]


# ---------- 各工具实现 ----------
def _list_models(args: dict) -> dict:
    from .. import main as core
    items = core.models()["items"]
    kind = args.get("kind")
    if kind:
        items = [m for m in items if m["kind"] == kind]
    # 只给必要字段，减少 token 与误导
    return {"items": [{
        "id": m["id"], "name": m["name"], "kind": m["kind"],
        "activation": m["activation"], "lifecycle": m["lifecycle"],
        "pricing": m["pricing"],
        "input_modes": m["capabilities"]["input_modes"],
        "ratios": m["capabilities"]["ratios"],
        "resolutions": m["capabilities"]["resolutions"],
        "duration_s": m["capabilities"]["duration_s"],
        **({"parameter_combinations": m["capabilities"]["parameter_combinations"]}
           if "parameter_combinations" in m["capabilities"] else {}),
    } for m in items]}


def _list_templates(args: dict) -> dict:
    rows = templates.list_templates(kind=args.get("kind"), category=args.get("category"))
    return {"items": [{
        "template_id": r["template_id"], "name": r["name"], "kind": r["kind"],
        "category": r["category"], "latest_version": r["latest_version"],
        "description": (r.get("snapshot") or {}).get("description"),
        "required_assets": (r.get("snapshot") or {}).get("required_assets", []),
        "fields": (r.get("snapshot") or {}).get("fields", []),
        "default_params": (r.get("snapshot") or {}).get("default_params", {}),
    } for r in rows]}


def _get_template(args: dict) -> dict:
    d = templates.detail(args["template_id"], args.get("version"))
    return {"template_id": d["template_id"], "version": d.get("requested_version"),
            "snapshot": d["snapshot"]}


def _list_assets(args: dict) -> dict:
    sql = "SELECT id, kind, mime, width, height, duration_ms, source, created_at FROM assets"
    params: list = []
    if args.get("kind"):
        sql += " WHERE kind=?"
        params.append(args["kind"])
    sql += " ORDER BY created_at DESC LIMIT ?"
    params.append(min(int(args.get("limit") or 20), 50))
    return {"items": db.query(sql, params),
            "note": "仅元数据；助手看不到图片内容"}


def _get_brief(args: dict) -> dict:
    from .service import get_brief
    return get_brief(args["project_id"])


def _save_brief(args: dict) -> dict:
    from .service import save_brief
    return save_brief(args["project_id"], args.get("brief") or {})


def _quote(args: dict, ctx: dict) -> dict:
    """报价工具。**权限由 service.quote 读服务端会话意图判定**，此处不信任任何传入 intent。"""
    from .service import quote as svc_quote
    return svc_quote(session_id=ctx["session_id"], project_id=ctx.get("project_id"),
                     payload=args.get("payload") or {}, record=True)


def _get_task(args: dict) -> dict:
    from .. import main as core
    t = core.get_task(args["task_id"])
    return {"task_id": t["id"], "status": t["status"], "stage": t.get("stage"),
            "progress": t.get("progress"), "error_code": t.get("error_code"),
            "output_asset_id": t.get("output_asset_id"),
            "cost_cny": t.get("cost_cny"), "model_id": t.get("model_id")}


def _cancel_task(args: dict, ctx: dict) -> dict:
    """取消任务属于副作用操作：仅优化会话禁止，且只允许取消本会话产生的任务。"""
    from .service import session_intent
    from .. import db as _db, worker
    if session_intent(ctx["session_id"]) == "optimize_only":
        raise err("TOOL_FORBIDDEN", "仅优化提示词的会话不可取消任务",
                  detail={"tool": "cancel_task", "intent": "optimize_only"})
    task = _db.get("tasks", args["task_id"])
    if not task:
        raise err("TASK_NOT_FOUND", f"任务不存在：{args['task_id']}")
    if task.get("caller_id") != "assistant" or task.get("external_request_id") != ctx["session_id"]:
        raise err("TOOL_FORBIDDEN", "只能取消本会话提交的任务",
                  detail={"tool": "cancel_task"})
    return worker.cancel(args["task_id"])


_NEED_CTX = {"quote", "cancel_task"}

_HANDLERS: dict[str, Callable[..., dict]] = {
    "list_models": _list_models,
    "list_templates": _list_templates,
    "get_template": _get_template,
    "list_assets": _list_assets,
    "get_brief": _get_brief,
    "save_brief": _save_brief,
    "quote": _quote,
    "get_task": _get_task,
    "cancel_task": _cancel_task,
}


def execute(name: str, arguments: dict, ctx: dict) -> dict:
    """执行一个工具调用。未知/禁用工具一律拒绝（不静默忽略）。"""
    if name in FORBIDDEN_TOOLS:
        raise err("TOOL_FORBIDDEN", f"工具 {name} 不允许由助手调用",
                  detail={"tool": name})
    handler = _HANDLERS.get(name)
    if not handler:
        raise err("TOOL_NOT_FOUND", f"未知工具：{name}",
                  detail={"allowed": sorted(_HANDLERS)})
    args = arguments or {}
    if name in _NEED_CTX:
        return handler(args, ctx)
    return handler(args)
