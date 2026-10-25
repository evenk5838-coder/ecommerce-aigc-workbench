import { ApiError } from '../api';
import {
  assertGenerationIntent, buildApprovedSubmission, isRecord, isStubProvider,
  quoteId, requireConfirmableQuote,
} from './guards';
import type {
  AssistantApproval, AssistantBrief, AssistantQuote, AssistantSession, AssistantStatus,
  AssistantTaskResult, AssistantUsage, BriefResponse, ChatInput, ChatResponse,
  ConfirmRequest, QuoteRequest, RequestOptions, SessionRecovery, SubmitApprovedRequest, AssistantIntent,
} from './types';

/** 3 model/tool rounds may exceed the legacy 25s client timeout. This is a wait limit, not a retry. */
export const ASSISTANT_REQUEST_TIMEOUT_MS = 120_000;
type Fetcher = typeof fetch;
type Parser<T> = (value: unknown) => T;
const invalid = (): never => { throw new ApiError('ASSISTANT_RESPONSE_INVALID', '助手返回的数据格式不完整，请恢复会话后核对。', 0); };
const nonEmpty = (value: unknown): value is string => typeof value === 'string' && value.trim().length > 0;
const json = (method: string, body: unknown): RequestInit => ({
  method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
});
function id(value: string, label: string): string {
  if (!nonEmpty(value)) throw new ApiError('ASSISTANT_INPUT_REQUIRED', '缺少' + label + '。', 0);
  return encodeURIComponent(value);
}
function parseSession(value: unknown): AssistantSession {
  if (!isRecord(value) || !nonEmpty(value.session_id) || !nonEmpty(value.intent) || !nonEmpty(value.status)) return invalid();
  if (value.project_id != null && typeof value.project_id !== 'string') return invalid();
  return value as unknown as AssistantSession;
}
function parseStatus(value: unknown): AssistantStatus {
  if (!isRecord(value) || typeof value.available !== 'boolean' || typeof value.state !== 'string') return invalid();
  if (typeof value.provider !== 'string' && !(isRecord(value.provider) && nonEmpty(value.provider.name))) return invalid();
  return value as unknown as AssistantStatus;
}
function validateStructured(value: Record<string, unknown>): void {
  for (const key of ['reply_type', 'optimized_prompt']) {
    if (value[key] != null && typeof value[key] !== 'string') invalid();
  }
  if (value.options != null && (!Array.isArray(value.options) || !value.options.every(option => isRecord(option) && nonEmpty(option.id) && nonEmpty(option.label)))) invalid();
  if (value.missing_fields != null && (!Array.isArray(value.missing_fields) || !value.missing_fields.every(field => typeof field === 'string'))) invalid();
  for (const key of ['quote', 'manual_params', 'submit_payload', 'awaiting_facts']) {
    if (value[key] != null && !isRecord(value[key])) invalid();
  }
}
function parseRecovery(value: unknown): SessionRecovery {
  if (!isRecord(value) || !nonEmpty(value.project_id) || !Array.isArray(value.history) || !isRecord(value.brief)) return invalid();
  if (value.session !== null) parseSession(value.session);
  for (const entry of value.history) {
    if (!isRecord(entry) || typeof entry.role !== 'string' || typeof entry.content !== 'string') return invalid();
    if (entry.structured != null) {
      if (!isRecord(entry.structured)) return invalid();
      validateStructured(entry.structured);
    }
  }
  return value as unknown as SessionRecovery;
}
function parseChat(value: unknown): ChatResponse {
  if (!isRecord(value) || !nonEmpty(value.session_id) || typeof value.reply !== 'string' || typeof value.intent !== 'string') return invalid();
  parseSession(value.session);
  if (!isRecord(value.provider) || !nonEmpty(value.provider.name)) return invalid();
  if (isStubProvider(value.provider)) throw new ApiError('ASSISTANT_TEST_PROVIDER', '收到测试替身回复，不能作为正式助手结果。', 0);
  validateStructured(value);
  return value as unknown as ChatResponse;
}
function parseQuote(value: unknown): AssistantQuote {
  // Incomplete quotes are returned for display with inspectQuote(), never silently repaired.
  if (!isRecord(value)) return invalid();
  return value as AssistantQuote;
}
function parseApproval(value: unknown): AssistantApproval {
  if (!isRecord(value)) return invalid();
  return value as AssistantApproval;
}
function parseBrief(value: unknown): BriefResponse {
  if (!isRecord(value) || !nonEmpty(value.project_id) || !isRecord(value.brief)) return invalid();
  return value as unknown as BriefResponse;
}
function parseUsage(value: unknown): AssistantUsage {
  if (!isRecord(value) || !isRecord(value.assistant) || !isRecord(value.generation)) return invalid();
  // Do not coerce missing/null monetary fields to zero, or calculate a balance.
  return value as unknown as AssistantUsage;
}
function parseTask(value: unknown): AssistantTaskResult {
  if (!isRecord(value) || !nonEmpty(value.task_id)) return invalid();
  return value as unknown as AssistantTaskResult;
}

