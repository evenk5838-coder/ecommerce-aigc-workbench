"""助手服务层：会话、连续对话、brief、限制、报价、用量、结构化回复。

设计要点（阶段一修复 2026-10-24）：
- **意图以服务端会话为准**：权限判断只读 `assistant_sessions.intent`，
  不信任请求里传入或省略的 `intent`。
- **连续对话**：用户/助手消息落 `assistant_messages`，多轮上下文按条数与字符预算裁剪；
  刷新后用 `GET /api/assistant/session?project_id=` 恢复当前会话。
- **工具结果回传模型**：最多 `ASSISTANT_MAX_TOOL_ROUNDS` 轮"调用→观察→继续/停止"。
- **结构化回复**：`reply_type/options/optimized_prompt/missing_fields/quote/manual_params`
  由后端给出，前端不解析自然语言。
"""
from __future__ import annotations

import os

from .. import db
from ..errors import err
from . import approval, tools
from .provider import get_provider, provider_status

MAX_QUESTIONS = int(os.getenv("ASSISTANT_MAX_QUESTIONS", "4"))
MAX_TURNS = int(os.getenv("ASSISTANT_MAX_TURNS", "12"))
# 注意区分两个不同概念：
#   MAX_TOOL_EXECUTIONS = 单轮可**执行**的工具次数（不产生 LLM 费用）
#   MAX_TOOL_ROUNDS     = 单轮「模型→工具→模型」的**LLM 请求**轮数上限（产生费用）
MAX_TOOL_EXECUTIONS = int(os.getenv("ASSISTANT_MAX_TOOL_CALLS_PER_TURN", "6"))
MAX_TOOL_ROUNDS = int(os.getenv("ASSISTANT_MAX_TOOL_ROUNDS", "3"))
MAX_TOOL_CALLS = MAX_TOOL_EXECUTIONS   # 兼容旧引用
MAX_SESSION_COST = float(os.getenv("ASSISTANT_MAX_SESSION_COST_CNY", "0"))  # 0=不限
HISTORY_MAX_MESSAGES = int(os.getenv("ASSISTANT_HISTORY_MAX_MESSAGES", "8"))
HISTORY_MAX_CHARS = int(os.getenv("ASSISTANT_HISTORY_MAX_CHARS", "4000"))

INTENTS = ("optimize_only", "generate", "auto")


# ---------- 会话 ----------
def get_session(session_id: str) -> dict:
    row = db.get("assistant_sessions", session_id)
    if not row:
        raise err("ASSISTANT_SESSION_NOT_FOUND", f"会话不存在：{session_id}",
                  detail={"session_id": session_id})
    return row


def find_session_by_project(project_id: str) -> dict | None:
    rows = db.query(
        "SELECT * FROM assistant_sessions WHERE project_id=? ORDER BY updated_at DESC LIMIT 1",
        (project_id,))
    return rows[0] if rows else None


def session_intent(session_id: str) -> str:
    """权限判断的唯一来源。"""
    return str(get_session(session_id).get("intent") or "auto")


def ensure_session(session_id: str | None, project_id: str | None,
                   intent: str | None) -> dict:
    if intent and intent not in INTENTS:
        raise err("VALIDATION_FAILED", f"intent 只能为 {list(INTENTS)}",
                  detail={"allowed": list(INTENTS), "got": intent})
    if session_id:
        row = get_session(session_id)
        if intent and row.get("intent") and row["intent"] != intent:
            set_intent(session_id, intent, reason="请求显式切换")
            row = get_session(session_id)
        if project_id and row.get("project_id") != project_id:
            db.update("assistant_sessions", session_id, {"project_id": project_id})
            row["project_id"] = project_id
        return row
    sid = db.new_id("asst")
    row = {"id": sid, "project_id": project_id, "intent": intent or "auto",
           "status": "active", "turns": 0, "questions": 0, "tool_calls": 0,
           "input_tokens": 0, "output_tokens": 0,
           "created_at": db.now(), "updated_at": db.now()}
    db.insert("assistant_sessions", row)
    return row


def set_intent(session_id: str, intent: str, reason: str = "用户切换") -> dict:
    """用户明确切换意图（如从"仅优化"改为"生成"）。落库并留痕。"""
    if intent not in INTENTS:
        raise err("VALIDATION_FAILED", f"intent 只能为 {list(INTENTS)}",
                  detail={"allowed": list(INTENTS), "got": intent})
    row = get_session(session_id)
    db.update("assistant_sessions", session_id,
              {"intent": intent, "status": "active", "updated_at": db.now()})
    record_usage(session_id, "intent_change", note=f"{row.get('intent')} -> {intent} ({reason})")
    return session_view(get_session(session_id))


