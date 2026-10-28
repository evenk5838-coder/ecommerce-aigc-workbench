"""Offline spending boundary checks. Never imports the product, dotenv or providers."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location("acceptance_guard", Path(__file__).resolve().parents[2] / "scripts/real-director-acceptance.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class RealAcceptanceGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = self.enterContext(tempfile.TemporaryDirectory(prefix="acceptance-guard-offline-"))
        self.approval = MODULE.approval_template()
        self.approval.update(approved=True, authorization_reference="UNIT TEST ONLY; not human permission",
                             run_id="offline-unit-test", approved_at="2026-10-28T09:00:00+08:00")
        self.guard = MODULE.Guard(self.tmp, self.approval)
        self.send = Mock(return_value={})

    def state(self):
        return json.loads(self.guard.path.read_text())

    def image(self, reference=False):
        body = {"model": MODULE.IMAGE, "prompt": "one cup", "size": "2688x1536",
                "watermark": False, "response_format": "b64_json"}
        if reference:
            body["image"] = "data:image/png;base64,c2Vuc2l0aXZlLWltYWdl"
        return body

    def video(self, reference=False):
        items = [{"type": "text", "text": "one cup"}]
        if reference:
            items.append({"type": "image_url", "image_url": {"url": "data:image/png;base64,c2VjcmV0"}, "role": "first_frame"})
        return {"model": MODULE.VIDEO, "content": items, "duration": 5, "ratio": "16:9",
                "resolution": "720p", "generate_audio": False, "watermark": False, "output_format": "mp4"}

    def llm(self):
        return {"model": "deepseek-flash", "messages": [{"role": "user", "content": "写脚本"}],
                "max_tokens": 1600, "thinking": {"type": "disabled"}}

    def test_preflight_template_is_not_approval(self):
        with self.assertRaises(ValueError):
            MODULE.validate_approval(MODULE.approval_template())
        proc = subprocess.run([sys.executable, str(Path(MODULE.__file__))], check=True, capture_output=True, text=True)
        value = json.loads(proc.stdout)
        self.assertEqual(value["network_calls"], 0)
        self.assertFalse(value["approval_template"]["approved"])

    def test_serve_without_approval_stops_before_importing_product(self):
        proc = subprocess.run([sys.executable, str(Path(MODULE.__file__)), "--serve"], capture_output=True, text=True)
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("--serve requires --approval", proc.stderr)

    def test_approval_validation_rejects_broader_or_ambiguous_scope(self):
        for patch in ({"total_cny": 5}, {"llm_cny": "NaN"}, {"media_cny": True},
                      {"image_model": "other"}, {"no_automatic_retry": False},
                      {"approved_at": "2026-10-28"}, {"run_id": "../../business"}, {"unexpected": 1}):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                MODULE.validate_approval({**self.approval, **patch})
        with self.assertRaises(ValueError):
            MODULE.validate_approval({**self.approval, "max_calls": {**MODULE.CAPS, "llm": 21}})

    def test_count_cap_survives_restart_and_approval_cannot_be_replaced(self):
        for _ in range(2):
            self.guard.ark(self.send, "POST", "/images/generations", self.image())
        restored = MODULE.Guard(self.tmp, self.approval)
        with self.assertRaises(ValueError):
            restored.ark(self.send, "POST", "/images/generations", self.image())
        self.assertEqual(self.send.call_count, 2)
        with self.assertRaises(ValueError):
            MODULE.Guard(self.tmp, {**self.approval, "authorization_reference": "changed"})

    def test_failure_reservation_is_kept_and_no_automatic_retry(self):
        self.send.side_effect = TimeoutError("SECRET and https://signed.example/token=SECRET")
        with self.assertRaises(TimeoutError):
            self.guard.ark(self.send, "POST", "/images/generations", self.image(reference=True))
        self.assertEqual(self.send.call_count, 1)
        self.assertEqual(self.state()["calls"][0]["status"], "error")
        with self.assertRaises(ValueError):
            MODULE.Guard(self.tmp, self.approval).ark(self.send, "POST", "/images/generations", self.image(reference=True))
        self.assertNotIn("SECRET", self.guard.path.read_text())
        self.assertNotIn("base64", self.guard.path.read_text())

    def test_independent_caps_and_total_are_enforced_before_send(self):
        self.approval.update(total_cny="0.20", media_cny="0.20")
        with tempfile.TemporaryDirectory() as other:
            guard = MODULE.Guard(other, self.approval)
            guard.ark(self.send, "POST", "/images/generations", self.image())
            with self.assertRaises(ValueError):
                guard.ark(self.send, "POST", "/images/generations", self.image(reference=True))
        self.assertEqual(self.send.call_count, 1)

    def test_concurrent_reservation_cannot_exceed_count(self):
        def invoke(_):
            try:
                MODULE.Guard(self.tmp, self.approval).ark(self.send, "POST", "/images/generations", self.image())
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(pool.map(invoke, range(8))), 2)
        self.assertEqual(len(self.state()["calls"]), 2)

    def test_ledger_write_failure_prevents_transport(self):
        with patch.object(MODULE.os, "replace", side_effect=OSError("disk unavailable")), self.assertRaises(OSError):
            self.guard.ark(self.send, "POST", "/images/generations", self.image())
        self.send.assert_not_called()

    def test_llm_call_count_never_returns_after_success_settlement(self):
        self.send.return_value = {"usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}}
        for _ in range(20):
            self.guard.deepseek(self.send, "https://api.deepseek.com/chat/completions", self.llm(), "SECRET", 30)
        with self.assertRaises(ValueError):
            MODULE.Guard(self.tmp, self.approval).deepseek(self.send, "https://api.deepseek.com/chat/completions", self.llm(), "SECRET", 30)
        self.assertEqual(self.send.call_count, 20)

    def test_changed_query_id_is_not_adopted(self):
        self.send.return_value = {"id": "original-owned"}
        self.guard.ark(self.send, "POST", "/contents/generations/tasks", self.video())
        self.send.return_value = {"id": "new-unowned", "status": "succeeded", "usage": {"completion_tokens": 108000}}
        with self.assertRaises(ValueError):
            self.guard.ark(self.send, "GET", "/contents/generations/tasks/original-owned")
        state = self.state()
        self.assertTrue(state["blocked"])
        self.assertEqual(state["calls"][0]["remote_task_id"], "original-owned")

    def test_all_unapproved_media_options_are_blocked(self):
        requests = [("POST", "/chat/completions", self.image()), ("DELETE", "/contents/generations/tasks/old", None),
                    ("POST", "/images/generations", {**self.image(), "size": "1024x1024"}),
                    ("POST", "/images/generations", {**self.image(), "sequential_image_generation": "auto"}),
                    ("POST", "/images/generations", {**self.image(), "image": "https://remote.example/a.png"}),
                    ("POST", "/contents/generations/tasks", {**self.video(), "generate_audio": True}),
                    ("POST", "/contents/generations/tasks", {**self.video(), "duration": 10}),
                    ("POST", "/contents/generations/tasks", {**self.video(), "draft": True})]
        for method, path, body in requests:
            with self.subTest(path=path, body=body), self.assertRaises(ValueError):
                self.guard.ark(self.send, method, path, body)
        self.send.assert_not_called()
        self.assertEqual(self.state()["calls"], [])

    def test_queries_require_owned_remote_id_and_cost_overrun_freezes_new_calls(self):
        self.send.return_value = {"id": "remote-acceptance-1"}
        self.guard.ark(self.send, "POST", "/contents/generations/tasks", self.video())
        with self.assertRaises(ValueError):
            self.guard.ark(self.send, "GET", "/contents/generations/tasks/not-ours")
        self.send.return_value = {"id": "remote-acceptance-1", "status": "succeeded",
                                  "usage": {"completion_tokens": 200000}, "content": {"video_url": "https://signed.example/SECRET"}}
        self.guard.ark(self.send, "GET", "/contents/generations/tasks/remote-acceptance-1")
        self.assertTrue(self.state()["blocked"])
        self.assertEqual(MODULE.money(self.state()["calls"][0]["observed_cny"]), MODULE.Decimal("0.84"))
        with self.assertRaises(ValueError):
            self.guard.ark(self.send, "POST", "/images/generations", self.image())
        self.assertEqual(self.send.call_count, 2)
        self.assertNotIn("SECRET", self.guard.path.read_text())

    def test_llm_response_stores_only_safe_summary(self):
        self.send.return_value = {"usage": {"prompt_tokens": 10, "completion_tokens": 20},
                                  "choices": [{"message": {"content": "SECRET", "tool_calls": [
                                      {"function": {"name": "write_script", "arguments": "SECRET"}}]}}]}
        self.guard.deepseek(self.send, "https://api.deepseek.com/chat/completions", self.llm(), "SECRET", 30)
        row = self.state()["calls"][0]
        self.assertEqual(row["tool_names"], ["write_script"])
        self.assertEqual(row["tokens"], {"prompt_tokens": 10, "completion_tokens": 20})
        self.assertNotIn("SECRET", self.guard.path.read_text())
        self.assertGreater(MODULE.money(row["reserved_cny"]), MODULE.money(row["observed_cny"]))
        self.assertTrue(row["settled"])
        self.assertEqual(self.guard.totals(self.state())["llm"], MODULE.money(row["observed_cny"]))

    def test_guard_cny_allowance_rates_and_rounding_stay_conservative(self):
        self.assertEqual(MODULE.Guard.llm_cost(1_000_000, 0), MODULE.Decimal("2.160000"))
        self.assertEqual(MODULE.Guard.llm_cost(0, 1_000_000), MODULE.Decimal("8.640000"))
        self.assertEqual(MODULE.Guard.llm_cost(1, 1), MODULE.Decimal("0.000011"))

    def test_llm_success_without_complete_usage_keeps_reservation(self):
        for usage in ({}, {"prompt_tokens": 10}):
            self.send.return_value = {"usage": usage}
            self.guard.deepseek(self.send, "https://api.deepseek.com/chat/completions", self.llm(), "SECRET", 30)
        state = self.state()
        self.assertFalse(any(row.get("settled") for row in state["calls"]))
        self.assertEqual(self.guard.totals(state)["llm"], sum(MODULE.money(row["reserved_cny"]) for row in state["calls"]))

    def test_invalid_usage_does_not_release_reservation(self):
        for usage in ({"prompt_tokens": -1, "completion_tokens": 3},
                      {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 7},
                      {"prompt_tokens": 5, "completion_tokens": 3, "prompt_cache_hit_tokens": 6},
                      {"prompt_tokens": 5, "completion_tokens": 3, "prompt_cache_hit_tokens": 3, "prompt_cache_miss_tokens": 3}):
            with self.subTest(usage=usage), tempfile.TemporaryDirectory() as temp:
                guard = MODULE.Guard(temp, self.approval)
                self.send.return_value = {"usage": usage}
                with self.assertRaises(ValueError):
                    guard.deepseek(self.send, "https://api.deepseek.com/chat/completions", self.llm(), "SECRET", 30)
                state = json.loads(guard.path.read_text())
                self.assertTrue(state["blocked"])
                self.assertFalse(state["calls"][0].get("settled"))

    def test_video_pending_usage_does_not_settle_and_success_does(self):
        self.send.return_value = {"id": "pending-owned"}
        self.guard.ark(self.send, "POST", "/contents/generations/tasks", self.video())
        self.send.return_value = {"id": "pending-owned", "status": "running", "usage": {"completion_tokens": 0}}
        self.guard.ark(self.send, "GET", "/contents/generations/tasks/pending-owned")
        self.assertEqual(self.guard.totals(self.state())["media"], MODULE.VIDEO_RESERVE)
        self.send.return_value = {"id": "pending-owned", "status": "succeeded", "usage": {"completion_tokens": 108000}}
        self.guard.ark(self.send, "GET", "/contents/generations/tasks/pending-owned")
        self.assertEqual(self.guard.totals(self.state())["media"], MODULE.Decimal("0.4536"))

    def test_llm_endpoint_model_token_limit_and_extra_options_blocked(self):
        for body in ({**self.llm(), "model": "deepseek-pro"}, {**self.llm(), "max_tokens": 9999},
                     {**self.llm(), "max_tokens": True}, {**self.llm(), "n": 2},
                     {**self.llm(), "messages": [{"role": "user", "content": [{"type": "image_url"}]}]},
                     {**self.llm(), "messages": [{"role": "user", "content": "中" * 100000}]}):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.guard.deepseek(self.send, "https://api.deepseek.com/chat/completions", body, "SECRET", 30)
        with self.assertRaises(ValueError):
            self.guard.deepseek(self.send, "https://other.example/chat/completions", self.llm(), "SECRET", 30)
        self.send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
