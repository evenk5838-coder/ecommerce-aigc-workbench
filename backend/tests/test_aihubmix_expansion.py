"""Four candidate contracts: offline bodies, pricing and durable execution only.

Official schema snapshot is the independent source for allowed upstream fields.
All transports are blocked or fixed fixtures, never a paid API request.
"""
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import test_node_execution as fixture
from app import aihubmix_models as catalog, cloud_tasks, config, db, main, worker
from app import providers_aihubmix as provider


class AiHubMixExpansionTests(unittest.TestCase):
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
        self.enterContext(patch.dict(os.environ, {"AIHUBMIX_API_KEY": "fake-test",
            "AIHUBMIX_USD_TO_CNY": "8", "AIHUBMIX_ENABLED_MODELS": ""}))
        self.enterContext(patch.object(config, "_load_dotenv", return_value=None))
        self.transport = self.enterContext(patch.object(provider, "_request", side_effect=AssertionError("network forbidden")))
        self.enterContext(patch.object(provider, "POLL_SECONDS", 0))
        path = Path(__file__).resolve().parents[2] / "docs/evidence/v0.11-AiHubMix扩充清单-20261030/public-endpoint-check.json"
        self.schemas = {item["id"]: item["schema"]["endpoints"][0]["request"]["schema"]
                        for item in json.loads(path.read_text())["items"] if item["status"] == 200}

    def params(self, mid, mode=None):
        if mid == catalog.MINI:
            return {"prompt": "a complete white cup", "generation_mode": mode or "text_to_video",
                    "duration": 5, "ratio": "16:9", "resolution": "480p", "generate_audio": False}
        return {"prompt": "a complete white cup", "generation_mode": mode or "text_to_image",
                "size": "1024x1024", "ratio": "1:1"}

    def test_all_eight_input_modes_use_documented_fields_and_do_not_send_text_mode_references(self):
        reference = "https://fixture.invalid/one.png"
        for mid in (catalog.NANO, catalog.GPT_IMAGE, catalog.QWEN, catalog.MINI):
            kind = catalog.MODELS[mid]["kind"]
            schema = self.schemas[mid[len(catalog.PREFIX):]]
            for mode in ("text_to_" + kind, "image_to_" + kind):
                with self.subTest(model=mid, mode=mode), patch.object(provider.media_policy, "prepare_reference", return_value=(reference, {})):
                    body = provider.build_body(kind, mid, self.params(mid, mode), reference)
                    self.assertEqual(body["model"], mid[len(catalog.PREFIX):])
                    self.assertLessEqual(set(body), set(schema["properties"]))
                    self.assertLessEqual(set(schema["required"]), set(body))
                    extra_schema = schema["properties"]["extra"]["properties"]
                    self.assertLessEqual(set(body.get("extra", {})), set(extra_schema))
                    for key, value in body.get("extra", {}).items():
                        if "enum" in extra_schema[key]:
                            self.assertIn(value, extra_schema[key]["enum"])
                    if kind == "image":
                        self.assertTrue(body["async"])
                        self.assertEqual(body["n"], 1)
                        self.assertEqual(body.get("image"), reference if mode == "image_to_image" else None)
                    else:
                        self.assertEqual(bool(body.get("frame_images")), mode == "image_to_video")
                        self.assertFalse(body["generate_audio"])
                        self.assertIn("商品构图要求", body["prompt"])
        self.transport.assert_not_called()

    def test_nano_translates_square_pixel_sizes_to_tiers_not_unsupported_size_or_watermark(self):
        for size, tier in (("1024x1024", "1K"), ("2048x2048", "2K"), ("4096x4096", "4K")):
            body = provider.build_body("image", catalog.NANO, {**self.params(catalog.NANO), "size": size})
            self.assertEqual((body["aspect_ratio"], body["resolution"]), ("1:1", tier))
            self.assertNotIn("size", body)
            self.assertNotIn("extra", body)

    def test_gpt_uses_fixed_quality_and_qwen_disables_rewriting_and_watermark(self):
        body = provider.build_body("image", catalog.GPT_IMAGE, self.params(catalog.GPT_IMAGE))
        self.assertEqual(body["extra"], {"quality": "medium"})
        body = provider.build_body("image", catalog.QWEN, {**self.params(catalog.QWEN),
            "seed": 42, "negative_prompt": "blur"})
        self.assertEqual(body["extra"], {"watermark": False, "prompt_extend": False,
            "enable_thinking": False, "prompt_extend_mode": "direct"})
        self.assertEqual((body["seed"], body["negative_prompt"]), (42, "blur"))

    def test_incompatible_parameters_and_missing_reference_fail_without_network(self):
        for mid, changes in ((catalog.NANO, {"size": "1536x1024"}),
                (catalog.NANO, {"size": {"width": 1024}}), (catalog.NANO, {"resolution": "4K"}), (catalog.NANO, {"seed": 4}),
                (catalog.GPT_IMAGE, {"prompt": "x" * 32001}), (catalog.GPT_IMAGE, {"quality": "max"}), (catalog.GPT_IMAGE, {"negative_prompt": "blur"}),
                (catalog.QWEN, {"seed": True}), (catalog.QWEN, {"seed": 2147483648}),
                (catalog.QWEN, {"negative_prompt": 1}), (catalog.QWEN, {"extra": {"watermark": True}}),
                (catalog.QWEN, {"size": "4096x4096"}), (catalog.MINI, {"duration": 5.0}),
                (catalog.MINI, {"resolution": "720p"}), (catalog.MINI, {"generate_audio": True})):
            with self.subTest(model=mid, changes=changes):
                self.error("MODEL_CAPABILITY_MISMATCH", lambda: provider.media_create(
                    catalog.MODELS[mid]["kind"], mid, {**self.params(mid), **changes}))
        for mid in (catalog.NANO, catalog.GPT_IMAGE, catalog.QWEN, catalog.MINI):
            kind = catalog.MODELS[mid]["kind"]
            self.error("REFERENCE_IMAGE_REQUIRED", lambda: provider.build_body(kind, mid, self.params(mid, "image_to_" + kind)))
            self.error("MODEL_CAPABILITY_MISMATCH", lambda: provider.build_body(kind, mid, {**self.params(mid), "watermark": True}))
        self.transport.assert_not_called()

    def test_new_models_stay_hidden_and_cannot_be_enabled_by_key_or_allowlist(self):
        for mid in (catalog.NANO, catalog.GPT_IMAGE, catalog.QWEN, catalog.MINI):
            info = catalog.validation_info(mid)
            self.assertTrue(info["contract_verified"])
            self.assertFalse(info["sample_generation_verified"])
            self.assertFalse(config.model_submittability(mid)["submittable"])
            self.assertEqual(config.public_model_capabilities(mid)["input_modes"], [])
            with patch.dict(os.environ, {"AIHUBMIX_ENABLED_MODELS": mid}):
                self.assertFalse(catalog.enabled_models_config()["valid"])
                self.error("MODEL_NOT_VERIFIED", lambda: provider.media_create(catalog.MODELS[mid]["kind"], mid, self.params(mid)))
            self.error("MODEL_NOT_VERIFIED", lambda: main.create_task(main.TaskIn(
                kind=catalog.MODELS[mid]["kind"], model_id=mid, params=self.params(mid))))
        self.assertEqual(db.query("SELECT * FROM tasks"), [])
        self.transport.assert_not_called()

    def test_qwen_price_accounts_for_long_edge_and_reference_in_all_quote_helpers(self):
        for size, base, edit in (("1024x1024", .2858, .3087), ("2048x2048", .5715, .5944),
                                 ("1536x1024", .5715, .5944)):
            self.assertEqual(cloud_tasks.estimate_cost("image", catalog.QWEN, {"size": size}), base)
            self.assertEqual(config.image_cost_cny(catalog.QWEN, size, reference_images=1), edit)
            self.assertEqual(cloud_tasks.estimate_cost("image", catalog.QWEN,
                {"size": size, "generation_mode": "image_to_image", "reference_images": 0}), edit)
        self.error("MODEL_PRICE_UNCONFIRMED", lambda: config.image_cost_cny(catalog.QWEN, "1024x1024", reference_images=2))
        self.error("MODEL_PRICE_UNCONFIRMED", lambda: config.image_cost_cny(catalog.QWEN, "8000x8000"))
        self.error("MODEL_PRICE_UNCONFIRMED", lambda: config.image_cost_cny(catalog.QWEN, {"width": 1024}))
        self.assertEqual(cloud_tasks.estimate_cost("video", catalog.MINI, self.params(catalog.MINI)), 2.2)
        for mid in (catalog.NANO, catalog.GPT_IMAGE):
            self.error("MODEL_PRICE_UNCONFIRMED", lambda: cloud_tasks.estimate_cost("image", mid, self.params(mid)))
            self.assertEqual(catalog.public_pricing(mid)["pending_reason"], "token_cost_bound_unconfirmed")

    def test_image_token_usage_is_retained_without_strings_negative_or_nonfinite_values(self):
        self.assertEqual(provider._numeric_usage({"input_image_tokens": 23, "output_image_tokens": 42,
            "cached_tokens": 4, "prompt_tokens": 6, "total_tokens": float("nan"),
            "completion_tokens": -1, "output_tokens": "private", "private": "secret"}),
            {"input_image_tokens": 23, "output_image_tokens": 42, "cached_tokens": 4, "prompt_tokens": 6})

    def test_qwen_mock_worker_keeps_reference_fee_and_remote_id_before_query(self):
        # Simulate a FUTURE accepted model only inside this disposable test.
        self.enterContext(patch.dict(catalog.SAMPLE_PROFILES, {catalog.QWEN: catalog.CANDIDATE_PROFILES[catalog.QWEN]}))
        self.enterContext(patch.dict(os.environ, {"AIHUBMIX_ENABLED_MODELS": catalog.QWEN}))
        self.create_node("qwen", "image_to_image", model_id=catalog.QWEN,
                         params={"size": "1024x1024", "ratio": "1:1"})
        from app import node_execution
        execution = self.generate(node_execution.quote_node(self.project, "qwen", {}), node_id="qwen")
        from PIL import Image
        stream = io.BytesIO()
        Image.new("RGB", (32, 32), "white").save(stream, "PNG")
        import base64
        def transport(method, path, body=None, **kwargs):
            if method == "POST":
                self.assertIn("image", body)
                self.assertFalse(body["extra"]["watermark"])
                return {"id": "qwen-fixture"}
            self.assertEqual(db.get("tasks", execution["task_id"])["remote_task_id"], "qwen-fixture")
            return {"id": "qwen-fixture", "object": "image", "status": "completed",
                    "usage": {"images": 1, "input_image_tokens": 23},
                    "output": [{"b64_json": base64.b64encode(stream.getvalue()).decode()}]}
        with patch.object(provider, "_request", side_effect=transport) as send:
            worker.execute(worker.claim_next())
            self.assertEqual(sum(call.args[0] == "POST" for call in send.call_args_list), 1)
        task = db.get("tasks", execution["task_id"])
        self.assertEqual(task["status"], "succeeded")
        self.assertEqual(task["cost_cny"], .3087)


if __name__ == "__main__":
    unittest.main()
