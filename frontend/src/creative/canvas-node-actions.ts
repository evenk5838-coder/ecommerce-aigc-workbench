import { ApiError } from '../api';
import { parseSavedCanvas, type CanvasEdge, type CanvasNodeRecord, type SavedCanvasLayout } from './canvas-layout-sync';

const object = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const id = (v: unknown): v is string => typeof v === 'string' && v.length > 0;
export interface NodeQuote {
  quote_id: string; project_id: string; node_id: string; config_revision: number;
  estimated_cost_cny: number; expires_at: number;
  input: {kind:'image'|'video'; model_id:string; params:Record<string,unknown>; input_asset_id?:string|null};
}
export interface NodeExecution {
  execution_id: string; project_id: string; node_id: string; quote_id: string; client_request_id: string;
  task_id: string; status: string; stage: string|null; progress: number|null; active: boolean;
  output_asset_id: string|null; asset_id: string|null; asset: CanvasNodeRecord['asset'];
  error: {code:string;message:string|null}|null;
  estimated_cost_cny: number|null; cost_cny: number|null; created_at: number; finished_at: number|null;
  config_revision: number; input_hash: string; input: NodeQuote['input']|null;
  last_successful_execution?: NodeExecution|null;
}
export interface NodeGenerationReceipt extends NodeExecution { idempotent_replay:boolean }
export interface DeletionOperation { operation_id:string; status:'deleted'|'restored'; node_ids:string[]; created_at:number }
export interface PendingNodeGeneration {nodeId:string; quoteId:string; requestId:string}
const pendingKey = (projectId:string) => `material-node-request-v1:${projectId}`;
export function readPendingGeneration(projectId:string):PendingNodeGeneration|null {
  try {const v:unknown=JSON.parse(localStorage.getItem(pendingKey(projectId))??'null');return object(v)&&id(v.nodeId)&&id(v.quoteId)&&id(v.requestId)?v as unknown as PendingNodeGeneration:null;}catch{return null;}
}
export function storePendingGeneration(projectId:string,value:PendingNodeGeneration|null):void {
  if(value)localStorage.setItem(pendingKey(projectId),JSON.stringify(value));else localStorage.removeItem(pendingKey(projectId));
}

/** Display the saved input reference; never invent or persist workflow edges. */
export function deriveCanvasReferenceEdges(nodes:readonly CanvasNodeRecord[],visibleIds?:ReadonlySet<string>,preferredEdges:readonly CanvasEdge[]=[]):CanvasEdge[] {
  const visible=nodes.filter(node=>!visibleIds||visibleIds.has(node.node_id));
  const byId=(a:CanvasNodeRecord,b:CanvasNodeRecord)=>a.node_id<b.node_id?-1:a.node_id>b.node_id?1:0;
  const sources=visible.filter(node=>node.type==='asset'||node.type==='image')
    .sort((a,b)=>Number(a.type!=='asset')-Number(b.type!=='asset')||byId(a,b));
  return visible.slice().sort(byId).flatMap(node=>{
    const mode=node.config.generation_mode,reference=node.config.input_asset_id;
    if(!id(reference)||!(node.type==='image'&&mode==='image_to_image'||node.type==='video'&&mode==='image_to_video'))return [];
    const matches=(candidate:CanvasNodeRecord)=>candidate.node_id!==node.node_id
      &&(candidate.type==='asset'?candidate.binding.asset_id:candidate.asset?.id)===reference;
    const source=sources.find(candidate=>matches(candidate)&&preferredEdges.some(edge=>edge.to===node.node_id&&edge.from===candidate.node_id))??sources.find(matches);
    return source?[{from:source.node_id,to:node.node_id}]:[];
  });
}
const finite = (value:unknown):value is number => typeof value==='number'&&Number.isFinite(value);
const nullableNumber = (value:unknown):boolean => value===null||finite(value);
const nullableId = (value:unknown):boolean => value===null||id(value);
function execution(value:unknown):value is NodeExecution {
  return object(value)&&['execution_id','project_id','node_id','quote_id','client_request_id','task_id','status','input_hash'].every(key=>id(value[key]))
    &&typeof value.active==='boolean'&&(value.stage===null||typeof value.stage==='string')
    &&nullableNumber(value.progress)&&nullableNumber(value.estimated_cost_cny)&&nullableNumber(value.cost_cny)
    &&finite(value.created_at)&&nullableNumber(value.finished_at)&&Number.isSafeInteger(value.config_revision)&&Number(value.config_revision)>=1
    &&nullableId(value.output_asset_id)&&nullableId(value.asset_id)
    &&(value.asset===null||object(value.asset)&&id(value.asset.id)&&['image','video'].includes(String(value.asset.kind)))
    &&(value.error===null||object(value.error)&&id(value.error.code)&&(value.error.message===null||typeof value.error.message==='string'))
    &&(value.input===null||object(value.input)&&['image','video'].includes(String(value.input.kind))&&id(value.input.model_id)&&object(value.input.params));
}

