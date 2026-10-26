import {describe,it,expect} from 'vitest';
import {parseSingleTemplates,templateInput} from './single-templates';
import type {SingleTemplateSelection} from './single-templates';
import type {Model} from './types';
const model:Model={id:'flash',name:'Flash',provider:'ark',kind:'image',downloaded:true,loaded:false,weights_mb:0,submittability:{submittable:true,reasons:[]},capabilities:{ratios:['1:1'],sizes:['2048x2048']}};
const selection:SingleTemplateSelection={template:{template_id:'tpl_scene_bg',latest_version:2,name:'场景',kind:'image',category:'work',status:'active',fields:[{key:'scene',label:'场景',type:'select',required:true,options:['木桌','自定义'],custom_key:'scene_custom'}],required_assets:[{required:true}],default_params:{size:'2048x2048'},compatible_model_ids:['flash']},fields:{scene:'木桌'},conceptAcknowledged:false};
const values={ratio:'1:1',imageSize:'2048x2048',duration:5,resolution:'720p',reference:'asset_product',prompt:''};
describe('single-use template request boundaries',()=>{
 it('pins the chosen version and leaves prompt composition to the backend',()=>{const input=templateInput(selection,model,values);expect(input).toMatchObject({template_id:'tpl_scene_bg',template_version:2,template_fields:{scene:'木桌'},input_asset_id:'asset_product',params:{size:'2048x2048'}});expect(input.params).not.toHaveProperty('prompt');selection.fields.scene='changed';expect(input.template_fields?.scene).toBe('木桌');selection.fields.scene='木桌';});
 it('does not silently replace an explicit prompt',()=>{expect(templateInput(selection,model,{...values,prompt:'  自己的描述  '}).params.prompt).toBe('  自己的描述  ');});
 it('rejects missing reference, incompatible model and unsupported size before estimates',()=>{expect(()=>templateInput(selection,model,{...values,reference:null})).toThrow('参考图');expect(()=>templateInput(selection,{...model,id:'other'},values)).toThrow('不兼容');expect(()=>templateInput(selection,model,{...values,imageSize:'4096x4096'})).toThrow('清晰度');});
 it('requires acknowledgement of concept mode only when no product image is supplied',()=>{const concept={...selection,template:{...selection.template,template_id:'tpl_season_theme'}};expect(()=>templateInput(concept,model,{...values,reference:null})).toThrow('概念图');expect(templateInput({...concept,conceptAcknowledged:true},model,{...values,reference:null}).input_asset_id).toBeNull();expect(templateInput(concept,model,values).input_asset_id).toBe('asset_product');});
 it('keeps required custom field validation and blocks bad list responses',()=>{expect(()=>templateInput({...selection,fields:{scene:'自定义'}},model,values)).toThrow('自定义');expect(()=>parseSingleTemplates({items:[{}]})).toThrow('不完整');expect(parseSingleTemplates({items:[selection.template]})).toEqual([selection.template]);});
});
