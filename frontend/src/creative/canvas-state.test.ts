import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import WorkflowCanvas from './WorkflowCanvas';
import {
  MAX_CANVAS_ZOOM, MIN_CANVAS_ZOOM, WORKFLOW_STAGES, canvasStatus, canvasStorageKey, clampCanvasZoom,
  defaultStagePosition, fitCanvasViewport, initialCanvasLayout, moveCanvasNode, parseCanvasLayout, revealCanvasControl, shotNodeId, zoomCanvasAt,
} from './canvas-state';

afterEach(() => { vi.unstubAllGlobals(); });

describe('canvas geometry and keyboard-sized movement', () => {
  it('keeps the point beneath the cursor fixed during zoom, including clamped zoom', () => {
    const before = { x: -80, y: 35, zoom: 0.5 }, anchor = { x: 280, y: 190 };
    for (const requested of [1.2, 20, 0.001]) {
      const after = zoomCanvasAt(before, requested, anchor);
      expect((anchor.x - after.x) / after.zoom).toBeCloseTo((anchor.x - before.x) / before.zoom);
      expect((anchor.y - after.y) / after.zoom).toBeCloseTo((anchor.y - before.y) / before.zoom);
      expect(after.zoom).toBeGreaterThanOrEqual(MIN_CANVAS_ZOOM);
      expect(after.zoom).toBeLessThanOrEqual(MAX_CANVAS_ZOOM);
    }
  });
  it('translates screen dragging into world coordinates without changing the original position', () => {
    const origin = Object.freeze({ x: 40, y: -20 });
    expect(moveCanvasNode(origin, { x: 50, y: -25 }, 0.5)).toEqual({ x: 140, y: -70 });
    expect(origin).toEqual({ x: 40, y: -20 });
  });
  it('fits positive and negative node positions inside the viewport where zoom limits permit', () => {
    const bounds = [{ x: -200, y: -100, width: 248, height: 224 }, { x: 600, y: 400, width: 248, height: 324 }];
    const view = fitCanvasViewport(bounds, 900, 700);
    for (const node of bounds) {
      expect(node.x * view.zoom + view.x).toBeGreaterThanOrEqual(23);
      expect(node.y * view.zoom + view.y).toBeGreaterThanOrEqual(23);
      expect((node.x + node.width) * view.zoom + view.x).toBeLessThanOrEqual(877);
      expect((node.y + node.height) * view.zoom + view.y).toBeLessThanOrEqual(677);
    }
  });
  it('keeps scale bounded on tiny viewports and invalid scale values', () => {
    expect(fitCanvasViewport([{ x: 0, y: 0, width: 10000, height: 10000 }], 20, 20).zoom).toBe(MIN_CANVAS_ZOOM);
    expect(clampCanvasZoom(Infinity)).toBe(1);
    expect(clampCanvasZoom(NaN)).toBe(1);
    expect(clampCanvasZoom(-2)).toBe(MIN_CANVAS_ZOOM);
    expect(clampCanvasZoom(10)).toBe(MAX_CANVAS_ZOOM);
  });
  it('pans offscreen keyboard controls into view without changing zoom or a visible control', () => {
    const view = { x: -50, y: 20, zoom: 0.7 };
    expect(revealCanvasControl(view, { x: 20, y: 20, width: 100, height: 30 }, 800, 600)).toEqual(view);
    expect(revealCanvasControl(view, { x: 900, y: 700, width: 100, height: 30 }, 800, 600)).toEqual({ x: -266, y: -126, zoom: 0.7 });
    expect(revealCanvasControl(view, { x: -200, y: -100, width: 100, height: 30 }, 800, 600)).toEqual({ x: 166, y: 136, zoom: 0.7 });
  });
  it('has exactly eight fixed stages with distinct default positions', () => {
    expect(WORKFLOW_STAGES.map(stage => stage.id)).toEqual(['brief', 'script', 'plan', 'storyboard', 'review', 'generation', 'results', 'export']);
    expect(new Set(WORKFLOW_STAGES.map((_, index) => JSON.stringify(defaultStagePosition(index)))).size).toBe(8);
  });
});

