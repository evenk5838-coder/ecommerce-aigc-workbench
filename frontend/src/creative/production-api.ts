import { ApiError } from '../api';
import {
  inspectApproval, inspectScope, inspectScopeInput, inspectStructureSnapshot, inspectSubmission,
  isCount, isMoney, isPositiveInteger, isRecord, isText, isUniqueIds,
} from './production-guards';
import type {
  AppliedStructure, Film, FilmCreation, FilmItem, ProductionApi, ProductionRequestOptions,
  ProductionStatus, ScopeApproval, ScopeInput, ScopeQuote, ScopeSubmission, Shot, ShotInput,
  ShotStatus, ShotTake, Storyboard, StoryboardCreation, StoryboardReview,
  StructureTemplate, StructureTemplateSummary, ProjectExport,
} from './production-types';

const invalid = (message = '创作接口返回的数据不完整，请刷新核对。'): never => { throw new ApiError('PRODUCTION_RESPONSE_INVALID', message, 0); };
const inputError = (message: string): never => { throw new ApiError('PRODUCTION_INPUT_INVALID', message, 0); };
const assertInput = (ok: unknown, message: string): void => { if (!ok) inputError(message); };
const pathId = (id: string): string => { assertInput(isText(id), '缺少项目、镜头或版本编号。'); return encodeURIComponent(id); };
const json = (method: string, body: unknown): RequestInit => {
  try { return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }; }
  catch { return inputError('提交内容无法序列化。'); }
};
const nullableText = (value: unknown) => value == null || isText(value);
const optionalNumber = (value: unknown) => value == null || isMoney(value);
const actualDuration = (value: unknown) => value == null || (isMoney(value) && value > 0);
const stringList = (value: unknown): value is string[] => Array.isArray(value) && value.every(item => typeof item === 'string');
const idsMatch = (a: string[], b: string[]) => a.length === b.length && a.every((id, i) => id === b[i]);
function list<T>(value: unknown, parse: (value: unknown) => T): { items: T[] } {
  if (!isRecord(value) || !Array.isArray(value.items)) return invalid();
  return { ...value, items: value.items.map(parse) };
}
function parseStoryboard(value: unknown, shotId?: string): Storyboard {
  if (!isRecord(value) || !isText(value.storyboard_id) || !isText(value.shot_id) || (shotId !== undefined && value.shot_id !== shotId) || !isPositiveInteger(value.version) || !isText(value.status) || !nullableText(value.task_id) || !nullableText(value.asset_id) || !optionalNumber(value.created_at) || (value.reject_reason != null && typeof value.reject_reason !== 'string')) return invalid();
  return value as unknown as Storyboard;
}
function parseTake(value: unknown, shotId?: string): ShotTake {
  if (!isRecord(value) || !isText(value.take_id) || !isText(value.status) || (value.shot_id !== undefined && (!isText(value.shot_id) || (shotId !== undefined && value.shot_id !== shotId))) || !nullableText(value.task_id) || !nullableText(value.asset_id) || !actualDuration(value.actual_duration_ms) || !optionalNumber(value.cost_cny) || !optionalNumber(value.created_at)) return invalid();
  return value as unknown as ShotTake;
}
function parseShot(value: unknown, projectId?: string, shotId?: string): Shot {
  if (!isRecord(value) || !isText(value.shot_id) || !isText(value.project_id) || (projectId !== undefined && value.project_id !== projectId) || (shotId !== undefined && value.shot_id !== shotId) || !isPositiveInteger(value.revision) || !isPositiveInteger(value.seq) || !isPositiveInteger(value.duration_s) || typeof value.description !== 'string') return invalid();
  for (const key of ['script_id', 'model_id', 'status']) if (value[key] !== undefined && !isText(value[key])) return invalid();
  for (const key of ['motion', 'prompt']) if (value[key] != null && typeof value[key] !== 'string') return invalid();
  if (!nullableText(value.input_asset_id) || !optionalNumber(value.approved_at)) return invalid();
  if (value.storyboards !== undefined) { if (!Array.isArray(value.storyboards)) return invalid(); value.storyboards.forEach(item => parseStoryboard(item, value.shot_id as string)); }
  if (value.takes !== undefined) { if (!Array.isArray(value.takes)) return invalid(); value.takes.forEach(item => parseTake(item, value.shot_id as string)); }
  const result = { ...value } as unknown as Shot;
  if (value.storyboard !== undefined) result.storyboards = value.storyboard === null ? [] : [parseStoryboardSummary(value.storyboard, value.shot_id)];
  return result;
}
function parseStoryboardSummary(value: unknown, shotId: string): Storyboard {
  if (!isRecord(value) || !isText(value.storyboard_id) || !isPositiveInteger(value.version) || !isText(value.status) || !nullableText(value.asset_id) || typeof value.approved !== 'boolean') return invalid();
  return { storyboard_id: value.storyboard_id, shot_id: shotId, version: value.version, status: value.status, asset_id: value.asset_id as string | null | undefined, approved: value.approved };
}
/** Backend returns one latest take plus a total count, not take history. */
function parseShotStatus(value: unknown): ShotStatus {
  if (!isRecord(value) || !isText(value.shot_id) || !isCount(value.takes) || !isPositiveInteger(value.seq) || !isPositiveInteger(value.duration_s) || typeof value.description !== 'string') return invalid();
  if (value.take !== null) parseTake(value.take, value.shot_id);
  const take = value.take as ShotTake | null;
  const storyboards = value.storyboard === null ? [] : [parseStoryboardSummary(value.storyboard, value.shot_id)];
  return { shot_id: value.shot_id, status: take?.status ?? 'not_started',
    ...(take ? { take_id: take.take_id, task_id: take.task_id, asset_id: take.asset_id, actual_duration_ms: take.actual_duration_ms } : {}),
    takes: take ? [take] : [], take_count: value.takes, storyboards };
}
function parseFilm(value: unknown, filmId?: string, projectId?: string): Film {
  if (!isRecord(value) || !isText(value.film_id) || !isText(value.status) || (filmId !== undefined && value.film_id !== filmId) || (value.project_id !== undefined && (!isText(value.project_id) || (projectId !== undefined && value.project_id !== projectId)))) return invalid();
  if ((value.duration_ms !== undefined && !actualDuration(value.duration_ms)) || (value.version !== undefined && !isPositiveInteger(value.version)) || !optionalNumber(value.planned_duration_s) || !actualDuration(value.actual_duration_ms) || !nullableText(value.asset_id) || !nullableText(value.error_code) || (value.error_message != null && typeof value.error_message !== 'string') || (value.partial !== undefined && typeof value.partial !== 'boolean')) return invalid();
  if (value.missing_shot_ids !== undefined && !isUniqueIds(value.missing_shot_ids, true)) return invalid();
  if (value.items !== undefined) {
    if (!Array.isArray(value.items) || !value.items.every(item => isRecord(item) && isText(item.shot_id) && isText(item.take_id) && actualDuration(item.actual_duration_ms))) return invalid();
    const items = value.items as FilmItem[];
    if (!isUniqueIds(items.map(item => item.take_id), true) || !isUniqueIds(items.map(item => item.shot_id), true)) return invalid();
  }
  if (value.missing_shots !== undefined && (!Array.isArray(value.missing_shots) || !value.missing_shots.every(item => isRecord(item) && isText(item.shot_id)))) return invalid();
  return { ...value, ...(Object.hasOwn(value, 'duration_ms') ? { actual_duration_ms: value.duration_ms } : {}),
    ...(Array.isArray(value.missing_shots) ? { missing_shot_ids: value.missing_shots.map(item => (item as { shot_id: string }).shot_id) } : {}) } as unknown as Film;
}
function parseStatus(value: unknown, projectId: string): ProductionStatus {
  if (!isRecord(value) || value.project_id !== projectId || !['locked', 'current', 'done'].includes(String(value.mode)) || !isText(value.current_stage) || !stringList(value.allowed_actions) || !Array.isArray(value.warnings) || !Array.isArray(value.shots) || !Array.isArray(value.films)) return invalid();
  const shots = value.shots.map(parseShotStatus);
  if (!isUniqueIds(shots.map(shot => shot.shot_id), true)) return invalid();
  const films = value.films.map(film => parseFilm(film, undefined, projectId));
  return { ...value, shots, films } as unknown as ProductionStatus;
}
function validateShotInput(value: Partial<ShotInput>, partial = false): void {
  if (!isRecord(value)) inputError('镜头参数需为对象。');
  for (const key of ['seq', 'duration_s']) if ((!partial || value[key as keyof ShotInput] !== undefined) && !isPositiveInteger(value[key as keyof ShotInput])) inputError('镜头顺序和时长需为正整数。');
  if ((!partial || value.description !== undefined) && typeof value.description !== 'string') inputError('请填写镜头描述。');
  for (const key of ['motion', 'prompt']) if (value[key as keyof ShotInput] !== undefined && typeof value[key as keyof ShotInput] !== 'string') inputError('运镜和提示词需为文字。');
  if (!nullableText(value.input_asset_id) || (value.model_id !== undefined && !isText(value.model_id))) inputError('参考图或模型编号无效。');
}
function parseTemplateSummary(value: unknown): StructureTemplateSummary {
  if (!isRecord(value) || !isText(value.template_id) || !isText(value.name) || !isPositiveInteger(value.latest_version) || (value.status !== undefined && !isText(value.status))) return invalid();
  return value as unknown as StructureTemplateSummary;
}

