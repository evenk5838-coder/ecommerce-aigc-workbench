"""Isolated node execution contract; no worker, real model, or business DB."""
from __future__ import annotations

import io
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
                  "" if path.name == ".env" else _read_text(path, *a, **kw)):
    from app import canvas, config, db, node_execution as service, projects, storage
from app.errors import AppError

IMAGE = "doubao-seedream-5-0-flash-260915"
VIDEO = "doubao-seedance-1-0-pro-fast-251015"


class NodeExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="node-execution-")
        self.addCleanup(self.tmp.cleanup)
        for key, value in (("DB_PATH", Path(self.tmp.name) / "test.db"),
                           ("ASSETS", Path(self.tmp.name) / "assets"), ("BUDGET_CNY", 20)):
            self.enterContext(patch.object(config, key, value))
        self.enterContext(patch("app.providers_ark._request", side_effect=AssertionError("network forbidden")))
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("network forbidden")))
        self.enterContext(patch("app.worker.start", side_effect=AssertionError("worker forbidden")))
        # This suite tests execution plumbing independently of a future EOS date.
        self.enterContext(patch.object(config, "model_lifecycle_state", return_value={"status": "active"}))
        db.init_db()
        self.project = projects.create_project("single node")['project_id']
        self.other = projects.create_project("other")['project_id']

    def error(self, code, callback):
        with self.assertRaises(AppError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def asset(self, color="blue"):
        output = io.BytesIO()
        Image.new("RGB", (32, 24), color).save(output, "PNG")
        return storage.save_bytes(output.getvalue(), "image", "test")

    def create_node(self, node_id="image", mode="text_to_image", **overrides):
        kind = "image" if mode.endswith("_image") else "video"
        cfg = {"model_id": IMAGE if kind == "image" else VIDEO,
               "generation_mode": mode, "prompt": "A cup in morning light",
               "params": {"size": "1024x1024"} if kind == "image" else
                         {"duration": 5, "ratio": "16:9", "resolution": "720p"}}
        if mode.startswith("image_"):
            cfg["input_asset_id"] = self.asset()["id"]
        cfg.update(overrides)
        canvas.patch_canvas(self.project, {"nodes": [{"node_id": node_id, "type": kind, "config": cfg}]})
        return cfg

    def generate(self, quote, request="first", node_id=None):
        return service.generate_node(self.project, node_id or quote["node_id"], {
            "quote_id": quote["quote_id"], "client_request_id": request, "user_action": "confirm_generate"})

    def finish(self, execution, status="succeeded", asset_id=None):
        db.update("tasks", execution["task_id"], {"status": status, "stage": "done", "progress": 1,
            "output_asset_id": asset_id, "finished_at": db.now(), "cost_cny": .12 if status == "succeeded" else None})

    def test_all_four_modes_without_scripts_or_shots(self):
        for i, mode in enumerate(("text_to_image", "image_to_image", "text_to_video", "image_to_video")):
            name = f"node-{i}"
            cfg = self.create_node(name, mode)
            quote = service.quote_node(self.project, name)
            self.assertEqual(len(db.query("SELECT * FROM tasks")), i)
            self.assertFalse(quote["side_effects"]["model_called"])
            execution = self.generate(quote, name)
            task = db.get("tasks", execution["task_id"])
            params = db.loads(task["params_json"])
            self.assertEqual(params["generation_mode"], mode)
            self.assertEqual(task["input_asset_id"], cfg.get("input_asset_id"))
            if mode.startswith("image_"):
                self.assertEqual(params["input_asset_id"], cfg["input_asset_id"])
                self.assertEqual(len(quote["input"]["reference"]["sha256"]), 64)
            self.assertEqual(task["external_request_id"], self.project)
            self.assertEqual(task["caller_id"], "canvas")
            self.assertEqual(execution["status"], "queued")
        self.assertEqual(db.query("SELECT * FROM shots"), [])
        self.assertEqual(db.query("SELECT * FROM script_versions"), [])
        self.assertEqual(len(db.query("SELECT * FROM tasks")), 4)

    def test_quote_has_no_generation_and_expected_revision_guard(self):
        self.create_node()
        quote = service.quote_node(self.project, "image", {"expected_config_revision": 1})
        self.assertEqual(quote["config_revision"], 1)
        self.assertEqual(db.query("SELECT * FROM tasks"), [])
        self.error("CANVAS_REVISION_CONFLICT", lambda: service.quote_node(self.project, "image", {"expected_config_revision": 2}))
        self.error("VALIDATION_FAILED", lambda: service.quote_node(self.project, "image", {"expected_config_revision": True}))

    def test_existing_canvas_image_ratio_size_presets_are_supported(self):
        for i, (ratio, size) in enumerate((("16:9", "2688x1536"), ("9:16", "1536x2688"),
                                          ("3:4", "1728x2304"), ("1:1", "1024x1024"))):
            self.create_node(f"preset-{i}", params={"ratio": ratio, "size": size})
            quote = service.quote_node(self.project, f"preset-{i}")
            self.assertEqual(quote["input"]["params"]["ratio"], ratio)
            self.assertEqual(quote["input"]["params"]["size"], size)
            execution = self.generate(quote, str(i))
            self.assertEqual(service.get_execution(self.project, f"preset-{i}", execution["execution_id"])["input"]["params"]["size"], size)
        self.create_node("invalid-ratio", params={"ratio": "9:16", "size": "2688x1536"})
        self.error("MODEL_CAPABILITY_MISMATCH", lambda: service.quote_node(self.project, "invalid-ratio"))

    def test_mode_model_parameter_and_reference_validation(self):
        self.create_node()
        row = db.query("SELECT * FROM canvas_nodes WHERE project_id=?", (self.project,))[0]
        cases = (({"model_id": VIDEO}, "VALIDATION_FAILED"),
                 ({"generation_mode": "image_to_image"}, "REFERENCE_IMAGE_REQUIRED"),
                 ({"params": {"size": "1x1"}}, "MODEL_CAPABILITY_MISMATCH"),
                 ({"params": {"watermark": "false"}}, "VALIDATION_FAILED"),
                 ({"params": {"unsupported": 1}}, "VALIDATION_FAILED"),
                 ({"model_id": "sd15"}, "NODE_NOT_EXECUTABLE"))
        original = db.loads(row["config_json"])
        for change, code in cases:
            db.update("canvas_nodes", row["id"], {"config_json": db.dumps({**original, **change})})
            self.error(code, lambda: service.quote_node(self.project, "image"))
        self.assertEqual(db.query("SELECT * FROM tasks"), [])

    def test_non_media_node_cannot_execute(self):
        canvas.patch_canvas(self.project, {"nodes": [{"node_id": "words", "type": "text", "config": {"text": "story"}}]})
        self.error("NODE_NOT_EXECUTABLE", lambda: service.quote_node(self.project, "words"))
        self.error("CANVAS_NODE_NOT_FOUND", lambda: service.quote_node(self.project, "missing"))

    def test_confirmation_is_required_and_unknown_approval_fields_rejected(self):
        self.create_node()
        quote = service.quote_node(self.project, "image")
        body = {"quote_id": quote["quote_id"], "client_request_id": "a"}
        self.error("CONFIRM_NOT_ALLOWED", lambda: service.generate_node(self.project, "image", body))
        self.error("VALIDATION_FAILED", lambda: service.generate_node(self.project, "image", {**body, "approval": True}))
        self.assertEqual(db.query("SELECT * FROM tasks"), [])

    def test_exact_request_replays_after_config_change_and_expiry(self):
        self.create_node()
        quote = service.quote_node(self.project, "image")
        first = self.generate(quote)
        canvas.patch_canvas(self.project, {"nodes": [{"node_id": "image", "config": {**self.create_node("another"), "prompt": "changed"}}]})
        db.update("canvas_node_quotes", quote["quote_id"], {"expires_at": 0})
        replay = self.generate(quote)
        self.assertEqual(replay["execution_id"], first["execution_id"])
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(len(db.query("SELECT * FROM tasks")), 1)

    def test_same_quote_with_new_request_does_not_generate_twice(self):
        self.create_node()
        quote = service.quote_node(self.project, "image")
        self.generate(quote)
        self.error("NODE_QUOTE_CONSUMED", lambda: self.generate(quote, "different"))
        self.assertEqual(len(db.query("SELECT * FROM tasks")), 1)

    def test_request_id_with_different_quote_or_node_conflicts(self):
        self.create_node()
        first, second = service.quote_node(self.project, "image"), service.quote_node(self.project, "image")
        self.generate(first)
        self.error("NODE_EXECUTION_CONFLICT", lambda: self.generate(second))
        self.error("NODE_EXECUTION_CONFLICT", lambda: self.generate(first, node_id="other-node"))

    def test_multiple_quotes_same_active_node_cannot_double_submit(self):
        self.create_node()
        first, second = service.quote_node(self.project, "image"), service.quote_node(self.project, "image")
        self.generate(first)
        self.error("NODE_EXECUTION_ACTIVE", lambda: self.generate(second, "second"))
        self.assertEqual(len(db.query("SELECT * FROM tasks")), 1)

    def test_expired_quote_cannot_create_task(self):
        self.create_node()
        quote = service.quote_node(self.project, "image")
        db.update("canvas_node_quotes", quote["quote_id"], {"expires_at": 0})
        self.error("NODE_QUOTE_EXPIRED", lambda: self.generate(quote))
        self.assertEqual(db.query("SELECT * FROM tasks"), [])

    def test_prompt_change_invalidates_quote_but_title_and_layout_do_not(self):
        cfg = self.create_node()
        quote = service.quote_node(self.project, "image")
        canvas.patch_canvas(self.project, {"nodes": [{"node_id": "image", "config": {**cfg, "title": "My title"},
            "position": {"x": 100, "y": 400}}], "viewport": {"x": 50, "y": 20, "zoom": .4}})
        created = self.generate(quote)
        self.finish(created)
        fresh = service.quote_node(self.project, "image")
        canvas.patch_canvas(self.project, {"nodes": [{"node_id": "image", "config": {**cfg, "prompt": "different light"}}]})
        self.error("NODE_QUOTE_STALE", lambda: self.generate(fresh, "fresh"))

    def test_reference_file_changed_after_quote_is_stale(self):
        cfg = self.create_node(mode="image_to_image")
        quote = service.quote_node(self.project, "image")
        asset = db.get("assets", cfg["input_asset_id"])
        replacement = io.BytesIO()
        Image.new("RGB", (32, 24), "red").save(replacement, "PNG")
        storage.asset_path(asset).write_bytes(replacement.getvalue())
        self.error("NODE_QUOTE_STALE", lambda: self.generate(quote))
        self.assertEqual(db.query("SELECT * FROM tasks"), [])

    def test_quote_and_execution_are_project_scoped(self):
        self.create_node()
        quote = service.quote_node(self.project, "image")
        self.error("NODE_QUOTE_NOT_FOUND", lambda: service.generate_node(self.other, "image", {
            "quote_id": quote["quote_id"], "client_request_id": "foreign", "user_action": "confirm_generate"}))
        execution = self.generate(quote)
        self.error("NODE_EXECUTION_NOT_FOUND", lambda: service.get_execution(self.other, "image", execution["execution_id"]))
        self.assertEqual(service.list_executions(self.other, "image")["executions"], [])

    def test_global_library_image_can_be_used_as_input_without_script(self):
        asset = self.asset()
        self.create_node("video", "image_to_video", input_asset_id=asset["id"])
        execution = self.generate(service.quote_node(self.project, "video"))
        self.assertEqual(db.get("tasks", execution["task_id"])["input_asset_id"], asset["id"])
        self.assertEqual(db.query("SELECT * FROM shots"), [])

    def test_execution_link_failure_rolls_back_task_and_quote_consumption(self):
        self.create_node()
        quote = service.quote_node(self.project, "image")
        with db.conn() as connection:
            connection.execute("CREATE TRIGGER reject_execution BEFORE INSERT ON canvas_node_executions "
                               "BEGIN SELECT RAISE(ABORT, 'test execution insert failed'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "test execution insert failed"):
            self.generate(quote)
        self.assertEqual(db.query("SELECT * FROM tasks"), [])
        self.assertEqual(db.query("SELECT * FROM canvas_node_executions"), [])
        self.assertIsNone(db.get("canvas_node_quotes", quote["quote_id"])["consumed_at"])

    def test_budget_gate_includes_queued_reservations_and_unapproved_budget(self):
        self.create_node()
        self.create_node("second")
        first, second = service.quote_node(self.project, "image"), service.quote_node(self.project, "second")
        with patch.object(config, "BUDGET_CNY", 0):
            self.error("BUDGET_NOT_APPROVED", lambda: self.generate(first))
        with patch.object(config, "BUDGET_CNY", .12):
            self.generate(first)
            self.error("BUDGET_EXCEEDED", lambda: self.generate(second, "second"))
        self.assertEqual(len(db.query("SELECT * FROM tasks")), 1)
        self.assertIsNone(db.get("canvas_node_quotes", second["quote_id"])["consumed_at"])

    def test_remote_success_download_failure_retains_unsettled_charge_reservation(self):
        self.create_node("first-video", "text_to_video")
        self.create_node("second-video", "text_to_video")
        first = service.quote_node(self.project, "first-video")
        second = service.quote_node(self.project, "second-video")
        execution = self.generate(first)
        db.update("tasks", execution["task_id"], {"status": "failed", "stage": "failed",
            "error_code": "DOWNLOAD_FAILED", "remote_task_id": "remote-success",
            "remote_status": "succeeded", "cost_cny": None, "finished_at": db.now()})
        self.assertFalse(service.get_execution(self.project, "first-video", execution["execution_id"])["active"])
        self.assertEqual(service.cloud_tasks.spent_today(), 0)
        with patch.object(config, "BUDGET_CNY", first["estimated_cost_cny"]):
            self.error("BUDGET_EXCEEDED", lambda: self.generate(second, "second"))
        self.assertEqual(len(db.query("SELECT * FROM tasks")), 1)
        self.assertIsNone(db.get("canvas_node_quotes", second["quote_id"])["consumed_at"])

    def test_settled_remote_success_charge_is_not_reserved_twice(self):
        self.create_node("first-video", "text_to_video")
        self.create_node("second-video", "text_to_video")
        first = service.quote_node(self.project, "first-video")
        second = service.quote_node(self.project, "second-video")
        execution = self.generate(first)
        db.update("tasks", execution["task_id"], {"status": "failed", "stage": "failed",
            "error_code": "DOWNLOAD_FAILED", "remote_task_id": "remote-success",
            "remote_status": "succeeded", "cost_cny": first["estimated_cost_cny"], "finished_at": db.now()})
        self.assertEqual(service.cloud_tasks.spent_today(), first["estimated_cost_cny"])
        with patch.object(config, "BUDGET_CNY", first["estimated_cost_cny"] + second["estimated_cost_cny"]):
            self.generate(second, "second")
        self.assertEqual(len(db.query("SELECT * FROM tasks")), 2)

    def test_reservation_uses_consumed_quote_after_model_capability_and_rate_change(self):
        self.create_node("old-video", "text_to_video")
        quote = service.quote_node(self.project, "old-video")
        execution = self.generate(quote)
        db.update("tasks", execution["task_id"], {"status": "failed", "error_code": "CREATE_RESULT_UNKNOWN"})
        self.create_node("new-image")
        image = service.quote_node(self.project, "new-image")
        total = round(quote["estimated_cost_cny"] + image["estimated_cost_cny"], 4)
        with patch.dict(config.MODEL_CAPABILITIES, {VIDEO: {}}), \
                patch.dict(config.VIDEO_PRICE_PER_MTOKEN, {VIDEO: 999}), \
                patch.object(config, "BUDGET_CNY", total), \
                patch.object(service.cloud_tasks, "budget_gate", wraps=service.cloud_tasks.budget_gate) as gate:
            self.generate(image, "new-image", "new-image")
            gate.assert_called_once_with(total)
        self.assertEqual(db.get("tasks", execution["task_id"])["error_code"], "CREATE_RESULT_UNKNOWN")

    def test_corrupt_frozen_reservation_is_not_silently_repriced_or_ignored(self):
        self.create_node("old-video", "text_to_video")
        old = service.quote_node(self.project, "old-video")
        self.generate(old)
        self.create_node("new-image")
        new = service.quote_node(self.project, "new-image")
        with self.assertRaises(sqlite3.IntegrityError):
            db.update("canvas_node_quotes", old["quote_id"], {"estimated_cost_cny": None})
        for price in (0, -1, float("inf")):
            with self.subTest(price=price):
                db.update("canvas_node_quotes", old["quote_id"], {"estimated_cost_cny": price})
                self.error("BUDGET_RESERVATION_UNAVAILABLE", lambda: self.generate(new, "new-image", "new-image"))
                self.assertEqual(len(db.query("SELECT * FROM tasks")), 1)
                self.assertIsNone(db.get("canvas_node_quotes", new["quote_id"])["consumed_at"])

    def test_mismatched_frozen_reservation_requires_reconciliation(self):
        self.create_node("old-video", "text_to_video")
        old = service.quote_node(self.project, "old-video")
        execution = self.generate(old)
        params = db.loads(db.get("tasks", execution["task_id"])["params_json"])
        params["duration"] = 10
        db.update("tasks", execution["task_id"], {"params_json": db.dumps(params)})
        self.create_node("new-image")
        new = service.quote_node(self.project, "new-image")
        caught = self.error("BUDGET_RESERVATION_UNAVAILABLE", lambda: self.generate(new, "new-image", "new-image"))
        self.assertEqual(caught.task_id, execution["task_id"])

    def test_legacy_reservation_uses_recorded_lossless_duration_without_default(self):
        model = "doubao-seedance-2-0-260128"
        self.assertEqual(config.duration_policy(model)["kind"], "unavailable")
        for value in (5, 5.0, "5", 10, 10.0, "10"):
            with self.subTest(duration=value):
                cost = config.historical_video_reservation_cny(model, {
                    "duration": value, "resolution": "720p", "ratio": "9:16"})
                expected = round(config.video_token_estimate(int(value), "720p", "9:16") / 1e6 * 46, 4)
                self.assertEqual(cost, expected)
                self.assertGreater(cost, 0)
        for value in (None, True, 0, -1, 5.5, "5.5", float("nan"), float("inf")):
            with self.subTest(invalid_duration=value):
                self.error("BUDGET_RESERVATION_UNAVAILABLE", lambda: config.historical_video_reservation_cny(
                    model, {"duration": value, "resolution": "720p", "ratio": "9:16"}))

    def test_unpriceable_legacy_task_blocks_with_budget_error_and_no_new_task(self):
        self.create_node()
        new = service.quote_node(self.project, "image")
        db.insert("tasks", {"id": "legacy-unknown", "kind": "video", "status": "failed",
            "model_id": "doubao-seedance-2-0-260128", "error_code": "CREATE_RESULT_UNKNOWN",
            "params_json": db.dumps({})})
        for params in ({"duration": 5}, {"duration": 5, "resolution": "unknown", "ratio": "9:16"},
                       {"duration": 5, "resolution": "720p", "ratio": "invalid"},
                       {"duration": 5, "resolution": "720p", "ratio": "9:16", "generate_audio": True}):
            with self.subTest(params=params):
                db.update("tasks", "legacy-unknown", {"params_json": db.dumps(params)})
                caught = self.error("BUDGET_RESERVATION_UNAVAILABLE", lambda: self.generate(new))
                self.assertEqual(caught.task_id, "legacy-unknown")
                self.assertEqual(len(db.query("SELECT * FROM tasks")), 1)
                self.assertIsNone(db.get("canvas_node_quotes", new["quote_id"])["consumed_at"])

    def test_legacy_reservation_requires_known_positive_rate(self):
        params = {"duration": 5, "resolution": "720p", "ratio": "9:16"}
        self.error("BUDGET_RESERVATION_UNAVAILABLE", lambda: config.historical_video_reservation_cny("unknown-model", params))
        for rate in (0, -1, True, float("inf"), None):
            with self.subTest(rate=rate), patch.dict(config.VIDEO_PRICE_PER_MTOKEN, {VIDEO: rate}):
                self.error("BUDGET_RESERVATION_UNAVAILABLE", lambda: config.historical_video_reservation_cny(VIDEO, params))

    def test_new_video_cost_keeps_specific_duration_error_reasons(self):
        for value, reason in ((5.5, "not_integer"), (5.0, "not_integer"), ("5", "not_integer"),
                              (True, "not_integer"), (1, "out_of_range"), (13, "out_of_range")):
            with self.subTest(value=value):
                caught = self.error("MODEL_CAPABILITY_MISMATCH", lambda: config.video_cost_cny(VIDEO, {"duration": value}))
                self.assertEqual(caught.detail["reason"], reason)
        caught = self.error("MODEL_CAPABILITY_MISMATCH", lambda: config.video_cost_cny(
            "doubao-seedance-2-0-260128", {"duration": 5}))
        self.assertEqual(caught.detail["reason"], "policy_unavailable")
        self.assertIn("时长配置暂不可用", caught.message)

    def test_concurrent_repeated_request_returns_one_execution(self):
        self.create_node()
        quote = service.quote_node(self.project, "image")
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.generate(quote), range(4)))
        self.assertEqual(len({r["execution_id"] for r in results}), 1)
        self.assertEqual(sum(not r["idempotent_replay"] for r in results), 1)
        self.assertEqual(len(db.query("SELECT * FROM tasks")), 1)

    def test_concurrent_different_quotes_same_node_one_task(self):
        self.create_node()
        quotes = [service.quote_node(self.project, "image") for _ in range(4)]
        def submit(index):
            try:
                return self.generate(quotes[index], str(index))["execution_id"]
            except AppError as exc:
                return exc.code
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(submit, range(4)))
        self.assertEqual(results.count("NODE_EXECUTION_ACTIVE"), 3)
        self.assertEqual(len(db.query("SELECT * FROM tasks")), 1)

    def test_config_write_cannot_change_frozen_input_during_submission(self):
        cfg = self.create_node()
        quote = service.quote_node(self.project, "image")
        entered, release, write_attempted, write_finished = (threading.Event() for _ in range(4))
        freeze = service._freeze
        def paused_freeze(node):
            entered.set()
            self.assertTrue(release.wait(5), "test submission was not released")
            return freeze(node)
        def change_config():
            write_attempted.set()
            canvas.patch_canvas(self.project, {"nodes": [{"node_id": "image", "config": {**cfg, "prompt": "new prompt"}}]})
            write_finished.set()
        with patch.object(service, "_freeze", side_effect=paused_freeze), ThreadPoolExecutor(max_workers=2) as pool:
            submission = pool.submit(self.generate, quote)
            try:
                self.assertTrue(entered.wait(5))
                update = pool.submit(change_config)
                self.assertTrue(write_attempted.wait(5))
                self.assertFalse(write_finished.wait(.1))
            finally:
                release.set()
            execution = submission.result(timeout=5)
            update.result(timeout=5)
        task = db.get("tasks", execution["task_id"])
        self.assertEqual(db.loads(task["params_json"])["prompt"], cfg["prompt"])
        current = db.query("SELECT * FROM canvas_nodes WHERE project_id=?", (self.project,))[0]
        self.assertEqual(db.loads(current["config_json"])["prompt"], "new prompt")

    def test_status_reads_real_task_and_history_preserves_success_after_retry_failure(self):
        self.create_node()
        first = self.generate(service.quote_node(self.project, "image"))
        asset = self.asset()
        self.finish(first, asset_id=asset["id"])
        saved = service.get_last_execution(self.project, "image")
        self.assertEqual(saved["asset_id"], asset["id"])
        self.assertEqual(saved["status"], "succeeded")
        second = self.generate(service.quote_node(self.project, "image"), "second")
        self.finish(second, "failed")
        history = service.list_executions(self.project, "image")["executions"]
        self.assertEqual([item["status"] for item in history], ["failed", "succeeded"])
        self.assertEqual(history[1]["asset"]["id"], asset["id"])
        self.assertNotIn("rel_path", str(history))
        latest = service.get_last_execution(self.project, "image")
        self.assertEqual(latest["status"], "failed")
        self.assertEqual(latest["last_successful_execution"]["asset_id"], asset["id"])

    def test_missing_output_file_is_unavailable_without_changing_success_or_retrying(self):
        self.create_node()
        execution = self.generate(service.quote_node(self.project, "image"))
        asset = self.asset()
        self.finish(execution, asset_id=asset["id"])
        available = service.get_execution(self.project, "image", execution["execution_id"])
        self.assertTrue(available["asset_available"])
        self.assertEqual(available["result_status"], "available")
        original = db.get("tasks", execution["task_id"])
        storage.asset_path(asset).unlink()
        missing = service.get_execution(self.project, "image", execution["execution_id"])
        self.assertEqual(missing["status"], "succeeded")
        self.assertFalse(missing["asset_available"])
        self.assertEqual(missing["result_status"], "unavailable")
        self.assertEqual(missing["asset_error"]["reason"], "file_missing")
        self.assertEqual(missing["asset"]["id"], asset["id"])
        self.assertFalse(service.list_executions(self.project, "image")["executions"][0]["asset_available"])
        self.assertFalse(service.get_last_execution(self.project, "image")["last_successful_execution"]["asset_available"])
        self.assertEqual(db.get("tasks", execution["task_id"]), original)
        self.assertEqual(len(db.query("SELECT id FROM tasks")), 1)
        self.assertEqual(len(db.query("SELECT id FROM canvas_node_executions")), 1)
        self.assertNotIn(str(config.ASSETS), str(missing))

    def test_same_size_replacement_is_detected_and_read_restores_when_original_file_returns(self):
        self.create_node()
        execution = self.generate(service.quote_node(self.project, "image"))
        asset = self.asset()
        self.finish(execution, asset_id=asset["id"])
        path = storage.asset_path(asset)
        original = path.read_bytes()
        path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
        changed = service.get_execution(self.project, "image", execution["execution_id"])
        self.assertFalse(changed["asset_available"])
        self.assertEqual(changed["asset_error"]["code"], "OUTPUT_ASSET_CHANGED")
        self.assertEqual(changed["asset_error"]["reason"], "content_changed")
        path.write_bytes(original)
        restored = service.get_execution(self.project, "image", execution["execution_id"])
        self.assertTrue(restored["asset_available"])
        self.assertIsNone(restored["asset_error"])
        self.assertEqual(len(db.query("SELECT id FROM tasks")), 1)

    def test_missing_asset_record_and_success_without_output_are_unavailable(self):
        self.create_node()
        execution = self.generate(service.quote_node(self.project, "image"))
        self.finish(execution)
        empty = service.get_execution(self.project, "image", execution["execution_id"])
        self.assertFalse(empty["asset_available"])
        self.assertEqual(empty["asset_error"]["reason"], "output_not_recorded")
        db.update("tasks", execution["task_id"], {"output_asset_id": "missing-asset-record"})
        missing = service.get_execution(self.project, "image", execution["execution_id"])
        self.assertFalse(missing["asset_available"])
        self.assertIsNone(missing["asset"])
        self.assertEqual(missing["asset_error"]["reason"], "asset_record_missing")
        self.assertEqual(db.get("tasks", execution["task_id"])["status"], "succeeded")

    def test_pending_and_failed_without_output_keep_original_task_error(self):
        self.create_node()
        execution = self.generate(service.quote_node(self.project, "image"))
        self.assertEqual(execution["result_status"], "pending")
        self.assertFalse(execution["asset_available"])
        self.assertIsNone(execution["asset_error"])
        db.update("tasks", execution["task_id"], {"status": "failed", "error_code": "PROVIDER_UNAVAILABLE",
                                                "error_message": "fixed fixture failure"})
        failed = service.get_execution(self.project, "image", execution["execution_id"])
        self.assertEqual(failed["result_status"], "unavailable")
        self.assertIsNone(failed["asset_error"])
        self.assertEqual(failed["error"]["code"], "PROVIDER_UNAVAILABLE")

    def test_delete_keeps_tasks_and_undo_record_identity_restores_last_execution(self):
        self.create_node()
        quote = service.quote_node(self.project, "image")
        execution = self.generate(quote)
        before = db.query("SELECT * FROM canvas_nodes WHERE project_id=?", (self.project,))[0]
        removed = canvas.patch_canvas(self.project, {"delete_node_ids": ["image"]})
        self.assertEqual(service.get_execution(self.project, "image", execution["execution_id"])["status"], "queued")
        self.assertEqual(len(service.list_executions(self.project, "image")["executions"]), 1)
        from app import canvas_history
        canvas_history.undo_deletion(self.project, removed["deletion_operation_id"],
                                     {"expected_layout_revision": removed["layout_revision"]})
        restored = db.query("SELECT * FROM canvas_nodes WHERE project_id=?", (self.project,))[0]
        self.assertEqual(restored["id"], before["id"])
        self.assertEqual(service.get_last_execution(self.project, "image")["execution_id"], execution["execution_id"])
        self.assertIsNotNone(db.get("tasks", execution["task_id"]))

    def test_recreated_node_does_not_inherit_previous_representation_execution(self):
        self.create_node()
        self.generate(service.quote_node(self.project, "image"))
        canvas.patch_canvas(self.project, {"delete_node_ids": ["image"]})
        self.create_node()
        self.assertIsNone(service.get_last_execution(self.project, "image"))
        self.assertEqual(len(service.list_executions(self.project, "image")["executions"]), 1)

    def test_recreated_node_invalidates_unconsumed_quote(self):
        self.create_node()
        quote = service.quote_node(self.project, "image")
        canvas.patch_canvas(self.project, {"delete_node_ids": ["image"]})
        self.create_node()
        self.error("NODE_QUOTE_STALE", lambda: self.generate(quote))

    def test_stopped_wait_with_unsettled_remote_still_blocks_new_generation(self):
        self.create_node()
        execution = self.generate(service.quote_node(self.project, "image"))
        db.update("tasks", execution["task_id"], {"status": "canceled", "remote_task_id": "remote1", "remote_status": "running"})
        fresh = service.quote_node(self.project, "image")
        self.error("NODE_EXECUTION_ACTIVE", lambda: self.generate(fresh, "second"))
        db.update("tasks", execution["task_id"], {"remote_status": "canceled"})
        self.assertFalse(service.get_execution(self.project, "image", execution["execution_id"])["active"])
        self.generate(fresh, "second")

    def test_project_tombstone_blocks_service_and_direct_sql(self):
        self.create_node()
        quote = service.quote_node(self.project, "image")
        with db.conn() as connection:
            connection.execute("UPDATE projects SET deleted_at=? WHERE id=?", (db.now(), self.project))
        self.error("PROJECT_DELETED", lambda: self.generate(quote))
        self.error("PROJECT_DELETED", lambda: db.update("canvas_node_quotes", quote["quote_id"], {"expires_at": 0}))
        self.assertEqual(db.query("SELECT * FROM tasks"), [])


if __name__ == "__main__":
    unittest.main()
