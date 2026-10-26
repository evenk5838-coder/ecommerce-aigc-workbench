"""LLM 适配层：厂商可替换。

设计约束（对应 PRD v0.9 §13 与阶段一约定）：
- **默认不可用**：未配置 `ASSISTANT_PROVIDER` 时返回 UnavailableProvider，
  前端必须显示"助手暂不可用"，不得伪造已接通。
- 只看文本与素材**元数据**，不发送图片字节（看图能力尚未确认）。
- 适配层只做「给消息 + 工具声明 → 回复 + 工具调用」，不参与计价、
  不判断用户是否确认、不碰额度。

新增厂商时只需实现 `LLMProvider.chat()`，业务代码不变。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"name": self.name, "description": self.description,
                "parameters": self.parameters}


@dataclass
class ToolCall:
    name: str
    arguments: dict = field(default_factory=dict)
    id: str = ""          # 厂商返回的工具调用 ID，回传工具结果时必须带上


@dataclass
class ChatResult:
    reply: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    # 结构化输出（前端直接消费，避免解析自然语言）：
    #   reply_type / options / optimized_prompt / missing_fields / quote_ref
    structured: dict = field(default_factory=dict)
    # 真实厂商可返回 stop_reason / raw，便于审计
    meta: dict = field(default_factory=dict)


class LLMProvider(Protocol):
    """厂商适配接口。

    `tool_results` 传入上一轮工具执行结果，供模型"观察结果后继续或停止"。
    真实适配层必须把工具结果回传给模型；只把原始 JSON 丢给前端不算完成判断。
    """

    name: str
    model: str

    def available(self) -> bool: ...

    def chat(self, messages: list[dict], tools: list[ToolSpec],
             tool_results: list[dict] | None = None, *,
             max_tokens: int | None = None) -> ChatResult: ...


class UnavailableProvider:
    """未配置 LLM 时的默认实现：不假装接通。"""

    name = "none"
    model = ""

    def available(self) -> bool:
        return False

    def chat(self, messages, tools, tool_results=None, *, max_tokens=None) -> ChatResult:  # noqa: ANN001
        raise RuntimeError("assistant provider not configured")


class FixedReplyProvider:
    """**测试替身**：按预设脚本返回，不联网、不产生任何费用。

    仅用于隔离测试。不得在正式页面配置为已接通状态。
    """

    name = "fixed"

    def __init__(self, script: list[dict] | None = None, model: str = "fixed-test"):
        self.model = model
        self._script = list(script or [])
        self._i = 0
        self.calls: list[dict] = []

    def available(self) -> bool:
        return True

    def push(self, item: dict) -> None:
        self._script.append(item)

    def chat(self, messages, tools, tool_results=None, *, max_tokens=None) -> ChatResult:  # noqa: ANN001
        self.calls.append({"messages": list(messages),
                           "tools": [t.name for t in tools],
                           "max_tokens": max_tokens,
                           "tool_results": tool_results or []})
        item = self._script[self._i] if self._i < len(self._script) else {
            "reply": "（测试替身无更多脚本）", "tool_calls": []}
        self._i += 1
        return ChatResult(
            reply=str(item.get("reply") or ""),
            tool_calls=[ToolCall(name=tc["name"], arguments=tc.get("arguments") or {})
                        for tc in item.get("tool_calls", [])],
            input_tokens=int(item.get("input_tokens") or 0),
            output_tokens=int(item.get("output_tokens") or 0),
            structured=dict(item.get("structured") or {}),
            meta={"provider": self.name, "model": self.model, "stub": True},
        )


class ArkChatProvider:
    """火山方舟方舟对话模型适配器（OpenAI 兼容 /chat/completions）。

    约束：
    - 复用 `providers_ark._request`：统一 Key 读取与错误映射；测试守卫可拦截。
    - **不自动重试**（网络/限流失败直接抛出，由调用方决定）。
    - 只发送文本；不发送图片字节（看图能力未启用）。
    - 支持工具调用：返回 `tool_calls`（含 id），工具结果由上层以 role=tool 回传。
    """

    name = "ark"

    def __init__(self, model: str, timeout: float | None = None):
        self.model = model or ""
        self.timeout = timeout or float(os.getenv("ASSISTANT_TIMEOUT_S", "30"))

    def available(self) -> bool:
        from .. import providers_ark
        return bool(self.model) and providers_ark.configured()

    def chat(self, messages, tools, tool_results=None, *, max_tokens=None) -> ChatResult:  # noqa: ANN001
        from .. import providers_ark

        body: dict = {
            "model": self.model,
            "messages": _to_openai_messages(messages),
            "temperature": float(os.getenv("ASSISTANT_TEMPERATURE", "0.3")),
        }
        max_tokens = max_tokens if max_tokens is not None else os.getenv("ASSISTANT_MAX_TOKENS")
        if max_tokens:
            body["max_tokens"] = int(max_tokens)
        if tools:
            body["tools"] = [{"type": "function",
                              "function": {"name": t.name,
                                           "description": t.description,
                                           "parameters": t.parameters or {"type": "object",
                                                                          "properties": {}}}}
                             for t in tools]

        data = providers_ark._request("POST", "/chat/completions", body,
                                      timeout=self.timeout)  # noqa: SLF001
        return _parse_chat_response(data, self.name, self.model)


def _to_openai_messages(messages: list[dict]) -> list[dict]:
    """把内部消息规范化为 OpenAI/Ark 兼容格式。

    - `context` 角色折叠为一条 system 消息（避免非标准 role 被厂商拒绝）
    - 其余 system/user/assistant/tool 原样保留
    """
    out: list[dict] = []
    for m in messages:
        role = m.get("role")
        content = m.get("content")
        if role == "context":
            import json as _json
            out.append({"role": "system",
                        "content": "会话上下文（JSON）：" +
                                   _json.dumps(content, ensure_ascii=False)})
        elif role in ("system", "user", "assistant", "tool"):
            item = {"role": role, "content": content if isinstance(content, str)
                    else json.dumps(content, ensure_ascii=False)}
            for k in ("tool_calls", "tool_call_id", "name"):
                if k in m:
                    item[k] = m[k]
            out.append(item)
        else:
            out.append({"role": "system", "content": str(content)})
    return out


def _parse_chat_response(data: dict, provider_name: str, model: str) -> ChatResult:
    """Ark / DeepSeek 共用的响应解析（OpenAI 兼容格式）。"""
    from ..errors import err as _err
    choices = data.get("choices") or []
    if not choices:
        raise _err("PROVIDER_BAD_REQUEST", "模型未返回 choices",
                   detail={"raw_keys": list(data)})
    msg = (choices[0].get("message") or {})
    raw_calls = msg.get("tool_calls") or []
    calls: list[ToolCall] = []
    for i, tc in enumerate(raw_calls):
        fn = tc.get("function") or {}
        args_raw = fn.get("arguments")
        if isinstance(args_raw, dict):
            args = args_raw
        else:
            try:
                args = json.loads(args_raw or "{}")
            except json.JSONDecodeError:
                args = {}
        calls.append(ToolCall(name=str(fn.get("name") or ""), arguments=args,
                              id=str(tc.get("id") or f"call_{i}")))
    usage = data.get("usage") or {}
    structured: dict = {}
    content = msg.get("content") or ""
    if content.strip().startswith("{"):
        try:
            parsed = json.loads(content)
            if isinstance(parsed, dict):
                for k in ("reply_type", "options", "optimized_prompt", "missing_fields",
                  "explanation"):
                    if k in parsed:
                        structured[k] = parsed[k]
                if not calls and parsed.get("reply"):
                    content = str(parsed["reply"])
        except json.JSONDecodeError:
            pass
    return ChatResult(
        reply=content,
        tool_calls=calls,
        input_tokens=int(usage.get("prompt_tokens") or 0),
        output_tokens=int(usage.get("completion_tokens") or 0),
        structured=structured,
        meta={"provider": provider_name, "model": model,
              "finish_reason": choices[0].get("finish_reason"), "stub": False},
    )


# ---------------- DeepSeek 官方通道 ----------------
DEEPSEEK_DEFAULT_BASE = "https://api.deepseek.com"
DEEPSEEK_DEFAULT_MODEL = "deepseek-flash"


def deepseek_config() -> dict:
    """读取 DeepSeek 独立配置（**不使用** ARK_API_KEY / ARK_BASE_URL）。"""
    return {
        "api_key": (os.getenv("DEEPSEEK_API_KEY") or "").strip(),
        "base_url": (os.getenv("DEEPSEEK_BASE_URL") or DEEPSEEK_DEFAULT_BASE).rstrip("/"),
        "model": (os.getenv("ASSISTANT_MODEL") or "").strip() or DEEPSEEK_DEFAULT_MODEL,
        "timeout": float(os.getenv("ASSISTANT_TIMEOUT_S", "30")),
    }


def _deepseek_post(url: str, body: dict, api_key: str, timeout: float) -> dict:
    """向 DeepSeek 官方发一次 POST；错误分类明确（Key/余额/模型/网络/超时）。

    不重试、不记录 Key、不记录请求体中的敏感内容。
    """
    import socket
    import urllib.error
    import urllib.request

    from ..errors import err

    req = urllib.request.Request(
        url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "ignore")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {}
        code = str((payload.get("error") or {}).get("code") or "")
        msg = str((payload.get("error") or {}).get("message") or raw[:200])
        low = f"{code} {msg}".lower()
        if e.code in (401, 403) or "authentication" in low or "invalid api key" in low:
            raise err("ASSISTANT_KEY_INVALID",
                      f"DeepSeek Key 无效或已撤销（HTTP {e.code}）：{msg[:120]}",
                      detail={"http": e.code, "provider": "deepseek"}) from e
        if e.code == 402 or "insufficient" in low or "balance" in low:
            raise err("ASSISTANT_INSUFFICIENT_BALANCE",
                      f"DeepSeek 账户余额不足（HTTP {e.code}）：{msg[:120]}",
                      detail={"http": e.code, "provider": "deepseek"}) from e
        if e.code == 404 or "model" in low and ("not found" in low or "not exist" in low):
            raise err("ASSISTANT_MODEL_UNAVAILABLE",
                      f"DeepSeek 模型不可用（HTTP {e.code}）：{msg[:120]}",
                      detail={"http": e.code, "provider": "deepseek"}) from e
        if e.code == 429:
            raise err("PROVIDER_RATE_LIMITED",
                      f"DeepSeek 限流（HTTP 429）：{msg[:120]}",
                      detail={"provider": "deepseek"}) from e
        if e.code == 400:
            raise err("PROVIDER_BAD_REQUEST",
                      f"请求参数被 DeepSeek 拒绝（HTTP 400）：{msg[:120]}",
                      detail={"provider": "deepseek"}) from e
        raise err("PROVIDER_UNAVAILABLE",
                  f"DeepSeek 返回错误 HTTP {e.code}：{msg[:120]}",
                  detail={"provider": "deepseek"}) from e
    except (socket.timeout, TimeoutError) as e:
        raise err("ASSISTANT_TIMEOUT",
                  f"DeepSeek 请求超时（>{timeout:.0f}s）；不会自动重试",
                  detail={"provider": "deepseek", "timeout_s": timeout}) from e
    except urllib.error.URLError as e:
        reason = getattr(e, "reason", e)
        if isinstance(reason, (socket.timeout, TimeoutError)):
            raise err("ASSISTANT_TIMEOUT",
                      f"DeepSeek 连接超时（>{timeout:.0f}s）；不会自动重试",
                      detail={"provider": "deepseek"}) from e
        raise err("ASSISTANT_NETWORK_ERROR",
                  f"无法连接 DeepSeek（{type(reason).__name__}）；请检查网络或代理",
                  detail={"provider": "deepseek"}) from e
    except json.JSONDecodeError as e:
        raise err("PROVIDER_UNAVAILABLE", "DeepSeek 返回非 JSON 响应",
                  detail={"provider": "deepseek"}) from e


class DeepSeekChatProvider:
    """DeepSeek 官方 API 适配器（OpenAI 兼容 /chat/completions）。

    - base_url 默认 https://api.deepseek.com；**不读 ARK_BASE_URL**
    - Key 只用 `DEEPSEEK_API_KEY`；**不读 ARK_API_KEY**
    - 首版非思考模式（body 显式 `thinking.type=disabled`）、非流式
    - 支持工具调用；不自动重试；不发送图片
    """

    name = "deepseek"

    def __init__(self, model: str | None = None, api_key: str | None = None,
                 base_url: str | None = None, timeout: float | None = None):
        cfg = deepseek_config()
        self.model = (model or cfg["model"] or DEEPSEEK_DEFAULT_MODEL).strip()
        self._api_key = (api_key if api_key is not None else cfg["api_key"])
        self.base_url = (base_url or cfg["base_url"]).rstrip("/")
        self.timeout = float(timeout or cfg["timeout"])

    def available(self) -> bool:
        return bool(self.model) and bool(self._api_key)

    def chat(self, messages, tools, tool_results=None, *, max_tokens=None) -> ChatResult:  # noqa: ANN001
        from ..errors import err
        if not self._api_key:
            raise err("ASSISTANT_CONFIG_INCOMPLETE",
                      "未配置 DEEPSEEK_API_KEY，请双击「配置DeepSeek.command」",
                      detail={"missing": ["DEEPSEEK_API_KEY"], "provider": "deepseek"})
        if not self.model:
            raise err("ASSISTANT_CONFIG_INCOMPLETE",
                      "未配置 ASSISTANT_MODEL（默认应写 deepseek-flash）",
                      detail={"missing": ["ASSISTANT_MODEL"], "provider": "deepseek"})

        body: dict = {
            "model": self.model,
            "messages": _to_openai_messages(messages),
            "temperature": float(os.getenv("ASSISTANT_TEMPERATURE", "0.3")),
            # 非思考模式（官方文档：thinking 默认开启，需显式关闭）
            "thinking": {"type": "disabled"},
        }
        max_tokens = max_tokens if max_tokens is not None else os.getenv("ASSISTANT_MAX_TOKENS")
        if max_tokens:
            body["max_tokens"] = int(max_tokens)
        if tools:
            body["tools"] = [{"type": "function",
                              "function": {"name": t.name,
                                           "description": t.description,
                                           "parameters": t.parameters or {"type": "object",
                                                                          "properties": {}}}}
                             for t in tools]
        data = _deepseek_post(f"{self.base_url}/chat/completions", body,
                              self._api_key, self.timeout)
        return _parse_chat_response(data, self.name, self.model)


_PROVIDER: LLMProvider | None = None


def get_provider() -> LLMProvider:
    """按环境变量返回单例。`ASSISTANT_PROVIDER=none` 为默认（不可用）。"""
    global _PROVIDER
    if _PROVIDER is not None:
        return _PROVIDER
    kind = (os.getenv("ASSISTANT_PROVIDER") or "none").strip().lower()
    model = (os.getenv("ASSISTANT_MODEL") or "").strip()
    if kind == "fixed":
        # 仅供自动化测试注入；生产环境不应配置成 fixed
        _PROVIDER = FixedReplyProvider(model=model or "fixed-test")
    elif kind == "ark":
        _PROVIDER = ArkChatProvider(model=model)
    elif kind == "deepseek":
        _PROVIDER = DeepSeekChatProvider(model=model or None)
    else:
        _PROVIDER = UnavailableProvider()
    return _PROVIDER


def set_provider(provider: LLMProvider | None) -> None:
    """测试注入入口（也可用于未来热切换）。"""
    global _PROVIDER
    _PROVIDER = provider


VERIFY_KIND = "verify_ok"


def _verified_record(p) -> dict | None:  # noqa: ANN001
    """查询是否存在针对**当前 provider+model** 的成功验证记录（落库，非内存）。"""
    try:
        from .. import db
        rows = db.query(
            "SELECT * FROM assistant_usage WHERE kind=? AND provider=? AND model=? "
            "ORDER BY created_at DESC LIMIT 1",
            (VERIFY_KIND, p.name, p.model or ""))
        return rows[0] if rows else None
    except Exception:  # noqa: BLE001  服务未初始化数据库时不阻塞状态查询
        return None


def provider_status() -> dict:
    p = get_provider()
    kind = (os.getenv("ASSISTANT_PROVIDER") or "none").strip().lower()
    if p.name == "fixed":
        state, note = "stub", "当前为测试替身，不代表真实模型能力"
    elif p.available() and _verified_record(p):
        state = "connected_verified"
        note = ("真实调用已验证（连接级：1 次短请求成功）；"
                "多轮对话质量与工具选择准确性尚未评测")
    elif p.available():
        state = "configured_unverified"
        note = "已配置语言模型，但尚未完成真实调用验证"
    elif kind == "deepseek":
        state = "misconfigured"
        missing = []
        if not (os.getenv("DEEPSEEK_API_KEY") or "").strip():
            missing.append("DEEPSEEK_API_KEY")
        if not (os.getenv("ASSISTANT_MODEL") or "").strip():
            missing.append("ASSISTANT_MODEL")
        note = "DeepSeek 配置不完整，缺少：" + "、".join(missing or ["未知项"])
    elif kind == "ark":
        state = "misconfigured"
        note = "已选择方舟适配器，但缺少 ASSISTANT_MODEL 或 ARK_API_KEY"
    else:
        state, note = "not_configured", "助手未配置语言模型，暂不可用；可直接使用工作台手动生成"
    return {
        "available": bool(p.available()),
        "state": state,
        "provider": p.name,
        "model": p.model,
        "vision": False,          # 看图能力未启用
        "streaming": False,       # 首版非流式
        "verified": state == "connected_verified",
        "base_url": (p.base_url if hasattr(p, "base_url") else None),
        "note": note,
    }
