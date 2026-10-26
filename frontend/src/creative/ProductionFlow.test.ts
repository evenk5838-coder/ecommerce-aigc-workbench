import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import ProductionFlow, { buildProductionPreview } from './ProductionFlow';
import type { Task } from '../types';
import type { Film, ProductionStatus, Shot, StoryboardCreation } from './production-types';
import type { ProductionFlowProps } from './ProductionFlow';
import type { ProductionEditorState } from './production-state';

const editor: ProductionEditorState = {
  version: 1,
  rows: [
    { key: 'draft-one', shot_id: 'shot-one', revision: 2, description: '瓶身整体', motion: '', duration: '5', prompt: '保留第一镜输入', input_asset_id: '' },
    { key: 'draft-two', shot_id: 'shot-two', revision: 3, description: '瓶盖细节', motion: '', duration: '6', prompt: '保留第二镜输入', input_asset_id: '' },
  ],
  videoModelId: 'video', ratio: '9:16', resolution: '720p', imageModelId: 'image', imageSize: '2048x2048', mode: 'clips', selectedIds: ['shot-one', 'shot-two'],
};
const props: ProductionFlowProps = {
  project: { project_id: 'project', name: '分镜项目', brief: {}, status: 'script_confirmed', script_count: 1, latest_script: null },
  script: { script_id: 'script', project_id: 'project', version: 1, content: { summary: '商品展示' }, source: 'manual', confirmed_at: 1 },
  panel: 'storyboard',
};
const writeStorage = vi.fn();
function render(extra: Partial<ProductionFlowProps> = {}) {
  return renderToStaticMarkup(createElement(ProductionFlow, { ...props, ...extra }));
}
function shotTag(html: string, id: string) {
  return html.match(new RegExp(`<article\\b[^>]*id="pf-shot-${id}"[^>]*>`))?.[0] ?? '';
}
function sectionTag(html: string, title: string) {
  return html.match(new RegExp(`<section\\b[^>]*aria-labelledby="${title}"[^>]*>`))?.[0] ?? '';
}

beforeEach(() => {
  vi.stubGlobal('document', { visibilityState: 'visible' });
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => key === 'material-studio-production-edit-v1:project' ? JSON.stringify(editor) : null,
    setItem: writeStorage,
  });
});
afterEach(() => { vi.unstubAllGlobals(); vi.clearAllMocks(); });

describe('production focused shot display', () => {
  it('keeps every draft mounted while displaying only the selected shot', () => {
    const html = render({ focusShotId: 'shot-two' });
    expect(shotTag(html, 'shot-one')).toContain('hidden=""');
    expect(shotTag(html, 'shot-two')).not.toContain('hidden=');
    expect(html).toContain('保留第一镜输入');
    expect(html).toContain('保留第二镜输入');
    expect(html).toContain('镜头 2');
    expect(writeStorage).not.toHaveBeenCalled();
  });

  it('accepts a local draft key, and restores the full plan when focus is absent', () => {
    expect(shotTag(render({ focusShotId: 'draft-one' }), 'shot-one')).not.toContain('hidden=');
    const html = render();
    expect(shotTag(html, 'shot-one')).not.toContain('hidden=');
    expect(shotTag(html, 'shot-two')).not.toContain('hidden=');
  });

  it('does not fall back to a different shot when the selected one no longer exists', () => {
    const html = render({ focusShotId: 'removed' });
    expect(html).toContain('当前镜头暂未找到');
    expect(shotTag(html, 'shot-one')).toContain('hidden=""');
    expect(shotTag(html, 'shot-two')).toContain('hidden=""');
  });

  it.each(['generation', 'delivery'] as const)('does not apply shot focus to the %s panel', panel => {
    const html = render({ panel, focusShotId: 'shot-two' });
    expect(shotTag(html, 'shot-one')).not.toContain('hidden=');
    expect(shotTag(html, 'shot-two')).not.toContain('hidden=');
    expect(sectionTag(html, panel === 'generation' ? 'pf-scope-title' : 'pf-film-title')).not.toContain('hidden=');
    expect(sectionTag(html, 'pf-plan-title')).toContain('hidden=""');
  });

  it('shows only the selected video shot editor while keeping model settings explicitly shared', () => {
    const html = render({ panel: 'generation', focusShotId: 'shot-two' });
    const editorHtml = html.match(/<section class="pf-focused-video"[\s\S]*?<\/section>/)?.[0] ?? '';
    expect(editorHtml).toContain('瓶盖细节');
    expect(editorHtml).not.toContain('瓶身整体');
    expect(editorHtml).toContain('镜头时长（秒）');
    expect(editorHtml).toContain('保存镜头修改');
    expect(editorHtml).not.toContain('model_id');
    expect(html).toContain('本组所有镜头共用以上视频设置');
    expect(writeStorage).not.toHaveBeenCalled();
  });

  it('labels focused image settings as node-specific and the full plan settings as defaults', () => {
    expect(render({ focusShotId: 'shot-one' })).toContain('此分镜的图片模型');
    expect(render({ focusShotId: 'shot-one' })).toContain('只调整当前分镜');
    expect(render()).toContain('默认分镜模型');
    expect(render()).toContain('未单独设置的分镜使用以上默认值');
  });

  it('uses capability-driven output settings without inventing sizes when models have not loaded', () => {
    const html = render({ focusShotId: 'shot-one' });
    expect(html).toContain('storyboard-output-settings');
    expect(html).toContain('模型信息尚未加载');
    expect(html).not.toContain('2048x2048</option>');
    expect(html).toContain('本组所有镜头共用以上视频设置');
  });
});


