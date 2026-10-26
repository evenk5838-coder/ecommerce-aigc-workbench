import { afterEach, describe, expect, it, vi } from 'vitest';
import { createProductionApi, filmFileUrl } from './production-api';
import type { ScopeInput, ScopeQuote } from './production-types';
const input: ScopeInput = { mode: 'clips', shot_ids: ['s1', 's2'], specs: { model_id: 'video', ratio: '9:16', resolution: '720p' } };
const quote = (): ScopeQuote => ({ scope_id: 'scp1', mode: 'clips', shots: [{ shot_id: 's1', duration_s: 5, estimate_cny: 0.3 }, { shot_id: 's2', duration_s: 3, estimate_cny: 0.2 }], total_estimate_cny: 0.5, planned_film_duration_s: 8, scope_hash: 'sha256:' + 'a'.repeat(64), expires_at: Date.now() / 1000 + 600, side_effects: { tasks_created: 0, model_called: false } });
const approval = () => ({ scope_id: 'scp1', approved: true, expires_in_s: 600, max_uses: 1 });
const submission = () => ({ batch_id: 'b1', mode: 'clips', tasks: [{ shot_id: 's1', task_id: 't1' }, { shot_id: 's2', task_id: 't2' }], auto_film: false, sequential: true });
const shot = (patch: Record<string, unknown> = {}) => ({ shot_id: 's1', project_id: 'p1', revision: 1, seq: 1, duration_s: 5, description: '细节', ...patch });
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
function fixture(...responses: Response[]) { const fetcher = vi.fn<typeof fetch>(); responses.forEach(item => fetcher.mockResolvedValueOnce(item)); return { fetcher, api: createProductionApi(fetcher) }; }
const body = (call: Parameters<typeof fetch>) => JSON.parse(String(call[1]?.body)) as unknown;
afterEach(() => vi.useRealTimers());

