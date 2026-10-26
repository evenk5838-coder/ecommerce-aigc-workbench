import { useEffect, useId, useRef, useState } from 'react';
import type { KeyboardEvent, PointerEvent } from 'react';
import { ArrowDown, ArrowRight, ArrowUp, Expand, Grip, ImageOff, LayoutGrid, Minus, Plus, RotateCcw, Trash2 } from 'lucide-react';
import { mediaUrl } from '../api';
import {
  CANVAS_NODE_WIDTH, CANVAS_SHOT_HEIGHT, CANVAS_STAGE_HEIGHT, MAX_CANVAS_ZOOM, MIN_CANVAS_ZOOM,
  WORKFLOW_STAGES, canvasStatus, canvasStorageKey, defaultShotPosition, defaultStagePosition,
  fitCanvasViewport, initialCanvasLayout, moveCanvasNode, parseCanvasLayout, revealCanvasControl, shotNodeId, stageNodeId, zoomCanvasAt,
} from './canvas-state';
import type { CanvasLayout, CanvasPoint, WorkflowStageId } from './canvas-state';
import './WorkflowCanvas.css';

export interface WorkflowShot { shot_id: string; seq: number; description?: string; status?: string; asset_id?: string | null }
export interface WorkflowStageState { status?: string; summary?: string; asset_id?: string | null; error?: string | null }
export interface WorkflowCanvasProps {
  projectId: string;
  shots: WorkflowShot[];
  stage: string;
  onSelectStage: (stage: string) => void;
  stages?: Partial<Record<WorkflowStageId, WorkflowStageState>>;
  /** null asks the parent to open its add-shot editor. */
  onEditShot?: (shotId: string | null) => void;
  onDeleteShot?: (shotId: string) => void;
  onReorderShot?: (shotId: string, direction: 'up' | 'down') => void;
}
interface Gesture { pointerId: number; nodeId?: string; start: CanvasPoint; before: CanvasLayout; position?: CanvasPoint }
function readLayout(projectId: string): { layout: CanvasLayout; warning: string } {
  try {
    const raw = localStorage.getItem(canvasStorageKey(projectId)), saved = parseCanvasLayout(raw, projectId);
    return { layout: saved ?? initialCanvasLayout(projectId), warning: raw && !saved ? '已存布局无法读取，暂用默认位置。你的项目内容不受影响。' : '' };
  } catch { return { layout: initialCanvasLayout(projectId), warning: '浏览器无法读取布局；可继续使用画布。' }; }
}
function Preview({ assetId, label }: { assetId?: string | null; label: string }) {
  const [failed, setFailed] = useState(false);
  if (!assetId?.trim()) return <div className="wc-preview wc-preview-empty"><ImageOff size={19} /><span>暂无预览素材</span></div>;
  return <div className="wc-preview">{failed ? <span className="wc-preview-error"><ImageOff size={19} />素材预览不可用</span> : <img src={mediaUrl(assetId)} alt={label} loading="lazy" draggable={false} onError={() => setFailed(true)} />}</div>;
}

