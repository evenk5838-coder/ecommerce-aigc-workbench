import type { PendingWorkflowRequest, WorkflowAction } from './director-workflow-api';

/** A visible, editable draft can intentionally refer to a completed action's whole batch. */
export interface WorkflowDraftScope { nodeIds: string[]; label: string }

export function validWorkflowDraftScope(value: unknown): value is WorkflowDraftScope {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const scope = value as Record<string, unknown>;
  return typeof scope.label === 'string' && !!scope.label.trim() && scope.label.length <= 100
    && Array.isArray(scope.nodeIds) && scope.nodeIds.length > 0 && scope.nodeIds.length <= 40
    && scope.nodeIds.every(id => typeof id === 'string' && !!id.trim())
    && new Set(scope.nodeIds).size === scope.nodeIds.length;
}

export function workflowActionDraftScope(action: WorkflowAction): WorkflowDraftScope | undefined {
  const compositionNodes = action.kind === 'composition' && action.sources?.length
    && action.sources.every(source => typeof source.node_id === 'string' && !!source.node_id.trim())
    ? action.sources.map(source => source.node_id!) : undefined;
  // Partial node selection would silently omit an asset-only source from this card.
  const nodeIds = action.kind === 'composition' ? compositionNodes : action.node_ids;
  if (!nodeIds?.length) return undefined;
  const label = action.kind === 'confirm_script' ? '这份脚本'
    : action.quotes?.every(quote => quote.input.kind === 'image') ? '本组分镜图片' : '本组视频';
  const scope = { nodeIds: [...nodeIds], label };
  return validWorkflowDraftScope(scope) ? scope : undefined;
}

/** Renew the same output contract; a film quote must never silently become clips-only. */
export function workflowActionRenewalMessage(action: WorkflowAction): string {
  if (action.kind === 'confirm_script') return '请为当前脚本重新准备确认。';
  if (action.kind === 'composition') {
    const sources = action.sources ?? [];
    const order = sources.map((source, index) => `第 ${index + 1} 个视频（${(source.metadata.duration_ms / 1000).toFixed(1)} 秒）`).join(' → ');
    if (!workflowActionDraftScope(action)) return `请先核对当前「${action.title || '合成成片'}」合成卡内的 ${sources.length} 个视频来源，以这张卡片的素材为本次范围，当前画布选区不能替代原卡片来源。顺序保持为：${order}。确认来源后再重新准备合成方案，保留每个片段的完整时长。`;
    return `请为当前「${action.title || '合成成片'}」合成卡内的 ${sources.length} 个视频重新准备合成方案，范围和顺序保持为：${order}。保留每个片段的完整时长。`;
  }
  const count = action.node_ids?.length ?? action.quotes?.length ?? 0;
  const order = Array.from({ length: count }, (_, index) => `镜头 ${index + 1}`).join(' → ');
  const output = action.output_mode === 'film' ? '生成视频并合成完整成片'
    : action.quotes?.every(quote => quote.input.kind === 'image') ? '分镜图片生成' : '逐镜视频生成';
  return `请根据当前卡片中这 ${count} 个节点的当前设置，按原顺序（${order}）重新准备${output}方案。${action.output_mode === 'film' ? '全部镜头生成完成后合成，保留每个片段的完整时长。' : ''}`;
}

/** Recovery always uses the submitted identity; an unsubmitted suggestion uses its visible batch. */
export function workflowMessageNodeIds(pending: PendingWorkflowRequest | null, scope: WorkflowDraftScope | undefined, selectedNodeIds: readonly string[]): string[] {
  if (pending) return pending.endpoint === 'conversation' ? [...pending.selected_node_ids] : [];
  return [...(scope?.nodeIds ?? selectedNodeIds)];
}

/** A failed message restored to the composer must still show its original node scope. */
export function workflowFailedDraftScope(pending: PendingWorkflowRequest, currentScope?: WorkflowDraftScope): WorkflowDraftScope | undefined {
  return currentScope ?? (pending.endpoint === 'conversation' && pending.selected_node_ids.length
    ? { nodeIds: [...pending.selected_node_ids], label: '上次创作范围' } : undefined);
}
