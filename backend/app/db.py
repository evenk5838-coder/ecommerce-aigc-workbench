"""SQLite 持久化（stdlib sqlite3，无额外依赖；与架构方案一致的数据模型，字段按本地推理调整）。"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterable

from . import config

_lock = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS assets(
  id TEXT PRIMARY KEY, sha256 TEXT UNIQUE, kind TEXT, mime TEXT, size INTEGER,
  width INTEGER, height INTEGER, duration_ms INTEGER, fps INTEGER, frames INTEGER,
  rel_path TEXT, source TEXT, meta_json TEXT, created_at REAL, owner TEXT DEFAULT 'local');

CREATE TABLE IF NOT EXISTS tasks(
  id TEXT PRIMARY KEY, kind TEXT, status TEXT, params_json TEXT,
  model_id TEXT, model_revision TEXT, input_asset_id TEXT, output_asset_id TEXT,
  progress REAL DEFAULT 0, stage TEXT,
  latency_ms INTEGER, load_ms INTEGER, peak_mem_mb INTEGER,
  frames INTEGER, fps INTEGER, width INTEGER, height INTEGER, duration_ms INTEGER,
  error_code TEXT, error_message TEXT, attempts INTEGER DEFAULT 0,
  cancel_requested INTEGER DEFAULT 0, batch_id TEXT, retry_of TEXT, client_request_id TEXT,
  created_at REAL, started_at REAL, finished_at REAL, updated_at REAL);

CREATE TABLE IF NOT EXISTS task_attempts(
  id TEXT PRIMARY KEY, task_id TEXT, attempt_no INTEGER, started_at REAL, finished_at REAL,
  result TEXT, error_code TEXT, error_message TEXT, peak_mem_mb INTEGER, latency_ms INTEGER,
  params_json TEXT);

CREATE TABLE IF NOT EXISTS mem_samples(
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, ts REAL,
  mps_alloc_mb INTEGER, mps_driver_mb INTEGER, sys_used_mb INTEGER);

CREATE TABLE IF NOT EXISTS batches(id TEXT PRIMARY KEY, name TEXT, created_at REAL, params_json TEXT);

CREATE TABLE IF NOT EXISTS templates(
  id TEXT PRIMARY KEY, template_id TEXT UNIQUE, category TEXT, kind TEXT,
  name TEXT, latest_version INTEGER DEFAULT 1, status TEXT DEFAULT 'active',
  created_at REAL, updated_at REAL);

CREATE TABLE IF NOT EXISTS template_versions(
  id TEXT PRIMARY KEY, template_id TEXT, version INTEGER, snapshot_json TEXT,
  status TEXT DEFAULT 'active', created_at REAL,
  UNIQUE(template_id, version));

-- ===== v0.9 助手（阶段一）：会话 / brief / 报价 / 批准 / 用量 =====
CREATE TABLE IF NOT EXISTS assistant_sessions(
  id TEXT PRIMARY KEY, project_id TEXT, intent TEXT, status TEXT DEFAULT 'active',
  turns INTEGER DEFAULT 0, questions INTEGER DEFAULT 0, tool_calls INTEGER DEFAULT 0,
  input_tokens INTEGER DEFAULT 0, output_tokens INTEGER DEFAULT 0,
  created_at REAL, updated_at REAL);

CREATE TABLE IF NOT EXISTS assistant_briefs(
  project_id TEXT PRIMARY KEY, brief_json TEXT, version INTEGER DEFAULT 1, updated_at REAL);

CREATE TABLE IF NOT EXISTS assistant_quotes(
  id TEXT PRIMARY KEY, session_id TEXT, project_id TEXT, plan_hash TEXT,
  payload_json TEXT, effective_json TEXT, cost_cny REAL,
  expires_at REAL, created_at REAL);

CREATE TABLE IF NOT EXISTS assistant_approvals(
  id TEXT PRIMARY KEY, quote_id TEXT, session_id TEXT, project_id TEXT, plan_hash TEXT,
  expires_at REAL, max_uses INTEGER DEFAULT 1, used_count INTEGER DEFAULT 0,
  task_id TEXT, created_at REAL, used_at REAL);

-- ===== v0.10 S2：项目 / 脚本版本 =====
CREATE TABLE IF NOT EXISTS projects(
  id TEXT PRIMARY KEY, name TEXT, brief_json TEXT, status TEXT DEFAULT 'draft',
  structure_template_id TEXT, structure_template_version INTEGER,
  created_at REAL, updated_at REAL, deleted_at REAL);

CREATE TABLE IF NOT EXISTS shots(
  id TEXT PRIMARY KEY, project_id TEXT, script_version_id TEXT, seq INTEGER,
  duration_s INTEGER, description TEXT, motion TEXT, status TEXT DEFAULT 'draft',
  revision INTEGER DEFAULT 1, created_at REAL, updated_at REAL);

CREATE TABLE IF NOT EXISTS storyboards(
  id TEXT PRIMARY KEY, shot_id TEXT, task_id TEXT, asset_id TEXT, version INTEGER,
  status TEXT DEFAULT 'draft', reject_reason TEXT, created_at REAL,
  UNIQUE(shot_id, version));

CREATE TABLE IF NOT EXISTS shot_takes(
  id TEXT PRIMARY KEY, shot_id TEXT, task_id TEXT, asset_id TEXT, duration_ms INTEGER,
  spec_json TEXT, cost_cny REAL, status TEXT DEFAULT 'queued', created_at REAL);

CREATE TABLE IF NOT EXISTS generation_scopes(
  id TEXT PRIMARY KEY, project_id TEXT, mode TEXT, shot_ids_json TEXT, frozen_json TEXT,
  scope_hash TEXT, estimated_cost_cny REAL, expires_at REAL, approved_at REAL,
  consumed_at REAL, status TEXT DEFAULT 'quoted', created_at REAL);

CREATE TABLE IF NOT EXISTS submit_receipts(
  id TEXT PRIMARY KEY, scope_id TEXT UNIQUE, project_id TEXT, batch_id TEXT, mode TEXT,
  tasks_json TEXT, scope_hash TEXT, created_at REAL);

CREATE TABLE IF NOT EXISTS films(
  id TEXT PRIMARY KEY, project_id TEXT, version INTEGER, status TEXT DEFAULT 'pending',
  items_json TEXT, duration_ms INTEGER, asset_id TEXT, partial INTEGER DEFAULT 0,
  created_at REAL, UNIQUE(project_id, version));

CREATE TABLE IF NOT EXISTS structure_templates(
  id TEXT PRIMARY KEY, name TEXT, latest_version INTEGER DEFAULT 1,
  status TEXT DEFAULT 'active', created_at REAL, updated_at REAL);

CREATE TABLE IF NOT EXISTS structure_template_versions(
  id TEXT PRIMARY KEY, template_id TEXT, version INTEGER, snapshot_json TEXT,
  created_at REAL, UNIQUE(template_id, version));

CREATE TABLE IF NOT EXISTS project_exports(
  id TEXT PRIMARY KEY, project_id TEXT, file_name TEXT, rel_path TEXT, bytes INTEGER,
  includes_json TEXT, created_at REAL);

CREATE TABLE IF NOT EXISTS assembly_errors(
  id TEXT PRIMARY KEY, project_id TEXT, batch_id TEXT, error TEXT, created_at REAL);

-- ===== v0.11：对话导演 / 自由画布 =====
CREATE TABLE IF NOT EXISTS director_requests(
  id TEXT PRIMARY KEY, project_id TEXT, client_request_id TEXT, payload_hash TEXT,
  status TEXT DEFAULT 'pending', turn_id TEXT, error_code TEXT, error_message TEXT,
  response_json TEXT, request_json TEXT, created_at REAL, updated_at REAL,
  UNIQUE(project_id, client_request_id));

CREATE TABLE IF NOT EXISTS director_turns(
  id TEXT PRIMARY KEY, project_id TEXT, session_id TEXT, request_id TEXT,
  user_text TEXT, reply_text TEXT, reply_type TEXT, structured_json TEXT,
  created_at REAL);

CREATE TABLE IF NOT EXISTS director_proposals(
  id TEXT PRIMARY KEY, project_id TEXT, kind TEXT, version INTEGER,
  content_json TEXT, target_id TEXT, target_version INTEGER, status TEXT DEFAULT 'draft',
  source_session_id TEXT, source_request_id TEXT, created_at REAL, applied_at REAL,
  UNIQUE(project_id, kind, version));

CREATE TABLE IF NOT EXISTS canvas_states(
  project_id TEXT PRIMARY KEY, schema_version INTEGER DEFAULT 1,
  layout_revision INTEGER DEFAULT 0, viewport_json TEXT, edges_json TEXT,
  updated_at REAL);

CREATE TABLE IF NOT EXISTS canvas_nodes(
  id TEXT PRIMARY KEY, project_id TEXT, node_id TEXT, type TEXT,
  position_json TEXT, config_json TEXT, binding_json TEXT,
  config_revision INTEGER DEFAULT 1, created_at REAL, updated_at REAL,
  UNIQUE(project_id, node_id));

-- v0.11: recoverable canvas edits and action-scoped media execution.
CREATE TABLE IF NOT EXISTS canvas_deletions(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
  deleted_nodes_json TEXT NOT NULL, removed_edges_json TEXT NOT NULL,
  delete_layout_revision INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'deleted',
  created_at REAL NOT NULL, restored_at REAL, restore_layout_revision INTEGER);

CREATE TABLE IF NOT EXISTS canvas_node_quotes(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, node_id TEXT NOT NULL,
  node_record_id TEXT NOT NULL, config_revision INTEGER NOT NULL,
  input_hash TEXT NOT NULL, frozen_json TEXT NOT NULL, estimated_cost_cny REAL NOT NULL,
  expires_at REAL NOT NULL, consumed_at REAL, execution_id TEXT, created_at REAL NOT NULL);

CREATE TABLE IF NOT EXISTS canvas_node_executions(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, node_id TEXT NOT NULL,
  node_record_id TEXT NOT NULL, quote_id TEXT UNIQUE NOT NULL,
  client_request_id TEXT NOT NULL, payload_hash TEXT NOT NULL, task_id TEXT UNIQUE NOT NULL,
  input_hash TEXT NOT NULL, config_revision INTEGER NOT NULL, created_at REAL NOT NULL,
  UNIQUE(project_id, client_request_id));

-- A tool's durable result survives a lost conversation response.
CREATE TABLE IF NOT EXISTS director_tool_operations(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, request_id TEXT NOT NULL,
  tool_name TEXT NOT NULL, input_hash TEXT NOT NULL, response_json TEXT NOT NULL,
  created_at REAL NOT NULL,
  UNIQUE(project_id, request_id, tool_name, input_hash));

-- Model tools may prepare these scopes; only the user confirmation endpoint submits.
CREATE TABLE IF NOT EXISTS director_actions(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, request_id TEXT NOT NULL,
  kind TEXT NOT NULL, input_hash TEXT NOT NULL, frozen_json TEXT NOT NULL,
  estimated_cost_cny REAL NOT NULL DEFAULT 0, expires_at REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'proposed', submit_client_request_id TEXT,
  result_json TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
  UNIQUE(project_id, request_id, kind, input_hash),
  UNIQUE(project_id, submit_client_request_id));

CREATE TABLE IF NOT EXISTS director_compositions(
  id TEXT PRIMARY KEY, project_id TEXT NOT NULL, action_id TEXT UNIQUE NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued', input_json TEXT NOT NULL,
  output_asset_id TEXT, result_node_id TEXT, error_json TEXT,
  created_at REAL NOT NULL, updated_at REAL NOT NULL, started_at REAL, finished_at REAL);

CREATE TABLE IF NOT EXISTS confirmations(
  id TEXT PRIMARY KEY, project_id TEXT, object_type TEXT, object_id TEXT,
  object_version INTEGER, status TEXT DEFAULT 'active', reason TEXT,
  created_at REAL, invalidated_at REAL,
  UNIQUE(object_type, object_id, object_version));

CREATE TABLE IF NOT EXISTS confirmation_events(
  id TEXT PRIMARY KEY, confirmation_id TEXT, project_id TEXT,
  event TEXT, snapshot_json TEXT, created_at REAL);

CREATE TABLE IF NOT EXISTS script_versions(
  id TEXT PRIMARY KEY, project_id TEXT, version INTEGER, content_json TEXT,
  source TEXT, session_id TEXT, confirmed_at REAL, created_at REAL,
  UNIQUE(project_id, version));

CREATE TABLE IF NOT EXISTS assistant_messages(
  id TEXT PRIMARY KEY, session_id TEXT, role TEXT, content TEXT,
  reply_type TEXT, structured_json TEXT, created_at REAL);

CREATE TABLE IF NOT EXISTS assistant_usage(
  id TEXT PRIMARY KEY, session_id TEXT, kind TEXT, provider TEXT, model TEXT,
  input_tokens INTEGER, output_tokens INTEGER, tool_calls INTEGER,
  cost_cny REAL, note TEXT, created_at REAL);

CREATE TABLE IF NOT EXISTS drafts(
  id TEXT PRIMARY KEY, name TEXT, content_json TEXT, version INTEGER,
  created_at REAL, updated_at REAL);

CREATE TABLE IF NOT EXISTS draft_versions(
  id TEXT PRIMARY KEY, draft_id TEXT, version INTEGER, content_json TEXT, created_at REAL);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, created_at);
CREATE INDEX IF NOT EXISTS idx_tasks_batch ON tasks(batch_id);
CREATE INDEX IF NOT EXISTS idx_assets_sha ON assets(sha256);
CREATE INDEX IF NOT EXISTS idx_template_versions ON template_versions(template_id, version);
CREATE INDEX IF NOT EXISTS idx_assistant_quotes ON assistant_quotes(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_assistant_approvals ON assistant_approvals(quote_id);
CREATE INDEX IF NOT EXISTS idx_assistant_usage ON assistant_usage(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_assistant_messages ON assistant_messages(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_assistant_sessions_project ON assistant_sessions(project_id, updated_at);
CREATE INDEX IF NOT EXISTS idx_script_versions ON script_versions(project_id, version);
CREATE INDEX IF NOT EXISTS idx_confirmations ON confirmations(project_id, object_type, status);
CREATE INDEX IF NOT EXISTS idx_shots ON shots(project_id, seq);
CREATE INDEX IF NOT EXISTS idx_storyboards ON storyboards(shot_id, version);
CREATE INDEX IF NOT EXISTS idx_takes ON shot_takes(shot_id, created_at);
CREATE INDEX IF NOT EXISTS idx_scopes ON generation_scopes(project_id, status);
CREATE INDEX IF NOT EXISTS idx_films ON films(project_id, version);
CREATE INDEX IF NOT EXISTS idx_director_turns ON director_turns(project_id, created_at);
CREATE INDEX IF NOT EXISTS idx_director_proposals ON director_proposals(project_id, kind, version);
CREATE INDEX IF NOT EXISTS idx_canvas_deletions ON canvas_deletions(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_node_executions ON canvas_node_executions(project_id, node_record_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_node_quotes ON canvas_node_quotes(project_id, node_record_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_director_actions ON director_actions(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_director_tool_operations ON director_tool_operations(project_id, request_id);
CREATE INDEX IF NOT EXISTS idx_director_compositions ON director_compositions(status, created_at);
"""


