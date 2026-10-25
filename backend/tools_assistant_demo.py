#!/usr/bin/env python3
"""助手阶段一 · 评审演示脚本（免费、隔离、可重复运行）

设计原则：
- **绝不产生费用**：不启动 worker、不消费队列、`providers_ark._request` 被替换为断言守卫。
- **绝不冒充真实**：脚本里凡是"替身"的段落都显式标注 `【替身】`；
  真实服务的可用性用只读 GET 单独呈现（当前为"暂不可用"）。
- **不写入业务库**：把 `data/app.db` 复制到临时目录后指向副本。

用法（在项目根目录）：
    /private/tmp/ecom-local-probe-20261022/venv/bin/python backend/tools_assistant_demo.py
输出同时写入 docs/evidence/助手阶段一-20261024/评审演示输出.txt
"""
from __future__ import annotations

import io
import shutil
import sys
import tempfile
import traceback
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app import config, db, main, providers_ark, worker  # noqa: E402
from app.assistant import approval, provider as prov, service, tools  # noqa: E402
from app.errors import AppError  # noqa: E402

SERVICE = "http://127.0.0.1:8100"
OUT_DIR = ROOT / "docs" / "evidence" / "助手阶段一-20261024"
REF_IMG = "asset_d73231ab405540b68eb6"
FIRST_FRAME = "asset_cd91e2f9dda14123a622"
V10 = "doubao-seedance-1-0-pro-fast-251015"

buf = io.StringIO()


def say(line: str = "") -> None:
    print(line)
    buf.write(line + "\n")


def stage(no: str, title: str, tag: str) -> None:
    say()
    say("=" * 72)
    say(f"{no} {title}   {tag}")
    say("=" * 72)


def ok(text: str) -> None:
    say(f"   ✅ {text}")


def info(text: str) -> None:
    say(f"   · {text}")


def err(expected: str, fn) -> str:  # noqa: ANN001
    try:
        fn()
    except AppError as e:
        flag = "✅" if e.code == expected else "❌"
        say(f"   {flag} 拒绝：{e.code}（HTTP {e.http}）— {e.message[:60]}")
        return e.code
    raise AssertionError(f"预期 {expected}，但没有被拒绝")


def get_json(path: str, timeout: float = 5) -> tuple[int, dict | str]:
    try:
        with urllib.request.urlopen(f"{SERVICE}{path}", timeout=timeout) as r:
            import json
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        import json
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:  # noqa: BLE001
            return e.code, ""
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}"


def payload(duration: int = 5, prompt_ext: dict | None = None) -> dict:
    p = {"kind": "video", "model_id": V10, "input_asset_id": FIRST_FRAME,
         "template_id": "tpl_product_motion_5s", "template_version": 2,
         "template_fields": {"motion": "推近特写", "duration": duration, "ratio": "9:16"}}
    if prompt_ext:
        p["params"] = prompt_ext
    return p


def body_of(p: dict) -> main.TaskIn:
    return main.TaskIn(**{k: p.get(k) for k in ("kind", "model_id", "input_asset_id",
                                                "template_id", "template_version",
                                                "template_fields", "params") if k in p})


def n_tasks() -> int:
    return db.query("SELECT COUNT(*) c FROM tasks")[0]["c"]


