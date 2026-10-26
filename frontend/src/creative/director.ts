import { ApiError } from '../api';
import { CREATIVE_REQUEST_TIMEOUT_MS } from './api';
import { isCount, isPositiveInteger, isRecord, isText } from './production-guards';
import type { CreativeRequestOptions, CreativeScriptSummary, ProjectBrief } from './types';

export type DirectorStageKey = 'brief' | 'script' | 'storyboard' | 'generation' | 'delivery';
export type DirectorStageStatus = 'locked' | 'current' | 'done';
export interface DirectorStage {
  key: DirectorStageKey;
  label: string;
  status: DirectorStageStatus;
  visible: boolean;
  actions: string[];
}
export interface DirectorConfirmation {
  /** A historical confirmation may exist while valid is false. Never infer validity from a timestamp. */
  confirmed: boolean;
  valid: boolean;
  status?: 'active' | 'invalidated' | 'superseded';
  reason?: string | null;
  object_version?: number;
  confirmed_at?: number | null;
  invalidated_at?: number | null;
}
export interface DirectorWarning {
  object: string;
  reason: string | null;
  message: string;
  version?: number;
}
export interface DirectorReadiness {
  project_id: string;
  project: { name: string; status: string | null };
  facts: { brief: ProjectBrief; ready: boolean };
  current: {
    stage: DirectorStageKey;
    script: CreativeScriptSummary | null;
    confirmation: DirectorConfirmation;
  };
  stages: DirectorStage[];
  allowed_actions: string[];
  warnings: DirectorWarning[];
  /** Informational counts only; they do not authorize a stage or establish confirmation. */
  counts: { scripts: number; shots: number; takes: number; films: number };
}

const stageKeys: Record<string, DirectorStageKey> = {
  requirements: 'brief', script: 'script', storyboard: 'storyboard', video: 'generation', delivery: 'delivery',
};
const invalid = (): never => {
  throw new ApiError('DIRECTOR_RESPONSE_INVALID', '导演台状态数据不完整或版本不兼容，请刷新核对；暂不能继续生成。', 0);
};
const stringList = (value: unknown): value is string[] => Array.isArray(value) && value.every(isText);
const nullableTime = (value: unknown) => value === null || (typeof value === 'number' && Number.isFinite(value) && value >= 0);
function stageKey(value: unknown): DirectorStageKey {
  if (typeof value !== 'string' || !Object.hasOwn(stageKeys, value)) return invalid();
  return stageKeys[value];
}
function parseConfirmation(value: unknown, script: CreativeScriptSummary | null): DirectorConfirmation {
  if (!isRecord(value) || typeof value.confirmed !== 'boolean' || typeof value.valid !== 'boolean') return invalid();
  if (value.status !== undefined && !['active', 'invalidated', 'superseded'].includes(String(value.status))) return invalid();
  if (value.reason !== undefined && value.reason !== null && typeof value.reason !== 'string') return invalid();
  if (value.object_version !== undefined && !isPositiveInteger(value.object_version)) return invalid();
  for (const key of ['confirmed_at', 'invalidated_at']) if (value[key] !== undefined && !nullableTime(value[key])) return invalid();
  if (value.valid && (!value.confirmed || value.status !== 'active' || !script || value.object_version !== script.version)) return invalid();
  return { ...value } as unknown as DirectorConfirmation;
}

/** Validate the real GET contract, mapping only its five group names. Never infer readiness locally. */
export function parseDirector(value: unknown, expectedProjectId?: string): DirectorReadiness {
  if (!isRecord(value) || !isText(value.project_id) || (expectedProjectId !== undefined && value.project_id !== expectedProjectId) ||
    !isRecord(value.project) || !isText(value.project.name) || !(value.project.status === null || isText(value.project.status)) ||
    !isRecord(value.facts) || !isRecord(value.facts.brief) || typeof value.facts.ready !== 'boolean' ||
    !isRecord(value.current) || !Array.isArray(value.stages) || value.stages.length !== 5 ||
    !stringList(value.allowed_actions) || !Array.isArray(value.warnings) || !isRecord(value.counts)) return invalid();
  let script: CreativeScriptSummary | null = null;
  if (value.current.script !== null) {
    const candidate = value.current.script;
    if (!isRecord(candidate) || !isText(candidate.script_id) || !isPositiveInteger(candidate.version) ||
      !(candidate.source === null || isText(candidate.source)) || !nullableTime(candidate.confirmed_at)) return invalid();
    script = { script_id: candidate.script_id, version: candidate.version, source: candidate.source, confirmed_at: candidate.confirmed_at as number | null };
  }
  const stages = value.stages.map((item): DirectorStage => {
    if (!isRecord(item) || !isText(item.label) || !['locked', 'current', 'done'].includes(String(item.status)) ||
      typeof item.visible !== 'boolean' || !stringList(item.actions)) return invalid();
    return { key: stageKey(item.key), label: item.label, status: item.status as DirectorStageStatus, visible: item.visible, actions: [...item.actions] };
  });
  if (new Set(stages.map(stage => stage.key)).size !== 5) return invalid();
  const warnings = value.warnings.map((item): DirectorWarning => {
    if (!isRecord(item) || !isText(item.object) || !isText(item.message) || !(item.reason === null || typeof item.reason === 'string') ||
      (item.version !== undefined && !isPositiveInteger(item.version))) return invalid();
    return { object: item.object, reason: item.reason, message: item.message, ...(item.version === undefined ? {} : { version: item.version as number }) };
  });
  const counts = value.counts;
  if (!['scripts', 'shots', 'takes', 'films'].every(key => isCount(counts[key]))) return invalid();
  return {
    project_id: value.project_id,
    project: { name: value.project.name, status: value.project.status },
    facts: { brief: { ...value.facts.brief }, ready: value.facts.ready },
    current: { stage: stageKey(value.current.stage), script, confirmation: parseConfirmation(value.current.confirmation, script) },
    stages, allowed_actions: [...value.allowed_actions], warnings,
    counts: { scripts: counts.scripts as number, shots: counts.shots as number, takes: counts.takes as number, films: counts.films as number },
  };
}

