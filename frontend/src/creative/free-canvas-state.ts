export interface FreePoint { x: number; y: number }
export interface FreeViewport extends FreePoint { zoom: number }
export interface FreeCanvasLayout {
  version: 1;
  projectId: string;
  viewport: FreeViewport;
  positions: Record<string, FreePoint>;
}
export interface FreeNodePosition { id: string; x?: number; y?: number }
export interface FreeNodeBounds extends FreePoint { width: number; height: number }

export const FREE_NODE_WIDTH = 288;
export const FREE_TEXT_HEIGHT = 196;
export const FREE_MEDIA_HEIGHT = 326;
export const FREE_PANEL_WIDTH = 440;
export const FREE_PANEL_HEIGHT = 380;
export const FREE_PANEL_GAP = 14;
export const MIN_FREE_ZOOM = 0.15;
export const MAX_FREE_ZOOM = 2;
const LIMIT = 10_000_000;
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const coordinate = (value: unknown): value is number => finite(value) && Math.abs(value) <= LIMIT;
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
const bounded = (value: number): number => finite(value) ? Math.max(-LIMIT, Math.min(LIMIT, value)) : 0;

export const freeCanvasStorageKey = (projectId: string): string => `material-studio-free-canvas-v1:${encodeURIComponent(projectId)}`;
export const clampFreeZoom = (zoom: number): number => finite(zoom) ? Math.max(MIN_FREE_ZOOM, Math.min(MAX_FREE_ZOOM, zoom)) : 1;
export const initialFreeLayout = (projectId: string): FreeCanvasLayout => ({ version: 1, projectId, positions: {}, viewport: { x: 24, y: 28, zoom: 0.9 } });
export const defaultFreePosition = (index: number): FreePoint => ({ x: 32 + index % 3 * 344, y: 32 + Math.floor(index / 3) * 386 });

export function freeNodePosition(layout: FreeCanvasLayout, node: FreeNodePosition, index: number): FreePoint {
  if (Object.hasOwn(layout.positions, node.id)) return { ...layout.positions[node.id] };
  const fallback = defaultFreePosition(index);
  return { x: coordinate(node.x) ? node.x : fallback.x, y: coordinate(node.y) ? node.y : fallback.y };
}

/** The inspector lives in the same world coordinates as its node, never in screen pixels. */
export function freePanelBounds(node: FreeNodeBounds): FreeNodeBounds {
  return { x: node.x, y: node.y + node.height + FREE_PANEL_GAP, width: FREE_PANEL_WIDTH, height: FREE_PANEL_HEIGHT };
}

/** Movement is measured in screen pixels; layout coordinates stay in the canvas world. */
export function moveFreeNode(start: FreePoint, delta: FreePoint, zoom: number): FreePoint {
  const scale = clampFreeZoom(zoom);
  return { x: bounded(start.x + delta.x / scale), y: bounded(start.y + delta.y / scale) };
}

export function panFreeViewport(view: FreeViewport, delta: FreePoint): FreeViewport {
  return { ...view, x: bounded(view.x + delta.x), y: bounded(view.y + delta.y) };
}

/** Zoom is anchored at the pointer, not at the origin. */
export function zoomFreeViewport(view: FreeViewport, requestedZoom: number, anchor: FreePoint): FreeViewport {
  const zoom = clampFreeZoom(requestedZoom), ratio = zoom / clampFreeZoom(view.zoom);
  return { x: bounded(anchor.x - (anchor.x - view.x) * ratio), y: bounded(anchor.y - (anchor.y - view.y) * ratio), zoom };
}

export function fitFreeViewport(nodes: readonly FreeNodeBounds[], width: number, height: number, leftInset = 0, rightReserve = 0): FreeViewport {
  const valid = nodes.filter(node => coordinate(node.x) && coordinate(node.y) && finite(node.width) && finite(node.height) && node.width > 0 && node.height > 0);
  if (!valid.length || !finite(width) || !finite(height) || width < 1 || height < 1) return { x: 24, y: 28, zoom: 1 };
  const workingWidth = width - Math.max(0, Math.min(finite(rightReserve) ? rightReserve : 0, width - 1));
  const inset = Math.max(0, Math.min(finite(leftInset) ? leftInset : 0, workingWidth - 200));
  const left = Math.min(...valid.map(node => node.x)), top = Math.min(...valid.map(node => node.y));
  const right = Math.max(...valid.map(node => node.x + node.width)), bottom = Math.max(...valid.map(node => node.y + node.height));
  // Fit into the uncovered area: caption above, view/selection controls below,
  // conversation to the left and creation tools to the right.
  const availableHeight = Math.max(1, height - 56 - 132);
  const zoom = clampFreeZoom(Math.min(1, Math.max(1, workingWidth - inset - 80) / (right - left), availableHeight / (bottom - top)));
  return { x: bounded(inset + (workingWidth - inset - (right - left) * zoom) / 2 - left * zoom),
    y: bounded(56 + (availableHeight - (bottom - top) * zoom) / 2 - top * zoom), zoom };
}