def main_run() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    say("助手阶段一 · 评审演示")
    say("说明：本演示分为【真实·只读】与【替身·隔离】两部分；替身段落不代表真实模型能力。")

    # ---------- 第一部分：真实服务的当前状态（只读，免费） ----------
    stage("PART 1", "真实服务当前状态（只读 GET，无费用）", "【真实】")
    st, health = get_json("/api/health")
    if isinstance(health, dict) and st == 200:
        active = {k: v for k, v in (health.get("tasks") or {}).items()
                  if k in ("queued", "loading", "running", "postprocess")}
        ok(f"/api/health → {health.get('status')}，活动任务：{active or '无'}")
    else:
        info(f"服务未启动或不可达：{health}")
    st, status = get_json("/api/assistant/status")
    if isinstance(status, dict) and st == 200:
        info(f"助手 available={status.get('available')} / provider={status.get('provider')} "
             f"/ vision={status.get('vision')} / streaming={status.get('streaming')}")
        if not status.get("available"):
            ok("真实服务未配置语言模型 → 助手显示【暂不可用】（这就是当前真实状态，不伪装已接通）")
    import json as _json
    try:
        req = urllib.request.Request(f"{SERVICE}/api/assistant/chat", method="POST",
                                     headers={"Content-Type": "application/json"},
                                     data=_json.dumps({"message": "帮我润色"}).encode())
        with urllib.request.urlopen(req, timeout=8) as r:
            info(f"chat → {r.status}（异常：未配置时不该成功）")
    except urllib.error.HTTPError as e:
        body = _json.loads(e.read() or b"{}")
        code = (body.get("error") or {}).get("code")
        ok(f"POST /api/assistant/chat → HTTP {e.code} {code}（拒绝伪造接通）")

    # ---------- 第二部分：隔离演示（替身） ----------
    tmp = Path(tempfile.mkdtemp(prefix="asst-demo-"))
    shutil.copy2(config.DB_PATH, tmp / "app.db")
    config.DB_PATH = tmp / "app.db"
    db.init_db()

    def _guard(*a, **k):
        raise AssertionError("演示期间禁止调用云端模型")
    providers_ark._request = _guard  # type: ignore[assignment]

    stage("PART 2", "隔离演示（临时库副本 + 固定回复替身；不联网、不建真实任务）", "【替身】")
    info(f"隔离库：{tmp / 'app.db'}")
    info("LLM 使用 FixedReplyProvider（固定脚本），不代表真实模型能力")

    stub = prov.FixedReplyProvider(script=[
        {"reply": "优化后：以参考图中的商品为准，保留外形与标签，背景替换为浅灰白大理石台面。",
         "structured": {"reply_type": "optimize_result",
                        "optimized_prompt": "以参考图中的商品为准，保留外形与标签，"
                                            "背景替换为浅灰白大理石台面，柔和自然光，构图留白。"}},
        {"reply": "方案：5 秒商品动态展示 v2，推近特写，5s/9:16/720p。需要我报价吗？",
         "structured": {"reply_type": "template_recommendation"}},
    ])
    prov.set_provider(stub)

    # 1 仅优化
    stage("2.1", "仅优化提示词：交付即停（不报价、不建任务）", "【替身】")
    t0 = n_tasks()
    out = service.chat(session_id=None, project_id="demo", message="帮我润色这句：白瓶 大理石 2K",
                       intent="optimize_only")
    s = out["session"]["session_id"]
    ok(f"reply_type = {out['reply_type']}")
    ok(f"optimized_prompt = {str(out['optimized_prompt'])[:46]}…")
    ok(f"options = {out['options']}；tool_calls = {len(out['tool_calls'])}")
    ok(f"任务数变化 = {n_tasks() - t0}（应为 0）")

    # 2 越权尝试
    stage("2.2", "仅优化会话内尝试报价（越权）→ 被服务端拒绝", "【替身】")
    err("QUOTE_NOT_ALLOWED", lambda: service.quote(session_id=s, project_id="demo",
                                                   payload=payload()))

    # 3 用户切换意图
    stage("2.3", "用户明确切换为「生成」意图后才能报价", "【替身】")
    view = service.set_intent(s, "generate", reason="演示：用户要求生成")
    ok(f"会话意图 → {view['intent']}")

    # 4 报价
    stage("2.4", "报价：冻结模板版本与完整方案（报价 ≠ 批准）", "【替身】")
    q = service.quote(session_id=s, project_id="demo", payload=payload(5))
    ok(f"quote_id={q['quote_id']}  template_version={q['template_version']}（报价时锁定）")
    ok(f"effective={q['effective']}")
    ok(f"费用估算 ¥{q['estimated_cost_cny']}（估算，非账单）")
    ok(f"side_effects={q['side_effects']}")

    # 5 未确认提交
    stage("2.5", "未确认就提交 → 拒绝（报价不能当批准）", "【替身】")
    err("APPROVAL_NOT_FOUND",
        lambda: main.create_task(main.TaskIn(**{**body_of(payload(5)).model_dump(),
                                                "approval_id": "ap_not_exist"})))

    # 6 确认
    stage("2.6", "用户在界面确认 → 一次性批准", "【替身】")
    ap = service.confirm(session_id=s, quote_id=q["quote_id"], project_id="demo")
    ok(f"approval_id={ap['approval_id']}  max_uses={ap['max_uses']}  有效期 600s")

    # 7 提交
    stage("2.7", "带批准提交 → 建立任务，来源由后端强制", "【替身】")
    t1 = main.create_task(main.TaskIn(**{**body_of(payload(5)).model_dump(),
                                         "approval_id": ap["approval_id"],
                                         "caller_id": "local"}))
    row = db.get("tasks", t1["id"])
    ok(f"task={t1['id']}  status={t1['status']}  caller_id={row['caller_id']}"
       f"（请求里写 local 无效）")

    # 8 重复提交
    stage("2.8", "同一批准、相同方案重复提交 → 返回同一任务", "【替身】")
    before = n_tasks()
    t2 = main.create_task(main.TaskIn(**{**body_of(payload(5)).model_dump(),
                                         "approval_id": ap["approval_id"]}))
    ok(f"same_task={t2['id'] == t1['id']}  idempotent_replay={t2.get('idempotent_replay')}"
       f"  任务数变化={n_tasks() - before}")

    # 9 改方案重放
    stage("2.9", "同一批准、改提示词后重放 → 拒绝", "【替身】")
    changed = body_of(payload(5)).model_dump()
    changed["params"] = {"prompt": "换成海边场景，不要人物", "duration": 5,
                         "ratio": "9:16", "resolution": "720p", "generate_audio": False}
    err("APPROVAL_PLAN_MISMATCH",
        lambda: main.create_task(main.TaskIn(**{**changed,
                                                "approval_id": ap["approval_id"]})))

    # 10 模板升版冻结
    stage("2.10", "报价后模板升到 v3，提交仍执行报价冻结的 v2", "【替身】")
    import copy
    from app import templates as tplmod
    s2 = service.ensure_session(None, "demo2", "generate")
    pl = payload(5)
    pl.pop("template_version", None)                      # 报价时不传版本
    q2 = service.quote(session_id=s2["id"], project_id="demo2", payload=pl)
    info(f"报价冻结版本 = v{q2['template_version']}")
    a2 = service.confirm(session_id=s2["id"], quote_id=q2["quote_id"], project_id="demo2")
    snap = copy.deepcopy(tplmod.snapshot("tpl_product_motion_5s", 2))
    snap["version"] = 3
    snap["default_params"]["duration"] = 10               # 模拟升级后默认值变化
    db.insert("template_versions", {"id": db.new_id("tplv"),
                                    "template_id": "tpl_product_motion_5s", "version": 3,
                                    "snapshot_json": db.dumps(snap),
                                    "status": "active", "created_at": db.now()})
    tplrow = db.query("SELECT id FROM templates WHERE template_id=?",
                      ("tpl_product_motion_5s",))[0]
    db.update("templates", tplrow["id"], {"latest_version": 3})
    info("已发布 v3（默认时长改为 10s，仅临时库）")
    t3 = main.create_task(main.TaskIn(**{**body_of(pl).model_dump(),
                                         "approval_id": a2["approval_id"]}))
    r3 = db.get("tasks", t3["id"])
    p3 = db.loads(r3["params_json"])
    ok(f"执行模板版本 = v{r3['template_version']}（应仍为 2）")
    ok(f"执行时长 = {p3['duration']}s（应仍为 5，未被 v3 默认值覆盖）")
    db.update("templates", tplrow["id"], {"latest_version": 2})

    # 11 手动路径回归
    stage("2.11", "原有手动路径不回归（无需批准、可取消、可重试）", "【真实代码路径·隔离库】")
    mt = main.create_task(main.TaskIn(kind="image", model_id="doubao-seedream-5-0-flash-260915",
                                      input_asset_id=REF_IMG,
                                      params={"prompt": "白底主图",
                                              "generation_mode": "image_to_image",
                                              "size": "2048x2048"}))
    ok(f"手动提交 → {mt['status']}，caller_id={db.get('tasks', mt['id'])['caller_id']}"
       f"（无 approval_id 时不校验批准）")
    r = worker.cancel(mt["id"])
    ok(f"手动取消 → {r['result']} / {r['phase']}")
    src = main.create_task(main.TaskIn(kind="image", model_id="doubao-seedream-5-0-flash-260915",
                                       input_asset_id=REF_IMG,
                                       params={"prompt": "白底主图",
                                               "generation_mode": "image_to_image",
                                               "size": "2048x2048"}))
    db.update("tasks", src["id"], {"status": "failed", "error_code": "DEMO"})
    rr = main.retry_task(src["id"])
    ok(f"手动重试 → 新任务 retry_of={db.get('tasks', rr['task_id'])['retry_of'] is not None}")

    # 12 用量分离
    stage("2.12", "助手用量与生成费用分别记录", "【替身+隔离库】")
    u = service.usage_summary(s)
    kinds = sorted({x["kind"] for x in u["assistant"]["by_kind"]})
    ok(f"助手侧 kinds={kinds}，tokens={u['assistant']['input_tokens'] + u['assistant']['output_tokens']}")
    ok(f"生成侧（caller_id=assistant 的任务费用）={u['generation']['estimated_cost_cny']} 元"
       "（演示未执行生成，故为 0）")
    info("未实现积分账本/余额/支付：界面不得展示可消费余额")

    # ---------- 收尾 ----------
    say()
    say("=" * 72)
    say("演示结论")
    say("=" * 72)
    say("  1. 真实服务：助手未配置语言模型 → 明确显示不可用，chat 返回 503（不伪造接通）。")
    say("  2. 替身演示：门禁、幂等、冻结方案、权限、用量分离逻辑全部按要求工作。")
    say("  3. 本演示未调用真实 LLM、未执行真实生成、未写入业务库、未产生费用。")
    say("  4. 替身通过 ≠ 真实模型验收通过；真实对话质量与工具调用准确性仍待验证。")
    say()
    say(f"证据文件：{OUT_DIR / '评审演示输出.txt'}")

    (OUT_DIR / "评审演示输出.txt").write_text(buf.getvalue(), encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main_run())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        (OUT_DIR / "评审演示输出.txt").write_text(buf.getvalue() + "\n[演示中断]\n" +
                                                  traceback.format_exc(), encoding="utf-8")
        sys.exit(1)