/** URL only: rendering a link does not fetch, create or regenerate a film. */
export const filmFileUrl = (film: Pick<Film, 'asset_id'>): string => { assertInput(isText(film.asset_id), '成片尚无可下载的素材。'); return `/api/assets/${pathId(film.asset_id!)}/file`; };

/** Each method is one explicit request. No model call, polling or confirmation is automatic. */
export function createProductionApi(fetcher: typeof fetch = (input, init) => globalThis.fetch(input, init)): ProductionApi {
  const scopes = new WeakMap<ScopeQuote, { projectId: string; input: ScopeInput; snapshot: string }>();
  const approvals = new WeakMap<ScopeApproval, { scopeId: string; scopeHash: string; confirmedAtMs: number; snapshot: string }>();
  async function request<T>(path: string, init: RequestInit, parse: (value: unknown) => T, options: ProductionRequestOptions = {}): Promise<T> {
    const method = init.method ?? 'GET', mutation = method !== 'GET';
    const timeoutMs = options.timeoutMs ?? 25_000;
    assertInput(Number.isFinite(timeoutMs) && timeoutMs > 0, '请求等待时间无效。');
    if (options.signal?.aborted) throw new ApiError('CREATIVE_REQUEST_ABORTED', '请求已取消，尚未发送。', 0);
    const controller = new AbortController(); let timedOut = false; let knownRejection: ApiError | null = null;
    const unknown = (reason: string, original?: ApiError) => new ApiError('CREATIVE_RESULT_UNKNOWN', '操作结果尚不确定，服务端可能已经处理。请先读取项目状态核对，不要直接重复生成。', original?.status ?? 0, { reason, method, path, ...(original ? { original: { code: original.code, message: original.message, detail: original.detail, status: original.status } } : {}) });
    const stopped = () => knownRejection ?? (mutation ? unknown(timedOut ? 'timeout' : 'aborted') : new ApiError(timedOut ? 'CREATIVE_REQUEST_TIMEOUT' : 'CREATIVE_REQUEST_ABORTED', '已停止等待，请重新读取状态。', 0));
    const abort = () => controller.abort(); options.signal?.addEventListener('abort', abort, { once: true });
    const timer = globalThis.setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
    let cleanupAbort = () => {};
    const interruption = new Promise<never>((_, reject) => { const listener = () => reject(stopped()); controller.signal.addEventListener('abort', listener, { once: true }); cleanupAbort = () => controller.signal.removeEventListener('abort', listener); });
    try {
      const operation = (async () => {
        const response = await fetcher('/api' + path, { ...init, signal: controller.signal });
        if (!response.ok && response.status < 500) knownRejection = new ApiError('HTTP_ERROR', `请求被拒绝（${response.status}）。`, response.status);
        if (response.status === 405 || response.status === 501) { knownRejection = new ApiError('PRODUCTION_UNAVAILABLE', '此创作接口尚未接入，请保留当前内容并等待后端就绪。', response.status); throw knownRejection; }
        let body: unknown;
        try { body = await response.json(); } catch { if (controller.signal.aborted) throw stopped(); if (response.ok) return invalid(); }
        if (!response.ok) {
          const error = isRecord(body) && isRecord(body.error) ? body.error : null;
          if (response.status === 404 && !(error && isText(error.code))) { knownRejection = new ApiError('PRODUCTION_UNAVAILABLE', '此创作接口尚未接入，请保留当前内容并等待后端就绪。', 404); throw knownRejection; }
          const failure = new ApiError(error && isText(error.code) ? error.code : 'HTTP_ERROR', error && isText(error.message) ? error.message : `创作请求失败（${response.status}）。`, response.status, error?.detail);
          if (mutation && response.status >= 500) throw unknown('server_error', failure);
          throw failure;
        }
        return parse(body);
      })();
      return await Promise.race([operation, interruption]);
    } catch (error) {
      if (controller.signal.aborted) throw stopped();
      if (error instanceof ApiError) { if (mutation && error.code === 'PRODUCTION_RESPONSE_INVALID') throw unknown('response_invalid', error); throw error; }
      if (knownRejection) throw knownRejection;
      if (mutation) throw unknown('network');
      throw new ApiError('CREATIVE_NETWORK_ERROR', '无法读取创作状态，请稍后刷新；没有自动重试。', 0);
    } finally { globalThis.clearTimeout(timer); options.signal?.removeEventListener('abort', abort); cleanupAbort(); }
  }
  const boundScope = (quote: ScopeQuote) => {
    const context = scopes.get(quote);
    if (!context) return inputError('报价来源或内容已改变，请重新报价。');
    if (context.snapshot !== JSON.stringify(quote)) inputError('报价来源或内容已改变，请重新报价。');
    const check = inspectScope(quote, context.input); if (!check.ok) inputError(check.reason!);
    return context;
  };
  return {
    listShots: (projectId, options) => request(`/projects/${pathId(projectId)}/shots`, {}, value => {
      const result = list(value, shot => parseShot(shot, projectId));
      if (!isUniqueIds(result.items.map(shot => shot.shot_id), true) || new Set(result.items.map(shot => shot.seq)).size !== result.items.length) return invalid();
      return result;
    }, options),
    createShots: (projectId, input, options) => {
      assertInput(isText(input.script_id) && Array.isArray(input.shots) && input.shots.length, '请选择脚本并填写镜头计划。'); input.shots.forEach(shot => validateShotInput(shot));
      assertInput(new Set(input.shots.map(shot => shot.seq)).size === input.shots.length, '镜头顺序不能重复。');
      const models = [...new Set(input.shots.map(shot => shot.model_id).filter((id): id is string => !!id))];
      assertInput(models.length <= 1 && (!input.model_id || !models.length || input.model_id === models[0]), '每批镜头需使用同一时长校验模型。');
      const body = { script_id: input.script_id, model_id: input.model_id ?? models[0], shots: input.shots.map(({seq, description, duration_s, motion}) => ({seq, description, duration_s, motion})) };
      return request(`/projects/${pathId(projectId)}/shots`, json('POST', body), value => {
        if (!isRecord(value) || value.project_id !== projectId || !isUniqueIds(value.shot_ids) || !isPositiveInteger(value.count) || value.shot_ids.length !== value.count || value.count !== input.shots.length) return invalid();
        return { project_id: projectId, shot_ids: value.shot_ids, count: value.count };
      }, options);
    },
    updateShot: (shotId, input, options) => { assertInput(isPositiveInteger(input.expected_revision), '保存镜头需提供当前版本。'); validateShotInput(input, true); const body = { expected_revision: input.expected_revision, description: input.description, duration_s: input.duration_s, motion: input.motion }; return request(`/shots/${pathId(shotId)}`, json('PATCH', body), value => parseShot(value, undefined, shotId), options); },
    deleteShot: (shotId, options) => request(`/shots/${pathId(shotId)}`, { method: 'DELETE' }, value => { if (!isRecord(value) || value.deleted !== shotId || !isText(value.project_id)) return invalid(); return { ...value, shot_id: shotId, deleted: true as const }; }, options),
    reorderShots: (projectId, input, options) => { assertInput(isUniqueIds(input.order), '请提交完整且不重复的镜头顺序。'); return request(`/projects/${pathId(projectId)}/shots/reorder`, json('POST', input), value => { if (!isRecord(value) || value.project_id !== projectId || !isUniqueIds(value.order) || !idsMatch(value.order, input.order)) return invalid(); return { ...value, project_id: projectId, order: value.order }; }, options); },
    createStoryboard: (shotId, input, options) => {
      assertInput(isText(input.model_id) && isText(input.size) && isText(input.prompt) && nullableText(input.input_asset_id), '请填写分镜模型、尺寸和提示词。');
      return request(`/shots/${pathId(shotId)}/storyboard`, json('POST', { ...input, confirm: true }), value => {
        if (!isRecord(value) || value.shot_id !== shotId || !isText(value.storyboard_id) || !isPositiveInteger(value.version) || !isText(value.task_id) || !isMoney(value.estimated_cost_cny)) return invalid();
        return value as unknown as StoryboardCreation;
      }, options);
    },
    approveStoryboard: (id, options) => request(`/storyboards/${pathId(id)}/review`, json('POST', { approve: true }), value => {
      if (!isRecord(value) || value.storyboard_id !== id || value.status !== 'approved' || !isRecord(value.confirmation)) return invalid();
      const c = value.confirmation;
      if (!isText(c.confirmation_id) || c.status !== 'active' || c.object_type !== 'storyboard' || c.object_id !== id || !isPositiveInteger(c.object_version) || typeof c.reused !== 'boolean') return invalid();
      return { ...value, version: c.object_version } as unknown as StoryboardReview;
    }, options),
    rejectStoryboard: (id, reason, options) => { assertInput(isText(reason), '请填写拒绝原因。'); return request(`/storyboards/${pathId(id)}/review`, json('POST', { approve: false, reason }), value => {
      if (!isRecord(value) || value.storyboard_id !== id || value.status !== 'rejected' || !isText(value.reason)) return invalid();
      return { ...value, reject_reason: value.reason } as unknown as StoryboardReview;
    }, options); },
    createScope: async (projectId, input, options) => {
      const check = inspectScopeInput(input); if (!check.ok) inputError(check.reason!);
      const frozen = JSON.parse(JSON.stringify(input)) as ScopeInput;
      const quote = await request(`/projects/${pathId(projectId)}/scope`, json('POST', frozen), value => {
        const checked = inspectScope(value, frozen); if (!checked.ok) return invalid(checked.reason!);
        const quote = value as ScopeQuote; if (quote.project_id !== undefined && quote.project_id !== projectId) return invalid(); return quote;
      }, options);
      scopes.set(quote, { projectId, input: frozen, snapshot: JSON.stringify(quote) }); return quote;
    },
    confirmScope: async (quote, options) => {
      boundScope(quote); const confirmedAtMs = Date.now();
      const approval = await request(`/scopes/${pathId(quote.scope_id)}/confirm`, json('POST', { confirm: true }), value => { const check = inspectApproval(quote, value, Date.now(), confirmedAtMs); if (!check.ok) return invalid(check.reason!); return value as ScopeApproval; }, options);
      approvals.set(approval, { scopeId: quote.scope_id, scopeHash: quote.scope_hash, confirmedAtMs, snapshot: JSON.stringify(approval) }); return approval;
    },
    submitScope: (projectId, quote, approval, options) => {
      const context = boundScope(quote), receipt = approvals.get(approval);
      assertInput(context.projectId === projectId, '报价不属于当前项目。');
      assertInput(receipt && receipt.scopeId === quote.scope_id && receipt.scopeHash === quote.scope_hash && receipt.snapshot === JSON.stringify(approval), '缺少此报价的真实确认回执，请先确认。');
      const check = inspectApproval(quote, approval, Date.now(), receipt!.confirmedAtMs); if (!check.ok) inputError(check.reason!);
      return request(`/projects/${pathId(projectId)}/generate`, json('POST', { scope_id: quote.scope_id }), value => { const checked = inspectSubmission(value, quote); if (!checked.ok) return invalid(checked.reason!); return value as ScopeSubmission; }, options);
    },
    getStatus: (projectId, options) => request(`/projects/${pathId(projectId)}/status`, {}, value => parseStatus(value, projectId), options),
    createFilm: (projectId, input, options) => { assertInput(isUniqueIds(input.take_ids) && typeof input.confirm_partial === 'boolean', '请选择不重复的镜头版本，并明确是否允许部分合成。'); return request(`/projects/${pathId(projectId)}/films`, json('POST', input), value => {
      if (!isRecord(value) || !isText(value.film_id) || !isPositiveInteger(value.version) || !isText(value.status) || (value.partial !== undefined && typeof value.partial !== 'boolean') || (value.missing_shot_ids !== undefined && !isUniqueIds(value.missing_shot_ids, true)) || (!input.confirm_partial && value.partial === true)) return invalid();
      return parseFilm(value) as FilmCreation;
    }, options); },
    listFilms: (projectId, options) => request(`/projects/${pathId(projectId)}/films`, {}, value => list(value, film => parseFilm(film, undefined, projectId)), options),
    getFilm: (id, options) => request(`/films/${pathId(id)}`, {}, value => parseFilm(value, id), options),
    filmFileUrl,
    listStructureTemplates: options => request('/structure-templates', {}, value => list(value, parseTemplateSummary), options),
    createStructureTemplate: (input, options) => { assertInput(isText(input.name), '请填写结构模板名称。'); const check = inspectStructureSnapshot(input.snapshot); if (!check.ok) inputError(check.reason!); return request('/structure-templates', json('POST', input), value => { if (!isRecord(value) || !isText(value.template_id) || !isPositiveInteger(value.version)) return invalid(); return { ...value, template_id: value.template_id, version: value.version }; }, options); },
    getStructureTemplate: (id, version, options) => { if (version !== undefined) assertInput(isPositiveInteger(version), '模板版本需为正整数。'); return request(`/structure-templates/${pathId(id)}${version === undefined ? '' : `?version=${version}`}`, {}, value => {
      if (!isRecord(value) || value.template_id !== id || !isText(value.name) || !isPositiveInteger(value.version) || (version !== undefined && value.version !== version) || !Array.isArray(value.snapshot)) return invalid();
      const snapshot = value.snapshot.map(item => isRecord(item) ? { ...item, motion: item.motion ?? '', rhythm: item.rhythm ?? '' } : item);
      if (!inspectStructureSnapshot(snapshot).ok) return invalid();
      return { ...value, snapshot } as unknown as StructureTemplate;
    }, options); },
    applyStructure: (projectId, input, options) => { assertInput(isText(input.template_id) && isPositiveInteger(input.version), '请明确选择结构模板及版本。'); return request(`/projects/${pathId(projectId)}/apply-structure`, json('POST', input), value => {
      if (!isRecord(value) || value.project_id !== projectId || value.template_id !== input.template_id || value.template_version !== input.version || !Array.isArray(value.created_shots) || !value.created_shots.every(item => isRecord(item) && isText(item.shot_id) && isPositiveInteger(item.seq) && isPositiveInteger(item.duration_s) && isText(item.role)) || !stringList(value.requires) || !isRecord(value.model_revalidation) || typeof value.model_revalidation.ok !== 'boolean' || !isText(value.model_revalidation.note)) return invalid();
      return { ...value, created_shots: value.created_shots.length, created_shot_details: value.created_shots,
        model_revalidation: { ok: value.model_revalidation.ok, notes: [value.model_revalidation.note] } } as unknown as AppliedStructure;
    }, options); },
    exportProject: (projectId, input, options) => {
      assertInput(isUniqueIds(input.include) && input.include.every(part => ['film', 'clips', 'storyboards', 'manifest'].includes(part)), '请选择有效且不重复的导出内容。');
      return request(`/projects/${pathId(projectId)}/export`, json('POST', input), value => {
        if (!isRecord(value) || !isText(value.export_id) || value.project_id !== projectId || !isText(value.file_name) || value.download_url !== `/api/exports/${encodeURIComponent(value.export_id)}/file` || !isCount(value.bytes) || !stringList(value.includes) || !idsMatch(value.includes, input.include) || !['films_included', 'clips_included', 'storyboards_included'].every(key => isCount(value[key])) || !Array.isArray(value.missing) || !value.missing.every(item => isRecord(item) && isText(item.kind) && isText(item.reason) && nullableText(item.film_id) && nullableText(item.shot_id))) return invalid();
        return value as unknown as ProjectExport;
      }, options);
    },
  };
}
export const productionApi = createProductionApi();
