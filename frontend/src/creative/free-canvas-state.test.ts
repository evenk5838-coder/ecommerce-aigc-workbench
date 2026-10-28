import { describe, expect, it } from 'vitest';
import { freeMarqueeBounds, freeMarqueeHits, mergeFreeSelection, moveFreeNodeGroup, freeCanvasShortcut, clampFreeZoom, defaultFreePosition, fitFreeViewport, FREE_NODE_WIDTH, FREE_PANEL_GAP, FREE_PANEL_HEIGHT, FREE_PANEL_WIDTH, freeCanvasStorageKey, freeNodePosition, freePanelBounds, initialFreeLayout, MAX_FREE_ZOOM, MIN_FREE_ZOOM, moveFreeNode, panFreeViewport, parseFreeLayout, revealFreeViewport, revealFreeWorkingViewport, serializeFreeLayout, zoomFreeViewport } from './free-canvas-state';

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


describe('floating conversation leaves canvas space accessible', () => {
  it.each([{ width: 1440, height: 820, inset: 360 }, { width: 1280, height: 720, inset: 360 },
    { width: 704, height: 604, inset: 0 }, { width: 390, height: 604, inset: 0 }])(
    'fits six nodes clear of the creation and selection toolbars at $width px', ({ width, height, inset }) => {
      const nodes = Array.from({ length: 6 }, (_, index) => ({ ...defaultFreePosition(index), width: FREE_NODE_WIDTH, height: 326 }));
      const positions = structuredClone(nodes);
      const view = fitFreeViewport(nodes, width, height, inset, 82);
      for (const node of nodes) {
        expect(node.x * view.zoom + view.x).toBeGreaterThanOrEqual(inset + 39);
        expect((node.x + node.width) * view.zoom + view.x).toBeLessThanOrEqual(width - 82 - 39);
        expect(node.y * view.zoom + view.y).toBeGreaterThanOrEqual(55);
        expect((node.y + node.height) * view.zoom + view.y).toBeLessThanOrEqual(height - 131);
      }
      expect(nodes).toEqual(positions);
    });
  it('fits an open inspector with the same scale and without changing its node position', () => {
    const node = { x: 920, y: 440, width: FREE_NODE_WIDTH, height: 326 }, panel = freePanelBounds(node);
    const view = fitFreeViewport([node, panel], 704, 604, 0, 82);
    expect((panel.x + panel.width) * view.zoom + view.x).toBeLessThanOrEqual(704 - 82 - 39);
    expect((panel.y + panel.height) * view.zoom + view.y).toBeLessThanOrEqual(604 - 131);
    expect((panel.y - node.y - node.height) * view.zoom).toBeCloseTo(FREE_PANEL_GAP * view.zoom);
    expect(node).toEqual({ x: 920, y: 440, width: FREE_NODE_WIDTH, height: 326 });
  });
  it.each([1145, 704, 390])('keeps the final film clear in the real six-node workflow at %i px', width => {
    const nodes = [{ x: 80, y: 100 }, { x: 560, y: 100 }, { x: 560, y: 440 },
      { x: 1040, y: 100 }, { x: 1040, y: 440 }, { x: 1600, y: 100 }]
      .map(position => ({ ...position, width: FREE_NODE_WIDTH, height: 326 }));
    const inset = width > 760 ? 360 : 0;
    const view = fitFreeViewport(nodes, width, 640, inset, 82);
    for (const node of nodes) {
      expect(node.x * view.zoom + view.x).toBeGreaterThanOrEqual(inset);
      expect((node.x + node.width) * view.zoom + view.x).toBeLessThanOrEqual(width - 82);
      expect((node.y + node.height) * view.zoom + view.y).toBeLessThanOrEqual(640 - 132);
    }
  });
  it('fit reserves the conversation inset without changing world coordinates', () => {
    const nodes = [{ x: 20, y: 30, width: FREE_NODE_WIDTH, height: 326 }];
    const view = fitFreeViewport(nodes, 1200, 800, 360);
    expect(nodes[0].x * view.zoom + view.x).toBeGreaterThan(400);
    expect((nodes[0].x + FREE_NODE_WIDTH) * view.zoom + view.x).toBeLessThan(1160);
    expect(nodes[0]).toEqual({ x: 20, y: 30, width: FREE_NODE_WIDTH, height: 326 });
  });
  it('selection reveals nodes to the right of chat and then stops moving', () => {
    const nodes = [{ x: 0, y: 80, width: FREE_NODE_WIDTH, height: 196 }];
    const view = revealFreeViewport({ x: 0, y: 0, zoom: 1 }, nodes, 1200, 800, 360);
    expect(view.x).toBe(372);
    expect(revealFreeViewport(view, nodes, 1200, 800, 360)).toBe(view);
  });
});


