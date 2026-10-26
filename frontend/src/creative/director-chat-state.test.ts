import { afterEach, describe, expect, it, vi } from 'vitest';
import { createAssistantApi } from '../assistant/api';
import type { AssistantSession, SessionRecovery } from '../assistant/types';
import {
  assertDirectorRecovery, directorChatInput, directorHistory, directorStorageKey,
  emptyDirectorCache, parseDirectorCache, recoveredDirectorReply,
  type DirectorChatCache, type DirectorMessage, type DirectorPending,
} from './director-chat-state';

const projectId = 'project_a';
const session: AssistantSession = { session_id: 'assistant_a', project_id: projectId, intent: 'auto', status: 'active', limits: { turns_used: 1, turns_max: 12 } };
const before: DirectorMessage[] = [{ role: 'user', content: '拍摄一支香水' }, { role: 'assistant', content: '希望呈现什么感觉？' }];
const pending: DirectorPending = { message: '清晨的清新感', sentAt: 1793001600000, sessionId: session.session_id, turns: 1, before };
const cache: DirectorChatCache = { ...emptyDirectorCache(projectId), draft: ' 清晨的清新感 ', session, history: before };
const restored = (history: DirectorMessage[], turns = 2): SessionRecovery => ({
  project_id: projectId, session: { ...session, limits: { ...session.limits, turns_used: turns } }, history, brief: {},
});
const completed = [...before, { role: 'user' as const, content: pending.message }, { role: 'assistant' as const, content: '可以从晨光中的瓶身特写开始。' }];

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

describe('director project and pending state boundaries', () => {
  it('uses distinct keys and preserves draft, history and pending across a reload', () => {
    expect(directorStorageKey('a/b')).not.toBe(directorStorageKey('a%2Fb'));
    expect(directorStorageKey(projectId)).not.toContain('optimizer');
    const waiting = { ...cache, pending, draft: '下一条未发送的补充' };
    expect(parseDirectorCache(JSON.stringify(waiting), projectId)).toEqual(waiting);
    expect(() => parseDirectorCache(JSON.stringify(waiting), 'project_b')).toThrow('当前项目');
  });

  it.each(['project_b', undefined, null])('rejects a restored session bound to %s', boundProject => {
    const recovery = { ...restored(before, 1), session: { ...session, project_id: boundProject } };
    expect(() => assertDirectorRecovery(recovery, projectId)).toThrow('会话与当前项目不匹配');
    expect(() => recoveredDirectorReply(recovery, pending, projectId)).toThrow();
    expect(() => parseDirectorCache(JSON.stringify({ ...cache, session: recovery.session }), projectId)).toThrow();
  });

  it('does not accept a mismatched recovery envelope or unbound history', () => {
    expect(() => assertDirectorRecovery({ ...restored(before), project_id: 'project_b' }, projectId)).toThrow();
    expect(() => assertDirectorRecovery({ ...restored(before), session: null }, projectId)).toThrow();
    expect(() => assertDirectorRecovery({ project_id: projectId, session: null, history: [], brief: {} }, projectId)).not.toThrow();
  });

  it('constructs only an explicit auto chat request and rejects cross-project sends', () => {
    expect(directorChatInput(projectId, cache)).toEqual({
      project_id: projectId, session_id: session.session_id, message: '清晨的清新感', intent: 'auto', context: { kind: 'video' },
    });
    expect(() => directorChatInput('project_b', cache)).toThrow();
    expect(() => directorChatInput(projectId, { ...cache, session: { ...session, project_id: 'project_b' } })).toThrow();
    expect(() => directorChatInput(projectId, { ...cache, session: { ...session, project_id: undefined } })).toThrow();
  });

  it('keeps the pending guard when recovery finds only old history or an unanswered user message', () => {
    for (const history of [before, [...before, { role: 'user' as const, content: pending.message }]]) {
      expect(recoveredDirectorReply(restored(history), pending, projectId)).toBe(false);
    }
    expect(() => directorChatInput(projectId, { ...cache, pending, draft: '新的文字也不能绕过等待' })).toThrow('上一条请求结果尚未核对');
  });

  it('unlocks only a new matching assistant reply in the same session', () => {
    expect(recoveredDirectorReply(restored(completed), pending, projectId)).toBe(true);
    expect(recoveredDirectorReply(restored(completed, 1), pending, projectId)).toBe(false);
    expect(recoveredDirectorReply({ ...restored(completed), session: { ...session, session_id: 'another_session' } }, pending, projectId)).toBe(false);
    expect(recoveredDirectorReply(restored([...before, { role: 'user', content: '另一条请求' }, { role: 'assistant', content: '其他回复' }]), pending, projectId)).toBe(false);
  });

  it('does not mistake a repeated old pair for completion when the counter advances before the reply is stored', () => {
    const repeat: DirectorPending = { ...pending, message: before[0].content, before: [...before, ...before] };
    expect(recoveredDirectorReply(restored(before, 2), repeat, projectId)).toBe(false);
  });

  it('accepts a known newer turn despite history trimming, but stays conservative without turn evidence', () => {
    const truncated = completed.slice(-2);
    expect(recoveredDirectorReply(restored(truncated), pending, projectId)).toBe(true);
    const noTurns = { ...restored(truncated), session: { ...session, limits: undefined } };
    expect(recoveredDirectorReply(noTurns, { ...pending, turns: null }, projectId)).toBe(false);
  });

  it('hides system/tool messages without inventing structured script output', () => {
    expect(directorHistory([{ role: 'system', content: 'internal' }, { role: 'tool', content: 'internal' }, ...completed])).toEqual(completed);
  });

  it('makes one mocked chat request on network failure and blocks another after cache restoration', async () => {
    const fetcher = vi.fn<typeof fetch>().mockRejectedValue(new TypeError('connection lost'));
    const api = createAssistantApi(fetcher), input = directorChatInput(projectId, cache);
    const waiting = { ...cache, pending };
    await expect(api.chat(input)).rejects.toMatchObject({ code: 'ASSISTANT_NETWORK_ERROR' });
    const reloaded = parseDirectorCache(JSON.stringify(waiting), projectId);
    expect(() => directorChatInput(projectId, reloaded)).toThrow('上一条请求结果尚未核对');
    expect(recoveredDirectorReply(restored(before, 1), reloaded.pending!, projectId)).toBe(false);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][0]).toBe('/api/assistant/chat');
  });

  it('timeout stops browser waiting without retrying or clearing the persisted pending guard', async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn<typeof fetch>().mockImplementation(() => new Promise(() => {}));
    const api = createAssistantApi(fetcher), waiting = { ...cache, pending };
    const assertion = expect(api.chat(directorChatInput(projectId, cache), { timeoutMs: 25 })).rejects.toMatchObject({ code: 'ASSISTANT_REQUEST_TIMEOUT' });
    await vi.advanceTimersByTimeAsync(26);
    await assertion;
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][1]?.signal?.aborted).toBe(true);
    expect(() => directorChatInput(projectId, parseDirectorCache(JSON.stringify(waiting), projectId))).toThrow('上一条请求结果尚未核对');
  });
});
