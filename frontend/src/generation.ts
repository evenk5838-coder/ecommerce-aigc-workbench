import type { Asset, Model, TaskInput } from './types';
import { getDefaultImageSize, getImageSizesForRatio, getSupportedRatios, getSupportedResolutions, validateModelSelection, validateVideoDuration } from './capabilities';

export type ImageMode = 'text_to_image' | 'image_to_image';
export type ImagePurpose = 'white' | 'scene' | 'custom';

export interface GenerationOptions {
  kind: 'image' | 'video';
  imageMode: ImageMode;
  modelId: string;
  prompt: string;
  reference?: Asset;
  ratio: string;
  duration: number;
  /** Keep old call sites source-compatible; missing runtime metadata blocks submission. */
  model?: Model;
  resolution?: string;
  imageSize?: string;
}

const preserveReference = '以参考图中实际出现的商品为准，保留所有商品及其数量、相对位置、形状比例、材质、颜色、品牌标识和包装文字，不增加或删除商品。';

export function getImagePrompt(mode: ImageMode, purpose: ImagePurpose): string {
  if (mode === 'image_to_image') {
    if (purpose === 'white') return `${preserveReference}将背景替换为干净的纯白背景，柔和棚拍光线，自然接触阴影，商品完整清晰，适合电商商品展示。`;
    if (purpose === 'scene') return `${preserveReference}将背景替换为简洁的生活方式场景，柔和自然光，背景不遮挡商品，突出商品质感，构图留白。`;
    return `${preserveReference}请描述希望修改的背景、光线或呈现方式。`;
  }
  if (purpose === 'white') return '生成一张电商商品展示图，纯白背景，柔和棚拍光线，自然接触阴影，商品完整清晰，构图留白。请补充具体商品及外观描述。';
  if (purpose === 'scene') return '生成一张电商商品场景图，简洁的生活方式场景，柔和自然光，背景不遮挡商品，突出商品质感，构图留白。请补充具体商品及场景描述。';
  return '';
}

function requireImage(reference: Asset | undefined, label: string): Asset {
  if (!reference?.id) throw new Error(`请先选择或上传${label}`);
  if (reference.kind !== 'image' || !reference.mime.startsWith('image/')) {
    throw new Error(`${label}必须是图片，请重新选择`);
  }
  if (!Number.isFinite(reference.size) || reference.size <= 0) {
    throw new Error(`${label}文件无效，请重新上传`);
  }
  if (reference.size > 10 * 1024 * 1024) {
    throw new Error(`${label}不能超过 10 MB，请压缩后重新上传`);
  }
  return reference;
}

/** Build one cloud task; the caller adds a unique request ID per submission. */
export function buildGenerationInput(options: GenerationOptions): Omit<TaskInput, 'client_request_id'> {
  const { kind, imageMode, modelId, reference, ratio, duration } = options;
  const prompt = options.prompt.trim();
  if (!prompt) throw new Error('请先填写生成描述');
  if (!modelId.trim()) throw new Error('请先选择可用模型');
  const model = options.model;
  const modelError = validateModelSelection(model, kind);
  if (modelError) throw new Error(modelError);
  if (model?.id !== modelId) throw new Error('模型信息与所选模型不一致，请刷新后重新选择');
  if (!getSupportedRatios(model).includes(ratio)) throw new Error('当前模型不支持所选画面比例，请重新选择');

  if (kind === 'video') {
    const image = requireImage(reference, '视频首帧图片');
    if (!model.capabilities?.input_modes?.includes('image_to_video')) throw new Error('当前模型未提供首帧生视频能力');
    const durationError = validateVideoDuration(model, duration);
    if (durationError) throw new Error(durationError);
    const resolution = options.resolution ?? '720p';
    if (!getSupportedResolutions(model).includes(resolution)) throw new Error('当前模型不支持所选视频清晰度，请重新选择');
    return {
      kind, model_id: modelId, input_asset_id: image.id,
      params: { prompt, duration, ratio, resolution, generate_audio: false },
    };
  }

  if (imageMode !== 'text_to_image' && imageMode !== 'image_to_image') {
    throw new Error('请选择文生图或图生图');
  }
  if (!model.capabilities?.input_modes?.includes(imageMode)) throw new Error('当前模型不支持所选生图方式');
  const size = options.imageSize ?? getDefaultImageSize(model, ratio);
  if (!size || !getImageSizesForRatio(model, ratio).includes(size)) throw new Error('当前图片尺寸不支持所选比例，请重新选择输出规格');
  const inputAssetId = imageMode === 'image_to_image' ? requireImage(reference, '参考图').id : null;
  return {
    kind, model_id: modelId, input_asset_id: inputAssetId,
    params: { prompt, generation_mode: imageMode, size },
  };
}