/** Read-only, same-origin and single-attempt. Inject fetch for tests; no model calls or business writes. */
export function createDirectorReader(fetcher: typeof fetch = (input, init) => globalThis.fetch(input, init)) {
  return async function read(projectId: string, options: CreativeRequestOptions = {}): Promise<DirectorReadiness> {
    if (!isText(projectId)) throw new ApiError('CREATIVE_INPUT_INVALID', '缺少项目编号。', 0);
    const timeoutMs = options.timeoutMs ?? CREATIVE_REQUEST_TIMEOUT_MS;
    if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new ApiError('CREATIVE_TIMEOUT_INVALID', '请求等待时间无效。', 0);
    if (options.signal?.aborted) throw new ApiError('CREATIVE_REQUEST_ABORTED', '请求已取消，尚未发送。', 0);
    const controller = new AbortController();
    let timedOut = false;
    let knownRejection: ApiError | null = null;
    const stopped = () => knownRejection ?? new ApiError(timedOut ? 'CREATIVE_REQUEST_TIMEOUT' : 'CREATIVE_REQUEST_ABORTED',
      timedOut ? '读取导演台状态超时，请稍后刷新。' : '已停止等待导演台响应。', 0);
    const abort = () => controller.abort();
    options.signal?.addEventListener('abort', abort, { once: true });
    const timer = globalThis.setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
    let cleanup = () => {};
    const interrupted = new Promise<never>((_, reject) => {
      const onAbort = () => reject(stopped());
      controller.signal.addEventListener('abort', onAbort, { once: true });
      cleanup = () => controller.signal.removeEventListener('abort', onAbort);
    });
    try {
      const operation = (async () => {
        const response = await fetcher(`/api/projects/${encodeURIComponent(projectId)}/director`, { method: 'GET', signal: controller.signal });
        if (!response.ok && response.status < 500) knownRejection = new ApiError('HTTP_ERROR', `导演台请求被拒绝（${response.status}）。`, response.status);
        let body: unknown;
        try { body = await response.json(); }
        catch { if (controller.signal.aborted) throw stopped(); if (response.ok) return invalid(); }
        if (!response.ok) {
          const error = isRecord(body) && isRecord(body.error) ? body.error : null;
          if ([405, 501].includes(response.status) || (response.status === 404 && !(error && isText(error.code)))) {
            knownRejection = new ApiError('DIRECTOR_UNAVAILABLE', '当前服务尚未提供导演台状态，请更新或核对后端连接；暂不能继续生成。', response.status);
            throw knownRejection;
          }
          throw new ApiError(error && isText(error.code) ? error.code : 'HTTP_ERROR',
            error && isText(error.message) ? error.message : `读取导演台状态失败（${response.status}）。`, response.status, error?.detail);
        }
        return parseDirector(body, projectId);
      })();
      return await Promise.race([operation, interrupted]);
    } catch (error) {
      if (controller.signal.aborted) throw stopped();
      if (error instanceof ApiError) throw error;
      if (knownRejection) throw knownRejection;
      throw new ApiError('CREATIVE_NETWORK_ERROR', '无法读取导演台状态，请稍后刷新；没有自动重试。', 0);
    } finally {
      globalThis.clearTimeout(timer);
      options.signal?.removeEventListener('abort', abort);
      cleanup();
    }
  };
}

export const readDirector = createDirectorReader();
