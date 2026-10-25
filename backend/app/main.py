"""FastAPI 入口：素材 / 任务 / 批次 / 草稿 / 模型 / 系统状态。"""
from __future__ import annotations

import contextlib
import time
from pathlib import Path

from fastapi import FastAPI, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, db, templates, worker
from .engines import GUARD, MANAGER
from .errors import AppError, app_error_handler, err, unhandled_handler
from .storage import asset_path, load_asset, save_bytes, validate_image_asset

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
FRONTEND_DIST = ROOT.parent / "frontend" / "dist"
STARTED_AT = time.time()


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    try:
        app.state.template_seed = templates.seed()
    except Exception as e:  # noqa: BLE001  模板播种失败不阻塞服务启动
        app.state.template_seed = {"error": f"{type(e).__name__}: {e}"}
    rec = worker.reconcile()
    worker.start()
    app.state.recovery = rec
    yield
    worker.stop()


app = FastAPI(title="电商 AIGC 本地工作台", version="0.1.0", lifespan=lifespan)

# 前端（Vite）与后端不同端口，属于跨源；仅允许本机开发源
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:8100"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_exception_handler(AppError, app_error_handler)
app.add_exception_handler(Exception, unhandled_handler)

# 可选创作助手（阶段一）：不影响既有路由与手动生成路径
from .assistant.router import router as assistant_router  # noqa: E402

app.include_router(assistant_router)
if STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
if (FRONTEND_DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="frontend-assets")


# ---------- 健康与系统 ----------
@app.get("/api/health")
def health(request: Request):
    q = db.query("SELECT status, COUNT(*) c FROM tasks GROUP BY status")
    return {
        "status": "ok",
        "version": app.version,
        "uptime_s": int(time.time() - STARTED_AT),
        "recovery": getattr(request.app.state, "recovery", {"interrupted": 0, "queued": 0}),
        "tasks": {r["status"]: r["c"] for r in q},
    }


@app.get("/api/system/status")
def system_status():
    return {
        "memory": GUARD.snapshot(),
        "loaded_model": MANAGER.loaded_id,
        "queue": {
            "queued": db.query("SELECT COUNT(*) c FROM tasks WHERE status='queued'")[0]["c"],
            "active": db.query(
                "SELECT COUNT(*) c FROM tasks WHERE status IN ('loading','running','postprocess')"
            )[0]["c"],
        },
        "concurrency": config.CONCURRENCY,
    }


@app.get("/api/models")
def models():
    # 列表与提交校验共用同一判定入口，避免“列表说未到期、提交却拒绝”
    now = _now()
    out = []
    for mid, meta in config.MODEL_REGISTRY.items():
        is_cloud = meta.get("provider") == "ark"
        caps = config.MODEL_CAPABILITIES.get(mid, {})
        out.append({
            "id": mid,
            "name": meta["name"],
            "kind": meta["kind"],
            "provider": meta.get("provider", "local"),
            "license": meta["license"],
            "weights_mb": 0 if is_cloud else MANAGER.weights_mb(mid),
            "downloaded": True if is_cloud else MANAGER.weights_ready(mid),
            "loaded": (MANAGER.loaded_id == mid) if not is_cloud else False,
            "default_size": meta.get("default_size"),
            "max_size": meta.get("max_size"),
            "default_steps": meta.get("default_steps"),
            "default_frames": meta.get("default_frames"),
            "note": meta.get("note"),
            "activation": config.MODEL_ACTIVATION.get(mid, "unknown"),
            # v0.7：能力发现 + 生命周期（EOS）
            "capabilities": {
                "input_modes": caps.get("input_modes", []),
                "ratios": caps.get("ratios", []),
                "resolutions": caps.get("resolutions", []),
                "sizes": caps.get("sizes", []),
                "duration_s": caps.get("duration_s", []),
                "duration_range_s": caps.get("duration_range_s"),
                "duration_step_s": caps.get("duration_step_s"),
                # B10：前端只按 duration_policy 渲染（range 优先，其次 enum，否则不可用）
                "duration_policy": (config.duration_policy(mid)
                                    if meta["kind"] == "video" else None),
                "max_reference_images": caps.get("max_reference_images"),
                "supports": caps.get("supports", []),
                "fps": caps.get("fps"),
                "limits": caps.get("limits"),
            },
            "pricing": caps.get("pricing"),
            # 生命周期由 config.model_lifecycle_state() 计算（服务端时间比较，到期即 expired）
            "lifecycle": config.model_lifecycle_state(mid, now),
            "submittability": config.model_submittability(mid, now),
        })
    return {"items": out}


