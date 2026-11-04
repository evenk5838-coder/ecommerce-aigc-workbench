import { describe, expect, it, vi } from 'vitest';
import type { Model, Task } from '../types';
import type { ProductionStatus, ScopeInput, Shot } from './production-types';
import { draftFromShot, failedShotIds, hasActiveProduction, inspectShotDraft, inspectStoryboardDraft, initialProductionSelection, parseProductionEditor, planOrderChanged, productionFingerprint, restoreShotDraft, savedShotPlan, shotDraftChanged, shotPlanChanged, storyboardSettings, submitAfterScopeConfirmation, successfulTakes, unionFilms } from './production-state';
const model: Model = { id:'video', name:'video', kind:'video', provider:'ark', downloaded:true, loaded:false, weights_mb:0, submittability:{submittable:true,reasons:[]}, capabilities:{duration_policy:{kind:'range',min:2,max:12,step:1}} };
const shot: Shot = {shot_id:'s1',project_id:'p',revision:2,seq:1,duration_s:6,description:'保留产品',input_asset_id:'a1'};
const status: ProductionStatus = {project_id:'p',mode:'clips',shots:[],batch:null,film:null};
const input: ScopeInput={mode:'clips',shot_ids:['s1'],specs:{model_id:'video',ratio:'9:16',resolution:'720p'}};
describe('production UI state safety',()=>{
  it('passes exact native duration and reference unchanged',()=>{ const checked=inspectShotDraft(draftFromShot(shot),model,1);expect(checked.error).toBeNull();expect(checked.input?.duration_s).toBe(6);expect(checked.input?.input_asset_id).toBe('a1'); });
  it.each(['5.5','13','1','-1','NaN',''])('rejects unsupported duration %s without normalizing',duration=>{const draft={...draftFromShot(shot),duration};expect(inspectShotDraft(draft,model,1).input).toBeNull();expect(draft.duration).toBe(duration);});
  it('does not grant model permission from downloaded flags',()=>{expect(inspectShotDraft(draftFromShot(shot),{...model,submittability:undefined},1).input).toBeNull();});
  it('finds changed inputs and order independently',()=>{expect(shotDraftChanged(draftFromShot(shot),shot)).toBe(false);expect(shotDraftChanged({...draftFromShot(shot),prompt:'new'},shot)).toBe(false);expect(shotDraftChanged({...draftFromShot(shot),description:'new'},shot)).toBe(true);expect(planOrderChanged([draftFromShot(shot)],[shot])).toBe(false);expect(planOrderChanged([draftFromShot({...shot,shot_id:'s2'})],[shot])).toBe(true);});
  it('preserves browser-only board inputs when refreshing saved shot data',()=>{const local={...draftFromShot(shot),prompt:'new prompt',input_asset_id:'local-ref'};expect(restoreShotDraft({...shot,revision:3,description:'最新'},local)).toMatchObject({revision:3,description:'最新',prompt:'new prompt',input_asset_id:'local-ref'});});
  it('only selects explicitly failed shots for retry',()=>{expect(failedShotIds({...status,shots:[{shot_id:'a',status:'succeeded'},{shot_id:'b',status:'failed'},{shot_id:'c',status:'running'}]})).toEqual(['b']);});
  it('keeps successful prior takes when the latest attempt failed',()=>{const item={...shot,takes:[{take_id:'old',asset_id:'asset',status:'succeeded'},{take_id:'bad',status:'failed'}]};expect(successfulTakes(item,{...status,shots:[{shot_id:'s1',status:'failed'}]}).map(t=>t.take_id)).toEqual(['old']);});
  it('cannot use missing assets or planned duration as successful media',()=>{expect(successfulTakes({...shot,takes:[{take_id:'empty',status:'succeeded'}]},status)).toEqual([]);});
  it('invalidates fingerprints for prompt/ref/model/order and script/brief changes',()=>{const rows=[draftFromShot(shot)],base=productionFingerprint('p','scr',{},rows,[shot],input);for(const row of [{...rows[0],prompt:'new'},{...rows[0],input_asset_id:'other'},{...rows[0],duration:'7'}])expect(productionFingerprint('p','scr',{},[row],[shot],input)).not.toBe(base);expect(productionFingerprint('p','scr2',{},rows,[shot],input)).not.toBe(base);expect(productionFingerprint('p','scr',{subject:'new'},rows,[shot],input)).not.toBe(base);expect(productionFingerprint('p','scr',{},rows,[shot],{...input,specs:{...input.specs,model_id:'changed'}})).not.toBe(base);});
  it('only active production or explicit task receipts require polling',()=>{expect(hasActiveProduction(status,[],[])).toBe(false);expect(hasActiveProduction(status,[],['new'])).toBe(true);expect(hasActiveProduction(status,[{id:'new',status:'failed'} as Task],['new'])).toBe(false);expect(hasActiveProduction({...status,batch:{batch_id:'b',queued:1,running:0,succeeded:0,failed:0}},[],[])).toBe(true);});
  it('does not invent film history from missing server fields',()=>{expect(unionFilms([],status)).toEqual([]);expect(unionFilms([],{...status,film:{film_id:'f',status:'ready'}})).toEqual([{film_id:'f',status:'ready'}]);});
  it('restores only valid local input without any approval object',()=>{const draft={version:1,rows:[draftFromShot(shot)],videoModelId:'video',ratio:'9:16',resolution:'720p',imageModelId:'img',imageSize:'2048x2048',mode:'clips',selectedIds:['s1']};expect(parseProductionEditor(JSON.stringify(draft))).toEqual(draft);expect(parseProductionEditor('{"version":1,"rows":[]}')).toBeNull();});
});

