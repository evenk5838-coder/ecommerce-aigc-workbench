import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../api';
import { ASSISTANT_REQUEST_TIMEOUT_MS, createAssistantApi } from './api';
import type { AssistantApproval, AssistantQuote, AssistantSession, ChatInput, ConfirmRequest } from './types';

const session: AssistantSession = { session_id: 'asst_test', project_id: 'draft_test', intent: 'generate', status: 'active' };
const input: ChatInput = { project_id: 'draft_test', message: '请帮我润色当前描述', intent: 'optimize_only' };
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
function frozen() {
  const payload = { kind: 'video' as const, model_id: 'doubao-seedance-1-0-pro-fast-251015', params: {},
    input_asset_id: 'asset_product', template_id: 'tpl_product_motion_5s', template_version: 2,
    template_fields: { motion: '推近特写', duration: 5, ratio: '9:16' } };
  const expires_at = Date.now() / 1000 + 600;
  const quote: AssistantQuote = { quote_id: 'q_test', plan_hash: 'backend-plan-hash', expires_at, cost_cny: 0.4536,
    resolved_params: { prompt: ' 后端最终提示词，不要裁剪或重建。 ' }, effective: { duration: 5, ratio: '9:16' }, submit_payload: payload };
  const approval: AssistantApproval = { approval_id: 'ap_test', quote_id: 'q_test', plan_hash: quote.plan_hash,
    session_id: session.session_id, project_id: session.project_id, expires_at, max_uses: 1, cost_cny: 0.4536, submit_payload: payload };
  return { quote, approval, payload };
}

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

