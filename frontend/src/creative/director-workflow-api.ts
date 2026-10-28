import { ApiError } from '../api';
import { isRecord, isText } from './production-guards';
import { parseDirectorActions, type DirectorAction, type DirectorIntent, type DirectorTurn } from './director-project-api';
import type { CreativeRequestOptions } from './types';

export type WorkflowRole = 'writer' | 'director' | 'cinematographer' | 'editor';
export type WorkflowTurn = DirectorTurn;
export type WorkflowActionKind = 'confirm_script' | 'generation' | 'composition';
export type WorkflowUserAction = 'confirm_script' | 'confirm_generate' | 'confirm_compose';
export interface WorkflowPendingRequest {
  endpoint: 'conversation'; id: string; message: string; session_id?: string;
  revision?: number; selected_node_ids: string[];
}
export interface LegacyPendingRequest {
  endpoint: 'legacy'; id: string; message: string; session_id?: string; revision?: number; intent?: DirectorIntent;
}
export type PendingWorkflowRequest = WorkflowPendingRequest | LegacyPendingRequest;
export interface WorkflowActionPending {
  action_id: string; kind: WorkflowActionKind; client_request_id: string; user_action: WorkflowUserAction;
}
export interface WorkflowError { code: string; message: string; [key: string]: unknown }
export interface WorkflowOutcome {
  status: 'saved' | 'clarify' | 'message' | 'failed' | 'partial'; node_ids: string[];
  error?: WorkflowError; missing_fields?: string[]; goal?: 'auto' | 'script';
}
export interface WorkflowArtifact {
  type: string; node_id?: string; saved?: boolean; persistence?: 'saved';
  target_id?: string | null; target_version?: number | null;
  proposal_id?: string; proposal_version?: number; config_revision?: number;
  config?: Record<string, unknown>; binding?: Record<string, unknown>; [key: string]: unknown;
}
export interface WorkflowQuote {
  quote_id: string; project_id: string; node_id: string; config_revision: number;
  estimated_cost_cny: number; expires_at: number;
  input: { kind: 'image' | 'video'; model_id: string; prompt?: string; params: Record<string, unknown>; input_asset_id?: string | null; [key: string]: unknown };
}
export interface WorkflowExecution {
  execution_id: string; project_id: string; node_id: string; task_id: string; quote_id: string;
  status: string; active: boolean; asset_available: boolean;
  result_status: 'available' | 'pending' | 'unavailable'; asset_id: string | null;
  asset: { id: string; kind: 'image' | 'video'; duration_ms?: number | null } | null;
  progress: number | null; stage: string | null; error: WorkflowError | null; asset_error: WorkflowError | null;
  last_successful_execution?: WorkflowExecution | null;
}
export interface WorkflowSource {
  asset_id: string; node_id?: string | null; execution_id?: string | null;
  metadata: { duration_ms: number; [key: string]: unknown }; [key: string]: unknown;
}
export interface WorkflowCompositionProgress {
  status: string; reason?: string; error?: WorkflowError | null;
  asset_available?: boolean; asset_id?: string | null; result_node_id?: string | null;
  actual_duration_ms?: number | null; asset_error?: WorkflowError | null;
  job?: { status: string; error?: WorkflowError | null; [key: string]: unknown } | null;
}
/** Optional fields reflect the minimal, non-executable fallback on interrupted recovery. */
export interface WorkflowAction extends WorkflowCompositionProgress {
  action_id: string; kind: WorkflowActionKind; requires_user_action: boolean;
  project_id?: string; action_status?: 'proposed' | 'submitted'; request_id?: string;
  submit_client_request_id?: string | null; estimated_cost_cny?: number; expires_at?: number;
  expired?: boolean; stale?: boolean; node_ids?: string[]; script_id?: string; script_version?: number;
  output_mode?: 'clips' | 'film'; auto_film?: boolean; media_status?: string;
  quotes?: WorkflowQuote[]; tasks?: { node_id: string; task_id: string; execution_id: string; quote_id: string }[];
  executions?: WorkflowExecution[]; composition?: WorkflowCompositionProgress | WorkflowAction | null;
  scope?: { generate: true; compose: boolean; ordered_node_ids: string[]; no_trim: boolean; composition_cost_cny: number };
  sources?: WorkflowSource[]; title?: string; no_trim?: boolean; planned_duration_ms?: number;
  generation_action_id?: string | null; current_role?: WorkflowRole; workflow_revision?: number;
  idempotent_replay?: boolean; result?: unknown;
}
export interface WorkflowUsage {
  input_tokens?: number; output_tokens?: number; cost_cny?: number; estimated_cny?: number; has_unknown_cost?: boolean;
}
export interface WorkflowReply {
  flow_version?: 2; session_id: string | null; request_id: string; request_status: 'completed' | 'failed';
  reply: string; reply_type: string; outcome: WorkflowOutcome; artifacts: WorkflowArtifact[];
  actions: WorkflowAction[]; workflow_revision: number; current_role?: WorkflowRole; usage?: WorkflowUsage;
  recovered_after_restart?: boolean; idempotent_replay?: boolean;
}
export interface WorkflowState {
  project_id: string; session_id: string | null; current_role: WorkflowRole; workflow_revision: number;
  creation: Record<string, unknown>; project: Record<string, unknown>;
  outcome: WorkflowOutcome | null; artifacts: WorkflowArtifact[]; actions: WorkflowAction[];
}
export interface WorkflowReceipt {
  client_request_id: string; status: 'pending' | 'completed' | 'failed'; response: WorkflowReply | null;
  error_code?: string | null; artifacts: WorkflowArtifact[]; actions: WorkflowAction[]; may_still_be_running: boolean;
}
export interface WorkflowMessages {
  project_id: string; session_id: string | null; workflow_revision: number; items: DirectorTurn[];
  /** The shared history endpoint still returns legacy proposal actions. Never submit these as new actions. */
  actions: DirectorAction[]; next_cursor: string | null; has_more: boolean;
}

