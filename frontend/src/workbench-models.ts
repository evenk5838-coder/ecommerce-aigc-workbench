import type { GenerationMode, GenerationSettings, Model } from './types';
import { getDefaultImageSize, getImageSizesForRatio, getModelUnavailableReason, getSupportedDurations, getSupportedRatios, getSupportedResolutions, validateGenerationSpecs } from './capabilities';
import { supportedGenerationModes } from './generation-modes';
import { orderCloudModels } from './model-catalog';

export function initialWorkbenchModel(models: Model[], settings: GenerationSettings, mode: GenerationMode, restored: boolean): Model | undefined {
  const choices = orderCloudModels(models).filter(model => model.kind === settings.kind && !getModelUnavailableReason(model) && supportedGenerationModes(model).includes(mode));
  if (!restored) return choices[0];
  // Old drafts did not save a model ID. Match their existing Ark specification;
  // do not migrate them to another provider or normalize their saved inputs.
  return choices.find(model => model.provider === 'ark' && !validateGenerationSpecs(model, settings.kind,
    settings.kind === 'image'
      ? { generation_mode: mode, ratio: settings.imageRatio, size: settings.imageSize ?? getDefaultImageSize(model, settings.imageRatio) }
      : { generation_mode: mode, ratio: settings.videoRatio, resolution: settings.videoResolution ?? '720p', duration: settings.duration, generate_audio: false }));
}

const prefer = <T,>(values: T[], current: T): T[] => values.includes(current) ? [current, ...values.filter(value => value !== current)] : values;

/** Only call for explicit model changes or brand-new creations, never polling. */
export function settingsForWorkbenchModel(model: Model, settings: GenerationSettings, mode: GenerationMode): Partial<GenerationSettings> {
  const selected = model.kind === 'image' ? { imageModelId: model.id } : { videoModelId: model.id };
  const selection = { generation_mode: mode, ...(model.kind === 'video' ? { generate_audio: false } : {}) };
  const currentRatio = model.kind === 'image' ? settings.imageRatio : settings.videoRatio;
  for (const ratio of prefer(getSupportedRatios(model, selection), currentRatio)) {
    if (model.kind === 'image') {
      const sizes = prefer(getImageSizesForRatio(model, ratio, selection), settings.imageSize ?? getDefaultImageSize(model, ratio) ?? '');
      if (sizes.length) return { ...selected, imageRatio: ratio, imageSize: sizes[0] };
    } else {
      for (const resolution of prefer(getSupportedResolutions(model, { ...selection, ratio }), settings.videoResolution ?? '720p')) {
        const durations = prefer(getSupportedDurations(model, { ...selection, ratio, resolution }), settings.duration);
        if (durations.length) return { ...selected, videoRatio: ratio, videoResolution: resolution, duration: durations[0] };
      }
    }
  }
  return selected;
}