def _bump(session_id: str, **fields) -> None:
    db.update("assistant_sessions", session_id, {**fields, "updated_at": db.now()})


def session_view(row: dict) -> dict:
    return {
        "session_id": row["id"], "project_id": row.get("project_id"),
        "intent": row.get("intent"), "status": row.get("status"),
        "limits": {
            "turns_used": int(row.get("turns") or 0), "turns_max": MAX_TURNS,
            "questions_used": int(row.get("questions") or 0), "questions_max": MAX_QUESTIONS,
            "tool_calls_used": int(row.get("tool_calls") or 0),
            "tool_calls_per_turn_max": MAX_TOOL_EXECUTIONS,
            "tool_rounds_max": MAX_TOOL_ROUNDS,
        },
        "usage": {"input_tokens": int(row.get("input_tokens") or 0),
                  "output_tokens": int(row.get("output_tokens") or 0)},
    }


# ---------- 连续对话 ----------
def append_message(session_id: str, role: str, content: str,
                   reply_type: str | None = None, structured: dict | None = None) -> str:
    mid = db.new_id("amsg")
    db.insert("assistant_messages", {
        "id": mid, "session_id": session_id, "role": role,
        "content": content or "", "reply_type": reply_type,
        "structured_json": db.dumps(structured) if structured else None,
        "created_at": db.now(),
    })
    return mid


def history(session_id: str, limit: int | None = None) -> list[dict]:
    """按时间正序返回最近若干条消息（字符预算内）。"""
    rows = db.query(
        "SELECT * FROM assistant_messages WHERE session_id=? ORDER BY created_at DESC LIMIT ?",
        (session_id, int(limit or HISTORY_MAX_MESSAGES)))
    rows.reverse()
    out, chars = [], 0
    for r in reversed(rows):          # 从最新往前累加，超预算即停
        text = r.get("content") or ""
        if chars + len(text) > HISTORY_MAX_CHARS and out:
            break
        chars += len(text)
        out.append({"role": role_map(r["role"]), "content": text,
                    "reply_type": r.get("reply_type"),
                    "structured": db.loads(r.get("structured_json"))})
    out.reverse()
    return out


def role_map(role: str) -> str:
    return {"user": "user", "assistant": "assistant"}.get(role, "system")


def _stage_context(project_id: str) -> dict | None:
    """读取导演台就绪度（只读、无模型调用）。项目不存在时返回 None。"""
    try:
        from .. import director
        return director.readiness(project_id)
    except Exception:  # noqa: BLE001  项目不存在/未迁移时不影响对话
        return None


def messages_for_model(session: dict, project_id: str | None, message: str,
                       facts: dict | None = None,
                       context: dict | None = None,
                       stage: dict | None = None) -> list[dict]:
    stored = get_brief(project_id)["brief"] if project_id else {}
    return [
        {"role": "system", "content": _SYSTEM_PROMPT},
        {"role": "context", "content": {
            "intent": session.get("intent"),
            "project_id": project_id,
            "brief": stored,
            "facts": _facts_context(facts or {"templates": {}, "assets": {}}),
            # 用户在本轮明确指定的素材/类型：**报价必须使用**，不得自行改选
            "stage": {
                "current": (stage or {}).get("current"),
                "facts_ready": ((stage or {}).get("facts") or {}).get("ready"),
                "allowed_actions": (stage or {}).get("allowed_actions") or [],
                "warnings": (stage or {}).get("warnings") or [],
                "rule": ("只围绕 current.stage 引导；已保存/已确认的信息不要重复询问；"
                         "你的建议是草案（draft），不得声称已保存或已确认；"
                         "采用草案必须由用户在界面确认后调用保存接口"),
            },
            "context_hint": {
                "selected_asset_id": (context or {}).get("selected_asset_id"),
                "kind": (context or {}).get("kind"),
                "rule": ("若 selected_asset_id 非空，报价 payload 的 input_asset_id 必须使用它，"
                         "不得改用其它素材；未指定时才需要向用户询问或从素材库挑选并说明来源"),
            },
            "limits": {"questions_max": MAX_QUESTIONS,
                       "questions_used": int(session.get("questions") or 0),
                       "tool_executions_max": MAX_TOOL_EXECUTIONS,
                       "llm_rounds_max": MAX_TOOL_ROUNDS},
        }},
        *history(session["id"]),
        {"role": "user", "content": message},
    ]


# ---------- brief ----------
BRIEF_FIELDS = ("subject", "key_selling_points", "keep_features", "forbidden_elements",
                "audience", "style", "spec_preference", "assumptions")