# ---------- 模板（v0.7 单次创作模板） ----------
def _template_summary(row: dict) -> dict:
    snap = row.get("snapshot") or {}
    return {
        "template_id": row["template_id"],
        "kind": row["kind"],
        "category": row["category"],
        "name": row["name"],
        "status": row["status"],
        "latest_version": row["latest_version"],
        "description": snap.get("description"),
        "effect_sample": snap.get("effect_sample"),
        "required_assets": snap.get("required_assets", []),
        "fields": snap.get("fields", []),
        "default_params": snap.get("default_params", {}),
        "compatible_models": snap.get("compatible_models", {}),
        "compatible_model_ids": _compatible_model_ids(snap),
    }


def _compatible_model_ids(snap: dict) -> list[str]:
    """按能力元数据反推可用模型 ID（只列已开通/已登记的云端模型）。"""
    out = []
    for mid, meta in config.MODEL_REGISTRY.items():
        caps = {**config.MODEL_CAPABILITIES.get(mid, {}), "kind": meta.get("kind")}
        if not config.MODEL_CAPABILITIES.get(mid):
            continue
        if templates.model_compatible(snap, mid, caps):
            out.append(mid)
    return out


@app.get("/api/templates")
def list_templates(kind: str | None = None, category: str | None = None):
    rows = templates.list_templates(kind=kind, category=category)
    return {"items": [_template_summary(r) for r in rows]}


@app.get("/api/templates/{template_id}")
def get_template(template_id: str, version: int | None = None):
    d = templates.detail(template_id, version)
    return {**_template_summary({**d, "snapshot": d["snapshot"]}),
            "requested_version": d["requested_version"],
            "versions": d["versions"],
            "template_snapshot": d["snapshot"]}


# ---------- 素材 ----------
@app.post("/api/assets")
async def upload_asset(file: UploadFile):
    data = await file.read()
    if len(data) > config.MAX_UPLOAD_MB * 1024 * 1024:
        raise err("UPLOAD_REJECTED", f"文件超过 {config.MAX_UPLOAD_MB}MB 上限")
    if not data:
        raise err("UPLOAD_REJECTED", "空文件")
    a = save_bytes(data, "image", "upload", meta={"filename": file.filename})
    return {"asset_id": a["id"], "sha256": a["sha256"], "width": a["width"], "height": a["height"]}


@app.get("/api/assets")
def list_assets(limit: int = 50, kind: str | None = None):
    sql = "SELECT * FROM assets"
    args: list = []
    if kind:
        sql += " WHERE kind=?"
        args.append(kind)
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(min(limit, 200))
    return {"items": db.query(sql, args)}


@app.get("/api/assets/{asset_id}")
def get_asset(asset_id: str):
    return load_asset(asset_id)


@app.get("/api/assets/{asset_id}/file")
def get_asset_file(asset_id: str):
    a = load_asset(asset_id)
    p = asset_path(a)
    if not p.exists():
        raise err("ASSET_NOT_FOUND", "素材文件缺失")
    return FileResponse(p, media_type=a["mime"], filename=p.name)


