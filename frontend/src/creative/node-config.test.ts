import { describe, expect, it } from 'vitest';
import { nodeGenerationIssue, sameNodeConfig, withDefaultNodeModel, withNodeMode, withNodeModel } from './node-config';
import type { Model } from '../types';
describe('independent node settings',()=>{
  it('recognizes a saved config after top-level and params field normalization',()=>{
    const draft={title:'产品图',prompt:'a cup',model_id:'image-model',params:{ratio:'16:9',size:'2048x1152'}};
    const saved={params:{size:'2048x1152',ratio:'16:9'},model_id:'image-model',prompt:'a cup',title:'产品图'};
    expect(sameNodeConfig(draft,saved)).toBe(true);
    expect(Object.keys(draft)).toEqual(['title','prompt','model_id','params']);
    expect(Object.keys(draft.params)).toEqual(['ratio','size']);
  });
  it('keeps an actual prompt change dirty after saving the previous config',()=>{
    const saved={params:{size:'2048x1152',ratio:'16:9'},prompt:'a cup'};
    expect(sameNodeConfig({prompt:'a blue cup',params:{ratio:'16:9',size:'2048x1152'}},saved)).toBe(false);
  });
  it('compares objects inside arrays recursively while retaining creative sequence',()=>{
    const first={references:[{asset_id:'cup',settings:{weight:1,crop:'center'}},{asset_id:'table'}]};
    const reorderedFields={references:[{settings:{crop:'center',weight:1},asset_id:'cup'},{asset_id:'table'}]};
    expect(sameNodeConfig(first,reorderedFields)).toBe(true);
    expect(sameNodeConfig(first,{references:[...reorderedFields.references].reverse()})).toBe(false);
  });
  it('detects changed nested settings and removed fields',()=>{
    expect(sameNodeConfig({params:{ratio:'16:9',size:'2048x1152'}},{params:{size:'2048x1152',ratio:'9:16'}})).toBe(false);
    expect(sameNodeConfig({prompt:'a cup',input_asset_id:'ref'},{prompt:'a cup'})).toBe(false);
  });
  it('removes retained references when switching to text input without mutating the source',()=>{
    const source={generation_mode:'image_to_video',input_asset_id:'original',prompt:'a cup',title:'镜头'};
    expect(withNodeMode(source,'text_to_video')).toEqual({generation_mode:'text_to_video',prompt:'a cup',title:'镜头'});
    expect(source.input_asset_id).toBe('original');
  });
  it('keeps prompt and title while replacing incompatible model specs from actual capabilities',()=>{
    const model={id:'new',kind:'video',capabilities:{ratios:['16:9'],resolutions:['720p'],duration_policy:{kind:'enum',allowed:[4,8]}}} as Model;
    expect(withNodeModel({prompt:'move',title:'one',params:{ratio:'9:16',duration:5,resolution:'4K',size:'2048x2048'}},model)).toEqual({prompt:'move',title:'one',model_id:'new',params:{ratio:'16:9',duration:4,resolution:'720p'}});
  });
});

describe('node generation input before quote', () => {
  const image: Model = { id: 'image', name: 'Image', kind: 'image', provider: 'ark', downloaded: false, loaded: false, weights_mb: 0, submittability: { submittable: true, reasons: [] }, capabilities: { input_modes: ['text_to_image', 'image_to_image'], ratios: ['1:1', '16:9'], sizes: ['1024x1024', '2688x1536'] } };
  const video: Model = { id: 'video', name: 'Video', kind: 'video', provider: 'ark', downloaded: false, loaded: false, weights_mb: 0, submittability: { submittable: true, reasons: [] }, capabilities: { input_modes: ['text_to_video', 'image_to_video'], ratios: ['16:9'], resolutions: ['720p'], duration_policy: { kind: 'enum', allowed: [5, 10] } } };
  const imageConfig = { prompt: 'A cup on a table', generation_mode: 'text_to_image', model_id: 'image', params: { ratio: '1:1', size: '1024x1024' } };
  const videoConfig = { prompt: 'Slow camera push', generation_mode: 'text_to_video', model_id: 'video', params: { ratio: '16:9', resolution: '720p', duration: 5 } };

  it('accepts the exact supported inputs of all four modes', () => {
    expect(nodeGenerationIssue('image', imageConfig, image)).toBeNull();
    expect(nodeGenerationIssue('image', { ...imageConfig, generation_mode: 'image_to_image', input_asset_id: 'uploaded-reference' }, image)).toBeNull();
    expect(nodeGenerationIssue('video', videoConfig, video)).toBeNull();
    expect(nodeGenerationIssue('video', { ...videoConfig, generation_mode: 'image_to_video', input_asset_id: 'uploaded-reference' }, video)).toBeNull();
  });
  it('requires the reference for image input and rejects a retained hidden reference in text mode', () => {
    expect(nodeGenerationIssue('image', { ...imageConfig, generation_mode: 'image_to_image' }, image)).toMatch('参考图片');
    expect(nodeGenerationIssue('video', { ...videoConfig, input_asset_id: 'hidden-reference' }, video)).toMatch('移除参考图片');
  });
  it('rejects unsupported ratios, mismatched image sizes and invalid exact video durations', () => {
    expect(nodeGenerationIssue('image', { ...imageConfig, params: { ratio: '16:9', size: '1024x1024' } }, image)).toMatch('清晰度');
    expect(nodeGenerationIssue('video', { ...videoConfig, params: { ...videoConfig.params, ratio: '9:16' } }, video)).toMatch('比例');
    expect(nodeGenerationIssue('video', { ...videoConfig, params: { ...videoConfig.params, duration: 6 } }, video)).toMatch('5、10');
    expect(nodeGenerationIssue('video', { ...videoConfig, params: { ...videoConfig.params, duration: '5' } }, video)).toMatch('整数秒');
  });
  it('never infers model permission or input-mode support from a selected model id', () => {
    expect(nodeGenerationIssue('image', imageConfig, { ...image, submittability: undefined })).toMatch('提交许可');
    expect(nodeGenerationIssue('image', { ...imageConfig, generation_mode: 'image_to_image', input_asset_id: 'ref' }, { ...image, capabilities: { ...image.capabilities, input_modes: ['text_to_image'] } })).toMatch('输入方式');
    expect(nodeGenerationIssue('image', imageConfig, undefined)).toMatch('选择一个生成模型');
    expect(nodeGenerationIssue('image', { ...imageConfig, prompt: '  ' }, image)).toMatch('先描述');
  });
});

