"""对话导演（D1/D2/D4）：项目范围对话 → 结构化提案**自动保存** → 用户确认。

硬规则：
- **项目由服务端绑定**：工具不接受 project_id 参数；模型无法切换到别的项目。
- **自动保存 ≠ 用户确认 ≠ 授权付费**：只写未确认草案（脚本版本 `confirmed_at=NULL`），
  不创建任何图片/视频任务，不调用确认/提交/付费工具。
- **草案/事实分离**：`director_proposals` 存提案；用户 apply 后才写入项目事实或镜头计划。
- **幂等**：`director_requests(project_id, client_request_id)` 唯一；同键同内容返回原结果，
  同键不同内容 → 409；pending 状态返回 409（未知状态先查回执）。
- **预算**：每次 LLM 请求前检查单次/单会话上限；用量写入 `assistant_usage`。
"""
from __future__ import annotations

import hashlib
import json

from . import config, db, director, projects, shots
from .assistant import provider as prov
from .assistant.provider import ToolSpec
from .errors import err

# ---------------- 工具（项目已绑定，无 project_id 参数） ----------------
PROJECT_TOOLS: list[ToolSpec] = [
    ToolSpec("get_project_state", "读取当前项目真实状态（brief/脚本/镜头/就绪度）",
             {"type": "object", "properties": {}}),
    ToolSpec("list_models", "列出可用生成模型的能力与可提交状态",
             {"type": "object", "properties": {"kind": {"type": "string", "enum": ["image", "video"]}}}),
    ToolSpec("propose_brief", "保存需求提案（不写入项目事实，等用户确认）",
             {"type": "object", "properties": {"brief": {"type": "object"}},
              "required": ["brief"]}),
    ToolSpec("propose_script", "保存脚本草案（自动落为未确认脚本版本，返回真实 ID/版本）",
             {"type": "object", "properties": {
                 "summary": {"type": "string"},
                 "shots": {"type": "array", "items": {"type": "object"}}},
              "required": ["summary"]}),
    ToolSpec("propose_shot_plan", "保存镜头计划草案（绑定当前脚本版本，需用户确认后应用）",
             {"type": "object", "properties": {
                 "shots": {"type": "array", "items": {"type": "object"}}},
              "required": ["shots"]}),
]

PROPOSAL_KINDS = ("brief", "script", "shot_plan")
BRIEF_FIELDS = ("subject", "quantity", "selling_points", "forbidden_elements",
                "target_platform", "target_duration_s", "style", "notes")

SYSTEM_PROMPT = """你是电商短视频的「AI 导演」，在项目范围内与用户对话。

# 目标
帮用户逐步形成：需求 → 脚本 → 镜头计划 → 分镜 → 视频。你只做三件事：
澄清需求、提出方案、把方案保存成**草案**。你**不能**替用户确认，也**不能**生成图片或视频。

# 硬规则
1. 只依据项目真实状态与用户提供的信息；不确定就问，不要编造商品事实（成分/功效/认证/价格）。
2. 需要落库时调用工具：需求用 propose_brief、脚本用 propose_script、镜头计划用 propose_shot_plan。
   工具返回真实 ID/版本才算保存成功；**不要声称"已保存"却没有调用工具**。
3. 保存的只是**草案**。必须明确告诉用户"请确认"，确认/生成由用户在界面上操作。
4. 不要输出图片或视频任务，不要承诺效果，不要给出与实际不同的价格。
5. 简短中文；一次只推进一件事。用户已确认过的信息不要重复询问。

# 输出
先判断当前阶段（需求/脚本/镜头计划/分镜/视频），围绕当前阶段给出下一步；需要工具就直接调用。
"""


# ---------------- 工具执行（服务端绑定项目） ----------------
def _tool_state(project_id: str) -> dict:
    r = director.readiness(project_id)
    script = db.query("SELECT * FROM script_versions WHERE project_id=? ORDER BY version DESC LIMIT 1",
                      (project_id,))
    shots = db.query("SELECT * FROM shots WHERE project_id=? ORDER BY seq", (project_id,))
    return {
        "project_id": project_id,
        "name": r["project"]["name"],
        "brief": r["facts"]["brief"],
        "current_stage": r["current"]["stage"],
        "script": ({"script_id": script[0]["id"], "version": script[0]["version"],
                    "confirmed": bool(script[0]["confirmed_at"])} if script else None),
        "shots": [{"shot_id": s["id"], "seq": s["seq"], "duration_s": s["duration_s"],
                   "description": s["description"]} for s in shots],
        "allowed_actions": r["allowed_actions"],
    }


