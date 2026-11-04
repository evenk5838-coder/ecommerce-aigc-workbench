"""Specification combinations on private data; no providers or workers."""
from copy import deepcopy
import unittest
from unittest.mock import patch

import test_node_execution as fixture
from app import canvas, config, db, main, model_specs, node_execution, director_workflow, projects, shots, scope
from app.errors import AppError
from app.assistant import tools as quick_tools


class CombinationPolicyTests(unittest.TestCase):
    def test_missing_policy_keeps_existing_behavior_but_empty_or_malformed_denies(self):
        self.assertEqual(model_specs.validate_combination({}, {}), [])
        for bad in (None, [], [{}], [{"typo": ["720p"]}], [{"duration": [True]}],
                    [{"resolution": "720p"}], [{"generate_audio": [1]}],
                    [{"duration": {"min": 4, "max": 9, "step": 2}}]):
            with self.subTest(bad=bad):
                errors = model_specs.validate_combination({"parameter_combinations": bad}, {})
                self.assertEqual(errors[0]["reason"], "configuration_invalid")

    def test_mode_and_size_can_be_linked_without_restricting_unmentioned_dimensions(self):
        caps = {"parameter_combinations": [
            {"generation_mode": ["text_to_image"], "size": ["4096x4096"]},
            {"generation_mode": ["image_to_image"], "size": ["2048x2048"]}]}
        self.assertEqual(model_specs.validate_combination(caps, {
            "generation_mode": "text_to_image", "size": "4096x4096", "ratio": "1:1"}), [])
        self.assertTrue(model_specs.validate_combination(caps, {
            "generation_mode": "image_to_image", "size": "4096x4096"}))

    def test_duration_range_missing_fields_and_types_never_silently_coerce(self):
        caps = {"parameter_combinations": [{"resolution": ["720p"],
            "duration": {"min": 4, "max": 12, "step": 2}, "generate_audio": [False]}]}
        valid = {"resolution": "720p", "duration": 8, "generate_audio": False}
        self.assertEqual(model_specs.validate_combination(caps, valid), [])
        for change in ({"duration": 5}, {"duration": 8.0}, {"duration": True},
                       {"duration": "8"}, {"generate_audio": 0}, {"resolution": None}):
            self.assertTrue(model_specs.validate_combination(caps, {**valid, **change}))

    def test_serialized_policy_is_a_copy(self):
        caps = {"parameter_combinations": [{"resolution": ["720p"]}]}
        before = deepcopy(caps)
        public = model_specs.public_capabilities(caps, None)
        public["parameter_combinations"][0]["resolution"].append("4k")
        self.assertEqual(caps, before)


