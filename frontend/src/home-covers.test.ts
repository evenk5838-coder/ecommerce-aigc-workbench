import { describe, expect, it } from 'vitest';
import type { Asset, Task } from './types';
import { homeCovers } from './home-covers';

const asset = (id: string, patch: Partial<Asset> = {}): Asset => ({ id, kind: 'image', mime: 'image/png', size: 10,
  width: 100, height: 100, source: 'cloud_generated', created_at: 1, meta_json: '{}', ...patch });
const task = (id: string, output: string, patch: Partial<Task> = {}): Task => ({ id, kind: 'image', model_id: 'image',
  status: 'succeeded', params_json: '{}', progress: 1, stage: 'done', created_at: 1, finished_at: 2,
  output_asset_id: output, caller_id: 'workbench', ...patch });

describe('homepage workspace covers', () => {
  it('keeps each workspace separate and accepts assistant output', () => {
    const images = [asset('d'), asset('q')];
    expect(homeCovers(images, [task('a', 'd', { caller_id: 'director', external_request_id: 'project' }),
      task('b', 'q', { caller_id: 'assistant' })], ['project'])).toEqual({ director: images[0], quick: images[1] });
  });
  it('sorts by completion even when a new result reuses an old deduplicated asset', () => {
    const images = [asset('old', { created_at: 1 }), asset('new', { created_at: 100 })];
    expect(homeCovers(images, [task('a', 'old', { created_at: 2, finished_at: 200 }),
      task('b', 'new', { created_at: 100, finished_at: 150 })], []).quick?.id).toBe('old');
  });
  it('ignores unfinished tasks, uploads, missing files, videos and smoke models', () => {
    const images = [asset('good'), asset('upload', { source: 'upload' }), asset('video', { kind: 'video' })];
    const invalid = [task('a', 'good', { status: 'failed' }), task('b', 'good', { status: 'running' }),
      task('c', 'upload'), task('d', 'missing'), task('e', 'video'), task('f', 'good', { kind: 'video' }),
      task('g', 'good', { model_id: 'fake-image' })];
    expect(homeCovers(images, invalid, [])).toEqual({});
  });
  it('does not guess legacy local tasks or unknown validation callers into the workbench', () => {
    expect(homeCovers([asset('a')], ['local', 'acceptance', null].map((caller_id, i) =>
      task(String(i), 'a', { caller_id })), [])).toEqual({});
  });
  it('skips deleted or unavailable director projects and falls back to an existing project', () => {
    const images = [asset('deleted'), asset('active')];
    expect(homeCovers(images, [task('a', 'deleted', { caller_id: 'canvas', external_request_id: 'gone', finished_at: 9 }),
      task('b', 'active', { caller_id: 'canvas', external_request_id: 'kept' })], ['kept']).director?.id).toBe('active');
  });
});
