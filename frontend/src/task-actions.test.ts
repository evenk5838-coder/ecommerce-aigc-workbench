import { describe, expect, it } from 'vitest';
import type { Task } from './types';
import { taskRetryConfirmation, taskRetryKind, taskRetryLabel } from './task-actions';

const base: Task = { id:'task', kind:'video', model_id:'aihubmix:fast', provider:'aihubmix', status:'failed',
  remote_task_id:'remote', remote_status:'succeeded', params_json:'{}', progress:0, stage:'failed', created_at:0 };

describe('task recovery copy and permission', () => {
  it('offers continuation for an existing successful or running remote task, while disclosing a possible new charge', () => {
    for (const remote_status of ['succeeded', 'running', 'created']) {
      const task = {...base, remote_status};
      expect(taskRetryKind(task)).toBe('resume');
      expect(taskRetryLabel(task)).toBe('继续处理');
      expect(taskRetryConfirmation(task)).toContain('重新生成可能产生费用');
    }
  });
  it('treats remote terminal failure and missing remote ID as a new billed generation', () => {
    for (const task of [{...base, remote_task_id:undefined}, ...['failed','canceled','cancelled'].map(remote_status=>({...base,remote_status}))]) {
      expect(taskRetryKind(task)).toBe('new');
      expect(taskRetryLabel(task)).toBe('重新生成');
      expect(taskRetryConfirmation(task)).toContain('新的生成费用');
    }
  });
  it('keeps Ark retry semantics and recognizes historical AiHubMix IDs without a provider field', () => {
    expect(taskRetryKind({...base,model_id:'doubao-video',provider:'ark'})).toBe('new');
    expect(taskRetryKind({...base,provider:undefined})).toBe('resume');
  });
  it('never offers retry for ambiguous creation, ongoing work or completed tasks', () => {
    expect(taskRetryKind({...base,error_code:'CREATE_RESULT_UNKNOWN'})).toBe('blocked');
    for (const status of ['succeeded','queued','running','loading']) expect(taskRetryKind({...base,status})).toBe('blocked');
  });
});
