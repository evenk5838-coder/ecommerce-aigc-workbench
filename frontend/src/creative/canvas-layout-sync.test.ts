import { afterEach, describe, expect, it, vi } from 'vitest';
import { buildCanvasDeletionPreparation, buildCanvasLayoutPatch, buildCanvasMutationPatch, createCanvasLayoutApi, needsCanvasLayoutSave, parseSavedCanvas, reconcileCanvasRead, sameVisibleCanvasLayout, shouldReviewCanvasRead } from './canvas-layout-sync';
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
    expect(saved()).toEqual({ projectId, revision: 4, nodeTypes: { script: 'script' }, nodes: [{ ...canvas.nodes[0], last_execution: null, asset: null }], edges: [], layout: {
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


describe('independent node writes share optimistic canvas persistence', () => {
  it('keeps moving positions together with config edits and never forwards GET-only output fields', () => {
    const base = saved();
    const local = { ...base.layout, positions: { script: { x: -50, y: 120 } } };
    const result = buildCanvasMutationPatch(base, local, [node], { nodes: [{ node_id: 'script', config: { title: '新标题' }, expected_config_revision: 3 }] });
    expect(result).toEqual({ expected_layout_revision: 4, viewport: canvas.viewport, nodes: [{ node_id: 'script', position: { x: -50, y: 120 }, config: { title: '新标题' }, expected_config_revision: 3 }] });
    expect(result.nodes?.[0]).not.toHaveProperty('last_execution');
    expect(result.nodes?.[0]).not.toHaveProperty('asset');
    expect(result.nodes?.[0]).not.toHaveProperty('binding');
    expect(result).not.toHaveProperty('prune');
  });
  it('deletes only specified metadata and leaves edge pruning to the backend', () => {
    const result = buildCanvasMutationPatch(saved(), saved().layout, [node], { delete_node_ids: ['script'] });
    expect(result.nodes).toEqual([]);
    expect(result.delete_node_ids).toEqual(['script']);
    expect(result).not.toHaveProperty('edges');
    expect(() => buildCanvasMutationPatch(saved(), saved().layout, [node], { delete_node_ids: ['script'], nodes: [{ node_id: 'script', config: {} }] })).toThrow();
  });
  it('creates a typed node with configuration and preserves separate layout/business snapshots', () => {
    const result = buildCanvasMutationPatch(saved(), saved().layout, [node], { nodes: [{ node_id: 'text-1', type: 'text', position: { x: 200, y: 70 }, config: { title: '笔记', text: '第一幕\n第二幕' }, expected_config_revision: 0 }], edges: [{ from: 'script', to: 'text-1' }] });
    expect(result.nodes?.[1]).toMatchObject({ node_id: 'text-1', type: 'text', config: { text: '第一幕\n第二幕' }, expected_config_revision: 0 });
    expect(result.edges).toEqual([{ from: 'script', to: 'text-1' }]);
    expect(saved().layout).not.toHaveProperty('nodes');
    expect(saved().nodes[0].binding.script_id).toBe('scr_real');
  });
  it('accepts a validated config change receipt while keeping layout-only transport strict', async () => {
    const changed = { ...ack(), config_changed: true, invalidated: ['scope_old'] };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response(changed));
    const api = createCanvasLayoutApi(fetcher);
    const result = await api.mutate(projectId, buildCanvasMutationPatch(saved(), saved().layout, [node], { nodes: [{ node_id: 'script', config: { title: '重写' }, expected_config_revision: 3 }] }));
    expect(result.revision).toBe(5);
    expect(fetcher).toHaveBeenCalledTimes(1);
  });
  it('keeps config conflicts and uncertain writes explicit without silent retries', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(response({ error: { code: 'CANVAS_REVISION_CONFLICT', message: '节点已更新' } }, 409)).mockRejectedValueOnce(new Error('offline'));
    const api = createCanvasLayoutApi(fetcher), update = buildCanvasMutationPatch(saved(), saved().layout, [node], { nodes: [{ node_id: 'script', config: { title: 'draft' }, expected_config_revision: 3 }] });
    await expect(api.mutate(projectId, update)).rejects.toMatchObject({ code: 'CANVAS_REVISION_CONFLICT', status: 409 });
    await expect(api.mutate(projectId, update)).rejects.toMatchObject({ code: 'CANVAS_LAYOUT_RESULT_UNKNOWN' });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});


describe('new conversation artifacts synchronize without manufactured canonical nodes', () => {
  it('never creates the reserved script from a layout-only display draft', () => {
    const empty = parseSavedCanvas({ ...canvas, nodes: [] }, projectId);
    expect(buildCanvasLayoutPatch(empty, empty.layout, [node]).nodes).toEqual([]);
    expect(needsCanvasLayoutSave(empty, empty.layout, [node])).toBe(false);
    expect(needsCanvasLayoutSave(empty, { ...empty.layout, positions: { script: { x: 700, y: 300 } } }, [node])).toBe(false);
  });
  it('adopts a newly created server script when only the fallback display position differs', () => {
    const before = parseSavedCanvas({ ...canvas, nodes: [] }, projectId);
    const after = parseSavedCanvas({ ...canvas, layout_revision: 5, nodes: [{ ...canvas.nodes[0], position: { x: 0, y: 0 } }] }, projectId);
    expect(shouldReviewCanvasRead(before.layout, after, [node], true, false, before)).toBe(false);
    const locallyMoved = { ...before.layout, positions: { script: { x: 300, y: 90 } } };
    expect(shouldReviewCanvasRead(locallyMoved, after, [node], true, false, before)).toBe(true);
  });
});


describe('three-way layout recovery after same-client script confirmation', () => {
  it('keeps a pending fit/selection viewport while accepting script confirmation and a new derived plan', () => {
    const baseline = saved();
    const local = { ...baseline.layout, viewport: { x: 306, y: -180, zoom: 0.48 } };
    const confirmed = parseSavedCanvas({ ...canvas, layout_revision: 5, nodes: [{ ...canvas.nodes[0], config_revision: 4, binding: { script_id: 'scr_confirmed', script_version: 2 } }] }, projectId);
    const visible = [node, { id: 'plan', type: '分镜设置', x: 450, y: 80 }];
    const reconciled = reconcileCanvasRead(local, confirmed, visible, true, true, baseline);
    expect(reconciled.review).toBe(false);
    expect(reconciled.layout.viewport).toEqual(local.viewport);
    expect(confirmed.nodes[0].binding.script_id).toBe('scr_confirmed');
    expect(needsCanvasLayoutSave(confirmed, reconciled.layout, visible)).toBe(true);
    const pending = buildCanvasLayoutPatch(confirmed, reconciled.layout, visible);
    expect(pending.expected_layout_revision).toBe(5);
    expect(pending.viewport).toEqual(local.viewport);
    expect(pending.nodes.find(n => n.node_id === 'plan')).toEqual({ node_id: 'plan', type: 'script', config: {}, position: { x: 450, y: 80 } });
  });
  it('preserves a local move made during GET when server layout itself has not moved', () => {
    const baseline = saved(), local = { ...baseline.layout, positions: { script: { x: 190, y: -40 } } };
    const result = reconcileCanvasRead(local, baseline, [node], true, true, baseline);
    expect(result.review).toBe(false);
    expect(result.layout.positions.script).toEqual({ x: 190, y: -40 });
  });
  it('requires choice when local and another client change the same viewport differently', () => {
    const baseline = saved(), local = { ...baseline.layout, viewport: { x: 80, y: 50, zoom: 0.6 } };
    const remote = parseSavedCanvas({ ...canvas, layout_revision: 5, viewport: { x: -10, y: 160, zoom: 1.1 } }, projectId);
    expect(reconcileCanvasRead(local, remote, [node], true, true, baseline).review).toBe(true);
  });
  it('requires choice for competing moves of the same node', () => {
    const baseline = saved(), local = { ...baseline.layout, positions: { script: { x: 100, y: 80 } } };
    const remote = parseSavedCanvas({ ...canvas, layout_revision: 5, nodes: [{ ...canvas.nodes[0], position: { x: 800, y: 200 } }] }, projectId);
    expect(reconcileCanvasRead(local, remote, [node], true, false, baseline).review).toBe(true);
  });
  it('merges a local viewport with a separate server-side node move', () => {
    const baseline = saved(), local = { ...baseline.layout, viewport: { x: 80, y: 50, zoom: 0.6 } };
    const remote = parseSavedCanvas({ ...canvas, layout_revision: 5, nodes: [{ ...canvas.nodes[0], position: { x: 800, y: 200 } }] }, projectId);
    const result = reconcileCanvasRead(local, remote, [node], true, false, baseline);
    expect(result.review).toBe(false);
    expect(result.layout.viewport).toEqual(local.viewport);
    expect(result.layout.positions.script).toEqual({ x: 800, y: 200 });
  });
  it('accepts server deletion if the local node was not moved, but protects a concurrent local move', () => {
    const baseline = saved(), remote = parseSavedCanvas({ ...canvas, layout_revision: 5, nodes: [] }, projectId);
    expect(reconcileCanvasRead(baseline.layout, remote, [], true, false, baseline)).toMatchObject({ review: false, layout: { positions: {} } });
    const moved = { ...baseline.layout, positions: { script: { x: 11, y: 22 } } };
    expect(reconcileCanvasRead(moved, remote, [], true, true, baseline).review).toBe(true);
  });
  it('does not treat an inherited object property as a local edit for special node IDs', () => {
    const baseline = parseSavedCanvas({ ...canvas, nodes: [] }, projectId);
    const remote = parseSavedCanvas({ ...canvas, nodes: [{ ...canvas.nodes[0], node_id: 'constructor' }] }, projectId);
    const result = reconcileCanvasRead({ ...baseline.layout, positions: {} }, remote, [], true, false, baseline);
    expect(result.review).toBe(false);
    expect(result.layout.positions.constructor).toEqual({ x: 60, y: 80 });
  });
});


describe('delete receipts are only delivered by the explicit mutation', () => {
  it('returns the deletion operation to the caller, without deriving one from a subsequent read', async () => {
    const deletedCanvas = { ...canvas, layout_revision: 5, nodes: [] };
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(response({ ...ack(), canvas: deletedCanvas, deletion_operation_id: 'delete-123' }))
      .mockResolvedValueOnce(response({ ...deletedCanvas, deletion_operation_id: 'not-a-receipt' }));
    const api = createCanvasLayoutApi(fetcher);
    const result = await api.mutate(projectId, buildCanvasMutationPatch(saved(), saved().layout, [node], { delete_node_ids: ['script'] }));
    expect(result.deletionOperationId).toBe('delete-123');
    expect(result.nodes).toEqual([]);
    expect(await api.read(projectId)).not.toHaveProperty('deletionOperationId');
  });
  it('does not invent an operation ID when a mutation has no deletion receipt', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response(ack()));
    expect(await createCanvasLayoutApi(fetcher).mutate(projectId, buildCanvasMutationPatch(saved(), saved().layout, [node], { nodes: [] }))).not.toHaveProperty('deletionOperationId');
  });
  it.each(['', 123])('rejects a malformed receipt instead of enabling an unverifiable undo: %j', deletion_operation_id => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response({ ...ack(), deletion_operation_id }));
    return expect(createCanvasLayoutApi(fetcher).mutate(projectId, buildCanvasMutationPatch(saved(), saved().layout, [node], { delete_node_ids: ['script'] }))).rejects.toMatchObject({ code: 'CANVAS_LAYOUT_RESULT_UNKNOWN' });
  });
});


