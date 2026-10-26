import type { ScriptConfirmation, ScriptWriteResult } from './types';

type ScriptIdentity = Pick<ScriptWriteResult, 'script_id' | 'version'>;
export interface ConfirmScriptDraftOptions<
  TSaved extends ScriptWriteResult = ScriptWriteResult,
  TConfirmed extends ScriptIdentity = ScriptConfirmation,
> {
  savedScript: ScriptIdentity | null;
  dirty: boolean;
  save: () => Promise<TSaved>;
  confirm: (id: string) => Promise<TConfirmed>;
  /** Persist the successful save acknowledgement before trying confirmation. */
  onSaved: (result: TSaved) => void | Promise<void>;
}

function identityOf(value: ScriptIdentity): ScriptIdentity {
  if (typeof value?.script_id !== 'string' || !value.script_id.trim() || !Number.isSafeInteger(value.version) || value.version < 1) {
    throw new Error('脚本保存回执缺少有效的脚本 ID 或版本，请重新读取核对。');
  }
  return { script_id: value.script_id, version: value.version };
}

/** One explicit user action; no retry, rollback, fallback version, or swallowed error. */
export async function confirmScriptDraft<
  TSaved extends ScriptWriteResult = ScriptWriteResult,
  TConfirmed extends ScriptIdentity = ScriptConfirmation,
>(options: ConfirmScriptDraftOptions<TSaved, TConfirmed>): Promise<TConfirmed> {
  let target: ScriptIdentity;
  if (options.dirty || !options.savedScript) {
    const saved = await options.save();
    target = identityOf(saved);
    await options.onSaved(saved);
  } else {
    target = identityOf(options.savedScript);
  }
  const result = await options.confirm(target.script_id);
  if (result?.script_id !== target.script_id || result.version !== target.version) {
    throw new Error('确认回执与刚保存或所选脚本版本不匹配，请重新读取核对；已保存的脚本保持不变。');
  }
  return result;
}
