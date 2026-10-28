"""S2：项目 / 需求信息（brief）/ 脚本版本。

设计约束（PRD v0.10 §2.1–§2.3、技术方案 v6 §1.3、接口契约 v0.10 §3）：
- **脚本只追加不覆盖**：任何编辑都产生新 `version`，旧版本可回滚查看。
- **AI 脚本生成由用户主动触发**；助手不可用时返回 `assistant_unavailable=true` 并给提示，
  **不报 5xx、不阻断手工编辑**。
- **确认绑定 `script_id + version`**；改 brief / 改脚本后确认失效（返回 stale 标记，不自动改状态）。
- 不在此层调用图片/视频模型；不新建任务。
"""
from __future__ import annotations

import json
import inspect
import os
import threading
from functools import wraps
from typing import Any

from . import db
from .errors import err

BRIEF_FIELDS = ("subject", "quantity", "selling_points", "forbidden_elements",
                "target_platform", "target_duration_s", "style", "notes")
PROJECT_STATUS = ("draft", "scripting", "script_confirmed", "archived")

_project_locks: dict[str, threading.RLock] = {}
_project_locks_guard = threading.Lock()


def _project_lock(project_id: str):
    with _project_locks_guard:
        return _project_locks.setdefault(project_id, threading.RLock())


def related_project_id(table: str, object_id: str) -> str | None:
    """Resolve stored ownership; never infer a project from user supplied labels."""
    row = db.get(table, object_id)
    if not row:
        return None
    if table == "projects":
        return row["id"]
    if table in ("storyboards", "shot_takes"):
        shot = db.get("shots", row["shot_id"])
        return shot["project_id"] if shot else None
    if table == "tasks":
        if row.get("external_request_id") and db.get("projects", row["external_request_id"]):
            return row["external_request_id"]
        linked = db.query("SELECT s.project_id FROM shots s JOIN storyboards b ON b.shot_id=s.id "
                          "WHERE b.task_id=? UNION SELECT s.project_id FROM shots s "
                          "JOIN shot_takes t ON t.shot_id=s.id WHERE t.task_id=?", (object_id, object_id))
        return linked[0]["project_id"] if linked else None
    return row.get("project_id")


def project_guard(table: str = "projects"):
    """Serialize a project write with deletion, including work outside SQLite."""
    def decorate(fn):
        signature = inspect.signature(fn)
        first = next(iter(signature.parameters))
        @wraps(fn)
        def guarded(*args, **kwargs):
            object_id = signature.bind(*args, **kwargs).arguments[first]
            project_id = object_id if table == "projects" else related_project_id(table, object_id)
            if not project_id:
                return fn(*args, **kwargs)  # preserve the endpoint's not-found error
            with _project_lock(project_id):
                get_project(project_id)
                return fn(*args, **kwargs)
        return guarded
    return decorate


def task_creation_guard(fn):
    @wraps(fn)
    def guarded(body, *args, **kwargs):
        project_id = getattr(body, "external_request_id", None)
        if project_id and db.get("projects", project_id):
            with _project_lock(project_id):
                get_project(project_id)
                return fn(body, *args, **kwargs)
        return fn(body, *args, **kwargs)
    return guarded


def assistant_guard(fn):
    """Keep legacy keyword-only chat calls safe without changing their API."""
    signature = inspect.signature(fn)
    @wraps(fn)
    def guarded(*args, **kwargs):
        supplied = signature.bind(*args, **kwargs).arguments
        session = db.get("assistant_sessions", supplied.get("session_id")) if supplied.get("session_id") else None
        project_id = supplied.get("project_id") or (session or {}).get("project_id")
        if project_id and db.get("projects", project_id):
            with _project_lock(project_id):
                get_project(project_id)
                return fn(*args, **kwargs)
        return fn(*args, **kwargs)
    return guarded


