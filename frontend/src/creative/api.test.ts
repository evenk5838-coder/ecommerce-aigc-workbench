import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../api';
import { CREATIVE_GENERATION_TIMEOUT_MS, CREATIVE_REQUEST_TIMEOUT_MS, createCreativeApi } from './api';
import type { CreativeApi, CreativeProject, CreativeScript, ScriptWriteResult } from './types';

const project: CreativeProject = {
  project_id: 'prj_test', name: '商品短片', brief: { subject: '陶瓷杯', quantity: 1 },
  status: 'script_confirmed', created_at: 1_700_000_000, updated_at: 1_700_000_010,
};
const script: CreativeScript = {
  script_id: 'scr_test', project_id: project.project_id, version: 2,
  content: { summary: '展示杯身与杯口', shots: [{ seq: 1, hint: '杯身特写' }], parse_ok: true },
  source: 'manual', confirmed_at: 1_700_000_015, created_at: 1_700_000_012,
};
const summary = { script_id: script.script_id, version: script.version, source: script.source, confirmed_at: script.confirmed_at };
const detail = { ...project, script_count: 2, latest_script: summary };
const written: ScriptWriteResult = { script_id: script.script_id, version: script.version, content: script.content, source: 'manual' };
const generated = { ...written, source: 'assistant', assistant_unavailable: false, llm_usage: { input_tokens: 20, output_tokens: 50 } };
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const mutators: Array<{ name: string; call: (api: CreativeApi) => Promise<unknown> }> = [
  { name: 'create project', call: api => api.createProject('商品短片', project.brief) },
  { name: 'update project', call: api => api.updateProject(project.project_id, { brief: { notes: '改为明亮风格' } }) },
  { name: 'generate script', call: api => api.generateScript(project.project_id, { instruction: '简洁' }) },
  { name: 'manual script', call: api => api.addManualScript(project.project_id, { content: script.content }) },
  { name: 'edit script', call: api => api.editScript(script.script_id, { content: script.content, base_version: 2 }) },
  { name: 'confirm script', call: api => api.confirmScript(script.script_id) },
];

afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

