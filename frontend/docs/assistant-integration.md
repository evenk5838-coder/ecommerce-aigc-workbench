# 助手前端基础层接入说明

日期：2026-10-25
工作分支：`codex/assistant-frontend-v1`，起始基线：`bbc20d6`。

## 本轮范围

本轮只增加接口类型、同源请求层、报价与批准保护，以及离线测试。沿用 React、Vite、TypeScript 和现有 `src/api.ts` 的 `ApiError`，没有新增依赖或状态管理框架。

没有修改 App、Home、现有布局、手动生成、草稿保存、素材库或任务中心；没有修改后端、密钥文件或业务数据，没有请求真实模型或生成接口。尚未完成 UI 接入、浏览器联调或真实模型能力验收，不能据此宣布助手前端阶段验收完成。

“G1 / G5”在本轮协作中的编号来源尚未明确，代码不使用该编号，也不推断其对应能力已实现。

## 文件与接口

| 文件 | 职责 |
| --- | --- |
| `src/assistant/types.ts` | 状态、会话、历史、brief、对话、报价、批准、用量和请求选项类型 |
| `src/assistant/api.ts` | `assistantApi` 与可注入 fetch 的 `createAssistantApi`，集中超时、取消和错误处理 |
| `src/assistant/guards.ts` | 可用性判断、未知回复降级、报价检查和批准提交参数校验 |
| `src/assistant/api.test.ts` | 模拟 fetch 的接口与异常测试 |
| `src/assistant/guards.test.ts` | 冻结方案、缺字段、费用、模式和批准匹配测试 |

请求入口：

- `status()`：读取服务端状态。
- `recoverSession(projectId)`：按草稿恢复最近会话、历史与 brief；`session(sessionId)` 读取会话状态。
- `chat(input)`：非流式对话；输入携带稳定草稿 ID，不在前端拼接历史。
- `setIntent(sessionId, intent, reason)`：用户显式切换模式。
- `brief(projectId)` / `saveBrief(projectId, brief)`：用户已确认商品信息。
- `quote({ session, payload })` / `getQuote(quoteId)`：服务端报价与报价详情。
- `confirm({ session, quote, userAction: 'confirm_generate' })`：仅在用户点击确认时调用；该函数不会提交任务。
- `submitApproved({ session, quote, approval })`：经校验后向 `POST /api/tasks` 提交冻结参数和批准 ID。
- `usage(sessionId?)`：分别读取助手用量和生成用量，不计算价格、余额或积分。

所有入口接收 `RequestOptions`（`signal`、`timeoutMs`）；没有重试选项或隐式重试。

## 可用性与真实能力边界

`assistantAvailability(status, responseProvider?)` 返回 `canChat`、`isStub`、`connectionVerified` 和 `capabilityVerification`。

- `available=true` 仍需要已知可用状态；未配置、配置错误、未知状态或测试替身都不能启用正式助手。
- `state=stub`、provider 名称为 `fixed`、`provider.stub=true` 均视为测试替身。对话接口收到替身回复时抛出 `ASSISTANT_TEST_PROVIDER`，不作为正式成功回复提供给 UI。
- `connected_verified` 只能作为连接证据，不代表多轮对话、工具选择、参数填写或拒答能力通过。
- 当前契约未提供能力评测字段，`capabilityVerification` 固定为 `not_reported`；后续不能根据可连接状态推导 B 已通过。
- 未识别的 `reply_type` 经 `safeReplyType()` 返回 `unknown`；UI 应只展示信息，不猜测操作。
- 首版没有看图能力，不根据素材名称、尺寸或 ID 补写商品事实。

本轮未执行真实 B 评测。其结果须以独立后端评测证据为准，仍待核验；这里的模拟 fetch 测试不构成真实能力证明。

## 报价与提交保护

UI 在显示确认按钮或“转为直接操作”前，应调用：

```ts
const readiness = inspectQuote(quote, session);
```

分别读取 `canConfirm` / `confirmationIssues` 和 `canTransferToManual` / `manualIssues`。每项 issue 都有可展示的中文原因；字段未齐全时保留现有草稿和对话，不自动替换为手动任务。

以下任一条件会同时禁用确认和转手动：

- 未明确进入服务端 `generate` 模式；`optimize_only`、`auto` 或未知模式不允许生成操作。
- 会话缺失、处于 `awaiting_facts` 或未知状态。
- 报价缺编号、冻结方案标识、有效的 Unix 秒有效期，或已经过期。
- 缺少费用字段，费用为 null、非数字、负数或非有限值；两个费用字段不一致也不能使用。服务端明确返回的数字 0 合法。
- 缺少非空的 `resolved_params.prompt`、生效规格或完整 `submit_payload`。
- 模板请求缺少被冻结的模板版本和字段。
- 报价明确属于别的会话或草稿。

转手动另要求 `manual_params` 齐全，且与报价冻结参数、生效规格一致；只缺手动映射时，可以继续展示完整报价供用户确认，但不能转手动。

