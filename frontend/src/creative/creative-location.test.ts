import { describe, expect, it, vi } from 'vitest';
import { directorProjectHash, isCreativeRouteChange, parseCreativeLocation, replaceCreativeLocation } from './creative-location';

describe('导演台刷新与项目地址', () => {
  it.each(['prj_ab12', '演示 项目', '项目%25'])('项目地址可准确往返：%s', id => {
    const hash = directorProjectHash(id);
    expect(hash).toBe(`#/director/${encodeURIComponent(id)}`);
    expect(parseCreativeLocation(hash)).toEqual({ view: 'creative', projectId: id });
  });
  it('导演台列表和首页没有隐含的上次项目', () => {
    expect(parseCreativeLocation('#/director')).toEqual({ view: 'director', projectId: null });
    expect(parseCreativeLocation('#/director/')).toEqual({ view: 'director', projectId: null });
    expect(parseCreativeLocation('')).toEqual({ view: 'projects', projectId: null });
    expect(parseCreativeLocation('#')).toEqual({ view: 'projects', projectId: null });
  });
  it('快捷工作台内部锚点只滚动页面，不被当作导航离开工作台', () => {
    expect(isCreativeRouteChange('#creation-settings')).toBe(false);
    expect(isCreativeRouteChange('#creation-results')).toBe(false);
    expect(isCreativeRouteChange('#/director/prj_a')).toBe(true);
    expect(isCreativeRouteChange('')).toBe(true);
    expect(isCreativeRouteChange('#/unknown')).toBe(true);
  });
  it.each([null, 8, '#/unknown/prj_a', '#/director/a/b', '#/director/%', '#/director/%E0%A4%A',
    '#/director/..', '#/director/%2Ftmp', '#/director/%5Cfoo', '#/director/%00bad',
    '#/director/%20', '#/director/id%3Fquery', '#/director/id%23hash', '#/director/' + 'x'.repeat(201)])('畸形地址安全回到首页：%s', hash => {
    expect(parseCreativeLocation(hash)).toEqual({ view: 'projects', projectId: null });
  });
  it('非法项目ID不能作为路径或外部URL写入', () => {
    for (const id of ['', '../p', 'https://example.com', '\\server', '\u0001id', 'id\n', '\uD800']) expect(directorProjectHash(id)).toBeNull();
  });
  it('打开项目替换当前历史项，保留查询参数、路径和已有history state', () => {
    const state = { existing: 'unchanged' }, replaceState = vi.fn();
    const browser = { location: { pathname: '/local-studio/', search: '?preview=1', hash: '' }, history: { state, replaceState } };
    replaceCreativeLocation('prj_abc', false, browser);
    expect(replaceState).toHaveBeenCalledExactlyOnceWith(state, '', '/local-studio/?preview=1#/director/prj_abc');
  });
  it('返回导演台只留下列表路由，跳到其他功能清除项目路由', () => {
    const replaceState = vi.fn(), browser = { location: { pathname: '/', search: '', hash: '#/director/prj_a' }, history: { state: null, replaceState } };
    replaceCreativeLocation(null, true, browser);
    expect(replaceState).toHaveBeenLastCalledWith(null, '', '/#/director');
    replaceCreativeLocation(null, false, browser);
    expect(replaceState).toHaveBeenLastCalledWith(null, '', '/');
    expect(replaceState).toHaveBeenCalledTimes(2);
  });
  it('重复同步不额外改动历史记录', () => {
    const replaceState = vi.fn();
    replaceCreativeLocation('prj_a', false, { location: { pathname: '/', search: '', hash: '#/director/prj_a' }, history: { state: null, replaceState } });
    expect(replaceState).not.toHaveBeenCalled();
  });
});
