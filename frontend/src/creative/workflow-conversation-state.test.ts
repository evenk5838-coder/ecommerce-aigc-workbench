import { describe, expect, it, vi } from 'vitest';
import {
  legacyConversationKey, parseStoredWorkflowConversation, presentWorkflowTurn, readWorkflowConversation,
  workflowConversationKey, workflowRoleLabel, writeWorkflowConversation, type StoredWorkflowConversation,
} from './workflow-conversation-state';
import { parseWorkflowAction, type WorkflowAction, type WorkflowTurn } from './director-workflow-api';

const projectId = 'project /一';
const action: WorkflowAction = { action_id: 'a1', project_id: projectId, kind: 'confirm_script', status: 'proposed', action_status: 'proposed',
  request_id: 'request-original', script_id: 'script1', script_version: 1, node_ids: ['script'], estimated_cost_cny: 0,
  expires_at: 200, expired: false, stale: false, requires_user_action: true, submit_client_request_id: null };
const turn: WorkflowTurn = { turn_id: 'turn1', request_id: 'request-original', user: '写一个脚本', reply: '脚本已放入画布', reply_type: 'saved',
  created_at: 100, structured: { current_role: 'writer', outcome: { status: 'saved', node_ids: ['script'] },
    artifacts: [{ type: 'script', node_id: 'script', target_id: 'script1', persistence: 'saved' }], actions: [action] } };
const stored: StoredWorkflowConversation = { projectId, draft: '下一轮未发的修改意见',
  pending: { endpoint: 'conversation', id: 'request-original', message: '已发送的完整原文  ', selected_node_ids: ['second', 'first'], session_id: 'session1', revision: 0 },
  actionPending: { action_id: 'a1', kind: 'confirm_script', client_request_id: 'approve-original', user_action: 'confirm_script' },
  legacyActionPending: false, turns: [turn], actions: [action], sessionId: 'session1', workflowRevision: 32, currentRole: 'writer' };

function quotaStorage(limitBytes: number, initial: [string, string][] = []) {
  const values = new Map(initial);
  const setItem = vi.fn((key: string, text: string) => {
    const updated = new Map(values); updated.set(key, text);
    const bytes = [...updated].reduce((total, [name, value]) => total + (name.length + value.length) * 2, 0);
    if (bytes > limitBytes) throw new DOMException('Quota exceeded', 'QuotaExceededError');
    values.set(key, text);
  });
  return { values, setItem, getItem: (key: string) => values.get(key) ?? null };
}

function largeGeneration(index: number): WorkflowAction {
  const nodeIds = Array.from({ length: 20 }, (_, n) => `node-${index}-${n}`);
  return parseWorkflowAction({ ...action, action_id: `generation-${index}`, request_id: `request-${index}`,
    kind: 'generation', node_ids: nodeIds, output_mode: 'clips', auto_film: false, media_status: 'proposed',
    scope: { generate: true, compose: false, ordered_node_ids: nodeIds, no_trim: false, composition_cost_cny: 0 },
    quotes: nodeIds.map((id, n) => ({ quote_id: `quote-${index}-${n}`, project_id: projectId, node_id: id,
      config_revision: 1, estimated_cost_cny: 0.01, expires_at: 200,
      input: { kind: 'image', model_id: 'image-model', params: {}, prompt: '画'.repeat(20000) } })),
    tasks: [], executions: [], composition: null,
  }, projectId);
}

