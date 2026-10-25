# Codex 前端交接 v0.7.1（2026-10-24）

对应 PRD v0.7.1、接口契约 v5、验收清单 v0.7（修订）。本文替代 `交接-Codex-v0.7.md` 的对应段落。

> **口径纪律**
> - `doubao-seedream-5-0-pro-260628`、`doubao-seedance-2-0-mini-260615` **仍未开通**；前端必须置灰，**不得默认选中**，也**不得**因本轮修复把它们标为可用。
> - 鉴权**未实现**；`caller_id` 仅为预留字段，不能作为权限依据。
> - 模板＝一次模型调用，不是多步骤编排。
> - 不承诺固定输出尺寸（`9:16 + 720p` 实测 704×1248）。
> - `cost_cny` 是**应用估算**，不得展示为“供应商账单”；失败任务 `cost_cny=null` 不代表“账单为零”。
> - **不要**修改 `frontend/` 之外的约定；后端本轮已完成修复，前端按本文接入即可。

## 1. 本轮后端修复了什么（影响前端接入方式）

| # | 修复 | 对前端的影响 |
| --- | --- | --- |
| 1 | 新增 `POST /api/tasks/estimate`（无副作用） | **生成前确认改为调它**；不能用 `POST /api/tasks` 的响应做确认 |
| 2 | 模板自定义字段（`*_custom`）被保留 | 表单可直接提交 `scene_custom`/`motion_custom`/`theme_custom`，无需前端拼提示词 |
| 3 | 视频模板字段映射到参数 | `duration`/`ratio` 选什么就生成什么；需用 `effective` 回填 |
| 4 | 概念图走 `text_to_image` | 季节模板无图时不再报“缺参考图”，改要求商品名 |
| 5 | 模型到期服务端拦截 | 到期后 `MODEL_EOS`；前端需按 `lifecycle` 提示并禁用 |

## 2. 生成流程（前端必须按此顺序）

```text
① 填表单（模板字段 / 自由创作）
② POST /api/tasks/estimate   ← 只报价，不建任务
   → 用响应 effective 回填控件、展示 estimated_cost_cny、展示 model_lifecycle 提示
③ 用户点「确认生成」
④ POST /api/tasks            ← 真正入队（此时才可能产生费用）
⑤ GET /api/tasks/{id} 轮询
```

**反例（不要这样做）**：
- ❌ 先 `POST /api/tasks` 再看费用（会先入队、可能已产生调用）。
- ❌ 自己在前端算价格（像素档/token 公式以后端为准）。
- ❌ 用 `default_params` 回填 `duration`/`ratio`（会覆盖用户选择，造成“选了 10 秒显示 5 秒”）。

## 3. 新接口：POST /api/tasks/estimate

请求体与 `POST /api/tasks` **完全一致**。

```json
{
  "kind": "video",
  "model_id": "doubao-seedance-1-0-pro-fast-251015",
  "input_asset_id": "asset_cd91e2f9dda14123a622",
  "template_id": "tpl_product_motion_5s",
  "template_fields": {"motion": "缓慢旋转", "duration": 10, "ratio": "1:1"}
}
```

响应（真实返回，2026-10-24 实测）：

```json
{
  "kind": "video",
  "model_id": "doubao-seedance-1-0-pro-fast-251015",
  "template_id": "tpl_product_motion_5s",
  "template_version": 2,
  "template_name": "5 秒商品动态展示",
  "concept_mode": false,
  "estimated_cost_cny": 0.9072,
  "effective": {"duration": 10, "ratio": "1:1", "resolution": "720p", "generate_audio": false},
  "params_preview": {"prompt": "以首帧图中的商品为准，……", "fields": {"motion": "缓慢旋转", "duration": 10, "ratio": "1:1"}},
  "model_lifecycle": {
    "status": "eos_scheduled",
    "eos_at": "2026-11-24T14:00:00+08:00",
    "eom_at": "2026-09-24T10:00:00+08:00",
    "migrate_to": "doubao-seedance-2-0-mini-260615",
    "action": "关停",
    "announce_url": "https://www.volcengine.com/docs/82379/1350667",
    "seconds_remaining": 2686494,
    "migration_available": true
  },
  "warnings": [],
  "side_effects": {"task_created": false, "queued": false, "model_called": false, "budget_deducted": false}
}
```

错误示例：

```json
// 400：模板字段缺失
{"error":{"code":"TEMPLATE_FIELD_REQUIRED","message":"请填写目标场景","detail":{"field":"scene"}}}

// 400：自定义为空
{"error":{"code":"TEMPLATE_FIELD_REQUIRED",
 "message":"目标场景选择「自定义」时必须填写具体内容",
 "detail":{"field":"scene_custom","parent":"scene"}}}

// 400：最终参数超出模型能力
{"error":{"code":"MODEL_CAPABILITY_MISMATCH",
 "message":"模型 doubao-seedance-2-0-mini-260615 不支持所选参数：resolution=1080p",
 "detail":{"template_id":"tpl_product_motion_5s",
           "problems":[{"field":"resolution","got":"1080p","allowed":["480p","720p"]}]}}}

// 409：模型已到期（入队前拒绝）
{"error":{"code":"MODEL_EOS",
 "message":"模型 doubao-seedance-1-0-pro-fast-251015 已于 2026-11-24T14:00:00+08:00 下线，不可新增生成任务；请改用 doubao-seedance-2-0-mini-260615",
 "detail":{"status":"eos_expired","eos_at":"2026-11-24T14:00:00+08:00","migrate_to":"doubao-seedance-2-0-mini-260615"}}}
```

## 4. 参数优先级（必须与后端一致）

