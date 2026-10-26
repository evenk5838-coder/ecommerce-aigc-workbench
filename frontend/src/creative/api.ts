import { ApiError } from '../api';
import type {
  CreativeApi, CreativeProject, CreativeProjectDetail, CreativeProjectListItem, CreativeProjectUpdate,
  CreativeRequestOptions, CreativeResultUnknownDetail, CreativeScript, CreativeScriptSummary,
  CreativeUnknownReason, ScriptConfirmation, ScriptContent, ScriptGenerationResult, ScriptWriteResult,
} from './types';

export const CREATIVE_REQUEST_TIMEOUT_MS = 20_000;
export const CREATIVE_GENERATION_TIMEOUT_MS = 120_000;
type Parser<T> = (value: unknown) => T;
const record = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null && !Array.isArray(value);
const text = (value: unknown): value is string => typeof value === 'string' && value.trim().length > 0;
const integer = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
const timestamp = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value) && value >= 0;
const strings = (value: unknown): value is string[] => Array.isArray(value) && value.every(item => typeof item === 'string');
const invalid = (): never => { throw new ApiError('CREATIVE_RESPONSE_INVALID', '创作空间返回的数据不完整，请刷新项目或脚本版本核对。', 0); };

function pathId(id: string): string {
  if (!text(id)) throw new ApiError('CREATIVE_INPUT_INVALID', '缺少项目或脚本编号。', 0);
  return encodeURIComponent(id);
}
function json(method: string, body: unknown): RequestInit {
  try { return { method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }; }
  catch { throw new ApiError('CREATIVE_INPUT_INVALID', '提交内容无法序列化，请检查输入。', 0); }
}
function parseContent(value: unknown): ScriptContent {
  if (!record(value) || (value.summary !== undefined && typeof value.summary !== 'string') ||
    (value.shots !== undefined && !Array.isArray(value.shots))) return invalid();
  return value as ScriptContent;
}
function parseProject(value: unknown, expectedId?: string): CreativeProject {
  if (!record(value) || !text(value.project_id) || !text(value.name) || !record(value.brief) ||
    !(value.status === null || text(value.status)) || (expectedId !== undefined && value.project_id !== expectedId)) return invalid();
  for (const key of ['created_at', 'updated_at']) {
    if (value[key] != null && !timestamp(value[key])) return invalid();
  }
  if (value.structure_template_id != null && !text(value.structure_template_id)) return invalid();
  if (value.structure_template_version != null && (!integer(value.structure_template_version) || value.structure_template_version < 1)) return invalid();
  return value as unknown as CreativeProject;
}
function parseProjectListItem(value: unknown): CreativeProjectListItem {
  parseProject(value);
  if (!record(value) || !integer(value.script_count)) return invalid();
  return value as unknown as CreativeProjectListItem;
}
function parseSummary(value: unknown): CreativeScriptSummary {
  if (!record(value) || !text(value.script_id) || !integer(value.version) || value.version < 1 ||
    !(value.source === null || text(value.source)) || !(value.confirmed_at === null || timestamp(value.confirmed_at))) return invalid();
  return value as unknown as CreativeScriptSummary;
}
function parseProjectDetail(value: unknown, expectedId: string): CreativeProjectDetail {
  parseProject(value, expectedId);
  parseProjectListItem(value);
  if (!record(value)) return invalid();
  if (value.latest_script !== null) parseSummary(value.latest_script);
  return value as unknown as CreativeProjectDetail;
}
function parseProjectUpdate(value: unknown, expectedId: string): CreativeProjectUpdate {
  parseProject(value, expectedId);
  if (!record(value) || !strings(value.stale)) return invalid();
  return value as unknown as CreativeProjectUpdate;
}
function parseWrite(value: unknown): ScriptWriteResult {
  if (!record(value) || !text(value.script_id) || !integer(value.version) || value.version < 1 ||
    !(value.source === null || text(value.source))) return invalid();
  parseContent(value.content);
  return value as unknown as ScriptWriteResult;
}
function parseScript(value: unknown, expectedId?: string, expectedProjectId?: string): CreativeScript {
  parseWrite(value);
  parseSummary(value);
  if (!record(value) || !text(value.project_id) || (expectedId !== undefined && value.script_id !== expectedId) ||
    (expectedProjectId !== undefined && value.project_id !== expectedProjectId) ||
    (value.created_at != null && !timestamp(value.created_at))) return invalid();
  return value as unknown as CreativeScript;
}
function parseGeneration(value: unknown, projectId: string): ScriptGenerationResult {
  if (!record(value)) return invalid();
  if (value.assistant_unavailable === true) {
    if (!text(value.hint) || value.project_id !== projectId) return invalid();
  } else if (value.assistant_unavailable === false) {
    parseWrite(value);
    if (value.source !== 'assistant' || !record(value.llm_usage) ||
      !integer(value.llm_usage.input_tokens) || !integer(value.llm_usage.output_tokens)) return invalid();
  } else return invalid();
  return value as unknown as ScriptGenerationResult;
}
function parseConfirmation(value: unknown, expectedId: string): ScriptConfirmation {
  if (!record(value) || value.script_id !== expectedId || !integer(value.version) || value.version < 1 ||
    !timestamp(value.confirmed_at) || !strings(value.unlocked)) return invalid();
  return value as unknown as ScriptConfirmation;
}
function parseList<T>(value: unknown, parse: Parser<T>): { items: T[] } {
  if (!record(value) || !Array.isArray(value.items)) return invalid();
  return { ...value, items: value.items.map(parse) };
}