def _brief_row(project_id: str) -> dict | None:
    rows = db.query("SELECT * FROM assistant_briefs WHERE project_id=?", (project_id,))
    return rows[0] if rows else None


def get_brief(project_id: str) -> dict:
    row = _brief_row(project_id)
    if not row:
        return {"project_id": project_id, "brief": {}, "version": 0}
    return {"project_id": project_id, "brief": db.loads(row["brief_json"]) or {},
            "version": row["version"]}


def save_brief(project_id: str, brief: dict) -> dict:
    if not isinstance(brief, dict):
        raise err("VALIDATION_FAILED", "brief 必须是对象")
    unknown = [k for k in brief if k not in BRIEF_FIELDS]
    if unknown:
        raise err("VALIDATION_FAILED", f"brief 含未知字段：{unknown}",
                  detail={"allowed": list(BRIEF_FIELDS), "unknown": unknown})
    existing = _brief_row(project_id)
    version = (int(existing["version"]) + 1) if existing else 1
    merged = {**(db.loads(existing["brief_json"]) if existing else {}), **brief}
    if existing:
        with db.conn() as c:
            c.execute("UPDATE assistant_briefs SET brief_json=?, version=?, updated_at=? "
                      "WHERE project_id=?",
                      (db.dumps(merged), version, db.now(), project_id))
    else:
        db.insert("assistant_briefs", {"project_id": project_id,
                                       "brief_json": db.dumps(merged),
                                       "version": version, "updated_at": db.now()})
    return {"project_id": project_id, "brief": merged, "version": version}


# ---------- 用量 ----------
def record_usage(session_id: str | None, kind: str, *, provider: str = "",
                 model: str = "", input_tokens: int = 0, output_tokens: int = 0,
                 tool_calls: int = 0, cost_cny: float | None = None,
                 note: str | None = None) -> str:
    uid = db.new_id("ausg")
    db.insert("assistant_usage", {
        "id": uid, "session_id": session_id, "kind": kind, "provider": provider,
        "model": model, "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens), "tool_calls": int(tool_calls),
        "cost_cny": cost_cny, "note": note, "created_at": db.now(),
    })
    return uid


def usage_summary(session_id: str | None = None, limit: int = 100) -> dict:
    sql = ("SELECT kind, COUNT(*) n, COALESCE(SUM(input_tokens),0) ti, "
           "COALESCE(SUM(output_tokens),0) to_, COALESCE(SUM(tool_calls),0) tc, "
           "COALESCE(SUM(cost_cny),0) c FROM assistant_usage")
    args: list = []
    if session_id:
        sql += " WHERE session_id=?"
        args.append(session_id)
    sql += " GROUP BY kind"
    rows = db.query(sql, args)
    items_sql = "SELECT * FROM assistant_usage"
    if session_id:
        items_sql += " WHERE session_id=?"
    items_sql += " ORDER BY created_at DESC LIMIT ?"
    items = db.query(items_sql, [*args, min(int(limit), 200)])
    gen = db.query(
        "SELECT COUNT(*) n, COALESCE(SUM(cost_cny),0) c FROM tasks "
        "WHERE caller_id='assistant' AND cost_cny IS NOT NULL")
    return {
        "session_id": session_id,
        "assistant": {"by_kind": rows,
                      "input_tokens": sum(r["ti"] for r in rows),
                      "output_tokens": sum(r["to_"] for r in rows),
                      "tool_calls": sum(r["tc"] for r in rows),
                      "estimated_cost_cny": round(sum(float(r["c"] or 0) for r in rows), 6)},
        "generation": {"tasks": gen[0]["n"],
                       "estimated_cost_cny": round(float(gen[0]["c"] or 0), 6),
                       "note": "与助手用量分开记录；均为估算，非账单"},
        "items": items,
        "disclaimer": "助手用量为 LLM token 记录；未实现积分账本/余额/支付。",
    }


