import { describe, expect, it } from 'vitest';
import { actualDurationMs, inspectApproval, inspectFailedSelection, inspectScope, inspectScopeInput, inspectStructureSnapshot, inspectSubmission, usableApproval } from './production-guards';
import type { ProductionStatus, ScopeApproval, ScopeInput, ScopeQuote } from './production-types';

const now = 1_800_000_000_000;
const input: ScopeInput = { mode: 'full', shot_ids: ['s1', 's2'], specs: { model_id: 'video-model', ratio: '9:16', resolution: '720p' } };
function quote(): ScopeQuote {
  return { scope_id: 'scope1', mode: 'full', shots: [{ shot_id: 's1', duration_s: 5, estimate_cny: 0.3 }, { shot_id: 's2', duration_s: 3, estimate_cny: 0.2 }], total_estimate_cny: 0.5, planned_film_duration_s: 8, scope_hash: 'sha256:' + 'a'.repeat(64), expires_at: now / 1000 + 600, side_effects: { tasks_created: 0, model_called: false } };
}
const approval: ScopeApproval = { scope_id: 'scope1', approved: true, expires_in_s: 600, max_uses: 1 };

describe('scope quote safety', () => {
  it('accepts complete server values without substituting price or duration', () => {
    const value = quote(); expect(inspectScope(value, input, now)).toEqual({ ok: true, reason: null });
    expect(value.total_estimate_cny).toBe(0.5);
  });
  it.each([
    ['mode coercion', { mode: ['full'] }], ['missing identity', { scope_id: '' }], ['short hash', { scope_hash: 'sha256:abc' }],
    ['expiry equality', { expires_at: now / 1000 }], ['infinite expiry', { expires_at: Infinity }], ['overflow expiry', { expires_at: Number.MAX_VALUE }],
    ['missing side effects', { side_effects: undefined }], ['tasks already made', { side_effects: { tasks_created: 1, model_called: false } }],
    ['provider called', { side_effects: { tasks_created: 0, model_called: true } }], ['not submittable', { submittable: false }],
    ['blocked reasons', { blocked_reasons: ['MODEL_NOT_OPENED'] }], ['invalid submittability', { submittable: 'true' }],
    ['negative total', { total_estimate_cny: -1 }], ['NaN total', { total_estimate_cny: NaN }],
    ['total mismatch', { total_estimate_cny: 0.6 }], ['planned duration mismatch', { planned_film_duration_s: 9 }],
    ['no shots', { shots: [] }], ['missing price', { shots: [{ shot_id: 's1', duration_s: 5 }] }],
  ])('blocks %s', (_, patch) => { expect(inspectScope({ ...quote(), ...patch }, undefined, now).ok).toBe(false); });
  it('requires exact selected order and quantity, including duration overrides', () => {
    expect(inspectScope(quote(), { ...input, shot_ids: ['s2', 's1'] }, now).ok).toBe(false);
    expect(inspectScope(quote(), { ...input, shot_ids: ['s1'] }, now).ok).toBe(false);
    expect(inspectScope(quote(), { ...input, mode: 'clips' }, now).ok).toBe(false);
    expect(inspectScope(quote(), { ...input, overrides: { s1: { duration_s: 6 } } }, now).ok).toBe(false);
  });
  it('rejects duplicate shot identities even if amounts sum correctly', () => {
    const q = quote(); q.shots[1].shot_id = 's1'; expect(inspectScope(q, undefined, now).ok).toBe(false);
  });
  it('does not coerce duration, money, or modes', () => {
    for (const value of [null, 5.5, '5', true, 0, -1]) {
      const q = quote(); (q.shots[0] as unknown as Record<string, unknown>).duration_s = value;
      expect(inspectScope(q, undefined, now).ok).toBe(false);
    }
    expect(inspectScopeInput({ ...input, mode: ['full'] }).ok).toBe(false);
    expect(inspectScopeInput({ ...input, overrides: { s3: { duration_s: 4 } } }).ok).toBe(false);
    expect(inspectScopeInput({ ...input, shot_ids: ['s1', 's1'] }).ok).toBe(false);
  });
});

