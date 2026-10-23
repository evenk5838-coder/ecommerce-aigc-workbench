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

CREATE TABLE IF NOT EXISTS drafts(
  id TEXT PRIMARY KEY, name TEXT, content_json TEXT, version INTEGER,
  created_at REAL, updated_at REAL);

CREATE TABLE IF NOT EXISTS draft_versions(
  id TEXT PRIMARY KEY, draft_id TEXT, version INTEGER, content_json TEXT, created_at REAL);

CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status, created_at);
CREATE INDEX IF NOT EXISTS idx_tasks_batch ON tasks(batch_id);
CREATE INDEX IF NOT EXISTS idx_assets_sha ON assets(sha256);
CREATE INDEX IF NOT EXISTS idx_template_versions ON template_versions(template_id, version);
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
]


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