describe('scope action boundaries', () => {
  it('makes only one request per action and finally sends only scope_id', async () => {
    const { api, fetcher } = fixture(response(quote()), response(approval()), response(submission()));
    const q = await api.createScope('p1', input); expect(fetcher).toHaveBeenCalledTimes(1); expect(body(fetcher.mock.calls[0])).toEqual(input);
    const a = await api.confirmScope(q); expect(fetcher).toHaveBeenCalledTimes(2); expect(body(fetcher.mock.calls[1])).toEqual({ confirm: true });
    await api.submitScope('p1', q, a); expect(fetcher).toHaveBeenCalledTimes(3); expect(body(fetcher.mock.calls[2])).toEqual({ scope_id: 'scp1' });
    expect(fetcher.mock.calls.map(call => call[0])).toEqual(['/api/projects/p1/scope', '/api/scopes/scp1/confirm', '/api/projects/p1/generate']);
  });
  it('blocks copied quotes, fabricated approvals and another project without sending', async () => {
    const { api, fetcher } = fixture(response(quote()), response(approval())); const q = await api.createScope('p1', input);
    await expect(api.confirmScope({ ...q })).rejects.toMatchObject({ code: 'PRODUCTION_INPUT_INVALID' });
    expect(() => api.submitScope('p1', q, approval() as never)).toThrow('真实确认回执');
    const a = await api.confirmScope(q); expect(() => api.submitScope('p2', q, a)).toThrow('不属于当前项目'); expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it('blocks edits to quote or approval after they were shown', async () => {
    const { api, fetcher } = fixture(response(quote()), response(approval())); const q = await api.createScope('p1', input), a = await api.confirmScope(q);
    a.expires_in_s = 9999; expect(() => api.submitScope('p1', q, a)).toThrow('真实确认回执');
    q.shots[0].duration_s = 6; await expect(api.confirmScope(q)).rejects.toMatchObject({ code: 'PRODUCTION_INPUT_INVALID' }); expect(fetcher).toHaveBeenCalledTimes(2);
  });
  it('freezes the request before a caller can mutate it', async () => {
    const snapshot = structuredClone(input), { api, fetcher } = fixture(response(quote()), response(approval()));
    const pending = api.createScope('p1', snapshot); snapshot.shot_ids[0] = 'other'; const q = await pending; await api.confirmScope(q);
    expect((body(fetcher.mock.calls[0]) as ScopeInput).shot_ids).toEqual(['s1', 's2']);
  });
  it('rejects wrong quote mapping and an incomplete submission acknowledgement', async () => {
    const wrong = quote(); wrong.shots.reverse(); const first = fixture(response(wrong));
    await expect(first.api.createScope('p1', input)).rejects.toMatchObject({ code: 'CREATIVE_RESULT_UNKNOWN', detail: { reason: 'response_invalid' } });
    const second = fixture(response(quote()), response(approval()), response({ ...submission(), tasks: [{ shot_id: 's1', task_id: 't1' }] }));
    const q = await second.api.createScope('p1', input), a = await second.api.confirmScope(q);
    await expect(second.api.submitScope('p1', q, a)).rejects.toMatchObject({ code: 'CREATIVE_RESULT_UNKNOWN' }); expect(second.fetcher).toHaveBeenCalledTimes(3);
  });
  it('checks expiry again immediately before confirmation/submission', async () => {
    vi.useFakeTimers(); vi.setSystemTime(new Date('2026-10-26T00:00:00Z'));
    const { api, fetcher } = fixture(response(quote()), response(approval())); const q = await api.createScope('p1', input), a = await api.confirmScope(q);
    vi.setSystemTime(Date.now() + 600_001); await expect(api.confirmScope(q)).rejects.toMatchObject({ code: 'PRODUCTION_INPUT_INVALID' });
    expect(() => api.submitScope('p1', q, a)).toThrow('过期'); expect(fetcher).toHaveBeenCalledTimes(2);
  });
});

describe('transport does not invent success or repeat writes', () => {
  it.each([404, 405, 501])('reports an unimplemented %s as unavailable', async status => {
    const { api, fetcher } = fixture(response({ detail: 'Not Found' }, status));
    await expect(api.listShots('p1')).rejects.toMatchObject({ code: 'PRODUCTION_UNAVAILABLE', status }); expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('preserves real resource and concurrency error codes', async () => {
    const { api } = fixture(response({ error: { code: 'SHOT_NOT_FOUND', message: '镜头不存在' } }, 404), response({ error: { code: 'SHOT_REVISION_CONFLICT', message: '版本更新', detail: { current_revision: 3 } } }, 409));
    await expect(api.updateShot('missing', { expected_revision: 1, description: 'x' })).rejects.toMatchObject({ code: 'SHOT_NOT_FOUND' });
    await expect(api.updateShot('s1', { expected_revision: 1, description: 'x' })).rejects.toMatchObject({ code: 'SHOT_REVISION_CONFLICT', detail: { current_revision: 3 } });
  });
  it('keeps 5xx writes unknown and never retries', async () => {
    const { api, fetcher } = fixture(response({ error: { code: 'ASSEMBLY_FAILED', message: '片段损坏' } }, 502));
    await expect(api.createFilm('p1', { take_ids: ['t1'], confirm_partial: false })).rejects.toMatchObject({ code: 'CREATIVE_RESULT_UNKNOWN', detail: { original: { code: 'ASSEMBLY_FAILED' } } }); expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('bounds even a stalled response body and does not resend', async () => {
    vi.useFakeTimers(); const fetcher = vi.fn<typeof fetch>().mockResolvedValue({ ok: true, status: 200, json: () => new Promise(() => {}) } as unknown as Response);
    const pending = createProductionApi(fetcher).deleteShot('s1', { timeoutMs: 10 });
    const checked = expect(pending).rejects.toMatchObject({ code: 'CREATIVE_RESULT_UNKNOWN', detail: { reason: 'timeout' } });
    await vi.advanceTimersByTimeAsync(11); await checked; expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('sends nothing when aborted before invocation', async () => {
    const { api, fetcher } = fixture(), controller = new AbortController(); controller.abort();
    await expect(api.listShots('p1', { signal: controller.signal })).rejects.toMatchObject({ code: 'CREATIVE_REQUEST_ABORTED' }); expect(fetcher).not.toHaveBeenCalled();
  });
});

describe('shots and storyboards', () => {
  it('requires a real revision and preserves absent history', async () => {
    const { api, fetcher } = fixture(response({ items: [shot()] }), response(shot({ revision: 2, description: '新描述' })));
    expect((await api.listShots('p1')).items[0].storyboards).toBeUndefined();
    expect(() => api.updateShot('s1', { expected_revision: 0, description: 'x' })).toThrow('当前版本');
    expect((await api.updateShot('s1', { expected_revision: 1, description: '新描述' })).revision).toBe(2);
    expect(body(fetcher.mock.calls[1])).toEqual({ expected_revision: 1, description: '新描述' });
  });
  it('blocks cross-project lists and missing revisions', async () => {
    const { api } = fixture(response({ items: [shot({ project_id: 'p2' })] }), response({ items: [shot({ revision: undefined })] }));
    await expect(api.listShots('p1')).rejects.toMatchObject({ code: 'PRODUCTION_RESPONSE_INVALID' }); await expect(api.listShots('p1')).rejects.toMatchObject({ code: 'PRODUCTION_RESPONSE_INVALID' });
  });
  it('keeps CRUD explicit and does not infer missing acknowledgements', async () => {
    const { api, fetcher } = fixture(response({ project_id: 'p1', shot_ids: ['s1'], count: 1 }), response({ project_id: 'p1', order: ['s1'] }), response({ project_id: 'p1', deleted: 's1' }));
    await api.createShots('p1', { script_id: 'scr1', shots: [{ seq: 1, duration_s: 5, description: '细节', model_id:'video', prompt:'browser only', input_asset_id:'browser-ref' }] }); expect(body(fetcher.mock.calls[0])).toEqual({script_id:'scr1', model_id:'video', shots:[{seq:1,duration_s:5,description:'细节'}]}); await api.reorderShots('p1', { order: ['s1'] }); await api.deleteShot('s1');
    expect(fetcher).toHaveBeenCalledTimes(3); expect(() => api.reorderShots('p1', { order: ['s1', 's1'] })).toThrow('不重复');
  });
  it('does not approve or create another storyboard automatically', async () => {
    const { api, fetcher } = fixture(response({ shot_id: 's1', storyboard_id: 'sb1', version: 1, task_id: 't1', estimated_cost_cny: 0.12 }), response({ storyboard_id: 'sb1', status: 'approved', confirmation: {confirmation_id: 'c1', status: 'active', object_type: 'storyboard', object_id: 'sb1', object_version: 1, reused: false} }), response({ storyboard_id: 'sb1', status: 'rejected', reason: '瓶身比例不对' }));
    await api.createStoryboard('s1', { model_id: 'image', size: '2048x2048', prompt: '商品细节', input_asset_id: 'a1' }); expect(fetcher).toHaveBeenCalledTimes(1);
    await api.approveStoryboard('sb1'); expect(fetcher).toHaveBeenCalledTimes(2);
    expect(() => api.rejectStoryboard('sb1', '')).toThrow('拒绝原因'); await api.rejectStoryboard('sb1', '瓶身比例不对'); expect(body(fetcher.mock.calls[0])).toMatchObject({ confirm: true }); expect(fetcher.mock.calls[1][0]).toBe('/api/storyboards/sb1/review'); expect(body(fetcher.mock.calls[1])).toEqual({ approve: true }); expect(body(fetcher.mock.calls[2])).toEqual({ approve: false, reason: '瓶身比例不对' });
  });
});

describe('actual outputs and reusable structure', () => {
  it('never fills missing actual durations or film history from plans', async () => {
    const { api } = fixture(response({ project_id: 'p1', mode: 'done', current_stage: 'delivery', allowed_actions: [], warnings: [], shots: [{ shot_id: 's1', seq: 1, duration_s: 5, description: '细节', storyboard: null, take: {take_id:'t1', status: 'succeeded', asset_id: 'a1'}, takes: 1 }], films: [] }), response({ film_id: 'f1', status: 'ready', planned_duration_s: 5, asset_id: 'a2' }));
    const status = await api.getStatus('p1'); expect(status.shots[0].actual_duration_ms).toBeUndefined(); expect(status.films).toEqual([]); expect(status.batch).toBeUndefined();
    const film = await api.getFilm('f1'); expect(film.actual_duration_ms).toBeUndefined(); expect(film.planned_duration_s).toBe(5);
  });
  it('reads film history; obtaining a file URL does not send a request', async () => {
    const { api, fetcher } = fixture(response({ items: [{ film_id: 'f1', project_id: 'p1', status: 'ready', actual_duration_ms: 5090 }] }));
    expect((await api.listFilms('p1')).items[0].actual_duration_ms).toBe(5090); expect(filmFileUrl({ asset_id: 'asset a' })).toBe('/api/assets/asset%20a/file'); expect(api.filmFileUrl({ asset_id: 'a1' })).toBe('/api/assets/a1/file'); expect(() => filmFileUrl({asset_id:null})).toThrow('尚无可下载'); expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('does not silently allow partial composition', async () => {
    const { api, fetcher } = fixture(response({ film_id: 'f1', version: 1, status: 'composing', partial: true }));
    await expect(api.createFilm('p1', { take_ids: ['t1'], confirm_partial: false })).rejects.toMatchObject({ code: 'CREATIVE_RESULT_UNKNOWN' }); expect(body(fetcher.mock.calls[0])).toEqual({ take_ids: ['t1'], confirm_partial: false });
  });
  it('uses explicit template versions and excludes product fields', async () => {
    const snapshot = [{ seq: 1, role: '开场', duration_s: 4, motion: '推近', rhythm: '快' }];
    const { api, fetcher } = fixture(response({ items: [{ template_id: 'st1', name: '快节奏', latest_version: 2 }] }), response({ template_id: 'st1', name: '快节奏', version: 2, snapshot }), response({ template_id: 'st1', version: 3 }), response({ project_id: 'p1', template_id: 'st1', template_version: 3, created_shots: [{shot_id:'s1',seq:1,duration_s:4,role:'开场'}], requires: ['brief', 'assets'], model_revalidation: { ok: true, note: '已校验' } }));
    await api.listStructureTemplates(); await api.getStructureTemplate('st1', 2); await api.createStructureTemplate({ name: '新版', snapshot });
    expect((await api.applyStructure('p1', { template_id: 'st1', version: 3 })).requires).toEqual(['brief', 'assets']);
    expect(fetcher.mock.calls[1][0]).toBe('/api/structure-templates/st1?version=2'); expect(body(fetcher.mock.calls[3])).toEqual({ template_id: 'st1', version: 3 });
    expect(() => api.createStructureTemplate({ name: 'bad', snapshot: [{ ...snapshot[0], prompt: '商品事实' }] as never })).toThrow('不保存商品');
  });
  it('exports selected outputs without regenerating', async () => {
    const { api, fetcher } = fixture(response({ export_id: 'e1', project_id: 'p1', download_url: '/api/exports/e1/file', file_name: 'project.zip', bytes: 100, includes: ['clips','manifest'], films_included: 0, clips_included: 1, storyboards_included: 0, missing: [] }));
    expect((await api.exportProject('p1', { include: ['clips', 'manifest'] })).file_name).toBe('project.zip'); expect(fetcher).toHaveBeenCalledTimes(1); expect(fetcher.mock.calls[0][0]).toBe('/api/projects/p1/export'); expect(body(fetcher.mock.calls[0])).toEqual({ include: ['clips', 'manifest'] });
  });
});
