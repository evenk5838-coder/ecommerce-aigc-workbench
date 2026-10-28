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
import math
from collections import Counter

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from . import config, db, director, projects, shots
from .assistant import provider as prov
from .assistant.provider import ToolSpec
from .errors import err


class ScriptShot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    seq: int | None = Field(default=None, ge=1)
    hint: str = Field(min_length=1)
    duration_s: int | None = Field(default=None, ge=1)
    motion: str | None = None

    @field_validator("hint")
    @classmethod
    def nonempty_hint(cls, value):
        if not value.strip():
            raise ValueError("镜头描述不能为空")
        return value.strip()


class ScriptContent(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    summary: str = Field(min_length=1)
    shots: list[ScriptShot] = Field(default_factory=list)

    @field_validator("summary")
    @classmethod
    def nonempty_summary(cls, value):
        if not value.strip():
            raise ValueError("脚本正文不能为空")
        return value.strip()


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
             ScriptContent.model_json_schema()),
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
6. 用户明确要求起稿时，应调用 propose_script 保存实际正文；只说“稍等”不算执行。
   脚本示例：{"summary":"先展示瓶身，再特写瓶盖。","shots":[{"seq":1,"hint":"瓶身全景"}]}。
   每个 shots 项必须有 hint，不要用 description 代替；工具报错后根据具体字段修正。

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
                    "content": db.loads(script[0]["content_json"]) or {},
                    "confirmed": bool(r["current"]["confirmation"].get("valid"))} if script else None),
        "shots": [{"shot_id": s["id"], "seq": s["seq"], "duration_s": s["duration_s"],
                   "description": s["description"]} for s in shots],
        "allowed_actions": r["allowed_actions"],
        "draft_proposals": [dict(kind=x["kind"], version=x["version"], proposal_id=x["id"],
                                 content=db.loads(x["content_json"]) or {})
            for x in db.query("SELECT * FROM director_proposals WHERE project_id=? AND status='draft' "
                              "ORDER BY created_at DESC LIMIT 3", (project_id,))],
    }


def _save_proposal_in_tx(c, project_id: str, kind: str, content: dict, *,
                   target_id: str | None = None, target_version: int | None = None,
                   session_id: str | None = None, request_id: str | None = None) -> dict:
    pid = db.new_id("prop")
    ver = int(c.execute("SELECT COALESCE(MAX(version),0) v FROM director_proposals "
                        "WHERE project_id=? AND kind=?", (project_id, kind)).fetchone()["v"]) + 1
    c.execute("INSERT INTO director_proposals(id,project_id,kind,version,content_json,target_id,"
              "target_version,status,source_session_id,source_request_id,created_at,applied_at) "
              "VALUES(?,?,?,?,?,?,?,'draft',?,?,?,NULL)",
              (pid,project_id,kind,ver,db.dumps(content),target_id,target_version,
               session_id,request_id,db.now()))
    return {"proposal_id": pid, "kind": kind, "version": ver,
            "target_id": target_id, "target_version": target_version}


def _save_proposal(project_id: str, kind: str, content: dict, **kwargs) -> dict:
    with db.transaction() as c:
        return _save_proposal_in_tx(c, project_id, kind, content, **kwargs)


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
        if k in ("subject", "target_platform", "style", "notes") and not isinstance(v, str):
            raise err("VALIDATION_FAILED", f"{k} 必须是字符串")
        if k == "target_duration_s" and (isinstance(v, bool) or not isinstance(v, int) or v <= 0):
            raise err("VALIDATION_FAILED", "target_duration_s 必须是正整数")
        if k in ("selling_points", "forbidden_elements") and not (
                isinstance(v, str) or isinstance(v, list) and all(isinstance(x, str) for x in v)):
            raise err("VALIDATION_FAILED", f"{k} 必须是字符串或字符串数组")
        out[k] = v
    if not out:
        raise err("VALIDATION_FAILED", "brief 提案为空或字段均非法",
                  detail={"allowed": list(BRIEF_FIELDS)})
    return out


