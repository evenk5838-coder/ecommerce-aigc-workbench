import { describe, expect, it } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import type { Model, ParameterCombination } from './types';
import { combinationRules, filterSpecOptions, validateSpecCombination } from './model-specs';
import { getSupportedDurations, getSupportedRatios, getSupportedResolutions, getImageSizesForRatio, validateGenerationSpecs, getModelUnavailableReason } from './capabilities';
import { buildGenerationInput } from './generation';
import { nodeExecutionConfig, nodeGenerationIssue, withNodeModel } from './creative/node-config';
import { templateInput } from './single-templates';
import { inspectStoryboardDraft } from './creative/production-state';
import ImageOutputSettings from './ImageOutputSettings';
import StoryboardOutputSettings from './creative/StoryboardOutputSettings';

// Hypothetical contracts only. These specs are not enabled for any production model.
const video: Model = { id:'fixture-video', name:'Fixture', kind:'video', provider:'ark', downloaded:false, loaded:false, weights_mb:0,
  submittability:{submittable:true,reasons:[]}, capabilities:{input_modes:['text_to_video','image_to_video'], ratios:['16:9','9:16'],resolutions:['720p','1080p'],duration_policy:{kind:'range',min:4,max:12,step:1},
    parameter_combinations:[
      {generation_mode:['text_to_video'],ratio:['16:9'],resolution:['720p'],duration:{min:4,max:12,step:2},generate_audio:[false]},
      {generation_mode:['image_to_video'],ratio:['16:9','9:16'],resolution:['720p'],duration:[5,10],generate_audio:[false]},
      {generation_mode:['image_to_video'],ratio:['16:9'],resolution:['1080p'],duration:[5],generate_audio:[false]},
    ]}};
const image: Model = {...video,id:'fixture-image',kind:'image',capabilities:{input_modes:['text_to_image','image_to_image'],ratios:['1:1','16:9'],sizes:['1024x1024','2048x2048','2688x1536'],parameter_combinations:[
  {generation_mode:['text_to_image'],size:['1024x1024','2048x2048','2688x1536']},
  {generation_mode:['image_to_image'],size:['1024x1024']},
]}};
const exact={generation_mode:'image_to_video',ratio:'16:9',resolution:'1080p',duration:5,generate_audio:false};
const withRules=(rules:unknown):Model=>({...video,capabilities:{...video.capabilities,parameter_combinations:rules as ParameterCombination[]}});

describe('strict server combination contract',()=>{
  it.each([null,undefined,[],{},[{}],[{future:['x']}],[{duration:['5']}],[{duration:[true]}],[{generate_audio:[0]}],[{size:[' ']}],[{duration:{min:4,max:9,step:2}}],[{duration:{min:4,max:8,step:2,extra:0}}]])('closes malformed explicit metadata %j',rules=>{
    const model=withRules(rules);
    expect(combinationRules(model)).toBeNull();
    expect(getSupportedRatios(model)).toEqual([]);
    expect(validateSpecCombination(model,exact)).toMatch('暂不可用');
    expect(getModelUnavailableReason(model)).toMatch('暂不可用');
  });
  it('preserves legacy independent capabilities when field is absent',()=>{
    const {parameter_combinations: omitted,...caps}=video.capabilities!;void omitted;
    const legacy={...video,capabilities:caps};
    expect(getSupportedRatios(legacy,{generation_mode:'text_to_video'})).toEqual(['16:9','9:16']);
    expect(validateSpecCombination(legacy,{})).toBeNull();
  });
  it('requires every constrained field in one whole row, without mixing rows',()=>{
    expect(validateSpecCombination(video,exact)).toBeNull();
    for(const change of [{duration:10},{ratio:'9:16'},{duration:'5'},{generate_audio:0},{generate_audio:undefined},{generation_mode:undefined}]) expect(validateSpecCombination(video,{...exact,...change})).not.toBeNull();
  });
  it('does not mutate inputs or add capabilities not in base lists',()=>{
    const old=JSON.stringify(video);
    expect(getSupportedResolutions(withRules([{resolution:['4K']}]),{})).toEqual([]);
    filterSpecOptions(video,'duration',[5,10],exact);
    expect(JSON.stringify(video)).toBe(old);
  });
});

describe('upstream-to-downstream options',()=>{
  it('filters video ratios by mode, resolution by ratio, duration by the selected resolution',()=>{
    expect(getSupportedRatios(video,{generation_mode:'text_to_video',generate_audio:false})).toEqual(['16:9']);
    expect(getSupportedResolutions(video,{generation_mode:'image_to_video',ratio:'9:16',generate_audio:false})).toEqual(['720p']);
    expect(getSupportedDurations(video,{...exact,duration:10})).toEqual([5]);
    expect(getSupportedDurations(video,{generation_mode:'text_to_video',ratio:'16:9',resolution:'720p',generate_audio:false})).toEqual([4,6,8,10,12]);
  });
  it('offers a way out of stale downstream options and retains the chosen value until the user changes it',()=>{
    const stale={...exact,duration:10,ratio:'9:16'};
    expect(getSupportedRatios(video,{generation_mode:stale.generation_mode,generate_audio:false})).toContain('16:9');
    expect(getSupportedResolutions(video,{generation_mode:stale.generation_mode,ratio:stale.ratio,generate_audio:false})).toEqual(['720p']);
    expect(stale).toEqual({...exact,duration:10,ratio:'9:16'});
  });
  it('restricts image editing sizes without losing text-to-image sizes',()=>{
    expect(getImageSizesForRatio(image,'1:1',{generation_mode:'image_to_image'})).toEqual(['1024x1024']);
    expect(getImageSizesForRatio(image,'1:1',{generation_mode:'text_to_image'})).toEqual(['1024x1024','2048x2048']);
  });
});