def _save_proposal(project_id: str, kind: str, content: dict, *,
                   target_id: str | None = None, target_version: int | None = None,
                   session_id: str | None = None, request_id: str | None = None) -> dict:
    rows = db.query("SELECT COALESCE(MAX(version),0) v FROM director_proposals "
                    "WHERE project_id=? AND kind=?", (project_id, kind))
    ver = int(rows[0]["v"]) + 1
    pid = db.new_id("prop")
    db.insert("director_proposals", {
        "id": pid, "project_id": project_id, "kind": kind, "version": ver,
        "content_json": db.dumps(content), "target_id": target_id,
        "target_version": target_version, "status": "draft",
        "source_session_id": session_id, "source_request_id": request_id,
        "created_at": db.now(), "applied_at": None})
    return {"proposal_id": pid, "kind": kind, "version": ver,
            "target_id": target_id, "target_version": target_version}


def _clean_brief(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise err("VALIDATION_FAILED", "brief 必须是对象")
    out = {}
    for k in BRIEF_FIELDS:
        v = raw.get(k)
        if v in (None, "", []):
            continue
        if k == "quantity":
            if isinstance(v, bool) or not isinstance(v, int) or v <= 0:
                raise err("VALIDATION_FAILED", "quantity 必须为正整数",
                          detail={"field": "quantity", "got": v})
        out[k] = v
    if not out:
        raise err("VALIDATION_FAILED", "brief 提案为空或字段均非法",
                  detail={"allowed": list(BRIEF_FIELDS)})
    return out


def _clean_script(args: dict) -> dict:
    summary = str(args.get("summary") or "").strip()
    if not summary:
        raise err("VALIDATION_FAILED", "脚本草案缺少 summary")
    shots = args.get("shots") or []
    if not isinstance(shots, list):
        raise err("VALIDATION_FAILED", "shots 必须是数组")
    clean = []
    for i, s in enumerate(shots, start=1):
        if not isinstance(s, dict):
            raise err("VALIDATION_FAILED", f"第 {i} 个镜头不是对象")
        hint = str(s.get("hint") or "").strip()
        if not hint:
            raise err("VALIDATION_FAILED", f"第 {i} 个镜头缺少 hint")
        clean.append({"seq": int(s.get("seq") or i), "hint": hint,
                      **({k: s[k] for k in ("duration_s", "motion") if k in s})})
    return {"summary": summary, "shots": clean}


def _clean_shot_plan(args: dict, project_id: str) -> tuple[dict, str, int]:
    shots = args.get("shots") or []
    if not isinstance(shots, list) or not shots:
        raise err("VALIDATION_FAILED", "镜头计划不能为空")
    latest = db.query("SELECT * FROM script_versions WHERE project_id=? ORDER BY version DESC LIMIT 1",
                      (project_id,))
    if not latest:
        raise err("PROPOSAL_NOT_APPLICABLE", "项目还没有脚本，无法生成镜头计划")
    model = _default_video_model()
    clean = []
    for i, s in enumerate(shots, start=1):
        if not isinstance(s, dict):
            raise err("VALIDATION_FAILED", f"第 {i} 个镜头不是对象")
        desc = str(s.get("description") or s.get("hint") or "").strip()
        if not desc:
            raise err("VALIDATION_FAILED", f"第 {i} 个镜头缺少画面描述")
        dur = config.resolve_duration_s(model, s.get("duration_s", 5), strict=True)
        clean.append({"seq": int(s.get("seq") or i), "description": desc,
                      "duration_s": dur, "motion": s.get("motion")})
    return ({"shots": clean, "script_id": latest[0]["id"],
             "script_version": latest[0]["version"]},
            latest[0]["id"], latest[0]["version"])


def _default_video_model() -> str:
    for mid, meta in config.MODEL_REGISTRY.items():
        if meta.get("kind") == "video" and meta.get("provider") == "ark" \
                and config.model_submittability(mid)["submittable"]:
            return mid
    raise err("MODEL_NOT_OPENED", "没有可用视频模型")


def execute_tool(name: str, args: dict, ctx: dict) -> dict:
    project_id = ctx["project_id"]
    if name == "get_project_state":
        return _tool_state(project_id)
    if name == "list_models":
        kind = args.get("kind")
        out = []
        for mid, meta in config.MODEL_REGISTRY.items():
            if meta.get("kind") != "video" and meta.get("kind") != "image":
                continue
            if kind and meta.get("kind") != kind:
                continue
            sub = config.model_submittability(mid)
            out.append({"id": mid, "kind": meta.get("kind"),
                        "submittable": sub["submittable"], "reasons": sub["reasons"],
                        "input_modes": (config.MODEL_CAPABILITIES.get(mid) or {}).get("input_modes", []),
                        "duration_policy": (config.duration_policy(mid)
                                            if meta.get("kind") == "video" else None)})
        return {"items": out}
    if name == "propose_brief":
        brief = _clean_brief(args.get("brief") or {})
        p = _save_proposal(project_id, "brief", brief, session_id=ctx.get("session_id"),
                           request_id=ctx.get("request_id"))
        return {**p, "saved": True, "confirmed": False,
                "note": "需求提案已保存为草案，需用户确认后写入项目事实"}
    if name == "propose_script":
        content = _clean_script(args)
        script = projects.add_script(project_id, content, source="assistant")
        p = _save_proposal(project_id, "script", content, target_id=script["script_id"],
                           target_version=script["version"],
                           session_id=ctx.get("session_id"), request_id=ctx.get("request_id"))
        return {**p, "target_id": script["script_id"],
                "target_version": script["version"], "saved": True, "confirmed": False,
                "note": "脚本草案已保存为未确认版本"}
    if name == "propose_shot_plan":
        content, sid, sver = _clean_shot_plan(args, project_id)
        p = _save_proposal(project_id, "shot_plan", content, target_id=sid,
                           target_version=sver, session_id=ctx.get("session_id"),
                           request_id=ctx.get("request_id"))
        return {**p, "saved": True, "confirmed": False,
                "note": "镜头计划草案已保存，需用户确认后应用"}
    if name in ("confirm_script", "submit", "generate", "apply_proposal"):
        raise err("TOOL_FORBIDDEN", f"导演不允许调用 {name}", detail={"tool": name})
    raise err("TOOL_NOT_FOUND", f"未知工具：{name}",
              detail={"allowed": [t.name for t in PROJECT_TOOLS]})


# ---------------- 预算闸门（每次 LLM 请求前） ----------------
def _estimate_call_cost(messages: list[dict], max_out_tokens: int = 400) -> float:
    chars = sum(len(str(m.get("content") or "")) for m in messages)
    in_tokens = int(chars / 1.6) + 200
    usd = in_tokens / 1e6 * 0.30 + max_out_tokens / 1e6 * 1.20
    return round(usd * 7.2, 6)


def check_budget(session_id: str, estimated_cny: float) -> dict:
    """返回预算状态；超限直接抛 429（可解释）。"""
    if config.DIRECTOR_MAX_COST_PER_CALL_CNY > 0 \
            and estimated_cny > config.DIRECTOR_MAX_COST_PER_CALL_CNY:
        raise err("ASSISTANT_BUDGET_EXCEEDED",
                  f"单次对话预估 ¥{estimated_cny:.4f} 超上限 ¥{config.DIRECTOR_MAX_COST_PER_CALL_CNY}",
                  detail={"scope": "per_call", "estimated_cny": estimated_cny,
                          "limit_cny": config.DIRECTOR_MAX_COST_PER_CALL_CNY})
    spent = float(db.query(
        "SELECT COALESCE(SUM(cost_cny),0) c FROM assistant_usage WHERE session_id=?",
        (session_id,))[0]["c"] or 0)
    if config.DIRECTOR_MAX_SESSION_COST_CNY > 0 \
            and spent + estimated_cny > config.DIRECTOR_MAX_SESSION_COST_CNY:
        raise err("ASSISTANT_BUDGET_EXCEEDED",
                  f"本会话对话预算不足：已用 ¥{spent:.4f} + 本次 ¥{estimated_cny:.4f} "
                  f"> 上限 ¥{config.DIRECTOR_MAX_SESSION_COST_CNY}",
                  detail={"scope": "session", "spent_cny": spent,
                          "estimated_cny": estimated_cny,
                          "limit_cny": config.DIRECTOR_MAX_SESSION_COST_CNY})
    return {"estimated_cny": estimated_cny, "session_spent_cny": spent,
            "ok": True}


# ---------------- 主流程 ----------------
def _payload_hash(body: dict) -> str:
    payload = {"message": (body.get("message") or "").strip(),
               "selected_asset_ids": sorted(body.get("selected_asset_ids") or []),
               "expected_workflow_revision": body.get("expected_workflow_revision"),
               "intent": body.get("intent") or "auto"}
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()


def _session_for(project_id: str, session_id: str | None) -> dict:
    """会话必须属于当前项目；跨项目复用 → 409（禁止 A 的方案流入 B）。"""
    from .assistant import service as asst
    if session_id:
        sess = asst.get_session(session_id)
        if sess.get("project_id") and sess["project_id"] != project_id:
            raise err("PROJECT_SESSION_MISMATCH",
                      "该会话属于其它项目；请为新项目创建新会话",
                      detail={"session_project_id": sess["project_id"]})
        if not sess.get("project_id"):
            db.update("assistant_sessions", session_id, {"project_id": project_id})
        return asst.get_session(session_id)
    return asst.ensure_session(None, project_id, "generate")


def _receipt(project_id: str, client_request_id: str) -> dict | None:
    rows = db.query("SELECT * FROM director_requests WHERE project_id=? AND client_request_id=?",
                    (project_id, client_request_id))
    return rows[0] if rows else None


def chat(project_id: str, body: dict, provider=None) -> dict:  # noqa: ANN001
    from .assistant import service as asst

    projects.get_project(project_id)
    message = (body.get("message") or "").strip()
    if not message:
        raise err("VALIDATION_FAILED", "message 不能为空")
    crid = (body.get("client_request_id") or "").strip()
    if not crid:
        raise err("VALIDATION_FAILED", "client_request_id 必填（用于幂等与断线恢复）")

    ph = _payload_hash(body)
    prior = _receipt(project_id, crid)
    if prior:
        if prior["payload_hash"] != ph:
            raise err("IDEMPOTENCY_CONFLICT",
                      "同一 client_request_id 提交了不同内容",
                      detail={"client_request_id": crid})
        if prior["status"] == "pending":
            raise err("DIRECTOR_REQUEST_PENDING", "该请求仍在处理中，请稍后查询回执",
                      detail={"client_request_id": crid})
        if prior["response_json"]:
            return {**json.loads(prior["response_json"]), "idempotent_replay": True}
        raise err("DIRECTOR_REQUEST_PENDING", "请求未完成，请查询回执",
                  detail={"client_request_id": crid, "status": prior["status"]})

    sess = _session_for(project_id, body.get("session_id"))
    rid = db.new_id("dreq")
    db.insert("director_requests", {"id": rid, "project_id": project_id,
                                    "client_request_id": crid, "payload_hash": ph,
                                    "status": "pending", "turn_id": None,
                                    "created_at": db.now(), "updated_at": db.now()})

    p = provider or prov.get_provider()
    if not p.available():
        db.update("director_requests", rid, {"status": "failed",
                                            "error_code": "ASSISTANT_UNAVAILABLE",
                                            "updated_at": db.now()})
        raise err("ASSISTANT_UNAVAILABLE", "导演暂时不可用（未配置语言模型）",
                  detail={"client_request_id": crid})

    state = _tool_state(project_id)
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "context", "content": {"project": state,
                                        "selected_asset_ids": body.get("selected_asset_ids") or []}},
        {"role": "user", "content": message},
    ]

    est = _estimate_call_cost(messages)
    check_budget(sess["id"], est)              # **LLM 调用前**预算检查

    ctx = {"project_id": project_id, "session_id": sess["id"], "request_id": crid}
    artifacts: list[dict] = []
    tool_results: list[dict] = []
    reply = ""
    res = None
    try:
        for _round in range(2):
            res = p.chat(messages, PROJECT_TOOLS, tool_results or None)
            reply = res.reply or reply
            calls = res.tool_calls or []
            if not calls:
                break
            for tc in calls[:config.DIRECTOR_MAX_TOOL_CALLS]:
                try:
                    out = execute_tool(tc.name, tc.arguments, ctx)
                    entry = {"tool": tc.name, "ok": True, "result": out}
                    if out.get("proposal_id"):
                        artifacts.append({
                            "type": out["kind"], "proposal_id": out["proposal_id"],
                            "proposal_version": out["version"],
                            "target_id": out.get("target_id"),
                            "target_version": out.get("target_version"),
                            "persistence": "saved", "confirmed": False})
                except Exception as e:  # noqa: BLE001
                    entry = {"tool": tc.name, "ok": False,
                             "error": {"code": getattr(e, "code", "TOOL_ERROR"),
                                       "message": str(getattr(e, "message", e))[:200]}}
                tool_results.append(entry)
            messages = [*messages, {"role": "assistant", "content": reply or None,
                                    "tool_calls": [{"id": tc.id or f"call_{i}",
                                                    "type": "function",
                                                    "function": {"name": tc.name,
                                                                 "arguments": json.dumps(tc.arguments, ensure_ascii=False)}}
                                                   for i, tc in enumerate(calls[:config.DIRECTOR_MAX_TOOL_CALLS])]},
                        *[{"role": "tool", "tool_call_id": tc.id or f"call_{i}",
                           "name": e["tool"],
                           "content": json.dumps(e.get("result") if e["ok"] else e["error"],
                                                 ensure_ascii=False)}
                          for i, (tc, e) in enumerate(zip(calls[:config.DIRECTOR_MAX_TOOL_CALLS],
                                                          tool_results[-len(calls[:config.DIRECTOR_MAX_TOOL_CALLS]):]))]]
    except Exception as e:  # noqa: BLE001
        db.update("director_requests", rid, {"status": "failed",
                                            "error_code": getattr(e, "code", "DIRECTOR_ERROR"),
                                            "error_message": str(getattr(e, "message", e))[:200],
                                            "updated_at": db.now()})
        raise

    usage = {"input_tokens": int(getattr(res, "input_tokens", 0) or 0),
             "output_tokens": int(getattr(res, "output_tokens", 0) or 0)}
    cost = round(usage["input_tokens"] / 1e6 * 0.30 * 7.2
                 + usage["output_tokens"] / 1e6 * 1.20 * 7.2, 6)
    asst.record_usage(sess["id"], "llm", provider=p.name, model=p.model,
                      input_tokens=usage["input_tokens"],
                      output_tokens=usage["output_tokens"],
                      tool_calls=len(tool_results), cost_cny=cost,
                      note=f"director:{project_id}")

    ready = director.readiness(project_id)
    actions = _actions_for(project_id, ready)
    reply_type = "proposal_ready" if artifacts else (
        "clarify" if "？" in reply or "?" in reply else "message")

    turn_id = db.new_id("dturn")
    out = {
        "session_id": sess["id"], "request_id": crid, "request_status": "completed",
        "reply": reply, "reply_type": reply_type,
        "artifacts": artifacts, "actions": actions,
        "current": ready["current"], "allowed_actions": ready["allowed_actions"],
        "workflow_revision": _workflow_revision(project_id),
        "usage": {**usage, "cost_cny": cost, "estimated_cny": est,
                  "note": "费用为应用侧估算，非供应商账单"},
        "tool_calls": tool_results,
        "budget": {"per_call_limit_cny": config.DIRECTOR_MAX_COST_PER_CALL_CNY,
                   "session_limit_cny": config.DIRECTOR_MAX_SESSION_COST_CNY},
        "note": "草案已自动保存，但未确认；生成图片/视频需用户显式操作",
    }
    db.insert("director_turns", {"id": turn_id, "project_id": project_id,
                                 "session_id": sess["id"], "request_id": crid,
                                 "user_text": message, "reply_text": reply,
                                 "reply_type": reply_type,
                                 "structured_json": db.dumps(
                                     {"artifacts": artifacts, "actions": actions}),
                                 "created_at": db.now()})
    db.update("director_requests", rid, {"status": "completed", "turn_id": turn_id,
                                        "response_json": db.dumps(out),
                                        "updated_at": db.now()})
    return out


