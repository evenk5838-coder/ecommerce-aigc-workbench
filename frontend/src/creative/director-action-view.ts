import { ApiError } from '../api';
import type { PendingWorkflowRequest, WorkflowAction, WorkflowActionPending, WorkflowExecution, WorkflowPendingRequest, WorkflowQuote, WorkflowReceipt, WorkflowReply } from './director-workflow-api';

/** Keep an acknowledged submission when an earlier state read arrives afterwards. */
export function mergeWorkflowActions(current: readonly WorkflowAction[], incoming: readonly WorkflowAction[]): WorkflowAction[] {
  const result = new Map(current.map(action => [action.action_id, action]));
  for (const action of incoming) {
    const prior = result.get(action.action_id);
    if (prior?.action_status === 'submitted' && action.action_status === 'proposed') continue;
    // Recovery can contain only an ID and "unknown" after an interrupted read.
    // It cannot revoke a durable acknowledgement or remove its tasks from polling.
    if (prior?.action_status && action.status === 'unknown') continue;
    // Terminal results may become unavailable when the actual file is lost or changed.
    result.set(action.action_id, action);
  }
  return [...result.values()];
}

export const isMissingWorkflowRequest = (error: unknown): boolean => error instanceof ApiError
  && error.status === 404 && error.code === 'DIRECTOR_REQUEST_NOT_FOUND';

interface WorkflowRetryApi {
  receipt: (projectId: string, requestId: string, options?: { signal?: AbortSignal }) => Promise<WorkflowReceipt>;
  conversation: (projectId: string, pending: WorkflowPendingRequest, options?: { signal?: AbortSignal }) => Promise<WorkflowReply>;
}

/** Explicit recovery only: first read the original identity, then replay its exact payload only on a real 404. */
export async function retryMissingWorkflowConversation(api: WorkflowRetryApi, projectId: string, pending: PendingWorkflowRequest, signal?: AbortSignal): Promise<{ kind: 'receipt'; receipt: WorkflowReceipt } | { kind: 'reply'; reply: WorkflowReply }> {
  if (pending.endpoint !== 'conversation') throw new ApiError('VALIDATION_FAILED', '旧版消息只能从原入口读取结果。', 422);
  const checkAbort = () => { if (signal?.aborted) throw new ApiError('REQUEST_ABORTED', '已停止等待。', 0); };
  checkAbort();
  let receipt: WorkflowReceipt;
  try { receipt = await api.receipt(projectId, pending.id, { signal }); }
  catch (caught) {
    if (!isMissingWorkflowRequest(caught)) throw caught;
    checkAbort();
    return { kind: 'reply', reply: await api.conversation(projectId, pending, { signal }) };
  }
  checkAbort();
  return { kind: 'receipt', receipt };
}

export function actionSubmissionState(action: WorkflowAction, pending: WorkflowActionPending): 'accepted' | 'other' | 'unconfirmed' {
  if (action.action_id !== pending.action_id || action.kind !== pending.kind) return 'unconfirmed';
  if (action.action_status !== 'submitted') return 'unconfirmed';
  return action.submit_client_request_id === pending.client_request_id ? 'accepted' : 'other';
}

export function actionCanConfirm(action: WorkflowAction, now = Date.now() / 1000): boolean {
  if (!action.requires_user_action || action.action_status !== 'proposed' || action.expired || action.stale
    || action.expires_at === undefined || action.expires_at <= now || action.generation_action_id) return false;
  if (action.kind === 'generation' && action.output_mode === 'film') {
    return !!action.scope?.compose && action.scope.generate && action.scope.no_trim
      && action.scope.ordered_node_ids.join('\0') === action.node_ids?.join('\0');
  }
  return true;
}

/** A saved action from a still-pending new conversation can be explicitly approved. */
export function actionWithinPendingRequest(action: WorkflowAction, pending: PendingWorkflowRequest | null): boolean {
  return !pending || pending.endpoint === 'conversation' && action.request_id === pending.id;
}

export function actionTitle(action: WorkflowAction): string {
  if (action.kind === 'confirm_script') return '创作脚本';
  if (action.kind === 'composition') return action.title || '合成成片';
  if (action.output_mode === 'film') return '分镜视频与成片';
  return action.quotes?.every(quote => quote.input.kind === 'image') ? '分镜图片' : '分镜视频';
}

export function actionStatusLabel(action: WorkflowAction): string {
  if (action.action_status === 'proposed' && (action.expired || action.expires_at !== undefined && action.expires_at <= Date.now() / 1000)) return '方案已过期';
  if (action.stale || action.status === 'stale') return '内容已更新';
  if (action.status === 'completed') return action.kind === 'confirm_script' ? '已确认' : '已完成';
  if (action.status === 'running' && action.output_mode === 'film' && action.media_status === 'completed') return '正在合成成片';
  return ({ proposed: '等你确认', running: '进行中', partial: '部分完成', failed: '未完成', unknown: '待核对' } as Record<string, string>)[action.status] || '待核对';
}

export function availableExecution(execution: WorkflowExecution): boolean {
  return execution.status === 'succeeded' && execution.asset_available && execution.result_status === 'available'
    && !!execution.asset_id && execution.asset?.id === execution.asset_id;
}

export function executionLabel(execution: WorkflowExecution): string {
  if (availableExecution(execution)) return '已完成';
  if (execution.active) return execution.progress !== null ? `${Math.round(Math.max(0, Math.min(1, execution.progress)) * 100)}%` : '生成中';
  if (execution.asset_error) return '素材暂不可用';
  return ({ queued: '排队中', failed: '生成未完成', canceled: '已取消', interrupted: '需核对结果' } as Record<string, string>)[execution.status] || '待核对';
}

export function quotePrompt(quote: WorkflowQuote): string {
  const prompt = quote.input.prompt ?? quote.input.params.prompt;
  return typeof prompt === 'string' ? prompt : '';
}

export function quoteSpecs(quote: WorkflowQuote): string[] {
  const p = quote.input.params;
  const mode = typeof p.generation_mode === 'string' ? ({ text_to_image: '文生图', image_to_image: '图生图', text_to_video: '文生视频', image_to_video: '图生视频' } as Record<string, string>)[p.generation_mode] : undefined;
  return [mode, typeof p.ratio === 'string' ? p.ratio : undefined,
    typeof p.resolution === 'string' ? p.resolution.toUpperCase() : undefined,
    typeof p.duration === 'number' ? `${p.duration} 秒` : typeof p.duration_s === 'number' ? `${p.duration_s} 秒` : undefined,
    !p.resolution && typeof p.size === 'string' ? (Math.max(...p.size.split('x').map(Number)) >= 3800 ? '4K' : Math.max(...p.size.split('x').map(Number)) >= 1900 ? '2K' : '标准') : undefined,
  ].filter((value): value is string => !!value);
}

export function estimatedCostLabel(cost: number | undefined): string {
  return cost === undefined ? '费用待核对' : `预计 ¥${cost.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 4 })}`;
}

export function finalComposition(action: WorkflowAction) {
  const result = action.kind === 'composition' ? action : action.output_mode === 'film' ? action.composition : null;
  return result?.status === 'completed' && result.asset_available && result.asset_id ? result : null;
}
