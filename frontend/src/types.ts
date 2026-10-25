export interface Asset {
  id: string; kind: 'image' | 'video'; mime: string; size: number;
  width: number; height: number; source: string; created_at: number;
  meta_json: string; duration_ms?: number; fps?: number; frames?: number;
}
export type DurationPolicy =
  | { kind: 'range'; min: number; max: number; step: number; allowed?: null }
  | { kind: 'enum'; min?: null; max?: null; step?: null; allowed: number[] }
  | { kind: 'unavailable'; min?: null; max?: null; step?: null; allowed?: null };
export interface ModelCapabilities {
  input_modes?: string[]; ratios?: string[]; resolutions?: string[]; sizes?: string[];
  /** Compatibility fields only: duration_policy is the sole frontend authority. */
  duration_s?: number[]; duration_range_s?: number[] | null; duration_step_s?: number | null;
  duration_policy?: DurationPolicy | null;
  max_reference_images?: number | null; supports?: string[]; fps?: number | null;
  limits?: Record<string, number> | null;
}
export interface ModelLifecycle {
  status: 'active' | 'eos_scheduled' | 'eos_expired';
  eos_at?: string | null; eom_at?: string | null; migrate_to?: string | null;
  migrate_to_activation?: string | null; announce_url?: string | null;
  seconds_remaining?: number; eom_passed?: boolean; action?: string | null;
}
/** Informational fields do not override the backend's explicit permission. */
export interface ModelSubmittability {
  submittable: boolean;
  reasons: string[];
  activation?: string; lifecycle_status?: string; duration_policy_kind?: string | null;
}
export interface Model {
  provider?: 'ark' | 'local';
  activation?: 'verified' | 'not_opened' | 'local_history' | 'unknown';
  capabilities?: ModelCapabilities; lifecycle?: ModelLifecycle;
  submittability?: ModelSubmittability | null;
  id: string; name: string; kind: 'image' | 'video'; downloaded: boolean;
  loaded: boolean; weights_mb: number; note?: string;
}
export interface Task {
  provider?: string; cost_cny?: number; remote_task_id?: string; remote_status?: string;
  id: string; kind: 'image' | 'video'; model_id: string; status: string;
  params_json: string; progress: number; stage: string; created_at: number;
  output_asset_id?: string; error_code?: string; error_message?: string;
  latency_ms?: number; load_ms?: number; peak_mem_mb?: number;
}
export interface SystemStatus {
  memory: { budget_mb: number; mps_alloc_mb?: number; mps_driver_mb?: number; sys_used_mb?: number };
  loaded_model: string | null; queue: { queued: number; active: number }; concurrency: number;
}
export interface Layer {
  id: string; type: 'image' | 'text' | 'shape'; name: string;
  x: number; y: number; w: number; h: number; rotation: number; opacity: number;
  assetId?: string; text?: string; fontSize?: number; color?: string; fill?: string;
}
export interface CanvasDocument {
  width: number; height: number; background: string; layers: Layer[];
}
export interface Project {
  assistantProjectId?: string;
  name: string; canvas: CanvasDocument; assetGroups: Record<string, string>;
  generation?: GenerationSettings;
}
export interface GenerationSettings {
  kind: 'image' | 'video'; imageMode: 'text_to_image' | 'image_to_image';
  purpose: 'white' | 'scene' | 'custom'; imagePrompt: string; videoPrompt: string;
  imageRatio: string; videoRatio: string; imageReference: string | null; videoReference: string | null;
  batchCount: number; duration: number; imageSize?: string; videoResolution?: string;
}
export interface Draft {
  id: string; name: string; version: number;
  content: { canvas: CanvasDocument; assetGroups?: Record<string, string>; generation?: GenerationSettings; meta?: { name?: string; assistantProjectId?: string } };
}
export interface TaskInput {
  kind: 'image' | 'video'; model_id: string; params: Record<string, unknown>;
  input_asset_id?: string | null; client_request_id: string;
}

/** Estimate payloads may be incomplete at runtime; explicit permission is required before confirmation. */
export interface TaskEstimate {
  submittable?: boolean | null;
  blocked_reasons?: string[] | null;
  estimated_cost_cny?: number | null;
  effective?: Record<string, unknown> | null;
  kind?: 'image' | 'video'; model_id?: string;
}
