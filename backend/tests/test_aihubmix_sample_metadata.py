"""Per-model acceptance metadata; every hypothetical acceptance is test-local."""
import os
import unittest
from unittest.mock import patch

from app import aihubmix_models as catalog, media_policy


class AiHubMixSampleMetadataTests(unittest.TestCase):
    def setUp(self):
        # Freeze the pre-expansion acceptance baseline: the following tests
        # intentionally exercise candidates that did not yet have real samples.
        baseline = (catalog.FLASH, catalog.FAST)
        self.enterContext(patch.dict(catalog.SAMPLE_PROFILES,
            {mid: catalog.SAMPLE_PROFILES[mid] for mid in baseline}, clear=True))
        self.enterContext(patch.dict(catalog.SAMPLE_DETAILS,
            {mid: catalog.SAMPLE_DETAILS[mid] for mid in baseline}, clear=True))
        self.enterContext(patch.dict(os.environ, {"AIHUBMIX_API_KEY": "fixture-unused",
            "AIHUBMIX_USD_TO_CNY": "8", "AIHUBMIX_ENABLED_MODELS": ""}))

    def caps(self, mid):
        return catalog.public_operational_capabilities(mid, {"supports": ["未经样例验收的能力"]}, None)

    def test_retained_fast_v2_and_flash_v1_keep_their_own_sources(self):
        flash = catalog.validation_info(catalog.FLASH)
        fast = catalog.validation_info(catalog.FAST)
        self.assertEqual(flash["tested_output_policy_versions"], ["no-watermark-contain-v1"])
        self.assertEqual(fast["tested_output_policy_versions"], ["no-watermark-edge-extend-v2"])
        self.assertTrue(any("接痕" in item for item in fast["limitations"]))
        self.assertNotEqual(flash["sample_evidence"], fast["sample_evidence"])
        self.assertTrue(fast["sample_evidence"].endswith("当前结果.md"))
        self.assertEqual(fast["current_output_policy_sample_verified"],
                         media_policy.specification("video")["version"] == "no-watermark-edge-extend-v2")

    def test_future_mini_v3_does_not_inherit_fast_v2_or_its_limitations(self):
        profile = [row for row in catalog.CANDIDATE_PROFILES[catalog.MINI]
                   if row["generation_mode"] == ["image_to_video"]]
        current = media_policy.specification("video")["version"]
        with patch.dict(catalog.SAMPLE_PROFILES, {catalog.MINI: profile}), patch.dict(catalog.SAMPLE_DETAILS,
                {catalog.MINI: {"tested_output_policy_version": current,
                    "limitations": ["仅本次虚构白杯图生样例"], "evidence": "fixture-mini-only"}}):
            info = catalog.validation_info(catalog.MINI)
            self.assertEqual(info["tested_output_policy_versions"], [current])
            self.assertTrue(info["current_output_policy_sample_verified"])
            self.assertEqual(info["limitations"], ["仅本次虚构白杯图生样例"])
            self.assertEqual(info["sample_evidence"], "fixture-mini-only")
        self.assertNotIn(catalog.MINI, catalog.SAMPLE_PROFILES)
        self.assertNotIn(catalog.MINI, catalog.SAMPLE_DETAILS)

    def test_missing_metadata_never_invents_a_tested_policy(self):
        with patch.dict(catalog.SAMPLE_PROFILES, {catalog.QWEN: catalog.CANDIDATE_PROFILES[catalog.QWEN]}):
            info = catalog.validation_info(catalog.QWEN)
            self.assertEqual(info["tested_output_policy_versions"], [])
            self.assertIsNone(info["sample_evidence"])
            self.assertFalse(info["current_output_policy_sample_verified"])
        untested = catalog.validation_info(catalog.MINI)
        self.assertFalse(untested["sample_generation_verified"])
        self.assertEqual(untested["tested_output_policy_versions"], [])

    def test_image_notes_use_kind_and_single_mode_notes_never_advertise_both_modes(self):
        for mid, modes, expected_note, expected_supports in (
            (catalog.QWEN, ["text_to_image", "image_to_image"], "商品图片与参考图编辑", ["文生图", "单张图生图"]),
            (catalog.QWEN, ["image_to_image"], "参考图编辑", ["单张图生图"]),
            (catalog.QWEN, ["text_to_image"], "文字生成商品图片", ["文生图"]),
            (catalog.MINI, ["image_to_video"], "首帧生成视频", ["首帧生视频"]),
            (catalog.MINI, ["text_to_video"], "文字生成短视频", ["文生视频"]),
        ):
            profiles = [row for row in catalog.CANDIDATE_PROFILES[mid]
                        if row["generation_mode"][0] in modes]
            with self.subTest(model=mid, modes=modes), patch.dict(catalog.SAMPLE_PROFILES, {mid: profiles}), \
                    patch.dict(os.environ, {"AIHUBMIX_ENABLED_MODELS": mid}):
                self.assertEqual(catalog.model_note(mid), expected_note)
                caps = self.caps(mid)
                self.assertEqual(caps["input_modes"], modes)
                self.assertEqual(caps["supports"], expected_supports)
        self.assertEqual(self.caps(catalog.MINI)["supports"], [])

    def test_mini_prices_explicitly_reserve_the_higher_conflicting_public_rate(self):
        price = catalog.public_pricing(catalog.MINI)
        self.assertTrue(price["conservative_upper_estimate"])
        self.assertEqual(price["per_second"], {"480p": "0.055", "720p": "0.113"})
        self.assertEqual(price["pricing_discrepancy"]["catalog_per_second"]["480p"], "0.0344")
        self.assertEqual(price["pricing_discrepancy"]["policy"], "higher_public_rate")
        self.assertTrue(price["estimate_only"])


if __name__ == "__main__":
    unittest.main()
