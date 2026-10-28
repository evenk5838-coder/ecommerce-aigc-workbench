import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { afterEach, describe, expect, it, vi } from 'vitest';
import StructureTemplates, { buildStructureTemplateDraft, isStructureTemplateDraftDirty } from './StructureTemplates';
import type { StructureDraftRow } from './StructureTemplates';
import type { StructureShot } from './production-types';

afterEach(() => { vi.unstubAllGlobals(); });
const row = (overrides: Partial<StructureDraftRow> = {}): StructureDraftRow => ({ role: ' 开场特写 ', duration_s: '4', motion: '缓慢推近', rhythm: '舒缓', ...overrides });

describe('structure template draft boundary', () => {
  it('exports only the agreed structural fields, preserving explicit values without modifying source rows', () => {
    const rows = Object.freeze([
      Object.freeze({ ...row(), description: '不可自动复制的商品事实', asset_id: 'asset_1', prompt: '不可复制' }),
      Object.freeze(row({ role: '场景展开', duration_s: '6', motion: '', rhythm: '' })),
    ]);
    const result = buildStructureTemplateDraft('  开场与展开  ', [...rows]);
    expect(result).toEqual({ input: { name: '开场与展开', snapshot: [
      { seq: 1, role: '开场特写', duration_s: 4, motion: '缓慢推近', rhythm: '舒缓' },
      { seq: 2, role: '场景展开', duration_s: 6, motion: '', rhythm: '' },
    ] }, error: null });
    expect(rows[0].role).toBe(' 开场特写 ');
    expect(JSON.stringify(result)).not.toContain('不可自动复制');
    expect(result.input?.snapshot[0]).not.toHaveProperty('asset_id');
  });

  it.each(['', '0', '-2', '1.5', '1e2', 'Infinity', 'NaN', '9007199254740992'])('rejects duration %s without rounding or providing a default', duration_s => {
    const result = buildStructureTemplateDraft('结构', [row({ duration_s })]);
    expect(result.input).toBeNull();
    expect(result.error).toContain('正整数');
  });

  it('requires a name, a row and a role', () => {
    expect(buildStructureTemplateDraft(' ', [row()]).input).toBeNull();
    expect(buildStructureTemplateDraft('结构', []).input).toBeNull();
    expect(buildStructureTemplateDraft('结构', [row({ role: ' ' })]).input).toBeNull();
  });

  it('renumbers only explicit remaining rows after deletion', () => {
    const result = buildStructureTemplateDraft('结构', [row({ role: '开场' }), row({ role: '收尾' })]);
    expect(result.input?.snapshot.map(shot => [shot.seq, shot.role])).toEqual([[1, '开场'], [2, '收尾']]);
  });
});

describe('structure template display and local safeguards', () => {
  it('starts with an empty manual editor and requires fact-free confirmation without invented template results', () => {
    const onApplied = vi.fn(), onSaved = vi.fn();
    const html = renderToStaticMarkup(createElement(StructureTemplates, { projectId: 'p', onApplied, onSaved }));
    expect(html).toContain('还没有结构模板');
    expect(html).toContain('请填写结构模板名称');
    expect(html).toContain('不含商品名称、数量、卖点');
    expect(html).not.toContain('套用此结构');
    expect(html).not.toContain('已保存结构模板');
    expect(onApplied).not.toHaveBeenCalled();
    expect(onSaved).not.toHaveBeenCalled();
  });

  it('does not copy incoming structure until explicitly requested, and refuses snapshots carrying product or asset fields', () => {
    const source = [{ seq: 1, role: '来自父组件的实际角色', duration_s: 4, motion: '', rhythm: '', asset_id: 'asset_secret' }];
    const snapshot: StructureShot[] = source;
    const html = renderToStaticMarkup(createElement(StructureTemplates, { projectId: 'p', snapshot }));
    expect(html).toContain('当前内容不能直接保存为结构模板');
    expect(html).not.toContain('来自父组件的实际角色');
    expect(html).not.toContain('asset_secret');
  });

  it('restores this project draft and keeps unknown mutations protected without clearing existing storage', () => {
    const getItem = vi.fn().mockReturnValue(JSON.stringify({ name: '本地结构草稿', rows: [row()], pending: { kind: 'apply', label: '套用已选结构 v2', acknowledged: 'truthy-is-not-a-receipt' } }));
    const setItem = vi.fn(), clear = vi.fn(), removeItem = vi.fn();
    vi.stubGlobal('localStorage', { getItem, setItem, clear, removeItem });
    const html = renderToStaticMarkup(createElement(StructureTemplates, { projectId: 'project/2' }));
    expect(getItem).toHaveBeenCalledWith('material-studio-structure-template-draft-v1:project%2F2');
    expect(html).toContain('本地结构草稿');
    expect(html).toContain('结果暂未确认');
    expect(html).not.toContain('已完成，请核对最新内容');
    expect(html).toContain('不会自动重试');
    expect(setItem).not.toHaveBeenCalled();
    expect(clear).not.toHaveBeenCalled();
    expect(removeItem).not.toHaveBeenCalled();
  });
});

describe('structure template saved draft boundary', () => {
  const content = { name: '双镜节奏', rows: [row(), row({ role: '收尾' })] };

  it('protects unsaved and legacy drafts, but leaves a fresh empty editor clean', () => {
    expect(isStructureTemplateDraftDirty({ name: '', rows: [row({ role: '', duration_s: '', motion: '', rhythm: '' })] })).toBe(false);
    expect(isStructureTemplateDraftDirty(content)).toBe(true);
  });

  it('recognizes the saved draft after serialization and protects subsequent edits', () => {
    const restored = JSON.parse(JSON.stringify(content));
    expect(isStructureTemplateDraftDirty(restored, content)).toBe(false);
    expect(isStructureTemplateDraftDirty({ ...restored, name: '新的结构' }, content)).toBe(true);
    expect(isStructureTemplateDraftDirty({ ...restored, rows: [row({ duration_s: '8' }), row({ role: '收尾' })] }, content)).toBe(true);
    expect(isStructureTemplateDraftDirty({ ...restored, rows: [] }, content)).toBe(true);
    expect(isStructureTemplateDraftDirty(content, restored)).toBe(false);
  });

  it('keeps pending or unknown writes protected even when the saved content matches', () => {
    expect(isStructureTemplateDraftDirty(content, content, true)).toBe(true);
    expect(isStructureTemplateDraftDirty(content, content, false)).toBe(false);
  });
});
