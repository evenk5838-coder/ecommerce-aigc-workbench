#!/bin/bash
# 双击运行：验证 DeepSeek 连接（只发 1 次最小请求，不调用生成工具、不重试）
# 注意：会消耗少量 DeepSeek 官方额度
set -uo pipefail
cd "$(dirname "$0")"

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

# --- 1. 先检查配置完整性（不联网） ---
CHECK="$("$PY" backend/tools_deepseek.py status 2>&1)"
OK="$(printf '%s' "$CHECK" | "$PY" -c 'import json,sys;print(json.load(sys.stdin).get("ok"))' 2>/dev/null)"
if [ "$OK" != "True" ]; then
  MISSING="$(printf '%s' "$CHECK" | "$PY" -c 'import json,sys;d=json.load(sys.stdin);print("、".join(d.get("missing") or []))' 2>/dev/null)"
  say_dialog "尚未配置" "DeepSeek 配置不完整，缺失：${MISSING:-未知}\n\n请先双击「配置DeepSeek.command」。"
  exit 3
fi

BUDGET="$("$PY" backend/tools_deepseek.py budget 2>/dev/null)"
MODEL="$(printf '%s' "$CHECK" | "$PY" -c 'import json,sys;print(json.load(sys.stdin).get("model",""))' 2>/dev/null)"

# --- 2. 明确告知会花钱，需用户确认 ---
ANSWER="$(osascript <<APPLESCRIPT 2>/dev/null || true
set r to display dialog "将向 DeepSeek 官方发送 1 次最小请求（不调用生成工具、不自动重试）。

模型：${MODEL}
预计消耗：${BUDGET}

是否继续？" with title "验证 DeepSeek 连接" buttons {"取消","继续验证"} default button "继续验证" with icon caution
return button returned of r
APPLESCRIPT
)"

if [ "${ANSWER:-}" != "继续验证" ]; then
  say_dialog "已取消" "未发起任何请求，未消耗额度。"
  exit 0
fi

# --- 3. 发一次请求 ---
RESULT="$(CONFIRM=1 "$PY" backend/tools_deepseek.py verify 2>&1)"
STATUS=$?

if [ $STATUS -eq 0 ]; then
  DETAIL="$(printf '%s' "$RESULT" | "$PY" -c '
import json,sys
d=json.load(sys.stdin)
print(f"回复：{d.get(\"reply\",\"\")}")
print(f"模型：{d.get(\"model\",\"\")}")
print(f"耗时：{d.get(\"elapsed_ms\",\"\")} ms")
print(f"用量：输入 {d.get(\"input_tokens\",0)} / 输出 {d.get(\"output_tokens\",0)} tokens")
' 2>/dev/null)"
  say_dialog "连接成功（已用官方额度）" "DeepSeek 真实调用通过。\n\n${DETAIL}\n\n注意：这仅验证“能连通并返回”，不代表多轮对话质量已验收。"
  exit 0
fi

# --- 4. 失败分类（不能都说成 Key 有问题）---
CATEGORY="$(printf '%s' "$RESULT" | "$PY" -c 'import json,sys;print(json.load(sys.stdin).get("category",""))' 2>/dev/null)"
MESSAGE="$(printf '%s' "$RESULT" | "$PY" -c 'import json,sys;print(json.load(sys.stdin).get("message",""))' 2>/dev/null)"

case "${CATEGORY:-}" in
  ASSISTANT_KEY_INVALID)          TITLE="Key 无效";        HINT="请确认复制的是 DeepSeek 官方 Key，且未过期/撤销。重新双击「配置DeepSeek.command」。" ;;
  ASSISTANT_INSUFFICIENT_BALANCE) TITLE="余额不足";        HINT="Key 有效，但账户余额不足，请到 DeepSeek 平台充值。" ;;
  ASSISTANT_MODEL_UNAVAILABLE)    TITLE="模型不可用";      HINT="当前模型名不被接受，请核对官方文档的模型名（如 deepseek-flash）。" ;;
  ASSISTANT_TIMEOUT)              TITLE="请求超时";        HINT="网络慢或被拦截；未自动重试，请检查网络/代理后重试。" ;;
  ASSISTANT_NETWORK_ERROR)        TITLE="网络不通";        HINT="无法连接 api.deepseek.com；请检查网络、代理或防火墙。" ;;
  PROVIDER_RATE_LIMITED)          TITLE="触发限流";        HINT="请求过于频繁，稍后再试。" ;;
  ASSISTANT_CONFIG_INCOMPLETE)    TITLE="配置不完整";      HINT="请重新双击「配置DeepSeek.command」。" ;;
  PROVIDER_BAD_REQUEST)           TITLE="请求参数被拒";    HINT="可能是模型名或参数不符合官方要求，请把下方信息反馈给开发。" ;;
  *)                              TITLE="验证失败";        HINT="未识别的错误类型，请把下方信息反馈给开发。" ;;
esac

say_dialog "${TITLE}" "${HINT}\n\n错误码：${CATEGORY:-未知}\n信息：$(printf '%s' "$MESSAGE" | head -c 300)"
exit 5
