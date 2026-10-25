# 助手 LLM 接入说明（阶段一补：DeepSeek 官方通道 + 方舟通道）

日期：2026-10-24｜状态：**DeepSeek 适配器已实现并通过 17 项离线测试；真实连接待用户点「验证连接」**

> 关键事实
> - **选型已定：DeepSeek 官方 API**（不通过火山方舟）。官方文档核对：`base_url=https://api.deepseek.com`、
>   模型名 **`deepseek-flash`**、工具调用为 OpenAI 兼容格式、**thinking 默认开启需显式关闭**。
> - Key 用**独立的 `DEEPSEEK_API_KEY`**；方舟的 `ARK_API_KEY` / `ARK_BASE_URL` 完全保留给图片与视频。
> - 保存 Key **不会**发起任何请求；只有点「验证连接」并确认后才发 **1 次**最小请求。
> - 未完成真实调用前，`/api/assistant/status` 的 `verified` 恒为 `false`（state=`configured_unverified`）。

## 1. 用户只需三步

1. **双击 `配置DeepSeek.command`** → 弹窗粘贴 Key → 保存（自动写好 provider / 地址 / 型号，自动重启服务）。
2. 看弹窗结果（成功/失败原因 + 下一步）。
3. 需要确认能连通时，**双击 `验证DeepSeek连接.command`** → 弹窗告知预计消耗 → 确认后只发 1 次短请求 → 弹窗给出分类结果。

不需要改代码、不需要填模型 ID、不需要复制终端命令、不需要手动杀进程。

## 2. 配置项（由工具自动写入，无需手填）

| 变量 | 值 | 说明 |
| --- | --- | --- |
| `ASSISTANT_PROVIDER` | `deepseek` | 选择 DeepSeek 通道 |
| `ASSISTANT_MODEL` | `deepseek-flash` | 官方当前模型名（legacy `deepseek-v4-flash` 仍被接受） |
| `DEEPSEEK_BASE_URL` | `https://api.deepseek.com` | 官方 OpenAI 格式地址 |
| `DEEPSEEK_API_KEY` | `<你的 Key>` | **独立**，不读 `ARK_API_KEY` |
| `ASSISTANT_TIMEOUT_S` | `30` | 单次调用超时 |

可选（默认已够用）：`ASSISTANT_TEMPERATURE=0.3`、`ASSISTANT_MAX_TOKENS`（验证时自动设 32）、`ASSISTANT_MAX_TOOL_ROUNDS=3`。

> 仍支持 `ASSISTANT_PROVIDER=ark` 作为备用通道（复用 `.env` 已有的 `ARK_API_KEY`）。

## 3. 模型与价格（DeepSeek 官方文档，2026-10-24 核对）

| 项 | 值 |
| --- | --- |
| 模型名 | **`deepseek-flash`**（官方注明：legacy `deepseek-v4-flash` 仍接受，实际由 DeepSeek-V4.1-Flash-1017 服务） |
| 备选 | `deepseek-v4-pro`（更强更贵，首版不用） |
| base_url | `https://api.deepseek.com`（OpenAI 格式）；Anthropic 格式为 `/anthropic`，本项目不用 |
| 对话路径 | `/chat/completions` |
| 思考模式 | **默认开启**；非思考需请求体带 `"thinking": {"type": "disabled"}`（本项目首版固定关闭） |
| 工具调用 | OpenAI 兼容（`tools` / `tool_calls` / `role=tool`）；strict 模式需 `/beta`，本项目不用 |
| 上下文 / 最大输出 | 1M / 最大 384K |
| 单价（普通，按 1M tokens） | 输入（缓存命中）$0.003–0.006；输入（未命中）$0.15–0.30；输出 $0.6–1.2（off-peak 为峰谷半价） |
| 峰值时段 | UTC 周一至周五 01:00–04:00、06:00–10:00（其余为 off-peak） |

**选择理由**：用户已有 DeepSeek 官方 Key 与额度；`deepseek-flash` 单价低、支持工具调用，与助手"短对话 + 少量工具调用"的负载匹配；不需要新增采购流程。

> 与方舟的关系：图片/视频继续走方舟（`ARK_API_KEY`），助手走 DeepSeek（`DEEPSEEK_API_KEY`），两套互不影响。

## 4. 连接验证（唯一花钱的一步）

入口：**双击 `验证DeepSeek连接.command`**（也可 CLI：`<venv>/bin/python backend/tools_deepseek.py verify`，需 `CONFIRM=1`）

| 项 | 规则 |
| --- | --- |
| 请求次数 | **1 次**，无工具、无重试 |
| 提示词 | 固定极短（"只回复两个字：可用"），`max_tokens=32` |
| 预算上限 | **≤ ¥0.01**（按官方峰值单价 + 7.2 汇率保守估算约 ¥0.0004；建议按 ¥0.01 封顶） |
| 保存 Key 时 | **不联网**，仅写配置 |
| 结果分类 | Key 无效 / 余额不足 / 模型不可用 / 网络不通 / 超时 / 请求参数被拒 / 限流，各自独立提示 |

错误分类与提示（不会一律说成"Key 有问题"）：

