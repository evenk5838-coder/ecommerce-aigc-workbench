"""Existing-video composition with isolated SQLite/assets and real local FFmpeg."""
from __future__ import annotations

import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_read_text = Path.read_text
with patch.object(Path, "read_text", lambda path, *a, **kw:
                  "" if path.name == ".env" else _read_text(path, *a, **kw)):
    from app import canvas, config, db, director_composition as service, director_generation, projects, storage
from app.errors import AppError


class DirectorCompositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            executable = storage.ffmpeg_exe()
            if not Path(executable).is_file():
                raise FileNotFoundError(executable)
        except Exception as exc:
            raise unittest.SkipTest(f"Local FFmpeg unavailable: {type(exc).__name__}") from exc
        cls.clips = []
        with tempfile.TemporaryDirectory(prefix="composition-fixtures-") as tmp:
            for index, (color, duration) in enumerate((("red", .4), ("blue", .6))):
                path = Path(tmp) / f"{index}.mp4"
                result = subprocess.run([executable, "-v", "error", "-f", "lavfi", "-i",
                                         f"color=c={color}:s=32x24:r=10:d={duration}", "-c:v", "libx264",
                                         "-pix_fmt", "yuv420p", "-an", str(path)], capture_output=True, timeout=20)
                if result.returncode:
                    raise AssertionError("Could not create fixed video fixture")
                cls.clips.append(path.read_bytes())

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="composition-test-")
        self.addCleanup(temp.cleanup)
        self.enterContext(patch.object(config, "DB_PATH", Path(temp.name) / "test.db"))
        self.enterContext(patch.object(config, "ASSETS", Path(temp.name) / "assets"))
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("network forbidden")))
        self.enterContext(patch("app.providers_ark._request", side_effect=AssertionError("provider forbidden")))
        self.enterContext(patch("app.worker.start", side_effect=AssertionError("generation worker forbidden")))
        db.init_db()
        self.project = projects.create_project("composition")['project_id']
        self.other = projects.create_project("other")['project_id']
        self.assets = [storage.save_upload_bytes(data, f"{index}.mp4") for index, data in enumerate(self.clips)]
        self.sources = [{"asset_id": asset["id"]} for asset in self.assets]

    def prepare(self, sources=None, request="prepare"):
        return service.prepare_composition(self.project, self.sources if sources is None else sources, request)

    def confirm(self, action, request="confirm"):
        return service.confirm_composition(self.project, action["action_id"], {
            "client_request_id": request, "user_action": "confirm_compose"})

    def error(self, code, fn):
        with self.assertRaises(AppError) as caught:
            fn()
        self.assertEqual(caught.exception.code, code)

    def assert_no_output(self):
        self.assertEqual(db.query("SELECT * FROM canvas_nodes WHERE node_id LIKE 'composition_%'"), [])
        self.assertEqual(db.query("SELECT * FROM director_compositions WHERE status='succeeded'"), [])
        self.assertEqual(db.query("SELECT * FROM tasks"), [])
        self.assertEqual(db.query("SELECT * FROM films"), [])

    def source_node(self, node_id="source", asset=None, project=None):
        asset = asset or self.assets[0]
        canvas.patch_canvas(project or self.project, {"nodes": [{"node_id": node_id, "type": "asset",
                            "position": {"x": 123, "y": 45}, "binding": {"asset_id": asset["id"]}}]})
        return {"asset_id": asset["id"], "node_id": node_id}

    def generated_source(self):
        canvas.patch_canvas(self.project, {"nodes": [{"node_id": "generated", "type": "video", "config": {
            "model_id": "doubao-seedance-1-0-pro-fast-251015", "generation_mode": "text_to_video",
            "prompt": "A red card", "params": {"duration": 5, "ratio": "16:9", "resolution": "720p"}}}]})
        with patch.object(config, "model_lifecycle_state", return_value={"status": "active"}), \
                patch.object(config, "BUDGET_CNY", 20):
            quote = service.node_execution.quote_node(self.project, "generated")
            receipt = service.node_execution.generate_node(self.project, "generated", {
                "quote_id": quote["quote_id"], "client_request_id": "fixture-generation",
                "user_action": "confirm_generate"})
        db.update("tasks", receipt["task_id"], {"status": "succeeded", "output_asset_id": self.assets[0]["id"]})
        return {"asset_id": self.assets[0]["id"], "node_id": "generated",
                "execution_id": receipt["execution_id"]}, receipt

    def pixel(self, asset, seconds):
        result = subprocess.run([storage.ffmpeg_exe(), "-v", "error", "-ss", str(seconds), "-i",
                                 str(storage.asset_path(asset)), "-frames:v", "1", "-pix_fmt", "rgb24",
                                 "-f", "rawvideo", "-"], capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 0)
        self.assertGreaterEqual(len(result.stdout), 3)
        return tuple(result.stdout[:3])

    def film(self, complete=True):
        ordered = ["blue-video", "red-video"]
        for node in ordered:
            canvas.patch_canvas(self.project, {"nodes": [{"node_id": node, "type": "video", "config": {
                "model_id": "doubao-seedance-1-0-pro-fast-251015", "generation_mode": "text_to_video",
                "prompt": node, "params": {"duration": 5, "ratio": "16:9", "resolution": "720p"}}}]})
        with patch.object(config, "model_lifecycle_state", return_value={"status": "active"}), \
                patch.object(config, "BUDGET_CNY", 20):
            action = director_generation.prepare_generation(self.project, ordered, dict.fromkeys(ordered, 1), "film", "film")
            receipt = director_generation.confirm_generation(self.project, action["action_id"], {
                "client_request_id": "film-confirm", "user_action": "confirm_generate"})
        if complete:
            for task, asset in zip(receipt["tasks"], reversed(self.assets)):
                db.update("tasks", task["task_id"], {"status": "succeeded", "output_asset_id": asset["id"],
                                                    "cost_cny": 0, "finished_at": db.now()})
        return receipt

    def read_film(self, action):
        return director_generation.get_action(self.project, action["action_id"])

    def test_film_all_clips_auto_compose_in_approved_order_without_second_confirmation(self):
        action = self.film()
        before = self.read_film(action)
        self.assertEqual(before["status"], "running")
        self.assertEqual(before["media_status"], "completed")
        self.assertEqual(db.query("SELECT * FROM director_compositions"), [])
        job = service.execute_next()
        self.assertEqual(job["status"], "succeeded", job["error_json"])
        after = self.read_film(action)
        composition = after["composition"]
        self.assertEqual(after["status"], "completed")
        self.assertEqual(composition["generation_action_id"], action["action_id"])
        self.assertEqual([s["node_id"] for s in composition["sources"]], action["node_ids"])
        self.assertEqual([s["execution_id"] for s in composition["sources"]], [t["execution_id"] for t in action["tasks"]])
        self.assertAlmostEqual(composition["actual_duration_ms"], 1000, delta=100)
        output = db.get("assets", composition["asset_id"])
        self.assertGreater(self.pixel(output, .1)[2], 200)
        self.assertGreater(self.pixel(output, .8)[0], 200)
        self.assertTrue(composition["asset_available"])
        self.assertIsNotNone(composition["result_node_id"])
        self.assertEqual(len(db.query("SELECT * FROM tasks")), 2)
        self.assertEqual(len(db.query("SELECT * FROM director_compositions")), 1)
        self.assertIsNone(service.execute_next())

    def test_film_pending_partial_and_failed_never_compose_a_successful_subset(self):
        action = self.film(complete=False)
        first, second = action["tasks"]
        db.update("tasks", first["task_id"], {"status": "succeeded", "output_asset_id": self.assets[1]["id"]})
        with patch.object(service, "_decode", side_effect=AssertionError("no partial probing")):
            self.assertIsNone(service.execute_next())
            self.assertEqual(self.read_film(action)["status"], "running")
            db.update("tasks", second["task_id"], {"status": "failed", "error_code": "CREATE_RESULT_UNKNOWN"})
            self.assertIsNone(service.execute_next())
            self.assertEqual(self.read_film(action)["status"], "running")
            db.update("tasks", second["task_id"], {"status": "failed", "error_code": "PROVIDER_UNAVAILABLE"})
            self.assertIsNone(service.execute_next())
            self.assertEqual(self.read_film(action)["status"], "partial")
            self.assertEqual(self.read_film(action)["error"]["code"], "FILM_MEDIA_INCOMPLETE")
            db.update("tasks", first["task_id"], {"status": "failed"})
            self.assertIsNone(service.execute_next())
            self.assertEqual(self.read_film(action)["status"], "failed")
        self.assertEqual(len(db.query("SELECT * FROM tasks")), 2)
        self.assertEqual(db.query("SELECT * FROM director_compositions"), [])

    def test_film_queued_job_survives_reinitialization_without_duplicate_job_or_task(self):
        action = self.film()
        with ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(lambda _: service.enqueue_ready_films(), range(4))), 1)
        queued = self.read_film(action)["composition"]
        self.assertEqual(queued["job"]["status"], "queued")
        db.init_db()
        self.assertEqual(service.recover_interrupted(), 0)
        with patch.object(service, "_freeze_sources", side_effect=AssertionError("must reuse queued job")):
            self.assertEqual(service.enqueue_ready_films(), 0)
            self.assertEqual(service.execute_next()["status"], "succeeded")
        after = self.read_film(action)
        self.assertEqual(after["composition"]["composition_id"], queued["composition_id"])
        self.assertEqual(after["status"], "completed")
        replay = director_generation.confirm_generation(self.project, action["action_id"], {
            "client_request_id": "film-confirm", "user_action": "confirm_generate"})
        self.assertEqual(replay["composition"]["composition_id"], queued["composition_id"])
        self.assertEqual(len(db.query("SELECT * FROM director_compositions")), 1)
        self.assertEqual(len(db.query("SELECT * FROM tasks")), 2)

    def test_film_project_deletion_waits_for_auto_composition_before_and_after_enqueue(self):
        action = self.film()
        self.assertEqual(self.read_film(action)["media_status"], "completed")
        self.assertEqual(db.query("SELECT * FROM director_compositions"), [])
        self.error("PROJECT_BUSY", lambda: projects.delete_project(self.project))
        self.assertIsNone(db.get("projects", self.project)["deleted_at"])
        self.assertEqual(service.enqueue_ready_films(), 1)
        self.error("PROJECT_BUSY", lambda: projects.delete_project(self.project))
        self.assertEqual(service.execute_next()["status"], "succeeded")
        self.assertEqual(self.read_film(action)["status"], "completed")
        projects.delete_project(self.project)
        self.assertIsNotNone(db.get("projects", self.project)["deleted_at"])
        self.assertTrue(all(db.get("assets", asset["id"]) for asset in self.assets))

    def test_film_failed_preparation_releases_project_deletion_without_deleting_clips(self):
        action = self.film()
        with patch.object(service, "_freeze_sources", side_effect=AppError("ASSEMBLY_FAILED", "fixture decode failure")):
            self.assertEqual(service.enqueue_ready_films(), 0)
        self.assertEqual(self.read_film(action)["status"], "failed")
        self.assertIn("composition_error", db.loads(db.get("director_actions", action["action_id"])["result_json"]))
        projects.delete_project(self.project)
        self.assertIsNotNone(db.get("projects", self.project)["deleted_at"])
        self.assertTrue(all(db.get("assets", asset["id"]) for asset in self.assets))
        self.assertTrue(all(storage.asset_path(asset).is_file() for asset in self.assets))

    def test_film_missing_input_records_one_error_and_never_reprobes_or_generates(self):
        action = self.film()
        path = storage.asset_path(self.assets[1])
        original = path.read_bytes()
        path.unlink()
        self.assertEqual(service.enqueue_ready_films(), 0)
        stored = db.get("director_actions", action["action_id"])
        self.assertEqual(db.loads(stored["result_json"])["composition_error"]["code"], "ASSEMBLY_FAILED")
        self.assertEqual(self.read_film(action)["status"], "partial")
        path.write_bytes(original)
        with patch.object(service, "_freeze_sources", side_effect=AssertionError("no repeated probing")):
            self.assertIsNone(service.execute_next())
        after = self.read_film(action)
        self.assertEqual(after["media_status"], "completed")
        self.assertEqual(after["status"], "failed")
        self.assertEqual(after["composition"]["status"], "failed")
        self.assertEqual(db.query("SELECT * FROM director_compositions"), [])
        self.assertEqual(len(db.query("SELECT * FROM tasks")), 2)

    def test_film_probe_has_no_open_write_transaction_and_rechecks_changed_input(self):
        action = self.film()
        original_decode = service._decode
        changed = False
        def decode_and_change(path):
            nonlocal changed
            result = original_decode(path)
            if not changed:
                changed = True
                # A separate connection can write while FFmpeg is probing.
                with sqlite3.connect(config.DB_PATH, timeout=.1) as connection:
                    connection.execute("UPDATE tasks SET output_asset_id=? WHERE id=?",
                                       (self.assets[0]["id"], action["tasks"][0]["task_id"]))
            return result
        with patch.object(service, "_decode", side_effect=decode_and_change):
            self.assertEqual(service.enqueue_ready_films(), 0)
        self.assertEqual(db.query("SELECT * FROM director_compositions"), [])
        self.assertEqual(self.read_film(action)["status"], "failed")
        self.assertEqual(self.read_film(action)["error"]["code"], "DIRECTOR_ACTION_STALE")

    def test_film_deleted_and_recreated_source_cannot_use_old_execution(self):
        action = self.film()
        node_id = action["node_ids"][0]
        canvas.patch_canvas(self.project, {"delete_node_ids": [node_id]})
        canvas.patch_canvas(self.project, {"nodes": [{"node_id": node_id, "type": "video", "config": {
            "model_id": "doubao-seedance-1-0-pro-fast-251015", "generation_mode": "text_to_video",
            "prompt": "A new unrelated clip", "params": {"duration": 5, "ratio": "16:9", "resolution": "720p"}}}]})
        with patch.object(service, "_decode", side_effect=AssertionError("changed identity must not probe")):
            self.assertIsNone(service.execute_next())
        self.assertEqual(self.read_film(action)["status"], "failed")
        self.assertEqual(db.query("SELECT * FROM director_compositions"), [])
        self.assertEqual(len(db.query("SELECT * FROM tasks")), 2)

    def test_film_newer_node_execution_does_not_replace_explicitly_approved_output(self):
        action = self.film()
        node_id = action["node_ids"][0]
        with patch.object(config, "model_lifecycle_state", return_value={"status": "active"}), \
                patch.object(config, "BUDGET_CNY", 20):
            quote = service.node_execution.quote_node(self.project, node_id)
            later = service.node_execution.generate_node(self.project, node_id, {
                "quote_id": quote["quote_id"], "client_request_id": "later-generation", "user_action": "confirm_generate"})
        db.update("tasks", later["task_id"], {"status": "succeeded", "output_asset_id": self.assets[0]["id"]})
        self.assertEqual(service.enqueue_ready_films(), 1)
        composition = self.read_film(action)["composition"]
        self.assertEqual(composition["sources"][0]["asset_id"], self.assets[1]["id"])
        self.assertEqual(composition["sources"][0]["execution_id"], action["tasks"][0]["execution_id"])
        self.assertNotEqual(composition["sources"][0]["execution_id"], later["execution_id"])
        self.error("DIRECTOR_ACTION_CONFLICT", lambda: service.confirm_composition(
            self.project, composition["action_id"], {"client_request_id": "second-approval", "user_action": "confirm_compose"}))
        self.assertEqual(len(db.query("SELECT * FROM director_compositions")), 1)

    def test_film_file_missing_after_queue_fails_existing_job_without_replacement(self):
        action = self.film()
        self.assertEqual(service.enqueue_ready_films(), 1)
        storage.asset_path(self.assets[0]).unlink()
        with patch.object(service.assembly, "concat_clips", side_effect=AssertionError("cannot omit a clip")):
            self.assertEqual(service.execute_next()["status"], "failed")
        after = self.read_film(action)
        self.assertEqual(after["status"], "failed")
        self.assertEqual(after["media_status"], "partial")
        self.assertEqual(after["error"]["code"], "ASSEMBLY_FAILED")
        self.assertIsNone(service.execute_next())
        self.assertEqual(len(db.query("SELECT * FROM director_compositions")), 1)

    def test_film_final_link_failure_rolls_back_action_and_job_then_stops(self):
        action = self.film()
        with db.conn() as connection:
            connection.execute("CREATE TRIGGER reject_film_link BEFORE UPDATE ON director_actions "
                               "WHEN NEW.kind='generation' AND json_extract(NEW.result_json,'$.composition_id') IS NOT NULL "
                               "BEGIN SELECT RAISE(ABORT,'link failure'); END")
        self.assertEqual(service.enqueue_ready_films(), 0)
        self.assertEqual(db.query("SELECT * FROM director_compositions"), [])
        self.assertEqual(len(db.query("SELECT * FROM director_actions")), 1)
        self.assertEqual(self.read_film(action)["status"], "failed")
        self.assertIsNone(service.execute_next())

    def test_film_interrupted_render_is_never_requeued(self):
        action = self.film()
        self.assertEqual(service.enqueue_ready_films(), 1)
        job = self.read_film(action)["composition"]
        db.update("director_compositions", job["composition_id"], {"status": "running"})
        self.assertEqual(service.recover_interrupted(), 1)
        with patch.object(service.assembly, "concat_clips", side_effect=AssertionError("no rerender")):
            self.assertIsNone(service.execute_next())
        after = self.read_film(action)
        self.assertEqual(after["status"], "failed")
        self.assertEqual(after["error"]["code"], "TASK_INTERRUPTED")
        self.assertEqual(len(db.query("SELECT * FROM director_compositions")), 1)

    def test_film_final_output_missing_or_replaced_is_not_completed(self):
        action = self.film()
        self.assertEqual(service.execute_next()["status"], "succeeded")
        output = db.get("assets", self.read_film(action)["composition"]["asset_id"])
        path = storage.asset_path(output)
        original = path.read_bytes()
        path.write_bytes(bytes([original[0] ^ 1]) + original[1:])
        after = self.read_film(action)
        self.assertEqual(after["status"], "failed")
        self.assertEqual(after["error"]["code"], "OUTPUT_ASSET_CHANGED")
        self.assertFalse(after["composition"]["asset_available"])
        self.assertEqual(after["composition"]["job"]["status"], "succeeded")
        path.unlink()
        self.assertEqual(self.read_film(action)["error"]["reason"], "file_missing")
        self.assertIsNone(service.execute_next())

    def test_prepare_freezes_order_sha_metadata_without_render_or_job(self):
        with patch.object(service.assembly, "concat_clips", side_effect=AssertionError("must not render")):
            action = self.prepare()
        self.assertEqual([item["asset_id"] for item in action["sources"]], [item["asset_id"] for item in self.sources])
        self.assertEqual([item["sha256"] for item in action["sources"]], [item["sha256"] for item in self.assets])
        self.assertEqual(action["planned_duration_ms"], 1000)
        self.assertEqual(action["estimated_cost_cny"], 0)
        self.assertTrue(action["requires_user_action"])
        self.assertFalse(action["model_called"])
        self.assertEqual(db.query("SELECT * FROM director_compositions"), [])
        self.assert_no_output()
        with patch.object(service, "_decode", side_effect=AssertionError("replay must not probe")):
            replay = self.prepare()
        self.assertEqual(replay["action_id"], action["action_id"])
        self.assertTrue(replay["idempotent_replay"])

    def test_actual_concat_order_duration_and_node_preserve_layout(self):
        source = self.source_node()
        canvas.patch_canvas(self.project, {"viewport": {"x": 200, "y": -90, "zoom": .6}})
        before = canvas.get_canvas(self.project)
        action = self.prepare([source, self.sources[1]])
        receipt = self.confirm(action)
        self.assertEqual(receipt["job"]["status"], "queued")
        job = service.execute_next()
        self.assertEqual(job["status"], "succeeded", job["error_json"])
        complete = service.get_composition_action(self.project, action["action_id"])
        self.assertEqual(complete["status"], "completed")
        self.assertAlmostEqual(complete["actual_duration_ms"], 1000, delta=100)
        asset = db.get("assets", complete["asset_id"])
        red, blue = self.pixel(asset, .1), self.pixel(asset, .7)
        self.assertGreater(red[0], 200)
        self.assertLess(red[2], 30)
        self.assertGreater(blue[2], 200)
        self.assertLess(blue[0], 30)
        after = canvas.get_canvas(self.project)
        self.assertEqual(after["viewport"], before["viewport"])
        self.assertEqual(after["layout_revision"], before["layout_revision"] + 1)
        self.assertEqual(after["edges"], [{"from": "source", "to": complete["result_node_id"]}])
        result_node = next(node for node in after["nodes"] if node["node_id"] == complete["result_node_id"])
        self.assertEqual(result_node["type"], "asset")
        self.assertEqual(result_node["binding"]["asset_id"], asset["id"])
        self.assertEqual(result_node["config"]["title"], "成片视频")
        self.assertIsNone(service.execute_next())
        replay = self.confirm(action)
        self.assertEqual(replay["result_node_id"], complete["result_node_id"])
        self.assertEqual(len(db.query("SELECT * FROM director_compositions")), 1)

    def test_reversed_and_repeated_clips_keep_explicit_sequence(self):
        action = self.prepare([self.sources[1], self.sources[0], self.sources[1]])
        self.confirm(action)
        self.assertEqual(service.execute_next()["status"], "succeeded")
        receipt = service.get_composition_action(self.project, action["action_id"])
        self.assertAlmostEqual(receipt["actual_duration_ms"], 1600, delta=100)
        asset = db.get("assets", receipt["asset_id"])
        self.assertGreater(self.pixel(asset, .1)[2], 200)
        self.assertGreater(self.pixel(asset, .7)[0], 200)
        self.assertGreater(self.pixel(asset, 1.2)[2], 200)

    def test_bad_selection_and_unknown_fields_rejected(self):
        for sources in ([], [None], [{"asset_id": ""}], [{"asset_id": "missing"}],
                        [{"asset_id": self.assets[0]["id"], "url": "https://invalid/"}], self.sources * 11):
            with self.subTest(sources=sources):
                with self.assertRaises(AppError):
                    self.prepare(sources)
        self.assertEqual(db.query("SELECT * FROM director_actions"), [])

    def test_source_node_project_and_asset_binding_checked(self):
        self.source_node(project=self.other)
        self.error("CANVAS_INVALID_REFERENCE", lambda: self.prepare([{"asset_id": self.assets[0]["id"], "node_id": "source"}]))
        source = self.source_node()
        self.error("CANVAS_INVALID_REFERENCE", lambda: self.prepare([{**source, "asset_id": self.assets[1]["id"]}]))

    def test_execution_source_requires_success_and_exact_project_asset_identity(self):
        source, receipt = self.generated_source()
        self.error("CANVAS_INVALID_REFERENCE", lambda: self.prepare([{**source, "asset_id": self.assets[1]["id"]}]))
        self.error("CANVAS_INVALID_REFERENCE", lambda: service.prepare_composition(self.other, [source], "other"))
        action = self.prepare([source])
        self.assertEqual(action["sources"][0]["execution_id"], receipt["execution_id"])
        inferred = self.prepare([{key: value for key, value in source.items() if key != "execution_id"}], "inferred")
        self.assertEqual(inferred["sources"][0]["execution_id"], receipt["execution_id"])
        db.update("tasks", receipt["task_id"], {"status": "failed"})
        self.error("DIRECTOR_ACTION_STALE", lambda: self.confirm(action))
        self.assertEqual(db.query("SELECT * FROM director_compositions"), [])

    def test_recreated_source_identity_stales_action(self):
        source = self.source_node()
        action = self.prepare([source])
        canvas.patch_canvas(self.project, {"delete_node_ids": ["source"]})
        self.source_node()
        self.error("DIRECTOR_ACTION_STALE", lambda: self.confirm(action))
        self.assertEqual(db.query("SELECT * FROM director_compositions"), [])

    def test_confirm_file_change_rejected_before_job(self):
        action = self.prepare()
        storage.asset_path(self.assets[1]).write_bytes(b"changed")
        self.error("DIRECTOR_ACTION_STALE", lambda: self.confirm(action))
        self.assertEqual(db.query("SELECT * FROM director_compositions"), [])

    def test_missing_input_after_confirmation_fails_without_partial_render(self):
        action = self.prepare()
        self.confirm(action)
        storage.asset_path(self.assets[1]).unlink()
        with patch.object(service.assembly, "concat_clips", side_effect=AssertionError("must not drop missing clip")):
            job = service.execute_next()
        self.assertEqual(job["status"], "failed")
        self.assertEqual(service.get_composition_action(self.project, action["action_id"])["status"], "failed")
        self.assert_no_output()
        self.assertIsNone(service.execute_next())

    def test_missing_or_invalid_video_cannot_prepare(self):
        storage.asset_path(self.assets[1]).unlink()
        self.error("ASSEMBLY_FAILED", self.prepare)
        storage.asset_path(self.assets[1]).write_bytes(b"not a video")
        import hashlib
        db.update("assets", self.assets[1]["id"], {"sha256": hashlib.sha256(b"not a video").hexdigest()})
        self.error("ASSEMBLY_FAILED", self.prepare)
        self.assertEqual(db.query("SELECT * FROM director_actions"), [])

    def test_partial_concat_never_publishes_success(self):
        action = self.prepare()
        self.confirm(action)
        def incomplete(paths, output):
            output.write_bytes(paths[0].read_bytes())
            return True, None
        with patch.object(service.assembly, "concat_clips", side_effect=incomplete):
            job = service.execute_next()
        self.assertEqual(job["status"], "failed")
        self.assert_no_output()

    def test_final_commit_rolls_back_result_node_and_records_failure(self):
        action = self.prepare()
        self.confirm(action)
        with db.conn() as connection:
            connection.execute("CREATE TRIGGER fail_publish BEFORE UPDATE ON director_compositions "
                               "WHEN NEW.status='succeeded' BEGIN SELECT RAISE(ABORT,'test publish failure'); END")
        job = service.execute_next()
        self.assertEqual(job["status"], "failed")
        self.assertIsNone(job["output_asset_id"])
        self.assertIsNone(job["result_node_id"])
        self.assert_no_output()
        # Content-addressed storage may retain the unreferenced output; it is not
        # presented as a finished job or canvas node and is never silently retried.
        self.assertIsNone(service.execute_next())

    def test_final_commit_and_failure_write_interruption_recovers_without_rerender(self):
        action = self.prepare()
        receipt = self.confirm(action)
        with db.conn() as connection:
            connection.execute("CREATE TRIGGER fail_writes BEFORE UPDATE ON director_compositions "
                               "WHEN NEW.status IN ('succeeded','failed') BEGIN SELECT RAISE(ABORT,'test disk failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            service.execute_next()
        self.assertEqual(db.get("director_compositions", receipt["composition_id"])["status"], "running")
        self.assert_no_output()
        self.assertEqual(service.recover_interrupted(), 1)
        self.assertEqual(service.recover_interrupted(), 0)
        self.assertEqual(service.get_composition_action(self.project, action["action_id"])["job"]["status"], "interrupted")
        with patch.object(service.assembly, "concat_clips", side_effect=AssertionError("no auto retry")):
            self.assertIsNone(service.execute_next())

    def test_confirmation_idempotency_expiry_and_scope_collision(self):
        action = self.prepare()
        receipt = self.confirm(action)
        db.update("director_actions", action["action_id"], {"expires_at": 0})
        replay = self.confirm(action)
        self.assertEqual(receipt["composition_id"], replay["composition_id"])
        self.error("DIRECTOR_ACTION_CONFLICT", lambda: self.confirm(action, "new"))
        other = self.prepare(request="other")
        self.error("DIRECTOR_ACTION_CONFLICT", lambda: self.confirm(other))
        db.update("director_actions", other["action_id"], {"expires_at": 0})
        self.error("DIRECTOR_ACTION_EXPIRED", lambda: self.confirm(other, "other"))
        self.assertEqual(len(db.query("SELECT * FROM director_compositions")), 1)

    def test_confirmation_requires_explicit_action_and_project(self):
        action = self.prepare()
        self.error("CONFIRM_NOT_ALLOWED", lambda: service.confirm_composition(self.project, action["action_id"], {
            "client_request_id": "x", "user_action": "confirm_generate"}))
        self.error("VALIDATION_FAILED", lambda: service.confirm_composition(self.project, action["action_id"], {
            "client_request_id": "x", "user_action": "confirm_compose", "sources": []}))
        self.error("DIRECTOR_ACTION_NOT_FOUND", lambda: service.get_composition_action(self.other, action["action_id"]))
        self.error("DIRECTOR_ACTION_NOT_FOUND", lambda: service.confirm_composition(self.other, action["action_id"], {
            "client_request_id": "x", "user_action": "confirm_compose"}))

    def test_response_lost_after_commit_replays_same_job(self):
        action = self.prepare()
        with patch.object(service, "get_composition_action", side_effect=TimeoutError("lost response")):
            with self.assertRaises(TimeoutError):
                self.confirm(action)
        receipt = self.confirm(action)
        self.assertTrue(receipt["idempotent_replay"])
        self.assertEqual(len(db.query("SELECT * FROM director_compositions")), 1)

    def test_concurrent_confirmation_and_claim_do_not_duplicate(self):
        action = self.prepare()
        with ThreadPoolExecutor(max_workers=4) as pool:
            receipts = list(pool.map(lambda _: self.confirm(action), range(4)))
        self.assertEqual(len({receipt["composition_id"] for receipt in receipts}), 1)
        real_concat = service.assembly.concat_clips
        with patch.object(service.assembly, "concat_clips", wraps=real_concat) as concat:
            with ThreadPoolExecutor(max_workers=4) as pool:
                jobs = list(pool.map(lambda _: service.execute_next(), range(4)))
        self.assertEqual(concat.call_count, 1)
        self.assertEqual(sum(job is not None for job in jobs), 1)
        self.assertEqual(len(db.query("SELECT * FROM canvas_nodes WHERE type='asset'")), 1)

    def test_deleted_project_proposal_cannot_be_confirmed(self):
        action = self.prepare()
        projects.delete_project(self.project)
        self.error("PROJECT_DELETED", lambda: self.confirm(action))
        self.assertEqual(db.query("SELECT * FROM director_compositions"), [])

    def test_controlled_worker_start_stop_executes_only_confirmed_jobs(self):
        action = self.prepare()
        self.confirm(action)
        complete = threading.Event()
        publish = service._publish_result
        def published(*args):
            publish(*args)
            complete.set()
        with patch.object(service, "_publish_result", side_effect=published):
            try:
                service.start()
                first = service._thread
                service.start()
                self.assertIs(service._thread, first)
                self.assertTrue(complete.wait(5), "confirmed composition did not finish")
            finally:
                service.stop()
        self.assertFalse(service._thread.is_alive())
        self.assertEqual(service.get_composition_action(self.project, action["action_id"])["status"], "completed")


if __name__ == "__main__":
    unittest.main()
