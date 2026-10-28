import type { StoredWorkflowConversation } from './workflow-conversation-state';

/** Preserve both the rejected message and text typed while it was in flight. */
export function mergeFailedDirectorDraft(currentDraft: string, failedMessage: string): string {
  if (!failedMessage.trim()) return currentDraft;
  if (!currentDraft.trim()) return failedMessage;
  if (currentDraft === failedMessage) return currentDraft;
  return `${failedMessage}\n\n${currentDraft}`;
}

/** A saved local draft may safely survive navigation; a failed save cannot. */
export function hasUnsavedDirectorDraft(draft: string, storageFailed: boolean): boolean {
  return storageFailed && draft.trim().length > 0;
}
/** Reads are cancelable. Only unacknowledged writes and unsaved input need a leave guard.
 * Every conversation/confirmation POST stores its pending identity before sending;
 * receipt recovery retains that identity until the server resolves the write.
 */
export function directorNavigationState(
  value: Pick<StoredWorkflowConversation, 'draft' | 'pending' | 'actionPending' | 'legacyActionPending'>,
  storageFailed: boolean,
): { busy: boolean; dirty: boolean } {
  const busy = !!value.pending || !!value.actionPending || value.legacyActionPending;
  return { busy, dirty: hasUnsavedDirectorDraft(value.draft, storageFailed) || storageFailed && busy };
}