/** Reveal a selected node and its inspector once, without changing zoom or moving oversized content above the viewport. */
export function revealFreeViewport(view: FreeViewport, bounds: readonly FreeNodeBounds[], width: number, height: number, leftInset = 0): FreeViewport {
  const inset = Math.max(0, Math.min(leftInset, width - 200));
  if (inset) { const shifted = { ...view, x: view.x - inset }; const reveal = revealFreeViewport(shifted, bounds, width - inset, height); return reveal === shifted ? view : { ...reveal, x: reveal.x + inset }; }
  const valid = bounds.filter(item => coordinate(item.x) && coordinate(item.y) && finite(item.width) && finite(item.height) && item.width > 0 && item.height > 0);
  if (!valid.length || !finite(width) || !finite(height) || width < 1 || height < 1) return view;
  const left = Math.min(...valid.map(item => item.x)) * view.zoom + view.x;
  const top = Math.min(...valid.map(item => item.y)) * view.zoom + view.y;
  const right = Math.max(...valid.map(item => item.x + item.width)) * view.zoom + view.x;
  const bottom = Math.max(...valid.map(item => item.y + item.height)) * view.zoom + view.y;
  const shift = (start: number, end: number, near: number, far: number): number => {
    if (end - start > far - near) return near - start;
    if (start < near) return near - start;
    return end > far ? far - end : 0;
  };
  const dx = shift(left, right, 12, Math.max(12, width - 12));
  const dy = shift(top, bottom, 52, Math.max(52, height - 100));
  return dx || dy ? panFreeViewport(view, { x: dx, y: dy }) : view;
}

/** Keep selection controls inside the uncovered work area; shrink the whole world only when it cannot fit. */
export function revealFreeWorkingViewport(view: FreeViewport, bounds: readonly FreeNodeBounds[], width: number, height: number, leftInset = 0, rightReserve = 0): FreeViewport {
  if (!finite(width) || !finite(height) || width < 1 || height < 1) return view;
  const valid = bounds.filter(item => coordinate(item.x) && coordinate(item.y) && finite(item.width) && finite(item.height) && item.width > 0 && item.height > 0);
  if (!valid.length) return view;
  const workingWidth = width - Math.max(0, Math.min(finite(rightReserve) ? rightReserve : 0, width - 1));
  const inset = Math.max(0, Math.min(finite(leftInset) ? leftInset : 0, workingWidth - 200));
  const worldWidth = Math.max(...valid.map(item => item.x + item.width)) - Math.min(...valid.map(item => item.x));
  const worldHeight = Math.max(...valid.map(item => item.y + item.height)) - Math.min(...valid.map(item => item.y));
  // These are the same horizontal 12px, top 52px and bottom 100px margins used by reveal.
  const availableWidth = Math.max(1, workingWidth - inset - 24), availableHeight = Math.max(1, height - 152);
  const zoom = clampFreeZoom(Math.min(view.zoom, availableWidth / worldWidth, availableHeight / worldHeight));
  const candidate = zoom < view.zoom ? zoomFreeViewport(view, zoom, { x: inset + (workingWidth - inset) / 2, y: 52 + availableHeight / 2 }) : view;
  return revealFreeViewport(candidate, valid, workingWidth, height, inset);
}

/** Only this namespace's layout is restored; no status, confirmation, content, or ordering is accepted. */
export function parseFreeLayout(raw: string | null, projectId: string): FreeCanvasLayout | null {
  if (!raw) return null;
  try {
    const value: unknown = JSON.parse(raw);
    if (!record(value) || value.version !== 1 || value.projectId !== projectId || !record(value.viewport) || !record(value.positions)) return null;
    const view = value.viewport;
    if (!coordinate(view.x) || !coordinate(view.y) || !finite(view.zoom) || view.zoom < MIN_FREE_ZOOM || view.zoom > MAX_FREE_ZOOM) return null;
    const positions: Record<string, FreePoint> = Object.create(null) as Record<string, FreePoint>;
    const entries = Object.entries(value.positions);
    if (entries.length > 10_000) return null;
    for (const [id, point] of entries) {
      if (!id || !record(point) || !coordinate(point.x) || !coordinate(point.y)) return null;
      positions[id] = { x: point.x, y: point.y };
    }
    return { version: 1, projectId, positions, viewport: { x: view.x, y: view.y, zoom: view.zoom } };
  } catch { return null; }
}

