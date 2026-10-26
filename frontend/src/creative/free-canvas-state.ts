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

export function fitFreeViewport(nodes: readonly FreeNodeBounds[], width: number, height: number): FreeViewport {
  const valid = nodes.filter(node => coordinate(node.x) && coordinate(node.y) && finite(node.width) && finite(node.height) && node.width > 0 && node.height > 0);
  if (!valid.length || !finite(width) || !finite(height) || width < 1 || height < 1) return { x: 24, y: 28, zoom: 1 };
  const left = Math.min(...valid.map(node => node.x)), top = Math.min(...valid.map(node => node.y));
  const right = Math.max(...valid.map(node => node.x + node.width)), bottom = Math.max(...valid.map(node => node.y + node.height));
  const zoom = clampFreeZoom(Math.min(1, Math.max(1, width - 80) / (right - left), Math.max(1, height - 110) / (bottom - top)));
  return { x: bounded((width - (right - left) * zoom) / 2 - left * zoom), y: bounded((height - (bottom - top) * zoom) / 2 - top * zoom), zoom };
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
