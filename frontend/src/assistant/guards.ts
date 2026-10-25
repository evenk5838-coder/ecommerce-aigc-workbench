import { ApiError } from '../api';
import type {
  ApprovedTaskInput, AssistantQuote, AssistantSession, FrozenTaskPayload,
  JsonObject, JsonValue, ReplyType, ResolvedParams,
} from './types';

export interface GuardIssue { code: string; message: string; field?: string }
export interface QuoteReadiness {
  canConfirm: boolean;
  canTransferToManual: boolean;
  confirmationIssues: GuardIssue[];
  manualIssues: GuardIssue[];
}
export interface AssistantAvailability {
  canChat: boolean;
  isStub: boolean;
  connectionVerified: boolean;
  /** The current status contract provides no capability-evaluation evidence. */
  capabilityVerification: 'not_reported';
  reason: string | null;
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}
function text(value: unknown): value is string { return typeof value === 'string' && value.trim().length > 0; }
function amount(value: unknown): value is number { return typeof value === 'number' && Number.isFinite(value) && value >= 0; }
function isJson(value: unknown): value is JsonValue {
  return value === null || typeof value === 'string' || typeof value === 'boolean' ||
    (typeof value === 'number' && Number.isFinite(value)) ||
    (Array.isArray(value) && value.every(isJson)) ||
    (isRecord(value) && Object.values(value).every(isJson));
}
function object(value: unknown): value is JsonObject { return isRecord(value) && isJson(value); }

/** Structural equality only; never computes the server's plan hash or changes its payload. */
function equal(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) return true;
  if (Array.isArray(left) && Array.isArray(right)) return left.length === right.length && left.every((v, i) => equal(v, right[i]));
  if (!isRecord(left) || !isRecord(right)) return false;
  const keys = Object.keys(left);
  return keys.length === Object.keys(right).length && keys.every(key => Object.hasOwn(right, key) && equal(left[key], right[key]));
}
export function isStubProvider(provider: unknown): boolean {
  return provider === 'fixed' || (isRecord(provider) && (provider.stub === true || provider.name === 'fixed'));
}
export function assistantAvailability(status: unknown, responseProvider?: unknown): AssistantAvailability {
  const valid = isRecord(status);
  const stub = (valid && (status.state === 'stub' || isStubProvider(status.provider))) || isStubProvider(responseProvider);
  const knownUsableState = valid && ['configured_unverified', 'connected_verified'].includes(String(status.state));
  const canChat = valid && status.available === true && knownUsableState && !stub;
  return {
    canChat, isStub: stub,
    connectionVerified: canChat && status.state === 'connected_verified' && status.verified === true,
    capabilityVerification: 'not_reported',
    reason: stub ? '当前为测试替身，不能用于正式助手。' : canChat ? null : '助手暂不可用，可继续直接使用工作台。',
  };
}
export function safeReplyType(value: unknown): ReplyType | 'unknown' {
  const known: ReplyType[] = ['optimize_result', 'clarify', 'template_recommendation', 'quote_ready', 'awaiting_facts', 'refusal', 'message'];
  return typeof value === 'string' && known.includes(value as ReplyType) ? value as ReplyType : 'unknown';
}
export function generationIntentIssues(session: unknown): GuardIssue[] {
  if (!isRecord(session) || !text(session.session_id)) return [{ code: 'ASSISTANT_SESSION_REQUIRED', message: '请先恢复或创建助手会话。' }];
  if (session.intent === 'optimize_only') return [{ code: 'ASSISTANT_OPTIMIZE_ONLY', message: '仅优化模式不报价或确认生成，请先主动切换到生成模式。' }];
  if (session.intent !== 'generate') return [{ code: 'ASSISTANT_GENERATION_INTENT_REQUIRED', message: '请明确选择生成模式后再查看报价或确认。' }];
  if (session.status === 'awaiting_facts') return [{ code: 'ASSISTANT_AWAITING_FACTS', message: '商品事实仍待补充，暂不能确认生成。' }];
  if (!['active', 'confirmed'].includes(String(session.status))) return [{ code: 'ASSISTANT_SESSION_NOT_READY', message: '当前会话状态未就绪，请先恢复会话核对。' }];
  return [];
}
export function assertGenerationIntent(session: unknown): asserts session is AssistantSession {
  const issues = generationIntentIssues(session);
  if (issues.length) throw new ApiError(issues[0].code, issues[0].message, 0);
}
export function quoteId(value: unknown): string | null {
  if (!isRecord(value)) return null;
  if (text(value.quote_id)) return value.quote_id;
  return text(value.id) ? value.id : null;
}
function payloadValid(value: unknown): value is FrozenTaskPayload {
  if (!object(value) || (value.kind !== 'image' && value.kind !== 'video') || !text(value.model_id) || !object(value.params)) return false;
  if (Object.hasOwn(value, 'approval_id')) return false;
  if (value.input_asset_id !== undefined && value.input_asset_id !== null && !text(value.input_asset_id)) return false;
  if (value.template_id != null) {
    if (!text(value.template_id) || typeof value.template_version !== 'number' || !Number.isInteger(value.template_version) || value.template_version < 1 || !object(value.template_fields)) return false;
  }
  return true;
}
function costIssues(quote: Record<string, unknown>): GuardIssue[] {
  const fields = ['estimated_cost_cny', 'cost_cny'].filter(key => Object.hasOwn(quote, key));
  if (!fields.length) return [{ code: 'QUOTE_COST_MISSING', field: 'estimated_cost_cny', message: '后端未提供费用估算，不能按零费用处理。' }];
  if (fields.some(key => !amount(quote[key]))) return [{ code: 'QUOTE_COST_INVALID', field: 'estimated_cost_cny', message: '后端费用估算缺失或无效，不能按零费用处理。' }];
  if (fields.length === 2 && quote.cost_cny !== quote.estimated_cost_cny) return [{ code: 'QUOTE_COST_MISMATCH', message: '后端返回的费用估算不一致，请重新读取报价。' }];
  return [];
}
function quotedCost(quote: { estimated_cost_cny?: unknown; cost_cny?: unknown }): number {
  return (Object.hasOwn(quote, 'estimated_cost_cny') ? quote.estimated_cost_cny : quote.cost_cny) as number;
}

