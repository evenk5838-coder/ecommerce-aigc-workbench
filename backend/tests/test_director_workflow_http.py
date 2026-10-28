"""Strict ASGI routes and real provider serializers with intercepted transports.

No lifespan/worker, real provider, dotenv, credentials or business database is
used. Provider tests exercise the production Ark/DeepSeek adapters; the remote
HTTP response is a fixture, not evidence of vendor/model compatibility online.
"""
from __future__ import annotations

import copy
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_read_text = Path.read_text
with patch.dict(os.environ, {}, clear=True), \
        patch.object(Path, "read_text", lambda path, *a, **kw:
                     "" if path.name == ".env" else _read_text(path, *a, **kw)), \
        patch.object(Path, "mkdir", lambda *a, **kw: None):
    from app import canvas, config, db, director_workflow, main, projects
    from app.assistant import provider as providers


SCRIPT = {"summary": "一只白杯在晨光中转动。\n最后停在杯把特写。",
          "shots": [{"hint": "杯身缓缓转动", "duration_s": 5}]}


def call(name, args, call_id):
    return {"id": call_id, "type": "function",
            "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}


def response(calls):
    return {"choices": [{"finish_reason": "tool_calls", "message": {"role": "assistant", "content": None,
            "tool_calls": calls}}], "usage": {"prompt_tokens": 0, "completion_tokens": 0}}


class DirectorWorkflowHTTPTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = self.enterContext(tempfile.TemporaryDirectory(prefix="director-http-routes-"))
        self.root = Path(temp)
        for key, value in (("DATA", self.root), ("DB_PATH", self.root / "test.db"),
                           ("ASSETS", self.root / "assets"), ("MODELS", self.root / "models"),
                           ("BUDGET_CNY", 20), ("DIRECTOR_MAX_COST_PER_CALL_CNY", 0),
                           ("DIRECTOR_MAX_SESSION_COST_CNY", 0)):
            self.enterContext(patch.object(config, key, value))
        config.ASSETS.mkdir()
        config.MODELS.mkdir()
        self.enterContext(patch.dict(os.environ, {}, clear=True))
        actual_connect = sqlite3.connect

        def private_connect(database, *args, **kwargs):
            if Path(str(database)).resolve() != config.DB_PATH.resolve():
                raise AssertionError("Only the private route database may be opened")
            return actual_connect(database, *args, **kwargs)

        self.enterContext(patch("sqlite3.connect", side_effect=private_connect))
        for target in ("socket.create_connection", "socket.socket.connect", "socket.socket.connect_ex",
                       "app.providers_ark._request", "app.assistant.provider._deepseek_post"):
            self.enterContext(patch(target, side_effect=AssertionError("External transport forbidden")))
        self.worker_start = self.enterContext(patch("app.worker.start", side_effect=AssertionError("Worker forbidden")))
        self.compose_start = self.enterContext(patch("app.director_composition.start", side_effect=AssertionError("Worker forbidden")))
        self.enterContext(patch.object(config, "model_lifecycle_state", return_value={"status": "active"}))
        self.provider = providers.FixedReplyProvider()
        self.provider_factory = self.enterContext(patch.object(providers, "get_provider", return_value=self.provider))
        db.init_db()
        self.pid = projects.create_project("ASGI private fixture", {"subject": "one white cup", "quantity": 1})["project_id"]
        self.base = f"/api/projects/{self.pid}/director"

    async def asyncSetUp(self):
        # ASGITransport intentionally does not run FastAPI lifespan handlers.
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://asgi.invalid")

    async def asyncTearDown(self):
        await self.client.aclose()
        self.worker_start.assert_not_called()
        self.compose_start.assert_not_called()

    def ok(self, result):
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    def assert_no_work(self):
        for table in ("director_requests", "script_versions", "director_actions", "tasks", "director_compositions"):
            self.assertEqual(db.query(f"SELECT * FROM {table}"), [], table)
        self.provider_factory.assert_not_called()

    async def test_conversation_rejects_unknown_fields_and_old_intent(self):
        for extra in ({"intent": "script"}, {"selected_asset_ids": []}, {"unknown": "value"}):
            with self.subTest(extra=extra):
                result = await self.client.post(self.base + "/conversation", json={
                    "message": "写脚本", "client_request_id": "strict-extra", **extra})
                self.assertEqual(result.status_code, 422, result.text)
        self.assert_no_work()

    async def test_conversation_rejects_non_strict_revisions_and_selection(self):
        for extra in ({"expected_workflow_revision": True}, {"expected_workflow_revision": "1"},
                      {"expected_workflow_revision": -1}, {"selected_node_ids": "script"},
                      {"selected_node_ids": [1]}, {"selected_node_ids": [False]},
                      {"selected_node_ids": ["x"] * 41}):
            with self.subTest(extra=extra):
                result = await self.client.post(self.base + "/conversation", json={
                    "message": "写脚本", "client_request_id": "strict-types", **extra})
                self.assertEqual(result.status_code, 422, result.text)
        self.assert_no_work()

    async def test_conversation_rejects_empty_or_missing_required_fields(self):
        for body in ({"message": "", "client_request_id": "empty"},
                     {"message": "写脚本", "client_request_id": ""}, {"message": "写脚本"},
                     {"client_request_id": "missing-message"}):
            with self.subTest(body=body):
                result = await self.client.post(self.base + "/conversation", json=body)
                self.assertEqual(result.status_code, 422, result.text)
        self.assert_no_work()

    async def test_confirm_rejects_missing_or_invalid_explicit_action(self):
        for body in ({"client_request_id": "confirm"},
                     {"client_request_id": "confirm", "user_action": "yes"},
                     {"client_request_id": "confirm", "user_action": True},
                     {"client_request_id": "confirm", "user_action": "confirm_script", "model_id": "injected"}):
            with self.subTest(body=body):
                result = await self.client.post(self.base + "/actions/missing/confirm", json=body)
                self.assertEqual(result.status_code, 422, result.text)
        self.assert_no_work()

    async def test_state_foreign_selection_is_rejected_without_provider(self):
        other = projects.create_project("foreign fixture")["project_id"]
        canvas.patch_canvas(other, {"nodes": [{"node_id": "private-node", "type": "text",
                                               "config": {"text": "private content"}}]})
        result = await self.client.get(self.base + "/conversation/state", params={"selected_node_id": "private-node"})
        self.assertEqual(result.status_code, 404, result.text)
        self.assertEqual(result.json()["error"]["code"], "CANVAS_NODE_NOT_FOUND")
        self.assertNotIn("private content", result.text)
        self.assert_no_work()

    async def test_state_repeated_selection_query_maps_to_selected_nodes(self):
        canvas.patch_canvas(self.pid, {"nodes": [
            {"node_id": "first", "type": "text", "config": {"text": "first"}},
            {"node_id": "second", "type": "text", "config": {"text": "second"}}]})
        state = self.ok(await self.client.get(self.base + "/conversation/state", params=[
            ("selected_node_id", "second"), ("selected_node_id", "first")]))
        self.assertEqual(state["creation"]["selected_node_ids"], ["second", "first"])
        self.assertEqual(state["current_role"], "writer")
        self.provider_factory.assert_not_called()

    async def test_legacy_body_remains_compatible_at_legacy_route(self):
        self.provider.push({"tool_calls": [{"id": "legacy-save", "name": "propose_script", "arguments": SCRIPT}]})
        state = self.ok(await self.client.get(self.base + "/conversation/state"))
        result = self.ok(await self.client.post(self.base + "/chat", json={
            "message": "请写白杯脚本", "client_request_id": "legacy-request", "intent": "script",
            "selected_asset_ids": [], "expected_workflow_revision": state["workflow_revision"]}))
        self.assertEqual(result["outcome"]["status"], "saved")
        self.assertEqual(result["outcome"]["goal"], "script")
        receipt = self.ok(await self.client.get(self.base + "/requests/legacy-request"))
        self.assertEqual(receipt["response"]["artifacts"], result["artifacts"])
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual(db.query("SELECT * FROM tasks"), [])

    async def test_post_script_exposes_persisted_action_confirmation_and_receipt(self):
        self.provider.push({"tool_calls": [{"id": "new-save", "name": "write_script", "arguments": SCRIPT}]})
        result = self.ok(await self.client.post(self.base + "/conversation", json={
            "message": "请直接写白杯脚本", "client_request_id": "new-request"}))
        self.assertEqual(result["flow_version"], 2)
        self.assertEqual(result["outcome"], {"status": "saved", "node_ids": ["script"]})
        self.assertEqual(result["current_role"], "writer")
        self.assertEqual(len(result["actions"]), 1)
        action = result["actions"][0]
        self.assertEqual(action["kind"], "confirm_script")
        self.assertEqual(projects.get_script(action["script_id"])["content"]["summary"], SCRIPT["summary"])
        receipt = self.ok(await self.client.get(self.base + "/conversation/requests/new-request"))
        self.assertEqual(receipt["response"], result)
        self.assertEqual(receipt["actions"][0]["action_id"], action["action_id"])
        confirm_body = {"client_request_id": "confirm-new", "user_action": "confirm_script"}
        confirmed = self.ok(await self.client.post(self.base + f"/actions/{action['action_id']}/confirm", json=confirm_body))
        self.assertEqual(confirmed["status"], "completed")
        self.assertEqual(confirmed["current_role"], "director")
        replay = self.ok(await self.client.post(self.base + f"/actions/{action['action_id']}/confirm", json=confirm_body))
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual(len(projects.list_scripts(self.pid)), 1)
        self.assertEqual(db.query("SELECT * FROM tasks"), [])

    async def test_real_provider_adapters_roundtrip_new_tools_through_asgi(self):
        for kind in ("ark", "deepseek"):
            with self.subTest(provider=kind):
                pid = projects.create_project("adapter fixture " + kind)["project_id"]
                first_calls = [call("get_creation_context", {"selected_node_ids": []}, "context-" + kind),
                               call("list_models", {"kind": "image"}, "models-" + kind)]
                final_calls = [call("write_script", SCRIPT, "script-" + kind)]
                bodies = []
                replies = iter([response(first_calls), response(final_calls)])

                def intercepted(*args, **kwargs):
                    bodies.append(copy.deepcopy(args[2] if kind == "ark" else args[1]))
                    return next(replies)

                if kind == "ark":
                    adapter = providers.ArkChatProvider(model="fixture-ark", timeout=1)
                    transport = "app.providers_ark._request"
                else:
                    # Explicit harmless placeholder and cleared environment; no credential lookup.
                    adapter = providers.DeepSeekChatProvider(model="deepseek-flash", api_key="fixture-only",
                                                             base_url="https://provider.invalid", timeout=1)
                    transport = "app.assistant.provider._deepseek_post"
                with patch.object(adapter, "available", return_value=True), \
                        patch.object(providers, "get_provider", return_value=adapter), \
                        patch(transport, side_effect=intercepted) as wire:
                    result = self.ok(await self.client.post(f"/api/projects/{pid}/director/conversation", json={
                        "message": "先读取项目和模型，再写出脚本", "client_request_id": "wire-" + kind}))
                self.assertEqual(wire.call_count, 2)
                self.assertEqual(result["outcome"]["status"], "saved")
                self.assertEqual(len(result["actions"]), 1)
                self.assertEqual(projects.get_script(result["actions"][0]["script_id"])["content"]["summary"], SCRIPT["summary"])
                self.assert_wire_protocol(bodies, first_calls)
                if kind == "deepseek":
                    self.assertEqual(bodies[0]["thinking"], {"type": "disabled"})

    def assert_wire_protocol(self, bodies, first_calls):
        expected = [{"type": "function", "function": tool.to_dict()} for tool in director_workflow.TOOLS]
        for body in bodies:
            self.assertEqual(body["tools"], expected)
            self.assertGreater(body["max_tokens"], 0)
            names = {tool["function"]["name"] for tool in body["tools"]}
            self.assertIn("prepare_script_confirmation", names)
            self.assertIn("prepare_generation", names)
            self.assertNotIn("confirm_generate", names)
            self.assertNotIn("confirm_action", names)
            self.assertTrue(all(message["role"] in {"system", "user", "assistant", "tool"} for message in body["messages"]))
            self.assertTrue(all("Authorization" not in message for message in body["messages"]))
        self.assertTrue(any(message["role"] == "system" and "会话上下文" in message["content"]
                            for message in bodies[0]["messages"]))
        continuation = bodies[1]["messages"]
        assistant_index = next(index for index, message in enumerate(continuation) if message.get("tool_calls"))
        self.assertIsNone(continuation[assistant_index]["content"])
        self.assertEqual(continuation[assistant_index]["tool_calls"], first_calls)
        tool_messages = continuation[assistant_index + 1:]
        self.assertEqual([m["role"] for m in tool_messages], ["tool", "tool"])
        self.assertEqual([m["tool_call_id"] for m in tool_messages], [c["id"] for c in first_calls])
        self.assertEqual([m["name"] for m in tool_messages], [c["function"]["name"] for c in first_calls])
        for message, advertised in zip(tool_messages, first_calls):
            self.assertIsInstance(json.loads(advertised["function"]["arguments"]), dict)
            self.assertIsInstance(json.loads(message["content"]), dict)
        self.assertEqual(len([m for m in continuation if m["role"] == "tool"]), 2)


if __name__ == "__main__":
    unittest.main()