describe('saved shot plan baseline during initial reads', () => {
  const second: Shot = { ...shot, shot_id: 's2', seq: 2, description: '细节镜头', motion: '缓慢推进' };
  const shots = [shot, second];
  const cache = () => ({ version: 1, rows: shots.map(draftFromShot), savedPlan: savedShotPlan(shots),
    videoModelId: 'video', ratio: '16:9', resolution: '720p', imageModelId: 'image', imageSize: '2K',
    mode: 'clips', selectedIds: ['s1', 's2'] });

  it('restores a clean saved plan before the first shot GET has returned', () => {
    const restored = parseProductionEditor(JSON.stringify(cache()))!;
    expect(restored.savedPlan).toEqual(savedShotPlan(shots));
    expect(shotPlanChanged(restored.rows, restored.savedPlan)).toBe(false);
    // Comparing these same restored rows with an unloaded [] was the false warning.
    expect(planOrderChanged(restored.rows, [])).toBe(true);
  });

  it.each([
    { description: '尚未保存的画面修改' }, { motion: '尚未保存的运镜' }, { duration: '7' },
  ])('protects local edits during loading or a failed initial read: %j', patch => {
    const value = cache(); value.rows[0] = { ...value.rows[0], ...patch };
    const restored = parseProductionEditor(JSON.stringify(value))!;
    expect(shotPlanChanged(restored.rows, restored.savedPlan)).toBe(true);
    expect(restored.rows[0]).toMatchObject(patch);
    expect(restored.savedPlan?.[0]).toEqual(savedShotPlan(shots)[0]);
  });

  it('protects unsaved order, removal and newly added rows against the saved plan', () => {
    const value = cache();
    for (const rows of [[...value.rows].reverse(), value.rows.slice(1), [...value.rows, { ...draftFromShot(shot), key: 'new-row', shot_id: undefined }]]) {
      expect(shotPlanChanged(rows, value.savedPlan)).toBe(true);
    }
    expect(shotPlanChanged([], value.savedPlan)).toBe(true);
  });

  it('keeps legacy nonempty drafts conservative until a real saved plan is read', () => {
    const value = cache(), legacy = { ...value, savedPlan: undefined };
    const restored = parseProductionEditor(JSON.stringify(legacy))!;
    expect(restored.savedPlan).toBeUndefined();
    expect(restored.rows).toEqual(value.rows);
    expect(shotPlanChanged(restored.rows, restored.savedPlan)).toBe(true);
    expect(shotPlanChanged(restored.rows, savedShotPlan(shots))).toBe(false);
    expect(shotPlanChanged([], undefined)).toBe(false);
  });

  it.each([
    { savedPlan: [{ shot_id: 's1', seq: 1, description: 'broken', duration_s: 6 }] },
    { savedPlan: [savedShotPlan(shots)[0], savedShotPlan(shots)[0]] },
    { savedPlan: [{ ...savedShotPlan(shots)[0], duration_s: 0 }] },
  ])('drops an invalid baseline without discarding the recoverable draft: %j', ({ savedPlan }) => {
    const value = cache(), restored = parseProductionEditor(JSON.stringify({ ...value, savedPlan }))!;
    expect(restored.rows).toEqual(value.rows);
    expect(restored.savedPlan).toBeUndefined();
    expect(shotPlanChanged(restored.rows, restored.savedPlan)).toBe(true);
  });

  it('stores only acknowledged plan fields, excluding prompts, references and result data', () => {
    const baseline = savedShotPlan([{ ...shot, prompt: '生成提示词', input_asset_id: 'ref', takes: [{ take_id: 'take', status: 'succeeded' }] }]);
    expect(baseline).toEqual([{ shot_id: 's1', seq: 1, description: '保留产品', motion: '', duration_s: 6 }]);
    const restored = parseProductionEditor(JSON.stringify({ ...cache(), savedPlan: [{ ...baseline[0], approval: true, input_asset_id: 'ref' }] }))!;
    expect(restored.savedPlan).toEqual(baseline);
  });

  it('compares new server results without overwriting a different local draft', () => {
    const value = cache(), originalRows = structuredClone(value.rows);
    const nextSaved = savedShotPlan([{ ...shot, description: '另一处更新' }, second]);
    expect(shotPlanChanged(value.rows, nextSaved)).toBe(true);
    expect(value.rows).toEqual(originalRows);
  });

  it('recognizes acknowledged save, deletion and reorder baselines as clean', () => {
    const saved = [{ ...second, seq: 1, description: '已保存的新内容' }];
    expect(shotPlanChanged(saved.map(draftFromShot), savedShotPlan(saved))).toBe(false);
    const restored = parseProductionEditor(JSON.stringify({ ...cache(), rows: [], savedPlan: [] }))!;
    expect(restored.savedPlan).toEqual([]);
    expect(shotPlanChanged(restored.rows, restored.savedPlan)).toBe(false);
  });
});