describe('assistant same-origin transport (all fetch calls mocked)', () => {
  it('uses encoded IDs and the correct methods for status, restore, intent, quote, usage and brief', async () => {
    const fetcher = vi.fn<typeof fetch>();
    const api = createAssistantApi(fetcher);
    fetcher.mockResolvedValueOnce(response({ available: false, state: 'not_configured', provider: 'none' }));
    expect((await api.status()).available).toBe(false);
    fetcher.mockResolvedValueOnce(response({ project_id: '草稿 /1', session: null, history: [], brief: {} }));
    expect((await api.recoverSession('草稿 /1')).session).toBeNull();
    fetcher.mockResolvedValueOnce(response(session));
    await api.setIntent(session.session_id, 'generate', '用户主动切换');
    const { quote, payload } = frozen();
    fetcher.mockResolvedValueOnce(response(quote));
    await api.quote({ session, payload });
    fetcher.mockResolvedValueOnce(response({ ...quote, quote_id: undefined, id: 'q_test' }));
    await api.getQuote('q/test');
    fetcher.mockResolvedValueOnce(response({ assistant: { estimated_cost_cny: null }, generation: { estimated_cost_cny: null } }));
    expect((await api.usage('asst/test')).assistant.estimated_cost_cny).toBeNull();
    fetcher.mockResolvedValueOnce(response({ project_id: 'draft_test', brief: { subject: '单瓶' }, version: 1 }));
    await api.saveBrief('draft_test', { subject: '单瓶' });
    expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
      '/api/assistant/status', '/api/assistant/session?project_id=' + encodeURIComponent('草稿 /1'),
      '/api/assistant/intent', '/api/assistant/quote', '/api/assistant/quote/q%2Ftest',
      '/api/assistant/usage?session_id=asst%2Ftest', '/api/assistant/brief?project_id=draft_test',
    ]);
    expect(fetcher.mock.calls[2][1]?.method).toBe('PUT');
    expect(fetcher.mock.calls[3][1]?.method).toBe('POST');
    expect(JSON.parse(String(fetcher.mock.calls[3][1]?.body))).toEqual({ session_id: session.session_id, project_id: session.project_id, payload });
  });

  it('confirms only on the explicit action and submits only the frozen payload plus approval_id', async () => {
    const { quote, approval, payload } = frozen();
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(response(approval))
      .mockResolvedValueOnce(response({ task_id: 'task_existing', idempotent_replay: true }));
    const api = createAssistantApi(fetcher);
    const confirmed = await api.confirm({ session, quote, userAction: 'confirm_generate' });
    expect(fetcher).toHaveBeenCalledTimes(1); // confirm never auto-submits.
    const result = await api.submitApproved({ session, quote, approval: confirmed });
    expect(result.idempotent_replay).toBe(true);
    expect(fetcher.mock.calls[0][0]).toBe('/api/assistant/confirm');
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual({
      session_id: session.session_id, project_id: session.project_id, quote_id: 'q_test', user_action: 'confirm_generate',
    });
    expect(fetcher.mock.calls[1][0]).toBe('/api/tasks');
    expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).toEqual({ ...payload, approval_id: 'ap_test' });
    expect(quote.submit_payload).toEqual(payload);
  });

  it('blocks new confirmation of an expired quote but honors an existing matching approval until its own expiry', async () => {
    const { quote, approval, payload } = frozen();
    const expiredQuote = { ...quote, expires_at: Date.now() / 1000 - 1, expired: true };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response({ task_id: 'task_approved' }));
    const api = createAssistantApi(fetcher);
    await expect(api.confirm({ session, quote: expiredQuote, userAction: 'confirm_generate' })).rejects.toThrow('报价已过期');
    expect(fetcher).not.toHaveBeenCalled();
    await expect(api.submitApproved({ session, quote: expiredQuote, approval })).resolves.toMatchObject({ task_id: 'task_approved' });
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual({ ...payload, approval_id: 'ap_test' });
    expect(() => api.submitApproved({
      session, quote: expiredQuote, approval: { ...approval, expires_at: Date.now() / 1000 - 1 },
    })).toThrow('确认记录已过期');
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('accepts a valid approval when the confirmation response arrives after the original quote expires', async () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date('2026-10-25T08:00:00Z'));
    const { quote, approval, payload } = frozen();
    const almostExpiredQuote = { ...quote, expires_at: Date.now() / 1000 + 1 };
    const fetcher = vi.fn<typeof fetch>()
      .mockImplementationOnce(async () => {
        vi.setSystemTime(Date.now() + 2_000);
        return response(approval);
      })
      .mockResolvedValueOnce(response({ task_id: 'task_after_confirmation' }));
    const api = createAssistantApi(fetcher);
    const confirmed = await api.confirm({ session, quote: almostExpiredQuote, userAction: 'confirm_generate' });
    expect(almostExpiredQuote.expires_at * 1000).toBeLessThan(Date.now());
    expect(fetcher).toHaveBeenCalledTimes(1);
    await expect(api.submitApproved({ session, quote: almostExpiredQuote, approval: confirmed }))
      .resolves.toMatchObject({ task_id: 'task_after_confirmation' });
    expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).toEqual({ ...payload, approval_id: 'ap_test' });
  });

  it('blocks optimize-only quote, confirm and submit before any request', async () => {
    const fetcher = vi.fn<typeof fetch>(), api = createAssistantApi(fetcher);
    const { quote, approval, payload } = frozen(), optimize = { ...session, intent: 'optimize_only' };
    expect(() => api.quote({ session: optimize, payload })).toThrow('仅优化');
    await expect(api.confirm({ session: optimize, quote, userAction: 'confirm_generate' })).rejects.toThrow('仅优化');
    expect(() => api.submitApproved({ session: optimize, quote, approval })).toThrow('仅优化');
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('blocks missing quote fields and absent confirmation action before any request', async () => {
    const fetcher = vi.fn<typeof fetch>(), api = createAssistantApi(fetcher), { quote } = frozen();
    await expect(api.confirm({ session, quote: { ...quote, resolved_params: null }, userAction: 'confirm_generate' })).rejects.toThrow('最终提示词');
    await expect(api.confirm({ session, quote } as ConfirmRequest)).rejects.toMatchObject({ code: 'ASSISTANT_USER_CONFIRM_REQUIRED' });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('does not silently promote a partial GET quote to a complete plan', async () => {
    const partial = { id: 'q_partial', cost_cny: 0.12 };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response(partial));
    expect(await createAssistantApi(fetcher).getQuote('q_partial')).toEqual(partial);
  });

  it('times out the entire request once, with no automatic retry', async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn<typeof fetch>().mockImplementation(() => new Promise<Response>(() => {}));
    const pending = createAssistantApi(fetcher).chat(input);
    const assertion = expect(pending).rejects.toMatchObject({ code: 'ASSISTANT_REQUEST_TIMEOUT', status: 0 });
    await vi.advanceTimersByTimeAsync(ASSISTANT_REQUEST_TIMEOUT_MS);
    await assertion;
    expect(ASSISTANT_REQUEST_TIMEOUT_MS).toBeGreaterThan(90_000);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][1]?.signal?.aborted).toBe(true);
    await vi.advanceTimersByTimeAsync(ASSISTANT_REQUEST_TIMEOUT_MS);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('also times out while waiting for a response body', async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue({ ok: true, json: () => new Promise(() => {}) } as Response);
    const assertion = expect(createAssistantApi(fetcher).status({ timeoutMs: 100 })).rejects.toMatchObject({ code: 'ASSISTANT_REQUEST_TIMEOUT' });
    await vi.advanceTimersByTimeAsync(100);
    await assertion;
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('allows stopping a wait without claiming to cancel backend processing', async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn<typeof fetch>().mockImplementation(() => new Promise<Response>(() => {}));
    const controller = new AbortController();
    const pending = createAssistantApi(fetcher).chat(input, { signal: controller.signal });
    controller.abort();
    await expect(pending).rejects.toMatchObject({ code: 'ASSISTANT_REQUEST_ABORTED', message: expect.stringContaining('不会取消服务端处理') });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('does not send a request for an already-aborted signal', async () => {
    const fetcher = vi.fn<typeof fetch>(), controller = new AbortController();
    controller.abort();
    await expect(createAssistantApi(fetcher).status({ signal: controller.signal })).rejects.toMatchObject({ code: 'ASSISTANT_REQUEST_ABORTED' });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('cleans up timers and caller abort listeners after a successful response', async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response({ available: true, state: 'configured_unverified', provider: 'deepseek' }));
    const controller = new AbortController();
    await createAssistantApi(fetcher).status({ signal: controller.signal });
    controller.abort();
    expect(fetcher.mock.calls[0][1]?.signal?.aborted).toBe(false);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('preserves ApiError codes for backend failures and never retries them', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response({ error: { code: 'ASSISTANT_UNAVAILABLE', message: '助手暂不可用' } }, 503));
    const pending = createAssistantApi(fetcher).chat(input);
    await expect(pending).rejects.toBeInstanceOf(ApiError);
    await expect(pending).rejects.toMatchObject({ code: 'ASSISTANT_UNAVAILABLE', status: 503 });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('does not retry network errors or leak their raw diagnostic text', async () => {
    const fetcher = vi.fn<typeof fetch>().mockRejectedValue(new Error('internal secret diagnostic'));
    await expect(createAssistantApi(fetcher).chat(input)).rejects.toMatchObject({
      code: 'ASSISTANT_NETWORK_ERROR', message: expect.not.stringContaining('internal secret'),
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('rejects malformed recovery rather than pretending there is no saved conversation', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response({ project_id: 'draft_test', history: [], brief: {} }));
    await expect(createAssistantApi(fetcher).recoverSession('draft_test')).rejects.toMatchObject({ code: 'ASSISTANT_RESPONSE_INVALID' });
  });

  it('rejects a provider.stub reply instead of exposing it as a successful assistant response', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response({
      session_id: session.session_id, session, intent: 'optimize_only', reply: '测试文本',
      provider: { name: 'fixed', stub: true },
    }));
    await expect(createAssistantApi(fetcher).chat(input)).rejects.toMatchObject({ code: 'ASSISTANT_TEST_PROVIDER' });
  });
});
