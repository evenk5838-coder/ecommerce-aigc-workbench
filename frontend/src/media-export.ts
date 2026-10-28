import JSZip from 'jszip';
import { mediaUrl } from './api';
import { metadata, safeName } from './editor';
import type { Asset } from './types';

export function assetExtension(asset: Pick<Asset, 'kind' | 'mime'>): string {
  const mime = asset.mime.toLowerCase().split(';')[0].trim();
  const formats: Record<string, string> = asset.kind === 'video'
    ? { 'video/mp4': 'mp4' } : { 'image/png': 'png', 'image/jpeg': 'jpg', 'image/webp': 'webp' };
  if (!formats[mime]) throw new Error('暂不支持打包此素材格式，请下载原文件。');
  return formats[mime];
}

export async function createAssetArchive(assets: readonly Asset[], groups: Record<string, string>,
  fetcher: typeof fetch = (...args) => fetch(...args)): Promise<Blob> {
  if (!assets.length) throw new Error('请先选择要导出的素材。');
  const zip = new JSZip();
  for (let i = 0; i < assets.length; i++) {
    const asset = assets[i], extension = assetExtension(asset);
    const response = await fetcher(mediaUrl(asset.id));
    if (!response.ok) throw new Error('素材下载失败，请重试打包。');
    const mime = response.headers.get('Content-Type')?.split(';')[0].trim().toLowerCase();
    if (mime !== asset.mime.split(';')[0].trim().toLowerCase()) throw new Error('素材返回格式异常，请刷新后重试。');
    const bytes = await response.arrayBuffer();
    if (!bytes.byteLength) throw new Error('素材文件为空，请刷新后重试。');
    zip.file(`${String(i + 1).padStart(2, '0')}-${safeName(asset.id)}.${extension}`, bytes);
  }
  zip.file('素材记录.json', JSON.stringify(assets.map(asset => ({ id: asset.id,
    name: typeof metadata(asset).filename === 'string' ? metadata(asset).filename : asset.kind === 'video' ? '生成视频' : `生成图片 · ${asset.width}×${asset.height}`,
    width: asset.width, height: asset.height, sku: groups[asset.id] || '', metadata: metadata(asset),
  })), null, 2));
  return zip.generateAsync({ type: 'blob' });
}