# ---------- 任务 ----------
class TaskIn(BaseModel):
    kind: str = Field(pattern="^(image|video)$")
    model_id: str
    params: dict = {}
    input_asset_id: str | None = None
    batch_id: str | None = None
    client_request_id: str | None = None
    # v0.7 增量（跨项目预留）
    template_id: str | None = None
    template_version: int | None = None
    template_fields: dict | None = None
    external_request_id: str | None = None
    caller_id: str | None = None
    # v0.9 助手：用户确认后的批准（仅助手路径使用；手动路径不传）
    approval_id: str | None = None


def _normalize_caller(caller_id: str | None) -> str:
    """手动路径的调用方标识。

    无鉴权阶段：禁止通过请求体把任务伪装成助手来源——助手来源只能由
    「报价→用户确认→批准」流程产生（见 assistant/approval.py）。
    未来接入鉴权后，才可信任外部调用方自报的 caller_id。
    """
    cid = (caller_id or "local").strip() or "local"
    return "local" if cid == "assistant" else cid


# 可注入时钟（测试用；生产为 time.time）
def _now() -> float:
    return time.time()


# 前端展示用的“最终生效参数”字段
def _effective(params: dict, kind: str) -> dict:
    if kind == "image":
        return {"generation_mode": params.get("generation_mode"), "size": params.get("size")}
    return {"duration": params.get("duration"), "ratio": params.get("ratio"),
            "resolution": params.get("resolution"), "generate_audio": params.get("generate_audio")}


