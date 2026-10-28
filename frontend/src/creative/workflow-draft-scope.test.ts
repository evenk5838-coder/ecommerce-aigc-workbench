import { describe, expect, it } from 'vitest';
import { workflowActionDraftScope, workflowFailedDraftScope, workflowMessageNodeIds, validWorkflowDraftScope } from './workflow-draft-scope';
import type { WorkflowAction, WorkflowPendingRequest } from './director-workflow-api';

const batch: WorkflowAction = { action_id: 'images', kind: 'generation', status: 'completed', requires_user_action: false,
  node_ids: ['second', 'first'], quotes: [] };

describe('explicit follow-up batch scope', () => {
  it('keeps the completed batch order even when automatic reveal selected only its first image', () => {
    const scope = workflowActionDraftScope(batch);
    expect(scope).toEqual({ nodeIds: ['second', 'first'], label: '本组分镜图片' });
    expect(workflowMessageNodeIds(null, scope, ['second'])).toEqual(['second', 'first']);
    batch.node_ids!.push('later');
    expect(scope?.nodeIds).toEqual(['second', 'first']);
    batch.node_ids!.pop();
  });
  it('uses the current user selection once the visible batch binding is cleared', () => {
    expect(workflowMessageNodeIds(null, undefined, ['new-choice'])).toEqual(['new-choice']);
    expect(workflowMessageNodeIds(null, undefined, [])).toEqual([]);
  });
  it('never changes an already submitted message when its later draft or selection changes', () => {
    const pending: WorkflowPendingRequest = { endpoint: 'conversation', id: 'sent', message: '完整成片', selected_node_ids: ['frozen-2', 'frozen-1'] };
    expect(workflowMessageNodeIds(pending, workflowActionDraftScope(batch), ['unrelated'])).toEqual(['frozen-2', 'frozen-1']);
    expect(pending.selected_node_ids).toEqual(['frozen-2', 'frozen-1']);
    expect(workflowFailedDraftScope(pending)).toEqual({ nodeIds: ['frozen-2', 'frozen-1'], label: '上次创作范围' });
    const nextScope = { nodeIds: ['next'], label: '下一组' };
    expect(workflowFailedDraftScope(pending, nextScope)).toBe(nextScope);
  });
  it.each([{ nodeIds: [], label: '一组' }, { nodeIds: ['same', 'same'], label: '一组' }, { nodeIds: ['node'], label: '' }])('rejects invalid restored bindings %j', value => {
    expect(validWorkflowDraftScope(value)).toBe(false);
  });
});
