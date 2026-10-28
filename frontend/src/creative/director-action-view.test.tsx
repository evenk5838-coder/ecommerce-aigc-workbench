import { describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { Children, isValidElement, type ReactNode } from 'react';
import { ApiError } from '../api';
import type { WorkflowAction, WorkflowActionPending, WorkflowExecution, WorkflowPendingRequest, WorkflowQuote, WorkflowReceipt, WorkflowReply } from './director-workflow-api';
import { actionCanConfirm, actionSubmissionState, actionWithinPendingRequest, availableExecution, estimatedCostLabel, executionLabel, finalComposition, mergeWorkflowActions, quoteSpecs, retryMissingWorkflowConversation } from './director-action-view';
import WorkflowActionCard from './WorkflowActionCard';

const quote = (id = 'blue'): WorkflowQuote => ({ quote_id: `q-${id}`, project_id: 'project', node_id: id, config_revision: 1, estimated_cost_cny: 0.001, expires_at: 9e9,
  input: { kind: 'video', model_id: 'video-model', params: { generation_mode: 'image_to_video', prompt: id === 'blue' ? '蓝色开场' : '红色结尾', duration: 5, ratio: '16:9', resolution: '720p' }, input_asset_id: `image-${id}` } });
const film = (): WorkflowAction => ({ action_id: 'film-action', kind: 'generation', project_id: 'project', request_id: 'conversation', status: 'proposed', action_status: 'proposed',
  requires_user_action: true, expires_at: 9e9, expired: false, submit_client_request_id: null, output_mode: 'film', auto_film: true, media_status: 'proposed',
  node_ids: ['blue', 'red'], quotes: [quote(), quote('red')], tasks: [], executions: [], composition: null, estimated_cost_cny: 0.002,
  scope: { generate: true, compose: true, ordered_node_ids: ['blue', 'red'], no_trim: true, composition_cost_cny: 0 } });
const pending: WorkflowActionPending = { action_id: 'film-action', kind: 'generation', client_request_id: 'confirmed-once', user_action: 'confirm_generate' };
const execution = (): WorkflowExecution => ({ execution_id: 'exec', project_id: 'project', node_id: 'blue', task_id: 'task', quote_id: 'q-blue', status: 'succeeded', active: false,
  asset_available: true, result_status: 'available', asset_id: 'output', asset: { id: 'output', kind: 'video', duration_ms: 600 }, progress: 100, stage: null, error: null, asset_error: null });
const render = (action: WorkflowAction, disabled = false, nodeTitles?: Readonly<Record<string, string>>) => renderToStaticMarkup(<WorkflowActionCard action={action} disabled={disabled} nodeTitles={nodeTitles} onConfirm={() => { throw new Error('render must not submit'); }} onReveal={() => {}} onSuggest={() => { throw new Error('render must not call LLM'); }}/>);
function findActionButton(children: ReactNode, label: string): (() => void) | undefined {
  for (const child of Children.toArray(children)) {
    if (!isValidElement<{ children?: ReactNode; onClick?: () => void }>(child)) continue;
    if (child.type === 'button' && Children.toArray(child.props.children).includes(label)) return child.props.onClick;
    const nested = findActionButton(child.props.children, label); if (nested) return nested;
  }
}
function clickRenewal(action: WorkflowAction) {
  const onSuggest = vi.fn(), tree = WorkflowActionCard({ action, disabled: false, onSuggest, onConfirm: () => { throw new Error('renewal only prepares a draft'); }, onReveal: () => {} });
  const click = findActionButton(tree, '重新准备方案'); expect(click).toBeTypeOf('function'); click!();
  expect(onSuggest).toHaveBeenCalledOnce(); return onSuggest.mock.calls[0];
}

describe('durable conversation action presentation', () => {
  it('binds the completed image group when the actual follow-up button is clicked', () => {
    const onSuggest = vi.fn(), action = { ...film(), output_mode: 'clips' as const, status: 'completed',
      quotes: film().quotes!.map(item => ({ ...item, input: { ...item.input, kind: 'image' as const } })) };
    const tree = WorkflowActionCard({ action, disabled: false, onSuggest, onConfirm: () => {}, onReveal: () => {} });
    const click = findActionButton(tree, '制作完整成片'); expect(click).toBeTypeOf('function'); click!();
    expect(onSuggest).toHaveBeenCalledWith('请使用刚才完成的分镜图片，按镜头顺序准备视频生成和完整成片方案。', { nodeIds: ['blue', 'red'], label: '本组分镜图片' });
  });
  it('renews an expired film quote with its complete film contract and original ordered node group', () => {
    const expiredFilm = { ...film(), node_ids: ['red', 'blue'], expires_at: 1, expired: true, requires_user_action: false };
    const [text, scope] = clickRenewal(expiredFilm);
    expect(scope).toEqual({ nodeIds: ['red', 'blue'], label: '本组视频' });
    expect(text).toContain('生成视频并合成完整成片');
    expect(text).toContain('按原顺序（镜头 1 → 镜头 2）');
    expect(text).toContain('保留每个片段的完整时长');
    expect(render(expiredFilm)).toContain('aria-label="分镜视频与成片"');
  });
  it('renews a composition using source order, even if it differs from the action node array', () => {
    const [text, scope] = clickRenewal({ ...film(), kind: 'composition', title: '旅行成片', node_ids: ['blue', 'red'], expires_at: 1,
      expired: true, requires_user_action: false, sources: [{ asset_id: 'red-file', node_id: 'red', metadata: { duration_ms: 800 } }, { asset_id: 'blue-file', node_id: 'blue', metadata: { duration_ms: 1200 } }] });
    expect(scope).toEqual({ nodeIds: ['red', 'blue'], label: '本组视频' });
    expect(text).toContain('「旅行成片」合成卡内的 2 个视频');
    expect(text).toContain('第 1 个视频（0.8 秒） → 第 2 个视频（1.2 秒）');
    expect(text).toContain('保留每个片段的完整时长');
    expect(text).not.toContain('生成视频');
  });
  it('identifies the whole original composition card without binding a partial selection when a source has no node', () => {
    const [text, scope] = clickRenewal({ ...film(), kind: 'composition', title: '导入素材成片', expires_at: 1, expired: true, requires_user_action: false,
      sources: [{ asset_id: 'asset-only', metadata: { duration_ms: 1500 } }, { asset_id: 'blue-file', node_id: 'blue', metadata: { duration_ms: 900 } }] });
    expect(scope).toBeUndefined();
    expect(text).toContain('「导入素材成片」合成卡内的 2 个视频');
    expect(text).toContain('第 1 个视频（1.5 秒） → 第 2 个视频（0.9 秒）');
    expect(text).toContain('请先核对');
    expect(text).toContain('当前画布选区不能替代原卡片来源');
  });
  it.each([[0, '0%'], [.4, '40%'], [.9, '90%'], [1, '100%'], [-.1, '0%'], [1.5, '100%']] as const)('shows backend progress %s on the 0..1 scale as %s', (progress, label) => {
    expect(executionLabel({ ...execution(), status: 'running', active: true, asset_available: false, progress })).toBe(label);
  });
  it('preserves a POST acknowledgement when a stale proposed snapshot arrives', () => {
    const submitted = { ...film(), action_status: 'submitted' as const, status: 'running', requires_user_action: false, submit_client_request_id: pending.client_request_id };
    expect(mergeWorkflowActions([submitted], [film()])[0]).toEqual(submitted);
    expect(mergeWorkflowActions([submitted], [])[0]).toEqual(submitted);
  });
  it('accepts a genuine completed-to-unavailable transition instead of retaining fake success', () => {
    const old = { ...film(), action_status: 'submitted' as const, status: 'completed' };
    const fresh = { ...old, status: 'failed', error: { code: 'OUTPUT_ASSET_CHANGED', message: '文件内容已变化' } };
    expect(mergeWorkflowActions([old], [fresh])[0].status).toBe('failed');
  });
  it('keeps acknowledged tasks and continued polling when an interrupted receipt contains only an unknown fallback', () => {
    const submitted = { ...film(), action_status: 'submitted' as const, status: 'running', requires_user_action: false,
      submit_client_request_id: pending.client_request_id, executions: [execution()] };
    const unknown: WorkflowAction = { action_id: submitted.action_id, kind: 'generation', status: 'unknown', requires_user_action: false };
    const merged = mergeWorkflowActions([submitted], [unknown]);
    expect(merged[0]).toEqual(submitted);
    expect(merged.some(action => action.status === 'running')).toBe(true);
    expect(actionSubmissionState(merged[0], pending)).toBe('accepted');
    expect(mergeWorkflowActions([unknown], [submitted])[0]).toEqual(submitted);
  });
  it('retains a full proposed scope through a fallback while accepting actual partial output updates', () => {
    const unknown: WorkflowAction = { action_id: film().action_id, kind: 'generation', status: 'unknown', requires_user_action: false };
    expect(mergeWorkflowActions([film()], [unknown])[0]).toEqual(film());
    const partial = { ...film(), action_status: 'submitted' as const, requires_user_action: false, status: 'partial', executions: [execution()] };
    expect(mergeWorkflowActions([film()], [partial])[0]).toEqual(partial);
  });
  it('only resolves the exact action and submission request, while identifying another submission', () => {
    expect(actionSubmissionState(film(), pending)).toBe('unconfirmed');
    expect(actionSubmissionState({ ...film(), action_status: 'submitted', submit_client_request_id: pending.client_request_id }, pending)).toBe('accepted');
    expect(actionSubmissionState({ ...film(), action_status: 'submitted', submit_client_request_id: 'elsewhere' }, pending)).toBe('other');
    expect(actionSubmissionState({ ...film(), action_id: 'other', action_status: 'submitted', submit_client_request_id: pending.client_request_id }, pending)).toBe('unconfirmed');
  });
  it('never confirms an expired scope or a film with reordered approval IDs', () => {
    expect(actionCanConfirm(film(), 1)).toBe(true);
    expect(actionCanConfirm({ ...film(), expires_at: 1 }, 2)).toBe(false);
    expect(actionCanConfirm({ ...film(), scope: { ...film().scope!, ordered_node_ids: ['red', 'blue'] } }, 1)).toBe(false);
    expect(actionCanConfirm({ ...film(), stale: true }, 1)).toBe(false);
  });
  it('allows explicit approval of a durable action before its conversation reply finishes, but never crosses requests', () => {
    const pendingConversation = { endpoint: 'conversation' as const, id: 'conversation', message: '写脚本', selected_node_ids: [] };
    expect(actionWithinPendingRequest(film(), pendingConversation)).toBe(true);
    expect(actionWithinPendingRequest(film(), { ...pendingConversation, id: 'another-conversation' })).toBe(false);
    expect(actionWithinPendingRequest(film(), { endpoint: 'legacy', id: 'conversation', message: '旧请求' })).toBe(false);
    expect(actionWithinPendingRequest(film(), null)).toBe(true);
  });
  it('requires a usable, matching asset instead of trusting task success alone', () => {
    expect(availableExecution(execution())).toBe(true);
    expect(availableExecution({ ...execution(), asset_available: false, result_status: 'unavailable' })).toBe(false);
    expect(availableExecution({ ...execution(), asset_id: 'another-result' })).toBe(false);
    expect(availableExecution({ ...execution(), status: 'failed' })).toBe(false);
  });
  it('does not show a final video while only the individual clips are complete', () => {
    expect(finalComposition({ ...film(), media_status: 'completed', composition: { status: 'waiting', reason: 'preparing' } })).toBeNull();
    expect(finalComposition({ ...film(), composition: { status: 'completed', asset_available: false, asset_id: 'missing' } })).toBeNull();
    expect(finalComposition({ ...film(), composition: { status: 'completed', asset_available: true, asset_id: 'film' } })?.asset_id).toBe('film');
  });
  it('keeps tiny positive estimates visible and does not invent a zero quote', () => {
    expect(estimatedCostLabel(0.001)).toBe('预计 ¥0.001');
    expect(estimatedCostLabel(undefined)).toBe('费用待核对');
    expect(quoteSpecs(quote())).toEqual(['图生视频', '16:9', '720P', '5 秒']);
  });
  it('renders the frozen order, explicit film scope, quote and a single confirmation', () => {
    const html = render(film());
    expect(html).toContain('全部完成后按此顺序合成');
    expect(html.indexOf('蓝色开场')).toBeLessThan(html.indexOf('红色结尾'));
    expect(html).toContain('预计 ¥0.002');
    expect(html.match(/生成视频并合成/g)).toHaveLength(1);
    expect(html).not.toContain('确认并合成');
    expect(html).toContain('参考图');
  });
  it('keeps the old successful clip visible when another clip fails, without a fake final film', () => {
    const action = { ...film(), action_status: 'submitted' as const, requires_user_action: false, status: 'partial', media_status: 'partial', executions: [execution()],
      composition: { status: 'blocked', reason: 'media_incomplete' }, error: { code: 'FILM_MEDIA_INCOMPLETE', message: '一个镜头未完成' } };
    const html = render(action);
    expect(html).toContain('/api/assets/output/file');
    expect(html).toContain('下载片段');
    expect(html).toContain('一个镜头未完成');
    expect(html).not.toContain('下载成片');
    expect(html).not.toContain('正在合成成片');
  });
  it('shows the final playable file only from an available composition and no second confirmation', () => {
    const action = { ...film(), action_status: 'submitted' as const, requires_user_action: false, status: 'completed', media_status: 'completed',
      composition: { status: 'completed', asset_available: true, asset_id: 'final', result_node_id: 'film-node', actual_duration_ms: 1000 } };
    const html = render(action);
    expect(html).toContain('/api/assets/final/file');
    expect(html).toContain('下载成片');
    expect(html).toContain('1.0 秒');
    expect(html).not.toContain('生成视频并合成');
    expect(html).not.toContain('确认并合成');
  });
  it('shows actual source titles in the frozen composition order and reveals the original nodes', () => {
    const action: WorkflowAction = { action_id: 'compose-reverse', kind: 'composition', status: 'proposed', action_status: 'proposed', requires_user_action: true,
      node_ids: ['blue', 'red'], sources: [{ asset_id: 'red-file', node_id: 'red', metadata: { duration_ms: 600 } }, { asset_id: 'blue-file', node_id: 'blue', metadata: { duration_ms: 400 } }] };
    const nodeTitles = { blue: '原视频 1 · 蓝色开场', red: '原视频 2 · 红色结尾' }, onReveal = vi.fn();
    const html = render(action, false, nodeTitles);
    expect(html.indexOf(nodeTitles.red)).toBeLessThan(html.indexOf(nodeTitles.blue));
    expect(html.indexOf('合成顺序 1')).toBeLessThan(html.indexOf(nodeTitles.red));
    expect(html.indexOf('合成顺序 2')).toBeLessThan(html.indexOf(nodeTitles.blue));
    expect(html.indexOf(nodeTitles.red)).toBeLessThan(html.indexOf('合成顺序 2'));
    const tree = WorkflowActionCard({ action, disabled: false, nodeTitles, onReveal, onConfirm: () => {}, onSuggest: () => {} });
    findActionButton(tree, nodeTitles.red)!(); findActionButton(tree, nodeTitles.blue)!();
    expect(onReveal.mock.calls).toEqual([['red'], ['blue']]);
    expect(action.sources?.map(source => source.node_id)).toEqual(['red', 'blue']);
    expect(render(action, false, { red: nodeTitles.red, blue: nodeTitles.blue })).toBe(html);
  });
  it('previews a source without a usable title while preserving its original node reveal', () => {
    const action: WorkflowAction = { action_id: 'compose-missing-title', kind: 'composition', status: 'proposed', requires_user_action: true,
      sources: [{ asset_id: 'red-file', node_id: 'red', metadata: { duration_ms: 600 } }, { asset_id: 'blue-file', node_id: 'blue', metadata: { duration_ms: 400 } }] };
    const nodeTitles = { red: '  ', blue: '蓝色镜头' }, onReveal = vi.fn(), html = render(action, false, nodeTitles);
    expect(html).toContain('src="/api/assets/red-file/file"');
    expect(html).toContain('aria-label="预览第 1 个合成来源"');
    expect(html.match(/<video /g)).toHaveLength(1);
    expect(html).not.toContain('视频 1');
    expect(html.indexOf('/api/assets/red-file/file')).toBeLessThan(html.indexOf('蓝色镜头'));
    const tree = WorkflowActionCard({ action, disabled: false, nodeTitles, onReveal, onConfirm: () => {}, onSuggest: () => {} });
    findActionButton(tree, '在画布查看')!();
    expect(onReveal).toHaveBeenCalledWith('red');
  });
  it('provides playable identification for asset-only and unmapped sources without inventing node names', () => {
    const action: WorkflowAction = { action_id: 'compose-assets', kind: 'composition', status: 'proposed', requires_user_action: true,
      sources: [{ asset_id: 'asset-only', metadata: { duration_ms: 600 } }, { asset_id: 'missing-title', node_id: 'unknown', metadata: { duration_ms: 400 } }] };
    const html = render(action);
    expect(html).toContain('视频素材');
    expect(html).toContain('aria-label="预览第 1 个合成来源"');
    expect(html).toContain('aria-label="预览第 2 个合成来源"');
    expect(html.indexOf('/api/assets/asset-only/file')).toBeLessThan(html.indexOf('/api/assets/missing-title/file'));
    expect(html.match(/<video controls="" playsInline="" preload="metadata"/g)).toHaveLength(2);
    expect(html).not.toContain('视频 1'); expect(html).not.toContain('视频 2');
  });
  it('supports an independent composition and disables writes while the workspace has unsaved settings', () => {
    const action: WorkflowAction = { action_id: 'compose', kind: 'composition', status: 'proposed', action_status: 'proposed', requires_user_action: true, expires_at: 9e9,
      sources: [{ asset_id: 'v2', metadata: { duration_ms: 600 } }, { asset_id: 'v1', metadata: { duration_ms: 400 } }], title: '我的作品' };
    const html = render(action, true);
    expect(html).toContain('确认并合成');
    expect(html).toContain('disabled=""');
    expect(html).toContain('0.6 秒');
    expect(html).not.toContain('生成视频并合成');
  });
});

describe('explicit original conversation recovery', () => {
  const original: WorkflowPendingRequest = { endpoint: 'conversation', id: 'original-id', message: '只改第二镜', session_id: 'original-session', revision: 4, selected_node_ids: ['second', 'first'] };
  const reply: WorkflowReply = { request_id: original.id, session_id: original.session_id!, request_status: 'completed', reply: '已保存', reply_type: 'saved',
    outcome: { status: 'saved', node_ids: ['second'] }, artifacts: [{ type: 'image', node_id: 'second' }], actions: [], workflow_revision: 5 };
  const receipt = (status: WorkflowReceipt['status']): WorkflowReceipt => ({ client_request_id: original.id, status, response: status === 'pending' ? null : { ...reply, request_status: status },
    artifacts: [], actions: [], may_still_be_running: status === 'pending' });
  it('reads first and posts the exact frozen request only after a genuine not-found response', async () => {
    const order: string[] = [], frozen = structuredClone(original);
    const api = { receipt: vi.fn(async () => { order.push('GET'); throw new ApiError('DIRECTOR_REQUEST_NOT_FOUND', 'missing', 404); }),
      conversation: vi.fn(async (_project: string, value: WorkflowPendingRequest) => { order.push('POST'); expect(value).toEqual(frozen); return reply; }) };
    expect(await retryMissingWorkflowConversation(api, 'project', original)).toEqual({ kind: 'reply', reply });
    expect(order).toEqual(['GET', 'POST']);
    expect(api.receipt).toHaveBeenCalledWith('project', original.id, { signal: undefined });
    expect(api.conversation).toHaveBeenCalledOnce();
    expect(original).toEqual(frozen);
  });
  it.each(['pending', 'completed', 'failed'] as const)('does not POST when the late original receipt is %s', async status => {
    const actual = receipt(status), api = { receipt: vi.fn(async () => actual), conversation: vi.fn(async () => reply) };
    expect(await retryMissingWorkflowConversation(api, 'project', original)).toEqual({ kind: 'receipt', receipt: actual });
    expect(api.conversation).not.toHaveBeenCalled();
  });
  it.each([
    new ApiError('DIRECTOR_REQUEST_NOT_FOUND', 'forbidden', 403),
    new ApiError('PROJECT_NOT_FOUND', 'missing project', 404),
    new ApiError('DIRECTOR_RESPONSE_INVALID', 'unknown receipt', 0),
    new ApiError('DIRECTOR_READ_FAILED', 'network interrupted', 0),
  ])('never interprets an unreadable or forbidden receipt as permission to resend: $code/$status', async error => {
    const api = { receipt: vi.fn(async () => { throw error; }), conversation: vi.fn(async () => reply) };
    await expect(retryMissingWorkflowConversation(api, 'project', original)).rejects.toBe(error);
    expect(api.conversation).not.toHaveBeenCalled();
  });
  it('does not replay legacy pending requests through the new API', async () => {
    const api = { receipt: vi.fn(async () => receipt('pending')), conversation: vi.fn(async () => reply) };
    await expect(retryMissingWorkflowConversation(api, 'project', { endpoint: 'legacy', id: 'old-id', message: 'old message' })).rejects.toMatchObject({ code: 'VALIDATION_FAILED' });
    expect(api.receipt).not.toHaveBeenCalled(); expect(api.conversation).not.toHaveBeenCalled();
  });
  it('honors cancellation between the not-found GET and the possible POST', async () => {
    const controller = new AbortController();
    const api = { receipt: vi.fn(async () => { controller.abort(); throw new ApiError('DIRECTOR_REQUEST_NOT_FOUND', 'missing', 404); }), conversation: vi.fn(async () => reply) };
    await expect(retryMissingWorkflowConversation(api, 'project', original, controller.signal)).rejects.toMatchObject({ code: 'REQUEST_ABORTED' });
    expect(api.conversation).not.toHaveBeenCalled();
  });
  it('propagates an unknown POST result without a second replay', async () => {
    const error = new ApiError('DIRECTOR_RESULT_UNKNOWN', 'unknown', 0);
    const api = { receipt: vi.fn(async () => { throw new ApiError('DIRECTOR_REQUEST_NOT_FOUND', 'missing', 404); }), conversation: vi.fn(async () => { throw error; }) };
    await expect(retryMissingWorkflowConversation(api, 'project', original)).rejects.toBe(error);
    expect(api.conversation).toHaveBeenCalledOnce();
  });
});