# ---------- 报价 ----------
def quote(*, session_id: str, project_id: str | None, payload: dict,
          record: bool = True, now_ts: float | None = None) -> dict:
    """助手层报价：复用现有解析与计价，冻结模板版本与完整方案。

    **权限只看服务端会话意图**（不采用请求里的 intent）。
    """
    if not isinstance(payload, dict) or not payload:
        raise err("VALIDATION_FAILED", "payload 不能为空")

    session = get_session(session_id)
    intent = str(session.get("intent") or "auto")
    if intent == "optimize_only":
        raise err("QUOTE_NOT_ALLOWED",
                  "本次会话为「仅优化提示词」，不报价、不生成；如需生成请先切换为生成意图",
                  detail={"intent": intent,
                          "how_to_change": "PUT /api/assistant/intent {intent: 'generate'}"})

    if MAX_SESSION_COST > 0:
        spent = db.query(
            "SELECT COALESCE(SUM(cost_cny),0) c FROM assistant_usage WHERE session_id=?",
            (session_id,))[0]["c"]
        if float(spent or 0) >= MAX_SESSION_COST:
            raise err("ASSISTANT_BUDGET_EXCEEDED",
                      "本次会话助手消耗已达上限，请新建会话",
                      detail={"limit_cny": MAX_SESSION_COST, "spent_cny": float(spent or 0)})

    from .. import main as core   # 运行时导入，避免与 main 循环依赖
    body = core.TaskIn(**{k: payload.get(k) for k in (
        "kind", "model_id", "input_asset_id", "template_id", "template_version",
        "template_fields", "params") if k in payload})
    resolved = core.resolve_task(body, now_ts=now_ts)   # 模板在这里锁定版本

    q = approval.create_quote(session_id=session_id,
                              project_id=project_id or session.get("project_id"),
                              payload=payload, resolved=resolved, now_ts=now_ts)
    frozen = q["frozen"]
    if record:
        record_usage(session_id, "quote", note=q["quote_id"])
    _bump(session_id, project_id=project_id or session.get("project_id"))
    return {
        "quote_id": q["quote_id"], "plan_hash": q["plan_hash"],
        "expires_at": q["expires_at"],
        "estimated_cost_cny": frozen["cost_cny"], "cost_cny": frozen["cost_cny"],
        "kind": frozen["submit_payload"]["kind"],
        "model_id": frozen["submit_payload"]["model_id"],
        "template_id": frozen["template_id"],
        "template_version": frozen["template_version"],
        "template_name": frozen["template_name"],
        "concept_mode": frozen["concept_mode"],
        "effective": frozen["effective"],
        "warnings": frozen["warnings"],
        "model_lifecycle": frozen["model_lifecycle"],
        # 提交/转手动操作的完整参数（前端直接用，不要自己重建提示词）
        "submit_payload": frozen["submit_payload"],
        # 解析后的最终参数（含 prompt）：前端直接展示/提交，不自行重建
        "resolved_params": frozen.get("resolved", {}).get("params"),
        "manual_params": {
            **frozen["submit_payload"],
            "effective": frozen["effective"],
            "template_name": frozen["template_name"],
            "concept_mode": frozen["concept_mode"],
        },
        "side_effects": {"task_created": False, "queued": False,
                         "model_called": False, "budget_deducted": False},
        "next_step": "用户确认后调用 PUT /api/assistant/confirm，再用 approval_id 提交",
    }


def confirm(*, session_id: str, quote_id: str, project_id: str | None,
            user_action: str = "confirm_generate", now_ts: float | None = None) -> dict:
    if session_intent(session_id) == "optimize_only":
        raise err("CONFIRM_NOT_ALLOWED",
                  "本次会话为「仅优化提示词」，不可确认生成；如需生成请先切换意图",
                  detail={"intent": "optimize_only",
                          "how_to_change": "PUT /api/assistant/intent {intent: 'generate'}"})
    quote_row = approval.get_quote(quote_id)
    ap = approval.create_approval(quote_id=quote_id, session_id=session_id,
                                 project_id=project_id, user_action=user_action,
                                 now_ts=now_ts)
    record_usage(session_id, "confirm", note=ap["approval_id"])
    _bump(session_id, status="confirmed")
    return {**ap,
            "submit_payload": ap.get("submit_payload"),
            "next_step": "POST /api/tasks 携带 approval_id 与 submit_payload"}


# ---------- 对话 ----------
def _is_question(text: str) -> bool:
    return ("？" in text) or ("?" in text)


PURE_PROMPT_MAX_CHARS = 900
_PROMPT_FORBIDDEN = (
    "改动说明", "说明：", "推荐模板", "下一步", "点击", "按钮", "报价",
    "费用", "元/张", "如需", "可以帮你", "以下为", "以上为", "注意：",
    "待补充", "需要我", "希望走", "请确认",
)


def pure_prompt(candidate: str | None) -> str | None:
    """G5：只接受**可当提示词直接使用**的文本。

    拒绝：整段回复、含说明/报价/交互引导、Markdown 列表、过长、空。
    宁可返回 None，也不把散文冒充提示词（前端会降级为只展示 reply）。
    """
    t = (candidate or "").strip()
    if not t or len(t) > PURE_PROMPT_MAX_CHARS:
        return None
    if any(bad in t for bad in _PROMPT_FORBIDDEN):
        return None
    if t.startswith(("#", "-", "*", "|", "```")) or "\n-" in t or "\n* " in t:
        return None
    if t.count("\n") > 6:
        return None
    return t