describe('S2 API routes and unmodified server data (fetch mocked)', () => {
  it('lists and restores projects, creates and updates using the documented methods', async () => {
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(response({ items: [{ ...project, script_count: 2 }] }))
      .mockResolvedValueOnce(response(detail))
      .mockResolvedValueOnce(response(project))
      .mockResolvedValueOnce(response({ ...project, stale: ['script_confirm', 'shot_plan_confirm'] }));
    const api = createCreativeApi(fetcher);
    expect(await api.listProjects(12)).toEqual({ items: [{ ...project, script_count: 2 }] });
    expect(await api.getProject(project.project_id)).toEqual(detail);
    expect(await api.createProject(project.name, project.brief)).toEqual(project);
    expect(await api.updateProject(project.project_id, { brief: { notes: '保留杯口' } })).toMatchObject({
      status: 'script_confirmed', stale: ['script_confirm', 'shot_plan_confirm'],
    });
    expect(fetcher.mock.calls.map(([url]) => url)).toEqual(['/api/projects?limit=12', '/api/projects/prj_test', '/api/projects', '/api/projects/prj_test']);
    expect(fetcher.mock.calls.map(([, init]) => init?.method ?? 'GET')).toEqual(['GET', 'GET', 'POST', 'PATCH']);
    expect(JSON.parse(String(fetcher.mock.calls[2][1]?.body))).toEqual({ name: project.name, brief: project.brief });
    expect(JSON.parse(String(fetcher.mock.calls[3][1]?.body))).toEqual({ brief: { notes: '保留杯口' } });
  });

  it('encodes IDs and keeps all operations within the documented S2 routes', async () => {
    const pid = '项目 /1', sid = '脚本 /2';
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(response({ ...detail, project_id: pid }))
      .mockResolvedValueOnce(response({ items: [{ ...script, project_id: pid }] }))
      .mockResolvedValueOnce(response({ ...script, script_id: sid }))
      .mockResolvedValueOnce(response({ script_id: sid, version: 2, confirmed_at: 20, unlocked: ['storyboard_plan'] }));
    const api = createCreativeApi(fetcher);
    await api.getProject(pid);
    await api.listScripts(pid);
    await api.getScript(sid);
    expect(await api.confirmScript(sid)).toEqual({ script_id: sid, version: 2, confirmed_at: 20, unlocked: ['storyboard_plan'] });
    expect(fetcher.mock.calls.map(([url]) => url)).toEqual([
      '/api/projects/' + encodeURIComponent(pid), '/api/projects/' + encodeURIComponent(pid) + '/scripts',
      '/api/scripts/' + encodeURIComponent(sid), '/api/scripts/' + encodeURIComponent(sid) + '/confirm',
    ]);
    expect(JSON.parse(String(fetcher.mock.calls[3][1]?.body))).toEqual({ confirm: true });
  });

  it('generates only when explicitly called and returns the AI unavailable union unchanged', async () => {
    const unavailable = { assistant_unavailable: true, hint: '助手暂不可用，可先手工编写脚本', project_id: project.project_id };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(response(unavailable)).mockResolvedValueOnce(response(generated));
    const api = createCreativeApi(fetcher);
    expect(fetcher).not.toHaveBeenCalled();
    expect(await api.generateScript(project.project_id, {})).toEqual(unavailable);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(await api.generateScript(project.project_id, { instruction: '偏简洁', target_duration_s: 20 })).toEqual(generated);
    expect(fetcher.mock.calls[1][0]).toBe('/api/projects/prj_test/scripts');
    expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).toEqual({ instruction: '偏简洁', target_duration_s: 20 });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it('keeps manual acknowledgements partial and sends explicit version preconditions for edits', async () => {
    const next = { ...script, script_id: 'scr_new', version: 3, confirmed_at: null };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(response(written)).mockResolvedValueOnce(response(next));
    const api = createCreativeApi(fetcher);
    const manual = await api.addManualScript(project.project_id, { content: script.content, base_version: 0 });
    expect(manual).toEqual(written);
    expect(manual).not.toHaveProperty('project_id');
    expect(manual).not.toHaveProperty('confirmed_at');
    expect(await api.editScript(script.script_id, { content: script.content, base_version: 2 })).toEqual(next);
    expect(fetcher.mock.calls[0][0]).toBe('/api/projects/prj_test/scripts/manual');
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual({ content: script.content, base_version: 0 });
    expect(fetcher.mock.calls[1][1]?.method).toBe('PATCH');
    expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).toEqual({ content: script.content, base_version: 2 });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it('preserves ambiguous brief values, unknown content fields, degraded output and historical confirmation without inferring validity', async () => {
    const brief = { subject: { original: '杯子' }, selling_points: ['双层'], notes: ['用户原文'], target_duration_s: '待定' };
    const degraded = { ...generated, content: { summary: '模型原文', shots: [], parse_ok: false, degraded_reason: 'model_output_not_structured', future_field: { keep: true } } };
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(response({ ...detail, brief }))
      .mockResolvedValueOnce(response(degraded));
    const api = createCreativeApi(fetcher);
    const restored = await api.getProject(project.project_id);
    expect(restored.brief).toEqual(brief);
    expect(restored.latest_script?.confirmed_at).toBe(script.confirmed_at);
    expect(restored).not.toHaveProperty('confirmation_valid');
    expect(await api.generateScript(project.project_id, {})).toEqual(degraded);
  });
});

describe('S2 response validation', () => {
  it.each([null, {}, { items: null }, { items: [{ ...project }] }, { items: [{ ...project, script_count: -1 }] }])('rejects malformed project lists: %j', body => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response(body));
    return expect(createCreativeApi(fetcher).listProjects()).rejects.toMatchObject({ code: 'CREATIVE_RESPONSE_INVALID' });
  });

  it.each([
    { ...detail, project_id: 'other' }, { ...detail, brief: [] }, { ...detail, latest_script: undefined },
    { ...detail, latest_script: { ...summary, version: 0 } }, { ...detail, latest_script: { ...summary, confirmed_at: 'yes' } },
  ])('rejects malformed project detail without inventing missing data: %j', body => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response(body));
    return expect(createCreativeApi(fetcher).getProject(project.project_id)).rejects.toMatchObject({ code: 'CREATIVE_RESPONSE_INVALID' });
  });

  it('accepts an empty project with an explicit null latest script', async () => {
    const empty = { ...detail, script_count: 0, latest_script: null };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response(empty));
    expect(await createCreativeApi(fetcher).getProject(project.project_id)).toEqual(empty);
  });

  it('rejects script lists belonging to another project', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response({ items: [script, { ...script, project_id: 'prj_other' }] }));
    await expect(createCreativeApi(fetcher).listScripts(project.project_id)).rejects.toMatchObject({ code: 'CREATIVE_RESPONSE_INVALID' });
  });

  it.each([
    { ...script, script_id: 'other' }, { ...script, version: '2' }, { ...script, content: null },
    { ...script, content: { summary: 12 } }, { ...script, content: { shots: {} } }, { ...script, confirmed_at: undefined },
  ])('rejects malformed script details: %j', body => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response(body));
    return expect(createCreativeApi(fetcher).getScript(script.script_id)).rejects.toMatchObject({ code: 'CREATIVE_RESPONSE_INVALID' });
  });

  it.each([
    { ...generated, assistant_unavailable: undefined }, { ...generated, assistant_unavailable: 'false' },
    { ...generated, source: 'manual' }, { ...generated, llm_usage: {} },
    { assistant_unavailable: true, hint: '', project_id: project.project_id },
    { assistant_unavailable: true, hint: '不可用', project_id: 'prj_other' },
  ])('treats malformed generation acknowledgements as unknown mutation results: %j', body => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response(body));
    return expect(createCreativeApi(fetcher).generateScript(project.project_id, {})).rejects.toMatchObject({
      code: 'CREATIVE_RESULT_UNKNOWN', detail: { reason: 'response_invalid' },
    });
  });

  it('does not infer successful confirmation from a partial or mismatched receipt', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response({ script_id: 'other', version: 2, confirmed_at: 10, unlocked: [] }));
    await expect(createCreativeApi(fetcher).confirmScript(script.script_id)).rejects.toMatchObject({
      code: 'CREATIVE_RESULT_UNKNOWN', detail: { reason: 'response_invalid' },
    });
  });

  it('rejects invalid IDs, version preconditions and JSON before sending', () => {
    const fetcher = vi.fn<typeof fetch>(), api = createCreativeApi(fetcher);
    expect(() => api.getProject(' ')).toThrow(ApiError);
    expect(() => api.listProjects(1.5)).toThrow(ApiError);
    expect(() => api.createProject('', {})).toThrow(ApiError);
    expect(() => api.editScript(script.script_id, { content: {}, base_version: 0 })).toThrow(ApiError);
    const cyclic: Record<string, unknown> = {};
    cyclic.self = cyclic;
    expect(() => api.addManualScript(project.project_id, { content: cyclic })).toThrow(ApiError);
    expect(fetcher).not.toHaveBeenCalled();
  });
});

