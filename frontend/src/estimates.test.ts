import { describe, expect, it } from 'vitest';
import { inspectGenerationEstimates } from './estimates';

const estimate = (cost = 0.12) => ({ submittable: true, blocked_reasons: [], estimated_cost_cny: cost });

describe('generation estimate validation', () => {
  it('accepts real zero and normal costs only with explicit submission permission', () => {
    expect(inspectGenerationEstimates([estimate(0), estimate(0.4536)], 2)).toEqual({ costs: [0, 0.4536], error: null });
  });

  it('blocks false permission even when a valid cost is present', () => {
    const result = inspectGenerationEstimates([{ ...estimate(), submittable: false, blocked_reasons: ['MODEL_NOT_OPENED'] }], 1);
    expect(result.costs).toBeNull();
    expect(result.error).toContain('尚未开通');
  });

  it('uses MODEL_EOS and unknown reason text without treating either as permission', () => {
    for (const [code, message] of [['MODEL_EOS', '已下线'], ['FUTURE_SERVER_POLICY', 'FUTURE_SERVER_POLICY']]) {
      const result = inspectGenerationEstimates([{ ...estimate(), submittable: false, blocked_reasons: [code] }], 1);
      expect(result.costs).toBeNull();
      expect(result.error).toContain(message);
    }
  });

  it.each([undefined, null, [], 'MODEL_EOS', [null, 7], {}].map(reasons => [reasons]))('still blocks false permission with malformed or missing reasons: %j', reasons => {
    const result = inspectGenerationEstimates([{ ...estimate(), submittable: false, blocked_reasons: reasons }], 1);
    expect(result.costs).toBeNull();
    expect(result.error).toContain('服务端暂不允许提交');
  });

  it.each([undefined, null, 'true', 'false', 1, 0, [], {}].map(submittable => [submittable]))('blocks missing or invalid submission permission: %j', submittable => {
    const result = inspectGenerationEstimates([{ ...estimate(), submittable }], 1);
    expect(result.costs).toBeNull();
    expect(result.error).toContain('许可缺失或无效');
  });

  it('does not infer permission from cost, activation, lifecycle or reason codes', () => {
    expect(inspectGenerationEstimates([{ estimated_cost_cny: 0, activation: 'verified', blocked_reasons: [] }], 1).costs).toBeNull();
    expect(inspectGenerationEstimates([{ ...estimate(), activation: 'not_opened', lifecycle: { status: 'eos_expired' }, blocked_reasons: ['MODEL_EOS'] }], 1))
      .toEqual({ costs: [0.12], error: null });
  });

  it('rejects the entire mixed batch without returning partial costs', () => {
    const result = inspectGenerationEstimates([estimate(), { ...estimate(0), submittable: false, blocked_reasons: ['MODEL_EOS'] }, estimate()], 3);
    expect(result.costs).toBeNull();
    expect(result.error).toContain('第 2 个任务');
  });

  it.each([undefined, null, {}, 'estimates', 1, true])('requires an array: %j', value => {
    expect(inspectGenerationEstimates(value, 1)).toEqual({ costs: null, error: expect.any(String) });
  });

  it.each([0, -1, 1.5, NaN, Infinity, -Infinity, Number.MAX_SAFE_INTEGER + 1, '1', null, undefined])(
    'requires a positive safe integer expected count: %j', count => {
      expect(inspectGenerationEstimates([estimate()], count as number)).toEqual({ costs: null, error: expect.stringContaining('数量无效') });
    },
  );

  it('requires exactly one estimate for each requested task', () => {
    for (const [values, count] of [[[], 1], [[estimate()], 2], [[estimate(), estimate()], 1]] as const) {
      expect(inspectGenerationEstimates(values, count)).toEqual({ costs: null, error: expect.stringContaining('数量与待生成任务不一致') });
    }
  });

  it.each([undefined, null, [], 'estimate', 1, true].map(value => [value]))('requires every estimate to be a record: %j', value => {
    expect(inspectGenerationEstimates([value], 1)).toEqual({ costs: null, error: expect.stringContaining('格式不完整') });
  });

  it('does not skip an absent array entry', () => {
    expect(inspectGenerationEstimates(new Array(1), 1)).toEqual({ costs: null, error: expect.stringContaining('格式不完整') });
  });

  it.each([undefined, null, '0', '0.12', true, false, -0.01, NaN, Infinity, -Infinity, [], {}].map(cost => [cost]))('rejects missing or invalid costs without coercion: %j', cost => {
    const result = inspectGenerationEstimates([{ ...estimate(), estimated_cost_cny: cost }], 1);
    expect(result.costs).toBeNull();
    expect(result.error).toContain('费用预估缺失或无效');
  });

  it('rejects a non-finite sum even when each individual estimate is finite', () => {
    expect(inspectGenerationEstimates([estimate(Number.MAX_VALUE), estimate(Number.MAX_VALUE)], 2))
      .toEqual({ costs: null, error: expect.stringContaining('合计无效') });
  });

  it('returns exact server costs and does not modify input records or nested data', () => {
    const input = Object.freeze([
      Object.freeze({ ...estimate(0), blocked_reasons: Object.freeze([]), effective: Object.freeze({ duration: 5 }) }),
      Object.freeze({ ...estimate(0.123456789), blocked_reasons: Object.freeze([]) }),
    ]);
    const before = structuredClone(input);
    const result = inspectGenerationEstimates(input, 2);
    expect(result).toEqual({ costs: [0, 0.123456789], error: null });
    expect(input).toEqual(before);
    result.costs?.push(5);
    expect(input).toEqual(before);
  });
});
