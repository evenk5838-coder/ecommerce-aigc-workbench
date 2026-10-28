"""Decode real local fixtures; never call a model, network or business database."""
from __future__ import annotations

import asyncio
import io
import sqlite3
import struct
import subprocess
import sys
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from starlette.datastructures import UploadFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import config, db, main, storage
from app.errors import AppError


class AssetUploadV011(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_dir = tempfile.TemporaryDirectory(prefix="upload-fixtures-")
        cls.videos = {}
        for ext, codec in (("mp4", "libx264"), ("mov", "libx264"), ("webm", "libvpx-vp9")):
            path = Path(cls.fixture_dir.name) / ("sample." + ext)
            subprocess.run([storage.ffmpeg_exe(), "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                            "color=c=blue:s=64x48:r=5", "-t", "0.4", "-c:v", codec, "-pix_fmt", "yuv420p", str(path)],
                           check=True, capture_output=True, timeout=30)
            cls.videos[ext] = path.read_bytes()

    @classmethod
    def tearDownClass(cls):
        cls.fixture_dir.cleanup()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="asset-upload-v011-")
        self.old_db, self.old_assets = config.DB_PATH, config.ASSETS
        config.DB_PATH, config.ASSETS = Path(self.tmp.name) / "test.db", Path(self.tmp.name) / "assets"
        config.ASSETS.mkdir()
        db.init_db()
        self.network = patch("app.providers_ark._request", side_effect=AssertionError("network forbidden"))
        self.network.start()

    def tearDown(self):
        self.network.stop()
        config.DB_PATH, config.ASSETS = self.old_db, self.old_assets
        self.tmp.cleanup()

    def upload(self, data, filename="misleading.bin"):
        file = UploadFile(file=io.BytesIO(data), filename=filename)
        try:
            return asyncio.run(main.upload_asset(file))
        finally:
            file.file.close()

    def png(self):
        stream = io.BytesIO()
        Image.new("RGB", (17, 21), "white").save(stream, format="PNG")
        return stream.getvalue()

    def expect_rejected(self, data):
        before = db.query("SELECT * FROM assets")
        with self.assertRaises(AppError) as caught:
            self.upload(data, "fake.mp4")
        self.assertEqual(caught.exception.code, "UPLOAD_REJECTED")
        self.assertEqual(db.query("SELECT * FROM assets"), before)
        self.assertEqual(list(config.ASSETS.rglob("*")), [])

    def test_legacy_image_return_fields_preserved_and_real_mime_used(self):
        out = self.upload(self.png(), "not-a-video.mp4")
        self.assertTrue(out["asset_id"])
        self.assertTrue(out["sha256"])
        self.assertEqual((out["width"], out["height"]), (17, 21))
        self.assertEqual((out["kind"], out["mime"], out["duration_ms"]), ("image", "image/png", None))

    def test_mp4_mov_webm_real_decoding_and_container_preserved(self):
        for ext, mime in (("mp4", "video/mp4"), ("mov", "video/quicktime"), ("webm", "video/webm")):
            with self.subTest(ext=ext):
                out = self.upload(self.videos[ext], "pretend-image.png")
                self.assertEqual((out["kind"], out["mime"], out["width"], out["height"]), ("video", mime, 64, 48))
                self.assertGreater(out["duration_ms"], 0)
                asset = db.get("assets", out["asset_id"])
                self.assertTrue(asset["rel_path"].endswith("." + ext))
                self.assertEqual(storage.asset_path(asset).read_bytes(), self.videos[ext])
        self.assertEqual(db.query("SELECT * FROM tasks"), [])

    def test_same_upload_reuses_asset_without_duplicate_rows(self):
        a = self.upload(self.videos["mp4"], "one.mp4")
        b = self.upload(self.videos["mp4"], "two.mov")
        self.assertEqual(a, b)
        self.assertEqual(len(db.query("SELECT * FROM assets")), 1)

    def test_parallel_identical_uploads_share_asset_and_publish_only_complete_files(self):
        for index, data in enumerate((self.png(), self.videos["mp4"])):
            with self.subTest(kind="image" if index == 0 else "video"):
                reads = threading.Barrier(2)
                publications = threading.Barrier(2)
                query, replace = db.query, storage.os.replace
                observed = []

                def query_together(sql, args=()):
                    result = query(sql, args)
                    if sql == "SELECT * FROM assets WHERE sha256=?":
                        self.assertEqual(result, [])
                        # Reproduce the real race: both uploads have observed no
                        # row, then attempt to persist the same content.
                        reads.wait(timeout=10)
                    return result

                def publish_together(source, destination):
                    source, destination = Path(source), Path(destination)
                    self.assertEqual(source.parent, destination.parent)
                    self.assertEqual(source.read_bytes(), data)
                    if destination.exists():
                        self.assertEqual(destination.read_bytes(), data)
                    publications.wait(timeout=10)
                    replace(source, destination)
                    # Even while the other uploader replaces the path, readers
                    # must see the complete original bytes, never a partial file.
                    observed.append(destination.read_bytes())

                with patch.object(db, "query", side_effect=query_together), \
                        patch.object(storage.os, "replace", side_effect=publish_together), \
                        ThreadPoolExecutor(max_workers=2) as pool:
                    futures = [pool.submit(self.upload, data, "one.bin"),
                               pool.submit(self.upload, data, "two.bin")]
                    outputs = [future.result(timeout=30) for future in futures]
                self.assertEqual(outputs[0], outputs[1])
                self.assertEqual(observed, [data, data])
                asset = db.get("assets", outputs[0]["asset_id"])
                self.assertEqual(storage.asset_path(asset).read_bytes(), data)
                self.assertEqual(len(db.query("SELECT * FROM assets")), index + 1)
                self.assertEqual(list(config.ASSETS.rglob(".upload-*")), [])

    def test_unrelated_integrity_error_is_not_hidden_by_deduplication(self):
        first = self.upload(self.png())
        stream = io.BytesIO()
        Image.new("RGB", (17, 21), "red").save(stream, format="PNG")
        with patch.object(db, "new_id", return_value=first["asset_id"]):
            with self.assertRaises(sqlite3.IntegrityError) as caught:
                self.upload(stream.getvalue())
        self.assertIn("assets.id", str(caught.exception))
        rows = db.query("SELECT * FROM assets")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sha256"], first["sha256"])

    def test_publish_failure_does_not_register_asset_or_leave_staged_files(self):
        with patch.object(storage.os, "replace", side_effect=OSError("fixture publish failed")):
            with self.assertRaisesRegex(OSError, "fixture publish failed"):
                self.upload(self.png())
        self.assertEqual(db.query("SELECT * FROM assets"), [])
        self.assertEqual([path for path in config.ASSETS.rglob("*") if path.is_file()], [])

    def test_allowed_image_formats_match_actual_decoder_and_stored_mime(self):
        for fmt, mime, extension in (("JPEG", "image/jpeg", "jpg"), ("PNG", "image/png", "png"),
                                     ("WEBP", "image/webp", "webp"), ("GIF", "image/gif", "gif")):
            with self.subTest(format=fmt):
                stream = io.BytesIO()
                Image.new("RGB", (19, 23), "blue").save(stream, format=fmt)
                data = stream.getvalue()
                out = self.upload(data, "misleading.jpg")
                asset = db.get("assets", out["asset_id"])
                self.assertEqual(out["mime"], mime)
                self.assertTrue(asset["rel_path"].endswith("." + extension))
                with Image.open(storage.asset_path(asset)) as image:
                    image.load()
                    self.assertEqual(image.format, fmt)

    def test_riff_header_on_decodable_fli_is_not_accepted_as_webp(self):
        # FLI's decoder checks the magic at offset 4, not its nominal file size.
        # A RIFF prefix therefore passes the legacy sniff while decoding as FLI.
        header = bytearray(128)
        header[:4] = b"RIFF"
        struct.pack_into("<HHHHHHI", header, 4, 0xAF12, 1, 2, 2, 8, 3, 100)
        chunk = struct.pack("<IH", 10, 16) + bytes(4)  # one uncompressed 2x2 frame
        frame = bytearray(16)
        struct.pack_into("<IHH", frame, 0, 16 + len(chunk), 0xF1FA, 1)
        data = bytes(header) + bytes(frame) + chunk
        self.assertEqual(storage.sniff(data), ("image/webp", "webp"))
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            self.assertEqual((image.format, image.size), ("FLI", (2, 2)))
        self.expect_rejected(data)

    def test_fake_mp4_header_is_not_registered(self):
        self.expect_rejected(b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isommp42" + b"invalid media" * 10)

    def test_truncated_video_is_rejected_without_asset(self):
        self.expect_rejected(self.videos["mp4"][:64])

    def test_bad_image_magic_is_rejected_without_asset(self):
        self.expect_rejected(b"\x89PNG\r\n\x1a\nnot png")

    def test_unrecognized_container_and_empty_file_are_rejected(self):
        self.expect_rejected(b"this is neither a picture nor a video")
        self.expect_rejected(b"")

    def test_upload_limit_is_enforced_before_decoding(self):
        with patch.object(config, "MAX_UPLOAD_MB", 1), patch.object(storage, "ffmpeg_exe", side_effect=AssertionError("must not decode")):
            self.expect_rejected(b"x" * (1024 * 1024 + 1))

    def test_decode_timeout_is_user_error_without_durable_files(self):
        with patch.object(storage.subprocess, "run", side_effect=subprocess.TimeoutExpired("decode", 30)):
            self.expect_rejected(self.videos["mp4"])


if __name__ == "__main__":
    unittest.main()
