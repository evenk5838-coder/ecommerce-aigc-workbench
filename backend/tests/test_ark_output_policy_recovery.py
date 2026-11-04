"""Creation-time Ark provenance survives polling/download recovery; no network."""
from __future__ import annotations

import base64
import hashlib
import unittest
from unittest.mock import Mock, patch

import test_node_execution as fixture
from app import cloud_tasks, db, media_policy, node_execution, providers_ark as ark
from app import storage
from app.errors import err


class ArkOutputPolicyRecoveryTests(unittest.TestCase):
    setUp = fixture.NodeExecutionTests.setUp
    asset = fixture.NodeExecutionTests.asset
    create_node = fixture.NodeExecutionTests.create_node
    generate = fixture.NodeExecutionTests.generate

    def enqueue(self, mode="text_to_video"):
        self.create_node("video", mode)
        quote = node_execution.quote_node(self.project, "video")
        execution = self.generate(quote, node_id="video")
        task = db.get("tasks", execution["task_id"])
        return task, db.loads(task["params_json"])

    def succeeded(self):
        return {"status": "succeeded", "video_url": "https://fixture.invalid/video.mp4",
                "usage": {}, "duration": 5, "fps": 24}

    def run_video(self, task, params):
        return cloud_tasks.run_video(task["id"], fixture.VIDEO, params, Mock())

    def test_build_content_exposes_preparation_metadata_without_changing_wire_fields(self):
        asset = self.asset()
        params = {"generation_mode": "image_to_video", "input_asset_id": asset["id"],
                  "prompt": "complete cup", "ratio": "16:9"}
        metadata = {}
        content = cloud_tasks.build_content(params, preparation=metadata)
        self.assertEqual(content, cloud_tasks.build_content(params))
        self.assertEqual(metadata["source_sha256"], asset["sha256"])
        prepared = base64.b64decode(content[1]["image_url"]["url"].split(",", 1)[1])
        self.assertEqual(metadata["prepared_sha256"], hashlib.sha256(prepared).hexdigest())
        text_metadata = {}
        text = cloud_tasks.build_content({"generation_mode": "text_to_video", "prompt": "cup"},
                                         preparation=text_metadata)
        self.assertEqual(text_metadata, {})
        self.assertEqual([item["type"] for item in text], ["text"])

    def test_new_i2v_persists_policy_and_reference_with_remote_id_before_first_poll(self):
        task, params = self.enqueue("image_to_video")
        sent = {}
        expected_policy = media_policy.specification("video")

        def create(model, content, **kwargs):
            sent["content"] = content
            self.assertIs(kwargs["watermark"], False)
            return {"remote_task_id": "new-job"}

        def query(remote_id):
            saved = db.get("tasks", task["id"])
            local = db.loads(saved["usage_json"])
            self.assertEqual(saved["remote_task_id"], remote_id)
            self.assertEqual(local["output_policy"], expected_policy)
            metadata = local["prepared_reference"]
            raw = base64.b64decode(sent["content"][1]["image_url"]["url"].split(",", 1)[1])
            self.assertEqual(metadata["prepared_sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(metadata["source_sha256"], storage.load_asset(params["input_asset_id"])["sha256"])
            return self.succeeded()

        with patch.object(ark, "video_create", side_effect=create) as create_request, \
             patch.object(ark, "video_query", side_effect=query), \
             patch.object(ark, "download", return_value=b"fixture-video"):
            result = self.run_video(task, params)
        create_request.assert_called_once()
        self.assertEqual(result["usage"]["output_policy"], expected_policy)
        self.assertIsNotNone(result["usage"]["prepared_reference"])

    def test_new_t2v_keeps_creation_policy_without_claiming_reference_preparation(self):
        task, params = self.enqueue()
        policy_at_creation = {"version": "submitted-policy", "watermark": False}
        with patch.object(media_policy, "specification", return_value=policy_at_creation) as specification, \
             patch.object(ark, "video_create", return_value={"remote_task_id": "new-text-job"}), \
             patch.object(ark, "video_query", return_value=self.succeeded()), \
             patch.object(ark, "download", return_value=b"fixture-video"):
            result = self.run_video(task, params)
        specification.assert_called_once_with("video")
        self.assertEqual(result["usage"]["output_policy"], policy_at_creation)
        self.assertIsNone(result["usage"]["prepared_reference"])
        saved = db.loads(db.get("tasks", task["id"])["usage_json"])
        self.assertEqual(saved["output_policy"], policy_at_creation)
        self.assertIsNone(saved["prepared_reference"])

    def test_existing_remote_video_retains_old_policy_and_skips_current_preparation(self):
        task, params = self.enqueue("image_to_video")
        provenance = {"output_policy": {"version": "older-policy", "watermark": False},
                      "prepared_reference": {"policy": "older-policy", "prepared_sha256": "old-hash"},
                      "retained_marker": "original"}
        db.update("tasks", task["id"], {"remote_task_id": "existing-job", "usage_json": db.dumps(provenance)})
        with patch.object(media_policy, "specification", side_effect=AssertionError("must retain old policy")), \
             patch.object(cloud_tasks, "build_content", side_effect=AssertionError("must not prepare again")), \
             patch.object(ark, "video_create") as create, \
             patch.object(ark, "video_query", return_value=self.succeeded()) as query, \
             patch.object(ark, "download", return_value=b"fixture-video"):
            result = self.run_video(task, params)
        create.assert_not_called()
        query.assert_called_once_with("existing-job")
        for key, value in provenance.items():
            self.assertEqual(result["usage"][key], value)

    def test_legacy_remote_without_policy_stays_unknown(self):
        task, params = self.enqueue()
        db.update("tasks", task["id"], {"remote_task_id": "legacy-job", "usage_json": None})
        with patch.object(media_policy, "specification", side_effect=AssertionError("no invented policy")), \
             patch.object(cloud_tasks, "build_content", side_effect=AssertionError("no preparation")), \
             patch.object(ark, "video_create") as create, \
             patch.object(ark, "video_query", return_value=self.succeeded()), \
             patch.object(ark, "download", return_value=b"fixture-video"):
            result = self.run_video(task, params)
        create.assert_not_called()
        self.assertIsNone(result["usage"]["output_policy"])
        self.assertIsNone(result["usage"]["prepared_reference"])

    def test_download_failure_keeps_creation_provenance_for_later_resume(self):
        task, params = self.enqueue("image_to_video")
        with patch.object(ark, "video_create", return_value={"remote_task_id": "download-job"}), \
             patch.object(ark, "video_query", return_value=self.succeeded()), \
             patch.object(ark, "download", side_effect=err("DOWNLOAD_FAILED", "fixture failure")):
            with self.assertRaises(Exception) as caught:
                self.run_video(task, params)
        self.assertEqual(caught.exception.code, "DOWNLOAD_FAILED")
        persisted = db.loads(db.get("tasks", task["id"])["usage_json"])
        self.assertTrue(persisted["output_policy"])
        self.assertTrue(persisted["prepared_reference"])
        with patch.object(media_policy, "specification", side_effect=AssertionError("do not upgrade provenance")), \
             patch.object(cloud_tasks, "build_content", side_effect=AssertionError("do not rebuild content")), \
             patch.object(ark, "video_create") as create, \
             patch.object(ark, "video_query", return_value=self.succeeded()), \
             patch.object(ark, "download", return_value=b"fixture-video"):
            result = self.run_video(task, params)
        create.assert_not_called()
        self.assertEqual(result["remote_task_id"], "download-job")
        self.assertEqual(result["usage"]["output_policy"], persisted["output_policy"])
        self.assertEqual(result["usage"]["prepared_reference"], persisted["prepared_reference"])


if __name__ == "__main__":
    unittest.main()
