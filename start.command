#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d frontend/node_modules ]; then
  (cd frontend && npm ci)
fi
(cd frontend && npm run build)

if lsof -nP -iTCP:8100 -sTCP:LISTEN >/dev/null 2>&1; then
  echo '8100 已有服务运行。正式工作台：http://127.0.0.1:8100/'
  echo '如果刚更新了后端代码，请先在原终端停止后端，再运行本脚本。'
  exit 0
fi

studio_python="${APP_PYTHON:-}"
if [ -z "$studio_python" ]; then
  for candidate in "$PWD/backend/.venv/bin/python" "$PWD/.venv/bin/python" /private/tmp/ecom-local-probe-20261022/venv/bin/python; do
    if [ -x "$candidate" ]; then studio_python="$candidate"; break; fi
  done
fi
if [ -z "$studio_python" ]; then
  echo '没有找到后端 Python 环境。请先安装 backend/requirements.txt，或设置 APP_PYTHON 为已有环境中的 python 路径。'
  exit 1
fi
echo '物料间：http://127.0.0.1:8100/ （按 Ctrl+C 停止）'
cd backend
exec "$studio_python" -m uvicorn app.main:app --host 127.0.0.1 --port 8100