def resolve_task(body: TaskIn, now_ts: float | None = None, *,
                 preview: bool = False, legacy: bool = False) -> dict:
    """**纯函数：不建任务、不入队、不调模型、不扣预算**。

    提交（create_task）与费用预估（estimate_task）共用本函数，保证两者参数完全一致。
    """
    now = _now() if now_ts is None else float(now_ts)

    if body.model_id not in config.MODEL_REGISTRY:
        raise err("VALIDATION_FAILED", f"未知模型：{body.model_id}",
                  detail={"models": list(config.MODEL_REGISTRY)})
    meta = config.MODEL_REGISTRY[body.model_id]
    if meta["kind"] != body.kind:
        raise err("VALIDATION_FAILED", f"模型 {body.model_id} 不是 {body.kind} 模型")

    # ---- 模型可提交性：账号未开通 / 已 EOS 一律拒绝新提交 ----
    # preview=True（仅费用预估）时允许返回报价，但必须标记 submittable=false。
    submittability = config.model_submittability(body.model_id, now)
    not_opened = "MODEL_NOT_OPENED" in submittability["reasons"]
    if not_opened and not preview:
        raise err("MODEL_NOT_OPENED",
                  f"模型 {body.model_id} 尚未在账号中开通，不可提交",
                  detail={"activation": submittability["activation"],
                          "reasons": submittability["reasons"]})

    # ---- 模型生命周期：到期后禁止新增任务（不自动替换模型）----
    lifecycle = config.model_lifecycle_state(body.model_id, now)
    if lifecycle.get("status") == "eos_expired":
        raise err("MODEL_EOS",
                  f"模型 {body.model_id} 已于 {lifecycle.get('eos_at')} 下线，不可新增生成任务；"
                  f"请改用 {lifecycle.get('migrate_to') or '已验收的替代模型'}",
                  detail=lifecycle)

    params = dict(body.params or {})
    tpl_info: dict | None = None
    warnings: list[dict] = []

    if body.template_id:
        fields = dict(body.template_fields or {})
        if not fields and isinstance(params.get("fields"), dict):
            fields = dict(params["fields"])
        has_ref = bool(body.input_asset_id)
        tpl_info = templates.resolve_for_task(body.template_id, body.template_version, fields,
                                              has_ref, explicit_params=params)
        params = dict(tpl_info["params"])
        params["prompt"] = (tpl_info.get("explicit_prompt") or tpl_info["prompt"])
        params["fields"] = tpl_info["fields"]
        params["template_id"] = tpl_info["template_id"]
        params["template_version"] = tpl_info["template_version"]
        warnings.extend(tpl_info.get("warnings") or [])
        if tpl_info["concept_mode"]:
            params["concept_mode"] = True
        # 兼容性校验（用模板声明的兼容约束）
        caps = {**config.MODEL_CAPABILITIES.get(body.model_id, {}), "kind": meta["kind"]}
        if config.MODEL_CAPABILITIES.get(body.model_id) and not templates.model_compatible(
                tpl_info, body.model_id, caps):
            raise err("MODEL_TEMPLATE_INCOMPATIBLE",
                      f"模型 {body.model_id} 与模板「{tpl_info['template_name']}」不兼容",
                      detail={"template_id": tpl_info["template_id"],
                              "compatible_models": _compatible_model_ids(
                                  {"compatible_models": tpl_info["compatible_models"]})})

    if body.kind == "video":
        if not body.input_asset_id:
            raise err("VALIDATION_FAILED", "视频任务需要 input_asset_id（首帧图）")
        load_asset(body.input_asset_id)
    if body.kind == "image":
        if not (params.get("prompt") or "").strip():
            raise err("VALIDATION_FAILED", "prompt 不能为空")
        mode = params.get("generation_mode", "text_to_image")
        if mode not in ("text_to_image", "image_to_image"):
            raise err("VALIDATION_FAILED",
                      "generation_mode 只能为 text_to_image 或 image_to_image",
                      detail={"allowed": ["text_to_image", "image_to_image"], "got": mode})
        if mode == "image_to_image":
            if not body.input_asset_id:
                raise err("REFERENCE_IMAGE_REQUIRED",
                          "图生图必须提供 input_asset_id（参考图素材 ID）",
                          detail={"generation_mode": mode})
            ref = load_asset(body.input_asset_id)            # 不存在 → 404 ASSET_NOT_FOUND
            validate_image_asset(ref)                        # 非图片/损坏 → 明确错误
            params["input_asset_id"] = body.input_asset_id
        elif body.input_asset_id:
            # 兼容旧行为：文生图即使带了参考图也不传给模型，仅回显提示
            params["ignored_reference"] = True
            params["input_asset_id"] = body.input_asset_id

    # ---- B10：时长先解析为唯一有效整数，并**写回** params ----
    # 之后落库、估价、供应商请求都读同一个值，避免"校验用 A、落库用 B"。
    if body.kind == "video":
        params["duration"] = config.resolve_duration_s(
            body.model_id, params.get("duration"), strict=True, legacy=legacy)

    # ---- 最终生效参数的模型能力校验 ----
    problems = config.validate_params_for_model(body.model_id, body.kind, params)
    if problems:
        raise err("MODEL_CAPABILITY_MISMATCH",
                  f"模型 {body.model_id} 不支持所选参数：" +
                  "，".join(f"{p['field']}={p['got']}" for p in problems),
                  detail={"template_id": (tpl_info or {}).get("template_id"), "problems": problems})

    # ---- 费用估算（与提交共用同一函数）----
    from . import cloud_tasks
    estimated = cloud_tasks.estimate_cost(body.kind, body.model_id, params)

    return {
        "kind": body.kind,
        "model_id": body.model_id,
        "input_asset_id": body.input_asset_id,
        "params": params,
        "template_id": (tpl_info or {}).get("template_id"),
        "template_version": (tpl_info or {}).get("template_version"),
        "template_name": (tpl_info or {}).get("template_name"),
        "concept_mode": bool(params.get("concept_mode")),
        "estimated_cost_cny": estimated,
        "effective": _effective(params, body.kind),
        "lifecycle": lifecycle,
        "submittable": submittability["submittable"],
        "blocked_reasons": submittability["reasons"],
        "warnings": warnings,
        "external_request_id": body.external_request_id,
        "caller_id": _normalize_caller(body.caller_id),
    }