def ensure_not_deleted(project_id: str | None) -> None:
    """Legacy workbench session IDs need not be projects, but tombstones stay closed."""
    row = db.get("projects", project_id) if project_id else None
    if row and row.get("deleted_at") is not None:
        raise err("PROJECT_DELETED", "该项目已删除，素材和历史任务仍保留。",
                  detail={"project_id": project_id, "deleted_at": row["deleted_at"]})


def assert_task_project_active(task_id: str) -> None:
    task = db.get("tasks", task_id)
    if task:
        ensure_not_deleted(task.get("external_request_id"))
    linked = db.query("SELECT s.project_id FROM shots s JOIN storyboards b ON b.shot_id=s.id "
                      "WHERE b.task_id=? UNION SELECT s.project_id FROM shots s "
                      "JOIN shot_takes t ON t.shot_id=s.id WHERE t.task_id=?", (task_id, task_id))
    for row in linked:
        get_project(row["project_id"])


# ---------------- 项目 ----------------
def create_project(name: str, brief: dict | None = None,
                   structure_template_id: str | None = None,
                   structure_template_version: int | None = None) -> dict:
    name = (name or "").strip()
    if not name:
        raise err("VALIDATION_FAILED", "项目名称不能为空", detail={"field": "name"})
    clean = _clean_brief(brief or {})
    pid = db.new_id("prj")
    row = {"id": pid, "name": name, "brief_json": db.dumps(clean), "status": "draft",
           "structure_template_id": structure_template_id,
           "structure_template_version": structure_template_version,
           "created_at": db.now(), "updated_at": db.now()}
    db.insert("projects", row)
    return _project_view(row)


def get_project(project_id: str) -> dict:
    row = db.get("projects", project_id)
    if not row:
        raise err("PROJECT_NOT_FOUND", f"项目不存在：{project_id}",
                  detail={"project_id": project_id})
    if row.get("deleted_at") is not None:
        raise err("PROJECT_DELETED", "该项目已删除，素材和历史任务仍保留。",
                  detail={"project_id": project_id, "deleted_at": row["deleted_at"]})
    return row


def project_detail(project_id: str) -> dict:
    row = get_project(project_id)
    scripts = list_scripts(project_id)
    latest = scripts[0] if scripts else None
    return {**_project_view(row), "script_count": len(scripts),
            "latest_script": _script_summary(latest) if latest else None}


def list_projects(limit: int = 20) -> list[dict]:
    rows = db.query("SELECT * FROM projects WHERE deleted_at IS NULL ORDER BY updated_at DESC LIMIT ?",
                    (max(1, min(limit, 100)),))
    out = []
    for r in rows:
        scripts = db.query("SELECT COUNT(*) c FROM script_versions WHERE project_id=?",
                           (r["id"],))[0]["c"]
        out.append({**_project_view(r), "script_count": scripts})
    return out


