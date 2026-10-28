import type { Model } from '../types';
import type { CanvasMutation, CanvasNodeRecord, SavedCanvasLayout } from './canvas-layout-sync';
import { objectValue, textValue } from './node-config';

export function referenceImageId(node: CanvasNodeRecord): string | null {
  if (node.asset?.kind !== 'image') return null;
  const execution = node.last_execution;
  if (execution?.asset_id === node.asset.id && execution.asset_available === false) return null;
  const previous = objectValue(execution?.last_successful_execution);
  if (previous.asset_id === node.asset.id && previous.asset_available === false) return null;
  return node.asset.id;
}

/** Connect the actual image input and its visible source in the same canvas write. */
export function buildReferenceConnection(saved: SavedCanvasLayout, sourceId: string, targetId: string, models: readonly Model[]): CanvasMutation {
  if (sourceId === targetId) throw new Error('请选择另一个节点作为参考。');
  const source = saved.nodes.find(node => node.node_id === sourceId), target = saved.nodes.find(node => node.node_id === targetId);
  if (!source || !target) throw new Error('节点已变化，请重新读取画布。');
  const assetId = referenceImageId(source);
  if (!assetId) throw new Error('请从可用的图片素材连接。');
  if (!['image', 'video'].includes(target.type) || targetId.startsWith('image:') || targetId.startsWith('video:') || targetId === 'generation') throw new Error('请连接到图片或视频生成节点。');
  const mode = target.type === 'image' ? 'image_to_image' : 'image_to_video';
  const modelId = textValue(target.config.model_id), model = models.find(item => item.id === modelId);
  if (modelId && (!model || !model.capabilities?.input_modes?.includes(mode))) throw new Error(`当前模型不支持${target.type === 'image' ? '图生图' : '图生视频'}，请先调整模型。`);
  const oldReference = target.config.input_asset_id;
  const edges = saved.edges.filter(edge => {
    if (edge.to !== targetId) return true;
    const previousSource = saved.nodes.find(node => node.node_id === edge.from);
    return !previousSource || !oldReference || referenceImageId(previousSource) !== oldReference;
  });
  if (!edges.some(edge => edge.from === sourceId && edge.to === targetId)) edges.push({ from: sourceId, to: targetId });
  return { nodes: [{ node_id: targetId, expected_config_revision: target.config_revision,
    config: { ...target.config, generation_mode: mode, input_asset_id: assetId } }], edges };
}
