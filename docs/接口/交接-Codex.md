# 交接包 · Codex 前端接入（v3 · 云端图生图）

> 2026-10-23状态同步：下文保留后端交接时的接口与建议。后续Codex已完成统一工作台、图生图入口及真实2K联调，见`docs/evidence/前端/验收记录.md` v0.6；操作手册现为v0.4，并发说明现为v0.3.1。当前完整状态见`docs/项目状态.md`，不要将历史“待前端接入”理解为当前仍未完成。

> **2026-10-22 17:35 增量**：后端已支持**云端图生图**，接口为兼容增量。
> 1) 图片任务新增 `params.generation_mode`：`text_to_image`（默认）/ `image_to_image`；
> 2) 参考图仍用顶层 `input_asset_id`，**只有 `image_to_image` 才会真的传给模型**；
> 3) 新增错误码 `REFERENCE_IMAGE_REQUIRED` / `ASSET_NOT_IMAGE` / `INVALID_REFERENCE_IMAGE`；
> 4) 不传 `generation_mode` 的旧请求行为完全不变；旧请求带了参考图会回显 `params.ignored_reference=true`（前端可提示"本次按文生图执行"）。
> 完整契约见 `docs/接口/接口契约.md` §3。

## 图片任务的两个准确示例

```jsonc
// A. 文生图（旧请求不变）
{ "kind":"image", "model_id":"doubao-seedream-5-0-flash-260915",
  "params": { "prompt":"白色背景护肤品主图，柔和棚光，留白", "size":"1024x1024" } }

// B. 图生图（新增）
{ "kind":"image", "model_id":"doubao-seedream-5-0-flash-260915",
  "input_asset_id":"asset_…",
  "params": { "generation_mode":"image_to_image",
              "prompt":"保留图中白色瓶子的瓶身/瓶盖/液滴图标与空白标签区域，仅把背景替换为浅灰大理石台面，保留构图留白",
              "size":"1024x1024" } }
```

**前端交互约定（建议）**
- 用途入口（白底商品图 / 氛围场景图 / 自定义）只切换提示词预设与默认参数，**不新增接口**。
- 有参考图 + 用户意图为"改背景/改场景" → 必须显式传 `generation_mode: "image_to_image"`；只选图不传该字段时后端按文生图执行。
- 显示费用前请注明"估算，非账单"；图片 0.12 元/张，视频按 token（4.20 元/百万 token）。
- 一致性没有保证：**不要**把 SKU 分组描述为"保证同款一致"。
- **风险提示（已实测）**：提示词若写了参考图里没有的商品，模型会**凭空生成不存在的商品**（实测：单瓶参考图 + "保留三件产品" → 输出三件）。建议 UI 在参考图旁标注"描述中请勿加入图中没有的商品"，并且不要把结果直接当"商品一致"展示。

## v2 内容（历史）

> **2026-10-22 15:55 变更**：形态改为「本地工作台 + 云端推理」。接口保持兼容，新增字段为增量：
> `/api/models` 加 `provider`；`/api/tasks/{id}` 加 `remote_task_id / remote_status / usage_json / cost_cny`；
> 新增状态 `remote_queued / remote_running`；新增 `/api/usage`；取消响应新增 `phase: cloud_queued|cloud_running` 与 `cloud_canceled`。
> **UI 必须显示「本地工作台 + 云端推理」**，并展示单次费用/当日花费（来自 `/api/usage`）。
> 细节见 `docs/接口/接口契约.md` §5.5。
> **已验证可用模型（可直接展示）**：图片 `doubao-seedream-5-0-flash-260915`（1024²/864×1152/720×1280，11–23s，¥0.12）；视频 `doubao-seedance-1-0-pro-fast-251015`（5s/704×1248/24fps，≈42s，≈¥1.56）。
> 未开通模型请前端置灰并显示 `MODEL_NOT_OPENED`，不要假装可选。

> 交接时间：2026-10-22 14:40｜后端现状：骨架可运行，**模型权重下载中**（SD1.5 约 60%），图片真实推理尚未执行
> 交接原则：按你的要求，**模型不可用不阻塞前端交接**。以下接口均已用真实请求验证，可直接接入。

## 1. 启动与地址

```bash
cd backend
APP_PORT=8100 /private/tmp/ecom-local-probe-20261022/venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8100
```