def delete_project(project_id: str) -> dict:
    """Hide a project atomically; retain every linked row and media file."""
    lock = _project_lock(project_id)
    if not lock.acquire(blocking=False):
        raise err("PROJECT_BUSY", "项目正在处理操作，请完成后再删除。",
                  detail={"project_id": project_id, "operation_in_progress": True})
    try:
        with db.transaction() as c:
            row = c.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
            if not row:
                raise err("PROJECT_NOT_FOUND", "项目不存在。", detail={"project_id": project_id})
            if row["deleted_at"] is not None:
                return {"project_id": project_id, "deleted": True,
                        "deleted_at": row["deleted_at"], "already_deleted": True}
            # Unknown states fail closed: deleting must not hide in-flight paid work.
            tasks = c.execute(
                "SELECT DISTINCT t.id, t.status FROM tasks t WHERE "
                "(t.external_request_id=? OR t.id IN (SELECT b.task_id FROM storyboards b "
                "JOIN shots s ON s.id=b.shot_id WHERE s.project_id=?) OR t.id IN "
                "(SELECT k.task_id FROM shot_takes k JOIN shots s ON s.id=k.shot_id "
                "WHERE s.project_id=?)) AND (t.status IS NULL OR t.status NOT IN "
                "('succeeded','failed','canceled','interrupted') OR t.error_code='CREATE_RESULT_UNKNOWN' OR "
                "(t.remote_task_id IS NOT NULL AND t.remote_task_id!='' AND t.status!='succeeded' "
                "AND (t.remote_status IS NULL OR t.remote_status NOT IN "
                "('succeeded','success','failed','cancelled','canceled'))))",
                (project_id,)*3).fetchall()
            films = c.execute("SELECT id, status FROM films WHERE project_id=? AND "
                              "(status IS NULL OR status NOT IN ('ready','partial','failed'))",
                              (project_id,)).fetchall()
            requests = c.execute("SELECT client_request_id, status FROM director_requests "
                                 "WHERE project_id=? AND (status IS NULL OR status NOT IN "
                                 "('completed','failed'))", (project_id,)).fetchall()
            # A worker marks a task terminal before it attaches the output and starts
            # automatic composition. Protect those gaps as part of the active work.
            pending_outputs = c.execute(
                "SELECT k.id, k.status FROM shot_takes k JOIN shots s ON s.id=k.shot_id "
                "WHERE s.project_id=? AND (k.status IS NULL OR k.status NOT IN "
                "('succeeded','failed','canceled','interrupted')) UNION "
                "SELECT b.id, 'attaching' FROM storyboards b JOIN shots s ON s.id=b.shot_id "
                "JOIN tasks t ON t.id=b.task_id WHERE s.project_id=? AND t.status='succeeded' "
                "AND b.asset_id IS NOT t.output_asset_id", (project_id, project_id)).fetchall()
            assembling_batches = c.execute(
                "SELECT r.batch_id FROM submit_receipts r WHERE r.project_id=? AND r.mode='full' "
                "AND EXISTS(SELECT 1 FROM tasks t WHERE t.batch_id=r.batch_id) "
                "AND NOT EXISTS(SELECT 1 FROM tasks t WHERE t.batch_id=r.batch_id "
                "AND (t.status IS NULL OR t.status!='succeeded')) "
                "AND NOT EXISTS(SELECT 1 FROM films f WHERE f.source_batch=r.batch_id) "
                "AND NOT EXISTS(SELECT 1 FROM assembly_errors e WHERE e.batch_id=r.batch_id)",
                (project_id,)).fetchall()
            compositions = []
            if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='director_compositions'").fetchone():
                compositions = c.execute(
                    "SELECT id, status FROM director_compositions WHERE project_id=? "
                    "AND status IN ('queued','running')", (project_id,)).fetchall()
            pending_director_films = []
            if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='director_actions'").fetchone():
                pending_director_films = c.execute(
                    "SELECT a.id FROM director_actions a WHERE a.project_id=? AND a.kind='generation' "
                    "AND a.status='submitted' AND json_extract(a.frozen_json,'$.output_mode')='film' "
                    "AND json_extract(a.result_json,'$.composition_action_id') IS NULL "
                    "AND json_extract(a.result_json,'$.composition_error') IS NULL "
                    "AND EXISTS(SELECT 1 FROM tasks t WHERE t.batch_id=json_extract(a.result_json,'$.batch_id')) "
                    "AND NOT EXISTS(SELECT 1 FROM tasks t WHERE t.batch_id=json_extract(a.result_json,'$.batch_id') "
                    "AND (t.status IS NULL OR t.status!='succeeded'))", (project_id,)).fetchall()
            if tasks or films or requests or pending_outputs or assembling_batches or compositions or pending_director_films:
                raise err("PROJECT_BUSY", "项目仍有生成、合成或待核对的对话请求，请处理完成后再删除。",
                          detail={"project_id": project_id,
                                  "tasks": [dict(t) for t in tasks], "films": [dict(f) for f in films],
                                  "requests": [dict(r) for r in requests],
                                  "pending_outputs": [dict(o) for o in pending_outputs],
                                  "assembling_batches": [dict(b) for b in assembling_batches],
                                  "compositions": [dict(item) for item in compositions],
                                  "pending_director_films": [dict(item) for item in pending_director_films]})
            deleted_at = db.now()
            c.execute("UPDATE projects SET deleted_at=?, updated_at=? WHERE id=?",
                      (deleted_at, deleted_at, project_id))
            return {"project_id": project_id, "deleted": True,
                    "deleted_at": deleted_at, "already_deleted": False}
    finally:
        lock.release()


