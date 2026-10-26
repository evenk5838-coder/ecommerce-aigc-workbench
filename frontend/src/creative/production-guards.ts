import type { ProductionStatus, ScopeApproval, ScopeInput, ScopeQuote, ScopeSubmission, StructureShot } from './production-types';

export interface ProductionInspection { ok: boolean; reason: string | null }
export const isRecord = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
export const isText = (value: unknown): value is string => typeof value === 'string' && value.trim().length > 0;
export const isCount = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
export const isPositiveInteger = (value: unknown): value is number => isCount(value) && value > 0;
export const isMoney = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 0;
export const isUniqueIds = (value: unknown, allowEmpty = false): value is string[] => Array.isArray(value) && (allowEmpty || value.length > 0) && value.every(isText) && new Set(value).size === value.length;
const good = (): ProductionInspection => ({ ok: true, reason: null });
const bad = (reason: string): ProductionInspection => ({ ok: false, reason });
const sameIds = (a: string[], b: string[]) => a.length === b.length && a.every((id, index) => id === b[index]);

export function inspectScopeInput(value: unknown): ProductionInspection {
  if (!isRecord(value) || (value.mode !== 'full' && value.mode !== 'clips') || !isUniqueIds(value.shot_ids)) return bad('请选择生成模式及不重复的镜头。');
  if (!isRecord(value.specs) || !['model_id', 'ratio', 'resolution'].every(key => isText((value.specs as Record<string, unknown>)[key]))) return bad('请完整选择模型、比例和清晰度。');
  if (value.overrides !== undefined) {
    if (!isRecord(value.overrides)) return bad('镜头覆盖参数格式不正确。');
    for (const [id, override] of Object.entries(value.overrides)) {
      if (!value.shot_ids.includes(id) || !isRecord(override) || !isPositiveInteger(override.duration_s) || Object.keys(override).some(key => key !== 'duration_s')) return bad('只能覆盖本次选中镜头的整数时长。');
    }
  }
  return good();
}

/** Verify the server's numbers, never calculate a model price or fill missing fields. */
export function inspectScope(value: unknown, expected?: ScopeInput, nowMs = Date.now()): ProductionInspection {
  if (!isRecord(value) || !isText(value.scope_id) || (value.mode !== 'full' && value.mode !== 'clips')) return bad('报价缺少范围编号或生成模式，请重新报价。');
  if (!isText(value.scope_hash) || !/^sha256:[a-f\d]{64}$/i.test(value.scope_hash)) return bad('报价缺少完整的输入校验值，请重新报价。');
  if (typeof value.expires_at !== 'number' || !Number.isFinite(value.expires_at) || !Number.isFinite(value.expires_at * 1000) || !Number.isFinite(nowMs) || value.expires_at * 1000 <= nowMs) return bad('报价已过期或有效期不明确，请重新报价。');
  if (!Array.isArray(value.shots) || !value.shots.length) return bad('报价没有镜头明细。');
  const ids: string[] = [];
  let cost = 0, duration = 0;
  for (const shot of value.shots) {
    if (!isRecord(shot) || !isText(shot.shot_id) || !isPositiveInteger(shot.duration_s) || !isMoney(shot.estimate_cny)) return bad('逐镜报价、时长或编号不完整。');
    ids.push(shot.shot_id); cost += shot.estimate_cny; duration += shot.duration_s;
  }
  if (!isUniqueIds(ids) || !Number.isFinite(cost) || !Number.isSafeInteger(duration)) return bad('报价镜头重复，或金额、时长超出有效范围。');
  if (!isMoney(value.total_estimate_cny) || Math.abs(value.total_estimate_cny - cost) > 0.000001) return bad('合计报价与逐镜明细不一致，请重新报价。');
  if (!isPositiveInteger(value.planned_film_duration_s) || value.planned_film_duration_s !== duration) return bad('计划总时长与逐镜明细不一致。');
  if (!isRecord(value.side_effects) || value.side_effects.tasks_created !== 0 || value.side_effects.model_called !== false) return bad('服务端未确认本次仅报价，请先核对任务。');
  if (value.project_id !== undefined && !isText(value.project_id)) return bad('报价的项目编号无效。');
  if (value.submittable !== undefined && typeof value.submittable !== 'boolean') return bad('报价可提交状态不明确。');
  if (value.blocked_reasons !== undefined && (!Array.isArray(value.blocked_reasons) || !value.blocked_reasons.every(isText))) return bad('报价阻断原因格式不正确。');
  if (value.submittable === false || (Array.isArray(value.blocked_reasons) && value.blocked_reasons.length)) return bad('本次报价不可提交，请检查模型与镜头状态。');
  if (expected) {
    const input = inspectScopeInput(expected); if (!input.ok) return input;
    if (value.mode !== expected.mode || !sameIds(ids, expected.shot_ids)) return bad('报价模式、数量或镜头顺序与本次选择不一致。');
    for (const shot of value.shots as ScopeQuote['shots']) {
      const override = expected.overrides?.[shot.shot_id];
      if (override && override.duration_s !== shot.duration_s) return bad('报价时长与本次明确选择不一致。');
    }
  }
  return good();
}

