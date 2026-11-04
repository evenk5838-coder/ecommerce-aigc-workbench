import { describe, expect, it } from 'vitest';
import { isCloudTask, modelDisplayName, orderCloudModels } from './model-catalog';
import { getModelUnavailableReason, validateGenerationSpecs } from './capabilities';
import type { Model, Task } from './types';
const base: Model = {id:'ark-video',name:'Seedance Fast',kind:'video',provider:'ark',downloaded:false,loaded:false,weights_mb:0,submittability:{submittable:true,reasons:[]}};
const hub: Model = {...base,id:'aihubmix:fast',provider:'aihubmix',activation:'pending_validation',capabilities:{input_modes:['text_to_video','image_to_video'],ratios:['16:9'],resolutions:['480p'],duration_policy:{kind:'enum',allowed:[5]},parameter_combinations:[{generation_mode:['text_to_video','image_to_video'],ratio:['16:9'],resolution:['480p'],duration:[5],generate_audio:[false]}]}};
describe('cloud provider catalogue',()=>{
 it('orders available Hub first without mutating a historical selection or granting permission',()=>{
  const closed={...hub,id:'aihubmix:mini',submittability:{submittable:false,reasons:['MODEL_NOT_VERIFIED']}};
  const source=[base,closed,hub];expect(orderCloudModels(source).map(m=>m.id)).toEqual([hub.id,base.id,closed.id]);expect(source[0]).toBe(base);
  expect(getModelUnavailableReason(hub)).toBeNull();expect(getModelUnavailableReason(closed)).toContain('暂未开放');
  expect(getModelUnavailableReason({...hub,submittability:{submittable:false,reasons:['MODEL_PRICE_UNCONFIRMED']}})).toContain('费用暂未就绪');
  expect(getModelUnavailableReason({...hub,submittability:null})).not.toBeNull();
 });
 it('accepts both tested video modes and rejects broader vendor specs',()=>{
  for(const mode of ['text_to_video','image_to_video']){
   const p={generation_mode:mode,ratio:'16:9',resolution:'480p',duration:5,generate_audio:false};
   expect(validateGenerationSpecs(hub,'video',p)).toBeNull();
   for(const patch of [{resolution:'720p'},{duration:10},{ratio:'9:16'},{generate_audio:true}])expect(validateGenerationSpecs(hub,'video',{...p,...patch})).not.toBeNull();
  }
 });
 it('recognizes historical cloud tasks and disambiguates providers',()=>{
  expect(isCloudTask({model_id:'aihubmix:retired'} as Task)).toBe(true);
  expect(isCloudTask({provider:'aihubmix',model_id:'future'} as Task)).toBe(true);
  expect(isCloudTask({model_id:'doubao-old'} as Task)).toBe(true);
  expect(isCloudTask({provider:'local',model_id:'sdxl'} as Task)).toBe(false);
  expect(modelDisplayName(hub)).toContain('AiHubMix');expect(modelDisplayName(base)).toContain('火山方舟');
 });
});
