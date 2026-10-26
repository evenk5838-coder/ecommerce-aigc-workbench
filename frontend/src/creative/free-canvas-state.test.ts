import { describe, expect, it } from 'vitest';
import { clampFreeZoom, defaultFreePosition, fitFreeViewport, FREE_NODE_WIDTH, FREE_PANEL_GAP, FREE_PANEL_HEIGHT, FREE_PANEL_WIDTH, freeCanvasStorageKey, freeNodePosition, freePanelBounds, initialFreeLayout, MAX_FREE_ZOOM, MIN_FREE_ZOOM, moveFreeNode, panFreeViewport, parseFreeLayout, revealFreeViewport, serializeFreeLayout, zoomFreeViewport } from './free-canvas-state';

describe('自由画布仅存布局', () => {
  it('项目命名空间独立，不覆盖已有工作流布局或其他项目', () => {
    expect(freeCanvasStorageKey('项目/a')).toBe('material-studio-free-canvas-v1:%E9%A1%B9%E7%9B%AE%2Fa');
    expect(freeCanvasStorageKey('a')).not.toBe(freeCanvasStorageKey('b'));
    expect(freeCanvasStorageKey('a')).not.toContain('workflow-canvas');
  });
  it('只恢复同一项目、同一版本、有效位置与视口', () => {
    const layout = { ...initialFreeLayout('p'), positions: { 'shot:2': { x: -30, y: 700 } } };
    expect(parseFreeLayout(serializeFreeLayout(layout), 'p')).toEqual(layout);
    expect(parseFreeLayout(serializeFreeLayout(layout), 'other')).toBeNull();
    expect(parseFreeLayout(JSON.stringify({ ...layout, version: 2 }), 'p')).toBeNull();
    expect(parseFreeLayout('bad-json', 'p')).toBeNull();
    expect(parseFreeLayout(null, 'p')).toBeNull();
  });
  it('序列化与恢复都剥离业务内容、状态、顺序和确认信息', () => {
    const extra = { ...initialFreeLayout('p'), status: 'approved', prompt: 'PRIVATE PROMPT', order: ['id'],
      viewport: { x: 4, y: 9, zoom: 1, secret: 'extra' }, positions: { id: { x: 1, y: 2, approved: true } } };
    const serialized = serializeFreeLayout(extra);
    expect(serialized).not.toMatch(/PRIVATE|approved|secret|order/);
    expect(parseFreeLayout(JSON.stringify(extra), 'p')).toEqual({ version: 1, projectId: 'p', viewport: { x: 4, y: 9, zoom: 1 }, positions: { id: { x: 1, y: 2 } } });
  });
  it.each([0, -1, 20, null, '1'])('拒绝损坏缩放 %s', zoom => {
    expect(parseFreeLayout(JSON.stringify({ ...initialFreeLayout('p'), viewport: { x: 1, y: 2, zoom } }), 'p')).toBeNull();
  });
  it('拒绝非数字位置和失控坐标，不把它们变成正常布局', () => {
    expect(parseFreeLayout(JSON.stringify({ ...initialFreeLayout('p'), positions: { a: { x: '5', y: 2 } } }), 'p')).toBeNull();
    expect(parseFreeLayout(JSON.stringify({ ...initialFreeLayout('p'), positions: { a: { x: 1e20, y: 2 } } }), 'p')).toBeNull();
  });
  it('特殊节点 ID 不可读取或污染对象原型', () => {
    const layout = initialFreeLayout('p');
    expect(freeNodePosition(layout, { id: 'constructor' }, 0)).toEqual(defaultFreePosition(0));
    const raw = '{"version":1,"projectId":"p","viewport":{"x":0,"y":0,"zoom":1},"positions":{"__proto__":{"x":4,"y":5}}}';
    const parsed = parseFreeLayout(raw, 'p');
    expect(parsed && freeNodePosition(parsed, { id: '__proto__' }, 0)).toEqual({ x: 4, y: 5 });
    expect(Object.prototype).not.toHaveProperty('x');
  });
});

describe('自由画布坐标与手势', () => {
  it('保存位置优先，初始 x/y 可逐项提供，不更改传入节点', () => {
    const layout = initialFreeLayout('p'), node = { id: 'one', x: -80 };
    expect(freeNodePosition(layout, node, 1)).toEqual({ x: -80, y: defaultFreePosition(1).y });
    layout.positions.one = { x: 400, y: 500 };
    expect(freeNodePosition(layout, node, 1)).toEqual({ x: 400, y: 500 });
    expect(node).toEqual({ id: 'one', x: -80 });
  });
  it('缩放后的屏幕拖动正确换算，不修改起点或业务顺序', () => {
    const point = { x: 10, y: 20 };
    expect(moveFreeNode(point, { x: 50, y: -25 }, 0.5)).toEqual({ x: 110, y: -30 });
    expect(point).toEqual({ x: 10, y: 20 });
    expect(panFreeViewport({ x: 10, y: 20, zoom: 0.5 }, { x: 50, y: -25 })).toEqual({ x: 60, y: -5, zoom: 0.5 });
  });
  it('鼠标下的世界坐标在放大与缩小时不偏移', () => {
    const view = { x: -140, y: 85, zoom: 0.7 }, anchor = { x: 320, y: 280 };
    const after = zoomFreeViewport(view, 1.6, anchor);
    expect((anchor.x - after.x) / after.zoom).toBeCloseTo((anchor.x - view.x) / view.zoom);
    expect((anchor.y - after.y) / after.zoom).toBeCloseTo((anchor.y - view.y) / view.zoom);
    expect(clampFreeZoom(0.0001)).toBe(MIN_FREE_ZOOM);
    expect(clampFreeZoom(10)).toBe(MAX_FREE_ZOOM);
    expect(clampFreeZoom(NaN)).toBe(1);
  });
  it('全图适配包含任意负坐标和不规则位置，居中并保留边距', () => {
    const bounds = [{ x: -420, y: 80, width: FREE_NODE_WIDTH, height: 196 }, { x: 680, y: 510, width: FREE_NODE_WIDTH, height: 326 }];
    const view = fitFreeViewport(bounds, 1000, 700);
    for (const node of bounds) {
      expect(node.x * view.zoom + view.x).toBeGreaterThanOrEqual(39);
      expect(node.y * view.zoom + view.y).toBeGreaterThanOrEqual(39);
      expect((node.x + node.width) * view.zoom + view.x).toBeLessThanOrEqual(961);
      expect((node.y + node.height) * view.zoom + view.y).toBeLessThanOrEqual(661);
    }
  });
  it('空画布和未测得的容器仍给出有效视口，不制造节点', () => {
    expect(fitFreeViewport([], 0, 0)).toEqual({ x: 24, y: 28, zoom: 1 });
    expect(initialFreeLayout('p').positions).toEqual({});
  });
});