describe('workflow v3 cache and migration', () => {
  it('restores the visible follow-up batch with an edited prompt without changing a pending request scope', () => {
    const scoped = { ...stored, draft: '按这组镜头制作成片，第二镜节奏更慢', draftScope: { nodeIds: ['image-2', 'image-1'], label: '本组分镜图片' } };
    const setItem = vi.fn(); writeWorkflowConversation({ setItem }, scoped);
    const restored = parseStoredWorkflowConversation(setItem.mock.calls[0][1], projectId);
    expect(restored.draftScope).toEqual(scoped.draftScope);
    expect(restored.draft).toBe(scoped.draft);
    expect(restored.pending?.endpoint === 'conversation' && restored.pending.selected_node_ids).toEqual(['second', 'first']);
  });
  it('does not restore an invalid or empty-prompt batch while retaining the original message', () => {
    expect(parseStoredWorkflowConversation(JSON.stringify({ ...stored, draftScope: { nodeIds: ['a', 'a'], label: '本组' } }), projectId).draftScope).toBeUndefined();
    const restored = parseStoredWorkflowConversation(JSON.stringify({ ...stored, draft: '', draftScope: { nodeIds: ['a'], label: '本组' } }), projectId);
    expect(restored.draftScope).toBeUndefined(); expect(restored.pending).toEqual(stored.pending);
  });
  it('round-trips exact write identity, endpoint, selected order and submitted content separately from draft', () => {
    const setItem = vi.fn(); writeWorkflowConversation({ setItem }, stored);
    expect(setItem.mock.calls[0][0]).toBe('material-project-director-v3:project%20%2F%E4%B8%80');
    const parsed = parseStoredWorkflowConversation(setItem.mock.calls[0][1], projectId);
    expect(parsed).toEqual(stored);
    expect(parsed.pending?.message).not.toBe(parsed.draft);
    expect(parsed.pending?.message.endsWith('  ')).toBe(true);
    expect(parsed.actionPending?.client_request_id).toBe('approve-original');
  });

  it('migrates v2 pending to a legacy receipt-only identity while keeping old intent and next draft', () => {
    const old = { projectId, draft: '新草稿', intent: 'auto', pending: { id: 'old-id', message: '原始请求', intent: 'script', session_id: 'old-session', revision: 4 }, actionPending: true };
    const migrated = parseStoredWorkflowConversation(JSON.stringify(old), projectId);
    expect(migrated.pending).toEqual({ ...old.pending, endpoint: 'legacy' });
    expect(migrated.draft).toBe('新草稿'); expect(migrated.actionPending).toBeNull(); expect(migrated.legacyActionPending).toBe(true);
  });

  it('preserves a valid pre-intent legacy request without adding a new intent or selection', () => {
    const migrated = parseStoredWorkflowConversation(JSON.stringify({ projectId, draft: '', pending: { id: 'old-id', message: '你好' } }), projectId);
    expect(migrated.pending).toEqual({ endpoint: 'legacy', id: 'old-id', message: '你好' });
  });

  it('reads v2 only as fallback; never deletes it during migration or revives it after resolved v3', () => {
    const old = JSON.stringify({ projectId, draft: '旧草稿', pending: { id: 'old-id', message: '你好' } });
    const values = new Map([[legacyConversationKey(projectId), old]]);
    const storage = { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => { values.set(key, value); } };
    const migrated = readWorkflowConversation(storage, projectId);
    expect(migrated.pending?.endpoint).toBe('legacy');
    writeWorkflowConversation(storage, { ...migrated, pending: null, draft: '继续创作' });
    expect(values.get(legacyConversationKey(projectId))).toBe(old);
    expect(readWorkflowConversation(storage, projectId)).toMatchObject({ pending: null, draft: '继续创作' });
  });

  it('falls back to an intact old cache if the v3 JSON is corrupted or belongs to another project', () => {
    const getItem = vi.fn((key: string) => key === workflowConversationKey(projectId) ? '{broken' : JSON.stringify({ projectId, draft: '有效旧稿', pending: { id: 'old', message: '原文' } }));
    expect(readWorkflowConversation({ getItem }, projectId).pending?.id).toBe('old');
    expect(parseStoredWorkflowConversation(JSON.stringify(stored), 'different').draft).toBe('');
  });

  it('never drops a pending write just because a cached display item is malformed', () => {
    const parsed = parseStoredWorkflowConversation(JSON.stringify({ ...stored, turns: [...stored.turns, { bogus: true }], actions: [...stored.actions, { kind: 'generation' }] }), projectId);
    expect(parsed.pending).toEqual(stored.pending); expect(parsed.actionPending).toEqual(stored.actionPending);
    expect(parsed.turns).toEqual([turn]); expect(parsed.actions).toEqual([action]); expect(parsed.draft).toBe(stored.draft);
  });

  it('does not reinterpret a wrong confirmation kind as another paid action', () => {
    const parsed = parseStoredWorkflowConversation(JSON.stringify({ ...stored, actionPending: { ...stored.actionPending, user_action: 'confirm_generate' } }), projectId);
    expect(parsed.actionPending).toBeNull(); expect(parsed.pending).toEqual(stored.pending);
  });

  it('propagates storage failures so a caller can stop before POST rather than lose operation identity', () => {
    expect(() => writeWorkflowConversation({ setItem: () => { throw new Error('quota exceeded'); } }, stored)).toThrow('quota exceeded');
    expect(() => readWorkflowConversation({ getItem: () => { throw new Error('access denied'); } }, projectId)).toThrow('access denied');
  });

  it('keeps the returned selection independent from any prior in-memory snapshot', () => {
    const parsed = parseStoredWorkflowConversation(JSON.stringify(stored), projectId);
    if (parsed.pending?.endpoint === 'conversation') parsed.pending.selected_node_ids.push('third');
    expect(stored.pending?.endpoint === 'conversation' && stored.pending.selected_node_ids).toEqual(['second', 'first']);
  });

  it('bounds eight large valid action snapshots while keeping the latest acknowledgement and exact pending writes', () => {
    const actions = Array.from({ length: 8 }, (_, i) => largeGeneration(i));
    const turns = actions.map((snapshot, i) => ({ ...turn, turn_id: `turn-${i}`, request_id: snapshot.request_id!,
      created_at: i + 100, structured: { ...turn.structured, actions: [snapshot] } }));
    const input = { ...stored, actions, turns };
    const original = JSON.stringify(input);
    expect(original.length * 2).toBeGreaterThan(5 * 1024 * 1024);
    const storage = quotaStorage(5 * 1024 * 1024);
    writeWorkflowConversation(storage, input);
    const cached = readWorkflowConversation(storage, projectId);
    expect(cached).toMatchObject({ draft: stored.draft, pending: stored.pending, actionPending: stored.actionPending,
      sessionId: stored.sessionId, workflowRevision: stored.workflowRevision, currentRole: stored.currentRole });
    expect(cached.turns.at(-1)?.request_id).toBe('request-7');
    expect(presentWorkflowTurn(cached.turns.at(-1)!).nodeIds).toEqual(['script']);
    expect(storage.getItem(workflowConversationKey(projectId))!.length).toBeLessThan(132000);
    expect(JSON.stringify(input)).toBe(original);
  });

  it('reduces display cache on a quota failure and retains a recent acknowledgement when it fits', () => {
    const input = { ...stored, turns: [turn, { ...turn, turn_id: 'new-turn', request_id: 'new-request', created_at: 101,
      structured: { ...turn.structured, debug_context: 'x'.repeat(70000) } }] };
    const storage = quotaStorage(10000);
    writeWorkflowConversation(storage, input);
    expect(storage.setItem).toHaveBeenCalledTimes(2);
    const cached = readWorkflowConversation(storage, projectId);
    expect(cached.turns.at(-1)?.request_id).toBe('new-request');
    expect(cached.turns.at(-1)?.reply).toBe(turn.reply);
    expect(cached.pending).toEqual(stored.pending);
    expect(cached.actionPending).toEqual(stored.actionPending);
  });

  it('saves only the full core when no display item fits and leaves v2 and unrelated storage untouched', () => {
    const old = JSON.stringify({ projectId, draft: '未删除的旧版草稿', pending: { id: 'old', message: '旧原文' } });
    const others: [string, string][] = [[legacyConversationKey(projectId), old], ['unrelated', 'keep me']];
    const scoped = { ...stored, draftScope: { nodeIds: ['batch-2', 'batch-1'], label: '本组分镜图片' } };
    const coreOnly = { ...scoped, turns: [], actions: [] };
    const first = quotaStorage(Infinity, others); writeWorkflowConversation(first, coreOnly);
    const limit = [...first.values].reduce((sum, [key, value]) => sum + (key.length + value.length) * 2, 0);
    const storage = quotaStorage(limit, others);
    writeWorkflowConversation(storage, scoped);
    expect(storage.setItem).toHaveBeenCalledTimes(3);
    expect(readWorkflowConversation(storage, projectId)).toEqual(coreOnly);
    expect(storage.getItem(legacyConversationKey(projectId))).toBe(old);
    expect(storage.getItem('unrelated')).toBe('keep me');
  });

  it('does not trim a legacy pending payload or old confirmation wait marker during quota fallback', () => {
    const legacy = parseStoredWorkflowConversation(JSON.stringify({ projectId, draft: '未发的新草稿  ',
      pending: { id: 'legacy-original', message: '旧原文  ', intent: 'script', revision: 0, session_id: 'old-session' }, actionPending: true }), projectId);
    const first = quotaStorage(Infinity); writeWorkflowConversation(first, legacy);
    const size = [...first.values].reduce((sum, [key, value]) => sum + (key.length + value.length) * 2, 0);
    const storage = quotaStorage(size);
    writeWorkflowConversation(storage, { ...legacy, turns: [turn], actions: [action] });
    expect(readWorkflowConversation(storage, projectId)).toEqual(legacy);
  });

  it('throws when the core itself cannot be saved and preserves the previous cache', () => {
    const key = workflowConversationKey(projectId), previous = JSON.stringify({ projectId, draft: '先前的完整草稿' });
    const storage = quotaStorage((key.length + previous.length) * 2, [[key, previous]]);
    expect(() => writeWorkflowConversation(storage, stored)).toThrow('Quota exceeded');
    expect(storage.setItem).toHaveBeenCalledTimes(3);
    expect(storage.getItem(key)).toBe(previous);
    expect(stored.pending?.message).toBe('已发送的完整原文  ');
  });

  it('does not retry permission or unknown storage failures with a smaller overwrite', () => {
    for (const failure of [new DOMException('denied', 'SecurityError'), new Error('unavailable')]) {
      const setItem = vi.fn(() => { throw failure; });
      expect(() => writeWorkflowConversation({ setItem }, stored)).toThrow(failure);
      expect(setItem).toHaveBeenCalledTimes(1);
    }
  });
});

