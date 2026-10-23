# Codex 前端交接（v0.7，2026-10-23）

范围：把 v0.7 新增的**模板能力、多模型能力发现、生成前确认、结果追溯**接到现有工作台。
本轮后端已就绪并验证；**`frontend/` 代码本轮未被修改**，以下为稳定接口与示例。

> 口径纪律
> - `doubao-seedream-5-0-pro-260628` 与 `doubao-seedance-2-0-mini-260615` **尚未开通**（实测 `ModelNotOpen`），前端必须置灰，**不得默认选中**。
> - 鉴权**未实现**，`caller_id` 仅为预留字段。
> - 模板不是多步骤编排：一个模板 = 一次模型调用。
> - 不承诺固定输出尺寸（`9:16 + 720p` 实测输出 704×1248）。

## 1. 当前服务与基线

- 正式入口 `http://127.0.0.1:8100/`（前端 dist 与 API 同源）；旧页 `/legacy`。
- 已开通可用：图片 `doubao-seedream-5-0-flash-260915`、视频 `doubao-seedance-1-0-pro-fast-251015`。
- 后端日志：`logs/server-v07.log`。

## 2. 接口清单（v4 契约，纯增量）

| 接口 | 用途 | 变更 |
| --- | --- | --- |
| `GET /api/models` | 模型 + 能力 + 定价 + 生命周期 | 新增 `capabilities`/`pricing`/`lifecycle` |
| `GET /api/templates?kind=&category=` | 模板列表 | 新增 |
| `GET /api/templates/{id}?version=` | 模板详情 + 版本 + 快照 | 新增 |
| `POST /api/tasks` | 提交（支持模板） | 新增模板/外部关联字段与 `estimated_cost_cny` |
| `GET /api/tasks/{id}` | 任务详情 | 新增 `template_id/version`、`caller_id`、`concept_mode` |
| `POST /api/tasks/{id}/retry` | 重试 | 保留模板与字段 |
| 其余（assets/drafts/batches/usage/health） | 不变 | — |

## 3. 关键请求示例

### 3.1 模板列表

```bash
curl -s http://127.0.0.1:8100/api/templates | python3 -m json.tool | head -60
```

返回 4 项：`tpl_white_bg`、`tpl_scene_bg`、`tpl_product_motion_5s`、`tpl_season_theme`。
每项含 `fields`（渲染表单用）、`required_assets`、`default_params`、`compatible_model_ids`。

### 3.2 用模板提交（场景换背景）

```json
POST /api/tasks
{
  "kind": "image",
  "model_id": "doubao-seedream-5-0-flash-260915",
  "input_asset_id": "asset_d73231ab405540b68eb6",
  "template_id": "tpl_scene_bg",
  "template_version": 1,
  "template_fields": {
    "product_name": "焕亮精华液",
    "scene": "大理石台面",
    "keep_features": "瓶盖金色、标签文字不可改",
    "forbidden_elements": "不要出现人物"
  },
  "client_request_id": "ui-<uuid>"
}
```

响应：

```json
{"task_id":"task_…","status":"queued","queue_position":1,
 "estimated_cost_cny":0.12,
 "template_id":"tpl_scene_bg","template_version":1,
 "concept_mode":false}
```

**前端注意**：
- `client_request_id` 每张图一个 UUID（幂等，防重复点击）。
- **不要**再自己拼提示词：模板会组装（字段值 + 预设 + 保真基座）。若要覆盖，显式传 `params.prompt`。
- `template_fields` 的 `scene` 选「自定义」时，需同时传 `scene_custom`。

### 3.3 季节模板的两条路径

有参考图 → 正常图生图；**无参考图** → 必须填 `product_name`，否则 400。
成功时响应 `concept_mode: true`，**UI 必须显著标注**：`创意概念图，不代表真实商品外观`。

### 3.4 视频模板（5 秒动态）

