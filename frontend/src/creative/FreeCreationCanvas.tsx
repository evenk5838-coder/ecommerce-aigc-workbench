import { useCallback, useEffect, useId, useRef, useState } from 'react';
import type { KeyboardEvent, PointerEvent, ReactNode } from 'react';
import { FileText, Film, Grip, Image as ImageIcon, Maximize, Minus, MousePointer2, Move, Plus, RotateCcw, Type, Upload, X, Hand, Trash2, Undo2, Link2 } from 'lucide-react';
import { defaultFreePosition, fitFreeViewport, FREE_MEDIA_HEIGHT, FREE_NODE_WIDTH, FREE_TEXT_HEIGHT, freeCanvasStorageKey, freeNodePosition, freePanelBounds, initialFreeLayout, MAX_FREE_ZOOM, MIN_FREE_ZOOM, moveFreeNodeGroup, freeMarqueeBounds, freeMarqueeHits, mergeFreeSelection, freeCanvasShortcut, panFreeViewport, parseFreeLayout, revealFreeWorkingViewport, serializeFreeLayout, zoomFreeViewport } from './free-canvas-state';
import type { FreeCanvasLayout, FreeCanvasTool, FreeNodeBounds, FreePoint } from './free-canvas-state';
import { buildCanvasDeletionPreparation, buildCanvasLayoutPatch, buildCanvasMutationPatch, canvasLayoutApi, needsCanvasLayoutSave, reconcileCanvasRead } from './canvas-layout-sync';
import type { CanvasEdge, CanvasMutation, SavedCanvasLayout } from './canvas-layout-sync';
import { isFreeReferenceSource, isFreeReferenceTarget, moveFreeReference, resolveFreeReference } from './free-canvas-reference';
import type { FreeReferenceDraft, FreeReferenceNode } from './free-canvas-reference';
import './FreeCreationCanvas.css';

export interface FreeCreationNode extends FreeReferenceNode {
  id: string;
  type: string;
  title: string;
  summary?: string;
  image?: string;
  video?: string;
  status?: string;
  statusLabel?: string;
  x?: number;
  y?: number;
}
export interface FreeCanvasController {
  read: () => Promise<SavedCanvasLayout>;
  mutate: (change: CanvasMutation) => Promise<SavedCanvasLayout>;
  positionForNewNode: () => FreePoint;
}
export interface FreeCreationCanvasProps {
  projectId: string;
  nodes: readonly FreeCreationNode[];
  referenceEdges?: CanvasEdge[];
  selectedNodeId: string | null;
  onSelect: (nodeId: string) => void | boolean;
  onSelectionChange?: (ids: string[], origin?: 'user' | 'programmatic') => void;
  onConnectReference?: (sourceId: string, targetId: string) => void | Promise<void>;
  children?: ReactNode;
  ariaLabel?: string;
  leftInset?: number;
  onSnapshot?: (snapshot: SavedCanvasLayout) => void;
  onController?: (controller: FreeCanvasController | null) => void;
  onAddNode?: (type: 'text' | 'image' | 'video', position: FreePoint) => void;
  onImport?: (files: File[], position: FreePoint) => void;
  onDeselect?: () => void | boolean;
  onDeleteNodes?: (ids: string[]) => Promise<void>;
  onUndoDelete?: () => Promise<void>;
  canUndoDelete?: boolean;
  deletionBusy?: boolean;
  toolbarContent?: ReactNode;
}
interface Gesture {
  pointerId: number;
  kind: 'pan' | 'move' | 'marquee';
  id?: string;
  start: FreePoint;
  screenStart: FreePoint;
  before: FreeCanvasLayout;
  positions?: Record<string, FreePoint>;
  nodeIds?: string[];
  selectionBefore: string[];
  toggle: boolean;
  moved: boolean;
}

const isVisualNode = (node: FreeCreationNode) => ['分镜图', '图片生成', '视频生成', '导入素材', '镜头视频', '作品', 'image', 'video', 'asset', 'storyboard', 'shot'].includes(node.type);
const isVideoNode = (node: FreeCreationNode) => ['镜头视频', '视频生成', '作品', 'video', 'shot'].includes(node.type);
const isIndependentGenerationNode = (node: FreeCreationNode) => ['图片生成', '视频生成', 'image', 'video'].includes(node.type) && node.id !== 'generation' && !/^(image|video):/.test(node.id);
const heightFor = (node: FreeCreationNode): number => node.image || node.video || isVisualNode(node) ? FREE_MEDIA_HEIGHT : FREE_TEXT_HEIGHT;
const interactive = 'button,a,input,textarea,select,video,audio,summary,[contenteditable="true"],[data-free-controls]';
const statusLabels: Record<string, string> = { draft: '草稿', saved: '已保存', queued: '排队中', running: '生成中', processing: '处理中', succeeded: '已完成', completed: '已完成', ready: '已就绪', approved: '已确认', failed: '失败', unavailable: '产物不可用', rejected: '已驳回', stale: '需重新核对', interrupted: '已中断', canceled: '已取消', cancelled: '已取消' };
function readLayout(projectId: string): { layout: FreeCanvasLayout; warning: string; cached: boolean } {
  try {
    const raw = localStorage.getItem(freeCanvasStorageKey(projectId)), parsed = parseFreeLayout(raw, projectId);
    return { layout: parsed ?? initialFreeLayout(projectId), cached: !!parsed, warning: raw && !parsed ? '已存画布布局无法读取，暂用初始位置；项目内容不受影响。' : '' };
  } catch { return { layout: initialFreeLayout(projectId), cached: false, warning: '浏览器无法读取布局；可继续整理，项目内容不受影响。' }; }
}

/** Parent renders business content; this surface serializes explicit node edits with local layout saves. */
export default function FreeCreationCanvas(props: FreeCreationCanvasProps) {
  return <FreeCreationCanvasSession key={props.projectId} {...props} />;
}

