import { describe, expect, it } from 'vitest';
import { isFreeReferenceSource, isFreeReferenceTarget, moveFreeReference, resolveFreeReference } from './free-canvas-reference';
import type { FreeReferenceDraft, FreeReferenceNode } from './free-canvas-reference';

const nodes: FreeReferenceNode[] = [
  { id: 'upload', referenceImageAssetId: 'asset-picture' },
  { id: 'generated-picture', referenceImageAssetId: 'asset-generated', referenceTargetKind: 'image' },
  { id: 'video', referenceTargetKind: 'video' },
  { id: 'legacy-video' }, { id: 'script' }, { id: 'imported-video' },
];
const draft: FreeReferenceDraft = { sourceId: 'upload', sourceAssetId: 'asset-picture', point: { x: 0, y: 0 }, targetId: null };

describe('真实参考图连接资格与手势', () => {
  it('真实导入图片和生成图片均能引用到独立图片或视频节点', () => {
    expect(resolveFreeReference(draft, 'generated-picture', nodes)).toEqual({ sourceId: 'upload', targetId: 'generated-picture' });
    expect(resolveFreeReference({ sourceId: 'generated-picture', sourceAssetId: 'asset-generated' }, 'video', nodes)).toEqual({ sourceId: 'generated-picture', targetId: 'video' });
  });
  it('视频封面、脚本、仅有预览URL和旧流程节点不获得引用资格', () => {
    const previewOnly = { id: 'poster', image: '/assets/cover.png', video: '/assets/film.mp4' };
    expect(isFreeReferenceSource(previewOnly)).toBe(false);
    expect(isFreeReferenceSource({ id: 'empty', referenceImageAssetId: '  ' })).toBe(false);
    for (const id of ['script', 'imported-video', 'legacy-video']) {
      expect(isFreeReferenceSource(nodes.find(node => node.id === id)!)).toBe(false);
      expect(isFreeReferenceTarget(nodes.find(node => node.id === id)!)).toBe(false);
      expect(resolveFreeReference(draft, id, nodes)).toBeNull();
    }
  });
  it('拒绝自引用、已删除来源/目标以及拖动中被换掉的源图片', () => {
    expect(resolveFreeReference({ sourceId: 'generated-picture', sourceAssetId: 'asset-generated' }, 'generated-picture', nodes)).toBeNull();
    expect(resolveFreeReference(draft, 'video', nodes.filter(node => node.id !== 'upload'))).toBeNull();
    expect(resolveFreeReference(draft, 'video', nodes.filter(node => node.id !== 'video'))).toBeNull();
    expect(resolveFreeReference(draft, 'video', nodes.map(node => node.id === 'upload' ? { ...node, referenceImageAssetId: 'asset-new' } : node))).toBeNull();
  });
  it.each([.5, 1.6])('在 %s 倍缩放和平移后，拖线终点仍对应指针世界坐标', zoom => {
    const viewport = { x: -120, y: 86, zoom }, world = { x: 760, y: 248 };
    const moved = moveFreeReference(draft, { x: world.x * zoom + viewport.x, y: world.y * zoom + viewport.y }, viewport, 'video', nodes);
    expect(moved.point.x).toBeCloseTo(world.x);
    expect(moved.point.y).toBeCloseTo(world.y);
    expect(moved.targetId).toBe('video');
    expect(draft).toEqual({ sourceId: 'upload', sourceAssetId: 'asset-picture', point: { x: 0, y: 0 }, targetId: null });
  });
  it('只认可实际命中的可连接节点，不越过遮挡控件或无效目标虚构连线', () => {
    for (const hit of [null, 'script', 'missing', 'upload']) {
      const moved = moveFreeReference(draft, { x: 260, y: 200 }, { x: 10, y: 20, zoom: .5 }, hit, nodes);
      expect(moved.targetId).toBeNull();
      expect(moved.point).toEqual({ x: 500, y: 360 });
    }
  });
});
