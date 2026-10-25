"""助手 HTTP 路由（阶段一）。"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..errors import err
from . import approval, service
from .provider import provider_status

router = APIRouter(prefix="/api/assistant", tags=["assistant"])


class ChatIn(BaseModel):
    session_id: str | None = None
    project_id: str | None = None
    message: str
    intent: str | None = Field(default=None, pattern="^(optimize_only|generate|auto)$")
    brief: dict | None = None
    context: dict | None = None


class BriefIn(BaseModel):
    brief: dict


class QuoteIn(BaseModel):
    session_id: str
    project_id: str | None = None
    payload: dict
    # 兼容字段：**忽略**，权限以服务端会话意图为准（保留仅为不破坏旧调用）
    intent: str | None = None


class IntentIn(BaseModel):
    session_id: str
    intent: str = Field(pattern="^(optimize_only|generate|auto)$")
    reason: str | None = None


class ConfirmIn(BaseModel):
    session_id: str
    quote_id: str
    project_id: str | None = None
    user_action: str = "confirm_generate"


@router.get("/status")
def status():
    """助手可用性。未配置 LLM 时 available=false，前端必须显示"暂不可用"。"""
    return provider_status()


@router.put("/intent")
def put_intent(body: IntentIn):
    """用户明确切换意图（如 仅优化 → 生成）。权限判断只读服务端存储的意图。"""
    return service.set_intent(body.session_id, body.intent, body.reason or "用户切换")


@router.get("/session")
def get_session_by_project(project_id: str):
    """刷新恢复：按项目取最近会话（含历史与当前状态）。"""
    row = service.find_session_by_project(project_id)
    if not row:
        return {"project_id": project_id, "session": None, "history": [],
                "brief": service.get_brief(project_id)["brief"]}
    return {"project_id": project_id,
            "session": service.session_view(row),
            "history": service.history(row["id"]),
            "brief": service.get_brief(project_id)["brief"]}


@router.post("/chat")
def chat(body: ChatIn):
    return service.chat(session_id=body.session_id, project_id=body.project_id,
                        message=body.message, intent=body.intent,
                        brief=body.brief, context=body.context)


@router.get("/brief")
def get_brief(project_id: str):
    return service.get_brief(project_id)


@router.put("/brief")
def put_brief(project_id: str, body: BriefIn):
    return service.save_brief(project_id, body.brief)


@router.post("/quote")
def quote(body: QuoteIn):
    """助手报价：复用现有解析与计价，另存报价单。**不建任务、不扣费、不等于批准**。"""
    return service.quote(session_id=body.session_id, project_id=body.project_id,
                         payload=body.payload)


@router.post("/confirm")
def confirm(body: ConfirmIn):
    """用户界面确认 → 生成一次性批准。**不是 LLM 工具**。"""
    return service.confirm(session_id=body.session_id, quote_id=body.quote_id,
                           project_id=body.project_id, user_action=body.user_action)


@router.get("/quote/{quote_id}")
def get_quote(quote_id: str):
    import json as _json
    q = approval.get_quote(quote_id)
    frozen = approval.quote_frozen(q)
    return {**q, "payload": _json.loads(q["payload_json"]),
            "effective": _json.loads(q["effective_json"]),
            "template_id": frozen.get("template_id"),
            "template_version": frozen.get("template_version"),
            "template_name": frozen.get("template_name"),
            "concept_mode": frozen.get("concept_mode"),
            "submit_payload": frozen.get("submit_payload"),
            "manual_params": ({**frozen["submit_payload"],
                               "effective": frozen.get("effective"),
                               "template_name": frozen.get("template_name"),
                               "concept_mode": frozen.get("concept_mode")}
                              if frozen.get("submit_payload") else None),
            "expired": float(q.get("expires_at") or 0) <= __import__("time").time()}


@router.get("/usage")
def usage(session_id: str | None = None):
    return service.usage_summary(session_id)


@router.get("/session/{session_id}")
def get_session(session_id: str):
    return service.session_view(service.get_session(session_id))
