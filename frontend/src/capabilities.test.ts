import { describe, expect, it } from 'vitest';
import { getDefaultImageSize, getDurationPolicy, getImageSizesForRatio, getModelUnavailableReason, getSupportedImageSizes, getSupportedRatios, getSupportedResolutions, validateModelSelection, validateVideoDuration } from './capabilities';
import type { DurationPolicy, Model } from './types';

const video: Model = {
  id: 'server-video-model', name: 'Video', kind: 'video', provider: 'ark', activation: 'verified',
  submittability: { submittable: true, reasons: [] },
  downloaded: true, loaded: false, weights_mb: 0, lifecycle: { status: 'active' },
  capabilities: { ratios: ['9:16', '16:9', '1:1'], resolutions: ['480p', '720p', '1080p'],
    duration_s: [5, 10], duration_range_s: [2, 12], duration_step_s: 1,
    duration_policy: { kind: 'range', min: 2, max: 12, step: 1 } },
};
const withPolicy = (policy: unknown): Model => ({ ...video, capabilities: { ...video.capabilities, duration_policy: policy as DurationPolicy } });

describe('server duration policy', () => {
  it('uses the resolved policy rather than legacy fields or a model-name lookup', () => {
    expect(getDurationPolicy(video)).toEqual({ kind: 'range', min: 2, max: 12, step: 1, allowed: null });
    expect(getDurationPolicy(withPolicy(undefined))).toEqual({ kind: 'unavailable' });
    expect(getDurationPolicy(withPolicy({ kind: 'unavailable' }))).toEqual({ kind: 'unavailable' });
    expect(getDurationPolicy(withPolicy({ kind: 'enum', allowed: [10, 5, 5] }))).toEqual({ kind: 'enum', min: null, max: null, step: null, allowed: [5, 10] });
  });

  it.each([undefined, null, {}, [], 'range', { kind: 'range', min: 2, max: 12 },
    { kind: 'range', min: 2, max: 12, step: 0 }, { kind: 'range', min: 2, max: 12, step: 3 },
    { kind: 'range', min: 2.5, max: 12, step: 1 }, { kind: 'range', min: '2', max: 12, step: 1 },
    { kind: 'enum', allowed: [] }, { kind: 'enum', allowed: [5, '10'] },
  ])('treats incomplete or malformed policy as unavailable: %j', policy => {
    expect(getDurationPolicy(withPolicy(policy)).kind).toBe('unavailable');
    expect(validateVideoDuration(withPolicy(policy), 5)).toContain('时长能力未配置');
  });

  it('validates exact integer range boundaries and steps without coercion', () => {
    for (const value of [2, 3, 6, 12]) expect(validateVideoDuration(video, value)).toBeNull();
    for (const value of [1, 13, 5.5, '5', true, NaN, Infinity, undefined]) expect(validateVideoDuration(video, value)).not.toBeNull();
    const stepped = withPolicy({ kind: 'range', min: 2, max: 12, step: 2 });
    expect(validateVideoDuration(stepped, 4)).toBeNull();
    expect(validateVideoDuration(stepped, 3)).toContain('按 2 秒递增');
  });

  it('accepts only the exact allowed enum and does not modify it', () => {
    const allowed = [10, 5];
    const model = withPolicy({ kind: 'enum', allowed });
    expect(validateVideoDuration(model, 10)).toBeNull();
    expect(validateVideoDuration(model, 6)).toContain('5、10');
    expect(allowed).toEqual([10, 5]);
  });
});

