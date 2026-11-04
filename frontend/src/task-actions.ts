import type { Task } from './types';

export type TaskRetryKind = 'blocked' | 'resume' | 'new';

/** A recoverable remote job can change state before the retry endpoint handles it. */
export function taskRetryKind(task: Task): TaskRetryKind {
  if (!['failed', 'interrupted', 'canceled'].includes(task.status) || task.error_code === 'CREATE_RESULT_UNKNOWN') return 'blocked';
  const hub = task.provider === 'aihubmix' || task.model_id.startsWith('aihubmix:');
  return hub && !!task.remote_task_id && !['failed', 'canceled', 'cancelled'].includes(task.remote_status ?? '') ? 'resume' : 'new';
}

export const taskRetryLabel = (task: Task): string => taskRetryKind(task) === 'resume' ? '继续处理' : '重新生成';

export const taskRetryConfirmation = (task: Task): string => taskRetryKind(task) === 'resume'
  ? '优先恢复已有结果；若原任务已失败或取消，重新生成可能产生费用，是否继续？'
  : '重新生成可能产生新的生成费用，是否继续？';