describe('scope confirmation races', () => {
  const quote = { scope_id: 'frozen' };
  const frozen = { quote, fingerprint: 'inputs-and-selection', retryOnly: true };
  const current = () => ({ currentQuote: quote as object | null, fingerprint: frozen.fingerprint, blocked: false, retryOnly: true, selectedStillFailed: true, mounted: true, aborted: false });
  it.each([
    { currentQuote: null },
    { fingerprint: 'changed-selection-or-input' },
    { blocked: true },
    { retryOnly: false },
    { selectedStillFailed: false },
    { mounted: false },
    { aborted: true },
  ])('does not submit when state changes while confirmation waits: %j', async change => {
    let finish!: (approval: object) => void;
    const confirmation = new Promise<object>(resolve => { finish = resolve; });
    let live = current();
    const submit = vi.fn(async () => 'unexpected');
    const result = submitAfterScopeConfirmation(frozen, () => confirmation, () => live, submit);
    live = { ...live, ...change };
    finish({ approval_id: 'confirmed' });
    await expect(result).rejects.toThrow('已停止提交');
    expect(submit).not.toHaveBeenCalled();
  });
  it('submits the exact approval once when the original scope and live inputs remain valid', async () => {
    const approval = { approval_id: 'exact' };
    const submit = vi.fn(async received => ({ received }));
    const result = await submitAfterScopeConfirmation(frozen, async () => approval, current, submit);
    expect(submit).toHaveBeenCalledTimes(1);
    expect(result.received).toBe(approval);
  });
});


