#!/usr/bin/env python3
"""阶段 B：付费冒烟（严格按批准清单，5 次调用，上限 5.00 元）。

用户批准范围（2026-10-23）：
  B-1 图生图回归   flash      2048×2048      ¥0.12
  B-2 文生图探针   pro        2048×2048      ¥0.60
  B-3 图生图探针   pro        2048×2048      ¥0.60
  B-4 视频回归     1.0 pro fast 704×1248/5s  ≈¥0.45
  B-5 视频探针     2.0 mini    704×1248/5s   ≈¥2.48
  阶段 B 支出上限 5.00 元；阶段 C 与总预算 15 元**未批准**。

硬约束（脚本内强制）：
  - 只跑清单内 5 项，顺序执行，**不重试**、不并发、不扩大调用；
  - 累计估算超 5.00 元立即中止；
  - 任何一项失败即记录真实错误码并继续下一项（失败项不计费），不自动补跑；
  - 不修改历史任务；不把候选模型写成已接通。

用法：
    /path/to/python backend/tools_stage_b_smoke.py            # 预演，只打印清单不提交
    /path/to/python backend/tools_stage_b_smoke.py --execute  # 真正执行 5 次付费调用
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE = "http://127.0.0.1:8100"
ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "evidence" / "阶段B-20261023"
STAGE_B_CAP_CNY = 5.00

REF_ASSET = "asset_d73231ab405540b68eb6"           # 单瓶参考图（已有真实素材）
FIRST_FRAME = "asset_cd91e2f9dda14123a622"         # 已有 2048×2048 商品图（作视频首帧）

PROMPT_IMG2IMG = ("保留参考图中唯一白色瓶子的外形、颜色、瓶盖和标识，仅把背景换成浅米色摄影棚背景，"
                  "柔和侧光，不添加其他商品或文字。")
PROMPT_TXT2IMG = "白色背景护肤品主图，柔和棚光，构图留白，电商产品摄影"
PROMPT_VIDEO = "商品静止展示，镜头缓慢推近，光线自然，不添加其他商品"

ITEMS = [
    {"id": "B-1", "label": "图生图回归", "kind": "image",
     "model_id": "doubao-seedream-5-0-flash-260915",
     "input_asset_id": REF_ASSET,
     "params": {"prompt": PROMPT_IMG2IMG, "generation_mode": "image_to_image", "size": "2048x2048"},
     "est": 0.12},
    {"id": "B-2", "label": "文生图探针（验证 pro 开通）", "kind": "image",
     "model_id": "doubao-seedream-5-0-pro-260628",
     "input_asset_id": None,
     "params": {"prompt": PROMPT_TXT2IMG, "generation_mode": "text_to_image", "size": "2048x2048"},
     "est": 0.60},
    {"id": "B-3", "label": "图生图探针（验证 pro 图生图）", "kind": "image",
     "model_id": "doubao-seedream-5-0-pro-260628",
     "input_asset_id": REF_ASSET,
     "params": {"prompt": PROMPT_IMG2IMG, "generation_mode": "image_to_image", "size": "2048x2048"},
     "est": 0.60},
    {"id": "B-4", "label": "视频回归", "kind": "video",
     "model_id": "doubao-seedance-1-0-pro-fast-251015",
     "input_asset_id": FIRST_FRAME,
     "params": {"prompt": PROMPT_VIDEO, "duration": 5, "ratio": "9:16",
                "resolution": "720p", "generate_audio": False},
     "est": 0.45},
    {"id": "B-5", "label": "视频探针（验证 2.0 mini 开通）", "kind": "video",
     "model_id": "doubao-seedance-2-0-mini-260615",
     "input_asset_id": FIRST_FRAME,
     "params": {"prompt": PROMPT_VIDEO, "duration": 5, "ratio": "9:16",
                "resolution": "720p", "generate_audio": False},
     "est": 2.48},
]

TERMINAL = {"succeeded", "failed", "canceled", "interrupted"}


def http(method: str, path: str, body: dict | None = None, timeout: int = 60) -> tuple[int, dict]:
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
            return e.code, {"raw": raw[:300].decode("utf-8", "ignore")}


def wait_task(task_id: str, kind: str) -> dict:
    limit = 240 if kind == "image" else 1500
    t0 = time.time()
    last = {}
    while time.time() - t0 < limit:
        st, task = http("GET", f"/api/tasks/{task_id}")
        if st != 200:
            return {"status": "query_failed", "http": st, "raw": task}
        last = task
        if task.get("status") in TERMINAL:
            return task
        time.sleep(5)
    return {**last, "status": "local_poll_timeout"}


def main() -> int:
    execute = "--execute" in sys.argv
    OUT.mkdir(parents=True, exist_ok=True)

    total_est = round(sum(i["est"] for i in ITEMS), 2)
    print("=== 阶段 B 清单（批准上限 ¥%.2f）===" % STAGE_B_CAP_CNY)
    for i in ITEMS:
        print(f"  {i['id']}  {i['label']:<28} {i['model_id'][:44]:<44} est ¥{i['est']:.2f}")
    print(f"  合计估算 ¥{total_est:.2f}")
    if total_est > STAGE_B_CAP_CNY:
        print("清单估算超上限，已中止（未提交任何调用）")
        return 2
    if not execute:
        print("\n[预演模式] 未提交任何调用。加 --execute 才会真正付费执行。")
        return 0

    st, usage0 = http("GET", "/api/usage")
    print(f"\n执行前：今日已用 ¥{usage0.get('spent_today_cny')} / 预算上限 ¥{usage0.get('budget_cny')}")

    results = []
    spent_stage = 0.0
    run_id = time.strftime("%Y%m%d-%H%M%S")

    for item in ITEMS:
        if spent_stage + item["est"] > STAGE_B_CAP_CNY:
            results.append({**{k: item[k] for k in ("id", "label", "model_id")},
                            "status": "skipped_over_cap", "cost_cny": 0.0})
            print(f"  {item['id']} 跳过：再加 ¥{item['est']:.2f} 会超阶段上限")
            continue

        print(f"\n>>> {item['id']} {item['label']}")
        body = {
            "kind": item["kind"], "model_id": item["model_id"],
            "params": item["params"], "input_asset_id": item["input_asset_id"],
            "client_request_id": f"stageb-{run_id}-{item['id']}-{uuid.uuid4().hex[:8]}",
            "external_request_id": f"STAGEB-{run_id}-{item['id']}",
            "caller_id": "stage_b_smoke",
        }
        st, created = http("POST", "/api/tasks", body)
        rec = {"id": item["id"], "label": item["label"], "model_id": item["model_id"],
               "spec": {k: item["params"].get(k) for k in
                        ("generation_mode", "size", "duration", "ratio", "resolution") if k in item["params"]},
               "create_http": st, "estimated_cost_cny": item["est"]}
        if st != 200:
            rec.update({"status": "rejected", "error": created.get("error")})
            results.append(rec)
            print(f"    被拒绝：{(created.get('error') or {}).get('code')} "
                  f"{(created.get('error') or {}).get('message', '')[:120]}")
            continue

        task_id = created["task_id"]
        rec.update({"task_id": task_id,
                    "server_estimated_cost_cny": created.get("estimated_cost_cny")})
        print(f"    task_id={task_id} 服务端估算 ¥{created.get('estimated_cost_cny')}，等待完成…")
        task = wait_task(task_id, item["kind"])
        cost = float(task.get("cost_cny") or 0.0)
        spent_stage += cost
        rec.update({
            "status": task.get("status"),
            "cost_cny": cost,
            "latency_ms": task.get("latency_ms"),
            "output_asset_id": task.get("output_asset_id"),
            "width": task.get("width"), "height": task.get("height"),
            "duration_ms": task.get("duration_ms"),
            "remote_task_id": task.get("remote_task_id"),
            "usage_json": task.get("usage_json"),
            "error_code": task.get("error_code"),
            "error_message": (task.get("error_message") or "")[:300] or None,
        })
        results.append(rec)
        print(f"    结果：{task.get('status')} 费用 ¥{cost:.2f}"
              f" 耗时 {task.get('latency_ms')}ms"
              + (f" 错误 {task.get('error_code')}" if task.get("error_code") else ""))
        time.sleep(2)

    st, usage1 = http("GET", "/api/usage")
    summary = {
        "date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "stage": "B", "run_id": run_id,
        "approved_cap_cny": STAGE_B_CAP_CNY,
        "estimated_total_cny": total_est,
        "actual_stage_b_cost_cny": round(spent_stage, 4),
        "spent_today_before_cny": usage0.get("spent_today_cny"),
        "spent_today_after_cny": usage1.get("spent_today_cny"),
        "budget_limit_cny": usage1.get("budget_cny"),
        "retries_performed": 0,
        "note": "阶段 B 内未重试、未并发、未扩大调用；失败项按官方规则不计费。",
        "items": results,
    }
    path = OUT / f"阶段B冒烟-{run_id}.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n=== 阶段 B 汇总 ===")
    print(f"阶段 B 实际费用：¥{summary['actual_stage_b_cost_cny']:.2f} / 上限 ¥{STAGE_B_CAP_CNY:.2f}")
    print(f"今日累计（API 统计）：¥{summary['spent_today_after_cny']}")
    print(f"证据：{path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