/** Status reads must never replace the layout/controller baseline or an editor's saved config. */
export function mergeCanvasExecutionState(saved:SavedCanvasLayout|null, state:SavedCanvasLayout|null, accepted:NodeExecution|null=null):SavedCanvasLayout|null {
  if(!saved)return null;
  const remote=new Map(state?.projectId===saved.projectId?state.nodes.map(node=>[node.node_id,node]):[]);
  return {...saved,nodes:saved.nodes.map(node=>{
    let result=node;
    const incoming=remote.get(node.node_id),current=node.last_execution,next=incoming?.last_execution;
    const sameExecution=!!current?.execution_id&&current.execution_id===next?.execution_id;
    const newerExecution=!!current?.execution_id&&!!next?.execution_id&&finite(current.created_at)&&finite(next.created_at)&&next.created_at>current.created_at;
    // An older layout response cannot attach an old task to a deleted/recreated node.
    const sameIdentity=incoming?.type===node.type&&(state!.revision>=saved.revision||sameExecution||newerExecution);
    const olderExecution=finite(current?.created_at)&&finite(next?.created_at)&&next.created_at<current.created_at;
    const terminalRegression=sameExecution&&current?.active===false&&next?.active===true;
    const progressRegression=sameExecution&&current?.active===true&&next?.active===true&&finite(current?.progress)&&finite(next?.progress)&&next.progress<current.progress;
    if(sameIdentity&&!olderExecution&&!terminalRegression&&!progressRegression&&(!current?.execution_id||!!next?.execution_id)) {
      result={...node,last_execution:next??node.last_execution,asset:incoming!.asset??node.asset};
    }
    if(accepted?.project_id===saved.projectId&&accepted.node_id===node.node_id&&['image','video'].includes(node.type)&&result.last_execution?.execution_id!==accepted.execution_id
      &&(!finite(result.last_execution?.created_at)||result.last_execution.created_at<=accepted.created_at)) {
      const previous=result.last_execution;
      result={...result,last_execution:{...accepted,last_successful_execution:accepted.last_successful_execution
        ??(previous?.status==='succeeded'?previous:previous?.last_successful_execution)??null},asset:accepted.asset??result.asset};
    }
    return result;
  })};
}