_REFUSAL_MARKERS = ("我不能", "不能做", "无法生成", "不能生成", "不予", "拒绝",
                    "不建议", "禁止", "违规", "不能帮你", "无法协助", "不能提供")


def looks_like_refusal(text: str | None) -> bool:
    t = text or ""
    return any(m in t for m in _REFUSAL_MARKERS)


ALLOWED_TYPES = {"optimize_result", "clarify", "template_recommendation",
                 "quote_ready", "awaiting_facts", "refusal", "message"}


# ---------- F2：会话级工具事实（区分来源，防止把推荐当确认） ----------
FACTS_MAX_TEMPLATES = 8
FACTS_MAX_ASSETS = 8


def _load_facts(session_id: str) -> dict:
    row = db.get("assistant_sessions", session_id) or {}
    facts = db.loads(row.get("facts_json")) or {}
    return {"templates": facts.get("templates") or {},
            "assets": facts.get("assets") or {}}


_FACT_SOURCE_RANK = {"tool_query": 1, "quoted": 2, "user_selected": 3}


def _trim_by_priority(mapping: dict, cap: int) -> dict:
    """超长时按来源优先级保留：user_selected > quoted > tool_query。

    不能简单取"最近 N 条"——那会把最先记录、但最重要的"用户指定素材"挤掉。
    """
    def rank(v) -> int:  # noqa: ANN001
        return _FACT_SOURCE_RANK.get((v or {}).get("source", ""), 0) if isinstance(v, dict) else 0

    items = list(mapping.items())
    high = [(k, v) for k, v in items if rank(v) >= 2]
    low = [(k, v) for k, v in items if rank(v) < 2]
    keep = high[-cap:] if len(high) > cap else high
    remain = max(cap - len(keep), 0)
    return dict(keep + (low[-remain:] if remain else []))


def _save_facts(session_id: str, facts: dict) -> None:
    tmpl = _trim_by_priority(facts["templates"], FACTS_MAX_TEMPLATES)
    asst = _trim_by_priority(facts["assets"], FACTS_MAX_ASSETS)
    db.update("assistant_sessions", session_id,
              {"facts_json": db.dumps({"templates": tmpl, "assets": asst})})


_ASSET_SOURCE_RANK = {"tool_query": 1, "quoted": 2, "user_selected": 3}


def _set_asset_source(facts: dict, asset_id: str, source: str, **extra) -> None:
    """素材来源按优先级合并，避免"用户已选"被后续工具结果降级。"""
    cur = facts["assets"].setdefault(asset_id, {})
    for k, v in extra.items():
        if v is not None:
            cur[k] = v
    if _ASSET_SOURCE_RANK.get(source, 0) >= _ASSET_SOURCE_RANK.get(cur.get("source", ""), 0):
        cur["source"] = source


def _merge_facts(session_id: str, call_log: list[dict],
                 selected_asset_id: str | None = None) -> dict:
    """把本轮工具结果写入会话事实；**来源标签必须区分**。

    - tool_query      ：工具查询到的（**不代表用户确认**）
    - quoted          ：进入报价方案的（**仍未获用户确认**）
    - user_selected   ：用户本轮明确选择/提供的
    """
    facts = _load_facts(session_id)
    if selected_asset_id:
        _set_asset_source(facts, selected_asset_id, "user_selected")

    for entry in call_log:
        if not entry.get("ok"):
            continue
        tool, res = entry.get("tool"), (entry.get("result") or {})
        if tool == "list_templates":
            for it in res.get("items") or []:
                tid = it.get("template_id")
                if tid:
                    facts["templates"].setdefault(tid, {})
                    facts["templates"][tid].update(
                        {"name": it.get("name"), "version": it.get("latest_version"),
                         "source": "tool_query"})
        elif tool == "get_template":
            tid = res.get("template_id")
            snap = res.get("snapshot") or {}
            if tid:
                facts["templates"].setdefault(tid, {})
                facts["templates"][tid].update(
                    {"name": snap.get("name"), "version": res.get("version"),
                     "source": "tool_query"})
        elif tool == "list_assets":
            for it in res.get("items") or []:
                aid = it.get("id")
                if aid:
                    _set_asset_source(facts, aid, "tool_query", kind=it.get("kind"))
        elif tool == "quote":
            tid = res.get("template_id")
            if tid:
                facts["templates"].setdefault(tid, {})
                facts["templates"][tid].update(
                    {"name": res.get("template_name"),
                     "version": res.get("template_version"), "source": "quoted"})
            aid = (res.get("submit_payload") or {}).get("input_asset_id")
            if aid:
                _set_asset_source(facts, aid, "quoted")

    _save_facts(session_id, facts)
    return facts