```json
{
  "kind": "video",
  "model_id": "doubao-seedance-1-0-pro-fast-251015",
  "input_asset_id": "asset_cd91e2f9dda14123a622",
  "template_id": "tpl_product_motion_5s",
  "template_version": 1,
  "template_fields": {"product_name":"焕亮精华液","motion":"推近特写","duration":5,"ratio":"9:16"}
}
```

费用估算返回 `estimated_cost_cny ≈ 0.4536`；成功后按真实 token 重算（实测 ¥0.436）。

## 4. 模型选择器实现要点

```
GET /api/models
 → filter(m.provider === 'ark' && m.kind === currentKind)
 → 与模板兼容：m.id ∈ template.compatible_model_ids（模板模式）
 → 禁用条件：m.activation === 'not_opened'   // 置灰 + 提示"需在火山方舟控制台开通"
 → EOS 提示：m.lifecycle.status === 'eos_scheduled' → 显示 eos_at + migrate_to
```

切换模型时：保留提示词/参考图/比例；对被新模型 `capabilities` 不支持的项（如 2.0 mini 不支持 1080p）
标红并回退到该模型默认值。

## 5. 错误处理映射（新增）

| code | HTTP | 前端建议 |
| --- | --- | --- |
| `TEMPLATE_NOT_FOUND` | 404 | 模板已下线，刷新列表 |
| `TEMPLATE_VERSION_NOT_FOUND` | 404 | 回到最新版本 |
| `TEMPLATE_FIELD_REQUIRED` | 400 | 聚焦 `detail.field`，提示必填 |
| `TEMPLATE_FIELD_INVALID` | 400 | 高亮非法项（`detail.allowed`） |
| `TEMPLATE_DEPRECATED` | 409 | 提示改用新版本 |
| `MODEL_TEMPLATE_INCOMPATIBLE` | 400 | 用 `detail.compatible_models` 重选 |
| `MODEL_EOS` | 409 | 提示迁移目标，禁止提交 |
| `MODEL_NOT_OPENED` | 502 | 保留现有提示；**不计费**，实测确认 |

`CREATE_RESULT_UNKNOWN` 继续**隐藏直接重试**（可能已计费）。

## 6. 原图对比与追溯（前端数据来源）

- 原图：`task.input_asset_id` → `GET /api/assets/{id}/file`
- 产物：`task.output_asset_id` → `GET /api/assets/{id}/file`
- 追溯展示：`task.template_id` + `task.template_version` → `GET /api/templates/{id}?version=` 取当时快照
- 参数：`task.params`（含 `fields`、`prompt`、`generation_mode`、`size`/`duration`）
- 费用：`task.cost_cny`（估算）、`task.usage_json`
- 采纳/驳回：**后端暂无审核表**，本轮用前端本地状态或草稿保存；不要声称已持久化审核流。

## 7. 验收要求（前端交付时）

1. `A1–A11` 中前端相关项全部通过（见 `docs/验收/v0.7-验收清单.md` §4 D-1~D-9）。
2. 严格 TypeScript、ESLint、现有 11 条测试不回归、生产构建通过。
3. 桌面 1440 + 390px 无横向溢出。
4. 未开通模型置灰且有说明；概念图路径有显著标注。
5. 不新增付费调用；需要真实模板联调时**先申请预算**（阶段 C 未批准）。

## 8. 本轮已实测数据（可直接用于前端展示校验）

| 项 | 值 |
| --- | --- |
| flash 图生图 2048² | 16,443 ms / ¥0.12 / `asset_e17025715b204e94a0ea` |
| 1.0 pro fast 5s 9:16 720p | 33,504 ms / 103,818 tokens / ¥0.436 / `asset_0047709553924208adbd` / **704×1248** |
| pro 文生图/图生图 | `MODEL_NOT_OPENED`，¥0.00 |
| 2.0 mini 视频 | `MODEL_NOT_OPENED`，¥0.00 |
