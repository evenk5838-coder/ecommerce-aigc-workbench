"""Filename-only secret findings and required-file checks; never print matches."""
from pathlib import Path
import hashlib
import json
import os
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIST = Path(os.environ.get("V011_WORKFLOW_DIST", "/private/tmp/ecom-v011-frontend-preview/workflow-dist"))
mode = sys.argv[1]
if mode in ("secrets", "secrets-frontend", "secrets-workflow-frontend"):
    names = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
    forbidden = [n for n in names if n and (Path(n).name == ".env" or n.endswith((".pem", ".key")))]
    ignored = subprocess.run(["git", "check-ignore", "-q", ".env"], cwd=ROOT).returncode == 0
    assert ignored, ".env is not ignored"
    # Scan production source and built text assets, not known test credentials or user media.
    pattern = re.compile(r"(?:sk-[A-Za-z0-9_-]{32,}|AKIA[A-Z0-9]{16}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)")
    folders = [ROOT / folder for folder in ("backend/app", "frontend/src", "frontend/dist")]
    if mode in ("secrets-frontend", "secrets-workflow-frontend"):
        isolated_dist = WORKFLOW_DIST if mode == "secrets-workflow-frontend" else Path("/private/tmp/ecom-v011-frontend-preview/dist")
        assert (isolated_dist / "index.html").is_file(), "Isolated frontend build is missing"
        folders.append(isolated_dist)
    for folder in folders:
        for path in folder.rglob("*"):
            if path.is_file() and path.suffix in (".py", ".ts", ".tsx", ".js", ".json", ".html", ".css", ".map"):
                if pattern.search(path.read_text(encoding="utf-8", errors="replace")):
                    forbidden.append(str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path))
    assert not forbidden, "Secret-like file(s): " + ", ".join(sorted(set(forbidden)))
    print("PASS: .env ignored and untracked; production source/built text assets contain no scanned credential patterns")
    print("Scope: pattern scan only; historical Git objects and arbitrary archives were not scanned")
    if mode in ("secrets-frontend", "secrets-workflow-frontend"):
        print(f"Isolated served output included: {isolated_dist}")
elif mode == "files-frontend-closing":
    required = ["docs/阶段文档/前端收尾-20261028.md",
        "docs/evidence/v0.11-前端收尾-20261028/是否表.md",
        "docs/evidence/v0.11-前端收尾-20261028/照着做清单.md",
        "frontend/src/creative/director-draft-state.ts", "frontend/src/creative/director-draft-state.test.ts",
        "frontend/src/creative/StructureTemplates.tsx", "frontend/src/creative/StructureTemplates.test.ts",
        "frontend/src/creative/ProductionFlow.tsx", "frontend/src/creative/free-canvas-state.test.ts"]
    assert all((ROOT / name).is_file() for name in required), "Frontend closing delivery files missing"
    subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)
    print(f"PASS: {len(required)} frontend closing files; git diff --check")
elif mode == "files":
    required = ["README.md", "docs/项目状态.md", "docs/PRD/PRD.md",
        "docs/阶段文档/技术适配声明-v0.11.md", "docs/阶段文档/阶段2-v0.11-切片1后端.md",
        "docs/接口/接口契约-v0.11-切片1.md", "scripts/check-v011-restart.py", "scripts/check-v011-service-restart.py", "verify.sh",
        "backend/tests/test_director_v011.py", "backend/tests/test_director_v011_atomic.py",
        "backend/tests/test_canvas_nodes_v011.py", "backend/tests/test_asset_upload_v011.py",
        "backend/requirements-test.txt", "scripts/check-v011-legacy.py"]
    missing = [name for name in required if not (ROOT / name).is_file()]
    assert not missing, "Missing files: " + ", ".join(missing)
    subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)
    print(f"PASS: {len(required)} required files in project; git diff --check")
elif mode == "files-director-workflow":
    required = ["README.md", "docs/项目状态.md", "verify.sh", "backend/app/director_workflow.py",
                "backend/app/director_creation.py", "backend/app/director_generation.py",
                "backend/app/director_composition.py", "backend/app/director_recovery.py",
                "backend/tests/test_director_workflow.py", "backend/tests/test_director_creation.py",
                "backend/tests/test_director_generation.py", "backend/tests/test_director_composition.py",
                "backend/tests/test_director_recovery.py", "backend/tests/test_director_workflow_http.py",
                "backend/tests/test_director_confirm_atomic.py", "scripts/check-director-workflow-http.py",
                "docs/接口/对话多角色执行-后端契约-v0.11.md",
                "docs/阶段文档/对话多角色执行-后端-20261027.md"]
    missing = [name for name in required if not (ROOT / name).is_file()]
    assert not missing, "Missing files: " + ", ".join(missing)
    subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)
    print(f"PASS: {len(required)} director workflow backend files; git diff --check")
