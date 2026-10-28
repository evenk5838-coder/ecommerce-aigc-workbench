import { isRecord, isText } from './production-guards';
import { validWorkflowDraftScope, type WorkflowDraftScope } from './workflow-draft-scope';
import {
  parseWorkflowAction, parseWorkflowMessages, validWorkflowActionPending, validWorkflowPending,
  workflowNodeIds, workflowOutcome, type LegacyPendingRequest, type PendingWorkflowRequest,
  type WorkflowAction, type WorkflowActionPending, type WorkflowRole, type WorkflowTurn,
} from './director-workflow-api';

export interface StoredWorkflowConversation {
  projectId: string;
  draft: string;
  draftScope?: WorkflowDraftScope;
  pending: PendingWorkflowRequest | null;
  actionPending: WorkflowActionPending | null;
  legacyActionPending: boolean;
  turns: WorkflowTurn[];
  actions: WorkflowAction[];
  sessionId: string | null;
  workflowRevision: number | null;
  currentRole: WorkflowRole | null;
}
const validRevision = (v: unknown): v is number => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0;
const roles = ['writer', 'director', 'cinematographer', 'editor'];
export const workflowConversationKey = (projectId: string) => 'material-project-director-v3:' + encodeURIComponent(projectId);
export const legacyConversationKey = (projectId: string) => 'material-project-director-v2:' + encodeURIComponent(projectId);
const empty = (projectId: string): StoredWorkflowConversation => ({ projectId, draft: '', pending: null,
  actionPending: null, legacyActionPending: false, turns: [], actions: [], sessionId: null, workflowRevision: null, currentRole: null });

function legacyPending(v: unknown): LegacyPendingRequest | null {
  if (!isRecord(v) || !isText(v.id) || typeof v.message !== 'string'
    || v.session_id !== undefined && !isText(v.session_id) || v.revision !== undefined && !validRevision(v.revision)
    || v.intent !== undefined && !['auto', 'script'].includes(String(v.intent))) return null;
  return { endpoint: 'legacy', id: v.id, message: v.message,
    ...(v.session_id !== undefined ? { session_id: v.session_id as string } : {}),
    ...(v.revision !== undefined ? { revision: v.revision as number } : {}),
    ...(v.intent !== undefined ? { intent: v.intent as 'auto' | 'script' } : {}) };
}
function storedObject(raw: string | null, projectId: string): Record<string, unknown> | null {
  try { const value: unknown = JSON.parse(raw ?? 'null'); return isRecord(value) && value.projectId === projectId && typeof value.draft === 'string' ? value : null; }
  catch { return null; }
}
/** Parse without sending, confirming, dropping a valid old request or changing its payload. */
export function parseStoredWorkflowConversation(raw: string | null, projectId: string): StoredWorkflowConversation {
  const value = storedObject(raw, projectId), result = empty(projectId);
  if (!value) return result;
  result.draft = value.draft as string;
  if (result.draft.trim() && validWorkflowDraftScope(value.draftScope)) result.draftScope = { ...value.draftScope, nodeIds: [...value.draftScope.nodeIds] };
  if (validWorkflowPending(value.pending)) result.pending = { ...value.pending, selected_node_ids: [...value.pending.selected_node_ids] };
  else if (isRecord(value.pending) && (value.pending.endpoint === undefined || value.pending.endpoint === 'legacy')) result.pending = legacyPending(value.pending);
  if (validWorkflowActionPending(value.actionPending)) result.actionPending = { ...value.actionPending };
  result.legacyActionPending = value.legacyActionPending === true || value.actionPending === true;
  result.sessionId = isText(value.sessionId) ? value.sessionId : null;
  result.workflowRevision = validRevision(value.workflowRevision) ? value.workflowRevision : null;
  result.currentRole = typeof value.currentRole === 'string' && roles.includes(value.currentRole) ? value.currentRole as WorkflowRole : null;
  // One damaged cached display item must not discard a draft or pending write.
  if (Array.isArray(value.turns)) for (const turn of value.turns) {
    try {
      result.turns.push(...parseWorkflowMessages({ project_id: projectId, session_id: null,
        workflow_revision: 0, items: [turn], actions: [], has_more: false, next_cursor: null }, projectId).items);
    } catch { /* Fetch this history item again; keep the pending request intact. */ }
  }
  if (Array.isArray(value.actions)) for (const action of value.actions) {
    try { result.actions.push(parseWorkflowAction(action, projectId)); }
    catch { /* A cached action is not authority to submit anything. */ }
  }
  return result;
}
/** v3 takes precedence once saved. Reading v2 never deletes or rewrites it. */
export function readWorkflowConversation(storage: Pick<Storage, 'getItem'>, projectId: string): StoredWorkflowConversation {
  const latest = storage.getItem(workflowConversationKey(projectId));
  if (storedObject(latest, projectId)) return parseStoredWorkflowConversation(latest, projectId);
  return parseStoredWorkflowConversation(storage.getItem(legacyConversationKey(projectId)), projectId);
}
// Budget only recoverable display data. Drafts and exact write identities are never trimmed.
const DISPLAY_CACHE_CHARACTERS = 128 * 1024;
const SMALL_CACHE_CHARACTERS = 16 * 1024;
const quotaExceeded = (error: unknown) => isRecord(error)
  && ['QuotaExceededError', 'NS_ERROR_DOM_QUOTA_REACHED'].includes(String(error.name));

