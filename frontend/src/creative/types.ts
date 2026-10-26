export type ProjectBrief = Partial<Record<
  'subject' | 'quantity' | 'selling_points' | 'forbidden_elements' |
  'target_platform' | 'target_duration_s' | 'style' | 'notes', unknown
>>;

export interface ScriptContent {
  summary?: string;
  shots?: unknown[];
  [key: string]: unknown;
}

export interface CreativeProject {
  project_id: string;
  name: string;
  brief: ProjectBrief;
  /** A server status is historical data, not proof that confirmation is still valid. */
  status: string | null;
  structure_template_id?: string | null;
  structure_template_version?: number | null;
  created_at?: number | null;
  updated_at?: number | null;
}
export interface CreativeProjectListItem extends CreativeProject { script_count: number }
export interface CreativeProjectDetail extends CreativeProjectListItem { latest_script: CreativeScriptSummary | null }
export interface CreativeProjectUpdate extends CreativeProject { stale: string[] }
export interface CreativeProjectPatch { name?: string; brief?: ProjectBrief }

export interface CreativeScriptSummary {
  script_id: string;
  version: number;
  source: string | null;
  /** Historical timestamp only. Brief changes can invalidate confirmation without removing it. */
  confirmed_at: number | null;
}
/** Manual creation returns this partial acknowledgement; do not synthesize missing script fields. */
export interface ScriptWriteResult {
  script_id: string;
  version: number;
  content: ScriptContent;
  source: string | null;
}
export interface CreativeScript extends ScriptWriteResult, CreativeScriptSummary {
  project_id: string;
  created_at?: number | null;
}
export interface ScriptGenerationInput { instruction?: string; target_duration_s?: number }
export interface ManualScriptInput {
  content: ScriptContent;
  /** Sent as supplied; the S2 manual-create route currently does not enforce it. */
  base_version?: number;
}
export interface ScriptEditInput { content: ScriptContent; base_version: number }
export type ScriptGenerationResult =
  | { assistant_unavailable: true; hint: string; project_id: string }
  | (ScriptWriteResult & {
    assistant_unavailable: false;
    source: 'assistant';
    llm_usage: { input_tokens: number; output_tokens: number };
  });
export interface ScriptConfirmation {
  script_id: string;
  version: number;
  confirmed_at: number;
  unlocked: string[];
}
export interface CreativeRequestOptions { signal?: AbortSignal; timeoutMs?: number }
export type CreativeUnknownReason = 'timeout' | 'aborted' | 'network' | 'response_invalid' | 'server_error';
export interface CreativeResultUnknownDetail {
  reason: CreativeUnknownReason;
  method: string;
  path: string;
  original?: { code: string; status: number; message: string; detail?: unknown };
}

export interface CreativeApi {
  listProjects(limit?: number, options?: CreativeRequestOptions): Promise<{ items: CreativeProjectListItem[] }>;
  deleteProject(id: string, options?: CreativeRequestOptions): Promise<{ project_id: string; deleted: true; deleted_at: number; already_deleted: boolean }>;
  getProject(id: string, options?: CreativeRequestOptions): Promise<CreativeProjectDetail>;
  createProject(name: string, brief: ProjectBrief, options?: CreativeRequestOptions): Promise<CreativeProject>;
  updateProject(id: string, patch: CreativeProjectPatch, options?: CreativeRequestOptions): Promise<CreativeProjectUpdate>;
  generateScript(id: string, input: ScriptGenerationInput, options?: CreativeRequestOptions): Promise<ScriptGenerationResult>;
  addManualScript(projectId: string, input: ManualScriptInput, options?: CreativeRequestOptions): Promise<ScriptWriteResult>;
  listScripts(projectId: string, options?: CreativeRequestOptions): Promise<{ items: CreativeScript[] }>;
  getScript(id: string, options?: CreativeRequestOptions): Promise<CreativeScript>;
  editScript(id: string, input: ScriptEditInput, options?: CreativeRequestOptions): Promise<CreativeScript>;
  confirmScript(id: string, options?: CreativeRequestOptions): Promise<ScriptConfirmation>;
}
