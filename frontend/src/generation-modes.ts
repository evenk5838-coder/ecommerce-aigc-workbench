import type { GenerationMode, Model, VideoMode } from './types';
import type { SingleTemplateSelection } from './single-templates';

export const GENERATION_MODES: Record<GenerationMode, { label: string; description: string }> = {
  text_to_image: { label: '文生图', description: '用文字描述商品、背景和构图，不发送参考图。' },
  image_to_image: { label: '图生图', description: '以商品原图为参考，描述希望修改的背景或呈现方式。' },
  text_to_video: { label: '文生视频', description: '用文字描述主体、场景和镜头，不发送首帧图片。' },
  image_to_video: { label: '图生视频', description: '从商品首帧出发，描述镜头运动和画面动作。' },
};

export const modesForKind = (kind: 'image' | 'video'): GenerationMode[] => kind === 'image'
  ? ['text_to_image', 'image_to_image'] : ['text_to_video', 'image_to_video'];
export const modeKind = (mode: GenerationMode): 'image' | 'video' => mode.endsWith('_video') ? 'video' : 'image';
export const modeNeedsReference = (mode: GenerationMode): boolean => mode === 'image_to_image' || mode === 'image_to_video';

/** Capability metadata is required. Display names and legacy `supports` never grant a mode. */
export function supportedGenerationModes(model: Model | undefined): GenerationMode[] {
  if (!model || !Array.isArray(model.capabilities?.input_modes)) return [];
  return modesForKind(model.kind).filter(mode => model.capabilities!.input_modes!.includes(mode));
}

export function validateGenerationMode(model: Model | undefined, mode: GenerationMode): string | null {
  if (!Object.hasOwn(GENERATION_MODES, mode)) return '请选择有效的生成方式';
  if (!supportedGenerationModes(model).includes(mode)) return `当前模型不支持所选${modeKind(mode) === 'image' ? '生图' : '视频'}方式（${GENERATION_MODES[mode].label}）`;
  return null;
}

/** A template pins its own input contract; the season template explicitly derives concept mode from the reference. */
export function templateGenerationMode(selection: SingleTemplateSelection, reference: string | null): GenerationMode | null {
  const template = selection.template;
  if (template.template_id === 'tpl_season_theme' && template.kind === 'image') return reference ? 'image_to_image' : 'text_to_image';
  const mode = template.default_params.generation_mode;
  return typeof mode === 'string' && modesForKind(template.kind).includes(mode as GenerationMode) ? mode as GenerationMode : null;
}

/** Remove only after task estimate/create/batch/retry accept and preserve text_to_video without a reference. */
export const TEXT_TO_VIDEO_SERVICE_READY = false;
export function workbenchModeIntegrationError(mode: GenerationMode): string | null {
  return mode === 'text_to_video' && !TEXT_TO_VIDEO_SERVICE_READY
    ? '文生视频服务接入待完成：后端提交入口仍要求首帧图，暂不能提交。可以先保存描述，或切换图生视频。'
    : null;
}

export function getVideoPrompt(mode: VideoMode): string {
  return mode === 'text_to_video'
    ? '生成一段电商商品展示视频。请描述商品的外观、场景、光线，以及镜头如何运动。'
    : '镜头缓慢推近商品，柔和光线掠过表面，保持商品形态和包装稳定，适合电商商品展示。';
}
