"""New application paths use permitted defaults; direct/legacy rules stay separate."""
import os
import unittest
from unittest.mock import patch

import test_node_execution as fixture
from app import aihubmix_models as catalog, canvas, config, db, director_creation, node_execution
from app import providers_aihubmix


class AiHubMixActivationPathsTests(unittest.TestCase):
    error = fixture.NodeExecutionTests.error

    def setUp(self):
        fixture.NodeExecutionTests.setUp(self)
        self.enterContext(patch.dict(os.environ, {"AIHUBMIX_API_KEY": "fake-test",
            "AIHUBMIX_ENABLED_MODELS": f"{catalog.FLASH},{catalog.FAST}", "AIHUBMIX_USD_TO_CNY": "8"}))
        self.enterContext(patch.object(config, "_load_dotenv", return_value=None))

    def test_empty_node_settings_resolve_to_permitted_video_defaults_without_tasks(self):
        canvas.patch_canvas(self.project, {"nodes": [{"node_id": "video", "type": "video", "config": {
            "model_id": catalog.FAST, "generation_mode": "text_to_video", "prompt": "one cup", "params": {}}}]})
        quote = node_execution.quote_node(self.project, "video")
        params = quote["input"]["params"]
        self.assertEqual((params["duration"], params["resolution"], params["ratio"], params["generate_audio"]),
                         (5, "480p", "16:9", False))
        self.assertFalse(db.query("SELECT * FROM tasks"))

    def test_director_defaults_follow_operational_profile_without_overwriting_explicit_values(self):
        result = director_creation._media_config("video", catalog.FAST, "one cup", {})
        self.assertEqual(result["params"]["resolution"], "480p")
        self.assertEqual(result["params"]["ratio"], "16:9")
        self.error("MODEL_CAPABILITY_MISMATCH", lambda: director_creation._media_config(
            "video", catalog.FAST, "one cup", {"resolution": "720p"}))
        self.assertFalse(db.query("SELECT * FROM tasks"))

    def test_direct_create_cannot_bypass_closed_allowlist_or_missing_price(self):
        params = {"prompt": "one cup", "size": "1024x1024", "ratio": "1:1", "generation_mode": "text_to_image"}
        with patch.dict(os.environ, {"AIHUBMIX_ENABLED_MODELS": ""}), patch.object(providers_aihubmix, "_request") as send:
            self.error("MODEL_NOT_VERIFIED", lambda: providers_aihubmix.media_create("image", catalog.FLASH, params))
            send.assert_not_called()
        with patch.dict(os.environ, {"AIHUBMIX_USD_TO_CNY": "0"}), patch.object(providers_aihubmix, "_request") as send:
            self.error("MODEL_PRICE_UNCONFIRMED", lambda: providers_aihubmix.media_create("image", catalog.FLASH, params))
            send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
