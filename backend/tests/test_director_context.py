"""Context compaction preserves creative facts and the provider tool protocol."""
from __future__ import annotations

from copy import deepcopy
import json
import unittest
from unittest.mock import patch

import test_director_workflow as fixture
from app import canvas, config, db, director_chat as legacy, director_context as compact, director_workflow as workflow
from app.assistant.provider import FixedReplyProvider


class ContextProjectionTests(unittest.TestCase):
    def test_media_state_distinguishes_current_output_old_revision_and_failure(self):
        success = {"config_revision": 2, "status": "succeeded", "active": False,
                   "asset_id": "result", "asset_available": True}
        cases = [(success, "completed"),
            ({**success, "config_revision": 1}, "settings_changed"),
            ({**success, "asset_available": False}, "output_unavailable"),
            ({**success, "status": "running", "active": True}, "running"),
            ({**success, "status": "failed", "last_successful_execution": success}, "failed"),
            ({**success, "status": "canceled"}, "canceled"),
            ({**success, "status": "interrupted"}, "interrupted"),
            ({"status": "succeeded", "asset_available": True}, "unknown"),
            (None, "not_submitted")]
        for execution, expected in cases:
            with self.subTest(expected=expected):
                source = {"nodes": [{"node_id": "image-1", "type": "image", "config_revision": 2,
                    "config": {"prompt": "current settings"}, "last_execution": execution}]}
                before = deepcopy(source)
                projected = compact.creation_for_model(source)["nodes"][0]
                self.assertEqual(projected["execution_state"], expected)
                self.assertEqual(source, before)
                self.assertNotIn("requires_user_action", projected)

    def setUp(self):
        self.node = {"node_id": "script", "type": "script", "config_revision": 2,
            "position": {"x": 20, "y": 80}, "binding": {"script_id": "s1", "script_version": 2},
            "config": {"text": "只拍一个水杯，不加杯碟", "custom": "keep me"},
            "script_content": {"summary": "只拍一个水杯，不加杯碟", "shots": [{"hint": "正面"}]}}
        self.creation = {"project_id": "p1", "current_role": "director", "selected_node_ids": ["script"],
            "nodes": [self.node], "total_nodes": 1, "nodes_truncated": False}
        self.project = {"brief": {"forbidden_elements": ["杯碟"]}, "script": {"script_id": "s1",
            "version": 2, "confirmed": True, "content": deepcopy(self.node["script_content"])}}

    def test_current_script_and_facts_are_kept_once_without_mutating_sources(self):
        before = deepcopy((self.creation, self.project))
        result = compact.snapshot(self.creation, self.project)
        self.assertEqual((self.creation, self.project), before)
        self.assertEqual(result["creation"]["nodes"][0]["script_content"], self.node["script_content"])
        self.assertEqual(result["creation"]["nodes"][0]["config"], {"custom": "keep me"})
        self.assertEqual(result["project"]["brief"]["forbidden_elements"], ["杯碟"])
        self.assertEqual(result["project"]["script"]["content_ref"]["node_id"], "script")

    def test_old_or_truncated_script_never_substitutes_for_current_script(self):
        for change in ("version", "body", "deleted"):
            with self.subTest(change=change):
                creation = deepcopy(self.creation)
                if change == "version": creation["nodes"][0]["binding"]["script_version"] = 1
                if change == "body": creation["nodes"][0]["script_content"]["summary"] = "截断"
                if change == "deleted": creation["nodes"] = []
                self.assertEqual(compact.snapshot(creation, self.project)["project"]["script"]["content"], self.project["script"]["content"])

    def test_only_identical_historical_revision_is_replaced_with_reference(self):
        historical = deepcopy(self.node)
        self.assertNotIn("config", compact.history_artifact(historical, [self.node]))
        for change in ("revision", "prompt", "deleted"):
            with self.subTest(change=change):
                current = deepcopy(self.node)
                if change == "revision": current["config_revision"] = 3
                if change == "prompt": current["config"]["text"] = "新的脚本"
                nodes = [] if change == "deleted" else [current]
                self.assertEqual(compact.history_artifact(historical, nodes)["config"], historical["config"])

    def test_execution_failure_and_last_successful_identity_survive(self):
        self.node["last_execution"] = {"execution_id": "bad", "active": True,
            "asset_available": False, "asset_error": {"code": "OUTPUT_ASSET_CHANGED"},
            "error": {"code": "CREATE_RESULT_UNKNOWN"}, "input": {"params": {"prompt": "旧提示词"}},
            "last_successful_execution": {"execution_id": "good", "asset_id": "a1"}}
        projected = compact.creation_for_model(self.creation)["nodes"][0]["last_execution"]
        self.assertEqual(projected, self.node["last_execution"])

    def test_unchanged_read_references_initial_but_changed_selection_gets_full_context(self):
        initial = compact.snapshot(self.creation, self.project)
        result = compact.context_tool_reply({"creation": self.creation, "project": self.project}, initial)
        self.assertTrue(result["unchanged"])
        self.assertEqual(result["creation"]["project_id"], "p1")
        self.creation["selected_node_ids"] = []
        changed = compact.context_tool_reply({"creation": self.creation, "project": self.project}, initial)
        self.assertEqual(changed["creation"]["selected_node_ids"], [])
        self.assertEqual(len(changed["creation"]["nodes"]), 1)


