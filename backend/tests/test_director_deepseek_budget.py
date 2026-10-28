"""Offline DeepSeek wire protocol and director cost regression checks.

No dotenv, real credentials, production SQLite or external transport is read.
"""
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
_read_text = Path.read_text
with patch.object(Path, "read_text", lambda path, *a, **kw:
                  "" if path.name == ".env" else _read_text(path, *a, **kw)), \
        patch.object(Path, "mkdir", lambda *a, **kw: None):
    from app import config

from app import db, director_chat, director_workflow, projects
from app.assistant import provider
from app.errors import AppError


def response(name="get_creation_context", arguments="{}", *, usage=None):
    return {"model": "DeepSeek-V4.1-Flash-1017", "choices": [{
        "finish_reason": "tool_calls", "message": {"content": None, "tool_calls": [{
            "id": "supplier-call-1", "type": "function", "function": {
                "name": name, "arguments": arguments}}]}}],
        "usage": usage or {"prompt_tokens": 1000, "completion_tokens": 20,
                            "prompt_cache_hit_tokens": 900, "prompt_cache_miss_tokens": 100}}


class DeepSeekBudgetTests(unittest.TestCase):
    def test_peak_cny_prices_and_cache_are_not_double_counted(self):
        result = provider._parse_chat_response(response(), "deepseek", "deepseek-flash")
        self.assertEqual(result.input_tokens, 1000)
        self.assertEqual(result.meta["usage"]["prompt_cache_hit_tokens"], 900)
        self.assertEqual(result.meta["response_model"], "DeepSeek-V4.1-Flash-1017")
        # 100 misses * 2 + 900 hits * .04 + 20 output * 8, per million.
        self.assertEqual(director_chat._result_token_cost(result, provider_name="deepseek", model="deepseek-flash"), .000396)
        self.assertEqual(director_chat._token_cost(1_000_000, 1_000_000), 10)
        self.assertEqual(director_chat._token_cost(1_000_000, 1_000_000, model="deepseek-v4-pro"), 36)
        for alias in ("deepseek-v4-flash", "deepseek-v4-flash-vision-exp"):
            self.assertEqual(director_chat._token_cost(1_000_000, 0, model=alias), 2)

    def test_missing_or_inconsistent_cache_usage_cannot_lower_recorded_cost(self):
        for cache in ({}, {"prompt_cache_hit_tokens": 1001}, {"prompt_cache_hit_tokens": -1},
                      {"prompt_cache_hit_tokens": True}, {"prompt_cache_hit_tokens": "900"},
                      {"prompt_cache_hit_tokens": 900, "prompt_cache_miss_tokens": -1},
                      {"prompt_cache_hit_tokens": 900, "prompt_cache_miss_tokens": "100"},
                      {"prompt_cache_hit_tokens": 900, "prompt_cache_miss_tokens": None},
                      {"prompt_cache_hit_tokens": 900, "prompt_cache_miss_tokens": 900}):
            with self.subTest(cache=cache):
                result = provider._parse_chat_response(response(usage={
                    "prompt_tokens": 1000, "completion_tokens": 20, **cache}), "deepseek", "deepseek-flash")
                self.assertEqual(director_chat._result_token_cost(result, provider_name="deepseek", model="deepseek-flash"), .00216)

    def test_unknown_model_cannot_silently_use_flash_price(self):
        with self.assertRaises(AppError) as caught:
            director_chat._estimate_call_cost([], model="unpriced-model")
        self.assertEqual(caught.exception.code, "ASSISTANT_PRICING_UNAVAILABLE")

    def test_estimate_counts_schemas_and_arguments_with_margin_not_one_token_per_char(self):
        # A realistic JSON-heavy context was previously counted as ~30k tokens.
        messages = [{"role": "context", "content": {"description": "a" * 30_000}}]
        no_tools = director_chat._estimate_call_cost(messages, 1600, tools=[])
        self.assertGreater(no_tools, .035)  # 25% margin + 200 and all max output tokens.
        self.assertLess(no_tools, .05)  # Existing default per-call limit, unchanged.
        self.assertGreater(director_chat._estimate_call_cost(messages, 1600), no_tools)
        with_args = messages + [{"role": "assistant", "content": None, "tool_calls": [{
            "id": "supplier-call-1", "type": "function", "function": {
                "name": "write_script", "arguments": json.dumps({"summary": "x" * 1000})}}]}]
        self.assertGreater(director_chat._estimate_call_cost(with_args, 1600, tools=[]), no_tools)
        chinese = [{"role": "user", "content": "杯" * 30_000}]
        self.assertGreater(director_chat._estimate_call_cost(chinese, 1600, tools=[]), no_tools)
        self.assertEqual(director_workflow._estimate(messages, 1600),
                         director_chat._estimate_call_cost(messages, 1600, tools=director_workflow.TOOLS))

    def test_assistant_null_and_original_tool_arguments_survive_normalization(self):
        call = response(arguments=' { "node_ids": [] } ')["choices"][0]["message"]["tool_calls"][0]
        messages = [{"role": "assistant", "content": None, "tool_calls": [call]},
                    {"role": "tool", "tool_call_id": call["id"], "name": "get_creation_context",
                     "content": {"ok": True, "result": "实际保存内容"}}]
        normalized = provider._to_openai_messages(messages)
        self.assertIsNone(normalized[0]["content"])
        self.assertEqual(normalized[0]["tool_calls"], [call])
        self.assertEqual(normalized[1]["tool_call_id"], call["id"])
        self.assertEqual(json.loads(normalized[1]["content"]), messages[1]["content"])


class DirectorDeepSeekRoundTripTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="director-deepseek-budget-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.database = root / "isolated.db"
        for key, value in (("DATA", root), ("DB_PATH", self.database), ("ASSETS", root / "assets"),
                           ("MODELS", root / "models"), ("DIRECTOR_MAX_COST_PER_CALL_CNY", .05),
                           ("DIRECTOR_MAX_SESSION_COST_CNY", .30), ("DIRECTOR_MAX_OUTPUT_TOKENS", 1600)):
            self.enterContext(patch.object(config, key, value))
        actual_connect = sqlite3.connect

        def private_connect(path, *args, **kwargs):
            self.assertEqual(Path(path).resolve(), self.database.resolve())
            return actual_connect(path, *args, **kwargs)

        self.enterContext(patch("sqlite3.connect", side_effect=private_connect))
        self.enterContext(patch.dict(os.environ, {}, clear=True))
        for target in ("socket.create_connection", "socket.socket.connect", "socket.socket.connect_ex",
                       "app.providers_ark._request", "app.assistant.provider._deepseek_post", "app.worker.start"):
            self.enterContext(patch(target, side_effect=AssertionError("Real transport forbidden")))
        self.enterContext(patch.object(provider, "deepseek_config", return_value={
            "api_key": "fake-for-test", "model": "deepseek-flash",
            "base_url": "https://api.deepseek.com", "timeout": 30}))
        self.provider = provider.DeepSeekChatProvider()
        db.init_db()
        self.pid = projects.create_project("预算与协议隔离项目", {"subject": "one white cup"})["project_id"]

    def test_actual_adapter_roundtrip_preserves_tool_id_null_and_records_usage_cost(self):
        scripted = [response(arguments=" { } "), response("write_script", json.dumps({
            "summary": "One cup in morning light", "shots": [{"hint": "Close-up of the cup", "duration_s": 5}]}))]
        with patch.object(provider, "_deepseek_post", side_effect=scripted) as transport:
            out = director_workflow.conversation(self.pid, {"message": "请写一个水杯脚本", "client_request_id": "wire"}, provider=self.provider)
        self.assertEqual(transport.call_count, 2)
        body = transport.call_args_list[1].args[1]
        assistant_message = next(item for item in body["messages"] if item.get("tool_calls"))
        self.assertIsNone(assistant_message["content"])
        self.assertEqual(assistant_message["tool_calls"][0]["function"]["arguments"], " { } ")
        tool_message = next(item for item in body["messages"] if item["role"] == "tool")
        self.assertEqual(tool_message["tool_call_id"], "supplier-call-1")
        self.assertEqual(json.loads(tool_message["content"])["creation"]["project_id"], self.pid)
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(out["usage"]["input_tokens"], 2000)
        self.assertEqual(out["usage"]["output_tokens"], 40)
        self.assertEqual(out["usage"]["cost_cny"], .000792)
        self.assertEqual(len(projects.list_scripts(self.pid)), 1)
        ledger = db.query("SELECT input_tokens, output_tokens, cost_cny FROM assistant_usage WHERE kind='llm'")
        self.assertEqual(len(ledger), 2)
        self.assertTrue(all(row["cost_cny"] == .000396 for row in ledger))

    def test_actual_usage_over_session_limit_stops_before_second_transport(self):
        with patch.object(config, "DIRECTOR_MAX_SESSION_COST_CNY", .001), \
                patch.object(director_workflow, "_estimate", return_value=.0001), \
                patch.object(provider, "_deepseek_post", return_value=response(usage={
                    "prompt_tokens": 1000, "completion_tokens": 20})) as transport:
            out = director_workflow.conversation(self.pid, {"message": "请写脚本", "client_request_id": "budget"}, provider=self.provider)
        self.assertEqual(out["outcome"]["error"]["code"], "ASSISTANT_BUDGET_EXCEEDED")
        self.assertEqual(out["request_status"], "failed")
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(projects.list_scripts(self.pid), [])


if __name__ == "__main__":
    unittest.main()