class SharedSpecificationTests(unittest.TestCase):
    setUp = fixture.NodeExecutionTests.setUp
    error = fixture.NodeExecutionTests.error

    def set_policy(self, rules):
        caps = {**config.MODEL_CAPABILITIES[fixture.VIDEO], "parameter_combinations": rules}
        self.enterContext(patch.dict(config.MODEL_CAPABILITIES, {fixture.VIDEO: caps}))

    def test_estimate_submission_and_canvas_quote_all_reject_illegal_combination(self):
        self.set_policy([{"resolution": ["720p"], "duration": [5]},
                         {"resolution": ["1080p"], "duration": [10]}])
        params = {"prompt": "one cup", "generation_mode": "text_to_video", "ratio": "16:9",
                  "resolution": "1080p", "duration": 5}
        body = main.TaskIn(kind="video", model_id=fixture.VIDEO, params=params,
                           client_request_id="invalid-combination")
        for action in (lambda: main.estimate_task(body), lambda: main.create_task(body)):
            self.error("MODEL_CAPABILITY_MISMATCH", action)
        saved = {"nodes": [{"node_id": "video", "type": "video",
            "config": {"model_id": fixture.VIDEO, "generation_mode": "text_to_video",
                       "prompt": "one cup", "params": {k:v for k,v in params.items()
                            if k not in ("prompt", "generation_mode")}}}]}
        self.error("MODEL_CAPABILITY_MISMATCH", lambda: canvas.patch_canvas(self.project, saved))
        # Simulate a draft saved before a provider narrows its legal combinations.
        older = {k:v for k,v in config.MODEL_CAPABILITIES[fixture.VIDEO].items()
                 if k != "parameter_combinations"}
        with patch.dict(config.MODEL_CAPABILITIES, {fixture.VIDEO: older}):
            canvas.patch_canvas(self.project, saved)
        self.error("MODEL_CAPABILITY_MISMATCH", lambda: node_execution.quote_node(self.project, "video", {}))
        self.assertEqual(db.query("SELECT * FROM tasks"), [])
        self.assertEqual(db.query("SELECT * FROM canvas_node_quotes"), [])
        good = main.TaskIn(kind="video", model_id=fixture.VIDEO, params={**params, "duration": 10})
        self.assertTrue(main.estimate_task(good)["submittable"])

    def test_model_api_and_agent_advertise_same_policy_and_preserve_activation(self):
        rules = [{"resolution": ["720p"], "duration": [5]}]
        self.set_policy(rules)
        api = next(m for m in main.models()["items"] if m["id"] == fixture.VIDEO)
        agent = director_workflow.execute_tool("list_models", {"kind": "video"}, {
            "project_id": self.project})
        agent = next(m for m in agent["items"] if m["id"] == fixture.VIDEO)
        self.assertEqual(api["capabilities"], {k:v for k,v in agent["capabilities"].items() if k!="pricing"})
        self.assertEqual(api["capabilities"]["parameter_combinations"], rules)
        quick = next(m for m in quick_tools._list_models({"kind": "video"})["items"]
                     if m["id"] == fixture.VIDEO)
        self.assertEqual(quick["parameter_combinations"], rules)
        with patch.dict(config.MODEL_ACTIVATION, {fixture.VIDEO: "not_opened"}):
            self.assertIn("MODEL_NOT_OPENED", config.model_submittability(fixture.VIDEO)["reasons"])

    def test_bad_combination_configuration_blocks_permission_and_validation(self):
        self.set_policy([])
        self.assertIn("MODEL_SPEC_POLICY_UNAVAILABLE", config.model_submittability(fixture.VIDEO)["reasons"])
        self.assertTrue(config.validate_params_for_model(fixture.VIDEO, "video", {
            "duration": 5, "resolution": "720p", "ratio": "16:9"}))