describe('independent storyboard node settings', () => {
  const defaults = { imageModelId: 'image-a', imageSize: '1024x1024' };
  const imageA: Model = { id: 'image-a', name: 'Image A', provider: 'ark', kind: 'image', downloaded: true, loaded: false, weights_mb: 0, submittability: { submittable: true, reasons: [] }, capabilities: { input_modes: ['text_to_image', 'image_to_image'], sizes: ['1024x1024', '2048x2048'], ratios: ['1:1'] } };
  const imageB: Model = { ...imageA, id: 'image-b', name: 'Image B', capabilities: { ...imageA.capabilities, sizes: ['2048x2048', '1536x2688'], ratios: ['1:1', '9:16'] } };
  const models = [imageA, imageB];
  const draft = { ...draftFromShot(shot), prompt: '保留真实包装', input_asset_id: 'ref-a' };

  it('keeps old version-1 drafts valid and resolves their existing defaults', () => {
    const cache = { version: 1, rows: [draft], videoModelId: 'video', ratio: '9:16', resolution: '720p', ...defaults, mode: 'clips', selectedIds: ['s1'] };
    const restored = parseProductionEditor(JSON.stringify(cache));
    expect(restored).toEqual(cache);
    expect(storyboardSettings(restored?.rows[0], defaults)).toEqual({ modelId: 'image-a', size: '1024x1024' });
  });

  it('quotes each node with its own model, size, prompt and reference', () => {
    const rows = [draft, { ...draft, key: 's2', shot_id: 's2', prompt: '第二镜提示词', input_asset_id: 'ref-b' }];
    const configured = rows.map(row => row.shot_id === 's1' ? { ...row, storyboardModelId: 'image-b', storyboardSize: '1536x2688' } : row);
    expect(inspectStoryboardDraft(configured[0], defaults, models).input).toEqual({ model_id: 'image-b', size: '1536x2688', prompt: '保留真实包装', input_asset_id: 'ref-a' });
    expect(inspectStoryboardDraft(configured[1], defaults, models).input).toEqual({ model_id: 'image-a', size: '1024x1024', prompt: '第二镜提示词', input_asset_id: 'ref-b' });
    expect(configured[1]).toBe(rows[1]);
    expect(rows[0]).not.toHaveProperty('storyboardModelId');
  });

  it('preserves node overrides when default settings change or a server shot refresh arrives', () => {
    const local = { ...draft, storyboardModelId: 'image-b', storyboardSize: '1536x2688' };
    const refreshed = restoreShotDraft({ ...shot, revision: 4, description: '服务端描述' }, local);
    expect(refreshed).toMatchObject({ storyboardModelId: 'image-b', storyboardSize: '1536x2688', prompt: draft.prompt, input_asset_id: 'ref-a' });
    expect(storyboardSettings(refreshed, { imageModelId: 'other-default', imageSize: '4096x4096' })).toEqual({ modelId: 'image-b', size: '1536x2688' });
    expect(shotDraftChanged(refreshed, { ...shot, revision: 4, description: '服务端描述' })).toBe(false);
  });

  it('round-trips independent settings and rejects invalid override types without coercing', () => {
    const rows = [{ ...draft, storyboardModelId: 'image-b', storyboardSize: '1536x2688' }];
    const cache = { version: 1, rows, videoModelId: 'video', ratio: '9:16', resolution: '720p', ...defaults, mode: 'clips', selectedIds: ['s1'] };
    expect(parseProductionEditor(JSON.stringify(cache))?.rows).toEqual(rows);
    for (const patch of [{ storyboardModelId: null }, { storyboardSize: 2048 }]) {
      expect(parseProductionEditor(JSON.stringify({ ...cache, rows: [{ ...rows[0], ...patch }] }))).toBeNull();
    }
  });

  it('keeps an explicitly cleared size empty and blocks mismatched or unavailable node capability', () => {
    expect(storyboardSettings({ ...draft, storyboardModelId: 'image-b', storyboardSize: '' }, defaults).size).toBe('');
    expect(inspectStoryboardDraft({ ...draft, storyboardModelId: 'image-b', storyboardSize: '1024x1024' }, defaults, models).input).toBeNull();
    expect(inspectStoryboardDraft({ ...draft, storyboardModelId: 'image-b', storyboardSize: '2048x2048' }, defaults, [imageA, { ...imageB, submittability: { submittable: false, reasons: ['MODEL_NOT_OPENED'] } }]).input).toBeNull();
    expect(inspectStoryboardDraft({ ...draft, storyboardModelId: 'image-b', storyboardSize: '2048x2048' }, defaults, [imageA, { ...imageB, capabilities: { ...imageB.capabilities, input_modes: ['text_to_image'] } }]).input).toBeNull();
    expect(inspectStoryboardDraft({ ...draft, storyboardModelId: 'image-b', storyboardSize: '2048x2048' }, { imageModelId: 'missing', imageSize: '' }, models).input?.model_id).toBe('image-b');
  });

  it('makes model or size edits distinguishable from the frozen quote without mutating it', () => {
    const row = { ...draft, storyboardModelId: 'image-b', storyboardSize: '2048x2048' };
    const quoted = inspectStoryboardDraft(row, defaults, models).input;
    const edited = inspectStoryboardDraft({ ...row, storyboardSize: '1536x2688' }, defaults, models).input;
    expect(quoted?.size).toBe('2048x2048');
    expect(edited?.size).toBe('1536x2688');
    expect(edited).not.toEqual(quoted);
    expect(productionFingerprint('p', 'scr', {}, [row], [shot], input)).not.toBe(productionFingerprint('p', 'scr', {}, [{ ...row, storyboardSize: '1536x2688' }], [shot], input));
  });
});

