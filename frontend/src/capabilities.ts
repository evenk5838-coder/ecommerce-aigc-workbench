import type { DurationPolicy, Model } from './types';
import { isCloudModel } from './model-catalog';
import { combinationRules, filterSpecOptions, SPEC_POLICY_ERROR, validateSpecCombination, type SpecSelection } from './model-specs';

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

export function getSupportedRatios(model: Model | undefined, selection?: SpecSelection): string[] {
  const ratios = filterSpecOptions(model, 'ratio', stringOptions(model?.capabilities?.ratios), selection);
  if (model?.kind !== 'image') return ratios;
  return ratios.filter(ratio => filterSpecOptions(model, 'size', stringOptions(model.capabilities?.sizes), {...selection, ratio})
    .some(size => imageSizeRatio(size) === ratio));
}
export const getSupportedResolutions = (model: Model | undefined, selection?: SpecSelection): string[] => filterSpecOptions(model, 'resolution', stringOptions(model?.capabilities?.resolutions), selection);
export const getSupportedImageSizes = (model: Model | undefined, selection?: SpecSelection): string[] => filterSpecOptions(model, 'size', stringOptions(model?.capabilities?.sizes), selection);

export function getSupportedDurations(model: Model | undefined, selection?: SpecSelection): number[] {
  const policy = getDurationPolicy(model);
  const options = policy.kind === 'enum' ? policy.allowed : policy.kind === 'range'
    ? Array.from({length: Math.min(10000, (policy.max-policy.min)/policy.step+1)}, (_, i) => policy.min+i*policy.step) : [];
  return filterSpecOptions(model, 'duration', options, selection);
}

// Existing pixel-aligned 2K presets use these display ratios. Membership in the
// server's sizes and ratios is still required; this mapping grants no capability.
const imagePresets: Record<string, string> = {
  '1:1': '2048x2048', '3:4': '1728x2304', '4:3': '2304x1728',
  '9:16': '1536x2688', '16:9': '2688x1536',
};

export function imageSizeRatio(size: string): string | null {
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

export function getImageSizesForRatio(model: Model | undefined, ratio: string, selection?: SpecSelection): string[] {
  if (!getSupportedRatios(model, selection).includes(ratio)) return [];
  return getSupportedImageSizes(model, {...selection, ratio}).filter(size => imageSizeRatio(size) === ratio);
}

/** Compatibility default only. Missing/unsupported presets require an explicit selection. */
export function getDefaultImageSize(model: Model | undefined, ratio: string, selection?: SpecSelection): string | undefined {
  const size = imagePresets[ratio];
  return size && getImageSizesForRatio(model, ratio, selection).includes(size) ? size : undefined;
}

const blockedReasonMessages: Record<string, string> = {
  MODEL_NOT_OPENED: '此模型尚未开通，请在控制台开通后刷新模型列表',
  MODEL_NOT_VERIFIED: '此模型暂未开放，请选择其他模型',
  MODEL_PRICE_UNCONFIRMED: '此模型费用暂未就绪，请选择其他模型',
  AUTH_MISSING: '模型服务尚未连接，请检查服务配置',
  MODEL_NOT_DOWNLOADED: '服务端模型尚未就绪，暂不可生成',
  MODEL_EOS: '此模型已下线，请重新选择模型',
  DURATION_POLICY_UNAVAILABLE: '时长能力未配置，请刷新模型列表',
  MODEL_SPEC_POLICY_UNAVAILABLE: SPEC_POLICY_ERROR,
};

/** Only the literal server permission grants submission; reason codes supply copy, not policy. */
export function getSubmissionBlockedReason(submittable: unknown, reasons: unknown): string | null {
  if (submittable === true) return null;
  if (submittable !== false) return '服务端提交许可缺失或无效，请刷新后重试';
  const codes = Array.isArray(reasons)
    ? [...new Set(reasons.filter((reason): reason is string => typeof reason === 'string' && reason.trim().length > 0))]
    : [];
  return codes.length
    ? codes.map(code => Object.hasOwn(blockedReasonMessages, code) ? blockedReasonMessages[code] : `服务端暂不允许提交（${code}）`).join('；')
    : '服务端暂不允许提交，请刷新模型列表后重试';
}

/** Provider compatibility is local; submission permission is exclusively server-supplied. */
export function getModelUnavailableReason(model: Model | undefined): string | null {
  if (!model) return '模型信息尚未加载，请刷新模型列表';
  if (!isCloudModel(model)) return '当前工作台仅支持云端模型，本地模型不可用于此入口';
  if (combinationRules(model) === null) return SPEC_POLICY_ERROR;
  const raw: unknown = model.submittability;
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return getSubmissionBlockedReason(undefined, undefined);
  const permission = raw as Record<string, unknown>;
  return getSubmissionBlockedReason(permission.submittable, permission.reasons);
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

/** Revalidate frozen quotes against newly loaded metadata without replacing any selected value. */
export function validateGenerationSpecs(model: Model | undefined, kind: 'image'|'video', params: Record<string, unknown>): string | null {
  const modelError = validateModelSelection(model, kind);
  if (modelError) return modelError;
  if (!stringOptions(model?.capabilities?.input_modes).includes(String(params.generation_mode))) return '当前模型不支持这种输入方式，请重新选择。';
  const ratio = kind === 'image' && params.ratio === undefined ? imageSizeRatio(String(params.size)) : params.ratio;
  if (typeof ratio !== 'string' || !getSupportedRatios(model).includes(ratio)) return '请重新选择当前模型支持的画面比例。';
  if (kind === 'image' && !getImageSizesForRatio(model, ratio).includes(String(params.size))) return '请为当前比例选择清晰度。';
  if (kind === 'video') {
    const durationError = validateVideoDuration(model, params.duration);
    if (durationError) return durationError;
    if (!getSupportedResolutions(model).includes(String(params.resolution))) return '请重新选择视频清晰度。';
  }
  return validateSpecCombination(model, params);
}