/** Same-origin, single-attempt transport. Inject fetch for offline tests. */
export function createCreativeApi(fetcher: typeof fetch = (input, init) => globalThis.fetch(input, init)): CreativeApi {
  async function request<T>(path: string, init: RequestInit, parse: Parser<T>, options: CreativeRequestOptions = {}, defaultTimeout = CREATIVE_REQUEST_TIMEOUT_MS): Promise<T> {
    const method = init.method ?? 'GET';
    const mutation = method !== 'GET';
    const timeoutMs = options.timeoutMs ?? defaultTimeout;
    if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new ApiError('CREATIVE_TIMEOUT_INVALID', '请求等待时间无效。', 0);
    if (options.signal?.aborted) throw new ApiError('CREATIVE_REQUEST_ABORTED', '请求已取消，尚未发送。', 0);
    const controller = new AbortController();
    let timedOut = false;
    let knownRejection: ApiError | null = null;
    const unknown = (reason: CreativeUnknownReason, original?: ApiError): ApiError => {
      const detail: CreativeResultUnknownDetail = { reason, method, path };
      if (original) detail.original = { code: original.code, status: original.status, message: original.message, detail: original.detail };
      return new ApiError('CREATIVE_RESULT_UNKNOWN', '操作结果尚不确定，服务端可能已完成。请先刷新项目或脚本版本核对，不要直接重复提交。', original?.status ?? 0, detail);
    };
    const stopped = (): ApiError => knownRejection ?? (mutation
      ? unknown(timedOut ? 'timeout' : 'aborted')
      : new ApiError(timedOut ? 'CREATIVE_REQUEST_TIMEOUT' : 'CREATIVE_REQUEST_ABORTED',
        timedOut ? '读取创作空间超时，请稍后刷新。' : '已停止等待创作空间响应。', 0));
    const abort = () => controller.abort();
    options.signal?.addEventListener('abort', abort, { once: true });
    const timer = globalThis.setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
    let removeAbortListener = () => {};
    const interrupted = new Promise<never>((_, reject) => {
      const onAbort = () => reject(stopped());
      controller.signal.addEventListener('abort', onAbort, { once: true });
      removeAbortListener = () => controller.signal.removeEventListener('abort', onAbort);
    });
    try {
      const operation = (async () => {
        const response = await fetcher('/api' + path, { ...init, signal: controller.signal });
        if (!response.ok && response.status < 500) knownRejection = new ApiError('HTTP_ERROR', `请求被拒绝（${response.status}），请刷新后核对。`, response.status);
        let body: unknown;
        try { body = await response.json(); }
        catch {
          if (controller.signal.aborted) throw stopped();
          if (response.ok) return invalid();
        }
        if (!response.ok) {
          const error = record(body) && record(body.error) ? body.error : null;
          const failure = new ApiError(error && text(error.code) ? error.code : 'HTTP_ERROR',
            error && text(error.message) ? error.message : `创作空间请求失败（${response.status}）。`,
            response.status, error?.detail);
          if (mutation && response.status >= 500) throw unknown('server_error', failure);
          throw failure;
        }
        return parse(body);
      })();
      return await Promise.race([operation, interrupted]);
    } catch (error) {
      if (controller.signal.aborted) throw stopped();
      if (error instanceof ApiError) {
        if (mutation && error.code === 'CREATIVE_RESPONSE_INVALID') throw unknown('response_invalid', error);
        throw error;
      }
      if (knownRejection) throw knownRejection;
      if (mutation) throw unknown('network');
      throw new ApiError('CREATIVE_NETWORK_ERROR', '创作空间连接中断，请稍后刷新。没有自动重发请求。', 0);
    } finally {
      globalThis.clearTimeout(timer);
      options.signal?.removeEventListener('abort', abort);
      removeAbortListener();
    }
  }

  return {
    listProjects: (limit, options) => {
      if (limit !== undefined && (!integer(limit) || limit < 1)) throw new ApiError('CREATIVE_INPUT_INVALID', '项目列表数量必须为正整数。', 0);
      return request('/projects' + (limit === undefined ? '' : '?limit=' + limit), {}, value => parseList(value, parseProjectListItem), options);
    },
    deleteProject: (id, options) => request('/projects/' + pathId(id), { method: 'DELETE' }, value => {
      if (!record(value) || value.project_id !== id || value.deleted !== true || !timestamp(value.deleted_at) || typeof value.already_deleted !== 'boolean') return invalid();
      return value as { project_id: string; deleted: true; deleted_at: number; already_deleted: boolean };
    }, options),
    getProject: (id, options) => request('/projects/' + pathId(id), {}, value => parseProjectDetail(value, id), options),
    createProject: (name, brief, options) => {
      if (!text(name) || !record(brief)) throw new ApiError('CREATIVE_INPUT_INVALID', '请填写项目名称及有效的需求信息。', 0);
      return request('/projects', json('POST', { name, brief }), parseProject, options);
    },
    updateProject: (id, patch, options) => request('/projects/' + pathId(id), json('PATCH', patch), value => parseProjectUpdate(value, id), options),
    generateScript: (id, input, options) => request('/projects/' + pathId(id) + '/scripts', json('POST', input), value => parseGeneration(value, id), options, CREATIVE_GENERATION_TIMEOUT_MS),
    addManualScript: (projectId, input, options) => request('/projects/' + pathId(projectId) + '/scripts/manual', json('POST', input), parseWrite, options),
    listScripts: (projectId, options) => request('/projects/' + pathId(projectId) + '/scripts', {}, value => parseList(value, item => parseScript(item, undefined, projectId)), options),
    getScript: (id, options) => request('/scripts/' + pathId(id), {}, value => parseScript(value, id), options),
    editScript: (id, input, options) => {
      if (!integer(input.base_version) || input.base_version < 1) throw new ApiError('CREATIVE_INPUT_INVALID', '保存脚本需提供当前项目最新版本。', 0);
      return request('/scripts/' + pathId(id), json('PATCH', input), parseScript, options);
    },
    confirmScript: (id, options) => request('/scripts/' + pathId(id) + '/confirm', json('POST', { confirm: true }), value => parseConfirmation(value, id), options),
  };
}

export const creativeApi = createCreativeApi();