const roles = ['writer', 'director', 'cinematographer', 'editor'];
const kinds = ['confirm_script', 'generation', 'composition'];
const finite = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v);
const version = (v: unknown): v is number => finite(v) && Number.isSafeInteger(v) && v >= 0;
const ids = (v: unknown): v is string[] => Array.isArray(v) && v.every(isText);
const nullableId = (v: unknown) => v === null || isText(v);
const validRole = (v: unknown): v is WorkflowRole => typeof v === 'string' && roles.includes(v);
const errorValue = (v: unknown): v is WorkflowError => isRecord(v) && isText(v.code) && typeof v.message === 'string';
const invalid = (): never => { throw new ApiError('DIRECTOR_RESPONSE_INVALID', '创作结果暂时无法核对，请重新读取会话。', 0); };
const invalidInput = (): never => { throw new ApiError('VALIDATION_FAILED', '请检查这次对话或确认内容后再试。', 422); };

export const workflowUserAction = (kind: WorkflowActionKind): WorkflowUserAction =>
  ({ confirm_script: 'confirm_script', generation: 'confirm_generate', composition: 'confirm_compose' } as const)[kind];

export function parseWorkflowOutcome(v: unknown): WorkflowOutcome {
  if (!isRecord(v) || !['saved', 'clarify', 'message', 'failed', 'partial'].includes(String(v.status)) || !ids(v.node_ids)) return invalid();
  if (v.error !== undefined && !errorValue(v.error)) return invalid();
  if (['failed', 'partial'].includes(String(v.status)) && !v.error) return invalid();
  if (['message', 'clarify', 'failed'].includes(String(v.status)) && v.node_ids.length) return invalid();
  if (v.missing_fields !== undefined && !ids(v.missing_fields)) return invalid();
  // New outcomes intentionally have no goal; old history may retain it.
  if (v.goal !== undefined && !['auto', 'script'].includes(String(v.goal))) return invalid();
  return v as unknown as WorkflowOutcome;
}
export function parseWorkflowArtifacts(v: unknown): WorkflowArtifact[] {
  if (!Array.isArray(v)) return invalid();
  for (const a of v) {
    if (!isRecord(a) || !isText(a.type) || !(a.saved === true || a.persistence === 'saved')) return invalid();
    if (a.node_id !== undefined && !isText(a.node_id)) return invalid();
    if (!isText(a.node_id) && !isText(a.target_id) && !isText(a.proposal_id)) return invalid();
    for (const key of ['target_version', 'proposal_version', 'config_revision']) if (a[key] != null && !version(a[key])) return invalid();
    for (const key of ['config', 'binding']) if (a[key] !== undefined && !isRecord(a[key])) return invalid();
  }
  return v as WorkflowArtifact[];
}
function quote(v: unknown, projectId: string): v is WorkflowQuote {
  return isRecord(v) && isText(v.quote_id) && v.project_id === projectId && isText(v.node_id)
    && version(v.config_revision) && v.config_revision > 0 && finite(v.estimated_cost_cny) && v.estimated_cost_cny >= 0
    && finite(v.expires_at) && isRecord(v.input) && ['image', 'video'].includes(String(v.input.kind))
    && isText(v.input.model_id) && isRecord(v.input.params)
    && (v.input.prompt === undefined || typeof v.input.prompt === 'string')
    && (v.input.input_asset_id === undefined || nullableId(v.input.input_asset_id));
}
function execution(v: unknown, projectId: string): v is WorkflowExecution {
  return isRecord(v) && v.project_id === projectId && ['execution_id', 'node_id', 'task_id', 'quote_id', 'status'].every(k => isText(v[k]))
    && typeof v.active === 'boolean' && typeof v.asset_available === 'boolean' && nullableId(v.asset_id)
    && ['available', 'pending', 'unavailable'].includes(String(v.result_status))
    && (v.progress === null || finite(v.progress)) && (v.stage === null || typeof v.stage === 'string')
    && (v.error === null || errorValue(v.error)) && (v.asset_error === null || errorValue(v.asset_error))
    && (v.asset === null || isRecord(v.asset) && isText(v.asset.id) && ['image', 'video'].includes(String(v.asset.kind)))
    && (!v.asset_available || v.status === 'succeeded' && v.result_status === 'available' && isRecord(v.asset) && v.asset.id === v.asset_id);
}
export function parseWorkflowAction(v: unknown, projectId?: string, actionId?: string): WorkflowAction {
  if (!isRecord(v) || !isText(v.action_id) || !kinds.includes(String(v.kind)) || typeof v.requires_user_action !== 'boolean'
    || !['proposed', 'stale', 'running', 'completed', 'partial', 'failed', 'unknown'].includes(String(v.status))
    || actionId !== undefined && v.action_id !== actionId || projectId !== undefined && v.project_id !== undefined && v.project_id !== projectId) return invalid();
  // A startup recovery may preserve an action whose current view cannot be read.
  if (v.status === 'unknown' && v.requires_user_action === false) return v as unknown as WorkflowAction;
  if (!isText(v.project_id) || !['proposed', 'submitted'].includes(String(v.action_status)) || !isText(v.request_id)
    || !finite(v.estimated_cost_cny) || v.estimated_cost_cny < 0 || !finite(v.expires_at)
    || typeof v.expired !== 'boolean' || !nullableId(v.submit_client_request_id)) return invalid();
  if (v.current_role !== undefined && !validRole(v.current_role) || v.workflow_revision !== undefined && !version(v.workflow_revision)) return invalid();
  if (v.requires_user_action && (v.action_status !== 'proposed' || v.expired || v.stale === true)) return invalid();
  if (v.action_status === 'proposed' && !['proposed', 'stale'].includes(v.status as string)) return invalid();
  if (v.kind === 'confirm_script') {
    if (!isText(v.script_id) || !version(v.script_version) || v.script_version < 1 || !ids(v.node_ids) || typeof v.stale !== 'boolean') return invalid();
    if (v.action_status === 'submitted' && v.status !== 'completed') return invalid();
  } else if (v.kind === 'generation') {
    if (!ids(v.node_ids) || !v.node_ids.length || new Set(v.node_ids).size !== v.node_ids.length
      || !['clips', 'film'].includes(String(v.output_mode)) || typeof v.auto_film !== 'boolean'
      || v.auto_film !== (v.output_mode === 'film') || !['proposed', 'running', 'completed', 'partial', 'failed'].includes(String(v.media_status))
      || !Array.isArray(v.quotes) || v.quotes.length !== v.node_ids.length || !v.quotes.every(q => quote(q, v.project_id as string))
      || v.quotes.some((q, i) => q.node_id !== (v.node_ids as string[])[i]) || !Array.isArray(v.tasks) || !Array.isArray(v.executions)
      || !v.executions.every(e => execution(e, v.project_id as string)) || !isRecord(v.scope) || v.scope.generate !== true
      || typeof v.scope.compose !== 'boolean' || v.scope.compose !== v.auto_film || !ids(v.scope.ordered_node_ids)
      || JSON.stringify(v.scope.ordered_node_ids) !== JSON.stringify(v.node_ids) || typeof v.scope.no_trim !== 'boolean'
      || !finite(v.scope.composition_cost_cny) || v.scope.composition_cost_cny < 0) return invalid();
    for (const task of v.tasks) if (!isRecord(task) || !['node_id', 'task_id', 'execution_id', 'quote_id'].every(k => isText(task[k])) || !v.node_ids.includes(String(task.node_id))) return invalid();
    if (v.action_status === 'submitted' && (v.tasks.length !== v.node_ids.length || v.executions.length !== v.node_ids.length)) return invalid();
    if (v.action_status === 'submitted') {
      for (let i = 0; i < v.node_ids.length; i++) {
        const task = v.tasks[i] as Record<string, unknown>, run = v.executions[i] as WorkflowExecution, frozen = v.quotes[i] as WorkflowQuote;
        if (task.node_id !== v.node_ids[i] || run.node_id !== v.node_ids[i] || task.quote_id !== frozen.quote_id
          || run.quote_id !== frozen.quote_id || run.execution_id !== task.execution_id || run.task_id !== task.task_id) return invalid();
      }
      const runs = v.executions as WorkflowExecution[], ready = runs.filter(e => e.status === 'succeeded' && e.asset_available).length;
      const progress = runs.some(e => e.active) ? 'running' : ready === runs.length ? 'completed' : ready ? 'partial' : 'failed';
      if (v.media_status !== progress || v.output_mode === 'clips' && v.status !== progress) return invalid();
    } else if (v.tasks.length || v.executions.length || v.media_status !== 'proposed') return invalid();
    if (v.composition !== null && v.composition !== undefined) {
      if (!isRecord(v.composition) || !isText(v.composition.status)) return invalid();
      if (v.composition.action_id !== undefined) parseWorkflowAction(v.composition, v.project_id);
      else if (!['waiting', 'blocked', 'failed'].includes(v.composition.status)) return invalid();
    }
    if (v.output_mode === 'film' && (v.quotes.some(q => q.input.kind !== 'video') || v.scope.no_trim !== true)) return invalid();
    if (v.output_mode === 'film' && v.status === 'completed' && (!isRecord(v.composition) || v.composition.asset_available !== true)) return invalid();
  } else {
    if (!Array.isArray(v.sources) || !v.sources.length || !v.sources.every(s => isRecord(s) && isText(s.asset_id) && isRecord(s.metadata) && finite(s.metadata.duration_ms) && s.metadata.duration_ms > 0)
      || typeof v.title !== 'string' || v.no_trim !== true || typeof v.asset_available !== 'boolean'
      || !nullableId(v.asset_id) || !nullableId(v.result_node_id) || !(v.actual_duration_ms === null || finite(v.actual_duration_ms))
      || !finite(v.planned_duration_ms) || v.planned_duration_ms <= 0) return invalid();
    if (v.asset_available && (!isText(v.asset_id) || !finite(v.actual_duration_ms) || v.status !== 'completed')) return invalid();
    if (v.status === 'completed' && !v.asset_available || !v.asset_available && (v.asset_id !== null || v.actual_duration_ms !== null)) return invalid();
    if (v.generation_action_id && v.requires_user_action) return invalid();
  }
  if (v.error != null && !errorValue(v.error) || v.asset_error != null && !errorValue(v.asset_error)) return invalid();
  return v as unknown as WorkflowAction;
}
export function parseWorkflowActions(v: unknown, projectId?: string): WorkflowAction[] {
  if (!Array.isArray(v)) return invalid();
  return v.map(a => parseWorkflowAction(a, projectId));
}
export function workflowOutcome(v: { outcome?: unknown; artifacts?: unknown; actions?: unknown; request_status?: string }): WorkflowOutcome {
  if (v.outcome !== undefined && v.outcome !== null) return parseWorkflowOutcome(v.outcome);
  const artifacts = v.artifacts === undefined ? [] : parseWorkflowArtifacts(v.artifacts);
  const failed = v.request_status === 'failed';
  return { status: failed ? 'failed' : artifacts.length ? 'saved' : 'message', node_ids: failed ? [] : artifacts.flatMap(a => a.node_id ? [a.node_id] : []),
    ...(failed ? { error: { code: 'DIRECTOR_REQUEST_FAILED', message: '这次创作未完成，请核对后继续。' } } : {}) };
}
export function workflowNodeIds(v: { outcome?: unknown; artifacts?: unknown; actions?: unknown; request_status?: string }): string[] {
  const outcome = workflowOutcome(v);
  if (!['saved', 'partial'].includes(outcome.status)) return [];
  const artifacts = v.artifacts === undefined ? [] : parseWorkflowArtifacts(v.artifacts);
  return [...new Set([...outcome.node_ids, ...artifacts.flatMap(a => a.node_id ? [a.node_id] : [])])];
}
export function parseWorkflowReply(v: unknown, requestId: string, projectId?: string, legacy = false): WorkflowReply {
  if (!isRecord(v) || v.request_id !== requestId || !['completed', 'failed'].includes(String(v.request_status))
    || !(isText(v.session_id) || v.recovered_after_restart === true && v.session_id === null)
    || typeof v.reply !== 'string' || typeof v.reply_type !== 'string' || !version(v.workflow_revision)) return invalid();
  if (!legacy && v.flow_version !== 2 || v.current_role !== undefined && !validRole(v.current_role)) return invalid();
  if (!legacy && v.recovered_after_restart !== true && !validRole(v.current_role)) return invalid();
  const artifacts = parseWorkflowArtifacts(v.artifacts);
  const actions = legacy ? (parseDirectorActions(v.actions), []) : parseWorkflowActions(v.actions, projectId);
  const outcome = workflowOutcome({ ...v, artifacts });
  if (!legacy && v.outcome === undefined || v.request_status === 'failed' && !['failed', 'partial'].includes(outcome.status)) return invalid();
  if (outcome.status === 'saved' && !artifacts.length && !actions.length && !legacy) return invalid();
  if (v.usage !== undefined) {
    if (!isRecord(v.usage)) return invalid();
    for (const k of ['input_tokens', 'output_tokens', 'cost_cny', 'estimated_cny']) if (v.usage[k] !== undefined && (!finite(v.usage[k]) || v.usage[k] < 0)) return invalid();
    if (v.usage.has_unknown_cost !== undefined && typeof v.usage.has_unknown_cost !== 'boolean') return invalid();
  }
  return { ...v, artifacts, actions, outcome } as unknown as WorkflowReply;
}
export function parseWorkflowState(v: unknown, projectId: string): WorkflowState {
  if (!isRecord(v) || v.project_id !== projectId || !nullableId(v.session_id) || !validRole(v.current_role)
    || !version(v.workflow_revision) || !isRecord(v.creation) || !isRecord(v.project)) return invalid();
  if (v.creation.project_id !== projectId || v.project.project_id !== projectId) return invalid();
  return { ...v, outcome: v.outcome == null ? null : parseWorkflowOutcome(v.outcome), artifacts: parseWorkflowArtifacts(v.artifacts), actions: parseWorkflowActions(v.actions, projectId) } as WorkflowState;
}
export function parseWorkflowMessages(v: unknown, projectId: string): WorkflowMessages {
  if (!isRecord(v) || v.project_id !== projectId || !nullableId(v.session_id) || !version(v.workflow_revision)
    || !Array.isArray(v.items) || !nullableId(v.next_cursor) || typeof v.has_more !== 'boolean') return invalid();
  for (const t of v.items) {
    if (!isRecord(t) || !isText(t.turn_id) || !isText(t.request_id) || typeof t.user !== 'string' || typeof t.reply !== 'string'
      || typeof t.reply_type !== 'string' || !finite(t.created_at) || !isRecord(t.structured)) return invalid();
    if (t.structured.outcome !== undefined) parseWorkflowOutcome(t.structured.outcome);
    if (t.structured.current_role !== undefined && !validRole(t.structured.current_role)) return invalid();
    if (t.structured.artifacts !== undefined) parseWorkflowArtifacts(t.structured.artifacts);
  }
  return { ...v, actions: parseDirectorActions(v.actions) } as unknown as WorkflowMessages;
}

