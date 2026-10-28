import { describe, expect, it } from 'vitest';
import { parseStoredConversation, presentDirectorTurn } from './director-conversation-state';
import type { DirectorTurn } from './director-project-api';

const turn: DirectorTurn = { turn_id: 'turn1', request_id: 'request1', user: '写一个脚本', reply: '脚本已完成', reply_type: 'message', created_at: 1, structured: {} };
const artifact = { type: 'script', target_id: 'script1', target_version: 1, node_id: 'script', persistence: 'saved', confirmed: false };
describe('conversation draft and result presentation', () => {
  it('preserves pending script intent independently from the next draft composer mode', () => {
    const value = { projectId: 'p1', draft: '还想改这里', intent: 'auto', pending: { id: 'r1', message: '开始写脚本', intent: 'script', revision: 8 }, actionPending: false };
    expect(parseStoredConversation(JSON.stringify(value), 'p1')).toEqual(value);
    expect(parseStoredConversation(JSON.stringify(value), 'p2').pending).toBeNull();
  });

  it('migrates the old cache without turning an old pending request into script execution', () => {
    const old = { projectId: 'p1', draft: '草稿', pending: { id: 'r1', message: '你好' }, actionPending: true };
    expect(parseStoredConversation(JSON.stringify(old), 'p1')).toEqual({ ...old, intent: 'auto' });
    expect(parseStoredConversation('unreadable', 'p1').pending).toBeNull();
  });

  it('does not create a saved result or reveal action from ordinary success-sounding prose', () => {
    const view = presentDirectorTurn(turn);
    expect(view.outcome.status).toBe('message');
    expect(view.nodeIds).toEqual([]);
    expect(presentDirectorTurn({ ...turn, reply: '' }).text).not.toContain('保存');
  });

  it('shows the actual failure instead of misleading model prose', () => {
    const view = presentDirectorTurn({ ...turn, structured: { outcome: { status: 'failed', goal: 'script', node_ids: [], error: { code: 'WRITE_FAILED', message: '保存失败' } } } });
    expect(view.text).toContain('保存失败');
    expect(view.text).not.toContain('脚本已完成');
    expect(view.role).toBe('AI 编剧');
    expect(view.nodeIds).toEqual([]);
  });

  it('keeps successful nodes visible when another part of the same turn failed', () => {
    const view = presentDirectorTurn({ ...turn, structured: { artifacts: [artifact], outcome: { status: 'partial', goal: 'script', node_ids: ['script'], error: { code: 'TOOL_ERROR', message: '镜头建议未完成' } } } });
    expect(view.text).toContain('部分内容已保存');
    expect(view.text).toContain('镜头建议未完成');
    expect(view.nodeIds).toEqual(['script']);
  });

  it('explains invalid generated content without exposing internal field names', () => {
    const view=presentDirectorTurn({...turn,structured:{outcome:{status:'failed',goal:'script',node_ids:[],error:{code:'VALIDATION_FAILED',message:'脚本字段不完整或格式有误：summary, shots.0'}}}});
    expect(view.text).toContain('内容不完整');
    expect(view.text).not.toMatch(/summary|shots/);
    expect(view.nodeIds).toEqual([]);
  });

  it('identifies actual script and shot plan work, without role-switching on prose', () => {
    expect(presentDirectorTurn({ ...turn, structured: { artifacts: [artifact] } }).role).toBe('AI 编剧');
    expect(presentDirectorTurn({ ...turn, structured: { artifacts: [{ ...artifact, type: 'shot_plan' }] } }).role).toBe('AI 导演');
    expect(presentDirectorTurn({ ...turn, reply: '我现在是摄影师' }).role).toBe('创作助手');
  });
});
