import { getSubmissionBlockedReason } from './capabilities';

/** Validate complete server estimates without changing them or calculating a price. */
export function inspectGenerationEstimates(value: unknown, expectedCount: number): { costs: number[] | null; error: string | null } {
  const fail = (error: string) => ({ costs: null, error });
  if (!Number.isSafeInteger(expectedCount) || expectedCount <= 0) return fail('待生成任务数量无效，请重新确认生成数量。');
  if (!Array.isArray(value)) return fail('费用预估响应格式不完整，请重新预估。');
  if (value.length !== expectedCount) return fail('费用预估数量与待生成任务不一致，请重新预估。');

  const costs: number[] = [];
  let total = 0;
  for (let index = 0; index < value.length; index += 1) {
    const estimate: unknown = value[index];
    if (typeof estimate !== 'object' || estimate === null || Array.isArray(estimate)) {
      return fail(`第 ${index + 1} 个任务的费用预估格式不完整，请重新预估。`);
    }
    const item = estimate as Record<string, unknown>;
    const blockedReason = getSubmissionBlockedReason(item.submittable, item.blocked_reasons);
    if (blockedReason !== null) return fail(`第 ${index + 1} 个任务：${blockedReason}`);

    const cost = item.estimated_cost_cny;
    if (typeof cost !== 'number' || !Number.isFinite(cost) || cost < 0) {
      return fail(`第 ${index + 1} 个任务的费用预估缺失或无效，请重新预估。`);
    }
    total += cost;
    if (!Number.isFinite(total)) return fail('费用预估合计无效，请重新预估。');
    costs.push(cost);
  }
  return { costs, error: null };
}
