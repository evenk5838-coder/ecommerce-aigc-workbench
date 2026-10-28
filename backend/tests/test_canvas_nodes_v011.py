"""Free node persistence and script transaction tests, isolated from business data."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import canvas, config, db, director, projects
from app.errors import AppError


class CanvasNodesV011(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="canvas-v011-")
        self.old_db = config.DB_PATH
        config.DB_PATH = Path(self.tmp.name) / "test.db"
        db.init_db()
        self.project = projects.create_project("free canvas")["project_id"]
        self.other = projects.create_project("other")["project_id"]
        self.network = patch("app.providers_ark._request", side_effect=AssertionError("network forbidden"))
        self.network.start()

    def tearDown(self):
        self.network.stop()
        config.DB_PATH = self.old_db
        self.tmp.cleanup()

    def expect_error(self, code, callback):
        with self.assertRaises(AppError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)

    def node(self, node_id):
        return next(node for node in canvas.get_canvas(self.project)["nodes"] if node["node_id"] == node_id)

    def seed_asset(self, kind="image"):
        asset_id = db.new_id("asset")
        db.insert("assets", {"id": asset_id, "kind": kind, "mime": "image/png" if kind == "image" else "video/mp4",
                             "width": 32, "height": 24, "duration_ms": 1000 if kind == "video" else None,
                             "rel_path": "/PRIVATE/secret", "meta_json": '{"token":"PRIVATE"}'})
        return asset_id

    def test_text_persists_and_position_patch_preserves_content_revision(self):
        out = canvas.patch_canvas(self.project, {"nodes": [{"node_id": "words", "type": "text",
            "position": {"x": -500, "y": 800}, "config": {"title": "故事", "text": "第一幕\n第二幕"}}]})
        before = self.node("words")
        self.assertTrue(out["config_changed"])
        out = canvas.patch_canvas(self.project, {"expected_layout_revision": out["layout_revision"],
            "nodes": [{"node_id": "words", "position": {"x": 45, "y": 60}}], "viewport": {"x": 5, "y": 10, "zoom": .3}})
        after = self.node("words")
        self.assertEqual(after["config"], before["config"])
        self.assertEqual(after["config_revision"], before["config_revision"])
        self.assertFalse(out["config_changed"])
        self.assertEqual(canvas.get_canvas(self.project)["project_id"], self.project)
        self.assertEqual(canvas.get_canvas(self.project)["viewport"]["zoom"], .3)

    def test_multiple_independent_media_configs_and_global_asset_import(self):
        image = self.seed_asset()
        video = self.seed_asset("video")
        canvas.patch_canvas(self.project, {"nodes": [
            {"node_id": "image", "type": "image", "config": {"prompt": "first", "generation_mode": "image_to_image", "input_asset_id": image}},
            {"node_id": "video", "type": "video", "config": {"prompt": "second", "generation_mode": "text_to_video"}},
            {"node_id": "import", "type": "asset", "binding": {"asset_id": video}}]})
        canvas.patch_canvas(self.project, {"nodes": [{"node_id": "video", "config": {"prompt": "new"}}]})
        self.assertEqual(self.node("image")["config"]["prompt"], "first")
        self.assertEqual(self.node("video")["config"], {"prompt": "new"})
        self.assertEqual(self.node("import")["asset"], {"id": video, "kind": "video", "mime": "video/mp4",
                                                       "width": 32, "height": 24, "duration_ms": 1000})
        self.assertNotIn("PRIVATE", str(canvas.get_canvas(self.project)))
        self.assertEqual(db.query("SELECT * FROM tasks"), [])

    def test_delete_only_removes_metadata_and_its_edges(self):
        asset = self.seed_asset()
        canvas.patch_canvas(self.project, {"nodes": [{"node_id": "a", "type": "asset", "binding": {"asset_id": asset}},
            {"node_id": "b", "type": "text", "config": {"text": "keep"}}, {"node_id": "c"}],
            "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}]})
        before = canvas.get_canvas(self.project)
        out = canvas.patch_canvas(self.project, {"expected_layout_revision": before["layout_revision"], "delete_node_ids": ["a"]})
        self.assertEqual(out["nodes_deleted"], 1)
        self.assertEqual(out["canvas"]["edges"], [{"from": "b", "to": "c"}])
        self.assertIsNotNone(db.get("assets", asset))
        self.assertEqual(self.node("b")["config"]["text"], "keep")
        self.expect_error("CANVAS_REVISION_CONFLICT", lambda: canvas.patch_canvas(self.project,
            {"expected_layout_revision": before["layout_revision"], "delete_node_ids": ["b"]}))
        self.assertIsNotNone(self.node("b"))

    def test_delete_with_invalid_explicit_edge_rolls_back(self):
        canvas.patch_canvas(self.project, {"nodes": [{"node_id": "a"}, {"node_id": "b"}]})
        before = canvas.get_canvas(self.project)
        self.expect_error("CANVAS_INVALID_REFERENCE", lambda: canvas.patch_canvas(self.project, {
            "delete_node_ids": ["a"], "edges": [{"from": "a", "to": "b"}]}))
        self.assertEqual(canvas.get_canvas(self.project), before)

    def test_nonfinite_and_invalid_zoom_never_partially_mutates(self):
        canvas.patch_canvas(self.project, {"nodes": [{"node_id": "a"}]})
        before = canvas.get_canvas(self.project)
        for value in (float("nan"), float("inf"), True, "12"):
            self.expect_error("VALIDATION_FAILED", lambda: canvas.patch_canvas(self.project, {
                "nodes": [{"node_id": "new", "type": "text", "config": {"text": "do not save"}}],
                "viewport": {"x": value, "y": 0, "zoom": 1}}))
        for zoom in (0, -1, float("inf"), True):
            self.expect_error("VALIDATION_FAILED", lambda: canvas.patch_canvas(self.project, {"viewport": {"zoom": zoom}}))
        self.assertEqual(canvas.get_canvas(self.project), before)

    def test_body_and_readonly_field_validation(self):
        for node in (
            {"node_id": "x", "type": "text", "config": {"text": "x" * 100001}},
            {"node_id": "x", "type": "text", "config": {"title": "x" * 201}},
            {"node_id": "x", "type": "script", "config": {"text": "duplicated script body"}},
            {"node_id": "x", "last_execution": {"status": "succeeded"}},
            {"node_id": "x", "config": {"params": {"duration": float("nan")}}},
        ):
            self.expect_error("VALIDATION_FAILED", lambda: canvas.patch_canvas(self.project, {"nodes": [node]}))
        self.expect_error("CANVAS_INVALID_REFERENCE", lambda: canvas.patch_canvas(self.project, {"nodes": [
            {"node_id": "x", "binding": {"task_id": "fake", "status": "succeeded"}}]}))
        self.assertEqual(canvas.get_canvas(self.project)["nodes"], [])

    def test_mode_and_node_kind_checks_without_model(self):
        self.expect_error("MODEL_CAPABILITY_MISMATCH", lambda: canvas.patch_canvas(self.project, {"nodes": [
            {"node_id": "x", "type": "image", "config": {"generation_mode": "text_to_video"}}]}))
        self.expect_error("REFERENCE_IMAGE_REQUIRED", lambda: canvas.patch_canvas(self.project, {"nodes": [
            {"node_id": "x", "type": "image", "config": {"generation_mode": "image_to_image"}}]}))
        asset = self.seed_asset()
        self.expect_error("GENERATION_MODE_CONFLICT", lambda: canvas.patch_canvas(self.project, {"nodes": [
            {"node_id": "x", "type": "image", "config": {"generation_mode": "text_to_image", "input_asset_id": asset}}]}))
        self.expect_error("CANVAS_INVALID_REFERENCE", lambda: canvas.patch_canvas(self.project, {"nodes": [
            {"node_id": "x", "type": "video", "binding": {"asset_id": asset}}]}))

    def test_wrong_model_kind_is_rejected(self):
        with patch.object(config, "model_submittability", return_value={"submittable": True}):
            self.expect_error("MODEL_CAPABILITY_MISMATCH", lambda: canvas.patch_canvas(self.project, {"nodes": [
                {"node_id": "x", "type": "video", "config": {"model_id": "sd15"}}]}))
            self.expect_error("MODEL_CAPABILITY_MISMATCH", lambda: canvas.patch_canvas(self.project, {"nodes": [
                {"node_id": "x", "type": "video", "config": {"model_id": "sd15", "generation_mode": "text_to_video"}}]}))

    def test_script_and_shot_references_are_project_scoped(self):
        foreign = projects.add_script(self.other, {"summary": "private"})
        self.expect_error("CANVAS_INVALID_REFERENCE", lambda: canvas.patch_canvas(self.project, {"nodes": [
            {"node_id": "x", "type": "script", "binding": {"script_id": foreign["script_id"]}}]}))
        self.expect_error("CANVAS_INVALID_REFERENCE", lambda: canvas.patch_canvas(self.project, {"nodes": [
            {"node_id": "x", "binding": {"shot_id": "missing"}}]}))

    def test_text_config_change_invalidates_scope_but_movement_does_not(self):
        canvas.patch_canvas(self.project, {"nodes": [{"node_id": "text", "type": "text", "config": {"text": "before"}}]})
        director.confirm_object(self.project, "scope", "fixture", 1)
        canvas.patch_canvas(self.project, {"nodes": [{"node_id": "text", "position": {"x": 200, "y": 300}}]})
        self.assertTrue(director.current_confirmation("scope", "fixture", 1)["valid"])
        before = self.node("text")["config_revision"]
        canvas.patch_canvas(self.project, {"nodes": [{"node_id": "text", "expected_config_revision": before, "config": {"text": "after"}}]})
        self.assertFalse(director.current_confirmation("scope", "fixture", 1)["valid"])
        self.expect_error("CANVAS_REVISION_CONFLICT", lambda: canvas.patch_canvas(self.project, {"nodes": [
            {"node_id": "text", "expected_config_revision": before, "config": {"text": "stale"}}]}))

    def seed_script_row(self, script_id, version):
        return {"id": script_id, "project_id": self.project, "version": version,
                "content_json": '{"summary":"actual body"}', "source": "manual", "created_at": db.now()}

    def test_script_helper_is_same_transaction_and_does_not_copy_body(self):
        row = self.seed_script_row("first-script", 1)
        with db.transaction() as connection:
            connection.execute("INSERT INTO script_versions(id,project_id,version,content_json,source,created_at) VALUES(?,?,?,?,?,?)", tuple(row.values()))
            node = canvas.upsert_script_node_in_tx(connection, self.project, row["id"], 1)
        self.assertEqual(node["binding"], {"script_id": "first-script", "script_version": 1})
        self.assertEqual(self.node("script")["config"], {})
        self.assertNotIn("actual body", str(self.node("script")))
        before = canvas.get_canvas(self.project)
        with db.transaction() as connection:
            again = canvas.upsert_script_node_in_tx(connection, self.project, row["id"], 1)
        self.assertEqual(again["layout_revision"], before["layout_revision"])
        with self.assertRaises(RuntimeError):
            with db.transaction() as connection:
                row = self.seed_script_row("rollback-script", 2)
                connection.execute("INSERT INTO script_versions(id,project_id,version,content_json,source,created_at) VALUES(?,?,?,?,?,?)", tuple(row.values()))
                canvas.upsert_script_node_in_tx(connection, self.project, row["id"], 2)
                raise RuntimeError("cancel whole unit")
        self.assertIsNone(db.get("script_versions", "rollback-script"))
        self.assertEqual(canvas.get_canvas(self.project), before)

    def test_script_helper_preserves_identity_layout_and_other_nodes(self):
        db.insert("script_versions", self.seed_script_row("old-script", 1))
        db.insert("script_versions", self.seed_script_row("new-script", 2))
        canvas.patch_canvas(self.project, {"nodes": [
            {"node_id": "script", "type": "script", "position": {"x": -40, "y": 900},
             "config": {"title": "my script"}, "binding": {"script_id": "old-script", "script_version": 1}},
            {"node_id": "free", "type": "text", "config": {"text": "keep"}}],
            "viewport": {"x": 300, "y": -80, "zoom": .7}, "edges": [{"from": "script", "to": "free"}]})
        before = canvas.get_canvas(self.project)
        old_id = db.query("SELECT id FROM canvas_nodes WHERE project_id=? AND node_id='script'", (self.project,))[0]["id"]
        director.confirm_object(self.project, "scope", "fixture", 1)
        with db.transaction() as connection:
            out = canvas.upsert_script_node_in_tx(connection, self.project, "new-script", 2)
        after = canvas.get_canvas(self.project)
        self.assertEqual(after["viewport"], before["viewport"])
        self.assertEqual(after["edges"], before["edges"])
        self.assertEqual(self.node("script")["position"], {"x": -40, "y": 900})
        self.assertEqual(self.node("script")["config"], {"title": "my script"})
        self.assertEqual(self.node("free")["config"], {"text": "keep"})
        self.assertEqual(out["config_revision"], 2)
        self.assertEqual(out["layout_revision"], before["layout_revision"] + 1)
        self.assertEqual(db.query("SELECT id FROM canvas_nodes WHERE project_id=? AND node_id='script'", (self.project,))[0]["id"], old_id)
        self.assertFalse(director.current_confirmation("scope", "fixture", 1)["valid"])


if __name__ == "__main__":
    unittest.main()
