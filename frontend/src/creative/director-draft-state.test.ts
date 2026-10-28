import { describe, expect, it } from 'vitest';
import { directorNavigationState, hasUnsavedDirectorDraft, mergeFailedDirectorDraft } from './director-draft-state';
import { parseStoredWorkflowConversation, readWorkflowConversation, writeWorkflowConversation, type StoredWorkflowConversation } from './workflow-conversation-state';
import { actionSubmissionState } from './director-action-view';
import type { WorkflowAction } from './director-workflow-api';

describe('director failed message and concurrent draft retention', () => {
  it('retains message A and the newer draft B after rejection or failed-receipt recovery', () => {
    expect(mergeFailedDirectorDraft('B: 新补充要求', 'A: 最初拍摄想法')).toBe('A: 最初拍摄想法\n\nB: 新补充要求');
  });
  it('restores a failed message when there is no newer draft', () => {
    expect(mergeFailedDirectorDraft('', 'A')).toBe('A');
  });
  it('does not duplicate an exact copy the user already restored', () => {
    expect(mergeFailedDirectorDraft('A', 'A')).toBe('A');
  });
  it('keeps distinct edits, including a newer draft containing the original as a substring', () => {
    expect(mergeFailedDirectorDraft('A，但换一个背景', 'A')).toBe('A\n\nA，但换一个背景');
  });
  it('does not erase a newer draft when there is no failed message', () => {
    expect(mergeFailedDirectorDraft('B', '')).toBe('B');
  });
  it('guards only nonempty drafts whose local persistence failed', () => {
    expect(hasUnsavedDirectorDraft('B', true)).toBe(true);
    expect(hasUnsavedDirectorDraft('B', false)).toBe(false);
    expect(hasUnsavedDirectorDraft('   ', true)).toBe(false);
  });
});

describe('director navigation protects writes without blocking read-only refreshes', () => {
  const projectId = 'leave-guard-project';
  const empty = () => parseStoredWorkflowConversation(null, projectId);
  const pending = { endpoint: 'conversation' as const, id: 'original-request', message: '写一版脚本', selected_node_ids: [] };
  const confirmation = { action_id: 'action-original', kind: 'generation' as const, client_request_id: 'original-confirmation', user_action: 'confirm_generate' as const };
  function persistAndRecover(value: StoredWorkflowConversation) {
    const values = new Map<string, string>();
    const storage = { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, text: string) => { values.set(key, text); } };
    writeWorkflowConversation(storage, value);
    return readWorkflowConversation(storage, projectId);
  }

  it('allows initial loading, history reads and refreshing an acknowledged running job', () => {
    expect(directorNavigationState(empty(), false)).toEqual({ busy: false, dirty: false });
    const snapshot: StoredWorkflowConversation = { ...empty(), actions: [{ action_id: 'generation', kind: 'generation', status: 'running', action_status: 'submitted', requires_user_action: false }] };
    expect(directorNavigationState(snapshot, false)).toEqual({ busy: false, dirty: false });
  });

  it('protects a conversation POST and its unresolved receipt after page recovery', () => {
    const sending = { ...empty(), pending };
    expect(directorNavigationState(sending, false).busy).toBe(true);
    const recovered = persistAndRecover(sending);
    expect(recovered.pending).toEqual(pending);
    // A timeout or receipt read does not acknowledge the original write.
    expect(directorNavigationState(recovered, false)).toEqual({ busy: true, dirty: false });
  });

  it('releases the write guard after acknowledgment while protecting an unsaved newer draft', () => {
    const sending = { ...empty(), pending, draft: '第二镜放慢一点' };
    const acknowledged = { ...sending, pending: null };
    expect(directorNavigationState(acknowledged, true)).toEqual({ busy: false, dirty: true });
    const recovered = persistAndRecover(acknowledged);
    expect(recovered.draft).toBe('第二镜放慢一点');
    expect(directorNavigationState(recovered, false)).toEqual({ busy: false, dirty: false });
  });

  it('keeps an unknown confirmation guarded until its original submission is acknowledged', () => {
    const recovered = persistAndRecover({ ...empty(), actionPending: confirmation });
    const proposal: WorkflowAction = { action_id: confirmation.action_id, kind: confirmation.kind, status: 'proposed', action_status: 'proposed', requires_user_action: true };
    expect(actionSubmissionState(proposal, confirmation)).toBe('unconfirmed');
    expect(directorNavigationState(recovered, false).busy).toBe(true);
    const submitted = { ...proposal, status: 'running', action_status: 'submitted' as const, submit_client_request_id: confirmation.client_request_id, requires_user_action: false };
    expect(actionSubmissionState(submitted, confirmation)).not.toBe('unconfirmed');
    const acknowledged = { ...recovered, actionPending: null, actions: [submitted] };
    expect(directorNavigationState(acknowledged, false)).toEqual({ busy: false, dirty: false });
  });

  it('retains both messages after a known rejection and guards their failed local save', () => {
    const rejected = { ...empty(), draft: mergeFailedDirectorDraft('新补充', pending.message) };
    expect(directorNavigationState(rejected, true)).toEqual({ busy: false, dirty: true });
    const recovered = persistAndRecover(rejected);
    expect(recovered.draft).toBe('写一版脚本\n\n新补充');
    expect(directorNavigationState(recovered, false)).toEqual({ busy: false, dirty: false });
  });

  it('preserves the legacy pending confirmation guard during migration and read recovery', () => {
    const migrated = parseStoredWorkflowConversation(JSON.stringify({ projectId, draft: '', actionPending: true }), projectId);
    expect(directorNavigationState(persistAndRecover(migrated), false)).toEqual({ busy: true, dirty: false });
    expect(directorNavigationState(migrated, true)).toEqual({ busy: true, dirty: true });
  });

  it('keeps empty-input writes guarded when local persistence fails', () => {
    expect(directorNavigationState({ ...empty(), pending }, true)).toEqual({ busy: true, dirty: true });
    expect(directorNavigationState({ ...empty(), actionPending: confirmation }, true)).toEqual({ busy: true, dirty: true });
    expect(directorNavigationState(empty(), true)).toEqual({ busy: false, dirty: false });
  });
});
