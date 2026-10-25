/** Assistant v0.2 wire types. Missing quote fields remain missing; they are never synthesized. */
export type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue };
export type JsonObject = { [key: string]: JsonValue };
export type AssistantIntent = 'optimize_only' | 'generate' | 'auto';
export type ReplyType = 'optimize_result' | 'clarify' | 'template_recommendation' | 'quote_ready' | 'awaiting_facts' | 'refusal' | 'message';
export type ProviderState = 'not_configured' | 'misconfigured' | 'configured_unverified' | 'stub' | 'connected_verified';

export interface ProviderIdentity {
  name: string;
  model?: string | null;
  stub?: boolean;
}
export interface AssistantStatus {
  available: boolean;
  state: string; // Unknown future states must not silently become usable.
  provider: string | ProviderIdentity;
  model?: string | null;
  vision?: boolean;
  streaming?: boolean;
  verified?: boolean; // Connection evidence only; not a model-capability evaluation.
  note?: string | null;
}
export interface AssistantSession {
  session_id: string;
  project_id?: string | null;
  intent: string;
  status: string;
  limits?: {
    turns_used?: number;
    turns_max?: number;
    questions_used?: number;
    questions_max?: number;
    tool_calls_used?: number;
    tool_calls_per_turn_max?: number;
    tool_rounds_max?: number;
  };
  usage?: { input_tokens?: number; output_tokens?: number };
}
export type AssistantBrief = Partial<Record<
  'subject' | 'key_selling_points' | 'keep_features' | 'forbidden_elements' |
  'audience' | 'style' | 'spec_preference' | 'assumptions', JsonValue
>>;
export interface BriefResponse {
  project_id: string;
  brief: AssistantBrief;
  version?: number;
}
export interface ChatInput {
  session_id?: string | null;
  project_id: string;
  message: string;
  /** Supply only following an explicit user mode selection. */
  intent?: AssistantIntent;
  brief?: AssistantBrief;
  context?: { selected_asset_id?: string | null; kind?: 'image' | 'video' };
}
export interface AssistantOption { id: string; label: string }
export interface AwaitingFacts {
  missing_fields?: string[];
  reason?: string;
  hint?: string;
  questions_used?: number;
  questions_max?: number;
}
export interface ModelLifecycle {
  status?: string;
  eos_at?: string | null;
  migrate_to?: string | null;
  migrate_to_activation?: string | null;
}

/** Frozen request supplied by the backend, including unknown future fields. */
export interface FrozenTaskPayload extends JsonObject {
  kind: 'image' | 'video';
  model_id: string;
  params: JsonObject;
}
export interface ResolvedParams extends JsonObject { prompt: string }
export interface ManualParams extends FrozenTaskPayload {
  effective: JsonObject;
}

/** GET quote, chat summaries and incomplete older responses deliberately share an optional shape. */
export interface AssistantQuote {
  quote_id?: string;
  id?: string;
  session_id?: string | null;
  project_id?: string | null;
  plan_hash?: string | null;
  expires_at?: number | null; // Unix seconds, as returned by the server.
  expired?: boolean;
  cost_cny?: number | null;
  estimated_cost_cny?: number | null;
  kind?: 'image' | 'video';
  model_id?: string;
  template_id?: string | null;
  template_version?: number | null;
  template_name?: string | null;
  concept_mode?: boolean;
  effective?: JsonObject | null;
  resolved_params?: ResolvedParams | null;
  submit_payload?: FrozenTaskPayload | null;
  manual_params?: ManualParams | null;
  warnings?: JsonValue[];
  model_lifecycle?: ModelLifecycle | null;
}
export interface StructuredReply {
  reply_type?: string; // Render unknown values as informational text, never an action.
  options?: AssistantOption[];
  optimized_prompt?: string | null;
  missing_fields?: string[];
  quote?: AssistantQuote | null;
  manual_params?: ManualParams | null;
  submit_payload?: FrozenTaskPayload | null;
  awaiting_facts?: AwaitingFacts | null;
}
export interface AssistantMessage {
  role: string;
  content: string;
  reply_type?: string | null;
  structured?: StructuredReply | null;
}
export interface SessionRecovery {
  project_id: string;
  session: AssistantSession | null;
  history: AssistantMessage[];
  brief: AssistantBrief;
}
export interface ChatResponse extends StructuredReply {
  session_id: string;
  reply: string;
  intent: string;
  session: AssistantSession;
  brief?: AssistantBrief;
  provider: ProviderIdentity;
  tool_calls?: Array<{ tool: string; ok: boolean; result?: unknown; error?: unknown }>;
  note?: string | null;
}
export interface AssistantApproval {
  approval_id?: string;
  quote_id?: string;
  session_id?: string;
  project_id?: string | null;
  plan_hash?: string | null;
  expires_at?: number | null;
  max_uses?: number;
  cost_cny?: number | null;
  submit_payload?: FrozenTaskPayload | null;
}
export interface ApprovedTaskInput extends FrozenTaskPayload { approval_id: string }
export interface AssistantTaskResult {
  task_id: string;
  status?: string;
  queue_position?: number;
  estimated_cost_cny?: number | null;
  origin?: string;
  caller_id?: string;
  approval_id?: string;
  idempotent_replay?: boolean;
}
export interface AssistantUsage {
  session_id?: string | null;
  assistant: {
    input_tokens?: number;
    output_tokens?: number;
    tool_calls?: number;
    estimated_cost_cny?: number | null;
    by_kind?: JsonObject[];
  };
  generation: {
    tasks?: number;
    estimated_cost_cny?: number | null;
    note?: string;
  };
  items?: JsonObject[];
  disclaimer?: string;
}
export interface RequestOptions {
  signal?: AbortSignal;
  /** Entire browser request, including response body; default 120 seconds. No retry. */
  timeoutMs?: number;
}
export interface QuoteRequest {
  session: AssistantSession;
  payload: JsonObject;
}
export interface ConfirmRequest {
  session: AssistantSession;
  quote: AssistantQuote;
  /** The UI must supply this only in a user's confirmation click handler. */
  userAction: 'confirm_generate';
}
export interface SubmitApprovedRequest {
  session: AssistantSession;
  quote: AssistantQuote;
  approval: AssistantApproval;
}
