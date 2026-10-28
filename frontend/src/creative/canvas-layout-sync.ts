import { ApiError } from '../api';
import { freeNodePosition, MAX_FREE_ZOOM, MIN_FREE_ZOOM } from './free-canvas-state';
import type { FreeCanvasLayout, FreeNodePosition, FreePoint, FreeViewport } from './free-canvas-state';

export interface CanvasDisplayNode extends FreeNodePosition { type: string }
export interface CanvasNodeRecord {
  node_id: string;
  type: string;
  position: FreePoint;
  config: Record<string, unknown>;
  binding: { script_id?: string; script_version?: number; shot_id?: string; asset_id?: string };
  config_revision: number;
  last_execution: Record<string, unknown> | null;
  asset: { id: string; kind: string; mime?: string; width?: number; height?: number; duration_ms?: number | null } | null;
}
export interface CanvasEdge { from: string; to: string }
export interface CanvasNodeMutation {
  node_id: string;
  type?: string;
  position?: FreePoint;
  config?: Record<string, unknown>;
  binding?: CanvasNodeRecord['binding'];
  expected_config_revision?: number;
}
export interface CanvasMutation {
  nodes?: CanvasNodeMutation[];
  delete_node_ids?: string[];
  edges?: CanvasEdge[];
}
export interface SavedCanvasLayout {
  projectId: string;
  revision: number;
  layout: FreeCanvasLayout;
  nodeTypes: Record<string, string>;
  nodes: CanvasNodeRecord[];
  edges: CanvasEdge[];
  deletionOperationId?: string;
  deletedNodeIds?: string[];
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

/** Older GET responses omit project_id; bind them to the requested project. */
export function parseSavedCanvas(value: unknown, projectId: string): SavedCanvasLayout {
  if (!text(projectId) || !record(value) || value.schema_version !== 1 || !revision(value.layout_revision) || !view(value.viewport) || !Array.isArray(value.nodes) || (value.project_id !== undefined && value.project_id !== projectId)) return invalid();
  if (value.deleted_node_ids !== undefined && (!Array.isArray(value.deleted_node_ids) || !value.deleted_node_ids.every(text) || new Set(value.deleted_node_ids).size !== value.deleted_node_ids.length)) return invalid();
  const deletedNodeIds = value.deleted_node_ids as string[] | undefined;
  const positions: Record<string, FreePoint> = Object.create(null), nodeTypes: Record<string, string> = Object.create(null);
  const nodes: CanvasNodeRecord[] = [];
  for (const node of value.nodes) {
    if (!record(node) || !text(node.node_id) || !text(node.type) || !point(node.position) || Object.hasOwn(positions, node.node_id)) return invalid();
    positions[node.node_id] = { x: node.position.x, y: node.position.y };
    nodeTypes[node.node_id] = node.type;
    if (node.config !== undefined && !record(node.config) || node.binding !== undefined && !record(node.binding) || node.config_revision !== undefined && !revision(node.config_revision)) return invalid();
    nodes.push({ node_id: node.node_id, type: node.type, position: { ...node.position },
      config: record(node.config) ? node.config : {}, binding: record(node.binding) ? node.binding : {},
      config_revision: revision(node.config_revision) ? node.config_revision : 1,
      last_execution: record(node.last_execution) ? node.last_execution : null,
      asset: record(node.asset) && text(node.asset.id) && text(node.asset.kind) ? node.asset as unknown as NonNullable<CanvasNodeRecord['asset']> : null });
  }
  const edges: CanvasEdge[] = [];
  if (value.edges !== undefined && !Array.isArray(value.edges)) return invalid();
  for (const edge of (value.edges as unknown[] | undefined) ?? []) {
    if (!record(edge) || !text(edge.from) || !text(edge.to) || !Object.hasOwn(nodeTypes, edge.from) || !Object.hasOwn(nodeTypes, edge.to)) return invalid();
    edges.push({ from: edge.from, to: edge.to });
  }
  return { projectId, revision: value.layout_revision, nodeTypes, nodes, edges, ...(deletedNodeIds ? { deletedNodeIds: [...deletedNodeIds] } : {}), layout: { version: 1, projectId, viewport: { ...value.viewport }, positions } };
}

const displayType: Record<string, string> = {
  脚本: 'script', 分镜设置: 'script', 分镜图: 'storyboard', 镜头视频: 'video', 视频生成: 'video', 作品: 'asset',
  文本: 'text', 图片生成: 'image', 导入素材: 'asset', text: 'text', script: 'script', storyboard: 'storyboard', shot: 'shot', image: 'image', video: 'video', asset: 'asset',
};

/** Only presentation fields. Existing type/config/binding/edges and hidden nodes stay server-owned. */
export function buildCanvasLayoutPatch(saved: SavedCanvasLayout, layout: FreeCanvasLayout, nodes: readonly CanvasDisplayNode[]): CanvasLayoutPatch {
  if (saved.projectId !== layout.projectId || !revision(saved.revision) || !view(layout.viewport)) return invalid();
  const ids = new Set<string>(), deleted = new Set(saved.deletedNodeIds ?? []);
  const patch: CanvasLayoutPatch = {
    expected_layout_revision: saved.revision, viewport: { ...layout.viewport },
    nodes: nodes.flatMap((node, index) => {
      if (!text(node.id) || ids.has(node.id)) return invalid();
      ids.add(node.id);
      // The reserved script identity is created atomically with the real script body.
      // A temporarily visible manual draft must not manufacture an unbound canonical node.
      if (!Object.hasOwn(saved.nodeTypes, node.id) && (node.id === 'script' || deleted.has(node.id))) return [];
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

const samePoint = (a: FreePoint, b: FreePoint): boolean => a.x === b.x && a.y === b.y;
const sameViewport = (a: FreeViewport, b: FreeViewport): boolean => samePoint(a, b) && a.zoom === b.zoom;

/** Reconcile a fresh GET against the last known server baseline, preserving pending local moves. */
export function reconcileCanvasRead(local: FreeCanvasLayout, saved: SavedCanvasLayout, nodes: readonly CanvasDisplayNode[], hasLocal: boolean, editedDuringRead: boolean, previouslySaved?: SavedCanvasLayout | null): { review: boolean; layout: FreeCanvasLayout } {
  if (!hasLocal && !editedDuringRead) return { review: false, layout: saved.layout };
  if (!previouslySaved || previouslySaved.projectId !== saved.projectId) {
    // Across reloads there is no trusted common baseline: never discard a differing cache.
    const review = !sameVisibleCanvasLayout(local, saved.layout, nodes) || Object.entries(local.positions).some(([id, value]) => {
      const remote = saved.layout.positions[id];
      return !remote || !samePoint(remote, value);
    });
    return { review, layout: saved.layout };
  }
  const base = previouslySaved.layout;
  const localViewChanged = !sameViewport(local.viewport, base.viewport);
  const remoteViewChanged = !sameViewport(saved.layout.viewport, base.viewport);
  let review = localViewChanged && remoteViewChanged && !sameViewport(local.viewport, saved.layout.viewport);
  const positions: Record<string, FreePoint> = Object.create(null);
  for (const [id, remote] of Object.entries(saved.layout.positions)) {
    const previous = Object.hasOwn(base.positions, id) ? base.positions[id] : undefined, pending = Object.hasOwn(local.positions, id) ? local.positions[id] : undefined;
    if (previous && pending) {
      const localMoved = !samePoint(pending, previous), remoteMoved = !samePoint(remote, previous);
      if (localMoved && remoteMoved && !samePoint(pending, remote)) review = true;
      positions[id] = { ...(localMoved ? pending : remote) };
    } else {
      // New server nodes have no local position intent unless the user moved a local draft.
      if (!previous && pending && !samePoint(pending, remote)) review = true;
      positions[id] = { ...remote };
    }
  }
  for (const [id, pending] of Object.entries(local.positions)) {
    if (Object.hasOwn(saved.layout.positions, id)) continue;
    const previous = Object.hasOwn(base.positions, id) ? base.positions[id] : undefined;
    if (previous) {
      // A deletion concurrent with a local move requires an explicit choice.
      if (!samePoint(pending, previous)) { review = true; positions[id] = { ...pending }; }
    } else {
      // A new display node may be waiting for its first layout save (e.g. a mirror plan).
      positions[id] = { ...pending };
    }
  }
  return { review, layout: { ...saved.layout, viewport: { ...(localViewChanged ? local.viewport : saved.layout.viewport) }, positions } };
}

/** Review is needed only for competing changes, or when a reload has no common baseline. */
export function shouldReviewCanvasRead(local: FreeCanvasLayout, saved: SavedCanvasLayout, nodes: readonly CanvasDisplayNode[], hasLocal: boolean, editedDuringRead: boolean, previouslySaved?: SavedCanvasLayout | null): boolean {
  return reconcileCanvasRead(local, saved, nodes, hasLocal, editedDuringRead, previouslySaved).review;
}

export function needsCanvasLayoutSave(saved: SavedCanvasLayout, layout: FreeCanvasLayout, nodes: readonly CanvasDisplayNode[]): boolean {
  const deleted = new Set(saved.deletedNodeIds ?? []);
  const persistable = nodes.filter(node => Object.hasOwn(saved.nodeTypes, node.id) || node.id !== 'script' && !deleted.has(node.id));
  return persistable.some(node => !Object.hasOwn(saved.nodeTypes, node.id)) ||
    !sameVisibleCanvasLayout(layout, saved.layout, persistable) || Object.entries(layout.positions).some(([id, position]) => {
      const prior = saved.layout.positions[id];
      return prior && (prior.x !== position.x || prior.y !== position.y);
    });
}

/** A new derived representation must exist before it can have a persistent delete/undo record. */
export function buildCanvasDeletionPreparation(saved: SavedCanvasLayout, layout: FreeCanvasLayout, visible: readonly CanvasDisplayNode[], change: CanvasMutation): CanvasLayoutPatch | null {
  const ids = change.delete_node_ids ?? [];
  if (!ids.every(text) || new Set(ids).size !== ids.length) return invalid();
  const missing = ids.filter(id => !Object.hasOwn(saved.nodeTypes, id) && !saved.deletedNodeIds?.includes(id));
  if (!missing.length) return null;
  const patch = buildCanvasLayoutPatch(saved, layout, visible);
  if (missing.some(id => !patch.nodes.some(node => node.node_id === id))) {
    throw new ApiError('CANVAS_NODE_NOT_SAVED', '节点尚未保存，请稍后再试。', 0);
  }
  // Only ordinary layout fields are prepared; the caller's edges/config/deletion stay in the final mutation.
  return patch;
}

/** Merge layout and explicit business edits in one optimistic request. Never spread read-only GET fields. */
export function buildCanvasMutationPatch(saved: SavedCanvasLayout, layout: FreeCanvasLayout, visible: readonly CanvasDisplayNode[], change: CanvasMutation): CanvasMutation & { expected_layout_revision: number; viewport: FreeViewport } {
  const removed = new Set(change.delete_node_ids ?? []);
  const base = buildCanvasLayoutPatch(saved, layout, visible.filter(node => !removed.has(node.id)));
  const nodes = new Map<string, CanvasNodeMutation>(base.nodes.filter(node => !removed.has(node.node_id)).map(node => [node.node_id, node]));
  for (const node of change.nodes ?? []) {
    if (!text(node.node_id) || removed.has(node.node_id)) return invalid();
    const next: CanvasNodeMutation = { ...nodes.get(node.node_id), node_id: node.node_id };
    for (const key of ['type', 'position', 'config', 'binding', 'expected_config_revision'] as const) {
      if (node[key] !== undefined) Object.assign(next, { [key]: node[key] });
    }
    nodes.set(node.node_id, next);
  }
  return { expected_layout_revision: saved.revision, viewport: { ...layout.viewport }, nodes: [...nodes.values()],
    ...(change.delete_node_ids ? { delete_node_ids: [...change.delete_node_ids] } : {}),
    ...(change.edges ? { edges: change.edges.map(edge => ({ from: edge.from, to: edge.to })) } : {}) };
}

export function createCanvasLayoutApi(fetcher: typeof fetch = (...args) => fetch(...args)) {
  async function request(projectId: string, patch: (CanvasMutation & { expected_layout_revision: number; viewport?: FreeViewport }) | undefined, signal?: AbortSignal): Promise<unknown> {
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
    async mutate(projectId: string, patch: CanvasMutation & { expected_layout_revision: number; viewport?: FreeViewport }, signal?: AbortSignal) {
      const value = await request(projectId, patch, signal);
      try {
        if (!record(value) || value.project_id !== projectId || value.layout_revision !== patch.expected_layout_revision + 1 || typeof value.config_changed !== 'boolean' || !Array.isArray(value.invalidated)) return invalid();
        const saved = parseSavedCanvas(value.canvas, projectId);
        if (saved.revision !== value.layout_revision || (value.deletion_operation_id != null && !text(value.deletion_operation_id))) return invalid();
        return { ...saved, ...(text(value.deletion_operation_id) ? { deletionOperationId: value.deletion_operation_id } : {}) };
      } catch { throw new ApiError('CANVAS_LAYOUT_RESULT_UNKNOWN', '保存结果需要核对，请重新读取；当前编辑已保留。', 0); }
    },
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