describe('AiHubMix canvas selections', () => {
  const flash: Model = { id: 'aihubmix:flash', name: 'Flash', kind: 'image', provider: 'aihubmix', downloaded: false, loaded: false, weights_mb: 0,
    submittability: { submittable: true, reasons: [] }, capabilities: { input_modes: ['text_to_image', 'image_to_image'], ratios: ['1:1'], sizes: ['1024x1024'],
      parameter_combinations: [{ generation_mode: ['text_to_image', 'image_to_image'], ratio: ['1:1'], size: ['1024x1024'] }] } };
  const fast: Model = { ...flash, id: 'aihubmix:fast', name: 'Fast', kind: 'video', capabilities: { input_modes: ['text_to_video', 'image_to_video'], ratios: ['16:9'], resolutions: ['480p'], duration_policy: { kind: 'enum', allowed: [5] },
    parameter_combinations: [{ generation_mode: ['text_to_video', 'image_to_video'], ratio: ['16:9'], resolution: ['480p'], duration: [5], generate_audio: [false] }] } };
  const ark: Model = { ...flash, id: 'ark-flash', provider: 'ark' };

  it('new nodes prefer available AiHubMix and adopt only public permitted specs', () => {
    const image = withDefaultNodeModel({ prompt: 'one cup', generation_mode: 'text_to_image' }, 'image', [ark, flash, fast]);
    expect(image).toMatchObject({ model_id: flash.id, params: { ratio: '1:1', size: '1024x1024' } });
    expect(nodeGenerationIssue('image', image, flash)).toBeNull();
    const video = withDefaultNodeModel({ prompt: 'move', generation_mode: 'image_to_video', input_asset_id: 'reference' }, 'video', [ark, fast]);
    expect(video).toMatchObject({ model_id: fast.id, params: { ratio: '16:9', resolution: '480p', duration: 5, generate_audio: false } });
    expect(nodeGenerationIssue('video', video, fast)).toBeNull();
  });

  it('does not replace historical choices, cleared choices or inputs when metadata refreshes', () => {
    for (const model_id of [ark.id, '', 'removed-model']) {
      const stored = { prompt: 'old prompt', model_id, params: { ratio: '9:16', size: '1536x2688' } };
      expect(withDefaultNodeModel(stored, 'image', [flash, ark])).toBe(stored);
    }
    const closed = { ...flash, submittability: { submittable: false, reasons: ['MODEL_NOT_VERIFIED'] } };
    expect(withDefaultNodeModel({ generation_mode: 'text_to_image' }, 'image', [closed, ark]).model_id).toBe(ark.id);
    expect(withDefaultNodeModel({ generation_mode: 'text_to_image' }, 'image', [closed])).not.toHaveProperty('model_id');
  });

  it('changing provider retains prompt/reference while replacing incompatible specs', () => {
    const original = { prompt: 'keep cup', generation_mode: 'image_to_video', input_asset_id: 'original', model_id: 'old-video', params: { ratio: '9:16', resolution: '720p', duration: 10, generate_audio: true } };
    const switched = withNodeModel(original, fast);
    expect(switched).toMatchObject({ prompt: original.prompt, input_asset_id: original.input_asset_id, model_id: fast.id, params: { ratio: '16:9', resolution: '480p', duration: 5, generate_audio: false } });
    expect(original.params).toEqual({ ratio: '9:16', resolution: '720p', duration: 10, generate_audio: true });
    expect(nodeGenerationIssue('video', { ...switched, params: { ...switched.params as object, resolution: '720p' } }, fast)).toMatch('清晰度');
  });
});
