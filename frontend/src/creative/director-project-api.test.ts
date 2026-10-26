import { afterEach, describe, expect, it, vi } from 'vitest';
import { createProjectDirectorApi, parseDirectorActions, parseDirectorMessages, parseDirectorReply } from './director-project-api';

const projectId = 'prj_director';
const requestId = 'req-fixture-1';
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
// Saved-tool/empty-text shape from docs/接口/样例-对话导演与画布.json.
// Message envelope uses the current director_chat.messages contract; the older
// archived envelope predates project/session/revision/current-actions fields.
const scriptArtifact = {
  type: 'script', proposal_id: 'prop_script', proposal_version: 1,
  target_id: 'scr_saved', target_version: 1, persistence: 'saved', confirmed: false,
};
const confirmAction = { type: 'confirm_script', target_id: 'scr_saved', target_version: 1, requires_user_action: true };
const savedReply = {
  session_id: 'asst_project', request_id: requestId, request_status: 'completed',
  reply: '', reply_type: 'proposal_ready', artifacts: [scriptArtifact],
  actions: [confirmAction], workflow_revision: 3,
};
const ordinaryReply = { ...savedReply, reply: '脚本草案已保存，请确认后继续。' };
const turn = {
  turn_id: 'dturn_saved', request_id: requestId, user: '两个镜头，先整体再瓶盖',
  reply: '', reply_type: 'proposal_ready', created_at: 1_793_010_097.1961842,
  structured: { artifacts: [scriptArtifact], actions: [confirmAction] },
};
const messages = {
  project_id: projectId, session_id: 'asst_project', workflow_revision: 3,
  actions: [confirmAction], items: [turn], next_cursor: turn.turn_id, has_more: false,
};

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

describe('director archived responses and current authority', () => {
  it('accepts the saved proposal response with no prose, without inventing prose or confirmation', () => {
    expect(parseDirectorReply(savedReply, requestId)).toEqual(savedReply);
  });

  it('accepts a saved brief proposal with null targets before application', () => {
    const brief = {
      ...ordinaryReply,
      artifacts: [{ ...scriptArtifact, type: 'brief', target_id: null, target_version: null }],
      actions: [{ type: 'apply_brief', proposal_id: 'prop_script', proposal_version: 1, requires_user_action: true }],
    };
    expect(parseDirectorReply(brief, requestId)).toEqual(brief);
  });

  it('keeps history as history and accepts only the current top-level actions as executable actions', () => {
    const value = { ...messages, actions: [] };
    const parsed = parseDirectorMessages(value, projectId);
    expect(parsed.items[0].structured.actions).toEqual([confirmAction]);
    expect(parsed.actions).toEqual([]);
    expect(parsed.items[0].reply).toBe('');
  });

  it.each([
    { ...ordinaryReply, request_id: 'another_request' },
    { ...ordinaryReply, request_status: 'pending' },
    { ...ordinaryReply, workflow_revision: Number.MAX_SAFE_INTEGER + 1 },
    { ...ordinaryReply, artifacts: [{ ...scriptArtifact, target_id: null, target_version: null }] },
    { ...ordinaryReply, artifacts: [{ ...scriptArtifact, confirmed: true }] },
    { ...ordinaryReply, actions: [{ ...confirmAction, requires_user_action: false }] },
  ])('rejects mismatched, unbound or implicitly approved chat acknowledgement: %j', value => {
    expect(() => parseDirectorReply(value, requestId)).toThrow();
  });

  it.each([
    { ...messages, project_id: 'another_project' },
    { ...messages, actions: undefined },
    { ...messages, session_id: '' },
    { ...messages, workflow_revision: -1 },
    { ...messages, has_more: 'false' },
  ])('rejects incomplete or cross-project message state: %j', value => {
    expect(() => parseDirectorMessages(value, projectId)).toThrow();
  });

  it('never accepts a generated submit action or a missing explicit user gate', () => {
    expect(() => parseDirectorActions([{ type: 'submit', requires_user_action: true }])).toThrow();
    expect(() => parseDirectorActions([{ type: 'quote_generation' }])).toThrow();
    expect(parseDirectorActions([{ type: 'quote_generation', requires_user_action: true }])).toEqual([
      { type: 'quote_generation', requires_user_action: true },
    ]);
  });
});

