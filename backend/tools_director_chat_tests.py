#!/usr/bin/env python3
"""对话导演 + 自由画布：免费验收（隔离库 + 替身 LLM，零真实调用）。

覆盖交接文档 §6 的 10 条最低验收（能在后端侧验证的部分）。
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import canvas, config, db, director, director_chat, projects, providers_ark, shots, storage  # noqa: E402
from app.assistant import provider as prov  # noqa: E402
from app.errors import AppError  # noqa: E402

RESULTS: list[dict] = []
OUT = ROOT / "docs" / "evidence" / "对话导演-20261026"
BRIEF = {"subject": "单瓶精华液", "quantity": 1}
V = "doubao-seedance-1-0-pro-fast-251015"
F = "doubao-seedream-5-0-flash-260915"
IMG = {"id": None}


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
    tmp = Path(tempfile.mkdtemp(prefix="dirchat-"))
    config.DB_PATH = tmp / "app.db"
    config.ASSETS = tmp / "assets"
    config.ASSETS.mkdir(parents=True, exist_ok=True)
    db.init_db()
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (200, 205, 215)).save(buf, format="PNG")
    IMG["id"] = storage.save_bytes(buf.getvalue(), "image", "upload")["id"]

    def _guard(*a, **k):
        raise AssertionError("测试禁止真实云端调用")
    providers_ark._request = _guard  # type: ignore[assignment]
    return tmp


def expect(code: str, fn, http: int | None = None) -> dict:
    try:
        fn()
    except AppError as e:
        assert e.code == code, f"期望 {code}，实际 {e.code}: {e.message}"
        if http is not None:
            assert e.http == http
        return {"code": e.code, "http": e.http}
    raise AssertionError(f"期望抛出 {code}，但没有报错")


def stub(script: list[dict]) -> prov.FixedReplyProvider:
    return prov.FixedReplyProvider(script=script)


def tc(name: str, args: dict) -> dict:
    return {"reply": "", "tool_calls": [{"name": name, "arguments": args}]}


def proj() -> str:
    return projects.create_project("导演项目", BRIEF)["project_id"]


def run() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = setup()
    print(f"临时库：{tmp}/app.db（业务库未打开）\n")

    print("=== 1) 只澄清 → 无产物；脚本草案自动保存并返回真实 ID ===")

    def clarify_no_artifact():
        pid = proj()
        p = stub([{"reply": "这条视频主要展示哪些卖点？",
                   "structured": {"reply_type": "clarify"}}])
        out = director_chat.chat(pid, {"message": "做个展示视频",
                                       "client_request_id": "req-clarify"}, provider=p)
        assert out["artifacts"] == [], out["artifacts"]
        assert out["reply_type"] == "clarify"
        assert db.query("SELECT COUNT(*) c FROM tasks")[0]["c"] == 0, "澄清产生了任务"
        assert db.query("SELECT COUNT(*) c FROM script_versions WHERE project_id=?",
                        (pid,))[0]["c"] == 0
        return {"artifacts": 0, "tasks": 0, "reply_type": out["reply_type"]}
    case("只澄清：无脚本/无任务", clarify_no_artifact)

    def script_autosaved():
        pid = proj()
        p = stub([tc("propose_script", {"summary": "整体展示后推近瓶盖",
                                        "shots": [{"seq": 1, "hint": "商品整体"},
                                                  {"seq": 2, "hint": "瓶盖细节"}]}),
                  {"reply": "已保存一版两镜头脚本草案，请确认。"}])
        out = director_chat.chat(pid, {"message": "两个镜头，先整体再瓶盖",
                                       "client_request_id": "req-script"}, provider=p)
        art = [a for a in out["artifacts"] if a["type"] == "script"]
        assert art, out["artifacts"]
        a = art[0]
        assert a["persistence"] == "saved" and a["confirmed"] is False
        s = db.get("script_versions", a["target_id"])
        assert s and s["project_id"] == pid and s["confirmed_at"] is None
        assert a["target_version"] == s["version"]
        # 未确认 → 不产生任务
        assert db.query("SELECT COUNT(*) c FROM tasks")[0]["c"] == 0
        # 刷新可取回（分页消息 + 提案列表）
        msgs = director_chat.messages(pid)
        assert msgs["items"] and msgs["items"][0]["request_id"] == "req-script"
        props = director_chat.proposals(pid, "script")["items"]
        assert props and props[0]["target_id"] == a["target_id"]
        # actions 由服务端根据真实状态生成
        assert any(x["type"] == "confirm_script" and x["target_id"] == a["target_id"]
                   for x in out["actions"]), out["actions"]
        return {"script_id": a["target_id"][:14] + "…", "version": a["target_version"],
                "confirmed": False, "actions": [x["type"] for x in out["actions"]]}
    case("脚本草案自动保存（真实 ID/版本，未确认）+ 刷新可取回",
         script_autosaved)

    print("\n=== 2) 畸形结构 / 伪造确认 / 越权工具 ===")

    def malformed_rejected():
        pid = proj()
        p = stub([tc("propose_script", {"summary": "", "shots": []}),
                  {"reply": "（结构不合法，未保存）"}])
        out = director_chat.chat(pid, {"message": "写脚本", "client_request_id": "req-bad"},
                                 provider=p)
        assert out["artifacts"] == [], out["artifacts"]
        assert db.query("SELECT COUNT(*) c FROM script_versions WHERE project_id=?",
                        (pid,))[0]["c"] == 0
        assert out["tool_calls"][0]["ok"] is False
        assert out["tool_calls"][0]["error"]["code"] == "VALIDATION_FAILED"
        return {"saved": 0, "error": out["tool_calls"][0]["error"]["code"]}
    case("LLM 返回畸形结构 → 不保存、不假成功", malformed_rejected)

    def forbidden_tools():
        pid = proj()
        for tool in ("confirm_script", "submit", "generate"):
            d = expect("TOOL_FORBIDDEN", lambda t=tool: director_chat.execute_tool(
                t, {}, {"project_id": pid, "session_id": "s", "request_id": "r"}), 400)
        # 工具签名不含 project_id（项目由服务端绑定）
        specs = [t.name for t in director_chat.PROJECT_TOOLS]
        assert "project_id" not in json.dumps([t.parameters for t in director_chat.PROJECT_TOOLS])
        return {**d, "tools": specs}
    case("确认/提交/生成工具不存在；工具不接受 project_id", forbidden_tools)

    print("\n=== 3) 幂等：重复请求 / 同键不同内容 / pending ===")

    def idempotent_replay():
        pid = proj()
        p = stub([{"reply": "好的，我理解你的需求。"}])
        body = {"message": "做个电商展示视频", "client_request_id": "req-idem"}
        a = director_chat.chat(pid, body, provider=p)
        calls_after_first = len(p.calls)
        b = director_chat.chat(pid, body, provider=p)
        assert b.get("idempotent_replay") is True
        assert b["reply"] == a["reply"]
        assert len(p.calls) == calls_after_first, "幂等重放又调用了 LLM"
        return {"replay": True, "llm_calls": len(p.calls)}
    case("同键同内容重发 → 返回原结果，不重复调用 LLM", idempotent_replay)

    def idem_conflict():
        pid = proj()
        p = stub([{"reply": "A"}])
        director_chat.chat(pid, {"message": "内容A", "client_request_id": "req-conf"}, provider=p)
        d = expect("IDEMPOTENCY_CONFLICT", lambda: director_chat.chat(
            pid, {"message": "内容B", "client_request_id": "req-conf"}, provider=p), 409)
        return d
    case("同键不同内容 → 409 IDEMPOTENCY_CONFLICT", idem_conflict)

    def receipt_lookup():
        pid = proj()
        p = stub([{"reply": "已完成"}])
        director_chat.chat(pid, {"message": "x", "client_request_id": "req-rcpt"}, provider=p)
        r = director_chat.request_receipt(pid, "req-rcpt")
        assert r["status"] == "completed" and r["response"]["reply"] == "已完成"
        d = expect("DIRECTOR_REQUEST_NOT_FOUND",
                   lambda: director_chat.request_receipt(pid, "req-none"), 404)
        return {**d, "status": r["status"]}
    case("断线恢复：按 client_request_id 查回执", receipt_lookup)

    print("\n=== 4) 项目隔离 ===")

    def cross_project_session():
        a, b = proj(), proj()
        p = stub([{"reply": "hi"}])
        s = director_chat.chat(a, {"message": "A 项目", "client_request_id": "req-a"}, provider=p)
        sid = s["session_id"]
        d = expect("PROJECT_SESSION_MISMATCH", lambda: director_chat.chat(
            b, {"message": "B 项目", "client_request_id": "req-b", "session_id": sid},
            provider=p), 409)
        return d
    case("A 项目会话不能改绑到 B（PROJECT_SESSION_MISMATCH）",
         cross_project_session)

    def cross_project_refs():
        a, b = proj(), proj()
        s = projects.add_script(a, {"summary": "A 的脚本"}, source="manual")
        d = expect("CANVAS_INVALID_REFERENCE",
                   lambda: canvas.patch_canvas(b, {"nodes": [
                       {"node_id": "n1", "type": "script", "position": {"x": 1, "y": 2},
                        "binding": {"script_id": s["script_id"]}}]}), 400)
        return d
    case("画布节点引用其它项目对象 → 拒绝", cross_project_refs)

    print("\n=== 5) 自动保存 ≠ 确认 ≠ 生成 ===")

    def no_task_until_user():
        pid = proj()
        p = stub([tc("propose_script", {"summary": "两镜", "shots": [{"seq": 1, "hint": "整体"}]}),
                  {"reply": "草案已保存，请确认。"}])
        out = director_chat.chat(pid, {"message": "两镜脚本", "client_request_id": "req-nt"},
                                 provider=p)
        assert db.query("SELECT COUNT(*) c FROM tasks")[0]["c"] == 0
        # 模型自称 confirm 也不会被采纳（工具不存在）
        assert all("confirm" not in t["tool"] for t in out["tool_calls"])
        s = db.query("SELECT * FROM script_versions WHERE project_id=?", (pid,))[0]
        assert s["confirmed_at"] is None
        return {"tasks": 0, "script_confirmed": False}
    case("导演流程不自动创建图片/视频任务", no_task_until_user)

    print("\n=== 6) 提案应用：brief / shot_plan ===")

    def brief_proposal_apply():
        pid = proj()
        p = stub([tc("propose_brief", {"brief": {"subject": "两件套装", "quantity": 2}}),
                  {"reply": "需求草案已保存。"}])
        out = director_chat.chat(pid, {"message": "其实是两件", "client_request_id": "req-bp"},
                                 provider=p)
        prop = [a for a in out["artifacts"] if a["type"] == "brief"][0]
        # 未应用前项目事实不变
        assert projects.project_detail(pid)["brief"]["subject"] == "单瓶精华液"
        r = director_chat.apply_proposal(pid, prop["proposal_id"], prop["proposal_version"], None)
        assert r["applied"] is True
        assert projects.project_detail(pid)["brief"]["subject"] == "两件套装"
        return {"applied": True, "stale": r.get("stale")}
    case("需求提案：应用前不改项目事实，应用后才写入", brief_proposal_apply)

    def shot_plan_requires_script_confirm():
        pid = proj()
        p = stub([tc("propose_script", {"summary": "两镜", "shots": [{"seq": 1, "hint": "整体"},
                                                                   {"seq": 2, "hint": "细节"}]}),
                  tc("propose_shot_plan", {"shots": [
                      {"seq": 1, "description": "整体展示", "duration_s": 5},
                      {"seq": 2, "description": "瓶盖特写", "duration_s": 5}]}),
                  {"reply": "脚本与镜头计划草案已保存。"}])
        out = director_chat.chat(pid, {"message": "两镜计划", "client_request_id": "req-sp"},
                                 provider=p)
        plan = [a for a in out["artifacts"] if a["type"] == "shot_plan"][0]
        # 脚本未确认 → 应用请求被拒
        d = expect("SCRIPT_NOT_CONFIRMED", lambda: director_chat.apply_proposal(
            pid, plan["proposal_id"], plan["proposal_version"], None), 409)
        # 确认脚本后可应用
        s = db.query("SELECT * FROM script_versions WHERE project_id=?", (pid,))[0]
        projects.confirm_script(s["id"])
        r = director_chat.apply_proposal(pid, plan["proposal_id"], plan["proposal_version"], None)
        assert r["applied"] and len(r["shot_ids"]) == 2
        assert db.query("SELECT COUNT(*) c FROM tasks")[0]["c"] == 0, "应用计划不应生成任务"
        return {**d, "shots": len(r["shot_ids"])}
    case("镜头计划：脚本未确认不可应用；应用不生成任务",
         shot_plan_requires_script_confirm)

    def plan_stale_when_script_changes():
        pid = proj()
        p = stub([tc("propose_script", {"summary": "v1"}), {"reply": "已保存脚本"}])
        director_chat.chat(pid, {"message": "写脚本", "client_request_id": "req-s1"}, provider=p)
        s1 = db.query("SELECT * FROM script_versions WHERE project_id=?", (pid,))[0]
        projects.confirm_script(s1["id"])
        p2 = stub([tc("propose_shot_plan", {"shots": [{"seq": 1, "description": "镜头", "duration_s": 5}]}),
                   {"reply": "计划已保存"}])
        out = director_chat.chat(pid, {"message": "计划", "client_request_id": "req-p1"}, provider=p2)
        plan = [a for a in out["artifacts"] if a["type"] == "shot_plan"][0]
        # 脚本新增版本 → 计划基于旧版本 → 不可应用
        projects.add_script(pid, {"summary": "v2"}, source="manual")
        d = expect("PROPOSAL_NOT_APPLICABLE", lambda: director_chat.apply_proposal(
            pid, plan["proposal_id"], plan["proposal_version"], None), 409)
        return d
    case("脚本升版后旧镜头计划不可应用（PROPOSAL_NOT_APPLICABLE）",
         plan_stale_when_script_changes)

    print("\n=== 7) 画布：布局 vs 配置；节点独立持久化 ===")

    def layout_only_no_invalidate():
        pid = proj()
        canvas.patch_canvas(pid, {"nodes": [
            {"node_id": "n1", "type": "video", "position": {"x": 10, "y": 20},
             "config": {"generation_mode": "text_to_video", "model_id": V, "prompt": "A",
                        "params": {"duration": 5, "ratio": "9:16", "resolution": "720p"}},
             "binding": {}}]})
        r1 = canvas.get_canvas(pid)
        lr1 = r1["layout_revision"]
        # 只移动 → 配置不变、不使 scope 失效
        out = canvas.patch_canvas(pid, {"expected_layout_revision": lr1, "nodes": [
            {"node_id": "n1", "type": "video", "position": {"x": 300, "y": 80}}]})
        assert out["config_changed"] is False
        assert out["invalidated"] == [], out["invalidated"]
        got = [n for n in out["canvas"]["nodes"] if n["node_id"] == "n1"][0]
        assert got["position"] == {"x": 300, "y": 80}
        assert got["config_revision"] == 1, "移动不应改变配置修订"
        assert got["config"]["prompt"] == "A", "移动不应丢配置"
        return {"layout_revision": out["layout_revision"], "config_changed": False}
    case("只移动节点：布局修订+1，配置与报价不受影响", layout_only_no_invalidate)

    def config_change_invalidates():
        pid = proj()
        canvas.patch_canvas(pid, {"nodes": [
            {"node_id": "n1", "type": "video", "position": {"x": 0, "y": 0},
             "config": {"generation_mode": "text_to_video", "model_id": V, "prompt": "旧提示词",
                        "params": {"duration": 5, "ratio": "9:16", "resolution": "720p"}},
             "binding": {}}]})
        out = canvas.patch_canvas(pid, {"expected_layout_revision": 1, "nodes": [
            {"node_id": "n1", "type": "video", "position": {"x": 0, "y": 0},
             "config": {"generation_mode": "text_to_video", "model_id": V, "prompt": "新提示词",
                        "params": {"duration": 5, "ratio": "9:16", "resolution": "720p"}}}]
        })
        assert out["config_changed"] is True
        assert "scope" in out["invalidated"], out["invalidated"]
        got = out["canvas"]["nodes"][0]
        assert got["config_revision"] == 2
        return {"config_revision": got["config_revision"],
                "invalidated": out["invalidated"]}
    case("改生成配置：配置修订+1 且整组报价失效", config_change_invalidates)

    def nodes_independent_persist():
        pid = proj()
        canvas.patch_canvas(pid, {"nodes": [
            {"node_id": "n1", "type": "video", "position": {"x": 0, "y": 0},
             "config": {"generation_mode": "text_to_video", "model_id": V, "prompt": "节点1",
                        "params": {"duration": 5, "ratio": "9:16", "resolution": "720p"}}},
            {"node_id": "n2", "type": "video", "position": {"x": 400, "y": 0},
             "config": {"generation_mode": "image_to_video", "model_id": V, "prompt": "节点2",
                        "input_asset_id": IMG["id"],
                        "params": {"duration": 8, "ratio": "1:1", "resolution": "720p"}}}]})
        c = canvas.get_canvas(pid)
        n1 = [n for n in c["nodes"] if n["node_id"] == "n1"][0]
        n2 = [n for n in c["nodes"] if n["node_id"] == "n2"][0]
        assert n1["config"]["prompt"] == "节点1" and n1["config"]["generation_mode"] == "text_to_video"
        assert n2["config"]["prompt"] == "节点2" and n2["config"]["params"]["duration"] == 8
        assert n2["config"]["input_asset_id"] == IMG["id"]
        return {"n1_mode": n1["config"]["generation_mode"],
                "n2_mode": n2["config"]["generation_mode"],
                "n2_duration": n2["config"]["params"]["duration"]}
    case("多节点各自保存模式/模型/规格/素材（互不串）", nodes_independent_persist)

    def node_validation():
        pid = proj()
        # 文生节点带参考素材 → 冲突
        d1 = expect("GENERATION_MODE_CONFLICT", lambda: canvas.patch_canvas(pid, {"nodes": [
            {"node_id": "bad1", "type": "video", "position": {"x": 0, "y": 0},
             "config": {"generation_mode": "text_to_video", "model_id": V,
                        "prompt": "x", "input_asset_id": IMG["id"],
                        "params": {"duration": 5, "ratio": "9:16", "resolution": "720p"}}}]}), 400)
        # 时长越界 → 拒绝
        d2 = expect("MODEL_CAPABILITY_MISMATCH", lambda: canvas.patch_canvas(pid, {"nodes": [
            {"node_id": "bad2", "type": "video", "position": {"x": 0, "y": 0},
             "config": {"generation_mode": "text_to_video", "model_id": V, "prompt": "x",
                        "params": {"duration": 99, "ratio": "9:16", "resolution": "720p"}}}]}), 400)
        # 图生节点无素材 → 拒绝
        d3 = expect("REFERENCE_IMAGE_REQUIRED", lambda: canvas.patch_canvas(pid, {"nodes": [
            {"node_id": "bad3", "type": "video", "position": {"x": 0, "y": 0},
             "config": {"generation_mode": "image_to_video", "model_id": V, "prompt": "x",
                        "params": {"duration": 5, "ratio": "9:16", "resolution": "720p"}}}]}), 400)
        return {"t2v_with_img": d1["code"], "duration": d2["code"], "i2v_no_img": d3["code"]}
    case("节点配置校验：模式冲突/时长越界/图生缺素材均拒绝", node_validation)

    def canvas_cycle_rejected():
        pid = proj()
        canvas.patch_canvas(pid, {"nodes": [
            {"node_id": "a", "type": "video", "position": {"x": 0, "y": 0}, "config": {}},
            {"node_id": "b", "type": "video", "position": {"x": 1, "y": 0}, "config": {}}]})
        d = expect("CANVAS_CYCLE", lambda: canvas.patch_canvas(pid, {
            "expected_layout_revision": 1,
            "nodes": [], "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}]}), 400)
        return d
    case("连线循环依赖 → CANVAS_CYCLE", canvas_cycle_rejected)

    def canvas_revision_conflict():
        pid = proj()
        canvas.patch_canvas(pid, {"nodes": [
            {"node_id": "n", "type": "video", "position": {"x": 0, "y": 0}, "config": {}}]})
        d = expect("CANVAS_REVISION_CONFLICT", lambda: canvas.patch_canvas(pid, {
            "expected_layout_revision": 99,
            "nodes": [{"node_id": "n", "type": "video", "position": {"x": 5, "y": 5}}]}), 409)
        return d
    case("布局并发冲突 → CANVAS_REVISION_CONFLICT", canvas_revision_conflict)

    print("\n=== 8) 预算闸门（每次 LLM 调用前）===")

    def budget_blocked():
        pid = proj()
        old = config.DIRECTOR_MAX_COST_PER_CALL_CNY
        config.DIRECTOR_MAX_COST_PER_CALL_CNY = 0.000001
        try:
            d = expect("ASSISTANT_BUDGET_EXCEEDED", lambda: director_chat.chat(
                pid, {"message": "长" * 4000, "client_request_id": "req-budget"},
                provider=stub([{"reply": "x"}])), 429)
        finally:
            config.DIRECTOR_MAX_COST_PER_CALL_CNY = old
        # 预算拒绝后没有产物、没有 LLM 用量
        assert db.query("SELECT COUNT(*) c FROM script_versions WHERE project_id=?",
                        (pid,))[0]["c"] == 0
        return d
    case("单次预估超限 → 429，且未产生产物", budget_blocked)

    def session_budget():
        pid = proj()
        old_call, old_sess = (config.DIRECTOR_MAX_COST_PER_CALL_CNY,
                              config.DIRECTOR_MAX_SESSION_COST_CNY)
        config.DIRECTOR_MAX_SESSION_COST_CNY = 0.000001
        try:
            d = expect("ASSISTANT_BUDGET_EXCEEDED", lambda: director_chat.chat(
                pid, {"message": "hi", "client_request_id": "req-sbudget"},
                provider=stub([{"reply": "hi"}])), 429)
        finally:
            config.DIRECTOR_MAX_COST_PER_CALL_CNY, config.DIRECTOR_MAX_SESSION_COST_CNY = old_call, old_sess
        return d
    case("会话累计超限 → 429（可解释）", session_budget)

    passed = sum(1 for r in RESULTS if r["ok"])
    summary = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "scope": "对话导演（D1/D2/D4）+ 自由画布（D3）免费验收",
        "isolation": {"fresh_db": True, "business_db_opened": False,
                      "provider": "FixedReplyProvider（不联网）", "worker": "未启动"},
        "paid_calls": 0, "real_llm_calls": 0, "image_video_tasks_executed": 0,
        "total": len(RESULTS), "passed": passed, "failed": len(RESULTS) - passed,
        "cases": RESULTS,
        "disclaimer": "全部为替身验证；真实 DeepSeek 导演质量未验证。",
    }
    (OUT / "免费验收结果.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{'=' * 60}\n结果：{passed}/{len(RESULTS)} 通过")
    print("付费 0｜真实 LLM 0｜业务库未打开")
    print(f"证据：{OUT / '免费验收结果.json'}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(run())