- 服务地址：**http://127.0.0.1:8100**（8000 被本机其他进程占用，请勿写死 8000）
- 最小验收页：`http://127.0.0.1:8100/`（`backend/static/index.html`，可作交互参考，不要直接当正式前端复用）
- OpenAPI：`http://127.0.0.1:8100/docs`
- 环境变量：见 `.env.example`（`APP_PORT` / `APP_CONCURRENCY` / `APP_MAX_UPLOAD_MB` / `APP_MPS_BUDGET_RATIO`）
- 测试专用：`APP_TEST_FAKE=1` 会额外注册 `fake-image` 假模型（仅用于流程联调，**不得出现在正式 UI 的模型列表里**，可按 `id == "fake-image"` 过滤）

## 2. 可用接口样例（真实响应）

### GET /api/health
```json
{"status":"ok","version":"0.1.0","uptime_s":10,"recovery":{"interrupted":1,"queued":2},
 "tasks":{"succeeded":4,"failed":2}}
```

### GET /api/system/status
```json
{"memory":{"budget_mb":10307,"mps_alloc_mb":0,"mps_driver_mb":0,"sys_used_mb":6014},
 "loaded_model":null,"queue":{"queued":0,"active":0},"concurrency":1}
```

### GET /api/models
```json
{"items":[{"id":"sd15","name":"Stable Diffusion 1.5 (fp16, diffusers)","kind":"image",
 "license":"creativeml-openrail-m","weights_mb":2140,"downloaded":false,"loaded":false,
 "default_size":512,"max_size":768,"default_steps":25,"default_frames":null,"note":"备选模型；本次用于链路验证与出图"}]}
```
> `downloaded=false` 时前端应置灰并显示 `note`；**不要伪装成可用**。

### POST /api/assets（multipart，字段 `file`）
```json
{"asset_id":"asset_f8022f12bca743da831d","sha256":"999a64…","width":640,"height":480}
```
错误：`415 UPLOAD_REJECTED`（类型/大小）

### POST /api/tasks
```json
// 请求
{"kind":"image","model_id":"sd15",
 "params":{"prompt":"白色背景商品主图","negative_prompt":"","width":512,"height":512,"steps":25,"seed":-1},
 "input_asset_id":null,"batch_id":null,"client_request_id":null}
// 响应
{"task_id":"task_380fb10df04e43de91ac","status":"queued","queue_position":1,"estimated_memory_mb":10307}
```
视频任务示例（`kind=video` 时必须带首帧素材）：
```json
{"kind":"video","model_id":"i2vgen-xl","input_asset_id":"asset_…",
 "params":{"prompt":"缓慢推近","width":320,"height":512,"frames":8,"fps":8,"steps":25,"seed":-1,"probe_spec":true}}
```
缺少 `input_asset_id` 时真实返回：
```json
{"error":{"code":"VALIDATION_FAILED","message":"视频任务需要 input_asset_id（首帧图）"}}
```

### GET /api/tasks/{id}（含尝试历史）
```json
{"id":"task_…","kind":"image","status":"failed","stage":"failed","progress":0.0,
 "model_id":"sd15","params":{"prompt":"…","width":512,"height":512,"steps":4},
 "load_ms":null,"latency_ms":3,"peak_mem_mb":6126,
 "error_code":"MODEL_NOT_DOWNLOADED","error_message":"模型 sd15 权重未就位（目录：…）",
 "attempts":1,"attempts_log":[{"attempt_no":1,"result":"failed","error_code":"MODEL_NOT_DOWNLOADED"}]}
```

### 图片参数补充（已实测可用）
```json
{"kind":"image","model_id":"sd15","params":{
  "prompt":"…","negative_prompt":"…","width":512,"height":512,"steps":20,"seed":-1,
  "precision":"fp32",                 // 默认 fp32（fp16 曾出全黑图）
  "init_image_asset_id":"asset_…",    // 可选：传入则走图生图（商品图 → 场景图）
  "strength":0.6                      // 图生图强度 0–1，越大改动越多
}}
```
实测：文生图 512×512/20步 ≈26.4s；图生图 512×512/20步/0.6 ≈26.7s。

### POST /api/tasks/{id}/cancel
| 场景 | 真实响应 |
| --- | --- |
| 排队中 | `{"result":"canceled","phase":"queued"}` |
| 运行中（已实测） | `{"result":"cancel_requested","phase":"running"}` → 最终 `status="canceled"`（本次实测延迟 1219ms） |
| 终态 | `409 {"error":{"code":"CANCEL_REJECTED_PHASE","detail":{"status":"failed"}}}` |

