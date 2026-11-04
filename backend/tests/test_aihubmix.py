"""AiHubMix contracts on disposable databases; every transport is blocked."""
from __future__ import annotations

import io
import json
import os
import unittest
import urllib.error
from unittest.mock import Mock, patch

import test_node_execution as fixture
from app import aihubmix_models as catalog, cloud_tasks, config, db, main
from app import node_execution, providers_aihubmix as provider, storage, worker
from app.errors import AppError, err


class AiHubMixTests(unittest.TestCase):
    error = fixture.NodeExecutionTests.error
    asset = fixture.NodeExecutionTests.asset
    create_node = fixture.NodeExecutionTests.create_node
    generate = fixture.NodeExecutionTests.generate

    def setUp(self):
        fixture.NodeExecutionTests.setUp(self)
        self.enterContext(patch.dict(os.environ, {"AIHUBMIX_API_KEY": "fake-test", "AIHUBMIX_USD_TO_CNY": "8",
                                                 "AIHUBMIX_ENABLED_MODELS": ""}))
        self.enterContext(patch.object(config, "_load_dotenv", return_value=None))
        self.enterContext(patch.object(provider, "POLL_SECONDS", 0))

    def enable(self):
        self.enterContext(patch.dict(os.environ, {"AIHUBMIX_ENABLED_MODELS": f"{catalog.FLASH},{catalog.FAST}"}))
        self.enterContext(patch.dict(config.MODEL_ACTIVATION, {catalog.FLASH: "verified", catalog.FAST: "verified"}))

    def enqueue(self, mode="text_to_image", node="image"):
        self.enable()
        model = catalog.FLASH if mode.endswith("image") else catalog.FAST
        self.create_node(node, mode, model_id=model, params={"size": "1024x1024", "ratio": "1:1"}
                         if mode.endswith("image") else {"duration": 5, "resolution": "480p",
                                                         "ratio": "16:9", "generate_audio": False})
        quote = node_execution.quote_node(self.project, node, {})
        return self.generate(quote, node_id=node)

    def test_key_does_not_enable_models_and_submission_creates_nothing(self):
        status = main.aihubmix_status()
        self.assertTrue(status["configured"])
        self.assertFalse(status["generation_verified"])
        self.assertNotIn("fake-test", json.dumps(status))
        for mid in catalog.MODELS:
            ready = config.model_submittability(mid)
            self.assertFalse(ready["submittable"])
            self.assertIn("MODEL_NOT_VERIFIED", ready["reasons"])
        self.error("MODEL_NOT_VERIFIED", lambda: main.create_task(main.TaskIn(kind="image", model_id=catalog.FLASH,
                    params={"prompt": "cup"})))
        self.assertEqual(db.query("SELECT * FROM tasks"), [])

    def test_provider_ids_are_distinct_from_verified_ark(self):
        listing = {m["id"]: m for m in main.models()["items"]}
        self.assertEqual(listing[catalog.FLASH]["provider"], "aihubmix")
        self.assertFalse(listing[catalog.FLASH]["submittability"]["submittable"])
        self.assertEqual(config.MODEL_ACTIVATION[fixture.IMAGE], "verified")
        self.assertEqual(listing[catalog.FLASH]["weights_mb"], 0)

    def test_price_uses_provider_rates_and_never_ark_fallback(self):
        self.assertEqual(cloud_tasks.estimate_cost("image", catalog.FLASH, {}), .1488)
        p = {"duration": 5, "resolution": "720p"}
        self.assertEqual(cloud_tasks.estimate_cost("video", catalog.FAST, p), 4.52)
        self.assertEqual(config.video_cost_cny(catalog.FAST, p), 4.52)
        self.assertEqual(cloud_tasks.estimate_cost("video", catalog.MINI, p), 4.52)
        for value in ("0", "nan", "Infinity", "bad", "-1"):
            with patch.dict(os.environ, {"AIHUBMIX_USD_TO_CNY": value}):
                self.error("MODEL_PRICE_UNCONFIRMED", lambda: cloud_tasks.estimate_cost("image", catalog.FLASH, {}))

    def test_four_modes_use_correct_native_fields_and_no_reference_in_text_modes(self):
        for kind in ("image", "video"):
            for source in ("text", "image"):
                mode = f"{source}_to_{kind}"
                params = {"prompt": "cup", "generation_mode": mode,
                          "size": "1024x1024", "duration": 5,
                          "ratio": "1:1" if kind == "image" else "16:9", "resolution": "480p", "generate_audio": False}
                body = provider.build_body(kind, catalog.FLASH if kind == "image" else catalog.FAST,
                                           params, storage.asset_data_url(storage.load_asset(self.asset()["id"])))
                self.assertNotIn("input_asset_id", body)
                if kind == "image":
                    self.assertEqual("image" in body, source == "image")
                    self.assertIs(body["async"], True)
                    self.assertNotIn("aspect_ratio", body)
                else:
                    self.assertEqual("frame_images" in body, source == "image")
                    self.assertEqual(body["aspect_ratio"], "16:9")
                    self.assertNotIn("size", body)
                    self.assertNotIn("content", body)
                    self.assertEqual(body["model"], "doubao-seedance-2-0-fast-260128")

    def test_reference_and_parameter_errors_happen_before_create(self):
        with patch.object(provider, "_request") as request:
            self.error("REFERENCE_IMAGE_REQUIRED", lambda: provider.media_create("image", catalog.FLASH,
                {"prompt": "cup", "generation_mode": "image_to_image"}))
            for changes in ({"duration": "5"}, {"duration": 5.0}, {"duration": True}, {"duration": 3},
                            {"resolution": "1080p"}, {"seed": 12}, {"generate_audio": 1}):
                self.error("MODEL_CAPABILITY_MISMATCH", lambda: provider.media_create("video", catalog.FAST,
                    {"prompt": "cup", "duration": 5, "generation_mode": "text_to_video", **changes}))
            request.assert_not_called()

    def test_ambiguous_creation_is_single_attempt_and_never_echoes_raw_error(self):
        self.enable()
        for failure in (err("PROVIDER_UNAVAILABLE", "network"),):
            with patch.object(provider, "_request", side_effect=failure) as request:
                self.error("CREATE_RESULT_UNKNOWN", lambda: provider.media_create("image", catalog.FLASH, {"prompt": "cup"}))
                self.assertEqual(request.call_count, 1)
        with patch.object(provider, "_request", return_value={"id": "../invalid"}):
            self.error("CREATE_RESULT_UNKNOWN", lambda: provider.media_create("image", catalog.FLASH, {"prompt": "cup"}))

    def test_http_error_classification_redacts_vendor_message(self):
        for status, code, expected in ((403, "async_not_enabled", "PROVIDER_ASYNC_NOT_ENABLED"),
                (401, "invalid", "AUTH_FAILED"), (402, "quota", "PROVIDER_INSUFFICIENT_BALANCE"),
                (429, "rate", "PROVIDER_RATE_LIMITED"), (400, "bad", "PROVIDER_BAD_REQUEST"),
                (503, "oops", "PROVIDER_UNAVAILABLE")):
            exc = urllib.error.HTTPError("https://aihubmix.com", status, "secret", {},
                io.BytesIO(json.dumps({"error": {"code": code, "message": "fake-test private prompt"}}).encode()))
            with patch("urllib.request.build_opener") as build:
                build.return_value.open.side_effect = exc
                result = self.error(expected, lambda: provider._request("GET", "/v1/models"))
                self.assertNotIn("fake-test", str(result.to_dict()))
                self.assertNotIn("private prompt", str(result.to_dict()))

    def test_download_does_not_forward_key_to_untrusted_or_other_task_url(self):
        for url in ("http://aihubmix.com/ai/v1/videos/job/content", "https://evil.test/file",
                    "https://aihubmix.com@evil.test/file", "https://aihubmix.com/ai/v1/videos/other/content",
                    "https://aihubmix.com/ai/v1/videos/job/content?key=private"):
            with patch.object(provider, "_request") as request:
                self.error("DOWNLOAD_FAILED", lambda: provider.download_result("video", "job", [{"content_url": url}]))
                request.assert_not_called()
        self.assertIsNone(provider._NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.test"))

    def test_worker_persists_remote_id_before_query_and_writes_real_provider(self):
        execution = self.enqueue("image_to_image")
        from PIL import Image
        output = io.BytesIO()
        Image.new("RGB", (36, 28), "yellow").save(output, "PNG")
        picture = output.getvalue()
        def transport(method, path, body=None, **kwargs):
            if method == "POST":
                self.assertTrue(body["image"].startswith("data:image/png;base64,"))
                return {"id": "remote-image"}
            self.assertEqual(db.get("tasks", execution["task_id"])["remote_task_id"], "remote-image")
            if kwargs.get("binary"):
                return picture
            return {"id": "remote-image", "object": "image", "status": "completed", "output": [
                {"content_url": "https://aihubmix.com/ai/v1/images/remote-image/content/result"}]}
        with patch.object(provider, "_request", side_effect=transport):
            worker.execute(worker.claim_next())
        task = db.get("tasks", execution["task_id"])
        self.assertEqual(task["status"], "succeeded", task["error_message"])
        self.assertEqual(task["provider"], "aihubmix")
        self.assertEqual(task["cost_cny"], .1488)
        self.assertEqual(db.loads(storage.load_asset(task["output_asset_id"])["meta_json"])["provider"], "aihubmix")

    def test_restart_and_manual_resume_query_same_id_with_frozen_price(self):
        execution = self.enqueue()
        tid = execution["task_id"]
        db.update("tasks", tid, {"status": "running", "remote_task_id": "existing", "remote_status": "running"})
        self.assertGreaterEqual(worker.reconcile()["queued"], 1)
        with patch.dict(os.environ, {"AIHUBMIX_USD_TO_CNY": "0"}), patch.object(provider, "media_create") as create, \
             patch.object(provider, "media_query", return_value={"status": "succeeded", "usage": {}, "output": []}), \
             patch.object(provider, "download_result", return_value=storage.asset_path(self.asset()).read_bytes()):
            worker.execute(worker.claim_next())
            create.assert_not_called()
        self.assertEqual(db.get("tasks", tid)["cost_cny"], .1488)
        db.update("tasks", tid, {"status": "failed", "error_code": "DOWNLOAD_FAILED"})
        self.assertTrue(main.retry_task(tid)["resumed"])
        self.assertEqual(len(db.query("SELECT * FROM tasks")), 1)

    def test_remote_video_keeps_original_preparation_policy_after_upgrade(self):
        from app import media_policy
        for index, saved in enumerate(({}, {"output_policy": {"version": "old-video-policy", "watermark": False},
                "prepared_reference": {"policy": "old-video-policy", "prepared_sha256": "old-reference"}})):
            with self.subTest(saved=saved):
                self.enable()
                name = f"video-{index}"
                self.create_node(name, "image_to_video", model_id=catalog.FAST,
                                 params={"duration":5,"resolution":"480p","ratio":"16:9","generate_audio":False})
                quote = node_execution.quote_node(self.project, name)
                execution = self.generate(quote, request=f"recovery-{index}", node_id=name)
                tid = execution["task_id"]
                db.update("tasks", tid, {"remote_task_id": f"existing-{index}", "usage_json": db.dumps(saved)})
                task = db.get("tasks", tid)
                with patch.object(media_policy, "specification", side_effect=AssertionError("must keep original policy")), \
                     patch.object(provider, "build_body", side_effect=AssertionError("must not prepare again")), \
                     patch.object(provider, "media_create") as create, \
                     patch.object(provider, "media_query", return_value={"status": "succeeded", "usage": {}, "output": []}), \
                     patch.object(provider, "download_result", return_value=b"original-video"):
                    result = provider.run_media(tid, catalog.FAST, db.loads(task["params_json"]), Mock())
                create.assert_not_called()
                self.assertEqual(result["usage"].get("output_policy"), saved.get("output_policy"))
                self.assertEqual(result["usage"].get("prepared_reference"), saved.get("prepared_reference"))

    def test_unknown_create_keeps_budget_reserved_and_blocks_retry(self):
        execution = self.enqueue()
        with patch.object(provider, "media_create", side_effect=err("CREATE_RESULT_UNKNOWN", "unknown")):
            worker.execute(worker.claim_next())
        task = db.get("tasks", execution["task_id"])
        self.assertEqual(task["error_code"], "CREATE_RESULT_UNKNOWN")
        self.assertTrue(node_execution._active(task))
        self.error("CREATE_RESULT_UNKNOWN", lambda: main.retry_task(task["id"]))
        with db.conn() as connection, patch.object(cloud_tasks, "budget_gate") as gate:
            node_execution._budget_in_tx(connection, 1)
            gate.assert_called_once_with(1.1488)

    def test_stopping_local_wait_does_not_cancel_remote_and_stays_reserved(self):
        execution = self.enqueue("text_to_video", "video")
        tid = execution["task_id"]
        db.update("tasks", tid, {"status": "running", "remote_task_id": "video-job", "remote_status": "running"})
        with patch("app.providers_ark.video_cancel") as ark_cancel:
            result = main.cancel_task(tid)
            self.assertFalse(result["cloud_canceled"])
            ark_cancel.assert_not_called()
        worker.TOKENS.pop(tid, None)
        self.assertTrue(node_execution._active(db.get("tasks", tid)))

    def test_failed_download_keeps_successful_remote_charge_reserved(self):
        execution = self.enqueue()
        tid = execution["task_id"]
        db.update("tasks", tid, {"status": "failed", "remote_task_id": "job", "remote_status": "succeeded"})
        self.assertFalse(node_execution._active(db.get("tasks", tid)))
        with db.conn() as connection, patch.object(cloud_tasks, "budget_gate") as gate:
            node_execution._budget_in_tx(connection, 1)
            gate.assert_called_once_with(1.1488)

    def test_pending_verification_is_rechecked_in_worker_before_post(self):
        execution = self.enqueue()
        with patch.dict(os.environ, {"AIHUBMIX_ENABLED_MODELS": ""}), \
             patch.object(provider, "media_create") as create:
            worker.execute(worker.claim_next())
            create.assert_not_called()
        self.assertEqual(db.get("tasks", execution["task_id"])["error_code"], "MODEL_NOT_VERIFIED")


if __name__ == "__main__":
    unittest.main()
