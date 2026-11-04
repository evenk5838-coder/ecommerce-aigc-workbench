"""Isolated real-HTTP director workflow and process-restart acceptance check.

Uses fixed LLM tools and fixed PNG/MP4 runners with the real queue and storage.
Only the final concatenation uses real FFmpeg. No paid/model transport or user
database is allowed. Run with the backend Python environment and optionally
--evidence-dir /private/tmp/director-http-evidence-<unique-name>.
Add --film for one video-generation confirmation that also authorizes ordered
concatenation after every clip succeeds. Default keeps explicit composition.
"""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
LABEL = "实际隔离 HTTP / 持久化 / worker / 重启；固定 LLM 与媒体替身，零真实模型调用，非模型质量验收"
IMAGE = "doubao-seedream-5-0-flash-260915"
VIDEO = "doubao-seedance-1-0-pro-fast-251015"


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def serve(directory: str, port: int, film: bool = False, legacy_reservation: bool = False, shared_reference: bool = False, spec_combinations: bool = False, aihubmix: bool = False) -> None:
    global IMAGE, VIDEO
    private = Path(directory).resolve()
    if (not private.is_relative_to(Path("/private/tmp")) or not private.name.startswith("director-workflow-http-")
            or json.loads((private / "isolation.json").read_text()).get("isolated") is not True):
        raise ValueError("Only the marked disposable HTTP directory is allowed")
    sys.path.insert(0, str(ROOT / "backend"))
    original_exists, original_mkdir = Path.exists, Path.mkdir
    forbidden_dirs = {ROOT / "data", ROOT / "data/assets", ROOT / "data/models"}
    with patch.object(Path, "exists", lambda path: False if path == ROOT / ".env" else original_exists(path)), \
            patch.object(Path, "mkdir", lambda path, *args, **kwargs: None if path in forbidden_dirs else original_mkdir(path, *args, **kwargs)):
        from app import config
    config.DATA, config.DB_PATH = private, private / "check.db"
    config.ASSETS, config.MODELS = private / "assets", private / "models"
    config.BUDGET_CNY = 20  # Queue-accounting fixture only, never a provider allowance.
    config.DIRECTOR_MAX_COST_PER_CALL_CNY = config.DIRECTOR_MAX_SESSION_COST_CNY = 0
    config.POLL_INTERVAL = .03
    config._load_dotenv = lambda: None
    config.model_lifecycle_state = lambda *args, **kwargs: {"status": "active"}
    if aihubmix:
        from app import aihubmix_models
        IMAGE, VIDEO = aihubmix_models.FLASH, aihubmix_models.FAST
        config.MODEL_ACTIVATION[IMAGE] = config.MODEL_ACTIVATION[VIDEO] = "verified"
        os.environ["AIHUBMIX_API_KEY"] = "fixture-unused-aihubmix-key"
        os.environ["AIHUBMIX_USD_TO_CNY"] = "8"  # Isolated estimate fixture, no payment authority.
        os.environ["AIHUBMIX_ENABLED_MODELS"] = f"{IMAGE},{VIDEO}"
    if spec_combinations:
        # Hypothetical rules in this disposable process only, not provider claims.
        config.MODEL_CAPABILITIES[IMAGE]["parameter_combinations"] = [{
            "generation_mode": ["text_to_image", "image_to_image"],
            "size": ["1024x1024"], "ratio": ["1:1"]}]
        config.MODEL_CAPABILITIES[VIDEO]["parameter_combinations"] = [{
            "generation_mode": ["image_to_video"], "ratio": ["16:9"],
            "resolution": ["720p"], "duration": [5], "generate_audio": [False]}]
    for folder in (config.ASSETS, config.MODELS):
        folder.mkdir(exist_ok=True)
    for model_id, metadata in config.MODEL_REGISTRY.items():
        if "dir" in metadata:
            metadata["dir"] = str(config.MODELS / model_id)
    connect = sqlite3.connect
    def isolated_connect(database, *args, **kwargs):
        if Path(database).resolve() != config.DB_PATH.resolve():
            raise AssertionError("Only the disposable database may be opened")
        return connect(database, *args, **kwargs)
    sqlite3.connect = isolated_connect
    socket_connect, socket_connect_ex = socket.socket.connect, socket.socket.connect_ex
    def no_outbound(connection, address, *, raw=False):
        if connection.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError("Outbound network/model requests are forbidden")
        return (socket_connect_ex if raw else socket_connect)(connection, address)
    socket.socket.connect = no_outbound
    socket.socket.connect_ex = lambda connection, address: no_outbound(connection, address, raw=True)
    from app import cloud_tasks, db, main, providers_ark, storage, worker
    from app.assistant import provider
    from PIL import Image

    def forbidden(*args, **kwargs):
        raise AssertionError("External model transport is forbidden in this check")
    provider._deepseek_post = providers_ark._request = forbidden
    providers_ark._load_dotenv = lambda: None
    worker.generate_image = worker.generate_video = forbidden
    if legacy_reservation:
        db.init_db()
        if db.get("tasks", "legacy-unknown-video") is None:
            db.insert("tasks", {"id": "legacy-unknown-video", "kind": "video", "status": "failed",
                "model_id": "doubao-seedance-2-0-260128", "error_code": "CREATE_RESULT_UNKNOWN",
                "params_json": db.dumps({"duration": 5, "resolution": "720p", "ratio": "9:16"}),
                "created_at": 1792657128.552663})
        assert config.duration_policy("doubao-seedance-2-0-260128")["kind"] == "unavailable"
    events_file = private / "fixtures.jsonl"
    shared_asset = None
    def event(value):
        with events_file.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"at": time.time(), "real_model": False, **value}, ensure_ascii=False) + "\n")

    class FixedDirector(provider.FixedReplyProvider):
        def __init__(self):
            super().__init__(model="fixed-http-director")

        def chat(self, messages, tools, tool_results=None, *, max_tokens=None):
            message = next(str(item["content"]) for item in reversed(messages) if item["role"] == "user")
            stage = next((name for name in ("CLARIFY", "SCRIPT", "STORYBOARDS", "VIDEOS", "COMPOSE")
                          if f"[{name}]" in message), None)
            if stage is None:
                raise AssertionError("Unknown fixed scenario")
            advertised = [call["id"] for item in messages if item["role"] == "assistant"
                          for call in item.get("tool_calls", [])]
            answered = [item["tool_call_id"] for item in messages if item["role"] == "tool"]
            assert sorted(advertised) == sorted(answered), "Orphaned tool call in continuation"
            event({"kind": "llm_fixture", "stage": stage, "answered_tool_calls": len(answered)})
            if stage in ("STORYBOARDS", "VIDEOS", "COMPOSE") and not tool_results:
                return provider.ChatResult(reply="", tool_calls=[
                    provider.ToolCall("get_creation_context", {}, id=f"{stage}-context"),
                    provider.ToolCall("list_models", {"kind": "image" if stage == "STORYBOARDS" else "video"}, id=f"{stage}-models")])
            context = next((item["result"]["creation"] for item in tool_results or []
                            if item["tool"] == "get_creation_context" and item["ok"]), None)
            if stage == "CLARIFY":
                name, args = "respond_to_user", {"kind": "clarify", "message": "这支短片希望采用什么画面比例？",
                                                   "missing_fields": ["ratio"]}
            elif stage == "SCRIPT":
                name, args = "write_script", {"summary": "一只水杯的两镜短片：先展示杯身，再展示杯口。",
                    "shots": [{"hint": "RED：杯身全景", "duration_s": 5}, {"hint": "BLUE：杯口细节", "duration_s": 5}]}
            elif stage == "STORYBOARDS":
                source = next(node for node in context["nodes"] if node["node_id"] == "script")
                name, args = "prepare_storyboards", {"source_node_id": "script", "expected_source_revision": source["config_revision"],
                    "model_id": IMAGE, "params": {"ratio": "1:1", "size": "1024x1024"}, "shots": [
                        {"title": "RED image", "prompt": "RED fixture: one cup full view", "duration_s": 5},
                        {"title": "BLUE image", "prompt": "BLUE fixture: one cup close-up", "duration_s": 5}]}
                if shared_reference:
                    args["input_asset_id"] = shared_asset["id"]
            elif stage == "VIDEOS":
                nodes = sorted((node for node in context["nodes"] if node["type"] == "image"),
                               key=lambda node: 0 if ("BLUE" if film else "RED") in node["config"]["title"] else 1)
                name, args = "prepare_video_nodes", {"model_id": VIDEO,
                    "params": {"duration": 5, "ratio": "16:9", "resolution": "480p" if aihubmix else "720p",
                               "generate_audio": False}, "sources": [
                        {"node_id": node["node_id"], "expected_config_revision": node["config_revision"],
                         "asset_id": node["asset"]["id"], "execution_id": node["last_execution"]["execution_id"],
                         "title": node["config"]["title"].replace("image", "clip"),
                         "prompt": node["config"]["prompt"] + "; slow camera push"} for node in nodes]}
                if film:
                    # This order is explicitly requested by the user and frozen
                    # with the generation action, not inferred from positions.
                    args["output_mode"] = "film"
            else:
                assert not film, "Film scope must not require a second COMPOSE conversation"
                # The user explicitly asks BLUE then RED. Canvas position/order is irrelevant.
                nodes = sorted((node for node in context["nodes"] if node["type"] == "video"),
                               key=lambda node: 0 if "BLUE" in node["config"]["title"] else 1)
                name, args = "prepare_composition", {"title": "BLUE then RED · fixed-media proof", "sources": [
                    {"asset_id": node["asset"]["id"], "node_id": node["node_id"],
                     "execution_id": node["last_execution"]["execution_id"]} for node in nodes]}
            return provider.ChatResult(reply="", tool_calls=[provider.ToolCall(name, args, id=f"{stage}-write")],
                                       meta={"provider": "fixed", "stub": True})

    provider.set_provider(FixedDirector())
    fixtures = private / "fixed-media"
    fixtures.mkdir(exist_ok=True)
    for color, duration in (("red", .4), ("blue", .6)):
        picture, clip = fixtures / (color + ".png"), fixtures / (color + ".mp4")
        if not picture.exists():
            Image.new("RGB", (64, 48), color).save(picture)
        if not clip.exists():
            subprocess.run([storage.ffmpeg_exe(), "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                            f"color=c={color}:s=64x48:r=10:d={duration}", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                            "-an", str(clip)], check=True, capture_output=True, timeout=20)
    if shared_reference:
        db.init_db()
        picture = fixtures / "product-reference.png"
        if not picture.exists():
            Image.new("RGB", (72, 56), "green").save(picture)
        shared_asset = storage.save_bytes(picture.read_bytes(), "image", "shared-reference-fixture")
    def runner(kind):
        def fixed(task_id, model_id, params, token, on_progress=None):
            token.raise_if_canceled()
            if spec_combinations:
                assert not config.validate_params_for_model(model_id, kind, params), "Worker received an invalid combination"
            cloud_tasks.budget_gate(cloud_tasks.estimate_cost(kind, model_id, params))
            if kind == "video":
                cloud_tasks.build_content(params)
            reference_digest = None
            if kind == "image" and shared_reference:
                assert params["generation_mode"] == "image_to_image"
                assert params["input_asset_id"] == shared_asset["id"]
                ref = storage.load_asset(params["input_asset_id"])
                encoded = storage.asset_data_url(ref).split(",", 1)[1]
                reference_digest = hashlib.sha256(base64.b64decode(encoded)).hexdigest()
                assert reference_digest == shared_asset["sha256"]
            color = "red" if "RED" in params["prompt"] else "blue"
            event({"kind": "media_fixture", "media_kind": kind, "task_id": task_id, "color": color,
                   "reference_asset_id": params.get("input_asset_id"), "reference_sha256": reference_digest})
            if on_progress:
                on_progress(.8)
            return {"data": (fixtures / (color + (".png" if kind == "image" else ".mp4"))).read_bytes(),
                    "kind": kind, "cost_cny": 0, "usage": {"fixture": True, "real_model": False, "real_charge": False},
                    "load_ms": 0, "latency_ms": 0}
        return fixed
    cloud_tasks.run_image, cloud_tasks.run_video = runner("image"), runner("video")
    if aihubmix:
        from app import providers_aihubmix
        providers_aihubmix.POLL_SECONDS = 0
        jobs = private / "native-jobs"
        jobs.mkdir(exist_ok=True)
        def native_transport(method, path, body=None, **kwargs):
            if method == "POST":
                kind = "image" if path == "/ai/v1/images/generations" else "video"
                assert path == ("/ai/v1/images/generations" if kind == "image" else "/ai/v1/videos")
                assert body["model"] == config.MODEL_REGISTRY[IMAGE if kind == "image" else VIDEO]["upstream_model_id"]
                reference = body.get("image") if kind == "image" else body["frame_images"][0]["image_url"]["url"]
                if shared_reference and kind == "image":
                    assert hashlib.sha256(base64.b64decode(reference.split(",", 1)[1])).hexdigest() == shared_asset["sha256"]
                color = "red" if "RED" in body["prompt"] else "blue"
                remote_id = db.new_id("fixture")
                write_json(jobs / (remote_id + ".json"), {"kind": kind, "color": color})
                event({"kind": "media_fixture", "media_kind": kind, "provider": "aihubmix",
                       "color": color, "remote_task_id": remote_id, "native_path": path,
                       "reference_sent": bool(reference),
                       "reference_asset_id": shared_asset["id"] if shared_reference and kind == "image" else None,
                       "reference_sha256": shared_asset["sha256"] if shared_reference and kind == "image" else None})
                return {"id": remote_id}
            assert method == "GET"
            remote_id = path.split("/")[4]
            job = json.loads((jobs / (remote_id + ".json")).read_text())
            assert db.query("SELECT id FROM tasks WHERE remote_task_id=?", (remote_id,)), "ID must be durable before query"
            if kwargs.get("binary"):
                return (fixtures / (job["color"] + (".png" if job["kind"] == "image" else ".mp4"))).read_bytes()
            suffix = "/content/result" if job["kind"] == "image" else "/content"
            return {"id": remote_id, "object": job["kind"], "status": "completed",
                    "output": [{"content_url": "https://aihubmix.com" + path + suffix}]}
        providers_aihubmix._request = native_transport

    @main.app.get("/__check__/metrics")
    def metrics():
        events = [json.loads(line) for line in events_file.read_text().splitlines()] if events_file.exists() else []
        return {"label": LABEL, "scope": "film" if film else "clips_then_explicit_compose",
                "shared_reference": {key: shared_asset[key] for key in ("id", "sha256")} if shared_asset else None,
                "legacy_task": db.get("tasks", "legacy-unknown-video") if legacy_reservation else None,
                "pid": os.getpid(), "task_count": len(db.query("SELECT id FROM tasks")),
                "node_count": len(db.query("SELECT id FROM canvas_nodes")), "script_count": len(db.query("SELECT id FROM script_versions")),
                "composition_count": len(db.query("SELECT id FROM director_compositions")),
                "llm_fixture_calls": sum(item["kind"] == "llm_fixture" for item in events),
                "media_fixture_calls": sum(item["kind"] == "media_fixture" for item in events), "events": events,
                "real_model_calls": 0, "provider_transports_blocked": True}
    import uvicorn
    uvicorn.run(main.app, host="127.0.0.1", port=port, log_level="warning", access_log=False)


def run(evidence_directory: str | None = None, film_mode: bool = False, legacy_reservation: bool = False, shared_reference: bool = False, spec_combinations: bool = False, aihubmix: bool = False) -> None:
    evidence = Path(evidence_directory).resolve() if evidence_directory else Path(tempfile.mkdtemp(prefix="director-http-evidence-", dir="/private/tmp"))
    evidence.mkdir(parents=True, exist_ok=True)
    if any((evidence / name).exists() for name in ("director-workflow-http.json", "fixed-blue-then-red.mp4")):
        raise FileExistsError("Use a fresh evidence directory; prior acceptance evidence must be preserved")
    scenario = "film" if film_mode else "clips_then_explicit_compose"
    record = {"label": LABEL + ("；一次确认视频与顺序合成" if film_mode else "；逐条素材与独立合成确认"),
              "scope": scenario, "video_output_mode": "film" if film_mode else "clips",
              "legacy_reservation_fixture": legacy_reservation,
              "shared_reference_fixture": shared_reference,
              "spec_combinations_fixture": spec_combinations,
              "started_at": datetime.now(timezone.utc).isoformat(), "passed": False, "http": []}
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def request(path, body=None, *, expected=200, binary=False, track=True):
        raw = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(base + path, data=raw, headers={"Content-Type": "application/json"} if raw else {})
        try:
            response = opener.open(req, timeout=30)
        except urllib.error.HTTPError as exc:
            response = exc
        with response:
            data = response.read()
            value = data if binary else json.loads(data)
            if track:
                record["http"].append({"method": req.get_method(), "path": path, "request": body,
                    "status": response.status, "response": {"size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
                     "content_type": response.headers.get("Content-Type")} if binary else value})
            assert response.status == expected, f"{req.get_method()} {path}: {response.status} {str(value)[:500]}"
            return value
    with tempfile.TemporaryDirectory(prefix="director-workflow-http-", dir="/private/tmp") as directory:
        private = Path(directory)
        write_json(private / "isolation.json", {"isolated": True})
        environment = {key: os.environ[key] for key in ("PATH", "LANG", "LC_ALL", "SYSTEMROOT") if key in os.environ}
        environment.update(HOME=directory, TMPDIR=directory, PYTHONDONTWRITEBYTECODE="1",
                           ARK_API_KEY="", DEEPSEEK_API_KEY="", ASSISTANT_API_KEY="", ASSISTANT_PROVIDER="none", APP_BUDGET_CNY="20")
        log = (private / "server.log").open("ab")
        def start():
            command = [sys.executable, __file__, "--serve", directory, "--port", str(port)]
            if film_mode:
                command.append("--film")
            if legacy_reservation:
                command.append("--legacy-budget-reservation")
            if shared_reference:
                command.append("--shared-reference")
            if spec_combinations:
                command.append("--spec-combinations")
            if aihubmix:
                command.append("--aihubmix")
            process = subprocess.Popen(command,
                                       env=environment, stdout=log, stderr=subprocess.STDOUT)
            try:
                for _ in range(150):
                    if process.poll() is not None:
                        raise RuntimeError("Isolated HTTP process exited: " + (private / "server.log").read_text()[-1200:])
                    try:
                        request("/api/health", track=False)
                        return process
                    except (OSError, ValueError):
                        time.sleep(.1)
                raise RuntimeError("Isolated HTTP readiness timeout")
            except Exception:
                stop(process)
                raise
        def stop(process):
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        process = None
        try:
            process = start()
            initial_legacy_task = request("/__check__/metrics")["legacy_task"] if legacy_reservation else None
            shared_asset = request("/__check__/metrics")["shared_reference"] if shared_reference else None
            project = request("/api/projects", {"name": "HTTP fixed-media director proof", "brief": {
                "subject": "one cup", "quantity": 1, "style": "simple", "target_duration_s": 10}})["project_id"]
            prefix = f"/api/projects/{project}/director"
            bodies, replies, confirmations = {}, {}, []
            session = None
            def talk(stage, text, selected=None):
                state = request(prefix + "/conversation/state")
                body = {"message": f"[{stage}] {text}", "client_request_id": stage.lower(),
                        "selected_node_ids": selected or [], "expected_workflow_revision": state["workflow_revision"]}
                if session:
                    body["session_id"] = session
                bodies[stage] = body
                replies[stage] = request(prefix + "/conversation", body)
                return replies[stage]
            def one_action(reply, kind):
                actions = [action for action in reply["actions"] if action["kind"] == kind]
                assert len(actions) == 1, str(reply)[:1500]
                return actions[0]
            def confirm(action, user_action):
                body = {"client_request_id": "confirm-" + action["action_id"], "user_action": user_action}
                path = prefix + "/actions/" + action["action_id"] + "/confirm"
                confirmations.append((path, body))
                return request(path, body)
            def completed(action):
                for _ in range(150):
                    current = request(prefix + "/actions/" + action["action_id"])
                    if current["status"] == "completed":
                        return current
                    assert current["status"] in ("running", "proposed"), str(current)[:1500]
                    time.sleep(.1)
                raise AssertionError("Fixed-media job timed out")

            clarify = talk("CLARIFY", "我想拍一只水杯")
            assert clarify["outcome"]["status"] == "clarify" and not clarify["artifacts"]
            session = clarify["session_id"]
            script = talk("SCRIPT", "横屏，两镜，先杯身再杯口，请写脚本")
            assert script["outcome"]["status"] == "saved" and script["outcome"]["node_ids"] == ["script"]
            script_action = one_action(script, "confirm_script")
            assert request("/__check__/metrics")["task_count"] == int(legacy_reservation)
            assert confirm(script_action, "confirm_script")["status"] == "completed"
            boards = talk("STORYBOARDS", "按已确认脚本准备两张分镜", ["script"])
            board_action = one_action(boards, "generation")
            image_ids = [item["node_id"] for item in boards["artifacts"]]
            if shared_reference:
                assert all(item["config"]["input_asset_id"] == shared_asset["id"]
                           and item["config"]["generation_mode"] == "image_to_image" for item in boards["artifacts"])
            assert len(image_ids) == 2 and request("/__check__/metrics")["task_count"] == int(legacy_reservation)
            assert len(confirm(board_action, "confirm_generate")["tasks"]) == 2
            images_done = completed(board_action)
            assert all(item["asset"]["kind"] == "image" for item in images_done["executions"])
            videos = talk("VIDEOS", "明确按 BLUE、RED 顺序将两张图生成视频并全部拼接为成片，作为一次确认范围" if film_mode
                          else "把这两张图分别做成视频", image_ids)
            video_action = one_action(videos, "generation")
            video_ids = [item["node_id"] for item in videos["artifacts"]]
            assert video_action["output_mode"] == ("film" if film_mode else "clips")
            if film_mode:
                assert [item["config"]["title"] for item in videos["artifacts"]] == ["BLUE clip", "RED clip"]
                assert video_action["node_ids"] == video_ids, "Film order must follow the explicitly ordered sources"
                assert video_action["auto_film"] is True
                assert video_action["scope"] == {"generate": True, "compose": True,
                    "ordered_node_ids": video_ids, "no_trim": True, "composition_cost_cny": 0}
            prepared_metrics = request("/__check__/metrics")
            assert len(video_ids) == 2 and prepared_metrics["task_count"] == 2 + int(legacy_reservation)
            assert prepared_metrics["composition_count"] == 0, "Preparing video scope must not queue composition"
            assert len(confirm(video_action, "confirm_generate")["tasks"]) == 2
            videos_done = completed(video_action)
            assert all(item["asset"]["kind"] == "video" for item in videos_done["executions"])
            if film_mode:
                film = videos_done["composition"]
                assert film["status"] == "completed" and film["asset"]["kind"] == "video"
                assert [source["node_id"] for source in film["sources"]] == video_ids
                assert len(confirmations) == 3, "Only script, images and video+film receive user confirmations"
                assert not any(body["user_action"] == "confirm_compose" for _, body in confirmations)
            else:
                composition = talk("COMPOSE", "明确按 BLUE 视频、RED 视频这个顺序合成", list(reversed(video_ids)))
                composition_action = one_action(composition, "composition")
                assert request("/__check__/metrics")["composition_count"] == 0
                confirm(composition_action, "confirm_compose")
                film = completed(composition_action)
            assert film["actual_duration_ms"] == 1000
            result_bytes = request("/api/assets/" + film["asset_id"] + "/file", binary=True)
            result_file = evidence / "fixed-blue-then-red.mp4"
            result_file.write_bytes(result_bytes)
            import imageio_ffmpeg
            pixels = []
            for second in (.1, .8):
                frame = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-ss", str(second), "-i",
                                        str(result_file), "-frames:v", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
                                       capture_output=True, check=True, timeout=20).stdout
                pixels.append(list(frame[:3]))
            assert pixels[0][2] > 200 and pixels[0][0] < 30 and pixels[1][0] > 200 and pixels[1][2] < 30
            before = request("/__check__/metrics")
            assert (before["task_count"], before["node_count"], before["script_count"], before["composition_count"]) == (4 + int(legacy_reservation), 6, 1, 1)
            assert (before["llm_fixture_calls"], before["media_fixture_calls"]) == (6 if film_mode else 8, 4)
            stop(process)
            process = None
            process = start()
            after_start = request("/__check__/metrics")
            assert after_start["pid"] != before["pid"]
            for stage, reply in replies.items():
                receipt = request(prefix + "/conversation/requests/" + stage.lower())
                assert receipt["status"] == "completed" and receipt["response"]["request_id"] == reply["request_id"]
            for path, body in confirmations:
                assert request(path, body)["idempotent_replay"] is True
            replay = request(prefix + "/conversation", bodies["STORYBOARDS"])
            assert replay["idempotent_replay"] and replay["outcome"]["node_ids"] == image_ids
            if film_mode:
                video_replay = request(prefix + "/conversation", bodies["VIDEOS"])
                assert video_replay["idempotent_replay"] and video_replay["outcome"]["node_ids"] == video_ids
                action_after_restart = request(prefix + "/actions/" + video_action["action_id"])
                assert action_after_restart["status"] == "completed"
                assert action_after_restart["composition"]["asset_id"] == film["asset_id"]
            request(prefix + "/conversation/requests/never-submitted", expected=404)
            selection = urllib.parse.urlencode([("selected_node_id", item) for item in reversed(video_ids)])
            state = request(prefix + "/conversation/state?" + selection)
            assert state["current_role"] == "editor"
            assert state["creation"]["selected_node_ids"] == list(reversed(video_ids))
            canvas = request(f"/api/projects/{project}/canvas")
            assert any(node["node_id"] == film["result_node_id"] and node["asset"]["id"] == film["asset_id"] for node in canvas["nodes"])
            final = request("/__check__/metrics")
            if shared_reference:
                assert before["shared_reference"] == final["shared_reference"] == shared_asset
                image_events = [item for item in final["events"] if item.get("media_kind") == "image"]
                assert len(image_events) == 2
                assert all(item["reference_asset_id"] == shared_asset["id"]
                           and item["reference_sha256"] == shared_asset["sha256"] for item in image_events)
            if legacy_reservation:
                assert before["legacy_task"] == final["legacy_task"] == initial_legacy_task
            for key in ("task_count", "node_count", "script_count", "composition_count", "llm_fixture_calls", "media_fixture_calls"):
                assert final[key] == before[key], f"Restart/replay changed {key}"
            record.update(passed=True, project_id=project, before_restart=before, after_restart=final,
                          final_action=film, sampled_rgb=pixels, final_file=str(result_file), actual_duration_ms=1000,
                          video_action=videos_done, user_confirmation_count=len(confirmations),
                          separate_composition_confirmation=not film_mode,
                          real_model_calls=0, restart_replayed_without_new_work=True)
        except Exception as exc:
            record["failure"] = {"type": type(exc).__name__, "message": str(exc)}
            raise
        finally:
            if process is not None:
                stop(process)
            log.close()
            record["server_log_tail"] = (private / "server.log").read_text(errors="replace")[-6000:]
            record["finished_at"] = datetime.now(timezone.utc).isoformat()
            write_json(evidence / "director-workflow-http.json", record)
            print(json.dumps({"passed": record["passed"], "evidence": str(evidence / "director-workflow-http.json"),
                              "real_model_calls": 0}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir")
    parser.add_argument("--film", action="store_true", help="Check one video+ordered-film confirmation instead of separate composition")
    parser.add_argument("--legacy-budget-reservation", action="store_true", help="Include the historical unknown-video budget regression")
    parser.add_argument("--shared-reference", action="store_true", help="Use one product reference for both image nodes and verify worker inputs")
    parser.add_argument("--spec-combinations", action="store_true", help="Validate mode/size/ratio/duration/audio combinations using isolated hypothetical rules")
    parser.add_argument("--aihubmix", action="store_true", help="Exercise native AiHubMix adapter with blocked network and fixed media, not real generation")
    parser.add_argument("--serve", help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, default=0, help=argparse.SUPPRESS)
    arguments = parser.parse_args()
    if arguments.serve:
        serve(arguments.serve, arguments.port, arguments.film, arguments.legacy_budget_reservation, arguments.shared_reference, arguments.spec_combinations, arguments.aihubmix)
    else:
        run(arguments.evidence_dir, arguments.film, arguments.legacy_budget_reservation, arguments.shared_reference, arguments.spec_combinations, arguments.aihubmix)
