import { describe, expect, it } from 'vitest';
import { hasUnsavedDirectorDraft, mergeFailedDirectorDraft } from './director-draft-state';

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
