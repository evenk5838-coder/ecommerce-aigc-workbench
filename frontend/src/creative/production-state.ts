import { validateSpecCombination } from '../model-specs';
import { getModelUnavailableReason, getSupportedImageSizes, imageSizeRatio, validateVideoDuration } from '../capabilities';
import { withDefaultNodeModel, objectValue, textValue } from './node-config';
import { validateGenerationMode } from '../generation-modes';
import type { Model, Task } from '../types';
import type { Film, ProductionStatus, ScopeInput, Shot, ShotInput, ShotTake, StoryboardInput } from './production-types';

export interface ShotDraft {
  key: string; shot_id?: string; revision?: number;
  description: string; motion: string; prompt: string; input_asset_id: string; duration: string;
  /** Browser-persisted node overrides; these are not shot API fields. */
  storyboardModelId?: string; storyboardSize?: string;
}
export interface ProductionEditorState {
  version: 1; rows: ShotDraft[]; videoModelId: string; ratio: string; resolution: string;
  imageModelId: string; imageSize: string; mode: 'full' | 'clips'; selectedIds: string[];
  /** Last acknowledged plan, separate from editable rows and generation settings. */
  savedPlan?: SavedShotPlan[];
}
export interface SavedShotPlan {
  shot_id: string; seq: number; description: string; motion: string; duration_s: number;
}
export type ProductionModelSelection = Pick<ProductionEditorState, 'videoModelId'|'ratio'|'resolution'|'imageModelId'|'imageSize'>;
/** Called once after both resources and the plan are read; refreshes do not select a new provider. */
export function initialProductionSelection(local: ProductionModelSelection | null, shots: Shot[], models: Model[]): ProductionModelSelection {
  if (local) return { videoModelId: local.videoModelId, ratio: local.ratio, resolution: local.resolution, imageModelId: local.imageModelId, imageSize: local.imageSize };
  if (shots.length) {
    const oldModels = [...new Set(shots.map(shot => shot.model_id).filter((id): id is string => !!id))];
    return { videoModelId: oldModels.length === 1 ? oldModels[0] : '', ratio: '', resolution: '', imageModelId: '', imageSize: '' };
  }
  const video = withDefaultNodeModel({ generation_mode: 'image_to_video' }, 'video', models);
  const image = withDefaultNodeModel({ generation_mode: 'text_to_image' }, 'image', models);
  const videoParams = objectValue(video.params), imageParams = objectValue(image.params);
  return { videoModelId: textValue(video.model_id), ratio: textValue(videoParams.ratio), resolution: textValue(videoParams.resolution), imageModelId: textValue(image.model_id), imageSize: textValue(imageParams.size) };
}
export function savedShotPlan(shots: readonly Shot[]): SavedShotPlan[] {
  return shots.map(shot => ({ shot_id: shot.shot_id, seq: shot.seq, description: shot.description,
    motion: shot.motion ?? '', duration_s: shot.duration_s }));
}

