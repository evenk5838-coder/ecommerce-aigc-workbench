import { useCallback, useEffect, useRef, useState } from 'react';
import type { KeyboardEvent, PointerEvent, ReactNode } from 'react';
import { FileText, Film, Grip, Image as ImageIcon, Maximize, Minus, MousePointer2, Move, Plus, RotateCcw } from 'lucide-react';
import { defaultFreePosition, fitFreeViewport, FREE_MEDIA_HEIGHT, FREE_NODE_WIDTH, FREE_TEXT_HEIGHT, freeCanvasStorageKey, freeNodePosition, freePanelBounds, initialFreeLayout, MAX_FREE_ZOOM, MIN_FREE_ZOOM, moveFreeNode, panFreeViewport, parseFreeLayout, revealFreeViewport, serializeFreeLayout, zoomFreeViewport } from './free-canvas-state';
import type { FreeCanvasLayout, FreeNodeBounds, FreePoint } from './free-canvas-state';
import { buildCanvasLayoutPatch, canvasLayoutApi, needsCanvasLayoutSave, shouldReviewCanvasRead } from './canvas-layout-sync';
import type { SavedCanvasLayout } from './canvas-layout-sync';
import './FreeCreationCanvas.css';

export interface FreeCreationNode {
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
export interface FreeCreationCanvasProps {
  projectId: string;
  nodes: readonly FreeCreationNode[];
  selectedNodeId: string | null;
  onSelect: (nodeId: string) => void;
  children?: ReactNode;
  ariaLabel?: string;
}
interface Gesture {
  pointerId: number; id?: string; start: FreePoint; before: FreeCanvasLayout;
  position?: FreePoint; moved: boolean;
}
const isVisualNode = (node: FreeCreationNode) => ['分镜图', '镜头视频', '作品'].includes(node.type);
const isVideoNode = (node: FreeCreationNode) => ['镜头视频', '作品'].includes(node.type);
const heightFor = (node: FreeCreationNode): number => node.image || node.video || isVisualNode(node) ? FREE_MEDIA_HEIGHT : FREE_TEXT_HEIGHT;
const interactive = 'button,a,input,textarea,select,video,audio,summary,[contenteditable="true"],[data-free-controls]';
const statusLabels: Record<string, string> = { draft: '草稿', saved: '已保存', queued: '排队中', running: '生成中', processing: '处理中', succeeded: '已完成', completed: '已完成', ready: '已就绪', approved: '已确认', failed: '失败', rejected: '已驳回', stale: '需重新核对', interrupted: '已中断', canceled: '已取消', cancelled: '已取消' };
function readLayout(projectId: string): { layout: FreeCanvasLayout; warning: string; cached: boolean } {
  try {
    const raw = localStorage.getItem(freeCanvasStorageKey(projectId)), parsed = parseFreeLayout(raw, projectId);
    return { layout: parsed ?? initialFreeLayout(projectId), cached: !!parsed, warning: raw && !parsed ? '已存画布布局无法读取，暂用初始位置；项目内容不受影响。' : '' };
  } catch { return { layout: initialFreeLayout(projectId), cached: false, warning: '浏览器无法读取布局；可继续整理，项目内容不受影响。' }; }
}

/** Content comes only from the parent. This component owns presentation layout, never production state. */
export default function FreeCreationCanvas(props: FreeCreationCanvasProps) {
  return <FreeCreationCanvasSession key={props.projectId} {...props} />;
}

function FreeCreationCanvasSession({ projectId, nodes, selectedNodeId, onSelect, children, ariaLabel = '自由创作画布' }: FreeCreationCanvasProps) {
  const [initial] = useState(() => readLayout(projectId));
  const [layout, setLayout] = useState(initial.layout), [warning, setWarning] = useState(initial.warning);
  const [size, setSize] = useState({ width: 800, height: 640 });
  const [dragging, setDragging] = useState(false), [announcement, setAnnouncement] = useState('');
  const viewportRef = useRef<HTMLDivElement>(null), current = useRef(layout), gesture = useRef<Gesture | null>(null);
  const timer = useRef<number | null>(null), dirty = useRef(false), suppressClick = useRef<string | null>(null);
  const selectedHandled = useRef<string | null>(null);
  const panelContentRef = useRef<HTMLDivElement>(null);
  const [syncPhase, setSyncPhase] = useState<'loading' | 'ready' | 'saving' | 'review' | 'error'>('loading');
  const [syncMessage, setSyncMessage] = useState(''), [savedChoice, setSavedChoice] = useState<SavedCanvasLayout | null>(null);
  const savedLayout = useRef<SavedCanvasLayout | null>(null), localChanged = useRef(false), editSequence = useRef(0);
  const hasLocal = useRef(initial.cached), syncRequest = useRef<AbortController | null>(null), nodesRef = useRef(nodes);
  nodesRef.current = nodes;
  const nodeLayoutKey = JSON.stringify(nodes.map(node => [node.id, node.type, node.x, node.y]));
  const storageKey = freeCanvasStorageKey(projectId);
  const selected = nodes.find(node => node.id === selectedNodeId);
  const hasPanel = !!selected && children != null && children !== false;

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

  const adoptSaved = useCallback((saved: SavedCanvasLayout) => {
    savedLayout.current = saved; current.current = saved.layout; localChanged.current = false;
    hasLocal.current = true; editSequence.current += 1;
    if (timer.current !== null) window.clearTimeout(timer.current);
    setLayout(saved.layout); setSavedChoice(null); setSyncMessage(''); setSyncPhase('ready');
    try { localStorage.setItem(storageKey, serializeFreeLayout(saved.layout)); dirty.current = false; setWarning(''); }
    catch { dirty.current = true; setWarning('布局已从项目读取，但浏览器缓存暂不可用。'); }
  }, [storageKey]);

  const readSaved = useCallback(async () => {
    if (syncRequest.current) return;
    const controller = new AbortController(), sequence = editSequence.current, hadLocal = hasLocal.current;
    syncRequest.current = controller; setSyncPhase('loading'); setSyncMessage(''); setSavedChoice(null);
    try {
      const saved = await canvasLayoutApi.read(projectId, controller.signal);
      if (controller.signal.aborted || syncRequest.current !== controller) return;
      if (shouldReviewCanvasRead(current.current, saved, nodesRef.current, hadLocal, editSequence.current !== sequence)) {
        setSavedChoice(saved); setSyncPhase('review'); setSyncMessage('本地布局与项目已保存布局不同，请选择保留哪一份。');
      } else adoptSaved(saved);
    } catch (error) {
      if (!controller.signal.aborted && syncRequest.current === controller) {
        setSyncPhase('error'); setSyncMessage(error instanceof Error ? error.message : '读取布局失败，本地位置已保留。');
      }
    } finally { if (syncRequest.current === controller) syncRequest.current = null; }
  }, [projectId, adoptSaved]);

  useEffect(() => {
    void readSaved();
    return () => { syncRequest.current?.abort(); syncRequest.current = null; };
  }, [readSaved]);

  useEffect(() => {
    if (syncPhase !== 'ready' || !savedLayout.current || dragging) return;
    if (!localChanged.current && !needsCanvasLayoutSave(savedLayout.current, current.current, nodesRef.current)) return;
    localChanged.current = true;
    const debounce = window.setTimeout(() => {
      if (syncRequest.current || !savedLayout.current || !localChanged.current) return;
      const sequence = editSequence.current, controller = new AbortController();
      let patch;
      try { patch = buildCanvasLayoutPatch(savedLayout.current, current.current, nodesRef.current); }
      catch (error) { setSyncPhase('error'); setSyncMessage(error instanceof Error ? error.message : '布局暂时无法同步。'); return; }
      syncRequest.current = controller; setSyncPhase('saving');
      void canvasLayoutApi.save(projectId, patch, controller.signal).then(saved => {
        if (controller.signal.aborted || syncRequest.current !== controller) return;
        savedLayout.current = saved; localChanged.current = editSequence.current !== sequence;
        setSyncMessage(''); setSyncPhase('ready');
      }).catch(error => {
        if (controller.signal.aborted || syncRequest.current !== controller) return;
        setSyncPhase('error'); setSyncMessage(error instanceof Error ? error.message : '保存结果待核对，本地位置已保留。');
      }).finally(() => { if (syncRequest.current === controller) syncRequest.current = null; });
    }, 700);
    return () => window.clearTimeout(debounce);
  }, [projectId, layout, syncPhase, dragging, nodeLayoutKey]);

  const keepLocal = () => {
    if (!savedChoice || syncRequest.current || dragging) return;
    savedLayout.current = savedChoice; localChanged.current = true;
    setSavedChoice(null); setSyncMessage(''); setSyncPhase('ready');
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
      if (gesture.current) return;
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
    if (syncPhase === 'loading') return;
    const token = selected ? `${selected.id}:${hasPanel}:${size.width}:${size.height}` : null;
    if (!selected || token === selectedHandled.current || size.width < 1 || size.height < 1) { if (!selected) selectedHandled.current = null; return; }
    selectedHandled.current = token;
    const before = current.current;
    const node = { ...freeNodePosition(before, selected, nodes.indexOf(selected)), width: FREE_NODE_WIDTH, height: heightFor(selected) };
    const viewport = revealFreeViewport(before.viewport, hasPanel ? [node, freePanelBounds(node)] : [node], size.width, size.height);
    if (viewport !== before.viewport) commit({ ...before, viewport });
  }, [selected, nodes, hasPanel, size, commit, syncPhase]);