def _clean_script(args: dict) -> dict:
    try:
        validated = ScriptContent.model_validate(args)
    except ValidationError as exc:
        fields = [".".join(map(str, e["loc"])) or "script" for e in exc.errors()]
        raise err("VALIDATION_FAILED", "脚本字段不完整或格式有误：" + ", ".join(fields),
                  detail={"fields": fields}) from None
    content = validated.model_dump(exclude_none=True)
    for index, shot in enumerate(content["shots"], 1):
        shot.setdefault("seq", index)
    return content


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
        with db.transaction() as c:
            script = projects._add_script_in_tx(c, project_id, content, source="assistant",
                                                session_id=ctx.get("session_id"))
            p = _save_proposal_in_tx(c, project_id, "script", content, target_id=script["script_id"],
                                    target_version=script["version"],
                                    session_id=ctx.get("session_id"), request_id=ctx.get("request_id"))
        return {**p, "target_id": script["script_id"],
                "target_version": script["version"], "saved": True, "confirmed": False,
                "node_id": "script",
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
# Official CNY prices checked 2026-10-28, per million tokens, at peak time:
# https://api-docs.deepseek.com/zh-cn/quick_start/pricing/
# Always use peak prices for budgeting; off-peak discounts are not guaranteed.
_DEEPSEEK_PRICES = {
    "deepseek-flash": (2.0, 0.04, 8.0),
    "deepseek-v4-flash": (2.0, 0.04, 8.0),
    "deepseek-v4-flash-vision-exp": (2.0, 0.04, 8.0),
    "deepseek-v4-pro": (9.0, 0.30, 27.0),
}


def _estimate_call_cost(messages: list[dict], max_out_tokens: int = 400, *,
                        tools: list[ToolSpec] | None = None,
                        provider_name: str = "deepseek", model: str = "deepseek-flash") -> float:
    """Estimate the wire payload, including schemas and previous tool arguments.

    DeepSeek documents ~0.3 tokens/English char and ~0.6/Chinese char:
    https://api-docs.deepseek.com/quick_start/token_usage/
    Add 25% and 200 tokens for protocol/tokenization uncertainty. This is an
    approximation, NOT a token upper bound or the supplier's bill. Other Unicode
    uses its UTF-8 byte count conservatively; no cache discount is assumed here.
    """
    payload = {"messages": prov._to_openai_messages(messages), "tools": [
        {"type": "function", "function": t.to_dict()}
        for t in (PROJECT_TOOLS if tools is None else tools)]}
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if provider_name in ("deepseek", "fixed"):
        units = sum(3 if char.isascii() else 6 if (
            "\u3400" <= char <= "\u9fff" or "\U00020000" <= char <= "\U000323af")
            else 10 * len(char.encode("utf-8")) for char in text)
        in_tokens = math.ceil(units / 10 * 1.25) + 200
    else:
        # Keep the previous conservative approximation for unverified providers.
        in_tokens = len(text) + 200
    return _token_cost(in_tokens, max_out_tokens, provider_name=provider_name, model=model)


def _token_cost(input_tokens: int, output_tokens: int, *, provider_name: str = "deepseek",
                model: str = "deepseek-flash", cache_hit_tokens: int = 0) -> float:
    """Application estimate from tokens; peak CNY prices, never an actual bill."""
    if provider_name in ("deepseek", "fixed"):
        pricing_model = "deepseek-flash" if provider_name == "fixed" else model
        if pricing_model not in _DEEPSEEK_PRICES:
            raise err("ASSISTANT_PRICING_UNAVAILABLE", "当前对话模型尚未配置费用估算", detail={"model": model})
        miss_rate, hit_rate, output_rate = _DEEPSEEK_PRICES[pricing_model]
        hit = cache_hit_tokens if type(cache_hit_tokens) is int and 0 <= cache_hit_tokens <= input_tokens else 0
        return round(((input_tokens - hit) * miss_rate + hit * hit_rate + output_tokens * output_rate) / 1e6, 6)
    # Existing non-DeepSeek fallback remains an application estimate only.
    return round((input_tokens * 0.30 + output_tokens * 1.20) / 1e6 * 7.2, 6)


def _result_token_cost(result: prov.ChatResult, *, provider_name: str, model: str) -> float:
    """Use reported usage once; inconsistent cache counters never earn a discount."""
    usage = result.meta.get("usage") or {}
    hit, miss = usage.get("prompt_cache_hit_tokens"), usage.get("prompt_cache_miss_tokens")
    valid_hit = type(hit) is int and 0 <= hit <= result.input_tokens
    if "prompt_cache_miss_tokens" in usage:
        valid_hit = valid_hit and type(miss) is int and miss >= 0 and hit + miss == result.input_tokens
    return _token_cost(result.input_tokens, result.output_tokens, provider_name=provider_name,
                       model=model, cache_hit_tokens=hit if valid_hit else 0)


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


def _history(project_id: str, *, compact_artifacts: bool = False, current_nodes: list[dict] | None = None) -> list[dict]:
    """Bounded project memory; the complete transcript remains available through messages()."""
    rows = db.query("SELECT user_text,reply_text,structured_json FROM director_turns "
                    "WHERE project_id=? ORDER BY created_at DESC,id DESC LIMIT 8", (project_id,))
    result = []
    remaining = 12000
    for row in rows:
        user, reply = row["user_text"], row["reply_text"]
        structured = db.loads(row["structured_json"]) or {}
        artifacts = structured.get("artifacts") or []
        if compact_artifacts:
            from .director_context import history_artifact
            artifacts = [history_artifact(artifact, current_nodes or []) for artifact in artifacts]
        assistant = reply + ("\n已保存草案回执：" + db.dumps(artifacts) if artifacts else "")
        if (structured.get("outcome") or {}).get("status") in ("failed", "partial"):
            assistant += "\n实际执行结果：" + db.dumps(structured["outcome"])
        if structured.get("tool_failures"):
            assistant += "\n工具失败：" + db.dumps(structured["tool_failures"])
        if len(user) + len(assistant) > remaining:
            break
        remaining -= len(user) + len(assistant)
        result[0:0] = [{"role": "user", "content": user}, {"role": "assistant", "content": assistant}]
    return result


@projects.project_guard()
def chat(project_id: str, body: dict, provider=None) -> dict:  # noqa: ANN001
    from .assistant import service as asst
    projects.get_project(project_id)
    message = (body.get("message") or "").strip()
    crid = (body.get("client_request_id") or "").strip()
    if not message or not crid:
        raise err("VALIDATION_FAILED", "message 和 client_request_id 必填")
    # Legacy workbench intent values remain accepted, without granting media authority.
    intent = body.get("intent") or "auto"
    if intent not in ("auto", "script", "generate", "optimize_only"):
        raise err("VALIDATION_FAILED", "intent 必须为 auto 或 script")
    goal = "script" if intent == "script" else "auto"
    ph = _payload_hash(body)
    # Replay first: a completed request remains valid even when newer work changed the baseline.
    prior = _receipt(project_id, crid)
    if prior:
        if prior["payload_hash"] != ph:
            raise err("IDEMPOTENCY_CONFLICT", "同一 client_request_id 提交了不同内容")
        if prior["response_json"] and prior["status"] == "completed":
            return {**json.loads(prior["response_json"]), "idempotent_replay": True}
        code = "DIRECTOR_REQUEST_PENDING" if prior["status"] == "pending" else "DIRECTOR_REQUEST_FAILED"
        raise err(code, "请求仍在处理" if prior["status"] == "pending" else "上次请求未完成，请查看已保存草案后使用新的请求编号",
                  detail={"client_request_id": crid, "status": prior["status"], "error_code": prior["error_code"]})
    expected = body.get("expected_workflow_revision")
    current = _workflow_revision(project_id)
    if expected is not None and (isinstance(expected, bool) or not isinstance(expected, int) or expected != current):
        raise err("WORKFLOW_REVISION_CONFLICT", "项目内容已变化，请刷新后继续",
                  detail={"current_workflow_revision": current})
    sess = _session_for(project_id, body.get("session_id"))
    rid = db.new_id("dreq")
    db.insert("director_requests", {"id": rid, "project_id": project_id,
              "client_request_id": crid, "payload_hash": ph, "status": "pending",
              "created_at": db.now(), "updated_at": db.now()})
    artifacts, tool_results = [], []
    usage = {"input_tokens": 0, "output_tokens": 0, "cost_cny": 0.0}
    estimates = 0.0
    reply = ""
    pending_errors: dict[str, dict] = {}
    failures: list[dict] = []
    round_audit: list[dict] = []

    def finish(receipt_status="completed"):
        nonlocal reply
        ready = director.readiness(project_id)
        actions = _actions_for(project_id, ready)
        if pending_errors:
            status = "partial" if artifacts else "failed"
            error = next(iter(pending_errors.values()))
            reply = ("已保存部分草案，其余操作未完成。" if artifacts else "本次未完成保存。") + error["message"] + "。可以修正后继续。"
        elif artifacts:
            status = "saved"
            reply = "脚本草案已保存并放入画布，请查看并确认。" if any(a["type"] == "script" for a in artifacts) else "草案已保存，请查看并确认。"
        else:
            status = "clarify" if "？" in reply or "?" in reply else "message"
        outcome = {"status": status, "goal": goal,
                   "node_ids": list(dict.fromkeys(a["node_id"] for a in artifacts if a.get("node_id")))}
        if pending_errors:
            outcome["error"] = error
        reply_type = "proposal_ready" if status == "saved" else status
        turn_id = db.new_id("dturn")
        response = {"session_id": sess["id"], "request_id": crid, "request_status": receipt_status,
            "reply": reply, "reply_type": reply_type, "artifacts": artifacts, "actions": actions,
            "outcome": outcome,
            "current": ready["current"], "allowed_actions": ready["allowed_actions"],
            "usage": {**usage, "estimated_cny": round(estimates, 6), "note": "费用为应用侧估算，非供应商账单"},
            "tool_calls": tool_results,
            "rounds": round_audit,
            "budget": {"per_call_limit_cny": config.DIRECTOR_MAX_COST_PER_CALL_CNY,
                       "session_limit_cny": config.DIRECTOR_MAX_SESSION_COST_CNY},
            "note": "草案自动保存但未确认；生成图片/视频需用户显式操作"}
        with db.transaction() as c:
            c.execute("INSERT INTO director_turns(id,project_id,session_id,request_id,user_text,reply_text,"
                      "reply_type,structured_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                      (turn_id,project_id,sess["id"],crid,message,reply,reply_type,
                       db.dumps({"artifacts": artifacts, "actions": actions, "outcome": outcome,
                                 "tool_failures": failures, "rounds": round_audit}),db.now()))
            response["workflow_revision"] = _workflow_revision(project_id, c)
            c.execute("UPDATE director_requests SET status=?,turn_id=?,response_json=?,updated_at=? WHERE id=?",
                      (receipt_status,turn_id,db.dumps(response),db.now(),rid))
        return response

    try:
        p = provider or prov.get_provider()
        if not p.available():
            raise err("ASSISTANT_UNAVAILABLE", "导演暂时不可用（未配置语言模型）")
        max_out = max(1, min(config.DIRECTOR_MAX_OUTPUT_TOKENS, 4096))
        messages_in = [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "context", "content": {"project": _tool_state(project_id),
                "selected_asset_ids": body.get("selected_asset_ids") or [], "goal": goal}},
            *_history(project_id), {"role": "user", "content": message}]
        ctx = {"project_id": project_id, "session_id": sess["id"], "request_id": crid}
        executed = 0
        repeated_errors: Counter = Counter()
        for round_index in range(max(1, config.DIRECTOR_MAX_ROUNDS)):
            estimate = _estimate_call_cost(messages_in, max_out, provider_name=p.name, model=p.model)
            check_budget(sess["id"], estimate)  # Every provider request, including tool continuation.
            estimates += estimate
            try:
                res = p.chat(messages_in, PROJECT_TOOLS, tool_results or None, max_tokens=max_out)
            except Exception:
                # A timeout does not prove zero spend. Reserve the estimate and expose that uncertainty.
                asst.record_usage(sess["id"], "llm", provider=p.name, model=p.model,
                    cost_cny=estimate, note=f"director:{project_id}:request_unknown_estimate")
                raise
            inp, outp = int(res.input_tokens or 0), int(res.output_tokens or 0)
            cost = _result_token_cost(res, provider_name=p.name, model=p.model)
            asst.record_usage(sess["id"], "llm", provider=p.name, model=p.model,
                input_tokens=inp, output_tokens=outp, tool_calls=0, cost_cny=cost,
                note=f"director:{project_id}:round:{round_index + 1}")
            usage["input_tokens"] += inp
            usage["output_tokens"] += outp
            usage["cost_cny"] = round(usage["cost_cny"] + cost, 6)
            finish_reason = res.meta.get("finish_reason")
            round_audit.append({"round": round_index + 1, "finish_reason": finish_reason,
                                "input_tokens": inp, "output_tokens": outp})
            reply = res.reply or ""
            calls = res.tool_calls or []
            if finish_reason == "length" and not calls:
                failure = {"code": "MODEL_OUTPUT_TRUNCATED", "message": "模型输出达到长度上限，本次未得到完整脚本"}
                pending_errors["model"] = failure
                failures.append({"tool": "model", "error": failure})
                repeated_errors[("model", failure["code"])] += 1
                if repeated_errors[("model", failure["code"])] >= max(1, config.DIRECTOR_MAX_REPEATED_ERRORS):
                    break
                messages_in.append({"role": "system", "content": "上次输出被截断，未执行任何保存。请简化内容并调用完整的脚本保存工具，不要只承诺稍后执行。"})
                continue
            pending_errors.pop("model", None)
            if not calls:
                if goal == "script" and not any(a["type"] == "script" for a in artifacts) and not pending_errors:
                    pending_errors["goal"] = {"code": "SCRIPT_NOT_SAVED", "message": "本次起稿尚未保存实际脚本"}
                    messages_in.extend([{"role": "assistant", "content": reply},
                        {"role": "system", "content": "本次目标是保存脚本，但还没有保存回执。请调用 propose_script；若缺少必要信息请明确说明，不能声称已保存。"}])
                    if round_index + 1 < max(1, config.DIRECTOR_MAX_ROUNDS):
                        continue
                break
            entries = []
            stop_for_error = False
            for tc in calls:
                try:
                    if executed >= config.DIRECTOR_MAX_TOOL_CALLS:
                        stop_for_error = True
                        raise err("DIRECTOR_TOOL_LIMIT", "本轮工具执行次数已达上限")
                    executed += 1
                    invalid = prov.tool_call_error(res, tc)
                    if invalid:
                        raise err(invalid["code"], invalid["message"])
                    result = execute_tool(tc.name, tc.arguments, ctx)
                    entry = {"tool": tc.name, "ok": True, "result": result}
                    pending_errors.pop(tc.name, None)
                    if result.get("proposal_id"):
                        artifacts.append({"type": result["kind"], "proposal_id": result["proposal_id"],
                            "proposal_version": result["version"], "target_id": result.get("target_id"),
                            "target_version": result.get("target_version"), "persistence": "saved", "confirmed": False,
                            **({"node_id": result["node_id"]} if result.get("node_id") else {})})
                        if result["kind"] == "script":
                            pending_errors.pop("goal", None)
                except Exception as e:
                    entry = {"tool": tc.name, "ok": False, "error": {
                        "code": getattr(e, "code", "TOOL_ERROR"), "message": str(getattr(e, "message", e))[:200]}}
                    # Never expose SQL/traceback details from unexpected persistence failures.
                    if not hasattr(e, "code"):
                        entry["error"]["message"] = "工具执行或保存失败，请重试或检查已保存内容"
                    pending_errors[tc.name] = entry["error"]
                    failures.append({"tool": tc.name, "error": entry["error"]})
                    key = (tc.name, entry["error"]["code"], entry["error"]["message"])
                    repeated_errors[key] += 1
                    stop_for_error = stop_for_error or repeated_errors[key] >= max(1, config.DIRECTOR_MAX_REPEATED_ERRORS)
                entries.append(entry)
                if entry["ok"] and entry["result"].get("kind") == "script":
                    break
            tool_results.extend(entries)
            # The durable artifact is sufficient; do not buy another model reply just to announce it.
            if any(a["type"] == "script" for a in artifacts) or stop_for_error:
                break
            messages_in.extend([{"role": "assistant", "content": res.reply or None,
                "tool_calls": [{"id": tc.id or f"call_{round_index}_{i}", "type": "function",
                    "function": {"name": tc.name, "arguments": prov.tool_arguments_json(tc)}}
                    for i, tc in enumerate(calls)]},
                *[{"role": "tool", "tool_call_id": tc.id or f"call_{round_index}_{i}", "name": entry["tool"],
                   "content": db.dumps(entry.get("result") if entry["ok"] else entry["error"])}
                  for i, (tc, entry) in enumerate(zip(calls, entries))]])
        if goal == "script" and not any(a["type"] == "script" for a in artifacts):
            pending_errors.setdefault("goal", {"code": "SCRIPT_NOT_SAVED", "message": "本次没有保存脚本，请修正后重试"})
        return finish()
    except Exception as e:
        failure = {"code": getattr(e, "code", "DIRECTOR_ERROR"),
                   "message": str(e.message)[:200] if hasattr(e, "message") else "本次执行中断，请查看已保存内容后继续"}
        pending_errors["execution"] = failure
        failures.append({"tool": "execution", "error": failure})
        # A later model/budget error must not erase earlier committed artifacts.
        if artifacts:
            return finish()
        try:
            finish(receipt_status="failed")
        finally:
            db.update("director_requests", rid, {"status": "failed", "error_code": failure["code"],
                      "error_message": failure["message"], "updated_at": db.now()})
        raise


