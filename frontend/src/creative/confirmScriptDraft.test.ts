import { describe, expect, it, vi } from 'vitest';
import { confirmScriptDraft } from './confirmScriptDraft';
import type { ScriptConfirmation, ScriptWriteResult } from './types';

const saved = (): ScriptWriteResult => ({ script_id: 'script-v2', version: 2, content: { summary: '商品展示', shots: [] }, source: 'manual' });
const confirmation = (): ScriptConfirmation => ({ script_id: 'script-v2', version: 2, confirmed_at: 123, unlocked: ['storyboard'] });
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}

describe('保存并确认同一个脚本版本', () => {
  it('有编辑时保存新版本，立即交付保存回执，再确认新 ID', async () => {
    const events: string[] = [], receipt = saved(), accepted = confirmation();
    const result = await confirmScriptDraft({
      savedScript: { script_id: 'script-v1', version: 1 }, dirty: true,
      save: vi.fn(async () => { events.push('save'); return receipt; }),
      onSaved: vi.fn(value => { expect(value).toBe(receipt); events.push('onSaved'); }),
      confirm: vi.fn(async id => { expect(id).toBe('script-v2'); events.push('confirm'); return accepted; }),
    });
    expect(events).toEqual(['save', 'onSaved', 'confirm']);
    expect(result).toBe(accepted);
  });

  it('干净但未保存的脚本仍先保存，不确认不存在的版本', async () => {
    const save = vi.fn(async () => saved()), onSaved = vi.fn(), confirm = vi.fn(async () => confirmation());
    await confirmScriptDraft({ savedScript: null, dirty: false, save, onSaved, confirm });
    expect(save).toHaveBeenCalledOnce();
    expect(onSaved).toHaveBeenCalledOnce();
    expect(confirm).toHaveBeenCalledExactlyOnceWith('script-v2');
  });

  it('干净且已保存时仅确认所选版本，不制造新版本', async () => {
    const save = vi.fn(async () => saved()), onSaved = vi.fn(), confirm = vi.fn(async () => confirmation());
    await confirmScriptDraft({ savedScript: saved(), dirty: false, save, onSaved, confirm });
    expect(save).not.toHaveBeenCalled();
    expect(onSaved).not.toHaveBeenCalled();
    expect(confirm).toHaveBeenCalledExactlyOnceWith('script-v2');
  });

  it('保存失败原样抛出，不确认、不重试、不声称保存成功', async () => {
    const error = new Error('version conflict'), save = vi.fn(async () => { throw error; }), onSaved = vi.fn(), confirm = vi.fn(async () => confirmation());
    await expect(confirmScriptDraft({ savedScript: null, dirty: true, save, onSaved, confirm })).rejects.toBe(error);
    expect(save).toHaveBeenCalledOnce();
    expect(onSaved).not.toHaveBeenCalled();
    expect(confirm).not.toHaveBeenCalled();
  });

  it('确认失败保留已保存回执，不回滚或重发保存和确认', async () => {
    const receipt = saved(), error = new Error('confirmation unavailable');
    let persisted: ScriptWriteResult | null = null;
    const save = vi.fn(async () => receipt), onSaved = vi.fn((value: ScriptWriteResult) => { persisted = value; });
    const confirm = vi.fn(async () => { throw error; });
    await expect(confirmScriptDraft({ savedScript: null, dirty: true, save, onSaved, confirm })).rejects.toBe(error);
    expect(persisted).toBe(receipt);
    expect(save).toHaveBeenCalledOnce();
    expect(onSaved).toHaveBeenCalledOnce();
    expect(confirm).toHaveBeenCalledOnce();
  });

  it.each([{ script_id: 'other-id', version: 2 }, { script_id: 'script-v2', version: 3 }])('拒绝错配的确认回执 %j，保存结果仍然保留', async wrong => {
    const onSaved = vi.fn();
    await expect(confirmScriptDraft({ savedScript: null, dirty: true, save: async () => saved(), onSaved, confirm: async () => ({ ...confirmation(), ...wrong }) })).rejects.toThrow('不匹配');
    expect(onSaved).toHaveBeenCalledOnce();
  });

  it('等待保存回执处理完成后才调用确认，避免异步持久化竞态', async () => {
    const gate = deferred<void>(), reached = deferred<void>(), confirm = vi.fn(async () => confirmation());
    const result = confirmScriptDraft({ savedScript: null, dirty: true, save: async () => saved(), confirm,
      onSaved: async () => { reached.resolve(); await gate.promise; } });
    await reached.promise;
    expect(confirm).not.toHaveBeenCalled();
    gate.resolve();
    await expect(result).resolves.toEqual(confirmation());
  });

  it('保存回调不能改写本次绑定的 ID 和版本', async () => {
    const receipt = saved(), confirm = vi.fn(async () => confirmation());
    await confirmScriptDraft({ savedScript: null, dirty: true, save: async () => receipt, confirm,
      onSaved: value => { value.script_id = 'mutated'; value.version = 99; } });
    expect(confirm).toHaveBeenCalledExactlyOnceWith('script-v2');
  });

  it('确认等待期间外部对象变化不改变已选中的版本快照', async () => {
    const selected = { script_id: 'script-v2', version: 2 }, gate = deferred<ScriptConfirmation>();
    const result = confirmScriptDraft({ savedScript: selected, dirty: false, save: async () => saved(), onSaved: vi.fn(), confirm: () => gate.promise });
    selected.script_id = 'another'; selected.version = 10;
    gate.resolve(confirmation());
    await expect(result).resolves.toEqual(confirmation());
  });

  it('回执处理自身失败也不被吞掉，不继续执行确认', async () => {
    const error = new Error('cannot persist acknowledgement'), confirm = vi.fn(async () => confirmation());
    await expect(confirmScriptDraft({ savedScript: null, dirty: true, save: async () => saved(), confirm,
      onSaved: () => { throw error; } })).rejects.toBe(error);
    expect(confirm).not.toHaveBeenCalled();
  });
});