elif mode == "files-canvas-core":
    required = ["README.md", "docs/项目状态.md", "docs/PRD/PRD.md", "verify.sh",
                "backend/app/canvas_history.py", "backend/app/node_execution.py",
                "backend/tests/test_canvas_history.py", "backend/tests/test_node_execution.py",
                "backend/tests/test_canvas_execution_http.py", "scripts/check-canvas-core-restart.py",
                "docs/接口/节点执行与删除撤销-后端契约-v0.11.md",
                "docs/阶段文档/节点执行与删除撤销-后端-20261027.md"]
    assert all((ROOT / name).is_file() for name in required), "Canvas core delivery files missing"
    subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)
    print(f"PASS: {len(required)} canvas core backend files; git diff --check")
elif mode == "files-frontend":
    required = ["README.md", "docs/项目状态.md", "docs/PRD/PRD.md",
        "docs/阶段文档/前端适配声明-v0.11.md", "docs/阶段文档/阶段3-v0.11-切片1前端.md",
        "docs/接口/接口契约-v0.11-切片1.md", "scripts/preview-v011-frontend.py",
        "scripts/check-v011-service-restart.py", "scripts/check-v011-restart.py", "verify.sh",
        "frontend/package.json", "frontend/package-lock.json", "frontend/tsconfig.json",
        "frontend/src/CreativeSpace.tsx", "frontend/src/creative/FreeCreationCanvas.tsx",
        "frontend/src/creative/DirectorChat.tsx", "frontend/src/creative/CanvasNodeSettings.tsx",
        "frontend/src/creative/node-config.test.ts", "frontend/src/creative/director-conversation-state.test.ts"]
    missing = [name for name in required if not (ROOT / name).is_file()]
    assert not missing, "Missing files: " + ", ".join(missing)
    subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)
    print(f"PASS: {len(required)} frontend technical files in project; git diff --check")
    print("Scope: technical files only; browser screenshots and final acceptance are checked separately")
elif mode == "files-canvas-core-frontend":
    required = ["README.md", "docs/项目状态.md", "docs/PRD/PRD.md", "verify.sh",
        "docs/阶段文档/前端适配声明-v0.11.md",
        "docs/阶段文档/画布操作与节点执行-前端-20261027.md",
        "docs/接口/节点执行与删除撤销-后端契约-v0.11.md",
        "scripts/check-v011-files.py", "scripts/check-canvas-core-restart.py",
        "scripts/preview-v011-frontend.py", "backend/app/canvas_history.py",
        "backend/app/node_execution.py", "frontend/package.json", "frontend/package-lock.json",
        "frontend/tsconfig.json", "frontend/src/CreativeSpace.tsx",
        "frontend/src/creative/FreeCreationCanvas.tsx", "frontend/src/creative/FreeCreationCanvas.css",
        "frontend/src/creative/CanvasNodeSettings.tsx", "frontend/src/creative/CanvasNodeSettings.css",
        "frontend/src/creative/canvas-node-actions.ts", "frontend/src/creative/canvas-node-actions.test.ts",
        "frontend/src/creative/free-canvas-state.ts", "frontend/src/creative/free-canvas-state.test.ts",
        "frontend/src/creative/node-config.ts", "frontend/src/creative/node-config.test.ts",
        "frontend/src/creative/canvas-layout-sync.ts", "frontend/src/creative/canvas-layout-sync.test.ts"]
    missing = [name for name in required if not (ROOT / name).is_file()]
    assert not missing, "Missing files: " + ", ".join(missing)
    subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)
    print(f"PASS: {len(required)} canvas core frontend technical files; git diff --check")
    print("Scope: technical files only; browser evidence, real-model evidence and final acceptance checked separately")