function inspectQuoteState(value: unknown, session: unknown, nowMs: number, requireUnexpired: boolean): QuoteReadiness {
  const issues = generationIntentIssues(session);
  const add = (code: string, message: string, field?: string) => issues.push({ code, message, field });
  if (!isRecord(value)) {
    add('QUOTE_MISSING', '尚未取得完整报价。');
    return { canConfirm: false, canTransferToManual: false, confirmationIssues: issues, manualIssues: [...issues] };
  }
  if (!quoteId(value)) add('QUOTE_ID_MISSING', '报价缺少编号，请重新获取。', 'quote_id');
  if (!text(value.plan_hash)) add('QUOTE_PLAN_HASH_MISSING', '报价缺少冻结方案标识。', 'plan_hash');
  if (typeof value.expires_at !== 'number' || !Number.isFinite(value.expires_at) || value.expires_at <= 0) add('QUOTE_EXPIRY_MISSING', '报价缺少有效期，暂不能使用。', 'expires_at');
  else if (requireUnexpired && (value.expired === true || value.expires_at * 1000 <= nowMs)) add('QUOTE_EXPIRED', '报价已过期，请重新报价后再确认。', 'expires_at');
  issues.push(...costIssues(value));
  if (!object(value.resolved_params) || !text(value.resolved_params.prompt)) add('QUOTE_PROMPT_MISSING', '后端未提供最终提示词，暂不能确认或转为直接操作。', 'resolved_params.prompt');
  if (!payloadValid(value.submit_payload)) add('QUOTE_PAYLOAD_MISSING', '后端未提供完整的冻结提交参数。', 'submit_payload');
  if (!object(value.effective) || !Object.keys(value.effective).length) add('QUOTE_EFFECTIVE_MISSING', '后端未提供生效规格。', 'effective');
  if (isRecord(session)) {
    if (value.session_id != null && value.session_id !== session.session_id) add('QUOTE_SESSION_MISMATCH', '此报价不属于当前助手会话。', 'session_id');
    if (value.project_id != null && value.project_id !== session.project_id) add('QUOTE_PROJECT_MISMATCH', '此报价不属于当前草稿。', 'project_id');
  }
  const manualIssues = [...issues];
  if (!payloadValid(value.manual_params) || !isRecord(value.manual_params) || !object(value.manual_params.effective)) {
    manualIssues.push({ code: 'QUOTE_MANUAL_PARAMS_MISSING', field: 'manual_params', message: '后端未提供完整的手动操作参数，暂不能转入工作台。' });
  } else if (isRecord(value.submit_payload)) {
    const manual = value.manual_params;
    if (!Object.entries(value.submit_payload).every(([key, entry]) => equal(entry, manual[key])) || !equal(value.effective, manual.effective)) {
      manualIssues.push({ code: 'QUOTE_MANUAL_PARAMS_MISMATCH', message: '手动参数与报价方案不一致，暂不能转入工作台。' });
    }
  }
  return { canConfirm: issues.length === 0, canTransferToManual: manualIssues.length === 0, confirmationIssues: issues, manualIssues };
}

