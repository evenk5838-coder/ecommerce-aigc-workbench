"""Explicit server enablement and sample-scoped submission; no real DB/network."""
from __future__ import annotations

import json
import os
import unittest
from unittest.mock import Mock, patch

import test_node_execution as fixture
from app import aihubmix_models as catalog, cloud_tasks, config, db, main, node_execution
from app import providers_aihubmix as provider, worker


class AiHubMixActivationTests(unittest.TestCase):
    error = fixture.NodeExecutionTests.error
    asset = fixture.NodeExecutionTests.asset
    create_node = fixture.NodeExecutionTests.create_node
    generate = fixture.NodeExecutionTests.generate

    def setUp(self):
        # Freeze the pre-expansion acceptance baseline: the following tests
        # intentionally exercise candidates that did not yet have real samples.
        baseline = (catalog.FLASH, catalog.FAST)
        self.enterContext(patch.dict(catalog.SAMPLE_PROFILES,
            {mid: catalog.SAMPLE_PROFILES[mid] for mid in baseline}, clear=True))
        self.enterContext(patch.dict(catalog.SAMPLE_DETAILS,
            {mid: catalog.SAMPLE_DETAILS[mid] for mid in baseline}, clear=True))
        fixture.NodeExecutionTests.setUp(self)
        self.enterContext(patch.dict(os.environ, {"AIHUBMIX_API_KEY": "fake-test", "AIHUBMIX_USD_TO_CNY": "8",
                                                "AIHUBMIX_ENABLED_MODELS": ""}))
        self.enterContext(patch.object(config, "_load_dotenv", return_value=None))
        self.enterContext(patch.object(provider, "POLL_SECONDS", 0))
        self.enterContext(patch.object(provider, "_request", side_effect=AssertionError("network forbidden")))

    def enable(self, value=None):
        self.enterContext(patch.dict(os.environ, {"AIHUBMIX_ENABLED_MODELS": value if value is not None else
                                                  catalog.FLASH + "," + catalog.FAST}))

    def video_params(self, **changes):
        return {"prompt": "complete cup", "generation_mode": "text_to_video", "duration": 5,
                "ratio": "16:9", "resolution": "480p", "generate_audio": False, **changes}

    def body(self, model, params, reference=None):
        return main.TaskIn(kind=catalog.MODELS[model]["kind"], model_id=model,
                           params=params, input_asset_id=reference)

    def test_key_and_manual_activation_dictionary_cannot_enable_a_model(self):
        with patch.dict(config.MODEL_ACTIVATION, {catalog.FLASH: "verified", catalog.FAST: "verified"}):
            for mid in (catalog.FLASH, catalog.FAST):
                self.assertFalse(config.model_submittability(mid)["submittable"])
        status = main.aihubmix_status()
        self.assertTrue(status["configured"])
        self.assertFalse(status["any_submittable"])
        self.assertEqual(status["enabled_models"], [])
        self.assertEqual(set(status["sample_verified_models"]), {catalog.FLASH, catalog.FAST})
        self.assertNotIn("fake-test", json.dumps(status))
        self.error("MODEL_NOT_VERIFIED", lambda: main.create_task(
            self.body(catalog.FLASH, {"prompt": "cup"})))
        self.assertEqual(db.query("SELECT * FROM tasks"), [])

    def test_explicit_allowlist_and_exchange_bound_enable_only_sample_models(self):
        self.enable()
        for mid in (catalog.FLASH, catalog.FAST):
            state = config.model_submittability(mid)
            self.assertTrue(state["submittable"], state)
            self.assertTrue(state["operational_enabled"])
            self.assertTrue(state["sample_generation_verified"])
        self.assertFalse(config.model_submittability(catalog.MINI)["submittable"])
        status = main.aihubmix_status()
        self.assertEqual(set(status["available_models"]), {catalog.FLASH, catalog.FAST})
        self.assertTrue(status["any_submittable"])
        self.assertFalse(status["generation_verified"], "The retained all-model flag includes unverified Mini")

    def test_allowlist_accepts_exact_upstream_aliases_and_deduplicates(self):
        self.enable(" " + catalog.FLASH.removeprefix(catalog.PREFIX) + "," + catalog.FLASH)
        self.assertEqual(catalog.enabled_models_config(), {"valid": True, "models": [catalog.FLASH]})
        self.assertTrue(config.model_submittability(catalog.FLASH)["submittable"])
        self.assertFalse(config.model_submittability(catalog.FAST)["submittable"])

    def test_enabled_and_priced_model_without_a_key_is_not_submittable(self):
        self.enable(catalog.FLASH)
        with patch.dict(os.environ, {"AIHUBMIX_API_KEY": " "}):
            state = config.model_submittability(catalog.FLASH)
            self.assertFalse(state["submittable"])
            self.assertIn("AUTH_MISSING", state["reasons"])
            estimate = main.estimate_task(self.body(catalog.FLASH, {"prompt": "cup"}))
            self.assertFalse(estimate["submittable"])
            self.assertEqual(estimate["estimated_cost_cny"], .1488)
            self.error("AUTH_MISSING", lambda: main.create_task(self.body(catalog.FLASH, {"prompt": "cup"})))
        self.assertEqual(db.query("SELECT * FROM tasks"), [])

    def test_unverified_or_unknown_allowlist_entry_closes_every_model_without_echo(self):
        for value in (catalog.MINI, catalog.FLASH + ",unrecognized-private-value", "*", catalog.FAST + ","):
            with self.subTest(value=value), patch.dict(os.environ, {"AIHUBMIX_ENABLED_MODELS": value}):
                self.assertEqual(catalog.enabled_models_config(), {"valid": False, "models": []})
                self.assertTrue(all(not config.model_submittability(mid)["submittable"] for mid in catalog.MODELS))
                status = main.aihubmix_status()
                self.assertFalse(status["enabled_models_config_valid"])
                self.assertNotIn("unrecognized-private-value", json.dumps(status))

    def test_enabled_model_without_exchange_bound_remains_blocked_and_truthful(self):
        self.enable(catalog.FLASH)
        with patch.dict(os.environ, {"AIHUBMIX_USD_TO_CNY": "0"}):
            state = config.model_submittability(catalog.FLASH)
            self.assertFalse(state["submittable"])
            self.assertIn("MODEL_PRICE_UNCONFIRMED", state["reasons"])
            self.assertTrue(state["operational_enabled"])
            self.assertTrue(state["sample_generation_verified"])
            self.assertEqual(config.model_note(catalog.FLASH), "费用暂未就绪")
            self.assertEqual(config.model_pricing(catalog.FLASH)["status"], "awaiting_exchange_bound")
            self.error("MODEL_PRICE_UNCONFIRMED", lambda: main.create_task(self.body(catalog.FLASH, {"prompt": "cup"})))
        self.assertEqual(db.query("SELECT * FROM tasks"), [])

    def test_public_state_separates_vendor_capabilities_profiles_and_visual_limits(self):
        caps = config.public_model_capabilities(catalog.FAST)
        self.assertEqual(caps["resolutions"], ["480p"])
        self.assertEqual(caps["duration_policy"]["allowed"], [5])
        self.assertIn("720p", caps["vendor_capabilities"]["resolutions"])
        self.assertEqual(config.duration_policy(catalog.FAST)["kind"], "range")
        self.assertEqual(config.duration_policy(catalog.FAST)["max"], 15)
        self.assertEqual(len(caps["operational_profiles"]), 2)
        self.assertTrue(caps["validation"]["contract_verified"])
        self.assertFalse(caps["validation"]["operational_enabled"])
        self.assertIn("no-watermark-edge-extend-v2", caps["validation"]["tested_output_policy_versions"])
        self.assertTrue(any("接痕" in item for item in caps["validation"]["limitations"]))
        listing = {m["id"]: m for m in main.models()["items"]}
        self.assertEqual(listing[catalog.FLASH]["note"], "暂未开放")
        self.enable()
        listing = {m["id"]: m for m in main.models()["items"]}
        self.assertEqual(listing[catalog.FLASH]["pricing"]["status"], "ready_estimate")
        self.assertNotIn("待真实验证", listing[catalog.FLASH]["note"])

    def test_all_four_sample_modes_resolve_without_writing_a_task(self):
        self.enable()
        reference = self.asset()["id"]
        for mode in ("text_to_image", "image_to_image", "text_to_video", "image_to_video"):
            image = mode.endswith("image")
            mid = catalog.FLASH if image else catalog.FAST
            params = {"prompt": "cup", "generation_mode": mode, "size": "1024x1024", "n": 1} if image else {
                **self.video_params(), "generation_mode": mode}
            resolved = main.resolve_task(self.body(mid, params, reference if mode.startswith("image_") else None))
            self.assertTrue(resolved["submittable"])
        self.assertEqual(db.query("SELECT * FROM tasks"), [])

    def test_application_video_defaults_match_the_exposed_sample_and_are_persisted(self):
        self.enable(catalog.FAST)
        resolved = main.resolve_task(self.body(catalog.FAST, {"prompt": "cup", "generation_mode": "text_to_video"}))
        for key, expected in (("duration", 5), ("ratio", "16:9"), ("resolution", "480p"), ("generate_audio", False)):
            self.assertEqual(resolved["params"][key], expected)
        native = provider.build_body("video", catalog.FAST, resolved["params"])
        self.assertEqual(native["aspect_ratio"], "16:9")
        self.assertEqual(native["resolution"], "480p")

    def test_native_missing_spec_is_not_misrepresented_as_an_operational_default(self):
        self.error("MODEL_CAPABILITY_MISMATCH", lambda: provider.build_body(
            "video", catalog.FAST, {"prompt": "cup", "duration": 5, "generation_mode": "text_to_video"}))

    def test_explicit_unverified_specs_and_multiple_images_are_rejected_before_quote_or_send(self):
        self.enable()
        for change in ({"duration": 10}, {"resolution": "720p"}, {"ratio": "9:16"}, {"generate_audio": True},
                       {"duration": 5.0}, {"resolution": None}):
            with self.subTest(change=change):
                self.error("MODEL_CAPABILITY_MISMATCH", lambda: main.estimate_task(
                    self.body(catalog.FAST, self.video_params(**change))))
                with patch.object(provider, "_request") as send:
                    self.error("MODEL_CAPABILITY_MISMATCH", lambda: provider.media_create(
                        "video", catalog.FAST, self.video_params(**change)))
                    send.assert_not_called()
        for change in ({"size": "2048x2048"}, {"ratio": "16:9"}, {"n": 2}, {"n": True}):
            with self.subTest(change=change):
                self.error("MODEL_CAPABILITY_MISMATCH", lambda: main.create_task(
                    self.body(catalog.FLASH, {"prompt": "cup", "size": "1024x1024", **change})))
        self.assertEqual(db.query("SELECT * FROM tasks"), [])

    def test_allowlist_is_rechecked_at_node_confirmation_and_unsent_worker(self):
        self.enable()
        self.create_node("video", "text_to_video", model_id=catalog.FAST,
                         params={k: v for k, v in self.video_params().items() if k not in ("prompt", "generation_mode")})
        quote = node_execution.quote_node(self.project, "video")
        with patch.dict(os.environ, {"AIHUBMIX_ENABLED_MODELS": ""}):
            self.error("MODEL_NOT_VERIFIED", lambda: self.generate(quote, node_id="video"))
        execution = self.generate(quote, node_id="video")
        with patch.dict(os.environ, {"AIHUBMIX_ENABLED_MODELS": ""}), patch.object(provider, "media_create") as send:
            worker.execute(worker.claim_next())
            send.assert_not_called()
        self.assertEqual(db.get("tasks", execution["task_id"])["error_code"], "MODEL_NOT_VERIFIED")

    def test_vendor_rates_and_legacy_duration_are_retained_for_old_remote_recovery(self):
        self.enable()
        task = main.create_task(self.body(catalog.FAST, self.video_params()))
        old_params = self.video_params(duration=10.0, resolution="720p")
        db.update("tasks", task["id"], {
            "remote_task_id": "original-remote", "params_json": db.dumps(old_params),
            "usage_json": None,  # Historical task created before frozen direct-task reservations.
        })
        self.assertEqual(config.resolve_duration_s(catalog.FAST, 10.0, strict=True, legacy=True), 10)
        self.assertEqual(cloud_tasks.estimate_cost("video", catalog.FAST, old_params, legacy=True), 9.04)
        with patch.dict(os.environ, {"AIHUBMIX_ENABLED_MODELS": ""}), \
             patch.object(provider, "media_create") as create, \
             patch.object(provider, "media_query", return_value={"status": "succeeded", "usage": {}, "output": []}), \
             patch.object(provider, "download_result", return_value=b"original-video"):
            result = provider.run_media(task["id"], catalog.FAST, old_params, Mock())
        create.assert_not_called()
        self.assertEqual(result["remote_task_id"], "original-remote")
        self.assertEqual(result["cost_cny"], 9.04)

    def test_existing_ark_defaults_and_permission_are_unchanged(self):
        self.assertTrue(config.model_submittability(fixture.IMAGE)["submittable"])
        self.assertEqual(config.model_activation(fixture.IMAGE), "verified")
        resolved = main.resolve_task(main.TaskIn(kind="video", model_id=fixture.VIDEO,
                                                params={"prompt": "cup", "generation_mode": "text_to_video"}))
        self.assertEqual(resolved["params"]["ratio"], "9:16")
        self.assertEqual(resolved["params"]["resolution"], "720p")