class WorkflowContextTests(unittest.TestCase):
    setUp = fixture.DirectorWorkflowTests.setUp
    call = staticmethod(fixture.DirectorWorkflowTests.call)
    conversation = fixture.DirectorWorkflowTests.conversation
    no_tasks = fixture.DirectorWorkflowTests.no_tasks

    def test_latest_state_follows_old_pending_confirmation_reply_and_new_idea_is_not_saved(self):
        canvas.patch_canvas(self.pid, {"nodes": [{"node_id": "image-1", "type": "image",
            "config": {"model_id": "doubao-seedream-5-0-flash-260915", "prompt": "saved neutral grey",
                       "params": {"size": "2688x1536"}}}]})
        db.insert("director_turns", {"id": "old-status", "project_id": self.pid,
            "user_text": "刚才重做第二镜", "reply_text": "等待你确认后才会生成。",
            "structured_json": db.dumps({}), "created_at": 1})
        real_context = workflow.creation.creation_context(self.pid)
        real_context["nodes"][0]["last_execution"] = {"status": "succeeded", "active": False,
            "config_revision": real_context["nodes"][0]["config_revision"], "asset_available": True,
            "asset_id": "completed-output", "execution_id": "completed-run"}
        before = canvas.get_canvas(self.pid)
        provider = FixedReplyProvider([{"tool_calls": [self.call("respond_to_user", {
            "kind": "discuss", "message": "已保存的灰背景版本已经完成；新提出的浅蓝灰还没有保存或生成。",
            "missing_fields": []}, "reply-status")]}])
        with patch.object(workflow.creation, "creation_context", return_value=real_context):
            result = self.conversation(provider, message="只讨论：新背景改成浅蓝灰，别执行。之前的重做完成了吗？")
        sent = provider.calls[0]["messages"]
        context_index = next(i for i, item in enumerate(sent) if item["role"] == "context")
        old_index = next(i for i, item in enumerate(sent) if item.get("content") == "等待你确认后才会生成。")
        self.assertGreater(context_index, old_index)
        node = sent[context_index]["content"]["creation"]["nodes"][0]
        self.assertEqual(node["execution_state"], "completed")
        self.assertEqual(node["config"]["prompt"], "saved neutral grey")
        self.assertEqual(sent[-1]["role"], "user")
        self.assertEqual(result["outcome"]["status"], "message")
        self.assertEqual(result["actions"], [])
        self.assertEqual(canvas.get_canvas(self.pid), before)
        self.no_tasks()

    def test_history_preserves_user_and_reply_and_retains_more_recent_turns(self):
        cfg = {"text": "商品要求" * 1000}
        canvas.patch_canvas(self.pid, {"nodes": [{"node_id": "note", "type": "text", "config": cfg}]})
        node = workflow.creation.creation_context(self.pid)["nodes"][0]
        for index in range(8):
            db.insert("director_turns", {"id": f"turn-{index}", "project_id": self.pid,
                "user_text": f"第{index}次要求：保留水杯外形", "reply_text": f"第{index}次回复：正在澄清",
                "structured_json": db.dumps({"artifacts": [node], "outcome": {"status": "partial", "error": {"code": "TEST_FAILURE"}}}),
                "created_at": index})
        before = db.query("SELECT * FROM director_turns ORDER BY id")
        raw = legacy._history(self.pid)
        projected = legacy._history(self.pid, compact_artifacts=True, current_nodes=[node])
        self.assertGreater(len(projected), len(raw))
        self.assertEqual(len(projected), 16)
        for i in range(8):
            self.assertEqual(projected[i*2]["content"], f"第{i}次要求：保留水杯外形")
            self.assertIn(f"第{i}次回复：正在澄清", projected[i*2+1]["content"])
            self.assertIn("TEST_FAILURE", projected[i*2+1]["content"])
        self.assertEqual(db.query("SELECT * FROM director_turns ORDER BY id"), before)

    def test_context_refresh_continuation_is_smaller_and_protocol_complete(self):
        canvas.patch_canvas(self.pid, {"nodes": [{"node_id": "note", "type": "text", "config": {"text": "杯" * 4000}}]})
        provider = FixedReplyProvider([
            {"tool_calls": [self.call("get_creation_context", {}, "exact-id")]},
            {"tool_calls": [self.call("respond_to_user", {"kind": "discuss", "message": "已读取，继续讨论。", "missing_fields": []}, "reply-id")]}])
        result = self.conversation(provider)
        messages = provider.calls[1]["messages"]
        call = next(m for m in messages if m.get("tool_calls"))
        response = next(m for m in messages if m["role"] == "tool")
        self.assertEqual(call["tool_calls"][0]["id"], response["tool_call_id"])
        self.assertTrue(json.loads(response["content"])["unchanged"])
        self.assertLess(len(response["content"]), 500)
        self.assertEqual(result["outcome"]["status"], "message")
        self.no_tasks()

    def test_oversized_current_user_message_still_blocks_before_provider(self):
        provider = FixedReplyProvider([])
        with patch.object(config, "DIRECTOR_MAX_COST_PER_CALL_CNY", .001):
            result = self.conversation(provider, message="必须保留商品外形。" * 1000)
        self.assertEqual(result["outcome"]["error"]["code"], "ASSISTANT_BUDGET_EXCEEDED")
        self.assertEqual(provider.calls, [])
        self.no_tasks()
