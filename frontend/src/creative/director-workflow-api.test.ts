import { afterEach, describe, expect, it, vi } from 'vitest';
import archivedFilm from '../../../docs/evidence/v0.11-对话多角色执行-后端-20261027/run-20261027T235902-4320/http-film/director-workflow-http.json';
import archivedClips from '../../../docs/evidence/v0.11-对话多角色执行-后端-20261027/run-20261027T235902-4320/http-clips/director-workflow-http.json';
import {
  createWorkflowDirectorApi, parseWorkflowAction, parseWorkflowMessages, parseWorkflowOutcome,
  parseWorkflowReply, parseWorkflowState, workflowNodeIds, type WorkflowPendingRequest,
} from './director-workflow-api';

// Archived real HTTP + SQLite + fixed-provider evidence, not a real model result.
const archive = archivedFilm as unknown as {
  project_id: string; http: { method: string; path: string; status: number; response: Record<string, unknown> }[];
};
const pid = archive.project_id;
const script = archive.http.find(row => row.path.endsWith('/conversation') && Array.isArray(row.response.artifacts)
  && row.response.artifacts.some((a: { type: string }) => a.type === 'script'))!.response;
const action = (script.actions as Record<string, unknown>[])[0];
const requestId = script.request_id as string;
const pending: WorkflowPendingRequest = { endpoint: 'conversation', id: requestId, message: '只修改第二镜',
  session_id: script.session_id as string, revision: 42, selected_node_ids: ['镜头 /二'] };
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status });
const messages = { project_id: pid, session_id: script.session_id, workflow_revision: 3, actions: [],
  items: [{ turn_id: 'turn-1', request_id: requestId, user: pending.message, reply: script.reply, reply_type: 'saved',
    created_at: 123, structured: { current_role: 'writer', outcome: script.outcome, artifacts: script.artifacts, actions: script.actions } }],
  next_cursor: null, has_more: false };
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

describe('new workflow wire contract', () => {
  it.each([['film', archive], ['clips', archivedClips as unknown as typeof archive]] as const)('reads archived %s conversation/state/action responses', (_mode, evidence) => {
    let checked = 0;
    for (const row of evidence.http.filter(r => r.status === 200)) {
      if (row.path.endsWith('/conversation')) {
        expect(parseWorkflowReply(row.response, row.response.request_id as string, evidence.project_id).request_id).toBe(row.response.request_id); checked++;
      } else if (row.path.includes('/conversation/state')) {
        expect(parseWorkflowState(row.response, evidence.project_id).project_id).toBe(evidence.project_id); checked++;
      } else if (row.path.includes('/director/actions/')) {
        expect(parseWorkflowAction(row.response, evidence.project_id).action_id).toBe(row.response.action_id); checked++;
      }
    }
    expect(checked).toBeGreaterThan(15);
  });

  it('accepts action-only saved results without fabricating a proposal artifact', () => {
    const parsed = parseWorkflowReply({ ...script, artifacts: [] }, requestId, pid);
    expect(parsed.outcome.status).toBe('saved');
    expect(parsed.artifacts).toEqual([]);
    expect(parsed.actions[0].kind).toBe('confirm_script');
    expect(workflowNodeIds(parsed)).toEqual(['script']);
  });

  it('reads new outcomes without goal and old history retaining goal together', () => {
    const old = { ...messages.items[0], turn_id: 'old', structured: { outcome: { status: 'message', goal: 'auto', node_ids: [] } } };
    const parsed = parseWorkflowMessages({ ...messages, items: [...messages.items, old] }, pid);
    expect(parsed.items).toHaveLength(2);
    expect(parseWorkflowOutcome(parsed.items[0].structured.outcome).goal).toBeUndefined();
    expect(parseWorkflowOutcome(parsed.items[1].structured.outcome).goal).toBe('auto');
  });

  it('reads recovered partial failure with unknown cost and no role/token counts', () => {
    const recovered = { ...script, request_status: 'failed', recovered_after_restart: true, current_role: undefined,
      session_id: null, usage: { has_unknown_cost: true }, outcome: { status: 'partial', node_ids: ['script'],
        error: { code: 'DIRECTOR_REQUEST_INTERRUPTED', message: '上次对话中断' } } };
    const parsed = parseWorkflowReply(recovered, requestId, pid);
    expect(parsed.usage?.has_unknown_cost).toBe(true);
    expect(parsed.usage?.cost_cny).toBeUndefined();
    expect(parsed.actions).toHaveLength(1);
  });

  it('preserves an unavailable recovery action as non-executable', () => {
    const fallback = { action_id: 'old-action', kind: 'generation', status: 'unknown', requires_user_action: false };
    expect(parseWorkflowAction(fallback, pid)).toEqual(fallback);
    expect(() => parseWorkflowAction({ ...fallback, requires_user_action: true }, pid)).toThrow();
  });

  it.each([
    { ...script, request_id: 'another' }, { ...script, workflow_revision: true },
    { ...script, actions: [], artifacts: [] }, { ...script, current_role: 'producer' },
    { ...script, artifacts: [{ type: 'image', node_id: 'n' }] },
    { ...script, request_status: 'failed' }, { ...script, usage: { cost_cny: Number.NaN } },
  ])('rejects a contradictory or unbound reply %#', value => {
    expect(() => parseWorkflowReply(value, requestId, pid)).toThrow();
  });

  it('rejects another project action, implicit confirmation and expired executable scope', () => {
    expect(() => parseWorkflowAction({ ...action, project_id: 'another' }, pid)).toThrow();
    expect(() => parseWorkflowAction({ ...action, action_status: 'submitted', requires_user_action: true }, pid)).toThrow();
    expect(() => parseWorkflowAction({ ...action, expired: true, requires_user_action: true }, pid)).toThrow();
  });

  it('rejects changed film ordering or premature completion', () => {
    const film = archive.http.find(r => r.path.includes('/director/actions/') && r.response.output_mode === 'film')!.response;
    expect(parseWorkflowAction(film, pid).auto_film).toBe(true);
    expect(() => parseWorkflowAction({ ...film, scope: { ...(film.scope as object), ordered_node_ids: ['wrong'] } }, pid)).toThrow();
    expect(() => parseWorkflowAction({ ...film, status: 'completed', composition: { status: 'waiting', reason: 'preparing' } }, pid)).toThrow();
  });

  it('rejects an execution or task substituted from another quote/node', () => {
    const generation = archive.http.find(r => r.response.kind === 'generation' && r.response.action_status === 'submitted')!.response;
    const wrong = { ...generation, executions: (generation.executions as Record<string, unknown>[]).map((e, i) => i ? e : { ...e, quote_id: 'other-quote' }) };
    expect(() => parseWorkflowAction(wrong, pid)).toThrow();
  });

  it('keeps a successful execution whose file disappeared unavailable, not completed', () => {
    const generation = archive.http.find(r => r.response.kind === 'generation' && r.response.media_status === 'completed')!.response;
    const unavailable = { ...generation, status: 'failed', media_status: 'failed', composition: { status: 'blocked', reason: 'media_incomplete' },
      executions: (generation.executions as Record<string, unknown>[]).map(e => ({ ...e, asset_available: false, result_status: 'unavailable',
        asset_error: { code: 'OUTPUT_ASSET_CHANGED', message: '素材内容改变' } })) };
    expect(parseWorkflowAction(unavailable, pid).executions?.[0].asset_available).toBe(false);
  });
});

