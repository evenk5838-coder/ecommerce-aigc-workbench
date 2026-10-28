import { directorNodeIds, directorOutcome, type DirectorIntent, type DirectorPendingRequest, type DirectorTurn } from './director-project-api';

export interface StoredConversation {
  projectId: string;
  draft: string;
  intent: DirectorIntent;
  pending: DirectorPendingRequest | null;
  actionPending: boolean;
}

export function parseStoredConversation(raw: string | null, projectId: string): StoredConversation {
  const empty: StoredConversation = { projectId, draft: '', intent: 'auto', pending: null, actionPending: false };
  try {
    const value = JSON.parse(raw || 'null');
    if (value?.projectId !== projectId || typeof value.draft !== 'string') return empty;
    if (value.pending !== null && (typeof value.pending?.id !== 'string' || typeof value.pending?.message !== 'string')) return empty;
    // Preserve the exact submitted intent. The composer can already contain a different next draft.
    if (value.pending?.intent !== undefined && !['auto', 'script'].includes(value.pending.intent)) return empty;
    return { projectId, draft: value.draft, intent: value.intent === 'script' ? 'script' : 'auto', pending: value.pending, actionPending: value.actionPending === true };
  } catch {
    return empty;
  }
}

export function presentDirectorTurn(turn: DirectorTurn) {
  const outcome = directorOutcome(turn.structured);
  const nodeIds = directorNodeIds(turn.structured);
  const artifacts = Array.isArray(turn.structured.artifacts) ? turn.structured.artifacts : [];
  const hasScript = artifacts.some(a => a?.type === 'script');
  const hasPlan = artifacts.some(a => a?.type === 'shot_plan');
  const role = outcome.goal === 'script' || hasScript ? 'AI 编剧' : hasPlan ? 'AI 导演' : '创作助手';
  const failureText = outcome.error?.code === 'VALIDATION_FAILED'
    ? '生成的内容不完整，请调整创作要求后再试。'
    : outcome.error?.code === 'SCRIPT_NOT_SAVED'
      ? '脚本还未完成，请重新起稿。'
      : outcome.error?.message;
  const text = outcome.status === 'failed'
    ? `这次创作没有完成。${failureText || '请调整后再试。'}`
    : outcome.status === 'partial'
      ? `部分内容已保存。${failureText || '其余内容暂未完成。'}`
      : turn.reply || (outcome.status === 'saved' ? '草案已保存，可以查看内容。' : '这次没有收到回复，请重新描述你的想法。');
  return { outcome, nodeIds, role, text };
}