describe('persistent deletion markers prevent derived nodes from reappearing', () => {
  const plan = { id: 'plan', type: '分镜设置', x: 450, y: 80 };
  it('parses an optional complete deleted-node list without mixing it into local layout storage', () => {
    const value = parseSavedCanvas({ ...canvas, deleted_node_ids: ['plan', 'image:shot-1'] }, projectId);
    expect(value.deletedNodeIds).toEqual(['plan', 'image:shot-1']);
    expect(value.layout).not.toHaveProperty('deletedNodeIds');
    expect(saved()).not.toHaveProperty('deletedNodeIds');
    expect(parseSavedCanvas({ ...canvas, deleted_node_ids: [] }, projectId).deletedNodeIds).toEqual([]);
  });
  it.each([null, 'plan', [42], [''], ['  '], ['plan', 'plan'], [{}]])('rejects invalid deleted IDs instead of silently ignoring the protection: %j', deleted_node_ids => {
    expect(() => parseSavedCanvas({ ...canvas, deleted_node_ids }, projectId)).toThrow();
  });
  it('ignores a deleted derived display node even with an old cached or manually reset position', () => {
    const base = parseSavedCanvas({ ...canvas, deleted_node_ids: ['plan'] }, projectId);
    const local = { ...base.layout, positions: { ...base.layout.positions, plan: { x: 999, y: 345 } } };
    const result = buildCanvasLayoutPatch(base, local, [node, plan]);
    expect(result.nodes.map(item => item.node_id)).toEqual(['script']);
    expect(needsCanvasLayoutSave(base, local, [node, plan])).toBe(false);
    expect(buildCanvasMutationPatch(base, local, [node, plan], { nodes: [{ node_id: 'note', type: 'text', config: { text: 'keep' } }] }).nodes?.map(item => item.node_id)).toEqual(['script', 'note']);
    expect(buildCanvasDeletionPreparation(base, local, [node, plan], { delete_node_ids: ['plan'] })).toBeNull();
  });
  it('allows a real restored node to move even if an older deleted ID is still reported', () => {
    const base = parseSavedCanvas({ ...canvas, deleted_node_ids: ['plan'], nodes: [...canvas.nodes, { node_id: 'plan', type: 'script', position: { x: 450, y: 80 }, config: {} }] }, projectId);
    const local = { ...base.layout, positions: { ...base.layout.positions, plan: { x: 600, y: 100 } } };
    expect(buildCanvasLayoutPatch(base, local, [node, plan]).nodes[1]).toEqual({ node_id: 'plan', position: { x: 600, y: 100 } });
    expect(needsCanvasLayoutSave(base, local, [node, plan])).toBe(true);
  });
  it('keeps viewport changes saveable without recreating a deleted node', () => {
    const base = parseSavedCanvas({ ...canvas, deleted_node_ids: ['plan'] }, projectId);
    const local = { ...base.layout, viewport: { x: -100, y: 200, zoom: 0.5 } };
    expect(needsCanvasLayoutSave(base, local, [plan])).toBe(true);
    expect(buildCanvasLayoutPatch(base, local, [plan])).toEqual({ expected_layout_revision: 4, viewport: local.viewport, nodes: [] });
  });
});