def _workflow_revision(project_id: str) -> int:
    """项目工作流修订号：脚本/镜头/提案变化即递增（用于并发校验）。"""
    n_scripts = db.query("SELECT COUNT(*) c FROM script_versions WHERE project_id=?",
                         (project_id,))[0]["c"]
    n_shots = db.query("SELECT COUNT(*) c FROM shots WHERE project_id=?", (project_id,))[0]["c"]
    n_props = db.query("SELECT COUNT(*) c FROM director_proposals WHERE project_id=?",
                       (project_id,))[0]["c"]
    n_turns = db.query("SELECT COUNT(*) c FROM director_turns WHERE project_id=?",
                       (project_id,))[0]["c"]
    return int(n_scripts + n_shots + n_props + n_turns)


def _actions_for(project_id: str, ready: dict) -> list[dict]:
    """根据**真实 DB 状态**生成下一步动作（不信任模型文本）。"""
    actions: list[dict] = []
    latest = db.query("SELECT * FROM script_versions WHERE project_id=? ORDER BY version DESC LIMIT 1",
                      (project_id,))
    if latest:
        s = latest[0]
        if not s["confirmed_at"]:
            actions.append({"type": "confirm_script", "target_id": s["id"],
                            "target_version": s["version"], "requires_user_action": True})
        else:
            plan = db.query("SELECT * FROM director_proposals WHERE project_id=? AND kind='shot_plan' "
                            "AND status='draft' ORDER BY version DESC LIMIT 1", (project_id,))
            if plan:
                actions.append({"type": "apply_shot_plan", "proposal_id": plan[0]["id"],
                                "proposal_version": plan[0]["version"],
                                "requires_user_action": True})
    brief_prop = db.query("SELECT * FROM director_proposals WHERE project_id=? AND kind='brief' "
                          "AND status='draft' ORDER BY version DESC LIMIT 1", (project_id,))
    if brief_prop:
        actions.append({"type": "apply_brief", "proposal_id": brief_prop[0]["id"],
                        "proposal_version": brief_prop[0]["version"],
                        "requires_user_action": True})
    if "quote_generation" in ready["allowed_actions"]:
        actions.append({"type": "quote_generation", "requires_user_action": True})
    return actions


