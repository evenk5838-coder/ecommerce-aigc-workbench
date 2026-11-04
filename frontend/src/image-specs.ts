import { getImageSizesForRatio } from './capabilities';
import type { SpecSelection } from './model-specs';
import type { Model } from './types';

/** Labels describe pixel budgets, not image quality or model capabilities. */
export function imageSizeTier(size: string): string {
  const match = /^(\d+)x(\d+)$/.exec(size);
  if (!match) return '自定义';
  const area = Number(match[1]) * Number(match[2]);
  if (!Number.isSafeInteger(area) || area <= 0) return '自定义';
  for (const [label, edge] of [['1K',1024],['1.5K',1536],['2K',2048],['3K',3072],['4K',4096]] as const) {
    if (area <= edge * edge * 1.1025) return label;
  }
  return '自定义';
}
export function imageTierOptions(model: Model | undefined, ratio: string, selection?: SpecSelection): { label: string; sizes: string[] }[] {
  const options: { label: string; sizes: string[] }[] = [];
  for (const size of getImageSizesForRatio(model, ratio, selection)) {
    const label = imageSizeTier(size), group = options.find(item => item.label === label);
    if (group) group.sizes.push(size); else options.push({ label, sizes: [size] });
  }
  return options;
}
export function ratioPreview(ratio: string): { width: number; height: number } {
  const match = /^(\d+):(\d+)$/.exec(ratio);
  if (!match || Number(match[1]) <= 0 || Number(match[2]) <= 0) return { width: 20, height: 20 };
  const w = Number(match[1]), h = Number(match[2]), scale = 27 / Math.max(w,h);
  return { width: Math.max(3,w*scale), height: Math.max(3,h*scale) };
}