describe('per-project layout persistence', () => {
  it('restores viewport and positions without restoring content or task status', () => {
    const layout = { ...initialCanvasLayout('prj_a'), positions: { [shotNodeId('shot_1')]: { x: -30, y: 240 } }, viewport: { x: 100, y: -20, zoom: 1.5 }, status: 'success', shots: [{ status: 'success' }] };
    const restored = parseCanvasLayout(JSON.stringify(layout), 'prj_a');
    expect(restored?.positions[shotNodeId('shot_1')]).toEqual({ x: -30, y: 240 });
    expect(restored?.viewport).toEqual(layout.viewport);
    expect(restored).not.toHaveProperty('status');
    expect(restored).not.toHaveProperty('shots');
  });
  it('does not reuse another project layout or collide with existing editor storage', () => {
    const raw = JSON.stringify(initialCanvasLayout('prj_a'));
    expect(parseCanvasLayout(raw, 'prj_b')).toBeNull();
    expect(canvasStorageKey('prj_a')).not.toBe(canvasStorageKey('prj_b'));
    expect(canvasStorageKey('a/b')).not.toBe(canvasStorageKey('a%2Fb'));
    expect(canvasStorageKey('prj_a')).not.toContain('creative-edit-v1');
  });
  it.each([
    null, 'not-json', '{}', JSON.stringify({ ...initialCanvasLayout('p'), version: 2 }),
    JSON.stringify({ ...initialCanvasLayout('p'), viewport: { x: 0, y: 0, zoom: 0 } }),
    JSON.stringify({ ...initialCanvasLayout('p'), positions: { 'shot:a': { x: null, y: 2 } } }),
    JSON.stringify({ ...initialCanvasLayout('p'), positions: { 'foreign:a': { x: 1, y: 2 } } }),
  ])('rejects malformed persistence without inventing positions: %s', raw => {
    expect(parseCanvasLayout(raw, 'p')).toBeNull();
  });
  it('retains positions by shot identity even after the parent reorders or temporarily hides shots', () => {
    const restored = parseCanvasLayout(JSON.stringify({ ...initialCanvasLayout('p'), positions: { 'shot:a': { x: 20, y: 30 }, 'shot:b': { x: 400, y: 600 } } }), 'p');
    expect(['b', 'a'].map(id => restored?.positions[shotNodeId(id)])).toEqual([{ x: 400, y: 600 }, { x: 20, y: 30 }]);
  });
});

describe('truthful node display', () => {
  it('does not derive completion from the current step or an unknown status', () => {
    expect(canvasStatus(undefined)).toEqual({ label: '未开始', tone: 'neutral' });
    expect(canvasStatus(undefined, true)).toEqual({ label: '状态未提供', tone: 'neutral' });
    expect(canvasStatus('future_state')).toEqual({ label: '状态：future_state', tone: 'neutral' });
    expect(canvasStatus('failed')).toEqual({ label: '失败', tone: 'error' });
    expect(canvasStatus('succeeded')).toEqual({ label: '已成功', tone: 'success' });
  });
  it('renders provided content, independent shot states and safe same-origin previews without calling actions', () => {
    const onSelectStage = vi.fn(), onEditShot = vi.fn(), onReorderShot = vi.fn();
    const html = renderToStaticMarkup(createElement(WorkflowCanvas, {
      projectId: 'p', stage: 'results', onSelectStage, onEditShot, onReorderShot,
      shots: [{ shot_id: 'a', seq: 1, description: '真实描述甲', status: 'failed' }, { shot_id: 'b', seq: 2, description: '真实描述乙', status: 'succeeded', asset_id: 'asset /2' }],
      stages: { script: { summary: '实际脚本内容', status: 'draft' } },
    }));
    expect(html).toContain('真实描述甲'); expect(html).toContain('真实描述乙'); expect(html).toContain('实际脚本内容');
    expect(html).toContain('tone-error'); expect(html).toContain('tone-success'); expect(html).toContain('未开始');
    expect(html).toContain('/api/assets/asset%20%2F2/file');
    expect(onSelectStage).not.toHaveBeenCalled(); expect(onEditShot).not.toHaveBeenCalled(); expect(onReorderShot).not.toHaveBeenCalled();
  });
  it('offers no mutations without callbacks and reports missing descriptions and duplicate identifiers', () => {
    const html = renderToStaticMarkup(createElement(WorkflowCanvas, {
      projectId: 'p', stage: 'plan', onSelectStage: vi.fn(), shots: [{ shot_id: 'a', seq: 1 }, { shot_id: 'a', seq: 2 }],
    }));
    expect(html).toContain('镜头描述未提供'); expect(html).toContain('编号缺失或重复');
    expect(html).not.toContain('新增镜头'); expect(html).not.toContain('删除镜头'); expect(html).not.toContain('成片顺序');
  });
  it('reads only this project layout and never writes or clears storage on initial rendering', () => {
    const getItem = vi.fn().mockReturnValue(JSON.stringify({ ...initialCanvasLayout('p'), viewport: { x: 123, y: -45, zoom: 1.2 } }));
    const setItem = vi.fn(), clear = vi.fn(), removeItem = vi.fn();
    vi.stubGlobal('localStorage', { getItem, setItem, clear, removeItem });
    const html = renderToStaticMarkup(createElement(WorkflowCanvas, { projectId: 'p', shots: [], stage: 'brief', onSelectStage: vi.fn() }));
    expect(html).toContain('translate(123px, -45px) scale(1.2)');
    expect(getItem).toHaveBeenCalledWith(canvasStorageKey('p'));
    expect(setItem).not.toHaveBeenCalled(); expect(clear).not.toHaveBeenCalled(); expect(removeItem).not.toHaveBeenCalled();
  });
});
