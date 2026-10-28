"""Bounded, isolated real-provider acceptance server. Default: offline preflight.

This file never grants spending permission. --serve requires a separate approval
record made from a new explicit human authorization. Its ledger reserves before
every paid transport; failures and process restarts do not restore allowances.
No automatic scenario or retry is initiated by this script. On restart, the
product worker may resume already authorized tasks under the retained ledger.
"""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BASE = Path("/private/tmp/ecom-v011-real-acceptance")
FRONTEND = Path("/private/tmp/ecom-v011-frontend-preview/workflow-dist")
IMAGE = "doubao-seedream-5-0-flash-260915"
VIDEO = "doubao-seedance-1-0-pro-fast-251015"
CAPS = {"llm": 20, "text_to_image": 2, "image_to_image": 1,
        "image_to_video": 2, "text_to_video": 1}
MAX_TOTAL, MAX_LLM, MAX_MEDIA = Decimal("4"), Decimal("1"), Decimal("3")
IMAGE_RESERVE, VIDEO_RESERVE = Decimal("0.12"), Decimal("0.50")
SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,160}$")


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode()).hexdigest()


def money(value):
    if isinstance(value, bool):
        raise ValueError("Money must be a finite nonnegative number")
    try:
        number = Decimal(str(value))
    except Exception as exc:
        raise ValueError("Invalid money") from exc
    if not number.is_finite() or number < 0:
        raise ValueError("Money must be finite and nonnegative")
    return number


def approval_template():
    return {"schema_version": 1, "approved": False,
            "authorization_reference": "Fill only after a new human approval; do not infer from .env or prior batches",
            "run_id": "replace-with-unique-run-name", "approved_at": None,
            "total_cny": "4.00", "llm_cny": "1.00", "media_cny": "3.00",
            "max_calls": CAPS, "image_model": IMAGE, "video_model": VIDEO,
            "llm_model": "deepseek-flash", "image_size": "2688x1536",
            "video": {"duration": 5, "resolution": "720p", "ratio": "16:9"},
            "no_automatic_retry": True}


def validate_approval(value):
    expected = set(approval_template())
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError("Approval must contain exactly the documented fields")
    if value["schema_version"] != 1 or value["approved"] is not True:
        raise ValueError("A new explicit approval is required before real transport")
    if not isinstance(value["authorization_reference"], str) or not value["authorization_reference"].strip() \
            or value["authorization_reference"] == approval_template()["authorization_reference"]:
        raise ValueError("Record the actual human authorization reference")
    if not isinstance(value["run_id"], str) or not SAFE_ID.fullmatch(value["run_id"]) \
            or value["run_id"] == "replace-with-unique-run-name":
        raise ValueError("A unique run_id is required")
    if not isinstance(value["approved_at"], str):
        raise ValueError("approved_at must be the actual approval timestamp")
    stamp = datetime.fromisoformat(value["approved_at"].replace("Z", "+00:00"))
    if stamp.tzinfo is None:
        raise ValueError("Approval timestamp must include a timezone")
    for key, upper in (("total_cny", MAX_TOTAL), ("llm_cny", MAX_LLM), ("media_cny", MAX_MEDIA)):
        if not 0 < money(value[key]) <= upper:
            raise ValueError("Approval amount exceeds this harness scope: " + key)
    if not isinstance(value["max_calls"], dict) or set(value["max_calls"]) != set(CAPS):
        raise ValueError("Approval must specify every call category")
    for kind, cap in CAPS.items():
        count = value["max_calls"][kind]
        if type(count) is not int or not 0 <= count <= cap:
            raise ValueError("Invalid call count: " + kind)
    for key in ("image_model", "video_model", "llm_model", "image_size", "video", "no_automatic_retry"):
        if value[key] != approval_template()[key]:
            raise ValueError("Approval changes the fixed acceptance scope: " + key)
    return value


