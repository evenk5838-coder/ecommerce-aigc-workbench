import type { CreativeRequestOptions } from './types';

/** Contract v0.10, S3–S6. Optional wire fields remain absent until the server supplies them. */
export type ProductionRequestOptions = CreativeRequestOptions;
export type GenerationMode = 'full' | 'clips';
export interface Storyboard {
  storyboard_id: string;
  shot_id: string;
  version: number;
  status: string;
  task_id?: string | null;
  asset_id?: string | null;
  reject_reason?: string | null;
  created_at?: number | null;
  approved?: boolean;
}
export interface ShotTake {
  take_id: string;
  shot_id?: string;
  task_id?: string | null;
  asset_id?: string | null;
  status: string;
  actual_duration_ms?: number | null;
  cost_cny?: number | null;
  created_at?: number | null;
}
export interface Shot {
  shot_id: string;
  project_id: string;
  revision: number;
  seq: number;
  duration_s: number;
  description: string;
  script_id?: string;
  prompt?: string;
  motion?: string | null;
  input_asset_id?: string | null;
  model_id?: string;
  status?: string;
  approved_at?: number | null;
  storyboards?: Storyboard[];
  takes?: ShotTake[];
}
export interface ShotInput {
  seq: number;
  duration_s: number;
  description: string;
  motion?: string;
  prompt?: string;
  input_asset_id?: string | null;
  model_id?: string;
}
export interface CreateShotsInput { script_id: string; shots: ShotInput[]; model_id?: string }
export interface ShotPatch extends Partial<ShotInput> { expected_revision: number }
/** Response shapes for CRUD/list must be supplied by Pi; no fabricated acknowledgement. */
export interface ShotsWriteResult { project_id: string; shot_ids: string[]; count: number }
export interface ShotDeleteResult { shot_id: string; deleted: true }
export interface ReorderResult { project_id: string; order: string[] }
export interface StoryboardInput {
  model_id: string; size: string; prompt: string; input_asset_id: string | null;
}
export interface StoryboardCreation {
  shot_id: string; storyboard_id: string; version: number; task_id: string;
  estimated_cost_cny: number;
}
export interface StoryboardReview {
  storyboard_id: string; version?: number; status: 'approved' | 'rejected';
  confirmation?: { confirmation_id: string; status: 'active'; object_type: 'storyboard'; object_id: string; object_version: number; reused: boolean };
  reason?: string; hint?: string;
  shot_id?: string; reject_reason?: string | null;
}
export interface ScopeSpecs { model_id: string; ratio: string; resolution: string }
export interface ScopeInput {
  mode: GenerationMode;
  shot_ids: string[];
  specs: ScopeSpecs;
  overrides?: Record<string, { duration_s: number }>;
}
export interface ScopeShotQuote { shot_id: string; duration_s: number; estimate_cny: number }
export interface ScopeQuote {
  scope_id: string;
  project_id?: string;
  mode: GenerationMode;
  shots: ScopeShotQuote[];
  total_estimate_cny: number;
  planned_film_duration_s: number;
  scope_hash: string;
  expires_at: number;
  side_effects: { tasks_created: 0; model_called: false };
  /** If supplied, a negative readiness result must prevent confirmation. */
  submittable?: boolean;
  blocked_reasons?: string[];
}
export interface ScopeApproval {
  scope_id: string; approved: true; expires_in_s: number; max_uses: 1;
  expires_at?: number;
  scope_hash?: string;
}
export interface ScopeTask { shot_id: string; task_id: string; take_id?: string }
export interface ScopeSubmission {
  batch_id: string; mode: GenerationMode; tasks: ScopeTask[];
  auto_film?: boolean; sequential: true;
  scope_id?: string;
  idempotent_replay?: boolean;
}
export interface ShotStatus {
  shot_id: string;
  status: string;
  take_id?: string | null;
  task_id?: string | null;
  asset_id?: string | null;
  actual_duration_ms?: number | null;
  error_code?: string | null;
  error_message?: string | null;
  takes?: ShotTake[];
  storyboards?: Storyboard[];
  /** Wire takes is a count; takes above contains only the explicitly returned latest take. */
  take_count?: number;
}
export interface FilmItem {
  shot_id: string; take_id: string; actual_duration_ms?: number | null;
}
export interface Film {
  film_id: string; status: string;
  project_id?: string;
  version?: number;
  planned_duration_s?: number | null;
  actual_duration_ms?: number | null;
  duration_ms?: number | null;
  items?: FilmItem[];
  partial?: boolean;
  missing_shot_ids?: string[];
  asset_id?: string | null;
  error_code?: string | null;
  error_message?: string | null;
}
export interface ProductionStatus {
  project_id: string;
  /** Backend uses this field for generation-stage status, not full/clips mode. */
  mode: GenerationMode | 'locked' | 'current' | 'done' | null;
  current_stage?: string;
  allowed_actions?: string[];
  shots: ShotStatus[];
  batch?: { batch_id: string; queued: number; running: number; succeeded: number; failed: number } | null;
  film?: Film | null;
  /** History is not synthesized from the latest film. */
  films?: Film[];
}
export interface FilmInput { take_ids: string[]; confirm_partial: boolean }
export interface FilmCreation { film_id: string; version: number; status: string; partial?: boolean; missing_shot_ids?: string[] }
export interface StructureShot {
  seq: number; role: string; duration_s: number; motion: string; rhythm: string;
}
export interface StructureTemplateInput { name: string; snapshot: StructureShot[] }
export interface StructureTemplateSummary { template_id: string; name: string; latest_version: number; status?: string }
export interface StructureTemplate { template_id: string; name: string; version: number; snapshot: StructureShot[] }
export interface StructureTemplateCreation { template_id: string; version: number }
export interface ApplyStructureInput { template_id: string; version: number }
export interface AppliedStructure {
  project_id: string; created_shots: number; requires: string[];
  created_shot_details: Array<{shot_id: string; seq: number; duration_s: number; role: string}>;
  model_revalidation: { ok: boolean; notes: string[] };
}
export type ExportPart = 'film' | 'clips' | 'storyboards' | 'manifest';
export interface ExportInput { include: ExportPart[] }
export interface ProjectExport {
  export_id: string; project_id: string; file_name: string; download_url: string;
  bytes: number; includes: ExportPart[]; films_included: number; clips_included: number; storyboards_included: number;
  missing: Array<{ kind: string; reason: string; film_id?: string; shot_id?: string }>;
  note?: string;
}