  const boundsFor = (source: FreeCanvasLayout): FreeNodeBounds[] => nodes.flatMap((node, index) => {
    const bounds = { ...freeNodePosition(source, node, index), width: FREE_NODE_WIDTH, height: heightFor(node) };
    return hasPanel && node.id === selectedNodeId ? [bounds, freePanelBounds(bounds)] : [bounds];
  });
  const fit = () => { const before = current.current; commit({ ...before, viewport: fitFreeViewport(boundsFor(before), size.width, size.height) }); setAnnouncement('已适配全部节点'); };
  const reset = () => { const initialLayout = initialFreeLayout(projectId); commit({ ...initialLayout, viewport: fitFreeViewport(boundsFor(initialLayout), size.width, size.height) }); setAnnouncement('已重置画布布局，内容和业务顺序保持不变'); };
  const zoom = (factor: number) => { const before = current.current; commit({ ...before, viewport: zoomFreeViewport(before.viewport, before.viewport.zoom * factor, { x: size.width / 2, y: size.height / 2 }) }); };
  const begin = (event: PointerEvent<HTMLElement>, node?: FreeCreationNode, index = 0) => {
    if (gesture.current || event.button !== 0 && event.button !== 1) return;
    const target = event.target;
    if (target instanceof Element && target.closest(interactive)) return;
    if (!node && target instanceof Element && target.closest('[data-free-node]')) return;
    event.preventDefault(); event.stopPropagation();
    event.currentTarget.focus({ preventScroll: true }); event.currentTarget.setPointerCapture(event.pointerId);
    suppressClick.current = null;
    gesture.current = { pointerId: event.pointerId, ...(node ? { id: node.id, position: freeNodePosition(current.current, node, index) } : {}), start: { x: event.clientX, y: event.clientY }, before: current.current, moved: false };
  };
  const move = (event: PointerEvent<HTMLDivElement>) => {
    const active = gesture.current;
    if (!active || active.pointerId !== event.pointerId) return;
    const delta = { x: event.clientX - active.start.x, y: event.clientY - active.start.y };
    if (!active.moved && Math.hypot(delta.x, delta.y) < 4) return;
    active.moved = true; setDragging(true);
    if (active.id && active.position) commit({ ...current.current, positions: { ...current.current.positions, [active.id]: moveFreeNode(active.position, delta, active.before.viewport.zoom) } });
    else commit({ ...current.current, viewport: panFreeViewport(active.before.viewport, delta) });
  };
  const end = (event: PointerEvent<HTMLDivElement>) => {
    const active = gesture.current;
    if (!active || active.pointerId !== event.pointerId) return;
    if (active.id && active.moved) suppressClick.current = active.id;
    gesture.current = null; setDragging(false);
  };
  const nodeKey = (event: KeyboardEvent<HTMLElement>, node: FreeCreationNode, index: number) => {
    if (event.target !== event.currentTarget) return;
    if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(node.id); return; }
    const step = event.shiftKey ? 48 : 12;
    const deltas: Record<string, FreePoint> = { ArrowLeft: { x: -step, y: 0 }, ArrowRight: { x: step, y: 0 }, ArrowUp: { x: 0, y: -step }, ArrowDown: { x: 0, y: step } };
    if (!deltas[event.key] && event.key !== 'Home') return;
    event.preventDefault(); event.stopPropagation();
    const before = current.current, point = freeNodePosition(before, node, index);
    if (event.key === 'Home') { const positions = { ...before.positions }; delete positions[node.id]; commit({ ...before, positions }); }
    else commit({ ...before, positions: { ...before.positions, [node.id]: moveFreeNode(point, deltas[event.key], 1) } });
    setAnnouncement(`${node.title}的位置已调整`);
  };
  const backgroundKey = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.target !== event.currentTarget) return;
    const step = event.shiftKey ? 120 : 40;
    const deltas: Record<string, FreePoint> = { ArrowLeft: { x: step, y: 0 }, ArrowRight: { x: -step, y: 0 }, ArrowUp: { x: 0, y: step }, ArrowDown: { x: 0, y: -step } };
    if (deltas[event.key]) { event.preventDefault(); const before = current.current; commit({ ...before, viewport: panFreeViewport(before.viewport, deltas[event.key]) }); }
    else if (event.key === 'Home') { event.preventDefault(); fit(); }
  };
  const view = layout.viewport;
  const selectedPosition = selected ? freeNodePosition(layout, selected, nodes.indexOf(selected)) : defaultFreePosition(0);
  const panelBounds = freePanelBounds({ ...selectedPosition, width: FREE_NODE_WIDTH, height: selected ? heightFor(selected) : 0 });

  return <section className="free-creation-canvas" aria-label={ariaLabel}>
    <div ref={viewportRef} className={`fcc-viewport${dragging ? ' is-dragging' : ''}`} tabIndex={0} role="region" aria-label="画布区域，方向键平移，Home 适配全图" onPointerDown={event => begin(event)} onPointerMove={move} onPointerUp={end} onPointerCancel={end} onLostPointerCapture={end} onKeyDown={backgroundKey} style={{ backgroundPosition: `${view.x}px ${view.y}px`, backgroundSize: `${24 * view.zoom}px ${24 * view.zoom}px` }}>
      <div className="fcc-caption" data-free-controls><span><MousePointer2 size={13} />自由画布</span><small>{nodes.length} 个节点</small></div>
      <div className={`fcc-sync${syncPhase === 'error' || syncPhase === 'review' ? ' needs-attention' : ''}`} data-free-controls role="status">
        <span>{syncPhase === 'loading' ? '读取项目布局…' : syncPhase === 'saving' ? '保存布局…' : syncPhase === 'error' || syncPhase === 'review' ? syncMessage : localChanged.current ? '本地位置待同步' : '布局已同步'}</span>
        <button type="button" disabled={syncPhase === 'loading' || syncPhase === 'saving' || dragging} onClick={() => void readSaved()}>重新读取</button>
        {syncPhase === 'review' && savedChoice && <div className="fcc-sync-choices"><button type="button" disabled={dragging} onClick={keepLocal}>保留本地并保存</button><button type="button" disabled={dragging} onClick={() => adoptSaved(savedChoice)}>使用已保存布局</button></div>}
      </div>
      <div className="fcc-world" style={{ transform: `translate(${view.x}px, ${view.y}px) scale(${view.zoom})` }}>
        {nodes.map((node, index) => {
          const position = freeNodePosition(layout, node, index), isSelected = node.id === selectedNodeId;
          const status = node.statusLabel || (node.status ? statusLabels[node.status] || node.status : '');
          const tone = node.status && ['failed', 'rejected', 'error'].includes(node.status) ? 'error' : node.status && ['approved', 'ready', 'succeeded', 'completed'].includes(node.status) ? 'done' : '';
          return <article key={node.id} data-free-node={node.id} className={`fcc-node${isSelected ? ' is-selected' : ''}`} role="group" tabIndex={0} aria-label={`${node.title}，${isSelected ? '已选中，' : ''}按回车查看设置，方向键移动位置`} aria-current={isSelected ? 'true' : undefined} style={{ left: position.x, top: position.y, width: FREE_NODE_WIDTH, height: heightFor(node) }} onPointerDown={event => begin(event, node, index)} onKeyDown={event => nodeKey(event, node, index)} onClick={event => { if (event.target instanceof Element && event.target.closest(interactive)) return; if (suppressClick.current === node.id) { suppressClick.current = null; return; } onSelect(node.id); }}>
            <header><span className="fcc-node-type">{node.video || isVideoNode(node) ? <Film size={14} /> : node.image || isVisualNode(node) ? <ImageIcon size={14} /> : <FileText size={14} />}{node.type}</span><Grip size={15} className="fcc-node-grip" aria-hidden="true" /></header>
            <h3>{node.title}</h3>
            {node.video ? <div className="fcc-node-media"><video src={node.video} poster={node.image} controls preload="metadata" aria-label={node.title} /></div> : node.image ? <div className="fcc-node-media"><img src={node.image} alt={node.title} loading="lazy" draggable={false} /></div> : isVisualNode(node) ? <div className="fcc-node-media fcc-media-empty">{isVideoNode(node)?<Film size={32} strokeWidth={1.2}/>:<ImageIcon size={32} strokeWidth={1.2}/>}<span>{node.status && ['queued','running','processing'].includes(node.status)?'正在准备画面':'等待创作画面'}</span></div> : null}
            {node.summary && <p className="fcc-node-summary">{node.summary}</p>}
            <footer>{status && <span className={`fcc-node-status ${tone}`}>{status}</span>}<span>{isSelected ? '正在查看' : '点击查看设置'}</span></footer>
          </article>;
        })}
        <section key="node-inspector" className="fcc-node-panel" data-free-controls role="region" hidden={!hasPanel} aria-label={`${selected?.title ?? '当前节点'}设置`} style={{ display: hasPanel ? undefined : 'none', left: panelBounds.x, top: panelBounds.y, width: panelBounds.width, maxHeight: panelBounds.height }} onPointerDown={event => event.stopPropagation()}>
          <header><span>节点设置</span><strong>{selected?.title ?? ''}</strong></header><div ref={panelContentRef} className="fcc-panel-content">{children}</div>
        </section>
      </div>
      {!nodes.length && <div className="fcc-empty"><Move size={27} /><strong>这里还没有创作节点</strong><p>项目的实际内容会显示在画布中。</p></div>}
      <div className="fcc-toolbar" data-free-controls role="group" aria-label="画布视图操作">
        <button type="button" aria-label="缩小画布" disabled={view.zoom <= MIN_FREE_ZOOM} onClick={() => zoom(1 / 1.2)}><Minus size={16} /></button><output aria-label="画布缩放比例">{Math.round(view.zoom * 100)}%</output><button type="button" aria-label="放大画布" disabled={view.zoom >= MAX_FREE_ZOOM} onClick={() => zoom(1.2)}><Plus size={16} /></button><i />
        <button type="button" onClick={fit} title="适配全图"><Maximize size={15} /><span>适配</span></button><button type="button" onClick={reset} title="仅重置位置与视口，不修改业务内容"><RotateCcw size={15} /><span>重置布局</span></button>
      </div>
      <div className="fcc-help">拖动节点整理 · 拖动空白或滚动平移 · Ctrl / ⌘ + 滚动缩放</div>
      {warning && <p className="fcc-warning" role="status" data-free-controls>{warning}</p>}
      <span className="fcc-sr-only" role="status" aria-live="polite">{announcement}</span>
    </div>
  </section>;
}
