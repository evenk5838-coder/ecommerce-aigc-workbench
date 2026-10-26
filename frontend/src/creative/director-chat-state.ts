import { ApiError } from '../api';
import { isRecord } from '../assistant/guards';
import type { AssistantMessage, AssistantSession, ChatInput, SessionRecovery } from '../assistant/types';

export type DirectorMessage = { role: 'user' | 'assistant'; content: string };
export interface DirectorPending {
  message: string;
  sentAt: number;
  sessionId: string | null;
  turns: number | null;
  before: DirectorMessage[];
}
export interface DirectorChatCache {
  version: 1;
  projectId: string;
  draft: string;
  history: DirectorMessage[];
  session: AssistantSession | null;
  pending: DirectorPending | null;
}
export const directorText = (value: unknown): value is string => typeof value === 'string' && value.trim().length > 0;
export const directorCount = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
const messageList = (value: unknown): value is DirectorMessage[] => Array.isArray(value) && value.every(item =>
  isRecord(item) && ['user', 'assistant'].includes(String(item.role)) && typeof item.content === 'string');
export const directorStorageKey = (projectId: string) => 'material-director-chat-v1:' + encodeURIComponent(projectId);
export const directorHistory = (history: AssistantMessage[]): DirectorMessage[] => history
  .filter((entry): entry is AssistantMessage & { role: 'user' | 'assistant' } => entry.role === 'user' || entry.role === 'assistant')
  .map(({ role, content }) => ({ role, content }));

export function emptyDirectorCache(projectId: string): DirectorChatCache {
  return { version: 1, projectId, draft: '', history: [], session: null, pending: null };
}
export function parseDirectorCache(raw: string | null, projectId: string): DirectorChatCache {
  if (!raw) return emptyDirectorCache(projectId);
  const data: unknown = JSON.parse(raw);
  if (!isRecord(data) || data.version !== 1 || data.projectId !== projectId || typeof data.draft !== 'string' || !messageList(data.history)) throw new Error('聊天缓存与当前项目不匹配或格式不完整。');
  if (data.session !== null && (!isRecord(data.session) || data.session.project_id !== projectId ||
    !directorText(data.session.session_id) || !directorText(data.session.intent) || !directorText(data.session.status))) throw new Error('聊天缓存中的会话未绑定当前项目。');
  if (data.pending !== null && (!isRecord(data.pending) || !directorText(data.pending.message) ||
    typeof data.pending.sentAt !== 'number' || !Number.isFinite(data.pending.sentAt) ||
    (data.pending.sessionId !== null && !directorText(data.pending.sessionId)) ||
    (data.pending.turns !== null && !directorCount(data.pending.turns)) || !messageList(data.pending.before))) throw new Error('待核对请求记录不完整。');
  return data as unknown as DirectorChatCache;
}
export function assertDirectorRecovery(recovery: SessionRecovery, projectId: string): void {
  if (recovery.project_id !== projectId || (recovery.session && recovery.session.project_id !== projectId) ||
    (!recovery.session && recovery.history.length > 0)) {
    throw new ApiError('DIRECTOR_SESSION_MISMATCH', '会话与当前项目不匹配，已停止使用，请重新恢复。', 0);
  }
}
/** Only call in the user's send handler, before persisting the new pending marker. */
export function directorChatInput(projectId: string, cache: DirectorChatCache): ChatInput {
  if (!directorText(projectId) || cache.projectId !== projectId || (cache.session &&
    (cache.session.project_id !== projectId || !directorText(cache.session.session_id)))) {
    throw new ApiError('DIRECTOR_SESSION_MISMATCH', '会话与当前项目不匹配，请先恢复。', 0);
  }
  if (cache.pending) throw new ApiError('DIRECTOR_RESULT_PENDING', '上一条请求结果尚未核对，不能再次发送。', 0);
  if (!directorText(cache.draft)) throw new ApiError('ASSISTANT_INPUT_REQUIRED', '请先填写拍摄想法。', 0);
  return {
    project_id: projectId, ...(cache.session ? { session_id: cache.session.session_id } : {}),
    message: cache.draft.trim(), intent: 'auto', context: { kind: 'video' },
  };
}
/** Server history is truncated and has no message IDs: uncertainty never clears pending. */
export function recoveredDirectorReply(recovery: SessionRecovery, pending: DirectorPending, projectId: string): boolean {
  assertDirectorRecovery(recovery, projectId);
  const messages = directorHistory(recovery.history);
  if (!recovery.session || (pending.sessionId && recovery.session.session_id !== pending.sessionId)) return false;
  const last = messages.at(-1), previous = messages.at(-2);
  if (last?.role !== 'assistant' || !directorText(last.content) || previous?.role !== 'user' || previous.content !== pending.message) return false;
  if (JSON.stringify(messages) === JSON.stringify(pending.before)) return false;
  // The turn counter may advance just before append_message persists the reply.
  // An identical last pair is ambiguous even when the server trimmed older history.
  const beforeLast = pending.before.at(-1), beforePrevious = pending.before.at(-2);
  if (beforeLast?.role === 'assistant' && beforePrevious?.role === 'user' &&
    beforeLast.content === last.content && beforePrevious.content === previous.content) return false;
  const turns = recovery.session.limits?.turns_used;
  if (pending.turns !== null && directorCount(turns)) return turns > pending.turns;
  return messages.length > pending.before.length &&
    pending.before.every((message, index) => message.role === messages[index]?.role && message.content === messages[index]?.content);
}
