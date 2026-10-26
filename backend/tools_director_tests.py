#!/usr/bin/env python3
"""S2 修复 + 渐进式导演台：确认持久化 / 版本并发 / 就绪度 / 助手上下文（免费）。

覆盖：
1. 确认持久化：confirmed_at 存在 ≠ 当前有效；确认绑定版本
2. brief 变更 → 下游确认 invalidated 并持久化原因（历史记录保留）
3. 版本写入并发：事务内分配版本，base_version 冲突 409
4. 就绪度只读：current stage / allowed_actions / warnings；不调用模型
5. 助手上下文：携带 stage/facts/allowed_actions，不重复问已知项
6. 采用草案：只写库，llm_called=false

用法：<venv>/bin/python backend/tools_director_tests.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import config, db, director, projects  # noqa: E402
from app.assistant import provider as prov, service  # noqa: E402
from app.errors import AppError  # noqa: E402

RESULTS: list[dict] = []
OUT = ROOT / "docs" / "evidence" / "导演台-S2修复-20261026"
BRIEF = {"subject": "单瓶精华液", "quantity": 1, "target_duration_s": 20}


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


def setup() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="director-"))
    config.DB_PATH = tmp / "app.db"
    config.ASSETS = tmp / "assets"
    config.ASSETS.mkdir(parents=True, exist_ok=True)
    db.init_db()
    prov.set_provider(prov.UnavailableProvider())
    return tmp


def expect(code: str, fn, http: int | None = None) -> dict:
    try:
        fn()
    except AppError as e:
        assert e.code == code, f"期望 {code}，实际 {e.code}: {e.message}"
        if http is not None:
            assert e.http == http, f"期望 HTTP {http}，实际 {e.http}"
        return {"code": e.code, "http": e.http}
    raise AssertionError(f"期望抛出 {code}，但没有报错")


def run() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = setup()
    print(f"临时库：{tmp}/app.db（业务库未打开）\n")

    print("=== 1) 确认持久化（不是靠 confirmed_at）===")

    def confirm_persisted():
        pid = projects.create_project("确认持久化", BRIEF)["project_id"]
        s = projects.add_script(pid, {"summary": "v1"}, source="manual")
        out = projects.confirm_script(s["script_id"])
        cfm = out["confirmation"]
        assert cfm["status"] == "active", cfm
        rows = db.query("SELECT * FROM confirmations WHERE object_type='script' AND object_id=?",
                        (s["script_id"],))
        assert len(rows) == 1 and rows[0]["object_version"] == s["version"], rows
        # 读回：当前版本有效
        cur = director.current_confirmation("script", s["script_id"], s["version"])
        assert cur["valid"] is True
        return {"status": cfm["status"], "rows": len(rows), "valid": True}
    case("确认写入 confirmations（绑定版本）", confirm_persisted)

    def new_version_not_inherit():
        pid = projects.create_project("版本不继承", BRIEF)["project_id"]
        v1 = projects.add_script(pid, {"summary": "v1"}, source="manual")
        projects.confirm_script(v1["script_id"])
        v2 = projects.edit_script(v1["script_id"], {"summary": "v2"})
        # v1 仍有历史确认；v2 无确认
        old = director.current_confirmation("script", v1["script_id"], v1["version"])
        new = director.current_confirmation("script", v2["script_id"], v2["version"])
        assert old["valid"] is True, "历史确认不应被删除"
        assert new["valid"] is False and new["reason"] == "never_confirmed", new
        return {"old_valid": True, "new_valid": False, "new_reason": new["reason"]}
    case("新版本不继承确认（confirmed_at 不等于有效）", new_version_not_inherit)

    def brief_change_invalidates_persistently():
        pid = projects.create_project("失效持久化", BRIEF)["project_id"]
        s = projects.add_script(pid, {"summary": "v1"}, source="manual")
        projects.confirm_script(s["script_id"])
        out = projects.update_project(pid, brief={"subject": "两件套装"})
        cur = director.current_confirmation("script", s["script_id"], s["version"])
        assert cur["valid"] is False, cur
        assert cur["reason"] == "upstream_brief_changed", cur
        # 持久化：直接查库
        row = db.query("SELECT * FROM confirmations WHERE object_id=?", (s["script_id"],))[0]
        assert row["status"] == "invalidated" and row["reason"], row
        assert row["invalidated_at"] is not None
        assert "script_confirm" in out["stale"]
        return {"valid": False, "reason": cur["reason"], "stale": out["stale"]}
    case("改 brief → 确认失效**持久化**（含原因与时间）", brief_change_invalidates_persistently)

    print("\n=== 2) 版本写入并发 ===")

    def version_conflict():
        pid = projects.create_project("并发", BRIEF)["project_id"]
        v1 = projects.add_script(pid, {"summary": "v1"}, source="manual")
        v2 = projects.add_script(pid, {"summary": "v2"}, source="manual")
        d = expect("SCRIPT_VERSION_CONFLICT",
                   lambda: projects.edit_script(v1["script_id"], {"summary": "x"},
                                                base_version=1), 409)
        assert v2["version"] == 2
        return d
    case("base_version 过期 → 409 SCRIPT_VERSION_CONFLICT", version_conflict)

    def concurrent_writes_no_duplicate_version():
        pid = projects.create_project("并发写入", BRIEF)["project_id"]
        errors: list[str] = []
        lock = threading.Lock()

        def worker(i: int) -> None:
            try:
                projects.add_script(pid, {"summary": f"c{i}"}, source="manual")
            except Exception as e:  # noqa: BLE001
                with lock:
                    errors.append(f"{type(e).__name__}: {e}")

        ts = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        rows = db.query("SELECT version FROM script_versions WHERE project_id=? ORDER BY version",
                        (pid,))
        versions = [r["version"] for r in rows]
        assert versions == list(range(1, len(versions) + 1)), f"版本不连续：{versions}"
        assert len(set(versions)) == len(versions), "出现重复版本"
        return {"versions": versions, "errors": errors}
    case("5 线程并发写脚本：版本连续且不重复", concurrent_writes_no_duplicate_version)

    print("\n=== 3) 导演台就绪度（不调用模型）===")

    def readiness_progression():
        pid = projects.create_project("就绪度", {"subject": "", "quantity": 1})["project_id"]
        r0 = director.readiness(pid)
        assert r0["current"]["stage"] == "requirements", r0["current"]
        assert "save_brief" in r0["allowed_actions"]
        assert [s["key"] for s in r0["stages"] if s["visible"]] == ["requirements"], \
            [s["key"] for s in r0["stages"] if s["visible"]]

        projects.update_project(pid, brief=BRIEF)
        r1 = director.readiness(pid)
        assert r1["current"]["stage"] == "script"
        assert "generate_script" in r1["allowed_actions"]
        # 只有 requirements + script 可见（后续不预铺）
        visible = [s["key"] for s in r1["stages"] if s["visible"]]
        assert visible == ["requirements", "script"], visible

        s = projects.add_script(pid, {"summary": "v1"}, source="manual")
        r2 = director.readiness(pid)
        assert "confirm_script" in r2["allowed_actions"], r2["allowed_actions"]
        assert r2["current"]["confirmation"]["valid"] is False

        projects.confirm_script(s["script_id"])
        r3 = director.readiness(pid)
        assert r3["current"]["confirmation"]["valid"] is True
        assert "add_shots" in r3["allowed_actions"], r3["allowed_actions"]
        return {"steps": [r0["current"]["stage"], r1["current"]["stage"],
                          r3["current"]["stage"]],
                "visible_after_brief": visible}
    case("就绪度逐步推进：需求→脚本→（确认后）镜头；未到步骤不出现", readiness_progression)

    def readiness_is_read_only():
        pid = projects.create_project("只读", BRIEF)["project_id"]
        s = projects.add_script(pid, {"summary": "v1"}, source="manual")
        projects.confirm_script(s["script_id"])
        before = db.query("SELECT COUNT(*) c FROM tasks")[0]["c"]
        usage_before = db.query("SELECT COUNT(*) c FROM assistant_usage")[0]["c"]
        for _ in range(3):
            director.readiness(pid)
        assert db.query("SELECT COUNT(*) c FROM tasks")[0]["c"] == before
        assert db.query("SELECT COUNT(*) c FROM assistant_usage")[0]["c"] == usage_before, \
            "就绪度查询产生了模型用量"
        return {"tasks": before, "usage_unchanged": True}
    case("打开项目/切换步骤不调用模型、不建任务", readiness_is_read_only)

    print("\n=== 4) 助手阶段上下文与草案采用 ===")

    def assistant_stage_context():
        pid = projects.create_project("助手上下文", BRIEF)["project_id"]
        s = projects.add_script(pid, {"summary": "已确认脚本"}, source="manual")
        projects.confirm_script(s["script_id"])
        captured = {}

        class Capture(prov.FixedReplyProvider):
            def chat(self, messages, tools, tool_results=None):  # noqa: ANN001
                captured["messages"] = list(messages)
                return super().chat(messages, tools, tool_results)

        stub = Capture(script=[{"reply": "好的，进入分镜步骤。"}])
        sess = service.ensure_session(None, pid, "generate")
        service.chat(session_id=sess["id"], project_id=pid,
                     message="下一步做什么", provider=stub)
        ctx = [m for m in captured["messages"] if m["role"] == "context"][0]["content"]
        stage = ctx["stage"]
        assert stage["current"]["stage"] in ("storyboard", "script"), stage["current"]
        assert stage["allowed_actions"], stage["allowed_actions"]
        assert "不要重复询问" in stage["rule"]
        return {"stage": stage["current"]["stage"],
                "actions": stage["allowed_actions"][:3]}
    case("助手上下文含当前阶段/允许动作，并约束不重复询问", assistant_stage_context)

    def adopt_draft_no_llm():
        pid = projects.create_project("采用草案", BRIEF)["project_id"]
        usage_before = db.query("SELECT COUNT(*) c FROM assistant_usage")[0]["c"]
        out = projects.adopt_draft(pid, "script", {"summary": "草案落库"}, base_version=0)
        usage_after = db.query("SELECT COUNT(*) c FROM assistant_usage")[0]["c"]
        assert out["llm_called"] is False
        assert usage_before == usage_after, "采用草案不应产生 LLM 用量"
        assert out["version"] == 1
        # brief 草案同样只写库
        b = projects.adopt_draft(pid, "brief", {"style": "简约"})
        assert b["llm_called"] is False and b["brief"]["style"] == "简约"
        assert b["brief"]["subject"] == "单瓶精华液", "草案不得覆盖未提及字段"
        return {"script_version": out["version"], "brief_merged": True,
                "llm_calls": 0}
    case("采用草案：只写库、不重复调用 LLM、不覆盖未提及字段", adopt_draft_no_llm)

    def assistant_unavailable_manual_path():
        pid = projects.create_project("手工路径", BRIEF)["project_id"]
        out = projects.generate_script(pid)      # provider = Unavailable
        assert out["assistant_unavailable"] is True
        r = director.readiness(pid)
        assert "generate_script" in r["allowed_actions"]
        # 手工路径可用
        s = projects.add_script(pid, {"summary": "手工"}, source="manual")
        assert s["version"] == 1
        return {"assistant_unavailable": True, "manual_ok": True}
    case("助手不可用时保留手工完成路径", assistant_unavailable_manual_path)

    passed = sum(1 for r in RESULTS if r["ok"])
    summary = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "scope": "S2 修复 + 渐进式导演台（确认持久化 / 版本并发 / 就绪度 / 助手上下文）",
        "isolation": {"fresh_db": True, "business_db_opened": False,
                      "provider": "Unavailable / FixedReply", "real_llm_calls": 0},
        "paid_calls": 0, "generation_tasks_executed": 0,
        "total": len(RESULTS), "passed": passed, "failed": len(RESULTS) - passed,
        "cases": RESULTS,
        "disclaimer": "全部为隔离免费验证；未调用真实模型，未生成图片/视频。",
    }
    (OUT / "免费验收结果.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{'=' * 60}\n结果：{passed}/{len(RESULTS)} 通过")
    print("付费 0｜真实 LLM 0｜真实生成 0｜业务库未打开")
    print(f"证据：{OUT / '免费验收结果.json'}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(run())