describe('all generation entry points guard combinations',()=>{
  const opts={kind:'video' as const,imageMode:'text_to_image' as const,videoMode:'text_to_video' as const,modelId:video.id,model:video,prompt:'A cup',ratio:'16:9',duration:6,resolution:'720p'};
  it('quick workbench rejects independently valid but unsupported combined settings',()=>{
    expect(buildGenerationInput(opts).params.duration).toBe(6);
    expect(()=>buildGenerationInput({...opts,duration:5})).toThrow('不兼容');
    expect(()=>buildGenerationInput({...opts,kind:'image',model:image,modelId:image.id,imageMode:'image_to_image',ratio:'1:1',imageSize:'2048x2048'})).toThrow();
  });
  it('sends image ratio with rules when it is constrained, keeps legacy payload stable',()=>{
    const model={...image,capabilities:{...image.capabilities,parameter_combinations:[{generation_mode:['text_to_image'],ratio:['1:1'],size:['1024x1024']}]}};
    expect(buildGenerationInput({...opts,kind:'image',model,modelId:model.id,ratio:'1:1',imageSize:'1024x1024'}).params).toMatchObject({ratio:'1:1',size:'1024x1024'});
  });
  it('node model switches choose one compatible combination while keeping the creative content',()=>{
    const config=withNodeModel({prompt:'Slow push',title:'Cup',generation_mode:'text_to_video',params:{ratio:'9:16',resolution:'1080p',duration:5}},video);
    expect(config).toMatchObject({prompt:'Slow push',title:'Cup',params:{ratio:'16:9',resolution:'720p',duration:4}});
    expect(nodeGenerationIssue('video',config,video)).toBeNull();
    expect(nodeGenerationIssue('video',{...config,params:{...(config.params as object),duration:5}},video)).toMatch('不兼容');
  });
  it('canonical node fields survive save without mutating a stored draft or changing its mode',()=>{
    const stored={generation_mode:'image_to_video',prompt:'Cup',model_id:video.id,input_asset_id:'cup',params:{ratio:'16:9',resolution:'1080p',duration:5}};
    const output=nodeExecutionConfig(stored,video);
    expect(output).toMatchObject({generation_mode:'image_to_video',params:{ratio:'16:9',resolution:'1080p',duration:5,generate_audio:false}});
    expect(output.params).not.toHaveProperty('generation_mode');
    expect(stored.params).not.toHaveProperty('generate_audio');
  });
  it('template requests cannot bypass a size limit through defaults',()=>{
    const template={template_id:'fixture',latest_version:1,kind:'image' as const,name:'fixture',category:'test',status:'active',fields:[],required_assets:[{required:true}],default_params:{},compatible_model_ids:[image.id]};
    const selection={template,fields:{},conceptAcknowledged:true};
    const params={ratio:'1:1',imageSize:'2048x2048',duration:5,resolution:'720p',reference:'cup',prompt:'Cup'};
    expect(()=>templateInput(selection,image,params)).toThrow('不兼容');
    expect(templateInput(selection,image,{...params,imageSize:'1024x1024'}).params.generation_mode).toBe('image_to_image');
  });
  it('legacy storyboard request respects input mode and size constraints',()=>{
    const draft={key:'one',description:'Cup',motion:'',prompt:'Cup',input_asset_id:'cup',duration:'5'};
    expect(inspectStoryboardDraft(draft,{imageModelId:image.id,imageSize:'2048x2048'},[image]).error).toMatch('不兼容');
    expect(inspectStoryboardDraft(draft,{imageModelId:image.id,imageSize:'1024x1024'},[image]).error).toBeNull();
  });
  it('refreshing capabilities makes a frozen quote unusable without changing its input',()=>{
    const old={...exact};
    expect(validateGenerationSpecs(video,'video',old)).toBeNull();
    expect(validateGenerationSpecs(withRules([{...video.capabilities!.parameter_combinations![1]}]),'video',old)).toMatch('清晰度');
    expect(validateGenerationSpecs({...video,capabilities:{...video.capabilities,resolutions:['720p']}},'video',old)).toMatch('清晰度');
    expect(old).toEqual(exact);
  });
});

describe('image controls show filtered tiers and keep a stale saved size visible',()=>{
  it('both quick and storyboard controls hide unsupported 2K editing choices',()=>{
    const selection={generation_mode:'image_to_image'};
    const quick=renderToStaticMarkup(<ImageOutputSettings model={image} selection={selection} ratio="1:1" imageSize="2048x2048" count={1} onRatioChange={()=>{}} onSizeChange={()=>{}} onCountChange={()=>{}}/>);
    const board=renderToStaticMarkup(<StoryboardOutputSettings model={image} selection={selection} size="2048x2048" onChange={()=>{}}/>);
    expect(quick).toContain('1K');expect(quick).not.toContain('>2K<');
    expect(board).toContain('2048 × 2048');expect(board).not.toContain('>2K<');
  });
});