def _workflow_revision(project_id: str, connection=None) -> int:
    """Opaque JS-safe equality token. Includes edits and confirmations, not just row counts."""
    def read(sql, args):
        return [dict(row) for row in connection.execute(sql,args)] if connection else db.query(sql,args)
    state = {}
    for table, columns in (
        ("projects", "id,name,brief_json,status,updated_at"),
        ("script_versions", "id,version,content_json,confirmed_at"),
        ("shots", "id,seq,description,motion,duration_s,revision"),
        ("director_proposals", "id,version,status,content_json"),
        ("director_turns", "id"),
        ("confirmations", "id,status,object_version"),
        ("canvas_nodes", "node_id,type,config_json,binding_json,config_revision")):
        key = "id" if table == "projects" else "project_id"
        state[table] = read(f"SELECT {columns} FROM {table} WHERE {key}=? ORDER BY 1", (project_id,))
        if table == "canvas_nodes":
            # Empty, unbound display cards are layout only. Adding one must not
            # invalidate a chat baseline; real bindings/config edits still do.
            state[table] = [row for row in state[table]
                            if db.loads(row["config_json"]) or db.loads(row["binding_json"])]
    raw = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return int(hashlib.sha256(raw.encode()).hexdigest()[:13], 16)


def _actions_for(project_id: str, ready: dict) -> list[dict]:
    """Current executable actions only; never reuse historical turn suggestions."""
    actions = []
    latest = db.query("SELECT * FROM script_versions WHERE project_id=? ORDER BY version DESC LIMIT 1", (project_id,))
    if latest:
        script = latest[0]
        valid = director.current_confirmation("script",script["id"],script["version"]).get("valid")
        if not valid:
            actions.append({"type":"confirm_script","target_id":script["id"],
                "target_version":script["version"],"requires_user_action":True})
        elif not db.query("SELECT 1 FROM shots WHERE project_id=? LIMIT 1", (project_id,)):
            plans = db.query("SELECT * FROM director_proposals WHERE project_id=? AND kind='shot_plan' "
                             "ORDER BY version DESC LIMIT 1", (project_id,))
            if (plans and plans[0]["status"] == "draft"
                    and plans[0]["target_id"] == script["id"]
                    and plans[0]["target_version"] == script["version"]):
                plan = plans[0]
                actions.append({"type":"apply_shot_plan","proposal_id":plan["id"],
                    "proposal_version":plan["version"],"requires_user_action":True})
    briefs = db.query("SELECT * FROM director_proposals WHERE project_id=? AND kind='brief' "
                      "ORDER BY version DESC LIMIT 1", (project_id,))
    if briefs and briefs[0]["status"] == "draft":
        actions.append({"type":"apply_brief","proposal_id":briefs[0]["id"],
            "proposal_version":briefs[0]["version"],"requires_user_action":True})
    if "quote_generation" in ready["allowed_actions"]:
        actions.append({"type":"quote_generation","requires_user_action":True})
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
        sql += " AND (created_at < ? OR (created_at = ? AND id < ?))"
        args.extend([row["created_at"],row["created_at"],row["id"]])
    sql += " ORDER BY created_at DESC,id DESC LIMIT ?"
    args.append(max(1, min(limit, 100)))
    rows = db.query(sql, args)
    items = [{"turn_id": r["id"], "request_id": r["request_id"],
              "user": r["user_text"], "reply": r["reply_text"],
              "reply_type": r["reply_type"],
              "structured": db.loads(r["structured_json"]) or {},
              "created_at": r["created_at"]} for r in rows]
    ready = director.readiness(project_id)
    sessions = db.query("SELECT t.session_id FROM director_turns t JOIN assistant_sessions s ON s.id=t.session_id "
                        "WHERE t.project_id=? AND s.project_id=? ORDER BY t.created_at DESC,t.id DESC LIMIT 1",
                        (project_id,project_id))
    return {"project_id": project_id, "session_id": sessions[0]["session_id"] if sessions else None,
            "workflow_revision": _workflow_revision(project_id), "actions": _actions_for(project_id,ready),
            "items": items, "next_cursor": rows[-1]["id"] if rows else None,
            "has_more": len(rows) == max(1, min(limit, 100))}


