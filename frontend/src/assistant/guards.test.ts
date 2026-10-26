import { describe, expect, it } from 'vitest';
import { ApiError } from '../api';
import { assistantAvailability, buildApprovedSubmission, inspectQuote, safeReplyType } from './guards';
import type { AssistantApproval, AssistantQuote, AssistantSession, FrozenTaskPayload } from './types';

const now = Date.UTC(2026, 9, 25, 8);
const session: AssistantSession = { session_id: 'asst_frozen', project_id: 'draft_product', intent: 'generate', status: 'active' };
/** Offline fixture follows approval.freeze_plan's request shape, including empty raw params. */
const payload: FrozenTaskPayload = {
  kind: 'video', model_id: 'doubao-seedance-1-0-pro-fast-251015',
  input_asset_id: 'asset_product', template_id: 'tpl_product_motion_5s', template_version: 2,
  template_fields: { motion: '推近特写', duration: 5, ratio: '9:16', keep_features: ['标签', '瓶盖'] }, params: {},
};
const effective = { duration: 5, ratio: '9:16', resolution: '720p', generate_audio: false };
const quote: AssistantQuote = {
  quote_id: 'q_frozen', plan_hash: 'server-hash-not-recomputed', expires_at: now / 1000 + 600,
  cost_cny: 0.4536, estimated_cost_cny: 0.4536, effective,
  resolved_params: { prompt: '  以首帧图中的商品为准。\n保留标签，不增加商品。  ', ...effective },
  submit_payload: payload, manual_params: { ...payload, effective, template_name: '5 秒商品动态展示', concept_mode: false },
};
const approval: AssistantApproval = {
  approval_id: 'ap_frozen', quote_id: 'q_frozen', plan_hash: quote.plan_hash, session_id: session.session_id,
  project_id: session.project_id, expires_at: now / 1000 + 600, max_uses: 1, cost_cny: 0.4536, submit_payload: payload,
};