def _recommended_templates(call_log: list[dict], quote_hit: dict | None,
                           limit: int = 3) -> list[dict]:
    """G1：只从**真实工具结果**生成推荐，并回库校验存在；否则返回 []。"""
    from .. import templates as tpl
    out: list[dict] = []
    seen: set[str] = set()

    def add(template_id: str | None, version, reason: str) -> None:
        if not template_id or template_id in seen or len(out) >= limit:
            return
        snap = tpl.snapshot(template_id, version if isinstance(version, int) else None)
        if not snap:                     # 不存在/版本不存在 → 丢弃，绝不编造
            return
        seen.add(template_id)
        out.append({"template_id": template_id, "name": snap.get("name"),
                    "version": int(snap.get("version") or 0),
                    "kind": snap.get("kind"), "category": snap.get("category"),
                    "reason": reason, "source": "tool",
                    "requires_asset": any(a.get("required") for a in snap.get("required_assets") or [])})

    if quote_hit:
        add(quote_hit.get("template_id"), quote_hit.get("template_version"), "quoted")

    for entry in call_log:
        if entry.get("tool") not in ("list_templates", "get_template") or not entry.get("ok"):
            continue
        res = entry.get("result") or {}
        if entry["tool"] == "get_template":
            add(res.get("template_id"), res.get("version"), "inspected")
        else:
            for it in res.get("items") or []:
                add(it.get("template_id"), it.get("latest_version"), "listed")
    return out


def _facts_context(facts: dict) -> dict:
    return {
        "templates": [{"template_id": k, **v} for k, v in facts["templates"].items()],
        "assets": [{"asset_id": k, **v} for k, v in facts["assets"].items()],
        "note": ("source=tool_query 表示工具查询结果，**不代表用户确认**；"
                 "quoted 表示已进入报价方案（仍未获用户确认）；"
                 "user_selected 表示用户明确选择。价格/可用性以报价时重新校验为准。"),
    }


def _reply_type(intent: str, awaiting: dict | None, structured: dict,
                quote_hit: dict | None, reply: str,
                prompt_pure: str | None) -> tuple[str, bool, bool]:
    """返回 (reply_type, structured_ok, degraded)。

    优先级：报价 > 模型结构化类型 > 正文拒答纠正 > 待补充 > 追问 > 仅优化 > message。
    结构化缺失或与行为矛盾时**明确降级**（degraded=True），不伪造成功类型。
    """
    explicit = structured.get("reply_type")
    structured_ok = explicit in ALLOWED_TYPES
    refusal_text = looks_like_refusal(reply)

    if quote_hit:
        return "quote_ready", structured_ok, not structured_ok
    if structured_ok:
        if refusal_text and explicit != "refusal":
            # 模型自报 clarify/message 但正文是拒答 → 以拒答为准并标记降级
            return "refusal", True, True
        return explicit, True, False
    if awaiting:
        return "awaiting_facts", False, True
    if refusal_text:
        return "refusal", False, True
    if _is_question(reply):
        return "clarify", False, True
    if intent == "optimize_only" and prompt_pure:
        return "optimize_result", False, True
    return "message", False, True