describe('approval and task mapping', () => {
  it('requires a receipt bound to this scope and a known confirmation clock', () => {
    expect(usableApproval(quote(), approval, now)).toBe(false);
    expect(usableApproval(quote(), approval, now, now)).toBe(true);
    expect(usableApproval(quote(), { ...approval, expires_at: now / 1000 + 60 }, now)).toBe(true);
    expect(inspectApproval(quote(), { ...approval, scope_id: 'other' }, now, now).ok).toBe(false);
    expect(inspectApproval(quote(), { ...approval, scope_hash: 'sha256:' + 'b'.repeat(64) }, now, now).ok).toBe(false);
    expect(inspectApproval(quote(), { ...approval, approved: false }, now, now).ok).toBe(false);
    expect(inspectApproval(quote(), { ...approval, expires_in_s: 1 }, now, now - 1000).ok).toBe(false);
    expect(inspectApproval(quote(), { ...approval, max_uses: 2 }, now, now).ok).toBe(false);
  });
  it('requires one unique task for every confirmed shot in order', () => {
    const result = { batch_id: 'b1', mode: 'full', auto_film: true, sequential: true, tasks: [{ shot_id: 's1', task_id: 't1' }, { shot_id: 's2', task_id: 't2' }] };
    expect(inspectSubmission(result, quote()).ok).toBe(true);
    expect(inspectSubmission({ ...result, tasks: result.tasks.slice(0, 1) }, quote()).ok).toBe(false);
    expect(inspectSubmission({ ...result, tasks: [...result.tasks].reverse() }, quote()).ok).toBe(false);
    expect(inspectSubmission({ ...result, tasks: [{ shot_id: 's1', task_id: 't1' }, { shot_id: 's2', task_id: 't1' }] }, quote()).ok).toBe(false);
    expect(inspectSubmission({ ...result, sequential: undefined }, quote()).ok).toBe(false);
    expect(inspectSubmission({ ...result, auto_film: false }, quote()).ok).toBe(false);
  });
});

describe('failure selection, measurements and template boundaries', () => {
  const status: ProductionStatus = { project_id: 'p1', mode: 'clips', shots: [{ shot_id: 's1', status: 'failed' }, { shot_id: 's2', status: 'succeeded' }, { shot_id: 's3', status: 'failed' }], batch: null, film: null };
  it('only accepts explicitly selected failed shots; never adds all failures', () => {
    expect(inspectFailedSelection(status, ['s3']).ok).toBe(true);
    for (const ids of [[], ['s2'], ['s4'], ['s1', 's1']]) expect(inspectFailedSelection(status, ids).ok).toBe(false);
    expect(status.shots).toHaveLength(3);
  });
  it('does not substitute planned duration when measurement is absent', () => {
    expect(actualDurationMs({})).toBeNull(); expect(actualDurationMs({ actual_duration_ms: null })).toBeNull();
    expect(actualDurationMs({ actual_duration_ms: '5000' })).toBeNull();
    expect(actualDurationMs({ actual_duration_ms: 5042 })).toBe(5042);
  });
  it('saves only explicit structure fields, not product/reference/prompt fields', () => {
    const snapshot = [{ seq: 1, role: '开场', duration_s: 4, motion: '推近', rhythm: '快' }];
    expect(inspectStructureSnapshot(snapshot).ok).toBe(true);
    for (const field of ['description', 'product_name', 'input_asset_id', 'prompt']) expect(inspectStructureSnapshot([{ ...snapshot[0], [field]: 'private product fact' }]).ok).toBe(false);
    expect(inspectStructureSnapshot([{ ...snapshot[0], seq: 2 }]).ok).toBe(false);
    expect(inspectStructureSnapshot([{ ...snapshot[0], duration_s: 4.5 }]).ok).toBe(false);
  });
});
