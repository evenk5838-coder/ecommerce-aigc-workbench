import { freeWorldPoint } from './free-canvas-state';
import type { FreePoint, FreeViewport } from './free-canvas-state';

/** Eligibility comes from saved business records, never from preview URLs or labels. */
export interface FreeReferenceNode {
  id: string;
  referenceImageAssetId?: string;
  referenceTargetKind?: 'image' | 'video';
}
export interface FreeReferenceDraft {
  sourceId: string;
  sourceAssetId: string;
  point: FreePoint;
  targetId: string | null;
}
export function isFreeReferenceSource(node: FreeReferenceNode): boolean {
  return typeof node.referenceImageAssetId === 'string' && !!node.referenceImageAssetId.trim();
}
export function isFreeReferenceTarget(node: FreeReferenceNode): boolean {
  return node.referenceTargetKind === 'image' || node.referenceTargetKind === 'video';
}
/** Reject stale source previews, deleted nodes, self-links and non-generation targets. */
export function resolveFreeReference(draft: Pick<FreeReferenceDraft, 'sourceId' | 'sourceAssetId'>, targetId: string | null, nodes: readonly FreeReferenceNode[]): { sourceId: string; targetId: string } | null {
  const source = nodes.find(node => node.id === draft.sourceId), target = nodes.find(node => node.id === targetId);
  if (!source || !isFreeReferenceSource(source) || source.referenceImageAssetId !== draft.sourceAssetId
    || !target || target.id === source.id || !isFreeReferenceTarget(target)) return null;
  return { sourceId: source.id, targetId: target.id };
}
/** Pointer coordinates are viewport pixels; the draft line belongs to the scaled world. */
export function moveFreeReference(draft: FreeReferenceDraft, screen: FreePoint, viewport: FreeViewport, hitId: string | null, nodes: readonly FreeReferenceNode[]): FreeReferenceDraft {
  return { ...draft, point: freeWorldPoint(screen, viewport), targetId: resolveFreeReference(draft, hitId, nodes)?.targetId ?? null };
}
