import { ApiError } from '../api';
import { freeNodePosition, MAX_FREE_ZOOM, MIN_FREE_ZOOM } from './free-canvas-state';
import type { FreeCanvasLayout, FreeNodePosition, FreePoint, FreeViewport } from './free-canvas-state';

export interface CanvasDisplayNode extends FreeNodePosition { type: string }
export interface SavedCanvasLayout {
  projectId: string;
  revision: number;
  layout: FreeCanvasLayout;
  nodeTypes: Record<string, string>;
}
export interface CanvasLayoutPatch {
  expected_layout_revision: number;
  viewport: FreeViewport;
  nodes: Array<{ node_id: string; position: FreePoint; type?: string; config?: Record<string, never> }>;
}
const record = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const text = (v: unknown): v is string => typeof v === 'string' && v.trim().length > 0;
const revision = (v: unknown): v is number => typeof v === 'number' && Number.isSafeInteger(v) && v >= 0;
const coordinate = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v) && Math.abs(v) <= 10_000_000;
const point = (v: unknown): v is FreePoint => record(v) && coordinate(v.x) && coordinate(v.y);
const view = (v: unknown): v is FreeViewport => point(v) && record(v) && typeof v.zoom === 'number' && Number.isFinite(v.zoom) && v.zoom >= MIN_FREE_ZOOM && v.zoom <= MAX_FREE_ZOOM;
const invalid = (): never => { throw new ApiError('CANVAS_LAYOUT_INVALID', '已保存的画布布局暂时无法读取，本地位置已保留。', 0); };

/** GET currently has no project_id: bind it to the requested project, never infer it from a node. */
export function parseSavedCanvas(value: unknown, projectId: string): SavedCanvasLayout {
  if (!text(projectId) || !record(value) || value.schema_version !== 1 || !revision(value.layout_revision) || !view(value.viewport) || !Array.isArray(value.nodes) || (value.project_id !== undefined && value.project_id !== projectId)) return invalid();
  const positions: Record<string, FreePoint> = Object.create(null), nodeTypes: Record<string, string> = Object.create(null);
  for (const node of value.nodes) {
    if (!record(node) || !text(node.node_id) || !text(node.type) || !point(node.position) || Object.hasOwn(positions, node.node_id)) return invalid();
    positions[node.node_id] = { x: node.position.x, y: node.position.y };
    nodeTypes[node.node_id] = node.type;
  }
  return { projectId, revision: value.layout_revision, nodeTypes, layout: { version: 1, projectId, viewport: { ...value.viewport }, positions } };
}

const displayType: Record<string, string> = {
  脚本: 'script', 分镜设置: 'script', 分镜图: 'storyboard', 镜头视频: 'video', 视频生成: 'video', 作品: 'asset',
  script: 'script', storyboard: 'storyboard', shot: 'shot', image: 'image', video: 'video', asset: 'asset',
};

/** Only presentation fields. Existing type/config/binding/edges and hidden nodes stay server-owned. */
export function buildCanvasLayoutPatch(saved: SavedCanvasLayout, layout: FreeCanvasLayout, nodes: readonly CanvasDisplayNode[]): CanvasLayoutPatch {
  if (saved.projectId !== layout.projectId || !revision(saved.revision) || !view(layout.viewport)) return invalid();
  const ids = new Set<string>();
  const patch: CanvasLayoutPatch = {
    expected_layout_revision: saved.revision, viewport: { ...layout.viewport },
    nodes: nodes.map((node, index) => {
      if (!text(node.id) || ids.has(node.id)) return invalid();
      ids.add(node.id);
      const position = freeNodePosition(layout, node, index);
      if (!point(position)) return invalid();
      if (Object.hasOwn(saved.nodeTypes, node.id)) return { node_id: node.id, position };
      const type = displayType[node.type];
      if (!type) throw new ApiError('CANVAS_NODE_TYPE_UNSUPPORTED', '此节点的布局类型暂不支持保存，本地位置已保留。', 0);
      return { node_id: node.id, position, type, config: {} };
    }),
  };
  // A temporarily hidden real node can still have a locally unsaved position.
  // Only update it when the server already knows its identity and position.
  for (const [id, position] of Object.entries(layout.positions)) {
    const previous = saved.layout.positions[id];
    if (!ids.has(id) && Object.hasOwn(saved.nodeTypes, id) && previous && (previous.x !== position.x || previous.y !== position.y)) {
      if (!point(position)) return invalid();
      patch.nodes.push({ node_id: id, position: { ...position } });
    }
  }
  return patch;
}