describe('AiHubMix production selection', () => {
  const image: Model = { id: 'aihubmix:flash', name: 'Flash', kind: 'image', provider: 'aihubmix', downloaded: false, loaded: false, weights_mb: 0,
    submittability: { submittable: true, reasons: [] }, capabilities: { input_modes: ['text_to_image', 'image_to_image'], ratios: ['1:1'], sizes: ['1024x1024'],
      parameter_combinations: [{ generation_mode: ['text_to_image', 'image_to_image'], ratio: ['1:1'], size: ['1024x1024'] }] } };
  const video: Model = { ...image, id: 'aihubmix:fast', kind: 'video', capabilities: { input_modes: ['image_to_video', 'text_to_video'], ratios: ['16:9'], resolutions: ['480p'], duration_policy: { kind: 'enum', allowed: [5] },
      parameter_combinations: [{ generation_mode: ['text_to_video', 'image_to_video'], ratio: ['16:9'], resolution: ['480p'], duration: [5], generate_audio: [false] }] } };
  const models = [{ ...image, id: 'old-image', provider: 'ark' as const }, image, video];

  it('starts an empty plan with supported AiHubMix defaults and does not mutate metadata', () => {
    const before = structuredClone(models);
    expect(initialProductionSelection(null, [], models)).toEqual({ imageModelId: image.id, imageSize: '1024x1024', videoModelId: video.id, ratio: '16:9', resolution: '480p' });
    expect(models).toEqual(before);
  });
  it('retains explicit browser settings including an empty choice and preserves an older shot provider', () => {
    const old = { imageModelId: 'old-image', imageSize: '2048x2048', videoModelId: '', ratio: '9:16', resolution: '720p' };
    expect(initialProductionSelection(old, [], models)).toEqual(old);
    expect(initialProductionSelection(null, [{ ...shot, model_id: 'ark-old-video' }], models)).toEqual({ videoModelId: 'ark-old-video', ratio: '', resolution: '', imageModelId: '', imageSize: '' });
    expect(initialProductionSelection(null, [{ ...shot, model_id: 'a' }, { ...shot, shot_id: 's2', model_id: 'b' }], models).videoModelId).toBe('');
  });
  it('derives storyboard ratio for the model combination rule without adding hidden API fields', () => {
    const draft = { ...draftFromShot(shot), prompt: 'whole cup', input_asset_id: '' };
    const defaults = { imageModelId: image.id, imageSize: '1024x1024' };
    for (const reference of ['', 'reference']) {
      const result = inspectStoryboardDraft({ ...draft, input_asset_id: reference }, defaults, models);
      expect(result.error).toBeNull();
      expect(result.input).toEqual({ model_id: image.id, size: '1024x1024', prompt: 'whole cup', input_asset_id: reference || null });
    }
    expect(inspectStoryboardDraft(draft, { ...defaults, imageSize: '2048x2048' }, models).input).toBeNull();
  });
});