@project_guard()
def update_project(project_id: str, name: str | None = None,
                   brief: dict | None = None) -> dict:
    row = get_project(project_id)
    patch: dict[str, Any] = {"updated_at": db.now()}
    if name is not None:
        name = name.strip()
        if not name:
            raise err("VALIDATION_FAILED", "项目名称不能为空", detail={"field": "name"})
        patch["name"] = name
    stale: list[str] = []
    if brief is not None:
        old = db.loads(row["brief_json"]) or {}
        new = {**old, **_clean_brief(brief)}
        if new != old:
            patch["brief_json"] = db.dumps(new)
            # brief 变化 → **持久化失效**下游确认（不删除历史确认记录）
            from . import director
            affected = director.invalidate_after_upstream_change(
                project_id=project_id, change="brief")
            stale = ["script_confirm"]
            if affected.get("shot_plan"):
                stale.append("shot_plan_confirm")
    db.update("projects", project_id, patch)
    return {**_project_view(get_project(project_id)), "stale": stale}


def _clean_brief(brief: dict) -> dict:
    if not isinstance(brief, dict):
        raise err("VALIDATION_FAILED", "brief 必须是对象")
    unknown = [k for k in brief if k not in BRIEF_FIELDS]
    if unknown:
        raise err("VALIDATION_FAILED", f"brief 含未知字段：{unknown}",
                  detail={"allowed": list(BRIEF_FIELDS), "unknown": unknown})
    q = brief.get("quantity")
    if q is not None and (isinstance(q, bool) or not isinstance(q, int) or q <= 0):
        raise err("VALIDATION_FAILED", "quantity 必须为正整数",
                  detail={"field": "quantity", "got": q})
    return {k: v for k, v in brief.items() if v is not None}


def _project_view(row: dict) -> dict:
    return {"project_id": row["id"], "name": row["name"],
            "brief": db.loads(row.get("brief_json")) or {},
            "status": row.get("status"),
            "structure_template_id": row.get("structure_template_id"),
            "structure_template_version": row.get("structure_template_version"),
            "created_at": row.get("created_at"), "updated_at": row.get("updated_at")}


# ---------------- 脚本 ----------------
def _next_version(project_id: str) -> int:
    rows = db.query("SELECT COALESCE(MAX(version),0) v FROM script_versions WHERE project_id=?",
                    (project_id,))
    return int(rows[0]["v"]) + 1


def _add_script_in_tx(connection, project_id: str, content: dict, source: str = "manual",
                      session_id: str | None = None, base_version: int | None = None) -> dict:
    """Create a script and its stable canvas binding on the caller's transaction."""
    from . import canvas
    c = connection
    project = c.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    if not project:
        raise err("PROJECT_NOT_FOUND", "项目不存在")
    if project["deleted_at"] is not None:
        raise err("PROJECT_DELETED", "该项目已删除，素材和历史任务仍保留。")
    if not isinstance(content, dict):
        raise err("VALIDATION_FAILED", "脚本 content 必须是对象")
    if base_version is not None and (isinstance(base_version, bool) or not isinstance(base_version, int) or base_version < 0):
        raise err("VALIDATION_FAILED", "base_version 必须为非负整数")
    row = c.execute("SELECT COALESCE(MAX(version),0) v FROM script_versions "
                    "WHERE project_id=?", (project_id,)).fetchone()
    latest = int(row["v"])
    if base_version is not None and base_version != latest:
        raise err("SCRIPT_VERSION_CONFLICT",
                  f"脚本已更新：项目当前最新 v{latest}，提交 v{base_version}",
                  detail={"current_version": latest, "base_version": base_version})
    sid = db.new_id("scr")
    ver = latest + 1
    c.execute("INSERT INTO script_versions(id, project_id, version, content_json, source, "
              "session_id, confirmed_at, created_at) VALUES(?,?,?,?,?,?,?,?)",
              (sid, project_id, ver, db.dumps(content), source, session_id, None, db.now()))
    c.execute("UPDATE projects SET status=?, updated_at=? WHERE id=?", ("scripting", db.now(), project_id))
    canvas.upsert_script_node_in_tx(c, project_id, sid, ver)
    return _script_view(dict(c.execute("SELECT * FROM script_versions WHERE id=?", (sid,)).fetchone()))