@app.post("/api/tasks/estimate")
def estimate_task(body: TaskIn):
    """**无副作用**费用预估：不建任务、不入队、不调用模型、不扣预算。

    注意：本路由必须在任何 `/api/tasks/{task_id}` 形态的 POST 路由之前注册；
    当前 `/api/tasks/{task_id}` 仅支持 GET，因此无冲突（已由测试断言路由顺序）。
    """
    resolved = resolve_task(body, preview=True)
    return {
        "kind": resolved["kind"],
        "submittable": resolved["submittable"],
        "blocked_reasons": resolved["blocked_reasons"],
        "model_id": resolved["model_id"],
        "template_id": resolved["template_id"],
        "template_version": resolved["template_version"],
        "template_name": resolved["template_name"],
        "concept_mode": resolved["concept_mode"],
        "estimated_cost_cny": resolved["estimated_cost_cny"],
        "effective": resolved["effective"],
        "params_preview": {
            "prompt": resolved["params"].get("prompt"),
            "fields": resolved["params"].get("fields"),
        },
        "model_lifecycle": resolved["lifecycle"],
        "warnings": resolved["warnings"],
        "side_effects": {"task_created": False, "queued": False,
                          "model_called": False, "budget_deducted": False},
    }


def _insert_resolved(resolved: dict, body: TaskIn) -> dict:
    params = resolved["params"]
    task = {
        "id": db.new_id("task"), "kind": resolved["kind"], "status": "queued",
        "params_json": db.dumps(params), "model_id": resolved["model_id"],
        "model_revision": "local-snapshot", "input_asset_id": resolved["input_asset_id"],
        "batch_id": body.batch_id, "client_request_id": body.client_request_id,
        "progress": 0.0, "stage": "queued", "attempts": 0, "cancel_requested": 0,
        "created_at": db.now(), "updated_at": db.now(),
        "template_id": resolved["template_id"],
        "template_version": resolved["template_version"],
        "external_request_id": resolved["external_request_id"],
        "caller_id": resolved["caller_id"],
        "concept_mode": 1 if resolved["concept_mode"] else 0,
    }
    db.insert("tasks", task)
    q = db.query("SELECT COUNT(*) c FROM tasks WHERE status='queued'")[0]["c"]
    return {**task, "queue_position": q, "estimated_cost_cny": resolved["estimated_cost_cny"]}


def create_task(body: TaskIn, *, legacy: bool = False) -> dict:
    # ---- 助手路径：必须带批准，且来源/幂等键均由后端确定 ----
    if body.approval_id:
        # 助手路径：以报价时冻结的方案为准，不重新解析模板（防模板升版漂移）。
        # 方案校验在幂等回放之前完成，改方案后同一批准会被拒绝。
        from .assistant import approval as approval_gate
        return approval_gate.submit_with_approval(body)

    if body.client_request_id:
        hit = db.query("SELECT * FROM tasks WHERE client_request_id=?", (body.client_request_id,))
        if hit:
            # 幂等回放：补上任务级费用估算，避免响应字段为空
            row = hit[0]
            try:
                from . import cloud_tasks
                # 历史记录回放：允许旧类型（10.0 / "10"），费用按当时参数计算
                row["estimated_cost_cny"] = cloud_tasks.estimate_cost(
                    row["kind"], row["model_id"], db.loads(row.get("params_json")) or {},
                    legacy=True)
            except Exception:  # noqa: BLE001
                row["estimated_cost_cny"] = None
            row["idempotent_replay"] = True
            return row
    resolved = resolve_task(body, legacy=legacy)
    return _insert_resolved(resolved, body)