/** A fresh business GET acknowledges the receipt; deleted nodes need no display recovery. */
export function hasObservedExecution(state:SavedCanvasLayout,accepted:NodeExecution,submittedLayoutRevision:number):boolean {
  if(state.projectId!==accepted.project_id)return false;
  const node=state.nodes.find(item=>item.node_id===accepted.node_id);
  if(!node)return true;
  if(node.last_execution?.execution_id===accepted.execution_id)return true;
  if(finite(node.last_execution?.created_at)&&node.last_execution.created_at>accepted.created_at)return true;
  return !node.last_execution&&state.revision>submittedLayoutRevision;
}
export function createNodeActions(fetcher:typeof fetch=(...args)=>fetch(...args)) {
  const path=(projectId:string)=>`/api/projects/${encodeURIComponent(projectId)}/canvas`;
  async function request(url:string,body?:unknown,signal?:AbortSignal):Promise<unknown>{
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),20000);
    const abort=()=>controller.abort();signal?.addEventListener('abort',abort,{once:true});
    try{
      if(signal?.aborted)throw new Error('aborted');
      const response=await fetcher(url,{method:body===undefined?'GET':'POST',signal:controller.signal,...(body===undefined?{}:{headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})});
      let value:unknown;
      try{value=await response.json();}catch{if(response.ok)throw new Error('invalid receipt');}
      if(!response.ok){const error=object(value)&&object(value.error)?value.error:{};throw new ApiError(id(error.code)?error.code:'HTTP_ERROR',id(error.message)?error.message:'暂时无法完成，请重新核对。',response.status);}
      return value;
    }catch(error){if(error instanceof ApiError)throw error;throw new ApiError(body===undefined?'NODE_STATE_READ_FAILED':'NODE_RESULT_UNKNOWN',body===undefined?'任务进度暂未更新，正在重新查询。':'连接暂时中断，请核对原操作结果。',0);}finally{clearTimeout(timer);signal?.removeEventListener('abort',abort);}
  }
  return {
    async quote(projectId:string,nodeId:string,revision:number):Promise<NodeQuote>{
      const value=await request(`${path(projectId)}/nodes/${encodeURIComponent(nodeId)}/quote`,{expected_config_revision:revision});
      if(!object(value)||!id(value.quote_id)||value.project_id!==projectId||value.node_id!==nodeId||value.config_revision!==revision||!Number.isSafeInteger(value.config_revision)||typeof value.estimated_cost_cny!=='number'||!Number.isFinite(value.estimated_cost_cny)||value.estimated_cost_cny<0||typeof value.expires_at!=='number'||!Number.isFinite(value.expires_at)||!object(value.input)||!['image','video'].includes(String(value.input.kind))||!id(value.input.model_id)||!object(value.input.params))throw new ApiError('NODE_QUOTE_INVALID','生成设置暂未核对完整，请重新读取。',0);
      return value as unknown as NodeQuote;
    },
    async generate(projectId:string,pending:PendingNodeGeneration):Promise<NodeGenerationReceipt>{
      const value=await request(`${path(projectId)}/nodes/${encodeURIComponent(pending.nodeId)}/generate`,{quote_id:pending.quoteId,client_request_id:pending.requestId,user_action:'confirm_generate'});
      if(!execution(value)||value.project_id!==projectId||value.node_id!==pending.nodeId||value.quote_id!==pending.quoteId||value.client_request_id!==pending.requestId||!object(value)||typeof value.idempotent_replay!=='boolean')throw new ApiError('NODE_RESULT_UNKNOWN','生成结果需要核对，请恢复原操作。',0);
      return {...value,idempotent_replay:value.idempotent_replay};
    },
    async readState(projectId:string,signal?:AbortSignal):Promise<SavedCanvasLayout>{
      const state=parseSavedCanvas(await request(path(projectId),undefined,signal),projectId);
      for(const node of state.nodes){
        const last=node.last_execution;
        if(last?.execution_id&&(!execution(last)||last.project_id!==projectId||last.node_id!==node.node_id))throw new ApiError('NODE_STATE_READ_FAILED','任务进度暂未核对完整，正在重新查询。',0);
      }
      return state;
    },
    async deletions(projectId:string):Promise<DeletionOperation[]>{
      const value=await request(`${path(projectId)}/deletions`);
      if(!object(value)||value.project_id!==projectId||!Array.isArray(value.operations)||!value.operations.every(v=>object(v)&&id(v.operation_id)&&['deleted','restored'].includes(String(v.status))&&Array.isArray(v.node_ids)&&v.node_ids.every(id)&&typeof v.created_at==='number'))throw new ApiError('CANVAS_HISTORY_INVALID','删除记录暂时无法读取。',0);
      return value.operations as DeletionOperation[];
    },
    async undo(projectId:string,operationId:string,revision:number):Promise<void>{
      const value=await request(`${path(projectId)}/deletions/${encodeURIComponent(operationId)}/undo`,{expected_layout_revision:revision});
      if(!object(value)||value.project_id!==projectId||value.operation_id!==operationId||typeof value.already_restored!=='boolean')throw new ApiError('NODE_RESULT_UNKNOWN','恢复结果需要重新核对。',0);
    },
  };
}
export const nodeActions=createNodeActions();
