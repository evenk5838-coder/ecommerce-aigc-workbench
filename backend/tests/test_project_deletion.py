"""Isolated lifecycle tests. Never start lifespan/worker or access the business DB."""
from __future__ import annotations

import sys
import asyncio
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config, db, main, projects
from app.errors import AppError


class Response:
    def __init__(self, status, body):
        self.status_code, self.text = status, body.decode()

    def json(self):
        return json.loads(self.text)


class LocalASGIClient:
    """Exercise real routes in memory; no network, lifespan, http client or worker."""
    def request(self, method, path, **kwargs):
        async def run():
            data = json.dumps(kwargs["json"]).encode() if "json" in kwargs else b""
            messages, consumed = [], False
            async def receive():
                nonlocal consumed
                if not consumed:
                    consumed = True
                    return {"type": "http.request", "body": data, "more_body": False}
                await asyncio.Event().wait()
            async def send(message):
                messages.append(message)
            await main.app({"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                            "method": method.upper(), "scheme": "http", "path": path,
                            "raw_path": path.encode(), "query_string": b"", "root_path": "",
                            "headers": [(b"content-type", b"application/json")],
                            "client": ("127.0.0.1", 1), "server": ("isolated", 80)}, receive, send)
            status = next(m["status"] for m in messages if m["type"] == "http.response.start")
            return Response(status, b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body"))
        return asyncio.run(run())

    def get(self, path):
        return self.request("get", path)

    def delete(self, path):
        return self.request("delete", path)


class ProjectDeletionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="project-delete-tests-")
        self.old_db, self.old_assets = config.DB_PATH, config.ASSETS
        config.DB_PATH = Path(self.tmp.name) / "app.db"
        config.ASSETS = Path(self.tmp.name) / "assets"
        config.ASSETS.mkdir()
        db.init_db()
        self.client = LocalASGIClient()
        self.worker = patch.object(main.worker, "start", side_effect=AssertionError("worker forbidden"))
        self.worker.start()
        self.providers = [patch("app.assistant.provider.get_provider", side_effect=AssertionError("LLM forbidden")),
                          patch("app.assistant.service.get_provider", side_effect=AssertionError("LLM forbidden")),
                          patch("app.providers_ark._request", side_effect=AssertionError("cloud forbidden"))]
        for guard in self.providers:
            guard.start()
        self.project = projects.create_project("保留历史", {"subject": "白瓶"})["project_id"]
        self.other = projects.create_project("另一个项目")["project_id"]

    def tearDown(self):
        self.worker.stop()
        for guard in self.providers:
            guard.stop()
        config.DB_PATH, config.ASSETS = self.old_db, self.old_assets
        self.tmp.cleanup()

    def expect_error(self, code, fn):
        with self.assertRaises(AppError) as caught:
            fn()
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def seed_history(self, task_status="failed", external=True):
        now = db.now()
        for table, row in (
            ("assets", {"id": "asset", "kind": "image", "rel_path": "kept.png", "size": 3}),
            ("script_versions", {"id": "script", "project_id": self.project, "version": 1,
                                 "content_json": "{}", "source": "manual", "created_at": now}),
            ("shots", {"id": "shot", "project_id": self.project, "script_version_id": "script",
                       "seq": 1, "duration_s": 5, "description": "商品", "revision": 1}),
            ("tasks", {"id": "task", "kind": "image", "status": task_status, "params_json": "{}",
                       "model_id": "unneeded-for-delete", "external_request_id": self.project if external else None,
                       "input_asset_id": "asset", "output_asset_id": "asset", "created_at": now,
                       "cancel_requested": 0}),
            ("storyboards", {"id": "board", "shot_id": "shot", "task_id": "task", "asset_id": "asset",
                             "version": 1, "status": "approved"}),
            ("shot_takes", {"id": "take", "shot_id": "shot", "task_id": "task", "asset_id": "asset",
                            "status": "failed", "spec_json": "{}"}),
            ("generation_scopes", {"id": "scope", "project_id": self.project, "mode": "clips",
                                   "status": "approved", "expires_at": now+1000, "frozen_json": "{}"}),
            ("films", {"id": "film", "project_id": self.project, "version": 1, "status": "ready",
                       "items_json": "{}", "asset_id": "asset"}),
            ("assistant_sessions", {"id": "session", "project_id": self.project, "intent": "auto"}),
            ("director_proposals", {"id": "proposal", "project_id": self.project, "kind": "brief",
                                    "version": 1, "status": "draft", "content_json": '{"subject":"新瓶"}'}),
        ):
            db.insert(table, row)
        (config.ASSETS / "kept.png").write_bytes(b"abc")

    def test_soft_delete_is_idempotent_and_preserves_history_files_and_other_projects(self):
        self.seed_history()
        tables = ("assets", "tasks", "shots", "storyboards", "shot_takes", "films", "script_versions",
                  "assistant_sessions", "director_proposals")
        before = {table: db.query(f"SELECT * FROM {table}") for table in tables}
        response = self.client.delete(f"/api/projects/{self.project}")
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["project_id"], self.project)
        self.assertTrue(result["deleted"])
        self.assertFalse(result["already_deleted"])
        second = self.client.delete(f"/api/projects/{self.project}")
        self.assertEqual(second.json(), {**result, "already_deleted": True})
        self.assertEqual(before, {table: db.query(f"SELECT * FROM {table}") for table in tables})
        self.assertEqual((config.ASSETS / "kept.png").read_bytes(), b"abc")
        self.assertEqual([r["project_id"] for r in self.client.get("/api/projects").json()["items"]], [self.other])
        self.assertEqual(self.client.get(f"/api/projects/{self.project}").status_code, 410)
        self.assertEqual(self.client.get(f"/api/projects/{self.other}").status_code, 200)
        self.assertEqual(self.client.get("/api/tasks/task").status_code, 200)
        self.assertEqual(self.client.delete("/api/projects/missing").status_code, 404)

    def test_all_related_write_entries_reject_deleted_project_before_side_effects(self):
        self.seed_history()
        projects.delete_project(self.project)
        p = self.project
        requests = [
            ("patch", f"/api/projects/{p}", {"name": "不可恢复"}),
            ("post", f"/api/projects/{p}/scripts", {}),
            ("post", f"/api/projects/{p}/scripts/manual", {"content": {}}),
            ("post", f"/api/projects/{p}/adopt-draft", {"kind": "script", "content": {}}),
            ("patch", "/api/scripts/script", {"content": {}}),
            ("post", "/api/scripts/script/confirm", {"confirm": True}),
            ("post", f"/api/projects/{p}/shots", {"script_id": "script", "shots": []}),
            ("patch", "/api/shots/shot", {"description": "不可改"}),
            ("delete", "/api/shots/shot", None),
            ("post", f"/api/projects/{p}/shots/reorder", {"order": ["shot"]}),
            ("post", "/api/shots/shot/storyboard", {"model_id": "any", "confirm": True}),
            ("post", "/api/storyboards/board/review", {"approve": True}),
            ("post", "/api/scopes/scope/confirm", {"confirm": True}),
            ("post", f"/api/projects/{p}/generate", {"scope_id": "scope"}),
            ("post", "/api/takes/take/retry", {}),
            ("post", f"/api/projects/{p}/films", {}),
            ("post", f"/api/projects/{p}/structure-templates", {"name": "模板"}),
            ("post", f"/api/projects/{p}/apply-structure", {"template_id": "missing"}),
            ("post", f"/api/projects/{p}/export", {}),
            ("patch", f"/api/projects/{p}/canvas", {"layout_revision": 0, "nodes": []}),
            ("post", f"/api/projects/{p}/director/chat", {"message": "不调用", "client_request_id": "once"}),
            ("post", f"/api/projects/{p}/director/proposals/proposal/apply", {}),
            ("post", "/api/assistant/chat", {"project_id": p, "message": "不调用"}),
            ("post", "/api/assistant/chat", {"session_id": "session", "message": "不调用"}),
            ("post", "/api/tasks", {"kind": "image", "model_id": "any", "params": {}, "external_request_id": p}),
            ("post", "/api/tasks/task/retry", {}),
        ]
        for method, path, body in requests:
            with self.subTest(path=path, method=method):
                response = self.client.request(method, path, **({"json": body} if body is not None else {}))
                self.assertEqual(response.status_code, 410, response.text)
                self.assertEqual(response.json()["error"]["code"], "PROJECT_DELETED")
        self.assertEqual(db.query("SELECT COUNT(*) n FROM tasks")[0]["n"], 1)
        self.assertEqual(db.get("tasks", "task")["cancel_requested"], 0)

    def test_active_or_unknown_tasks_block_delete_including_legacy_indirect_link(self):
        self.seed_history(task_status="queued", external=False)
        for state in ("queued", "loading", "running", "remote_running", "postprocess", "cancel_requested", "unknown"):
            with self.subTest(state=state):
                db.update("tasks", "task", {"status": state})
                result = self.client.delete(f"/api/projects/{self.project}")
                self.assertEqual(result.status_code, 409)
                self.assertEqual(result.json()["error"]["code"], "PROJECT_BUSY")
                self.assertIsNone(db.get("projects", self.project)["deleted_at"])
                self.assertEqual(db.get("tasks", "task")["cancel_requested"], 0)

    def test_active_composition_or_director_request_blocks_delete(self):
        db.insert("films", {"id": "f", "project_id": self.project, "version": 1, "status": "composing"})
        self.expect_error("PROJECT_BUSY", lambda: projects.delete_project(self.project))
        db.update("films", "f", {"status": "failed"})
        db.insert("director_requests", {"id": "req", "project_id": self.project,
                                       "client_request_id": "r", "status": "pending"})
        for state in ("pending", "unknown"):
            db.update("director_requests", "req", {"status": state})
            self.expect_error("PROJECT_BUSY", lambda: projects.delete_project(self.project))
        db.update("director_requests", "req", {"status": "failed"})
        self.assertTrue(projects.delete_project(self.project)["deleted"])

    def test_unknown_provider_creation_and_pending_output_attachment_block_delete(self):
        self.seed_history()
        db.update("tasks", "task", {"error_code": "CREATE_RESULT_UNKNOWN"})
        self.expect_error("PROJECT_BUSY", lambda: projects.delete_project(self.project))
        db.update("tasks", "task", {"error_code": None, "status": "succeeded"})
        db.update("storyboards", "board", {"asset_id": None})
        self.expect_error("PROJECT_BUSY", lambda: projects.delete_project(self.project))
        db.update("storyboards", "board", {"asset_id": "asset"})
        db.update("shot_takes", "take", {"status": "queued"})
        self.expect_error("PROJECT_BUSY", lambda: projects.delete_project(self.project))
        db.update("shot_takes", "take", {"status": "succeeded"})
        self.assertTrue(projects.delete_project(self.project)["deleted"])

    def test_full_batch_waiting_for_automatic_composition_blocks_delete(self):
        self.seed_history(task_status="succeeded")
        db.update("tasks", "task", {"batch_id": "batch"})
        db.insert("submit_receipts", {"id": "receipt", "project_id": self.project,
                                     "batch_id": "batch", "mode": "full"})
        self.expect_error("PROJECT_BUSY", lambda: projects.delete_project(self.project))
        # A durable terminal composition outcome closes the gap, even if failed.
        db.insert("assembly_errors", {"id": "error", "project_id": self.project,
                                     "batch_id": "batch", "error": "isolated failure"})
        self.assertTrue(projects.delete_project(self.project)["deleted"])

    def test_in_flight_operation_blocks_delete_without_waiting_or_canceling(self):
        entered, release = threading.Event(), threading.Event()
        @projects.project_guard()
        def operation(project_id):
            entered.set()
            release.wait(timeout=3)
        worker = threading.Thread(target=operation, args=(self.project,))
        worker.start()
        self.assertTrue(entered.wait(timeout=2))
        try:
            exc = self.expect_error("PROJECT_BUSY", lambda: projects.delete_project(self.project))
            self.assertTrue(exc.detail["operation_in_progress"])
        finally:
            release.set()
            worker.join(timeout=2)
        self.assertTrue(projects.delete_project(self.project)["deleted"])

    def test_sqlite_guards_close_stale_writers_and_roll_back_whole_transaction(self):
        self.seed_history()
        projects.delete_project(self.project)
        for action in (
            lambda: db.update("projects", self.project, {"name": "late"}),
            lambda: db.update("shots", "shot", {"description": "late"}),
            lambda: db.insert("shots", {"id": "late", "project_id": self.project}),
            lambda: db.update("storyboards", "board", {"status": "approved"}),
            lambda: db.update("assistant_sessions", "session", {"project_id": self.other}),
            lambda: db.query("DELETE FROM shot_takes WHERE id='take'"),
            lambda: db.update("tasks", "task", {"cancel_requested": 1}),
        ):
            self.expect_error("PROJECT_DELETED", action)
        def race():
            with db.transaction() as c:
                c.execute("UPDATE projects SET name='should rollback' WHERE id=?", (self.other,))
                c.execute("INSERT INTO script_versions(id,project_id,version) VALUES('late',?,2)", (self.project,))
        self.expect_error("PROJECT_DELETED", race)
        self.assertEqual(projects.get_project(self.other)["name"], "另一个项目")
        self.assertIsNone(db.get("script_versions", "late"))

    def test_migration_is_repeatable_and_tombstone_survives_reinitialization(self):
        original = projects.get_project(self.project)
        db.init_db()
        self.assertEqual(projects.get_project(self.project), original)
        receipt = projects.delete_project(self.project)
        db.init_db()
        self.assertEqual(db.get("projects", self.project)["deleted_at"], receipt["deleted_at"])
        self.expect_error("PROJECT_DELETED", lambda: projects.get_project(self.project))

    def test_old_project_schema_is_migrated_without_dropping_rows(self):
        config.DB_PATH = Path(self.tmp.name) / "legacy.db"
        with db.conn() as c:
            c.execute("CREATE TABLE projects(id TEXT PRIMARY KEY, name TEXT, brief_json TEXT, "
                      "status TEXT, structure_template_id TEXT, structure_template_version INTEGER, "
                      "created_at REAL, updated_at REAL)")
            c.execute("INSERT INTO projects(id,name,brief_json,status) VALUES('legacy','旧项目','{}','draft')")
        db.init_db()
        self.assertEqual(projects.get_project("legacy")["name"], "旧项目")
        self.assertIsNone(projects.get_project("legacy")["deleted_at"])
        self.assertTrue(projects.delete_project("legacy")["deleted"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
