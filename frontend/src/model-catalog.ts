import type { Model, Task } from './types';

export const isCloudModel = (model: Model): boolean => model.provider === 'ark' || model.provider === 'aihubmix';
export const isCloudTask = (task: Task): boolean => task.provider === 'ark' || task.provider === 'aihubmix'
  || task.model_id.startsWith('doubao-') || task.model_id.startsWith('aihubmix:');

/** Labels disambiguate the same model sold by different providers. */
export function modelDisplayName(model: Model): string {
  const provider = model.provider === 'aihubmix' ? 'AiHubMix' : model.provider === 'ark' ? '火山方舟' : '';
  return provider && !model.name.toLowerCase().includes(provider.toLowerCase()) ? `${model.name} · ${provider}` : model.name;
}

/** Ordering is only for menus and fresh selections; it never grants permission. */
export function orderCloudModels(models: Model[]): Model[] {
  const rank = (model: Model) => (model.submittability?.submittable === true ? 0 : 2) + (model.provider === 'aihubmix' ? 0 : 1);
  return models.filter(isCloudModel).sort((a, b) => rank(a) - rank(b));
}