# ---------------- 读取 / 应用 ----------------
def messages(project_id: str, cursor: str | None = None, limit: int = 30) -> dict:
    """分页读取完整对话（不依赖 LLM Memory 窗口）。"""
    projects.get_project(project_id)
    args: list = [project_id]
    sql = "SELECT * FROM director_turns WHERE project_id=?"
    if cursor:
        row = db.get("director_turns", cursor)
        if not row or row["project_id"] != project_id:
            raise err("VALIDATION_FAILED", "cursor 非法")
        sql += " AND created_at < ?"
        args.append(row["created_at"])
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(max(1, min(limit, 100)))
    rows = db.query(sql, args)
    items = [{"turn_id": r["id"], "request_id": r["request_id"],
              "user": r["user_text"], "reply": r["reply_text"],
              "reply_type": r["reply_type"],
              "structured": db.loads(r["structured_json"]) or {},
              "created_at": r["created_at"]} for r in rows]
    return {"items": items, "next_cursor": rows[-1]["id"] if rows else None,
            "has_more": len(rows) == max(1, min(limit, 100))}


def request_receipt(project_id: str, client_request_id: str) -> dict:
    row = _receipt(project_id, client_request_id)
    if not row:
        raise err("DIRECTOR_REQUEST_NOT_FOUND", "没有该请求的回执",
                  detail={"client_request_id": client_request_id})
    return {"client_request_id": row["client_request_id"], "status": row["status"],
            "turn_id": row["turn_id"],
            "error_code": row["error_code"],
            "response": db.loads(row["response_json"]) if row["response_json"] else None}