def now() -> float:
    return time.time()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:20]}"


@contextmanager
def conn():
    with _lock:
        c = sqlite3.connect(config.DB_PATH, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        try:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            yield c
        except sqlite3.IntegrityError as exc:
            _raise_project_guard_error(exc)
            raise
        finally:
            c.close()


@contextmanager
def transaction():
    """显式事务（BEGIN IMMEDIATE … COMMIT/ROLLBACK）。

    用于需要“多步写要么全成要么全不成”的场景，例如：
    消费助手批准 + 创建任务必须原子完成。
    """
    with _lock:
        c = sqlite3.connect(config.DB_PATH, timeout=30, isolation_level=None)
        c.row_factory = sqlite3.Row
        try:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=NORMAL")
            c.execute("BEGIN IMMEDIATE")
            yield c
            c.execute("COMMIT")
        except Exception as exc:
            try:
                c.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            _raise_project_guard_error(exc)
            raise
        finally:
            c.close()


MIGRATIONS = [
    ("tasks", "remote_task_id", "TEXT"),
    ("tasks", "remote_status", "TEXT"),
    ("tasks", "usage_json", "TEXT"),
    ("tasks", "cost_cny", "REAL"),
    ("tasks", "provider", "TEXT"),
    # v0.7：模板引用、外部任务关联、调用方归因、概念图标记
    ("tasks", "template_id", "TEXT"),
    ("tasks", "template_version", "INTEGER"),
    ("tasks", "external_request_id", "TEXT"),
    ("tasks", "caller_id", "TEXT"),
    ("tasks", "concept_mode", "INTEGER DEFAULT 0"),
    # v0.9.1 助手：报价/批准冻结完整方案（模板版本 + 解析后参数），防模板升版漂移
    ("assistant_quotes", "frozen_json", "TEXT"),
    ("assistant_approvals", "frozen_json", "TEXT"),
    ("assistant_messages", "reply_type", "TEXT"),
    ("assistant_messages", "structured_json", "TEXT"),
    # v0.3 助手：会话内工具事实摘要（区分 tool_query / quoted / user_selected）
    ("assistant_sessions", "facts_json", "TEXT"),
    # v0.10：成片来源批次（用于 full 模式自动合成幂等）
    ("films", "source_batch", "TEXT"),
    # v0.10.1：分镜绑定生成时的镜头修订号（B1 当前版本确认链）
    ("storyboards", "shot_revision", "INTEGER"),
    ("canvas_states", "edges_json", "TEXT"),
    ("projects", "deleted_at", "REAL"),
    ("director_requests", "request_json", "TEXT"),
]


def _raise_project_guard_error(exc: Exception) -> None:
    if isinstance(exc, sqlite3.IntegrityError) and str(exc) == "PROJECT_DELETED":
        from .errors import err
        raise err("PROJECT_DELETED", "该项目已删除，无法继续修改或生成。") from exc


def _install_project_guards(c) -> None:
    """Tombstones are enforced in SQLite too, across transactions/processes."""
    c.execute("CREATE TRIGGER IF NOT EXISTS protect_deleted_project_update BEFORE UPDATE ON projects "
              "WHEN OLD.deleted_at IS NOT NULL AND NEW.deleted_at IS NOT NULL "
              "BEGIN SELECT RAISE(ABORT, 'PROJECT_DELETED'); END")
    direct = ("shots", "script_versions", "generation_scopes", "submit_receipts",
              "films", "project_exports", "assembly_errors", "director_requests",
              "director_turns", "director_proposals", "canvas_states", "canvas_nodes",
              "canvas_deletions", "canvas_node_quotes", "canvas_node_executions",
              "director_tool_operations", "director_actions", "director_compositions",
              "confirmations", "confirmation_events", "assistant_sessions", "assistant_briefs", "assistant_quotes",
              "assistant_approvals")
    predicates = {table: "EXISTS(SELECT 1 FROM projects p WHERE p.id={row}.project_id "
                         "AND p.deleted_at IS NOT NULL)" for table in direct}
    for table in ("storyboards", "shot_takes"):
        predicates[table] = ("EXISTS(SELECT 1 FROM shots s JOIN projects p ON p.id=s.project_id "
                             "WHERE s.id={row}.shot_id AND p.deleted_at IS NOT NULL)")
    predicates["tasks"] = (
        "EXISTS(SELECT 1 FROM projects p WHERE p.deleted_at IS NOT NULL AND "
        "(p.id={row}.external_request_id OR p.id IN (SELECT s.project_id FROM shots s "
        "JOIN storyboards b ON b.shot_id=s.id WHERE b.task_id={row}.id) OR p.id IN "
        "(SELECT s.project_id FROM shots s JOIN shot_takes t ON t.shot_id=s.id "
        "WHERE t.task_id={row}.id)))")
    for table, predicate in predicates.items():
        for operation in ("INSERT", "UPDATE", "DELETE"):
            rows = ("NEW",) if operation == "INSERT" else ("OLD",) if operation == "DELETE" else ("OLD", "NEW")
            condition = " OR ".join(predicate.format(row=row) for row in rows)
            c.execute(f"CREATE TRIGGER IF NOT EXISTS protect_deleted_{table}_{operation.lower()} "
                      f"BEFORE {operation} ON {table} WHEN {condition} "
                      "BEGIN SELECT RAISE(ABORT, 'PROJECT_DELETED'); END")


def init_db() -> None:
    with conn() as c:
        c.executescript(SCHEMA)
        for table, col, typ in MIGRATIONS:
            try:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
            except sqlite3.OperationalError:
                pass  # 已存在
        # 依赖迁移列的索引必须在 ALTER 之后创建
        c.execute("CREATE INDEX IF NOT EXISTS idx_tasks_external ON tasks(external_request_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_tasks_template ON tasks(template_id)")
        _install_project_guards(c)


def row_to_dict(r: sqlite3.Row | None) -> dict | None:
    return dict(r) if r is not None else None


def insert(table: str, data: dict) -> None:
    cols = ",".join(data)
    ph = ",".join("?" * len(data))
    with conn() as c:
        c.execute(f"INSERT INTO {table}({cols}) VALUES({ph})", list(data.values()))


def update(table: str, row_id: str, data: dict) -> None:
    data = {**data, "updated_at": now()} if "updated_at" in _columns(table) else data
    sets = ",".join(f"{k}=?" for k in data)
    with conn() as c:
        c.execute(f"UPDATE {table} SET {sets} WHERE id=?", [*data.values(), row_id])


def get(table: str, row_id: str) -> dict | None:
    with conn() as c:
        return row_to_dict(c.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone())


def query(sql: str, args: Iterable[Any] = ()) -> list[dict]:
    with conn() as c:
        return [dict(r) for r in c.execute(sql, tuple(args)).fetchall()]


def _columns(table: str) -> set[str]:
    with conn() as c:
        return {r["name"] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def loads(s: str | None) -> Any:
    try:
        return json.loads(s) if s else None
    except json.JSONDecodeError:
        return None