describe('read-only production preview snapshots', () => {
  const shot: Shot = { shot_id: 'shot', project_id: 'project', revision: 1, seq: 1, duration_s: 5, description: '商品整体' };
  const receipt: StoryboardCreation = { shot_id: 'shot', storyboard_id: 'board', version: 1, task_id: 'image-task', estimated_cost_cny: 0.12 };
  const task: Task = { id: 'image-task', kind: 'image', model_id: 'image', status: 'succeeded', output_asset_id: 'real-asset', params_json: '{}', progress: 1, stage: 'done', created_at: 1 };
  const status: ProductionStatus = { project_id: 'project', mode: null, shots: [{ shot_id: 'shot', status: 'not_started' }] };

  it('exposes a completed image from its real creation receipt and task read without waiting for the shot list', () => {
    const preview = buildProductionPreview([shot], status, [receipt], [task], []);
    expect(preview.shots[0].storyboards).toEqual([expect.objectContaining({ storyboard_id: 'board', task_id: 'image-task', asset_id: 'real-asset', status: 'succeeded' })]);
    expect(preview.status?.shots[0].storyboards).toEqual(preview.shots[0].storyboards);
    expect(preview.shots[0].storyboards?.[0].approved).not.toBe(true);
    expect(shot.storyboards).toBeUndefined();
    expect(status.shots[0].storyboards).toBeUndefined();
  });

  it('does not treat a receipt, failed task or unrelated task as a generated image', () => {
    for (const tasks of [[], [{ ...task, status: 'failed' }], [{ ...task, id: 'unrelated' }], [{ ...task, kind: 'video' as const }]]) {
      const preview = buildProductionPreview([shot], status, [receipt], tasks, []);
      expect(preview.shots[0].storyboards?.[0].asset_id).toBeUndefined();
      expect(preview.shots[0].storyboards?.[0].status).not.toBe('approved');
    }
  });

  it('merges duplicate records without dropping a task asset or overriding a real rejection', () => {
    const existing = { ...shot, storyboards: [{ storyboard_id: 'board', shot_id: 'shot', version: 1, task_id: 'image-task', status: 'draft' }] };
    const current = { ...status, shots: [{ shot_id: 'shot', status: 'not_started', storyboards: [{ storyboard_id: 'board', shot_id: 'shot', version: 1, status: 'rejected', reject_reason: '标签不正确' }] }] };
    const preview = buildProductionPreview([existing], current, [receipt], [task], []);
    expect(preview.shots[0].storyboards).toHaveLength(1);
    expect(preview.shots[0].storyboards?.[0]).toMatchObject({ status: 'rejected', asset_id: 'real-asset', reject_reason: '标签不正确' });
  });

  it('uses the latest film read with real media instead of downgrading to an older composing summary', () => {
    const old: Film = { film_id: 'film', project_id: 'project', version: 1, status: 'composing' };
    const ready: Film = { ...old, status: 'ready', asset_id: 'real-video', actual_duration_ms: 10042 };
    const preview = buildProductionPreview([shot], { ...status, film: old }, [], [], [ready]);
    expect(preview.status?.film).toEqual(ready);
    expect(preview.status?.films).toEqual([ready]);
    expect(preview.films).toEqual([ready]);
    expect(old.status).toBe('composing');
  });

  it('keeps actual film history available when project status is unavailable without inventing status', () => {
    const film: Film = { film_id: 'film', version: 2, status: 'pending' };
    const preview = buildProductionPreview([shot], null, [], [], [film]);
    expect(preview.status).toBeNull();
    expect(preview.films).toEqual([film]);
    expect(preview.films[0].asset_id).toBeUndefined();
  });
});
