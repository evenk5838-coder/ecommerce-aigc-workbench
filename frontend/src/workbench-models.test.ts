import { describe, expect, it } from 'vitest';
import type { Model } from './types';
import { DEFAULT_GENERATION } from './editor';
import { initialWorkbenchModel, settingsForWorkbenchModel } from './workbench-models';

const image: Model = { id:'ark-image',name:'Image',provider:'ark',kind:'image',downloaded:false,loaded:false,weights_mb:0,
  submittability:{submittable:true,reasons:[]},capabilities:{input_modes:['text_to_image','image_to_image'],ratios:['1:1','16:9'],sizes:['2048x2048','2688x1536']} };
const hub: Model = {...image,id:'aihubmix:image',provider:'aihubmix',capabilities:{input_modes:['text_to_image','image_to_image'],ratios:['1:1'],sizes:['1024x1024']}};
const fast: Model = {...image,id:'aihubmix:video',provider:'aihubmix',kind:'video',capabilities:{input_modes:['text_to_video','image_to_video'],ratios:['16:9'],resolutions:['480p'],duration_policy:{kind:'enum',allowed:[5]},parameter_combinations:[{generation_mode:['text_to_video','image_to_video'],ratio:['16:9'],resolution:['480p'],duration:[5],generate_audio:[false]}]}};

describe('workbench model choices', () => {
  it('prefers enabled AiHubMix only for a fresh creation', () => {
    expect(initialWorkbenchModel([image,hub],DEFAULT_GENERATION,'text_to_image',false)?.id).toBe(hub.id);
    expect(initialWorkbenchModel([image,hub],DEFAULT_GENERATION,'text_to_image',true)?.id).toBe(image.id);
  });
  it('does not migrate an old incompatible draft or enable an unavailable candidate', () => {
    expect(initialWorkbenchModel([hub],DEFAULT_GENERATION,'text_to_image',true)).toBeUndefined();
    expect(initialWorkbenchModel([image,{...hub,submittability:{submittable:false,reasons:['MODEL_NOT_VERIFIED']}}],DEFAULT_GENERATION,'text_to_image',false)?.id).toBe(image.id);
    expect(initialWorkbenchModel([image,hub],{...DEFAULT_GENERATION,imageRatio:'3:4'},'text_to_image',true)).toBeUndefined();
  });
  it('chooses the permitted square image spec after an explicit switch without changing prompts or references', () => {
    const settings={...DEFAULT_GENERATION,imageRatio:'16:9',imageSize:'2688x1536',imagePrompt:'keep this',imageReference:'reference'};
    const before=structuredClone(settings);
    expect(settingsForWorkbenchModel(hub,settings,'image_to_image')).toEqual({imageModelId:hub.id,imageRatio:'1:1',imageSize:'1024x1024'});
    expect(settings).toEqual(before);
  });
  it('selects a valid video combination instead of retaining unsupported old 720p portrait settings', () => {
    expect(settingsForWorkbenchModel(fast,{...DEFAULT_GENERATION,kind:'video',duration:10},'image_to_video')).toEqual({videoModelId:fast.id,videoRatio:'16:9',videoResolution:'480p',duration:5});
  });
  it('preserves supported explicit specifications on a model switch', () => {
    expect(settingsForWorkbenchModel(image,{...DEFAULT_GENERATION,imageRatio:'16:9',imageSize:'2688x1536'},'text_to_image')).toEqual({imageModelId:image.id,imageRatio:'16:9',imageSize:'2688x1536'});
  });
});
