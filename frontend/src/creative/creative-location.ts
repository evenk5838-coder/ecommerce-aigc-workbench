export type CreativeLocation = { view: 'creative'; projectId: string } | { view: 'projects' | 'director'; projectId: null };

/** Existing workbench section anchors retain native scrolling during a session. */
export const isCreativeRouteChange = (hash: string): boolean => !hash || hash === '#' || hash.startsWith('#/');

function validProjectId(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0 && value.length <= 200 && value.trim() === value
    && value !== '.' && value !== '..' && !/[\\/?#]/.test(value)
    && !Array.from(value).some(char => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127);
}

/** A single encoded local identifier; never accept a URL, path or additional route segments. */
export function directorProjectHash(projectId: unknown): string | null {
  if (!validProjectId(projectId)) return null;
  try { return `#/director/${encodeURIComponent(projectId)}`; } catch { return null; }
}

export function parseCreativeLocation(hash: unknown): CreativeLocation {
  const fallback: CreativeLocation = { view: 'projects', projectId: null };
  if (hash === '#/director' || hash === '#/director/') return { view: 'director', projectId: null };
  if (typeof hash !== 'string' || !hash.startsWith('#/director/')) return fallback;
  const segment = hash.slice('#/director/'.length);
  if (!segment || segment.includes('/')) return fallback;
  try {
    const projectId = decodeURIComponent(segment);
    return directorProjectHash(projectId) ? { view: 'creative', projectId } : fallback;
  } catch { return fallback; }
}

interface CreativeLocationBrowser {
  location: Pick<Location, 'pathname' | 'search' | 'hash'>;
  history: Pick<History, 'state' | 'replaceState'>;
}

/** Keep the current origin/path/query and history entry. No draft or browser storage is touched. */
export function replaceCreativeLocation(projectId: string | null, directorIndex = false, browser: CreativeLocationBrowser = window): void {
  const hash = projectId ? directorProjectHash(projectId) ?? '' : directorIndex ? '#/director' : '';
  if (browser.location.hash === hash) return;
  browser.history.replaceState(browser.history.state, '', `${browser.location.pathname}${browser.location.search}${hash}`);
}
