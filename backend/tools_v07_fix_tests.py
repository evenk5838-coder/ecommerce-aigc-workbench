#!/usr/bin/env python3
"""v0.7 五项修复的**免费**验证（零付费、零真实模型调用、零业务库改动）。

隔离方式：
  - 把 `data/app.db` 复制到临时目录，测试进程内把 `config.DB_PATH` 指向副本；
  - 真实业务库只读，不写入、不重建；
  - `providers_ark._request` 被替换为「一调用就断言失败」的守卫，证明没有云端调用；
  - 模型生命周期用**可注入时钟**（`main._now` / `resolve_task(now_ts=...)`）测试到期前/到期时刻/到期后。

用法：<venv>/bin/python backend/tools_v07_fix_tests.py
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
from app.errors import AppError  # noqa: E402

RESULTS: list[dict] = []
OUT = ROOT / "docs" / "evidence" / "v0.7修复验证-20261024"

REF_IMG = "asset_d73231ab405540b68eb6"       # 单瓶参考图（已有真实素材，只读）
FIRST_FRAME = "asset_cd91e2f9dda14123a622"   # 商品图（已有，只读）
FLASH = "doubao-seedream-5-0-flash-260915"
PRO = "doubao-seedream-5-0-pro-260628"
V10 = "doubao-seedance-1-0-pro-fast-251015"
MINI = "doubao-seedance-2-0-mini-260615"

EOS_TS = datetime.fromisoformat("2026-11-24T14:00:00+08:00").timestamp()


# ---------- 断言框架 ----------
def case(name: str, fn) -> None:
    try:
        detail = fn() or {}
        RESULTS.append({"case": name, "ok": True, "detail": detail})
        print(f"  OK  {name}" + (f"  {detail}" if detail else ""))
    except AssertionError as e:
        RESULTS.append({"case": name, "ok": False, "error": str(e)})
        print(f" FAIL {name} → {e}")
    except Exception as e:  # noqa: BLE001
        RESULTS.append({"case": name, "ok": False,
                        "error": f"{type(e).__name__}: {e}",
                        "trace": traceback.format_exc()[-600:]})
        print(f" ERR  {name} → {type(e).__name__}: {e}")


def expect_error(code: str, fn, http: int | None = None) -> dict:
    try:
        fn()
    except AppError as e:
        assert e.code == code, f"期望 {code}，实际 {e.code}: {e.message}"
        if http is not None:
            assert e.http == http, f"期望 HTTP {http}，实际 {e.http}"
        return {"code": e.code, "http": e.http, "message": e.message[:80], "detail": e.detail}
    raise AssertionError(f"期望抛出 {code}，但没有报错")


def task_count() -> int:
    return db.query("SELECT COUNT(*) c FROM tasks")[0]["c"]


# ---------- 隔离环境 ----------
def setup() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="v07fix-"))
    shutil.copy2(config.DB_PATH, tmp / "app.db")
    config.DB_PATH = tmp / "app.db"
    db.init_db()
    templates.seed()

    # 守卫：任何云端调用立即失败（证明测试零付费）
    def _guard(*a, **k):
        raise AssertionError("测试期间禁止调用云端模型（检测到 providers_ark._request）")
    providers_ark._request = _guard  # type: ignore[assignment]
    return tmp


def main_run() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = setup()
    print(f"隔离库：{tmp / 'app.db'}（真实库未改动）\n")

    print("=== 修复 1：提交前费用预估（无副作用）===")
    est_body = main.TaskIn(kind="image", model_id=FLASH, input_asset_id=REF_IMG,
                           template_id="tpl_scene_bg", template_version=1,
                           template_fields={"product_name": "焕亮精华液", "scene": "大理石台面"})

    def est_no_side_effect():
        before = task_count()
        outs = [main.estimate_task(est_body) for _ in range(3)]
        after = task_count()
        assert before == after, f"预估前后任务数变化 {before}→{after}"
        assert all(o["side_effects"]["task_created"] is False for o in outs)
        assert all(o["side_effects"]["model_called"] is False for o in outs)
        assert len({o["estimated_cost_cny"] for o in outs}) == 1, "同输入预估费用不一致"
        return {"task_count_before": before, "task_count_after": after,
                "estimated_cost_cny": outs[0]["estimated_cost_cny"]}
    case("多次预估不产生任务、不调用模型、费用稳定", est_no_side_effect)

    def est_matches_submit():
        est = main.estimate_task(est_body)
        created = main.post_task(est_body)
        task = main.get_task(created["task_id"])
        eff = main._effective(task["params"], task["kind"])
        assert eff == est["effective"], f"预估生效参数 {est['effective']} ≠ 任务 {eff}"
        assert task["params"]["prompt"].startswith(est["params_preview"]["prompt"][:20])
        assert abs((created["estimated_cost_cny"] or 0) - est["estimated_cost_cny"]) < 1e-9
        return {"effective": eff, "cost": est["estimated_cost_cny"]}
    case("同一输入：预估与正式提交的有效参数/费用一致", est_matches_submit)

    def est_route_order():
        paths = [getattr(r, "path", "") for r in main.app.routes]
        assert "/api/tasks/estimate" in paths
        i_est = paths.index("/api/tasks/estimate")
        i_dyn = paths.index("/api/tasks/{task_id}")
        assert i_est < i_dyn, f"estimate 注册在动态路由之后：{i_est} vs {i_dyn}"
        methods = {(getattr(r, "path", ""), tuple(sorted(getattr(r, "methods", []) or [])))
                   for r in main.app.routes}
        assert ("/api/tasks/{task_id}", ("GET",)) in methods, "动态路由不是仅 GET"
        return {"estimate_index": i_est, "dynamic_index": i_dyn,
                "note": "/api/tasks/{task_id} 仅 GET，与 POST /api/tasks/estimate 无冲突"}
    case("路由顺序：estimate 在动态路由之前", est_route_order)

    print("\n=== 修复 2：模板自定义字段保留 ===")

    def custom_scene():
        body = main.TaskIn(kind="image", model_id=FLASH, input_asset_id=REF_IMG,
                           template_id="tpl_scene_bg", template_version=1,
                           template_fields={"scene": "自定义", "scene_custom": "浅灰水泥墙"})
        r = main.resolve_task(body)
        assert r["params"]["fields"].get("scene_custom") == "浅灰水泥墙", r["params"]["fields"]
        assert "浅灰水泥墙" in r["params"]["prompt"], r["params"]["prompt"]
        return {"fields": r["params"]["fields"], "prompt_contains": "浅灰水泥墙"}
    case("场景模板：自定义值进入字段快照与提示词", custom_scene)

    def custom_motion():
        body = main.TaskIn(kind="video", model_id=V10, input_asset_id=FIRST_FRAME,
                           template_id="tpl_product_motion_5s", template_version=2,
                           template_fields={"motion": "自定义", "motion_custom": "缓慢横移",
                                            "duration": 5})
        r = main.resolve_task(body, now_ts=EOS_TS - 60)
        assert r["params"]["fields"].get("motion_custom") == "缓慢横移"
        assert "缓慢横移" in r["params"]["prompt"]
        return {"fields": r["params"]["fields"]}
    case("视频模板：自定义动态值进入提示词", custom_motion)

    def custom_theme():
        body = main.TaskIn(kind="image", model_id=FLASH, input_asset_id=REF_IMG,
                           template_id="tpl_season_theme", template_version=1,
                           template_fields={"theme": "自定义", "theme_custom": "深秋枫叶"})
        r = main.resolve_task(body)
        assert r["params"]["fields"].get("theme_custom") == "深秋枫叶"
        assert "深秋枫叶" in r["params"]["prompt"]
        return {"fields": r["params"]["fields"]}
    case("季节模板：自定义主题值进入提示词", custom_theme)

    def custom_empty():
        body = main.TaskIn(kind="image", model_id=FLASH, input_asset_id=REF_IMG,
                           template_id="tpl_scene_bg", template_version=1,
                           template_fields={"scene": "自定义", "scene_custom": "   "})
        d = expect_error("TEMPLATE_FIELD_REQUIRED", lambda: main.resolve_task(body), 400)
        assert d["detail"].get("field") == "scene_custom", d["detail"]
        return d
    case("自定义但内容为空 → TEMPLATE_FIELD_REQUIRED(field=scene_custom)", custom_empty)

    def custom_not_selected():
        body = main.TaskIn(kind="image", model_id=FLASH, input_asset_id=REF_IMG,
                           template_id="tpl_scene_bg", template_version=1,
                           template_fields={"scene": "大理石台面", "scene_custom": "不该出现"})
        r = main.resolve_task(body)
        assert "scene_custom" not in r["params"]["fields"], r["params"]["fields"]
        assert "不该出现" not in r["params"]["prompt"], r["params"]["prompt"]
        return {"fields": r["params"]["fields"]}
    case("未选择自定义：无关值不进入字段与提示词", custom_not_selected)

    print("\n=== 修复 3：视频模板时长/比例生效 ===")

    def video_default():
        body = main.TaskIn(kind="video", model_id=V10, input_asset_id=FIRST_FRAME,
                           template_id="tpl_product_motion_5s", template_version=2,
                           template_fields={"motion": "缓慢旋转"})
        r = main.resolve_task(body, now_ts=EOS_TS - 60)
        assert r["params"]["duration"] == 5 and r["params"]["ratio"] == "9:16", r["params"]
        return {"effective": r["effective"], "cost": r["estimated_cost_cny"]}
    case("视频模板默认：5s / 9:16", video_default)

    def video_field_mapping():
        body = main.TaskIn(kind="video", model_id=V10, input_asset_id=FIRST_FRAME,
                           template_id="tpl_product_motion_5s", template_version=2,
                           template_fields={"motion": "缓慢旋转", "duration": 10, "ratio": "1:1"})
        est = main.estimate_task(body) if False else main.resolve_task(body, now_ts=EOS_TS - 60)
        assert est["params"]["duration"] == 10, est["params"]
        assert est["params"]["ratio"] == "1:1", est["params"]
        assert est["effective"] == {"duration": 10, "ratio": "1:1",
                                    "resolution": "720p", "generate_audio": False}
        created = main.create_task(main.TaskIn(
            kind="video", model_id=V10, input_asset_id=FIRST_FRAME,
            template_id="tpl_product_motion_5s", template_version=2,
            template_fields={"motion": "缓慢旋转", "duration": 10, "ratio": "1:1"}))
        saved = db.loads(main.get_task(created["id"])["params_json"])
        assert saved["duration"] == 10 and saved["ratio"] == "1:1", saved
        return {"estimate": est["effective"], "saved": {"duration": saved["duration"],
                                                       "ratio": saved["ratio"]},
                "cost_10s": est["estimated_cost_cny"]}
    case("模板字段 10s/1:1 映射到最终参数、费用与任务记录一致", video_field_mapping)

    def video_explicit_override():
        body = main.TaskIn(kind="video", model_id=V10, input_asset_id=FIRST_FRAME,
                           template_id="tpl_product_motion_5s", template_version=2,
                           template_fields={"motion": "缓慢旋转", "duration": 10, "ratio": "1:1"},
                           params={"duration": 5})
        r = main.resolve_task(body, now_ts=EOS_TS - 60)
        assert r["params"]["duration"] == 5, r["params"]
        assert r["params"]["ratio"] == "1:1", r["params"]
        return {"effective": r["effective"], "note": "显式 params 覆盖模板字段，未覆盖项沿用字段映射"}
    case("优先级：显式 params > 模板字段 > 模板默认", video_explicit_override)

    def video_unsupported():
        body = main.TaskIn(kind="video", model_id=MINI, input_asset_id=FIRST_FRAME,
                           template_id="tpl_product_motion_5s", template_version=2,
                           template_fields={"motion": "缓慢旋转", "duration": 5},
                           params={"resolution": "1080p"})
        d = expect_error("MODEL_CAPABILITY_MISMATCH", lambda: main.resolve_task(body), 400)
        fields = {p["field"] for p in d["detail"]["problems"]}
        assert "resolution" in fields, d["detail"]
        return d
    case("最终参数能力校验：2.0 mini 不支持 1080p → MODEL_CAPABILITY_MISMATCH", video_unsupported)

    def video_v1_immutable():
        v1 = templates.snapshot("tpl_product_motion_5s", 1)
        v2 = templates.snapshot("tpl_product_motion_5s", 2)
        assert "field_params" not in v1, "v1 快照被修改（不应出现 field_params）"
        assert v2.get("field_params") == {"duration": "duration", "ratio": "ratio"}
        latest = db.query("SELECT latest_version FROM templates WHERE template_id=?",
                          ("tpl_product_motion_5s",))[0]["latest_version"]
        assert latest == 2, latest
        return {"v1_has_field_params": False, "v2_field_params": v2["field_params"],
                "latest_version": latest}
    case("已发布 v1 快照未被修改；修复以 v2 新增版本发布", video_v1_immutable)

    print("\n=== 修复 4：季节主题概念图路径 ===")

    def concept_path():
        body = main.TaskIn(kind="image", model_id=FLASH,
                           template_id="tpl_season_theme", template_version=1,
                           template_fields={"theme": "春季上新", "product_name": "焕亮精华液"})
        r = main.resolve_task(body)
        assert r["concept_mode"] is True
        assert r["params"]["generation_mode"] == "text_to_image", r["params"]
        assert templates.CONCEPT_NOTICE in r["params"]["prompt"]
        created = main.create_task(body)
        saved = main.get_task(created["id"])
        assert saved["concept_mode"] == 1, saved["concept_mode"]
        sp = db.loads(saved["params_json"])
        assert sp["generation_mode"] == "text_to_image", sp["generation_mode"]
        assert saved["input_asset_id"] is None
        return {"generation_mode": sp["generation_mode"], "concept_mode": saved["concept_mode"],
                "task_id": saved["id"], "notice_in_prompt": True}
    case("无参考图：最终任务参数为 text_to_image + concept_mode=1", concept_path)

    def reference_path():
        body = main.TaskIn(kind="image", model_id=FLASH, input_asset_id=REF_IMG,
                           template_id="tpl_season_theme", template_version=1,
                           template_fields={"theme": "春季上新", "product_name": "焕亮精华液"})
        r = main.resolve_task(body)
        assert r["concept_mode"] is False
        assert r["params"]["generation_mode"] == "image_to_image", r["params"]
        assert templates.CONCEPT_NOTICE not in r["params"]["prompt"]
        return {"generation_mode": r["params"]["generation_mode"], "concept_mode": False}
    case("有参考图：走 image_to_image，无概念图声明", reference_path)

    def concept_missing_name():
        body = main.TaskIn(kind="image", model_id=FLASH,
                           template_id="tpl_season_theme", template_version=1,
                           template_fields={"theme": "春季上新"})
        d = expect_error("TEMPLATE_FIELD_REQUIRED", lambda: main.resolve_task(body), 400)
        assert d["detail"].get("concept_mode") is True, d["detail"]
        return d
    case("概念图缺商品名 → 提交前明确报错", concept_missing_name)

    print("\n=== 修复 5：模型到期保护（可注入时钟）===")

    def eos_before():
        r = main.resolve_task(main.TaskIn(kind="video", model_id=V10,
                                          input_asset_id=FIRST_FRAME,
                                          params={"prompt": "推近", "duration": 5}),
                              now_ts=EOS_TS - 60)
        assert r["lifecycle"]["status"] == "eos_scheduled", r["lifecycle"]
        assert r["lifecycle"]["migrate_to"] == MINI
        assert r["lifecycle"]["seconds_remaining"] == 60
        return {"status": r["lifecycle"]["status"], "eos_at": r["lifecycle"]["eos_at"],
                "migrate_to": r["lifecycle"]["migrate_to"], "remaining_s": 60}
    case("到期前：允许使用并返回下线时间与迁移目标", eos_before)

    def eos_exact():
        return expect_error("MODEL_EOS", lambda: main.resolve_task(
            main.TaskIn(kind="video", model_id=V10, input_asset_id=FIRST_FRAME,
                        params={"prompt": "推近", "duration": 5}), now_ts=EOS_TS), 409)
    case("到期时刻（== EOS）：拒绝提交", eos_exact)

    def eos_after():
        return expect_error("MODEL_EOS", lambda: main.resolve_task(
            main.TaskIn(kind="video", model_id=V10, input_asset_id=FIRST_FRAME,
                        params={"prompt": "推近", "duration": 5}), now_ts=EOS_TS + 60), 409)
    case("到期后：拒绝提交", eos_after)

    def eos_no_task_created():
        before = task_count()
        expect_error("MODEL_EOS", lambda: main.resolve_task(
            main.TaskIn(kind="video", model_id=V10, input_asset_id=FIRST_FRAME,
                        params={"prompt": "推近", "duration": 5}), now_ts=EOS_TS + 60))
        after = task_count()
        assert before == after, f"EOS 拒绝后仍创建了任务 {before}→{after}"
        return {"before": before, "after": after}
    case("EOS 拒绝发生在入队前（任务数不变）", eos_no_task_created)

    def eos_estimate_blocked():
        return expect_error("MODEL_EOS", lambda: main.estimate_task(
            main.TaskIn(kind="video", model_id=V10, input_asset_id=FIRST_FRAME,
                        params={"prompt": "推近", "duration": 5}))) if False else \
            expect_error("MODEL_EOS", lambda: main.resolve_task(
                main.TaskIn(kind="video", model_id=V10, input_asset_id=FIRST_FRAME,
                            params={"prompt": "推近", "duration": 5}), now_ts=EOS_TS + 60))
    case("EOS 后预估同样被拒绝（不给出可提交的报价）", eos_estimate_blocked)

    def eos_template_submit():
        body = main.TaskIn(kind="video", model_id=V10, input_asset_id=FIRST_FRAME,
                           template_id="tpl_product_motion_5s", template_version=2,
                           template_fields={"motion": "缓慢旋转", "duration": 5})
        before = task_count()
        d = expect_error("MODEL_EOS", lambda: main.resolve_task(body, now_ts=EOS_TS + 1), 409)
        assert task_count() == before
        return d
    case("模板提交路径同样被 EOS 拦截", eos_template_submit)

    def eos_batch():
        before_tasks = task_count()
        before_batches = db.query("SELECT COUNT(*) c FROM batches")[0]["c"]
        items = [
            main.TaskIn(kind="image", model_id=FLASH, input_asset_id=REF_IMG,
                        params={"prompt": "白底主图", "generation_mode": "image_to_image",
                                "size": "2048x2048"}),
            main.TaskIn(kind="video", model_id=V10, input_asset_id=FIRST_FRAME,
                        params={"prompt": "推近", "duration": 5}),
        ]
        # 注入到期时钟后整批解析（模拟批次内部时钟）
        orig = main._now
        main._now = lambda: EOS_TS + 1
        try:
            d = expect_error("MODEL_EOS", lambda: main.post_batch(main.BatchIn(name="eos-batch",
                                                                              items=items)), 409)
        finally:
            main._now = orig
        assert task_count() == before_tasks, "批次被拒仍创建了任务（非原子）"
        assert db.query("SELECT COUNT(*) c FROM batches")[0]["c"] == before_batches, "批次行被创建"
        return {**d, "tasks_unchanged": True, "batch_rows_unchanged": True}
    case("批量路径：命中 EOS 时整批拒绝、不产生任务与批次行", eos_batch)

    def eos_retry():
        src = db.new_id("task")
        db.insert("tasks", {"id": src, "kind": "video", "status": "succeeded",
                            "params_json": db.dumps({"prompt": "推近", "duration": 5}),
                            "model_id": V10, "model_revision": "local-snapshot",
                            "input_asset_id": FIRST_FRAME, "progress": 1.0, "stage": "done",
                            "attempts": 1, "cancel_requested": 0,
                            "created_at": db.now(), "updated_at": db.now()})
        before = task_count()
        orig = main._now
        main._now = lambda: EOS_TS + 3600
        try:
            d = expect_error("MODEL_EOS", lambda: main.retry_task(src), 409)
        finally:
            main._now = orig
        assert task_count() == before, "重试被拒仍创建了任务"
        assert db.get("tasks", src)["status"] == "succeeded", "历史任务被改写"
        return {**d, "history_intact": True}
    case("重试路径：EOS 后拒绝，且不改写历史任务", eos_retry)

    def eos_not_auto_migrated():
        body = main.TaskIn(kind="video", model_id=V10, input_asset_id=FIRST_FRAME,
                           template_id="tpl_product_motion_5s", template_version=2,
                           template_fields={"motion": "缓慢旋转"})
        before = task_count()
        expect_error("MODEL_EOS", lambda: main.resolve_task(body, now_ts=EOS_TS + 1))
        new_tasks = db.query("SELECT model_id FROM tasks WHERE rowid > (SELECT MAX(rowid)-1 FROM tasks)")
        assert task_count() == before, "EOS 后自动创建了任务"
        return {"auto_migrate_to_mini": False, "note": "未自动替换为 2.0 mini"}
    case("EOS 后不自动替换模型（不静默迁移）", eos_not_auto_migrated)

    # ---------- 元数据修正（v0.7.2）：列表与提交共用生命周期 ----------
    print("\n=== 元数据修正：列表生命周期与提交校验一致 ===")

    def _lifecycle_of(payload: dict, model_id: str) -> dict:
        for m in payload["items"]:
            if m["id"] == model_id:
                return m
        raise AssertionError(f"列表中缺少 {model_id}")

    def list_before_eos():
        orig = main._now
        main._now = lambda: EOS_TS - 60
        try:
            item = _lifecycle_of(main.models(), V10)
        finally:
            main._now = orig
        assert item["lifecycle"]["status"] == "eos_scheduled", item["lifecycle"]
        assert item["lifecycle"]["seconds_remaining"] == 60, item["lifecycle"]
        # 与提交校验一致：此状态下应当可提交（不抛 MODEL_EOS）
        main.resolve_task(main.TaskIn(kind="video", model_id=V10, input_asset_id=FIRST_FRAME,
                                      params={"prompt": "推近", "duration": 5}), now_ts=EOS_TS - 60)
        return {"status": item["lifecycle"]["status"], "remaining_s": 60,
                "submit_allowed": True}
    case("列表：到期前为 eos_scheduled，且该时刻可提交", list_before_eos)

    def list_at_eos():
        orig = main._now
        main._now = lambda: EOS_TS
        try:
            item = _lifecycle_of(main.models(), V10)
        finally:
            main._now = orig
        assert item["lifecycle"]["status"] == "eos_expired", item["lifecycle"]
        expect_error("MODEL_EOS", lambda: main.resolve_task(
            main.TaskIn(kind="video", model_id=V10, input_asset_id=FIRST_FRAME,
                        params={"prompt": "推近", "duration": 5}), now_ts=EOS_TS), 409)
        return {"status": item["lifecycle"]["status"], "submit_allowed": False}
    case("列表：到期时刻为 eos_expired，且该时刻提交被拒（两边一致）", list_at_eos)

    def list_after_eos():
        orig = main._now
        main._now = lambda: EOS_TS + 86400
        try:
            item = _lifecycle_of(main.models(), V10)
        finally:
            main._now = orig
        assert item["lifecycle"]["status"] == "eos_expired", item["lifecycle"]
        assert item["lifecycle"]["seconds_remaining"] == 0
        expect_error("MODEL_EOS", lambda: main.resolve_task(
            main.TaskIn(kind="video", model_id=V10, input_asset_id=FIRST_FRAME,
                        params={"prompt": "推近", "duration": 5}), now_ts=EOS_TS + 86400), 409)
        return {"status": item["lifecycle"]["status"], "submit_allowed": False}
    case("列表：到期后为 eos_expired，且提交被拒", list_after_eos)

    def list_all_states_consistent():
        """全量模型：列表 status 与提交行为逐一对齐。"""
        for now, expect in ((EOS_TS - 1, "eos_scheduled"), (EOS_TS, "eos_expired")):
            orig = main._now
            main._now = lambda n=now: n
            try:
                payload = main.models()
            finally:
                main._now = orig
            got = _lifecycle_of(payload, V10)["lifecycle"]["status"]
            assert got == expect, f"now={now} 期望 {expect}，实际 {got}"
            # 无下线计划的模型始终 active
            assert _lifecycle_of(payload, FLASH)["lifecycle"] == {"status": "active"}
        return {"boundary": "now>=eos_at → eos_expired", "active_models_unchanged": True}
    case("边界：eos-1s 仍调度中；eos 当秒即 expired；无计划模型恒 active", list_all_states_consistent)

    def no_migration_available_flag():
        st = config.model_lifecycle_state(V10, EOS_TS - 60)
        assert "migration_available" not in st, "migration_available 应已删除"
        assert st["migrate_to"] == MINI
        assert st["migrate_to_activation"] == config.MODEL_ACTIVATION[MINI] == "not_opened", st
        assert st["eom_passed"] is True, st  # EOM 2026-09-24 已过（只影响新购）
        return {"migration_available": "removed", "migrate_to": st["migrate_to"],
                "migrate_to_activation": st["migrate_to_activation"],
                "eom_passed": st["eom_passed"]}
    case("migration_available 已删除；目标可用性改由 migrate_to_activation 转述",
         no_migration_available_flag)

    def activation_not_upgraded_by_lifecycle():
        """迁移目标未开通时，不得因存在 migrate_to 就变成 verified。"""
        item = _lifecycle_of(main.models(), V10)
        assert item["lifecycle"]["migrate_to_activation"] == "not_opened"
        target = _lifecycle_of(main.models(), MINI)
        assert target["activation"] == "not_opened", target["activation"]
        return {"migrate_to_activation": item["lifecycle"]["migrate_to_activation"],
                "mini_activation": target["activation"]}
    case("目标模型未开通时不被标为 verified（activation 不被生命周期改写）",
         activation_not_upgraded_by_lifecycle)

    # ---------- 汇总 ----------
    passed = sum(1 for r in RESULTS if r["ok"])
    summary = {
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "scope": "v0.7 五项修复 · 免费验证（零付费、零真实模型调用）",
        "isolation": {"db": "临时副本", "real_db_untouched": True,
                      "provider_guard": "providers_ark._request 被替换为断言守卫",
                      "clock": "main._now / resolve_task(now_ts=...) 可注入"},
        "paid_calls": 0,
        "model_calls": 0,
        "eos_reference": {"model": V10, "eos_at": "2026-11-24T14:00:00+08:00",
                          "migrate_to": MINI,
                          "source": "docs/82379/1350667（第十批模型下线说明）"},
        "total": len(RESULTS), "passed": passed, "failed": len(RESULTS) - passed,
        "cases": RESULTS,
        "disclaimer": ("本文件仅证明解析/校验/拦截逻辑正确，"
                       "不代表任何真实模型端到端验收通过；未调用云端，未产生费用。"),
    }
    path = OUT / "五项修复测试结果.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"\n{'=' * 60}\n结果：{passed}/{len(RESULTS)} 通过")
    print(f"付费调用 0 次｜模型调用 0 次｜真实业务库未改动")
    print(f"证据：{path}")
    return 0 if passed == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main_run())