describe('server-owned frozen plans', () => {
  it('adds only approval_id, preserving raw empty params and the frozen template version', () => {
    const beforeQuote = structuredClone(quote), beforeApproval = structuredClone(approval);
    const result = buildApprovedSubmission(quote, approval, session, now);
    expect(result).toEqual({ ...payload, approval_id: 'ap_frozen' });
    expect(result.params).toBe(payload.params);
    expect(result.template_fields).toBe(payload.template_fields);
    expect(result.params).not.toHaveProperty('prompt');
    expect(result).not.toHaveProperty('client_request_id');
    expect(quote).toEqual(beforeQuote);
    expect(approval).toEqual(beforeApproval);
  });

  it('retains unknown backend payload fields without reconstructing the request', () => {
    const extra = { ...payload, server_extension: { label: '保留原值', value: 0, enabled: false } };
    const result = buildApprovedSubmission({ ...quote, submit_payload: extra }, { ...approval, submit_payload: extra }, session, now);
    expect(result).toEqual({ ...extra, approval_id: 'ap_frozen' });
  });

  it('compares JSON structure rather than relying on key insertion order', () => {
    const reversed = Object.fromEntries(Object.entries(payload).reverse()) as FrozenTaskPayload;
    expect(buildApprovedSubmission(quote, { ...approval, submit_payload: reversed }, session, now)).toEqual({ ...payload, approval_id: 'ap_frozen' });
  });

  it.each([
    ['final prompt', { resolved_params: undefined }],
    ['blank final prompt', { resolved_params: { prompt: '  ' } }],
    ['expiry', { expires_at: null }],
    ['invalid expiry', { expires_at: Number.NaN }],
    ['expired quote', { expires_at: now / 1000 }],
    ['cost', { cost_cny: null, estimated_cost_cny: null }],
    ['payload', { submit_payload: null }],
    ['hash', { plan_hash: null }],
    ['effective spec', { effective: {} }],
  ])('blocks both confirmation and manual handoff when missing %s', (_, patch) => {
    const result = inspectQuote({ ...quote, ...patch }, session, now);
    expect(result.canConfirm).toBe(false);
    expect(result.canTransferToManual).toBe(false);
    expect(result.confirmationIssues.length).toBeGreaterThan(0);
    expect(result.manualIssues.every(issue => issue.message.length > 0)).toBe(true);
  });

  it('does not turn an omitted price into zero', () => {
    const { cost_cny: _cost, estimated_cost_cny: _estimate, ...missing } = quote;
    void _cost; void _estimate;
    expect(inspectQuote(missing, session, now).confirmationIssues).toContainEqual(expect.objectContaining({ code: 'QUOTE_COST_MISSING' }));
  });

  it('allows a genuine zero estimate and never fills it from null', () => {
    const zero = { ...quote, cost_cny: 0, estimated_cost_cny: 0 };
    expect(inspectQuote(zero, session, now).canConfirm).toBe(true);
    expect(buildApprovedSubmission(zero, { ...approval, cost_cny: 0 }, session, now)).toEqual({ ...payload, approval_id: 'ap_frozen' });
    expect(inspectQuote({ ...zero, estimated_cost_cny: null }, session, now).canConfirm).toBe(false);
  });

  it.each([-1, '0', Number.NaN, Infinity])('rejects invalid cost %s', cost => {
    expect(inspectQuote({ ...quote, cost_cny: cost, estimated_cost_cny: cost }, session, now).canConfirm).toBe(false);
  });

  it('blocks incomplete or divergent manual parameters without disabling a valid confirmation', () => {
    const absent = inspectQuote({ ...quote, manual_params: null }, session, now);
    expect(absent.canConfirm).toBe(true);
    expect(absent.canTransferToManual).toBe(false);
    const changed = inspectQuote({ ...quote, manual_params: { ...payload, effective: { ...effective, duration: 10 } } }, session, now);
    expect(changed.canTransferToManual).toBe(false);
    expect(changed.manualIssues.at(-1)?.code).toBe('QUOTE_MANUAL_PARAMS_MISMATCH');
  });

  it('requires frozen template version and accepts GET quote id without mutating the object', () => {
    const { quote_id: _id, ...rest } = quote;
    void _id;
    const fromGet = { ...rest, id: 'q_frozen' };
    expect(inspectQuote(fromGet, session, now).canConfirm).toBe(true);
    expect(fromGet).not.toHaveProperty('quote_id');
    expect(inspectQuote({ ...quote, submit_payload: { ...payload, template_version: null } }, session, now).canConfirm).toBe(false);
  });

  it.each([
    ['quote', { quote_id: 'q_other' }],
    ['session', { session_id: 'asst_other' }],
    ['project', { project_id: 'draft_other' }],
    ['hash', { plan_hash: 'changed' }],
    ['expired approval', { expires_at: now / 1000 }],
    ['uses', { max_uses: 2 }],
    ['fee', { cost_cny: null }],
    ['raw params', { submit_payload: { ...payload, params: { prompt: '前端补写的提示词' } } }],
    ['template version', { submit_payload: { ...payload, template_version: 3 } }],
    ['reference', { submit_payload: { ...payload, input_asset_id: 'asset_other' } }],
  ])('rejects mismatched approval %s', (_, patch) => {
    expect(() => buildApprovedSubmission(quote, { ...approval, ...patch }, session, now)).toThrow(ApiError);
  });

  it.each(['optimize_only', 'auto', 'future_intent'])('does not enable generation in %s mode', intent => {
    const changed = { ...session, intent };
    expect(inspectQuote(quote, changed, now).canConfirm).toBe(false);
    expect(() => buildApprovedSubmission(quote, approval, changed, now)).toThrow(ApiError);
  });

  it('blocks awaiting facts and unknown states even if a quote exists', () => {
    expect(inspectQuote(quote, { ...session, status: 'awaiting_facts' }, now).canConfirm).toBe(false);
    expect(inspectQuote(quote, { ...session, status: 'future_status' }, now).canConfirm).toBe(false);
  });
});

describe('availability and capability evidence', () => {
  const status = { available: true, state: 'connected_verified', provider: 'deepseek', verified: true };
  it('separates connection verification from capability verification', () => {
    expect(assistantAvailability(status)).toMatchObject({ canChat: true, connectionVerified: true, capabilityVerification: 'not_reported' });
    expect(assistantAvailability({ ...status, state: 'configured_unverified', verified: false }).connectionVerified).toBe(false);
  });
  it.each([
    { ...status, state: 'stub' },
    { ...status, provider: 'fixed' },
    { ...status, provider: { name: 'deepseek', stub: true } },
    { ...status, available: false },
    { ...status, state: 'future_state' },
  ])('never enables a stub, unavailable or unknown provider state', value => {
    expect(assistantAvailability(value).canChat).toBe(false);
  });
  it('also rejects a provider.stub flag on a response and handles unknown reply types', () => {
    expect(assistantAvailability(status, { name: 'deepseek', stub: true }).canChat).toBe(false);
    expect(safeReplyType('new_action')).toBe('unknown');
  });
});