/** Confirmation and manual handoff always require an unexpired quote. */
export function inspectQuote(value: unknown, session: unknown, nowMs = Date.now()): QuoteReadiness {
  return inspectQuoteState(value, session, nowMs, true);
}

type CompleteQuote = AssistantQuote & {
  plan_hash: string; expires_at: number; submit_payload: FrozenTaskPayload; resolved_params: ResolvedParams;
};
function requireQuote(value: unknown, session: unknown, nowMs: number, requireUnexpired: boolean): asserts value is CompleteQuote {
  const check = inspectQuoteState(value, session, nowMs, requireUnexpired);
  if (!check.canConfirm) throw new ApiError('ASSISTANT_QUOTE_NOT_READY', check.confirmationIssues.map(issue => issue.message).join(' '), 0);
}
export function requireConfirmableQuote(value: unknown, session: unknown, nowMs = Date.now()): asserts value is CompleteQuote {
  requireQuote(value, session, nowMs, true);
}

/** Does not submit. Call only after a user-approved confirm response; keeps every frozen payload field. */
export function buildApprovedSubmission(quote: unknown, approval: unknown, session: unknown, nowMs = Date.now()): ApprovedTaskInput {
  assertGenerationIntent(session);
  // A server-issued approval has its own lifetime; the original quote may expire in transit.
  // Keep all content checks, then enforce the matching approval's expiry below.
  requireQuote(quote, session, nowMs, false);
  const fail = (message: string): never => { throw new ApiError('ASSISTANT_APPROVAL_MISMATCH', message, 0); };
  if (!isRecord(approval) || !text(approval.approval_id)) return fail('缺少有效的用户确认记录。');
  if (approval.quote_id !== quoteId(quote) || approval.plan_hash !== quote.plan_hash) return fail('确认记录与当前报价不匹配，请重新确认。');
  if (approval.session_id !== session.session_id || (approval.project_id ?? null) !== (session.project_id ?? null)) return fail('确认记录不属于当前会话或草稿。');
  if (typeof approval.expires_at !== 'number' || !Number.isFinite(approval.expires_at) || approval.expires_at * 1000 <= nowMs || approval.max_uses !== 1) return fail('确认记录已过期或无效，请重新确认。');
  if (!amount(approval.cost_cny) || approval.cost_cny !== quotedCost(quote)) return fail('确认记录的费用与报价不一致。');
  if (!payloadValid(approval.submit_payload) || !equal(approval.submit_payload, quote.submit_payload)) return fail('确认后的方案发生变化，请重新报价与确认。');
  return { ...approval.submit_payload, approval_id: approval.approval_id };
}
