"""Recover only ended-owner conversations, using private DB/assets and no network.

The workflow fixture blocks real model transports, sockets and generation
workers. Recovery mutates only the test database and never runs a server.
"""
from __future__ import annotations

import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

import test_director_workflow as fixture
from app import director_recovery as service

db = fixture.db
workflow = fixture.service
projects = fixture.projects


class DirectorRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture.DirectorWorkflowTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.pid = self.fixture.pid

    def pending_before_tool(self, request="interrupted", message="请直接写出这个水杯的脚本"):
        provider = self.fixture.provider("write_script", self.fixture.script_args())
        with patch.object(provider, "chat", side_effect=SystemExit("fixture owner exit")):
            with self.assertRaises(SystemExit):
                self.fixture.conversation(provider, request, message)
        row = self.request(request)
        self.assertEqual(row["status"], "pending")
        return row, provider

    def pending_after_script(self, request="saved-before-exit"):
        with patch.object(workflow, "_action_view", side_effect=SystemExit("fixture owner exit after commit")):
            with self.assertRaises(SystemExit):
                self.fixture.conversation(self.fixture.provider("write_script", self.fixture.script_args()), request)
        return self.request(request)

    def request(self, request):
        return db.query("SELECT * FROM director_requests WHERE project_id=? AND client_request_id=?",
                        (self.pid, request))[0]

    def dead_recover(self):
        # Signal zero is never actually sent; this is a deterministic dead owner.
        with patch.object(service.os, "kill", side_effect=ProcessLookupError), \
                patch.object(service, "_process_start", side_effect=AssertionError("Dead owner needs no ps")):
            return service.recover_interrupted_requests()

    def unchanged_tables(self):
        return {table: db.query(f"SELECT * FROM {table} ORDER BY id") for table in (
            "projects", "script_versions", "canvas_nodes", "director_actions", "director_tool_operations",
            "confirmations", "tasks", "canvas_node_executions", "director_compositions")}

    def sleeping_child(self):
        # This isolated child imports only time, never the app, and has no I/O.
        child = subprocess.Popen([sys.executable, "-I", "-S", "-c", "import time; time.sleep(30)"],
                                 cwd=self.fixture.root, stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        def stop_child():
            if child.poll() is None:
                child.terminate()
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=2)

        self.addCleanup(stop_child)
        return child, stop_child

    def test_dead_owner_without_tools_becomes_failed_replays_without_llm_and_can_delete(self):
        row, provider = self.pending_before_tool()
        context = db.loads(row["request_json"])
        self.assertEqual(context["message"], "请直接写出这个水杯的脚本")
        self.assertTrue(context["session_id"])
        self.assertIsInstance(context["owner_pid"], int)
        before = self.unchanged_tables()
        counts = self.dead_recover()
        self.assertEqual(counts, {"interrupted": 1, "active": 0, "unknown_owner": 0})
        self.assertEqual(self.unchanged_tables(), before)
        receipt = workflow.request_receipt(self.pid, "interrupted")
        self.assertEqual(receipt["status"], "failed")
        self.assertFalse(receipt["may_still_be_running"])
        self.assertEqual(receipt["response"]["outcome"]["status"], "failed")
        self.assertEqual(receipt["response"]["outcome"]["error"]["code"], "DIRECTOR_REQUEST_INTERRUPTED")
        replay = self.fixture.conversation(provider, "interrupted")
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(provider.calls, [])
        turns = db.query("SELECT * FROM director_turns WHERE project_id=?", (self.pid,))
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["user_text"], context["message"])
        self.assertEqual(turns[0]["session_id"], context["session_id"])
        self.assertTrue(projects.delete_project(self.pid)["deleted"])
        self.fixture.no_tasks()

    def test_dead_owner_saved_script_retains_action_and_confirms_without_new_version(self):
        self.pending_after_script()
        before = self.unchanged_tables()
        counts = self.dead_recover()
        self.assertEqual(counts["interrupted"], 1)
        self.assertEqual(self.unchanged_tables(), before)
        receipt = workflow.request_receipt(self.pid, "saved-before-exit")
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["response"]["outcome"]["status"], "partial")
        self.assertEqual(receipt["response"]["outcome"]["node_ids"], ["script"])
        self.assertEqual(len(receipt["artifacts"]), 1)
        action = self.fixture.action(receipt, "confirm_script")
        self.fixture.confirm(action, "confirm-recovered-script")
        self.assertEqual(len(projects.list_scripts(self.pid)), 1)
        self.assertEqual(len(db.query("SELECT * FROM confirmations WHERE status='active'")), 1)
        self.assertEqual(db.get("projects", self.pid)["status"], "script_confirmed")
        self.fixture.no_tasks()

    def test_live_owner_is_not_recovered_and_gets_stay_read_only(self):
        row, _ = self.pending_before_tool("live")
        context = db.loads(row["request_json"])
        context["owner_start"] = "fixture live process start"
        db.update("director_requests", row["id"], {"request_json": db.dumps(context)})
        before = self.fixture.snapshot()
        with patch.object(service.os, "kill", return_value=None), \
                patch.object(service, "_process_start", return_value=context["owner_start"]):
            self.assertEqual(service.recover_interrupted_requests(), {"interrupted": 0, "active": 1, "unknown_owner": 0})
        workflow.request_receipt(self.pid, "live")
        workflow.state(self.pid)
        self.assertEqual(self.fixture.snapshot(), before)
        self.fixture.error("PROJECT_BUSY", lambda: projects.delete_project(self.pid))
        self.assertEqual(db.query("SELECT * FROM director_turns"), [])
        self.fixture.no_tasks()

    def test_unknown_owner_and_legacy_pending_are_preserved_without_fabricated_messages(self):
        row, _ = self.pending_before_tool("unknown")
        db.update("director_requests", row["id"], {"request_json": None})
        db.insert("director_requests", {"id": db.new_id("dreq"), "project_id": self.pid,
            "client_request_id": "legacy-pending", "payload_hash": "legacy-hash", "status": "pending",
            "created_at": db.now(), "updated_at": db.now()})
        before = self.fixture.snapshot()
        with patch.object(service.os, "kill", side_effect=AssertionError("Unknown owner must not be probed")):
            counts = service.recover_interrupted_requests()
        self.assertEqual(counts, {"interrupted": 0, "active": 0, "unknown_owner": 1})
        self.assertEqual(self.fixture.snapshot(), before)
        self.assertEqual(db.query("SELECT * FROM director_turns"), [])
        self.fixture.no_tasks()

    def test_permission_denied_owner_remains_unknown(self):
        self.pending_before_tool("permission-unknown")
        before = self.fixture.snapshot()
        with patch.object(service.os, "kill", side_effect=PermissionError):
            self.assertEqual(service.recover_interrupted_requests()["unknown_owner"], 1)
        self.assertEqual(self.fixture.snapshot(), before)
        self.fixture.no_tasks()

    def test_reused_pid_start_stamp_proves_original_owner_has_ended(self):
        row, _ = self.pending_before_tool("pid-reused")
        context = db.loads(row["request_json"])
        context["owner_start"] = "utc-lstart-v1:Mon Oct 26 10:00:00 2026"
        db.update("director_requests", row["id"], {"request_json": db.dumps(context)})
        with patch.object(service.os, "kill", return_value=None), \
                patch.object(service, "_process_start", return_value="utc-lstart-v1:Tue Oct 27 11:00:00 2026"):
            self.assertEqual(service.recover_interrupted_requests()["interrupted"], 1)
        self.assertEqual(self.request("pid-reused")["status"], "failed")
        self.fixture.no_tasks()

    def test_real_child_liveness_then_exit_recovers_once_without_model_or_tasks(self):
        child, stop_child = self.sleeping_child()
        row, provider = self.pending_before_tool("real-process")
        context = db.loads(row["request_json"])
        context.update(owner_pid=child.pid, owner_start=service._process_start(child.pid))
        self.assertIsNotNone(context["owner_start"], "A real start stamp is required for this regression")
        db.update("director_requests", row["id"], {"request_json": db.dumps(context)})
        before = self.fixture.snapshot()

        # Exercise real os.kill(pid, 0) and ps, without ownership mocks.
        self.assertIsNone(child.poll())
        self.assertEqual(service.recover_interrupted_requests(),
                         {"interrupted": 0, "active": 1, "unknown_owner": 0})
        self.assertEqual(self.fixture.snapshot(), before)
        stop_child()
        self.assertIsNotNone(child.returncode)
        self.assertEqual(service.recover_interrupted_requests(),
                         {"interrupted": 1, "active": 0, "unknown_owner": 0})
        recovered = self.fixture.snapshot()
        self.assertEqual(service.recover_interrupted_requests()["interrupted"], 0)
        self.assertEqual(self.fixture.snapshot(), recovered)
        receipt = workflow.request_receipt(self.pid, "real-process")
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["response"]["outcome"]["error"]["code"], "DIRECTOR_REQUEST_INTERRUPTED")
        replay = self.fixture.conversation(provider, "real-process", context["message"])
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(provider.calls, [])
        turns = db.query("SELECT * FROM director_turns WHERE project_id=?", (self.pid,))
        self.assertEqual(len(turns), 1)
        self.assertEqual(turns[0]["user_text"], context["message"])
        self.fixture.no_tasks()

    def test_real_child_remains_active_across_timezone_and_locale_environments(self):
        child, _ = self.sleeping_child()
        row, provider = self.pending_before_tool("real-process-timezones")
        with patch.dict(service.os.environ, {"TZ": "UTC", "LC_ALL": "C"}):
            stamp = service._process_start(child.pid)
        self.assertIsNotNone(stamp, "This regression requires a real process start stamp")
        self.assertTrue(stamp.startswith("utc-lstart-v1:"))
        context = db.loads(row["request_json"])
        context.update(owner_pid=child.pid, owner_start=stamp)
        db.update("director_requests", row["id"], {"request_json": db.dumps(context)})
        before = self.fixture.snapshot()
        for zone, locale in (("UTC", "C"), ("Asia/Shanghai", "zh_CN.UTF-8"),
                             ("America/New_York", "fr_FR.UTF-8")):
            with self.subTest(zone=zone, locale=locale), \
                    patch.dict(service.os.environ, {"TZ": zone, "LC_ALL": locale}):
                # Only the caller environment changes; both probes are real.
                self.assertIsNone(child.poll())
                self.assertEqual(service._process_start(child.pid), stamp)
                self.assertEqual(service.recover_interrupted_requests(),
                                 {"interrupted": 0, "active": 1, "unknown_owner": 0})
                self.assertEqual(self.fixture.snapshot(), before)
        self.assertEqual(provider.calls, [])
        self.assertEqual(db.query("SELECT * FROM director_turns"), [])
        self.fixture.no_tasks()

    def test_legacy_local_time_stamp_does_not_take_over_a_live_owner(self):
        row, _ = self.pending_before_tool("legacy-local-stamp")
        context = db.loads(row["request_json"])
        context["owner_start"] = "Tue Oct 27 23:25:55 2026"
        db.update("director_requests", row["id"], {"request_json": db.dumps(context)})
        before = self.fixture.snapshot()
        with patch.object(service.os, "kill", return_value=None), \
                patch.object(service, "_process_start", return_value="utc-lstart-v1:Tue Oct 27 15:25:55 2026"):
            self.assertEqual(service.recover_interrupted_requests(),
                             {"interrupted": 0, "active": 1, "unknown_owner": 0})
        self.assertEqual(self.fixture.snapshot(), before)
        self.assertEqual(db.query("SELECT * FROM director_turns"), [])
        self.fixture.no_tasks()

    def test_unavailable_start_stamp_does_not_claim_pid_reuse(self):
        row, _ = self.pending_before_tool("start-unknown")
        context = db.loads(row["request_json"])
        context["owner_start"] = "known old start"
        db.update("director_requests", row["id"], {"request_json": db.dumps(context)})
        before = self.fixture.snapshot()
        with patch.object(service.os, "kill", return_value=None), \
                patch.object(service, "_process_start", return_value=None):
            counts = service.recover_interrupted_requests()
        self.assertEqual(counts["interrupted"], 0)
        self.assertEqual(self.fixture.snapshot(), before)

    def test_dead_owner_without_original_user_text_does_not_invent_a_turn(self):
        row, _ = self.pending_before_tool("no-original-text")
        context = db.loads(row["request_json"])
        context.pop("message")
        db.update("director_requests", row["id"], {"request_json": db.dumps(context)})
        self.assertEqual(self.dead_recover()["interrupted"], 1)
        self.assertEqual(self.request("no-original-text")["status"], "failed")
        self.assertIsNone(self.request("no-original-text")["turn_id"])
        self.assertEqual(db.query("SELECT * FROM director_turns"), [])
        self.fixture.no_tasks()

    def test_recovery_is_idempotent_without_duplicate_transcript_or_media(self):
        self.pending_after_script()
        self.assertEqual(self.dead_recover()["interrupted"], 1)
        before = self.fixture.snapshot()
        self.assertEqual(self.dead_recover()["interrupted"], 0)
        self.assertEqual(self.fixture.snapshot(), before)
        self.assertEqual(len(db.query("SELECT * FROM director_turns")), 1)
        self.assertEqual(len(projects.list_scripts(self.pid)), 1)
        self.fixture.no_tasks()

    def test_submitted_generation_tasks_survive_recovery_and_still_block_project_delete(self):
        out = self.fixture.make_boards()
        action = self.fixture.action(out, "generation")
        submitted = self.fixture.confirm(action, "submit-before-recovery")
        row = self.request("boards")
        db.update("director_requests", row["id"], {"status": "pending", "response_json": None, "turn_id": None})
        with db.transaction() as connection:
            connection.execute("DELETE FROM director_turns WHERE id=?", (row["turn_id"],))
        before = self.unchanged_tables()
        self.assertEqual(self.dead_recover()["interrupted"], 1)
        self.assertEqual(self.unchanged_tables(), before)
        recovered = workflow.request_receipt(self.pid, "boards")
        self.assertEqual(recovered["status"], "failed")
        actual = self.fixture.action(recovered, "generation")
        self.assertEqual(actual["action_status"], "submitted")
        self.assertEqual(actual["tasks"], submitted["tasks"])
        self.assertEqual(len(db.query("SELECT * FROM tasks")), 2)
        self.fixture.error("PROJECT_BUSY", lambda: projects.delete_project(self.pid))
        replay = self.fixture.confirm(action, "submit-before-recovery")
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(len(db.query("SELECT * FROM tasks")), 2)

    def test_final_recovery_update_failure_rolls_back_its_new_transcript(self):
        self.pending_after_script()
        before = self.fixture.snapshot()
        with db.conn() as connection:
            connection.execute("CREATE TRIGGER reject_recovery_update BEFORE UPDATE OF status ON director_requests "
                               "WHEN NEW.error_code='DIRECTOR_REQUEST_INTERRUPTED' "
                               "BEGIN SELECT RAISE(ABORT,'recovery update fixture'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "recovery update fixture"):
            self.dead_recover()
        self.assertEqual(self.fixture.snapshot(), before)
        self.assertEqual(db.query("SELECT * FROM director_turns"), [])
        self.assertEqual(len(projects.list_scripts(self.pid)), 1)
        self.assertEqual(len(db.query("SELECT * FROM director_actions")), 1)
        self.fixture.no_tasks()

    def test_owner_context_changed_during_probe_is_not_overwritten(self):
        row, _ = self.pending_before_tool("owner-change")
        context = db.loads(row["request_json"])

        def replace_owner(_context):
            db.update("director_requests", row["id"], {"request_json": db.dumps({**context, "owner_pid": 987654321})})
            return "ended"

        with patch.object(service, "_owner_state", side_effect=replace_owner):
            self.assertEqual(service.recover_interrupted_requests()["interrupted"], 0)
        self.assertEqual(self.request("owner-change")["status"], "pending")
        self.assertEqual(db.query("SELECT * FROM director_turns"), [])
        self.fixture.no_tasks()
