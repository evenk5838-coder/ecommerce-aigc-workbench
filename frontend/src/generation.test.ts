import { describe, expect, it } from 'vitest';
import { buildGenerationInput, getImagePrompt, type GenerationOptions } from './generation';
import type { Asset } from './types';

const reference: Asset = {
  id: 'asset_product', kind: 'image', mime: 'image/jpeg', size: 1024,
  width: 1024, height: 1024, source: 'upload', created_at: 1, meta_json: '{}',
};
const imageOptions: GenerationOptions = {
  kind: 'image', imageMode: 'image_to_image', modelId: 'doubao-seedream-5-0-flash-260915',
  prompt: '  保留商品，更换背景  ', reference, ratio: '3:4', duration: 5,
};

describe('cloud generation requests', () => {
  it('binds the chosen reference and explicit mode to an image-to-image request', () => {
    expect(buildGenerationInput(imageOptions)).toEqual({
      kind: 'image', model_id: imageOptions.modelId, input_asset_id: reference.id,
      params: { prompt: '保留商品，更换背景', generation_mode: 'image_to_image', size: '1728x2304' },
    });
  });

  it('drops retained reference state when changing back to text-to-image', () => {
    const request = buildGenerationInput({ ...imageOptions, imageMode: 'text_to_image' });
    expect(request.input_asset_id).toBeNull();
    expect(request.params.generation_mode).toBe('text_to_image');
  });

  it('requires an actual image for image-to-image and validates its size', () => {
    expect(() => buildGenerationInput({ ...imageOptions, reference: undefined })).toThrow('参考图');
    expect(() => buildGenerationInput({ ...imageOptions, reference: { ...reference, kind: 'video', mime: 'video/mp4' } })).toThrow('必须是图片');
    expect(() => buildGenerationInput({ ...imageOptions, reference: { ...reference, mime: 'application/octet-stream' } })).toThrow('必须是图片');
    expect(() => buildGenerationInput({ ...imageOptions, reference: { ...reference, size: 10 * 1024 * 1024 + 1 } })).toThrow('10 MB');
    expect(() => buildGenerationInput({ ...imageOptions, reference: { ...reference, size: 0 } })).toThrow('文件无效');
    expect(() => buildGenerationInput({ ...imageOptions, reference: { ...reference, size: 10 * 1024 * 1024 } })).not.toThrow();
  });

  it('builds video parameters without leaking local inference or image-only fields', () => {
    expect(buildGenerationInput({ ...imageOptions, kind: 'video', modelId: 'doubao-seedance-1-0-pro-fast-251015', ratio: '9:16' })).toEqual({
      kind: 'video', model_id: 'doubao-seedance-1-0-pro-fast-251015', input_asset_id: reference.id,
      params: { prompt: '保留商品，更换背景', duration: 5, ratio: '9:16', resolution: '720p', generate_audio: false },
    });
    expect(() => buildGenerationInput({ ...imageOptions, kind: 'video', reference: undefined })).toThrow('视频首帧图片');
    expect(() => buildGenerationInput({ ...imageOptions, kind: 'video', duration: 3 })).toThrow('5 秒或 10 秒');
  });

  it('keeps reference and parameters isolated between independent requests', () => {
    const first = buildGenerationInput(imageOptions);
    const second = buildGenerationInput({ ...imageOptions, reference: { ...reference, id: 'asset_other' }, ratio: '1:1' });
    second.params.prompt = '另一商品';
    expect(first.input_asset_id).toBe(reference.id);
    expect(second.input_asset_id).toBe('asset_other');
    expect(first.params.prompt).toBe('保留商品，更换背景');
    expect(first.params.size).toBe('1728x2304');
    expect(second.params.size).toBe('2048x2048');
  });

  it('blocks empty prompts and unsupported ratios before submission', () => {
    expect(() => buildGenerationInput({ ...imageOptions, prompt: ' \n ' })).toThrow('生成描述');
    expect(() => buildGenerationInput({ ...imageOptions, ratio: 'unknown' })).toThrow('画面比例');
  });
});

describe('image purpose presets', () => {
  it('keeps purposes below image mode and does not invent product counts', () => {
    const white = getImagePrompt('image_to_image', 'white');
    const scene = getImagePrompt('image_to_image', 'scene');
    const custom = getImagePrompt('image_to_image', 'custom');
    for (const preset of [white, scene, custom]) {
      expect(preset).toContain('参考图中实际出现的商品');
      expect(preset).toContain('不增加或删除商品');
      expect(preset).not.toMatch(/三件|罐子|软管|单瓶/);
    }
    expect(white).toContain('纯白背景');
    expect(scene).toContain('生活方式场景');
    expect(getImagePrompt('text_to_image', 'white')).not.toContain('参考图');
    expect(getImagePrompt('text_to_image', 'custom')).toBe('');
  });
});
