export interface Asset {
  id: string; kind: 'image' | 'video'; mime: string; size: number;
  width: number; height: number; source: string; created_at: number;
  meta_json: string; duration_ms?: number; fps?: number; frames?: number;
}
export interface Model {
  provider?: 'ark' | 'local';
  activation?: 'verified' | 'not_opened' | 'local_history' | 'unknown';
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
  name: string; canvas: CanvasDocument; assetGroups: Record<string, string>;
  generation?: GenerationSettings;
}
export interface GenerationSettings {
  kind: 'image' | 'video'; imageMode: 'text_to_image' | 'image_to_image';
  purpose: 'white' | 'scene' | 'custom'; imagePrompt: string; videoPrompt: string;
  imageRatio: string; videoRatio: string; imageReference: string | null; videoReference: string | null;
  batchCount: number; duration: number;
}
export interface Draft {
  id: string; name: string; version: number;
  content: { canvas: CanvasDocument; assetGroups?: Record<string, string>; generation?: GenerationSettings; meta?: { name?: string } };
}
export interface TaskInput {
  kind: 'image' | 'video'; model_id: string; params: Record<string, unknown>;
  input_asset_id?: string | null; client_request_id: string;
}