describe('selection and group movement use the same world as the scaled canvas', () => {
  const nodes = [
    { id: 'script', x: 200, y: 120, width: 288, height: 196 },
    { id: 'image', x: 560, y: 120, width: 288, height: 326 },
    { id: 'video', x: 920, y: 120, width: 288, height: 326 },
  ];
  it.each([0.5, 2])('selects the same nodes at zoom %s with a panned viewport', zoom => {
    const view = { x: -180, y: 70, zoom };
    const screen = (point: { x: number; y: number }) => ({ x: point.x * zoom + view.x, y: point.y * zoom + view.y });
    const marquee = freeMarqueeBounds(screen({ x: 180, y: 100 }), screen({ x: 850, y: 450 }), view);
    expect(freeMarqueeHits(marquee, nodes)).toEqual(['script', 'image']);
    expect(marquee).toEqual({ x: 180, y: 100, width: 670, height: 350 });
    // Reverse-direction drag has identical selection and does not depend on the viewport origin.
    expect(freeMarqueeHits(freeMarqueeBounds(screen({ x: 850, y: 450 }), screen({ x: 180, y: 100 }), view), nodes)).toEqual(['script', 'image']);
  });
  it('selects partial overlaps, but not adjacent edges or a zero-area background click', () => {
    expect(freeMarqueeHits({ x: 470, y: 300, width: 90, height: 30 }, nodes)).toEqual(['script']);
    expect(freeMarqueeHits({ x: 488, y: 100, width: 72, height: 350 }, nodes)).toEqual([]);
    expect(freeMarqueeHits({ x: 600, y: 200, width: 0, height: 0 }, nodes)).toEqual([]);
  });
  it('Shift adds and removes hits against the starting selection without changing unaffected members', () => {
    const original = ['script', 'video'];
    expect(mergeFreeSelection(original, ['script', 'image', 'image'], true)).toEqual(['video', 'image']);
    expect(mergeFreeSelection(original, ['image', 'image'], false)).toEqual(['image']);
    expect(mergeFreeSelection(original, [], true)).toEqual(original);
    expect(mergeFreeSelection(original, [], false)).toEqual([]);
    expect(original).toEqual(['script', 'video']);
  });
  it.each([0.5, 2])('group drag keeps spacing and moves only selected nodes at zoom %s', zoom => {
    const positions = { script: { x: 200, y: 120 }, image: { x: 560, y: 120 }, video: { x: 920, y: 120 } };
    const delta = { x: 90, y: -40 };
    const moved = moveFreeNodeGroup(positions, ['script', 'image', 'script', 'missing'], delta, zoom);
    expect((moved.script.x - positions.script.x) * zoom).toBe(delta.x);
    expect((moved.image.y - positions.image.y) * zoom).toBe(delta.y);
    expect(moved.image.x - moved.script.x).toBe(360);
    expect(moved.video).toBe(positions.video);
    expect(moved).not.toHaveProperty('missing');
    expect(positions.script).toEqual({ x: 200, y: 120 });
  });
  it('bounds the whole group together at the world edge, preserving its arrangement', () => {
    const positions = { a: { x: 9_999_900, y: -9_999_900 }, b: { x: 9_999_960, y: -9_999_960 } };
    const moved = moveFreeNodeGroup(positions, ['a', 'b'], { x: 200, y: -200 }, 0.5);
    expect(moved.b).toEqual({ x: 10_000_000, y: -10_000_000 });
    expect(moved.b.x - moved.a.x).toBe(60);
    expect(moved.b.y - moved.a.y).toBe(-60);
  });
});

