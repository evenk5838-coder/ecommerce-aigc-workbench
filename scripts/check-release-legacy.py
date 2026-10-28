"""Retained feature checks with fresh data, no credentials or network, new evidence only.

Run with the existing test Python. This never builds or replaces frontend/dist.
The budget is a fixture used to exercise validation paths, not spending authority.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
from datetime import datetime
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE = ROOT / "docs/evidence/v0.11-真实联调与整版验收-20261028"
SUITES = {
    "projects": "backend/tools_s2_projects_tests.py",
    "four-modes": "backend/tools_four_modes_tests.py",
    "template-contracts": "backend/tools_s1_fix_tests.py",
    "structure-and-zip": "backend/tools_s5_s6_tests.py",
    "pipeline-and-zip": "backend/tools_pipeline_tests.py",
    "retained-director": "backend/tools_director_chat_tests.py",
}
FRONTEND_FILES = [
    "src/generation-modes.test.ts", "src/generation.test.ts",
    "src/estimates.test.ts", "src/capabilities.test.ts", "src/image-specs.test.ts",
    "src/single-templates.test.ts", "src/editor.test.ts",
    "src/creative/StructureTemplates.test.ts", "src/creative/api.test.ts",
    "src/creative/state.test.ts", "src/creative/creative-location.test.ts",
    "src/creative/production-api.test.ts", "src/creative/production-wire.test.ts",
    "src/creative/production-guards.test.ts",
]


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def hashes(folder):
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file()} if folder.exists() else {}


def guarded_child(name, output):
    output.mkdir(parents=True, exist_ok=False)
    info = {"suite": name, "real_provider_calls": 0, "network_attempts_blocked": 0,
            "business_database_opened": False, "dotenv_read": False,
            "sqlite_paths": [], "fixture_budget_cny": 1000}
    with tempfile.TemporaryDirectory(prefix="release-legacy-") as temp, ExitStack() as stack:
        private = Path(temp).resolve()
        stack.enter_context(patch.object(tempfile, "tempdir", str(private)))
        exists = Path.exists
        stack.enter_context(patch.object(Path, "exists", lambda path: False if path == ROOT / ".env" else exists(path)))
        connect = sqlite3.connect

        def isolated_connect(database, *args, **kwargs):
            path = Path(database).resolve()
            if not path.is_relative_to(private):
                raise AssertionError("SQLite path outside this check's private directory")
            if str(path) not in info["sqlite_paths"]:
                info["sqlite_paths"].append(str(path))
            return connect(database, *args, **kwargs)

        def no_network(*args, **kwargs):
            info["network_attempts_blocked"] += 1
            raise AssertionError("Network forbidden in retained feature checks")

        stack.enter_context(patch("sqlite3.connect", isolated_connect))
        stack.enter_context(patch("socket.socket.connect", no_network))
        stack.enter_context(patch("socket.create_connection", no_network))
        stack.enter_context(patch.dict(os.environ, {
            "ARK_API_KEY": "", "DEEPSEEK_API_KEY": "", "ASSISTANT_API_KEY": "",
            "ASSISTANT_PROVIDER": "none", "APP_BUDGET_CNY": "1000",
        }))
        sys.path.insert(0, str(ROOT / "backend"))
        from app import config
        config.DATA, config.DB_PATH = private, private / "unused.db"
        config.ASSETS, config.MODELS = private / "assets", private / "models"
        config.ASSETS.mkdir()
        config.MODELS.mkdir()
        from app import worker
        stack.enter_context(patch.object(worker, "start", side_effect=AssertionError("Background worker forbidden")))
        try:
            if name == "project-lifecycle":
                suite = unittest.defaultTestLoader.discover(str(ROOT / "backend/tests"), pattern="test_project_deletion.py")
                result = unittest.TextTestRunner(verbosity=2).run(suite)
                info.update(total=result.testsRun, failed=len(result.failures) + len(result.errors),
                            skipped=len(result.skipped))
                code = 0 if result.wasSuccessful() else 1
            else:
                spec = importlib.util.spec_from_file_location("release_legacy_suite", ROOT / SUITES[name])
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                module.OUT = output
                code = module.run()
                info.update(total=len(module.RESULTS), failed=sum(not case["ok"] for case in module.RESULTS))
            info["exit_code"] = code
        finally:
            write_json(output / "isolation.json", info)
    return code


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--child", choices=[*SUITES, "project-lifecycle"])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.child:
        if args.output is None:
            parser.error("child requires output")
        return guarded_child(args.child, args.output.resolve())
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S") + f"-{os.getpid()}"
    output = (args.output or DEFAULT_EVIDENCE / f"legacy-{stamp}").resolve()
    output.mkdir(parents=True, exist_ok=False)
    before = hashes(ROOT / "frontend/dist")
    write_json(output / "live-build-before.json", before)
    write_json(output / "source-sha256.json", {
        **{k: v for k, v in hashes(ROOT / "backend/app").items() if k.endswith(".py")},
        **{k: v for k, v in hashes(ROOT / "frontend/src").items() if k.endswith((".ts", ".tsx"))},
    })
    environment = {k: os.environ[k] for k in ("PATH", "LANG", "LC_ALL", "SYSTEMROOT", "TMPDIR") if k in os.environ}
    environment.update(PYTHONDONTWRITEBYTECODE="1", ARK_API_KEY="", DEEPSEEK_API_KEY="", ASSISTANT_API_KEY="")
    entries = []
    for name in ["project-lifecycle", *SUITES]:
        command = [sys.executable, str(Path(__file__).resolve()), "--child", name, "--output", str(output / name)]
        with (output / f"{name}.log").open("x") as log:
            run = subprocess.run(command, cwd=ROOT, env=environment, stdout=log, stderr=subprocess.STDOUT)
        info_file = output / name / "isolation.json"
        info = json.loads(info_file.read_text()) if info_file.exists() else {}
        entries.append({"name": name, "exit_code": run.returncode, "details": info})
        print(f"{name}: exit={run.returncode}, total={info.get('total')}, failed={info.get('failed')}", flush=True)
    command = ["npm", "test", "--", "--run", *FRONTEND_FILES, "--reporter=default", "--reporter=json", f"--outputFile={output / 'frontend.json'}"]
    with (output / "frontend.log").open("x") as log:
        run = subprocess.run(command, cwd=ROOT / "frontend", env=environment, stdout=log, stderr=subprocess.STDOUT)
    frontend_report = output / "frontend.json"
    frontend_result = json.loads(frontend_report.read_text()) if frontend_report.exists() else {}
    entries.append({"name": "frontend-unit", "exit_code": run.returncode, "test_files": FRONTEND_FILES,
                    "total": frontend_result.get("numTotalTests"), "passed": frontend_result.get("numPassedTests"),
                    "failed": frontend_result.get("numFailedTests"), "files": len(frontend_result.get("testResults", []))})
    after = hashes(ROOT / "frontend/dist")
    write_json(output / "live-build-after.json", after)
    report = {"scope": "Fresh retained feature checks; fixed providers only, no browser acceptance",
              "runs": entries, "live_build_unchanged": before == after,
              "browser_pending": ["Actual PNG canvas render and file download", "Asset ZIP browser download",
                                  "Project create/delete controls and stale-project landing in browser", "Template apply controls"],
              "real_model_calls": 0, "paid_calls": 0}
    write_json(output / "summary.json", report)
    print(f"Evidence: {output}")
    return 0 if all(row["exit_code"] == 0 for row in entries) and before == after else 1


if __name__ == "__main__":
    raise SystemExit(main())
