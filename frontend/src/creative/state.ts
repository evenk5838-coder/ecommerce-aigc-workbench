import type { ProjectBrief, ScriptContent, CreativeScript } from './types';

export const SELECTED_PROJECT_KEY = 'material-studio-creative-selected-v1';
export const PROJECT_EDIT_PREFIX = 'material-studio-creative-edit-v1:';
export interface BriefEditor {
  name: string; subject: string; quantity: string; selling_points: string;
  forbidden_elements: string; target_platform: string; target_duration_s: string;
  style: string; notes: string;
}
export interface ProjectEditSnapshot {
  brief: BriefEditor; scriptId: string | null; scriptText: string;
  baseVersion: number; instruction: string;
}
export interface UncertainOperation { label: string; startedAt: string; beforeVersion: number }
export interface LocalProjectState {
  version: 1; edit?: ProjectEditSnapshot; stale: boolean; uncertain?: UncertainOperation;
}
const briefKeys = ['subject', 'quantity', 'selling_points', 'forbidden_elements', 'target_platform', 'target_duration_s', 'style', 'notes'] as const;
const isRecord = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
export function fieldText(value: unknown): string {
  if (value == null) return '';
  if (typeof value === 'string') return value;
  if (Array.isArray(value) && value.every(item => typeof item === 'string')) return value.join('\n');
  return typeof value === 'number' ? String(value) : JSON.stringify(value);
}
export function briefEditor(name: string, brief: ProjectBrief): BriefEditor {
  return Object.fromEntries([['name', name], ...briefKeys.map(key => [key, fieldText(brief[key])])]) as unknown as BriefEditor;
}
/** Preserve unchanged wire values, including arrays; edits are explicit field replacements. */
export function buildBrief(editor: BriefEditor, original: ProjectBrief): { brief: ProjectBrief | null; error: string | null } {
  if (!editor.name.trim()) return { brief: null, error: '请填写项目名称。' };
  const brief: ProjectBrief = { ...original };
  for (const key of briefKeys) {
    const text = editor[key];
    if (key === 'quantity' || key === 'target_duration_s') {
      if (text.trim() && (!/^\d+$/.test(text.trim()) || !Number.isSafeInteger(Number(text)) || Number(text) <= 0)) {
        return { brief: null, error: key === 'quantity' ? '商品数量需为正整数。' : '目标总片长需为正整数秒。' };
      }
      if (text === fieldText(original[key])) continue;
      if (!text.trim() && key === 'quantity' && original[key] != null) return { brief: null, error: '已有商品数量不能清空，请填写正整数。' };
      if (text.trim()) brief[key] = Number(text);
      else if (original[key] != null) brief[key] = '';
    } else if (text !== fieldText(original[key])) brief[key] = text;
  }
  return { brief, error: null };
}
export function prettyContent(content: ScriptContent): string { return JSON.stringify(content, null, 2); }
export const EMPTY_SCRIPT = prettyContent({ summary: '', shots: [] });
export function parseScriptText(text: string): { content: ScriptContent | null; error: string | null } {
  try {
    const value: unknown = JSON.parse(text);
    if (!isRecord(value)) return { content: null, error: '脚本内容需为 JSON 对象。' };
    if (value.summary !== undefined && typeof value.summary !== 'string') return { content: null, error: '脚本概述需为文字。' };
    if (value.shots !== undefined && !Array.isArray(value.shots)) return { content: null, error: '镜头建议需为列表。' };
    return { content: value as ScriptContent, error: null };
  } catch { return { content: null, error: '脚本 JSON 格式有误，请检查后保存。' }; }
}
export function scriptHasContent(content: ScriptContent): boolean {
  return (typeof content.summary === 'string' && !!content.summary.trim()) || (Array.isArray(content.shots) && content.shots.length > 0);
}
export function latestScript(scripts: CreativeScript[]): CreativeScript | null {
  return scripts.reduce<CreativeScript | null>((latest, script) => !latest || script.version > latest.version ? script : latest, null);
}
export function restoreLocalState(value: string | null): LocalProjectState | null {
  if (!value) return null;
  try {
    const parsed: unknown = JSON.parse(value);
    if (!isRecord(parsed) || parsed.version !== 1 || typeof parsed.stale !== 'boolean') return null;
    const result: LocalProjectState = { version: 1, stale: parsed.stale };
    if (isRecord(parsed.uncertain) && typeof parsed.uncertain.label === 'string' && typeof parsed.uncertain.startedAt === 'string' && Number.isSafeInteger(parsed.uncertain.beforeVersion)) result.uncertain = parsed.uncertain as unknown as UncertainOperation;
    const edit = parsed.edit;
    if (isRecord(edit) && isRecord(edit.brief) && ['name', ...briefKeys].every(key => typeof edit.brief === 'object' && typeof (edit.brief as Record<string, unknown>)[key] === 'string') && (edit.scriptId === null || typeof edit.scriptId === 'string') && typeof edit.scriptText === 'string' && Number.isSafeInteger(edit.baseVersion) && Number(edit.baseVersion) >= 0 && typeof edit.instruction === 'string') result.edit = edit as unknown as ProjectEditSnapshot;
    return result;
  } catch { return null; }
}
export function readLocalProject(projectId: string): LocalProjectState | null {
  try { return restoreLocalState(localStorage.getItem(PROJECT_EDIT_PREFIX + projectId)); } catch { return null; }
}
export function storeLocalProject(projectId: string, state: LocalProjectState): boolean {
  try { localStorage.setItem(PROJECT_EDIT_PREFIX + projectId, JSON.stringify(state)); return true; } catch { return false; }
}
export function displayDate(value?: string | number | null): string {
  if (!value) return '时间未提供';
  const date = new Date(typeof value === 'number' ? value * 1000 : value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}
