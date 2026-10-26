import { afterEach, describe, expect, it, vi } from 'vitest';
import { createDirectorReader, parseDirector } from './director';

const fixture = () => ({
  project_id: 'prj_director',
  project: { name: '单瓶商品视频', status: 'script_confirmed' },
  facts: { brief: { subject: '单瓶精华液', quantity: 1 }, ready: true },
  current: {
    stage: 'storyboard',
    script: { script_id: 'scr_current', version: 2, source: 'manual', confirmed_at: 100 },
    confirmation: { confirmed: true, valid: true, status: 'active', object_version: 2, confirmed_at: 100, invalidated_at: null, reason: null as string | null },
  },
  stages: [
    { key: 'requirements', label: '需求', status: 'done', visible: true, actions: ['edit_brief'] },
    { key: 'script', label: '脚本与镜头计划', status: 'done', visible: true, actions: ['regenerate_script', 'edit_shot_plan', 'add_shots'] },
    { key: 'storyboard', label: '分镜画面与审核', status: 'current', visible: true, actions: [] },
    { key: 'video', label: '视频生成', status: 'locked', visible: false, actions: [] },
    { key: 'delivery', label: '成片交付', status: 'locked', visible: false, actions: [] },
  ],
  allowed_actions: ['edit_brief', 'regenerate_script', 'edit_shot_plan', 'add_shots'],
  warnings: [] as Array<{ object: string; reason: string; message: string; version?: number }>,
  counts: { scripts: 2, shots: 0, takes: 0, films: 0 },
});
const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

describe('director server readiness contract', () => {
  it('maps only group names and preserves current, visible, actions and facts from the server', () => {
    const raw = fixture();
    const parsed = parseDirector(raw, raw.project_id);
    expect(parsed.stages.map(stage => stage.key)).toEqual(['brief', 'script', 'storyboard', 'generation', 'delivery']);
    expect(parsed.current.stage).toBe('storyboard');
    expect(parsed.stages.filter(stage => stage.visible).map(stage => stage.key)).toEqual(['brief', 'script', 'storyboard']);
    expect(parsed.stages[2].actions).toEqual([]); // A visible stage does not imply an executable action.
    expect(parsed.allowed_actions).toEqual(raw.allowed_actions);
    expect(parsed.facts).toEqual(raw.facts);
    expect(parsed.current.confirmation).toEqual(raw.current.confirmation);
    expect(raw.stages[0].key).toBe('requirements');
  });

  it('retains invalidated historical confirmation without using timestamps or artifact counts as proof', () => {
    const raw = fixture();
    raw.current.stage = 'script';
    raw.current.confirmation = { ...raw.current.confirmation, valid: false, status: 'invalidated', reason: 'upstream_brief_changed' };
    raw.stages[1].status = 'current';
    raw.stages[2] = { ...raw.stages[2], status: 'locked', visible: false };
    raw.allowed_actions = ['edit_brief', 'regenerate_script', 'confirm_script'];
    raw.warnings = [{ object: 'script', version: 2, reason: 'upstream_brief_changed', message: '脚本确认已失效或未确认，请重新核对' }];
    raw.counts = { scripts: 8, shots: 12, takes: 12, films: 2 };
    const parsed = parseDirector(raw);
    expect(parsed.current.script?.confirmed_at).toBe(100);
    expect(parsed.current.confirmation).toMatchObject({ confirmed: true, valid: false, status: 'invalidated', reason: 'upstream_brief_changed' });
    expect(parsed.stages[2]).toMatchObject({ status: 'locked', visible: false });
    expect(parsed.allowed_actions).not.toContain('quote_generation');
    expect(parsed.warnings).toEqual(raw.warnings);
  });

  it('accepts a new project with no script and does not synthesize a confirmation version', () => {
    const raw = fixture();
    const parsed = parseDirector({ ...raw, current: { stage: 'requirements', script: null, confirmation: { confirmed: false, valid: false, reason: 'no_script' } }, facts: { brief: {}, ready: false } });
    expect(parsed.current).toEqual({ stage: 'brief', script: null, confirmation: { confirmed: false, valid: false, reason: 'no_script' } });
    expect(parsed.facts.ready).toBe(false);
  });

  it('maps backend video current group to generation without changing status', () => {
    const raw = fixture(); raw.current.stage = 'video';
    expect(parseDirector(raw).current.stage).toBe('generation');
  });

  it.each([
    ['legacy local schema', { currentStep: 4, completed: ['brief', 'script'] }],
    ['missing confirmation', (() => { const raw = fixture(); return { ...raw, current: { stage: 'script', script: raw.current.script } }; })()],
    ['missing stage', (() => { const raw = fixture(); return { ...raw, stages: raw.stages.slice(0, 4) }; })()],
    ['duplicate stage', (() => { const raw = fixture(); raw.stages[4] = raw.stages[0]; return raw; })()],
    ['unknown stage', (() => { const raw = fixture(); raw.stages[4].key = 'custom'; return raw; })()],
    ['nonboolean visibility', (() => { const raw = fixture(); return { ...raw, stages: raw.stages.map((stage, index) => index ? stage : { ...stage, visible: 'yes' }) }; })()],
    ['unsupported stage status', (() => { const raw = fixture(); raw.stages[4].status = 'complete'; return raw; })()],
    ['missing permissions', (() => { const raw = fixture(); return { ...raw, allowed_actions: undefined }; })()],
    ['missing current valid', (() => { const raw = fixture(); return { ...raw, current: { ...raw.current, confirmation: { confirmed: true, confirmed_at: 100 } } }; })()],
    ['version mismatch', (() => { const raw = fixture(); raw.current.confirmation.object_version = 1; return raw; })()],
    ['invalid confirmation combination', (() => { const raw = fixture(); raw.current.confirmation.status = 'invalidated'; return raw; })()],
    ['malformed warning', (() => { const raw = fixture(); return { ...raw, warnings: [{ object: 'script', reason: 'stale' }] }; })()],
  ])('fails closed for %s', (_label, value) => {
    expect(() => parseDirector(value)).toThrow(expect.objectContaining({ code: 'DIRECTOR_RESPONSE_INVALID' }));
  });

  it('rejects a response belonging to another project', () => {
    expect(() => parseDirector(fixture(), 'prj_other')).toThrow(expect.objectContaining({ code: 'DIRECTOR_RESPONSE_INVALID' }));
  });
});

