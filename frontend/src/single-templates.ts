import { validateSpecCombination } from './model-specs';
import { ApiError } from './api';
import { validateModelSelection, validateVideoDuration, getImageSizesForRatio, getSupportedRatios, getSupportedResolutions } from './capabilities';
import type { Model, TaskInput } from './types';
export interface SingleTemplateField { key:string; label:string; type:string; required?:boolean; options?:(string|number)[]; allow_custom?:boolean; custom_key?:string; placeholder?:string; param_key?:string }
export interface SingleTemplate { template_id:string; latest_version:number; kind:'image'|'video'; name:string; category:string; status:string; description?:string; fields:SingleTemplateField[]; required_assets:{required?:boolean}[]; default_params:Record<string,unknown>; compatible_model_ids:string[] }
export interface SingleTemplateSelection { template:SingleTemplate; fields:Record<string,string>; conceptAcknowledged:boolean }
const record=(x:unknown):x is Record<string,unknown>=>!!x&&typeof x==='object'&&!Array.isArray(x);
export function parseSingleTemplates(raw:unknown):SingleTemplate[]{
 if(!record(raw)||!Array.isArray(raw.items))throw new ApiError('TEMPLATE_RESPONSE_INVALID','模板列表暂不可用，请继续手动创作。',0);
 return raw.items.map(x=>{if(!record(x)||typeof x.template_id!=='string'||typeof x.name!=='string'||!Number.isSafeInteger(x.latest_version)||(x.latest_version as number)<1||!['image','video'].includes(String(x.kind))||!Array.isArray(x.fields)||!x.fields.every(f=>record(f)&&typeof f.key==='string'&&typeof f.label==='string'&&typeof f.type==='string'&&(f.options===undefined||Array.isArray(f.options)&&f.options.every(option=>typeof option==='string'||typeof option==='number'&&Number.isFinite(option)))&&(f.required===undefined||typeof f.required==='boolean')&&(f.param_key===undefined||typeof f.param_key==='string'))||(!Array.isArray(x.required_assets)||!x.required_assets.every(asset=>record(asset)&&(asset.required===undefined||typeof asset.required==='boolean')))||!record(x.default_params)||!Array.isArray(x.compatible_model_ids)||!x.compatible_model_ids.every(v=>typeof v==='string'))throw new ApiError('TEMPLATE_RESPONSE_INVALID','模板数据不完整，请重新读取。',0);return x as unknown as SingleTemplate;});
}
export async function listSingleTemplates(kind:'image'|'video',signal:AbortSignal):Promise<SingleTemplate[]>{
 const r=await fetch('/api/templates?kind='+kind,{signal});const body=await r.json();if(!r.ok)throw new ApiError(body.error?.code||'TEMPLATE_UNAVAILABLE',body.error?.message||'模板读取失败',r.status);return parseSingleTemplates(body).filter(t=>t.kind===kind&&t.status==='active');
}
export function templateInput(selection:SingleTemplateSelection,model:Model|undefined,params:{ratio:string;imageSize:string;duration:number;resolution:string;reference:string|null;prompt:string}):Omit<TaskInput,'client_request_id'>{
 const t=selection.template;const error=validateModelSelection(model,t.kind);if(error||!model)throw new Error(error||'请选择模型');if(!t.compatible_model_ids.includes(model.id))throw new Error('当前模型与模板不兼容，请重新选择。');
 const concept=t.template_id==='tpl_season_theme'&&!params.reference;
 if(concept&&!selection.conceptAcknowledged)throw new Error('请确认未使用商品原图的概念图不代表真实商品。');
 if(!concept&&(t.kind==='video'||t.required_assets.some(a=>a.required))&&!params.reference)throw new Error('此模板需要商品参考图，请先上传或选择。');
 for(const f of t.fields){if(f.param_key)continue;const value=selection.fields[f.key]?.trim();if(f.required&&!value)throw new Error('请填写'+f.label);if(value==='自定义'&&f.custom_key&&!selection.fields[f.custom_key]?.trim())throw new Error('请填写'+f.label+'的自定义内容');}
 const values:Record<string,unknown>={};
 if(t.kind==='image'){if(!getImageSizesForRatio(model,params.ratio).includes(params.imageSize))throw new Error('请为当前比例选择清晰度。');values.size=params.imageSize;}
 else {const durationError=validateVideoDuration(model,params.duration);if(durationError)throw new Error(durationError);if(!getSupportedRatios(model).includes(params.ratio)||!getSupportedResolutions(model).includes(params.resolution))throw new Error('请重新选择当前模型支持的视频规格。');values.duration=params.duration;values.ratio=params.ratio;values.resolution=params.resolution;}
 const generation_mode=t.kind==='image'?(params.reference?'image_to_image':'text_to_image'):'image_to_video';
 const exact={...t.default_params,...values,ratio:params.ratio,generation_mode,...(t.kind==='video'?{generate_audio:t.default_params.generate_audio??false}:{})};
 const specError=validateSpecCombination(model,exact);if(specError)throw new Error(specError);
 if(model.capabilities?.parameter_combinations){values.ratio=params.ratio;values.generation_mode=generation_mode;if(t.kind==='video')values.generate_audio=exact.generate_audio;}
 if(params.prompt.trim())values.prompt=params.prompt;
 return {kind:t.kind,model_id:model.id,input_asset_id:params.reference,template_id:t.template_id,template_version:t.latest_version,template_fields:{...selection.fields},params:values};
}