def chat(*, session_id: str | None, project_id: str | None, message: str,
         intent: str | None = None, brief: dict | None = None,
         context: dict | None = None, provider=None) -> dict:  # noqa: ANN001
    p = provider or get_provider()
    if not p.available():
        raise err("ASSISTANT_UNAVAILABLE",
                  "助手暂不可用（未配置语言模型）；可直接使用工作台手动生成",
                  detail=provider_status())

    sess = ensure_session(session_id, project_id, intent)
    sid = sess["id"]
    if int(sess.get("turns") or 0) >= MAX_TURNS:
        raise err("ASSISTANT_TURN_LIMIT",
                  f"本次会话已达轮次上限（{MAX_TURNS}），请新建会话",
                  detail={"turns": sess["turns"], "max": MAX_TURNS})

    project = project_id or sess.get("project_id")
    if brief:
        save_brief(project or "default", brief)
    sess = get_session(sid)

    # 先按历史构建消息（不含本轮），再落库，避免本轮 user 消息在上下文里出现两次
    facts = _load_facts(sid)
    ctx_in = dict(context or {})
    selected = str(ctx_in.get("selected_asset_id") or "").strip() or None
    if selected:
        _set_asset_source(facts, selected, "user_selected")
        _save_facts(sid, facts)          # 先落库：模型本轮就能在上下文里看到
        ctx_in["selected_asset_id"] = selected
    # 导演台上下文：当前阶段 / 已保存事实 / 版本与确认状态 / 允许动作（不调用模型）
    stage_ctx = _stage_context(project) if project else None
    messages = messages_for_model(sess, project, message, facts,
                                  context=ctx_in, stage=stage_ctx)
    append_message(sid, "user", message)
    ctx = {"session_id": sid, "project_id": project, "intent": sess.get("intent")}

    # ---- 工具循环：调用 → 观察结果 → 继续/停止（有限轮）----
    tool_results: list[dict] = []
    call_log: list[dict] = []
    total_in = total_out = 0
    structured: dict = {}
    reply = ""
    stub = False
    for _round in range(max(1, MAX_TOOL_ROUNDS)):
        result = p.chat(messages, tools.tool_specs(), tool_results or None)
        total_in += int(result.input_tokens or 0)
        total_out += int(result.output_tokens or 0)
        structured = {**structured, **(result.structured or {})}
        reply = result.reply or reply
        stub = bool(result.meta.get("stub"))
        calls = result.tool_calls or []
        if not calls:
            break
        remaining = MAX_TOOL_EXECUTIONS - len(call_log)
        if remaining <= 0:
            call_log.append({"tool": "-", "ok": False,
                             "error": {"code": "ASSISTANT_TOOL_BUDGET",
                                       "message": f"单轮工具执行已达上限（{MAX_TOOL_EXECUTIONS}）"}})
            break
        round_entries = []
        for tc in calls[:remaining]:
            try:
                out = tools.execute(tc.name, tc.arguments, ctx)
                entry = {"tool": tc.name, "ok": True, "result": out,
                         "tool_call_id": getattr(tc, "id", "") or ""}
            except Exception as e:  # noqa: BLE001
                entry = {"tool": tc.name, "ok": False,
                         "error": {"code": getattr(e, "code", "TOOL_ERROR"),
                                   "message": str(getattr(e, "message", e))},
                         "tool_call_id": getattr(tc, "id", "") or ""}
            call_log.append(entry)
            tool_results.append(entry)
            round_entries.append((tc, entry))

        if len(calls) > remaining:
            dropped = len(calls) - remaining
            call_log.append({"tool": "-", "ok": False,
                             "error": {"code": "ASSISTANT_TOOL_BUDGET",
                                       "message": (f"单轮工具执行上限 {MAX_TOOL_EXECUTIONS} 已用尽，"
                                                   f"本轮另有 {dropped} 个工具调用被拒绝")}})

        # 按 OpenAI/Ark 兼容格式把工具结果回传模型（真实模型据此继续或停止）
        import json as _json
        messages = [*messages, {
            "role": "assistant",
            "content": reply or None,
            "tool_calls": [{"id": e["tool_call_id"] or f"call_{i}",
                            "type": "function",
                            "function": {"name": tc.name,
                                         "arguments": _json.dumps(tc.arguments,
                                                                  ensure_ascii=False)}}
                           for i, (tc, e) in enumerate(round_entries)],
        }, *[{
            "role": "tool",
            "tool_call_id": e["tool_call_id"] or f"call_{i}",
            "name": e["tool"],
            "content": _json.dumps(e.get("result") if e.get("ok") else e.get("error"),
                                   ensure_ascii=False),
        } for i, (tc, e) in enumerate(round_entries)]]

    quote_hit = None
    for entry in call_log:
        if entry.get("tool") == "quote" and entry.get("ok"):
            quote_hit = entry["result"]

    questions = int(sess.get("questions") or 0) + (1 if _is_question(reply) else 0)
    status = sess.get("status")
    awaiting = None
    if questions >= MAX_QUESTIONS and _is_question(reply):
        status = "awaiting_facts"
        awaiting = {"reason": "questions_limit_reached",
                    "questions_used": questions, "questions_max": MAX_QUESTIONS,
                    "missing_fields": structured.get("missing_fields") or [],
                    "hint": "请补充缺失的商品事实或素材；助手不会用假设补齐后生成"}

    prompt_pure = pure_prompt(structured.get("optimized_prompt"))
    explanation = (structured.get("explanation") or None)
    rtype, structured_ok, degraded = _reply_type(
        str(sess.get("intent") or "auto"), awaiting, structured, quote_hit, reply, prompt_pure)
    # G5：模型声称给了提示词但未通过纯度校验 → 明确降级
    if structured.get("optimized_prompt") and prompt_pure is None:
        degraded = True

    facts = _merge_facts(sid, call_log, selected_asset_id=selected)
    recommended = _recommended_templates(call_log, quote_hit)

    _bump(sid, turns=int(sess.get("turns") or 0) + 1, questions=questions,
          tool_calls=int(sess.get("tool_calls") or 0) + len(call_log),
          input_tokens=int(sess.get("input_tokens") or 0) + total_in,
          output_tokens=int(sess.get("output_tokens") or 0) + total_out,
          status=status)
    record_usage(sid, "llm", provider=p.name, model=p.model,
                 input_tokens=total_in, output_tokens=total_out,
                 tool_calls=len(call_log), note=f"chat:{rtype}")

    payload_out = {
        "session_id": sid,
        "reply": reply,
        "reply_type": rtype,
        "options": structured.get("options") or [],
        # G5：只有通过纯度校验的提示词才给；否则 null（前端只展示 reply）
        "optimized_prompt": prompt_pure,
        "explanation": explanation,
        "missing_fields": structured.get("missing_fields") or [],
        "brief": get_brief(project)["brief"] if project else {},
        "quote": ({"quote_id": quote_hit["quote_id"],
                   "estimated_cost_cny": quote_hit["estimated_cost_cny"],
                   "effective": quote_hit["effective"],
                   "template_id": quote_hit["template_id"],
                   "template_version": quote_hit["template_version"],
                   "expires_at": quote_hit["expires_at"],
                   "plan_hash": quote_hit["plan_hash"]} if quote_hit else None),
        "manual_params": quote_hit.get("manual_params") if quote_hit else None,
        "submit_payload": quote_hit.get("submit_payload") if quote_hit else None,
        "recommended_templates": recommended,
        "structured_ok": structured_ok,
        "degraded": degraded,
        "refusal": {"detected": looks_like_refusal(reply),
                    "structured": structured.get("reply_type") == "refusal"},
        "tool_calls": call_log,
        "awaiting_facts": awaiting,
        "intent": get_session(sid).get("intent"),
        "session": session_view(get_session(sid)),
        "provider": {"name": p.name, "model": p.model, "stub": stub},
        "note": ("当前为测试替身回复，不代表真实模型能力" if stub else None),
    }
    append_message(sid, "assistant", reply, reply_type=rtype,
                   structured={k: payload_out[k] for k in
                               ("reply_type", "options", "optimized_prompt", "explanation",
                                "missing_fields", "quote", "recommended_templates",
                                "structured_ok", "degraded", "refusal")})
    return payload_out