describe('canvas commands respect editing and focus', () => {
  it.each(['Delete', 'Backspace'])('%s deletes only with canvas or node focus', key => {
    expect(freeCanvasShortcut({ key, editable: false, canvasFocus: true })).toBe('delete');
    expect(freeCanvasShortcut({ key, editable: false, canvasFocus: false })).toBeNull();
    expect(freeCanvasShortcut({ key, editable: true, canvasFocus: true })).toBeNull();
    expect(freeCanvasShortcut({ key, ctrlKey: true, editable: false, canvasFocus: true })).toBeNull();
  });
  it.each([{ ctrlKey: true }, { metaKey: true }])('undo deletion works from canvas controls but leaves editor undo alone: %j', modifier => {
    expect(freeCanvasShortcut({ key: 'z', ...modifier, editable: false, canvasFocus: false })).toBe('undo_delete');
    expect(freeCanvasShortcut({ key: 'z', ...modifier, editable: true, canvasFocus: true })).toBeNull();
    expect(freeCanvasShortcut({ key: 'Z', ...modifier, shiftKey: true, editable: false, canvasFocus: true })).toBeNull();
  });
  it('plain V/H change tools without hijacking paste, text entry or modifier combinations', () => {
    expect(freeCanvasShortcut({ key: 'v', editable: false, canvasFocus: true })).toBe('select_tool');
    expect(freeCanvasShortcut({ key: 'h', editable: false, canvasFocus: true })).toBe('pan_tool');
    expect(freeCanvasShortcut({ key: 'v', metaKey: true, editable: false, canvasFocus: true })).toBeNull();
    expect(freeCanvasShortcut({ key: 'h', editable: true, canvasFocus: true })).toBeNull();
    expect(freeCanvasShortcut({ key: 'h', altKey: true, editable: false, canvasFocus: true })).toBeNull();
  });
});


describe('selected node settings avoid floating creation controls', () => {
  it('moves a 704px selection left of the right toolbar without changing an already usable zoom', () => {
    const node = { x: 700, y: 50, width: FREE_NODE_WIDTH, height: 326 }, panel = freePanelBounds(node);
    const original = { x: 80, y: 50, zoom: 0.5 };
    const view = revealFreeWorkingViewport(original, [node, panel], 704, 684, 0, 82);
    expect(view.zoom).toBe(original.zoom);
    expect((panel.x + panel.width) * view.zoom + view.x).toBeLessThanOrEqual(704 - 82 - 12);
    expect(node.x * view.zoom + view.x).toBeGreaterThanOrEqual(12);
    expect((panel.y + panel.height) * view.zoom + view.y).toBeLessThanOrEqual(584);
    expect(revealFreeWorkingViewport(view, [node, panel], 704, 684, 0, 82)).toBe(view);
  });
  it('shrinks a 440px inspector with its node to fit a 390px work area and bottom controls', () => {
    const node = { x: 110, y: 200, width: FREE_NODE_WIDTH, height: 326 }, panel = freePanelBounds(node);
    const original = { x: 20, y: 30, zoom: 1 };
    const view = revealFreeWorkingViewport(original, [node, panel], 390, 620, 0, 82);
    // Width is limiting here: 390 - 82 toolbar reserve - two 12px margins.
    expect(view.zoom).toBeCloseTo(284 / FREE_PANEL_WIDTH);
    for (const item of [node, panel]) {
      expect(item.x * view.zoom + view.x).toBeGreaterThanOrEqual(12 - 1e-8);
      expect((item.x + item.width) * view.zoom + view.x).toBeLessThanOrEqual(296 + 1e-8);
      expect(item.y * view.zoom + view.y).toBeGreaterThanOrEqual(52 - 1e-8);
      expect((item.y + item.height) * view.zoom + view.y).toBeLessThanOrEqual(520 + 1e-8);
    }
    expect((panel.y - node.y - node.height) * view.zoom).toBeCloseTo(FREE_PANEL_GAP * view.zoom);
    expect(panel.width / node.width).toBe(FREE_PANEL_WIDTH / FREE_NODE_WIDTH);
    expect(original).toEqual({ x: 20, y: 30, zoom: 1 });
  });
  it('leaves an already visible wide-screen selection stationary and also fits short windows by height', () => {
    const node = { x: 500, y: 100, width: FREE_NODE_WIDTH, height: 326 }, panel = freePanelBounds(node);
    const original = { x: 100, y: 20, zoom: 0.75 };
    expect(revealFreeWorkingViewport(original, [node, panel], 1440, 900, 340, 82)).toBe(original);
    const shortView = revealFreeWorkingViewport(original, [node, panel], 1440, 500, 340, 82);
    expect(shortView.zoom).toBeCloseTo((500 - 152) / 720);
    expect((panel.y + panel.height) * shortView.zoom + shortView.y).toBeLessThanOrEqual(400 + 1e-8);
    expect(node.x * shortView.zoom + shortView.x).toBeGreaterThanOrEqual(352 - 1e-8);
  });
});