/** Pure workflow visualization: no generation, confirmation or backend mutation is performed here. */
export default function WorkflowCanvas(props: WorkflowCanvasProps) {
  return <WorkflowCanvasSession key={props.projectId} {...props} />;
}
function WorkflowCanvasSession({ projectId, shots, stage, onSelectStage, stages = {}, onEditShot, onDeleteShot, onReorderShot }: WorkflowCanvasProps) {
  const helpId = useId();
  const [initial] = useState(() => readLayout(projectId));
  const [layout, setLayout] = useState(initial.layout);
  const [warning, setWarning] = useState(initial.warning);
  const [announcement, setAnnouncement] = useState('');
  const [undo, setUndo] = useState<CanvasLayout | null>(null);
  const [moving, setMoving] = useState(false);
  const viewportRef = useRef<HTMLDivElement | null>(null);
  const current = useRef(layout);
  const gesture = useRef<Gesture | null>(null);
  const changed = useRef(false);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const ids = new Set<string>();
  const visibleShots = [...shots].sort((a, b) => a.seq - b.seq).filter(shot => {
    if (!shot.shot_id?.trim() || ids.has(shot.shot_id)) return false;
    ids.add(shot.shot_id); return true;
  });
  const badShots = shots.length !== visibleShots.length;
  const position = (id: string, fallback: CanvasPoint): CanvasPoint => layout.positions[id] ?? fallback;
  const nodes = [
    ...WORKFLOW_STAGES.map((item, index) => ({ id: stageNodeId(item.id), ...position(stageNodeId(item.id), defaultStagePosition(index)), width: CANVAS_NODE_WIDTH, height: CANVAS_STAGE_HEIGHT })),
    ...visibleShots.map((shot, index) => ({ id: shotNodeId(shot.shot_id), ...position(shotNodeId(shot.shot_id), defaultShotPosition(index)), width: CANVAS_NODE_WIDTH, height: CANVAS_SHOT_HEIGHT })),
  ];
  const save = () => {
    if (!changed.current || !projectId.trim()) return;
    try { localStorage.setItem(canvasStorageKey(projectId), JSON.stringify(current.current)); }
    catch { setWarning('浏览器无法保存画布位置，请保持页面打开；项目内容仍由各步骤保存。'); }
  };
  const commit = (next: CanvasLayout) => {
    current.current = next; changed.current = true; setLayout(next);
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(save, 150);
  };
  useEffect(() => () => {
    if (saveTimer.current) clearTimeout(saveTimer.current);
    if (changed.current && projectId.trim()) {
      try { localStorage.setItem(canvasStorageKey(projectId), JSON.stringify(current.current)); } catch { /* An earlier save warning remains visible while mounted. */ }
    }
  }, [projectId]);
  useEffect(() => {
    const element = viewportRef.current;
    if (!element) return;
    const wheel = (event: WheelEvent) => {
      event.preventDefault();
      if (gesture.current) return;
      const view = current.current.viewport;
      const rect = element.getBoundingClientRect();
      const delta = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? element.clientHeight : 1;
      const viewport = event.ctrlKey || event.metaKey
        ? zoomCanvasAt(view, view.zoom * Math.exp(-event.deltaY * delta * 0.002), { x: event.clientX - rect.left, y: event.clientY - rect.top })
        : { ...view, x: view.x - event.deltaX * delta, y: view.y - event.deltaY * delta };
      commit({ ...current.current, viewport });
    };
    element.addEventListener('wheel', wheel, { passive: false });
    return () => element.removeEventListener('wheel', wheel);
    // The listener reads the latest layout from its ref; callbacks only save this keyed project.
  }, [projectId]);
  const fit = () => {
    const element = viewportRef.current;
    if (!element) return;
    commit({ ...current.current, viewport: fitCanvasViewport(nodes, element.clientWidth, element.clientHeight) });
    setAnnouncement('已调整视图以查看全部节点。');
  };
  const zoom = (factor: number) => {
    const element = viewportRef.current;
    const anchor = { x: (element?.clientWidth ?? 800) / 2, y: (element?.clientHeight ?? 600) / 2 };
    commit({ ...current.current, viewport: zoomCanvasAt(current.current.viewport, current.current.viewport.zoom * factor, anchor) });
  };
  const start = (event: PointerEvent<HTMLElement>, nodeId?: string, nodePosition?: CanvasPoint) => {
    if (event.button !== 0 || gesture.current) return;
    if (!nodeId && (event.target as HTMLElement).closest('[data-canvas-node]')) return;
    event.preventDefault(); event.stopPropagation(); event.currentTarget.focus(); event.currentTarget.setPointerCapture(event.pointerId);
    gesture.current = { pointerId: event.pointerId, nodeId, position: nodePosition, start: { x: event.clientX, y: event.clientY }, before: current.current };
    setMoving(true);
  };
  const move = (event: PointerEvent<HTMLDivElement>) => {
    const active = gesture.current;
    if (!active || active.pointerId !== event.pointerId) return;
    const delta = { x: event.clientX - active.start.x, y: event.clientY - active.start.y };
    if (active.nodeId && active.position) {
      commit({ ...current.current, positions: { ...current.current.positions, [active.nodeId]: moveCanvasNode(active.position, delta, active.before.viewport.zoom) } });
    } else commit({ ...current.current, viewport: { ...active.before.viewport, x: active.before.viewport.x + delta.x, y: active.before.viewport.y + delta.y } });
  };
  const end = (event: PointerEvent<HTMLDivElement>, cancel = false) => {
    const active = gesture.current;
    if (!active || active.pointerId !== event.pointerId) return;
    if (cancel) commit(active.before);
    gesture.current = null; setMoving(false); save();
  };
  const moveByKey = (event: KeyboardEvent, id: string, fallback: CanvasPoint, label: string) => {
    const step = event.shiftKey ? 80 : 20;
    const delta: Record<string, CanvasPoint> = { ArrowLeft: { x: -step, y: 0 }, ArrowRight: { x: step, y: 0 }, ArrowUp: { x: 0, y: -step }, ArrowDown: { x: 0, y: step } };
    if (event.key !== 'Home' && !delta[event.key]) return;
    event.preventDefault(); event.stopPropagation();
    const old = current.current.positions[id] ?? fallback;
    const next = event.key === 'Home' ? fallback : { x: old.x + delta[event.key].x, y: old.y + delta[event.key].y };
    commit({ ...current.current, positions: { ...current.current.positions, [id]: next } });
    setAnnouncement(`${label}位置：${Math.round(next.x)}，${Math.round(next.y)}。`);
  };
  const viewportKeys = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key === 'Escape' && gesture.current) { commit(gesture.current.before); gesture.current = null; setMoving(false); event.preventDefault(); return; }
    if (event.target !== event.currentTarget) return;
    const step = event.shiftKey ? 160 : 50;
    const delta: Record<string, CanvasPoint> = { ArrowLeft: { x: step, y: 0 }, ArrowRight: { x: -step, y: 0 }, ArrowUp: { x: 0, y: step }, ArrowDown: { x: 0, y: -step } };
    if (delta[event.key]) { event.preventDefault(); const view = current.current.viewport; commit({ ...current.current, viewport: { ...view, x: view.x + delta[event.key].x, y: view.y + delta[event.key].y } }); }
    else if (['+', '=', '-', '0', 'Home'].includes(event.key)) { event.preventDefault(); if (event.key === '0' || event.key === 'Home') fit(); else zoom(event.key === '-' ? 1 / 1.2 : 1.2); }
  };
  const arrange = () => {
    setUndo(current.current);
    const positions = { ...current.current.positions };
    WORKFLOW_STAGES.forEach((item, index) => { positions[stageNodeId(item.id)] = defaultStagePosition(index); });
    visibleShots.forEach((shot, index) => { positions[shotNodeId(shot.shot_id)] = defaultShotPosition(index); });
    commit({ ...current.current, positions }); setAnnouncement('已整理节点位置；镜头顺序未改变。');
  };
  const grip = (id: string, fallback: CanvasPoint, label: string) => <button type="button" className="wc-grip" aria-label={`移动${label}：拖动或用方向键，Home 复位`} title="拖动整理位置；方向键移动，Shift 加快，Home 复位" onPointerDown={event => start(event, id, position(id, fallback))} onKeyDown={event => moveByKey(event, id, fallback, label)}><Grip size={16} aria-hidden="true" /></button>;

  return <section className="workflow-canvas" aria-label="固定创作流程画布">
    <header className="wc-header"><div><span className="wc-eyebrow">创作全览</span><h2>把每一步，放在同一张画布上。</h2><p>8 个固定阶段 · {visibleShots.length} 个镜头</p></div><div className="wc-toolbar" aria-label="画布工具">
      <button type="button" onClick={() => zoom(1 / 1.2)} disabled={layout.viewport.zoom <= MIN_CANVAS_ZOOM} aria-label="缩小画布"><Minus size={16} /></button><output aria-label="画布缩放比例">{Math.round(layout.viewport.zoom * 100)}%</output><button type="button" onClick={() => zoom(1.2)} disabled={layout.viewport.zoom >= MAX_CANVAS_ZOOM} aria-label="放大画布"><Plus size={16} /></button>
      <button type="button" onClick={fit}><Expand size={15} />查看全部</button><button type="button" onClick={arrange}><LayoutGrid size={15} />整理位置</button>{undo && <button type="button" onClick={() => { commit(undo); setUndo(null); }}><RotateCcw size={15} />撤销整理</button>}
    </div></header>
    <p className="wc-help" id={helpId}>拖动空白处平移；Ctrl / ⌘ + 滚轮缩放。Tab 选择节点手柄，方向键移动节点；画布聚焦时方向键平移，+ / − 缩放，0 查看全部。拖动位置不改变镜头顺序。</p>
    {warning && <p className="wc-warning" role="status">{warning}</p>}{!projectId.trim() && <p className="wc-warning" role="alert">项目编号缺失，布局暂不保存。</p>}{badShots && <p className="wc-warning" role="alert">部分镜头编号缺失或重复，无法安全显示。请回到分镜计划核对。</p>}
    {stage && !WORKFLOW_STAGES.some(item => item.id === stage) && <p className="wc-warning">当前步骤“{stage}”不在固定流程中，请核对步骤数据。</p>}
    <div ref={viewportRef} className={`wc-viewport${moving ? ' is-moving' : ''}`} tabIndex={0} role="region" aria-label="画布视图" aria-describedby={helpId} onKeyDown={viewportKeys} onFocusCapture={event => {
      if (event.target === event.currentTarget || gesture.current) return;
      const bounds = event.target.getBoundingClientRect(), viewBounds = event.currentTarget.getBoundingClientRect();
      const view = current.current.viewport, next = revealCanvasControl(view, { x: bounds.left - viewBounds.left, y: bounds.top - viewBounds.top, width: bounds.width, height: bounds.height }, event.currentTarget.clientWidth, event.currentTarget.clientHeight);
      if (next.x !== view.x || next.y !== view.y) commit({ ...current.current, viewport: next });
    }} onPointerDown={event => start(event)} onPointerMove={move} onPointerUp={event => end(event)} onPointerCancel={event => end(event, true)}>
      <div className="wc-world" style={{ transform: `translate(${layout.viewport.x}px, ${layout.viewport.y}px) scale(${layout.viewport.zoom})` }}>
        <svg className="wc-connections" width="1" height="1" aria-hidden="true"><defs><marker id={`${helpId}-arrow`} markerWidth="8" markerHeight="8" refX="6" refY="4" orient="auto"><path d="M 1 1 L 6 4 L 1 7" fill="none" stroke="currentColor" strokeWidth="1.5" /></marker></defs>{WORKFLOW_STAGES.slice(0, -1).map((item, index) => {
          const left = nodes[index], right = nodes[index + 1];
          const forward = right.x >= left.x, x1 = left.x + (forward ? CANVAS_NODE_WIDTH : 0), x2 = right.x + (forward ? 0 : CANVAS_NODE_WIDTH), y1 = left.y + CANVAS_STAGE_HEIGHT / 2, y2 = right.y + CANVAS_STAGE_HEIGHT / 2;
          const turn = Math.max(28, Math.abs(x2 - x1) / 2) * (forward ? 1 : -1);
          return <path key={item.id} d={`M ${x1} ${y1} C ${x1 + turn} ${y1}, ${x2 - turn} ${y2}, ${x2} ${y2}`} markerEnd={`url(#${helpId}-arrow)`} />;
        })}</svg>
        {WORKFLOW_STAGES.map((item, index) => {
          const id = stageNodeId(item.id), point = position(id, defaultStagePosition(index)), data = stages[item.id];
          const status = canvasStatus(data?.status, !!data?.summary || !!data?.asset_id || !!data?.error);
          return <article key={id} data-canvas-node={id} data-stage-id={item.id} className={`wc-node wc-stage${stage === item.id ? ' is-current' : ''}`} style={{ left: point.x, top: point.y }}>
            <div className="wc-node-top"><span className="wc-step-number">{String(index + 1).padStart(2, '0')}</span><span className={`wc-status tone-${status.tone}`}>{status.label}</span>{grip(id, defaultStagePosition(index), item.label)}</div>
            <h3>{item.label}{stage === item.id && <small>当前步骤</small>}</h3>
            <p className={data?.error ? 'wc-node-error' : 'wc-node-summary'}>{data?.error || data?.summary || '暂无阶段内容'}</p>
            {data?.asset_id && <a className="wc-stage-asset" href={mediaUrl(data.asset_id)} target="_blank" rel="noreferrer">查看阶段素材</a>}
            <button type="button" className="wc-open" onClick={() => onSelectStage(item.id)}>进入此步骤<ArrowRight size={15} /></button>
          </article>;
        })}
        {visibleShots.map((shot, index) => {
          const id = shotNodeId(shot.shot_id), point = position(id, defaultShotPosition(index)), label = Number.isFinite(shot.seq) ? `镜头 ${shot.seq}` : '未编号镜头';
          const status = canvasStatus(shot.status, !!shot.description || !!shot.asset_id);
          return <article key={id} data-canvas-node={id} className="wc-node wc-shot" style={{ left: point.x, top: point.y }}>
            <div className="wc-node-top"><strong>{label}</strong><span className={`wc-status tone-${status.tone}`}>{status.label}</span>{grip(id, defaultShotPosition(index), label)}</div>
            <Preview key={shot.asset_id ?? 'empty'} assetId={shot.asset_id} label={`${label}素材预览`} /><p className="wc-shot-description">{shot.description?.trim() || '镜头描述未提供'}</p>
            <div className="wc-shot-actions"><button type="button" className="wc-open" onClick={() => onSelectStage('plan')}>回到分镜计划<ArrowRight size={14} /></button>{onEditShot && <button type="button" onClick={() => onEditShot(shot.shot_id)}>编辑</button>}{onDeleteShot && <button type="button" aria-label={`删除${label}`} onClick={() => onDeleteShot(shot.shot_id)}><Trash2 size={14} /></button>}</div>
            {onReorderShot && <div className="wc-order"><span>成片顺序</span><button type="button" aria-label={`${label}前移`} disabled={index === 0} onClick={() => onReorderShot(shot.shot_id, 'up')}><ArrowUp size={14} />前移</button><button type="button" aria-label={`${label}后移`} disabled={index === visibleShots.length - 1} onClick={() => onReorderShot(shot.shot_id, 'down')}><ArrowDown size={14} />后移</button></div>}
          </article>;
        })}
        {!visibleShots.length && <div className="wc-empty-shots" style={{ left: 40, top: 628 }}><span>还没有镜头</span><p>在分镜计划中添加镜头后，这里会显示各镜的内容与状态。</p><button type="button" onClick={() => onSelectStage('plan')}>打开分镜计划<ArrowRight size={15} /></button></div>}
      </div>
    </div>
    <footer className="wc-footer"><span>布局仅保存在此浏览器，按项目独立恢复。</span>{onEditShot && <button type="button" onClick={() => onEditShot(null)}><Plus size={15} />新增镜头</button>}</footer><span className="wc-sr-only" role="status" aria-live="polite">{announcement}</span>
  </section>;
}
