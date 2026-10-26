export const WORKFLOW_STAGES = [
  { id: 'brief', label: '需求与商品资料' }, { id: 'script', label: '视频脚本' },
  { id: 'plan', label: '分镜计划' }, { id: 'storyboard', label: '生成分镜图' },
  { id: 'review', label: '分镜审核' }, { id: 'generation', label: '选择生成模式' },
  { id: 'results', label: '检查结果与合成' }, { id: 'export', label: '导出与结构模板' },
] as const;
export type WorkflowStageId = typeof WORKFLOW_STAGES[number]['id'];
export interface CanvasPoint { x: number; y: number }
export interface CanvasViewport extends CanvasPoint { zoom: number }
export interface CanvasLayout { version: 1; projectId: string; viewport: CanvasViewport; positions: Record<string, CanvasPoint> }
export interface CanvasNodeBounds extends CanvasPoint { width: number; height: number }
export const MIN_CANVAS_ZOOM = 0.25;
export const MAX_CANVAS_ZOOM = 2;
export const CANVAS_NODE_WIDTH = 248;
export const CANVAS_STAGE_HEIGHT = 224;
export const CANVAS_SHOT_HEIGHT = 324;
const finite = (value: unknown): value is number => typeof value === 'number' && Number.isFinite(value);
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
export const canvasStorageKey = (projectId: string): string => 'material-studio-workflow-canvas-v1:' + encodeURIComponent(projectId);
export const stageNodeId = (stageId: string): string => 'stage:' + stageId;
export const shotNodeId = (shotId: string): string => 'shot:' + shotId;
export const clampCanvasZoom = (zoom: number): number => finite(zoom) ? Math.min(MAX_CANVAS_ZOOM, Math.max(MIN_CANVAS_ZOOM, zoom)) : 1;
export function defaultStagePosition(index: number): CanvasPoint {
  return { x: 40 + (index < 4 ? index : 7 - index) * 290, y: index < 4 ? 40 : 314 };
}
export function defaultShotPosition(index: number): CanvasPoint { return { x: 40 + index % 4 * 290, y: 628 + Math.floor(index / 4) * 362 }; }
export function initialCanvasLayout(projectId: string): CanvasLayout {
  return { version: 1, projectId, viewport: { x: 16, y: 16, zoom: 0.7 }, positions: {} };
}
/** The point under the cursor stays fixed when zoom changes. */
export function zoomCanvasAt(view: CanvasViewport, requestedZoom: number, anchor: CanvasPoint): CanvasViewport {
  const zoom = clampCanvasZoom(requestedZoom), ratio = zoom / view.zoom;
  return { x: anchor.x - (anchor.x - view.x) * ratio, y: anchor.y - (anchor.y - view.y) * ratio, zoom };
}
export function moveCanvasNode(start: CanvasPoint, screenDelta: CanvasPoint, zoom: number): CanvasPoint {
  return { x: start.x + screenDelta.x / zoom, y: start.y + screenDelta.y / zoom };
}
/** Focused controls stay visible even when their node is outside the current viewport. */
export function revealCanvasControl(view: CanvasViewport, screenBounds: CanvasNodeBounds, width: number, height: number): CanvasViewport {
  const margin = 16;
  const shift = (start: number, size: number, available: number) => start < margin ? margin - start : Math.max(0, start + size - available + margin) * -1;
  return { ...view, x: view.x + shift(screenBounds.x, screenBounds.width, width), y: view.y + shift(screenBounds.y, screenBounds.height, height) };
}
export function fitCanvasViewport(bounds: CanvasNodeBounds[], width: number, height: number): CanvasViewport {
  if (!bounds.length || width <= 0 || height <= 0) return { x: 16, y: 16, zoom: 1 };
  const left = Math.min(...bounds.map(node => node.x)), top = Math.min(...bounds.map(node => node.y));
  const right = Math.max(...bounds.map(node => node.x + node.width)), bottom = Math.max(...bounds.map(node => node.y + node.height));
  const zoom = clampCanvasZoom(Math.min(1, (width - 48) / Math.max(1, right - left), (height - 48) / Math.max(1, bottom - top)));
  return { x: (width - (right - left) * zoom) / 2 - left * zoom, y: (height - (bottom - top) * zoom) / 2 - top * zoom, zoom };
}
/** Only layout is stored; a saved layout never restores task status or product content. */
export function parseCanvasLayout(raw: string | null, projectId: string): CanvasLayout | null {
  if (!raw) return null;
  try {
    const value: unknown = JSON.parse(raw);
    if (!record(value) || value.version !== 1 || value.projectId !== projectId || !record(value.viewport) || !record(value.positions)) return null;
    const view = value.viewport;
    if (!finite(view.x) || !finite(view.y) || !finite(view.zoom) || view.zoom < MIN_CANVAS_ZOOM || view.zoom > MAX_CANVAS_ZOOM) return null;
    const positions: Record<string, CanvasPoint> = {};
    for (const [id, point] of Object.entries(value.positions)) {
      if ((!id.startsWith('stage:') && !id.startsWith('shot:')) || !record(point) || !finite(point.x) || !finite(point.y)) return null;
      positions[id] = { x: point.x, y: point.y };
    }
    return { version: 1, projectId, viewport: { x: view.x, y: view.y, zoom: view.zoom }, positions };
  } catch { return null; }
}
export function canvasStatus(status: string | undefined, hasContent = false): { label: string; tone: string } {
  if (!status?.trim()) return { label: hasContent ? '状态未提供' : '未开始', tone: 'neutral' };
  const known: Record<string, { label: string; tone: string }> = {
    queued: { label: '排队中', tone: 'waiting' }, pending: { label: '等待中', tone: 'waiting' },
    running: { label: '生成中', tone: 'working' }, processing: { label: '处理中', tone: 'working' },
    succeeded: { label: '已成功', tone: 'success' }, success: { label: '已成功', tone: 'success' },
    completed: { label: '已完成', tone: 'success' }, ready: { label: '已就绪', tone: 'success' },
    failed: { label: '失败', tone: 'error' }, error: { label: '错误', tone: 'error' },
    rejected: { label: '已驳回', tone: 'error' }, approved: { label: '已通过', tone: 'success' },
    cancelled: { label: '已取消', tone: 'neutral' }, canceled: { label: '已取消', tone: 'neutral' },
    draft: { label: '草稿', tone: 'neutral' }, stale: { label: '需重新核对', tone: 'waiting' },
    not_started: { label: '未开始', tone: 'neutral' },
    saved: {label:'已保存',tone:'neutral'}, unavailable:{label:'暂不可用',tone:'waiting'}, unknown:{label:'待核对',tone:'neutral'},
  };
  return known[status] ?? { label: `状态：${status}`, tone: 'neutral' };
}