export interface ProductionApi {
  listShots(projectId: string, options?: ProductionRequestOptions): Promise<{ items: Shot[] }>;
  createShots(projectId: string, input: CreateShotsInput, options?: ProductionRequestOptions): Promise<ShotsWriteResult>;
  updateShot(shotId: string, input: ShotPatch, options?: ProductionRequestOptions): Promise<Shot>;
  deleteShot(shotId: string, options?: ProductionRequestOptions): Promise<ShotDeleteResult>;
  reorderShots(projectId: string, input: { order: string[] }, options?: ProductionRequestOptions): Promise<ReorderResult>;
  createStoryboard(shotId: string, input: StoryboardInput, options?: ProductionRequestOptions): Promise<StoryboardCreation>;
  approveStoryboard(storyboardId: string, options?: ProductionRequestOptions): Promise<StoryboardReview>;
  rejectStoryboard(storyboardId: string, reason: string, options?: ProductionRequestOptions): Promise<StoryboardReview>;
  createScope(projectId: string, input: ScopeInput, options?: ProductionRequestOptions): Promise<ScopeQuote>;
  confirmScope(quote: ScopeQuote, options?: ProductionRequestOptions): Promise<ScopeApproval>;
  submitScope(projectId: string, quote: ScopeQuote, approval: ScopeApproval, options?: ProductionRequestOptions): Promise<ScopeSubmission>;
  getStatus(projectId: string, options?: ProductionRequestOptions): Promise<ProductionStatus>;
  createFilm(projectId: string, input: FilmInput, options?: ProductionRequestOptions): Promise<FilmCreation>;
  listFilms(projectId: string, options?: ProductionRequestOptions): Promise<{ items: Film[] }>;
  getFilm(filmId: string, options?: ProductionRequestOptions): Promise<Film>;
  filmFileUrl(film: Pick<Film, 'asset_id'>): string;
  listStructureTemplates(options?: ProductionRequestOptions): Promise<{ items: StructureTemplateSummary[] }>;
  createStructureTemplate(input: StructureTemplateInput, options?: ProductionRequestOptions): Promise<StructureTemplateCreation>;
  getStructureTemplate(templateId: string, version?: number, options?: ProductionRequestOptions): Promise<StructureTemplate>;
  applyStructure(projectId: string, input: ApplyStructureInput, options?: ProductionRequestOptions): Promise<AppliedStructure>;
  exportProject(projectId: string, input: ExportInput, options?: ProductionRequestOptions): Promise<ProjectExport>;
}