describe('single attempt and exact recovery transport', () => {
  it('sends exact selected IDs/session/revision and never old intent fields', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response(script));
    await createWorkflowDirectorApi(fetcher).conversation(pid, pending);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][0]).toBe(`/api/projects/${pid}/director/conversation`);
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual({ client_request_id: requestId,
      message: pending.message, session_id: pending.session_id, expected_workflow_revision: 42, selected_node_ids: ['镜头 /二'] });
  });

  it('does not send a legacy pending request through the new endpoint', async () => {
    const fetcher = vi.fn<typeof fetch>();
    await expect(createWorkflowDirectorApi(fetcher).conversation(pid, { ...pending, endpoint: 'legacy' } as unknown as WorkflowPendingRequest))
      .rejects.toMatchObject({ code: 'VALIDATION_FAILED' });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('uses repeated singular selection query parameters and pages exact cursor', async () => {
    const state = archive.http.find(r => r.path.endsWith('/conversation/state'))!.response;
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(response(state)).mockResolvedValueOnce(response(messages));
    const api = createWorkflowDirectorApi(fetcher);
    await api.state(pid, ['one /1', 'two?2']);
    await api.messages(pid, 'older /?');
    expect(fetcher.mock.calls[0][0]).toContain('selected_node_id=one%20%2F1&selected_node_id=two%3F2');
    expect(fetcher.mock.calls[1][0]).toContain('cursor=older%20%2F%3F');
    expect(fetcher.mock.calls.every(([, init]) => init?.method === 'GET')).toBe(true);
  });

  it('recovers pending committed artifacts with a GET, without posting another model call', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response({ client_request_id: requestId, status: 'pending', response: null,
      artifacts: script.artifacts, actions: script.actions, may_still_be_running: true }));
    const receipt = await createWorkflowDirectorApi(fetcher).receipt(pid, requestId);
    expect(receipt.response).toBeNull(); expect(receipt.artifacts).toHaveLength(1); expect(receipt.actions).toHaveLength(1);
    expect(fetcher.mock.calls[0][1]?.method).toBe('GET'); expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('queries old pending through its original receipt URL only', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response({ client_request_id: 'old /1', status: 'pending', response: null }));
    expect(await createWorkflowDirectorApi(fetcher).legacyReceipt(pid, 'old /1')).toMatchObject({ status: 'pending', response: null });
    expect(fetcher.mock.calls[0][0]).toBe(`/api/projects/${pid}/director/requests/old%20%2F1`);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('reads completed legacy replies without treating old proposal actions as new confirmation authority', async () => {
    const old = { ...script, flow_version: undefined, actions: [{ type: 'confirm_script', target_id: 'scr-1', target_version: 1, requires_user_action: true }],
      outcome: { status: 'saved', goal: 'script', node_ids: ['script'] } };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response({ client_request_id: requestId, status: 'completed', response: old }));
    const recovered = await createWorkflowDirectorApi(fetcher).legacyReceipt(pid, requestId);
    expect(recovered.response?.outcome.goal).toBe('script'); expect(recovered.actions).toEqual([]);
  });

  it('confirms only the original action and client request, including an explicit same-ID replay', async () => {
    const submitted = { ...action, action_status: 'submitted', status: 'completed', requires_user_action: false,
      submit_client_request_id: 'confirm-fixed', idempotent_replay: true };
    const fetcher = vi.fn<typeof fetch>().mockImplementation(async () => response(submitted));
    const confirmation = { action_id: action.action_id as string, kind: 'confirm_script' as const, client_request_id: 'confirm-fixed', user_action: 'confirm_script' as const };
    const api = createWorkflowDirectorApi(fetcher);
    await api.confirm(pid, confirmation); await api.confirm(pid, confirmation);
    for (const [url, init] of fetcher.mock.calls) {
      expect(url).toBe(`/api/projects/${pid}/director/actions/${action.action_id}/confirm`);
      expect(JSON.parse(String(init?.body))).toEqual({ client_request_id: 'confirm-fixed', user_action: 'confirm_script' });
    }
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it('rejects a confirmation receipt belonging to another request without claiming success', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response({ ...action, action_status: 'submitted', status: 'completed', requires_user_action: false,
      submit_client_request_id: 'somebody-else' }));
    await expect(createWorkflowDirectorApi(fetcher).confirm(pid, { action_id: action.action_id as string, kind: 'confirm_script',
      client_request_id: 'original', user_action: 'confirm_script' })).rejects.toMatchObject({ code: 'DIRECTOR_RESULT_UNKNOWN' });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it.each([422, 409, 400])('keeps HTTP %i as a known failure, with readable validation text', async status => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response(status === 422 ? { detail: [{ loc: ['body', 'selected_node_ids'], msg: 'bad internals' }] }
      : { error: { code: 'WORKFLOW_REVISION_CONFLICT', message: '内容已更新' } }, status));
    const error = await createWorkflowDirectorApi(fetcher).conversation(pid, pending).catch(e => e);
    expect(error.status).toBe(status); expect(error.code).not.toBe('DIRECTOR_RESULT_UNKNOWN');
    if (status === 422) { expect(error.code).toBe('VALIDATION_FAILED'); expect(error.message).not.toContain('selected_node_ids'); }
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it.each(['network', 'server', 'malformed'])('preserves unknown acceptance for %s without automatic retry', async kind => {
    const fetcher = vi.fn<typeof fetch>();
    if (kind === 'network') fetcher.mockRejectedValue(new Error('lost'));
    else fetcher.mockResolvedValue(kind === 'server' ? response({}, 503) : response({ ...script, request_id: 'wrong' }));
    await expect(createWorkflowDirectorApi(fetcher).conversation(pid, pending)).rejects.toMatchObject({ code: 'DIRECTOR_RESULT_UNKNOWN' });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('does not dispatch already aborted requests; an in-flight abort remains unknown', async () => {
    const controller = new AbortController(); controller.abort();
    const fetcher = vi.fn<typeof fetch>(); const api = createWorkflowDirectorApi(fetcher);
    await expect(api.conversation(pid, pending, { signal: controller.signal })).rejects.toMatchObject({ code: 'REQUEST_ABORTED' });
    expect(fetcher).not.toHaveBeenCalled();
    const active = new AbortController();
    fetcher.mockImplementation((_url, init) => new Promise((_resolve, reject) => init?.signal?.addEventListener('abort', () => reject(new Error('abort')))));
    const sending = api.conversation(pid, pending, { signal: active.signal }); active.abort();
    await expect(sending).rejects.toMatchObject({ code: 'DIRECTOR_RESULT_UNKNOWN' });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
});
