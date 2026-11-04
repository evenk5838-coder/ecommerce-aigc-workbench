import type { Model, ParameterCombination } from './types';

export type SpecSelection = Partial<Record<keyof ParameterCombination, unknown>>;
const fields = new Set(['generation_mode', 'ratio', 'resolution', 'size', 'duration', 'generate_audio']);
const record = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const positive = (v: unknown): v is number => typeof v === 'number' && Number.isSafeInteger(v) && v > 0;
export const SPEC_POLICY_ERROR = '模型规格暂不可用，请刷新模型列表或选择其他模型。';

/** undefined is legacy metadata; null is an explicitly malformed policy, never a wildcard. */
export function combinationRules(model: Model | undefined): ParameterCombination[] | null | undefined {
  const caps = model?.capabilities;
  if (!caps || !Object.hasOwn(caps, 'parameter_combinations')) return undefined;
  const rules: unknown = caps.parameter_combinations;
  if (!Array.isArray(rules) || !rules.length || rules.length > 100) return null;
  for (const rule of rules) {
    if (!record(rule) || !Object.keys(rule).length) return null;
    for (const [field, values] of Object.entries(rule)) {
      if (!fields.has(field)) return null;
      if (field === 'duration' && record(values)) {
        const { min, max, step } = values;
        if (Object.keys(values).sort().join() !== 'max,min,step' || !positive(min) || !positive(max) || !positive(step) || max < min || (max-min)%step !== 0) return null;
      } else if (!Array.isArray(values) || !values.length || !values.every(value => field === 'duration' ? positive(value) : field === 'generate_audio' ? typeof value === 'boolean' : typeof value === 'string' && !!value.trim())) return null;
    }
  }
  return rules as ParameterCombination[];
}

function matches(value: unknown, allowed: unknown): boolean {
  if (Array.isArray(allowed)) return allowed.some(item => typeof item === typeof value && item === value);
  if (!record(allowed) || !positive(value)) return false;
  const { min, max, step } = allowed as {min: number; max: number; step: number};
  return value >= min && value <= max && (value-min)%step === 0;
}

/** Project a field over upstream choices only, so a stale downstream choice cannot trap the user. */
export function filterSpecOptions<T extends string | number | boolean>(model: Model | undefined, field: keyof ParameterCombination, options: T[], selection: SpecSelection = {}): T[] {
  const rules = combinationRules(model);
  if (rules === null) return [];
  if (!rules) return options;
  return options.filter(value => rules.some(rule => Object.entries(rule).every(([key, allowed]) => {
    const chosen = key === field ? value : selection[key as keyof ParameterCombination];
    return chosen === undefined || chosen === '' || matches(chosen, allowed);
  })));
}

/** Exact submission validation: missing constrained fields do not match. Base capability checks remain required. */
export function validateSpecCombination(model: Model | undefined, selection: SpecSelection): string | null {
  const rules = combinationRules(model);
  if (rules === null) return SPEC_POLICY_ERROR;
  if (!rules || rules.some(rule => Object.entries(rule).every(([key, allowed]) => matches(selection[key as keyof ParameterCombination], allowed)))) return null;
  return '当前输入方式与画面规格不兼容，请重新选择比例、清晰度或时长。';
}