@project_guard()
def add_script(project_id: str, content: dict, source: str = "manual",
               session_id: str | None = None, base_version: int | None = None) -> dict:
    """Append a version and bind its node atomically; preserve prior versions and layout."""
    with db.transaction() as c:
        return _add_script_in_tx(c, project_id, content, source, session_id, base_version)


def list_scripts(project_id: str) -> list[dict]:
    get_project(project_id)
    rows = db.query("SELECT * FROM script_versions WHERE project_id=? ORDER BY version DESC",
                    (project_id,))
    return [_script_view(r) for r in rows]


def get_script(script_id: str) -> dict:
    row = db.get("script_versions", script_id)
    if not row:
        raise err("SCRIPT_NOT_FOUND", f"脚本不存在：{script_id}",
                  detail={"script_id": script_id})
    get_project(row["project_id"])
    return _script_view(row)


@project_guard("script_versions")
def edit_script(script_id: str, content: dict, base_version: int | None = None) -> dict:
    """手工编辑 → **新建版本**（不覆盖历史）。"""
    row = db.get("script_versions", script_id)
    if not row:
        raise err("SCRIPT_NOT_FOUND", f"脚本不存在：{script_id}")
    return add_script(row["project_id"], content, source="manual",
                      session_id=row.get("session_id"), base_version=base_version)


def confirm_script_in_tx(connection, script_id: str, confirm: bool = True) -> dict:
    """Confirm the exact script and update project state on the caller's transaction."""
    from . import director

    c = connection
    row = c.execute("SELECT * FROM script_versions WHERE id=?", (script_id,)).fetchone()
    if not row:
        raise err("SCRIPT_NOT_FOUND", f"脚本不存在：{script_id}")
    if not confirm:
        raise err("VALIDATION_FAILED", "确认动作需 confirm=true")
    now = db.now()
    cfm = director.confirm_object_in_tx(c, row["project_id"], "script", script_id,
                                       int(row["version"]))
    c.execute("UPDATE script_versions SET confirmed_at=? WHERE id=?", (now, script_id))
    c.execute("UPDATE projects SET status='script_confirmed' WHERE id=?", (row["project_id"],))
    return {"script_id": script_id, "version": row["version"],
            "confirmed_at": now, "confirmation": cfm,
            "unlocked": ["storyboard_plan"]}


@project_guard("script_versions")
def confirm_script(script_id: str, confirm: bool = True) -> dict:
    """确认记录、脚本确认时间和项目状态在同一事务中提交。"""
    with db.transaction() as c:
        return confirm_script_in_tx(c, script_id, confirm)


def _script_view(row: dict) -> dict:
    return {"script_id": row["id"], "project_id": row["project_id"],
            "version": row["version"], "content": db.loads(row.get("content_json")) or {},
            "source": row.get("source"), "confirmed_at": row.get("confirmed_at"),
            "created_at": row.get("created_at")}