/** Missing legacy baselines stay conservative; loading is never a reason to drop edits. */
export function shotPlanChanged(rows: ShotDraft[], saved: readonly SavedShotPlan[] | undefined): boolean {
  if (!saved) return rows.length > 0;
  return rows.some(row => shotDraftChanged(row, saved.find(shot => shot.shot_id === row.shot_id))) || planOrderChanged(rows, saved);
}
const ACTIVE = new Set(['queued', 'loading', 'running', 'postprocess', 'remote_queued', 'remote_running', 'cancel_requested', 'pending', 'composing']);
export const isActiveStatus = (value: string | undefined): boolean => !!value && ACTIVE.has(value);
export function draftFromShot(shot: Shot): ShotDraft {
  return { key: shot.shot_id, shot_id: shot.shot_id, revision: shot.revision, description: shot.description, motion: shot.motion ?? '', prompt: shot.prompt ?? '', input_asset_id: shot.input_asset_id ?? '', duration: String(shot.duration_s) };
}
/** Preserve local node inputs when refreshing the fields persisted by the shot API. */
export function restoreShotDraft(shot: Shot, local?: ShotDraft): ShotDraft {
  return {
    ...draftFromShot(shot), prompt: local?.prompt ?? shot.prompt ?? '', input_asset_id: local?.input_asset_id ?? shot.input_asset_id ?? '',
    ...(local?.storyboardModelId !== undefined ? { storyboardModelId: local.storyboardModelId } : {}),
    ...(local?.storyboardSize !== undefined ? { storyboardSize: local.storyboardSize } : {}),
  };
}
export interface StoryboardDefaults { imageModelId: string; imageSize: string }
/** Missing overrides inherit defaults; an explicit empty selection stays empty. */
export function storyboardSettings(draft: ShotDraft | undefined, defaults: StoryboardDefaults): { modelId: string; size: string } {
  return { modelId: draft?.storyboardModelId ?? defaults.imageModelId, size: draft?.storyboardSize ?? defaults.imageSize };
}
export function inspectStoryboardDraft(draft: ShotDraft, defaults: StoryboardDefaults, models: Model[]): { input: StoryboardInput | null; error: string | null } {
  const settings = storyboardSettings(draft, defaults);
  const model = models.find(item => item.id === settings.modelId);
  const fail = (error: string) => ({ input: null, error });
  const unavailable = getModelUnavailableReason(model);
  if (unavailable) return fail(unavailable);
  if (model?.kind !== 'image') return fail('请选择分镜图片模型。');
  const modeError = validateGenerationMode(model, draft.input_asset_id ? 'image_to_image' : 'text_to_image');
  if (modeError) return fail(modeError);
  if (!getSupportedImageSizes(model).includes(settings.size)) return fail('请选择这个图片模型支持的比例和清晰度。');
  const specError = validateSpecCombination(model, {generation_mode: draft.input_asset_id ? 'image_to_image' : 'text_to_image', ratio: imageSizeRatio(settings.size), size: settings.size});
  if (specError) return fail(specError);
  if (!draft.prompt.trim()) return fail('请填写这条分镜的生成提示词。');
  return { input: { model_id: settings.modelId, size: settings.size, prompt: draft.prompt, input_asset_id: draft.input_asset_id || null }, error: null };
}
export function shotDraftChanged(draft: ShotDraft, shot?: Shot | Pick<Shot, 'description' | 'motion' | 'duration_s'>): boolean {
  if (!shot) return true;
  const original = { description: shot.description, motion: shot.motion ?? '', duration: String(shot.duration_s) };
  return (['description', 'motion', 'duration'] as const).some(key => draft[key] !== original[key]);
}
export function inspectShotDraft(draft: ShotDraft, model: Model | undefined, seq: number): { input: ShotInput | null; error: string | null } {
  const fail = (error: string) => ({ input: null, error });
  const unavailable = getModelUnavailableReason(model);
  if (unavailable) return fail(unavailable);
  if (model?.kind !== 'video') return fail('请选择视频模型后规划每镜时长。');
  if (!draft.description.trim()) return fail('请填写这条镜头的画面描述。');
  if (!/^\d+$/.test(draft.duration.trim())) return fail('镜头时长需为模型支持的整数秒，不能输入小数。');
  const duration = Number(draft.duration);
  const durationError = validateVideoDuration(model, duration);
  if (durationError) return fail(durationError);
  if (!Number.isSafeInteger(seq) || seq < 1) return fail('镜头顺序无效。');
  return { input: { seq, duration_s: duration, description: draft.description, motion: draft.motion, prompt: draft.prompt, input_asset_id: draft.input_asset_id || null, model_id: model.id }, error: null };
}
export function planOrderChanged(rows: ShotDraft[], shots: readonly Pick<Shot, 'shot_id' | 'seq'>[]): boolean {
  return rows.map(row => row.shot_id ?? row.key).join('|') !== [...shots].sort((a,b) => a.seq-b.seq).map(shot => shot.shot_id).join('|');
}
export function failedShotIds(status: ProductionStatus | null): string[] {
  return status?.shots.filter(shot => shot.status === 'failed').map(shot => shot.shot_id) ?? [];
}
export function successfulTakes(shot: Shot, status: ProductionStatus | null): ShotTake[] {
  const current = status?.shots.find(item => item.shot_id === shot.shot_id);
  const candidates = [...(shot.takes ?? []), ...(current?.takes ?? [])];
  if (current?.take_id && current.asset_id && current.status === 'succeeded') candidates.push({ take_id: current.take_id, shot_id: shot.shot_id, task_id: current.task_id, asset_id: current.asset_id, status: current.status, actual_duration_ms: current.actual_duration_ms });
  return [...new Map(candidates.filter(take => take.status === 'succeeded' && typeof take.asset_id === 'string' && take.asset_id.length > 0).map(take => [take.take_id, take])).values()];
}
/** Fingerprint invalidates UI approval; it never substitutes for the server scope_hash. */
export function productionFingerprint(projectId: string, scriptId: string | null, brief: unknown, rows: ShotDraft[], shots: Shot[], input: ScopeInput): string {
  return JSON.stringify({ projectId, scriptId, brief, rows, shots: shots.map(shot => ({ id: shot.shot_id, revision: shot.revision, storyboards: shot.storyboards?.map(board => ({ id: board.storyboard_id, version: board.version, status: board.status })) })), input });
}
export function hasActiveProduction(status: ProductionStatus | null, tasks: Task[], trackedTaskIds: string[]): boolean {
  if (status?.batch && (status.batch.queued > 0 || status.batch.running > 0)) return true;
  if (status?.shots.some(shot => isActiveStatus(shot.status))) return true;
  if (isActiveStatus(status?.film?.status) || status?.films?.some(film => isActiveStatus(film.status))) return true;
  return trackedTaskIds.some(id => { const task = tasks.find(item => item.id === id); return !task || isActiveStatus(task.status); });
}
export function unionFilms(known: Film[], status: ProductionStatus | null): Film[] {
  return [...new Map([...known, ...(status?.films ?? []), ...(status?.film ? [status.film] : [])].map(film => [film.film_id, film])).values()].sort((a,b) => (b.version ?? 0) - (a.version ?? 0));
}
function parseSavedPlan(value: unknown): SavedShotPlan[] | undefined {
  if (!Array.isArray(value)) return;
  const ids = new Set<string>(), sequences = new Set<number>(), result: SavedShotPlan[] = [];
  for (const item of value) {
    if (!item || typeof item !== 'object' || Array.isArray(item)) return;
    const shot = item as Record<string, unknown>;
    if (typeof shot.shot_id !== 'string' || !shot.shot_id.trim() || ids.has(shot.shot_id)
      || typeof shot.seq !== 'number' || !Number.isSafeInteger(shot.seq) || shot.seq < 1 || sequences.has(shot.seq)
      || typeof shot.description !== 'string' || typeof shot.motion !== 'string'
      || typeof shot.duration_s !== 'number' || !Number.isFinite(shot.duration_s) || shot.duration_s <= 0) return;
    ids.add(shot.shot_id); sequences.add(shot.seq);
    result.push({ shot_id: shot.shot_id, seq: shot.seq, description: shot.description, motion: shot.motion, duration_s: shot.duration_s });
  }
  return result;
}
export function parseProductionEditor(value: string | null): ProductionEditorState | null {
  if (!value) return null;
  try {
    const parsed: unknown = JSON.parse(value);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null;
    const data = parsed as Record<string, unknown>;
    if (data.version !== 1 || !Array.isArray(data.rows) || !Array.isArray(data.selectedIds) || !data.selectedIds.every(id => typeof id === 'string') || (data.mode !== 'full' && data.mode !== 'clips')) return null;
    if (!['videoModelId','ratio','resolution','imageModelId','imageSize'].every(key => typeof data[key] === 'string')) return null;
    for (const row of data.rows) {
      if (!row || typeof row !== 'object' || !['key','description','motion','prompt','input_asset_id','duration'].every(key => typeof row[key] === 'string') || (row.shot_id !== undefined && typeof row.shot_id !== 'string') || (row.revision !== undefined && (!Number.isSafeInteger(row.revision) || row.revision < 1))) return null;
      if (['storyboardModelId', 'storyboardSize'].some(key => row[key] !== undefined && typeof row[key] !== 'string')) return null;
    }
    const savedPlan = parseSavedPlan(data.savedPlan);
    return { version:1, rows:data.rows as ShotDraft[], videoModelId:data.videoModelId as string, ratio:data.ratio as string, resolution:data.resolution as string, imageModelId:data.imageModelId as string, imageSize:data.imageSize as string, mode:data.mode, selectedIds:data.selectedIds as string[], ...(savedPlan ? { savedPlan } : {}) };
  } catch { return null; }
}

/** Read live state after confirmation resolves; a canceled or changed scope never dispatches paid work. */
export interface ScopeContinuationState {
  currentQuote: object | null; fingerprint: string; blocked: boolean; retryOnly: boolean;
  selectedStillFailed: boolean; mounted: boolean; aborted: boolean;
}
export async function submitAfterScopeConfirmation<Approval, Result>(
  frozen: { quote: object; fingerprint: string; retryOnly: boolean },
  confirm: () => Promise<Approval>,
  readCurrent: () => ScopeContinuationState,
  submit: (approval: Approval) => Promise<Result>,
): Promise<Result> {
  const approval = await confirm();
  const current = readCurrent();
  if (!current.mounted || current.aborted || current.currentQuote !== frozen.quote || current.blocked || current.fingerprint !== frozen.fingerprint || current.retryOnly !== frozen.retryOnly || (frozen.retryOnly && !current.selectedStillFailed)) {
    throw new Error('确认已取消，或输入、模型许可、上游状态和失败镜选择已变化。已停止提交，请重新核对并报价。');
  }
  return submit(approval);
}
