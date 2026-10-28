"""v0.11 script execution regressions: temporary SQLite and scripted providers only."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import config, db, director_chat, projects
from app.assistant import provider, service, tools
from app.errors import AppError


def call(name="propose_script", args=None, **extra):
    return {"tool_calls": [{"name": name, "arguments": args if args is not None else {}, **extra}]}


class DirectorV011(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="director-v011-")
        self.path = Path(self.tmp.name) / "isolated.db"
        self.addCleanup(self.tmp.cleanup)
        for name, value in {"DB_PATH": self.path, "DIRECTOR_MAX_ROUNDS": 4,
                            "DIRECTOR_MAX_TOOL_CALLS": 4, "DIRECTOR_MAX_REPEATED_ERRORS": 2,
                            "DIRECTOR_MAX_COST_PER_CALL_CNY": 0,
                            "DIRECTOR_MAX_SESSION_COST_CNY": 0}.items():
            p = patch.object(config, name, value)
            p.start()
            self.addCleanup(p.stop)
        connect = sqlite3.connect
        def isolated_connect(path, *args, **kwargs):
            self.assertEqual(Path(path).resolve(), self.path.resolve(), "Business DB access forbidden")
            return connect(path, *args, **kwargs)
        for p in (patch("sqlite3.connect", side_effect=isolated_connect),
                  patch("socket.create_connection", side_effect=AssertionError("Network forbidden")),
                  patch.object(provider, "_deepseek_post", side_effect=AssertionError("Real LLM forbidden"))):
            p.start()
            self.addCleanup(p.stop)
        db.init_db()
        self.pid = projects.create_project("isolated", {"subject": "one bottle"})["project_id"]

    def chat(self, script, intent="auto", key="r1", **kwargs):
        p = provider.FixedReplyProvider(script)
        out = director_chat.chat(self.pid, {"message": "test request", "client_request_id": key,
                                            "intent": intent, **kwargs}, provider=p)
        return out, p

    def test_schema_and_validator_agree_on_hint_and_strict_fields(self):
        schema = next(t.parameters for t in director_chat.PROJECT_TOOLS if t.name == "propose_script")
        self.assertIn("hint", schema["$defs"]["ScriptShot"]["required"])
        for content in ({"summary": 1}, {"summary": " "},
                        {"summary": "s", "shots": [{"description": "wrong field"}]},
                        {"summary": "s", "shots": [{"hint": "x", "seq": True}]},
                        {"summary": "s", "shots": [{"hint": "x", "duration_s": 5.5}]}):
            with self.subTest(content=content), self.assertRaises(AppError):
                director_chat._clean_script(content)
        self.assertEqual(director_chat._clean_script({"summary": " s ", "shots": [{"hint": " x "}]}),
                         {"summary": "s", "shots": [{"hint": "x", "seq": 1}]})

    def test_two_different_errors_can_be_repaired_without_fake_completion(self):
        out, p = self.chat([call(args={"summary": "s", "shots": [{}]}),
                            call(args={"shots": []}), call(args={"summary": "real script"})], intent="script")
        self.assertEqual(len(p.calls), 3)
        self.assertEqual(out["outcome"], {"status": "saved", "goal": "script", "node_ids": ["script"]})
        self.assertEqual(len(projects.list_scripts(self.pid)), 1)
        turn = director_chat.messages(self.pid)["items"][0]["structured"]
        self.assertEqual(len(turn["tool_failures"]), 2)
        self.assertEqual(turn["outcome"], out["outcome"])
        self.assertEqual(director_chat.request_receipt(self.pid, "r1")["response"]["outcome"], out["outcome"])

    def test_repeated_same_error_stops_and_corrects_model_promise(self):
        item = {**call(args={"summary": "", "shots": []}), "reply": "稍等，我这就把脚本落库"}
        out, p = self.chat([item, item, call(args={"summary": "must not execute"})], intent="script")
        self.assertEqual(len(p.calls), 2)
        self.assertEqual(out["outcome"]["status"], "failed")
        self.assertEqual(out["artifacts"], [])
        self.assertNotIn("稍等", out["reply"])
        self.assertEqual(projects.list_scripts(self.pid), [])
        self.assertIn("VALIDATION_FAILED", json.dumps(director_chat._history(self.pid)))

    def test_plain_message_cannot_hide_an_unrepaired_tool_error(self):
        out, _ = self.chat([call(args={"summary": ""}), {"reply": "已经全部保存成功"}])
        self.assertEqual(out["outcome"]["status"], "failed")
        self.assertNotIn("全部保存成功", out["reply"])

    def test_explicit_script_goal_needs_an_actual_artifact(self):
        out, p = self.chat([{"reply": "马上开始"}, {"reply": "已经写好了"}], intent="script")
        self.assertEqual(out["outcome"]["status"], "failed")
        self.assertEqual(out["outcome"]["error"]["code"], "SCRIPT_NOT_SAVED")
        self.assertEqual(len(p.calls), 2)
        self.assertEqual(out["request_status"], "completed")

    def test_auto_clarification_and_legacy_intents_do_not_require_artifact(self):
        for index, intent in enumerate(("auto", "generate", "optimize_only")):
            out, _ = self.chat([{"reply": "希望突出哪个卖点？"}], intent=intent, key=f"r{index}")
            self.assertEqual(out["outcome"], {"status": "clarify", "goal": "auto", "node_ids": []})
        out, _ = self.chat([{"reply": "可以继续讨论。"}], key="message")
        self.assertEqual(out["outcome"]["status"], "message")

    def test_truncated_tool_never_executes_and_retry_keeps_usage(self):
        first = {**call(args={"summary": "truncated but valid JSON"}),
                 "meta": {"finish_reason": "length"}, "input_tokens": 10, "output_tokens": 400}
        out, p = self.chat([first, {**call(args={"summary": "complete"}), "output_tokens": 20}], intent="script")
        self.assertEqual(len(p.calls), 2)
        self.assertEqual([s["content"]["summary"] for s in projects.list_scripts(self.pid)], ["complete"])
        self.assertEqual(out["usage"]["output_tokens"], 420)
        self.assertEqual(out["rounds"][0]["finish_reason"], "length")
        self.assertEqual(out["tool_calls"][0]["error"]["code"], "MODEL_OUTPUT_TRUNCATED")

    def test_duplicate_script_calls_in_one_model_reply_only_save_once(self):
        out, p = self.chat([{"tool_calls": [call(args={"summary": "one"})["tool_calls"][0],
                                             call(args={"summary": "two"})["tool_calls"][0]]}], intent="script")
        self.assertEqual(len(p.calls), 1)
        self.assertEqual(len(projects.list_scripts(self.pid)), 1)
        self.assertEqual(len(out["artifacts"]), 1)

    def test_partial_artifact_survives_later_provider_failure_and_replay(self):
        class Failing(provider.FixedReplyProvider):
            def chat(self, *a, **kw):
                if self.calls:
                    raise RuntimeError("fixture transport error")
                return super().chat(*a, **kw)
        p = Failing([call("propose_brief", {"brief": {"subject": "bottle"}})])
        body = {"message": "prepare brief", "client_request_id": "partial"}
        out = director_chat.chat(self.pid, body, provider=p)
        self.assertEqual(out["outcome"]["status"], "partial")
        self.assertEqual(out["artifacts"][0]["type"], "brief")
        replay = director_chat.chat(self.pid, body, provider=p)
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["outcome"], out["outcome"])

    def test_budget_gate_keeps_failed_outcome_and_never_invokes_provider(self):
        p = provider.FixedReplyProvider([call(args={"summary": "no"})])
        with patch.object(config, "DIRECTOR_MAX_COST_PER_CALL_CNY", .000001):
            with self.assertRaises(AppError):
                director_chat.chat(self.pid, {"message": "x", "client_request_id": "budget"}, provider=p)
        self.assertEqual(p.calls, [])
        receipt = director_chat.request_receipt(self.pid, "budget")
        self.assertEqual(receipt["status"], "failed")
        self.assertEqual(receipt["response"]["request_status"], "failed")
        self.assertEqual(receipt["response"]["outcome"]["status"], "failed")
        with self.assertRaises(AppError) as caught:
            director_chat.chat(self.pid, {"message": "x", "client_request_id": "budget"}, provider=p)
        self.assertEqual(caught.exception.code, "DIRECTOR_REQUEST_FAILED")

    def test_script_output_limit_is_independent_of_ordinary_assistant(self):
        with patch.dict(os.environ, {"ASSISTANT_MAX_TOKENS": "400"}), \
                patch.object(config, "DIRECTOR_MAX_OUTPUT_TOKENS", 1600):
            _, p = self.chat([call(args={"summary": "complete"})], intent="script")
        self.assertEqual(p.calls[0]["max_tokens"], 1600)

    def test_ordinary_assistant_blocks_invalid_and_truncated_tool_calls(self):
        cases = [call("list_models", arguments_error={"code": "TOOL_ARGUMENTS_INVALID", "message": "bad JSON"},
                      raw_arguments="{broken"),
                 {**call("list_models"), "meta": {"finish_reason": "length"}}]
        for item in cases:
            with self.subTest(item=item), patch.object(tools, "execute") as execute:
                p = provider.FixedReplyProvider([item, {"reply": "工具参数有误，未执行。"}])
                service.chat(session_id=None, project_id=self.pid, message="list", provider=p)
                execute.assert_not_called()
                self.assertFalse(p.calls[1]["tool_results"][0]["ok"])


class ProviderParsingV011(unittest.TestCase):
    def test_invalid_json_and_nonobject_are_explicit_errors_not_executable_empty_args(self):
        for args in ("{broken", "[]", "1", "null", ""):
            with self.subTest(args=args):
                res = provider._parse_chat_response({"choices": [{"finish_reason": "tool_calls", "message": {
                    "content": "", "tool_calls": [{"id": "real-id", "function": {
                        "name": "propose_script", "arguments": args}}]}}],
                    "usage": {"prompt_tokens": 5, "completion_tokens": 9}}, "deepseek", "test")
                tc = res.tool_calls[0]
                self.assertEqual(tc.id, "real-id")
                self.assertEqual(provider.tool_call_error(res, tc)["code"], "TOOL_ARGUMENTS_INVALID")
                self.assertEqual(provider.tool_arguments_json(tc), args)
                self.assertEqual(res.output_tokens, 9)


if __name__ == "__main__":
    unittest.main()
