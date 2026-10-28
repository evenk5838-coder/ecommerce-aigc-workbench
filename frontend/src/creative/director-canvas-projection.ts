import type { CanvasNodeRecord } from './canvas-layout-sync';
import type { ShotStatus, Shot } from './production-types';

/** New conversation shots already have independent nodes. Never manufacture
 * legacy cards when those nodes are created or later removed by the user. */
export function legacyCanvasShots(shots: readonly Shot[], statuses: readonly ShotStatus[], nodes: readonly CanvasNodeRecord[]): Shot[] {
  return shots.filter(shot => {
    if (nodes.some(node => node.binding.shot_id === shot.shot_id && ['image', 'video'].includes(node.type)
      && node.node_id !== `image:${shot.shot_id}` && node.node_id !== `video:${shot.shot_id}`)) return false;
    return nodes.some(node => node.node_id === `image:${shot.shot_id}` || node.node_id === `video:${shot.shot_id}`)
      || !!shot.storyboards?.length || !!shot.takes?.length || statuses.some(status => status.shot_id === shot.shot_id
        && (!!status.storyboards?.length || !!status.takes?.length || !!status.asset_id || !!status.task_id));
  });
}