def proposals(project_id: str, kind: str | None = None) -> dict:
    projects.get_project(project_id)
    sql = "SELECT * FROM director_proposals WHERE project_id=?"
    args: list = [project_id]
    if kind:
        sql += " AND kind=?"
        args.append(kind)
    sql += " ORDER BY created_at DESC LIMIT 100"
    rows = db.query(sql, args)
    return {"items": [{"proposal_id": r["id"], "kind": r["kind"], "version": r["version"],
                       "status": r["status"], "target_id": r["target_id"],
                       "target_version": r["target_version"],
                       "content": db.loads(r["content_json"]) or {},
                       "created_at": r["created_at"],
                       "applied_at": r["applied_at"]} for r in rows]}


def apply_proposal(project_id: str, proposal_id: str, expected_version: int | None,
                   client_request_id: str | None) -> dict:
    """用户应用提案。**不创建任何生成任务**。"""
    row = db.get("director_proposals", proposal_id)
    if not row or row["project_id"] != project_id:
        raise err("PROPOSAL_NOT_FOUND", f"提案不存在：{proposal_id}")
    if expected_version is not None and int(expected_version) != int(row["version"]):
        raise err("PROPOSAL_VERSION_CONFLICT",
                  f"提案已更新：当前 v{row['version']}，提交 v{expected_version}",
                  detail={"current_version": row["version"]})
    content = db.loads(row["content_json"]) or {}
    if row["status"] != "draft":
        return {"proposal_id": proposal_id, "status": row["status"],
                "applied": False, "note": "提案已应用或已被取代"}

    applied: dict = {"proposal_id": proposal_id, "kind": row["kind"], "applied": True}
    if row["kind"] == "brief":
        out = projects.update_project(project_id, brief=content)
        applied.update({"brief": out["brief"], "stale": out["stale"]})
    elif row["kind"] == "script":
        # 脚本在 propose_script 时已落库；这里只做确认入口提示，不复制版本
        s = db.get("script_versions", row["target_id"]) if row["target_id"] else None
        if not s:
            raise err("PROPOSAL_NOT_APPLICABLE", "脚本草案已不存在")
        applied.update({"script_id": s["id"], "version": s["version"],
                        "confirmed": bool(s["confirmed_at"]),
                        "note": "脚本已保存；请调用 /api/scripts/{id}/confirm 确认"})
    elif row["kind"] == "shot_plan":
        latest = db.query("SELECT * FROM script_versions WHERE project_id=? "
                          "ORDER BY version DESC LIMIT 1", (project_id,))
        if not latest or latest[0]["id"] != row["target_id"] \
                or int(latest[0]["version"]) != int(row["target_version"] or 0):
            raise err("PROPOSAL_NOT_APPLICABLE",
                      "脚本已更新，该镜头计划基于旧版本，请重新生成",
                      detail={"proposal_script_version": row["target_version"],
                              "current_script_version": latest[0]["version"] if latest else None})
        if not latest[0]["confirmed_at"]:
            raise err("SCRIPT_NOT_CONFIRMED", "请先确认脚本，再应用镜头计划")
        existing = db.query("SELECT COUNT(*) c FROM shots WHERE project_id=?", (project_id,))[0]["c"]
        if existing:
            raise err("PROPOSAL_NOT_APPLICABLE",
                      "项目已有镜头；调整镜头请通过镜头接口操作，避免静默替换")
        out = shots.add_shots(project_id, latest[0]["id"], content["shots"])
        applied.update({"shot_ids": out["shot_ids"], "count": out["count"]})
    else:
        raise err("PROPOSAL_NOT_APPLICABLE", f"未知提案类型：{row['kind']}")

    db.update("director_proposals", proposal_id,
              {"status": "applied", "applied_at": db.now()})
    applied["workflow_revision"] = _workflow_revision(project_id)
    applied["allowed_actions"] = director.readiness(project_id)["allowed_actions"]
    return applied
