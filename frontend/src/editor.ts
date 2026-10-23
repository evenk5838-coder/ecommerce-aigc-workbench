import type { Asset, Layer, Project, TaskInput } from './types';

export function metadata(asset: Asset): Record<string, unknown> {
  try { return JSON.parse(asset.meta_json || '{}') as Record<string, unknown>; } catch { return {}; }
}
export function realAssets(assets: Asset[]): Asset[] {
  return assets.filter(a => metadata(a).model_id !== 'fake-image' && metadata(a).filename !== 'smoke.png');
}
export function newProject(): Project {
  return { name: '未命名商品项目', canvas: { width: 1080, height: 1080, background: '#ffffff', layers: [] }, assetGroups: {} };
}
export function fitAsset(asset: Asset, width: number, height: number): Layer {
  const ratio = Math.min(width / asset.width, height / asset.height);
  const w = Math.round(asset.width * ratio), h = Math.round(asset.height * ratio);
  return { id: crypto.randomUUID(), type: 'image', name: '商品图片', assetId: asset.id,
    x: Math.round((width - w) / 2), y: Math.round((height - h) / 2), w, h, rotation: 0, opacity: 1 };
}
export function moveLayer(layer: Layer, dx: number, dy: number, width: number, height: number): Layer {
  return { ...layer, x: Math.round(Math.max(-layer.w / 2, Math.min(width - layer.w / 2, layer.x + dx))),
    y: Math.round(Math.max(-layer.h / 2, Math.min(height - layer.h / 2, layer.y + dy))) };
}
export function makeBatch(base: Omit<TaskInput, 'client_request_id'>, count: number, sizes: [number, number][]): TaskInput[] {
  return sizes.flatMap(([width, height]) => Array.from({ length: count }, (_, index) => ({
    ...base, params: { ...base.params, width, height,
      seed: typeof base.params.seed === 'number' && base.params.seed >= 0 ? base.params.seed + index : -1 },
    client_request_id: crypto.randomUUID(),
  })));
}
export function safeName(name: string): string { return name.replace(/[^\p{L}\p{N}._-]/gu, '_').slice(0, 80) || '商品画布'; }

export function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob), anchor = document.createElement('a');
  anchor.href = url; anchor.download = filename; anchor.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 30000);
}
export async function renderCanvas(project: Project): Promise<Blob> {
  const { canvas: doc } = project;
  const canvas = document.createElement('canvas'); canvas.width = doc.width; canvas.height = doc.height;
  const ctx = canvas.getContext('2d'); if (!ctx) throw new Error('当前浏览器无法导出画布');
  ctx.fillStyle = doc.background; ctx.fillRect(0, 0, doc.width, doc.height);
  for (const layer of doc.layers) {
    ctx.save(); ctx.globalAlpha = layer.opacity;
    ctx.translate(layer.x + layer.w / 2, layer.y + layer.h / 2); ctx.rotate(layer.rotation * Math.PI / 180);
    if (layer.type === 'image' && layer.assetId) {
      const image = new Image(); image.src = `/api/assets/${encodeURIComponent(layer.assetId)}/file`;
      await image.decode().catch(() => { throw new Error(`图层“${layer.name}”加载失败，未导出不完整画布`); });
      ctx.drawImage(image, -layer.w / 2, -layer.h / 2, layer.w, layer.h);
    } else if (layer.type === 'shape') {
      ctx.fillStyle = layer.fill || '#e9edff'; ctx.fillRect(-layer.w / 2, -layer.h / 2, layer.w, layer.h);
    } else if (layer.type === 'text') {
      const size = layer.fontSize || 64;
      ctx.fillStyle = layer.color || '#202334'; ctx.font = `600 ${size}px -apple-system, "PingFang SC", sans-serif`; ctx.textBaseline = 'top';
      (layer.text || '').split('\n').forEach((line, i) => ctx.fillText(line, -layer.w / 2, -layer.h / 2 + i * size * 1.3));
    }
    ctx.restore();
  }
  return new Promise((resolve, reject) => canvas.toBlob(blob => blob ? resolve(blob) : reject(new Error('PNG 导出失败')), 'image/png'));
}