class EffectiveSpecificationTests(unittest.TestCase):
    setUp = fixture.NodeExecutionTests.setUp
    error = fixture.NodeExecutionTests.error
    asset = fixture.NodeExecutionTests.asset
    create_node = fixture.NodeExecutionTests.create_node
    generate = fixture.NodeExecutionTests.generate

    def policy(self, model, rules):
        self.enterContext(patch.dict(config.MODEL_CAPABILITIES, {
            model: {**config.MODEL_CAPABILITIES[model], "parameter_combinations": rules}}))

    def ready_shots(self, durations=(5, 10)):
        script = projects.add_script(self.project, {"summary": "two cup shots"})
        projects.confirm_script(script["script_id"])
        ids = shots.add_shots(self.project, script["script_id"], [
            {"description": f"cup {i}", "duration_s": duration}
            for i, duration in enumerate(durations)], fixture.VIDEO)["shot_ids"]
        for sid in ids:
            board = shots.generate_storyboard(sid, fixture.IMAGE, True)
            shots.attach_storyboard_asset(board["storyboard_id"], self.asset()["id"])
            shots.review_storyboard(board["storyboard_id"], True)
        return ids

    def test_video_save_quote_and_submit_share_root_mode_and_audio_default(self):
        self.policy(fixture.VIDEO, [{"generation_mode": ["text_to_video", "image_to_video"],
            "ratio": ["16:9"], "resolution": ["720p"], "duration": [5], "generate_audio": [False]}])
        for mode in ("text_to_video", "image_to_video"):
            self.create_node(mode, mode)
            saved = db.loads(db.query("SELECT config_json FROM canvas_nodes WHERE node_id=?", (mode,))[0]["config_json"])
            self.assertNotIn("generation_mode", saved["params"])
            self.assertNotIn("generate_audio", saved["params"])
            quote = node_execution.quote_node(self.project, mode)
            self.assertEqual(quote["input"]["params"]["generation_mode"], mode)
            self.assertIs(quote["input"]["params"]["generate_audio"], False)
            execution = self.generate(quote, mode)
            task = db.get("tasks", execution["task_id"])
            self.assertEqual(db.loads(task["params_json"]), quote["input"]["params"])

    def test_video_explicit_audio_and_mode_restrictions_are_not_overridden(self):
        self.policy(fixture.VIDEO, [{"generation_mode": ["text_to_video"], "generate_audio": [False]}])
        self.error("MODEL_CAPABILITY_MISMATCH", lambda: self.create_node("image", "image_to_video"))
        self.error("MODEL_CAPABILITY_MISMATCH", lambda: self.create_node("audio", "text_to_video",
            params={"duration": 5, "ratio": "16:9", "resolution": "720p", "generate_audio": True}))
        self.assertEqual(db.query("SELECT * FROM canvas_nodes"), [])

    def test_missing_video_options_use_same_defaults_as_quote_and_task(self):
        duration = config.resolve_duration_s(fixture.VIDEO, None, strict=True)
        self.policy(fixture.VIDEO, [{"generation_mode": ["text_to_video"],
            "ratio": ["9:16"], "resolution": ["720p"], "duration": [duration], "generate_audio": [False]}])
        self.create_node("defaults", "text_to_video", params={})
        quote = node_execution.quote_node(self.project, "defaults")
        self.assertEqual({k:quote["input"]["params"][k] for k in ("ratio", "resolution", "duration", "generate_audio")},
            {"ratio": "9:16", "resolution": "720p", "duration": duration, "generate_audio": False})

    def test_saved_video_quote_rechecks_policy_changes_before_creating_task(self):
        self.create_node("video", "text_to_video")
        quote = node_execution.quote_node(self.project, "video")
        self.policy(fixture.VIDEO, [{"generation_mode": ["image_to_video"], "duration": [5]}])
        self.error("MODEL_CAPABILITY_MISMATCH", lambda: self.generate(quote))
        self.assertEqual(db.query("SELECT * FROM tasks"), [])

    def test_image_defaults_and_aligned_ratio_are_frozen_consistently(self):
        self.policy(fixture.IMAGE, [{"generation_mode": ["text_to_image"],
            "size": ["2688x1536"], "ratio": ["16:9"]}])
        self.create_node("wide", params={"size": "2688x1536"})
        quote = node_execution.quote_node(self.project, "wide")
        direct = main.resolve_task(main.TaskIn(kind="image", model_id=fixture.IMAGE,
            params={"prompt": "a cup", "size": "2688x1536"}))
        self.assertEqual(quote["input"]["params"]["ratio"], direct["params"]["ratio"])
        self.assertEqual(direct["params"]["generation_mode"], "text_to_image")
        self.error("MODEL_CAPABILITY_MISMATCH", lambda: main.estimate_task(main.TaskIn(
            kind="image", model_id=fixture.IMAGE,
            params={"prompt": "a cup", "size": "2688x1536", "ratio": "1:1"})))

    def test_defaults_copy_and_invalid_explicit_values_remain_invalid(self):
        original = {"duration": 5, "generate_audio": None}
        self.assertIsNone(model_specs.effective_params("video", original)["generate_audio"])
        self.assertEqual(original, {"duration": 5, "generate_audio": None})
        self.assertEqual(model_specs.effective_params("image", {})["ratio"], "1:1")
        self.assertEqual(model_specs.image_ratio("3000x2000"), "3:2")
        self.assertIsNone(model_specs.image_ratio("invalid"))

    def test_old_storyboard_quote_and_submission_derive_ratio_from_size(self):
        ids = self.ready_shots((5,))
        self.policy(fixture.IMAGE, [{"generation_mode": ["text_to_image"],
            "size": ["2688x1536"], "ratio": ["16:9"]}])
        before = len(db.query("SELECT * FROM tasks"))
        quote = shots.generate_storyboard(ids[0], fixture.IMAGE, False, "2688x1536")
        self.assertTrue(quote["need_confirm"])
        self.assertEqual(len(db.query("SELECT * FROM tasks")), before)
        generated = shots.generate_storyboard(ids[0], fixture.IMAGE, True, "2688x1536")
        params = db.loads(db.get("tasks", generated["task_id"])["params_json"])
        self.assertEqual(params["ratio"], "16:9")
        self.error("MODEL_CAPABILITY_MISMATCH", lambda: shots.quote_storyboard(ids, fixture.IMAGE, "2048x2048"))

    def test_storyboard_reference_quote_uses_actual_image_to_image_mode(self):
        ids = self.ready_shots((5,))
        self.policy(fixture.IMAGE, [{"generation_mode": ["image_to_image"],
            "size": ["2048x2048"], "ratio": ["1:1"]}])
        reference = self.asset()["id"]
        self.assertTrue(shots.generate_storyboard(ids[0], fixture.IMAGE, False,
            input_asset_id=reference)["need_confirm"])
        self.error("MODEL_CAPABILITY_MISMATCH", lambda: shots.generate_storyboard(ids[0], fixture.IMAGE, False))

    def test_batch_checks_each_shot_duration_and_never_quotes_partial_scope(self):
        ids = self.ready_shots()
        self.policy(fixture.VIDEO, [
            {"generation_mode": ["image_to_video"], "resolution": ["720p"], "duration": [5, 10], "generate_audio": [False]},
            {"generation_mode": ["image_to_video"], "resolution": ["1080p"], "duration": [5], "generate_audio": [False]}])
        valid = scope.create_scope(self.project, "clips", ids,
            {"model_id": fixture.VIDEO, "ratio": "16:9", "resolution": "720p"})
        self.assertEqual([item["duration_s"] for item in valid["shots"]], [5, 10])
        invalid = self.error("MODEL_CAPABILITY_MISMATCH", lambda: scope.create_scope(self.project, "clips", ids,
            {"model_id": fixture.VIDEO, "ratio": "16:9", "resolution": "1080p"}))
        self.assertEqual(invalid.detail["shot_id"], ids[1])
        self.assertEqual(len(db.query("SELECT * FROM generation_scopes")), 1)
        scope.confirm_scope(valid["scope_id"])
        submitted = scope.submit_scope(valid["scope_id"])
        for item in submitted["tasks"]:
            params = db.loads(db.get("tasks", item["task_id"])["params_json"])
            self.assertEqual(params["generation_mode"], "image_to_video")
            self.assertIs(params["generate_audio"], False)
        self.assertTrue(scope.submit_scope(valid["scope_id"])["idempotent_replay"])

    def test_batch_rechecks_narrowed_rules_before_any_task_is_created(self):
        ids = self.ready_shots()
        quoted = scope.create_scope(self.project, "clips", ids,
            {"model_id": fixture.VIDEO, "ratio": "16:9", "resolution": "720p"})
        scope.confirm_scope(quoted["scope_id"])
        before = len(db.query("SELECT * FROM tasks"))
        self.policy(fixture.VIDEO, [{"generation_mode": ["image_to_video"], "duration": [5]}])
        self.error("MODEL_CAPABILITY_MISMATCH", lambda: scope.submit_scope(quoted["scope_id"]))
        self.assertEqual(len(db.query("SELECT * FROM tasks")), before)
        self.assertEqual(db.query("SELECT * FROM submit_receipts"), [])


if __name__ == "__main__":
    unittest.main()