@app.post("/api/tasks")
def post_task(body: TaskIn):
    t = create_task(body)
    return {"task_id": t["id"], "status": t["status"], "queue_position": t.get("queue_position"),
            "estimated_memory_mb": GUARD.snapshot().get("budget_mb"),
            # 注意：这是“提交后”的任务级费用估算，不是提交前确认；
            # 提交前确认必须使用 POST /api/tasks/estimate。
            "estimated_cost_cny": t.get("estimated_cost_cny"),
            "template_id": t.get("template_id"),
            "template_version": t.get("template_version"),
            "external_request_id": t.get("external_request_id"),
            "concept_mode": bool(t.get("concept_mode")),
            # 助手路径回显：来源由后端强制
            "origin": t.get("origin") or ("assistant" if body.approval_id else "manual"),
            "caller_id": t.get("caller_id"),
            "approval_id": body.approval_id,
            "idempotent_replay": bool(t.get("idempotent_replay"))}


@app.get("/api/tasks")
def list_tasks(status: str | None = None, kind: str | None = None, batch_id: str | None = None, limit: int = 50):
    sql = "SELECT * FROM tasks WHERE 1=1"
    args: list = []
    for col, val in (("status", status), ("kind", kind), ("batch_id", batch_id)):
        if val:
            sql += f" AND {col}=?"
            args.append(val)
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(min(limit, 200))
    return {"items": db.query(sql, args)}


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str):
    t = db.get("tasks", task_id)
    if not t:
        raise err("TASK_NOT_FOUND", f"任务不存在：{task_id}")
    t["params"] = db.loads(t.get("params_json"))
    t["attempts_log"] = db.query("SELECT * FROM task_attempts WHERE task_id=? ORDER BY started_at", (task_id,))
    return t


@app.post("/api/tasks/{task_id}/cancel")
def cancel_task(task_id: str):
    return worker.cancel(task_id)


class RetryIn(BaseModel):
    params: dict | None = None   # 可选：覆盖参数（如 OOM 后降分辨率）


@app.post("/api/tasks/{task_id}/retry")
def retry_task(task_id: str, body: RetryIn | None = None):
    t = db.get("tasks", task_id)
    if not t:
        raise err("TASK_NOT_FOUND", f"任务不存在：{task_id}")
    if t["status"] in worker.ACTIVE:
        raise err("CANCEL_REJECTED_PHASE", "任务仍在执行，请先取消")
    params = (db.loads(t["params_json"]) or {})
    if body and body.params:
        params = {**params, **body.params}
    tfields = params.get("fields") if isinstance(params.get("fields"), dict) else None
    # 重试同样受模型生命周期保护：到期后 create_task → resolve_task 抛 MODEL_EOS
    new = create_task(TaskIn(kind=t["kind"], model_id=t["model_id"],
                             params=params,
                             input_asset_id=t["input_asset_id"], batch_id=t["batch_id"],
                             template_id=t.get("template_id"),
                             template_version=t.get("template_version"),
                             template_fields=tfields,
                             external_request_id=t.get("external_request_id"),
                             caller_id=t.get("caller_id")), legacy=True)
    db.update("tasks", new["id"], {"retry_of": task_id})
    return {"task_id": new["id"], "retry_of": task_id, "status": "queued",
            "template_id": new.get("template_id"),
            "template_version": new.get("template_version"),
            "estimated_cost_cny": new.get("estimated_cost_cny")}


# ---------- 批量 ----------
class BatchIn(BaseModel):
    name: str = "batch"
    items: list[TaskIn] = []
    caller_id: str | None = None


@app.post("/api/batches")
def post_batch(body: BatchIn):
    if not body.items:
        raise err("VALIDATION_FAILED", "items 不能为空")
    batch_id = db.new_id("batch")

    # 先全批解析（纯函数，不写库）：任一项非法（含模型 EOS）→ 整批拒绝，不产生半成品任务
    resolved_items: list[tuple[dict, TaskIn]] = []
    for it in body.items:
        it.batch_id = batch_id
        if body.caller_id and not it.caller_id:
            it.caller_id = body.caller_id
        resolved_items.append((resolve_task(it), it))

    db.insert("batches", {"id": batch_id, "name": body.name, "created_at": db.now(),
                          "params_json": db.dumps({"count": len(resolved_items)})})
    ids = [_insert_resolved(res, it)["id"] for res, it in resolved_items]
    return {"batch_id": batch_id, "task_ids": ids}