elif mode == "files-director-workflow-frontend":
    required = ["README.md", "docs/项目状态.md", "docs/PRD/PRD.md", "verify.sh",
        "docs/阶段文档/前端适配声明-v0.11.md",
        "docs/阶段文档/对话多角色执行-前端-20261028.md",
        "docs/evidence/v0.11-对话多角色执行-前端-20261028/是否表.md",
        "docs/evidence/v0.11-对话多角色执行-前端-20261028/照着做清单.md",
        "docs/evidence/v0.11-对话多角色执行-前端-20261028/自动检查命令与范围.md",
        "docs/接口/对话多角色执行-后端契约-v0.11.md",
        "scripts/check-v011-files.py", "scripts/check-director-workflow-http.py", "scripts/preview-v011-frontend.py",
        "frontend/package.json", "frontend/package-lock.json", "frontend/tsconfig.json", "frontend/src/App.tsx",
        "frontend/src/CreativeSpace.tsx", "frontend/src/creative/DirectorChat.tsx", "frontend/src/creative/DirectorChat.css",
        "frontend/src/creative/WorkflowActionCard.tsx", "frontend/src/creative/director-action-view.ts",
        "frontend/src/creative/director-action-view.test.tsx", "frontend/src/creative/director-workflow-api.ts",
        "frontend/src/creative/director-workflow-api.test.ts", "frontend/src/creative/workflow-conversation-state.ts",
        "frontend/src/creative/workflow-conversation-state.test.ts", "frontend/src/creative/FreeCreationCanvas.tsx",
        "frontend/src/creative/workflow-draft-scope.ts", "frontend/src/creative/workflow-draft-scope.test.ts",
        "frontend/src/creative/FreeCreationCanvas.css", "frontend/src/creative/CanvasNodeSettings.tsx",
        "frontend/src/creative/canvas-node-actions.ts", "frontend/src/creative/canvas-node-actions.test.ts",
        "frontend/src/creative/free-canvas-reference.ts", "frontend/src/creative/free-canvas-reference.test.ts",
        "frontend/src/creative/canvas-reference-connection.ts", "frontend/src/creative/canvas-reference-connection.test.ts",
        "frontend/src/creative/director-canvas-projection.ts", "frontend/src/creative/director-canvas-projection.test.ts",
        "frontend/src/creative/canvas-progress-sync.ts", "frontend/src/creative/canvas-progress-sync.test.ts",
        "frontend/src/creative/creative-location.ts", "frontend/src/creative/creative-location.test.ts"]
    missing = [name for name in required if not (ROOT / name).is_file()]
    assert not missing, "Missing files: " + ", ".join(missing)
    subprocess.run(["git", "diff", "--check"], cwd=ROOT, check=True)
    print(f"PASS: {len(required)} director workflow frontend technical files; git diff --check")
    print("Scope: technical files only; browser evidence, real-model evidence and final acceptance checked separately")
elif mode in ("snapshot-workflow-source", "check-workflow-source"):
    destination = Path(sys.argv[2])
    paths = {path for path in (ROOT / "frontend/src").rglob("*") if path.is_file()}
    paths.update(path for path in (ROOT / "frontend").iterdir()
                 if path.is_file() and path.suffix in (".json", ".ts", ".js", ".cjs", ".mjs"))
    paths.update(ROOT / name for name in ("verify.sh", "scripts/check-v011-files.py", "scripts/check-director-workflow-http.py"))
    snapshot = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(paths)}
    if mode == "snapshot-workflow-source":
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Recorded frontend verification input fingerprint: {len(snapshot)} files")
    else:
        assert snapshot == json.loads(destination.read_text(encoding="utf-8")), "Frontend verification inputs changed during the run; rerun checks on the final source"
        print(f"PASS: frontend verification inputs unchanged during the run ({len(snapshot)} file fingerprints)")
elif mode in ("snapshot-isolated-build", "snapshot-workflow-build", "check-workflow-build"):
    destination = Path(sys.argv[2])
    folder = Path("/private/tmp/ecom-v011-frontend-preview/dist") if mode == "snapshot-isolated-build" else WORKFLOW_DIST
    snapshot = {str(path.relative_to(folder)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(folder.rglob("*")) if path.is_file()}
    assert "index.html" in snapshot, "Isolated frontend build is missing"
    if mode == "check-workflow-build":
        assert snapshot == json.loads(destination.read_text(encoding="utf-8")), "Isolated workflow build changed during the run"
        print(f"PASS: isolated workflow build unchanged during the run ({len(snapshot)} file fingerprints)")
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Recorded isolated frontend build fingerprint: {len(snapshot)} files ({folder})")
elif mode in ("snapshot-live-build", "check-live-build"):
    destination = Path(sys.argv[2])
    folder = ROOT / "frontend/dist"
    snapshot = {str(path.relative_to(folder)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(folder.rglob("*")) if path.is_file()}
    assert "index.html" in snapshot, "Existing live frontend build is missing"
    if mode == "snapshot-live-build":
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Recorded live frontend build fingerprint: {len(snapshot)} files")
    else:
        assert snapshot == json.loads(destination.read_text(encoding="utf-8")), "Live frontend/dist changed during isolated verification"
        print(f"PASS: existing frontend/dist unchanged ({len(snapshot)} file fingerprints)")
else:
    raise SystemExit("Unknown check mode; see verify.sh for the supported stage-specific modes")