export function inspectApproval(quote: ScopeQuote, approval: unknown, nowMs = Date.now(), confirmedAtMs?: number): ProductionInspection {
  const scope = inspectScope(quote, undefined, nowMs); if (!scope.ok) return scope;
  if (!isRecord(approval) || approval.scope_id !== quote.scope_id || approval.approved !== true || approval.max_uses !== 1 || !isPositiveInteger(approval.expires_in_s)) return bad('确认回执无效或不属于当前报价。');
  if (approval.scope_hash !== undefined && approval.scope_hash !== quote.scope_hash) return bad('确认回执对应的输入已改变，请重新报价。');
  const deadlines = [quote.expires_at * 1000];
  if (approval.expires_at !== undefined) {
    if (!isMoney(approval.expires_at) || approval.expires_at === 0) return bad('确认有效期不明确。');
    deadlines.push(approval.expires_at * 1000);
  }
  if (confirmedAtMs !== undefined) {
    if (!isMoney(confirmedAtMs) || confirmedAtMs > nowMs) return bad('本地确认时间无效，请重新确认。');
    deadlines.push(confirmedAtMs + approval.expires_in_s * 1000);
  } else if (approval.expires_at === undefined) return bad('缺少确认接收时间，请重新确认。');
  if (deadlines.some(deadline => !Number.isFinite(deadline) || deadline <= nowMs)) return bad('确认已过期，请重新报价并确认。');
  return good();
}
export const usableApproval = (quote: ScopeQuote, approval: ScopeApproval, nowMs = Date.now(), confirmedAtMs?: number): boolean => inspectApproval(quote, approval, nowMs, confirmedAtMs).ok;

export function inspectSubmission(value: unknown, quote: ScopeQuote): ProductionInspection {
  if (!isRecord(value) || !isText(value.batch_id) || value.mode !== quote.mode || value.sequential !== true || value.auto_film !== (quote.mode === 'full')) return bad('任务回执的模式或执行方式与确认内容不一致。');
  if (value.idempotent_replay !== undefined && typeof value.idempotent_replay !== 'boolean') return bad('任务回执幂等状态不明确。');
  if (value.scope_id !== undefined && value.scope_id !== quote.scope_id) return bad('任务回执不属于当前报价。');
  if (!Array.isArray(value.tasks) || !value.tasks.every(task => isRecord(task) && isText(task.shot_id) && isText(task.task_id) && (task.take_id === undefined || isText(task.take_id)))) return bad('任务回执缺少镜头与任务编号映射。');
  const tasks = value.tasks as ScopeSubmission['tasks'];
  if (!sameIds(tasks.map(task => task.shot_id), quote.shots.map(shot => shot.shot_id)) || !isUniqueIds(tasks.map(task => task.task_id))) return bad('任务数量、顺序或编号映射不完整，请先核对任务状态。');
  return good();
}

export function inspectFailedSelection(status: ProductionStatus, selectedIds: string[]): ProductionInspection {
  if (!isUniqueIds(selectedIds)) return bad('请明确选择要重试的失败镜头，不能留空或重复。');
  const byId = new Map(status.shots.map(shot => [shot.shot_id, shot]));
  if (byId.size !== status.shots.length || selectedIds.some(id => byId.get(id)?.status !== 'failed')) return bad('只能重试当前项目中已选中的失败镜头，请先刷新状态。');
  return good();
}
/** Only measured output metadata counts as actual duration. No planned-duration fallback. */
export function actualDurationMs(value: { actual_duration_ms?: unknown }): number | null {
  return typeof value.actual_duration_ms === 'number' && Number.isFinite(value.actual_duration_ms) && value.actual_duration_ms > 0 ? value.actual_duration_ms : null;
}
export function inspectStructureSnapshot(value: unknown): ProductionInspection {
  if (!Array.isArray(value) || !value.length) return bad('请填写至少一个镜头结构。');
  const allowed = ['seq', 'role', 'duration_s', 'motion', 'rhythm'];
  const sequences: number[] = [];
  for (const item of value) {
    if (!isRecord(item) || Object.keys(item).some(key => !allowed.includes(key)) || !isPositiveInteger(item.seq) || !isPositiveInteger(item.duration_s) || !['role', 'motion', 'rhythm'].every(key => typeof item[key] === 'string') || !isText(item.role)) return bad('结构模板只保存顺序、镜头职能、时长、运镜和节奏，不保存商品或素材字段。');
    sequences.push(item.seq);
  }
  if (sequences.some((seq, index) => seq !== index + 1)) return bad('结构模板的镜头顺序需从 1 连续排列。');
  return good();
}
export function isStructureSnapshot(value: unknown): value is StructureShot[] { return inspectStructureSnapshot(value).ok; }