def request_receipt(project_id: str, client_request_id: str) -> dict:
    projects.get_project(project_id)
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


@projects.project_guard()
def apply_proposal(project_id: str, proposal_id: str, expected_version: int | None,
                   client_request_id: str | None) -> dict:
    """Persist application and its replay receipt atomically; no generation or implicit confirmation."""
    projects.get_project(project_id)
    row = db.get("director_proposals", proposal_id)
    if not row or row["project_id"] != project_id:
        raise err("PROPOSAL_NOT_FOUND", f"提案不存在：{proposal_id}")
    if expected_version is not None and (isinstance(expected_version, bool)
            or not isinstance(expected_version, int) or expected_version != row["version"]):
        raise err("PROPOSAL_VERSION_CONFLICT", "提案版本不匹配", detail={"current_version": row["version"]})
    receipt_key = "apply:" + (client_request_id or proposal_id)
    fingerprint = "apply:" + proposal_id + ":" + str(row["version"])
    prior = _receipt(project_id, receipt_key)
    if prior:
        if prior["payload_hash"] != fingerprint:
            raise err("IDEMPOTENCY_CONFLICT", "同一应用请求编号用于不同提案")
        if prior["response_json"]:
            return {**db.loads(prior["response_json"]), "idempotent_replay": True}
    if row["status"] != "draft":
        receipts = db.query("SELECT response_json FROM director_requests WHERE project_id=? "
                            "AND payload_hash=? AND status='completed' ORDER BY created_at DESC LIMIT 1",
                            (project_id, fingerprint))
        if receipts:
            return {**db.loads(receipts[0]["response_json"]), "idempotent_replay": True}
        return {"proposal_id": proposal_id, "status": row["status"], "applied": False,
                "note": "提案已应用或已被取代，请刷新项目状态"}
    content = db.loads(row["content_json"]) or {}
    applied = {"proposal_id": proposal_id, "kind": row["kind"], "applied": True}
    clean_shots = None
    if row["kind"] == "shot_plan":
        latest = db.query("SELECT id,version FROM script_versions WHERE project_id=? ORDER BY version DESC LIMIT 1", (project_id,))
        if not latest or latest[0]["id"] != row["target_id"] or latest[0]["version"] != row["target_version"]:
            raise err("PROPOSAL_NOT_APPLICABLE", "脚本已更新，请重新生成镜头计划")
        shots._require_script_confirmed(project_id)
        clean_shots = shots.validate_shot_input(project_id, content.get("shots"))
    elif row["kind"] == "brief":
        content = _clean_brief(content)
    with db.transaction() as c:
        current = c.execute("SELECT * FROM director_proposals WHERE id=? AND project_id=?",
                            (proposal_id, project_id)).fetchone()
        if not current or current["status"] != "draft":
            raise err("PROPOSAL_NOT_APPLICABLE", "提案状态已改变，请刷新")
        if row["kind"] == "brief":
            project = c.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            old = db.loads(project["brief_json"]) or {}
            brief = {**old, **content}
            stale = []
            if brief != old:
                c.execute("UPDATE projects SET brief_json=?,updated_at=? WHERE id=?", (db.dumps(brief),db.now(),project_id))
                c.execute("UPDATE confirmations SET status='invalidated',reason='upstream_brief_changed',invalidated_at=? "
                          "WHERE project_id=? AND object_type IN ('script','shot_plan') AND status='active'",
                          (db.now(),project_id))
                stale = ["script_confirm", "shot_plan_confirm"]
            applied.update({"brief": brief, "stale": stale})
        elif row["kind"] == "script":
            script = c.execute("SELECT * FROM script_versions WHERE id=? AND project_id=?", (row["target_id"],project_id)).fetchone()
            if not script:
                raise err("PROPOSAL_NOT_APPLICABLE", "脚本草案已不存在")
            confirmation = c.execute("SELECT status FROM confirmations WHERE object_type='script' "
                "AND object_id=? AND object_version=? ORDER BY created_at DESC LIMIT 1", (script["id"],script["version"])).fetchone()
            applied.update({"script_id": script["id"], "version": script["version"],
                "confirmed": bool(confirmation and confirmation["status"] == "active"),
                "note": "脚本已保存；请调用 /api/scripts/{id}/confirm 确认"})
        elif row["kind"] == "shot_plan":
            latest = c.execute("SELECT * FROM script_versions WHERE project_id=? ORDER BY version DESC LIMIT 1", (project_id,)).fetchone()
            if not latest or latest["id"] != row["target_id"] or latest["version"] != row["target_version"]:
                raise err("PROPOSAL_NOT_APPLICABLE", "脚本已更新，请重新生成镜头计划")
            cfm = c.execute("SELECT status FROM confirmations WHERE object_type='script' "
                            "AND object_id=? AND object_version=? ORDER BY created_at DESC LIMIT 1", (latest["id"], latest["version"])).fetchone()
            if not cfm or cfm["status"] != "active":
                raise err("SCRIPT_NOT_CONFIRMED", "脚本确认已失效，请重新确认")
            if c.execute("SELECT 1 FROM shots WHERE project_id=? LIMIT 1", (project_id,)).fetchone():
                raise err("PROPOSAL_NOT_APPLICABLE", "项目已有镜头，请通过镜头编辑调整")
            ids = []
            for i, shot in enumerate(clean_shots, 1):
                sid = db.new_id("shot")
                c.execute("INSERT INTO shots(id,project_id,script_version_id,seq,duration_s,description,motion,status,revision,created_at,updated_at) "
                          "VALUES(?,?,?,?,?,?,?,'draft',1,?,?)",
                          (sid,project_id,latest["id"],i,shot["duration_s"],shot["description"],shot["motion"],db.now(),db.now()))
                ids.append(sid)
            applied.update({"shot_ids": ids, "count": len(ids)})
        else:
            raise err("PROPOSAL_NOT_APPLICABLE", "未知提案类型")
        c.execute("UPDATE director_proposals SET status='applied',applied_at=? WHERE id=?", (db.now(),proposal_id))
        applied["workflow_revision"] = _workflow_revision(project_id, c)
        c.execute("INSERT INTO director_requests(id,project_id,client_request_id,payload_hash,status,response_json,created_at,updated_at) "
                  "VALUES(?,?,?,?,'completed',?,?,?)",
                  (db.new_id("dreq"),project_id,receipt_key,fingerprint,db.dumps(applied),db.now(),db.now()))
    # Execution outcome is already durable even if this convenience refresh fails.
    applied["allowed_actions"] = director.readiness(project_id)["allowed_actions"]
    return applied
