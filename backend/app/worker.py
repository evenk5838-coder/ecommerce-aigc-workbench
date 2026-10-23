"""持久化队列 + 单 worker + 取消 + 重启恢复。"""
from __future__ import annotations

import threading
import time

from . import config, db
from . import storage
from .engines import GUARD, MANAGER, CancelRequested, CancelToken, MemSampler, generate_image, generate_video
from .errors import AppError, err

TOKENS: dict[str, CancelToken] = {}
_stop = threading.Event()
_thread: threading.Thread | None = None
SAMPLER = MemSampler(GUARD)

ACTIVE = ("loading", "running", "postprocess")


def claim_next() -> dict | None:
    """原子领取一个 queued 任务。"""
    with db.conn() as c:
        c.execute("BEGIN IMMEDIATE")
        row = c.execute(
            "SELECT * FROM tasks WHERE status='queued' ORDER BY created_at LIMIT 1"
        ).fetchone()
        if not row:
            c.execute("COMMIT")
            return None
        task = dict(row)
        c.execute(
            "UPDATE tasks SET status='loading', started_at=?, updated_at=?, attempts=attempts+1, stage='loading' WHERE id=?",
            (db.now(), db.now(), task["id"]),
        )
        c.execute("COMMIT")
    task["attempts"] = task.get("attempts", 0) + 1
    return task


def _set(task_id: str, **fields) -> None:
    db.update("tasks", task_id, {**fields, "updated_at": db.now()})


def execute(task: dict) -> None:
    task_id = task["id"]
    params = db.loads(task["params_json"]) or {}
    model_id = task["model_id"]
    token = TOKENS.setdefault(task_id, CancelToken())
    SAMPLER.start(task_id)
    started = time.time()

    def progress(p: float) -> None:
        _set(task_id, progress=round(min(max(p, 0), 1), 3), stage="running")

    try:
        _set(task_id, status="running", stage="running")
        token.raise_if_canceled()

        meta = config.MODEL_REGISTRY.get(model_id, {})
        if meta.get("provider") == "ark":
            from . import cloud_tasks

            params = {**params, "input_asset_id": params.get("input_asset_id") or task.get("input_asset_id")}
            runner = cloud_tasks.run_image if task["kind"] == "image" else cloud_tasks.run_video
            res = runner(task_id, model_id, params, token, on_progress=progress)
            peak = SAMPLER.stop()
            asset = storage.save_bytes(
                res["data"], res["kind"], "cloud_generated",
                meta={"model_id": model_id, "provider": "ark", "prompt": params.get("prompt"),
                      "task_id": task_id, "remote_task_id": res.get("remote_task_id"),
                      "usage": res.get("usage"), "cost_cny": res.get("cost_cny"),
                      "seed": params.get("seed")},
            )
            _set(task_id, status="succeeded", stage="done", output_asset_id=asset["id"], progress=1.0,
                 peak_mem_mb=peak, load_ms=res.get("load_ms"), latency_ms=res.get("latency_ms"),
                 width=asset.get("width"), height=asset.get("height"), frames=asset.get("frames"),
                 fps=asset.get("fps"), duration_ms=asset.get("duration_ms"),
                 usage_json=db.dumps(res.get("usage") or {}), cost_cny=res.get("cost_cny"),
                 provider="ark", finished_at=db.now())
            db.insert("task_attempts", {
                "id": db.new_id("att"), "task_id": task_id, "attempt_no": task["attempts"],
                "started_at": started, "finished_at": db.now(), "result": "succeeded",
                "peak_mem_mb": peak, "latency_ms": res.get("latency_ms"),
            })
            return

        if task["kind"] == "image":
            res = generate_image(model_id, params, token, on_progress=progress)
            asset = storage.save_bytes(
                res["data"], "image", "generated",
                meta={"model_id": model_id, "prompt": params.get("prompt"), "seed": res["seed"],
                      "steps": res["steps"], "guidance": res["guidance"],
                      "precision": res.get("precision"), "task_id": task_id},
            )
        else:
            res = generate_video(model_id, params, token, on_progress=progress)
            _set(task_id, status="postprocess", stage="postprocess")
            asset = storage.save_bytes(
                res["data"], "video", "generated",
                meta={"model_id": model_id, "prompt": params.get("prompt"), "seed": res["seed"],
                      "steps": res["steps"], "fps": res["fps"], "frames": res["frames"],
                      "task_id": task_id, "probe_spec": params.get("probe_spec", False)},
            )

        peak = SAMPLER.stop()
        _set(
            task_id, status="succeeded", stage="done", output_asset_id=asset["id"],
            progress=1.0, peak_mem_mb=peak, load_ms=res.get("load_ms"),
            latency_ms=int((time.time() - started) * 1000),
            width=res.get("width"), height=res.get("height"),
            frames=res.get("frames"), fps=res.get("fps"), duration_ms=asset.get("duration_ms"),
            finished_at=db.now(),
        )
        db.insert("task_attempts", {
            "id": db.new_id("att"), "task_id": task_id, "attempt_no": task["attempts"],
            "started_at": started, "finished_at": db.now(), "result": "succeeded",
            "peak_mem_mb": peak, "latency_ms": int((time.time() - started) * 1000),
            "params_json": task["params_json"],
        })
    except CancelRequested:
        peak = SAMPLER.stop()
        _set(task_id, status="canceled", stage="canceled", peak_mem_mb=peak,
             latency_ms=int((time.time() - started) * 1000), finished_at=db.now(),
             error_code=None, error_message="用户取消（协作式，已在步/帧边界停止）")
        db.insert("task_attempts", {
            "id": db.new_id("att"), "task_id": task_id, "attempt_no": task["attempts"],
            "started_at": started, "finished_at": db.now(), "result": "canceled",
            "peak_mem_mb": peak,
        })
    except AppError as e:
        peak = SAMPLER.stop()
        _set(task_id, status="failed", stage="failed", error_code=e.code, error_message=e.message,
             peak_mem_mb=peak, latency_ms=int((time.time() - started) * 1000), finished_at=db.now())
        db.insert("task_attempts", {
            "id": db.new_id("att"), "task_id": task_id, "attempt_no": task["attempts"],
            "started_at": started, "finished_at": db.now(), "result": "failed",
            "error_code": e.code, "error_message": e.message, "peak_mem_mb": peak,
        })
    except Exception as e:  # noqa: BLE001
        peak = SAMPLER.stop()
        msg = f"{type(e).__name__}: {str(e)[:400]}"
        code = "MODEL_OOM" if "out of memory" in msg.lower() or "MPS backend out of memory" in msg else "INTERNAL_ERROR"
        _set(task_id, status="failed", stage="failed", error_code=code, error_message=msg,
             peak_mem_mb=peak, latency_ms=int((time.time() - started) * 1000), finished_at=db.now())
        db.insert("task_attempts", {
            "id": db.new_id("att"), "task_id": task_id, "attempt_no": task["attempts"],
            "started_at": started, "finished_at": db.now(), "result": "failed",
            "error_code": code, "error_message": msg, "peak_mem_mb": peak,
        })
    finally:
        TOKENS.pop(task_id, None)


