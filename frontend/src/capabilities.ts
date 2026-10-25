import type { DurationPolicy, Model } from './types';

const unavailable = (): DurationPolicy => ({ kind: 'unavailable' });
const isInteger = (value: unknown): value is number => typeof value === 'number' && Number.isSafeInteger(value);

/** Read the server's resolved policy only; legacy range/enum fields are not fallbacks. */
export function getDurationPolicy(model: Model | undefined): DurationPolicy {
  const raw: unknown = model?.capabilities?.duration_policy;
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return unavailable();
  const policy = raw as Record<string, unknown>;
  if (policy.kind === 'range') {
    const { min, max, step } = policy;
    if (!isInteger(min) || !isInteger(max) || !isInteger(step) || min <= 0 || max < min || step <= 0 || (max - min) % step !== 0) return unavailable();
    return { kind: 'range', min, max, step, allowed: null };
  }
  if (policy.kind === 'enum') {
    if (!Array.isArray(policy.allowed) || !policy.allowed.length || !policy.allowed.every(value => isInteger(value) && value > 0)) return unavailable();
    return { kind: 'enum', allowed: [...new Set(policy.allowed as number[])].sort((a, b) => a - b), min: null, max: null, step: null };
  }
  return unavailable();
}

function stringOptions(value: unknown): string[] {
  return Array.isArray(value) ? [...new Set(value.filter((item): item is string => typeof item === 'string' && item.trim().length > 0))] : [];
}

export const getSupportedRatios = (model: Model | undefined): string[] => stringOptions(model?.capabilities?.ratios);
export const getSupportedResolutions = (model: Model | undefined): string[] => stringOptions(model?.capabilities?.resolutions);
export const getSupportedImageSizes = (model: Model | undefined): string[] => stringOptions(model?.capabilities?.sizes);

// Existing pixel-aligned 2K presets use these display ratios. Membership in the
// server's sizes and ratios is still required; this mapping grants no capability.
const imagePresets: Record<string, string> = {
  '1:1': '2048x2048', '3:4': '1728x2304', '4:3': '2304x1728',
  '9:16': '1536x2688', '16:9': '2688x1536',
};

function imageSizeRatio(size: string): string | null {
  const preset = Object.entries(imagePresets).find(([, value]) => value === size);
  if (preset) return preset[0];
  const match = /^(\d+)x(\d+)$/.exec(size);
  if (!match) return null;
  const width = Number(match[1]), height = Number(match[2]);
  if (!Number.isSafeInteger(width) || !Number.isSafeInteger(height) || width <= 0 || height <= 0) return null;
  let a = width, b = height;
  while (b !== 0) { const rest = a % b; a = b; b = rest; }
  return `${width / a}:${height / a}`;
}

export function getImageSizesForRatio(model: Model | undefined, ratio: string): string[] {
  if (!getSupportedRatios(model).includes(ratio)) return [];
  return getSupportedImageSizes(model).filter(size => imageSizeRatio(size) === ratio);
}

/** Compatibility default only. Missing/unsupported presets require an explicit selection. */
export function getDefaultImageSize(model: Model | undefined, ratio: string): string | undefined {
  const size = imagePresets[ratio];
  return size && getImageSizesForRatio(model, ratio).includes(size) ? size : undefined;
}

/** A scheduled EOS is a warning, not a reason to switch or disable a model. */
export function getModelUnavailableReason(model: Model | undefined): string | null {
  if (!model) return '模型信息尚未加载，请刷新模型列表';
  if (model.provider !== 'ark' || model.activation === 'local_history') return '此模型仅保留历史记录，不能用于云端生成';
  if (model.activation === 'not_opened') return '此模型尚未开通，请选择已验证模型';
  if (model.activation !== 'verified') return '此模型尚未验证开通状态，暂不可生成';
  if (!model.downloaded) return '此模型尚未就绪';
  if (model.lifecycle?.status === 'eos_expired') return '此模型已下线，请重新选择模型';
  if (model.lifecycle?.status !== 'active' && model.lifecycle?.status !== 'eos_scheduled') return '模型生命周期状态未加载，请刷新模型列表';
  return null;
}

export function validateModelSelection(model: Model | undefined, kind: 'image' | 'video'): string | null {
  const unavailableReason = getModelUnavailableReason(model);
  if (unavailableReason) return unavailableReason;
  if (model?.kind !== kind) return '所选模型不支持当前生成类型，请重新选择';
  if (!getSupportedRatios(model).length) return '画面比例能力未配置，请刷新模型列表';
  if (kind === 'image' && !getSupportedImageSizes(model).length) return '图片尺寸能力未配置，请刷新模型列表';
  if (kind === 'video') {
    if (!getSupportedResolutions(model).length) return '视频清晰度能力未配置，请刷新模型列表';
    if (getDurationPolicy(model).kind === 'unavailable') return '时长能力未配置，请刷新模型列表';
  }
  return null;
}

/** A null return means the exact supplied value is legal; no coercion or clamping. */
export function validateVideoDuration(model: Model | undefined, value: unknown): string | null {
  const policy = getDurationPolicy(model);
  if (policy.kind === 'unavailable') return '时长能力未配置，请刷新模型列表';
  if (!isInteger(value)) return '视频时长必须是整数秒，不能输入小数或文字';
  if (policy.kind === 'enum') return policy.allowed.includes(value) ? null : `视频时长请选择 ${policy.allowed.join('、')} 秒`;
  if (value < policy.min || value > policy.max) return `视频时长须为 ${policy.min}–${policy.max} 秒`;
  if ((value - policy.min) % policy.step !== 0) return `视频时长须从 ${policy.min} 秒起，按 ${policy.step} 秒递增`;
  return null;
}
