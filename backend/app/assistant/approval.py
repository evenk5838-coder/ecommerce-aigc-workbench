"""报价 → 用户确认 → 批准 → 提交 的两阶段门禁。

核心约束（PRD v0.9 §13.5 + 阶段一复核 20261024）：
- **报价不是批准**：`quote` 只证明"系统算过这个方案"。
- **确认只能由界面触发**：`confirm()` 不是 LLM 工具；服务端记录批准。
- **报价即冻结方案**：报价时固定 `template_version` 与解析后的完整参数
  （含最终 prompt），后续模板升版**不会**改变已报价/已确认的内容。
- **批准绑定冻结方案**：`plan_hash` 不一致 / 过期 / 已用 → 拒绝。
- **幂等回放前先校验方案**：同批准 + 同方案 → 返回原任务；同批准 + 改方案 → 409。
- **一个批准只对应一个任务**：批准一次性 + `client_request_id="apv:<id>"`。
- **来源由后端写**：有批准即强制 `caller_id="assistant"`。
- **一致性**：消费批准与写入任务在同一个 SQLite 事务内。
"""
from __future__ import annotations

import hashlib
import json
import os

from .. import config, db
from ..errors import err

QUOTE_TTL_S = float(os.getenv("ASSISTANT_QUOTE_TTL_S", "600"))
APPROVAL_TTL_S = float(os.getenv("ASSISTANT_APPROVAL_TTL_S", "600"))

# 方案哈希覆盖的字段（影响生成结果的都算）
_HASH_FIELDS = ("kind", "model_id", "input_asset_id", "template_id",
                "template_version", "template_fields", "params")


def _canonical_payload(payload: dict) -> dict:
    return {k: (payload.get(k) if payload.get(k) is not None else None)
            for k in _HASH_FIELDS}