UI 文案要求：运行中取消必须提示「已请求停止，将在当前步/帧结束后停止」，**不得写成"立即停止"**。

### POST /api/tasks/{id}/retry
```json
{"params":{"width":384,"height":384}}   // 可选：覆盖参数（如 OOM 后降分辨率）
```
响应：`{"task_id":"task_new…","retry_of":"task_old…","status":"queued"}`
实测：失败任务带注入错误 → 覆盖参数后重试 → `succeeded`（`retry_of` 正确指向原任务）。

### 批量
```json
// POST /api/batches
{"name":"主图批量","items":[{"kind":"image","model_id":"sd15","params":{"prompt":"…","width":512,"height":512}}]}
// 响应
{"batch_id":"batch_c8bf702c7d8948209718","task_ids":["task_5d7bdc81…","task_187cb954…"]}
```
`GET /api/batches/{id}` 返回 `tasks[]`（id/status/kind/progress/stage/error_code）。

## 3. 草稿数据结构（画布）

```json
// POST /api/drafts  {"name":"画布草稿","content":{...}}
// PATCH /api/drafts/{id} {"content":{...},"base_version":3}
{"draft_id":"draft_c9496eb26def4e04af66","version":1}
```

`content` 建议结构（后端不校验内部字段，原样存取，前端自定义即可）：
```json
{
  "canvas": {
    "width": 1080,
    "height": 1440,
    "background": "#ffffff",
    "layers": [
      {"id":"lyr_1","type":"image","assetId":"asset_…","x":0,"y":0,"w":1080,"h":1080,
       "opacity":1,"rotation":0,"z":1},
      {"id":"lyr_2","type":"text","text":"卖点标题","x":80,"y":120,"fontSize":48,
       "color":"#111111","align":"left","z":2},
      {"id":"lyr_3","type":"shape","shape":"rect","x":0,"y":1100,"w":1080,"h":340,
       "fill":"#f5f5f5","z":0}
    ],
    "clips": [
      {"id":"clip_1","assetId":"asset_video_…","startMs":0,"durationMs":2000,"fadeInMs":200}
    ]
  },
  "selection": ["lyr_1"],
  "meta": {"zoom": 0.5, "savedBy": "canvas-v1"}
}
```
版本冲突：`PATCH` 带过期 `base_version` → `409 DRAFT_VERSION_CONFLICT`（前端提示「内容已在别处更新，请刷新」）。
每次保存写 `draft_versions` 快照，历史不覆盖。

## 4. 状态与轮询约定

- 任务状态：`queued → loading → running → postprocess → succeeded`；旁支 `failed / cancel_requested / canceled / interrupted`。
- 进度：`progress` 0–1 + `stage` 文本；建议 1.5–2s 轮询 `GET /api/tasks?limit=20`（无 SSE 实现，**不要假设有推送**）。
- 视频产物预览：`GET /api/assets/{id}/file` 支持 Range，可直接 `<video controls>`。
- 任务耗时/内存：`latency_ms` / `load_ms` / `peak_mem_mb` 已入库，任务中心建议展示（这是验收证据之一）。

## 5. 已知缺口（前端请按此规避）

| 缺口 | 影响 | 前端应对 |
| --- | --- | --- |
| **权重下载未完成** | 图片/视频推理当前返回 `MODEL_NOT_DOWNLOADED` | 模型置灰 + 展示 note；不要把该状态当成"功能可用" |
| 视频模型完全未就位 | 视频能力 0 证据 | UI 可先建流程，但需明示"视频未验证" |
| 无断点续跑 | 中断任务只能从头重试 | 「重试」按钮文案写"从头重试"，不要写"续跑" |
| 无 SSE | 只有轮询 | 不要依赖事件流 |
| 无鉴权/多用户 | 单机单用户，监听 127.0.0.1 | 不做登录页 |
| 编辑能力未实现 | 画布仅有草稿存取，无局部重绘/扩图接口 | 编辑按钮先置灰或标注"未开放" |
| 无删除接口 | 素材/任务无删除 | 不做删除按钮 |
| `fake-image` 测试模型 | 仅 `APP_TEST_FAKE=1` 出现 | 正式环境启动时不带该变量 |

## 6. 契约变更流程

- 我改接口必须同步改本文件并在 `docs/项目状态.md` 记录。
- 前端需要的字段若后端没有，先在本文档登记，不要自行 mock 成既定事实。