describe('model capability and availability', () => {
  it('uses only supplied ratio/resolution/size metadata', () => {
    expect(getSupportedRatios(video)).toEqual(['9:16', '16:9', '1:1']);
    expect(getSupportedResolutions(video)).toEqual(['480p', '720p', '1080p']);
    expect(getSupportedImageSizes(video)).toEqual([]);
    expect(getSupportedRatios(undefined)).toEqual([]);
    expect(validateModelSelection({ ...video, capabilities: {} }, 'video')).not.toBeNull();
  });

  it('filters image sizes by ratio while allowing supported 1K and existing pixel-aligned 2K sizes', () => {
    const image: Model = { ...video, kind: 'image', capabilities: { ratios: ['1:1', '9:16', '3:2'], sizes: ['1024x1024', '2048x2048', '1536x2688', '1536x1024'] } };
    expect(getImageSizesForRatio(image, '1:1')).toEqual(['1024x1024', '2048x2048']);
    expect(getImageSizesForRatio(image, '9:16')).toEqual(['1536x2688']);
    expect(getImageSizesForRatio(image, '3:2')).toEqual(['1536x1024']);
    expect(getImageSizesForRatio(image, '16:9')).toEqual([]);
    expect(getDefaultImageSize(image, '1:1')).toBe('2048x2048');
    expect(getDefaultImageSize(image, '3:2')).toBeUndefined();
    expect(getDefaultImageSize({ ...image, capabilities: { ratios: ['1:1'], sizes: ['1024x1024'] } }, '1:1')).toBeUndefined();
  });

  it('uses the server permission rather than recomputing activation, downloaded or lifecycle metadata', () => {
    expect(getModelUnavailableReason(video)).toBeNull();
    expect(getModelUnavailableReason({ ...video, activation: 'unknown', downloaded: false, lifecycle: undefined })).toBeNull();
    expect(getModelUnavailableReason({ ...video, activation: 'not_opened' })).toBeNull();
    expect(getModelUnavailableReason({ ...video, lifecycle: { status: 'eos_scheduled', migrate_to: 'some-other-model', migrate_to_activation: 'not_opened' } })).toBeNull();
    expect(getModelUnavailableReason({ ...video, provider: 'local' })).toContain('仅支持云端');
    expect(getModelUnavailableReason({ ...video, provider: undefined })).toContain('仅支持云端');
    expect(validateModelSelection(video, 'image')).toContain('生成类型');
  });

  it('blocks explicit false despite otherwise ready metadata and displays server reasons', () => {
    expect(getModelUnavailableReason({ ...video, submittability: { submittable: false, reasons: ['MODEL_NOT_OPENED'] } })).toContain('尚未开通');
    expect(getModelUnavailableReason({ ...video, lifecycle: { status: 'eos_expired' }, submittability: { submittable: false, reasons: ['MODEL_EOS'] } })).toContain('已下线');
    expect(getModelUnavailableReason({ ...video, submittability: { submittable: false, reasons: [] } })).toContain('服务端暂不允许提交');
  });

  it('never promotes an unknown or malformed blocked reason into permission', () => {
    for (const code of ['FUTURE_ACCOUNT_POLICY', 'toString', '__proto__']) {
      expect(getModelUnavailableReason({ ...video, submittability: { submittable: false, reasons: [code] } })).toBe(`服务端暂不允许提交（${code}）`);
    }
    for (const reasons of [null, undefined, 'MODEL_EOS', [null, 42], []]) {
      const submittability = { submittable: false, reasons } as unknown as Model['submittability'];
      expect(getModelUnavailableReason({ ...video, submittability })).not.toBeNull();
    }
  });

  it.each([undefined, null, {}, [], 'allowed', { submittable: null }, { submittable: 'true' }, { submittable: 1 }])(
    'blocks missing or malformed submission permission: %j', permission => {
      const submittability = permission as unknown as Model['submittability'];
      expect(getModelUnavailableReason({ ...video, submittability })).toContain('许可缺失或无效');
      expect(validateModelSelection({ ...video, submittability }, 'video')).not.toBeNull();
    },
  );

  it('keeps local parameter safety checks even with a legal server permission', () => {
    const noDuration = withPolicy({ kind: 'unavailable' });
    expect(getModelUnavailableReason(noDuration)).toBeNull();
    expect(validateModelSelection(noDuration, 'video')).toContain('时长能力未配置');
    expect(validateVideoDuration(video, 5.5)).toContain('整数秒');
  });
});