def plan_hash(payload: dict) -> str:
    raw = json.dumps(_canonical_payload(payload), sort_keys=True,
                     ensure_ascii=False, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ---------- 冻结方案 ----------
def freeze_plan(payload: dict, resolved: dict) -> dict:
    """把"用户确认时看到的方案"完整冻结下来。

    两层内容：
    - `submit_payload`：**请求形状**（可直接 POST /api/tasks），已补全 template_version；
      方案哈希基于它计算，保证"确认的就是提交的"。
    - `resolved`：解析后的完整参数（含最终 prompt）与生效参数，
      提交时用它执行，因此模板升版不会改变已确认内容。
    """
    submit_payload = {
        "kind": resolved["kind"],
        "model_id": resolved["model_id"],
        "input_asset_id": resolved["input_asset_id"],
        "template_id": resolved["template_id"],
        "template_version": resolved["template_version"],
        "template_fields": payload.get("template_fields") or {},
        "params": payload.get("params") or {},
    }
    return {
        "submit_payload": submit_payload,
        "plan_hash": plan_hash(submit_payload),
        "resolved": {
            "params": resolved["params"],
            "prompt": resolved["params"].get("prompt"),
            "effective": resolved["effective"],
            "concept_mode": bool(resolved["concept_mode"]),
            "kind": resolved["kind"],
            "model_id": resolved["model_id"],
        },
        "effective": resolved["effective"],
        "template_id": resolved["template_id"],
        "template_version": resolved["template_version"],
        "template_name": resolved["template_name"],
        "concept_mode": bool(resolved["concept_mode"]),
        "prompt": resolved["params"].get("prompt"),
        "cost_cny": resolved["estimated_cost_cny"],
        "warnings": resolved.get("warnings") or [],
        "model_lifecycle": resolved.get("lifecycle") or {},
    }


# ---------- 报价单 ----------
def create_quote(*, session_id: str, project_id: str | None, payload: dict,
                 resolved: dict, now_ts: float | None = None) -> dict:
    now = db.now() if now_ts is None else float(now_ts)
    frozen = freeze_plan(payload, resolved)
    qid = db.new_id("q")
    db.insert("assistant_quotes", {
        "id": qid, "session_id": session_id, "project_id": project_id,
        "plan_hash": frozen["plan_hash"],
        "payload_json": db.dumps(_canonical_payload(payload)),
        "effective_json": db.dumps(frozen["effective"]),
        "cost_cny": float(frozen["cost_cny"]),
        "expires_at": now + QUOTE_TTL_S,
        "created_at": now,
        "frozen_json": db.dumps(frozen),
    })
    return {"quote_id": qid, "plan_hash": frozen["plan_hash"],
            "expires_at": now + QUOTE_TTL_S, "cost_cny": frozen["cost_cny"],
            "effective": frozen["effective"], "frozen": frozen}


def get_quote(quote_id: str) -> dict:
    row = db.get("assistant_quotes", quote_id)
    if not row:
        raise err("QUOTE_NOT_FOUND", "报价不存在，请重新报价",
                  detail={"quote_id": quote_id})
    return row


def quote_frozen(quote: dict) -> dict:
    frozen = db.loads(quote.get("frozen_json"))
    if frozen:
        return frozen
    # 兼容旧报价（无 frozen_json）：退化为仅有效参数，不 frozen 提交参数
    return {"effective": db.loads(quote.get("effective_json")) or {},
            "plan_hash": quote.get("plan_hash"),
            "template_id": None, "template_version": None,
            "template_name": None, "concept_mode": False,
            "prompt": None, "cost_cny": quote.get("cost_cny"),
            "submit_payload": None, "warnings": [], "model_lifecycle": {}}


def assert_quote_usable(quote: dict, now_ts: float | None = None) -> None:
    now = db.now() if now_ts is None else float(now_ts)
    if float(quote.get("expires_at") or 0) <= now:
        raise err("QUOTE_EXPIRED", "报价已过期，请重新报价",
                  detail={"quote_id": quote["id"], "expired_at": quote.get("expires_at")})


# ---------- 用户确认 → 批准（继承冻结方案）----------
def create_approval(*, quote_id: str, session_id: str, project_id: str | None,
                    user_action: str, now_ts: float | None = None) -> dict:
    """**只应由前端界面调用**（用户点击"确认生成"）。不作为 LLM 工具暴露。"""
    if user_action != "confirm_generate":
        raise err("VALIDATION_FAILED", "确认动作必须为 confirm_generate",
                  detail={"allowed": ["confirm_generate"], "got": user_action})
    now = db.now() if now_ts is None else float(now_ts)
    quote = get_quote(quote_id)
    assert_quote_usable(quote, now)
    if quote["session_id"] != session_id:
        raise err("QUOTE_SESSION_MISMATCH", "报价不属于当前会话，请重新报价",
                  detail={"quote_session": quote["session_id"]})
    frozen = quote_frozen(quote)

    aid = db.new_id("ap")
    db.insert("assistant_approvals", {
        "id": aid, "quote_id": quote_id, "session_id": session_id,
        "project_id": project_id or quote.get("project_id"),
        "plan_hash": frozen["plan_hash"],
        "frozen_json": db.dumps(frozen),
        "expires_at": now + APPROVAL_TTL_S,
        "max_uses": 1, "used_count": 0, "task_id": None,
        "created_at": now, "used_at": None,
    })
    return {"approval_id": aid, "quote_id": quote_id,
            "plan_hash": frozen["plan_hash"], "expires_at": now + APPROVAL_TTL_S,
            "max_uses": 1, "session_id": session_id,
            "project_id": project_id or quote.get("project_id"),
            "template_id": frozen.get("template_id"),
            "template_version": frozen.get("template_version"),
            "effective": frozen.get("effective"),
            "cost_cny": frozen.get("cost_cny"),
            "submit_payload": frozen.get("submit_payload")}


# ---------- 提交 ----------
def normalize_submit_payload(body, approval_frozen: dict) -> dict:  # noqa: ANN001
    """把请求归一成可比较的方案；缺省字段用**冻结值**补齐（不用最新模板）。"""
    frozen_payload = approval_frozen.get("submit_payload") or {}
    tv = body.template_version
    if tv is None and body.template_id:
        tv = frozen_payload.get("template_version")
    return {
        "kind": body.kind,
        "model_id": body.model_id,
        "input_asset_id": body.input_asset_id,
        "template_id": body.template_id,
        "template_version": tv,
        "template_fields": body.template_fields or {},
        "params": body.params or {},
    }


def submit_with_approval(body, now_ts: float | None = None) -> dict:  # noqa: ANN001
    """校验批准 → （幂等命中则返回原任务）→ 消费批准 + 写任务（同一事务）。

    执行使用**报价时冻结的参数**（含 prompt 与 template_version），
    不重新解析模板，因此模板升版不会改变已确认内容。
    """
    now = db.now() if now_ts is None else float(now_ts)
    approval_id = body.approval_id

    row = db.get("assistant_approvals", approval_id)
    if not row:
        raise err("APPROVAL_NOT_FOUND", "确认记录不存在，请先报价并确认",
                  detail={"approval_id": approval_id})
    frozen = db.loads(row.get("frozen_json")) or {}
    if not frozen.get("submit_payload"):
        raise err("APPROVAL_INVALID", "该确认缺少冻结方案，请重新报价并确认",
                  detail={"approval_id": approval_id})

    # ---- 1) 先校验方案（包括幂等回放之前）----
    current = normalize_submit_payload(body, frozen)
    computed = plan_hash(current)
    if computed != frozen["plan_hash"]:
        raise err("APPROVAL_PLAN_MISMATCH",
                  "方案已变更（模板/模型/参数/素材），请重新报价并确认",
                  detail={"approval_id": approval_id,
                          "approved_plan": frozen["plan_hash"],
                          "current_plan": computed})
    if float(row.get("expires_at") or 0) <= now:
        raise err("APPROVAL_EXPIRED", "确认已过期，请重新报价并确认",
                  detail={"approval_id": approval_id,
                          "expired_at": row.get("expires_at")})

    # ---- 2) 幂等回放（同批准 + 同方案）----
    key = f"apv:{approval_id}"
    existing = db.query("SELECT * FROM tasks WHERE client_request_id=?", (key,))
    if existing:
        task = existing[0]
        try:
            from .. import cloud_tasks
            task["estimated_cost_cny"] = cloud_tasks.estimate_cost(
                task["kind"], task["model_id"], db.loads(task.get("params_json")) or {})
        except Exception:  # noqa: BLE001
            task["estimated_cost_cny"] = frozen.get("cost_cny")
        task["idempotent_replay"] = True
        task["origin"] = "assistant"
        task["approval_id"] = approval_id
        return task

    # ---- 3) 执行前仍校验模型可用性（不让未开通/已下线模型执行冻结方案）----
    model_id = frozen["submit_payload"]["model_id"]
    submittability = config.model_submittability(model_id, now)
    if "MODEL_NOT_OPENED" in submittability["reasons"]:
        raise err("MODEL_NOT_OPENED",
                  f"模型 {model_id} 尚未在账号中开通，不可提交",
                  detail={"activation": submittability["activation"],
                          "reasons": submittability["reasons"]})
    life = config.model_lifecycle_state(model_id, now)
    if life.get("status") == "eos_expired":
        raise err("MODEL_EOS",
                  f"模型 {model_id} 已于 {life.get('eos_at')} 下线，不可新增生成任务",
                  detail=life)

    frozen_resolved = frozen.get("resolved") or {}
    params = frozen_resolved.get("params") or frozen["submit_payload"].get("params") or {}
    problems = config.validate_params_for_model(model_id, frozen["submit_payload"]["kind"], params)
    if problems:
        raise err("MODEL_CAPABILITY_MISMATCH",
                  f"模型 {model_id} 不支持冻结方案中的参数",
                  detail={"problems": problems})

    # ---- 4) 事务：消费批准 + 写任务 ----
    task_id = db.new_id("task")
    task_row = {
        "id": task_id, "kind": frozen["submit_payload"]["kind"], "status": "queued",
        "params_json": db.dumps(params), "model_id": model_id,
        "model_revision": "local-snapshot",
        "input_asset_id": frozen["submit_payload"].get("input_asset_id"),
        "batch_id": None, "client_request_id": key,
        "progress": 0.0, "stage": "queued", "attempts": 0, "cancel_requested": 0,
        "created_at": now, "updated_at": now,
        "template_id": frozen.get("template_id"),
        "template_version": frozen.get("template_version"),
        "external_request_id": row.get("session_id"),
        "caller_id": "assistant",          # 后端强制
        "concept_mode": 1 if frozen.get("concept_mode") else 0,
    }
    with db.transaction() as c:
        cur = c.execute(
            "UPDATE assistant_approvals SET used_count=used_count+1, used_at=?, task_id=? "
            "WHERE id=? AND used_count=0", (now, task_id, approval_id))
        if cur.rowcount != 1:
            raise err("APPROVAL_ALREADY_USED", "该确认已被使用，请重新报价",
                      detail={"approval_id": approval_id})
        cols = ",".join(task_row)
        ph = ",".join("?" * len(task_row))
        c.execute(f"INSERT INTO tasks({cols}) VALUES({ph})", list(task_row.values()))

    q = db.query("SELECT COUNT(*) c FROM tasks WHERE status='queued'")[0]["c"]
    return {**task_row, "queue_position": q,
            "estimated_cost_cny": frozen.get("cost_cny"),
            "approval_id": approval_id, "origin": "assistant"}
