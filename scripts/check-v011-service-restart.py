"""Start/stop/restart a private Uvicorn service on temporary data, no real LLM."""
from __future__ import annotations

import os
import shutil
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def serve(directory: Path, port: int) -> None:
    sys.path.insert(0, str(ROOT / "backend"))
    from app import config
    config.DATA = directory
    config.DB_PATH = directory / "restart.db"
    config.ASSETS = directory / "assets"
    config.MODELS = directory / "models"
    config.DIRECTOR_MAX_COST_PER_CALL_CNY = 0
    config.DIRECTOR_MAX_SESSION_COST_CNY = 0
    for folder in (config.ASSETS, config.MODELS):
        folder.mkdir(exist_ok=True)
    from app.assistant import provider
    from app import providers_ark

    def forbidden(*args, **kwargs):
        raise AssertionError("Real model requests forbidden in restart check")

    provider._deepseek_post = forbidden
    providers_ark._request = forbidden
    class CountedFixtureProvider(provider.FixedReplyProvider):
        def chat(self, *args, **kwargs):
            with (directory / "provider-calls.txt").open("a") as calls:
                calls.write("fixture call\n")
            return super().chat(*args, **kwargs)

    provider.set_provider(CountedFixtureProvider([
        {"tool_calls": [{"name": "propose_script", "arguments": {
            "summary": "A real saved fixture script", "shots": [{"hint": "Show one bottle", "duration_s": 5}]}}]},
        *[{"tool_calls": [{"name": "propose_script", "arguments": {"summary": ""}}]}] * 2,
    ]))
    from app.main import app
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)


def run() -> None:
    with socket.socket() as reservation:
        reservation.bind(("127.0.0.1", 0))
        port = reservation.getsockname()[1]
    # Use a free private port; never take ownership of an existing app process.
    url = f"http://127.0.0.1:{port}"
    process = None
    environment = {**os.environ, "ARK_API_KEY": "", "DEEPSEEK_API_KEY": "",
                   "ASSISTANT_API_KEY": "", "ASSISTANT_PROVIDER": "none", "APP_BUDGET_CNY": "0"}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with tempfile.TemporaryDirectory(prefix="ecom-v011-service-restart-") as temporary:
        def start():
            server = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "serve", temporary, str(port)],
                                      env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            for attempt in range(100):
                if server.poll() is not None:
                    raise RuntimeError("Isolated server failed to start; no existing process was stopped")
                try:
                    with opener.open(url + "/api/health", timeout=.2) as response:
                        if response.status == 200:
                            return server
                except OSError:
                    time.sleep(.1)
            server.terminate()
            server.wait(timeout=10)
            raise RuntimeError("Isolated server readiness timeout")

        def stop(server):
            server.terminate()
            try:
                server.wait(timeout=10)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)
            assert server.poll() is not None
            if server.stdout:
                server.stdout.close()

        try:
            for mode in ("write", "read"):
                process = start()
                print(f"服务{'首次启动' if mode == 'write' else '重启'}就绪：隔离临时库，端口 {port}", flush=True)
                subprocess.run([sys.executable, str(ROOT / "scripts/check-v011-restart.py"), mode, temporary],
                               check=True, timeout=60,
                               env={**environment, "V011_CHECK_SERVER_URL": url})
                stop(process)
                process = None
                print("本次服务进程已退出", flush=True)
            evidence = os.environ.get("V011_EVIDENCE_DIR")
            if evidence:
                destination = Path(evidence)
                destination.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(Path(temporary) / "expected.json", destination / "隔离HTTP响应.json")
            print("PASS: isolated HTTP service restarted; scripts/nodes/assets/config/receipts recovered; no paid calls")
        finally:
            if process is not None:
                stop(process)


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "serve":
        serve(Path(sys.argv[2]), int(sys.argv[3]))
    else:
        run()
