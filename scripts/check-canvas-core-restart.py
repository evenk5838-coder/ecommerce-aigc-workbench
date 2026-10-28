"""HTTP restart check on disposable data, with paid workers explicitly disabled."""
from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def serve(directory: str, port: int):
    sys.path.insert(0, str(ROOT / "backend"))
    original = Path.read_text
    with patch.object(Path, "read_text", lambda path, *a, **kw:
                      "" if path.name == ".env" else original(path, *a, **kw)):
        from app import config
    private = Path(directory)
    config.DATA, config.DB_PATH = private, private / "test.db"
    config.ASSETS, config.MODELS = private / "assets", private / "models"
    config.ASSETS.mkdir(exist_ok=True)
    config.MODELS.mkdir(exist_ok=True)
    config.BUDGET_CNY = 20  # Test queue accounting only; no paid transport can run.
    from app import providers_ark, worker
    from app.assistant import provider

    def forbidden(*args, **kwargs):
        raise AssertionError("External model requests forbidden in this check")

    providers_ark._request = forbidden
    provider._deepseek_post = forbidden
    worker.start = lambda: None
    worker.stop = lambda: None
    from app.main import app
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)


def run():
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(path, body=None, method=None):
        raw = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(base + path, data=raw, method=method,
                                     headers={"Content-Type": "application/json"} if raw else {})
        with opener.open(req, timeout=15) as response:
            return json.load(response)

    environment = {**os.environ, "ARK_API_KEY": "", "DEEPSEEK_API_KEY": "", "ASSISTANT_API_KEY": "",
                   "ASSISTANT_PROVIDER": "none", "APP_BUDGET_CNY": "20"}
    with tempfile.TemporaryDirectory(prefix="canvas-core-http-") as temporary:
        def start():
            process = subprocess.Popen([sys.executable, __file__, "serve", temporary, str(port)],
                env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            for _ in range(100):
                if process.poll() is not None:
                    raise RuntimeError("Isolated server exited before readiness")
                try:
                    request("/api/health")
                    return process
                except OSError:
                    time.sleep(.1)
            process.terminate()
            process.wait(timeout=10)
            raise RuntimeError("Isolated server readiness timeout")

        def stop(process):
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            assert process.poll() is not None
            process.stdout.close()

        process = start()
        try:
            project = request("/api/projects", {"name": "Canvas execution restart fixture"})["project_id"]
            path = f"/api/projects/{project}/canvas"
            cfg = {"generation_mode": "text_to_image", "model_id": "doubao-seedream-5-0-flash-260915",
                   "prompt": "A cup in morning light", "params": {"ratio": "16:9", "size": "2688x1536"}}
            request(path, {"nodes": [
                {"node_id": "picture", "type": "image", "position": {"x": 200, "y": 100}, "config": cfg},
                {"node_id": "words", "type": "text", "position": {"x": 20, "y": 30}, "config": {"text": "Keep my script"}}],
                "edges": [{"from": "words", "to": "picture"}], "viewport": {"x": 5, "y": 7, "zoom": .8}}, "PATCH")
            quote = request(path + "/nodes/picture/quote", {})
            body = {"quote_id": quote["quote_id"], "client_request_id": "once-after-restart", "user_action": "confirm_generate"}
            execution = request(path + "/nodes/picture/generate", body)
            assert execution["status"] == "queued"
            deletion = request(path, {"delete_node_ids": ["picture", "words"]}, "PATCH")
            operation = deletion["deletion_operation_id"]
            request(path, {"nodes": [{"node_id": "later", "type": "text", "config": {"text": "New unrelated edit"}}]}, "PATCH")
            stop(process)
            process = None
            print("Write: real HTTP quote -> one queued task -> batch deletion + unrelated edit; server stopped")
            process = start()
            current = request(path)
            history = request(path + "/deletions")
            assert history["operations"][0]["operation_id"] == operation
            assert [n["node_id"] for n in current["nodes"]] == ["later"]
            view = request(path + "/nodes/picture/executions/" + execution["execution_id"])
            assert view["task_id"] == execution["task_id"]
            undone = request(path + f"/deletions/{operation}/undo", {"expected_layout_revision": current["layout_revision"]})
            nodes = {n["node_id"]: n for n in undone["canvas"]["nodes"]}
            assert len(nodes) == 3
            assert nodes["picture"]["config"] == cfg
            assert nodes["picture"]["position"] == {"x": 200, "y": 100}
            assert nodes["picture"]["last_execution"]["task_id"] == execution["task_id"]
            assert nodes["later"]["config"]["text"] == "New unrelated edit"
            assert undone["canvas"]["edges"] == [{"from": "words", "to": "picture"}]
            assert undone["canvas"]["viewport"] == {"x": 5, "y": 7, "zoom": .8}
            replay = request(path + "/nodes/picture/generate", body)
            assert replay["idempotent_replay"] and replay["task_id"] == execution["task_id"]
            tasks = request("/api/tasks")
            assert len(tasks["items"]) == 1
            record = {"label": "Actual isolated HTTP restart; worker disabled; zero external model calls",
                      "project_id": project, "quote_id": quote["quote_id"], "execution_id": execution["execution_id"],
                      "task_id": execution["task_id"], "operation_id": operation, "task_count": 1,
                      "restored_nodes": list(nodes), "nodes_after_restart_before_undo": ["later"],
                      "idempotent_replay": replay["idempotent_replay"], "real_model_calls": 0,
                      "worker_executed": False, "canvas": undone["canvas"]}
            evidence = os.environ.get("CANVAS_CORE_EVIDENCE_DIR")
            if evidence:
                output = Path(evidence)
                output.mkdir(parents=True, exist_ok=True)
                (output / "节点执行与撤销-重启HTTP.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            print("Read: original queued task/quote/history recovered; undo restores nodes/config/edges; replay adds no task")
            print("PASS: private HTTP restart, persistent undo and generation receipt recovery; 0 paid calls")
        finally:
            if process is not None:
                stop(process)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "serve":
        serve(sys.argv[2], int(sys.argv[3]))
    else:
        run()