```text
调用方显式 params  >  模板字段映射 field_params  >  模板默认值 default_params
```

- 前端**不需要**自己合并参数；把用户选择放进 `template_fields`，把高级覆盖项放进 `params`，以后端返回的 `effective` 为准回填。
- **唯一例外**：`tpl_season_theme` 的 `generation_mode` 由参考图存在性派生。若你显式传了不一致的值，后端会覆盖并在 `warnings` 里返回 `GENERATION_MODE_DERIVED`；请把这条 warning 作为提示展示，不要静默忽略。

### 前端使用的最终参数字段

| kind | `effective` 字段 |
| --- | --- |
| image | `generation_mode`、`size` |
| video | `duration`、`ratio`、`resolution`、`generate_audio` |

## 5. 模板变化（视频模板升到 v2）

```
GET /api/templates/tpl_product_motion_5s
→ latest_version: 2，versions: [1, 2]
→ fields: duration(param_key=duration, required=false), ratio(param_key=ratio)
```

- **v1 快照未被修改**，仅供历史任务追溯；新表单请用 `latest_version`。
- 若用户打开的是引用 v1 的历史任务，展示时按 `?version=1` 取快照，不要拿 v2 覆盖显示。

## 6. 模型生命周期（前端状态机）

```
lifecycle.status = active        → 正常可选
                 = eos_scheduled → 可选，但显示「将于 eos_at 下线，迁移至 migrate_to」
                 = eos_expired   → 禁用；提交会 409 MODEL_EOS
```

- 服务端比较规则：`now >= eos_at` 即到期（到期当秒即拒）。
- **v0.7.2 修正**：`/api/models` 的 `lifecycle` 已改为按服务器时间**实时计算**，与提交校验共用
  `config.model_lifecycle_state()`；前端不会遇到“列表说未到期、提交却拒绝”。
- **已删除**字段 `migration_available`（恒为真，易误读）。新增 `migrate_to_activation`：仅转述目标模型
  在 `MODEL_ACTIVATION` 中的真实调用结果（`verified`/`not_opened`/`unknown`），**不表示可用**。
  要判断迁移目标是否可用，请看目标模型自身的 `activation`。
- 新增 `eom_passed`：仅表示是否已过“停止新购”时间，与能否调用无关。
- 前端**不要**自行计算“是否到期”作为唯一依据；以 `/api/models` 与提交时的错误为准。
- **不要**自动把用户选择切到 `migrate_to`（后端不静默替换，前端也不应静默替换）。

> 当前真实值（2026-10-24 实时读取）：`1.0 pro fast` 为 `eos_scheduled`、
> `migrate_to=doubao-seedance-2-0-mini-260615`、`migrate_to_activation=not_opened`；
> 即迁移目标**尚未开通**，不得展示为“可迁移/可切换”。

## 7. 错误处理映射（含本轮新增）

| code | HTTP | 前端建议 |
| --- | --- | --- |
| `MODEL_CAPABILITY_MISMATCH` | 400 | 用 `detail.problems[].allowed` 修正控件选项 |
| `TEMPLATE_FIELD_REQUIRED` | 400 | 聚焦 `detail.field`（自定义字段时指向 `*_custom`） |
| `TEMPLATE_FIELD_INVALID` | 400 | 高亮非法项（`detail.allowed`） |
| `MODEL_EOS` | 409 | 禁用该模型，提示 `detail.migrate_to` + `announce_url` |
| `MODEL_TEMPLATE_INCOMPATIBLE` | 400 | 用 `detail.compatible_models` 重选 |
| `TEMPLATE_DEPRECATED` | 409 | 提示改用最新版本 |
| `MODEL_NOT_OPENED` | 502 | 置灰 + 提示去控制台开通（本轮未产生计费，但不要表述为“账单为零”） |
| `CREATE_RESULT_UNKNOWN` | 202 | 隐藏直接重试，先人工对账 |

## 8. 已实测数据（可用于前端展示校验，非真实端到端验收）

| 项 | 值 | 来源 |
| --- | --- | --- |
| flash 图生图 2048² | 16,443 ms / 估算 ¥0.12 / 2048×2048 | 阶段 B（2026-10-23） |
| 1.0 pro fast 5s 9:16 720p | 33,504 ms / 103,818 tokens / 估算 ¥0.436 / **704×1248** | 阶段 B |
| pro / 2.0 mini | `MODEL_NOT_OPENED` | 阶段 B |
| 预估接口 | 10s+1:1 → 估算 ¥0.9072，任务数不变 | 本轮实时核对（免费） |
| 概念图预估 | `concept_mode=true`、`text_to_image`、估算 ¥0.12 | 本轮实时核对（免费） |

## 9. 仍未完成（不要当成已具备）

- 采纳/驳回的**后端审核表**（当前只能前端本地状态或存草稿，不是跨端任务级审核）。
- 模板效果示例素材（`effect_sample` 仍为空，不得用任意图冒充）。
- 鉴权、数据隔离、额度、Webhook、无限画布、完整审核流。
- 两个新增模型的真实端到端验收（未开通）。
- 阶段 C 预算与同题实测。

## 10. 前端离线自检清单

1. 生成前**只**调 estimate，确认后再调 tasks。
2. 用 `effective` 回填，不用 `default_params`。
3. 未开通模型置灰；`eos_scheduled` 显示下线提示；`eos_expired` 禁用。
4. 概念图显著标注「创意概念图，不代表真实商品外观」。
5. `*_custom` 仅在选中「自定义」时可填，为空时前端先拦一次（后端也会拦）。
6. 费用展示统一标注“估算 · 非账单”。
