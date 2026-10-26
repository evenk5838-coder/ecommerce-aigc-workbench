import { describe, expect, it, vi } from 'vitest';
import backend from './test-fixtures/backend-s3s6-20261026.json';
import { createProductionApi } from './production-api';
import { inspectScope, inspectSubmission } from './production-guards';

// Copied from backend docs/接口/样例-S3-S6-真实响应.json, isolated DB/stub run.
// This verifies actual response shapes only; it does not claim model quality or live service validation.
const response = (body: unknown) => new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } });
const id = backend.director.project_id;
function fixture(...values: unknown[]) { const fetcher = vi.fn<typeof fetch>(); values.forEach(value => fetcher.mockResolvedValueOnce(response(value))); return { api: createProductionApi(fetcher), fetcher }; }

describe('backend S3-S6 documented wire evidence', () => {
  it('restores latest storyboard and nullable motion from actual shot-list data', async () => {
    const { api } = fixture({ items: backend.shot_list });
    const shots = (await api.listShots(id)).items;
    expect(shots[0].motion).toBeNull();
    expect(shots[0].storyboards).toEqual([{ ...backend.shot_list[0].storyboard, shot_id: shots[0].shot_id }]);
    expect(shots[0].storyboards).toHaveLength(1); // Only latest, not invented version history.
  });
  it('reads nested take data and count without treating count as history or completed status', async () => {
    const { api } = fixture(backend.project_status);
    const status = await api.getStatus(id);
    expect(status.mode).toBe('done');
    expect(status.shots[0]).toMatchObject({ status: 'succeeded', actual_duration_ms: 2000, take_count: 1 });
    expect(status.shots[0].takes).toEqual([backend.project_status.shots[0].take]);
    expect(status.films?.[0].actual_duration_ms).toBe(4000);
    expect(status.batch).toBeUndefined();
  });
  it('keeps latest failed status despite earlier takes existing', async () => {
    const data = structuredClone(backend.project_status);
    data.shots[0].take.status = 'failed'; data.shots[0].takes = 8;
    const { api } = fixture(data);
    const row = (await api.getStatus(id)).shots[0];
    expect(row.status).toBe('failed'); expect(row.takes).toHaveLength(1); expect(row.take_count).toBe(8);
  });
  it('rejects corrupted latest take instead of using the count', async () => {
    const data = structuredClone(backend.project_status);
    const { api } = fixture({ ...data, shots: [{ ...data.shots[0], take: { status:'succeeded' } }] });
    await expect(api.getStatus(id)).rejects.toMatchObject({code:'PRODUCTION_RESPONSE_INVALID'});
  });
  it('uses measured film duration and preserves explicit unknown duration', async () => {
    const { api } = fixture(backend.film, { ...backend.film, duration_ms: null, planned_duration_s: 20 });
    expect((await api.getFilm(backend.film.film_id)).actual_duration_ms).toBe(4000);
    expect((await api.getFilm(backend.film.film_id)).actual_duration_ms).toBeNull();
  });
  it('approves via review route with server confirmation version', async () => {
    const { api, fetcher } = fixture(backend.storyboard_review);
    const review = await api.approveStoryboard(backend.storyboard_review.storyboard_id);
    expect(review.version).toBe(backend.storyboard_review.confirmation.object_version);
    expect(fetcher.mock.calls[0][0]).toBe(`/api/storyboards/${backend.storyboard_review.storyboard_id}/review`);
    expect(JSON.parse(String(fetcher.mock.calls[0][1]?.body))).toEqual({approve:true});
  });
  it('accepts nullable optional template text without adding any product facts', async () => {
    const { api } = fixture(backend.structure_template);
    const template = await api.getStructureTemplate(backend.structure_template.template_id, 1);
    expect(template.snapshot[0]).toMatchObject({role:'镜1', motion:'', rhythm:''});
    expect(Object.keys(template.snapshot[0])).toEqual(['seq','role','duration_s','motion','rhythm']);
  });
  it('returns export download URL and missing details without inventing an asset', async () => {
    const missing = [{kind:'film',film_id:'flm_missing',reason:'file_not_found'}];
    const { api, fetcher } = fixture({...backend.export, missing});
    const result = await api.exportProject(id, {include:['film','clips','storyboards','manifest']});
    expect(result.download_url).toBe(backend.export.download_url);
    expect(result.missing).toEqual(missing);
    expect(result).not.toHaveProperty('asset_id'); expect(result).not.toHaveProperty('status');
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('rejects wrong-project or external download receipts', async () => {
    const { api } = fixture({...backend.export,project_id:'other'}, {...backend.export,download_url:'https://external.example/export.zip'});
    const input = {include:['film','clips','storyboards','manifest'] as Array<'film'|'clips'|'storyboards'|'manifest'>};
    await expect(api.exportProject(id,input)).rejects.toMatchObject({code:'CREATIVE_RESULT_UNKNOWN'});
    await expect(api.exportProject(id,input)).rejects.toMatchObject({code:'CREATIVE_RESULT_UNKNOWN'});
  });
  it('accepts backend four-decimal quote rounding and explicit idempotent replay without auto_film', () => {
    const quote = {scope_id:'scp1',mode:'full' as const, shots:[{shot_id:'s1',duration_s:5,estimate_cny:0.123456},{shot_id:'s2',duration_s:5,estimate_cny:0.123456}],total_estimate_cny:0.2469,planned_film_duration_s:10,scope_hash:'sha256:'+'a'.repeat(64),expires_at:Date.now()/1000+60,side_effects:{tasks_created:0 as const,model_called:false as const}};
    expect(inspectScope(quote).ok).toBe(true);
    const replay = {batch_id:'b1',mode:'full',sequential:true,idempotent_replay:true,tasks:[{shot_id:'s1',task_id:'t1'},{shot_id:'s2',task_id:'t2'}]};
    expect(inspectSubmission(replay,quote).ok).toBe(true);
    expect(inspectSubmission({...replay,idempotent_replay:false},quote).ok).toBe(false);
  });
});