describe('director read-only transport (fetch mocked)', () => {
  it('does not call on construction and sends exactly one encoded same-origin GET', async () => {
    const id = '项目 /1';
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response({ ...fixture(), project_id: id }));
    const read = createDirectorReader(fetcher);
    expect(fetcher).not.toHaveBeenCalled();
    expect((await read(id)).project_id).toBe(id);
    expect(fetcher).toHaveBeenCalledTimes(1);
    const [url, init] = fetcher.mock.calls[0];
    expect(url).toBe(`/api/projects/${encodeURIComponent(id)}/director`);
    expect(init?.method).toBe('GET');
    expect(init?.body).toBeUndefined();
  });

  it('reports a missing route explicitly instead of synthesizing readiness', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response('Not Found', { status: 404 }));
    await expect(createDirectorReader(fetcher)('prj_director')).rejects.toMatchObject({ code: 'DIRECTOR_UNAVAILABLE', status: 404 });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('preserves a business error for a deleted project', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response({ error: { code: 'PROJECT_NOT_FOUND', message: '项目不存在', detail: { project_id: 'deleted' } } }, 404));
    await expect(createDirectorReader(fetcher)('deleted')).rejects.toMatchObject({ code: 'PROJECT_NOT_FOUND', status: 404, detail: { project_id: 'deleted' } });
  });

  it('rejects an old success response as schema invalid', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response({ stages: [], currentStep: 3 }));
    await expect(createDirectorReader(fetcher)('prj_director')).rejects.toMatchObject({ code: 'DIRECTOR_RESPONSE_INVALID' });
  });

  it('does not retry a failed network read', async () => {
    const fetcher = vi.fn<typeof fetch>().mockRejectedValue(new TypeError('offline'));
    await expect(createDirectorReader(fetcher)('prj_director')).rejects.toMatchObject({ code: 'CREATIVE_NETWORK_ERROR' });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('stops before send for an already canceled request', async () => {
    const controller = new AbortController(); controller.abort();
    const fetcher = vi.fn<typeof fetch>();
    await expect(createDirectorReader(fetcher)('prj_director', { signal: controller.signal })).rejects.toMatchObject({ code: 'CREATIVE_REQUEST_ABORTED' });
    expect(fetcher).not.toHaveBeenCalled();
  });

  it('honors caller cancellation even if fetch never settles', async () => {
    const controller = new AbortController();
    const fetcher = vi.fn<typeof fetch>().mockImplementation(() => new Promise<Response>(() => {}));
    const result = createDirectorReader(fetcher)('prj_director', { signal: controller.signal });
    const checked = expect(result).rejects.toMatchObject({ code: 'CREATIVE_REQUEST_ABORTED' });
    controller.abort(); await checked;
    expect(fetcher.mock.calls[0][1]?.signal?.aborted).toBe(true);
  });

  it('times out a pending read without a retry', async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn<typeof fetch>().mockImplementation(() => new Promise<Response>(() => {}));
    const checked = expect(createDirectorReader(fetcher)('prj_director', { timeoutMs: 25 })).rejects.toMatchObject({ code: 'CREATIVE_REQUEST_TIMEOUT' });
    await vi.advanceTimersByTimeAsync(25); await checked;
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(fetcher.mock.calls[0][1]?.signal?.aborted).toBe(true);
    expect(vi.getTimerCount()).toBe(0);
  });
});