@app.get("/api/batches/{batch_id}")
def get_batch(batch_id: str):
    b = db.get("batches", batch_id)
    if not b:
        raise err("VALIDATION_FAILED", "批次不存在")
    b["tasks"] = db.query("SELECT id, status, kind, progress, stage, error_code FROM tasks WHERE batch_id=? ORDER BY created_at", (batch_id,))
    return b


# ---------- 草稿 ----------
class DraftIn(BaseModel):
    name: str = "draft"
    content: dict = {}


class DraftPatch(BaseModel):
    content: dict
    base_version: int | None = None


@app.post("/api/drafts")
def create_draft(body: DraftIn):
    did = db.new_id("draft")
    db.insert("drafts", {"id": did, "name": body.name, "content_json": db.dumps(body.content),
                         "version": 1, "created_at": db.now(), "updated_at": db.now()})
    db.insert("draft_versions", {"id": db.new_id("dv"), "draft_id": did, "version": 1,
                                 "content_json": db.dumps(body.content), "created_at": db.now()})
    return {"draft_id": did, "version": 1}


@app.get("/api/drafts")
def list_drafts(limit: int = 20):
    rows = db.query("SELECT * FROM drafts ORDER BY updated_at DESC LIMIT ?", (max(1, min(limit, 50)),))
    for draft in rows:
        draft["content"] = db.loads(draft.pop("content_json"))
    return {"items": rows}


@app.get("/api/drafts/{draft_id}")
def get_draft(draft_id: str):
    d = db.get("drafts", draft_id)
    if not d:
        raise err("DRAFT_NOT_FOUND", f"草稿不存在：{draft_id}")
    d["content"] = db.loads(d.pop("content_json"))
    return d


@app.patch("/api/drafts/{draft_id}")
def patch_draft(draft_id: str, body: DraftPatch):
    d = db.get("drafts", draft_id)
    if not d:
        raise err("DRAFT_NOT_FOUND", f"草稿不存在：{draft_id}")
    if body.base_version is not None and body.base_version != d["version"]:
        raise err("DRAFT_VERSION_CONFLICT", f"版本冲突：当前 {d['version']}，提交 {body.base_version}")
    v = d["version"] + 1
    db.update("drafts", draft_id, {"content_json": db.dumps(body.content), "version": v,
                                   "updated_at": db.now()})
    db.insert("draft_versions", {"id": db.new_id("dv"), "draft_id": draft_id, "version": v,
                                 "content_json": db.dumps(body.content), "created_at": db.now()})
    return {"draft_id": draft_id, "version": v}


@app.get("/api/usage")
def usage():
    from . import cloud_tasks

    rows = db.query(
        "SELECT id, kind, model_id, status, cost_cny, usage_json, latency_ms, finished_at "
        "FROM tasks WHERE cost_cny IS NOT NULL ORDER BY finished_at DESC LIMIT 100"
    )
    return {"budget_cny": config.BUDGET_CNY, "spent_today_cny": round(cloud_tasks.spent_today(), 4),
            "items": rows}


# ---------- 正式前端与最小验收页 ----------
@app.get("/")
def index():
    frontend = FRONTEND_DIST / "index.html"
    if frontend.exists():
        return FileResponse(frontend, media_type="text/html", headers={"Cache-Control": "no-store"})
    return legacy_index()


@app.get("/legacy")
def legacy_index():
    p = STATIC / "index.html"
    if not p.exists():
        return JSONResponse({"error": {"code": "INTERNAL_ERROR", "message": "验收页缺失"}}, status_code=500)
    return HTMLResponse(p.read_text(encoding="utf-8"))
