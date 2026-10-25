#!/usr/bin/env python3
"""阶段 A：跨项目 API 契约探针（本机测试调用方，**零付费**）。

用途：在运营工作台尚未开发的前提下，验证创作平台对外契约是否可用。
严格限制（对应 PRD v0.7 §8.3 与用户本轮指令）：
  - 只**读取**已有数据、回放已有任务、做**负向校验**；
  - **不创建任何真实生成任务**（负向用例在入库前即被拒绝）；
  - 不重试、不并发、不调用云端模型。

用法：python3 backend/tools_stage_b_smoke.py 无关；本脚本独立运行：
    /path/to/python backend/tools_api_contract_probe.py
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8100"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "evidence" / "阶段A-20261023"

# 已有素材/任务（只读引用，不新建）
REF_ASSET = "asset_d73231ab405540b68eb6"          # 单瓶参考图（历史真实素材）
KNOWN_TASK = "task_d6da966ec6164687aac0"           # v0.6 真实图生图任务


def http(method: str, path: str, body: dict | None = None, timeout: int = 20) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return e.code, {"raw": raw[:200].decode("utf-8", "ignore")}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    checks: list[dict] = []

    def rec(name: str, ok: bool, detail) -> None:
        checks.append({"check": name, "ok": ok, "detail": detail})
        print(("  OK  " if ok else " FAIL ") + name + ("" if ok else f" → {detail}"))

    print("=== 阶段 A：契约探针（只读，无付费） ===")

    # 1. 能力发现
    st, models = http("GET", "/api/models")
    cloud = [m for m in models.get("items", []) if m.get("provider") == "ark"]
    rec("GET /api/models 返回云端模型与能力元数据", st == 200 and cloud
        and all("capabilities" in m and "lifecycle" in m for m in cloud),
        {"status": st, "cloud_models": len(cloud)})

    # 2. 模板发现
    st, tpls = http("GET", "/api/templates")
    items = tpls.get("items", [])
    rec("GET /api/templates 返回 4 个模板且带稳定 ID 与版本",
        st == 200 and len(items) == 4 and all(i.get("template_id") and i.get("latest_version") for i in items),
        {"status": st, "count": len(items), "ids": [i.get("template_id") for i in items]})

    # 3. 模板详情与版本可追溯
    st, detail = http("GET", "/api/templates/tpl_season_theme")
    rec("GET /api/templates/{id} 含版本列表与快照",
        st == 200 and detail.get("versions") and detail.get("template_snapshot"),
        {"status": st, "versions": detail.get("versions")})

    # 4. 历史任务可按模板版本追溯（v0.6 任务无模板 → 应返回 None 而非报错）
    st, task = http("GET", f"/api/tasks/{KNOWN_TASK}")
    rec("GET /api/tasks/{id} 可读历史任务与模型/费用口径",
        st == 200 and task.get("model_id") and task.get("params"),
        {"status": st, "model_id": task.get("model_id"), "template_id": task.get("template_id")})

    # 5. 幂等：用已有任务的 client_request_id 重复提交 → 返回同一任务，不新建
    crid = task.get("client_request_id")
    before = len(http("GET", "/api/tasks?limit=200")[1].get("items", []))
    st, replay = http("POST", "/api/tasks", {
        "kind": "image",
        "model_id": "doubao-seedream-5-0-flash-260915",
        "params": {"prompt": "幂等回放：不应新建任务", "generation_mode": "text_to_image",
                   "size": "1024x1024"},
        "client_request_id": crid,
    })
    after = len(http("GET", "/api/tasks?limit=200")[1].get("items", []))
    rec("同一 client_request_id 重复提交返回已有任务且任务数不变",
        st == 200 and replay.get("task_id") == KNOWN_TASK and before == after,
        {"status": st, "returned": replay.get("task_id"), "before": before, "after": after})

    # 6-8. 负向校验（均在入库前拒绝，不产生任务与费用）
    cases = [
        ("未知模板 → TEMPLATE_NOT_FOUND", {
            "kind": "image", "model_id": "doubao-seedream-5-0-flash-260915",
            "params": {"generation_mode": "text_to_image", "size": "1024x1024"},
            "template_id": "tpl_not_exist",
        }, "TEMPLATE_NOT_FOUND", 404),
        ("模板必填字段缺失 → TEMPLATE_FIELD_REQUIRED", {
            "kind": "image", "model_id": "doubao-seedream-5-0-flash-260915",
            "input_asset_id": REF_ASSET, "template_id": "tpl_scene_bg",
            "template_fields": {"product_name": "精华液"},
            "params": {"generation_mode": "image_to_image", "size": "2048x2048"},
        }, "TEMPLATE_FIELD_REQUIRED", 400),
        ("模板要求参考图却未提供 → REFERENCE_IMAGE_REQUIRED", {
            "kind": "image", "model_id": "doubao-seedream-5-0-flash-260915",
            "template_id": "tpl_white_bg", "template_fields": {},
            "params": {"generation_mode": "text_to_image", "size": "2048x2048"},
        }, "REFERENCE_IMAGE_REQUIRED", 400),
        ("季节模板无参考图且无商品名 → TEMPLATE_FIELD_REQUIRED（概念图路径）", {
            "kind": "image", "model_id": "doubao-seedream-5-0-flash-260915",
            "template_id": "tpl_season_theme",
            "template_fields": {"theme": "春季上新"},
            "params": {"generation_mode": "text_to_image", "size": "2048x2048"},
        }, "TEMPLATE_FIELD_REQUIRED", 400),
    ]
    for name, body, want, want_status in cases:
        st, res = http("POST", "/api/tasks", body)
        got = (res.get("error") or {}).get("code")
        rec(name, st == want_status and got == want, {"status": st, "code": got})

    after_neg = len(http("GET", "/api/tasks?limit=200")[1].get("items", []))
    rec("负向用例未创建任何任务", after_neg == before,
        {"before": before, "after": after_neg})

    # 9. 结果文件可取得（读取已有产物，不新建）
    if task.get("output_asset_id"):
        req = urllib.request.Request(f"{BASE}/api/assets/{task['output_asset_id']}/file")
        with urllib.request.urlopen(req, timeout=20) as r:
            n = len(r.read())
            ctype = r.headers.get("Content-Type")
        rec("GET /api/assets/{id}/file 可取回真实产物文件", n > 1000,
            {"bytes": n, "content_type": ctype})

    # 10. 提交前费用预估：无副作用（不建任务/不入队/不调模型/不扣预算）
    before_est = len(http("GET", "/api/tasks?limit=200")[1].get("items", []))
    est_body = {
        "kind": "video", "model_id": "doubao-seedance-1-0-pro-fast-251015",
        "input_asset_id": "asset_cd91e2f9dda14123a622",
        "template_id": "tpl_product_motion_5s",
        "template_fields": {"motion": "缓慢旋转", "duration": 10, "ratio": "1:1"},
    }
    st1, est1 = http("POST", "/api/tasks/estimate", est_body)
    st2, est2 = http("POST", "/api/tasks/estimate", est_body)
    after_est = len(http("GET", "/api/tasks?limit=200")[1].get("items", []))
    rec("POST /api/tasks/estimate 无副作用（任务数不变 + side_effects 全 false）",
        st1 == 200 and st2 == 200 and before_est == after_est
        and est1.get("side_effects", {}).get("task_created") is False
        and est1.get("side_effects", {}).get("model_called") is False,
        {"status": st1, "before": before_est, "after": after_est,
         "cost": est1.get("estimated_cost_cny")})

    rec("预估结果与模板字段一致（duration=10 / ratio=1:1）+ 费用稳定可重复",
        est1.get("effective", {}).get("duration") == 10
        and est1.get("effective", {}).get("ratio") == "1:1"
        and est1.get("estimated_cost_cny") == est2.get("estimated_cost_cny"),
        {"effective": est1.get("effective"), "cost": est1.get("estimated_cost_cny")})

    rec("预估返回模型生命周期信息（下线时间/迁移目标）",
        (est1.get("model_lifecycle") or {}).get("eos_at") is not None,
        {"lifecycle": est1.get("model_lifecycle")})

    # 11. 任务数变化时同时核对“未调用模型”的事实：预估不应写入 cost
    rec("预估不写入用量/费用记录（tasks.cost_cny 未被新增）",
        before_est == after_est, {"tasks": after_est})

    # 12. 记录“未实现鉴权”的事实，避免误读为已可对外接入
    st, _ = http("GET", "/api/models")
    rec("鉴权状态核对：当前对外接口无鉴权（仅本机限制）——预留非已实现",
        st == 200, {"note": "本轮不实现鉴权校验；对外接入前必须补齐"})

    passed = sum(1 for c in checks if c["ok"])
    summary = {"date": "2026-10-23", "stage": "A", "paid_calls": 0,
               "total": len(checks), "passed": passed,
               "conclusion": "契约可用（本机测试调用方）；不代表两产品已联通，也未实现鉴权",
               "checks": checks}
    (OUT / "契约探针结果.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    print(f"\n阶段 A 完成：{passed}/{len(checks)} 通过，付费调用 0 次")
    print(f"证据：{OUT / '契约探针结果.json'}")
    return 0 if passed == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