function compactTurn(turn: WorkflowTurn): WorkflowTurn {
  const structured: Record<string, unknown> = {};
  for (const key of ['flow_version', 'current_role', 'outcome']) {
    if (turn.structured[key] !== undefined) structured[key] = turn.structured[key];
  }
  // Quotes/configuration are fetched from action/state endpoints. Keep saved artifact identity
  // and outcome for the acknowledgement without duplicating full prompts inside every turn.
  if (Array.isArray(turn.structured.artifacts)) structured.artifacts = turn.structured.artifacts.map(artifact => {
    if (!isRecord(artifact)) return artifact;
    return Object.fromEntries(['type', 'node_id', 'target_id', 'target_version', 'proposal_id',
      'proposal_version', 'config_revision', 'saved', 'persistence']
      .filter(key => artifact[key] !== undefined).map(key => [key, artifact[key]]));
  });
  return { ...turn, structured };
}

function serializeConversation(value: StoredWorkflowConversation, budget: number): string {
  const cached = { version: 3, projectId: value.projectId, draft: value.draft, ...(value.draftScope ? { draftScope: value.draftScope } : {}), pending: value.pending,
    actionPending: value.actionPending, legacyActionPending: value.legacyActionPending,
    sessionId: value.sessionId, workflowRevision: value.workflowRevision, currentRole: value.currentRole,
    turns: [] as WorkflowTurn[], actions: [] as WorkflowAction[] };
  const recentTurns = [...value.turns].sort((a, b) => b.created_at - a.created_at).slice(0, 12);
  const recentActions = [...value.actions].sort((a, b) => {
    const pendingId = value.actionPending?.action_id;
    return Number(b.action_id === pendingId) - Number(a.action_id === pendingId)
      || (b.expires_at ?? 0) - (a.expires_at ?? 0);
  }).slice(0, 8);
  function retain<T>(item: T, target: T[]): boolean {
    const size = JSON.stringify(item).length + 1;
    if (size > budget) return false;
    target.push(item); budget -= size; return true;
  }
  function retainTurn(turn: WorkflowTurn) {
    if (!retain(turn, cached.turns)) retain(compactTurn(turn), cached.turns);
  }
  // Prefer the most recent acknowledgement, then recoverable action views and older turns.
  if (budget > 0 && recentTurns[0]) retainTurn(recentTurns[0]);
  if (budget > 0) for (const action of recentActions) retain(action, cached.actions);
  if (budget > 0) for (const turn of recentTurns.slice(1)) retainTurn(turn);
  cached.turns.sort((a, b) => a.created_at - b.created_at);
  return JSON.stringify(cached);
}

/** Call before POST. Only quota errors reduce display cache; an unsaved core still stops the write. */
export function writeWorkflowConversation(storage: Pick<Storage, 'setItem'>, value: StoredWorkflowConversation): void {
  const key = workflowConversationKey(value.projectId);
  for (const budget of [DISPLAY_CACHE_CHARACTERS, SMALL_CACHE_CHARACTERS, 0]) {
    try { storage.setItem(key, serializeConversation(value, budget)); return; }
    catch (error) { if (!quotaExceeded(error) || budget === 0) throw error; }
  }
}
export const workflowRoleLabel = (role: WorkflowRole | null | undefined) => role
  ? ({ writer: 'AI 编剧', director: 'AI 导演', cinematographer: 'AI 摄影师', editor: 'AI 剪辑师' } as const)[role]
  : '创作助手';

/** Historical role is derived from saved server data, never from the model's prose. */
export function presentWorkflowTurn(turn: WorkflowTurn) {
  const outcome = workflowOutcome(turn.structured), nodeIds = workflowNodeIds(turn.structured);
  const explicitRole = turn.structured.current_role;
  const artifacts = Array.isArray(turn.structured.artifacts) ? turn.structured.artifacts.filter(isRecord) : [];
  const inferred: WorkflowRole | null = artifacts.some(a => a.type === 'script') || outcome.goal === 'script' ? 'writer'
    : artifacts.some(a => a.type === 'image' || a.type === 'shot_plan') ? 'director'
      : artifacts.some(a => a.type === 'video') ? 'cinematographer' : null;
  const role = typeof explicitRole === 'string' && roles.includes(explicitRole) ? explicitRole as WorkflowRole : inferred;
  const reason = outcome.error?.code === 'VALIDATION_FAILED' ? '生成内容不完整，请调整要求后再试。'
    : outcome.error?.code === 'DIRECTOR_REQUEST_INTERRUPTED' ? '上次对话已中断，请核对已保留的内容后继续。'
      : outcome.error?.message;
  const text = outcome.status === 'failed' ? `这次创作没有完成。${reason ?? '请调整要求后再试。'}`
    : outcome.status === 'partial' ? `已保留完成的内容。${reason ?? '其余内容尚未完成。'}`
      : turn.reply || (outcome.status === 'saved' ? '内容或方案已准备好，请查看。' : '这次没有收到回复，请继续描述你的想法。');
  return { outcome, nodeIds, currentRole: role, role: workflowRoleLabel(role), text };
}
export { workflowNodeIds };
