import { afterEach, describe, expect, it, vi } from 'vitest';
import { buildCanvasLayoutPatch, createCanvasLayoutApi, needsCanvasLayoutSave, parseSavedCanvas, sameVisibleCanvasLayout, shouldReviewCanvasRead } from './canvas-layout-sync';
import { initialFreeLayout } from './free-canvas-state';

const projectId = 'prj_layout';
const node = { id: 'script', type: '脚本', x: 60, y: 80 };
const canvas = {
  schema_version: 1, layout_revision: 4, viewport: { x: 10, y: 20, zoom: 0.9 },
  nodes: [{ node_id: 'script', type: 'script', position: { x: 60, y: 80 }, config: { prompt: '保留服务器内容' }, binding: { script_id: 'scr_real' }, config_revision: 3 }],
  edges: [],
};
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
const saved = () => parseSavedCanvas(canvas, projectId);
const patch = () => buildCanvasLayoutPatch(saved(), saved().layout, [node]);
const ack = () => ({ project_id: projectId, layout_revision: 5, config_changed: false, invalidated: [], canvas: { ...canvas, layout_revision: 5 } });
afterEach(() => { vi.useRealTimers(); vi.restoreAllMocks(); });

describe('layout-only canvas contract', () => {
  it('reads positions and viewport while excluding server config, binding, outputs and edges from the local layout', () => {
    expect(saved()).toEqual({ projectId, revision: 4, nodeTypes: { script: 'script' }, layout: {
      version: 1, projectId, viewport: canvas.viewport, positions: { script: { x: 60, y: 80 } },
    } });
  });

  it('moves an existing node without supplying its type, config, binding or any production fields', () => {
    const base = saved(), local = { ...base.layout, positions: { script: { x: -90, y: 230 } } };
    expect(buildCanvasLayoutPatch(base, local, [node])).toEqual({
      expected_layout_revision: 4, viewport: canvas.viewport, nodes: [{ node_id: 'script', position: { x: -90, y: 230 } }],
    });
  });

  it('creates a display plan with an explicit harmless type and empty config without inferred generation or binding', () => {
    const value = buildCanvasLayoutPatch(saved(), saved().layout, [{ id: 'plan', type: '分镜设置', x: 450, y: 80 }]);
    expect(value.nodes).toEqual([{ node_id: 'plan', position: { x: 450, y: 80 }, type: 'script', config: {} }]);
    expect(value).not.toHaveProperty('prune');
    expect(value).not.toHaveProperty('edges');
  });

  it('keeps hidden server nodes by never sending prune and does not forward cached extra fields', () => {
    const base = parseSavedCanvas({ ...canvas, nodes: [...canvas.nodes, { node_id: 'hidden', type: 'video', position: { x: 8, y: 9 }, config: { model_id: 'real' } }] }, projectId);
    const cached = { ...base.layout, unexpected: 'do not send' };
    const value = buildCanvasLayoutPatch(base, cached, [node]);
    expect(Object.keys(value)).toEqual(['expected_layout_revision', 'viewport', 'nodes']);
    expect(value.nodes.map(item => item.node_id)).toEqual(['script']);
  });

  it('reset positions use the visible node defaults rather than deleting server nodes', () => {
    const value = buildCanvasLayoutPatch(saved(), initialFreeLayout(projectId), [node]);
    expect(value.nodes[0].position).toEqual({ x: 60, y: 80 });
    expect(value).not.toHaveProperty('prune');
  });

  it('refuses unknown new types and cross-project writes instead of inventing capabilities', () => {
    expect(() => buildCanvasLayoutPatch(saved(), saved().layout, [{ id: 'mystery', type: 'new workflow' }])).toThrow();
    expect(() => buildCanvasLayoutPatch(saved(), initialFreeLayout('another_project'), [node])).toThrow();
    expect(() => buildCanvasLayoutPatch(saved(), saved().layout, [node, node])).toThrow();
  });

  it.each([
    { ...canvas, schema_version: 2 }, { ...canvas, layout_revision: -1 },
    { ...canvas, project_id: 'other' }, { ...canvas, viewport: { x: 0, y: 0, zoom: 0 } },
    { ...canvas, nodes: [{ ...canvas.nodes[0], position: { x: Infinity, y: 0 } }] },
    { ...canvas, nodes: [canvas.nodes[0], canvas.nodes[0]] },
  ])('refuses invalid or incompatible remote state without clamping/inventing layout: %j', value => {
    expect(() => parseSavedCanvas(value, projectId)).toThrow();
  });
});

