"""Persistent deletion undo regressions; isolated DB, no provider/network/worker."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import canvas, canvas_history, config, db, director, projects
from app.errors import AppError


class CanvasHistory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="canvas-history-")
        self.previous_db = config.DB_PATH
        config.DB_PATH = Path(self.tmp.name) / "test.db"
        db.init_db()
        self.pid = projects.create_project("undo isolated")["project_id"]
        self.other = projects.create_project("other isolated")["project_id"]
        self.network = patch("app.providers_ark._request", side_effect=AssertionError("network forbidden"))
        self.network.start()

    def tearDown(self):
        self.network.stop()
        config.DB_PATH = self.previous_db
        self.tmp.cleanup()

    def failure(self, code, callback):
        with self.assertRaises(AppError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def patch(self, **body):
        return canvas.patch_canvas(self.pid, body)

    def view(self):
        return canvas.get_canvas(self.pid)

    def undo(self, operation_id, revision=None):
        if revision is None:
            revision = self.view()["layout_revision"]
        return canvas_history.undo_deletion(self.pid, operation_id, {"expected_layout_revision": revision})

    def seed(self, edges=None):
        return self.patch(nodes=[
            {"node_id": "a", "type": "text", "position": {"x": -20, "y": 90},
             "config": {"title": "开场", "text": "保留完整正文"}},
            {"node_id": "b", "type": "image", "config": {"prompt": "side light"}},
            {"node_id": "c", "type": "video", "config": {"prompt": "slow pan"}}],
            edges=edges if edges is not None else [{"from": "a", "to": "b"}, {"from": "b", "to": "c"}])

    def delete(self, *ids):
        return self.patch(expected_layout_revision=self.view()["layout_revision"], delete_node_ids=list(ids))

    def test_single_delete_and_restore_full_row_without_deleting_media_or_tasks(self):
        asset_id = db.new_id("asset")
        db.insert("assets", {"id": asset_id, "kind": "video", "mime": "video/mp4", "rel_path": "private.mp4"})
        db.insert("tasks", {"id": "fixture-task", "kind": "video", "status": "succeeded", "output_asset_id": asset_id})
        self.seed()
        self.patch(nodes=[{"node_id": "c", "binding": {"asset_id": asset_id}, "position": {"x": 100, "y": -200}}])
        before = self.view()
        old = next(node for node in before["nodes"] if node["node_id"] == "c")
        old_record_id = db.query("SELECT id FROM canvas_nodes WHERE project_id=? AND node_id='c'", (self.pid,))[0]["id"]
        deleted = self.delete("c")
        self.assertEqual(deleted["nodes_deleted"], 1)
        operation = db.get("canvas_deletions", deleted["deletion_operation_id"])
        self.assertEqual(db.loads(operation["deleted_nodes_json"])[0]["node_id"], "c")
        self.assertEqual(db.loads(operation["removed_edges_json"]), [{"from": "b", "to": "c"}])
        self.assertIsNotNone(db.get("assets", asset_id))
        self.assertEqual(db.get("tasks", "fixture-task")["status"], "succeeded")
        # Reopening the DB must retain the undo record and the original binding.
        db.init_db()
        restored = self.undo(deleted["deletion_operation_id"])
        actual = next(node for node in restored["canvas"]["nodes"] if node["node_id"] == "c")
        for field in ("type", "position", "config", "binding", "asset"):
            self.assertEqual(actual[field], old[field])
        self.assertEqual(actual["config_revision"], old["config_revision"] + 1)
        self.assertEqual(db.query("SELECT id FROM canvas_nodes WHERE project_id=? AND node_id='c'", (self.pid,))[0]["id"], old_record_id)
        self.assertEqual(restored["canvas"]["edges"], before["edges"])
        self.assertEqual(db.query("SELECT COUNT(*) AS total FROM tasks")[0]["total"], 1)

    def test_batch_undo_preserves_other_node_edits_viewport_and_new_edges(self):
        self.seed()
        before = {node["node_id"]: node for node in self.view()["nodes"]}
        deleted = self.delete("a", "c")
        self.patch(nodes=[{"node_id": "b", "position": {"x": 500, "y": 600}, "config": {"prompt": "new prompt"}},
                          {"node_id": "d", "type": "text", "config": {"text": "later work"}}],
                   viewport={"x": -900, "y": 200, "zoom": .4}, edges=[{"from": "b", "to": "d"}])
        latest = self.view()
        out = self.undo(deleted["deletion_operation_id"])
        actual = {node["node_id"]: node for node in out["canvas"]["nodes"]}
        self.assertEqual(out["restored_node_ids"], ["a", "c"])
        self.assertEqual(out["canvas"]["viewport"], latest["viewport"])
        self.assertEqual(actual["b"], next(node for node in latest["nodes"] if node["node_id"] == "b"))
        self.assertEqual(actual["d"]["config"], {"text": "later work"})
        self.assertEqual(actual["a"]["config"], before["a"]["config"])
        self.assertEqual(out["canvas"]["edges"], [{"from": "b", "to": "d"},
                                                  {"from": "a", "to": "b"}, {"from": "b", "to": "c"}])

    def test_noop_delete_does_not_record_or_advance_revision(self):
        self.seed()
        before = self.view()
        out = self.delete("missing")
        self.assertEqual(out["nodes_deleted"], 0)
        self.assertIsNone(out["deletion_operation_id"])
        self.assertEqual(out["canvas"], before)
        self.assertEqual(canvas_history.list_deletions(self.pid)["operations"], [])

    def test_prune_keeps_existing_validation_and_records_successful_removal(self):
        self.seed()
        before = self.view()
        self.failure("CANVAS_INVALID_REFERENCE", lambda: self.patch(nodes=[{"node_id": "a"}], prune=True))
        self.assertEqual(self.view(), before)
        self.assertEqual(canvas_history.list_deletions(self.pid)["operations"], [])
        out = self.patch(nodes=[{"node_id": "a"}], prune=True, edges=[])
        self.assertEqual(out["nodes_deleted"], 2)
        restored = self.undo(out["deletion_operation_id"])
        self.assertEqual({node["node_id"] for node in restored["canvas"]["nodes"]}, {"a", "b", "c"})
        self.assertEqual(restored["canvas"]["edges"], before["edges"])

    def test_invalid_delete_does_not_leave_undo_history(self):
        self.seed()
        before = self.view()
        self.failure("CANVAS_INVALID_REFERENCE", lambda: self.patch(delete_node_ids=["a"], edges=[{"from": "a", "to": "b"}]))
        self.assertEqual(self.view(), before)
        self.assertEqual(canvas_history.list_deletions(self.pid)["operations"], [])

    def test_history_record_failure_rolls_back_delete_and_other_changes(self):
        self.seed()
        before = self.view()
        with db.conn() as connection:
            connection.execute("CREATE TRIGGER fail_history BEFORE INSERT ON canvas_deletions "
                               "BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(Exception):
            self.patch(delete_node_ids=["a"], nodes=[{"node_id": "b", "config": {"prompt": "must roll back"}}])
        self.assertEqual(self.view(), before)
        self.assertEqual(canvas_history.list_deletions(self.pid)["operations"], [])

    def test_stale_revision_cannot_restore_or_consume_operation(self):
        self.seed()
        deleted = self.delete("a")
        self.patch(nodes=[{"node_id": "b", "position": {"x": 50, "y": 70}}])
        before = self.view()
        self.failure("CANVAS_REVISION_CONFLICT", lambda: self.undo(deleted["deletion_operation_id"], deleted["layout_revision"]))
        self.assertEqual(self.view(), before)
        self.assertEqual(db.get("canvas_deletions", deleted["deletion_operation_id"])["status"], "deleted")

    def test_mid_restore_database_failure_rolls_back_whole_batch_and_receipt(self):
        self.seed()
        deleted = self.delete("a", "c")
        before = self.view()
        with db.conn() as connection:
            connection.execute("CREATE TRIGGER fail_restore BEFORE INSERT ON canvas_nodes "
                               "WHEN NEW.node_id='c' BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(Exception):
            self.undo(deleted["deletion_operation_id"])
        self.assertEqual(self.view(), before)
        operation = db.get("canvas_deletions", deleted["deletion_operation_id"])
        self.assertEqual(operation["status"], "deleted")
        self.assertIsNone(operation["restored_at"])

    def test_repeated_undo_does_not_rewind_later_edits_or_increment_revision(self):
        self.seed()
        deleted = self.delete("a")
        first = self.undo(deleted["deletion_operation_id"])
        self.patch(nodes=[{"node_id": "a", "config": {"text": "edited after restoring"}}])
        before = self.view()
        replay = self.undo(deleted["deletion_operation_id"], deleted["layout_revision"])
        self.assertTrue(replay["already_restored"])
        self.assertEqual(replay["restored_node_ids"], first["restored_node_ids"])
        self.assertEqual(replay["canvas"], before)
        self.assertEqual(replay["layout_revision"], before["layout_revision"])

    def test_recreated_node_id_causes_atomic_conflict(self):
        self.seed()
        deleted = self.delete("a", "c")
        self.patch(nodes=[{"node_id": "c", "type": "text", "config": {"text": "new identity"}}])
        before = self.view()
        error = self.failure("CANVAS_REVISION_CONFLICT", lambda: self.undo(deleted["deletion_operation_id"]))
        self.assertEqual(error.detail["reason"], "node_id_reused")
        self.assertEqual(self.view(), before)
        self.assertEqual(db.get("canvas_deletions", deleted["deletion_operation_id"])["status"], "deleted")

    def test_later_delete_of_reused_node_id_prevents_restoring_older_identity(self):
        self.seed()
        original = self.delete("a")
        self.patch(nodes=[{"node_id": "a", "type": "text", "config": {"text": "replacement"}}])
        newest = self.delete("a")
        error = self.failure("CANVAS_REVISION_CONFLICT", lambda: self.undo(original["deletion_operation_id"]))
        self.assertEqual(error.detail["reason"], "newer_deletion")
        out = self.undo(newest["deletion_operation_id"])
        self.assertEqual(next(n for n in out["canvas"]["nodes"] if n["node_id"] == "a")["config"]["text"], "replacement")

    def test_missing_incident_endpoint_prevents_partial_batch_restore(self):
        self.seed()
        deleted = self.delete("a", "c")
        self.delete("b")
        before = self.view()
        error = self.failure("CANVAS_REVISION_CONFLICT", lambda: self.undo(deleted["deletion_operation_id"]))
        self.assertEqual(error.detail["reason"], "edge_endpoint_missing")
        self.assertEqual(self.view(), before)

    def test_changed_edge_id_is_not_overwritten(self):
        self.seed(edges=[{"id": "link", "from": "a", "to": "b", "label": "original"}])
        deleted = self.delete("a")
        self.patch(edges=[{"id": "link", "from": "b", "to": "c", "label": "newer"}])
        before = self.view()
        error = self.failure("CANVAS_REVISION_CONFLICT", lambda: self.undo(deleted["deletion_operation_id"]))
        self.assertEqual(error.detail["reason"], "edge_changed")
        self.assertEqual(self.view(), before)

    def test_new_edges_cannot_make_restoration_cyclic(self):
        self.seed(edges=[{"from": "a", "to": "b"}, {"from": "c", "to": "a"}])
        deleted = self.delete("a")
        self.patch(edges=[{"from": "b", "to": "c"}])
        before = self.view()
        error = self.failure("CANVAS_REVISION_CONFLICT", lambda: self.undo(deleted["deletion_operation_id"]))
        self.assertEqual(error.detail["reason"], "edge_cycle")
        self.assertEqual(self.view(), before)

    def test_operation_is_scoped_and_not_found_does_not_leak_other_project(self):
        self.seed()
        deleted = self.delete("a")
        self.failure("CANVAS_DELETION_NOT_FOUND", lambda: canvas_history.undo_deletion(
            self.other, deleted["deletion_operation_id"], {"expected_layout_revision": 0}))
        self.failure("CANVAS_DELETION_NOT_FOUND", lambda: self.undo("missing"))
        self.assertEqual(canvas_history.list_deletions(self.other)["operations"], [])

    def test_deleted_project_cannot_list_or_restore_nodes(self):
        self.seed()
        deleted = self.delete("a")
        projects.delete_project(self.pid)
        self.failure("PROJECT_DELETED", lambda: canvas_history.list_deletions(self.pid))
        self.failure("PROJECT_DELETED", lambda: self.undo(deleted["deletion_operation_id"], deleted["layout_revision"]))

    def test_required_revision_validation_and_history_public_summary(self):
        self.seed()
        deleted = self.delete("a", "c")
        operation_id = deleted["deletion_operation_id"]
        for body in ({}, {"expected_layout_revision": True}, {"expected_layout_revision": -1},
                     {"expected_layout_revision": "2"}, {"expected_layout_revision": 2, "nodes": []}, []):
            self.failure("VALIDATION_FAILED", lambda: canvas_history.undo_deletion(self.pid, operation_id, body))
        public = canvas_history.list_deletions(self.pid)["operations"][0]
        self.assertEqual(public["node_ids"], ["a", "c"])
        self.assertEqual(public["node_count"], 2)
        self.assertNotIn("deleted_nodes_json", str(public))
        self.assertNotIn("保留完整正文", str(public))

    def test_config_revision_after_restore_rejects_predelete_stale_editor(self):
        self.seed()
        revision = next(node for node in self.view()["nodes"] if node["node_id"] == "a")["config_revision"]
        deleted = self.delete("a")
        self.undo(deleted["deletion_operation_id"])
        before = self.view()
        self.failure("CANVAS_REVISION_CONFLICT", lambda: self.patch(nodes=[{
            "node_id": "a", "expected_config_revision": revision, "config": {"text": "stale write"}}]))
        self.assertEqual(self.view(), before)

    def test_undo_invalidates_new_scope_confirmation(self):
        self.seed()
        deleted = self.delete("a")
        director.confirm_object(self.pid, "scope", "fixture", 1)
        self.undo(deleted["deletion_operation_id"])
        self.assertFalse(director.current_confirmation("scope", "fixture", 1)["valid"])

    def test_script_removal_preserves_versions_and_undo_same_version(self):
        saved = projects.add_script(self.pid, {"summary": "actual script", "shots": []})
        # projects.add_script itself is a script version API; bind its canvas representation explicitly.
        with db.transaction() as connection:
            canvas.upsert_script_node_in_tx(connection, self.pid, saved["script_id"], saved["version"])
        deleted = self.delete("script")
        self.assertIsNotNone(db.get("script_versions", saved["script_id"]))
        out = self.undo(deleted["deletion_operation_id"])
        actual = next(n for n in out["canvas"]["nodes"] if n["node_id"] == "script")
        self.assertEqual(actual["binding"], {"script_id": saved["script_id"], "script_version": saved["version"]})

    def test_old_script_snapshot_never_replaces_newer_canonical_script(self):
        saved = projects.add_script(self.pid, {"summary": "old", "shots": []})
        with db.transaction() as connection:
            canvas.upsert_script_node_in_tx(connection, self.pid, saved["script_id"], saved["version"])
        deleted = self.delete("script")
        newer = projects.add_script(self.pid, {"summary": "new script", "shots": []})
        with db.transaction() as connection:
            canvas.upsert_script_node_in_tx(connection, self.pid, newer["script_id"], newer["version"])
        before = self.view()
        self.failure("CANVAS_REVISION_CONFLICT", lambda: self.undo(deleted["deletion_operation_id"]))
        self.assertEqual(self.view(), before)
        self.assertEqual(len(db.query("SELECT * FROM script_versions WHERE project_id=?", (self.pid,))), 2)

    def test_newer_script_without_canvas_node_also_blocks_old_snapshot(self):
        saved = projects.add_script(self.pid, {"summary": "old", "shots": []})
        with db.transaction() as connection:
            canvas.upsert_script_node_in_tx(connection, self.pid, saved["script_id"], saved["version"])
        deleted = self.delete("script")
        db.insert("script_versions", {"id": "new-script-without-card", "project_id": self.pid, "version": 2,
                                      "content_json": '{"summary":"latest"}', "source": "manual", "created_at": db.now()})
        before = self.view()
        error = self.failure("CANVAS_REVISION_CONFLICT", lambda: self.undo(deleted["deletion_operation_id"]))
        self.assertEqual(error.detail["reason"], "newer_script")
        self.assertEqual(self.view(), before)

    def test_canvas_snapshot_has_complete_scoped_markers_and_undo_clears_them(self):
        self.seed()
        deleted = self.delete("a", "c")
        self.assertEqual(deleted["canvas"]["deleted_node_ids"], ["a", "c"])
        self.assertEqual(self.view()["deleted_node_ids"], ["a", "c"])
        self.assertEqual(canvas.get_canvas(self.other)["deleted_node_ids"], [])
        restored = self.undo(deleted["deletion_operation_id"])
        self.assertEqual(restored["canvas"]["deleted_node_ids"], [])
        self.assertEqual(self.view()["deleted_node_ids"], [])

    def test_complete_markers_are_not_limited_by_the_latest_100_undo_operations(self):
        self.seed(edges=[])
        old = self.delete("a")
        # Fixture history uses the same transactional recorder as real deletion.
        # Each marker is older/newer independently of the public undo listing limit.
        old_row = db.get("canvas_deletions", old["deletion_operation_id"])
        with db.transaction() as connection:
            for index in range(105):
                marker = f"missing-{index:03}"
                operation_id = canvas_history.record_deletion_in_tx(
                    connection, self.pid, [{"node_id": marker}], [], index + 10)
                connection.execute("UPDATE canvas_deletions SET created_at=? WHERE id=?",
                                   (old_row["created_at"] + index + 1, operation_id))
        listing = canvas_history.list_deletions(self.pid)["operations"]
        self.assertEqual(len(listing), 100)
        self.assertNotIn(old["deletion_operation_id"], {item["operation_id"] for item in listing})
        complete = self.view()["deleted_node_ids"]
        self.assertEqual(len(complete), 106)
        self.assertIn("a", complete)
        self.assertIn("missing-000", complete)
        self.assertIn("missing-104", complete)
        self.assertEqual(complete, sorted(set(complete)))

    def test_real_recreated_identity_overrides_all_old_markers_and_redelete_deduplicates(self):
        self.seed(edges=[])
        first = self.delete("a")
        self.assertEqual(self.view()["deleted_node_ids"], ["a"])
        self.patch(nodes=[{"node_id": "a", "type": "text", "config": {"text": "new content"}}])
        self.assertEqual(db.get("canvas_deletions", first["deletion_operation_id"])["status"], "deleted")
        self.assertEqual(self.view()["deleted_node_ids"], [])
        second = self.delete("a")
        self.assertEqual(self.view()["deleted_node_ids"], ["a"])
        self.assertEqual(len(canvas_history.list_deletions(self.pid)["operations"]), 2)
        restored = self.undo(second["deletion_operation_id"])
        self.assertEqual(restored["canvas"]["deleted_node_ids"], [])
        self.assertEqual(next(node for node in restored["canvas"]["nodes"] if node["node_id"] == "a")["config"],
                         {"text": "new content"})

    def test_real_get_route_adds_markers_without_changing_existing_canvas_fields(self):
        from fastapi.testclient import TestClient
        from app import main

        self.seed()
        expected = self.view()
        # Instantiate without a context manager: do not enter application lifespan or start workers.
        client = TestClient(main.app)
        self.addCleanup(client.close)
        route = f"/api/projects/{self.pid}/canvas"
        with patch("socket.socket.connect", side_effect=AssertionError("network forbidden")):
            response = client.get(route)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json(), expected)
            self.assertEqual(response.json()["deleted_node_ids"], [])
            deletion = self.delete("a")
            removed = client.get(route)
            self.assertEqual(removed.status_code, 200, removed.text)
            self.assertEqual(removed.json()["deleted_node_ids"], ["a"])
            self.assertEqual(removed.json()["schema_version"], 1)
            self.assertIsInstance(removed.json()["layout_revision"], int)
            self.assertEqual(set(removed.json()["viewport"]), {"x", "y", "zoom"})
            for node in removed.json()["nodes"]:
                self.assertTrue({"node_id", "type", "position", "config", "binding", "config_revision",
                                 "last_execution", "asset"}.issubset(node))
            self.undo(deletion["deletion_operation_id"])
            restored = client.get(route)
            self.assertEqual(restored.status_code, 200, restored.text)
            self.assertEqual(restored.json()["deleted_node_ids"], [])


if __name__ == "__main__":
    unittest.main()
