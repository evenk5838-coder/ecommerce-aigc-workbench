/** Deferred from the current Web release; keep its implementation and drafts. */
export const LAYOUT_EDITOR_ENABLED = false;
export type StudioView = 'projects' | 'home' | 'director' | 'creative' | 'canvas' | 'library';

export function availableStudioView(view: StudioView): StudioView {
  return view === 'canvas' && !LAYOUT_EDITOR_ENABLED ? 'home' : view;
}