describe('read/restore choices protect local edits', () => {
  it('adopts server layout only without cache or edits made during GET', () => {
    expect(shouldReviewCanvasRead(initialFreeLayout(projectId), saved(), [node], false, false)).toBe(false);
    expect(shouldReviewCanvasRead(initialFreeLayout(projectId), saved(), [node], true, false)).toBe(true);
    expect(shouldReviewCanvasRead(initialFreeLayout(projectId), saved(), [node], false, true)).toBe(true);
  });

  it('resolves a lost acknowledgement when the server has exactly the same visible layout', () => {
    expect(shouldReviewCanvasRead(saved().layout, saved(), [node], true, true)).toBe(false);
    expect(sameVisibleCanvasLayout(saved().layout, { ...saved().layout, positions: { ...saved().layout.positions, unused: { x: 1, y: 2 } } }, [node])).toBe(true);
  });

  it('uses the freshly selected server revision when the user explicitly keeps local changes', () => {
    const newest = parseSavedCanvas({ ...canvas, layout_revision: 9 }, projectId);
    const local = { ...saved().layout, viewport: { x: 100, y: -90, zoom: 0.8 } };
    expect(buildCanvasLayoutPatch(newest, local, [node]).expected_layout_revision).toBe(9);
    expect(buildCanvasLayoutPatch(newest, local, [node]).viewport).toEqual(local.viewport);
  });

  it('does not discard unsaved positions while their nodes are still loading', () => {
    const local = { ...saved().layout, positions: { ...saved().layout.positions, 'image:shot1': { x: 800, y: 320 } } };
    expect(shouldReviewCanvasRead(local, saved(), [], true, false)).toBe(true);
    // When the real node arrives, its retained position can be persisted with its explicit display type.
    const nodes = [{ id: 'image:shot1', type: '分镜图' }];
    expect(needsCanvasLayoutSave(saved(), local, nodes)).toBe(true);
    expect(buildCanvasLayoutPatch(saved(), local, nodes).nodes[0]).toEqual({ node_id: 'image:shot1', position: { x: 800, y: 320 }, type: 'storyboard', config: {} });
  });

  it('saves an edited hidden existing node as position-only while retaining its configuration', () => {
    const local = { ...saved().layout, positions: { script: { x: 240, y: 400 } } };
    expect(needsCanvasLayoutSave(saved(), local, [])).toBe(true);
    expect(buildCanvasLayoutPatch(saved(), local, []).nodes).toEqual([{ node_id: 'script', position: { x: 240, y: 400 } }]);
    expect(needsCanvasLayoutSave(saved(), saved().layout, [node])).toBe(false);
  });
});

describe('single-attempt layout transport (mock fetch; no business calls)', () => {
  it('reads only on request and sends one layout PATCH with the version precondition', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(response(canvas)).mockResolvedValueOnce(response(ack()));
    const api = createCanvasLayoutApi(fetcher);
    expect(fetcher).not.toHaveBeenCalled();
    expect((await api.read(projectId)).revision).toBe(4);
    expect((await api.save(projectId, patch())).revision).toBe(5);
    expect(fetcher.mock.calls.map(([url, init]) => [url, init?.method])).toEqual([
      ['/api/projects/prj_layout/canvas', 'GET'], ['/api/projects/prj_layout/canvas', 'PATCH'],
    ]);
    expect(JSON.parse(String(fetcher.mock.calls[1][1]?.body))).toEqual(patch());
  });

  it('keeps a revision conflict visible and does not retry with the server revision', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response({ error: { code: 'CANVAS_REVISION_CONFLICT', message: '画布已被更新', detail: { current_layout_revision: 9 } } }, 409));
    await expect(createCanvasLayoutApi(fetcher).save(projectId, patch())).rejects.toMatchObject({ code: 'CANVAS_REVISION_CONFLICT', status: 409 });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it.each([
    { ...ack(), project_id: 'other' }, { ...ack(), layout_revision: 4 },
    { ...ack(), config_changed: true }, { ...ack(), invalidated: ['scope'] },
  ])('keeps an unsafe or mismatched acknowledgement unknown: %j', async value => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response(value));
    await expect(createCanvasLayoutApi(fetcher).save(projectId, patch())).rejects.toMatchObject({ code: 'CANVAS_LAYOUT_RESULT_UNKNOWN' });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('never overwrites after a read error and does not auto-retry an unknown write', async () => {
    const fetcher = vi.fn<typeof fetch>().mockRejectedValue(new Error('offline'));
    const api = createCanvasLayoutApi(fetcher);
    await expect(api.read(projectId)).rejects.toMatchObject({ code: 'CANVAS_LAYOUT_READ_FAILED' });
    expect(fetcher).toHaveBeenCalledTimes(1);
    await expect(api.save(projectId, patch())).rejects.toMatchObject({ code: 'CANVAS_LAYOUT_RESULT_UNKNOWN' });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it('settles an aborted write as unknown even if the injected transport ignores cancellation', async () => {
    const fetcher = vi.fn<typeof fetch>().mockImplementation(() => new Promise<Response>(() => {}));
    const controller = new AbortController(), api = createCanvasLayoutApi(fetcher);
    const pending = api.save(projectId, patch(), controller.signal);
    controller.abort();
    await expect(pending).rejects.toMatchObject({ code: 'CANVAS_LAYOUT_RESULT_UNKNOWN' });
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it('never dispatches an aborted project read or save', async () => {
    const fetcher = vi.fn<typeof fetch>(), controller = new AbortController();
    controller.abort();
    await expect(createCanvasLayoutApi(fetcher).read(projectId, controller.signal)).rejects.toMatchObject({ code: 'CANVAS_LAYOUT_ABORTED' });
    await expect(createCanvasLayoutApi(fetcher).save(projectId, patch(), controller.signal)).rejects.toMatchObject({ code: 'CANVAS_LAYOUT_ABORTED' });
    expect(fetcher).not.toHaveBeenCalled();
  });
});