_SYSTEM_PROMPT = """你是「物料间」电商创作工作台的创作助手。
先判断用户要什么：①只要润色/改写 → 直接给优化后的提示词并结束（不报价、不推荐模板、不提交）；
②明确要生成 → 补齐关键项 → 从真实模板中选 → 用 quote 报价 → 让用户在界面确认；
③问建议 → 给 2–3 个选项与推荐理由。

硬规则：
- 你看不到图片内容。商品名称、数量、包装、材质、功效只能来自用户文本；不得从文件名/尺寸/素材 ID 推断。
- 不虚构商品事实，不承诺画质/审核/上架/销量。
- 报价≠用户同意：你不能自己确认，也没有提交工具；确认必须由用户在界面完成。
- 参数（模板/模型/比例/时长/主体）改变后必须重新报价。
- 不自动重试；拒绝涉黄/涉暴/涉政与违规电商宣传。
- 简短中文输出；复述结论而非原始 JSON。
- 若 context_hint.selected_asset_id 非空，说明**用户已明确指定素材**：
  报价 payload 的 input_asset_id 必须用它，不要另选素材，也不要再向用户确认素材。
- 上下文里的 facts 带 source：tool_query 只是工具查到的东西，**不等于用户确认**；
  quoted 是已报价方案（仍未经用户确认）；user_selected 才是用户明确选择。
  不得把助手推荐当成用户已确认的事实；价格与可用性以报价时重新校验的结果为准。
- optimized_prompt 只放**可直接使用的纯提示词**，不要夹带说明、报价或交互引导；
  说明性内容放 explanation。

输出结构化字段（供界面渲染，勿省略）：
reply_type（optimize_result|clarify|template_recommendation|quote_ready|awaiting_facts|refusal|message）、
options（2–4 个候选，含 id/label）、optimized_prompt（仅优化模式必填）、
missing_fields（缺失的商品事实字段名列表）。"""
