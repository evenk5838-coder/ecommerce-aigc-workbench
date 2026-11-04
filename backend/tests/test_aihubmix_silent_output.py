"""Real local FFmpeg invariants; no model, provider transport, or paid request."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from app import aihubmix_audio as audio, config, media_policy, node_execution
from app import providers_aihubmix as provider, storage
from app.errors import AppError


class AiHubMixSilentOutputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sources = tempfile.TemporaryDirectory(prefix="h3-silent-test-input-")
        cls.addClassCleanup(cls.sources.cleanup)
        cls.executable = storage.ffmpeg_exe()
        cls.fixtures = {}
        for has_audio in (False, True):
            path = Path(cls.sources.name) / ("audio.mp4" if has_audio else "silent.mp4")
            command = [cls.executable, "-nostdin", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=96x64:rate=12"]
            if has_audio:
                command += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000"]
            command += ["-t", "0.5", "-c:v", "libx264", "-pix_fmt", "yuv420p"]
            if has_audio:
                command += ["-c:a", "aac", "-shortest"]
            command += [str(path)]
            subprocess.run(command, capture_output=True, check=True, timeout=30)
            cls.fixtures[has_audio] = path.read_bytes()

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="h3-silent-test-output-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.enterContext(patch.object(config, "DATA", self.root))
        self.enterContext(patch.object(provider, "_request", side_effect=AssertionError("provider transport forbidden")))

    def original_files(self):
        return sorted(self.root.glob("provider-originals/aihubmix/*.mp4"))

    def frame_hashes(self, path):
        result = subprocess.run([self.executable, "-v", "error", "-i", str(path),
            "-map", "0:v:0", "-an", "-vsync", "0", "-f", "framemd5", "-"],
            capture_output=True, text=True, check=True, timeout=30)
        return [line.rsplit(",", 1)[-1].strip() for line in result.stdout.splitlines() if line and not line.startswith("#")]

    def test_audio_removed_with_every_decoded_video_frame_unchanged_and_original_preserved(self):
        native = self.fixtures[True]
        delivered, metadata = audio.apply(audio.H3, {"generate_audio": False}, "h3-job-1", native)
        self.assertEqual(metadata["version"], audio.VERSION)
        self.assertTrue(metadata["native_has_audio"])
        self.assertFalse(metadata["delivered_has_audio"])
        self.assertTrue(metadata["local_remux"])
        original = self.root / metadata["native_original_relpath"]
        self.assertFalse(Path(metadata["native_original_relpath"]).is_absolute())
        self.assertEqual(original.read_bytes(), native)
        self.assertEqual(metadata["native_original_sha256"], hashlib.sha256(native).hexdigest())
        self.assertEqual(self.original_files(), [original])
        output = self.root / "delivered.mp4"
        output.write_bytes(delivered)
        self.assertFalse(audio._inspect(output)["has_audio"])
        self.assertEqual(self.frame_hashes(original), self.frame_hashes(output))
        self.assertEqual(len(self.frame_hashes(output)), 6)
        self.assertEqual((metadata["native_video"]["width"], metadata["native_video"]["height"]), (96, 64))
        self.assertEqual(metadata["native_video"]["decoded_video_sha256"], metadata["delivered_video"]["decoded_video_sha256"])
        self.assertEqual(metadata["delivered_sha256"], hashlib.sha256(delivered).hexdigest())
        # Repeated free download of the same result reuses its immutable original.
        audio.apply(audio.H3, {}, "h3-job-1", native)
        self.assertEqual(self.original_files(), [original])

    def test_silent_native_input_is_identical_without_remux(self):
        with patch.object(audio.subprocess, "run", wraps=subprocess.run) as run:
            delivered, metadata = audio.apply(audio.H3, {}, "silent-native", self.fixtures[False])
        self.assertIs(delivered, self.fixtures[False])
        self.assertFalse(metadata["local_remux"])
        self.assertFalse(metadata["native_has_audio"])
        self.assertFalse(metadata["delivered_has_audio"])
        self.assertEqual(run.call_count, 1)
        self.assertNotIn("-c:v", run.call_args.args[0])
        self.assertEqual(self.original_files()[0].read_bytes(), self.fixtures[False])

    def test_other_models_and_audio_requested_are_exact_noops(self):
        for model, params in (("aihubmix:wan3.0-video", {}), ("aihubmix:doubao-seedance-2-0-fast-260128", {}),
                              ("aihubmix:flux-2-pro", {}), (audio.H3, {"generate_audio": True})):
            with self.subTest(model=model, params=params), patch.object(audio.subprocess, "run") as run:
                delivered, metadata = audio.apply(model, params, "../invalid", self.fixtures[True])
                self.assertIs(delivered, self.fixtures[True])
                self.assertIsNone(metadata)
                run.assert_not_called()
        self.assertEqual(list(self.root.iterdir()), [])

    def test_invalid_task_id_quarantines_original_without_path_traversal(self):
        with self.assertRaises(AppError) as raised:
            audio.apply(audio.H3, {}, "../private-token", self.fixtures[True])
        metadata = raised.exception.detail["audio_processing"]
        self.assertEqual(raised.exception.code, "MEDIA_ENCODE_FAILED")
        self.assertFalse(metadata["delivery_verified"])
        self.assertEqual((self.root / metadata["native_original_relpath"]).read_bytes(), self.fixtures[True])
        self.assertTrue(self.original_files()[0].name.startswith("invalid-id-"))
        self.assertNotIn("private-token", json.dumps(raised.exception.to_dict()))
        self.assertNotIn(str(self.root), json.dumps(raised.exception.to_dict()))

    def test_remux_failure_keeps_original_and_only_safe_provenance(self):
        native_inspection = audio._inspect(Path(self.sources.name) / "audio.mp4")
        with patch.object(audio, "_inspect", return_value=native_inspection), \
             patch.object(audio.subprocess, "run", side_effect=OSError("private-token /private/path")):
            with self.assertRaises(AppError) as raised:
                audio.apply(audio.H3, {}, "failed-remux", self.fixtures[True])
        metadata = raised.exception.detail["audio_processing"]
        self.assertEqual((self.root / metadata["native_original_relpath"]).read_bytes(), self.fixtures[True])
        self.assertTrue(metadata["native_has_audio"])
        self.assertNotIn("private-token", json.dumps(raised.exception.to_dict()))
        self.assertNotIn("/private/path", json.dumps(raised.exception.to_dict()))
        self.assertFalse(any(path.is_dir() for path in self.original_files()[0].parent.iterdir()))

    def test_undecodable_result_is_retained_and_existing_different_file_never_overwritten(self):
        with self.assertRaises(AppError) as raised:
            audio.apply(audio.H3, {}, "bad-result", b"not a video")
        original = self.root / raised.exception.detail["audio_processing"]["native_original_relpath"]
        self.assertEqual(original.read_bytes(), b"not a video")
        native = self.fixtures[True]
        expected = original.parent / f"collision-{hashlib.sha256(native).hexdigest()}.mp4"
        expected.write_bytes(b"existing-different-content")
        with self.assertRaises(AppError):
            audio.apply(audio.H3, {}, "collision", native)
        self.assertEqual(expected.read_bytes(), b"existing-different-content")

    def test_output_policy_versions_h3_without_changing_other_models(self):
        self.assertEqual(provider.output_policy("video", audio.H3)["audio_processing"], audio.policy(audio.H3))
        for kind, model in (("image", "aihubmix:seedream-5.0-flash"), ("video", "aihubmix:wan3.0-video"),
                            ("video", "aihubmix:doubao-seedance-2-0-fast-260128")):
            self.assertEqual(provider.output_policy(kind, model), media_policy.specification(kind))
        with patch.object(provider, "build_body", return_value={}), patch.object(provider, "_require_ready"), \
             patch.object(provider, "_request", return_value={"id": "created-h3"}):
            result = provider.media_create("video", audio.H3, {})
        self.assertEqual(result["output_policy"], provider.output_policy("video", audio.H3))

    def run_existing(self):
        task = {"remote_task_id": "existing-h3", "usage_json": None}
        contexts = [patch.object(provider.db, "get", return_value=task), patch.object(provider.db, "conn"),
                    patch.object(provider.db, "update"), patch.object(node_execution, "_reserved_cost_in_tx", return_value=1),
                    patch.object(provider, "media_create", side_effect=AssertionError("paid creation forbidden")),
                    patch.object(provider, "media_query", return_value={"status": "succeeded", "output": [], "usage": {}}),
                    patch.object(provider, "download_result", return_value=self.fixtures[True])]
        for context in contexts:
            self.enterContext(context)
        return provider.run_media("task-h3", audio.H3, {"generate_audio": False}, Mock())

    def test_existing_remote_success_saves_actual_audio_provenance_in_usage(self):
        result = self.run_existing()
        metadata = result["usage"]["audio_processing"]
        self.assertTrue(metadata["native_has_audio"])
        self.assertFalse(metadata["delivered_has_audio"])
        self.assertEqual(result["remote_task_id"], "existing-h3")
        persisted = json.loads(provider.db.update.call_args.args[2]["usage_json"])
        self.assertEqual(persisted["audio_processing"], metadata)

    def test_existing_remote_processing_failure_persists_retained_original(self):
        with patch.object(audio, "_inspect", side_effect=ValueError("private-native-error")):
            with self.assertRaises(AppError):
                self.run_existing()
        persisted = json.loads(provider.db.update.call_args.args[2]["usage_json"])
        metadata = persisted["audio_processing"]
        self.assertFalse(metadata["delivery_verified"])
        self.assertEqual((self.root / metadata["native_original_relpath"]).read_bytes(), self.fixtures[True])


if __name__ == "__main__":
    unittest.main()
