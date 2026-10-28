"""Two fresh processes exercise real ASGI routes on one temporary database.

No lifespan/worker, business database or external provider is used. This checks
durable application state across process replacement, not paid model quality.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import ExitStack
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def child(mode: str, directory: Path) -> None:
    sys.path.insert(0, str(ROOT / "backend"))
    from app import config, db
    config.DATA = directory
    config.DB_PATH = directory / "restart.db"
    config.ASSETS = directory / "assets"
    config.MODELS = directory / "models"
    for path in (config.ASSETS, config.MODELS):
        path.mkdir(exist_ok=True)
    config.DIRECTOR_MAX_COST_PER_CALL_CNY = 0
    config.DIRECTOR_MAX_SESSION_COST_CNY = 0
    original_connect = sqlite3.connect

    def connect(database, *args, **kwargs):
        assert Path(database).resolve() == config.DB_PATH.resolve(), "Business DB forbidden"
        return original_connect(database, *args, **kwargs)

    with ExitStack() as stack:
        stack.enter_context(patch("sqlite3.connect", side_effect=connect))
        remote_url = os.environ.get("V011_CHECK_SERVER_URL")
        if not remote_url:
            stack.enter_context(patch("socket.socket.connect", side_effect=AssertionError("Network forbidden")))
            stack.enter_context(patch("socket.create_connection", side_effect=AssertionError("Network forbidden")))
        from app import main
        from app.assistant import provider
        from PIL import Image
        db.init_db()
        # No context manager: lifespan and background generation never run.
        if remote_url:
            import httpx
            assert remote_url.startswith("http://127.0.0.1:"), "Only an isolated loopback server is allowed"
            client = httpx.Client(base_url=remote_url, timeout=30, trust_env=False)
        else:
            from fastapi.testclient import TestClient
            client = TestClient(main.app)

        def request(method, path, **kwargs):
            response = client.request(method, path, **kwargs)
            assert response.status_code == 200, (path, response.status_code, response.text)
            return response.json()

        if mode == "write":
            pid = request("POST", "/api/projects", json={"name": "isolated restart fixture"})["project_id"]
            endpoint = f"/api/projects/{pid}"
            stream = io.BytesIO()
            Image.new("RGB", (24, 32), "blue").save(stream, format="PNG")
            asset = request("POST", "/api/assets", files={"file": ("fixture.png", stream.getvalue(), "image/png")})
            request("PATCH", endpoint + "/canvas", json={"nodes": [
                {"node_id": "note", "type": "text", "position": {"x": -150, "y": 95},
                 "config": {"title": "创意笔记", "text": "保留用户的第一幕\n第二幕"}},
                {"node_id": "import", "type": "asset", "binding": {"asset_id": asset["asset_id"]}},
                {"node_id": "image", "type": "image", "config": {"prompt": "one bottle", "generation_mode": "text_to_image"}},
                {"node_id": "video", "type": "video", "config": {"prompt": "slow rotation", "generation_mode": "text_to_video"}}],
                "viewport": {"x": 120, "y": -45, "zoom": .65}, "edges": [{"from": "note", "to": "image"}]})
            body = {"message": "请按需求写脚本", "intent": "script", "client_request_id": "restart-saved"}
            fixed = provider.FixedReplyProvider([{"tool_calls": [{"name": "propose_script", "arguments": {
                "summary": "A real saved fixture script", "shots": [{"hint": "Show one bottle", "duration_s": 5}]}}]}])
            with patch.object(provider, "get_provider", return_value=fixed):
                saved = request("POST", endpoint + "/director/chat", json=body)
            assert saved["outcome"]["status"] == "saved" and (remote_url or len(fixed.calls) == 1)
            failed_body = {"message": "起稿", "intent": "script", "client_request_id": "restart-failed"}
            broken = provider.FixedReplyProvider([{"tool_calls": [{"name": "propose_script", "arguments": {"summary": ""}}]}] * 2)
            with patch.object(provider, "get_provider", return_value=broken):
                failed = request("POST", endpoint + "/director/chat", json=failed_body)
            assert failed["outcome"]["status"] == "failed"
            snapshot = {"project_id": pid, "body": body, "failed_body": failed_body,
                        "saved": saved, "failed": failed,
                        "canvas": request("GET", endpoint + "/canvas"),
                        "scripts": request("GET", endpoint + "/scripts"),
                        "asset": asset}
            if remote_url:
                snapshot["fixture_provider_calls"] = (directory / "provider-calls.txt").read_text()
            (directory / "expected.json").write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
            print("write process: HTTP upload, free nodes, saved script, explicit failure persisted")
        else:
            expected = json.loads((directory / "expected.json").read_text())
            endpoint = f"/api/projects/{expected['project_id']}"
            assert request("GET", endpoint + "/canvas") == expected["canvas"]
            assert request("GET", endpoint + "/scripts") == expected["scripts"]
            node = next(n for n in expected["canvas"]["nodes"] if n["node_id"] == "script")
            assert node["binding"]["script_id"] == expected["scripts"]["items"][0]["script_id"]
            no_call = provider.FixedReplyProvider()
            with patch.object(provider, "get_provider", return_value=no_call):
                for body_key, result_key in (("body", "saved"), ("failed_body", "failed")):
                    replay = request("POST", endpoint + "/director/chat", json=expected[body_key])
                    assert replay.pop("idempotent_replay") is True
                    assert replay == expected[result_key]
            assert no_call.calls == []
            if remote_url:
                assert (directory / "provider-calls.txt").read_text() == expected["fixture_provider_calls"]
            assert len(request("GET", endpoint + "/scripts")["items"]) == 1
            assert db.query("SELECT COUNT(*) AS n FROM tasks")[0]["n"] == 0
            asset = db.get("assets", expected["asset"]["asset_id"])
            assert (config.ASSETS / asset["rel_path"]).is_file()
            print("read process: nodes/config/body/bindings/viewport/assets recovered; receipts replayed without LLM")
        client.close()


if __name__ == "__main__":
    if len(sys.argv) == 3:
        child(sys.argv[1], Path(sys.argv[2]))
    else:
        with tempfile.TemporaryDirectory(prefix="ecom-v011-restart-") as temporary:
            for mode in ("write", "read"):
                subprocess.run([sys.executable, str(Path(__file__).resolve()), mode, temporary], check=True, timeout=60)
        print("PASS: process replacement recovery (isolated ASGI, no live service restart)")