| 错误码 | HTTP | 弹窗标题 | 用户该做什么 |
| --- | --- | --- | --- |
| `ASSISTANT_KEY_INVALID` | 401 | Key 无效 | 确认是 DeepSeek 官方 Key、未撤销；重新配置 |
| `ASSISTANT_INSUFFICIENT_BALANCE` | 402 | 余额不足 | Key 有效，去平台充值 |
| `ASSISTANT_MODEL_UNAVAILABLE` | 404 | 模型不可用 | 核对官方模型名（应为 `deepseek-flash`） |
| `ASSISTANT_TIMEOUT` | 504 | 请求超时 | 检查网络/代理；未自动重试 |
| `ASSISTANT_NETWORK_ERROR` | 503 | 网络不通 | 检查网络/代理/防火墙 |
| `PROVIDER_RATE_LIMITED` | 429 | 触发限流 | 稍后再试 |
| `PROVIDER_BAD_REQUEST` | 400 | 请求参数被拒 | 反馈开发（可能模型名或参数不符） |
| `ASSISTANT_CONFIG_INCOMPLETE` | 400 | 配置不完整 | 重新双击「配置DeepSeek.command」 |

**验证通过 ≠ 能力验收通过**：它只证明"能连通并返回"，多轮对话质量、工具选择准确性、拒答行为仍需单独评测。

## 5. 真实连接验证结果（2026-10-24，已完成 1 次）

| 项 | 值 |
| --- | --- |
| 结果 | ✅ 成功 |
| provider / model | `deepseek` / `deepseek-flash` |
| base_url | `https://api.deepseek.com` |
| 回复 | `可用` |
| tokens | 输入 9 / 输出 1 |
| 耗时 | 2935 ms |
| 请求数 | **1 次**（无工具、无重试） |
| 估算成本 | 约 **¥0.000028**（上限 ¥0.01，未超） |
| 状态跃迁 | `configured_unverified` → **`connected_verified`**（已落库，重启后保持） |

证据：`docs/evidence/deepseek接入-20261024/真实连接验证.json`
离线证据：`docs/evidence/deepseek接入-20261024/离线测试结果.json`（17/17）

**这次验证只证明"能连通并返回"**，以下四项仍未验证：
多轮对话质量、模型自主选工具与填参准确性、结构化字段遵循度、违规拒答行为。

### 配置勘误（同日修复）

首次双击配置时发现：`osascript` 弹窗返回值格式与我原先的 `sed` 解析不匹配，
导致 `.env` 里写入了 `button returned:…, text returned:sk-…` 的污染值。已修复：

1. 取 Key 改为 `return text returned of r`（只取输入框内容）；
2. `apply_config()` 增加格式校验：含 `button/text returned`、空白、或不符合 `[A-Za-z0-9_-]{16,200}` 一律拒绝写入；
3. 已从污染值中恢复真实 Key 并重写 `.env`（保留 `ARK_API_KEY` 等其它配置，权限 600）。

## 5. 已知限制（接入前必须知道）

| 限制 | 说明 |
| --- | --- |
| 不看图 | `vision=false`：模型只收到文本与素材元数据，看不到商品图 |
| 不流式 | `streaming=false`：一次性返回，前端显示"思考中" |
| 不自动重试 | 限流/网络失败直接抛出，由用户决定 |
| 工具不便宜 | 每次工具调用都会增加 token 消耗，已有单轮 3 次上限 |
| 结构化字段依赖模型配合 | 模型若只返回纯文本，`options/optimized_prompt` 可能为空，前端需降级为纯文本展示 |
| 无额度体系 | `verified` 之前不得展示余额；积分账本未实现 |

## 6. 适配器实现要点（给 Codex / 复核用）

- `backend/app/assistant/provider.py::ArkChatProvider`
  - 复用 `providers_ark._request("POST", "/chat/completions", body)` → 继承统一的 Key 读取与错误映射
    （`AUTH_FAILED` / `MODEL_NOT_OPENED` / `PROVIDER_RATE_LIMITED` / `PROVIDER_UNAVAILABLE`）
  - 请求体：`model` / `messages` / `tools`（OpenAI function 形状）/ `temperature` / 可选 `max_tokens`
  - 响应解析：`choices[0].message.content`、`tool_calls`（含 `id` 与 JSON 参数）、`usage.prompt_tokens/completion_tokens`
  - 模型若返回约定 JSON（`reply_type/options/optimized_prompt/missing_fields`）则解析为 `structured`，
    `reply` 取其中的 `reply`
  - **不重试**、不发图片、不落 Key
- **DeepSeek**：`DeepSeekChatProvider`
  - 独立 `_deepseek_post()`（自带 urllib 实现，**不复用方舟地址/Key**），错误按 401/402/404/429/超时/网络分类
  - 请求体固定 `thinking: {"type": "disabled"}`（非思考）、`stream` 不发送（非流式）
  - 与 Ark 共用 `_parse_chat_response()`（tool_calls/usage/结构化 JSON）
- 消息规范化：`_to_openai_messages()` 把内部 `context` 角色折叠为 system 消息
- 工具回传：`service.chat` 生成 `assistant.tool_calls` + `role=tool(tool_call_id)`，真实模型据此继续或停止
- 测试：
  - `backend/tools_deepseek_offline_tests.py`：**17 项**（URL/Key 来源、非思考参数、context 折叠、
    tool_calls 解析、工具结果回传、配置不完整、5 类错误分类、Ark 未受影响、配置写入安全、手动路径不回归），**零真实调用**
  - `backend/tools_assistant_stage1_tests.py`：41 项（含 Ark 契约 5 项）
