"""Independent public-Schema checks for new video contracts; no transport."""
from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import unittest
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import aihubmix_extra_videos as models, media_policy
from app.errors import AppError

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = ROOT / "docs/evidence/v0.11-八款模型扩充-后端-20261030"


class AiHubMixExtraVideoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        source = json.loads((SNAPSHOT / "schemas.json").read_text())
        cls.schemas = {row["id"]: next(ep["request"]["schema"] for ep in row["schema"]["endpoints"]
                       if ep["path"] == "/ai/v1/videos")
                       for row in source["items"] if row["id"] in {m[len(models.PREFIX):] for m in models.MODELS}}
        source = json.loads((SNAPSHOT / "prices.json").read_text())
        cls.prices = {row["model_id"]: row["pricing_lines"] for row in source["items"]}

    def assertError(self, code, action):
        with self.assertRaises(AppError) as cm:
            action()
        self.assertEqual(cm.exception.code, code)

    def params(self, model, mode="text_to_video", **changes):
        return {"prompt": "a complete fictional cup", "generation_mode": mode, "duration": 5,
                "ratio": "16:9", "resolution": models.DEFAULT_RESOLUTIONS[model],
                "generate_audio": False, "watermark": False, **changes}

    def assertDocumentedObject(self, value, schema):
        """Check actual keys against the retained vendor definitions recursively.

        No local copy of allowed request fields is used as the source of truth.
        The selected body avoids arbitrary escape-hatch and conditional modes.
        """
        if not isinstance(value, dict):
            return
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            self.assertLessEqual(set(value), set(props))
        self.assertLessEqual(set(schema.get("required", [])), set(value))
        for key, item in value.items():
            definition = props.get(key, {})
            if "enum" in definition:
                self.assertIn(item, definition["enum"])
            alternatives = [allowed for variant in definition.get("anyOf", [])
                            for allowed in (variant.get("enum", []) + ([variant["const"]] if "const" in variant else []))]
            open_scalar = any(variant.get("type") not in (None, "null") and
                              "enum" not in variant and "const" not in variant
                              for variant in definition.get("anyOf", []))
            if alternatives and not open_scalar:
                self.assertIn(item, alternatives)
            if type(item) in (int, float):
                if "minimum" in definition:
                    self.assertGreaterEqual(item, definition["minimum"])
                if "maximum" in definition:
                    self.assertLessEqual(item, definition["maximum"])
            if isinstance(item, dict):
                self.assertDocumentedObject(item, definition)
            elif isinstance(item, list) and isinstance(definition.get("items"), dict):
                for child in item:
                    self.assertDocumentedObject(child, definition["items"])

    def test_eight_mode_bodies_use_only_documented_native_fields(self):
        reference = "data:image/png;base64,fixture"
        for model in models.MODELS:
            schema = self.schemas[model[len(models.PREFIX):]]
            for mode in ("text_to_video", "image_to_video"):
                with self.subTest(model=model, mode=mode):
                    body = {"model": model[len(models.PREFIX):], "prompt": "cup",
                            **models.body_options(model, self.params(model, mode), reference)}
                    self.assertDocumentedObject(body, schema)
                    if mode == "text_to_video":
                        self.assertNotIn("frame_images", body)
                        self.assertNotIn("input_references", body)
                        self.assertNotIn(reference, json.dumps(body))
                    else:
                        self.assertEqual(body["frame_images"], [
                            {"frame_type": "first_frame", "image_url": {"url": reference}}])
                    self.assertEqual(body["duration"], 5)

    def test_seedance_first_frame_uses_required_adaptive_ratio(self):
        schema = self.schemas[models.SEEDANCE_25[len(models.PREFIX):]]
        rule = next(rule for rule in schema["allOf"]
                    if rule.get("if", {}).get("required") == ["frame_images"]
                    and "aspect_ratio" in rule.get("then", {}).get("properties", {}))
        required_ratio = rule["then"]["properties"]["aspect_ratio"]["const"]
        body = models.body_options(models.SEEDANCE_25,
            self.params(models.SEEDANCE_25, "image_to_video"), "data:image/png;base64,fixture")
        self.assertEqual(body["aspect_ratio"], required_ratio)
        self.assertEqual(models.CAPABILITIES[models.SEEDANCE_25]["ratios"], ["16:9"])
        self.assertIn("prepared_16_9", models.CAPABILITIES[models.SEEDANCE_25]["limits"]["first_frame_aspect_ratio"])
        self.assertEqual(models.body_options(models.SEEDANCE_25, self.params(models.SEEDANCE_25))["aspect_ratio"], "16:9")

    def test_h3_does_not_invent_audio_or_watermark_control(self):
        schema = self.schemas[models.MINIMAX_H3[len(models.PREFIX):]]
        self.assertNotIn("generate_audio", schema["properties"])
        body = models.body_options(models.MINIMAX_H3, self.params(models.MINIMAX_H3))
        self.assertEqual(body["resolution"], "768P")
        self.assertNotIn("generate_audio", body)
        self.assertNotIn("extra", body)
        self.assertEqual(models.WATERMARK_CONTROLS[models.MINIMAX_H3], "not_exposed")
        self.assertFalse(models.CAPABILITIES[models.MINIMAX_H3]["limits"]["native_silent_output_verified"])

    def test_kling_mode_and_watermark_control_match_current_schema(self):
        schema = self.schemas[models.KLING_V3[len(models.PREFIX):]]
        extras = schema["properties"]["extra"]["properties"]
        self.assertIn("watermark_info", extras)
        body = models.body_options(models.KLING_V3, self.params(models.KLING_V3))
        self.assertIn(body["extra"]["mode"], extras["mode"]["enum"])
        self.assertEqual(body["extra"]["mode"], "std")
        self.assertFalse(body["extra"]["watermark_info"]["enabled"])
        self.assertFalse(body["generate_audio"])
        self.assertNotIn("watermark", body["extra"])

    def test_bad_parameters_and_missing_reference_are_rejected_without_transport(self):
        for model in models.MODELS:
            for change in ({"duration": 5.0}, {"duration": True}, {"duration": "5"},
                    {"duration": -1}, {"duration": 10}, {"ratio": "9:16"}, {"resolution": "auto"},
                    {"seed": 42}, {"seed": False}, {"watermark": True}, {"watermark": 0},
                    {"generate_audio": True}, {"generate_audio": 0}, {"n": 2},
                    {"input_references": []}, {"frame_images": []}, {"extra": {"watermark": True}},
                    {"reference_images": 2}, {"reference_images": True}, {"generation_mode": "image_to_image"}):
                with self.subTest(model=model, changes=change):
                    self.assertTrue(models.parameter_problems(model, self.params(model, **change)))
                    self.assertError("MODEL_CAPABILITY_MISMATCH", lambda: models.body_options(model, self.params(model, **change)))
            self.assertError("REFERENCE_IMAGE_REQUIRED", lambda: models.body_options(model, self.params(model, "image_to_video")))
            self.assertFalse(models.parameter_problems(model, self.params(model, seed=-1)))
        self.assertError("MODEL_CAPABILITY_MISMATCH", lambda: models.body_options(
            models.MINIMAX_H3, self.params(models.MINIMAX_H3, prompt="x" * 7000)))
        prompt = "x" * (7000 - len(media_policy.FRAMING_INSTRUCTION) - 2)
        self.assertFalse(models.parameter_problems(models.MINIMAX_H3, self.params(models.MINIMAX_H3, prompt=prompt)))

    def test_estimates_use_published_units_and_keep_seedance_token_bound_closed(self):
        self.assertError("MODEL_PRICE_UNCONFIRMED", lambda: models.estimate_usd(models.SEEDANCE_25, self.params(models.SEEDANCE_25)))
        self.assertNotIn(models.SEEDANCE_25, models.USD_PRICING)
        self.assertEqual(models.PRICING_NOTES[models.SEEDANCE_25]["reason"], "video_token_cost_bound_unconfirmed")
        self.assertEqual(models.estimate_usd(models.WAN_3, self.params(models.WAN_3)), Decimal("0.21125"))
        self.assertEqual(models.estimate_usd(models.MINIMAX_H3, self.params(models.MINIMAX_H3)), Decimal("0.38720"))
        self.assertEqual(models.estimate_usd(models.MINIMAX_H3, self.params(models.MINIMAX_H3, "image_to_video")), Decimal("0.41540"))
        self.assertNotIn(models.KLING_V3, models.USD_PRICING)
        self.assertEqual(models.PRICING_NOTES[models.KLING_V3]["reason"],
                         "input_token_and_generation_cost_bound_unconfirmed")
        for mode in ("text_to_video", "image_to_video"):
            self.assertError("MODEL_PRICE_UNCONFIRMED", lambda: models.estimate_usd(
                models.KLING_V3, self.params(models.KLING_V3, mode)))
        for model in (models.MINIMAX_H3, models.WAN_3, models.KLING_V3):
            mid = model[len(models.PREFIX):]
            lines = self.prices[mid]
            self.assertTrue(any(row["unit"] == "second" for row in lines))
            self.assertFalse(any(row["unit"] == "token" for row in lines))
            self.assertError("MODEL_PRICE_UNCONFIRMED", lambda: models.estimate_usd(model, self.params(model, duration=5.0)))
        for refs in (2, True, -1):
            self.assertError("MODEL_PRICE_UNCONFIRMED", lambda: models.estimate_usd(
                models.MINIMAX_H3, self.params(models.MINIMAX_H3, reference_images=refs)))
        self.assertEqual(models.estimate_usd(models.MINIMAX_H3,
            self.params(models.MINIMAX_H3, "image_to_video", reference_images=0)), Decimal("0.41540"))


if __name__ == "__main__":
    unittest.main()
