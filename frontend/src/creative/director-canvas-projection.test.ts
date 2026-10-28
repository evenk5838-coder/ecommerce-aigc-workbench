import { describe, expect, it } from 'vitest';
import { legacyCanvasShots } from './director-canvas-projection';
import type { CanvasNodeRecord } from './canvas-layout-sync';
import type { Shot } from './production-types';

const shot = (shot_id: string): Shot => ({ shot_id, project_id: 'p', revision: 1, seq: 1, duration_s: 5, description: 'one cup' });
const node = (node_id: string, shot_id: string): CanvasNodeRecord => ({ node_id, type: 'image', binding: { shot_id }, position: { x: 0, y: 0 }, config: {}, config_revision: 1, last_execution: null, asset: null });

describe('director canvas projection', () => {
  it('does not create a second legacy image beside an actual conversational image node', () => {
    expect(legacyCanvasShots([shot('s')], [], [node('director-image-1', 's')])).toEqual([]);
  });
  it('does not recreate deleted conversational nodes from their retained shot record', () => {
    expect(legacyCanvasShots([shot('s')], [], [])).toEqual([]);
  });
  it('retains an existing legacy canvas card and real legacy outputs', () => {
    const a = shot('a'), b = { ...shot('b'), storyboards: [{ storyboard_id: 'board', shot_id: 'b', version: 1, status: 'approved', asset_id: 'image' }] };
    expect(legacyCanvasShots([a, b], [], [node('image:a', 'a')])).toEqual([a, b]);
  });
  it('keeps a legacy queued output visible and suppresses a managed counterpart in a mixed project', () => {
    const a = shot('a'), b = shot('b');
    expect(legacyCanvasShots([a, b], [{ shot_id: 'a', status: 'queued', task_id: 'task' }], [node('director-image-b', 'b')])).toEqual([a]);
  });
});