describe('single-attempt transport and uncertain mutations', () => {
  it.each(mutators)('marks a network failure during $name as unknown without retrying', async ({ call }) => {
    const fetcher = vi.fn<typeof fetch>().mockRejectedValue(new Error('private diagnostic'));
    await expect(call(createCreativeApi(fetcher))).rejects.toMatchObject({
      code: 'CREATIVE_RESULT_UNKNOWN', status: 0, detail: { reason: 'network' }, message: expect.not.stringContaining('private diagnostic'),
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('keeps read-only network errors ordinary and never retries', async () => {
    const fetcher = vi.fn<typeof fetch>().mockRejectedValue(new Error('network down'));
    await expect(createCreativeApi(fetcher).getProject(project.project_id)).rejects.toMatchObject({ code: 'CREATIVE_NETWORK_ERROR' });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('preserves 409 code, status and detail as a known rejection', async () => {
    const conflict = { current_version: 4, base_version: 2, future_detail: 'keep' };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response({ error: { code: 'SCRIPT_VERSION_CONFLICT', message: '已有更新版本', detail: conflict } }, 409));
    const pending = createCreativeApi(fetcher).editScript(script.script_id, { content: {}, base_version: 2 });
    await expect(pending).rejects.toBeInstanceOf(ApiError);
    await expect(pending).rejects.toMatchObject({ code: 'SCRIPT_VERSION_CONFLICT', status: 409, detail: conflict });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it.each(mutators)('marks HTTP 5xx during $name as unknown while retaining backend error information', async ({ call }) => {
    const original = { code: 'PERSISTENCE_FAILURE', message: '写入后发生异常', detail: { script_id: 'scr_possible' } };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response({ error: original }, 500));
    await expect(call(createCreativeApi(fetcher))).rejects.toMatchObject({
      code: 'CREATIVE_RESULT_UNKNOWN', status: 500,
      detail: { reason: 'server_error', original: { ...original, status: 500 } },
    });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('keeps a read-only 503 and a mutation 400 as ordinary backend errors', async () => {
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(response({ error: { code: 'UNAVAILABLE', message: '稍后再试', detail: { read: true } } }, 503))
      .mockResolvedValueOnce(response({ error: { code: 'VALIDATION_FAILED', message: '缺少商品', detail: { field: 'subject' } } }, 400));
    const api = createCreativeApi(fetcher);
    await expect(api.getProject(project.project_id)).rejects.toMatchObject({ code: 'UNAVAILABLE', status: 503, detail: { read: true } });
    await expect(api.generateScript(project.project_id, {})).rejects.toMatchObject({ code: 'VALIDATION_FAILED', status: 400, detail: { field: 'subject' } });
  });

  it('waits 120 seconds for generation and then reports an unknown result once', async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn<typeof fetch>().mockImplementation(() => new Promise<Response>(() => {}));
    const pending = createCreativeApi(fetcher).generateScript(project.project_id, {});
    const assertion = expect(pending).rejects.toMatchObject({ code: 'CREATIVE_RESULT_UNKNOWN', detail: { reason: 'timeout', method: 'POST', path: '/projects/prj_test/scripts' } });
    expect(CREATIVE_GENERATION_TIMEOUT_MS).toBe(120_000);
    await vi.advanceTimersByTimeAsync(CREATIVE_REQUEST_TIMEOUT_MS);
    expect(fetcher.mock.calls[0][1]?.signal?.aborted).toBe(false);
    await vi.advanceTimersByTimeAsync(CREATIVE_GENERATION_TIMEOUT_MS - CREATIVE_REQUEST_TIMEOUT_MS);
    await assertion;
    expect(fetcher.mock.calls[0][1]?.signal?.aborted).toBe(true);
    await vi.advanceTimersByTimeAsync(CREATIVE_GENERATION_TIMEOUT_MS);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(vi.getTimerCount()).toBe(0);
  });

  it('uses the 20-second default for other mutations and includes response-body time', async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue({ ok: true, status: 200, json: () => new Promise(() => {}) } as Response);
    const assertion = expect(createCreativeApi(fetcher).updateProject(project.project_id, { name: '新名' })).rejects.toMatchObject({ code: 'CREATIVE_RESULT_UNKNOWN', detail: { reason: 'timeout' } });
    expect(CREATIVE_REQUEST_TIMEOUT_MS).toBe(20_000);
    await vi.advanceTimersByTimeAsync(CREATIVE_REQUEST_TIMEOUT_MS);
    await assertion;
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('uses ordinary timeout for reads and permits an explicit shorter wait', async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn<typeof fetch>().mockImplementation(() => new Promise<Response>(() => {}));
    const assertion = expect(createCreativeApi(fetcher).getScript(script.script_id, { timeoutMs: 50 })).rejects.toMatchObject({ code: 'CREATIVE_REQUEST_TIMEOUT' });
    await vi.advanceTimersByTimeAsync(50);
    await assertion;
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('marks cancellation after dispatch as unknown and cleans up listeners and timers', async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn<typeof fetch>().mockImplementation(() => new Promise<Response>(() => {}));
    const controller = new AbortController();
    const remove = vi.spyOn(controller.signal, 'removeEventListener');
    const pending = createCreativeApi(fetcher).confirmScript(script.script_id, { signal: controller.signal });
    controller.abort();
    await expect(pending).rejects.toMatchObject({ code: 'CREATIVE_RESULT_UNKNOWN', detail: { reason: 'aborted' } });
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][1]?.signal?.aborted).toBe(true);
    expect(remove).toHaveBeenCalledWith('abort', expect.any(Function));
    expect(vi.getTimerCount()).toBe(0);
  });

  it('does not dispatch an already-aborted operation or mislabel it as an unknown result', async () => {
    const fetcher = vi.fn<typeof fetch>(), controller = new AbortController();
    controller.abort();
    await expect(createCreativeApi(fetcher).createProject('商品', {}, { signal: controller.signal })).rejects.toMatchObject({ code: 'CREATIVE_REQUEST_ABORTED' });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('cannot report a late response as success after timeout', async () => {
    vi.useFakeTimers();
    let resolve: ((response: Response) => void) | undefined;
    const fetcher = vi.fn<typeof fetch>().mockImplementation(() => new Promise<Response>(done => { resolve = done; }));
    const success = vi.fn();
    const pending = createCreativeApi(fetcher).generateScript(project.project_id, {}, { timeoutMs: 10 }).then(success);
    const assertion = expect(pending).rejects.toMatchObject({ code: 'CREATIVE_RESULT_UNKNOWN', detail: { reason: 'timeout' } });
    await vi.advanceTimersByTimeAsync(10);
    await assertion;
    resolve?.(response(generated));
    await Promise.resolve();
    await Promise.resolve();
    expect(success).not.toHaveBeenCalled();
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('treats invalid JSON in a mutation success response as unknown but keeps read failures ordinary', async () => {
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response('not-json', { status: 200 }))
      .mockResolvedValueOnce(new Response('not-json', { status: 200 }));
    const api = createCreativeApi(fetcher);
    await expect(api.addManualScript(project.project_id, { content: {} })).rejects.toMatchObject({ code: 'CREATIVE_RESULT_UNKNOWN', detail: { reason: 'response_invalid' } });
    await expect(api.getProject(project.project_id)).rejects.toMatchObject({ code: 'CREATIVE_RESPONSE_INVALID' });
  });

  it('keeps non-JSON 4xx rejection ordinary and non-JSON 5xx mutation uncertain', async () => {
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response('rejected', { status: 403 }))
      .mockResolvedValueOnce(new Response('bad gateway', { status: 502 }));
    const api = createCreativeApi(fetcher);
    await expect(api.createProject('商品', {})).rejects.toMatchObject({ code: 'HTTP_ERROR', status: 403 });
    await expect(api.createProject('商品', {})).rejects.toMatchObject({ code: 'CREATIVE_RESULT_UNKNOWN', status: 502, detail: { reason: 'server_error', original: { code: 'HTTP_ERROR', status: 502 } } });
  });

  it('retains known 4xx rejection even if reading its error body times out', async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue({ ok: false, status: 409, json: () => new Promise(() => {}) } as Response);
    const assertion = expect(createCreativeApi(fetcher).editScript(script.script_id, { content: {}, base_version: 2 }, { timeoutMs: 20 })).rejects.toMatchObject({ code: 'HTTP_ERROR', status: 409 });
    await vi.advanceTimersByTimeAsync(20);
    await assertion;
  });

  it('clears timeouts and stops following the caller signal after success', async () => {
    vi.useFakeTimers();
    const controller = new AbortController();
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response(detail));
    await createCreativeApi(fetcher).getProject(project.project_id, { signal: controller.signal });
    controller.abort();
    expect(fetcher.mock.calls[0][1]?.signal?.aborted).toBe(false);
    expect(vi.getTimerCount()).toBe(0);
  });

  it.each([0, -1, NaN, Infinity])('rejects invalid timeout without dispatch: %j', async timeoutMs => {
    const fetcher = vi.fn<typeof fetch>();
    await expect(createCreativeApi(fetcher).getProject(project.project_id, { timeoutMs })).rejects.toMatchObject({ code: 'CREATIVE_TIMEOUT_INVALID' });
    expect(fetcher).not.toHaveBeenCalled();
  });
});
