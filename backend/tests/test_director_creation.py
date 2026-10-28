"""Isolated creation tools: real temporary SQLite/assets, no provider or worker."""
from __future__ import annotations

import copy
import io
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_read_text = Path.read_text
with patch.object(Path, "read_text", lambda path, *a, **kw:
                  "" if path.name == ".env" else _read_text(path, *a, **kw)):
    from app import canvas, config, db, director, director_creation as service, node_execution, projects, storage
from app.errors import AppError

IMAGE = "doubao-seedream-5-0-flash-260915"
VIDEO = "doubao-seedance-1-0-pro-fast-251015"


class DirectorCreationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="director-creation-")
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.db_path = self.path / "test.db"
        for name, value in (("DB_PATH", self.db_path), ("ASSETS", self.path / "assets"),
                            ("DATA", self.path), ("MODELS", self.path / "models"), ("BUDGET_CNY", 20)):
            self.enterContext(patch.object(config, name, value))
        actual_connect = sqlite3.connect

        def isolated_connect(database, *args, **kwargs):
            if Path(str(database)).resolve() != self.db_path.resolve():
                raise AssertionError("Only this temporary database may be opened")
            return actual_connect(database, *args, **kwargs)

        self.enterContext(patch("sqlite3.connect", side_effect=isolated_connect))
        for target in ("socket.create_connection", "socket.socket.connect", "socket.socket.connect_ex",
                       "app.providers_ark._request", "app.assistant.provider._deepseek_post", "app.worker.start"):
            self.enterContext(patch(target, side_effect=AssertionError("Network/model/worker forbidden")))
        self.enterContext(patch.object(config, "model_lifecycle_state", return_value={"status": "active"}))
        db.init_db()
        self.project = projects.create_project("creation", {"subject": "one cup"})["project_id"]
        self.other = projects.create_project("other")["project_id"]

    def error(self, code, callback):
        with self.assertRaises(AppError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def ctx(self, request="request-1"):
        return {"project_id": self.project, "request_id": request}

    @staticmethod
    def script_args(summary="A single cup rotates", **extra):
        return {"summary": summary, "shots": [{"hint": "Cup on a table", "duration_s": 5}], **extra}

    def script(self, confirmed=True):
        result = service.write_script(self.project, self.script_args(), self.ctx("script"))
        if confirmed:
            projects.confirm_script(result["target_id"])
        return result

    def text_node(self, node_id="notes", project=None):
        canvas.patch_canvas(project or self.project, {"nodes": [{"node_id": node_id, "type": "text",
            "config": {"text": "A cup turns in morning light"}, "position": {"x": 100, "y": 200}}]})
        return node_id

    def board_args(self, source="notes", revision=1):
        return {"source_node_id": source, "expected_source_revision": revision,
                "model_id": IMAGE, "params": {"ratio": "16:9"},
                "shots": [{"prompt": "Cup on a table", "duration_s": 5}, {"prompt": "Cup detail", "duration_s": 5}]}

    def asset(self, color="blue"):
        stream = io.BytesIO()
        Image.new("RGB", (32, 24), color).save(stream, "PNG")
        return storage.save_bytes(stream.getvalue(), "image", "fixture")

    def imported(self, node_id="imported", project=None):
        asset = self.asset()
        canvas.patch_canvas(project or self.project, {"nodes": [{"node_id": node_id, "type": "asset",
            "binding": {"asset_id": asset["id"]}, "position": {"x": 20, "y": 30}}]})
        return asset

    def video_args(self, asset, node_id=None, revision=1, **source):
        item = {"asset_id": asset["id"], "prompt": "Slow orbit around the cup", **source}
        if node_id:
            item.update(node_id=node_id, expected_config_revision=revision)
        return {"model_id": VIDEO, "params": {"duration": 5}, "sources": [item]}

    def snapshot(self):
        result = {"project": db.get("projects", self.project)}
        for table in ("script_versions", "canvas_nodes", "canvas_states", "shots", "director_proposals",
                      "director_tool_operations", "confirmations"):
            order = "project_id" if table == "canvas_states" else "id"
            result[table] = db.query(f"SELECT * FROM {table} WHERE project_id=? ORDER BY {order}", (self.project,))
        return result

    def assert_no_generation(self):
        for table in ("tasks", "canvas_node_quotes", "canvas_node_executions", "storyboards", "shot_takes", "generation_scopes"):
            self.assertEqual(db.query(f"SELECT * FROM {table}"), [], table)

    def test_script_version_node_proposal_and_receipt_are_atomic_and_replay_exactly(self):
        args = self.script_args()
        first = service.write_script(self.project, args, self.ctx())
        node = canvas.get_canvas(self.project)["nodes"][0]
        self.assertEqual(node["node_id"], "script")
        self.assertEqual(node["binding"]["script_id"], first["target_id"])
        self.assertEqual(first["target_version"], 1)
        self.assertFalse(first["confirmed"])
        self.assertEqual(db.query("SELECT * FROM confirmations"), [])
        # A retry after the client missed the committed result must not append v2.
        replay = service.write_script(self.project, dict(reversed(list(args.items()))), self.ctx())
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["target_id"], first["target_id"])
        self.assertEqual(len(projects.list_scripts(self.project)), 1)
        self.assertEqual(len(db.query("SELECT * FROM director_tool_operations")), 1)
        self.assert_no_generation()

    def test_script_edit_requires_current_base_and_keeps_stable_binding(self):
        first = self.script()
        self.error("SCRIPT_VERSION_CONFLICT", lambda: service.write_script(self.project, self.script_args("changed"), self.ctx("edit")))
        self.error("SCRIPT_VERSION_CONFLICT", lambda: service.write_script(self.project, self.script_args("changed", base_version=0), self.ctx("edit")))
        second = service.write_script(self.project, self.script_args("changed", base_version=1), self.ctx("edit"))
        self.assertEqual(second["target_version"], 2)
        self.assertIsNone(projects.get_script(second["target_id"])["confirmed_at"])
        self.assertEqual(canvas.get_canvas(self.project)["nodes"][0]["binding"]["script_id"], second["target_id"])
        self.assertEqual(projects.get_script(first["target_id"])["content"]["summary"], "A single cup rotates")

    def test_receipt_failure_rolls_back_script_and_all_nodes(self):
        self.text_node()
        asset = self.imported()
        before = self.snapshot()
        with db.conn() as connection:
            connection.execute("CREATE TRIGGER reject_tool_receipt BEFORE INSERT ON director_tool_operations "
                               "BEGIN SELECT RAISE(ABORT, 'receipt write failed'); END")
        for callback in (lambda: service.write_script(self.project, self.script_args(), self.ctx()),
                         lambda: service.prepare_storyboards(self.project, self.board_args(), self.ctx()),
                         lambda: service.prepare_video_nodes(self.project, self.video_args(asset, "imported"), self.ctx())):
            with self.assertRaisesRegex(sqlite3.IntegrityError, "receipt write failed"):
                callback()
            self.assertEqual(self.snapshot(), before)
        self.assert_no_generation()

    def test_context_prioritizes_selected_nodes_and_new_draft_over_old_media(self):
        asset = self.imported()
        self.script(confirmed=False)
        self.assertEqual(service.creation_context(self.project)["current_role"], "writer")
        self.assertEqual(service.creation_context(self.project, ["imported"])["current_role"], "cinematographer")
        prepared = service.prepare_video_nodes(self.project, self.video_args(asset), self.ctx("video"))
        self.assertEqual(service.creation_context(self.project, prepared["node_ids"])["current_role"], "cinematographer")
        self.text_node()
        boards = service.prepare_storyboards(self.project, self.board_args(), self.ctx("boards"))
        self.assertEqual(service.creation_context(self.project, boards["node_ids"])["current_role"], "director")
        self.assert_no_generation()

    def test_context_bounds_keep_selection_and_validate_all_project_bindings(self):
        canvas.patch_canvas(self.project, {"nodes": [{"node_id": f"note-{index}", "type": "text", "config": {"text": "hello"}}
                                                        for index in range(42)]})
        context = service.creation_context(self.project, ["note-41"])
        self.assertEqual(context["nodes"][0]["node_id"], "note-41")
        self.assertEqual(len(context["nodes"]), 40)
        self.assertEqual(context["total_nodes"], 42)
        self.assertTrue(context["nodes_truncated"])
        self.error("VALIDATION_FAILED", lambda: service.creation_context(self.project, ""))
        foreign = projects.add_script(self.other, {"summary": "private"})
        row = db.query("SELECT * FROM canvas_nodes WHERE project_id=? LIMIT 1", (self.project,))[0]
        db.update("canvas_nodes", row["id"], {"binding_json": db.dumps({"script_id": foreign["script_id"]})})
        self.error("CANVAS_INVALID_REFERENCE", lambda: service.creation_context(self.project, ["note-41"]))
        self.assert_no_generation()

    def test_confirmed_script_creates_ordered_real_shots_and_image_nodes_without_tasks(self):
        script = self.script()
        before = canvas.get_canvas(self.project)
        prepared = service.prepare_storyboards(self.project, self.board_args("script"), self.ctx("boards"))
        self.assertEqual(len(prepared["node_ids"]), 2)
        self.assertEqual(prepared["source"]["script_id"], script["target_id"])
        self.assertEqual(prepared["layout_revision"], before["layout_revision"] + 1)
        self.assertEqual([x["seq"] for x in prepared["artifacts"]], [1, 2])
        for artifact in prepared["artifacts"]:
            shot = db.get("shots", artifact["binding"]["shot_id"])
            self.assertEqual(shot["script_version_id"], script["target_id"])
            self.assertEqual(artifact["config"]["params"], {"ratio": "16:9", "size": "2688x1536"})
            node = db.query("SELECT * FROM canvas_nodes WHERE project_id=? AND node_id=?", (self.project, artifact["node_id"]))[0]
            frozen, _ = node_execution._freeze(node)
            self.assertEqual(frozen["params"]["generation_mode"], "text_to_image")
        self.assert_no_generation()

    def test_draft_and_invalidated_script_confirmation_are_not_media_authority(self):
        script = self.script(confirmed=False)
        self.error("SCRIPT_NOT_CONFIRMED", lambda: service.prepare_storyboards(self.project, self.board_args("script"), self.ctx()))
        projects.confirm_script(script["target_id"])
        director.invalidate("script", project_id=self.project, reason="brief_changed")
        self.assertIsNotNone(projects.get_script(script["target_id"])["confirmed_at"])
        self.error("SCRIPT_NOT_CONFIRMED", lambda: service.prepare_storyboards(self.project, self.board_args("script"), self.ctx()))
        self.assert_no_generation()

    def test_free_text_independent_plan_replays_after_source_change_and_preserves_layout(self):
        self.text_node()
        canvas.patch_canvas(self.project, {"viewport": {"x": 44, "y": 55, "zoom": .5}, "edges": []})
        args = self.board_args()
        first = service.prepare_storyboards(self.project, args, self.ctx())
        self.assertEqual(first["source"]["type"], "text")
        self.assertEqual(projects.list_scripts(self.project), [])
        self.assertEqual(canvas.get_canvas(self.project)["viewport"], {"x": 44, "y": 55, "zoom": .5})
        canvas.patch_canvas(self.project, {"nodes": [{"node_id": "notes", "config": {"text": "Different"}}]})
        replay = service.prepare_storyboards(self.project, args, self.ctx())
        self.assertEqual(replay["node_ids"], first["node_ids"])
        self.error("CANVAS_REVISION_CONFLICT", lambda: service.prepare_storyboards(self.project, args, self.ctx("new-request")))
        self.assertEqual(len(db.query("SELECT * FROM shots")), 2)
        self.assert_no_generation()

    def test_invalid_second_shot_rolls_back_the_first_and_receipt(self):
        self.text_node()
        before = self.snapshot()
        args = self.board_args()
        args["shots"][1]["params"] = {"size": "1x1"}
        self.error("MODEL_CAPABILITY_MISMATCH", lambda: service.prepare_storyboards(self.project, args, self.ctx()))
        self.assertEqual(self.snapshot(), before)
        self.assert_no_generation()

    def test_invalid_size_type_is_structured_validation_error(self):
        self.text_node()
        before = self.snapshot()
        for size in ([], None, "0x0", "-1x10"):
            args = self.board_args()
            args["params"] = {"size": size}
            self.error("MODEL_CAPABILITY_MISMATCH", lambda: service.prepare_storyboards(self.project, args, self.ctx()))
            self.assertEqual(self.snapshot(), before)

    def test_imported_image_and_library_asset_can_prepare_video_without_script(self):
        asset = self.imported()
        for index, node_id in enumerate(("imported", None)):
            args = self.video_args(asset, node_id)
            result = service.prepare_video_nodes(self.project, args, self.ctx(f"video-{index}"))
            artifact = result["artifacts"][0]
            self.assertEqual(artifact["config"]["input_asset_id"], asset["id"])
            self.assertEqual(artifact["config"]["generation_mode"], "image_to_video")
            self.assertEqual(artifact["source"]["asset"]["sha256"], asset["sha256"])
            self.assertEqual(artifact["binding"], {})
            replay = service.prepare_video_nodes(self.project, args, self.ctx(f"video-{index}"))
            self.assertEqual(replay["node_ids"], result["node_ids"])
        self.assertEqual(projects.list_scripts(self.project), [])
        self.assertEqual(db.query("SELECT * FROM shots"), [])
        self.assert_no_generation()

    def test_source_node_revision_and_selected_asset_must_match(self):
        asset = self.imported()
        different = self.asset("red")
        self.error("CANVAS_INVALID_REFERENCE", lambda: service.prepare_video_nodes(self.project, self.video_args(different, "imported"), self.ctx()))
        canvas.patch_canvas(self.project, {"nodes": [{"node_id": "imported", "binding": {"asset_id": different["id"]}}]})
        self.error("CANVAS_REVISION_CONFLICT", lambda: service.prepare_video_nodes(self.project, self.video_args(asset, "imported"), self.ctx()))
        result = service.prepare_video_nodes(self.project, self.video_args(different, "imported", 2), self.ctx())
        self.assertEqual(result["artifacts"][0]["config"]["input_asset_id"], different["id"])
        self.assert_no_generation()

    def test_source_file_change_and_nonimage_assets_fail_before_node_creation(self):
        asset = self.imported()
        storage.asset_path(asset).write_bytes(b"changed bytes")
        before = self.snapshot()
        self.error("INVALID_REFERENCE_IMAGE", lambda: service.prepare_video_nodes(self.project, self.video_args(asset, "imported"), self.ctx()))
        self.assertEqual(self.snapshot(), before)
        db.update("assets", asset["id"], {"kind": "video"})
        self.error("INVALID_REFERENCE_IMAGE", lambda: service.prepare_video_nodes(self.project, self.video_args(asset), self.ctx()))
        self.assert_no_generation()

    def test_context_and_writes_reject_cross_project_nodes_and_context(self):
        self.text_node("foreign", self.other)
        self.error("CANVAS_NODE_NOT_FOUND", lambda: service.creation_context(self.project, ["foreign"]))
        self.error("CANVAS_NODE_NOT_FOUND", lambda: service.prepare_storyboards(self.project, self.board_args("foreign"), self.ctx()))
        self.error("PROJECT_SESSION_MISMATCH", lambda: service.write_script(self.project, self.script_args(),
            {"project_id": self.other, "request_id": "foreign"}))
        asset = self.imported("foreign-image", self.other)
        self.error("CANVAS_NODE_NOT_FOUND", lambda: service.prepare_video_nodes(self.project, self.video_args(asset, "foreign-image"), self.ctx()))
        self.assert_no_generation()

    def test_context_has_actual_role_content_config_binding_and_explicit_bounds(self):
        self.text_node()
        script = self.script(confirmed=False)
        context = service.creation_context(self.project, ["script"])
        self.assertEqual(context["current_role"], "writer")
        self.assertEqual(context["nodes"][0]["script_content"]["summary"], "A single cup rotates")
        self.assertEqual(context["nodes"][0]["binding"]["script_id"], script["target_id"])
        projects.confirm_script(script["target_id"])
        self.assertEqual(service.creation_context(self.project, ["script"])["current_role"], "director")
        asset = self.imported()
        focused = service.creation_context(self.project, ["imported"])
        self.assertEqual(focused["current_role"], "cinematographer")
        self.assertEqual(focused["nodes"][0]["asset"]["id"], asset["id"])
        canvas.patch_canvas(self.project, {"nodes": [{"node_id": "notes", "config": {"text": "a" * 7000}}]})
        focused = service.creation_context(self.project, ["notes"])
        self.assertTrue(focused["nodes"][0]["content_truncated"])
        self.assertEqual(len(focused["nodes"][0]["config"]["text"]), 6000)
        self.assert_no_generation()

    def test_generation_output_source_is_successful_exact_and_stays_frozen(self):
        canvas.patch_canvas(self.project, {"nodes": [{"node_id": "picture", "type": "image", "config": {
            "model_id": IMAGE, "generation_mode": "text_to_image", "prompt": "cup", "params": {"size": "1024x1024"}}}]})
        quote = node_execution.quote_node(self.project, "picture")
        executed = node_execution.generate_node(self.project, "picture", {"quote_id": quote["quote_id"],
            "client_request_id": "fixture-task", "user_action": "confirm_generate"})
        asset = self.asset()
        args = self.video_args(asset, "picture", execution_id=executed["execution_id"])
        self.error("CANVAS_INVALID_REFERENCE", lambda: service.prepare_video_nodes(self.project, args, self.ctx()))
        db.update("tasks", executed["task_id"], {"status": "succeeded", "output_asset_id": asset["id"]})
        first = service.prepare_video_nodes(self.project, args, self.ctx())
        self.assertEqual(first["artifacts"][0]["source"]["execution_id"], executed["execution_id"])
        different = self.asset("red")
        db.update("tasks", executed["task_id"], {"output_asset_id": different["id"]})
        self.error("CANVAS_INVALID_REFERENCE", lambda: service.prepare_video_nodes(self.project, args, self.ctx("later")))
        replay = service.prepare_video_nodes(self.project, args, self.ctx())
        self.assertEqual(replay["artifacts"][0]["config"]["input_asset_id"], asset["id"])
        self.assertEqual(len(db.query("SELECT * FROM tasks")), 1, "Only the explicit fixture execution exists")
        self.assertEqual(db.query("SELECT * FROM shot_takes"), [])
        self.assertEqual(db.query("SELECT * FROM storyboards"), [])

    def test_invalid_model_capabilities_rejected_without_any_prepared_nodes(self):
        asset = self.imported()
        before = self.snapshot()
        base = self.video_args(asset)
        for params in ({"duration": 2.5}, {"resolution": "4k"}, {"seed": True}, {"generate_audio": "yes"}):
            args = copy.deepcopy(base)
            args["params"] = params
            with self.assertRaises(AppError):
                service.prepare_video_nodes(self.project, args, self.ctx())
            self.assertEqual(self.snapshot(), before)
        self.assert_no_generation()

    def test_update_only_second_shot_preserves_other_nodes_binding_layout_and_order(self):
        self.text_node()
        prepared = service.prepare_storyboards(self.project, self.board_args(), self.ctx("boards"))
        first, second = prepared["node_ids"]
        before = self.snapshot()
        director.confirm_object(self.project, "scope", "scope-fixture", 1)
        result = service.update_node(self.project, {"node_id": second, "expected_config_revision": 1,
            "config": {"prompt": "Close detail in warm light"}}, self.ctx("edit-second"))
        self.assertEqual(result["config_revision"], 2)
        self.assertEqual(result["layout_revision"], prepared["layout_revision"] + 1)
        self.assertEqual(result["merge_strategy"], "partial_config_and_params")
        updated = result["artifacts"][0]
        self.assertEqual(updated["config"]["model_id"], IMAGE)
        self.assertEqual(updated["config"]["params"], prepared["artifacts"][1]["config"]["params"])
        self.assertEqual(updated["binding"], prepared["artifacts"][1]["binding"])
        self.assertEqual(updated["position"], prepared["artifacts"][1]["position"])
        self.assertEqual(db.query("SELECT * FROM shots WHERE project_id=? ORDER BY id", (self.project,)), before["shots"])
        rows = {node["node_id"]: node for node in self.snapshot()["canvas_nodes"]}
        old = {node["node_id"]: node for node in before["canvas_nodes"]}
        self.assertEqual(rows[first], old[first])
        self.assertEqual(rows["notes"], old["notes"])
        self.assertEqual(db.query("SELECT status FROM confirmations WHERE object_id='scope-fixture'")[0]["status"], "invalidated")
        self.assert_no_generation()

    def test_update_partial_video_params_and_explicit_reference_clear(self):
        asset = self.imported()
        prepared = service.prepare_video_nodes(self.project, self.video_args(asset), self.ctx("video"))
        node_id = prepared["node_ids"][0]
        updated = service.update_node(self.project, {"node_id": node_id, "expected_config_revision": 1,
            "config": {"params": {"duration": 7}}}, self.ctx("duration"))
        cfg = updated["artifacts"][0]["config"]
        self.assertEqual(cfg["params"], {"duration": 7, "ratio": "16:9", "resolution": "720p"})
        self.assertEqual(cfg["input_asset_id"], asset["id"])
        self.error("GENERATION_MODE_CONFLICT", lambda: service.update_node(self.project,
            {"node_id": node_id, "expected_config_revision": 2, "config": {"generation_mode": "text_to_video"}}, self.ctx("switch")))
        changed = service.update_node(self.project, {"node_id": node_id, "expected_config_revision": 2,
            "config": {"generation_mode": "text_to_video", "input_asset_id": None}}, self.ctx("switch"))
        self.assertIsNone(changed["artifacts"][0]["config"]["input_asset_id"])
        self.assertEqual(changed["artifacts"][0]["config"]["params"]["duration"], 7)
        self.assert_no_generation()

    def test_update_requires_current_revision_and_project_owned_node(self):
        self.text_node()
        self.text_node("foreign", self.other)
        self.error("CANVAS_NODE_NOT_FOUND", lambda: service.update_node(self.project,
            {"node_id": "foreign", "expected_config_revision": 1, "config": {"text": "change"}}, self.ctx()))
        self.error("CANVAS_REVISION_CONFLICT", lambda: service.update_node(self.project,
            {"node_id": "notes", "expected_config_revision": 2, "config": {"text": "change"}}, self.ctx()))
        self.error("VALIDATION_FAILED", lambda: service.update_node(self.project,
            {"node_id": "notes", "expected_config_revision": True, "config": {"text": "change"}}, self.ctx()))
        self.assertEqual(db.query("SELECT * FROM director_tool_operations"), [])
        self.assert_no_generation()

    def test_update_rejects_identity_binding_execution_and_invalid_media_config(self):
        self.text_node()
        script = self.script(confirmed=False)
        asset = self.imported()
        prepared = service.prepare_video_nodes(self.project, self.video_args(asset), self.ctx("video"))
        node_id = prepared["node_ids"][0]
        before = self.snapshot()
        for extra in ({"type": "text"}, {"binding": {"asset_id": asset["id"]}}, {"position": {"x": 9, "y": 1}},
                      {"last_execution": {"status": "succeeded"}}):
            self.error("VALIDATION_FAILED", lambda: service.update_node(self.project,
                {"node_id": node_id, "expected_config_revision": 1, "config": {}, **extra}, self.ctx("invalid")))
        for change in ({"unhandled": "value"}, {"params": {"duration": 1}}, {"params": {"seed": True}},
                       {"params": None}, {"input_asset_id": "missing"}, {"model_id": IMAGE}):
            with self.assertRaises(AppError):
                service.update_node(self.project, {"node_id": node_id, "expected_config_revision": 1, "config": change}, self.ctx("invalid"))
        self.error("NODE_NOT_EDITABLE", lambda: service.update_node(self.project,
            {"node_id": "script", "expected_config_revision": 1, "config": {"text": "overwrite"}}, self.ctx("invalid")))
        self.error("VALIDATION_FAILED", lambda: service.update_node(self.project,
            {"node_id": "notes", "expected_config_revision": 1, "config": {"params": {}}}, self.ctx("invalid")))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(projects.get_script(script["target_id"])["version"], 1)
        self.assert_no_generation()

    def test_update_replay_does_not_overwrite_newer_edits_and_identical_change_is_noop(self):
        self.text_node()
        args = {"node_id": "notes", "expected_config_revision": 1, "config": {"text": "first edit"}}
        first = service.update_node(self.project, args, self.ctx("first"))
        latest = service.update_node(self.project, {"node_id": "notes", "expected_config_revision": 2,
            "config": {"text": "later edit"}}, self.ctx("later"))
        before = self.snapshot()
        replay = service.update_node(self.project, args, self.ctx("first"))
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["artifacts"], first["artifacts"])
        self.assertEqual(self.snapshot(), before)
        noop = service.update_node(self.project, {"node_id": "notes", "expected_config_revision": 3,
            "config": {"text": "later edit"}}, self.ctx("noop"))
        self.assertFalse(noop["config_changed"])
        self.assertEqual(noop["config_revision"], 3)
        self.assertEqual(noop["layout_revision"], latest["layout_revision"])
        self.assert_no_generation()

    def test_update_receipt_failure_rolls_back_config_revision_layout_and_scope(self):
        self.text_node()
        director.confirm_object(self.project, "scope", "scope-fixture", 1)
        before = self.snapshot()
        with db.conn() as connection:
            connection.execute("CREATE TRIGGER reject_tool_receipt BEFORE INSERT ON director_tool_operations "
                               "BEGIN SELECT RAISE(ABORT, 'receipt write failed'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "receipt write failed"):
            service.update_node(self.project, {"node_id": "notes", "expected_config_revision": 1,
                "config": {"text": "should not commit"}}, self.ctx())
        self.assertEqual(self.snapshot(), before)
        self.assert_no_generation()


if __name__ == "__main__":
    unittest.main()
