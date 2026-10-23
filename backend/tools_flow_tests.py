"""队列/批量/取消/重试 流程验证（使用 fake-image 假引擎，不产生任何真实推理）。"""
import json, os, time, urllib.request

BASE = "http://127.0.0.1:8100/api"

def call(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)

def submit(**params):
    _, r = call("/tasks", "POST", {"kind": "image", "model_id": "fake-image", "params": params})
    return r["task_id"]

def wait(task_id, timeout=90):
    t0 = time.time()
    while time.time() - t0 < timeout:
        _, t = call(f"/tasks/{task_id}")
        if t["status"] in ("succeeded", "failed", "canceled", "interrupted"):
            return t
        time.sleep(0.4)
    return call(f"/tasks/{task_id}")[1]

out = {"note": "使用 fake-image 假引擎，仅验证流程语义，不代表真实模型能力", "cases": {}}

# A. 批量：3 个任务顺序执行
ts = [submit(prompt=f"batch{i}", width=64, height=64, steps=6, fake_step_delay=0.3) for i in range(3)]
_, b = call("/batches", "POST", {"name": "flow-batch", "items": [
    {"kind": "image", "model_id": "fake-image", "params": {"prompt": "b", "width": 64, "height": 64, "steps": 4, "fake_step_delay": 0.2}}
    for _ in range(2)]})
done = [wait(t) for t in ts]
out["cases"]["A_batch_sequential"] = {
    "task_ids": ts,
    "statuses": [d["status"] for d in done],
    "started_at": [round(d["started_at"], 3) for d in done if d.get("started_at")],
    "finished_at": [round(d["finished_at"], 3) for d in done if d.get("finished_at")],
    "no_overlap": all(done[i]["finished_at"] <= done[i + 1]["started_at"] + 0.5
                      for i in range(len(done) - 1) if done[i].get("finished_at") and done[i + 1].get("started_at")),
    "batch_api": {"batch_id": b.get("batch_id"), "task_ids": b.get("task_ids")},
}

# B. 排队中取消
q1 = submit(prompt="q1", width=64, height=64, steps=20, fake_step_delay=0.4)
q2 = submit(prompt="q2", width=64, height=64, steps=20, fake_step_delay=0.4)
_, c = call(f"/tasks/{q2}/cancel", "POST")
time.sleep(0.2)
_, t2 = call(f"/tasks/{q2}")
out["cases"]["B_cancel_queued"] = {"cancel_response": c, "task_status": t2["status"], "stage": t2["stage"]}
call(f"/tasks/{q1}/cancel", "POST")
wait(q1)

# C. 运行中取消
r1 = submit(prompt="run-cancel", width=64, height=64, steps=40, fake_step_delay=0.4)
time.sleep(2.0)
_, before = call(f"/tasks/{r1}")
_, c2 = call(f"/tasks/{r1}/cancel", "POST")
after = wait(r1, timeout=30)
out["cases"]["C_cancel_running"] = {
    "status_before_cancel": before["status"], "stage_before": before["stage"],
    "cancel_response": c2, "final_status": after["status"], "final_stage": after["stage"],
    "elapsed_ms": after.get("latency_ms"), "cancel_requested_flag": after.get("cancel_requested"),
}

# D. 失败 + 重试
f1 = submit(prompt="fail-inject", width=64, height=64, steps=6, fake_step_delay=0.2, fail_at_step=2)
fd = wait(f1)
_, rt = call(f"/tasks/{f1}/retry", "POST")
rd = wait(rt["task_id"])
out["cases"]["D_fail_then_retry"] = {
    "failed_task": {"id": f1, "status": fd["status"], "code": fd["error_code"], "message": fd["error_message"]},
    "retry_task": {"id": rt["task_id"], "retry_of": rt.get("retry_of"), "final_status": rd["status"]},
}

# E. 取消终态任务应被拒绝
_, rej = call(f"/tasks/{f1}/cancel", "POST")
out["cases"]["E_cancel_rejected_terminal"] = {"http_body": rej}

os.makedirs("docs/evidence/阶段2", exist_ok=True)
with open("docs/evidence/阶段2/flow-tests.json", "w") as f:
    json.dump(out, f, ensure_ascii=False, indent=2)
print(json.dumps(out, ensure_ascii=False, indent=2)[:2400])