/** Inject fetch in offline tests. The default transport only uses same-origin backend routes. */
export function createAssistantApi(fetcher: Fetcher = (input, init) => globalThis.fetch(input, init)) {
  async function request<T>(path: string, init: RequestInit, parse: Parser<T>, options: RequestOptions = {}): Promise<T> {
    const timeoutMs = options.timeoutMs ?? ASSISTANT_REQUEST_TIMEOUT_MS;
    if (!Number.isFinite(timeoutMs) || timeoutMs <= 0) throw new ApiError('ASSISTANT_TIMEOUT_INVALID', '请求等待时间无效。', 0);
    const controller = new AbortController();
    let timedOut = false;
    const stopped = () => new ApiError(
      timedOut ? 'ASSISTANT_REQUEST_TIMEOUT' : 'ASSISTANT_REQUEST_ABORTED',
      timedOut ? '等待助手响应超时，服务端可能仍在处理。请先恢复会话核对结果，不要直接重发。' :
        '已停止等待；这不会取消服务端处理。请恢复会话核对结果。',
      0,
    );
    if (options.signal?.aborted) throw stopped();
    const abort = () => controller.abort();
    options.signal?.addEventListener('abort', abort, { once: true });
    const timer = globalThis.setTimeout(() => { timedOut = true; controller.abort(); }, timeoutMs);
    let removeAbortListener = () => {};
    const interrupted = new Promise<never>((_, reject) => {
      const onAbort = () => reject(stopped());
      controller.signal.addEventListener('abort', onAbort, { once: true });
      removeAbortListener = () => controller.signal.removeEventListener('abort', onAbort);
    });
    try {
      const operation = (async () => {
        const response = await fetcher('/api' + path, { ...init, signal: controller.signal });
        let body: unknown;
        try { body = await response.json(); }
        catch {
          if (controller.signal.aborted) throw stopped();
          if (!response.ok) throw new ApiError('HTTP_ERROR', '助手请求失败（' + response.status + '）。', response.status);
          return invalid();
        }
        if (!response.ok) {
          const error = isRecord(body) && isRecord(body.error) ? body.error : null;
          throw new ApiError(
            error && nonEmpty(error.code) ? error.code : 'HTTP_ERROR',
            error && nonEmpty(error.message) ? error.message : '助手请求失败（' + response.status + '）。',
            response.status,
          );
        }
        return parse(body);
      })();
      return await Promise.race([operation, interrupted]);
    } catch (error) {
      if (controller.signal.aborted) throw stopped();
      if (error instanceof ApiError) throw error;
      throw new ApiError('ASSISTANT_NETWORK_ERROR', '助手连接中断，请先恢复会话核对结果。没有自动重发请求。', 0);
    } finally {
      globalThis.clearTimeout(timer);
      options.signal?.removeEventListener('abort', abort);
      removeAbortListener();
    }
  }

  return {
    status: (options?: RequestOptions) => request('/assistant/status', {}, parseStatus, options),
    recoverSession: (projectId: string, options?: RequestOptions) =>
      request('/assistant/session?project_id=' + id(projectId, '草稿编号'), {}, parseRecovery, options),
    session: (sessionId: string, options?: RequestOptions) =>
      request('/assistant/session/' + id(sessionId, '会话编号'), {}, parseSession, options),
    chat: (input: ChatInput, options?: RequestOptions) => {
      id(input.project_id, '草稿编号');
      if (!nonEmpty(input.message)) throw new ApiError('ASSISTANT_INPUT_REQUIRED', '请先填写创作需求。', 0);
      return request('/assistant/chat', json('POST', input), parseChat, options);
    },
    setIntent: (sessionId: string, intent: AssistantIntent, reason: string, options?: RequestOptions) => {
      id(sessionId, '会话编号');
      return request('/assistant/intent', json('PUT', { session_id: sessionId, intent, reason }), parseSession, options);
    },
    brief: (projectId: string, options?: RequestOptions) =>
      request('/assistant/brief?project_id=' + id(projectId, '草稿编号'), {}, parseBrief, options),
    saveBrief: (projectId: string, brief: AssistantBrief, options?: RequestOptions) =>
      request('/assistant/brief?project_id=' + id(projectId, '草稿编号'), json('PUT', { brief }), parseBrief, options),
    quote: (input: QuoteRequest, options?: RequestOptions) => {
      assertGenerationIntent(input.session);
      return request('/assistant/quote', json('POST', {
        session_id: input.session.session_id, project_id: input.session.project_id, payload: input.payload,
      }), parseQuote, options);
    },
    getQuote: (quoteId: string, options?: RequestOptions) =>
      request('/assistant/quote/' + id(quoteId, '报价编号'), {}, parseQuote, options),
    confirm: async (input: ConfirmRequest, options?: RequestOptions) => {
      assertGenerationIntent(input.session);
      requireConfirmableQuote(input.quote, input.session);
      if (input.userAction !== 'confirm_generate') throw new ApiError('ASSISTANT_USER_CONFIRM_REQUIRED', '请由用户点击确认生成。', 0);
      const approval = await request('/assistant/confirm', json('POST', {
        session_id: input.session.session_id, project_id: input.session.project_id,
        quote_id: quoteId(input.quote), user_action: input.userAction,
      }), parseApproval, options);
      // A bad confirm response must never enable submission.
      buildApprovedSubmission(input.quote, approval, input.session);
      return approval;
    },
    usage: (sessionId?: string, options?: RequestOptions) =>
      request('/assistant/usage' + (sessionId === undefined ? '' : '?session_id=' + id(sessionId, '会话编号')), {}, parseUsage, options),
    submitApproved: (input: SubmitApprovedRequest, options?: RequestOptions) => {
      const payload = buildApprovedSubmission(input.quote, input.approval, input.session);
      return request('/tasks', json('POST', payload), parseTask, options);
    },
  };
}

export const assistantApi = createAssistantApi();