export function serializeFreeLayout(layout: FreeCanvasLayout): string {
  // Use an explicit schema even if a caller accidentally adds business fields.
  return JSON.stringify({ version: 1, projectId: layout.projectId,
    viewport: { x: layout.viewport.x, y: layout.viewport.y, zoom: layout.viewport.zoom },
    positions: Object.fromEntries(Object.entries(layout.positions).map(([id, point]) => [id, { x: point.x, y: point.y }])) });
}

export type FreeCanvasTool = 'select' | 'pan';
export interface FreeSelectableBounds extends FreeNodeBounds { id: string }
export type FreeCanvasShortcut = 'delete' | 'undo_delete' | 'select_tool' | 'pan_tool' | null;

/** Convert viewport-relative screen points to world coordinates at the gesture's starting zoom. */
export function freeWorldPoint(screen: FreePoint, viewport: FreeViewport): FreePoint {
  const zoom = clampFreeZoom(viewport.zoom);
  return { x: bounded((screen.x - viewport.x) / zoom), y: bounded((screen.y - viewport.y) / zoom) };
}
export function freeMarqueeBounds(start: FreePoint, end: FreePoint, viewport: FreeViewport): FreeNodeBounds {
  const a = freeWorldPoint(start, viewport), b = freeWorldPoint(end, viewport);
  return { x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), width: Math.abs(b.x - a.x), height: Math.abs(b.y - a.y) };
}
export function freeMarqueeHits(bounds: FreeNodeBounds, nodes: readonly FreeSelectableBounds[]): string[] {
  if (bounds.width <= 0 || bounds.height <= 0) return [];
  return nodes.filter(node => node.x < bounds.x + bounds.width && node.x + node.width > bounds.x && node.y < bounds.y + bounds.height && node.y + node.height > bounds.y).map(node => node.id);
}
/** Shift toggles against the selection at gesture start, never repeatedly against each move. */
export function mergeFreeSelection(current: readonly string[], hits: readonly string[], toggle: boolean): string[] {
  const uniqueHits = [...new Set(hits)];
  if (!toggle) return uniqueHits;
  const toggled = new Set(uniqueHits);
  return [...new Set(current.filter(id => !toggled.has(id))), ...uniqueHits.filter(id => !current.includes(id))];
}
/** One common bounded delta preserves the group's spacing even near the world limit. */
export function moveFreeNodeGroup(positions: Readonly<Record<string, FreePoint>>, ids: readonly string[], screenDelta: FreePoint, zoom: number): Record<string, FreePoint> {
  const entries = [...new Set(ids)].filter(id => Object.hasOwn(positions, id)).map(id => [id, positions[id]] as const);
  if (!entries.length) return { ...positions };
  const dx = Math.max(-LIMIT - Math.min(...entries.map(([, p]) => p.x)), Math.min(LIMIT - Math.max(...entries.map(([, p]) => p.x)), screenDelta.x / clampFreeZoom(zoom)));
  const dy = Math.max(-LIMIT - Math.min(...entries.map(([, p]) => p.y)), Math.min(LIMIT - Math.max(...entries.map(([, p]) => p.y)), screenDelta.y / clampFreeZoom(zoom)));
  return { ...positions, ...Object.fromEntries(entries.map(([id, p]) => [id, { x: p.x + dx, y: p.y + dy }])) };
}
/** Deletion requires canvas/node focus; text-editing always owns its own undo and delete keys. */
export function freeCanvasShortcut(input: { key: string; ctrlKey?: boolean; metaKey?: boolean; altKey?: boolean; shiftKey?: boolean; editable: boolean; canvasFocus: boolean }): FreeCanvasShortcut {
  if (input.editable || input.altKey) return null;
  const modified = input.ctrlKey || input.metaKey;
  if (modified && !input.shiftKey && input.key.toLowerCase() === 'z') return 'undo_delete';
  if (!input.canvasFocus || modified) return null;
  if (input.key === 'Delete' || input.key === 'Backspace') return 'delete';
  if (input.key.toLowerCase() === 'v') return 'select_tool';
  if (input.key.toLowerCase() === 'h') return 'pan_tool';
  return null;
}