describe('节点设置使用世界坐标', () => {
  it('面板始终位于节点下方，拖动的屏幕位移与节点一致', () => {
    const node = { x: -40, y: 80, width: FREE_NODE_WIDTH, height: 326 };
    const panel = freePanelBounds(node);
    expect(panel).toEqual({ x: -40, y: 80 + 326 + FREE_PANEL_GAP, width: FREE_PANEL_WIDTH, height: FREE_PANEL_HEIGHT });
    const delta = { x: 120, y: -50 }, zoom = 0.5;
    const moved = { ...node, ...moveFreeNode(node, delta, zoom) }, movedPanel = freePanelBounds(moved);
    expect((movedPanel.x - panel.x) * zoom).toBe(delta.x);
    expect((movedPanel.y - panel.y) * zoom).toBe(delta.y);
    expect(movedPanel.y - moved.y - moved.height).toBe(FREE_PANEL_GAP);
    expect(node).toEqual({ x: -40, y: 80, width: FREE_NODE_WIDTH, height: 326 });
  });
  it('改变缩放时面板大小与节点间距等比例变化，不固定为屏幕像素', () => {
    const node = { x: 210, y: 90, width: FREE_NODE_WIDTH, height: 196 }, panel = freePanelBounds(node);
    const before = { x: 10, y: -25, zoom: 0.5 };
    const after = zoomFreeViewport(before, 1.5, { x: 370, y: 260 });
    const gap = (view: typeof before) => (panel.y * view.zoom + view.y) - ((node.y + node.height) * view.zoom + view.y);
    expect(gap(after)).toBeCloseTo(gap(before) * 3);
    expect(panel.width * after.zoom).toBe(panel.width * before.zoom * 3);
    expect(panel.height * after.zoom).toBe(panel.height * before.zoom * 3);
  });
  it('全图适配包含展开面板，不把面板下缘截在视口之外', () => {
    const node = { x: -240, y: 110, width: FREE_NODE_WIDTH, height: 326 }, panel = freePanelBounds(node);
    const view = fitFreeViewport([node, panel], 390, 620);
    expect(view.zoom).toBeLessThan(fitFreeViewport([node], 390, 620).zoom);
    for (const item of [node, panel]) {
      expect(item.x * view.zoom + view.x).toBeGreaterThanOrEqual(39);
      expect(item.y * view.zoom + view.y).toBeGreaterThanOrEqual(54);
      expect((item.x + item.width) * view.zoom + view.x).toBeLessThanOrEqual(351);
      expect((item.y + item.height) * view.zoom + view.y).toBeLessThanOrEqual(566);
    }
  });
  it('选中后仅最小平移，重复核对可见面板不会跳动或改缩放', () => {
    const node = { x: 720, y: 680, width: FREE_NODE_WIDTH, height: 196 }, panel = freePanelBounds(node);
    const original = { x: 20, y: 30, zoom: 0.5 };
    const view = revealFreeViewport(original, [node, panel], 800, 700);
    expect(view.zoom).toBe(original.zoom);
    expect(view.x).toBe(original.x);
    expect((panel.y + panel.height) * view.zoom + view.y).toBe(600);
    expect(revealFreeViewport(view, [node, panel], 800, 700)).toBe(view);
    expect(original).toEqual({ x: 20, y: 30, zoom: 0.5 });
  });
  it('节点加面板高于屏幕时保留节点顶部，不为露出底部将节点推到屏幕外', () => {
    const node = { x: 110, y: 200, width: FREE_NODE_WIDTH, height: 326 }, panel = freePanelBounds(node);
    const view = revealFreeViewport({ x: 20, y: 30, zoom: 1 }, [node, panel], 390, 620);
    expect(node.x * view.zoom + view.x).toBe(12);
    expect(node.y * view.zoom + view.y).toBe(52);
    expect(view.zoom).toBe(1);
    expect(revealFreeViewport(view, [node, panel], 390, 620)).toBe(view);
    expect(revealFreeViewport(view, [], 390, 620)).toBe(view);
  });
});
