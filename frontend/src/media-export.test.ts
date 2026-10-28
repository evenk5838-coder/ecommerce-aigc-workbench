import { describe, expect, it, vi } from 'vitest';
import JSZip from 'jszip';
import { createAssetArchive } from './media-export';
import type { Asset } from './types';

const asset = (id: string, mime = 'image/png'): Asset => ({ id, kind: mime.startsWith('video/') ? 'video' : 'image',
  mime, size: 4, width: 64, height: 48, source: 'upload', created_at: 1, meta_json: '{}' });

describe('Web 素材打包', () => {
  it('WebP、JPEG、PNG 和 MP4 保留真实扩展名、原始字节及 SKU 清单', async () => {
    const assets = [asset('webp-id', 'image/webp'), asset('jpeg-id', 'image/jpeg'), asset('png-id'), asset('video-id', 'video/mp4')];
    const inputs = assets.map((_, index) => new Uint8Array([index + 1, 2, 3, 4]));
    const fetcher = vi.fn<typeof fetch>();
    assets.forEach((item, i) => fetcher.mockResolvedValueOnce(new Response(inputs[i], { headers: { 'Content-Type': item.mime } })));
    const output = await createAssetArchive(assets, { 'webp-id': '白色杯' }, fetcher);
    const zip = await JSZip.loadAsync(await output.arrayBuffer());
    const names = ['01-webp-id.webp', '02-jpeg-id.jpg', '03-png-id.png', '04-video-id.mp4'];
    for (const [i, name] of names.entries()) expect(await zip.file(name)!.async('uint8array')).toEqual(inputs[i]);
    const manifest = JSON.parse(await zip.file('素材记录.json')!.async('string'));
    expect(manifest[0]).toMatchObject({ id: 'webp-id', sku: '白色杯' });
    expect(fetcher.mock.calls.map(args => args[0])).toEqual(assets.map(a => `/api/assets/${a.id}/file`));
  });

  it('任何素材请求失败都拒绝整个导出，不能下载不完整 ZIP', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(new Response(new Uint8Array([1]), { headers: { 'Content-Type': 'image/png' } }))
      .mockResolvedValueOnce(new Response('missing', { status: 404 }));
    await expect(createAssetArchive([asset('one'), asset('two')], {}, fetcher)).rejects.toThrow('素材下载失败');
  });

  it.each(['application/json', 'text/html', 'image/jpeg'])('不将 %s 错误或不匹配响应当作 PNG 打包', async mime => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response('invalid', { headers: { 'Content-Type': mime } }));
    await expect(createAssetArchive([asset('one')], {}, fetcher)).rejects.toThrow('素材返回格式异常');
  });

  it('空文件和空选择不生成仅含清单的压缩包', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(new Response(null, { headers: { 'Content-Type': 'image/png' } }));
    await expect(createAssetArchive([], {}, fetcher)).rejects.toThrow('请先选择');
    expect(fetcher).not.toHaveBeenCalled();
    await expect(createAssetArchive([asset('empty')], {}, fetcher)).rejects.toThrow('素材文件为空');
  });
});
