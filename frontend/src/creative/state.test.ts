import { describe, expect, it } from 'vitest';
import { briefEditor, buildBrief, displayDate, EMPTY_SCRIPT, latestScript, parseScriptText, prettyContent, restoreLocalState, scriptHasContent } from './state';
import type { CreativeScript } from './types';

describe('S2 brief and editing boundaries', () => {
  it('formats backend second timestamps and preserves absent dates', () => {
    expect(displayDate(1793001600)).toBe(displayDate(new Date(1793001600000).toISOString()));
    expect(displayDate(undefined)).toBe('时间未提供');
  });
  it('treats duration as total video length and permits 20 or 120 seconds', () => {
    for (const target_duration_s of ['20', '120']) {
      const result = buildBrief({ ...briefEditor('项目', {}), target_duration_s }, {});
      expect(result.error).toBeNull(); expect(result.brief?.target_duration_s).toBe(Number(target_duration_s));
    }
  });
  it.each(['1.5', '-1', '0', 'abc', 'Infinity'])('rejects invalid quantity and duration %s', value => {
    expect(buildBrief({ ...briefEditor('项目', {}), quantity: value }, {}).error).toContain('正整数');
    expect(buildBrief({ ...briefEditor('项目', {}), target_duration_s: value }, {}).error).toContain('正整数');
  });
  it('does not silently rewrite unchanged structured brief values', () => {
    const brief = { selling_points: ['轻便', '白色'], quantity: 3, notes: { source: '用户提供' } };
    expect(buildBrief(briefEditor('项目', brief), brief).brief).toEqual(brief);
    expect(brief.selling_points).toEqual(['轻便', '白色']);
  });
  it('empty names and clearing an existing quantity do not silently save', () => {
    expect(buildBrief(briefEditor(' ', {}), {}).error).toBeTruthy();
    expect(buildBrief({ ...briefEditor('项目', { quantity: 3 }), quantity: '' }, { quantity: 3 }).error).toBeTruthy();
  });
  it('keeps nonstandard shot fields intact without inventing shots', () => {
    const content = { summary: '原文', shots: [{ seq: 2, hint: '保留商品', duration_s: 7 }, '供应商原始条目'], parse_ok: false, extra: true };
    expect(parseScriptText(prettyContent(content)).content).toEqual(content);
    expect(parseScriptText(EMPTY_SCRIPT).content?.shots).toEqual([]);
    expect(scriptHasContent(parseScriptText(EMPTY_SCRIPT).content!)).toBe(false);
  });
  it.each(['null', '[]', '{', '{"summary":3}', '{"shots":"mock"}', '{"summary":null,"shots":[{"hint":"特写"}]}', '{"summary":"保留","shots":null}'])('rejects malformed script edit %s', value => {
    expect(parseScriptText(value).error).toBeTruthy();
  });
  it('finds latest project version rather than taking viewed historical version', () => {
    const scripts = [1, 3, 2].map(version => ({ script_id: `s${version}`, project_id: 'p', version, content: {}, source: 'manual' } as CreativeScript));
    expect(latestScript(scripts)?.version).toBe(3); expect(scripts.map(s => s.version)).toEqual([1, 3, 2]);
  });
  it('restores local uncertain marker and user edits, including unsaved invalid JSON', () => {
    const state = { version: 1, stale: true, uncertain: { label: 'AI 脚本', startedAt: '2026-10-26', beforeVersion: 2 }, edit: { brief: briefEditor('项目', {}), scriptId: 'scr1', scriptText: '{unfinished', baseVersion: 2, instruction: '保留' } };
    expect(restoreLocalState(JSON.stringify(state))).toEqual(state);
  });
  it('ignores corrupt local state rather than losing server data', () => {
    for (const data of [null, '{', '{}', '{"version":2,"stale":false}', '{"version":1,"stale":false,"edit":{"scriptText":1}}']) {
      expect(restoreLocalState(data)?.edit).toBeUndefined();
    }
  });
});
