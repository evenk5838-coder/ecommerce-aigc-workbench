"""Real retained sample scopes drive availability; no model transport is used.

Unlike the pre-expansion baseline fixtures, this suite reads the current catalog
and makes the newly accepted modes explicit. A retained sample is not a blanket
license for the supplier's wider dimensions, durations or input modes.
"""
import os
import unittest
from unittest.mock import patch

import test_node_execution as fixture
from app import aihubmix_models as catalog, config, db, main, media_policy
from app import providers_aihubmix as provider


class AiHubMixAcceptedModelsTests(unittest.TestCase):
    error = fixture.NodeExecutionTests.error
    asset = fixture.NodeExecutionTests.asset

    def setUp(self):
        fixture.NodeExecutionTests.setUp(self)
        self.enterContext(patch.dict(os.environ, {"AIHUBMIX_API_KEY": "fixture-unused",
            "AIHUBMIX_USD_TO_CNY": "8", "AIHUBMIX_ENABLED_MODELS": ""}))
        self.enterContext(patch.object(config, "_load_dotenv", return_value=None))
        self.send = self.enterContext(patch.object(provider, "_request", side_effect=AssertionError("network forbidden")))

    def body(self, model, mode, **changes):
        image = mode.endswith("_image")
        params = {"prompt": "one complete fictional white cup", "generation_mode": mode,
                  **({"size": "1024x1024", "ratio": "1:1"} if image else
                     {"duration": 5, "resolution": "480p", "ratio": "16:9", "generate_audio": False}),
                  **changes}
        reference = self.asset()["id"] if mode.startswith("image_") else None
        return main.TaskIn(kind="image" if image else "video", model_id=model,
                           params=params, input_asset_id=reference)

    def enable(self, mid):
        self.enterContext(patch.dict(os.environ, {"AIHUBMIX_ENABLED_MODELS": mid}))
        self.assertTrue(catalog.enabled_models_config()["valid"])

    def test_qwen_retained_scope_is_exactly_two_modes_and_1024_square(self):
        self.assertIn(catalog.QWEN, catalog.SAMPLE_PROFILES)
        profiles = catalog.operational_profiles(catalog.QWEN)
        self.assertEqual(len(profiles), 2)
        self.assertEqual({tuple(row["generation_mode"]) for row in profiles},
                         {("text_to_image",), ("image_to_image",)})
        for row in profiles:
            self.assertEqual(row["size"], ["1024x1024"])
            self.assertEqual(row["ratio"], ["1:1"])
        info = catalog.validation_info(catalog.QWEN)
        self.assertTrue(info["sample_generation_verified"])
        self.assertEqual(info["tested_output_policy_versions"], ["no-watermark-contain-v1"])
        self.assertTrue(info["current_output_policy_sample_verified"])
        self.assertTrue(info["sample_evidence"])
        self.assertFalse(info["operational_enabled"])

    def test_qwen_requires_allowlist_and_does_not_expand_vendor_sizes_or_image_count(self):
        self.assertFalse(config.model_submittability(catalog.QWEN)["submittable"])
        self.error("MODEL_NOT_VERIFIED", lambda: main.resolve_task(self.body(catalog.QWEN, "text_to_image")))
        self.enable(catalog.QWEN)
        self.assertTrue(config.model_submittability(catalog.QWEN)["submittable"])
        self.assertFalse(config.model_submittability(catalog.FLASH)["submittable"])
        self.assertFalse(config.model_submittability(catalog.MINI)["submittable"])
        for mode in ("text_to_image", "image_to_image"):
            resolved = main.resolve_task(self.body(catalog.QWEN, mode))
            self.assertTrue(resolved["submittable"])
        for changes in ({"size": "2048x2048"}, {"size": "1536x1024", "ratio": "3:2"},
                        {"ratio": "16:9"}, {"n": 2}, {"watermark": True}):
            with self.subTest(changes=changes):
                self.error("MODEL_CAPABILITY_MISMATCH", lambda: main.resolve_task(
                    self.body(catalog.QWEN, "text_to_image", **changes)))
        caps = config.public_model_capabilities(catalog.QWEN)
        self.assertEqual(caps["input_modes"], ["text_to_image", "image_to_image"])
        self.assertEqual(caps["sizes"], ["1024x1024"])
        self.assertEqual(caps["supports"], ["文生图", "单张图生图"])
        self.assertEqual(config.model_note(catalog.QWEN), "商品图片与参考图编辑")
        self.assertEqual(db.query("SELECT * FROM tasks"), [])
        self.send.assert_not_called()

    def test_mini_if_retained_only_opens_its_single_image_to_video_profile(self):
        if catalog.MINI not in catalog.SAMPLE_PROFILES:
            self.assertFalse(config.model_submittability(catalog.MINI)["submittable"])
            self.assertFalse(catalog.validation_info(catalog.MINI)["sample_generation_verified"])
            return
        profiles = catalog.operational_profiles(catalog.MINI)
        self.assertEqual(profiles, [{"generation_mode": ["image_to_video"], "duration": [5],
            "ratio": ["16:9"], "resolution": ["480p"], "generate_audio": [False]}])
        self.assertFalse(config.model_submittability(catalog.MINI)["submittable"])
        self.enable(catalog.MINI)
        self.assertTrue(main.resolve_task(self.body(catalog.MINI, "image_to_video"))["submittable"])
        self.error("MODEL_CAPABILITY_MISMATCH", lambda: main.resolve_task(self.body(catalog.MINI, "text_to_video")))
        for changes in ({"duration": 10}, {"duration": 5.0}, {"resolution": "720p"},
                        {"ratio": "9:16"}, {"generate_audio": True}, {"watermark": True}):
            with self.subTest(changes=changes):
                self.error("MODEL_CAPABILITY_MISMATCH", lambda: main.resolve_task(
                    self.body(catalog.MINI, "image_to_video", **changes)))
        caps = config.public_model_capabilities(catalog.MINI)
        self.assertEqual(caps["input_modes"], ["image_to_video"])
        self.assertEqual(caps["supports"], ["首帧生视频"])
        self.assertEqual(caps["duration_policy"]["allowed"], [5])
        self.assertEqual(caps["resolutions"], ["480p"])
        self.assertEqual(config.model_note(catalog.MINI), "首帧生成视频")
        info = caps["validation"]
        self.assertEqual(info["tested_output_policy_versions"], ["no-watermark-edge-gradient-v3"])
        self.assertEqual(info["current_output_policy_sample_verified"],
            media_policy.specification("video")["version"] == "no-watermark-edge-gradient-v3")
        self.assertTrue(info["sample_evidence"])
        self.assertTrue(caps["pricing"]["conservative_upper_estimate"])
        self.assertEqual(db.query("SELECT * FROM tasks"), [])
        self.send.assert_not_called()

    def test_current_retained_models_still_need_key_price_and_explicit_enablement(self):
        self.assertIn(catalog.QWEN, catalog.SAMPLE_PROFILES)
        for mid in (catalog.QWEN, catalog.MINI):
            if mid not in catalog.SAMPLE_PROFILES:
                continue
            with self.subTest(model=mid), patch.dict(os.environ, {"AIHUBMIX_ENABLED_MODELS": mid}):
                self.assertTrue(config.model_submittability(mid)["submittable"])
                with patch.dict(os.environ, {"AIHUBMIX_API_KEY": ""}):
                    self.assertIn("AUTH_MISSING", config.model_submittability(mid)["reasons"])
                with patch.dict(os.environ, {"AIHUBMIX_USD_TO_CNY": "0"}):
                    self.assertIn("MODEL_PRICE_UNCONFIRMED", config.model_submittability(mid)["reasons"])
        for mid in (catalog.NANO, catalog.GPT_IMAGE):
            self.assertFalse(config.model_submittability(mid)["submittable"])
            self.assertFalse(catalog.validation_info(mid)["sample_generation_verified"])


if __name__ == "__main__":
    unittest.main()
