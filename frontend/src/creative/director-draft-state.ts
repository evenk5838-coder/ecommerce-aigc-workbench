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
