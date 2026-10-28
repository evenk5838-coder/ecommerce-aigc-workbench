"""Conversation workflow contract on private SQLite and fixed replies only.

These are service integration tests: artifacts, quotes, confirmations and task
receipts are real database writes; no worker, external network or model runs.
"""
from __future__ import annotations

import io
import json
import sqlite3
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_read_text = Path.read_text
with patch.object(Path, "read_text", lambda path, *a, **kw:
                  "" if path.name == ".env" else _read_text(path, *a, **kw)), \
        patch.object(Path, "mkdir", lambda *a, **kw: None):
    from app import config

from app import canvas, db, director_chat, director_workflow as service, projects, storage
from app.assistant.provider import FixedReplyProvider
from app.errors import AppError

IMAGE = "doubao-seedream-5-0-flash-260915"
VIDEO = "doubao-seedance-1-0-pro-fast-251015"


class DirectorWorkflowTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="director-workflow-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.db_path = self.root / "workflow.db"
        for key, value in (("DATA", self.root), ("DB_PATH", self.db_path),
                           ("ASSETS", self.root / "assets"), ("MODELS", self.root / "models"),
                           ("BUDGET_CNY", 20), ("DIRECTOR_MAX_COST_PER_CALL_CNY", 0),
                           ("DIRECTOR_MAX_SESSION_COST_CNY", 0)):
            self.enterContext(patch.object(config, key, value))
        config.ASSETS.mkdir()
        config.MODELS.mkdir()
        actual_connect = sqlite3.connect

        def private_connect(database, *args, **kwargs):
            if Path(str(database)).resolve() != self.db_path.resolve():
                raise AssertionError("Only the isolated workflow database may be opened")
            return actual_connect(database, *args, **kwargs)

        self.enterContext(patch("sqlite3.connect", side_effect=private_connect))
        for target in ("socket.create_connection", "socket.socket.connect", "socket.socket.connect_ex",
                       "app.providers_ark._request", "app.assistant.provider._deepseek_post", "app.worker.start"):
            self.enterContext(patch(target, side_effect=AssertionError("Network/model/worker forbidden")))
        self.enterContext(patch.object(config, "model_lifecycle_state", return_value={"status": "active"}))
        db.init_db()
        self.pid = projects.create_project("晨光水杯 · 工作流测试", {
            "subject": "one white cup", "selling_points": ["smooth handle"], "quantity": 1,
            "target_duration_s": 10, "style": "warm morning light"})["project_id"]
        self.other = projects.create_project("other workflow fixture")["project_id"]

    @staticmethod
    def call(name, arguments, call_id="call-fixture"):
        return {"id": call_id, "name": name, "arguments": arguments}

    @classmethod
    def provider(cls, name, arguments, call_id="call-fixture"):
        return FixedReplyProvider([{"tool_calls": [cls.call(name, arguments, call_id)]}])

    @staticmethod
    def script_args(summary="A white cup turns in warm morning light", **extra):
        return {"summary": summary, "shots": [
            {"hint": "The single cup stands on a table", "duration_s": 5},
            {"hint": "Close-up of the smooth handle", "duration_s": 5}], **extra}

    def conversation(self, provider, request="request-1", message="请直接写出这个水杯的脚本", **extra):
        return service.conversation(self.pid, {"message": message, "client_request_id": request, **extra}, provider=provider)

    def error(self, code, callback):
        with self.assertRaises(AppError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def action(self, out, kind):
        matches = [a for a in out["actions"] if a["kind"] == kind]
        self.assertEqual(len(matches), 1, f"Exactly one {kind} action is expected")
        return matches[0]

    def make_script(self, request="script", confirmed=False):
        out = self.conversation(self.provider("write_script", self.script_args()), request)
        if confirmed:
            self.confirm(self.action(out, "confirm_script"), request + "-confirm")
        return out

    def confirm(self, action, request="confirm-1"):
        return service.confirm_action(self.pid, action["action_id"], {
            "client_request_id": request,
            "user_action": "confirm_script" if action["kind"] == "confirm_script" else "confirm_generate"})

    def board_args(self):
        node = next(n for n in canvas.get_canvas(self.pid)["nodes"] if n["node_id"] == "script")
        return {"source_node_id": "script", "expected_source_revision": node["config_revision"],
                "model_id": IMAGE, "params": {"ratio": "16:9"}, "shots": [
                    {"prompt": "One white cup in warm morning light", "duration_s": 5},
                    {"prompt": "Close-up of one smooth white handle", "duration_s": 5}]}

    def make_boards(self):
        self.make_script(confirmed=True)
        return self.conversation(self.provider("prepare_storyboards", self.board_args()), "boards", "按这个脚本准备两张分镜")

    def no_tasks(self):
        for table in ("tasks", "canvas_node_executions", "director_compositions"):
            self.assertEqual(db.query(f"SELECT * FROM {table}"), [], table)

    def snapshot(self):
        return {table: db.query(f"SELECT * FROM {table} ORDER BY id") for table in (
            "script_versions", "canvas_nodes", "director_actions", "director_requests", "director_turns",
            "director_tool_operations", "confirmations", "confirmation_events", "tasks")}

    def test_auto_conversation_saves_script_and_canonical_node_without_extra_model_call(self):
        provider = self.provider("write_script", self.script_args())
        provider.push({"reply": "This final-model call must never be needed"})
        out = self.conversation(provider)
        self.assertEqual(out["outcome"]["status"], "saved")
        self.assertEqual(out["outcome"]["node_ids"], ["script"])
        self.assertEqual(len(provider.calls), 1)
        scripts = projects.list_scripts(self.pid)
        self.assertEqual(len(scripts), 1)
        node = next(n for n in canvas.get_canvas(self.pid)["nodes"] if n["node_id"] == "script")
        self.assertEqual(node["binding"]["script_id"], scripts[0]["script_id"])
        self.assertEqual(projects.get_script(scripts[0]["script_id"])["content"]["summary"], self.script_args()["summary"])
        self.assertIsNone(projects.get_script(scripts[0]["script_id"])["confirmed_at"])
        self.action(out, "confirm_script")
        self.assertEqual(out["usage"]["cost_cny"], 0)
        self.no_tasks()

    def test_plain_promise_gets_one_repair_round_and_must_actually_write(self):
        provider = FixedReplyProvider([{"reply": "稍等，我这就把脚本草案落库。"},
            {"tool_calls": [self.call("write_script", self.script_args(), "after-repair")]}])
        out = self.conversation(provider)
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(out["outcome"]["status"], "saved")
        self.assertEqual(out["outcome"]["node_ids"], ["script"])
        self.assertEqual(len(projects.list_scripts(self.pid)), 1)
        self.no_tasks()

    def test_repeated_unacted_promise_is_failed_and_never_fake_saved(self):
        provider = FixedReplyProvider([{"reply": "稍等，我会生成。"}, {"reply": "已经为你写好了。"}])
        out = self.conversation(provider)
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(out["outcome"]["status"], "failed")
        self.assertEqual(out["artifacts"], [])
        self.assertEqual(out["actions"], [])
        self.assertEqual(projects.list_scripts(self.pid), [])
        self.assertNotIn(out["reply"], ("稍等，我会生成。", "已经为你写好了。"))
        self.no_tasks()

    def test_explicit_clarify_tool_returns_question_without_artifacts(self):
        provider = self.provider("respond_to_user", {"kind": "clarify", "message": "想做横屏还是竖屏？",
                                                        "missing_fields": ["ratio"]})
        out = self.conversation(provider)
        self.assertEqual(out["outcome"]["status"], "clarify")
        self.assertIn("横屏", out["reply"])
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(out["artifacts"], [])
        self.assertEqual(projects.list_scripts(self.pid), [])
        self.no_tasks()

    def test_explicit_discussion_can_end_without_creating_artifacts(self):
        out = self.conversation(self.provider("respond_to_user", {
            "kind": "discuss", "message": "可以采用暖色晨光，突出杯身质感。", "missing_fields": []}))
        self.assertEqual(out["outcome"]["status"], "message")
        self.assertEqual(out["artifacts"], [])
        self.no_tasks()

    def test_request_shape_and_stale_context_rejected_before_llm(self):
        provider = self.provider("write_script", self.script_args())
        for extra in ({"intent": "script"}, {"expected_workflow_revision": True}, {"selected_node_ids": "script"}):
            with self.subTest(extra=extra):
                with self.assertRaises(AppError):
                    self.conversation(provider, **extra)
        before = service.state(self.pid)["workflow_revision"]
        projects.update_project(self.pid, brief={"subject": "a changed product"})
        self.error("WORKFLOW_REVISION_CONFLICT", lambda: self.conversation(provider, expected_workflow_revision=before))
        self.assertEqual(provider.calls, [])
        self.assertEqual(db.query("SELECT * FROM director_requests"), [])
        self.no_tasks()

    def test_foreign_selection_and_session_rejected_before_llm(self):
        canvas.patch_canvas(self.other, {"nodes": [{"node_id": "foreign-note", "type": "text", "config": {"text": "private"}}]})
        foreign = service.conversation(self.other, {"message": "hello", "client_request_id": "other"},
            provider=self.provider("respond_to_user", {"kind": "discuss", "message": "hello", "missing_fields": []}))
        provider = self.provider("write_script", self.script_args())
        self.error("CANVAS_NODE_NOT_FOUND", lambda: self.conversation(provider, selected_node_ids=["foreign-note"]))
        self.error("PROJECT_SESSION_MISMATCH", lambda: self.conversation(provider, session_id=foreign["session_id"]))
        self.assertEqual(provider.calls, [])
        self.assertEqual(db.query("SELECT * FROM director_requests WHERE project_id=?", (self.pid,)), [])

    def test_known_brief_and_selected_node_are_present_in_provider_context(self):
        canvas.patch_canvas(self.pid, {"nodes": [{"node_id": "note", "type": "text", "config": {"text": "selected gold rim"}}]})
        provider = self.provider("respond_to_user", {"kind": "discuss", "message": "保留金色杯口。", "missing_fields": []})
        self.conversation(provider, selected_node_ids=["note"])
        content = json.dumps(provider.calls[0]["messages"], ensure_ascii=False)
        self.assertIn("selected gold rim", content)
        self.assertIn("one white cup", content)
        self.assertIn("smooth handle", content)

    def test_duplicate_request_replays_without_model_or_new_script(self):
        provider = self.provider("write_script", self.script_args())
        first = self.conversation(provider)
        before = self.snapshot()
        replay = self.conversation(provider)
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["outcome"], first["outcome"])
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(self.snapshot(), before)
        self.error("IDEMPOTENCY_CONFLICT", lambda: self.conversation(provider, message="different request"))
        self.assertEqual(len(provider.calls), 1)

    def test_script_confirmation_is_atomic_replayable_and_bound_to_frozen_version(self):
        out = self.make_script()
        action = self.action(out, "confirm_script")
        stored = db.get("director_actions", action["action_id"])
        self.assertEqual(stored["status"], "proposed")
        self.assertEqual(db.query("SELECT * FROM confirmations"), [])
        confirmed = self.confirm(action)
        self.assertEqual(db.get("projects", self.pid)["status"], "script_confirmed")
        self.assertIsNotNone(projects.get_script(projects.list_scripts(self.pid)[0]["script_id"])["confirmed_at"])
        self.assertEqual(len(db.query("SELECT * FROM confirmations WHERE status='active'")), 1)
        self.assertEqual(db.get("director_actions", action["action_id"])["status"], "submitted")
        snapshot = self.snapshot()
        replay = self.confirm(action)
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["action_id"], confirmed["action_id"])
        self.assertEqual(self.snapshot(), snapshot)
        self.error("DIRECTOR_ACTION_CONFLICT", lambda: self.confirm(action, "another-submit"))
        self.no_tasks()

    def test_script_confirmation_action_receipt_failure_rolls_back_all_confirmation_writes(self):
        action = self.action(self.make_script(), "confirm_script")
        before = self.snapshot()
        with db.conn() as connection:
            connection.execute("CREATE TRIGGER fail_workflow_confirm BEFORE UPDATE OF status ON director_actions "
                               "WHEN NEW.status='submitted' BEGIN SELECT RAISE(ABORT,'action fixture failure'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "action fixture failure"):
            self.confirm(action)
        self.assertEqual(self.snapshot(), before)

    def test_changed_script_or_expired_scope_cannot_be_confirmed(self):
        action = self.action(self.make_script(), "confirm_script")
        projects.add_script(self.pid, self.script_args("Revised script"), base_version=1)
        self.error("DIRECTOR_ACTION_STALE", lambda: self.confirm(action))
        self.assertEqual(db.query("SELECT * FROM confirmations"), [])
        another = self.conversation(self.provider("write_script", self.script_args("Version three", base_version=2)), "v3")
        fresh = self.action(another, "confirm_script")
        db.update("director_actions", fresh["action_id"], {"expires_at": 1})
        self.error("DIRECTOR_ACTION_EXPIRED", lambda: self.confirm(fresh))
        self.assertEqual(db.query("SELECT * FROM confirmations"), [])

    def test_action_identity_and_explicit_user_authority_cannot_be_injected(self):
        action = self.action(self.make_script(), "confirm_script")
        self.error("DIRECTOR_ACTION_NOT_FOUND", lambda: service.get_action(self.other, action["action_id"]))
        self.error("DIRECTOR_ACTION_NOT_FOUND", lambda: service.confirm_action(self.other, action["action_id"], {
            "client_request_id": "foreign", "user_action": "confirm_script"}))
        for body in ({"client_request_id": "bad", "user_action": "confirm_generate"},
                     {"client_request_id": "bad", "user_action": "yes"},
                     {"client_request_id": "bad", "user_action": "confirm_script", "script_id": "injected"}):
            with self.subTest(body=body):
                with self.assertRaises(AppError):
                    service.confirm_action(self.pid, action["action_id"], body)
        self.assertEqual(db.query("SELECT * FROM confirmations"), [])

    def test_confirmed_script_prepares_two_real_image_nodes_and_quotes_but_no_tasks(self):
        out = self.make_boards()
        self.assertEqual(out["outcome"]["status"], "saved")
        self.assertEqual(out["current_role"], "director")
        nodes = [n for n in canvas.get_canvas(self.pid)["nodes"] if n["type"] == "image"]
        self.assertEqual(len(nodes), 2)
        self.assertEqual(len(db.query("SELECT * FROM shots WHERE project_id=?", (self.pid,))), 2)
        action = self.action(out, "generation")
        self.assertEqual(set(action["node_ids"]), {n["node_id"] for n in nodes})
        self.assertEqual(len(action["quotes"]), 2)
        self.assertTrue(action["requires_user_action"])
        self.no_tasks()

    def test_user_generation_confirmation_enqueues_exactly_two_tasks_and_replays(self):
        action = self.action(self.make_boards(), "generation")
        self.no_tasks()
        submitted = self.confirm(action, "generate-1")
        self.assertEqual(submitted["action_status"], "submitted")
        self.assertEqual(len(submitted["tasks"]), 2)
        self.assertEqual(len(db.query("SELECT * FROM tasks")), 2)
        self.assertTrue(all(row["status"] == "queued" for row in db.query("SELECT * FROM tasks")))
        replay = self.confirm(action, "generate-1")
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["tasks"], submitted["tasks"])
        self.assertEqual(len(db.query("SELECT * FROM tasks")), 2)
        self.assertEqual(len(db.query("SELECT * FROM batches")), 1)

    def test_change_second_shot_only_preserves_first_and_quotes_only_updated_node(self):
        boards = self.make_boards()
        first_id, second_id = self.action(boards, "generation")["node_ids"]
        nodes = {n["node_id"]: n for n in canvas.get_canvas(self.pid)["nodes"]}
        first = db.query("SELECT * FROM canvas_nodes WHERE project_id=? AND node_id=?", (self.pid, first_id))[0]
        out = self.conversation(self.provider("update_node", {"node_id": second_id,
            "expected_config_revision": nodes[second_id]["config_revision"], "config": {"prompt": "Only the second shot: amber backlight"}}),
            "change-second", "只把第二镜改成琥珀色逆光", selected_node_ids=[second_id])
        self.assertEqual(db.get("canvas_nodes", first["id"]), first)
        changed = next(n for n in canvas.get_canvas(self.pid)["nodes"] if n["node_id"] == second_id)
        self.assertEqual(changed["config"]["prompt"], "Only the second shot: amber backlight")
        self.assertEqual(changed["config_revision"], nodes[second_id]["config_revision"] + 1)
        self.assertEqual(self.action(out, "generation")["node_ids"], [second_id])
        self.error("DIRECTOR_ACTION_STALE", lambda: self.confirm(self.action(boards, "generation")))
        self.no_tasks()

    def test_imported_image_can_enter_cinematographer_without_script(self):
        stream = io.BytesIO()
        Image.new("RGB", (32, 24), "gold").save(stream, "PNG")
        asset = storage.save_bytes(stream.getvalue(), "image", "fixture")
        canvas.patch_canvas(self.pid, {"nodes": [{"node_id": "imported", "type": "asset", "binding": {"asset_id": asset["id"]}}]})
        self.assertEqual(service.state(self.pid, ["imported"])["current_role"], "cinematographer")
        out = self.conversation(self.provider("prepare_video_nodes", {"model_id": VIDEO, "params": {"duration": 5},
            "sources": [{"asset_id": asset["id"], "node_id": "imported", "expected_config_revision": 1,
                         "prompt": "Slow camera orbit"}]}), "import-video", "把这张图片变成慢速环绕视频", selected_node_ids=["imported"])
        self.assertEqual(out["current_role"], "cinematographer")
        action = self.action(out, "generation")
        self.assertEqual(len(action["node_ids"]), 1)
        node = next(n for n in canvas.get_canvas(self.pid)["nodes"] if n["node_id"] == action["node_ids"][0])
        self.assertEqual(node["config"]["generation_mode"], "image_to_video")
        self.assertEqual(node["config"]["input_asset_id"], asset["id"])
        self.assertEqual(projects.list_scripts(self.pid), [])
        self.no_tasks()

    def test_model_tool_list_excludes_confirmation_and_rejects_attempt(self):
        provider = FixedReplyProvider([
            {"tool_calls": [self.call("confirm_script", {"script_id": "fake"}, "forbidden")]},
            {"tool_calls": [self.call("respond_to_user", {"kind": "clarify", "message": "请先查看脚本。", "missing_fields": []}, "clarify")]}])
        out = self.conversation(provider)
        for call in provider.calls:
            self.assertFalse({"confirm_script", "confirm_generate", "confirm_action", "confirm_generation", "confirm_composition"} & set(call["tools"]))
        self.assertNotEqual(out["outcome"]["status"], "saved")
        self.assertEqual(db.query("SELECT * FROM confirmations"), [])
        self.no_tasks()

    def test_multiple_tool_calls_have_matching_protocol_results_before_next_round(self):
        provider = FixedReplyProvider([
            {"tool_calls": [self.call("get_creation_context", {}, "read-context"),
                            self.call("list_models", {"kind": "image"}, "read-models")]},
            {"tool_calls": [self.call("write_script", self.script_args(), "fixed-script")]}])
        out = self.conversation(provider)
        self.assertEqual(out["outcome"]["status"], "saved")
        self.assertEqual(len(provider.calls), 2)
        messages = provider.calls[1]["messages"]
        assistant = next(m for m in messages if m.get("tool_calls"))
        self.assertEqual({c["id"] for c in assistant["tool_calls"]}, {"read-context", "read-models"})
        responses = [m for m in messages if m.get("role") == "tool"]
        self.assertEqual({m["tool_call_id"] for m in responses}, {"read-context", "read-models"})
        self.assertEqual(len(responses), 2)
        self.assertTrue(all(json.loads(m["content"]) for m in responses))
        self.assertEqual(len(projects.list_scripts(self.pid)), 1)
        self.no_tasks()

    def test_cross_flow_request_collision_is_explicit_and_preserves_legacy_receipt(self):
        legacy = director_chat.chat(self.pid, {"message": "讨论脚本", "client_request_id": "shared-id", "intent": "auto"},
                                    provider=FixedReplyProvider([{"reply": "你想展示哪件商品？"}]))
        original_receipt = director_chat.request_receipt(self.pid, "shared-id")
        provider = self.provider("write_script", self.script_args())
        self.error("IDEMPOTENCY_CONFLICT", lambda: self.conversation(provider, "shared-id"))
        self.assertEqual(provider.calls, [])
        self.assertEqual(director_chat.request_receipt(self.pid, "shared-id"), original_receipt)
        self.assertEqual(projects.list_scripts(self.pid), [])
        out = self.conversation(provider, "new-workflow-id")
        self.assertEqual(out["outcome"]["status"], "saved")
        self.assertEqual(service.request_receipt(self.pid, "new-workflow-id")["response"]["outcome"]["status"], "saved")
        self.assertEqual(director_chat.request_receipt(self.pid, "shared-id")["response"]["reply"], legacy["reply"])
        self.assertEqual(len(db.query("SELECT * FROM director_requests WHERE project_id=?", (self.pid,))), 2)

    def test_failed_request_is_replayed_without_more_provider_calls(self):
        provider = FixedReplyProvider([{"reply": "马上写"}, {"reply": "马上写"}])
        first = self.conversation(provider)
        self.assertEqual(first["request_status"], "failed")
        self.assertEqual(first["outcome"]["status"], "failed")
        replay = self.conversation(provider)
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["outcome"], first["outcome"])
        self.assertEqual(len(provider.calls), 2)
        receipt = service.request_receipt(self.pid, "request-1")
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["response"]["outcome"], first["outcome"])
        self.no_tasks()

    def test_brief_change_invalidates_frozen_script_confirmation(self):
        action = self.action(self.make_script(), "confirm_script")
        projects.update_project(self.pid, brief={"subject": "A red cup instead"})
        self.error("DIRECTOR_ACTION_STALE", lambda: self.confirm(action))
        self.assertEqual(db.query("SELECT * FROM confirmations"), [])
        self.assertIsNone(projects.get_script(projects.list_scripts(self.pid)[0]["script_id"])["confirmed_at"])

    def test_same_submission_id_cannot_confirm_another_action(self):
        first = self.action(self.make_script(), "confirm_script")
        self.confirm(first, "shared-confirm-id")
        out = self.conversation(self.provider("write_script", self.script_args("Revised ending", base_version=1)), "rewrite")
        second = self.action(out, "confirm_script")
        self.error("DIRECTOR_ACTION_CONFLICT", lambda: self.confirm(second, "shared-confirm-id"))
        self.assertIsNone(projects.get_script(projects.list_scripts(self.pid)[0]["script_id"])["confirmed_at"])
        self.assertEqual(db.get("director_actions", second["action_id"])["status"], "proposed")

    def test_recreated_script_node_cannot_reuse_old_frozen_confirmation(self):
        action = self.action(self.make_script(), "confirm_script")
        original = db.query("SELECT * FROM canvas_nodes WHERE project_id=? AND node_id='script'", (self.pid,))[0]
        with db.transaction() as connection:
            connection.execute("DELETE FROM canvas_nodes WHERE id=?", (original["id"],))
            copy = {**original, "id": db.new_id("different-node")}
            columns = list(copy)
            connection.execute(f"INSERT INTO canvas_nodes({','.join(columns)}) VALUES({','.join('?' for _ in columns)})",
                               [copy[c] for c in columns])
        self.error("DIRECTOR_ACTION_STALE", lambda: self.confirm(action))
        self.assertEqual(db.query("SELECT * FROM confirmations"), [])

    def test_successful_write_defers_other_tools_but_accounts_for_every_call_id(self):
        provider = FixedReplyProvider([{"tool_calls": [self.call("write_script", self.script_args(), "save-first"),
            self.call("write_script", self.script_args("Should never append a second version", base_version=1), "defer-second")]}])
        out = self.conversation(provider)
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(len(projects.list_scripts(self.pid)), 1)
        self.assertEqual(len(out["tool_calls"]), 2)
        self.assertEqual({entry["tool_call_id"] for entry in out["tool_calls"]}, {"save-first", "defer-second"})
        second = next(entry for entry in out["tool_calls"] if entry["tool_call_id"] == "defer-second")
        self.assertEqual(second["error"]["code"], "TOOL_DEFERRED")
        self.no_tasks()

    def test_pending_recovery_lists_durable_tools_without_reissuing_model(self):
        provider = self.provider("write_script", self.script_args())
        out = self.conversation(provider, "recovery")
        request = db.query("SELECT * FROM director_requests WHERE project_id=? AND status='completed'", (self.pid,))[0]
        # Model a process loss after tool commit but before the outer turn receipt.
        db.update("director_requests", request["id"], {"status": "pending", "turn_id": None, "response_json": None})
        with db.transaction() as connection:
            connection.execute("DELETE FROM director_turns WHERE project_id=?", (self.pid,))
        before = self.snapshot()
        receipt = service.request_receipt(self.pid, "recovery")
        self.assertEqual(receipt["status"], "pending")
        self.assertIsNone(receipt["response"])
        self.assertTrue(any(artifact.get("node_id") == "script" for artifact in receipt["artifacts"]))
        self.assertTrue(receipt["operations"])
        self.assertTrue(any(a["action_id"] == self.action(out, "confirm_script")["action_id"] for a in receipt["actions"]))
        self.assertEqual(self.snapshot(), before, "Receipt reads must not finish or modify a pending request")
        self.error("DIRECTOR_REQUEST_PENDING", lambda: self.conversation(provider, "recovery"))
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(len(projects.list_scripts(self.pid)), 1)

    def test_final_turn_write_failure_keeps_actual_script_and_readable_tool_receipt(self):
        provider = self.provider("write_script", self.script_args())
        with db.conn() as connection:
            connection.execute("CREATE TRIGGER fail_workflow_turn BEFORE INSERT ON director_turns "
                               "BEGIN SELECT RAISE(ABORT,'turn fixture failure'); END")
        try:
            out = self.conversation(provider, "lost-final")
        except (AppError, sqlite3.IntegrityError):
            out = None
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(len(projects.list_scripts(self.pid)), 1)
        self.assertEqual(len(db.query("SELECT * FROM canvas_nodes WHERE project_id=? AND node_id='script'", (self.pid,))), 1)
        self.assertEqual(len(db.query("SELECT * FROM director_tool_operations WHERE project_id=?", (self.pid,))), 1)
        receipt = service.request_receipt(self.pid, "lost-final")
        serialized = json.dumps(receipt)
        self.assertIn("script", serialized)
        self.assertNotEqual(receipt["status"], "completed")
        if out is not None:
            self.assertIn(out["outcome"]["status"], ("partial", "failed"))
        self.no_tasks()

    def test_a_repaired_tool_error_can_finish_saved_after_actual_write(self):
        provider = FixedReplyProvider([
            {"tool_calls": [self.call("write_script", {"summary": ""}, "invalid-script")]},
            {"tool_calls": [self.call("write_script", self.script_args(), "repaired-script")]}])
        out = self.conversation(provider)
        self.assertEqual(out["outcome"]["status"], "saved")
        self.assertEqual(len(projects.list_scripts(self.pid)), 1)
        self.assertEqual(len(provider.calls), 2)

    def test_unrepaired_second_tool_failure_remains_partial_with_saved_script(self):
        provider = FixedReplyProvider([
            {"tool_calls": [self.call("write_script", {"summary": ""}, "invalid-script"),
                            self.call("update_node", {"node_id": "absent", "expected_config_revision": 1,
                                                     "config": {"text": "x"}}, "missing-node")]},
            {"tool_calls": [self.call("write_script", self.script_args(), "repaired-script")]}])
        out = self.conversation(provider)
        self.assertEqual(out["outcome"]["status"], "partial")
        self.assertEqual(len(projects.list_scripts(self.pid)), 1)
        self.assertEqual(out["outcome"]["node_ids"], ["script"])
        self.no_tasks()

    def test_budget_rejection_is_durable_failure_before_provider_call(self):
        provider = self.provider("write_script", self.script_args())
        with patch.object(config, "DIRECTOR_MAX_COST_PER_CALL_CNY", 0.000001):
            out = self.conversation(provider, "budget")
        self.assertEqual(out["outcome"]["status"], "failed")
        self.assertEqual(out["request_status"], "failed")
        self.assertEqual(provider.calls, [])
        self.assertEqual(out["usage"]["cost_cny"], 0)
        self.assertEqual(service.request_receipt(self.pid, "budget")["status"], "failed")
        self.no_tasks()

    def test_quote_followup_failure_preserves_both_created_nodes_as_partial(self):
        self.make_script(confirmed=True)
        provider = self.provider("prepare_storyboards", self.board_args())
        with patch.object(service.director_generation, "prepare_generation", side_effect=RuntimeError("quote fixture failure")):
            out = self.conversation(provider, "quote-failed")
        self.assertEqual(out["outcome"]["status"], "partial")
        self.assertEqual(len(out["artifacts"]), 2)
        self.assertEqual(out["actions"], [])
        self.assertEqual(len([n for n in canvas.get_canvas(self.pid)["nodes"] if n["type"] == "image"]), 2)
        self.assertEqual(len(provider.calls), 1)
        receipt = service.request_receipt(self.pid, "quote-failed")
        self.assertEqual(receipt["response"]["outcome"]["status"], "partial")
        self.assertEqual(len(receipt["artifacts"]), 2)
        self.no_tasks()

    def test_lost_process_before_tool_commit_keeps_empty_pending_receipt_read_only(self):
        provider = self.provider("write_script", self.script_args())
        with patch.object(provider, "chat", side_effect=KeyboardInterrupt("fixture process loss")):
            with self.assertRaises(KeyboardInterrupt):
                self.conversation(provider, "no-tool-yet")
        before = self.snapshot()
        receipt = service.request_receipt(self.pid, "no-tool-yet")
        self.assertEqual(receipt["status"], "pending")
        self.assertEqual(receipt["artifacts"], [])
        self.assertEqual(receipt["actions"], [])
        self.assertEqual(receipt["operations"], [])
        self.assertEqual(self.snapshot(), before)
        self.error("DIRECTOR_REQUEST_PENDING", lambda: self.conversation(provider, "no-tool-yet"))
        self.assertEqual(provider.calls, [])
        self.no_tasks()

    def test_duplicate_inflight_request_does_not_start_second_model_call(self):
        provider = self.provider("write_script", self.script_args())
        entered, release = threading.Event(), threading.Event()
        actual_chat = provider.chat

        def slow_fixed_chat(*args, **kwargs):
            entered.set()
            if not release.wait(5):
                raise AssertionError("Test did not release the fixed provider")
            return actual_chat(*args, **kwargs)

        with patch.object(provider, "chat", side_effect=slow_fixed_chat), ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(self.conversation, provider, "inflight")
            try:
                self.assertTrue(entered.wait(5))
                self.error("DIRECTOR_REQUEST_PENDING", lambda: self.conversation(provider, "inflight"))
                self.assertEqual(service.request_receipt(self.pid, "inflight")["status"], "pending")
            finally:
                release.set()
            result = future.result(timeout=5)
        self.assertEqual(result["outcome"]["status"], "saved")
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(len(projects.list_scripts(self.pid)), 1)
        self.no_tasks()

    def test_action_insert_failure_rolls_back_script_node_proposal_and_tool_receipt(self):
        with db.conn() as connection:
            connection.execute("CREATE TRIGGER reject_script_action BEFORE INSERT ON director_actions "
                               "WHEN NEW.kind='confirm_script' BEGIN SELECT RAISE(ABORT,'action insert fixture'); END")
        out = self.conversation(self.provider("write_script", self.script_args()), "action-insert-failure")
        self.assertEqual(out["outcome"]["status"], "failed")
        for table in ("script_versions", "canvas_nodes", "canvas_states", "director_proposals",
                      "director_actions", "director_tool_operations", "confirmations"):
            self.assertEqual(db.query(f"SELECT * FROM {table}"), [], table)
        self.assertEqual(db.get("projects", self.pid)["status"], "draft")
        self.no_tasks()

    def test_tool_receipt_insert_failure_also_rolls_back_prepared_script_action(self):
        with db.conn() as connection:
            connection.execute("CREATE TRIGGER reject_script_receipt BEFORE INSERT ON director_tool_operations "
                               "BEGIN SELECT RAISE(ABORT,'tool receipt fixture'); END")
        out = self.conversation(self.provider("write_script", self.script_args()), "tool-receipt-failure")
        self.assertEqual(out["outcome"]["status"], "failed")
        for table in ("script_versions", "canvas_nodes", "director_proposals", "director_actions", "director_tool_operations"):
            self.assertEqual(db.query(f"SELECT * FROM {table}"), [], table)
        self.assertEqual(db.get("projects", self.pid)["status"], "draft")
        self.no_tasks()

    def test_postcommit_process_loss_keeps_script_and_confirm_action_together(self):
        # Reading the action is the first operation after the script tool's
        # commit. Losing the process here must not strand an unconfirmable script.
        with patch.object(service, "_action_view", side_effect=SystemExit("postcommit fixture loss")):
            with self.assertRaises(SystemExit):
                self.conversation(self.provider("write_script", self.script_args()), "postcommit-loss")
        before = self.snapshot()
        receipt = service.request_receipt(self.pid, "postcommit-loss")
        state = service.state(self.pid)
        self.assertEqual(receipt["status"], "pending")
        self.assertEqual(len(receipt["artifacts"]), 1)
        self.assertEqual(len(receipt["operations"]), 1)
        action = self.action(receipt, "confirm_script")
        self.assertEqual(receipt["operations"][0]["result"]["action_ids"], [action["action_id"]])
        self.assertEqual(self.action(state, "confirm_script")["action_id"], action["action_id"])
        self.assertEqual(self.snapshot(), before, "Recovery GETs must remain read-only")
        self.confirm(action, "confirm-surviving-script")
        self.assertEqual(len(projects.list_scripts(self.pid)), 1)
        self.assertEqual(len(db.query("SELECT * FROM confirmations WHERE status='active'")), 1)
        self.no_tasks()

    def test_expired_confirmation_can_be_prepared_again_without_rewriting_script(self):
        old = self.action(self.make_script(), "confirm_script")
        db.update("director_actions", old["action_id"], {"expires_at": 1})
        script = projects.list_scripts(self.pid)[0]
        original_nodes = db.query("SELECT * FROM canvas_nodes WHERE project_id=?", (self.pid,))
        before = self.snapshot()
        self.assertTrue(service.get_action(self.pid, old["action_id"])["expired"])
        service.state(self.pid)
        self.assertEqual(self.snapshot(), before, "GET must not renew an expired action")
        provider = self.provider("prepare_script_confirmation", {"script_id": script["script_id"], "expected_version": 1})
        out = self.conversation(provider, "renew-script", "重新准备这个脚本的确认入口")
        new = self.action(out, "confirm_script")
        self.assertNotEqual(new["action_id"], old["action_id"])
        self.assertFalse(new["expired"])
        self.assertEqual(out["artifacts"], [])
        self.assertEqual(projects.list_scripts(self.pid), [script])
        self.assertEqual(db.query("SELECT * FROM canvas_nodes WHERE project_id=?", (self.pid,)), original_nodes)
        self.assertEqual(db.query("SELECT * FROM confirmations"), [])
        # Retrying the same preparation must never silently extend its expiry.
        db.update("director_actions", new["action_id"], {"expires_at": 1})
        self.conversation(provider, "renew-script", "重新准备这个脚本的确认入口")
        self.assertEqual(db.get("director_actions", new["action_id"])["expires_at"], 1)
        self.assertEqual(len(provider.calls), 1)
        self.no_tasks()

    def test_existing_script_without_action_gets_confirmation_without_new_version(self):
        script = projects.add_script(self.pid, self.script_args())
        self.assertEqual(db.query("SELECT * FROM director_actions"), [])
        before_nodes = db.query("SELECT * FROM canvas_nodes WHERE project_id=?", (self.pid,))
        before = self.snapshot()
        self.assertEqual(service.state(self.pid)["actions"], [])
        self.assertEqual(self.snapshot(), before)
        out = self.conversation(self.provider("prepare_script_confirmation", {
            "script_id": script["script_id"], "expected_version": script["version"]}), "recover-existing")
        action = self.action(out, "confirm_script")
        self.assertEqual(action["script_id"], script["script_id"])
        self.assertEqual(len(projects.list_scripts(self.pid)), 1)
        self.assertEqual(db.query("SELECT * FROM canvas_nodes WHERE project_id=?", (self.pid,)), before_nodes)
        self.assertEqual(db.query("SELECT * FROM confirmations"), [])
        self.no_tasks()

    def test_reprepare_rejects_old_version_without_creating_action_or_script(self):
        old = projects.add_script(self.pid, self.script_args())
        projects.add_script(self.pid, self.script_args("new current script"), base_version=1)
        out = self.conversation(self.provider("prepare_script_confirmation", {
            "script_id": old["script_id"], "expected_version": 1}), "stale-reprepare")
        self.assertEqual(out["outcome"]["status"], "failed")
        self.assertEqual(out["tool_calls"][0]["error"]["code"], "DIRECTOR_ACTION_STALE")
        self.assertEqual(len(projects.list_scripts(self.pid)), 2)
        self.assertEqual(db.query("SELECT * FROM director_actions"), [])
        self.no_tasks()
