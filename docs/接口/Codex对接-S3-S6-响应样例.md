# Codex 对接：S3–S6 逐项响应样例（2026-10-26）

真实响应样例（隔离库 + 替身执行，结构与字段均为真实产出）：
**`docs/接口/样例-S3-S6-真实响应.json`**（键：director / storyboard_review / shot_list /
project_status / film / export / structure_template）

对应后端提交：见文末「提交号」。

---

## 1. 分镜确认 / 审核

**POST `/api/storyboards/{id}/review`** → `{approve:true}`

```json
{"storyboard_id":"sb_1a2b…","status":"approved",
 "confirmation":{"confirmation_id":"cfm_9c8d…","status":"active",
                 "object_type":"storyboard","object_id":"sb_1a2b…",
                 "object_version":1,"reused":false}}
```

- 驳回：`{approve:false, reason:"瓶身比例不对"}` → `{"status":"rejected","reason":"…","hint":"只重做该镜分镜图即可…"}`
- **未生成完成（无 asset）→ 409 `STORYBOARD_NOT_READY`**；驳回缺原因 → 400 `VALIDATION_FAILED`。
- 前端要点：审核成功后才写 `confirmations`；**新版本不继承批准**，需重新审核。

## 2. 镜头状态

**GET `/api/shots/{id}`**（节选）

```json
{"shot_id":"shot_…","seq":1,"duration_s":5,"description":"镜1",
 "status":"ready","revision":1,
 "storyboard":{"storyboard_id":"sb_…","version":1,"asset_id":"asset_…",
               "status":"approved","approved":true},
 "storyboard_versions":1}
```

`status` 取值：`draft`（待生成/被驳回）→ `storyboard_pending`（分镜任务在跑/已出图待审）
→ `storyboard_approved`（已通过，可生成视频）→ `ready`（已有成功片段）。

**GET `/api/projects/{id}/status`**（刷新恢复入口，节选）

```json
{"current_stage":"video",
 "shots":[{"shot_id":"shot_…","seq":1,"duration_s":5,
           "storyboard":{"storyboard_id":"sb_…","version":1,"asset_id":"asset_…","approved":true},
           "take":{"take_id":"take_…","task_id":"task_…","status":"succeeded",
                   "asset_id":"asset_…","actual_duration_ms":2000},"takes":1}],
 "films":[{"film_id":"flm_…","version":1,"status":"ready","asset_id":"asset_…",
           "duration_ms":4000,"partial":false}],
 "allowed_actions":["compose_film"],"warnings":[]}
```

- **任务成功后由生产链路自动回写**（`storyboards.asset_id`、`shot_takes.status/asset_id/duration_ms`、`shots.status=ready`），
  前端**不需要**手动调接口补数据。
- `films` 在 `full` 模式**全部镜头成功后自动出现**（无需调合成接口）。

## 3. 结构模板

**POST `/api/projects/{id}/structure-templates`** `{name}` → `{"template_id":"stpl_…","version":1}`

**GET `/api/structure-templates/{id}?version=`**

```json
{"template_id":"stpl_…","name":"两镜快节奏","version":1,"latest_version":1,
 "snapshot":[{"seq":1,"role":"镜1","duration_s":5,"motion":null,"rhythm":null},
             {"seq":2,"role":"镜2","duration_s":5,"motion":null,"rhythm":null}],
 "note":"模板只含结构，不含商品事实与素材"}
```

**POST `/api/projects/{id}/apply-structure`** `{template_id, version?}`

```json
{"project_id":"prj_…","template_id":"stpl_…","template_version":1,
 "created_shots":[{"shot_id":"shot_…","seq":1,"duration_s":5,"role":"镜1"}],
 "requires":["brief","assets"],
 "model_revalidation":{"ok":true,"note":"时长已在套用时按当前模型能力重新校验"}}
```

- 套用只生成**镜头骨架**；素材/商品事实需重填；模板升级不自动跟随（项目记录 `structure_template_version`）。

## 4. 成片时长

**GET `/api/films/{id}`**

```json
{"film_id":"flm_…","project_id":"prj_…","version":1,"status":"ready",
 "asset_id":"asset_…","duration_ms":4000,"partial":false,
 "items":[{"take_id":"take_…","shot_id":"shot_…","asset_id":"asset_…",
           "actual_duration_ms":2000}],
 "missing_shots":[],"planned_duration_s":10}
```

| 字段 | 含义 |
| --- | --- |
| `planned_duration_s` | 分镜计划时长（各镜 `duration_s` 之和） |
| `duration_ms` | **实测成片时长**（ffmpeg 产出后探测；探测不到为 `null`） |
| `partial` | 是否部分合成（用户显式同意才会为 `true`） |

> 前端展示：`duration_ms` 优先；为空时显示"时长未知"，**不要用 planned 冒充实测**。
> 真实样片：计划 10s → 实测 **10,080ms**（见 `docs/evidence/真实验收-20261026/`）。

## 5. 导出

**POST `/api/projects/{id}/export`** `{include?:["film","clips","storyboards","manifest"]}`

```json
{"export_id":"exp_…","project_id":"prj_…","file_name":"prj_…-699de7.zip",
 "bytes":5254,"includes":["film","clips","storyboards","manifest"],
 "download_url":"/api/exports/exp_…/file",
 "films_included":1,"clips_included":2,"storyboards_included":2,
 "missing":[],"note":"导出不重新生成；缺失项已在 manifest.missing 标注"}
```

**下载**：`GET /api/exports/{export_id}/file` → `application/zip`
zip 结构：`films/v{n}.mp4`、`clips/{seq}-{take}.mp4`、`storyboards/{seq}-v{n}.jpg`、`manifest.json`。

- 前端直接用 `download_url` 触发下载（同源，无需额外鉴权）。
- 若 `missing` 非空，说明对应文件缺失，UI 应提示而不是谎报完整。

## 6. 提交号

| 内容 | 提交 |
| --- | --- |
| S2 修复 + 导演台就绪度 + 助手阶段上下文 | `162b624` |
| S3 镜头与分镜 | `939842f` |
| S4 整组生成（两模式/冻结/事务/幂等/重试） | `b1943ea` |
| S5–S6 成片/模板/导出 | `f25d50f` |
| 真实 ffmpeg 拼接 + 两镜真实验收 | `e74ba14` |
| **本轮收尾：生产链路回写 + full 自动合成 + ZIP 改修** | 见最新提交（README 式核对） |

## 7. 免费验收（本轮）

`backend/tools_pipeline_tests.py` **9/9**：

| 用例 | 结果 |
| --- | --- |
| worker 完成分镜任务 → `storyboards.asset_id` 自动回写 | ✅ |
| full：逐镜完成 → **自动拼接** ready（实测约 4000ms） | ✅ |
| clips：完成逐镜后**不**自动合成 | ✅ |
| 失败镜 → take=failed，且 full **不**出片 | ✅ |
| 排队中取消 → take=canceled | ✅ |
| 重复完成回报 → 不重复建 take / 不重复合成 | ✅ |
| 重启：中断任务 take=interrupted，不自动合成 | ✅ |
| ZIP 含 `films/` + `clips/` + `storyboards/` + manifest | ✅ |
| `GET /api/exports/{id}/file` 返回 zip | ✅ |

## 8. 仍需 Codex 联调确认

1. 状态映射：`shot.status` 四态 → UI 文案/颜色；
2. `films` 自动出现时的提示（full 模式）；
3. 导出 `missing` 非空时的提示；
4. `duration_ms` 为 `null` 时的展示（"时长未知"）。