describe('deleting a not-yet-saved derived node prepares a real undoable record', () => {
  const plan = { id: 'plan', type: '分镜设置', x: 450, y: 80 };
  it('needs no preparation for an already saved target or a non-delete mutation', () => {
    expect(buildCanvasDeletionPreparation(saved(), saved().layout, [node], { delete_node_ids: ['script'] })).toBeNull();
    expect(buildCanvasDeletionPreparation(saved(), saved().layout, [node, plan], { nodes: [{ node_id: 'note', type: 'text' }] })).toBeNull();
  });
  it('prepares only normal layout state, without applying deletion or explicit edge edits early', () => {
    const change = { delete_node_ids: ['plan'], edges: [] };
    const prepare = buildCanvasDeletionPreparation(saved(), saved().layout, [node, plan], change);
    expect(prepare).toEqual({ expected_layout_revision: 4, viewport: canvas.viewport, nodes: [
      { node_id: 'script', position: { x: 60, y: 80 } },
      { node_id: 'plan', position: { x: 450, y: 80 }, type: 'script', config: {} },
    ] });
    expect(prepare).not.toHaveProperty('edges');
    expect(prepare).not.toHaveProperty('delete_node_ids');
    expect(prepare).not.toHaveProperty('prune');
  });
  it('refuses unknown targets and the reserved manual script before dispatching a preparation', () => {
    const empty = parseSavedCanvas({ ...canvas, nodes: [] }, projectId);
    expect(() => buildCanvasDeletionPreparation(empty, empty.layout, [node], { delete_node_ids: ['script'] })).toThrow('节点尚未保存');
    expect(() => buildCanvasDeletionPreparation(saved(), saved().layout, [node], { delete_node_ids: ['unseen'] })).toThrow('节点尚未保存');
    expect(() => buildCanvasDeletionPreparation(saved(), saved().layout, [node, plan], { delete_node_ids: ['plan', 'plan'] })).toThrow();
  });
  it('uses the preparation acknowledgement revision for the actual deletion and preserves the receipt', async () => {
    const visible = [node, plan], change = { delete_node_ids: ['plan'], edges: [] };
    const preparedCanvas = { ...canvas, layout_revision: 5, nodes: [...canvas.nodes, { node_id: 'plan', type: 'script', position: { x: 450, y: 80 }, config: {} }] };
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(response({ ...ack(), canvas: preparedCanvas }))
      .mockResolvedValueOnce(response({ ...ack(), layout_revision: 6, canvas: { ...canvas, layout_revision: 6, deleted_node_ids: ['plan'] }, deletion_operation_id: 'delete-plan' }));
    const api = createCanvasLayoutApi(fetcher);
    const prepare = buildCanvasDeletionPreparation(saved(), saved().layout, visible, change)!;
    const prepared = await api.save(projectId, prepare);
    const deleted = await api.mutate(projectId, buildCanvasMutationPatch(prepared, prepared.layout, visible, change));
    const writes = fetcher.mock.calls.map(([, init]) => JSON.parse(String(init?.body)));
    expect(writes[0]).toHaveProperty('expected_layout_revision', 4);
    expect(writes[0].nodes.map((item: { node_id: string }) => item.node_id)).toContain('plan');
    expect(writes[1]).toMatchObject({ expected_layout_revision: 5, delete_node_ids: ['plan'], edges: [] });
    expect(writes[1].nodes.map((item: { node_id: string }) => item.node_id)).not.toContain('plan');
    expect(deleted.deletionOperationId).toBe('delete-plan');
    expect(deleted.deletedNodeIds).toEqual(['plan']);
    expect(needsCanvasLayoutSave(deleted, deleted.layout, visible)).toBe(false);
  });
});
