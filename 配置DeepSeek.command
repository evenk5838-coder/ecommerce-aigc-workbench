#!/bin/bash
# 双击运行：配置 DeepSeek 官方 API（只填一次 Key）
# 安全：Key 只经 stdin 传给 Python，不回显、不进命令行参数、不进日志
set -uo pipefail
cd "$(dirname "$0")"

say_dialog() {  # $1=标题 $2=正文
  osascript -e "display dialog \"$2\" with title \"$1\" buttons {\"好\"} default button \"好\" with icon note" >/dev/null 2>&1
}

find_python() {
  for c in "backend/.venv/bin/python" ".venv/bin/python" \
           "/private/tmp/ecom-local-probe-20261022/venv/bin/python"; do
    [ -x "$c" ] && { echo "$c"; return 0; }
  done
  command -v python3
}

PY="$(find_python)"

# --- 1. 取 Key（macOS 隐藏输入弹窗）---
KEY="$(osascript <<'APPLESCRIPT' 2>/dev/null || true
set r to display dialog "请粘贴 DeepSeek 官方 API Key（输入框内容不会显示）：" with title "配置 DeepSeek" default answer "" with hidden answer buttons {"取消","保存"} default button "保存" with icon note
return text returned of r
APPLESCRIPT
)"

if [ -z "${KEY:-}" ]; then
  say_dialog "配置 DeepSeek" "已取消，未做任何修改。"
  exit 0
fi

# --- 2. 写入配置（Key 经 stdin，不出现在进程参数里）---
RESULT="$(printf '%s' "$KEY" | "$PY" backend/tools_deepseek.py setup 2>&1)"
STATUS=$?
unset KEY

if [ $STATUS -ne 0 ]; then
  say_dialog "配置失败" "写入配置失败，请确认项目目录完整。\n\n$(printf '%s' "$RESULT" | head -c 300)"
  exit 1
fi

# --- 3. 检查完整性 ---
CHECK="$("$PY" backend/tools_deepseek.py status 2>&1)"
OK="$(printf '%s' "$CHECK" | "$PY" -c 'import json,sys;print(json.load(sys.stdin).get("ok"))' 2>/dev/null)"
MASKED="$(printf '%s' "$CHECK" | "$PY" -c 'import json,sys;print(json.load(sys.stdin).get("api_key_masked",""))' 2>/dev/null)"
MODEL="$(printf '%s' "$CHECK" | "$PY" -c 'import json,sys;print(json.load(sys.stdin).get("model",""))' 2>/dev/null)"
MISSING="$(printf '%s' "$CHECK" | "$PY" -c 'import json,sys;d=json.load(sys.stdin);print("、".join(d.get("missing") or []))' 2>/dev/null)"

if [ "$OK" != "True" ]; then
  say_dialog "配置不完整" "已写入，但检查未通过。\n缺失：${MISSING:-未知}\n\n请重新双击本脚本。"
  exit 3
fi

# --- 4. 如需重载服务：先检查活动任务 ---
RELOAD_MSG=""
if curl -s --max-time 3 http://127.0.0.1:8100/api/health >/tmp/ds_health.json 2>/dev/null; then
  ACTIVE="$("$PY" -c 'import json;d=json.load(open("/tmp/ds_health.json"));t=d.get("tasks") or {};print(sum(v for k,v in t.items() if k in ("queued","loading","running","postprocess")))' 2>/dev/null)"
  if [ "${ACTIVE:-0}" -gt 0 ]; then
    RELOAD_MSG="检测到 ${ACTIVE} 个生成任务正在执行，已跳过自动重启以免打断。\n任务结束后请重新双击本脚本，或手动重启服务。"
  else
    PID="$(pgrep -f "uvicorn app.main:app --host 127.0.0.1 --port 8100" | head -1)"
    if [ -n "${PID:-}" ]; then
      kill "$PID" 2>/dev/null
      sleep 3
    fi
    ( cd backend && nohup "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8100 \
        >> ../logs/server.log 2>&1 & )
    sleep 7
    STATE="$(curl -s --max-time 5 http://127.0.0.1:8100/api/assistant/status)"
    if printf '%s' "$STATE" | grep -q '"state":"configured_unverified"'; then
      RELOAD_MSG="服务已自动重启，助手状态：已配置但未验证。"
    else
      RELOAD_MSG="服务已尝试重启，但状态未确认。请打开 http://127.0.0.1:8100/ 检查。"
    fi
  fi
else
  RELOAD_MSG="服务当前未运行；下次启动（双击 start.command）后生效。"
fi

say_dialog "配置成功" "DeepSeek 已配置完成\n\nKey：${MASKED}\n模型：${MODEL}\n地址：https://api.deepseek.com\n\n${RELOAD_MSG}\n\n下一步：如需确认能连通，双击「验证DeepSeek连接.command」（会消耗少量官方额度）。"
echo "配置完成：model=$MODEL key=$MASKED"
