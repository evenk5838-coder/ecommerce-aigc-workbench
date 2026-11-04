"""Current retained FLUX/H3/Wan sample scopes, without generation or network.

These tests read real catalog acceptance records, never invent sample evidence.
The enablement, exchange bound, assets and database are disposable test fixtures.
"""
import os
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch

import test_node_execution as fixture
from app import aihubmix_audio as audio, aihubmix_models as catalog, config, db, main
from app import aihubmix_extra_images as images, aihubmix_extra_videos as videos
from app import node_execution, storage, worker
from app import providers_aihubmix as provider

ACCEPTED = (catalog.FLASH, catalog.FAST, catalog.QWEN, catalog.MINI,
            images.FLUX, videos.MINIMAX_H3, videos.WAN_3)
NEW_MODES = ((images.FLUX, "text_to_image", None, .48),
             (videos.MINIMAX_H3, "text_to_video", "768P", 3.0976),
             (videos.MINIMAX_H3, "image_to_video", "768P", 3.3232),
             (videos.WAN_3, "text_to_video", "480p", 1.69),
             (videos.WAN_3, "image_to_video", "480p", 1.69))


class AiHubMixMultiAcceptanceTests(unittest.TestCase):
    error = fixture.NodeExecutionTests.error
    asset = fixture.NodeExecutionTests.asset
    create_node = fixture.NodeExecutionTests.create_node

    def setUp(self):
        fixture.NodeExecutionTests.setUp(self)
        self.enterContext(patch.dict(os.environ, {"AIHUBMIX_API_KEY": "fixture-unused",
            "AIHUBMIX_USD_TO_CNY": "8", "AIHUBMIX_ENABLED_MODELS": ",".join(ACCEPTED)}))
        self.enterContext(patch.object(config, "_load_dotenv", return_value=None))
        self.enterContext(patch.object(config, "DATA", Path(self.tmp.name)))
        self.send = self.enterContext(patch.object(provider, "_request", side_effect=AssertionError("network forbidden")))
        self.enterContext(patch.object(provider, "media_create", side_effect=AssertionError("generation forbidden")))

    def body(self, model, mode, default_resolution=None, **changes):
        kind = catalog.MODELS[model]["kind"]
        params = {"prompt": "one complete fictional white cup", "generation_mode": mode,
                  **({"size": "1024x1024", "ratio": "1:1"} if kind == "image" else
                     {"duration": 5, "resolution": default_resolution, "ratio": "16:9", "generate_audio": False}),
                  **changes}
        reference = self.asset()["id"] if mode.startswith("image_") else None
        return main.TaskIn(kind=kind, model_id=model, params=params, input_asset_id=reference)

    def quote(self, model, mode, resolution=None, suffix=""):
        body = self.body(model, mode, resolution)
        node = model.replace(":", "-") + "-" + mode + suffix
        settings = {key: value for key, value in body.params.items() if key not in ("prompt", "generation_mode")}
        cfg = self.create_node(node, mode, model_id=model, params=settings, prompt=body.params["prompt"],
                               input_asset_id=body.input_asset_id)
        quote = node_execution.quote_node(self.project, node)
        return quote, cfg

    def assert_no_generation(self):
        self.assertEqual(db.query("SELECT * FROM tasks"), [])
        self.send.assert_not_called()
        provider.media_create.assert_not_called()

    def test_retained_new_sample_profiles_are_the_exact_five_accepted_modes(self):
        self.assertEqual(catalog.operational_profiles(images.FLUX), [
            {"generation_mode": ["text_to_image"], "ratio": ["1:1"], "size": ["1024x1024"]}])
        for model, resolution in ((videos.MINIMAX_H3, "768P"), (videos.WAN_3, "480p")):
            self.assertEqual(catalog.operational_profiles(model), [
                {"generation_mode": [mode], "duration": [5], "ratio": ["16:9"],
                 "resolution": [resolution], "generate_audio": [False]}
                for mode in ("text_to_video", "image_to_video")])
        for model in ACCEPTED:
            with self.subTest(model=model):
                self.assertIn(model, catalog.SAMPLE_PROFILES)
                self.assertTrue(catalog.SAMPLE_DETAILS[model]["evidence"])
                self.assertTrue(catalog.validation_info(model)["sample_generation_verified"])
                self.assertFalse(catalog.validation_info(model)["supplier_bill_verified"])
        self.assert_no_generation()

    def test_explicit_allowlist_opens_seven_and_keeps_untested_models_unselectable(self):
        enabled = catalog.enabled_models_config()
        self.assertTrue(enabled["valid"])
        self.assertEqual(set(enabled["models"]), set(ACCEPTED))
        rows = main.models()["items"]
        selectable = {row["id"] for row in rows if row["provider"] == "aihubmix"
                      and row["submittability"]["submittable"]}
        self.assertEqual(selectable, set(ACCEPTED))
        for model in (catalog.NANO, catalog.GPT_IMAGE, images.WAN, videos.SEEDANCE_25, videos.KLING_V3):
            with self.subTest(model=model):
                self.assertFalse(config.model_submittability(model)["submittable"])
                caps = config.public_model_capabilities(model)
                self.assertEqual(caps["input_modes"], [])
                self.assertEqual(caps["operational_profiles"], [])
                self.assertFalse(caps["validation"]["sample_generation_verified"])
        # An accepted sample still needs explicit operator enablement.
        with patch.dict(os.environ, {"AIHUBMIX_ENABLED_MODELS": ""}):
            for model in ACCEPTED:
                self.assertFalse(config.model_submittability(model)["submittable"])
        self.assert_no_generation()

    def test_flux_image_editing_and_unaccepted_specs_remain_closed(self):
        caps = config.public_model_capabilities(images.FLUX)
        self.assertEqual(caps["input_modes"], ["text_to_image"])
        self.assertEqual(caps["sizes"], ["1024x1024"])
        self.error("MODEL_CAPABILITY_MISMATCH", lambda: main.resolve_task(
            self.body(images.FLUX, "image_to_image")))
        self.error("MODEL_PRICE_UNCONFIRMED", lambda: catalog.estimate_cost("image", images.FLUX,
            {"generation_mode": "image_to_image", "size": "1024x1024"}))
        self.error("MODEL_CAPABILITY_MISMATCH", lambda: main.resolve_task(
            self.body(images.FLUX, "text_to_image", size="2048x2048")))
        for model, resolution in ((videos.MINIMAX_H3, "768P"), (videos.WAN_3, "480p")):
            for changes in ({"duration": 10}, {"resolution": "720p"}, {"ratio": "9:16"}, {"generate_audio": True}):
                with self.subTest(model=model, changes=changes):
                    self.error("MODEL_CAPABILITY_MISMATCH", lambda: main.resolve_task(
                        self.body(model, "text_to_video", resolution, **changes)))
        self.assert_no_generation()

    def test_all_five_quotes_freeze_exact_accepted_parameters_and_conservative_prices(self):
        for model, mode, resolution, price in NEW_MODES:
            with self.subTest(model=model, mode=mode):
                submitted = self.body(model, mode, resolution)
                resolved = main.resolve_task(submitted)
                self.assertTrue(resolved["submittable"])
                self.assertEqual(resolved["estimated_cost_cny"], price)
                quote, cfg = self.quote(model, mode, resolution)
                self.assertEqual(quote["estimated_cost_cny"], price)
                frozen = quote["input"]
                self.assertEqual(frozen["params"]["generation_mode"], mode)
                self.assertEqual(frozen["output_policy"], provider.output_policy(submitted.kind, model))
                self.assertFalse(quote["side_effects"]["model_called"])
                if mode.startswith("image_"):
                    self.assertEqual(frozen["input_asset_id"], cfg["input_asset_id"])
                    self.assertEqual(len(frozen["reference"]["sha256"]), 64)
                    reference = storage.asset_data_url(storage.load_asset(cfg["input_asset_id"]))
                else:
                    self.assertIsNone(frozen["reference"])
                    reference = None
                body = provider.build_body(submitted.kind, model, frozen["params"], reference)
                if resolution:
                    self.assertEqual((body["duration"], body["aspect_ratio"], body["resolution"]), (5, "16:9", resolution))
                else:
                    self.assertEqual((body["size"], body["n"]), ("1024x1024", 1))
        h3_price = config.public_model_capabilities(videos.MINIMAX_H3)["pricing"]
        self.assertTrue(h3_price["conservative_upper_estimate"])
        self.assertTrue(h3_price["estimate_only"])
        self.assertEqual(h3_price["pricing_discrepancy"]["policy"], "reserve_first_reference_image_until_bill_verified")
        self.assert_no_generation()

    def test_h3_api_and_frozen_policy_version_local_silence_without_claiming_native_control(self):
        row = next(row for row in main.models()["items"] if row["id"] == videos.MINIMAX_H3)
        caps = row["capabilities"]
        expected = provider.output_policy("video", videos.MINIMAX_H3)
        self.assertEqual(caps["output_policy"], expected)
        policy = expected["audio_processing"]
        self.assertEqual(policy["version"], "h3-video-only-v1")
        self.assertEqual(policy["silent_delivery"], "local_video_stream_copy")
        self.assertTrue(policy["preserve_native_original"])
        self.assertFalse(policy["native_audio_switch"])
        self.assertFalse(policy["reencode_video"])
        native = caps["vendor_capabilities"]["limits"]
        self.assertEqual(native["audio_control"], "not_exposed")
        self.assertFalse(native["native_silent_output_verified"])
        validation = caps["validation"]
        self.assertEqual(validation["tested_audio_processing_version"], audio.VERSION)
        self.assertEqual(validation["current_audio_processing_version"], audio.VERSION)
        self.assertTrue(validation["current_audio_processing_sample_verified"])
        for mode in ("text_to_video", "image_to_video"):
            quote, cfg = self.quote(videos.MINIMAX_H3, mode, "768P")
            self.assertEqual(quote["input"]["output_policy"], expected)
            body = provider.build_body("video", videos.MINIMAX_H3, quote["input"]["params"],
                storage.asset_data_url(storage.load_asset(cfg["input_asset_id"])) if mode.startswith("image_") else None)
            self.assertNotIn("generate_audio", body)
            self.assertNotIn("generate_audio", body.get("extra", {}))
        self.assert_no_generation()

    def test_h3_audio_delivery_policy_change_invalidates_an_existing_quote(self):
        quote, _ = self.quote(videos.MINIMAX_H3, "text_to_video", "768P")
        self.assertEqual(quote["input"]["output_policy"]["audio_processing"]["version"], audio.VERSION)
        with patch.object(audio, "VERSION", "fixture-next-audio-policy"):
            validation = catalog.validation_info(videos.MINIMAX_H3)
            self.assertFalse(validation["current_audio_processing_sample_verified"])
            self.assertEqual(validation["tested_audio_processing_version"], "h3-video-only-v1")
            self.error("NODE_QUOTE_STALE", lambda: node_execution.generate_node(self.project, quote["node_id"],
                {"quote_id": quote["quote_id"], "client_request_id": "policy-stale", "user_action": "confirm_generate"}))
        self.assert_no_generation()

    def test_new_h3_canvas_task_passes_the_worker_frozen_policy_guard(self):
        quote, _ = self.quote(videos.MINIMAX_H3, "text_to_video", "768P")
        execution = node_execution.generate_node(self.project, quote["node_id"],
            {"quote_id": quote["quote_id"], "client_request_id": "new-h3-guard", "user_action": "confirm_generate"})
        task = db.get("tasks", execution["task_id"])
        self.assertIsNone(task["remote_task_id"])
        worker._validate_canvas_reference(task)
        self.send.assert_not_called()
        provider.media_create.assert_not_called()
        self.assertEqual(len(db.query("SELECT * FROM tasks")), 1)

    def test_resumed_h3_task_without_local_reference_still_delivers_silent_video_not_native_bytes(self):
        quote, cfg = self.quote(videos.MINIMAX_H3, "image_to_video", "768P")
        execution = node_execution.generate_node(self.project, quote["node_id"],
            {"quote_id": quote["quote_id"], "client_request_id": "recover-h3", "user_action": "confirm_generate"})
        task_id = execution["task_id"]
        db.update("tasks", task_id, {"status": "failed", "remote_task_id": "retained-h3",
            "remote_status": "succeeded", "error_code": "MEDIA_ENCODE_FAILED"})
        # Recovering an existing supplier result must not require re-uploading
        # the reference, or bypass local audio normalization when it is absent.
        storage.asset_path(storage.load_asset(cfg["input_asset_id"])).unlink()
        native = Path(self.tmp.name) / "fixture-native.mp4"
        subprocess.run([storage.ffmpeg_exe(), "-nostdin", "-v", "error", "-f", "lavfi", "-i",
            "testsrc2=size=96x64:rate=12", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000",
            "-t", "0.5", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(native)],
            capture_output=True, check=True, timeout=30)
        native_data = native.read_bytes()
        resumed = main.retry_task(task_id)
        self.assertEqual(resumed["task_id"], task_id)
        self.assertTrue(resumed["resumed"])
        self.assertFalse(resumed["model_called"])
        with patch.object(provider, "media_query", return_value={"status": "succeeded", "usage": {}, "output": []}) as query, \
             patch.object(provider, "download_result", return_value=native_data), \
             patch.object(worker, "SAMPLER", Mock(stop=Mock(return_value=0))), \
             patch("app.pipeline.on_task_succeeded"):
            worker.execute(db.get("tasks", task_id))
        result = db.get("tasks", task_id)
        self.assertEqual(result["status"], "succeeded", result["error_message"])
        self.assertEqual(result["remote_task_id"], "retained-h3")
        self.assertEqual(result["cost_cny"], 3.3232)
        delivered = storage.load_asset(result["output_asset_id"])
        self.assertFalse(audio._inspect(storage.asset_path(delivered))["has_audio"])
        metadata = db.loads(result["usage_json"])["audio_processing"]
        self.assertTrue(metadata["native_has_audio"])
        self.assertFalse(metadata["delivered_has_audio"])
        self.assertTrue(metadata["local_remux"])
        self.assertTrue(metadata["video_frames_unchanged"])
        self.assertEqual((config.DATA / metadata["native_original_relpath"]).read_bytes(), native_data)
        self.assertEqual(metadata["native_video"]["decoded_video_sha256"], metadata["delivered_video"]["decoded_video_sha256"])
        self.assertEqual(len(db.query("SELECT * FROM tasks")), 1)
        query.assert_called_once_with("video", "retained-h3")
        self.send.assert_not_called()
        provider.media_create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