仅优化结果的复制或套用提示词是独立流程，不需要报价；不应把套用操作变为确认生成。

`buildApprovedSubmission(quote, approval, session)` 只校验并构造请求，不发起网络调用。它核对冻结内容完整性，以及批准的报价 ID、会话、草稿、方案 hash、批准有效期、次数限制、费用和完整提交参数，然后只增加 `approval_id`。不生成新提示词、不加默认参数、不重新计算 hash 或费用、不替换模板版本、不创建新的请求 ID，也不调用旧的 `buildGenerationInput`。

有效期分两个阶段：用户发起新确认时，报价必须尚未过期；后端已经签发批准后，提交以批准自身的有效期为准，原报价在网络等待期间过期不使该批准失效。确认后仍检查冻结内容和批准匹配，不放宽任何参数、会话或费用校验；批准过期则停止提交。

前端 guard 是操作保护，不能替代后端的权限、报价有效性、幂等与预算校验。会话切换或方案改变后不能沿用旧批准；尚未获批且已过期的报价需重新报价再确认。没有“确认失败后自动改走手动提交”的退路。

## 当前契约缺口的降级方式

在本次基线中，聊天中的 quote 是摘要；现有 `GET /assistant/quote/{id}` 也可能没有公开返回最终 `resolved_params.prompt`。基础层保留这些字段的缺失状态，`inspectQuote` 会阻止确认和转手动。不能解析内部存储 JSON、从自然语言回复抽取 prompt，或在前端补写模板内容来绕过。

报价详情有的返回 `id`，有的返回 `quote_id`；`quoteId()` 只读取这两个已知编号字段，不改写原对象。

后端 `chat.context` 的引用素材传递和 B 真实能力评测仍由后端核对。本轮请求层按契约传参，不声称其已在模型中生效。

## 超时、取消与恢复

默认完整请求超时为 120 秒，覆盖响应体读取。当前后端可能执行多轮模型和工具调用，因此没有复用旧 API 层的 25 秒超时。若后端调整总执行预算，需同步核对这一等待时限。

- 超时抛出 `ASSISTANT_REQUEST_TIMEOUT`。
- 用户取消等待抛出 `ASSISTANT_REQUEST_ABORTED`；只停止浏览器等待，不代表后端任务或模型调用已取消。
- 网络中断抛出 `ASSISTANT_NETWORK_ERROR`。
- 后端错误保留 `ApiError.code/status`；畸形成功响应抛出 `ASSISTANT_RESPONSE_INVALID`。
- 请求完成后清除计时器和取消监听；不自动重发 chat、quote、confirm 或任务提交。

超时或断线后，UI 应先恢复会话、读取报价或检查任务状态，再由用户决定下一步。尤其不能把超时当成“服务端一定没有受理”，不能自动重新确认或换一个批准提交。

## 后续 UI 接入时保留现有数据

- 助手入口默认可选，不阻止现有手动生成。
- 按已保存的 draft ID 绑定会话；不能用项目名称或通用 default 替代稳定 ID。
- 用户“另存为新草稿”后切换助手会话上下文，旧草稿、对话和报价不迁移冒用。
- 保留现有 `material-studio-project-v1`、服务端草稿版本冲突处理和本地备份，不清除或重写它们。
- 只有用户主动套用时才更新手动表单，已有内容不能被助手回复自动覆盖。
- 助手生成使用冻结提交路径；既有手动生成继续使用原路径，不能混用批准记录。
- 助手用量与生成费用分开展示；缺失费用显示未提供，不按零费用处理，所有费用均为估算、非账单。

## 离线检查

在已有依赖的前提下，从 `frontend/` 执行：

```bash
npm run typecheck
npm run lint
npm run test -- src/assistant --configLoader runner --no-cache --no-fsModuleCache
```

若需要运行全部前端测试，移除 `src/assistant` 过滤条件。测试使用注入的模拟 fetch，不访问后端或模型。

本工作副本借用主目录 node_modules 的符号链接；该链接不应提交，也不应写入共享依赖。本次测试另外使用临时 Vite 配置：

```js
export default {
  cacheDir: '/private/tmp/material-assistant-vite-cache',
  test: { cache: false, fsModuleCache: false },
  optimizeDeps: { noDiscovery: true, include: [] },
};
```

配置存于 `/private/tmp/material-assistant-vitest.config.mjs` 后执行：

```bash
node node_modules/vitest/vitest.mjs run --config /private/tmp/material-assistant-vitest.config.mjs --configLoader runner --no-cache --no-fsModuleCache
```

本轮执行结果：类型检查通过，全部前端 Lint 通过；助手新增 57 条测试通过，全部前端 68/68 通过；独立生产构建通过。测试覆盖冻结参数不变、缺字段禁用、真实零费用与 null 区分、仅优化权限、批准不匹配、报价与批准的独立有效期、确认响应跨报价截止时间、响应体超时、取消清理和不重试。未启动页面，未运行真实 B 评测，未对正式服务或数据做联调。
