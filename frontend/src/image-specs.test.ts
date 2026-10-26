import { describe, expect, it } from 'vitest';
import { imageSizeTier, imageTierOptions, ratioPreview } from './image-specs';
import type { Model } from './types';
const model = { capabilities: { ratios: ['1:1','9:16'], sizes: ['1024x1024','2048x2048','1536x2688'] } } as Model;
describe('image presentation uses exact server size options', () => {
  it('offers only sizes supplied for the chosen ratio, without inventing 4K', () => {
    expect(imageTierOptions(model,'1:1')).toEqual([{label:'1K',sizes:['1024x1024']},{label:'2K',sizes:['2048x2048']}]);
    expect(imageTierOptions(model,'9:16')).toEqual([{label:'2K',sizes:['1536x2688']}]);
    expect(imageTierOptions(model,'3:4')).toEqual([]);
    expect(imageTierOptions(undefined,'1:1')).toEqual([]);
  });
  it('keeps future explicit 1.5K and 4K entries visible only when metadata supplies them', () => {
    const future = {capabilities:{ratios:['1:1'],sizes:['1536x1536','4096x4096']}} as Model;
    expect(imageTierOptions(future,'1:1').map(x=>x.label)).toEqual(['1.5K','4K']);
  });
  it('labels pixel budgets independently of a long edge in portrait presets', () => {
    expect(imageSizeTier('1536x2688')).toBe('2K');
    expect(imageSizeTier('2304x1728')).toBe('2K');
    expect(imageSizeTier('invalid')).toBe('自定义');
    expect(imageSizeTier('0x1024')).toBe('自定义');
    expect(ratioPreview('9:16').width).toBeLessThan(ratioPreview('9:16').height);
  });
});
