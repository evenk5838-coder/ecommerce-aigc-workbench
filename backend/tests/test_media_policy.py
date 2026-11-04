"""No network, real credential, or billable generation in output-policy checks."""
import base64
import hashlib
import io
import json
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageOps
import test_node_execution as fixture
from app import canvas, cloud_tasks, config, db, main, media_policy as policy, node_execution
from app import providers_aihubmix as hub, providers_ark as ark, storage, worker
from app.aihubmix_models import FLASH, FAST


def reference(size=(100, 100), *, exif=False, transparent=False):
    image = Image.new("RGBA" if transparent else "RGB", size, (220, 220, 220, 0) if transparent else "white")
    draw = ImageDraw.Draw(image)
    w, h = size
    for box, color in [((0, 0, w//4, h//4), "red"), ((w*3//4, 0, w-1, h//4), "lime"),
                       ((0, h*3//4, w//4, h-1), "blue"), ((w*3//4, h*3//4, w-1, h-1), "yellow")]:
        draw.rectangle(box, fill=color)
    output = io.BytesIO()
    if exif:
        metadata = Image.Exif()
        metadata[274] = 6
        image.save(output, "JPEG", exif=metadata)
    else:
        image.save(output, "PNG")
    raw = output.getvalue()
    return "data:image/" + ("jpeg" if exif else "png") + ";base64," + base64.b64encode(raw).decode()


class GeometryTests(unittest.TestCase):
    def decode(self, value):
        return Image.open(io.BytesIO(base64.b64decode(value.split(",", 1)[1])))

    def test_contain_keeps_all_four_corners_in_every_ratio_and_source_shape(self):
        for size in [(100, 100), (80, 160), (240, 80)]:
            for ratio in ["16:9", "9:16", "1:1", "4:3", "3:4", "21:9"]:
                with self.subTest(size=size, ratio=ratio):
                    source = reference(size)
                    prepared, metadata = policy.prepare_reference(source, ratio)
                    output = self.decode(prepared)
                    a, b = map(int, ratio.split(":"))
                    self.assertEqual(output.width * b, output.height * a)
                    self.assertEqual(output.width % 16, 0)
                    self.assertEqual(output.height % 16, 0)
                    left, top, right, bottom = metadata["content_box"]
                    self.assertGreaterEqual(left, output.width * .1 - 1)
                    self.assertGreaterEqual(top, output.height * .1 - 1)
                    self.assertLessEqual(right, output.width * .9 + 1)
                    self.assertLessEqual(bottom, output.height * .9 + 1)
                    self.assertLess(abs((right-left)/(bottom-top) - size[0]/size[1]), .01)
                    for x, y, expected in [(left+5, top+5, (255,0,0)), (right-6, top+5, (0,255,0)),
                                           (left+5, bottom-6, (0,0,255)), (right-6, bottom-6, (255,255,0))]:
                        self.assertEqual(output.getpixel((x,y)), expected)
                    self.assertEqual(metadata["source_sha256"], hashlib.sha256(base64.b64decode(source.split(",",1)[1])).hexdigest())

    def test_deterministic_without_mutating_input_and_alpha_is_flattened(self):
        source = reference(transparent=True)
        before = source
        first = policy.prepare_reference(source, "16:9")
        self.assertEqual(first, policy.prepare_reference(source, "16:9"))
        self.assertEqual(before, source)
        output = self.decode(first[0])
        self.assertEqual(output.mode, "RGB")
        left, top, right, bottom = first[1]["content_box"]
        self.assertEqual(output.getpixel(((left+right)//2, (top+bottom)//2)), (255,255,255))

    def test_exif_rotation_is_applied_before_fitting(self):
        _, metadata = policy.prepare_reference(reference((80, 160), exif=True), "16:9")
        self.assertEqual(metadata["source_size"], [160,80])

    def test_bad_input_is_local_error_and_animated_input_is_rejected(self):
        output = io.BytesIO()
        Image.new("RGB", (30,30), "red").save(output, "GIF", save_all=True,
            append_images=[Image.new("RGB", (30,30), "blue")])
        animated = "data:image/gif;base64," + base64.b64encode(output.getvalue()).decode()
        for value in ("https://example.test/image.png", "data:image/png;base64,invalid", animated):
            with self.assertRaises(Exception) as exc:
                policy.prepare_reference(value, "16:9")
            self.assertEqual(exc.exception.code, "INVALID_REFERENCE_IMAGE")

    def test_guard_is_appended_once_and_preserves_user_prompt(self):
        original = "镜头缓慢推进；保持杯子旋转"
        effective = policy.video_prompt(original)
        self.assertTrue(effective.startswith(original))
        self.assertIn("不得切掉商品边缘", effective)
        for boundary in ("60%", "15%", "5%", "起始帧和结束帧"):
            self.assertIn(boundary, effective)
        self.assertEqual(policy.video_prompt(effective), effective)

    def test_gradient_background_is_continuous_and_product_pixels_are_preserved(self):
        source = Image.new("RGB", (240, 160))
        for y in range(source.height):
            for x in range(source.width):
                source.putpixel((x, y), (120 + x//10, 130 + y//5, 150 + (x+y)//12))
        ImageDraw.Draw(source).rectangle((90, 45, 150, 115), fill="blue")
        raw = io.BytesIO()
        source.save(raw, "PNG")
        value = "data:image/png;base64," + base64.b64encode(raw.getvalue()).decode()
        output_uri, metadata = policy.prepare_reference(value, "16:9")
        output = self.decode(output_uri)
        box = metadata["content_box"]
        fitted = ImageOps.contain(source, (1024, 576), Image.Resampling.LANCZOS)
        self.assertEqual(output.crop(box).tobytes(), fitted.tobytes())
        left, top, right, bottom = box
        pairs = [((left-1, y), (left, y)) for y in range(top, bottom)]
        pairs += [((right-1, y), (right, y)) for y in range(top, bottom)]
        pairs += [((x, top-1), (x, top)) for x in range(left, right)]
        pairs += [((x, bottom-1), (x, bottom)) for x in range(left, right)]
        jumps = [abs(a-b) for outside, inside in pairs
                 for a, b in zip(output.getpixel(outside), output.getpixel(inside))]
        self.assertLess(sum(jumps)/len(jumps), 2)
        self.assertLessEqual(sorted(jumps)[int(len(jumps)*.95)], 4)
        self.assertEqual(metadata["policy"], policy.VIDEO_VERSION)
        self.assertEqual(metadata["background_fill"], "edge_gradient_extend")

    def test_padding_does_not_mirror_or_repeat_the_product_and_flat_background_stays_flat(self):
        source = Image.new("RGB", (160, 160), "white")
        ImageDraw.Draw(source).rectangle((55, 40, 105, 120), fill="blue")
        raw = io.BytesIO()
        source.save(raw, "PNG")
        prepared, metadata = policy.prepare_reference(
            "data:image/png;base64," + base64.b64encode(raw.getvalue()).decode(), "16:9")
        output = self.decode(prepared)
        left, top, right, bottom = metadata["content_box"]
        for outside in [(0, 0, left, output.height), (right, 0, output.width, output.height),
                        (left, 0, right, top), (left, bottom, right, output.height)]:
            self.assertEqual(output.crop(outside).getextrema(), ((255,255),)*3)
        plain = Image.new("RGB", (100, 150), (173,184,195))
        raw = io.BytesIO()
        plain.save(raw, "PNG")
        prepared, _ = policy.prepare_reference(
            "data:image/png;base64," + base64.b64encode(raw.getvalue()).decode(), "9:16")
        self.assertEqual(self.decode(prepared).getextrema(), ((173,173),(184,184),(195,195)))


class PipelinePolicyTests(unittest.TestCase):
    setUp = fixture.NodeExecutionTests.setUp
    error = fixture.NodeExecutionTests.error
    asset = fixture.NodeExecutionTests.asset
    create_node = fixture.NodeExecutionTests.create_node
    generate = fixture.NodeExecutionTests.generate

    def params(self, kind):
        return {"prompt": "一个完整的杯子", "generation_mode": "text_to_"+kind,
                **({"size": "1024x1024"} if kind=="image" else
                   {"duration":5, "resolution":"480p", "ratio":"16:9", "generate_audio":False})}

    def test_hub_images_and_videos_always_explicitly_disable_watermarks(self):
        for kind, model in [("image",FLASH),("video",FAST)]:
            for mode in ["text_to_"+kind, "image_to_"+kind]:
                body = hub.build_body(kind, model, {**self.params(kind),"generation_mode":mode}, reference())
                self.assertEqual(body["extra"], {"watermark": False})
                if kind=="image":
                    self.assertEqual(body.get("image"), reference() if mode.startswith("image_") else None)

    def test_every_cloud_model_rejects_watermark_true_before_submission(self):
        for mid, model in config.MODEL_REGISTRY.items():
            if model.get("provider") not in config.CLOUD_PROVIDERS:
                continue
            for value in (True, 1, "false", None):
                issues = config.validate_params_for_model(mid, model["kind"], {
                    **self.params(model["kind"]), "watermark": value})
                self.assertTrue(any(p.get("reason")=="watermark_forbidden" for p in issues), (mid,value))
        self.error("MODEL_CAPABILITY_MISMATCH", lambda: main.estimate_task(main.TaskIn(kind="image",
            model_id=fixture.IMAGE,params={"prompt":"cup","watermark":True})))
        self.assertFalse(db.query("SELECT * FROM tasks"))

    def test_direct_provider_calls_cannot_bypass_no_watermark_policy(self):
        with patch.object(ark,"_request") as send:
            self.error("MODEL_CAPABILITY_MISMATCH", lambda: ark.image_generate(fixture.IMAGE,"cup",watermark=True))
            self.error("MODEL_CAPABILITY_MISMATCH", lambda: ark.video_create(fixture.VIDEO,[],watermark=True))
            self.error("MODEL_CAPABILITY_MISMATCH", lambda: ark.image_generate(fixture.IMAGE,"cup --wm true"))
            self.error("MODEL_CAPABILITY_MISMATCH", lambda: ark.video_create(fixture.VIDEO,[{"type":"text","text":"cup --watermark=1"}]))
            send.assert_not_called()
        with patch.object(hub,"_request") as send:
            self.error("MODEL_CAPABILITY_MISMATCH", lambda: hub.media_create("image",FLASH,{**self.params("image"),"watermark":True}))
            self.error("MODEL_CAPABILITY_MISMATCH", lambda: hub.media_create("video",FAST,{**self.params("video"),"prompt":"cup --WM true"}))
            send.assert_not_called()

    def test_native_and_ark_send_same_contain_frame_but_i2i_uses_original(self):
        asset=storage.save_bytes(base64.b64decode(reference().split(",",1)[1]),"image","fixture")
        params={**self.params("video"),"generation_mode":"image_to_video","input_asset_id":asset["id"]}
        original=storage.asset_data_url(asset)
        native=hub.build_body("video",FAST,params,original)
        content=cloud_tasks.build_content(params)
        self.assertEqual(native["frame_images"][0]["image_url"]["url"], content[1]["image_url"]["url"])
        self.assertNotEqual(original,content[1]["image_url"]["url"])
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))
        self.assertEqual(storage.asset_data_url(asset),original)
        self.assertEqual(hub.build_body("image",FLASH,{**self.params("image"),"generation_mode":"image_to_image"},original)["image"],original)
        self.assertNotIn("frame_images",hub.build_body("video",FAST, self.params("video"),original))
        self.assertEqual(cloud_tasks.build_content({**params,"prompt":""})[0]["type"],"text")

    def test_freeze_and_model_api_publish_output_policy_without_claiming_real_acceptance(self):
        self.create_node()
        quote=node_execution.quote_node(self.project,"image")
        self.assertEqual(quote["input"]["output_policy"],policy.specification("image"))
        item=next(m for m in main.models()["items"] if m["id"]==FLASH)
        self.assertEqual(item["capabilities"]["output_policy"]["watermark"],False)
        self.assertFalse(item["submittability"]["submittable"])

    def test_policy_change_invalidates_open_quotes_and_queued_new_generation(self):
        self.create_node()
        quote=node_execution.quote_node(self.project,"image")
        with patch.object(policy,"VERSION","changed"):
            self.error("NODE_QUOTE_STALE",lambda:self.generate(quote))
        execution=self.generate(quote)
        with patch.object(policy,"VERSION","changed"), patch.object(ark,"image_generate") as send:
            worker.execute(worker.claim_next())
            send.assert_not_called()
        self.assertEqual(db.get("tasks",execution["task_id"])["error_code"],"NODE_QUOTE_STALE")

    def test_remote_poll_recovery_skips_new_policy_checks(self):
        self.create_node(mode="image_to_video")
        quote=node_execution.quote_node(self.project,"image")
        execution=self.generate(quote)
        db.update("tasks",execution["task_id"],{"remote_task_id":"existing"})
        with patch.object(policy,"specification",side_effect=AssertionError("must not refreeze a remote task")):
            worker._validate_canvas_reference(db.get("tasks",execution["task_id"]))

    def test_video_policy_change_invalidates_only_unsent_video_work(self):
        self.create_node(mode="image_to_video")
        quote = node_execution.quote_node(self.project, "image")
        with patch.object(policy, "VIDEO_VERSION", "new-video-policy"):
            self.error("NODE_QUOTE_STALE", lambda: self.generate(quote))
        execution = self.generate(quote)
        with patch.object(policy, "VIDEO_VERSION", "new-video-policy"), patch.object(ark, "video_create") as send:
            worker.execute(worker.claim_next())
            send.assert_not_called()
        self.assertEqual(db.get("tasks", execution["task_id"])["error_code"], "NODE_QUOTE_STALE")

    def test_video_policy_change_preserves_image_quotes(self):
        self.create_node()
        quote = node_execution.quote_node(self.project, "image")
        self.assertEqual(quote["input"]["output_policy"]["version"], policy.VERSION)
        with patch.object(policy, "VIDEO_VERSION", "new-video-policy"):
            execution = self.generate(quote)
        self.assertTrue(execution["task_id"])


if __name__=="__main__":
    unittest.main()
