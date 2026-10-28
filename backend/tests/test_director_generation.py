"""Director group actions: isolated SQLite and real services, all networking forbidden."""
from __future__ import annotations

import io
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_read_text = Path.read_text
with patch.object(Path, "read_text", lambda path, *a, **kw:
                  "" if path.name == ".env" else _read_text(path, *a, **kw)):
    from app import canvas, config, db, director_generation as service, node_execution, projects, storage
from app.errors import AppError

IMAGE = "doubao-seedream-5-0-flash-260915"
VIDEO = "doubao-seedance-1-0-pro-fast-251015"


class DirectorGenerationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="director-generation-")
        self.addCleanup(temp.cleanup)
        for key, value in (("DB_PATH", Path(temp.name) / "test.db"),
                           ("ASSETS", Path(temp.name) / "assets"), ("BUDGET_CNY", 20)):
            self.enterContext(patch.object(config, key, value))
        self.enterContext(patch("app.providers_ark._request", side_effect=AssertionError("network forbidden")))
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("network forbidden")))
        self.enterContext(patch("app.worker.start", side_effect=AssertionError("worker forbidden")))
        self.enterContext(patch.object(config, "model_lifecycle_state", return_value={"status": "active"}))
        db.init_db()
        self.project = projects.create_project("director group")['project_id']
        self.other = projects.create_project("other")['project_id']
        self.configs = {}
        self.create_node("first")
        self.create_node("second")

    def create_node(self, node_id, mode="text_to_image", reference=None):
        kind = "image" if mode.endswith("_image") else "video"
        cfg = {"model_id": IMAGE if kind == "image" else VIDEO,
               "generation_mode": mode, "prompt": f"A cup in morning light, {node_id}",
               "params": {"size": "1024x1024"} if kind == "image" else
                         {"duration": 5, "ratio": "16:9", "resolution": "720p"}}
        if reference:
            cfg["input_asset_id"] = reference
        canvas.patch_canvas(self.project, {"nodes": [{"node_id": node_id, "type": kind, "config": cfg}]})
        self.configs[node_id] = cfg

    def prepare(self, request="plan", nodes=None, revisions=None):
        nodes = ["first", "second"] if nodes is None else nodes
        return service.prepare_generation(self.project, nodes, revisions or {node: 1 for node in nodes}, request)

    def confirm(self, action, request="submit"):
        return service.confirm_generation(self.project, action["action_id"], {
            "client_request_id": request, "user_action": "confirm_generate"})

    def error(self, code, callback):
        with self.assertRaises(AppError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def assert_unsubmitted(self, action):
        self.assertEqual(db.query("SELECT * FROM tasks"), [])
        self.assertEqual(db.query("SELECT * FROM batches"), [])
        self.assertEqual(db.query("SELECT * FROM canvas_node_executions"), [])
        self.assertTrue(all(row["consumed_at"] is None for row in db.query("SELECT * FROM canvas_node_quotes")))
        stored = db.get("director_actions", action["action_id"])
        self.assertEqual(stored["status"], "proposed")
        self.assertIsNone(stored["result_json"])

    def asset(self, color="blue"):
        output = io.BytesIO()
        Image.new("RGB", (32, 24), color).save(output, "PNG")
        return storage.save_bytes(output.getvalue(), "image", "test")

    def finish(self, task, success=True):
        db.update("tasks", task["task_id"], {"status": "succeeded" if success else "failed",
                  "output_asset_id": self.asset()["id"] if success else None,
                  "cost_cny": .12 if success else None, "finished_at": db.now()})

    def test_prepare_quotes_every_node_and_replays_without_tasks(self):
        action = self.prepare()
        replay = self.prepare()
        self.assertEqual(action["action_id"], replay["action_id"])
        self.assertFalse(action["idempotent_replay"])
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(action["status"], "proposed")
        self.assertEqual(len(db.query("SELECT * FROM canvas_node_quotes")), 2)
        self.assertEqual(len(db.query("SELECT * FROM director_actions")), 1)
        self.assertAlmostEqual(action["estimated_cost_cny"], sum(q["estimated_cost_cny"] for q in action["quotes"]))
        self.assertFalse(action["side_effects"]["model_called"])
        self.assert_unsubmitted(action)

    def test_storyboard_group_can_submit_with_unknown_legacy_video(self):
        # The real business DB contains this pre-canvas video: integer duration,
        # known price, no current duration capability and unknown remote result.
        old = {"id": "historical-video", "kind": "video", "status": "failed",
               "model_id": "doubao-seedance-2-0-260128",
               "params_json": db.dumps({"duration": 5, "resolution": "720p", "ratio": "9:16"}),
               "error_code": "CREATE_RESULT_UNKNOWN", "cost_cny": None}
        db.insert("tasks", old)
        before = db.get("tasks", old["id"])
        action = self.prepare()
        result = self.confirm(action)
        replay = self.confirm(action)
        self.assertEqual(result["tasks"], replay["tasks"])
        self.assertEqual([db.get("tasks", t["task_id"])["kind"] for t in result["tasks"]], ["image", "image"])
        self.assertEqual(len(db.query("SELECT * FROM tasks")), 3)
        self.assertEqual(db.get("tasks", old["id"]), before)

    def test_legacy_unknown_video_still_reserves_budget_for_storyboards(self):
        db.insert("tasks", {"id": "historical-video", "kind": "video", "status": "failed",
            "model_id": "doubao-seedance-2-0-260128", "error_code": "CREATE_RESULT_UNKNOWN",
            "params_json": db.dumps({"duration": 5, "resolution": "720p", "ratio": "9:16"})})
        action = self.prepare()
        with patch.object(config, "BUDGET_CNY", action["estimated_cost_cny"]):
            self.error("BUDGET_EXCEEDED", lambda: self.confirm(action))
        self.assertEqual(len(db.query("SELECT * FROM tasks")), 1)
        self.assertEqual(db.query("SELECT * FROM canvas_node_executions"), [])
        self.assertTrue(all(q["consumed_at"] is None for q in db.query("SELECT * FROM canvas_node_quotes")))
        self.assertEqual(db.get("director_actions", action["action_id"])["status"], "proposed")

    def test_prepare_late_missing_node_rolls_back_all_quotes(self):
        self.error("CANVAS_NODE_NOT_FOUND", lambda: self.prepare(nodes=["first", "missing"]))
        self.assertEqual(db.query("SELECT * FROM canvas_node_quotes"), [])
        self.assertEqual(db.query("SELECT * FROM director_actions"), [])

    def test_selection_revision_and_mode_validation(self):
        invalid = [([], {}), (["first", "first"], {"first": 1}), (["first"], {}),
                   (["first"], {"first": True}), (["first"], {"first": 0}),
                   (["first"], {"first": 1, "other": 1}),
                   ([str(i) for i in range(21)], {str(i): 1 for i in range(21)})]
        for nodes, revisions in invalid:
            with self.subTest(nodes=nodes, revisions=revisions):
                self.error("VALIDATION_FAILED", lambda: service.prepare_generation(
                    self.project, nodes, revisions, "invalid"))
        self.error("VALIDATION_FAILED", lambda: service.prepare_generation(
            self.project, ["first"], {"first": 1}, "full", "full"))
        self.error("VALIDATION_FAILED", lambda: service.prepare_generation(
            self.project, ["first"], {"first": 1}, ""))
        self.error("CANVAS_REVISION_CONFLICT", lambda: self.prepare(revisions={"first": 1, "second": 2}))
        self.assertEqual(db.query("SELECT * FROM canvas_node_quotes"), [])

    def test_confirm_two_nodes_one_atomic_batch_and_real_receipt(self):
        action = self.prepare()
        receipt = self.confirm(action)
        self.assertEqual(receipt["status"], "running")
        self.assertEqual(receipt["action_status"], "submitted")
        self.assertEqual(len(receipt["tasks"]), 2)
        self.assertEqual(len(receipt["executions"]), 2)
        self.assertFalse(receipt["auto_film"])
        self.assertEqual(len(db.query("SELECT * FROM batches")), 1)
        self.assertEqual(len(db.query("SELECT * FROM canvas_node_executions")), 2)
        for item in receipt["tasks"]:
            task = db.get("tasks", item["task_id"])
            self.assertEqual(task["batch_id"], receipt["batch_id"])
            self.assertEqual(task["external_request_id"], self.project)
            self.assertEqual(task["caller_id"], "director")
            self.assertEqual(db.loads(task["params_json"])["prompt"], self.configs[item["node_id"]]["prompt"])
        self.assertEqual(db.query("SELECT * FROM films"), [])

    def test_film_rejects_images_and_rolls_back_all_quotes(self):
        self.create_node("video", "text_to_video")
        self.error("VALIDATION_FAILED", lambda: service.prepare_generation(
            self.project, ["video", "first"], {"video": 1, "first": 1}, "film", "film"))
        self.assertEqual(db.query("SELECT * FROM canvas_node_quotes"), [])
        self.assertEqual(db.query("SELECT * FROM director_actions"), [])

    def test_film_freezes_explicit_order_and_one_confirmation_scope_without_composing_early(self):
        for node in ("video-a", "video-b"):
            self.create_node(node, "text_to_video")
        ordered = ["video-b", "video-a"]
        action = service.prepare_generation(self.project, ordered, dict.fromkeys(ordered, 1), "film", "film")
        self.assertTrue(action["auto_film"])
        self.assertEqual(action["scope"], {"generate": True, "compose": True, "ordered_node_ids": ordered,
                                          "no_trim": True, "composition_cost_cny": 0})
        self.assertEqual(db.query("SELECT * FROM tasks"), [])
        receipt = self.confirm(action)
        self.assertEqual([task["node_id"] for task in receipt["tasks"]], ordered)
        self.assertEqual(receipt["status"], "running")
        self.assertEqual(receipt["media_status"], "running")
        self.assertEqual(receipt["composition"], {"status": "waiting", "reason": "media_pending"})
        self.assertEqual(db.query("SELECT * FROM director_compositions"), [])
        replay = self.confirm(action)
        self.assertEqual(replay["scope"], receipt["scope"])
        self.assertEqual(len(db.query("SELECT * FROM tasks")), 2)

    def test_confirmation_requires_explicit_user_action(self):
        action = self.prepare()
        for body in ({"client_request_id": "submit"}, {"client_request_id": "submit", "user_action": "yes"}):
            self.error("CONFIRM_NOT_ALLOWED", lambda: service.confirm_generation(self.project, action["action_id"], body))
        self.error("VALIDATION_FAILED", lambda: service.confirm_generation(self.project, action["action_id"], {
            "client_request_id": "submit", "user_action": "confirm_generate", "model_id": "injected"}))
        self.assert_unsubmitted(action)

    def test_second_node_changed_after_quote_rolls_back_whole_group(self):
        action = self.prepare()
        canvas.patch_canvas(self.project, {"nodes": [{"node_id": "second", "config": {
            **self.configs["second"], "prompt": "A different scene"}}]})
        error = self.error("DIRECTOR_ACTION_STALE", lambda: self.confirm(action))
        self.assertEqual(error.detail["node_id"], "second")
        self.assert_unsubmitted(action)

    def test_late_execution_insert_failure_rolls_back_first_member_and_batch(self):
        action = self.prepare()
        with db.conn() as connection:
            connection.execute("CREATE TRIGGER fail_second BEFORE INSERT ON canvas_node_executions "
                               "WHEN NEW.node_id='second' BEGIN SELECT RAISE(ABORT,'late insert failure'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "late insert failure"):
            self.confirm(action)
        self.assert_unsubmitted(action)

    def test_group_budget_is_checked_as_a_total_before_any_task(self):
        action = self.prepare()
        with patch.object(config, "BUDGET_CNY", action["estimated_cost_cny"] - .01):
            self.error("BUDGET_EXCEEDED", lambda: self.confirm(action))
        self.assert_unsubmitted(action)

    def test_group_budget_includes_existing_queued_reservations(self):
        self.create_node("outside")
        quote = node_execution.quote_node(self.project, "outside")
        existing = node_execution.generate_node(self.project, "outside", {
            "quote_id": quote["quote_id"], "client_request_id": "single", "user_action": "confirm_generate"})
        action = self.prepare()
        with patch.object(config, "BUDGET_CNY", action["estimated_cost_cny"] + quote["estimated_cost_cny"] - .01):
            self.error("BUDGET_EXCEEDED", lambda: self.confirm(action))
        self.assertEqual([t["id"] for t in db.query("SELECT * FROM tasks")], [existing["task_id"]])
        self.assertEqual(db.get("director_actions", action["action_id"])["status"], "proposed")
        self.assertEqual(sum(row["consumed_at"] is None for row in db.query("SELECT * FROM canvas_node_quotes")), 2)

    def test_identical_confirmation_replays_after_expiry_and_new_request_cannot_reuse_action(self):
        action = self.prepare()
        first = self.confirm(action)
        db.update("director_actions", action["action_id"], {"expires_at": 0})
        replay = self.confirm(action)
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(first["tasks"], replay["tasks"])
        self.assertEqual(first["batch_id"], replay["batch_id"])
        self.error("DIRECTOR_ACTION_CONFLICT", lambda: self.confirm(action, "different"))
        self.assertEqual(len(db.query("SELECT * FROM tasks")), 2)

    def test_same_confirmation_request_cannot_be_reused_for_another_action(self):
        first, second = self.prepare("first-plan"), self.prepare("second-plan")
        self.confirm(first)
        self.error("DIRECTOR_ACTION_CONFLICT", lambda: self.confirm(second))
        self.assertEqual(len(db.query("SELECT * FROM tasks")), 2)

    def test_response_lost_after_commit_replays_without_duplicate_tasks(self):
        action = self.prepare()
        with patch.object(service, "get_action", side_effect=TimeoutError("response lost")):
            with self.assertRaises(TimeoutError):
                self.confirm(action)
        replay = self.confirm(action)
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(len(replay["tasks"]), 2)
        self.assertEqual(len(db.query("SELECT * FROM tasks")), 2)
        self.assertEqual(len(db.query("SELECT * FROM batches")), 1)

    def test_concurrent_confirmations_create_one_batch(self):
        action = self.prepare()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.confirm(action), range(4)))
        self.assertEqual(len({r["batch_id"] for r in results}), 1)
        self.assertEqual(sum(not r["idempotent_replay"] for r in results), 1)
        self.assertEqual(len(db.query("SELECT * FROM tasks")), 2)

    def test_concurrent_preparations_reuse_the_same_quotes(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.prepare(), range(4)))
        self.assertEqual(len({r["action_id"] for r in results}), 1)
        self.assertEqual(len(db.query("SELECT * FROM canvas_node_quotes")), 2)

    def test_expired_proposal_never_creates_tasks(self):
        action = self.prepare()
        db.update("director_actions", action["action_id"], {"expires_at": 0})
        self.error("DIRECTOR_ACTION_EXPIRED", lambda: self.confirm(action))
        self.assertFalse(service.get_action(self.project, action["action_id"])["requires_user_action"])
        self.assert_unsubmitted(action)

    def test_deleted_and_recreated_node_identity_cannot_reuse_a_quote(self):
        action = self.prepare()
        canvas.patch_canvas(self.project, {"delete_node_ids": ["second"]})
        self.create_node("second")
        self.error("DIRECTOR_ACTION_STALE", lambda: self.confirm(action))
        self.assert_unsubmitted(action)

    def test_layout_only_changes_do_not_invalidate_generation(self):
        action = self.prepare()
        canvas.patch_canvas(self.project, {"viewport": {"x": 800, "y": -200, "zoom": .4},
                                          "nodes": [{"node_id": "first", "position": {"x": -1200, "y": 500}}]})
        self.assertEqual(self.confirm(action)["status"], "running")

    def test_cross_project_and_deleted_project_actions_are_closed(self):
        action = self.prepare()
        body = {"client_request_id": "submit", "user_action": "confirm_generate"}
        self.error("DIRECTOR_ACTION_NOT_FOUND", lambda: service.get_action(self.other, action["action_id"]))
        self.error("DIRECTOR_ACTION_NOT_FOUND", lambda: service.confirm_generation(self.other, action["action_id"], body))
        projects.delete_project(self.project)
        self.error("PROJECT_DELETED", lambda: self.confirm(action))
        self.assertEqual(db.query("SELECT * FROM tasks"), [])

    def test_live_status_reports_running_partial_failed_and_completed_without_retry(self):
        action = self.prepare()
        receipt = self.confirm(action)
        self.finish(receipt["tasks"][0])
        self.assertEqual(service.get_action(self.project, action["action_id"])["status"], "running")
        self.finish(receipt["tasks"][1], False)
        self.assertEqual(service.get_action(self.project, action["action_id"])["status"], "partial")
        self.finish(receipt["tasks"][0], False)
        self.assertEqual(service.get_action(self.project, action["action_id"])["status"], "failed")
        for task in receipt["tasks"]:
            self.finish(task)
        self.assertEqual(service.get_action(self.project, action["action_id"])["status"], "completed")
        self.assertEqual(len(db.query("SELECT * FROM tasks")), 2)
        self.assertEqual(len(db.query("SELECT * FROM batches")), 1)

    def test_remote_unknown_state_is_still_running_and_blocks_new_group(self):
        self.create_node("video", "text_to_video")
        action = self.prepare(nodes=["video"])
        receipt = self.confirm(action)
        db.update("tasks", receipt["tasks"][0]["task_id"], {"status": "failed", "error_code": "CREATE_RESULT_UNKNOWN"})
        self.assertEqual(service.get_action(self.project, action["action_id"])["status"], "running")
        next_action = self.prepare("next", nodes=["video"])
        self.error("NODE_EXECUTION_ACTIVE", lambda: self.confirm(next_action, "next-submit"))
        self.assertEqual(len(db.query("SELECT * FROM tasks")), 1)

    def test_successful_tasks_with_missing_or_replaced_media_are_not_completed_and_never_retried(self):
        action = self.prepare()
        receipt = self.confirm(action)
        first, second = self.asset("red"), self.asset("blue")
        for task, asset in zip(receipt["tasks"], (first, second)):
            db.update("tasks", task["task_id"], {"status": "succeeded", "output_asset_id": asset["id"],
                                                "cost_cny": .12, "finished_at": db.now()})
        original = {table: db.query(f"SELECT * FROM {table} ORDER BY id") for table in (
            "tasks", "canvas_node_executions", "canvas_node_quotes", "director_actions", "batches")}
        self.assertEqual(service.get_action(self.project, action["action_id"])["status"], "completed")
        first_path = storage.asset_path(first)
        original_bytes = first_path.read_bytes()
        first_path.unlink()
        partial = service.get_action(self.project, action["action_id"])
        self.assertEqual(partial["status"], "partial")
        self.assertEqual(partial["executions"][0]["asset_error"]["reason"], "file_missing")
        second_path = storage.asset_path(second)
        second_bytes = second_path.read_bytes()
        second_path.write_bytes(bytes([second_bytes[0] ^ 1]) + second_bytes[1:])
        unavailable = service.get_action(self.project, action["action_id"])
        self.assertEqual(unavailable["status"], "failed")
        self.assertTrue(all(item["status"] == "succeeded" for item in unavailable["executions"]))
        self.assertEqual(unavailable["executions"][1]["asset_error"]["code"], "OUTPUT_ASSET_CHANGED")
        self.assertEqual(self.confirm(action)["status"], "failed")
        first_path.write_bytes(original_bytes)
        second_path.write_bytes(second_bytes)
        self.assertEqual(service.get_action(self.project, action["action_id"])["status"], "completed")
        self.assertEqual({table: db.query(f"SELECT * FROM {table} ORDER BY id") for table in original}, original)

    def test_group_can_mix_all_four_modes_without_script_or_shot_rows(self):
        asset_id = self.asset()["id"]
        modes = ["text_to_image", "image_to_image", "text_to_video", "image_to_video"]
        names = []
        for index, mode in enumerate(modes):
            name = f"mode-{index}"
            self.create_node(name, mode, asset_id if mode.startswith("image_") else None)
            names.append(name)
        result = self.confirm(self.prepare(nodes=names))
        actual = [db.loads(db.get("tasks", t["task_id"])["params_json"])["generation_mode"] for t in result["tasks"]]
        self.assertEqual(actual, modes)
        self.assertEqual(db.query("SELECT * FROM shots"), [])
        self.assertEqual(db.query("SELECT * FROM script_versions"), [])

    def test_reference_content_change_rejects_group(self):
        asset = self.asset()
        self.create_node("second", "image_to_image", asset["id"])
        action = self.prepare(revisions={"first": 1, "second": 2})
        storage.asset_path(asset).write_bytes(b"not the quoted image")
        self.error("DIRECTOR_ACTION_STALE", lambda: self.confirm(action))
        self.assert_unsubmitted(action)


if __name__ == "__main__":
    unittest.main()
