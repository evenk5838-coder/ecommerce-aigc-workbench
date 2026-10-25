#!/bin/bash
# 双击运行：轮换/更新火山方舟 ARK_API_KEY（生图、生视频用）
# 安全：Key 只经 stdin 传给 Python；不回显、不进命令行参数、不进日志
set -uo pipefail
cd "$(dirname "$0")"

LOG="$PWD/logs/ark-key-rotation.log"
mkdir -p "$PWD/logs"
log_line() {  # 只记诊断信息，绝不记录 Key 明文
  printf '%s | %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$1" >> "$LOG" 2>/dev/null || true
}

log_line "start: pid=$$ cwd=$PWD"

say_dialog() {
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

# --- 1. 优先读剪贴板（最可靠）；剪贴板不是 ark- Key 时再弹隐藏输入框 ---
SOURCE="clipboard"
KEY="$(pbpaste 2>/dev/null | head -c 300 | tr -d '\r\n' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
case "$KEY" in
  ark-*) : ;;
  *)
    SOURCE="dialog"
    KEY="$(osascript <<'APPLESCRIPT' 2>/dev/null || true
set r to display dialog "剪贴板里没有检测到 ark- 开头的 Key。\n请手动粘贴【新的】方舟 API Key（输入框内容不会显示）：" with title "轮换方舟 Key" default answer "" with hidden answer buttons {"取消","保存"} default button "保存" with icon note
return text returned of r
APPLESCRIPT
)"
    ;;
esac
log_line "source=$SOURCE"

if [ -z "${KEY:-}" ]; then
  say_dialog "已取消" "未做任何修改，旧 Key 仍然有效。"
  exit 0
fi

# --- 2. 先做免费校验（GET /models，不消耗额度、不写入）---
PROBE="$(printf '%s' "$KEY" | "$PY" backend/tools_set_key.py ARK_API_KEY --probe 2>&1)"
POK="$(printf '%s' "$PROBE" | "$PY" -c 'import json,sys;print(json.load(sys.stdin).get("ok"))' 2>/dev/null)"

log_line "probe: ok=$POK category=${PCAT:-none}"

if [ "$POK" != "True" ]; then
  PCAT="$(printf '%s' "$PROBE" | "$PY" -c 'import json,sys;print(json.load(sys.stdin).get("category",""))' 2>/dev/null)"
  PMSG="$(printf '%s' "$PROBE" | "$PY" -c 'import json,sys;print(json.load(sys.stdin).get("message",""))' 2>/dev/null)"
  log_line "probe_fail: category=${PCAT:-none} msg=${PMSG:0:120}"
  unset KEY
  say_dialog "新 Key 未通过校验（未写入）" "原因：${PMSG:-未知}\n错误类型：${PCAT:-未知}\n\n.env 未被修改，旧 Key 仍然有效，生图/生视频不受影响。\n请确认后再双击本脚本。"
  exit 4
fi

MCOUNT="$(printf '%s' "$PROBE" | "$PY" -c 'import json,sys;print(json.load(sys.stdin).get("model_count",0))' 2>/dev/null)"

# --- 3. 校验通过才写入（Key 走 stdin）---
RESULT="$(printf '%s' "$KEY" | "$PY" backend/tools_set_key.py ARK_API_KEY 2>&1)"
STATUS=$?
unset KEY

if [ $STATUS -ne 0 ]; then
  ERR="$(printf '%s' "$RESULT" | "$PY" -c 'import json,sys;print(json.load(sys.stdin).get("error",""))' 2>/dev/null)"
  say_dialog "未写入" "Key 校验未通过：${ERR:-未知原因}\n\n.env 未被修改，旧 Key 仍有效。"
  exit 3
fi

MASKED="$(printf '%s' "$RESULT" | "$PY" -c 'import json,sys;print(json.load(sys.stdin).get("masked",""))' 2>/dev/null)"

# --- 3. 重启服务（先查活动任务）---
RELOAD_MSG=""
if curl -s --max-time 3 http://127.0.0.1:8100/api/health >/tmp/ark_health.json 2>/dev/null; then
  ACTIVE="$("$PY" -c 'import json;d=json.load(open("/tmp/ark_health.json"));t=d.get("tasks") or {};print(sum(v for k,v in t.items() if k in ("queued","loading","running","postprocess")))' 2>/dev/null)"
  if [ "${ACTIVE:-0}" -gt 0 ]; then
    RELOAD_MSG="⚠️ 检测到 ${ACTIVE} 个生成任务正在执行，已跳过自动重启。\n新 Key 已写入 .env，但要等任务结束、服务重启后才生效。"
  else
    PID="$(pgrep -f "uvicorn app.main:app --host 127.0.0.1 --port 8100" | head -1)"
    [ -n "${PID:-}" ] && kill "$PID" 2>/dev/null && sleep 3
    ( cd backend && nohup "$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8100 \
        >> ../logs/server.log 2>&1 & )
    sleep 7
    if curl -s --max-time 5 http://127.0.0.1:8100/api/health >/dev/null 2>&1; then
      RELOAD_MSG="服务已重启，新 Key 已生效。"
    else
      RELOAD_MSG="服务重启失败，请双击 start.command 手动启动。"
    fi
  fi
else
  RELOAD_MSG="服务当前未运行；下次启动后新 Key 生效。"
fi

say_dialog "已写入新 Key" "新 Key：${MASKED}\n免费校验：通过（可读取 ${MCOUNT} 个模型）\n\n${RELOAD_MSG}\n\n下一步（重要）：\n1. 先在火山方舟控制台「API Key 管理」里确认新 Key 可用；\n2. 让我做一次免费的关键校验；\n3. 校验通过后，再回控制台【删除旧 Key】。\n\n顺序不要颠倒，否则会导致生图/生视频中断。"
echo "已写入 $MASKED"
