import type { GenerationMode, Model } from '../types';
import { getDurationPolicy, getImageSizesForRatio, getSupportedRatios, getSupportedResolutions, validateModelSelection, validateVideoDuration } from '../capabilities';

export type NodeConfig = Record<string, unknown>;
function orderedConfig(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(orderedConfig);
  if (value !== null && typeof value === 'object') {
    const fields = value as Record<string, unknown>;
    return Object.fromEntries(Object.keys(fields).sort().map(key => [key, orderedConfig(fields[key])]));
  }
  return value;
}
/** Server normalization can reorder object fields; array order still carries creative intent. */
export function sameNodeConfig(left: NodeConfig, right: NodeConfig): boolean {
  return JSON.stringify(orderedConfig(left)) === JSON.stringify(orderedConfig(right));
}
export const modeLabels: Record<GenerationMode, string> = { text_to_image: '文生图', image_to_image: '图生图', text_to_video: '文生视频', image_to_video: '图生视频' };
export function nodeModes(kind: string): GenerationMode[] { return kind === 'image' ? ['text_to_image', 'image_to_image'] : ['text_to_video', 'image_to_video']; }
export function objectValue(value: unknown): Record<string, unknown> { return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : {}; }
export function textValue(value: unknown): string { return typeof value === 'string' ? value : ''; }
export function durationsFor(model: Model | undefined): number[] {
  const p = getDurationPolicy(model);
  return p.kind === 'enum' ? p.allowed : p.kind === 'range' ? Array.from({length: Math.min(300, Math.floor((p.max-p.min)/p.step)+1)}, (_,i)=>p.min+i*p.step) : [];
}
/** Model switches retain creative content, while incompatible specs are intentionally replaced. */
export function withNodeModel(config: NodeConfig, model: Model): NodeConfig {
  const old = objectValue(config.params), ratios = getSupportedRatios(model), ratio = ratios.includes(textValue(old.ratio)) ? textValue(old.ratio) : ratios[0];
  const params: NodeConfig = {};
  if (ratio) params.ratio = ratio;
  if (model.kind === 'image') {
    const sizes = getImageSizesForRatio(model, ratio);
    if (sizes.length) params.size = sizes.includes(textValue(old.size)) ? old.size : sizes[0];
  } else {
    const resolutions = getSupportedResolutions(model), durations = durationsFor(model);
    if (resolutions.length) params.resolution = resolutions.includes(textValue(old.resolution)) ? old.resolution : resolutions[0];
    if (durations.length) params.duration = durations.includes(Number(old.duration)) ? Number(old.duration) : durations[0];
  }
  return {...config, model_id: model.id, params};
}
/** Text input never submits a hidden reference. Selecting image input requires a real reference. */
export function withNodeMode(config: NodeConfig, mode: GenerationMode): NodeConfig {
  const next: NodeConfig = {...config, generation_mode: mode};
  if (mode.startsWith('text_')) delete next.input_asset_id;
  return next;
}

/** Check the exact visible input before opening a quote. The backend remains authoritative. */
export function nodeGenerationIssue(kind: 'image'|'video', config: NodeConfig, model: Model|undefined): string|null {
  if (!textValue(config.prompt).trim()) return '先描述你想生成的画面。';
  if (!model || model.id !== config.model_id) return '选择一个生成模型。';
  const unavailable = validateModelSelection(model, kind);
  if (unavailable) return unavailable;
  const mode = textValue(config.generation_mode) || nodeModes(kind)[0];
  if (!nodeModes(kind).includes(mode as GenerationMode) || !model.capabilities?.input_modes?.includes(mode)) return '当前模型不支持这种输入方式，请重新选择。';
  if (mode.startsWith('image_') && !textValue(config.input_asset_id)) return '选择一张参考图片。';
  if (mode.startsWith('text_') && config.input_asset_id) return '请移除参考图片，或切换为图生模式。';
  const params = objectValue(config.params), ratio = textValue(params.ratio);
  if (!getSupportedRatios(model).includes(ratio)) return '选择模型支持的画面比例。';
  if (kind === 'image') return getImageSizesForRatio(model, ratio).includes(textValue(params.size)) ? null : '选择与画面比例对应的清晰度。';
  if (!getSupportedResolutions(model).includes(textValue(params.resolution))) return '选择模型支持的视频清晰度。';
  return validateVideoDuration(model, params.duration);
}
