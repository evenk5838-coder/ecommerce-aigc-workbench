import { describe, expect, it } from 'vitest';
import type { Model } from '../types';
import type { CanvasNodeRecord, SavedCanvasLayout } from './canvas-layout-sync';
import { buildReferenceConnection, referenceImageId } from './canvas-reference-connection';

const node = (node_id: string, type = 'image', config = {}): CanvasNodeRecord => ({ node_id, type, config, binding: {}, position: { x: 10, y: 20 }, config_revision: 7, last_execution: null, asset: null });
const saved = (): SavedCanvasLayout => ({ projectId: 'p', revision: 9, layout: { version: 1, projectId: 'p', viewport: { x: 0, y: 0, zoom: 1 }, positions: {} }, nodeTypes: {}, edges: [{ from: 'script', to: 'target' }], nodes: [
  { ...node('source', 'asset'), asset: { id: 'image-a', kind: 'image' } }, node('target', 'video', { prompt: 'slow camera', model_id: 'm', params: { duration: 5 } }), node('script', 'script') ] });
const models = [{ id: 'm', capabilities: { input_modes: ['image_to_video'] } }] as Model[];

describe('actual reference connection', () => {
  it('saves the exact reference, current revision and line while retaining prompt, specs and unrelated relations', () => {
    expect(buildReferenceConnection(saved(), 'source', 'target', models)).toEqual({ nodes: [{ node_id: 'target', expected_config_revision: 7, config: { generation_mode: 'image_to_video', input_asset_id: 'image-a', model_id: 'm', prompt: 'slow camera', params: { duration: 5 } } }], edges: [{ from: 'script', to: 'target' }, { from: 'source', to: 'target' }] });
  });
  it('replaces the old input relation without modifying unrelated edges', () => {
    const state = saved(); state.nodes[1].config.input_asset_id = 'old'; state.nodes.push({ ...node('old-source', 'asset'), asset: { id: 'old', kind: 'image' } }); state.edges.push({ from: 'old-source', to: 'target' });
    expect(buildReferenceConnection(state, 'source', 'target', models).edges).toEqual([{ from: 'script', to: 'target' }, { from: 'source', to: 'target' }]);
  });
  it('does not silently select another model when the current one cannot use images', () => {
    expect(() => buildReferenceConnection(saved(), 'source', 'target', [])).toThrow('调整模型');
  });
  it('can connect an unconfigured node before the user chooses its model', () => {
    const state = saved(); state.nodes[1].config = { prompt: 'keep me' };
    expect(buildReferenceConnection(state, 'source', 'target', []).nodes?.[0].config).toEqual({ prompt: 'keep me', generation_mode: 'image_to_video', input_asset_id: 'image-a' });
  });
  it('rejects a missing, changed, video or self source', () => {
    const state = saved();
    expect(() => buildReferenceConnection(state, 'source', 'source', models)).toThrow('另一个');
    expect(() => buildReferenceConnection(state, 'missing', 'target', models)).toThrow('重新读取');
    state.nodes[0].asset!.kind = 'video'; expect(() => buildReferenceConnection(state, 'source', 'target', models)).toThrow('可用的图片');
  });
  it('keeps unavailable output metadata from becoming a reference', () => {
    const source = { ...node('source'), asset: { id: 'a', kind: 'image' }, last_execution: { asset_id: 'a', asset_available: false } };
    expect(referenceImageId(source)).toBeNull();
    source.last_execution = { asset_id: 'b', asset_available: false, last_successful_execution: { asset_id: 'a', asset_available: false } } as unknown as typeof source.last_execution;
    expect(referenceImageId(source)).toBeNull();
  });
  it('keeps a valid previous successful image usable while another execution is pending', () => {
    const source = { ...node('source'), asset: { id: 'a', kind: 'image' }, last_execution: { status: 'queued', asset_id: null, asset_available: false, last_successful_execution: { asset_id: 'a', asset_available: true } } };
    expect(referenceImageId(source)).toBe('a');
  });
  it('does not change the read snapshot when building a proposal', () => {
    const state = saved(), before = JSON.stringify(state); buildReferenceConnection(state, 'source', 'target', models); expect(JSON.stringify(state)).toBe(before);
  });
});
