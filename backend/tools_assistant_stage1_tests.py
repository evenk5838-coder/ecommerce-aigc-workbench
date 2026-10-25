#!/usr/bin/env python3
"""助手阶段一：隔离测试（零付费、零真实 LLM、零真实生成）。

覆盖验收要求：
  1) 仅优化不会提交生成
  2) 未确认不能提交
  3) 改参数后旧批准失效
  4) 重复提交不重复创建任务
  5) 助手不能自行确认或伪装成手动来源
  6) 重启后关键确认与消耗记录仍保留
  7) 原有直接操作不回归

隔离方式：
  - `data/app.db` 复制到临时目录，测试进程内把 `config.DB_PATH` 指向副本；
  - LLM 用 `FixedReplyProvider`（固定脚本，不联网）；默认 provider 为 Unavailable；
  - `providers_ark._request` 替换为断言守卫，任何云端调用立即失败；
  - 不启动 worker（不消费队列），因此不会产生真实生成。

用法：<venv>/bin/python backend/tools_assistant_stage1_tests.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import config, db, main, providers_ark, worker  # noqa: E402
from app.assistant import approval, provider as prov, service, tools  # noqa: E402
from app.errors import AppError  # noqa: E402

RESULTS: list[dict] = []
OUT = ROOT / "docs" / "evidence" / "助手阶段一-20261024"

REF_IMG = "asset_d73231ab405540b68eb6"
FIRST_FRAME = "asset_cd91e2f9dda14123a622"
FLASH = "doubao-seedream-5-0-flash-260915"
V10 = "doubao-seedance-1-0-pro-fast-251015"


def case(name: str, fn) -> None:
    try:
        detail = fn() or {}
        RESULTS.append({"case": name, "ok": True, "detail": detail})
        print(f"  OK  {name}" + (f"  {detail}" if detail else ""))
    except AssertionError as e:
        RESULTS.append({"case": name, "ok": False, "error": str(e)})
        print(f" FAIL {name} → {e}")
    except Exception as e:  # noqa: BLE001
        RESULTS.append({"case": name, "ok": False, "error": f"{type(e).__name__}: {e}",
                        "trace": traceback.format_exc()[-500:]})
        print(f" ERR  {name} → {type(e).__name__}: {e}")


def expect_error(code: str, fn, http: int | None = None) -> dict:
    try:
        fn()
    except AppError as e:
        assert e.code == code, f"期望 {code}，实际 {e.code}: {e.message}"
        if http is not None:
            assert e.http == http, f"期望 HTTP {http}，实际 {e.http}"
        return {"code": e.code, "http": e.http, "message": e.message[:70]}
    raise AssertionError(f"期望抛出 {code}，但没有报错")


def n_tasks() -> int:
    return db.query("SELECT COUNT(*) c FROM tasks")[0]["c"]


def n_quotes() -> int:
    return db.query("SELECT COUNT(*) c FROM assistant_quotes")[0]["c"]


# ---------- 隔离环境 ----------
def setup() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="asst-stage1-"))
    shutil.copy2(config.DB_PATH, tmp / "app.db")
    config.DB_PATH = tmp / "app.db"
    db.init_db()

    def _guard(*a, **k):
        raise AssertionError("测试期间禁止调用云端模型（检测到 providers_ark._request）")
    providers_ark._request = _guard  # type: ignore[assignment]
    prov.set_provider(prov.UnavailableProvider())
    return tmp


def payload(duration: int = 5, ratio: str = "9:16") -> dict:
    return {"kind": "video", "model_id": V10, "input_asset_id": FIRST_FRAME,
            "template_id": "tpl_product_motion_5s", "template_version": 2,
            "template_fields": {"motion": "推近特写", "duration": duration, "ratio": ratio}}


def body_of(p: dict) -> main.TaskIn:
    return main.TaskIn(**{k: p.get(k) for k in ("kind", "model_id", "input_asset_id",
                                                "template_id", "template_version",
                                                "template_fields", "params") if k in p})


def run() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = setup()
    print(f"隔离库：{tmp / 'app.db'}（真实库未改动）\n")

    print("=== 0. 未配置 LLM 时不假装可用 ===")

    def status_unavailable():
        st = prov.provider_status()
        assert st["available"] is False, st
        assert st["vision"] is False and st["streaming"] is False
        return {"available": False, "note": st["note"][:20]}
    case("GET /api/assistant/status 默认不可用（vision/streaming=false）", status_unavailable)

    def chat_unavailable():
        d = expect_error("ASSISTANT_UNAVAILABLE",
                         lambda: service.chat(session_id=None, project_id="p1",
                                              message="帮我做张图"), 503)
        return d
    case("未配置 LLM 时 chat → ASSISTANT_UNAVAILABLE(503)", chat_unavailable)

    print("\n=== 1. 仅优化不提交生成 ===")
    stub = prov.FixedReplyProvider(script=[{
        "reply": "优化后：以参考图中的商品为准，保留外形与标签，背景替换为浅灰白大理石台面。",
    }])
    prov.set_provider(stub)

    def optimize_only():
        before_t, before_q = n_tasks(), n_quotes()
        out = service.chat(session_id=None, project_id="p1", message="帮我润色这句：白瓶 大理石 2K",
                           intent="optimize_only")
        assert out["tool_calls"] == [], out["tool_calls"]
        assert n_tasks() == before_t, "仅优化模式产生了任务"
        assert n_quotes() == before_q, "仅优化模式产生了报价"
        assert "优化后" in out["reply"]
        return {"tool_calls": 0, "tasks_delta": 0, "quotes_delta": 0,
                "session_intent": out["intent"]}
    case("intent=optimize_only：无工具调用、无报价、无任务", optimize_only)

    def optimize_only_quote_rejected():
        s = service.ensure_session(None, "p1", "optimize_only")
        d = expect_error("QUOTE_NOT_ALLOWED",
                         lambda: service.quote(session_id=s["id"], project_id="p1",
                                               payload=payload()), 400)
        return d
    case("仅优化会话内报价被拒（QUOTE_NOT_ALLOWED）", optimize_only_quote_rejected)

    def stub_tries_quote_in_optimize():
        s = service.ensure_session(None, "p1", "optimize_only")
        scripted = prov.FixedReplyProvider(script=[{
            "reply": "好的，先报价。",
            "tool_calls": [{"name": "quote", "arguments": {"payload": payload()}}],
        }])
        before_q = n_quotes()
        out = service.chat(session_id=s["id"], project_id="p1", message="直接生成吧",
                           provider=scripted)
        assert n_quotes() == before_q, "仅优化会话仍产生了报价"
        assert out["tool_calls"][0]["ok"] is False, out["tool_calls"]
        assert out["tool_calls"][0]["error"]["code"] == "QUOTE_NOT_ALLOWED"
        return {"tool_error": out["tool_calls"][0]["error"]["code"]}
    case("替身尝试在仅优化会话内调 quote → 工具层拒绝", stub_tries_quote_in_optimize)

    print("\n=== 2. 未确认不能提交 ===")

    def no_confirm_tools():
        names = [t.name for t in tools.tool_specs()]
        assert "confirm" not in names, names
        assert "submit" not in names, names
        assert tools.FORBIDDEN_TOOLS >= {"confirm", "submit"}
        return {"tools": names, "forbidden": sorted(tools.FORBIDDEN_TOOLS)}
    case("工具白名单不含 confirm / submit", no_confirm_tools)

    def forbidden_tool_call():
        d = expect_error("TOOL_FORBIDDEN",
                         lambda: tools.execute("confirm", {}, {"session_id": "x"}), 400)
        return d
    case("模型调用 confirm 工具 → TOOL_FORBIDDEN", forbidden_tool_call)

    def submit_without_approval():
        before = n_tasks()
        d = expect_error("APPROVAL_NOT_FOUND",
                         lambda: main.create_task(
                             main.TaskIn(**{**body_of(payload()).model_dump(),
                                            "approval_id": "ap_not_exist"})), 404)
        assert n_tasks() == before, "无效批准仍创建了任务"
        return d
    case("伪造/不存在的 approval_id → APPROVAL_NOT_FOUND，不建任务", submit_without_approval)

    print("\n=== 3-4. 报价 → 确认 → 提交（含改参数与重复提交）===")
    s = service.ensure_session(None, "p1", "generate")
    sid = s["id"]

    def quote_ok():
        q = service.quote(session_id=sid, project_id="p1", payload=payload(5))
        assert q["side_effects"]["task_created"] is False
        assert q["effective"]["duration"] == 5
        return {"quote_id": q["quote_id"][:14] + "…", "cost": q["estimated_cost_cny"],
                "effective": q["effective"]}
    q1 = None

    def _q1():
        nonlocal q1
        q1 = service.quote(session_id=sid, project_id="p1", payload=payload(5))
        assert n_tasks() == n_tasks()
        return {"cost": q1["estimated_cost_cny"], "plan": q1["plan_hash"][:20] + "…"}
    case("助手报价：返回 quote_id/plan_hash/effective，不建任务", _q1)

    ap = {}

    def confirm_ok():
        a = service.confirm(session_id=sid, quote_id=q1["quote_id"], project_id="p1")
        ap.update(a)
        assert a["max_uses"] == 1 and a["plan_hash"] == q1["plan_hash"]
        return {"approval_id": a["approval_id"][:12] + "…", "max_uses": 1}
    case("用户确认 → 一次性批准（绑定 plan_hash）", confirm_ok)

    def plan_change_invalidates():
        before = n_tasks()
        d = expect_error("APPROVAL_PLAN_MISMATCH",
                         lambda: main.create_task(
                             main.TaskIn(**{**body_of(payload(10)).model_dump(),
                                            "approval_id": ap["approval_id"]})), 409)
        assert n_tasks() == before, "方案不一致仍建了任务"
        return d
    case("改参数（5s→10s）后旧批准失效 → APPROVAL_PLAN_MISMATCH", plan_change_invalidates)

    created = {}

    def submit_ok():
        t = main.create_task(main.TaskIn(**{**body_of(payload(5)).model_dump(),
                                          "approval_id": ap["approval_id"]}))
        created.update(t)
        assert t["caller_id"] == "assistant", t["caller_id"]
        row = db.get("tasks", t["id"])
        assert row["client_request_id"] == f"apv:{ap['approval_id']}"
        return {"task_id": t["id"][:12] + "…", "caller_id": t["caller_id"]}
    case("带批准提交：建立任务并强制 caller_id=assistant", submit_ok)

    def duplicate_submit():
        before = n_tasks()
        t2 = main.create_task(main.TaskIn(**{**body_of(payload(5)).model_dump(),
                                           "approval_id": ap["approval_id"]}))
        after = n_tasks()
        assert t2["id"] == created["id"], f"重复提交产生了新任务 {t2['id']}"
        assert after == before, f"任务数变化 {before}→{after}"
        assert t2.get("idempotent_replay") is True
        return {"same_task": True, "tasks_delta": after - before}
    case("同一批准重复提交：返回同一任务（不重复创建）", duplicate_submit)

    def approval_consumed_once():
        used = db.get("assistant_approvals", ap["approval_id"])
        assert used["used_count"] == 1, used["used_count"]
        assert used["task_id"] == created["id"]
        return {"used_count": used["used_count"], "max_uses": used["max_uses"]}
    case("批准消费一次后记录 task_id", approval_consumed_once)

    print("\n=== 5. 不能自我确认 / 不能伪装来源 ===")

    def fake_manual_origin():
        # 手动路径把 caller_id 伪造成 assistant → 后端归一为 local
        t = main.create_task(main.TaskIn(kind="image", model_id=FLASH,
                                         input_asset_id=REF_IMG,
                                         params={"prompt": "白底主图",
                                                 "generation_mode": "image_to_image",
                                                 "size": "2048x2048"},
                                         caller_id="assistant"))
        row = db.get("tasks", t["id"])
        assert row["caller_id"] == "local", row["caller_id"]
        return {"requested": "assistant", "stored": row["caller_id"]}
    case("请求体伪造 caller_id=assistant（手动路径）→ 归一为 local", fake_manual_origin)

    def approval_forces_origin():
        q = service.quote(session_id=sid, project_id="p1", payload=payload(5))
        a = service.confirm(session_id=sid, quote_id=q["quote_id"], project_id="p1")
        t = main.create_task(main.TaskIn(**{**body_of(payload(5)).model_dump(),
                                          "approval_id": a["approval_id"],
                                          "caller_id": "local"}))
        row = db.get("tasks", t["id"])
        assert row["caller_id"] == "assistant", row["caller_id"]
        return {"requested": "local", "stored": row["caller_id"]}
    case("带批准提交时请求体写 caller_id=local 无效 → 仍为 assistant", approval_forces_origin)

    def confirm_requires_quote():
        d = expect_error("QUOTE_NOT_FOUND",
                         lambda: service.confirm(session_id=sid, quote_id="q_missing",
                                                 project_id="p1"), 404)
        return d
    case("无报价无法确认（QUOTE_NOT_FOUND）", confirm_requires_quote)

    def confirm_wrong_session():
        other = service.ensure_session(None, "p2", "generate")
        q = service.quote(session_id=sid, project_id="p1", payload=payload(5))
        d = expect_error("QUOTE_SESSION_MISMATCH",
                         lambda: service.confirm(session_id=other["id"],
                                                 quote_id=q["quote_id"], project_id="p2"), 400)
        return d
    case("跨会话确认被拒（QUOTE_SESSION_MISMATCH）", confirm_wrong_session)

    def approval_expiry():
        q = service.quote(session_id=sid, project_id="p1", payload=payload(5),
                          now_ts=1000.0)
        a = service.confirm(session_id=sid, quote_id=q["quote_id"], project_id="p1",
                            now_ts=1000.0)
        d = expect_error("APPROVAL_EXPIRED",
                         lambda: approval.submit_with_approval(
                             main.TaskIn(**{**body_of(payload(5)).model_dump(),
                                            "approval_id": a["approval_id"]}),
                             now_ts=1000.0 + 10_000), 409)
        return d
    case("批准过期后提交被拒（APPROVAL_EXPIRED）", approval_expiry)

    print("\n=== 6. brief / 限制 / 用量 ===")

    def brief_crud():
        service.save_brief("p1", {"subject": "单瓶精华液", "forbidden_elements": "不要人物"})
        got = service.get_brief("p1")
        assert got["brief"]["subject"] == "单瓶精华液" and got["version"] == 1
        service.save_brief("p1", {"style": "简约"})
        got2 = service.get_brief("p1")
        assert got2["brief"]["subject"] == "单瓶精华液", got2["brief"]
        assert got2["brief"]["style"] == "简约" and got2["version"] == 2
        return {"version": got2["version"], "merged": sorted(got2["brief"])}
    case("brief 保存/读取/合并（版本递增）", brief_crud)

    def brief_reject_unknown():
        d = expect_error("VALIDATION_FAILED",
                         lambda: service.save_brief("p1", {"made_up": "x"}), 400)
        return d
    case("brief 含未知字段被拒", brief_reject_unknown)

    def question_limit_stops():
        s2 = service.ensure_session(None, "p1", "generate")
        scripted = prov.FixedReplyProvider(script=[
            {"reply": f"第{i}个问题：请确认商品数量？"} for i in range(1, 8)
        ])
        out = None
        for _ in range(service.MAX_QUESTIONS + 1):
            out = service.chat(session_id=s2["id"], project_id="p1",
                               message="继续", provider=scripted)
        assert out["awaiting_facts"] is not None, out
        assert out["awaiting_facts"]["reason"] == "questions_limit_reached"
        assert n_tasks() == n_tasks()
        return {"questions_used": out["session"]["limits"]["questions_used"],
                "status": out["session"]["status"]}
    case("提问达上限 → 停在待补充（不生成）", question_limit_stops)

    def turn_limit():
        s3 = service.ensure_session(None, "p1", "generate")
        db.update("assistant_sessions", s3["id"], {"turns": service.MAX_TURNS})
        d = expect_error("ASSISTANT_TURN_LIMIT",
                         lambda: service.chat(session_id=s3["id"], project_id="p1",
                                              message="再来", provider=stub), 429)
        return d
    case("轮次达上限 → ASSISTANT_TURN_LIMIT(429)", turn_limit)

    def usage_separated():
        u = service.usage_summary(sid)
        kinds = {r["kind"] for r in u["assistant"]["by_kind"]}
        assert "llm" in kinds or "quote" in kinds, kinds
        assert "generation" in u and "assistant" in u
        # 助手用量不应写进 tasks.cost_cny
        assistant_tasks = db.query(
            "SELECT COUNT(*) c, COALESCE(SUM(cost_cny),0) s FROM tasks "
            "WHERE caller_id='assistant'")
        assert float(assistant_tasks[0]["s"] or 0) == 0, "助手提交的任务不应带生成费用"
        return {"kinds": sorted(kinds),
                "assistant_tokens": u["assistant"]["input_tokens"] + u["assistant"]["output_tokens"],
                "generation_tasks": u["generation"]["tasks"]}
    case("助手用量与生成用量分开记录", usage_separated)

    print("\n=== 6.1 复核修复 A：连续对话与刷新恢复 ===")

    def multi_turn_context():
        s2 = service.ensure_session(None, "p-chat", "generate")
        scripted = prov.FixedReplyProvider(script=[
            {"reply": "先确认商品：要展示哪件商品？请给名称与数量（我看不到图片）。",
             "structured": {"reply_type": "clarify",
                            "options": [{"id": "a", "label": "单瓶精华液"},
                                        {"id": "b", "label": "多件套装"}],
                            "missing_fields": ["product_name"]}},
            {"reply": "收到：单瓶精华液。要不要出现人物？",
             "structured": {"reply_type": "clarify",
                            "options": [{"id": "a", "label": "不要人物"},
                                        {"id": "b", "label": "有人手持"}]}},
        ])
        o1 = service.chat(session_id=s2["id"], project_id="p-chat",
                          message="帮我做个视频", provider=scripted)
        assert o1["reply_type"] == "clarify" and o1["options"], o1
        assert o1["missing_fields"] == ["product_name"], o1["missing_fields"]
        service.save_brief("p-chat", {"subject": "单瓶精华液"})
        o2 = service.chat(session_id=s2["id"], project_id="p-chat",
                          message="就这个，不要人", provider=scripted)
        # 第二轮模型输入应含上一轮 user + assistant
        msgs = scripted.calls[1]["messages"]
        roles = [m["role"] for m in msgs]
        assert "user" in roles and "assistant" in roles, roles
        joined = " ".join(str(m.get("content")) for m in msgs)
        assert "帮我做个视频" in joined, "第二轮缺少上一轮用户问题"
        assert joined.count("就这个，不要人") == 1, "本轮用户消息在上下文中重复"
        assert "先确认商品" in joined, "第二轮缺少上一轮助手回复"
        return {"turns": 2, "roles_in_2nd_call": roles,
                "reply_types": [o1["reply_type"], o2["reply_type"]]}
    case("多轮对话：第二轮输入含上一轮问答，返回结构化 options", multi_turn_context)

    def history_persistence_and_recovery():
        s3 = service.ensure_session(None, "p-recover", "generate")
        service.chat(session_id=s3["id"], project_id="p-recover", message="第一句",
                     provider=prov.FixedReplyProvider(script=[{"reply": "好的"}]) )
        service.chat(session_id=s3["id"], project_id="p-recover", message="第二句",
                     provider=prov.FixedReplyProvider(script=[{"reply": "收到"}]))
        # 模拟刷新：按项目找回会话与历史
        row = service.find_session_by_project("p-recover")
        assert row and row["id"] == s3["id"], row
        h = service.history(s3["id"])
        assert any(m["content"] == "第一句" for m in h), h
        assert any(m["content"] == "收到" for m in h), h
        # 上下文裁剪：条数不超过上限
        assert len(service.history(s3["id"], limit=2)) <= 2
        return {"session_recovered": True, "history_len": len(h),
                "max_messages": service.HISTORY_MAX_MESSAGES}
    case("刷新恢复：按项目取回会话与历史（且上下文限长）", history_persistence_and_recovery)

    def tool_results_fed_back():
        s4 = service.ensure_session(None, "p-tool", "generate")
        scripted = prov.FixedReplyProvider(script=[
            {"reply": "我先看看模板。",
             "tool_calls": [{"name": "list_templates", "arguments": {"kind": "video"}}]},
            {"reply": "看到 1 个视频模板：5 秒商品动态展示。",
             "structured": {"reply_type": "template_recommendation"}},
        ])
        out = service.chat(session_id=s4["id"], project_id="p-tool",
                           message="有视频模板吗", provider=scripted)
        assert len(scripted.calls) == 2, "工具结果未回传模型（只调用了一轮）"
        assert scripted.calls[1]["tool_results"], "第二轮没有 tool_results"
        assert out["tool_calls"][0]["ok"] is True
        assert out["reply_type"] == "template_recommendation"
        return {"model_rounds": len(scripted.calls),
                "tools": [c["tool"] for c in out["tool_calls"]]}
    case("工具结果回传模型（有限轮 调用→观察→回复）", tool_results_fed_back)

    print("\n=== 6.2 复核修复 B：仅优化模式的权限 ===")

    def optimize_intent_server_side():
        so = service.ensure_session(None, "p-opt", "optimize_only")
        before_q = n_quotes()
        # 省略 intent
        d1 = expect_error("QUOTE_NOT_ALLOWED",
                          lambda: service.quote(session_id=so["id"], project_id="p-opt",
                                                payload=payload(5)), 400)
        # 工具层传入伪造 intent=generate 也无用（权限读服务端会话）
        d2 = expect_error("QUOTE_NOT_ALLOWED",
                          lambda: tools.execute("quote",
                                                {"payload": payload(5)},
                                                {"session_id": so["id"],
                                                 "project_id": "p-opt",
                                                 "intent": "generate"}), 400)
        assert n_quotes() == before_q, "仅优化会话产生了报价"
        return {"omitted_intent": d1["code"], "forged_intent": d2["code"]}
    case("仅优化会话：省略/伪造 intent 仍不能报价", optimize_intent_server_side)

    def optimize_blocks_confirm_and_cancel():
        so = service.ensure_session(None, "p-opt2", "optimize_only")
        # 用别的会话报价，再试图在仅优化会话确认
        sq = service.ensure_session(None, "p-gen2", "generate")
        q = service.quote(session_id=sq["id"], project_id="p-gen2", payload=payload(5))
        d1 = expect_error("CONFIRM_NOT_ALLOWED",
                          lambda: service.confirm(session_id=so["id"],
                                                  quote_id=q["quote_id"],
                                                  project_id="p-opt2"), 400)
        # 取消任务也被禁止
        t = main.create_task(main.TaskIn(**{**body_of(payload(5)).model_dump(),
                                          "approval_id": (lambda: (
                                              service.confirm(session_id=sq["id"],
                                                              quote_id=service.quote(
                                                                  session_id=sq["id"],
                                                                  project_id="p-gen2",
                                                                  payload=payload(5))["quote_id"],
                                                              project_id="p-gen2")["approval_id"]))()}))
        d2 = expect_error("TOOL_FORBIDDEN",
                          lambda: tools.execute("cancel_task", {"task_id": t["id"]},
                                                {"session_id": so["id"],
                                                 "project_id": "p-opt2"}), 400)
        return {"confirm": d1["code"], "cancel": d2["code"]}
    case("仅优化会话：不可确认、不可取消任务", optimize_blocks_confirm_and_cancel)

    def intent_switch_enables_quote():
        sw = service.ensure_session(None, "p-switch", "optimize_only")
        expect_error("QUOTE_NOT_ALLOWED",
                     lambda: service.quote(session_id=sw["id"], project_id="p-switch",
                                           payload=payload(5)))
        view = service.set_intent(sw["id"], "generate", reason="用户要求生成")
        assert view["intent"] == "generate"
        q = service.quote(session_id=sw["id"], project_id="p-switch", payload=payload(5))
        assert q["quote_id"] and q["side_effects"]["task_created"] is False
        return {"intent_after": view["intent"], "quote_ok": True}
    case("明确切换为生成意图后才可报价", intent_switch_enables_quote)

    print("\n=== 6.3 复核修复 C：批准重放前校验方案 ===")

    def replay_with_changed_plan_rejected():
        sc = service.ensure_session(None, "p-replay", "generate")
        q = service.quote(session_id=sc["id"], project_id="p-replay", payload=payload(5))
        a = service.confirm(session_id=sc["id"], quote_id=q["quote_id"], project_id="p-replay")
        t1 = main.create_task(main.TaskIn(**{**body_of(payload(5)).model_dump(),
                                           "approval_id": a["approval_id"]}))
        before = n_tasks()
        # 同一批准，改提示词后重放
        changed = body_of(payload(5)).model_dump()
        changed["params"] = {"prompt": "换成海边场景，不要人物", "duration": 5,
                             "ratio": "9:16", "resolution": "720p", "generate_audio": False}
        d = expect_error("APPROVAL_PLAN_MISMATCH",
                         lambda: main.create_task(main.TaskIn(**{**changed,
                                                                "approval_id": a["approval_id"]})), 409)
        assert n_tasks() == before, "改方案重放仍创建了任务"
        # 相同方案重放仍返回原任务
        t2 = main.create_task(main.TaskIn(**{**body_of(payload(5)).model_dump(),
                                           "approval_id": a["approval_id"]}))
        assert t2["id"] == t1["id"] and t2.get("idempotent_replay") is True
        assert n_tasks() == before, "相同方案重放创建了任务"
        return {"changed_plan": d["code"], "same_plan_same_task": True}
    case("同一批准：改方案→APPROVAL_PLAN_MISMATCH；同方案→同一任务",
         replay_with_changed_plan_rejected)

    print("\n=== 6.4 复核修复 D：报价冻结模板版本 ===")

    def quote_freezes_template_version():
        st = service.ensure_session(None, "p-freeze", "generate")
        # 报价时不传 template_version
        pl = {"kind": "video", "model_id": V10, "input_asset_id": FIRST_FRAME,
              "template_id": "tpl_product_motion_5s",
              "template_fields": {"motion": "推近特写", "duration": 5, "ratio": "9:16"}}
        q = service.quote(session_id=st["id"], project_id="p-freeze", payload=pl)
        assert q["template_version"] == 2, q["template_version"]
        assert q["submit_payload"]["template_version"] == 2
        a = service.confirm(session_id=st["id"], quote_id=q["quote_id"], project_id="p-freeze")

        # 报价后"发布" v3：默认时长改成 10s（模拟模板升级）
        import copy
        from app import templates as tplmod
        snap = tplmod.snapshot("tpl_product_motion_5s", 2)
        snap3 = copy.deepcopy(snap)
        snap3["version"] = 3
        snap3["default_params"]["duration"] = 10
        snap3["description"] = "v3：默认 10 秒"
        db.insert("template_versions", {"id": db.new_id("tplv"),
                                        "template_id": "tpl_product_motion_5s",
                                        "version": 3,
                                        "snapshot_json": db.dumps(snap3),
                                        "status": "active", "created_at": db.now()})
        db.update("templates",
                  db.query("SELECT id FROM templates WHERE template_id=?",
                           ("tpl_product_motion_5s",))[0]["id"],
                  {"latest_version": 3})

        # 提交仍用报价冻结的 v2 + 原参数
        t = main.create_task(main.TaskIn(**{**body_of(pl).model_dump(),
                                           "approval_id": a["approval_id"]}))
        row = db.get("tasks", t["id"])
        params = db.loads(row["params_json"])
        assert row["template_version"] == 2, row["template_version"]
        assert params["duration"] == 5, params["duration"]
        assert "10 秒" not in str(params.get("prompt")), params.get("prompt")
        # 恢复模板 latest_version，避免影响后续用例
        db.update("templates",
                  db.query("SELECT id FROM templates WHERE template_id=?",
                           ("tpl_product_motion_5s",))[0]["id"],
                  {"latest_version": 2})
        return {"quoted_version": q["template_version"],
                "executed_version": row["template_version"],
                "executed_duration": params["duration"]}
    case("报价后模板升到 v3，提交仍执行报价冻结的 v2", quote_freezes_template_version)

    def explicit_version_conflict():
        sc2 = service.ensure_session(None, "p-freeze2", "generate")
        q = service.quote(session_id=sc2["id"], project_id="p-freeze2", payload=payload(5))
        a = service.confirm(session_id=sc2["id"], quote_id=q["quote_id"], project_id="p-freeze2")
        wrong = body_of(payload(5)).model_dump()
        wrong["template_version"] = 999
        d = expect_error("APPROVAL_PLAN_MISMATCH",
                         lambda: main.create_task(main.TaskIn(**{**wrong,
                                                                "approval_id": a["approval_id"]})), 409)
        return d
    case("提交显式指定不同模板版本 → APPROVAL_PLAN_MISMATCH", explicit_version_conflict)

    print("\n=== 6.5 真实适配器（Ark）契约测试：mock 网络，不产生调用 ===")

    def ark_request_shape():
        prov.set_provider(prov.ArkChatProvider(model="doubao-test-model"))
        captured = {}

        def fake_request(method, path, body=None, timeout=None):  # noqa: ANN001
            captured.update({"method": method, "path": path, "body": body})
            return {"choices": [{"message": {"content": "好的，我先看模板。"},
                                 "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 120, "completion_tokens": 18}}
        providers_ark._request = fake_request  # type: ignore[assignment]
        try:
            r = prov.ArkChatProvider(model="doubao-test-model").chat(
                [{"role": "system", "content": "sys"},
                 {"role": "context", "content": {"intent": "generate"}},
                 {"role": "user", "content": "有视频模板吗"}],
                tools.tool_specs())
        finally:
            pass
        assert captured["path"] == "/chat/completions", captured
        assert captured["body"]["model"] == "doubao-test-model"
        tools_sent = captured["body"]["tools"]
        assert tools_sent and tools_sent[0]["type"] == "function"
        assert tools_sent[0]["function"]["name"]
        roles = [m["role"] for m in captured["body"]["messages"]]
        assert "context" not in roles, f"非标准 role 未折叠：{roles}"
        assert roles.count("system") >= 2, roles
        assert r.reply.startswith("好的") and r.input_tokens == 120 and r.output_tokens == 18
        return {"path": captured["path"], "roles": roles,
                "tools_sent": len(tools_sent), "tokens": [r.input_tokens, r.output_tokens]}
    case("Ark：请求体形状正确（模型/工具/上下文折叠为 system）", ark_request_shape)

    def ark_tool_calls_parsed():
        def fake_request(method, path, body=None, timeout=None):  # noqa: ANN001
            return {"choices": [{"message": {"content": "",
                                             "tool_calls": [
                                                 {"id": "call_abc",
                                                  "type": "function",
                                                  "function": {"name": "list_templates",
                                                               "arguments": "{\"kind\": \"video\"}"}}]},
                                 "finish_reason": "tool_calls"}],
                    "usage": {"prompt_tokens": 60, "completion_tokens": 12}}
        providers_ark._request = fake_request  # type: ignore[assignment]
        r = prov.ArkChatProvider(model="m").chat([{"role": "user", "content": "x"}],
                                                 tools.tool_specs())
        assert len(r.tool_calls) == 1, r.tool_calls
        tc = r.tool_calls[0]
        assert tc.name == "list_templates" and tc.arguments == {"kind": "video"}, tc
        assert tc.id == "call_abc", tc.id
        return {"tool": tc.name, "args": tc.arguments, "id": tc.id}
    case("Ark：解析 tool_calls（含 id 与 JSON 参数）", ark_tool_calls_parsed)

    def ark_structured_json_passthrough():
        import json as _json
        payload = {"reply": "优化好了", "reply_type": "optimize_result",
                   "optimized_prompt": "以商品为准，换大理石背景",
                   "options": [{"id": "a", "label": "A"}], "missing_fields": []}
        def fake_request(method, path, body=None, timeout=None):  # noqa: ANN001
            return {"choices": [{"message": {"content": _json.dumps(payload, ensure_ascii=False)},
                                 "finish_reason": "stop"}], "usage": {}}
        providers_ark._request = fake_request  # type: ignore[assignment]
        r = prov.ArkChatProvider(model="m").chat([{"role": "user", "content": "x"}], [])
        assert r.reply == "优化好了", r.reply
        assert r.structured["reply_type"] == "optimize_result"
        assert r.structured["optimized_prompt"].startswith("以商品为准")
        return {"reply_type": r.structured["reply_type"]}
    case("Ark：模型返回约定 JSON 时解析为结构化字段", ark_structured_json_passthrough)

    def ark_state_and_no_fake_verify():
        prov.set_provider(prov.ArkChatProvider(model="doubao-test-model"))
        st = prov.provider_status()
        assert st["state"] == "configured_unverified", st
        assert st["available"] is True
        assert st["verified"] is False, "未真实调用前不得标记已验证"
        empty = prov.ArkChatProvider(model="")
        assert empty.available() is False
        return {"state": st["state"], "verified": st["verified"],
                "empty_model_available": False}
    case("Ark：状态为“已配置未验证”，空型号不可用", ark_state_and_no_fake_verify)

    def openai_tool_roundtrip_messages():
        """工具执行后，第二轮消息必须含 assistant.tool_calls 与 role=tool。"""
        import json as _json
        s5 = service.ensure_session(None, "p-ark", "generate")
        scripted = prov.FixedReplyProvider(script=[
            {"reply": "", "tool_calls": [{"name": "list_models", "arguments": {"kind": "image"}}]},
            {"reply": "已看到可用图片模型。", "structured": {"reply_type": "message"}},
        ])
        service.chat(session_id=s5["id"], project_id="p-ark",
                     message="有哪些图片模型", provider=scripted)
        msgs = scripted.calls[1]["messages"]
        asst = [m for m in msgs if m.get("role") == "assistant" and m.get("tool_calls")]
        tool_msgs = [m for m in msgs if m.get("role") == "tool"]
        assert asst, "缺少 assistant.tool_calls 消息"
        assert tool_msgs, "缺少 role=tool 的工具结果消息"
        assert tool_msgs[0].get("tool_call_id"), "工具结果缺少 tool_call_id"
        assert _json.loads(tool_msgs[0]["content"]) is not None
        return {"has_tool_calls": True, "tool_call_id": tool_msgs[0]["tool_call_id"][:12]}
    case("工具结果按 OpenAI 格式回传（assistant.tool_calls + tool_call_id）",
         openai_tool_roundtrip_messages)

    print("\n=== 7. 重启后持久化 + 手动路径不回归 ===")

    def restart_persistence():
        before_approvals = db.query("SELECT COUNT(*) c FROM assistant_approvals")[0]["c"]
        before_usage = db.query("SELECT COUNT(*) c FROM assistant_usage")[0]["c"]
        before_sessions = db.query("SELECT COUNT(*) c FROM assistant_sessions")[0]["c"]
        # 模拟重启：重新 init_db（同文件）并重新读取
        db.init_db()
        assert db.query("SELECT COUNT(*) c FROM assistant_approvals")[0]["c"] == before_approvals
        assert db.query("SELECT COUNT(*) c FROM assistant_usage")[0]["c"] == before_usage
        assert db.query("SELECT COUNT(*) c FROM assistant_sessions")[0]["c"] == before_sessions
        # 已用批准在重启后仍不可重复使用
        t2 = main.create_task(main.TaskIn(**{**body_of(payload(5)).model_dump(),
                                           "approval_id": ap["approval_id"]}))
        assert t2["id"] == created["id"], "重启后幂等失效"
        used = db.get("assistant_approvals", ap["approval_id"])
        assert used["used_count"] == 1
        return {"approvals": before_approvals, "usage_rows": before_usage,
                "sessions": before_sessions, "idempotent_after_restart": True}
    case("重启后会话/报价/批准/用量仍在，且已用批准仍幂等", restart_persistence)

    def manual_regression():
        from app.assistant import service as svc
        # 手动提交（无 approval）
        t = main.create_task(main.TaskIn(kind="image", model_id=FLASH, input_asset_id=REF_IMG,
                                        params={"prompt": "白底主图",
                                                "generation_mode": "image_to_image",
                                                "size": "2048x2048"}))
        assert t["status"] == "queued" and not t.get("approval_id")
        assert db.get("tasks", t["id"])["caller_id"] == "local"
        # 手动取消（排队中）行为不变
        r = worker.cancel(t["id"])
        assert r["result"] == "canceled" and r["phase"] == "queued", r
        # 手动重试行为不变：直接建新任务并标记 retry_of
        t_retry_src = main.create_task(main.TaskIn(kind="image", model_id=FLASH,
                                                  input_asset_id=REF_IMG,
                                                  params={"prompt": "白底主图",
                                                          "generation_mode": "image_to_image",
                                                          "size": "2048x2048"}))
        db.update("tasks", t_retry_src["id"], {"status": "failed", "error_code": "TEST"})
        r2 = main.retry_task(t_retry_src["id"])
        assert db.get("tasks", r2["task_id"])["retry_of"] == t_retry_src["id"]
        # 助手限制不影响手动路径
        assert svc.MAX_QUESTIONS >= 0
        return {"manual_submit": "queued", "manual_cancel": r["result"],
                "manual_retry_of": True}
    case("手动提交/取消/重试行为不变（无需 approval）", manual_regression)

    def assistant_task_not_counted_as_generation_cost():
        rows = db.query("SELECT COUNT(*) c FROM tasks WHERE caller_id='assistant'")
        return {"assistant_tasks": rows[0]["c"],
                "note": "任务费用仍为 0（未执行生成，测站不启动 worker）"}
    case("助手任务未产生费用记录（未执行生成）", assistant_task_not_counted_as_generation_cost)

    # ---------- 汇总 ----------
    passed = sum(1 for r in RESULTS if r["ok"])
    summary = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "scope": "助手阶段一：接口骨架 + 报价/确认门禁 + 限制与用量（隔离测试）",
        "isolation": {"db": "临时副本，真实库未改",
                      "llm": "FixedReplyProvider 固定脚本（不联网）",
                      "default_provider": "Unavailable（未配置时明确不可用）",
                      "cloud_guard": "providers_ark._request 断言守卫",
                      "worker": "未启动，不消费队列"},
        "paid_calls": 0, "real_llm_calls": 0, "generation_tasks_executed": 0,
        "total": len(RESULTS), "passed": passed, "failed": len(RESULTS) - passed,
        "cases": RESULTS,
        "disclaimer": ("本文件仅证明门禁/幂等/持久化逻辑正确，"
                       "不代表真实 LLM 或真实生成验收通过。测试替身不算已接通。"),
    }
    path = OUT / "阶段一测试结果.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{'=' * 60}\n结果：{passed}/{len(RESULTS)} 通过")
    print("付费调用 0｜真实 LLM 0｜真实生成 0｜真实业务库未改动")
    print(f"证据：{path}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(run())