def _script_summary(script: dict) -> dict:
    return {"script_id": script["script_id"], "version": script["version"],
            "source": script.get("source"), "confirmed_at": script.get("confirmed_at")}


# ---------------- AI 脚本生成（用户主动触发） ----------------
SCRIPT_SYSTEM_PROMPT = (
    "你是电商短视频脚本助手。根据商品资料写一个**简单视频脚本**，"
    "输出 JSON：{\"summary\": \"3-6 句整体叙事\", \"shots\": [{\"seq\": 1, \"hint\": \"一句话画面描述\"}]}。"
    "要求：只使用用户提供的商品事实，不虚构成分/功效/认证；不承诺效果；镜头数 2-6 个。"
)


@project_guard()
def generate_script(project_id: str, instruction: str | None = None,
                    target_duration_s: int | None = None,
                    provider=None) -> dict:  # noqa: ANN001
    """brief → 脚本。**调用方必须先由用户点击触发**；本函数不写任务、不调图片/视频模型。"""
    from .assistant import provider as prov
    from .assistant.service import record_usage

    row = get_project(project_id)
    brief = db.loads(row.get("brief_json")) or {}
    if not brief.get("subject"):
        raise err("VALIDATION_FAILED", "请先填写商品名称（brief.subject）",
                  detail={"field": "subject"})

    p = provider or prov.get_provider()
    if not p.available():
        # 助手不可用：不报 5xx，返回可手工继续的提示
        return {"assistant_unavailable": True,
                "hint": "助手暂不可用，可先手工编写脚本并继续后续步骤",
                "project_id": project_id}

    payload = {"brief": brief, "instruction": instruction,
               "target_duration_s": target_duration_s}
    messages = [
        {"role": "system", "content": SCRIPT_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]
    res = p.chat(messages, [])
    content = _parse_script(res.reply)
    script = add_script(project_id, content, source="assistant")
    try:
        record_usage(None, "llm", provider=p.name, model=p.model,
                     input_tokens=res.input_tokens, output_tokens=res.output_tokens,
                     tool_calls=0, note=f"script_generate:{project_id}")
    except Exception:  # noqa: BLE001  用量记录失败不影响结果
        pass
    return {"script_id": script["script_id"], "version": script["version"],
            "content": content, "source": "assistant",
            "llm_usage": {"input_tokens": res.input_tokens,
                          "output_tokens": res.output_tokens},
            "assistant_unavailable": False}


def _parse_script(text: str) -> dict:
    """解析模型输出；解析失败**明确降级**（原文保存，不编造镜头）。"""
    raw = (text or "").strip()
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
            if isinstance(data, dict) and isinstance(data.get("summary"), str):
                shots = data.get("shots")
                return {"summary": data["summary"],
                        "shots": shots if isinstance(shots, list) else [],
                        "parse_ok": True}
        except json.JSONDecodeError:
            pass
    return {"summary": raw, "shots": [], "parse_ok": False,
            "degraded_reason": "model_output_not_structured"}


# ---------------- 采用助手草案（**不调用模型**） ----------------
@project_guard()
def adopt_draft(project_id: str, kind: str, content: dict,
                base_version: int | None = None) -> dict:
    """把助手产出的草案**落库**。仅写库，不产生任何模型调用。

    - kind="brief"  → 合并进项目 brief（不覆盖未提及字段）
    - kind="script" → 新增脚本版本（可带 base_version 做并发校验）
    """
    get_project(project_id)
    if kind == "brief":
        out = update_project(project_id, brief=content)
        return {"kind": "brief", "project_id": project_id, "brief": out["brief"],
                "stale": out["stale"], "llm_called": False}
    if kind == "script":
        script = add_script(project_id, content, source="assistant",
                            base_version=base_version)
        return {"kind": "script", "script_id": script["script_id"],
                "version": script["version"], "content": script["content"],
                "llm_called": False}
    raise err("VALIDATION_FAILED", f"未知草案类型：{kind}",
              detail={"allowed": ["brief", "script"]})