def _loop() -> None:
    while not _stop.is_set():
        try:
            task = claim_next()
            if task is None:
                _stop.wait(config.POLL_INTERVAL)
                continue
            execute(task)
        except Exception:  # noqa: BLE001
            _stop.wait(1.0)


def start() -> None:
    global _thread
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="task-worker", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()
    if _thread:
        _thread.join(timeout=3)


def cancel(task_id: str) -> dict:
    task = db.get("tasks", task_id)
    if not task:
        raise err("TASK_NOT_FOUND", f"任务不存在：{task_id}")
    st = task["status"]
    if st == "queued":
        _set(task_id, status="canceled", stage="canceled", finished_at=db.now(),
             error_message="排队中取消（未占用算力）")
        return {"result": "canceled", "phase": "queued"}
    remote_id = task.get("remote_task_id")
    if remote_id:
        # 先向云端确认真实状态（本地 remote_status 可能已过期）
        try:
            from . import providers_ark as ark

            live = ark.video_query(remote_id)
            remote_status = (live.get("status") or "").lower()
            _set(task_id, remote_status=remote_status)
        except Exception:  # noqa: BLE001
            remote_status = (task.get("remote_status") or "").lower()
        if remote_status in ("created", "queued"):
            try:
                from . import providers_ark as ark

                ark.video_cancel(remote_id)
                _set(task_id, status="canceled", stage="canceled", remote_status="canceled",
                     finished_at=db.now(), error_message="云端排队中任务已取消（官方 DELETE）")
                return {"result": "canceled", "phase": "cloud_queued", "cloud_canceled": True,
                        "message": "云端任务已取消"}
            except Exception as e:  # noqa: BLE001
                _set(task_id, cancel_requested=1, stage="cancel_failed")
                return {"result": "cancel_failed", "phase": "cloud_queued", "cloud_canceled": False,
                        "message": f"云端取消失败：{getattr(e, 'message', str(e))[:120]}"}
        # 运行中：云端无取消能力
        TOKENS.setdefault(task_id, CancelToken()).request()
        _set(task_id, cancel_requested=1, stage="cancel_requested_cloud_running")
        return {"result": "cancel_requested", "phase": "cloud_running", "cloud_canceled": False,
                "message": "已停止本地等待并停止下载；云端任务可能仍在执行并计费"}

    if st in ("loading", "running", "postprocess"):
        TOKENS.setdefault(task_id, CancelToken()).request()
        _set(task_id, cancel_requested=1, stage=f"cancel_requested({st})")
        return {"result": "cancel_requested", "phase": st}
    raise err("CANCEL_REJECTED_PHASE", f"当前状态不可取消：{st}", detail={"status": st}, task_id=task_id)


def reconcile() -> dict:
    """重启恢复：active 状态标 interrupted；queued 继续执行。"""
    cloud_rows = db.query(
        "SELECT id, status, remote_task_id FROM tasks WHERE status IN "
        "('loading','running','postprocess','remote_queued','remote_running') AND remote_task_id IS NOT NULL"
    )
    for r in cloud_rows:
        _set(r["id"], status="queued", stage="resume_poll", attempts=0,
             error_message="服务重启：保留云端任务 ID，继续查询（不重新创建）")
    rows = db.query(
        "SELECT id, status, stage FROM tasks WHERE status IN ('loading','running','postprocess')"
    )
    for r in rows:
        _set(r["id"], status="interrupted", error_code="TASK_INTERRUPTED",
             error_message="进程中断（重启后不自动重跑，请手动重试）", stage="interrupted",
             finished_at=db.now())
        db.insert("task_attempts", {
            "id": db.new_id("att"), "task_id": r["id"], "attempt_no": 0,
            "started_at": db.now(), "finished_at": db.now(), "result": "interrupted",
            "error_code": "TASK_INTERRUPTED",
        })
    queued = db.query("SELECT COUNT(*) c FROM tasks WHERE status='queued'")[0]["c"]
    MANAGER.unload()
    return {"interrupted": len(rows), "queued": queued}
