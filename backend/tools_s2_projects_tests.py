#!/usr/bin/env python3
"""S2 免费验收：项目 / brief / 脚本（隔离库、替身模型、零付费）。

覆盖 PRD v0.10 §10-S2 验收：
① 新建项目→填 brief→保存；② 用户主动"生成脚本"→查看→编辑→确认；
③ 重新生成保留旧版本；④ 助手不可用时提示且允许手工继续；
⑤ 刷新恢复 brief/脚本/版本；⑥ 确认绑定 script_version；改 brief 后确认变 stale。

用法：<venv>/bin/python backend/tools_s2_projects_tests.py
"""
from __future__ import annotations

import json
import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import config, db, projects, projects as projects_mod  # noqa: E402
from app.assistant import provider as prov  # noqa: E402
from app.errors import AppError  # noqa: E402

RESULTS: list[dict] = []
OUT = ROOT / "docs" / "evidence" / "S2-20261026"


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
    tmp = Path(tempfile.mkdtemp(prefix="s2-"))
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


BRIEF = {"subject": "单瓶精华液", "quantity": 1,
         "selling_points": ["轻盈质地"], "forbidden_elements": "不要人物",
         "target_duration_s": 20}


def run() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = setup()
    print(f"临时库：{tmp}/app.db（业务库未打开）\n")

    print("=== 1) 项目与 brief ===")

    def create_project():
        p = projects.create_project("秋季上新-多镜头", BRIEF)
        assert p["project_id"].startswith("prj_")
        assert p["brief"]["subject"] == "单瓶精华液"
        assert p["status"] == "draft"
        return {"project_id": p["project_id"][:14] + "…", "status": p["status"]}
    case("新建项目 + 写入 brief", create_project)

    def brief_validation():
        expect("VALIDATION_FAILED", lambda: projects.create_project("x", {"made_up": 1}))
        expect("VALIDATION_FAILED",
               lambda: projects.create_project("x", {"quantity": 1.5}))
        expect("VALIDATION_FAILED", lambda: projects.create_project("  "))
        return {"unknown_field": "rejected", "quantity_float": "rejected",
                "empty_name": "rejected"}
    case("brief 未知字段 / 小数数量 / 空项目名 → 拒绝", brief_validation)

    def list_and_recover():
        items = projects.list_projects()
        assert items and items[0]["brief"]["subject"] == "单瓶精华液"
        pid = items[0]["project_id"]
        detail = projects.project_detail(pid)
        assert detail["brief"]["selling_points"] == ["轻盈质地"]
        return {"projects": len(items), "recovered": True}
    case("刷新恢复：列表与详情带回 brief", list_and_recover)

    print("\n=== 2) AI 脚本生成（用户主动触发；替身模型）===")

    def assistant_unavailable():
        pid = projects.create_project("助手离线场景", BRIEF)["project_id"]
        out = projects.generate_script(pid)
        assert out["assistant_unavailable"] is True
        assert "手工" in out["hint"]
        assert projects.list_scripts(pid) == [], "不可用时不应生成脚本版本"
        return {"assistant_unavailable": True, "hint": out["hint"][:18] + "…"}
    case("助手不可用：返回提示、不报错、不产生脚本版本", assistant_unavailable)

    def manual_fallback():
        pid = projects.create_project("手工兜底", BRIEF)["project_id"]
        s = projects.add_script(pid, {"summary": "手工写的脚本", "shots": []}, source="manual")
        assert s["source"] == "manual" and s["version"] == 1
        projects.confirm_script(s["script_id"])
        return {"script": s["script_id"][:12] + "…", "version": s["version"]}
    case("助手不可用时可手工写脚本并确认（不阻断流程）", manual_fallback)

    def generate_with_stub():
        pid = projects.create_project("AI 生成", BRIEF)["project_id"]
        payload = {"summary": "开场展示瓶身，随后推近瓶盖，最后定格卖点。",
                   "shots": [{"seq": 1, "hint": "瓶身特写"},
                             {"seq": 2, "hint": "推近瓶盖"},
                             {"seq": 3, "hint": "卖点定格"}]}
        stub = prov.FixedReplyProvider(script=[{
            "reply": json.dumps(payload, ensure_ascii=False),
            "input_tokens": 420, "output_tokens": 96}])
        out = projects.generate_script(pid, "偏简洁", 20, provider=stub)
        assert out["assistant_unavailable"] is False
        assert out["content"]["parse_ok"] is True
        assert len(out["content"]["shots"]) == 3
        assert out["llm_usage"]["output_tokens"] == 96
        # 用量落库（用于后续统一计量）
        usage = db.query("SELECT COUNT(*) c FROM assistant_usage WHERE kind='llm'")[0]["c"]
        assert usage >= 1, "未记录 LLM 用量"
        return {"version": out["version"], "shots": len(out["content"]["shots"]),
                "usage_rows": usage}
    case("生成脚本：结构化解析 + 记录 LLM 用量（替身，无真实调用）", generate_with_stub)

    def parse_degraded():
        pid = projects.create_project("解析失败", BRIEF)["project_id"]
        stub = prov.FixedReplyProvider(script=[{"reply": "这是纯文本脚本，没有 JSON。"}])
        out = projects.generate_script(pid, provider=stub)
        c = out["content"]
        assert c["parse_ok"] is False and c["summary"].startswith("这是纯文本脚本")
        assert c["shots"] == [], "解析失败不得编造镜头"
        assert c["degraded_reason"] == "model_output_not_structured"
        return {"parse_ok": False, "degraded": c["degraded_reason"]}
    case("模型输出非结构化：明确降级，不编造镜头", parse_degraded)

    print("\n=== 3) 版本、编辑、确认 ===")

    def versions_append_only():
        pid = projects.create_project("版本测试", BRIEF)["project_id"]
        v1 = projects.add_script(pid, {"summary": "v1"}, source="manual")
        v2 = projects.add_script(pid, {"summary": "v2"}, source="manual")
        v3 = projects.edit_script(v2["script_id"], {"summary": "v3"})
        assert (v1["version"], v2["version"], v3["version"]) == (1, 2, 3), \
            (v1["version"], v2["version"], v3["version"])
        # 旧版本仍在且内容未被覆盖
        assert projects.get_script(v1["script_id"])["content"]["summary"] == "v1"
        assert projects.get_script(v2["script_id"])["content"]["summary"] == "v2"
        return {"versions": [v1["version"], v2["version"], v3["version"]],
                "history_intact": True}
    case("重新生成/编辑只追加版本，旧版本保留", versions_append_only)

    def edit_conflict():
        pid = projects.create_project("并发编辑", BRIEF)["project_id"]
        v1 = projects.add_script(pid, {"summary": "v1"}, source="manual")
        projects.add_script(pid, {"summary": "v2"}, source="manual")
        # 客户端基于 v1 编辑，但项目最新已是 v2 → 冲突
        d = expect("SCRIPT_VERSION_CONFLICT",
                   lambda: projects.edit_script(v1["script_id"], {"summary": "x"},
                                                base_version=1), 409)
        # 基于最新版本则可继续
        ok = projects.edit_script(v1["script_id"], {"summary": "v3"}, base_version=2)
        assert ok["version"] == 3
        return {**d, "latest_ok_version": ok["version"]}
    case("并发编辑：base_version 过期 → SCRIPT_VERSION_CONFLICT(409)", edit_conflict)

    def confirm_binds_version():
        pid = projects.create_project("确认测试", BRIEF)["project_id"]
        s = projects.add_script(pid, {"summary": "待确认"}, source="manual")
        out = projects.confirm_script(s["script_id"])
        assert out["version"] == s["version"]
        assert projects.get_script(s["script_id"])["confirmed_at"] is not None
        # 生成新版本 → 新版本未确认（确认不继承）
        s2 = projects.edit_script(s["script_id"], {"summary": "改了一版"})
        assert projects.get_script(s2["script_id"])["confirmed_at"] is None
        return {"confirmed": s["version"], "new_version_unconfirmed": s2["version"]}
    case("确认绑定具体版本；新版本不继承确认", confirm_binds_version)

    def brief_change_marks_stale():
        pid = projects.create_project("brief 变更", BRIEF)["project_id"]
        s = projects.add_script(pid, {"summary": "脚本"}, source="manual")
        projects.confirm_script(s["script_id"])
        out = projects.update_project(pid, brief={"subject": "两件套装"})
        assert "script_confirm" in out["stale"], out["stale"]
        # 分镜计划确认尚未存在（S3 才有），因此不应虚报 shot_plan_confirm
        assert "shot_plan_confirm" not in out["stale"], out["stale"]
        # 历史确认记录不被删除
        assert projects.get_script(s["script_id"])["confirmed_at"] is not None
        return {"stale": out["stale"], "history_confirm_kept": True}
    case("改 brief → 返回 stale 标记（确认作废但不删历史）", brief_change_marks_stale)

    def missing_resources():
        expect("PROJECT_NOT_FOUND", lambda: projects.project_detail("prj_none"), 404)
        expect("SCRIPT_NOT_FOUND", lambda: projects.get_script("scr_none"), 404)
        return {"project_404": True, "script_404": True}
    case("不存在的项目/脚本 → 404", missing_resources)

    def no_tasks_side_effect():
        n = db.query("SELECT COUNT(*) c FROM tasks")[0]["c"]
        pid = projects.create_project("无副作用", BRIEF)["project_id"]
        stub = prov.FixedReplyProvider(script=[{"reply": json.dumps(
            {"summary": "s", "shots": []})}])
        projects.generate_script(pid, provider=stub)
        assert db.query("SELECT COUNT(*) c FROM tasks")[0]["c"] == n, "脚本流程创建了生成任务"
        return {"tasks_before": n, "tasks_after": n}
    case("脚本流程不创建任何生成任务（不调图片/视频模型）", no_tasks_side_effect)

    passed = sum(1 for r in RESULTS if r["ok"])
    summary = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "scope": "S2 项目 / brief / 脚本",
        "isolation": {"fresh_db": True, "business_db_opened": False,
                      "provider": "FixedReplyProvider / Unavailable",
                      "real_llm_calls": 0, "worker": "未启动"},
        "paid_calls": 0, "generation_tasks_executed": 0,
        "total": len(RESULTS), "passed": passed, "failed": len(RESULTS) - passed,
        "cases": RESULTS,
        "disclaimer": "脚本生成为替身模型；真实 DeepSeek 生成未在本轮验证。",
    }
    (OUT / "免费验收结果.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{'=' * 60}\n结果：{passed}/{len(RESULTS)} 通过")
    print("付费 0｜真实 LLM 0｜真实生成 0｜业务库未打开")
    print(f"证据：{OUT / '免费验收结果.json'}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(run())