export function sameVisibleCanvasLayout(a: FreeCanvasLayout, b: FreeCanvasLayout, nodes: readonly CanvasDisplayNode[]): boolean {
  if (a.projectId !== b.projectId || a.viewport.x !== b.viewport.x || a.viewport.y !== b.viewport.y || a.viewport.zoom !== b.viewport.zoom) return false;
  return nodes.every((node, index) => {
    const left = freeNodePosition(a, node, index), right = freeNodePosition(b, node, index);
    return left.x === right.x && left.y === right.y;
  });
}

/** A read never overwrites a differing local cache or edits made while the read was pending. */
export function shouldReviewCanvasRead(local: FreeCanvasLayout, saved: SavedCanvasLayout, nodes: readonly CanvasDisplayNode[], hasLocal: boolean, editedDuringRead: boolean): boolean {
  if (!hasLocal && !editedDuringRead) return false;
  if (!sameVisibleCanvasLayout(local, saved.layout, nodes)) return true;
  return Object.entries(local.positions).some(([id, value]) => {
    const remote = saved.layout.positions[id];
    return !remote || remote.x !== value.x || remote.y !== value.y;
  });
}

export function needsCanvasLayoutSave(saved: SavedCanvasLayout, layout: FreeCanvasLayout, nodes: readonly CanvasDisplayNode[]): boolean {
  return nodes.some(node => !Object.hasOwn(saved.nodeTypes, node.id)) ||
    !sameVisibleCanvasLayout(layout, saved.layout, nodes) || Object.entries(layout.positions).some(([id, position]) => {
      const prior = saved.layout.positions[id];
      return prior && (prior.x !== position.x || prior.y !== position.y);
    });
}

export function createCanvasLayoutApi(fetcher: typeof fetch = (...args) => fetch(...args)) {
  async function request(projectId: string, patch: CanvasLayoutPatch | undefined, signal?: AbortSignal): Promise<unknown> {
    if (!text(projectId)) throw new ApiError('PROJECT_REQUIRED', '请先选择项目。', 0);
    if (signal?.aborted) throw new ApiError('CANVAS_LAYOUT_ABORTED', '已停止等待布局。', 0);
    const controller = new AbortController(), abort = () => controller.abort();
    signal?.addEventListener('abort', abort, { once: true });
    const timer = setTimeout(abort, 20_000);
    let remove = () => {}, knownStatus = 0;
    const stopped = new Promise<never>((_, reject) => {
      const listener = () => reject(new Error('aborted'));
      controller.signal.addEventListener('abort', listener, { once: true });
      remove = () => controller.signal.removeEventListener('abort', listener);
    });
    try {
      const response = await Promise.race([fetcher('/api/projects/' + encodeURIComponent(projectId) + '/canvas', {
        method: patch ? 'PATCH' : 'GET', signal: controller.signal,
        ...(patch ? { headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch) } : {}),
      }), stopped]);
      if (!response.ok && response.status < 500) knownStatus = response.status;
      const body: unknown = await Promise.race([response.json(), stopped]);
      if (!response.ok) {
        const error = record(body) && record(body.error) ? body.error : {};
        throw new ApiError(text(error.code) ? error.code : 'HTTP_ERROR', text(error.message) ? error.message : '暂时无法同步画布布局。', response.status, error.detail);
      }
      return body;
    } catch (error) {
      if (knownStatus) throw error instanceof ApiError ? error : new ApiError('HTTP_ERROR', '布局保存被拒绝，本地位置已保留。', knownStatus);
      if (patch) throw new ApiError('CANVAS_LAYOUT_RESULT_UNKNOWN', '布局保存结果待核对，请重新读取；本地位置已保留。', 0);
      if (error instanceof ApiError) throw error;
      throw new ApiError('CANVAS_LAYOUT_READ_FAILED', '暂时无法读取已保存布局，本地位置已保留。', 0);
    } finally { clearTimeout(timer); remove(); signal?.removeEventListener('abort', abort); }
  }
  return {
    async read(projectId: string, signal?: AbortSignal) { return parseSavedCanvas(await request(projectId, undefined, signal), projectId); },
    async save(projectId: string, patch: CanvasLayoutPatch, signal?: AbortSignal) {
      const value = await request(projectId, patch, signal);
      try {
        if (!record(value) || value.project_id !== projectId || value.layout_revision !== patch.expected_layout_revision + 1 || value.config_changed !== false || !Array.isArray(value.invalidated) || value.invalidated.length) return invalid();
        const saved = parseSavedCanvas(value.canvas, projectId);
        if (saved.revision !== value.layout_revision) return invalid();
        return saved;
      } catch { throw new ApiError('CANVAS_LAYOUT_RESULT_UNKNOWN', '布局回执需要核对，请重新读取；本地位置已保留。', 0); }
    },
  };
}
export const canvasLayoutApi = createCanvasLayoutApi();
