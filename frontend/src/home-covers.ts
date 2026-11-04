import type { Asset, Task } from './types';

type HomeCovers = { director?: Asset; quick?: Asset };

/** Use completed task outputs, never an uploaded reference or a guessed project association. */
export function homeCovers(assets: Asset[], tasks: Task[], projectIds: string[]): HomeCovers {
  const images = new Map(assets.filter(asset => asset.kind === 'image'
    && ['generated', 'cloud_generated'].includes(asset.source)).map(asset => [asset.id, asset]));
  const projects = new Set(projectIds);
  const covers: HomeCovers = {};
  const completedAt = (task: Task) => Number.isFinite(task.finished_at) && (task.finished_at ?? 0) > 0
    ? task.finished_at! : task.created_at;
  for (const task of [...tasks].sort((a, b) => completedAt(b) - completedAt(a))) {
    if (task.status !== 'succeeded' || task.kind !== 'image' || !task.output_asset_id || task.model_id === 'fake-image') continue;
    const image = images.get(task.output_asset_id);
    if (!image) continue;
    if (['director', 'canvas', 'creative'].includes(task.caller_id ?? '')
      && projects.has(task.external_request_id ?? '')) covers.director ??= image;
    // Legacy "local" tasks also include old storyboards; their origin is ambiguous.
    if (['workbench', 'assistant'].includes(task.caller_id ?? '')) covers.quick ??= image;
    if (covers.director && covers.quick) break;
  }
  return covers;
}