function FreeCreationCanvasSession({ projectId, nodes, referenceEdges, selectedNodeId, onSelect, onSelectionChange, onConnectReference, children, ariaLabel = '自由创作画布', leftInset = 0, onSnapshot, onController, onAddNode, onImport, onDeselect, onDeleteNodes, onUndoDelete, canUndoDelete = false, deletionBusy = false, toolbarContent }: FreeCreationCanvasProps) {
  const edgeMarkerId = useId();
  const [initial] = useState(() => readLayout(projectId));
  const [layout, setLayout] = useState(initial.layout), [warning, setWarning] = useState(initial.warning);
  const [size, setSize] = useState({ width: 800, height: 640 });
  const [tool, setTool] = useState<FreeCanvasTool>('select');
  const [selectedIds, setSelectedIds] = useState<string[]>(selectedNodeId ? [selectedNodeId] : []);
  const selectionRef = useRef(selectedIds), previousParentSelection = useRef(selectedNodeId); selectionRef.current = selectedIds;
  const [marquee, setMarquee] = useState<FreeNodeBounds | null>(null);
  const [referenceDraft, setReferenceDraft] = useState<FreeReferenceDraft | null>(null);
  const referenceRef = useRef(referenceDraft); referenceRef.current = referenceDraft;
  const referencePointer = useRef<{ pointerId: number; start: FreePoint; moved: boolean } | null>(null);
  const lastSelectionNotice = useRef('');
  const [actionBusy, setActionBusy] = useState(false), [actionError, setActionError] = useState('');
  const actionLock = useRef(false);
  const [dragging, setDragging] = useState(false), [announcement, setAnnouncement] = useState('');
  const viewportRef = useRef<HTMLDivElement>(null), current = useRef(layout), gesture = useRef<Gesture | null>(null);
  const timer = useRef<number | null>(null), dirty = useRef(false), suppressClick = useRef<string | null>(null);
  const selectedHandled = useRef<string | null>(null);
  const panelContentRef = useRef<HTMLDivElement>(null);
  const [syncPhase, setSyncPhase] = useState<'loading' | 'ready' | 'saving' | 'review' | 'error'>('loading');
  const phaseRef = useRef(syncPhase);
  const setPhase = useCallback((value: typeof syncPhase) => { phaseRef.current = value; setSyncPhase(value); }, []);
  const [syncMessage, setSyncMessage] = useState(''), [savedChoice, setSavedChoice] = useState<SavedCanvasLayout | null>(null);
  const savedLayout = useRef<SavedCanvasLayout | null>(null), localChanged = useRef(false), editSequence = useRef(0);
  const hasLocal = useRef(initial.cached), syncRequest = useRef<AbortController | null>(null), nodesRef = useRef(nodes);
  nodesRef.current = nodes;
  const callbacks = useRef({ onSnapshot, onController, onSelectionChange, onConnectReference, leftInset, size });
  callbacks.current = { onSnapshot, onController, onSelectionChange, onConnectReference, leftInset, size };
  const operationQueue = useRef<Promise<unknown>>(Promise.resolve()), alive = useRef(true);
  const enqueue = useCallback(<T,>(operation: () => Promise<T>): Promise<T> => {
    const pending = operationQueue.current.catch(() => undefined).then(() => {
      if (!alive.current) throw new Error('画布已关闭，请重新打开项目。');
      return operation();
    });
    operationQueue.current = pending;
    return pending;
  }, []);
  const [dropActive, setDropActive] = useState(false);
  const uploadInput = useRef<HTMLInputElement>(null);
  const nodeIdsKey = JSON.stringify(nodes.map(node => node.id));
  const nodeLayoutKey = JSON.stringify(nodes.map(node => [node.id, node.type, node.x, node.y]));
  const storageKey = freeCanvasStorageKey(projectId);
  const selected = nodes.find(node => node.id === selectedNodeId);
  const hasPanel = !!selected && selectedIds.length === 1 && selectedIds[0] === selectedNodeId && children != null && children !== false;
  const operationsBusy = deletionBusy || actionBusy;
  const rightReserve = onAddNode || onImport || toolbarContent ? 82 : 0;
  const persistencePaused = useRef(operationsBusy); persistencePaused.current = operationsBusy;
  const notifySelection = useCallback((ids: readonly string[], origin: 'user' | 'programmatic') => {
    const key = JSON.stringify(ids);
    if (origin === 'programmatic' && key === lastSelectionNotice.current) return;
    lastSelectionNotice.current = key;
    callbacks.current.onSelectionChange?.([...ids], origin);
  }, []);

  useEffect(() => {
    const visible = new Set(nodesRef.current.map(node => node.id));
    let next = selectionRef.current.filter(id => visible.has(id));
    if (selectedNodeId && visible.has(selectedNodeId) && !next.includes(selectedNodeId)) next = [selectedNodeId];
    else if (!selectedNodeId && previousParentSelection.current !== selectedNodeId) next = [];
    previousParentSelection.current = selectedNodeId;
    if (next.length !== selectionRef.current.length || next.some((id, index) => id !== selectionRef.current[index])) { selectionRef.current = next; setSelectedIds(next); }
    notifySelection(next, 'programmatic');
  }, [selectedNodeId, nodeIdsKey, notifySelection]);

  const updateReference = (next: FreeReferenceDraft | null) => { referenceRef.current = next; setReferenceDraft(next); };
  const cancelReference = () => {
    referencePointer.current = null; updateReference(null); setDragging(false);
    setAnnouncement('已取消参考图连线');
  };
  const connectReference = async (targetId: string) => {
    const draft = referenceRef.current, connect = callbacks.current.onConnectReference;
    if (!draft || !connect || operationsBusy || actionLock.current) return;
    const connection = resolveFreeReference(draft, targetId, nodesRef.current);
    if (!connection) { setAnnouncement('请选择另一个图片或视频生成节点'); return; }
    referencePointer.current = null; updateReference(null); setDragging(false);
    actionLock.current = true; setActionBusy(true); setActionError(''); setAnnouncement('正在连接参考图');
    try {
      await connect(connection.sourceId, connection.targetId);
      if (alive.current) setAnnouncement('参考图已连接');
    } catch (error) {
      if (alive.current) setActionError(error instanceof Error ? error.message : '参考图尚未连接，请重试。');
    } finally { actionLock.current = false; if (alive.current) setActionBusy(false); }
  };
  const referenceAtPointer = (clientX: number, clientY: number): FreeReferenceDraft | null => {
    const draft = referenceRef.current, element = viewportRef.current;
    if (!draft || !element) return null;
    const rect = element.getBoundingClientRect();
    const hit = document.elementFromPoint(clientX, clientY)?.closest<HTMLElement>('[data-free-node]');
    const hitId = hit && element.contains(hit) ? hit.dataset.freeNode ?? null : null;
    return moveFreeReference(draft, { x: clientX - rect.left, y: clientY - rect.top }, current.current.viewport, hitId, nodesRef.current);
  };
  const armReference = (node: FreeCreationNode) => {
    if (!onConnectReference || !isFreeReferenceSource(node) || operationsBusy || actionLock.current || gesture.current || referencePointer.current) return false;
    const position = freeNodePosition(current.current, node, nodesRef.current.indexOf(node));
    updateReference({ sourceId: node.id, sourceAssetId: node.referenceImageAssetId!,
      point: { x: position.x + FREE_NODE_WIDTH, y: position.y + heightFor(node) / 2 }, targetId: null });
    setActionError(''); setAnnouncement('选择另一个图片或视频节点作为目标，Escape 取消');
    return true;
  };
  const beginReference = (event: PointerEvent<HTMLButtonElement>, node: FreeCreationNode) => {
    event.stopPropagation();
    if (event.button !== 0 || !armReference(node)) return;
    event.preventDefault();
    viewportRef.current?.focus({ preventScroll: true });
    viewportRef.current?.setPointerCapture(event.pointerId);
    referencePointer.current = { pointerId: event.pointerId, start: { x: event.clientX, y: event.clientY }, moved: false };
    setDragging(true);
  };

  useEffect(() => {
    const draft = referenceRef.current;
    if (draft && (!onConnectReference || !nodes.some(node => node.id === draft.sourceId && isFreeReferenceSource(node) && node.referenceImageAssetId === draft.sourceAssetId))) {
      referencePointer.current = null; referenceRef.current = null;
      setReferenceDraft(null); setDragging(false); setAnnouncement('来源图片已变化，请重新连接');
    }
  }, [nodes, onConnectReference]);

  const choose = (ids: readonly string[]): boolean => {
    const visible = new Set(nodesRef.current.map(node => node.id));
    const next = [...new Set(ids)].filter(id => visible.has(id));
    const primary = selectedNodeId && next.includes(selectedNodeId) ? selectedNodeId : next[0] ?? null;
    if (primary !== selectedNodeId) {
      if (primary ? onSelect(primary) === false : onDeselect?.() === false) return false;
    }
    selectionRef.current = next; setSelectedIds(next);
    notifySelection(next, 'user');
    setAnnouncement(next.length > 1 ? `已选择 ${next.length} 个节点` : next.length ? '已选择节点' : '已清空选择');
    return true;
  };
  const deleteSelection = async (ids = selectionRef.current) => {
    if (!onDeleteNodes || operationsBusy || actionLock.current || !ids.length) return;
    const present = ids.filter(id => nodesRef.current.some(node => node.id === id));
    if (!present.length) return;
    actionLock.current = true; setActionBusy(true); setActionError('');
    try { await onDeleteNodes(present); }
    catch (error) { setActionError(error instanceof Error ? error.message : '节点暂未移除，请重新读取画布核对。'); }
    finally { actionLock.current = false; if (alive.current) setActionBusy(false); }
  };
  const undoDelete = async () => {
    if (!onUndoDelete || !canUndoDelete || operationsBusy || actionLock.current) return;
    actionLock.current = true; setActionBusy(true); setActionError('');
    try { await onUndoDelete(); }
    catch (error) { setActionError(error instanceof Error ? error.message : '暂未恢复节点，请重新读取画布核对。'); }
    finally { actionLock.current = false; if (alive.current) setActionBusy(false); }
  };

  useEffect(() => {
    if (panelContentRef.current) panelContentRef.current.scrollTop = 0;
  }, [selectedNodeId]);

  const commit = useCallback((next: FreeCanvasLayout) => {
    localChanged.current = true; hasLocal.current = true; editSequence.current += 1;
    current.current = next; dirty.current = true; setLayout(next);
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => {
      try { localStorage.setItem(storageKey, serializeFreeLayout(current.current)); dirty.current = false; setWarning(''); }
      catch { setWarning('布局暂未保存到浏览器，请保持页面开启；项目内容不受影响。'); }
    }, 200);
  }, [storageKey]);

  const adoptSaved = useCallback((saved: SavedCanvasLayout, displayLayout = saved.layout) => {
    savedLayout.current = saved; current.current = displayLayout; localChanged.current = needsCanvasLayoutSave(saved, displayLayout, nodesRef.current);
    hasLocal.current = true; editSequence.current += 1;
    if (timer.current !== null) window.clearTimeout(timer.current);
    callbacks.current.onSnapshot?.(saved);
    setLayout(displayLayout); setSavedChoice(null); setSyncMessage(''); setPhase('ready');
    try { localStorage.setItem(storageKey, serializeFreeLayout(displayLayout)); dirty.current = false; setWarning(''); }
    catch { dirty.current = true; setWarning('布局已从项目读取，但浏览器缓存暂不可用。'); }
  }, [storageKey, setPhase]);

  const readSaved = useCallback((): Promise<SavedCanvasLayout> => enqueue(async () => {
    const controller = new AbortController(), sequence = editSequence.current, hadLocal = hasLocal.current;
    syncRequest.current = controller; setPhase('loading'); setSyncMessage(''); setSavedChoice(null);
    try {
      const saved = await canvasLayoutApi.read(projectId, controller.signal);
      if (controller.signal.aborted || !alive.current) throw new Error('已停止读取画布。');
      callbacks.current.onSnapshot?.(saved);
      const reconciled = reconcileCanvasRead(current.current, saved, nodesRef.current, hadLocal, editSequence.current !== sequence, savedLayout.current);
      if (reconciled.review) {
        setSavedChoice(saved); setPhase('review'); setSyncMessage('画布位置有另一份保存记录，请选择要继续使用的布局。');
      } else adoptSaved(saved, reconciled.layout);
      return saved;
    } catch (error) {
      if (!controller.signal.aborted && alive.current) {
        setPhase('error'); setSyncMessage(error instanceof Error ? error.message : '画布暂时无法读取，当前编辑已保留。');
      }
      throw error;
    } finally { if (syncRequest.current === controller) syncRequest.current = null; }
  }), [projectId, adoptSaved, enqueue, setPhase]);

  useEffect(() => {
    alive.current = true;
    void readSaved().catch(() => undefined);
    return () => { alive.current = false; syncRequest.current?.abort(); syncRequest.current = null; };
  }, [readSaved]);

  const mutate = useCallback((change: CanvasMutation): Promise<SavedCanvasLayout> => enqueue(async () => {
    if (!savedLayout.current || phaseRef.current === 'error' || phaseRef.current === 'review') throw new Error('请先处理画布的保存提示，当前编辑会保留。');
    let baseline = savedLayout.current, sequence = editSequence.current;
    // Validate the final change before a preparation PATCH can have any side effect.
    let patch = buildCanvasMutationPatch(baseline, current.current, nodesRef.current, change);
    const preparation = buildCanvasDeletionPreparation(baseline, current.current, nodesRef.current, change);
    const controller = new AbortController();
    const acceptWrite = (saved: SavedCanvasLayout, removedIds: readonly string[] = []) => {
      if (controller.signal.aborted || !alive.current) throw new Error('已停止等待保存结果，请重新打开项目核对。');
      if (sequence === editSequence.current) adoptSaved(saved);
      else {
        const positions = { ...saved.layout.positions, ...current.current.positions };
        for (const id of removedIds) delete positions[id];
        savedLayout.current = saved;
        current.current = { ...current.current, positions }; setLayout(current.current);
        localChanged.current = true; setPhase('ready'); setSyncMessage('');
        callbacks.current.onSnapshot?.(saved);
      }
    };
    syncRequest.current = controller; setPhase('saving');
    try {
      if (preparation) {
        const prepared = await canvasLayoutApi.save(projectId, preparation, controller.signal);
        acceptWrite(prepared);
        baseline = prepared; sequence = editSequence.current;
        if ((change.delete_node_ids ?? []).some(id => !Object.hasOwn(prepared.nodeTypes, id) && !prepared.deletedNodeIds?.includes(id))) throw new Error('节点尚未保存，请重新读取画布后再试。');
        patch = buildCanvasMutationPatch(baseline, current.current, nodesRef.current, change);
        setPhase('saving');
      }
      const saved = await canvasLayoutApi.mutate(projectId, patch, controller.signal);
      acceptWrite(saved, change.delete_node_ids);
      return saved;
    } catch (error) {
      if (alive.current && !controller.signal.aborted) { setPhase('error'); setSyncMessage(error instanceof Error ? error.message : '保存结果待核对，当前编辑已保留。'); }
      throw error;
    } finally { if (syncRequest.current === controller) syncRequest.current = null; }
  }), [projectId, enqueue, adoptSaved, setPhase]);

  const positionForNewNode = useCallback((): FreePoint => {
    const view = current.current.viewport, available = callbacks.current;
    const inset = Math.min(available.leftInset, Math.max(0, available.size.width - 220));
    return { x: (inset + (available.size.width - inset) / 2 - view.x) / view.zoom - FREE_NODE_WIDTH / 2,
      y: (Math.min(available.size.height / 2, 280) - view.y) / view.zoom - FREE_TEXT_HEIGHT / 2 };
  }, []);
  useEffect(() => {
    onController?.({ read: readSaved, mutate, positionForNewNode });
    return () => onController?.(null);
  }, [onController, readSaved, mutate, positionForNewNode]);

  useEffect(() => {
    if (syncPhase !== 'ready' || !savedLayout.current || dragging || operationsBusy) return;
    if (!localChanged.current && !needsCanvasLayoutSave(savedLayout.current, current.current, nodesRef.current)) return;
    localChanged.current = true;
    const debounce = window.setTimeout(() => {
      void enqueue(async () => {
        if (persistencePaused.current || !savedLayout.current || !localChanged.current || phaseRef.current !== 'ready') return;
        const sequence = editSequence.current, controller = new AbortController();
        let patch;
        try { patch = buildCanvasLayoutPatch(savedLayout.current, current.current, nodesRef.current); }
        catch (error) { setPhase('error'); setSyncMessage(error instanceof Error ? error.message : '画布暂时无法保存。'); return; }
        syncRequest.current = controller; setPhase('saving');
        try {
          const saved = await canvasLayoutApi.save(projectId, patch, controller.signal);
          if (controller.signal.aborted || !alive.current) return;
          savedLayout.current = saved; localChanged.current = editSequence.current !== sequence;
          callbacks.current.onSnapshot?.(saved);
          setSyncMessage(''); setPhase('ready');
        } catch (error) {
          if (!controller.signal.aborted && alive.current) { setPhase('error'); setSyncMessage(error instanceof Error ? error.message : '保存结果待核对，当前编辑已保留。'); }
        } finally { if (syncRequest.current === controller) syncRequest.current = null; }
      }).catch(() => undefined);
    }, 700);
    return () => window.clearTimeout(debounce);
  }, [projectId, layout, syncPhase, dragging, operationsBusy, nodeLayoutKey, enqueue, setPhase]);

  const keepLocal = () => {
    if (!savedChoice || syncRequest.current || dragging) return;
    savedLayout.current = savedChoice; localChanged.current = true;
    setSavedChoice(null); setSyncMessage(''); setPhase('ready');
  };

  useEffect(() => () => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    if (dirty.current) { try { localStorage.setItem(storageKey, serializeFreeLayout(current.current)); } catch { /* Never mutate business storage as a fallback. */ } }
  }, [storageKey]);

  useEffect(() => {
    const element = viewportRef.current;
    if (!element) return;
    const measure = () => setSize({ width: element.clientWidth, height: element.clientHeight });
    measure();
    const observer = new ResizeObserver(measure); observer.observe(element);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const element = viewportRef.current;
    if (!element) return;
    const wheel = (event: WheelEvent) => {
      if (event.target instanceof Element && event.target.closest('[data-free-controls],video,audio')) return;
      event.preventDefault();
      if (gesture.current || referencePointer.current) return;
      const before = current.current, rect = element.getBoundingClientRect();
      const factor = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? element.clientHeight : 1;
      const delta = { x: event.deltaX * factor, y: event.deltaY * factor };
      const viewport = event.ctrlKey || event.metaKey
        ? zoomFreeViewport(before.viewport, before.viewport.zoom * Math.exp(-delta.y * 0.002), { x: event.clientX - rect.left, y: event.clientY - rect.top })
        : panFreeViewport(before.viewport, event.shiftKey && !delta.x ? { x: -delta.y, y: 0 } : { x: -delta.x, y: -delta.y });
      commit({ ...before, viewport });
    };
    element.addEventListener('wheel', wheel, { passive: false });
    return () => element.removeEventListener('wheel', wheel);
  }, [commit]);

  useEffect(() => {
    if (syncPhase === 'loading' || dragging || selectedIds.length > 1) return;
    const token = selected ? `${selected.id}:${hasPanel}:${size.width}:${size.height}:${leftInset}:${rightReserve}` : null;
    if (!selected || token === selectedHandled.current || size.width < 1 || size.height < 1) { if (!selected) selectedHandled.current = null; return; }
    selectedHandled.current = token;
    const before = current.current;
    const node = { ...freeNodePosition(before, selected, nodes.indexOf(selected)), width: FREE_NODE_WIDTH, height: heightFor(selected) };
    const viewport = revealFreeWorkingViewport(before.viewport, hasPanel ? [node, freePanelBounds(node)] : [node], size.width, size.height, leftInset, rightReserve);
    if (viewport !== before.viewport) commit({ ...before, viewport });
  }, [selected, nodes, hasPanel, size, commit, syncPhase, leftInset, rightReserve, dragging, selectedIds.length]);

  const boundsFor = (source: FreeCanvasLayout): FreeNodeBounds[] => nodes.flatMap((node, index) => {
    const bounds = { ...freeNodePosition(source, node, index), width: FREE_NODE_WIDTH, height: heightFor(node) };
    return hasPanel && node.id === selectedNodeId ? [bounds, freePanelBounds(bounds)] : [bounds];
  });
  const fit = () => { const before = current.current; commit({ ...before, viewport: fitFreeViewport(boundsFor(before), size.width, size.height, leftInset, rightReserve) }); setAnnouncement('已适配全部节点'); };
  const reset = () => { const initialLayout = { ...initialFreeLayout(projectId), positions: Object.fromEntries(nodes.map((node, index) => [node.id, defaultFreePosition(index)])) }; commit({ ...initialLayout, viewport: fitFreeViewport(boundsFor(initialLayout), size.width, size.height, leftInset, rightReserve) }); setAnnouncement('已重置画布布局，内容和业务顺序保持不变'); };
  const zoom = (factor: number) => { const before = current.current; commit({ ...before, viewport: zoomFreeViewport(before.viewport, before.viewport.zoom * factor, { x: leftInset + (size.width - leftInset) / 2, y: size.height / 2 }) }); };
  const begin = (event: PointerEvent<HTMLElement>, node?: FreeCreationNode) => {
    if (gesture.current || operationsBusy || event.button !== 0 && event.button !== 1) return;
    const target = event.target;
    if (target instanceof Element && target.closest(interactive)) return;
    if (!node && target instanceof Element && target.closest('[data-free-node]')) return;
    if (referenceRef.current && event.button === 1) cancelReference();
    if (referenceRef.current && event.button === 0) {
      event.preventDefault(); event.stopPropagation();
      if (node) { suppressClick.current = node.id; void connectReference(node.id); }
      else cancelReference();
      return;
    }
    event.preventDefault(); event.stopPropagation();
    suppressClick.current = node?.id ?? null;
    const kind = event.button === 1 || tool === 'pan' ? 'pan' : node ? 'move' : 'marquee';
    const selectionBefore = [...selectionRef.current];
    if (node && kind === 'move') {
      const next = event.shiftKey ? mergeFreeSelection(selectionBefore, [node.id], true) : selectionBefore.includes(node.id) ? selectionBefore : [node.id];
      if (!choose(next) || !next.includes(node.id)) return;
    }
    const rect = viewportRef.current?.getBoundingClientRect();
    if (!rect) return;
    event.currentTarget.focus({ preventScroll: true }); event.currentTarget.setPointerCapture(event.pointerId);
    const nodeIds = kind === 'move' ? [...selectionRef.current] : undefined;
    const positions = nodeIds ? Object.fromEntries(nodes.filter(item => nodeIds.includes(item.id)).map(item => [item.id, freeNodePosition(current.current, item, nodes.indexOf(item))])) : undefined;
    gesture.current = { pointerId: event.pointerId, kind, id: node?.id, start: { x: event.clientX, y: event.clientY }, screenStart: { x: event.clientX - rect.left, y: event.clientY - rect.top }, before: current.current, nodeIds, positions, selectionBefore, toggle: event.shiftKey, moved: false };
    setDragging(true);
  };
  const move = (event: PointerEvent<HTMLDivElement>) => {
    if (referenceRef.current) {
      const pointer = referencePointer.current;
      if (pointer && pointer.pointerId !== event.pointerId) return;
      if (pointer && Math.hypot(event.clientX - pointer.start.x, event.clientY - pointer.start.y) >= 4) pointer.moved = true;
      const next = referenceAtPointer(event.clientX, event.clientY);
      if (next) updateReference(next);
      return;
    }
    const active = gesture.current;
    if (!active || active.pointerId !== event.pointerId) return;
    const delta = { x: event.clientX - active.start.x, y: event.clientY - active.start.y };
    if (!active.moved && Math.hypot(delta.x, delta.y) < 4) return;
    active.moved = true;
    if (active.kind === 'move' && active.positions && active.nodeIds) commit({ ...current.current, positions: { ...current.current.positions, ...moveFreeNodeGroup(active.positions, active.nodeIds, delta, active.before.viewport.zoom) } });
    else if (active.kind === 'marquee') setMarquee(freeMarqueeBounds(active.screenStart, { x: active.screenStart.x + delta.x, y: active.screenStart.y + delta.y }, active.before.viewport));
    else commit({ ...current.current, viewport: panFreeViewport(active.before.viewport, delta) });
  };
  const end = (event: PointerEvent<HTMLDivElement>) => {
    const pointer = referencePointer.current;
    if (pointer) {
      if (pointer.pointerId !== event.pointerId) return;
      referencePointer.current = null; setDragging(false);
      if (event.type !== 'pointerup') { cancelReference(); return; }
      if (pointer.moved || Math.hypot(event.clientX - pointer.start.x, event.clientY - pointer.start.y) >= 4) {
        const next = referenceAtPointer(event.clientX, event.clientY);
        if (next?.targetId) { updateReference(next); void connectReference(next.targetId); }
        else cancelReference();
      }
      return;
    }
    const active = gesture.current;
    if (!active || active.pointerId !== event.pointerId) return;
    if (event.type === 'pointerup') {
      if (active.kind === 'marquee') {
        if (active.moved) {
          const delta = { x: event.clientX - active.start.x, y: event.clientY - active.start.y };
          const bounds = freeMarqueeBounds(active.screenStart, { x: active.screenStart.x + delta.x, y: active.screenStart.y + delta.y }, active.before.viewport);
          const hits = freeMarqueeHits(bounds, nodes.map((node, index) => ({ id: node.id, ...freeNodePosition(current.current, node, index), width: FREE_NODE_WIDTH, height: heightFor(node) })));
          choose(mergeFreeSelection(active.selectionBefore, hits, active.toggle));
        } else if (!active.toggle) choose([]);
      } else if (active.kind === 'move' && !active.moved && !active.toggle && active.id) choose([active.id]);
    }
    gesture.current = null; setMarquee(null); setDragging(false);
  };
  const shortcut = (event: KeyboardEvent<HTMLElement>): boolean => {
    const target = event.target;
    const editable = target instanceof Element && !!target.closest('input,textarea,select,[contenteditable="true"],[contenteditable=""],[role="textbox"]');
    if (!editable && event.key === 'Escape' && referenceRef.current) {
      event.preventDefault(); event.stopPropagation(); cancelReference(); return true;
    }
    const canvasFocus = target === viewportRef.current || target instanceof Element && target.hasAttribute('data-free-node');
    const action = freeCanvasShortcut({ key: event.key, ctrlKey: event.ctrlKey, metaKey: event.metaKey, altKey: event.altKey, shiftKey: event.shiftKey, editable, canvasFocus });
    if (!action) return false;
    if (action === 'undo_delete') { if (!canUndoDelete || !onUndoDelete) return false; event.preventDefault(); event.stopPropagation(); void undoDelete(); return true; }
    if (action === 'delete') { if (!onDeleteNodes || !selectionRef.current.length) return false; event.preventDefault(); event.stopPropagation(); void deleteSelection(); return true; }
    event.preventDefault(); event.stopPropagation(); if (referenceRef.current) cancelReference(); setTool(action === 'pan_tool' ? 'pan' : 'select'); return true;
  };
  const nodeKey = (event: KeyboardEvent<HTMLElement>, node: FreeCreationNode, index: number) => {
    if (event.target !== event.currentTarget || shortcut(event)) return;
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); if (referenceRef.current) void connectReference(node.id); else choose(mergeFreeSelection(selectionRef.current, [node.id], event.shiftKey)); return; }
    if (operationsBusy) return;
    const step = event.shiftKey ? 48 : 12;
    const deltas: Record<string, FreePoint> = { ArrowLeft: { x: -step, y: 0 }, ArrowRight: { x: step, y: 0 }, ArrowUp: { x: 0, y: -step }, ArrowDown: { x: 0, y: step } };
    if (!deltas[event.key] && event.key !== 'Home') return;
    event.preventDefault(); event.stopPropagation();
    const before = current.current;
    if (event.key === 'Home') commit({ ...before, positions: { ...before.positions, [node.id]: defaultFreePosition(index) } });
    else {
      const group = selectionRef.current.includes(node.id) ? selectionRef.current : [node.id];
      const positions = Object.fromEntries(nodes.filter(item => group.includes(item.id)).map(item => [item.id, freeNodePosition(before, item, nodes.indexOf(item))]));
      commit({ ...before, positions: { ...before.positions, ...moveFreeNodeGroup(positions, group, deltas[event.key], 1) } });
    }
    setAnnouncement('所选节点的位置已调整');
  };
  const backgroundKey = (event: KeyboardEvent<HTMLDivElement>) => {
    if (shortcut(event) || event.target !== event.currentTarget) return;
    if (event.key === 'Escape') { event.preventDefault(); choose([]); return; }
    const step = event.shiftKey ? 120 : 40;
    const deltas: Record<string, FreePoint> = { ArrowLeft: { x: step, y: 0 }, ArrowRight: { x: -step, y: 0 }, ArrowUp: { x: 0, y: step }, ArrowDown: { x: 0, y: -step } };
    if (deltas[event.key]) { event.preventDefault(); const before = current.current; commit({ ...before, viewport: panFreeViewport(before.viewport, deltas[event.key]) }); }
    else if (event.key === 'Home') { event.preventDefault(); fit(); }
  };
  const view = layout.viewport;
  const controlsInset = Math.max(0, Math.min(leftInset, size.width - 220));
  const controlsPosition = { left: `calc(50% + ${controlsInset / 2}px)`, maxWidth: `calc(100% - ${controlsInset + 24}px)` };
  const selectedPosition = selected ? freeNodePosition(layout, selected, nodes.indexOf(selected)) : defaultFreePosition(0);
  const panelBounds = freePanelBounds({ ...selectedPosition, width: FREE_NODE_WIDTH, height: selected ? heightFor(selected) : 0 });
  const nodeIndexes = new Map(nodes.map((node, index) => [node.id, index]));
  const visibleEdges = new Map<string, CanvasEdge>();
  for (const edge of savedLayout.current?.edges ?? []) {
    const targetIndex = nodeIndexes.get(edge.to);
    if (!nodeIndexes.has(edge.from) || targetIndex === undefined) continue;
    // Explicit saved references replace prior generation-input edges, including an empty reference list.
    if (referenceEdges !== undefined && isIndependentGenerationNode(nodes[targetIndex])) continue;
    visibleEdges.set(JSON.stringify([edge.from, edge.to]), edge);
  }
  for (const edge of referenceEdges ?? []) {
    if (nodeIndexes.has(edge.from) && nodeIndexes.has(edge.to)) visibleEdges.set(JSON.stringify([edge.from, edge.to]), edge);
  }
  const referenceSourceIndex = referenceDraft ? nodeIndexes.get(referenceDraft.sourceId) : undefined;
  const referenceTargetIndex = referenceDraft?.targetId ? nodeIndexes.get(referenceDraft.targetId) : undefined;
  const referenceStart = referenceSourceIndex !== undefined ? (() => {
    const node = nodes[referenceSourceIndex], p = freeNodePosition(layout, node, referenceSourceIndex);
    return { x: p.x + FREE_NODE_WIDTH, y: p.y + heightFor(node) / 2 };
  })() : null;
  const referenceEnd = referenceTargetIndex !== undefined ? (() => {
    const node = nodes[referenceTargetIndex], p = freeNodePosition(layout, node, referenceTargetIndex);
    return { x: p.x, y: p.y + heightFor(node) / 2 };
  })() : referenceDraft?.point;

  return <section className={`free-creation-canvas${dropActive ? ' is-drop-active' : ''}`} aria-label={ariaLabel}>
    <div ref={viewportRef} className={`fcc-viewport is-${tool}-tool${dragging ? ' is-dragging' : ''}${dragging && gesture.current?.kind === 'marquee' ? ' is-marquee-selecting' : ''}${referenceDraft ? ' is-connecting-reference' : ''}`} tabIndex={0} role="region" aria-label="画布区域，V 选择，H 平移，Delete 删除所选，Home 适配全图" onPointerDown={event => begin(event)} onPointerMove={move} onPointerUp={end} onPointerCancel={end} onLostPointerCapture={end} onKeyDown={backgroundKey} onDragOver={event => { if (onImport && event.dataTransfer.types.includes('Files')) { event.preventDefault(); setDropActive(true); } }} onDragLeave={event => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDropActive(false); }} onDrop={event => { setDropActive(false); if (!onImport || !event.dataTransfer.files.length) return; event.preventDefault(); const rect = event.currentTarget.getBoundingClientRect(); onImport(Array.from(event.dataTransfer.files), { x: (event.clientX - rect.left - view.x) / view.zoom, y: (event.clientY - rect.top - view.y) / view.zoom }); }} style={{ backgroundPosition: `${view.x}px ${view.y}px`, backgroundSize: `${Math.max(12, 24 * view.zoom)}px ${Math.max(12, 24 * view.zoom)}px` }}>
      <div className="fcc-caption" data-free-controls style={{ left: leftInset + 20 }}><span><MousePointer2 size={13} />自由画布</span><small>{nodes.length} 个节点</small></div>
      {(syncPhase === 'error' || syncPhase === 'review') && <div className="fcc-sync needs-attention" data-free-controls role="status">
        <span>{syncMessage}</span>
        <button type="button" disabled={dragging} onClick={() => void readSaved().catch(() => undefined)}>重新读取</button>
        {syncPhase === 'review' && savedChoice && <div className="fcc-sync-choices"><button type="button" disabled={dragging} onClick={keepLocal}>保留当前布局</button><button type="button" disabled={dragging} onClick={() => adoptSaved(savedChoice)}>使用已保存布局</button></div>}
      </div>}
      {(onAddNode || onImport || toolbarContent) && <div className="fcc-create-tools" data-free-controls role="toolbar" aria-label="添加创作节点">
        {onAddNode && <><button type="button" onClick={() => onAddNode('text', positionForNewNode())} title="添加文本节点" aria-label="添加文本节点"><Type size={19}/><span>文本</span></button><button type="button" onClick={() => onAddNode('image', positionForNewNode())} title="添加图片节点" aria-label="添加图片节点"><ImageIcon size={19}/><span>图片</span></button><button type="button" onClick={() => onAddNode('video', positionForNewNode())} title="添加视频节点" aria-label="添加视频节点"><Film size={19}/><span>视频</span></button></>}
        {onImport && <><button type="button" onClick={() => uploadInput.current?.click()} title="导入图片或视频" aria-label="导入图片或视频"><Upload size={18}/><span>导入</span></button><input ref={uploadInput} type="file" accept="image/jpeg,image/png,image/webp,image/gif,video/mp4,video/quicktime,video/webm" multiple hidden onChange={event => { const files = Array.from(event.currentTarget.files ?? []); event.currentTarget.value = ''; if (files.length) onImport(files, positionForNewNode()); }}/></>}
        {toolbarContent}
      </div>}
      <div className="fcc-world" style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.zoom})` }}>
        <svg className="fcc-edges" width="1" height="1" aria-hidden="true" focusable="false"><defs><marker id={edgeMarkerId} viewBox="0 0 8 8" refX="8" refY="4" markerWidth="6" markerHeight="6" orient="auto"><polygon points="0,0 8,4 0,8" fill="var(--studio-muted)"/></marker></defs>{Array.from(visibleEdges, ([key, edge]) => {
          const fromIndex = nodeIndexes.get(edge.from)!, toIndex = nodeIndexes.get(edge.to)!;
          const source = nodes[fromIndex], target = nodes[toIndex], a = freeNodePosition(layout, source, fromIndex), b = freeNodePosition(layout, target, toIndex);
          const x1 = a.x + FREE_NODE_WIDTH, y1 = a.y + heightFor(source) / 2, x2 = b.x, y2 = b.y + heightFor(target) / 2, bend = Math.max(60, Math.abs(x2 - x1) / 2);
          return <path key={key} markerEnd={`url(#${edgeMarkerId})`} d={`M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`}/>;
        })}</svg>
        {referenceStart && referenceEnd && <svg className="fcc-edges fcc-reference-preview" width="1" height="1" aria-hidden="true" focusable="false"><path d={`M ${referenceStart.x} ${referenceStart.y} C ${referenceStart.x + Math.max(60, Math.abs(referenceEnd.x - referenceStart.x) / 2)} ${referenceStart.y}, ${referenceEnd.x - Math.max(60, Math.abs(referenceEnd.x - referenceStart.x) / 2)} ${referenceEnd.y}, ${referenceEnd.x} ${referenceEnd.y}`} /></svg>}
        {nodes.map((node, index) => {
          const position = freeNodePosition(layout, node, index), isSelected = selectedIds.includes(node.id);
          const connectable = referenceDraft && !!resolveFreeReference(referenceDraft, node.id, nodes);
          const status = node.statusLabel || (node.status ? statusLabels[node.status] || node.status : '');
          const tone = node.status && ['failed', 'rejected', 'error'].includes(node.status) ? 'error' : node.status && ['approved', 'ready', 'succeeded', 'completed'].includes(node.status) ? 'done' : '';
          return <article key={node.id} data-free-node={node.id} className={`fcc-node${isSelected ? ' is-selected' : ''}${connectable ? ' is-reference-target' : ''}${referenceDraft?.targetId === node.id ? ' is-reference-hovered' : ''}${referenceDraft?.sourceId === node.id ? ' is-reference-source' : ''}`} role="group" tabIndex={0} aria-label={`${node.title}，${referenceDraft ? connectable ? '可连接参考图，按回车连接' : '不可作为参考图目标' : `${isSelected ? '已选中，' : ''}按回车查看设置，方向键移动位置`}`} aria-current={isSelected ? 'true' : undefined} style={{ left: position.x, top: position.y, width: FREE_NODE_WIDTH, height: heightFor(node) }} onPointerDown={event => begin(event, node)} onKeyDown={event => nodeKey(event, node, index)} onClick={event => { if (event.target instanceof Element && event.target.closest(interactive)) return; if (suppressClick.current === node.id) { suppressClick.current = null; return; } if (referenceRef.current) { void connectReference(node.id); return; } if (tool === 'select') choose(mergeFreeSelection(selectionRef.current, [node.id], event.shiftKey)); }}>
            {onConnectReference && isFreeReferenceSource(node) && <button type="button" className="fcc-reference-port fcc-reference-output" aria-label={`以${node.title}作为参考图连接`} title="拖到图片或视频节点作为参考图，也可点击后选择目标" disabled={operationsBusy} onPointerDown={event => beginReference(event, node)} onClick={event => { event.stopPropagation(); if (event.detail === 0) armReference(node); }}><Link2 size={13}/></button>}
            {onConnectReference && isFreeReferenceTarget(node) && <button type="button" className="fcc-reference-port fcc-reference-input" aria-label={`连接参考图到${node.title}`} title="参考图输入" disabled={!connectable || operationsBusy} onPointerDown={event => event.stopPropagation()} onClick={event => { event.stopPropagation(); void connectReference(node.id); }}><span/></button>}
            <header><span className="fcc-node-type">{node.video || isVideoNode(node) ? <Film size={14} /> : node.image || isVisualNode(node) ? <ImageIcon size={14} /> : <FileText size={14} />}{node.type}</span><span className="fcc-node-header-actions">{onDeleteNodes && <button type="button" aria-label={`删除节点：${node.title}`} title="删除节点" disabled={operationsBusy} onClick={event => { event.stopPropagation(); void deleteSelection([node.id]); }}><Trash2 size={13}/></button>}<Grip size={15} className="fcc-node-grip" aria-hidden="true" /></span></header>
            <h3>{node.title}</h3>
            {node.video ? <div className="fcc-node-media"><video src={node.video} poster={node.image} controls preload="metadata" aria-label={node.title} /></div> : node.image ? <div className="fcc-node-media"><img src={node.image} alt={node.title} loading="lazy" draggable={false} /></div> : isVisualNode(node) ? <div className="fcc-node-media fcc-media-empty">{isVideoNode(node)?<Film size={32} strokeWidth={1.2}/>:<ImageIcon size={32} strokeWidth={1.2}/>}<span>{node.status && ['queued','running','processing'].includes(node.status)?'正在准备画面':'点击设置，开始创作'}</span></div> : null}
            {node.summary && <p className="fcc-node-summary">{node.summary}</p>}
            <footer>{status && <span className={`fcc-node-status ${tone}`}>{status}</span>}<span>{isSelected ? selectedIds.length > 1 ? '已选中' : '正在查看' : '点击查看设置'}</span></footer>
          </article>;
        })}
        {marquee && <div className="fcc-marquee" aria-hidden="true" style={{ left: marquee.x, top: marquee.y, width: marquee.width, height: marquee.height, borderWidth: 1 / view.zoom }}/> }
        <section key="node-inspector" className="fcc-node-panel" data-free-controls role="region" hidden={!hasPanel} aria-label={`${selected?.title ?? '当前节点'}设置`} style={{ display: hasPanel ? undefined : 'none', left: panelBounds.x, top: panelBounds.y, width: panelBounds.width, maxHeight: panelBounds.height }} onPointerDown={event => event.stopPropagation()}>
          <header><span>节点设置</span><strong>{selected?.title ?? ''}</strong>{onDeselect && <button type="button" aria-label="收起节点设置" onClick={() => choose([])}><X size={15}/></button>}</header><div ref={panelContentRef} className="fcc-panel-content">{children}</div>
        </section>
      </div>
      {!nodes.length && <div className="fcc-empty" style={{ left: leftInset }}><Move size={27} /><strong>把想法放上画布</strong><p>添加文本、图片或视频，也可以直接拖入素材。</p></div>}
      {referenceDraft && <div className="fcc-reference-hint" style={controlsPosition} data-free-controls role="status"><Link2 size={14}/><span>{referenceDraft.targetId ? '松开，连接为参考图' : '选择图片或视频节点 · Esc 取消'}</span><button type="button" aria-label="取消参考图连线" onClick={cancelReference}><X size={14}/></button></div>}
      {selectedIds.length > 0 && onDeleteNodes && <div className="fcc-selection-toolbar" style={controlsPosition} data-free-controls role="group" aria-label="所选节点操作"><span>已选 {selectedIds.length} 项</span><button type="button" disabled={operationsBusy} onClick={() => void deleteSelection()} aria-label={`删除选中的 ${selectedIds.length} 个节点`}><Trash2 size={14}/>{operationsBusy ? '处理中…' : '删除'}</button></div>}
      <div className="fcc-toolbar" style={controlsPosition} data-free-controls role="group" aria-label="画布视图操作">
        <button type="button" aria-label="选择工具" title="选择工具 V · 拖动空白框选，Shift 增减选择" aria-pressed={tool === 'select'} onClick={() => { if (referenceRef.current) cancelReference(); setTool('select'); }}><MousePointer2 size={17}/></button><button type="button" aria-label="手型平移工具" title="手型平移工具 H" aria-pressed={tool === 'pan'} onClick={() => { if (referenceRef.current) cancelReference(); setTool('pan'); }}><Hand size={17}/></button><i/>
        <button type="button" aria-label="缩小画布" disabled={view.zoom <= MIN_FREE_ZOOM} onClick={() => zoom(1 / 1.2)}><Minus size={16} /></button><output aria-label="画布缩放比例">{Math.round(view.zoom * 100)}%</output><button type="button" aria-label="放大画布" disabled={view.zoom >= MAX_FREE_ZOOM} onClick={() => zoom(1.2)}><Plus size={16} /></button><i />
        <button type="button" onClick={fit} aria-label="适配全图" title="适配全图"><Maximize size={15} /><span>适配</span></button><button type="button" onClick={reset} aria-label="重置布局" title="仅重置位置与视口，不修改业务内容"><RotateCcw size={15} /><span>重置布局</span></button>
        {onUndoDelete && <><i/><button type="button" aria-label="撤销删除" title="撤销删除 Ctrl / ⌘ + Z" disabled={!canUndoDelete || operationsBusy} onClick={() => void undoDelete()}><Undo2 size={16}/><span>撤销</span></button></>}
      </div>
      <div className="fcc-help" style={{ left: leftInset }}>{tool === 'select' ? '拖动空白框选 · Shift 增减选择' : '拖动画布平移'} · Ctrl / ⌘ + 滚动缩放</div>
      {dropActive && <div className="fcc-drop-hint">松开，添加到画布</div>}
      {actionError && <div className="fcc-action-error" role="alert" data-free-controls><span>{actionError}</span><button type="button" aria-label="关闭操作提示" onClick={() => setActionError('')}><X size={13}/></button></div>}
      {warning && <p className="fcc-warning" role="status" data-free-controls>{warning}</p>}
      <span className="fcc-sr-only" role="status" aria-live="polite">{announcement}</span>
    </div>
  </section>;
}
