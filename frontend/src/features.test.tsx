import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import Home from './Home';
import { availableStudioView } from './features';
import { parseCreativeLocation } from './creative/creative-location';
import type { Asset } from './types';

describe('暂缓排版编辑，保留核心创作', () => {
  it('不可进入暂缓页面，旧画布路径不会打开排版编辑', () => {
    expect(availableStudioView('canvas')).toBe('home');
    expect(parseCreativeLocation('#/canvas').view).toBe('projects');
    expect(availableStudioView('creative')).toBe('creative');
  });
  it('快捷工作台隐藏排版入口，同时保留预览、参考生图和生视频', () => {
    const a: Asset = { id: 'image', kind: 'image', mime: 'image/png', size: 100, width: 64, height: 48,
      source: 'cloud_generated', created_at: 1, meta_json: '{}' };
    const html = renderToStaticMarkup(<Home assets={[a]} activeCount={0} connected loading={false} kind="image"
      onPreview={() => undefined} onReference={() => undefined} onLibrary={() => undefined} onTasks={() => undefined}>创作</Home>);
    expect(html).not.toContain('排版编辑');
    for (const text of ['预览', '参考生图', '生视频']) expect(html).toContain(text);
  });
});
