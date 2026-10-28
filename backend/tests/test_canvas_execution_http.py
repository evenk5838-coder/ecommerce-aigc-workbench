"""Real route/worker/persistence integration with an explicit media fixture.

No application lifespan, background worker, external provider or business data.
The one manual worker invocation uses a fixed PNG provider response, not AI.
"""
from __future__ import annotations

import base64
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import canvas, config, db, main, node_execution, projects, storage, worker

IMAGE = "doubao-seedream-5-0-flash-260915"


class CanvasExecutionHTTP(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory(prefix="canvas-http-"))
        self.enterContext(patch.object(config, "DB_PATH", Path(temporary) / "test.db"))
        self.enterContext(patch.object(config, "ASSETS", Path(temporary) / "assets"))
        self.enterContext(patch.object(config, "BUDGET_CNY", 20))
        self.enterContext(patch.object(config, "model_lifecycle_state", return_value={"status": "active"}))
        self.enterContext(patch("app.providers_ark._request", side_effect=AssertionError("network forbidden")))
        self.enterContext(patch("socket.socket.connect", side_effect=AssertionError("network forbidden")))
        self.enterContext(patch.object(worker.SAMPLER, "start"))
        self.enterContext(patch.object(worker.SAMPLER, "stop", return_value=0))
        db.init_db()
        self.client = TestClient(main.app)  # No with: application lifespan is not entered.
        self.addCleanup(self.client.close)
        self.pid = self.ok(self.client.post("/api/projects", json={"name": "HTTP fixture"}))["project_id"]
        self.base = f"/api/projects/{self.pid}/canvas"
        self.config = {"generation_mode": "text_to_image", "model_id": IMAGE,
                       "prompt": "A cup in daylight", "params": {"ratio": "1:1", "size": "1024x1024"}}
        self.ok(self.client.patch(self.base, json={"nodes": [{"node_id": "picture", "type": "image",
            "position": {"x": 80, "y": 90}, "config": self.config}]}))

    def ok(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def execute_request(self):
        quote = self.ok(self.client.post(self.base + "/nodes/picture/quote", json={"expected_config_revision": 1}))
        self.body = {"quote_id": quote["quote_id"], "client_request_id": "generate-once",
                     "user_action": "confirm_generate"}
        return self.ok(self.client.post(self.base + "/nodes/picture/generate", json=self.body))

    def png(self, color="blue"):
        stream = io.BytesIO()
        Image.new("RGB", (32, 24), color).save(stream, "PNG")
        return stream.getvalue()

    def test_route_to_fixture_worker_output_and_canvas_history(self):
        execution = self.execute_request()
        with patch("app.providers_ark.image_generate", return_value={"b64": base64.b64encode(self.png()).decode()}) as provider:
            worker.execute(db.get("tasks", execution["task_id"]))
        provider.assert_called_once()
        view = self.ok(self.client.get(self.base + "/nodes/picture/executions/" + execution["execution_id"]))
        self.assertEqual(view["status"], "succeeded")
        self.assertTrue(view["asset_id"])
        state = self.ok(self.client.get(self.base))["nodes"][0]
        self.assertEqual(state["last_execution"]["task_id"], execution["task_id"])
        self.assertEqual(state["asset"]["id"], view["asset_id"])
        media = self.client.get("/api/assets/" + view["asset_id"] + "/file")
        self.assertEqual(media.status_code, 200)
        self.assertEqual(Image.open(io.BytesIO(media.content)).size, (32, 24))
        replay = self.ok(self.client.post(self.base + "/nodes/picture/generate", json=self.body))
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(len(db.query("SELECT * FROM tasks")), 1)

    def test_http_delete_undo_preserves_pending_task_and_original_identity(self):
        execution = self.execute_request()
        original = db.query("SELECT id FROM canvas_nodes WHERE project_id=?", (self.pid,))[0]["id"]
        deleted = self.ok(self.client.patch(self.base, json={"delete_node_ids": ["picture"]}))
        self.assertEqual(deleted["nodes_deleted"], 1)
        operation = deleted["deletion_operation_id"]
        self.assertEqual(self.ok(self.client.get(self.base))["nodes"], [])
        operations = self.ok(self.client.get(self.base + "/deletions"))["operations"]
        self.assertEqual(operations[0]["operation_id"], operation)
        undone = self.ok(self.client.post(self.base + f"/deletions/{operation}/undo",
            json={"expected_layout_revision": deleted["layout_revision"]}))
        restored = undone["canvas"]["nodes"][0]
        self.assertEqual(restored["last_execution"]["task_id"], execution["task_id"])
        self.assertEqual(restored["position"], {"x": 80, "y": 90})
        self.assertEqual(restored["config"], self.config)
        self.assertEqual(db.query("SELECT id FROM canvas_nodes WHERE project_id=?", (self.pid,))[0]["id"], original)
        self.ok(self.client.post(self.base + "/nodes/picture/generate", json=self.body))
        self.assertEqual(len(db.query("SELECT * FROM tasks")), 1)

    def test_old_retry_route_cannot_bypass_node_quote_or_link(self):
        execution = self.execute_request()
        db.update("tasks", execution["task_id"], {"status": "failed"})
        response = self.client.post("/api/tasks/" + execution["task_id"] + "/retry", json={})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "NODE_EXECUTION_CONFLICT")
        self.assertEqual(len(db.query("SELECT * FROM tasks")), 1)

    def test_unfinished_remote_task_blocks_project_deletion(self):
        execution = self.execute_request()
        db.update("tasks", execution["task_id"], {"status": "failed", "remote_task_id": "existing-remote",
            "remote_status": "running", "error_code": "TIMEOUT_LOCAL_POLL"})
        response = self.client.delete("/api/projects/" + self.pid)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["code"], "PROJECT_BUSY")
        self.assertIsNone(db.get("projects", self.pid)["deleted_at"])

    def test_reference_changed_while_queued_fails_before_provider_call(self):
        reference = storage.save_bytes(self.png(), "image", "fixture")
        updated = {**self.config, "generation_mode": "image_to_image", "input_asset_id": reference["id"]}
        canvas.patch_canvas(self.pid, {"nodes": [{"node_id": "picture", "config": updated}]})
        quote = node_execution.quote_node(self.pid, "picture")
        execution = node_execution.generate_node(self.pid, "picture", {"quote_id": quote["quote_id"],
            "client_request_id": "reference", "user_action": "confirm_generate"})
        storage.asset_path(reference).write_bytes(self.png("red"))
        with patch("app.providers_ark.image_generate", side_effect=AssertionError("must not call")) as provider:
            worker.execute(db.get("tasks", execution["task_id"]))
        provider.assert_not_called()
        task = db.get("tasks", execution["task_id"])
        self.assertEqual((task["status"], task["error_code"]), ("failed", "INVALID_REFERENCE_IMAGE"))

    def test_remote_poll_recovery_does_not_require_local_reference(self):
        execution = self.execute_request()
        db.update("tasks", execution["task_id"], {"remote_task_id": "already-submitted"})
        # The validator must not start over or demand a reference for polling.
        worker._validate_canvas_reference(db.get("tasks", execution["task_id"]))

    def test_routes_keep_project_boundaries_and_unknown_undo_errors(self):
        execution = self.execute_request()
        other = projects.create_project("other")["project_id"]
        other_path = f"/api/projects/{other}/canvas"
        missing = self.client.get(other_path + "/nodes/picture/executions/" + execution["execution_id"])
        self.assertEqual(missing.status_code, 404)
        undo = self.client.post(other_path + "/deletions/unknown/undo", json={"expected_layout_revision": 0})
        self.assertEqual(undo.status_code, 404)
        self.assertEqual(undo.json()["error"]["code"], "CANVAS_DELETION_NOT_FOUND")
        self.assertEqual(len(db.query("SELECT * FROM tasks")), 1)

    def test_new_tables_have_deleted_project_guards(self):
        self.execute_request()
        # Simulate a tombstone from another process; all new tables must close.
        with db.transaction() as connection:
            connection.execute("UPDATE projects SET deleted_at=? WHERE id=?", (db.now(), self.pid))
        for table in ("canvas_node_quotes", "canvas_node_executions"):
            from app.errors import AppError
            with self.subTest(table=table), self.assertRaises(AppError) as caught:
                with db.transaction() as connection:
                    connection.execute(f"DELETE FROM {table} WHERE project_id=?", (self.pid,))
            self.assertEqual(caught.exception.code, "PROJECT_DELETED")


if __name__ == "__main__":
    unittest.main()