class Guard:
    """Persistent reservations, safe summaries and remote-ID ownership.

    Reservations remain charged against the allowance on HTTP errors or missing
    usage. Complete, valid usage settles a successful request; call counts never
    return to the allowance.
    Observed token cost above a reservation freezes further paid calls, including
    when the aggregate cap has not yet been reached. Values are application-side
    conservative estimates, not a guarantee about a supplier's final invoice.
    """

    def __init__(self, directory, approval):
        self.directory = Path(directory)
        self.approval = validate_approval(approval)
        self.approval_hash = digest(self.approval)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "provider-ledger.json"
        self.lock_path = self.directory / "provider-ledger.lock"
        with self._state() as state:
            if not state:
                state.update(schema_version=1, approval_sha256=self.approval_hash,
                             run_id=self.approval["run_id"], created_at=now(), calls=[], blocked=False)
            elif state.get("approval_sha256") != self.approval_hash:
                raise ValueError("An existing ledger cannot be reset with another approval")

    @contextlib.contextmanager
    def _state(self):
        with self.lock_path.open("a+") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            state = json.loads(self.path.read_text()) if self.path.exists() else {}
            if state and state.get("approval_sha256") != self.approval_hash:
                raise ValueError("Approval changed after ledger creation")
            yield state
            temporary = self.directory / (".ledger-" + uuid.uuid4().hex + ".tmp")
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(state, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            folder_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(folder_fd)
            finally:
                os.close(folder_fd)

    @staticmethod
    def totals(state):
        values = {"llm": Decimal(0), "media": Decimal(0)}
        for call in state["calls"]:
            bucket = "llm" if call["kind"] == "llm" else "media"
            values[bucket] += money(call["observed_cny"]) if call.get("settled") else \
                max(money(call["reserved_cny"]), money(call.get("observed_cny", 0)))
        return values

    def reserve(self, kind, cost, request_body):
        cost = money(cost)
        if kind not in CAPS or cost <= 0:
            raise ValueError("Unknown paid call or invalid reservation")
        with self._state() as state:
            if state["blocked"]:
                raise ValueError("Real acceptance stopped: inspect the retained ledger")
            if sum(call["kind"] == kind for call in state["calls"]) >= self.approval["max_calls"][kind]:
                raise ValueError("Call allowance exhausted: " + kind)
            totals = self.totals(state)
            bucket = "llm" if kind == "llm" else "media"
            if totals[bucket] + cost > money(self.approval[bucket + "_cny"]) \
                    or sum(totals.values()) + cost > money(self.approval["total_cny"]):
                raise ValueError("Reserved allowance exhausted; no paid request sent")
            record = {"id": uuid.uuid4().hex, "kind": kind, "reserved_cny": str(cost),
                      "request_sha256": digest(request_body), "at": now(), "status": "reserved"}
            state["calls"].append(record)
            return record["id"]

    def finish(self, call_id, *, response=None, error=None, observed=None, settle=False, invalid_usage=False):
        invalid_remote = False
        with self._state() as state:
            call = next(row for row in state["calls"] if row["id"] == call_id)
            call.update(status="error" if error else "returned", finished_at=now())
            if error:
                # Never include provider exception text, URLs, bodies or credentials.
                call["error_type"] = type(error).__name__
            if response:
                remote = response.get("id") if call["kind"] in ("image_to_video", "text_to_video") else None
                if remote is not None:
                    if not isinstance(remote, str) or not SAFE_ID.fullmatch(remote) \
                            or (call.get("remote_task_id") and call["remote_task_id"] != remote):
                        state["blocked"] = True
                        call["error_type"] = "InvalidRemoteId"
                        invalid_remote = True
                    else:
                        call["remote_task_id"] = remote
                usage = response.get("usage") or {}
                call["tokens"] = {key: usage[key] for key in (
                    "prompt_tokens", "completion_tokens", "total_tokens", "prompt_cache_hit_tokens",
                    "prompt_cache_miss_tokens") if type(usage.get(key)) is int and usage[key] >= 0}
                names = [tc.get("function", {}).get("name") for choice in response.get("choices", [])
                         for tc in choice.get("message", {}).get("tool_calls", [])]
                call["tool_names"] = [name for name in names if isinstance(name, str) and SAFE_ID.fullmatch(name)]
            if observed is not None:
                call["observed_cny"] = str(money(observed))
                call["settled"] = bool(settle)
                if money(observed) > money(call["reserved_cny"]):
                    state["blocked"] = True
                    state["stop_reason"] = "Observed token estimate exceeded its reservation; inspect before any further call"
            if invalid_usage:
                state["blocked"] = True
                state["stop_reason"] = "Invalid provider usage; keep reservation and inspect before further calls"
        if invalid_remote:
            raise ValueError("Provider returned an invalid or changed task ID; stop and inspect ledger")

    def remote_owner(self, remote_id):
        if not SAFE_ID.fullmatch(remote_id):
            raise ValueError("Invalid task ID")
        with self._state() as state:
            rows = [row for row in state["calls"] if row.get("remote_task_id") == remote_id]
            if len(rows) != 1:
                raise ValueError("Only task IDs created in this approval ledger may be queried")
            return rows[0]["id"]

    @staticmethod
    def llm_cost(input_tokens, output_tokens):
        # Conservative allowance rates: CNY 2.16 / 8.64 per million tokens.
        # No cache discount; these are guard estimates, not provider bill rates.
        value = (Decimal(input_tokens) * Decimal("2.16") + Decimal(output_tokens) * Decimal("8.64")) / 1_000_000
        return value.quantize(Decimal("0.000001"), rounding=ROUND_CEILING)

    @staticmethod
    def checked_llm_usage(usage):
        """None means absent/incomplete. Invalid supplied counts are rejected."""
        if not isinstance(usage, dict):
            raise ValueError("Invalid token usage")
        keys = ("prompt_tokens", "completion_tokens", "total_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens")
        for key in keys:
            if key in usage and (type(usage[key]) is not int or not 0 <= usage[key] <= 100_000_000):
                raise ValueError("Invalid token count")
        if "prompt_tokens" not in usage or "completion_tokens" not in usage:
            return None
        prompt, completion = usage["prompt_tokens"], usage["completion_tokens"]
        if "total_tokens" in usage and usage["total_tokens"] != prompt + completion:
            raise ValueError("Token total mismatch")
        for key in ("prompt_cache_hit_tokens", "prompt_cache_miss_tokens"):
            if usage.get(key, 0) > prompt:
                raise ValueError("Cache count exceeds input count")
        if "prompt_cache_hit_tokens" in usage and "prompt_cache_miss_tokens" in usage \
                and usage["prompt_cache_hit_tokens"] + usage["prompt_cache_miss_tokens"] != prompt:
            raise ValueError("Cache token total mismatch")
        return prompt, completion

    def deepseek(self, send, url, body, api_key, timeout):
        if url not in ("https://api.deepseek.com/chat/completions", "https://api.deepseek.com/v1/chat/completions"):
            raise ValueError("Unexpected DeepSeek endpoint")
        if body.get("model") != "deepseek-flash" or body.get("thinking") != {"type": "disabled"}:
            raise ValueError("Only the approved non-thinking text model is allowed")
        if set(body) - {"model", "messages", "temperature", "thinking", "max_tokens", "tools"}:
            raise ValueError("Unapproved LLM options")
        max_tokens = body.get("max_tokens")
        if type(max_tokens) is not int or not 1 <= max_tokens <= 1600:
            raise ValueError("Every LLM call must have max_tokens <= 1600")
        if body.get("stream") or any(not isinstance(item.get("content"), (str, type(None))) for item in body.get("messages", [])):
            raise ValueError("Only nonstreaming text messages are approved")
        # Byte-count upper bound includes messages, schemas and serialization margin.
        estimated = self.llm_cost(len(json.dumps(body, ensure_ascii=False).encode()) + 1024, max_tokens)
        if estimated > Decimal("0.25"):
            raise ValueError("Conservative single-call reservation exceeds ¥0.25")
        call_id = self.reserve("llm", estimated, body)
        try:
            data = send(url, body, api_key, timeout)
        except Exception as exc:
            self.finish(call_id, error=exc)
            raise
        try:
            usage = self.checked_llm_usage(data.get("usage") or {})
        except ValueError:
            self.finish(call_id, response=data, invalid_usage=True)
            raise
        observed = self.llm_cost(*usage) if usage is not None else None
        self.finish(call_id, response=data, observed=observed, settle=usage is not None)
        return data

    def ark(self, send, method, path, body=None, timeout=None):
        if method == "GET" and path.startswith("/contents/generations/tasks/"):
            call_id = self.remote_owner(path.rsplit("/", 1)[1])
            data = send(method, path, body, timeout=timeout)
            completion = (data.get("usage") or {}).get("completion_tokens")
            success = data.get("status") in ("succeeded", "success")
            if completion is not None and (type(completion) is not int or not 0 <= completion <= 100_000_000):
                self.finish(call_id, invalid_usage=True)
                raise ValueError("Invalid video token usage")
            if completion is not None and success:
                observed = Decimal(completion) * Decimal("4.2") / 1_000_000
                self.finish(call_id, response=data, observed=observed, settle=True)
            return data
        if method != "POST" or not isinstance(body, dict):
            raise ValueError("Unapproved Ark endpoint; no request sent")
        if path == "/images/generations":
            if set(body) - {"model", "prompt", "size", "watermark", "response_format", "image"}:
                raise ValueError("Unapproved image options")
            if body.get("model") != IMAGE or body.get("size") != self.approval["image_size"] \
                    or body.get("response_format") != "b64_json" or body.get("watermark") is not False:
                raise ValueError("Image request is outside the approved model/2K scope")
            ref = body.get("image")
            if ref is not None and (not isinstance(ref, str) or not ref.startswith("data:image/")):
                raise ValueError("Only a local single reference image is approved")
            kind, cost = ("image_to_image" if ref else "text_to_image"), IMAGE_RESERVE
        elif path == "/contents/generations/tasks":
            if set(body) - {"model", "content", "duration", "ratio", "resolution", "generate_audio",
                            "watermark", "seed", "safety_identifier", "output_format"}:
                raise ValueError("Unapproved video options")
            if body.get("model") != VIDEO or any(body.get(key) != value for key, value in self.approval["video"].items()) \
                    or body.get("generate_audio") is not False or body.get("output_format") != "mp4" \
                    or body.get("watermark") is not False:
                raise ValueError("Video request is outside the approved 5s/720p/16:9 scope")
            items = body.get("content")
            if not isinstance(items, list) or not items or any(item.get("type") not in ("text", "image_url") for item in items):
                raise ValueError("Unsupported video input")
            refs = [item for item in items if item.get("type") == "image_url"]
            if len(refs) > 1 or any(item.get("role") != "first_frame" or not str(item.get("image_url", {}).get("url", "")).startswith("data:image/") for item in refs):
                raise ValueError("Only one local first-frame reference is approved")
            kind, cost = ("image_to_video" if refs else "text_to_video"), VIDEO_RESERVE
        else:
            raise ValueError("Unapproved Ark endpoint; no request sent")
        call_id = self.reserve(kind, cost, body)
        try:
            data = send(method, path, body, timeout=timeout)
        except Exception as exc:
            self.finish(call_id, error=exc)
            raise
        self.finish(call_id, response=data, observed=cost if kind.endswith("image") else None,
                    settle=kind.endswith("image"))
        return data


def serve(approval_path, port):
    approval = validate_approval(json.loads(Path(approval_path).read_text(encoding="utf-8")))
    directory = BASE / approval["run_id"]
    if directory.is_symlink() or BASE.is_symlink() or directory.resolve() != directory:
        raise ValueError("Isolation directory must not traverse symlinks")
    if not (FRONTEND / "index.html").is_file():
        raise ValueError("The independently built workflow frontend is required")
    if directory.exists() and not (directory / "provider-ledger.json").exists():
        raise ValueError("Refuse an existing directory without this harness ledger")
    guard = Guard(directory, approval)
    sys.path.insert(0, str(ROOT / "backend"))
    mkdir = Path.mkdir
    forbidden = {ROOT / "data", ROOT / "data/assets", ROOT / "data/models"}
    with patch.object(Path, "mkdir", lambda path, *args, **kwargs: None if path in forbidden else mkdir(path, *args, **kwargs)):
        from app import config
    config.DATA, config.DB_PATH = directory, directory / "acceptance.db"
    config.ASSETS, config.MODELS = directory / "assets", directory / "models"
    config.BUDGET_CNY = float(money(approval["media_cny"]))
    config.DIRECTOR_MAX_COST_PER_CALL_CNY = .15
    config.DIRECTOR_MAX_SESSION_COST_CNY = float(money(approval["llm_cny"]))
    config.DIRECTOR_MAX_OUTPUT_TOKENS = 1600
    config.CONCURRENCY = 1
    for folder in (config.ASSETS, config.MODELS):
        folder.mkdir(exist_ok=True)
    for model_id, meta in config.MODEL_REGISTRY.items():
        if "dir" in meta:
            meta["dir"] = str(config.MODELS / model_id)
    original_connect = sqlite3.connect
    def isolated_connect(database, *args, **kwargs):
        if Path(str(database)).resolve() != config.DB_PATH.resolve():
            raise ValueError("Only the approval's isolated database may be opened")
        return original_connect(database, *args, **kwargs)
    sqlite3.connect = isolated_connect
    from app import db, main, providers_ark, worker
    from app.assistant import provider
    if providers_ark.BASE_URL.rstrip("/") != "https://ark.cn-beijing.volces.com/api/v3":
        raise ValueError("Unexpected Ark base URL; do not send credentials")
    os.environ["ASSISTANT_PROVIDER"] = "deepseek"
    os.environ["ASSISTANT_MODEL"] = "deepseek-flash"
    provider.set_provider(None)
    transport = provider._deepseek_post
    provider._deepseek_post = lambda url, body, api_key, timeout: guard.deepseek(transport, url, body, api_key, timeout)
    ark_transport = providers_ark._request
    providers_ark._request = lambda method, path, body=None, timeout=None: guard.ark(ark_transport, method, path, body, timeout)
    def no_local(*args, **kwargs):
        raise ValueError("Local model inference is outside this acceptance")
    worker.generate_image = worker.generate_video = no_local
    db.init_db()
    with db.transaction() as connection:
        connection.execute("CREATE TRIGGER IF NOT EXISTS acceptance_models_only BEFORE INSERT ON tasks "
                           f"WHEN COALESCE(NEW.model_id, '') NOT IN ('{IMAGE}', '{VIDEO}') "
                           "BEGIN SELECT RAISE(ABORT, 'Unapproved acceptance model'); END")
    from fastapi.staticfiles import StaticFiles
    main.FRONTEND_DIST = FRONTEND
    main.app.routes[:] = [route for route in main.app.routes if getattr(route, "name", None) != "frontend-assets"]
    main.app.mount("/assets", StaticFiles(directory=str(FRONTEND / "assets")), name="frontend-assets")
    @main.app.get("/__acceptance__/ledger")
    def ledger():
        return json.loads(guard.path.read_text())
    print(json.dumps({"url": f"http://127.0.0.1:{port}/", "data_dir": str(directory),
                      "approval_sha256": guard.approval_hash,
                      "worker_may_resume_authorized_tasks": True}, ensure_ascii=False), flush=True)
    import uvicorn
    uvicorn.run(main.app, host="127.0.0.1", port=port, log_level="warning", access_log=False)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--approval", help="JSON record of this run's actual human authorization")
    parser.add_argument("--port", type=int, default=52662)
    args = parser.parse_args()
    if args.serve:
        if not args.approval:
            parser.error("--serve requires --approval; prior budgets are not reusable")
        if not 1024 <= args.port <= 65535:
            parser.error("Use a nonprivileged localhost port")
        serve(args.approval, args.port)
    else:
        print(json.dumps({"mode": "offline_preflight", "network_calls": 0,
                          "approval_template": approval_template(),
                          "note": "No product import, credentials, database, provider or network is accessed"}, ensure_ascii=False, indent=2))
