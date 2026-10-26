import { describe, expect, it } from 'vitest';
import { getVideoPrompt, modeKind, modeNeedsReference, supportedGenerationModes, templateGenerationMode, validateGenerationMode, workbenchModeIntegrationError } from './generation-modes';
import type { GenerationMode, Model } from './types';
import type { SingleTemplateSelection } from './single-templates';

const model: Model = { id: 'test-model', name: '测试模型', kind: 'video', provider: 'ark', downloaded: true, loaded: false, weights_mb: 0,
  capabilities: { input_modes: ['image_to_video', 'text_to_video'] } };

describe('四种输入方式与运行能力', () => {
  it.each<[GenerationMode, 'image' | 'video', boolean]>([
    ['text_to_image', 'image', false], ['image_to_image', 'image', true],
    ['text_to_video', 'video', false], ['image_to_video', 'video', true],
  ])('%s 使用对应生成类型和参考图约束', (mode, kind, reference) => {
    expect(modeKind(mode)).toBe(kind);
    expect(modeNeedsReference(mode)).toBe(reference);
  });
  it('只采用 input_modes 中同类的已知能力，不从名称或 supports 猜测', () => {
    expect(supportedGenerationModes(model)).toEqual(['text_to_video', 'image_to_video']);
    expect(supportedGenerationModes({ ...model, capabilities: { input_modes: ['image_to_video', 'image_to_video', 'text_to_image', 'future_mode'] } })).toEqual(['image_to_video']);
    expect(supportedGenerationModes({ ...model, capabilities: { supports: ['文生视频', '首帧生视频'] } })).toEqual([]);
    expect(supportedGenerationModes(undefined)).toEqual([]);
    expect(validateGenerationMode(model, 'image_to_image')).toContain('不支持');
  });
  it('四模式服务已接入，文生视频由当前模型 input_modes 决定，不再静态禁用', () => {
    expect(validateGenerationMode(model, 'text_to_video')).toBeNull();
    expect(workbenchModeIntegrationError('text_to_video')).toBeNull();
    expect(workbenchModeIntegrationError('image_to_video')).toBeNull();
    expect(workbenchModeIntegrationError('text_to_image')).toBeNull();
    expect(workbenchModeIntegrationError('image_to_image')).toBeNull();
    const firstFrameOnly = { ...model, capabilities: { input_modes: ['image_to_video'] } };
    expect(validateGenerationMode(firstFrameOnly, 'text_to_video')).toContain('不支持');
    expect(validateGenerationMode(firstFrameOnly, 'image_to_video')).toBeNull();
    expect(validateGenerationMode({ ...model, capabilities: {} }, 'text_to_video')).toContain('不支持');
    expect(validateGenerationMode(undefined, 'text_to_video')).toContain('不支持');
  });
  it('未知模式仍在服务兼容入口和模型能力校验中拒绝', () => {
    const unknownMode = 'future_video' as GenerationMode;
    expect(workbenchModeIntegrationError(unknownMode)).toContain('有效的生成方式');
    expect(validateGenerationMode(model, unknownMode)).toContain('有效的生成方式');
  });
  it('文生视频预设要求描述主体，图生视频预设保留商品形态', () => {
    expect(getVideoPrompt('text_to_video')).toContain('描述商品的外观');
    expect(getVideoPrompt('image_to_video')).toContain('保持商品形态和包装稳定');
  });
  it('模板输入方式来自固定版本默认值，季节概念模式明确区分有图和无图', () => {
    const selection: SingleTemplateSelection = { template: { template_id: 'tpl_white_bg', latest_version: 1, kind: 'image', name: '白底',
      category: 'work', status: 'active', fields: [], required_assets: [{ required: true }], default_params: { generation_mode: 'image_to_image' }, compatible_model_ids: [] }, fields: {}, conceptAcknowledged: false };
    expect(templateGenerationMode(selection, 'product-ref')).toBe('image_to_image');
    expect(templateGenerationMode({ ...selection, template: { ...selection.template, default_params: {} } }, null)).toBeNull();
    const season = { ...selection, template: { ...selection.template, template_id: 'tpl_season_theme' } };
    expect(templateGenerationMode(season, 'product-ref')).toBe('image_to_image');
    expect(templateGenerationMode(season, null)).toBe('text_to_image');
    const video = { ...selection, template: { ...selection.template, kind: 'video' as const, default_params: { generation_mode: 'image_to_video' } } };
    expect(templateGenerationMode(video, 'product-ref')).toBe('image_to_video');
    expect(templateGenerationMode({ ...video, template: { ...video.template, default_params: { generation_mode: 'text_to_image' } } }, null)).toBeNull();
  });
});