describe('director single-attempt routes, paging and recovery (fetch mocked)', () => {
  it('does not call on construction and pages using the exact cursor inside the same project', async () => {
    const pid = '项目 /1', cursor = 'dturn/ older?1';
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(response({ ...messages, project_id: pid }))
      .mockResolvedValueOnce(response({ ...messages, project_id: pid, items: [], next_cursor: null, has_more: false }));
    const api = createProjectDirectorApi(fetcher);
    expect(fetcher).not.toHaveBeenCalled();
    await api.messages(pid);
    expect(await api.messages(pid, cursor)).toMatchObject({ items: [], has_more: false });
    expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
      '/api/projects/' + encodeURIComponent(pid) + '/director/messages',
      '/api/projects/' + encodeURIComponent(pid) + '/director/messages?cursor=' + encodeURIComponent(cursor),
    ]);
    expect(fetcher.mock.calls.map(([, init]) => init?.method)).toEqual(['GET', 'GET']);
  });

  it('sends one explicit chat with the persisted request, project session and workflow revision', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response(ordinaryReply));
    const api = createProjectDirectorApi(fetcher);
    expect(await api.chat(projectId, { id: requestId, message: '保留瓶盖', session_id: 'asst_project', revision: 3 })).toEqual(ordinaryReply);
    expect(fetcher.mock.calls[0][0]).toBe('/api/projects/prj_director/director/chat');
    expect(fetcher.mock.calls[0][1]?.method).toBe('POST');
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual({
      client_request_id: requestId, message: '保留瓶盖', session_id: 'asst_project', expected_workflow_revision: 3,
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it.each(['pending', 'failed'] as const)('recovers a %s receipt with a GET only, without replaying chat', async status => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response({
      client_request_id: requestId, status, response: null, error_code: status === 'failed' ? 'LLM_ERROR' : null,
    }));
    expect(await createProjectDirectorApi(fetcher).receipt(projectId, requestId)).toMatchObject({ status, response: null });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][0]).toBe('/api/projects/prj_director/director/requests/req-fixture-1');
    expect(fetcher.mock.calls[0][1]?.method).toBe('GET');
  });

  it('recovers an archived completed empty-text proposal instead of leaving it pending', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response({
      client_request_id: requestId, status: 'completed', response: savedReply,
    }));
    expect(await createProjectDirectorApi(fetcher).receipt(projectId, requestId)).toEqual({
      status: 'completed', error_code: undefined, response: savedReply,
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('rejects a receipt for a different request and does not issue a new request', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response({ client_request_id: 'wrong', status: 'completed', response: ordinaryReply }));
    await expect(createProjectDirectorApi(fetcher).receipt(projectId, requestId)).rejects.toMatchObject({ code: 'DIRECTOR_RESPONSE_INVALID' });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('applies only the selected proposal version and never dispatches generation or confirmation', async () => {
    const action = { type: 'apply_shot_plan' as const, proposal_id: 'prop /1', proposal_version: 2, requires_user_action: true as const };
    const result = { proposal_id: action.proposal_id, applied: true, workflow_revision: 4 };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response(result));
    expect(await createProjectDirectorApi(fetcher).apply(projectId, action, 'apply-1')).toEqual(result);
    expect(fetcher.mock.calls[0][0]).toBe('/api/projects/prj_director/director/proposals/' + encodeURIComponent(action.proposal_id) + '/apply');
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual({ expected_version: 2, client_request_id: 'apply-1', user_action: 'apply_proposal' });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('retains a known revision conflict and preserves an uncertain chat for recovery without retrying', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(response({ error: { code: 'WORKFLOW_VERSION_CONFLICT', message: '流程已有更新' } }, 409))
      .mockRejectedValueOnce(new Error('network lost'));
    const api = createProjectDirectorApi(fetcher), pending = { id: requestId, message: '两个镜头' };
    await expect(api.chat(projectId, pending)).rejects.toMatchObject({ code: 'WORKFLOW_VERSION_CONFLICT', status: 409 });
    await expect(api.chat(projectId, pending)).rejects.toMatchObject({ code: 'DIRECTOR_RESULT_UNKNOWN', status: 0 });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it('never dispatches an already-aborted read or write', async () => {
    const fetcher = vi.fn<typeof fetch>(), controller = new AbortController();
    controller.abort();
    const api = createProjectDirectorApi(fetcher);
    await expect(api.messages(projectId, undefined, { signal: controller.signal })).rejects.toMatchObject({ code: 'REQUEST_ABORTED' });
    await expect(api.chat(projectId, { id: requestId, message: '不要重发' }, { signal: controller.signal })).rejects.toMatchObject({ code: 'REQUEST_ABORTED' });
    expect(fetcher).not.toHaveBeenCalled();
  });
});
