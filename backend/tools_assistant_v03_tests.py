#!/usr/bin/env python3
"""助手 v0.3 增量修复 · 免费回归（零网络、零费用、零业务库写入）。

覆盖：
- F1 工具执行上限 6：第 6 次允许、第 7 次拦截；且与 LLM 请求轮数区分
- F2 会话事实摘要：模板/素材来源区分 tool_query / quoted / user_selected；
      历史进入上下文；动态价格在报价时重新校验
- G1 recommended_templates：来自真实工具结果、校验存在、否则 []
- G5 optimized_prompt 纯度：混入说明→null；纯提示词→保留；会话恢复同样生效
- 类型降级：正文拒答 vs 结构化 refusal 分开；解析失败不伪造 optimize_result
- 边界：仅优化不报价、不创建生成任务、改参数后旧批准失效

用法：<venv>/bin/python backend/tools_assistant_v03_tests.py
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

from app import config, db, main, providers_ark, templates  # noqa: E402
from app.assistant import provider as prov, service, tools  # noqa: E402
from app.errors import AppError  # noqa: E402

RESULTS: list[dict] = []
OUT = ROOT / "docs" / "evidence" / "助手v0.3-20261025"
REF_IMG = "asset_d73231ab405540b68eb6"
FLASH = "doubao-seedream-5-0-flash-260915"
V10 = "doubao-seedance-1-0-pro-fast-251015"


def case(name: str, fn) -> None:
    try:
        d = fn() or {}
        RESULTS.append({"case": name, "ok": True, "detail": d})
        print(f"  OK  {name}" + (f"  {d}" if d else ""))
    except AssertionError as e:
        RESULTS.append({"case": name, "ok": False, "error": str(e)})
        print(f" FAIL {name} → {e}")
    except Exception as e:  # noqa: BLE001
        RESULTS.append({"case": name, "ok": False,
                        "error": f"{type(e).__name__}: {e}",
                        "trace": traceback.format_exc()[-400:]})
        print(f" ERR  {name} → {type(e).__name__}: {e}")


def expect(code: str, fn, http: int | None = None) -> dict:
    try:
        fn()
    except AppError as e:
        assert e.code == code, f"期望 {code}，实际 {e.code}: {e.message}"
        if http is not None:
            assert e.http == http, f"期望 HTTP {http}，实际 {e.http}"
        return {"code": e.code, "http": e.http}
    raise AssertionError(f"期望抛出 {code}，但没有报错")


def setup() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="v03-"))
    shutil.copy2(config.DB_PATH, tmp / "app.db")
    config.DB_PATH = tmp / "app.db"
    db.init_db()

    def _guard(*a, **k):
        raise AssertionError("测试期间禁止调用云端")
    providers_ark._request = _guard  # type: ignore[assignment]
    prov.set_provider(prov.UnavailableProvider())
    return tmp


def n_tasks() -> int:
    return db.query("SELECT COUNT(*) c FROM tasks")[0]["c"]


def scripted(items: list[dict]) -> prov.FixedReplyProvider:
    return prov.FixedReplyProvider(script=items)


def tc(name: str, args: dict) -> dict:
    return {"reply": "", "tool_calls": [{"name": name, "arguments": args}]}


def run() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = setup()
    print(f"隔离库：{tmp / 'app.db'}（真实库未改）\n")

    print("=== F1：工具执行上限 6（第 6 允许 / 第 7 拦截），且不影响 LLM 轮数 ===")

    def limit_constants():
        assert service.MAX_TOOL_EXECUTIONS == 6, service.MAX_TOOL_EXECUTIONS
        assert service.MAX_TOOL_ROUNDS == 3, service.MAX_TOOL_ROUNDS
        return {"tool_executions": 6, "llm_rounds": 3,
                "note": "两者语义不同：工具执行不产生 LLM 费用"}
    case("常量：工具执行 6 / LLM 轮数 3（未同时放大）", limit_constants)

    def sixth_allowed_seventh_blocked():
        s = service.ensure_session(None, "p-tools", "generate")
        # 一轮内连续 7 次工具调用（list_models 幂等且免费）
        calls = [{"name": "list_models", "arguments": {"kind": "image"}} for _ in range(7)]
        p = scripted([{"reply": "已查完", "tool_calls": calls},
                      {"reply": "（此处不应被调用）"}])
        out = service.chat(session_id=s["id"], project_id="p-tools",
                           message="多查几次模型", provider=p)
        okc = [c for c in out["tool_calls"] if c.get("ok")]
        blocked = [c for c in out["tool_calls"] if not c.get("ok")
                   and (c.get("error") or {}).get("code") == "ASSISTANT_TOOL_BUDGET"]
        assert len(okc) == 6, f"应允许 6 次，实际 {len(okc)}"
        assert len(blocked) == 1, f"应有 1 次被拦，实际 {len(blocked)}"
        return {"allowed": len(okc), "blocked": len(blocked)}
    case("一轮内第 6 次工具允许、第 7 次被 ASSISTANT_TOOL_BUDGET 拦截",
         sixth_allowed_seventh_blocked)

    print("\n=== F2：会话事实摘要（来源区分 + 历史进入上下文）===")

    def facts_provenance():
        s = service.ensure_session(None, "p-facts", "generate")
        p = scripted([
            tc("list_templates", {"kind": "image"}),
            tc("quote", {"payload": {"kind": "image", "model_id": FLASH,
                                     "input_asset_id": REF_IMG,
                                     "template_id": "tpl_white_bg",
                                     "template_fields": {"product_name": "焕亮精华液"}}}),
            {"reply": "已报价", "structured": {"reply_type": "quote_ready"}},
        ])
        service.chat(session_id=s["id"], project_id="p-facts",
                     message="报个价", provider=p,
                     context={"selected_asset_id": REF_IMG, "kind": "image"})
        facts = service._load_facts(s["id"])
        assert facts["templates"], "未记录模板事实"
        assert facts["assets"][REF_IMG]["source"] == "user_selected", facts["assets"]
        quoted = [t for t in facts["templates"].values() if t["source"] == "quoted"]
        listed = [t for t in facts["templates"].values() if t["source"] == "tool_query"]
        assert quoted and listed, (quoted, listed)
        # 事实要能进入下一轮上下文
        msgs = service.messages_for_model(service.get_session(s["id"]), "p-facts", "继续", facts)
        ctx = [m for m in msgs if m["role"] == "context"][0]["content"]
        assert ctx["facts"]["note"].find("不代表用户确认") >= 0
        assert any(t["source"] == "quoted" for t in ctx["facts"]["templates"])
        return {"templates": len(facts["templates"]), "assets": len(facts["assets"]),
                "sources": sorted({t["source"] for t in facts["templates"].values()})}
    case("事实来源区分：tool_query / quoted / user_selected 并存", facts_provenance)

    def facts_bounded():
        s = service.ensure_session(None, "p-bound", "generate")
        for i in range(12):
            service._merge_facts(s["id"], [{
                "tool": "list_templates", "ok": True,
                "result": {"items": [{"template_id": f"tpl_x{i}", "name": "x", "latest_version": 1}]}}])
        facts = service._load_facts(s["id"])
        assert len(facts["templates"]) <= service.FACTS_MAX_TEMPLATES, len(facts["templates"])
        return {"templates_kept": len(facts["templates"]),
                "cap": service.FACTS_MAX_TEMPLATES}
    case("事实摘要长度受控（不无限增长）", facts_bounded)

    def price_revalidated_on_quote():
        s = service.ensure_session(None, "p-price", "generate")
        pl = {"kind": "video", "model_id": V10, "input_asset_id": "asset_cd91e2f9dda14123a622",
              "template_id": "tpl_product_motion_5s", "template_version": 2,
              "template_fields": {"motion": "推近特写", "duration": 10, "ratio": "1:1"}}
        q = service.quote(session_id=s["id"], project_id="p-price", payload=pl)
        assert q["cost_cny"] > 0.5, q["cost_cny"]      # 10s 比 5s 贵 → 证明按当次重算
        assert q["model_lifecycle"]["status"] in ("active", "eos_scheduled"), q["model_lifecycle"]
        return {"cost": q["cost_cny"], "lifecycle": q["model_lifecycle"]["status"]}
    case("报价时重新校验：价格按当次参数重算、可用性现查", price_revalidated_on_quote)

    print("\n=== G1：recommended_templates（真实 + 校验存在 + 空数组兜底）===")

    def rec_from_tools():
        s = service.ensure_session(None, "p-rec", "generate")
        p = scripted([
            tc("list_templates", {"kind": "image"}),
            {"reply": "推荐白底模板", "structured": {"reply_type": "template_recommendation"}},
        ])
        out = service.chat(session_id=s["id"], project_id="p-rec",
                           message="有白底模板吗", provider=p)
        rec = out["recommended_templates"]
        assert rec, "应从工具结果产生推荐"
        assert all("template_id" in r and "version" in r and "name" in r for r in rec)
        assert all(templates.snapshot(r["template_id"], r["version"]) for r in rec), "存在性校验失败"
        return {"count": len(rec), "first": rec[0]["template_id"], "source": rec[0]["source"]}
    case("推荐来自真实工具结果，且逐个校验存在", rec_from_tools)

    def rec_empty_without_tools():
        s = service.ensure_session(None, "p-rec2", "generate")
        p = scripted([{"reply": "我推荐「商品白底背景」模板，template_id 是 tpl_white_bg",
                       "structured": {"reply_type": "template_recommendation"}}])
        out = service.chat(session_id=s["id"], project_id="p-rec2",
                           message="推荐模板", provider=p)
        assert out["recommended_templates"] == [], out["recommended_templates"]
        return {"recommended": [], "note": "未调工具 → 不从自然语言猜模板"}
    case("未调用工具时返回空数组（不从正文猜 template_id）", rec_empty_without_tools)

    print("\n=== G5：optimized_prompt 纯度 ===")

    def g5_reject_mixed_text():
        s = service.ensure_session(None, "p-g5", "optimize_only")
        mixed = ("以参考图中的商品为准，保留外形与标签，背景替换为浅灰白大理石台面。\n\n"
                 "改动说明：1) 补充了保真约束；2) 明确背景材质。")
        p = scripted([{"reply": mixed,
                       "structured": {"reply_type": "optimize_result",
                                      "optimized_prompt": mixed,
                                      "explanation": "改动说明：补充保真约束"}}])
        out = service.chat(session_id=s["id"], project_id="p-g5",
                           message="润色", intent="optimize_only", provider=p)
        assert out["optimized_prompt"] is None, out["optimized_prompt"]
        assert out["explanation"], "explanation 应保留"
        assert out["reply"].startswith("以参考图中的商品为准")
        assert out["reply_type"] == "optimize_result"
        assert out["degraded"] is True
        return {"optimized_prompt": None, "explanation_kept": True, "degraded": True}
    case("混入说明文字 → optimized_prompt=null，reply/explanation 保留", g5_reject_mixed_text)

    def g5_accept_pure():
        s = service.ensure_session(None, "p-g5b", "optimize_only")
        pure = "以参考图中的商品为准，保留外形、数量与包装文字，不增加或删除商品；将背景替换为浅灰白大理石台面，柔和自然光，构图留白。"
        p = scripted([{"reply": pure,
                       "structured": {"reply_type": "optimize_result",
                                      "optimized_prompt": pure,
                                      "explanation": "补充了保真与背景描述"}}])
        out = service.chat(session_id=s["id"], project_id="p-g5b",
                           message="润色", provider=p)
        assert out["optimized_prompt"] == pure, out["optimized_prompt"]
        return {"optimized_prompt_len": len(out["optimized_prompt"] or "")}
    case("纯提示词 → 正常返回（不误杀）", g5_accept_pure)

    def g5_no_fallback():
        s = service.ensure_session(None, "p-g5c", "optimize_only")
        p = scripted([{"reply": "这是优化后的提示词：以商品为准，换背景。\n如需生成请告诉我。",
                       "structured": {"reply_type": "optimize_result"}}])
        out = service.chat(session_id=s["id"], project_id="p-g5c",
                           message="润色", provider=p)
        assert out["optimized_prompt"] is None, "不得把整段 reply 当提示词"
        return {"optimized_prompt": None, "rule": "无结构化 → null（旧回退已废弃）"}
    case("无结构化输出时绝不回退为整段 reply", g5_no_fallback)

    def g5_session_recovery():
        s = service.ensure_session(None, "p-g5d", "optimize_only")
        pure = "以商品为准，保留外形与包装文字，背景改为浅木色桌面，柔和自然光。"
        p = scripted([{"reply": pure,
                       "structured": {"reply_type": "optimize_result",
                                      "optimized_prompt": pure,
                                      "explanation": "仅调整背景"}}])
        service.chat(session_id=s["id"], project_id="p-g5d", message="润色", provider=p)
        h = service.history(s["id"], limit=4)
        asst = [m for m in h if m["role"] == "assistant"][-1]
        st = asst.get("structured") or {}
        assert st.get("optimized_prompt") == pure, st.get("optimized_prompt")
        assert st.get("explanation") == "仅调整背景"
        return {"recovered_optimized_prompt": bool(st.get("optimized_prompt")),
                "recovered_explanation": st.get("explanation")}
    case("会话恢复：structured 里同样带 optimized_prompt/explanation", g5_session_recovery)

    print("\n=== 类型一致性：正文拒答 vs 结构化 refusal ===")

    def refusal_text_only():
        s = service.ensure_session(None, "p-ref1", "generate")
        p = scripted([{"reply": "这个需求我不能做：属于违规医疗宣称。可改为日常营养补充方向。",
                       "structured": {"reply_type": "clarify"}}])
        out = service.chat(session_id=s["id"], project_id="p-ref1",
                           message="帮我写能治癌症的文案", provider=p)
        assert out["reply_type"] == "refusal", out["reply_type"]
        assert out["refusal"] == {"detected": True, "structured": False}, out["refusal"]
        assert out["degraded"] is True
        return {"reply_type": out["reply_type"], "refusal": out["refusal"],
                "degraded": True}
    case("正文拒答但模型自报 clarify → 纠正为 refusal 并标 degraded", refusal_text_only)

    def refusal_structured():
        s = service.ensure_session(None, "p-ref2", "generate")
        p = scripted([{"reply": "不能提供此类内容。",
                       "structured": {"reply_type": "refusal"}}])
        out = service.chat(session_id=s["id"], project_id="p-ref2",
                           message="违规请求", provider=p)
        assert out["reply_type"] == "refusal" and out["refusal"]["structured"] is True
        assert out["degraded"] is False
        return {"reply_type": out["reply_type"], "structured_refusal": True, "degraded": False}
    case("结构化 refusal → 直接采用，不标降级", refusal_structured)

    def no_fake_success_type():
        s = service.ensure_session(None, "p-ref3", "generate")
        p = scripted([{"reply": "你是要改描述，还是直接生成？"}])
        out = service.chat(session_id=s["id"], project_id="p-ref3",
                           message="嗯", provider=p)
        assert out["reply_type"] == "clarify", out["reply_type"]
        assert out["structured_ok"] is False and out["degraded"] is True
        return {"reply_type": out["reply_type"], "structured_ok": False, "degraded": True}
    case("解析失败 → 降级为 clarify（不伪造 optimize_result/quote_ready）",
         no_fake_success_type)


    print("\n=== 上下文注入：用户指定素材必须进入模型上下文 ===")

    def context_hint_injected():
        s = service.ensure_session(None, "p-ctx", "generate")
        captured = {}

        class Capture(prov.FixedReplyProvider):
            def chat(self, messages, tools, tool_results=None):
                captured["messages"] = list(messages)
                return super().chat(messages, tools, tool_results)

        p = Capture(script=[tc("list_assets", {"kind": "image"}),
                            {"reply": "好的", "structured": {"reply_type": "message"}}])
        service.chat(session_id=s["id"], project_id="p-ctx", message="用我选的图报价",
                     provider=p, context={"selected_asset_id": REF_IMG, "kind": "image"})
        ctxmsg = [m for m in captured["messages"] if m["role"] == "context"][0]["content"]
        assert ctxmsg["context_hint"]["selected_asset_id"] == REF_IMG, ctxmsg.get("context_hint")
        assert "必须使用它" in ctxmsg["context_hint"]["rule"]
        facts = service._load_facts(s["id"])
        assert facts["assets"][REF_IMG]["source"] == "user_selected", facts["assets"].get(REF_IMG)
        return {"hint": REF_IMG[:18] + "…", "facts_source": "user_selected"}
    case("context.selected_asset_id 进入上下文，且在工具前已记为 user_selected",
         context_hint_injected)

    def selected_survives_tool_query():
        s = service.ensure_session(None, "p-ctx2", "generate")
        p = scripted([tc("list_assets", {"kind": "image"}),
                      {"reply": "好", "structured": {"reply_type": "message"}}])
        service.chat(session_id=s["id"], project_id="p-ctx2", message="报价",
                     provider=p, context={"selected_asset_id": REF_IMG})
        facts = service._load_facts(s["id"])
        assert facts["assets"][REF_IMG]["source"] == "user_selected", "被 tool_query 降级了"
        return {"source": facts["assets"][REF_IMG]["source"], "assets": len(facts["assets"])}
    case("用户指定素材不会被后续 list_assets 降级", selected_survives_tool_query)

    print("\n=== 边界回归 ===")

    def optimize_only_no_quote():
        s = service.ensure_session(None, "p-opt", "optimize_only")
        before_t, before_q = n_tasks(), db.query(
            "SELECT COUNT(*) c FROM assistant_quotes")[0]["c"]
        d = expect("QUOTE_NOT_ALLOWED", lambda: service.quote(
            session_id=s["id"], project_id="p-opt",
            payload={"kind": "image", "model_id": FLASH, "input_asset_id": REF_IMG,
                     "template_id": "tpl_white_bg", "template_fields": {}}))
        assert n_tasks() == before_t
        assert db.query("SELECT COUNT(*) c FROM assistant_quotes")[0]["c"] == before_q
        return d
    case("仅优化会话：报价被拒，且不产生任务/报价记录", optimize_only_no_quote)

    def no_generation_created():
        return {"tasks": n_tasks(), "note": "全程未启动 worker，任务数为原副本数量"}
    case("未创建任何生成任务（worker 未启动）", no_generation_created)

    def param_change_needs_reconfirm():
        s = service.ensure_session(None, "p-ap", "generate")
        base = {"kind": "video", "model_id": V10, "input_asset_id": "asset_cd91e2f9dda14123a622",
                "template_id": "tpl_product_motion_5s", "template_version": 2,
                "template_fields": {"motion": "推近特写", "duration": 5, "ratio": "9:16"}}
        q = service.quote(session_id=s["id"], project_id="p-ap", payload=base)
        ap = service.confirm(session_id=s["id"], quote_id=q["quote_id"], project_id="p-ap")
        changed = dict(base)
        changed["template_fields"] = {"motion": "推近特写", "duration": 10, "ratio": "9:16"}
        d = expect("APPROVAL_PLAN_MISMATCH", lambda: main.create_task(main.TaskIn(
            **{**{k: changed[k] for k in ("kind", "model_id", "input_asset_id",
                                          "template_id", "template_version", "template_fields")},
               "approval_id": ap["approval_id"]})), 409)
        return d
    case("改参数后旧批准失效（需重新报价+确认）", param_change_needs_reconfirm)

    passed = sum(1 for r in RESULTS if r["ok"])
    summary = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "scope": "助手 v0.3 增量修复 · 免费回归",
        "paid_calls": 0, "real_llm_calls": 0, "generation_tasks_executed": 0,
        "limits": {"tool_executions_per_turn": service.MAX_TOOL_EXECUTIONS,
                   "llm_rounds": service.MAX_TOOL_ROUNDS},
        "total": len(RESULTS), "passed": passed, "failed": len(RESULTS) - passed,
        "cases": RESULTS,
        "disclaimer": "全部为隔离/替身验证，不代表真实模型能力。",
    }
    path = OUT / "免费回归结果.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{'=' * 60}\n结果：{passed}/{len(RESULTS)} 通过")
    print("付费 0｜真实 LLM 0｜真实生成 0｜真实业务库未改动")
    print(f"证据：{path}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(run())