export function validWorkflowPending(v: unknown): v is WorkflowPendingRequest {
  return isRecord(v) && v.endpoint === 'conversation' && isText(v.id) && v.id.length <= 200
    && typeof v.message === 'string' && !!v.message.trim() && v.message.length <= 20000
    && (v.session_id === undefined || isText(v.session_id)) && (v.revision === undefined || version(v.revision))
    && ids(v.selected_node_ids) && v.selected_node_ids.length <= 40
    && Object.keys(v).every(k => ['endpoint', 'id', 'message', 'session_id', 'revision', 'selected_node_ids'].includes(k));
}
export function validWorkflowActionPending(v: unknown): v is WorkflowActionPending {
  return isRecord(v) && isText(v.action_id) && isText(v.client_request_id) && v.client_request_id.length <= 200
    && kinds.includes(String(v.kind)) && v.user_action === workflowUserAction(v.kind as WorkflowActionKind)
    && Object.keys(v).every(k => ['action_id', 'kind', 'client_request_id', 'user_action'].includes(k));
}
export function createWorkflowDirectorApi(fetcher: typeof fetch = (...args) => fetch(...args)) {
  async function request<T>(path: string, body: unknown, parse: (value: unknown) => T, options: CreativeRequestOptions = {}): Promise<T> {
    if (options.signal?.aborted) throw new ApiError('REQUEST_ABORTED', '已停止等待。', 0);
    const controller = new AbortController(), abort = () => controller.abort();
    options.signal?.addEventListener('abort', abort, { once: true });
    const write = body !== undefined, timer = setTimeout(abort, options.timeoutMs ?? (write ? 120000 : 20000));
    let knownFailure = false;
    try {
      const response = await fetcher('/api' + path, { method: write ? 'POST' : 'GET', signal: controller.signal,
        ...(write ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {}) });
      knownFailure = !response.ok && response.status >= 400 && response.status < 500;
      const value: unknown = await response.json().catch(() => null);
      if (!response.ok) {
        const e = isRecord(value) && isRecord(value.error) ? value.error : {};
        throw new ApiError(response.status === 422 ? 'VALIDATION_FAILED' : isText(e.code) ? e.code : 'HTTP_ERROR',
          response.status === 422 ? '这次内容或设置不完整，请检查后再试。' : isText(e.message) ? e.message : '创作服务暂时无法响应，请稍后核对。', response.status, e.detail);
      }
      return parse(value);
    } catch (error) {
      if (knownFailure && error instanceof ApiError) throw error;
      if (write) throw new ApiError('DIRECTOR_RESULT_UNKNOWN', '还未收到完整结果，请核对原操作；不要重复发送。', 0);
      if (error instanceof ApiError) throw error;
      throw new ApiError('DIRECTOR_READ_FAILED', '暂时无法读取创作结果，请稍后重试。', 0);
    } finally { clearTimeout(timer); options.signal?.removeEventListener('abort', abort); }
  }
  const base = (id: string) => { if (!isText(id)) throw new ApiError('PROJECT_REQUIRED', '请先选择项目。', 0); return '/projects/' + encodeURIComponent(id) + '/director'; };
  const receipt = (id: string, requestId: string, legacy: boolean, options?: CreativeRequestOptions) => request(
    base(id) + (legacy ? '/requests/' : '/conversation/requests/') + encodeURIComponent(requestId), undefined, v => {
      if (!isRecord(v) || v.client_request_id !== requestId || !['pending', 'completed', 'failed'].includes(String(v.status))) return invalid();
      const response = v.response == null ? null : parseWorkflowReply(v.response, requestId, id, legacy);
      if (v.status === 'completed' && !response || response && response.request_status !== v.status) return invalid();
      return { ...v, response, artifacts: legacy ? response?.artifacts ?? [] : parseWorkflowArtifacts(v.artifacts),
        actions: legacy ? [] : parseWorkflowActions(v.actions, id), may_still_be_running: v.status === 'pending' } as WorkflowReceipt;
    }, options);
  return {
    conversation(id: string, pending: WorkflowPendingRequest, options?: CreativeRequestOptions) {
      if (!validWorkflowPending(pending)) return Promise.reject(new ApiError('VALIDATION_FAILED', '请检查本次对话内容。', 422));
      return request(base(id) + '/conversation', { client_request_id: pending.id, message: pending.message,
        ...(pending.session_id !== undefined ? { session_id: pending.session_id } : {}),
        ...(pending.revision !== undefined ? { expected_workflow_revision: pending.revision } : {}),
        selected_node_ids: pending.selected_node_ids }, v => parseWorkflowReply(v, pending.id, id), options);
    },
    state(id: string, selectedNodeIds: string[] = [], options?: CreativeRequestOptions) {
      if (!ids(selectedNodeIds) || selectedNodeIds.length > 40) return invalidInput();
      const query = selectedNodeIds.map(nodeId => 'selected_node_id=' + encodeURIComponent(nodeId)).join('&');
      return request(base(id) + '/conversation/state' + (query ? '?' + query : ''), undefined, v => parseWorkflowState(v, id), options);
    },
    receipt: (id: string, requestId: string, options?: CreativeRequestOptions) => receipt(id, requestId, false, options),
    legacyReceipt: (id: string, requestId: string, options?: CreativeRequestOptions) => receipt(id, requestId, true, options),
    action: (id: string, actionId: string, options?: CreativeRequestOptions) => request(base(id) + '/actions/' + encodeURIComponent(actionId), undefined, v => parseWorkflowAction(v, id, actionId), options),
    confirm(id: string, pending: WorkflowActionPending, options?: CreativeRequestOptions) {
      if (!validWorkflowActionPending(pending)) return invalidInput();
      return request(base(id) + '/actions/' + encodeURIComponent(pending.action_id) + '/confirm',
        { client_request_id: pending.client_request_id, user_action: pending.user_action }, v => {
          const action = parseWorkflowAction(v, id, pending.action_id);
          if (action.kind !== pending.kind || action.submit_client_request_id !== pending.client_request_id || action.action_status !== 'submitted') return invalid();
          return action;
        }, options);
    },
    messages: (id: string, cursor?: string, options?: CreativeRequestOptions) => request(base(id) + '/messages' + (cursor ? '?cursor=' + encodeURIComponent(cursor) : ''), undefined, v => parseWorkflowMessages(v, id), options),
  };
}
export const workflowDirectorApi = createWorkflowDirectorApi();
