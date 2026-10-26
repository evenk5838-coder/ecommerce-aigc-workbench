import { ApiError } from '../api';
import { isRecord, isText } from './production-guards';
import type { CreativeRequestOptions } from './types';
export interface DirectorAction { type:'confirm_script'|'apply_shot_plan'|'apply_brief'|'quote_generation'; target_id?:string; target_version?:number; proposal_id?:string; proposal_version?:number; requires_user_action:true }
export interface DirectorArtifact {type:string;proposal_id:string;proposal_version:number;target_id:string|null;target_version:number|null;persistence:'saved';confirmed:false}
export interface DirectorTurn {turn_id:string;request_id:string;user:string;reply:string;reply_type:string;created_at:number;structured:Record<string,unknown>}
export interface DirectorMessages {project_id:string;session_id:string|null;workflow_revision:number;actions:DirectorAction[];items:DirectorTurn[];next_cursor:string|null;has_more:boolean}
export interface DirectorReply {session_id:string;request_id:string;request_status:'completed';reply:string;reply_type:string;artifacts:DirectorArtifact[];actions:DirectorAction[];workflow_revision:number}
export interface DirectorProposal {proposal_id:string;kind:string;version:number;status:string;target_id:string|null;target_version:number|null;content:Record<string,unknown>}
export interface DirectorPendingRequest {id:string;message:string;session_id?:string;revision?:number}
const validVersion=(x:unknown):x is number=>typeof x==='number'&&Number.isSafeInteger(x)&&x>=0;
const invalid=():never=>{throw new ApiError('DIRECTOR_RESPONSE_INVALID','导演回复暂时无法读取，请恢复会话核对。',0);};
export function parseDirectorActions(value:unknown):DirectorAction[]{
 if(!Array.isArray(value))return invalid();
 return value.map(a=>{
  if(!isRecord(a)||!['confirm_script','apply_shot_plan','apply_brief','quote_generation'].includes(String(a.type))||a.requires_user_action!==true)return invalid();
  if(a.type==='confirm_script'&&(!isText(a.target_id)||!validVersion(a.target_version)||a.target_version<1))return invalid();
  if(['apply_shot_plan','apply_brief'].includes(String(a.type))&&(!isText(a.proposal_id)||!validVersion(a.proposal_version)||a.proposal_version<1))return invalid();
  return a as unknown as DirectorAction;
 });
}
export function parseDirectorReply(value:unknown,requestId:string):DirectorReply{
 if(!isRecord(value)||value.request_id!==requestId||value.request_status!=='completed'||!isText(value.session_id)||typeof value.reply!=='string'||typeof value.reply_type!=='string'||!validVersion(value.workflow_revision)||!Array.isArray(value.artifacts))return invalid();
 for(const a of value.artifacts){if(!isRecord(a)||a.persistence!=='saved'||a.confirmed!==false||!isText(a.type)||!isText(a.proposal_id)||!validVersion(a.proposal_version))return invalid();if(a.type==='brief'){if(!(a.target_id===null||isText(a.target_id))||!(a.target_version===null||validVersion(a.target_version)))return invalid();}else if(!isText(a.target_id)||!validVersion(a.target_version)||a.target_version<1)return invalid();}
 return {...value,actions:parseDirectorActions(value.actions)} as unknown as DirectorReply;
}
export function parseDirectorMessages(value:unknown,projectId:string):DirectorMessages{
 if(!isRecord(value)||value.project_id!==projectId||!(value.session_id===null||isText(value.session_id))||!validVersion(value.workflow_revision)||!Array.isArray(value.items)||typeof value.has_more!=='boolean'||!(value.next_cursor===null||isText(value.next_cursor)))return invalid();
 for(const t of value.items)if(!isRecord(t)||!isText(t.turn_id)||!isText(t.request_id)||typeof t.user!=='string'||typeof t.reply!=='string'||typeof t.reply_type!=='string'||typeof t.created_at!=='number'||!isRecord(t.structured))return invalid();
 return {...value,actions:parseDirectorActions(value.actions)} as unknown as DirectorMessages;
}
export function createProjectDirectorApi(fetcher:typeof fetch=(...args)=>fetch(...args)){
 async function request(path:string,method:string,body:unknown,options:CreativeRequestOptions={}){
  if(options.signal?.aborted)throw new ApiError('REQUEST_ABORTED','已停止等待。',0);
  const c=new AbortController(),abort=()=>c.abort();options.signal?.addEventListener('abort',abort,{once:true});
  const timer=setTimeout(abort,options.timeoutMs??(method==='POST'?120000:20000));
  let known=false;
  try{
   const response=await fetcher('/api'+path,{method,signal:c.signal,...(body!==undefined?{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)}:{})});
   known=!response.ok&&response.status<500;
   const data:unknown=await response.json();
   if(!response.ok){const e=isRecord(data)&&isRecord(data.error)?data.error:{};throw new ApiError(isText(e.code)?e.code:'HTTP_ERROR',isText(e.message)?e.message:'导演服务暂时无法响应。',response.status,e.detail);}
   return data;
  }catch(error){
   if(known&&error instanceof ApiError)throw error;
   if(method!=='GET')throw new ApiError('DIRECTOR_RESULT_UNKNOWN','还未收到完整结果，请恢复会话核对。',0);
   if(error instanceof ApiError)throw error;
   throw new ApiError('DIRECTOR_READ_FAILED','暂时无法读取导演对话，请稍后重试。',0);
  }finally{clearTimeout(timer);options.signal?.removeEventListener('abort',abort);}
 }
 const base=(id:string)=>{if(!isText(id))throw new ApiError('PROJECT_REQUIRED','请先选择项目。',0);return '/projects/'+encodeURIComponent(id)+'/director';};
 return {
  async proposals(id:string,options?:CreativeRequestOptions){const v=await request(base(id)+'/proposals','GET',undefined,options);if(!isRecord(v)||!Array.isArray(v.items))return invalid();for(const p of v.items)if(!isRecord(p)||!isText(p.proposal_id)||!isText(p.kind)||!validVersion(p.version)||!isText(p.status)||!isRecord(p.content))return invalid();return v.items as unknown as DirectorProposal[];},
  async messages(id:string,cursor?:string,options?:CreativeRequestOptions){return parseDirectorMessages(await request(base(id)+'/messages'+(cursor?'?cursor='+encodeURIComponent(cursor):''),'GET',undefined,options),id);},
  async chat(id:string,p:DirectorPendingRequest,options?:CreativeRequestOptions){return parseDirectorReply(await request(base(id)+'/chat','POST',{client_request_id:p.id,message:p.message,session_id:p.session_id,expected_workflow_revision:p.revision},options),p.id);},
  async receipt(id:string,requestId:string,options?:CreativeRequestOptions){const v=await request(base(id)+'/requests/'+encodeURIComponent(requestId),'GET',undefined,options);if(!isRecord(v)||v.client_request_id!==requestId||!['pending','completed','failed'].includes(String(v.status)))return invalid();return {status:v.status as 'pending'|'completed'|'failed',error_code:typeof v.error_code==='string'?v.error_code:undefined,response:v.status==='completed'?parseDirectorReply(v.response,requestId):null};},
  async apply(id:string,action:DirectorAction,requestId:string,options?:CreativeRequestOptions){if(!action.proposal_id||!action.proposal_version)return invalid();const v=await request(base(id)+'/proposals/'+encodeURIComponent(action.proposal_id)+'/apply','POST',{expected_version:action.proposal_version,client_request_id:requestId,user_action:'apply_proposal'},options);if(!isRecord(v)||v.proposal_id!==action.proposal_id||typeof v.applied!=='boolean')return invalid();return v;},
 };
}
export const projectDirectorApi=createProjectDirectorApi();
