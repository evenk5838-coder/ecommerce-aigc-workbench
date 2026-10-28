"""Isolated browser QA: real FastAPI persistence, explicitly fixed LLM responses.

Run with /private/tmp/ecom-v011-backend-20261027/bin/python. The only writable
application data is a fresh directory below /private/tmp/ecom-v011-frontend-preview.
Default mode forbids all media tasks. --node-fixtures opts into the real queue
and worker with fixed PNG/MP4 runners only. No real model transport is allowed;
fixture outputs and test-only budget are not model acceptance or actual billing.
--workflow-fixtures adds new conversation tools and real local composition in
its own fresh data directory; its default build is BASE/workflow-dist.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import hashlib
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import re
from unittest.mock import patch
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
BASE = Path("/private/tmp/ecom-v011-frontend-preview")
LABEL = "隔离前端验收：真实接口与持久化，固定 LLM 替身，零真实模型调用"
NODE_LABEL = "隔离节点验收：真实队列与 worker，固定 PNG/MP4 替身，非模型产物／真实账单"
WORKFLOW_LABEL = "隔离对话工作流验收：真实工具/队列/合成，固定文本及 PNG/MP4 替身，零真实模型调用"


def checked_directory(value: str) -> Path:
    directory = Path(value).resolve()
    if not directory.is_relative_to(BASE.resolve()) or directory == BASE.resolve():
        raise ValueError("Preview data must be a child directory of " + str(BASE))
    return directory


def prepare_mode(directory: Path, *, node_fixtures: bool, workflow_fixtures: bool) -> None:
    """Mode changes require a fresh database, including enabling workflow fixtures."""
    mode_marker = directory / "preview-mode.json"
    requested = (node_fixtures, workflow_fixtures)
    if mode_marker.exists():
        prior = json.loads(mode_marker.read_text(encoding="utf-8"))
        prior_mode = (bool(prior.get("node_fixtures")), bool(prior.get("workflow_fixtures")))
    else:
        prior_mode = (False, False) if (directory / "preview.db").exists() else requested
    if prior_mode != requested:
        raise ValueError("Preview modes cannot share a database; omit --data-dir for a fresh isolated run")
    mode_marker.write_text(json.dumps({"node_fixtures": node_fixtures, "workflow_fixtures": workflow_fixtures,
        "label": WORKFLOW_LABEL if workflow_fixtures else NODE_LABEL if node_fixtures else LABEL}), encoding="utf-8")


def workflow_fixture_reply(message: str, messages: list[dict]) -> tuple[str, list[tuple[str, dict]]]:
    """Deterministic QA planner over actual supplied context; no endpoint shortcut.

    The model substitute only proposes normal tool calls. All persistence,
    quoting, user confirmation, task execution and composition remain real.
    """
    context = next((m.get("content") for m in messages if m.get("role") == "context"), {})
    context = context if isinstance(context, dict) else {}
    creation, project = context.get("creation") or {}, context.get("project") or {}
    nodes = creation.get("nodes") or []
    selected_ids = creation.get("selected_node_ids") or []
    selected = [node for node_id in selected_ids for node in nodes if node.get("node_id") == node_id]
    focus = selected if selected_ids else nodes
    script = creation.get("script") or {}

    def clarify(text, fields):
        return "", [("respond_to_user", {"kind": "clarify", "message": text, "missing_fields": fields})]

    def usable(node, kind):
        asset = node.get("asset") or {}
        if asset.get("kind") != kind or not asset.get("id"):
            return False
        if node.get("type") == "asset":
            return (node.get("binding") or {}).get("asset_id") == asset["id"]
        run = node.get("last_execution") or {}
        return run.get("status") == "succeeded" and run.get("asset_available") is True and run.get("asset_id") == asset["id"]

    if "[失败测试]" in message:
        return "", [("write_script", {"summary": ""})]
    if "[普通回复测试]" in message:
        return "稍等，我这就把作品准备好。", []
    if "[澄清测试]" in message:
        return clarify("这次希望突出商品外观，还是展示使用场景？请补充想要的风格。", ["创作方向"])
    if "重新准备" in message and "生成" in message and selected_ids:
        generation_nodes = [node for node in selected if node.get("type") in ("image", "video")]
        if len(generation_nodes) != len(selected_ids):
            return clarify("请选中本次需要重新准备方案的图片或视频节点。", ["本次生成节点"])
        return "", [("prepare_generation", {"node_ids": [node["node_id"] for node in generation_nodes],
            "expected_revisions": {node["node_id"]: node["config_revision"] for node in generation_nodes},
            "output_mode": "film" if "成片" in message else "clips"})]
    if "第二镜" in message or "第2镜" in message or "第二个镜头" in message:
        candidates = [n for n in focus if n.get("type") in ("text", "image", "video")]
        if len(candidates) == 1 and selected_ids:
            target = candidates[0]
        else:
            second_shots = {s.get("shot_id") for s in project.get("shots", []) if s.get("seq") == 2}
            candidates = [n for n in candidates if (n.get("binding") or {}).get("shot_id") in second_shots
                          or re.search(r"(?:分镜|镜头视频|镜头)\s*2\b", str((n.get("config") or {}).get("title", "")))]
            preferred = "video" if "视频" in message else "image"
            candidates = [n for n in candidates if n.get("type") == preferred]
            target = candidates[0] if len(candidates) == 1 else None
        if not target:
            return clarify("请在画布中选中要修改的第二个镜头，我会只调整这个节点。", ["要修改的镜头"])
        field = "text" if target["type"] == "text" else "prompt"
        return "", [("update_node", {"node_id": target["node_id"], "expected_config_revision": target["config_revision"],
                       "config": {field: message.replace("[慢速测试]", "").strip()}})]
    videos = [node for node in focus if usable(node, "video")]
    if videos and any(word in message for word in ("合成", "拼接", "成片")):
        # A message must expressly state order; neither selection order nor x/y
        # positions alone count as the user's ordered composition instruction.
        order = re.search(r"([12一二])\s*(?:号|个|条|段|镜)?\s*(?:再|然后|之后|后|到|→|->|、|,|，|/|和)\s*([12一二])", message)
        if not order:
            return clarify("请告诉我已有视频的顺序，例如先 1 后 2，或先 2 再 1。", ["视频顺序"])
        indexes = [{"1": 0, "一": 0, "2": 1, "二": 1}[part] for part in order.groups()]
        if len(videos) != 2 or len(set(indexes)) != 2:
            return clarify("请选中要合成的两条视频，并明确 1、2 的顺序。", ["两条视频和顺序"])
        numbered = {}
        for node in videos:
            label = re.search(r"(?:视频|片段|镜头)\s*([12一二])", str((node.get("config") or {}).get("title", "")))
            if label:
                numbered[{"1": 0, "一": 0, "2": 1, "二": 1}[label.group(1)]] = node
        if set(numbered) != {0, 1}:
            return clarify("请给两条视频标明 1 和 2，再告诉我顺序，以免混淆片段。", ["视频编号"])
        sources = []
        for index in indexes:
            node = numbered[index]
            item = {"asset_id": node["asset"]["id"], "node_id": node["node_id"]}
            if node.get("type") == "video":
                item["execution_id"] = node["last_execution"]["execution_id"]
            sources.append(item)
        return "", [("prepare_composition", {"sources": sources, "title": "晨光水杯 · 完整成片"})]
    if any(word in message for word in ("视频", "成片")) and (script or selected_ids or any(usable(n, "image") for n in focus) or len(message.strip()) < 8):
        images = [node for node in focus if usable(node, "image")]
        if not images:
            return clarify("请先选中已生成的分镜图，或导入想用的图片，再准备视频。", ["可用的来源图片"])
        sources = []
        for index, node in enumerate(images, 1):
            item = {"asset_id": node["asset"]["id"], "node_id": node["node_id"], "expected_config_revision": node["config_revision"],
                    "prompt": f"镜头 {index}：保持同一只水杯，晨光自然，镜头缓慢推进。创作要求：" + message[:200],
                    "title": f"镜头视频 {index}"}
            if node.get("type") == "image":
                item["execution_id"] = node["last_execution"]["execution_id"]
            sources.append(item)
        return "", [("prepare_video_nodes", {"model_id": "doubao-seedance-1-0-pro-fast-251015", "params": {"duration": 5, "ratio": "16:9", "resolution": "720p"},
                       "sources": sources, "output_mode": "film" if "成片" in message else "clips"})]
    if ("分镜" in message or "镜头方案" in message) and (script or selected_ids or len(message.strip()) < 8):
        texts = [n for n in selected if n.get("type") == "text"]
        source = texts[0] if len(texts) == 1 else next((n for n in nodes if n.get("type") == "script" and n.get("node_id") == "script"), None)
        if not source:
            return clarify("请先写好脚本，或选中一段已有文字，再准备分镜。", ["脚本或文字内容"])
        if source["type"] == "script" and not script.get("confirmed"):
            return "", [("prepare_script_confirmation", {"script_id": script["script_id"], "expected_version": script["version"]})]
        return "", [("prepare_storyboards", {"source_node_id": source["node_id"], "expected_source_revision": source["config_revision"],
            "model_id": "doubao-seedream-5-0-flash-260915", "params": {"size": "1024x1024", "ratio": "1:1"}, "shots": [
                {"prompt": "一只水杯置于干净桌面，窗边自然晨光，展示杯身全景，保持原有外观。", "title": "分镜 1", "duration_s": 5, "motion": "缓慢推进"},
                {"prompt": "同一只水杯的杯口和手持细节，自然晨光，保持商品数量和外观。", "title": "分镜 2", "duration_s": 5, "motion": "轻微侧移"}]})]
    if len(message.replace("[慢速测试]", "").strip()) < 8:
        return clarify("想为哪件商品拍摄？可以告诉我风格和想表达的画面。", ["商品和创作方向"])
    content = {"summary": "《晨光里的日常》\n创作要求：" + message.replace("[慢速测试]", "").strip()[:300]
               + "\n开场展示水杯全景，随后展现杯口与手持细节，最后停留在安静桌面。保持商品外观，不添加未经确认的卖点。",
               "shots": [{"seq": 1, "hint": "窗边晨光中的水杯全景，简洁桌面", "duration_s": 5, "motion": "缓慢推近"},
                         {"seq": 2, "hint": "同一只水杯的杯口与手持细节", "duration_s": 5, "motion": "轻微侧移"}]}
    if script.get("version"):
        content["base_version"] = script["version"]
    return "", [("write_script", content)]


def serve(directory: Path, port: int, *, node_fixtures: bool = False, workflow_fixtures: bool = False) -> None:
    node_fixtures = node_fixtures or workflow_fixtures
    directory = checked_directory(str(directory))
    directory.mkdir(parents=True, exist_ok=True)
    prepare_mode(directory, node_fixtures=node_fixtures, workflow_fixtures=workflow_fixtures)
    for key in ("ARK_API_KEY", "DEEPSEEK_API_KEY", "ASSISTANT_API_KEY"):
        os.environ[key] = ""
    os.environ["APP_BUDGET_CNY"] = "20" if node_fixtures else "0"
    os.environ["ASSISTANT_PROVIDER"] = "none"
    sys.path.insert(0, str(ROOT / "backend"))
    directory.mkdir(parents=True, exist_ok=True)
    # config normally reads .env and mkdirs the business data locations at import.
    # Suppress only those import side effects before assigning isolated locations.
    # Never read the actual .env or install a permissive production credential.
    original_exists, original_mkdir = Path.exists, Path.mkdir
    business_dirs = {ROOT / "data", ROOT / "data/assets", ROOT / "data/models"}

    def exists(path):
        return False if path == ROOT / ".env" else original_exists(path)

    def mkdir(path, *args, **kwargs):
        if path in business_dirs:
            return None
        return original_mkdir(path, *args, **kwargs)

    with patch.object(Path, "exists", exists), patch.object(Path, "mkdir", mkdir):
        from app import config
    config.DATA, config.DB_PATH = directory, directory / "preview.db"
    config.ASSETS, config.MODELS = directory / "assets", directory / "models"
    config.BUDGET_CNY = 20 if node_fixtures else 0
    config._load_dotenv = lambda: None
    config.DIRECTOR_MAX_COST_PER_CALL_CNY = 0
    config.DIRECTOR_MAX_SESSION_COST_CNY = 0
    for folder in (config.ASSETS, config.MODELS):
        folder.mkdir(exist_ok=True)
    for model_id, meta in config.MODEL_REGISTRY.items():
        if "dir" in meta:
            meta["dir"] = str(config.MODELS / model_id)

    original_connect = sqlite3.connect

    def isolated_connect(database, *args, **kwargs):
        if Path(database).resolve() != config.DB_PATH.resolve():
            raise AssertionError("Only the isolated preview database may be accessed")
        return original_connect(database, *args, **kwargs)

    sqlite3.connect = isolated_connect
    original_socket_connect = socket.socket.connect

    def no_outgoing_network(connection, address):
        if connection.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError("Outgoing network is forbidden in isolated preview")
        return original_socket_connect(connection, address)

    socket.socket.connect = no_outgoing_network
    from app import canvas, cloud_tasks, db, main, projects, providers_ark, storage, templates, worker
    from app.errors import err
    from app.assistant import provider
    from PIL import Image
    from fastapi.staticfiles import StaticFiles

    # The live service serves ROOT/frontend/dist dynamically. QA must have its own
    # build directory so rebuilding this preview cannot change that live UI.
    preview_dist = checked_directory(os.environ.get("V011_PREVIEW_DIST", str(BASE / ("workflow-dist" if workflow_fixtures else "dist"))))
    if not (preview_dist / "index.html").is_file() or not (preview_dist / "assets").is_dir():
        raise RuntimeError("Build the isolated frontend output before starting preview: " + str(preview_dist))
    main.FRONTEND_DIST = preview_dist
    main.app.router.routes = [route for route in main.app.router.routes if getattr(route, "path", None) != "/assets"]
    main.app.mount("/assets", StaticFiles(directory=str(preview_dist / "assets")), name="frontend-preview-assets")

    def forbidden(*args, **kwargs):
        raise AssertionError("Real model transport is forbidden in isolated preview")

    provider._deepseek_post = forbidden
    providers_ark._request = forbidden
    providers_ark._load_dotenv = lambda: None
    # Even an unexpected legacy/local task must never load a local model.
    worker.generate_image = forbidden
    worker.generate_video = forbidden

    class PreviewProvider(provider.FixedReplyProvider):
        """Prompt-sensitive fixed fixtures; never a real language model."""

        name = "fixed"

        def __init__(self):
            super().__init__(model="fixed-frontend-preview")

        def chat(self, messages, tools, tool_results=None, *, max_tokens=None):
            message = next((str(m.get("content", "")) for m in reversed(messages)
                            if m.get("role") == "user"), "")
            workflow_tools = workflow_fixtures and any(tool.name == "write_script" for tool in tools)
            plan = workflow_fixture_reply(message, messages) if workflow_tools else None
            actual_context = next((m.get("content") for m in messages if m.get("role") == "context"), {}) or {}
            with (directory / "provider-calls.jsonl").open("a", encoding="utf-8") as log:
                log.write(json.dumps({"at": time.time(), "provider": self.model,
                                      "scenario": "failure" if "[失败测试]" in message else "fixture",
                                      "workflow_fixtures": workflow_tools,
                                      "project_id": (actual_context.get("project") or {}).get("project_id") if isinstance(actual_context, dict) else None,
                                      "message_sha256": hashlib.sha256(message.encode()).hexdigest(),
                                      "tool_names": [name for name, _ in plan[1]] if plan else [],
                                      "real_model": False}, ensure_ascii=False) + "\n")
            if "[慢速测试]" in message:
                time.sleep(8)
            if plan is not None:
                reply, proposed = plan
                calls = [provider.ToolCall(name=name, arguments=arguments) for name, arguments in proposed]
            elif "[失败测试]" in message:
                # The real tool validator fails twice and the real outcome builder
                # returns failed. No fake endpoint response bypasses that path.
                calls = [provider.ToolCall(name="propose_script", arguments={"summary": ""})]
                reply = ""
            elif "[澄清测试]" in message or len(message.strip()) < 8:
                calls = []
                reply = "这次希望突出商品外观，还是展示使用场景？可以补充风格和预计时长。"
            elif "[普通回复测试]" in message:
                calls, reply = [], "可以继续补充创作要求，我会保留已有内容。"
            elif "[镜头方案测试]" in message:
                calls = [provider.ToolCall(name="propose_shot_plan", arguments={"shots": [
                    {"seq": 1, "description": "晨光落在水杯边缘，展示杯身轮廓", "duration_s": 5,
                     "motion": "缓慢推近"},
                    {"seq": 2, "description": "杯口细节与桌面光影，保持同一只水杯", "duration_s": 5,
                     "motion": "轻微侧移"},
                ]})]
                reply = ""
            else:
                request = message.replace("[慢速测试]", "").strip()
                calls = [provider.ToolCall(name="propose_script", arguments={
                    "summary": "《晨光里的日常》\n\n创作要求：" + request[:300] +
                    "\n\n第一幕｜清晨，柔和的窗边光落在桌面上。镜头从杯身轮廓缓慢推进，保持商品原有外观。"
                    "\n\n第二幕｜切至杯口与手持细节，让画面更贴近日常使用。沿用同一只水杯，不增添未经确认的商品卖点。"
                    "\n\n结尾｜画面停留在安静的桌面与水杯，保留简洁、自然的生活感。",
                    "shots": [
                        {"seq": 1, "hint": "窗边晨光中的水杯全景，简洁桌面，保留原有外观", "duration_s": 5,
                         "motion": "缓慢推近"},
                        {"seq": 2, "hint": "同一只水杯的杯口与手持细节，自然生活感", "duration_s": 5,
                         "motion": "轻微侧移"},
                    ]})]
                reply = ""
            return provider.ChatResult(reply=reply, tool_calls=calls,
                                       meta={"provider": "fixed", "model": self.model, "stub": True})

    provider.set_provider(PreviewProvider())
    db.init_db()
    with db.transaction() as connection:
        if node_fixtures:
            connection.execute("DROP TRIGGER IF EXISTS preview_forbid_tasks")
            cloud_ids = ",".join("'" + model_id.replace("'", "''") + "'"
                                 for model_id, meta in config.MODEL_REGISTRY.items()
                                 if meta.get("provider") == "ark")
            connection.execute("DROP TRIGGER IF EXISTS preview_cloud_fixtures_only")
            # Normal queued rows receive their provider field only on execution;
            # route through the same model registry that worker.execute uses.
            connection.execute("CREATE TRIGGER preview_cloud_fixtures_only BEFORE INSERT ON tasks "
                               "WHEN COALESCE(NEW.model_id, '') NOT IN (" + cloud_ids + ") "
                               "BEGIN SELECT RAISE(ABORT, 'Only cloud-shaped fixture tasks are allowed'); END")
        else:
            if connection.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]:
                raise ValueError("Default preview requires a zero-task database")
            connection.execute("CREATE TRIGGER IF NOT EXISTS preview_forbid_tasks BEFORE INSERT ON tasks "
                               "BEGIN SELECT RAISE(ABORT, 'Media tasks are disabled in isolated frontend QA'); END")
    marker = directory / "fixtures.json"
    if not marker.exists():
        # These are synthetic decode fixtures, not AI-generated assets or user media.
        fixtures = directory / "fixtures"
        fixtures.mkdir(exist_ok=True)
        image_file, video_file = fixtures / "fixture-blue.png", fixtures / "fixture-blue.mp4"
        stream = io.BytesIO()
        Image.new("RGB", (320, 240), (65, 95, 165)).save(stream, format="PNG")
        image_file.write_bytes(stream.getvalue())
        subprocess.run([storage.ffmpeg_exe(), "-nostdin", "-v", "error", "-f", "lavfi", "-i",
                        "color=c=0x415fa5:s=320x240:r=10", "-t", "0.8", "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", str(video_file)],
                       check=True, capture_output=True, timeout=30)
        image_asset = storage.save_upload_bytes(image_file.read_bytes(), image_file.name)
        video_asset = storage.save_upload_bytes(video_file.read_bytes(), video_file.name)
        project = projects.create_project("晨光水杯 · 前端验收", {
            "subject": "水杯", "quantity": 1, "style": "自然晨光、简洁生活感", "target_duration_s": 10,
            "notes": "隔离前端验收项目；仅使用固定替身，不包含真实模型质量结论。",
        })
        empty = projects.create_project("空白画布 · 前端验收")
        seed_nodes = [
            {"node_id": "note-preview", "type": "text", "position": {"x": 340, "y": 70},
             "config": {"title": "创作笔记", "text": "晨光、干净桌面、自然生活感。\n每个镜头都保持同一只水杯。"}},
            {"node_id": "image-preview", "type": "image", "position": {"x": 700, "y": 70},
             "config": {"title": "画面灵感", "generation_mode": "text_to_image", "prompt": "晨光中的水杯",
                        **({"model_id": "doubao-seedream-5-0-flash-260915",
                            "params": {"size": "1024x1024", "ratio": "1:1"}} if node_fixtures else {})}},
            {"node_id": "video-preview", "type": "video", "position": {"x": 1060, "y": 70},
             "config": {"title": "镜头动作", "generation_mode": "text_to_video", "prompt": "镜头缓慢推进水杯",
                        **({"model_id": "doubao-seedance-1-0-pro-fast-251015",
                            "params": {"duration": 5, "ratio": "16:9", "resolution": "720p"}}
                           if node_fixtures else {})}},
            {"node_id": "asset-preview-image", "type": "asset", "position": {"x": 340, "y": 450},
             "binding": {"asset_id": image_asset["id"]}},
            {"node_id": "asset-preview-video", "type": "asset", "position": {"x": 700, "y": 450},
             "binding": {"asset_id": video_asset["id"]}},
        ]
        imported_project_id = None
        if workflow_fixtures:
            # The main workspace begins with zero nodes, so writer→director QA
            # is not preempted by imported video content or future empty nodes.
            imported = projects.create_project("已有素材 · 对话验收")
            imported_project_id = imported["project_id"]
            imports = []
            for kind, asset in (("图片", image_asset), ("视频", video_asset)):
                for index in (1, 2):
                    imports.append({"node_id": f"import-{'image' if kind == '图片' else 'video'}-{index}", "type": "asset",
                        "position": {"x": 100 + (index - 1) * 420, "y": 80 if kind == "图片" else 420},
                        "binding": {"asset_id": asset["id"]}, "config": {"title": f"导入{kind} {index}"}})
            canvas.patch_canvas(imported_project_id, {"nodes": imports, "viewport": {"x": 40, "y": 40, "zoom": 0.7}})
        else:
            canvas.patch_canvas(project["project_id"], {"nodes": seed_nodes, "viewport": {"x": 40, "y": 40, "zoom": 0.7}})
        marker.write_text(json.dumps({"label": LABEL, "project_id": project["project_id"],
                                     "empty_project_id": empty["project_id"],
                                     "imported_project_id": imported_project_id,
                                     "image_asset_id": image_asset["id"], "video_asset_id": video_asset["id"],
                                     "image_file": str(image_file), "video_file": str(video_file)},
                                    ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = json.loads(marker.read_text(encoding="utf-8"))
    if node_fixtures:
        # Only replace the remote runners. Actual routes, quote/confirmation,
        # claim_next/execute, task attempts, asset storage and canvas views remain.
        fixture_paths = {
            "image": checked_directory(manifest["image_file"]),
            "video": checked_directory(manifest["video_file"]),
        }
        if any(not path.is_relative_to(directory) or not path.is_file() for path in fixture_paths.values()):
            raise ValueError("Fixture media must be existing files inside this isolated data directory")

        def media_runner(kind):
            def run_fixture(task_id, model_id, params, token, on_progress=None):
                started = time.monotonic()
                prompt = str(params.get("prompt") or "")
                failure = "[失败测试]" in prompt
                scenario = "failure" if failure else "fixed_" + kind
                event = {"at": time.time(), "task_id": task_id, "kind": kind,
                         "model_id": model_id, "scenario": scenario, "fixture": True,
                         "real_model": False, "label": NODE_LABEL}
                with (directory / "media-fixture-calls.jsonl").open("a", encoding="utf-8") as log:
                    log.write(json.dumps({**event, "phase": "started"}, ensure_ascii=False) + "\n")
                # Exercise real budget and reference validation before returning a
                # fixed decoded asset. This test-only budget cannot reach a provider.
                cloud_tasks.budget_gate(cloud_tasks.estimate_cost(kind, model_id, params))
                if kind == "video":
                    cloud_tasks.build_content(params)
                elif params.get("generation_mode") == "image_to_image":
                    storage.asset_data_url(storage.load_asset(params.get("input_asset_id")))
                duration = 8 if "[慢速测试]" in prompt else 0.6
                while time.monotonic() - started < duration:
                    token.raise_if_canceled()
                    if on_progress:
                        on_progress(min(0.8, 0.15 + 0.65 * (time.monotonic() - started) / duration))
                    time.sleep(0.1)
                token.raise_if_canceled()
                if failure:
                    with (directory / "media-fixture-calls.jsonl").open("a", encoding="utf-8") as log:
                        log.write(json.dumps({**event, "phase": "failed"}, ensure_ascii=False) + "\n")
                    raise err("PROVIDER_UNAVAILABLE", "固定素材替身失败测试：请调整提示词后重试。")
                data = fixture_paths[kind].read_bytes()
                with (directory / "media-fixture-calls.jsonl").open("a", encoding="utf-8") as log:
                    log.write(json.dumps({**event, "phase": "fixture_returned"}, ensure_ascii=False) + "\n")
                return {"data": data, "kind": kind, "cost_cny": 0,
                        "usage": {"fixture": True, "real_model": False, "real_charge": False,
                                  "label": NODE_LABEL, "fixed_media": fixture_paths[kind].name},
                        "load_ms": 0, "latency_ms": int((time.monotonic() - started) * 1000)}
            return run_fixture

        cloud_tasks.run_image = media_runner("image")
        cloud_tasks.run_video = media_runner("video")
        config.POLL_INTERVAL = 0.1
    manifest.update(url=f"http://127.0.0.1:{port}", pid=os.getpid(), directory=str(directory),
                    label=WORKFLOW_LABEL if workflow_fixtures else NODE_LABEL if node_fixtures else LABEL,
                    node_fixtures=node_fixtures, worker_enabled=node_fixtures,
                    workflow_fixtures=workflow_fixtures, composition_enabled=workflow_fixtures,
                    budget_cny=20 if node_fixtures else 0, budget_is_fixture_only=True,
                    media_outputs="fixed PNG 320x240 / MP4 320x240 0.8s" if node_fixtures else "disabled",
                    frontend_dist=str(main.FRONTEND_DIST), real_model_calls=0)
    (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    @contextlib.asynccontextmanager
    async def preview_lifespan(app):
        db.init_db()
        app.state.template_seed = templates.seed()
        app.state.recovery = worker.reconcile() if node_fixtures else {"interrupted": 0, "queued": 0}
        if workflow_fixtures:
            app.state.recovery["director_requests"] = main.director_recovery.recover_interrupted_requests()
        if node_fixtures:
            worker.start()
        try:
            if workflow_fixtures:
                main.director_composition.start()
            yield
        finally:
            if workflow_fixtures:
                main.director_composition.stop()
            if node_fixtures:
                for token in list(worker.TOKENS.values()):
                    token.request()
                worker.stop()

    main.app.router.lifespan_context = preview_lifespan

    @main.app.middleware("http")
    async def label_preview(request, call_next):
        response = await call_next(request)
        response.headers["X-Preview-Mode"] = ("isolated-workflow-fixtures-no-real-model" if workflow_fixtures else "isolated-node-media-fixtures-no-real-model" if node_fixtures
                                              else "isolated-fixed-provider-no-real-model")
        return response

    @main.app.get("/__preview__/status")
    def preview_status():
        return {**manifest, "task_count": db.query("SELECT COUNT(*) AS n FROM tasks")[0]["n"]}

    import uvicorn
    uvicorn.run(main.app, host="127.0.0.1", port=port, log_level="warning", access_log=False)


def run() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=int(os.environ.get("V011_PREVIEW_PORT", "0")))
    parser.add_argument("--data-dir", help="Reuse isolated preview data only in its original mode")
    parser.add_argument("--node-fixtures", action="store_true",
                        help="Opt into real queue/worker with fixed PNG/MP4 only; test budget 20, no real model")
    parser.add_argument("--workflow-fixtures", action="store_true",
                        help="Opt into new conversation tool fixtures plus real media worker/composition; requires its own fresh database")
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.workflow_fixtures:
        args.node_fixtures = True
    if not 0 <= args.port <= 65535:
        parser.error("Port must be between 0 and 65535")
    BASE.mkdir(parents=True, exist_ok=True)
    directory = checked_directory(args.data_dir) if args.data_dir else Path(tempfile.mkdtemp(prefix="run-", dir=BASE))
    if args.serve:
        if not args.port:
            parser.error("Internal server requires an allocated port")
        serve(directory, args.port, node_fixtures=args.node_fixtures, workflow_fixtures=args.workflow_fixtures)
        return
    port = args.port
    if not port:
        with socket.socket() as reservation:
            reservation.bind(("127.0.0.1", 0))
            port = reservation.getsockname()[1]
    # Do not inherit credentials. dotenv loading is suppressed inside the child.
    environment = {key: os.environ[key] for key in ("PATH", "TMPDIR", "LANG", "LC_ALL") if key in os.environ}
    environment.update(PYTHONUNBUFFERED="1", ARK_API_KEY="", DEEPSEEK_API_KEY="", ASSISTANT_API_KEY="",
                       ASSISTANT_PROVIDER="none", APP_BUDGET_CNY="20" if args.node_fixtures else "0", APP_PROVIDER="ark",
                       V011_PREVIEW_DIST=str(checked_directory(os.environ.get("V011_PREVIEW_DIST", str(BASE / ("workflow-dist" if args.workflow_fixtures else "dist"))))))
    child_args = [sys.executable, str(Path(__file__).resolve()), "--serve", "--port", str(port),
                  "--data-dir", str(directory)]
    if args.node_fixtures:
        child_args.append("--node-fixtures")
    if args.workflow_fixtures:
        child_args.append("--workflow-fixtures")
    process = subprocess.Popen(child_args, env=environment)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    stopped = False

    def stop(signum=None, frame=None):
        nonlocal stopped
        if stopped:
            return
        stopped = True
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        for _ in range(150):
            if process.poll() is not None:
                raise RuntimeError("Isolated preview failed to start; no existing service was stopped")
            try:
                with opener.open(f"http://127.0.0.1:{port}/__preview__/status", timeout=0.2) as response:
                    manifest = json.load(response)
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise RuntimeError("Isolated preview readiness timeout")
        (BASE / "latest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(manifest, ensure_ascii=False, indent=2), flush=True)
        print("Scenarios: meaningful script request; [失败测试]; [澄清测试]; [慢速测试]; [普通回复测试].", flush=True)
        if args.node_fixtures:
            print("NODE FIXTURES: real queue/worker, fixed PNG/MP4 output; NOT real AI or billing. "
                  "Media prompts support [失败测试] and [慢速测试].", flush=True)
        if args.workflow_fixtures:
            print("WORKFLOW FIXTURES: describe a cup → confirm script → ask for 分镜 → confirm images → "
                  "select available images and ask for 视频 or 成片. For imported videos, explicitly state 先1再2/先2再1. "
                  "[普通回复测试] and [失败测试] produce real failed outcomes; [慢速测试] delays the fixed LLM 8s.", flush=True)
        print("Ctrl-C stops only this preview. Its isolated data remains for refresh/restart checks.", flush=True)
        process.wait()
    finally:
        stop()


if __name__ == "__main__":
    run()