describe('actual role and outcome presentation', () => {
  it.each([['writer', 'AI 编剧'], ['director', 'AI 导演'], ['cinematographer', 'AI 摄影师'], ['editor', 'AI 剪辑师']] as const)('labels %s from server role', (role, label) => {
    expect(workflowRoleLabel(role)).toBe(label);
    expect(presentWorkflowTurn({ ...turn, structured: { ...turn.structured, current_role: role } }).role).toBe(label);
  });

  it('does not derive role or success from a promise in prose', () => {
    const view = presentWorkflowTurn({ ...turn, reply: '我是剪辑师，成片已经完成', structured: {} });
    expect(view.currentRole).toBeNull(); expect(view.role).toBe('创作助手'); expect(view.outcome.status).toBe('message'); expect(view.nodeIds).toEqual([]);
  });

  it('presents actual partial saved content with interrupted status and preserved node IDs', () => {
    const view = presentWorkflowTurn({ ...turn, reply: '全部完成', structured: { ...turn.structured,
      outcome: { status: 'partial', node_ids: ['script'], error: { code: 'DIRECTOR_REQUEST_INTERRUPTED', message: 'owner exit' } } } });
    expect(view.text).toContain('已保留'); expect(view.text).toContain('中断'); expect(view.text).not.toContain('全部完成');
    expect(view.nodeIds).toEqual(['script']);
  });

  it('shows a validation failure in product language and does not expose field names', () => {
    const view = presentWorkflowTurn({ ...turn, structured: { outcome: { status: 'failed', node_ids: [], error: { code: 'VALIDATION_FAILED', message: 'shots.0.duration_s wrong' } } } });
    expect(view.text).toContain('内容不完整'); expect(view.text).not.toContain('duration_s');
  });

  it('recognizes saved image/video nodes without requiring proposal fields', () => {
    for (const [type, role] of [['image', 'AI 导演'], ['video', 'AI 摄影师']]) {
      const view = presentWorkflowTurn({ ...turn, structured: { artifacts: [{ type, node_id: 'node2', saved: true }], outcome: { status: 'saved', node_ids: ['node2'] } } });
      expect(view.role).toBe(role); expect(view.nodeIds).toEqual(['node2']);
    }
  });
});
