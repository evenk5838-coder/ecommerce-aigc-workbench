"""v0.11 script/proposal/canvas transactions, using temporary SQLite and a stub LLM.

No server or worker is started. Every SQLite connection is restricted to this
test's temporary database; socket connections and real provider calls fail.
"""
from __future__ import annotations

from contextlib import ExitStack
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import canvas, config, db, director_chat, projects
from app.assistant import provider as provider_module
from app.assistant.provider import FixedReplyProvider
from app.errors import AppError


class DirectorV011Atomic(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="director-v011-atomic-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.db_path = self.root / "atomic.db"
        for name, value in (
            ("DB_PATH", self.db_path),
            ("DATA", self.root),
            ("ASSETS", self.root / "assets"),
            ("MODELS", self.root / "models"),
            ("DIRECTOR_MAX_COST_PER_CALL_CNY", 0),
            ("DIRECTOR_MAX_SESSION_COST_CNY", 0),
        ):
            self.stack.enter_context(patch.object(config, name, value))
        for folder in (config.ASSETS, config.MODELS):
            folder.mkdir()

        real_connect = sqlite3.connect

        def isolated_connect(database, *args, **kwargs):
            if Path(str(database)).resolve() != self.db_path.resolve():
                raise AssertionError("Only the isolated fixture database may be opened")
            return real_connect(database, *args, **kwargs)

        self.stack.enter_context(patch("sqlite3.connect", side_effect=isolated_connect))
        for target in ("socket.create_connection", "socket.socket.connect", "socket.socket.connect_ex"):
            self.stack.enter_context(patch(target, side_effect=AssertionError("Network forbidden in atomic tests")))
        self.stack.enter_context(patch.object(
            provider_module, "_deepseek_post", side_effect=AssertionError("Real LLM forbidden")))
        self.stack.enter_context(patch(
            "app.providers_ark._request", side_effect=AssertionError("Real generation forbidden")))
        db.init_db()
        self.pid = projects.create_project("atomic fixture", {"subject": "one white bottle", "quantity": 1})["project_id"]

    @staticmethod
    def content(summary="one bottle on a clean background"):
        return {"summary": summary, "shots": [{"seq": 1, "hint": "Show the single bottle", "duration_s": 5}]}

    def snapshot(self, project_id=None):
        pid = project_id or self.pid
        result = {"project": db.get("projects", pid)}
        for table in ("script_versions", "director_proposals", "canvas_nodes", "canvas_states",
                      "director_requests", "director_turns", "confirmations", "confirmation_events"):
            order = "project_id" if table == "canvas_states" else "id"
            result[table] = db.query(f"SELECT * FROM {table} WHERE project_id=? ORDER BY {order}", (pid,))
        return result

    def node(self, project_id=None):
        rows = db.query("SELECT * FROM canvas_nodes WHERE project_id=? AND node_id='script'", (project_id or self.pid,))
        self.assertEqual(len(rows), 1, "Each project has exactly one canonical script node")
        return rows[0]

    def assert_binding(self, script, project_id=None):
        node = self.node(project_id)
        self.assertEqual(node["type"], "script")
        binding = db.loads(node["binding_json"])
        self.assertEqual(binding["script_id"], script["script_id"])
        self.assertEqual(binding["script_version"], script["version"])
        return node

    def expect_error(self, code, fn):
        with self.assertRaises(AppError) as caught:
            fn()
        self.assertEqual(caught.exception.code, code)

    def propose(self, request_id="tool-fixture"):
        return director_chat.execute_tool("propose_script", self.content(),
            {"project_id": self.pid, "session_id": None, "request_id": request_id})

    def script_provider(self):
        return FixedReplyProvider([
            {"tool_calls": [{"name": "propose_script", "arguments": self.content()}],
             "input_tokens": 12, "output_tokens": 20},
            {"reply": "Unexpected extra model call after the draft was saved"},
        ])

    def test_transaction_helper_creates_version_and_canonical_node_together(self):
        with db.transaction() as connection:
            script = projects._add_script_in_tx(connection, self.pid, self.content(), base_version=0)
            rows = connection.execute("SELECT * FROM canvas_nodes WHERE project_id=? AND node_id='script'", (self.pid,)).fetchall()
            self.assertEqual(len(rows), 1, "The node must exist before the shared transaction commits")
            self.assertEqual(db.loads(rows[0]["binding_json"])["script_id"], script["script_id"])
        self.assert_binding(script)
        self.assertEqual(script["version"], 1)
        self.assertIsNone(projects.get_script(script["script_id"])["confirmed_at"])

    def test_outer_failure_rolls_back_helper_script_node_and_project_state(self):
        before = self.snapshot()
        with self.assertRaisesRegex(RuntimeError, "late fixture failure"):
            with db.transaction() as connection:
                projects._add_script_in_tx(connection, self.pid, self.content())
                raise RuntimeError("late fixture failure")
        self.assertEqual(self.snapshot(), before)

    def test_public_add_script_binds_a_stable_node_and_preserves_history_and_layout(self):
        first = projects.add_script(self.pid, self.content("version one"))
        projects.confirm_script(first["script_id"])
        old_script = projects.get_script(first["script_id"])
        canvas.patch_canvas(self.pid, {
            "nodes": [{"node_id": "script", "position": {"x": 301, "y": -85}},
                      {"node_id": "display-note", "type": "asset", "position": {"x": 800, "y": 0}}],
            "viewport": {"x": 15, "y": 22, "zoom": 0.8},
            "edges": [{"from": "script", "to": "display-note"}],
        })
        old_node = self.node()
        old_canvas = canvas.get_canvas(self.pid)
        second = projects.add_script(self.pid, self.content("version two"), base_version=1)
        new_node = self.assert_binding(second)
        for key in ("id", "node_id", "position_json", "config_json", "created_at"):
            self.assertEqual(new_node[key], old_node[key], key)
        self.assertGreater(new_node["config_revision"], old_node["config_revision"])
        new_canvas = canvas.get_canvas(self.pid)
        self.assertGreater(new_canvas["layout_revision"], old_canvas["layout_revision"])
        self.assertEqual(new_canvas["viewport"], old_canvas["viewport"])
        self.assertEqual(new_canvas["edges"], old_canvas["edges"])
        self.assertEqual(projects.get_script(first["script_id"]), old_script)
        self.assertEqual([s["version"] for s in projects.list_scripts(self.pid)], [2, 1])
        self.assertIsNone(projects.get_script(second["script_id"])["confirmed_at"])

    def test_existing_unbound_frontend_script_node_is_adopted_without_reset(self):
        canvas.patch_canvas(self.pid, {"nodes": [{"node_id": "script", "type": "script",
            "position": {"x": 222, "y": 333}, "config": {"prompt": "keep existing config"}}]})
        old = self.node()
        script = projects.add_script(self.pid, self.content())
        new = self.assert_binding(script)
        for key in ("id", "position_json", "config_json", "created_at"):
            self.assertEqual(new[key], old[key], key)

    def test_canvas_helper_failure_after_writes_rolls_back_public_add_script(self):
        projects.add_script(self.pid, self.content("existing"))
        before = self.snapshot()
        real_upsert = canvas.upsert_script_node_in_tx

        def fail_after_node_write(*args, **kwargs):
            real_upsert(*args, **kwargs)
            raise RuntimeError("node fixture failure")

        with patch.object(canvas, "upsert_script_node_in_tx", side_effect=fail_after_node_write):
            with self.assertRaisesRegex(RuntimeError, "node fixture failure"):
                projects.add_script(self.pid, self.content("must roll back"), base_version=1)
        self.assertEqual(self.snapshot(), before)
        next_script = projects.add_script(self.pid, self.content("after rollback"), base_version=1)
        self.assertEqual(next_script["version"], 2, "A rollback must not consume a version")

    def test_proposal_insert_failure_rolls_back_script_node_and_project_state(self):
        projects.add_script(self.pid, self.content("existing"))
        before = self.snapshot()
        with db.conn() as connection:
            connection.execute("CREATE TRIGGER fixture_reject_script_proposal BEFORE INSERT ON director_proposals "
                "WHEN NEW.kind='script' BEGIN SELECT RAISE(ABORT, 'proposal fixture failure'); END")
        with self.assertRaisesRegex(Exception, "proposal fixture failure"):
            self.propose()
        self.assertEqual(self.snapshot(), before)

    def test_canvas_helper_failure_rolls_back_director_proposal_operation(self):
        before = self.snapshot()
        real_upsert = canvas.upsert_script_node_in_tx

        def fail_after_node_write(*args, **kwargs):
            real_upsert(*args, **kwargs)
            raise RuntimeError("director node fixture failure")

        with patch.object(canvas, "upsert_script_node_in_tx", side_effect=fail_after_node_write):
            with self.assertRaisesRegex(RuntimeError, "director node fixture failure"):
                self.propose()
        self.assertEqual(self.snapshot(), before)

    def test_script_chat_saves_real_node_reference_and_stops_after_success(self):
        provider = self.script_provider()
        out = director_chat.chat(self.pid, {"message": "Write and save a bottle script",
            "intent": "script", "client_request_id": "saved-script"}, provider=provider)
        self.assertEqual(len(provider.calls), 1, "A saved script must not invoke the model again")
        self.assertEqual(out["outcome"]["status"], "saved")
        self.assertEqual(out["outcome"]["goal"], "script")
        self.assertEqual(out["outcome"]["node_ids"], ["script"])
        artifacts = [a for a in out["artifacts"] if a["type"] == "script"]
        self.assertEqual(len(artifacts), 1)
        artifact = artifacts[0]
        self.assertEqual(artifact["node_id"], "script")
        self.assertFalse(artifact["confirmed"])
        script = projects.get_script(artifact["target_id"])
        self.assertEqual(script["version"], artifact["target_version"])
        self.assert_binding(script)
        proposal = db.get("director_proposals", artifact["proposal_id"])
        self.assertEqual(proposal["target_id"], script["script_id"])
        self.assertEqual(proposal["target_version"], script["version"])
        self.assertEqual(proposal["status"], "draft")
        receipt = director_chat.request_receipt(self.pid, "saved-script")
        self.assertEqual(receipt["response"]["artifacts"], out["artifacts"])
        self.assertEqual(receipt["response"]["outcome"], out["outcome"])

    def test_replay_uses_saved_receipt_without_new_script_proposal_node_or_model_call(self):
        provider = self.script_provider()
        body = {"message": "Write and save a bottle script", "intent": "script",
            "client_request_id": "same-request", "expected_workflow_revision": director_chat._workflow_revision(self.pid)}
        first = director_chat.chat(self.pid, body, provider=provider)
        before = self.snapshot()
        provider_calls = len(provider.calls)
        replay = director_chat.chat(self.pid, body, provider=provider)
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["artifacts"], first["artifacts"])
        self.assertEqual(replay["outcome"], first["outcome"])
        self.assertEqual(len(provider.calls), provider_calls)
        self.assertEqual(self.snapshot(), before)

    def test_stale_base_version_rejects_without_partial_state(self):
        projects.add_script(self.pid, self.content("existing"))
        before = self.snapshot()
        self.expect_error("SCRIPT_VERSION_CONFLICT", lambda: projects.add_script(
            self.pid, self.content("stale"), base_version=0))
        self.assertEqual(self.snapshot(), before)
        with self.assertRaises(AppError) as caught:
            with db.transaction() as connection:
                projects._add_script_in_tx(connection, self.pid, self.content("stale helper"), base_version=0)
        self.assertEqual(caught.exception.code, "SCRIPT_VERSION_CONFLICT")
        self.assertEqual(self.snapshot(), before)

    def test_deleted_project_rejects_public_helper_and_director_writes(self):
        projects.add_script(self.pid, self.content("history"))
        projects.delete_project(self.pid)
        before = self.snapshot()
        self.expect_error("PROJECT_DELETED", lambda: projects.add_script(self.pid, self.content()))
        with self.assertRaises(AppError) as caught:
            with db.transaction() as connection:
                projects._add_script_in_tx(connection, self.pid, self.content())
        self.assertEqual(caught.exception.code, "PROJECT_DELETED")
        self.expect_error("PROJECT_DELETED", self.propose)
        provider = self.script_provider()
        self.expect_error("PROJECT_DELETED", lambda: director_chat.chat(self.pid,
            {"message": "must not run", "intent": "script", "client_request_id": "deleted"}, provider=provider))
        self.assertEqual(provider.calls, [])
        self.assertEqual(self.snapshot(), before)

    def test_cross_project_script_binding_does_not_change_either_project(self):
        projects.add_script(self.pid, self.content("project one"))
        other = projects.create_project("other fixture", {"subject": "other product"})["project_id"]
        foreign = projects.add_script(other, self.content("project two"))
        before, foreign_before = self.snapshot(), self.snapshot(other)
        self.expect_error("CANVAS_INVALID_REFERENCE", lambda: canvas.patch_canvas(self.pid, {
            "nodes": [{"node_id": "script", "position": {"x": 777, "y": 888},
                       "binding": {"script_id": foreign["script_id"], "script_version": foreign["version"]}}]}))
        with self.assertRaises(AppError) as caught:
            with db.transaction() as connection:
                canvas.upsert_script_node_in_tx(connection, self.pid, foreign["script_id"], foreign["version"])
        self.assertEqual(caught.exception.code, "CANVAS_INVALID_REFERENCE")
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.snapshot(other), foreign_before)

    def test_script_node_helper_rejects_a_mismatched_version_without_changes(self):
        script = projects.add_script(self.pid, self.content())
        before = self.snapshot()
        with self.assertRaises(AppError) as caught:
            with db.transaction() as connection:
                canvas.upsert_script_node_in_tx(connection, self.pid, script["script_id"], script["version"] + 1)
        self.assertEqual(caught.exception.code, "CANVAS_INVALID_REFERENCE")
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
