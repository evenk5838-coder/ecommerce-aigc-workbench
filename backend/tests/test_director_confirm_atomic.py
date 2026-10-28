"""Shared confirmation transactions on a private SQLite database; no network or models."""
from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# A standalone test invocation must not load .env or create production data folders.
_read_text = Path.read_text

def _without_dotenv(path, *args, **kwargs):
    return "" if path.name == ".env" else _read_text(path, *args, **kwargs)

with patch.object(Path, "read_text", _without_dotenv), patch.object(Path, "mkdir", lambda *a, **k: None):
    from app import config

from app import db, director, projects
from app.errors import AppError


class DirectorConfirmAtomicTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="director-confirm-atomic-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.db_path = self.root / "confirmation.db"
        for name, value in (("DB_PATH", self.db_path), ("DATA", self.root),
                            ("ASSETS", self.root / "assets"), ("MODELS", self.root / "models")):
            self.stack.enter_context(patch.object(config, name, value))
        config.ASSETS.mkdir()
        config.MODELS.mkdir()
        real_connect = sqlite3.connect

        def private_connect(database, *args, **kwargs):
            if Path(str(database)).resolve() != self.db_path.resolve():
                raise AssertionError("Only the isolated confirmation database is allowed")
            return real_connect(database, *args, **kwargs)

        self.stack.enter_context(patch("sqlite3.connect", side_effect=private_connect))
        for target in ("socket.socket.connect", "socket.socket.connect_ex", "socket.create_connection"):
            self.stack.enter_context(patch(target, side_effect=AssertionError("Network forbidden")))
        for target in ("app.assistant.provider._deepseek_post", "app.providers_ark._request"):
            self.stack.enter_context(patch(target, side_effect=AssertionError("Real model forbidden")))
        db.init_db()
        self.pid = projects.create_project("confirmation fixture", {"subject": "white cup"})["project_id"]
        self.script = projects.add_script(self.pid, {"summary": "A cup in morning light"})
        self.sid = self.script["script_id"]

    def snapshot(self):
        return {table: db.query(f"SELECT * FROM {table} ORDER BY id") for table in
                ("projects", "script_versions", "confirmations", "confirmation_events", "tasks")}

    def error(self, code, call):
        with self.assertRaises(AppError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def fail_project_update(self):
        with db.conn() as connection:
            connection.execute("CREATE TRIGGER fixture_project_failure BEFORE UPDATE OF status ON projects "
                               "WHEN NEW.status='script_confirmed' "
                               "BEGIN SELECT RAISE(ABORT, 'project fixture failure'); END")

    def composition(self, status, project_id=None):
        cid = db.new_id("composition_fixture")
        db.insert("director_compositions", {"id": cid, "project_id": project_id or self.pid,
                  "action_id": db.new_id("action_fixture"), "status": status, "input_json": "{}",
                  "created_at": db.now(), "updated_at": db.now()})
        return cid

    def test_public_confirmation_preserves_response_and_updates_all_records(self):
        with patch.object(db, "now", return_value=100.0):
            out = projects.confirm_script(self.sid)
        self.assertEqual(out["script_id"], self.sid)
        self.assertEqual(out["version"], 1)
        self.assertEqual(out["confirmed_at"], 100.0)
        self.assertEqual(out["unlocked"], ["storyboard_plan"])
        self.assertEqual(out["confirmation"], {
            "confirmation_id": out["confirmation"]["confirmation_id"], "status": "active",
            "object_type": "script", "object_id": self.sid, "object_version": 1, "reused": False})
        self.assertEqual(db.get("script_versions", self.sid)["confirmed_at"], 100.0)
        self.assertEqual(db.get("projects", self.pid)["status"], "script_confirmed")
        self.assertEqual(len(db.query("SELECT * FROM confirmations")), 1)
        self.assertEqual(db.query("SELECT * FROM confirmation_events"), [])
        self.assertEqual(db.query("SELECT * FROM tasks"), [])

    def test_active_confirmation_reuse_retains_identity_and_existing_timestamp_behavior(self):
        with patch.object(db, "now", return_value=100.0):
            first = projects.confirm_script(self.sid)
        with patch.object(db, "now", return_value=200.0):
            second = projects.confirm_script(self.sid)
        self.assertEqual(second["confirmation"]["confirmation_id"], first["confirmation"]["confirmation_id"])
        self.assertTrue(second["confirmation"]["reused"])
        self.assertEqual(second["confirmed_at"], 200.0)
        self.assertEqual(db.get("confirmations", first["confirmation"]["confirmation_id"])["created_at"], 100.0)
        self.assertEqual(len(db.query("SELECT * FROM confirmations")), 1)
        self.assertEqual(db.query("SELECT * FROM confirmation_events"), [])

    def test_reconfirmation_archives_previous_state_and_reactivates_same_row(self):
        first = projects.confirm_script(self.sid)
        cid = first["confirmation"]["confirmation_id"]
        director.invalidate("script", project_id=self.pid, reason="upstream_brief_changed")
        previous = db.get("confirmations", cid)
        out = projects.confirm_script(self.sid)
        self.assertEqual(out["confirmation"]["confirmation_id"], cid)
        self.assertFalse(out["confirmation"]["reused"])
        events = db.query("SELECT * FROM confirmation_events")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["confirmation_id"], cid)
        self.assertEqual(events[0]["project_id"], self.pid)
        self.assertEqual(events[0]["event"], "reconfirmed")
        self.assertEqual(db.loads(events[0]["snapshot_json"]), previous)
        current = db.get("confirmations", cid)
        self.assertEqual(current["status"], "active")
        self.assertIsNone(current["reason"])
        self.assertIsNone(current["invalidated_at"])

    def test_public_false_and_missing_script_keep_original_errors_without_writes(self):
        before = self.snapshot()
        self.error("VALIDATION_FAILED", lambda: projects.confirm_script(self.sid, False))
        self.error("SCRIPT_NOT_FOUND", lambda: projects.confirm_script("missing"))
        self.assertEqual(self.snapshot(), before)

    def test_object_helper_retains_supported_types_and_rejects_unknown_type(self):
        for kind in director.OBJECT_TYPES:
            with self.subTest(kind=kind):
                out = director.confirm_object(self.pid, kind, f"fixture-{kind}", 2)
                self.assertEqual((out["object_type"], out["object_version"], out["reused"]), (kind, 2, False))
        before = self.snapshot()
        self.error("VALIDATION_FAILED", lambda: director.confirm_object(self.pid, "unknown", "fixture", 1))
        self.assertEqual(self.snapshot(), before)

    def test_helpers_use_supplied_connection_and_see_uncommitted_script(self):
        with db.transaction() as connection:
            script = projects._add_script_in_tx(connection, self.pid, {"summary": "uncommitted version"})
            with patch.object(db, "get", side_effect=AssertionError("No separate read")), \
                    patch.object(db, "query", side_effect=AssertionError("No separate read")), \
                    patch.object(db, "update", side_effect=AssertionError("No separate write")), \
                    patch.object(db, "transaction", side_effect=AssertionError("No nested transaction")), \
                    patch.object(projects, "get_project", side_effect=AssertionError("Use SQL connection")):
                out = projects.confirm_script_in_tx(connection, script["script_id"])
            self.assertTrue(connection.in_transaction)
            self.assertEqual(connection.execute("SELECT status FROM projects WHERE id=?", (self.pid,)).fetchone()[0],
                             "script_confirmed")
        self.assertEqual(out["version"], 2)
        self.assertEqual(director.current_confirmation("script", script["script_id"], 2)["valid"], True)

    def test_outer_failure_rolls_back_confirmation_script_and_project(self):
        before = self.snapshot()
        with self.assertRaisesRegex(RuntimeError, "outer fixture failure"):
            with db.transaction() as connection:
                projects.confirm_script_in_tx(connection, self.sid)
                connection.execute("UPDATE projects SET name='uncommitted action' WHERE id=?", (self.pid,))
                raise RuntimeError("outer fixture failure")
        self.assertEqual(self.snapshot(), before)

    def test_script_timestamp_write_failure_rolls_back_confirmation(self):
        before = self.snapshot()
        with db.conn() as connection:
            connection.execute("CREATE TRIGGER fixture_script_failure BEFORE UPDATE OF confirmed_at ON script_versions "
                               "BEGIN SELECT RAISE(ABORT, 'script fixture failure'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "script fixture failure"):
            projects.confirm_script(self.sid)
        self.assertEqual(self.snapshot(), before)

    def test_project_status_failure_rolls_back_confirmation_and_script_timestamp(self):
        before = self.snapshot()
        self.fail_project_update()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "project fixture failure"):
            projects.confirm_script(self.sid)
        self.assertEqual(self.snapshot(), before)

    def test_reconfirmation_failure_rolls_back_event_and_reactivation(self):
        projects.confirm_script(self.sid)
        director.invalidate("script", project_id=self.pid, reason="fixture invalidation")
        before = self.snapshot()
        self.fail_project_update()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "project fixture failure"):
            projects.confirm_script(self.sid)
        self.assertEqual(self.snapshot(), before)

    def test_repeated_confirmation_failure_preserves_existing_confirmation_and_timestamp(self):
        projects.confirm_script(self.sid)
        before = self.snapshot()
        self.fail_project_update()
        with self.assertRaisesRegex(sqlite3.IntegrityError, "project fixture failure"):
            projects.confirm_script(self.sid)
        self.assertEqual(self.snapshot(), before)

    def test_helpers_validate_missing_and_deleted_project_on_same_transaction(self):
        projects.delete_project(self.pid)
        before = self.snapshot()
        for expected, call in (
            ("PROJECT_NOT_FOUND", lambda c: director.confirm_object_in_tx(c, "missing", "script", "absent", 1)),
            ("PROJECT_DELETED", lambda c: director.confirm_object_in_tx(c, self.pid, "script", self.sid, 1)),
            ("PROJECT_DELETED", lambda c: projects.confirm_script_in_tx(c, self.sid)),
        ):
            with self.subTest(expected=expected):
                with db.transaction() as connection:
                    self.error(expected, lambda: call(connection))
        self.assertEqual(self.snapshot(), before)

    def test_public_and_sql_guards_reject_deleted_project_without_partial_writes(self):
        out = projects.confirm_script(self.sid)
        cid = out["confirmation"]["confirmation_id"]
        projects.delete_project(self.pid)
        before = self.snapshot()
        self.error("PROJECT_DELETED", lambda: projects.confirm_script(self.sid))
        self.error("PROJECT_DELETED", lambda: director.confirm_object(self.pid, "script", self.sid, 1))
        statements = [
            ("UPDATE script_versions SET confirmed_at=123 WHERE id=?", (self.sid,)),
            ("UPDATE confirmations SET status='active' WHERE id=?", (cid,)),
            ("INSERT INTO confirmations(id,project_id,object_type,object_id,object_version) VALUES(?,?,?,?,?)",
             ("stale-confirm", self.pid, "script", "stale", 1)),
            ("INSERT INTO confirmation_events(id,confirmation_id,project_id,event) VALUES(?,?,?,?)",
             ("stale-event", cid, self.pid, "reconfirmed")),
        ]
        for sql, values in statements:
            with self.subTest(sql=sql):
                with self.assertRaises(AppError) as caught:
                    with db.transaction() as connection:
                        connection.execute(sql, values)
                self.assertEqual(caught.exception.code, "PROJECT_DELETED")
        self.assertEqual(self.snapshot(), before)

    def test_confirmation_cannot_be_reused_or_reactivated_across_projects(self):
        other = projects.create_project("other fixture")["project_id"]
        director.confirm_object(self.pid, "script", self.sid, 1)
        for status in ("active", "invalidated"):
            with self.subTest(status=status):
                if status == "invalidated":
                    director.invalidate("script", project_id=self.pid, reason="fixture")
                before = self.snapshot()
                self.error("VALIDATION_FAILED", lambda: director.confirm_object(other, "script", self.sid, 1))
                self.assertEqual(self.snapshot(), before)

    def test_queued_and_running_local_compositions_block_project_deletion(self):
        for status in ("queued", "running"):
            with self.subTest(status=status):
                cid = self.composition(status)
                failure = self.error("PROJECT_BUSY", lambda: projects.delete_project(self.pid))
                self.assertEqual(failure.detail["compositions"], [{"id": cid, "status": status}])
                self.assertIsNone(db.get("projects", self.pid)["deleted_at"])
                db.update("director_compositions", cid, {"status": "failed"})
        self.assertTrue(projects.delete_project(self.pid)["deleted"])

    def test_completed_composition_and_other_project_work_do_not_block_delete(self):
        other = projects.create_project("other composition fixture")["project_id"]
        for status in ("succeeded", "failed"):
            self.composition(status)
        self.composition("running", other)
        self.assertTrue(projects.delete_project(self.pid)["deleted"])
        self.assertIsNone(projects.get_project(other)["deleted_at"])
        self.assertEqual(len(db.query("SELECT * FROM director_compositions")), 3)

    def test_project_deletion_stays_compatible_without_composition_table(self):
        with db.conn() as connection:
            connection.execute("DROP TABLE director_compositions")
        self.assertTrue(projects.delete_project(self.pid)["deleted"])


if __name__ == "__main__":
    unittest.main()
