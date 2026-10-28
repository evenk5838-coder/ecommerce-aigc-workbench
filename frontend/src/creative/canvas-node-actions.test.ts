import {describe,expect,it,vi} from 'vitest';
import {createNodeActions,deriveCanvasReferenceEdges,hasObservedExecution,mergeCanvasExecutionState,type NodeExecution} from './canvas-node-actions';
import {parseSavedCanvas,type CanvasNodeRecord,type SavedCanvasLayout} from './canvas-layout-sync';

const response=(value:unknown,status=200)=>new Response(JSON.stringify(value),{status,headers:{'Content-Type':'application/json'}});
const receipt=(changes:Partial<NodeExecution>={})=>({
 execution_id:'e',project_id:'p',node_id:'n',quote_id:'q',client_request_id:'r',task_id:'t',status:'queued',stage:'queued',progress:0,active:true,
 output_asset_id:null,asset_id:null,asset:null,error:null,estimated_cost_cny:.1,cost_cny:null,created_at:100,finished_at:null,
 config_revision:1,input_hash:'frozen-input',input:{kind:'image' as const,model_id:'model',params:{prompt:'保留这份提示词'}},idempotent_replay:false,...changes,
});
const canvas=(last:unknown=null,asset:unknown=null)=>({project_id:'p',schema_version:1,layout_revision:3,
 viewport:{x:10,y:20,zoom:.8},deleted_node_ids:['deleted-script'],
 nodes:[{node_id:'n',type:'image',position:{x:80,y:160},config:{prompt:'saved'},binding:{},config_revision:1,last_execution:last,asset}],edges:[],
});
const saved=(last:unknown=null,asset:unknown=null)=>parseSavedCanvas(canvas(last,asset),'p');
const referenceNode=(node_id:string,type:string,fields:Partial<CanvasNodeRecord>={}):CanvasNodeRecord=>({
 node_id,type,position:{x:0,y:0},config:{},binding:{},config_revision:1,last_execution:null,asset:null,...fields,
});
describe('saved canvas reference edges',()=>{
 it('maps imported images and actual generated image outputs to image/video inputs',()=>{
  const imported=referenceNode('upload','asset',{binding:{asset_id:'photo'}});
  const image=referenceNode('image','image',{config:{generation_mode:'image_to_image',input_asset_id:'photo'},asset:{id:'result',kind:'image'}});
  const video=referenceNode('video','video',{config:{generation_mode:'image_to_video',input_asset_id:'result'}});
  expect(deriveCanvasReferenceEdges([imported,image,video])).toEqual([{from:'upload',to:'image'},{from:'image',to:'video'}]);
 });
 it('does not connect text modes even when an earlier reference ID remains saved',()=>{
  const source=referenceNode('source','asset',{binding:{asset_id:'photo'}});
  const image=referenceNode('image','image',{config:{generation_mode:'text_to_image',input_asset_id:'photo'}});
  const video=referenceNode('video','video',{config:{generation_mode:'text_to_video',input_asset_id:'photo'}});
  expect(deriveCanvasReferenceEdges([source,image,video])).toEqual([]);
 });
 it('does not invent a source for an off-canvas reference or connect a node to itself',()=>{
  const missing=referenceNode('missing','video',{config:{generation_mode:'image_to_video',input_asset_id:'library-only'}});
  const self=referenceNode('self','image',{config:{generation_mode:'image_to_image',input_asset_id:'own-output'},asset:{id:'own-output',kind:'image'}});
  expect(deriveCanvasReferenceEdges([missing,self])).toEqual([]);
 });
 it('removes connections when either endpoint is deleted or filtered from the canvas',()=>{
  const source=referenceNode('source','asset',{binding:{asset_id:'photo'}});
  const target=referenceNode('target','video',{config:{generation_mode:'image_to_video',input_asset_id:'photo'}});
  expect(deriveCanvasReferenceEdges([source,target],new Set(['target']))).toEqual([]);
  expect(deriveCanvasReferenceEdges([source,target],new Set(['source']))).toEqual([]);
  expect(deriveCanvasReferenceEdges([target])).toEqual([]);
 });
 it('moves a connection when the saved reference changes and picks a stable imported source first',()=>{
  const image=referenceNode('0-generated','image',{asset:{id:'photo',kind:'image'}});
  const sourceB=referenceNode('b-upload','asset',{binding:{asset_id:'photo'}}),sourceA=referenceNode('a-upload','asset',{binding:{asset_id:'photo'}});
  const alternate=referenceNode('alternate','asset',{binding:{asset_id:'other-photo'}});
  const target=referenceNode('target','video',{config:{generation_mode:'image_to_video',input_asset_id:'photo'}});
  const nodes=[image,sourceB,sourceA,alternate,target],originalOrder=nodes.map(node=>node.node_id);
  expect(deriveCanvasReferenceEdges(nodes)).toEqual([{from:'a-upload',to:'target'}]);
  expect(deriveCanvasReferenceEdges([...nodes].reverse())).toEqual([{from:'a-upload',to:'target'}]);
  const changed={...target,config:{...target.config,input_asset_id:'other-photo'}};
  expect(deriveCanvasReferenceEdges([...nodes.slice(0,-1),changed])).toEqual([{from:'alternate',to:'target'}]);
  expect(nodes.map(node=>node.node_id)).toEqual(originalOrder);expect(target.config.input_asset_id).toBe('photo');
 });
 it('retains the chosen visible source when multiple nodes share one image asset',()=>{
  const first=referenceNode('a-upload','asset',{binding:{asset_id:'photo'}});
  const chosen=referenceNode('z-generated','image',{asset:{id:'photo',kind:'image'}});
  const wrong=referenceNode('wrong','asset',{binding:{asset_id:'different-photo'}});
  const target=referenceNode('target','video',{config:{generation_mode:'image_to_video',input_asset_id:'photo'}});
  const nodes=[first,chosen,wrong,target],preferred=[{from:'wrong',to:'target'},{from:'z-generated',to:'target'}];
  expect(deriveCanvasReferenceEdges(nodes,undefined,preferred)).toEqual([{from:'z-generated',to:'target'}]);
  expect(deriveCanvasReferenceEdges([...nodes].reverse(),undefined,preferred)).toEqual([{from:'z-generated',to:'target'}]);
  // Deletion/filtering must fall back to a valid source, never resurrect the chosen node.
  expect(deriveCanvasReferenceEdges(nodes,new Set(['a-upload','wrong','target']),preferred)).toEqual([{from:'a-upload',to:'target'}]);
  expect(deriveCanvasReferenceEdges([wrong,target],undefined,preferred)).toEqual([]);
  expect(preferred).toEqual([{from:'wrong',to:'target'},{from:'z-generated',to:'target'}]);
 });
});
describe('node action receipts',()=>{
 it('does not accept a quote for another node or a guessed fee',async()=>{
  const quote={quote_id:'q',project_id:'p',node_id:'other',config_revision:1,estimated_cost_cny:.1,expires_at:123,input:{kind:'image',model_id:'model',params:{}}};
  const fetcher=vi.fn().mockResolvedValue(response(quote));
  await expect(createNodeActions(fetcher).quote('p','mine',1)).rejects.toMatchObject({code:'NODE_QUOTE_INVALID'});
  fetcher.mockResolvedValue(response({...quote,node_id:'mine',config_revision:2}));
  await expect(createNodeActions(fetcher).quote('p','mine',1)).rejects.toMatchObject({code:'NODE_QUOTE_INVALID'});
  fetcher.mockResolvedValue(response({...quote,node_id:'mine',estimated_cost_cny:-1}));
  await expect(createNodeActions(fetcher).quote('p','mine',1)).rejects.toMatchObject({code:'NODE_QUOTE_INVALID'});
 });
 it('recovery resubmits exactly the persisted request rather than inventing an id',async()=>{
  const pending={nodeId:'n',quoteId:'q',requestId:'same-request'};
  const fetcher=vi.fn().mockImplementation(()=>Promise.resolve(response(receipt({client_request_id:'same-request'}))));
  const api=createNodeActions(fetcher);
  await api.generate('p',pending);await api.generate('p',pending);
  expect(fetcher.mock.calls.map(call=>JSON.parse(call[1].body))).toEqual([
   {quote_id:'q',client_request_id:'same-request',user_action:'confirm_generate'},
   {quote_id:'q',client_request_id:'same-request',user_action:'confirm_generate'},
  ]);
 });
 it.each([{project_id:'other'},{node_id:'other'},{quote_id:'another-quote'},{client_request_id:'another-request'},{config_revision:0},{progress:undefined}])('an incomplete or mismatched generation receipt remains unknown: %j',async changes=>{
  const fetcher=vi.fn().mockResolvedValue(response(receipt(changes)));
  await expect(createNodeActions(fetcher).generate('p',{nodeId:'n',quoteId:'q',requestId:'r'})).rejects.toMatchObject({code:'NODE_RESULT_UNKNOWN'});
 });
 it('does not automatically retry a network-unknown write',async()=>{
  const fetcher=vi.fn().mockRejectedValue(new Error('offline'));
  await expect(createNodeActions(fetcher).generate('p',{nodeId:'n',quoteId:'q',requestId:'r'})).rejects.toMatchObject({code:'NODE_RESULT_UNKNOWN'});
  expect(fetcher).toHaveBeenCalledTimes(1);
 });
 it('undo uses current layout revision and validates the original operation',async()=>{
  const fetcher=vi.fn().mockResolvedValue(response({project_id:'p',operation_id:'d',already_restored:false}));
  await createNodeActions(fetcher).undo('p','d',4);
  expect(JSON.parse(fetcher.mock.calls[0][1].body)).toEqual({expected_layout_revision:4});
  fetcher.mockResolvedValue(response({project_id:'p',operation_id:'other',already_restored:true}));
  await expect(createNodeActions(fetcher).undo('p','d',4)).rejects.toMatchObject({code:'NODE_RESULT_UNKNOWN'});
 });
 it('does not mask server conflicts as unknown results',async()=>{
  const fetcher=vi.fn().mockResolvedValue(response({error:{code:'CANVAS_REVISION_CONFLICT',message:'changed'}},409));
  await expect(createNodeActions(fetcher).undo('p','d',4)).rejects.toMatchObject({code:'CANVAS_REVISION_CONFLICT',status:409});
 });
 it('keeps a known HTTP rejection known even when its body is not JSON',async()=>{
  const fetcher=vi.fn().mockResolvedValue(new Response('request rejected',{status:409}));
  await expect(createNodeActions(fetcher).generate('p',{nodeId:'n',quoteId:'q',requestId:'r'})).rejects.toMatchObject({code:'HTTP_ERROR',status:409});
 });
 it('retains an accepted task through a failed status read and recovers with GET only',async()=>{
  const fetcher=vi.fn().mockResolvedValueOnce(response(receipt())).mockRejectedValueOnce(new Error('offline'))
   .mockResolvedValueOnce(response(canvas(receipt({status:'running',progress:.4}))));
  const api=createNodeActions(fetcher),accepted=await api.generate('p',{nodeId:'n',quoteId:'q',requestId:'r'});
  await expect(api.readState('p')).rejects.toMatchObject({code:'NODE_STATE_READ_FAILED'});
  const before=saved();
  expect(mergeCanvasExecutionState(before,null,accepted)?.nodes[0].last_execution).toMatchObject({execution_id:'e',status:'queued',active:true});
  const state=await api.readState('p');
  expect(hasObservedExecution(state,accepted,3)).toBe(true);
  expect(mergeCanvasExecutionState(before,state,accepted)?.nodes[0].last_execution).toMatchObject({execution_id:'e',status:'running',progress:.4});
  expect(fetcher.mock.calls.map(call=>[call[0],call[1].method])).toEqual([
   ['/api/projects/p/canvas/nodes/n/generate','POST'],['/api/projects/p/canvas','GET'],['/api/projects/p/canvas','GET'],
  ]);
 });
 it('rejects task records for a different project/node during status polling',async()=>{
  const fetcher=vi.fn().mockResolvedValue(response(canvas(receipt({node_id:'other'}))));
  await expect(createNodeActions(fetcher).readState('p')).rejects.toMatchObject({code:'NODE_STATE_READ_FAILED'});
 });
 it('does not dispatch a read after its owner has unmounted',async()=>{
  const fetcher=vi.fn(),controller=new AbortController();controller.abort();
  await expect(createNodeActions(fetcher).readState('p',controller.signal)).rejects.toMatchObject({code:'NODE_STATE_READ_FAILED'});
  expect(fetcher).not.toHaveBeenCalled();
 });
});

describe('execution rendering is independent from canvas layout/config persistence',()=>{
 it('only merges execution and asset, retaining every layout/config/binding reference and full tombstones',()=>{
  const before=saved(receipt()),output={id:'asset-new',kind:'image'};
  const remote=saved(receipt({status:'succeeded',progress:1,active:false,asset_id:'asset-new',asset:output}),output);
  remote.revision=20;remote.layout.viewport={x:999,y:888,zoom:2};remote.nodes[0].position={x:-100,y:-200};
  remote.nodes[0].config={prompt:'another tab'};remote.nodes[0].config_revision=7;remote.nodes[0].binding={asset_id:'foreign-binding'};
  remote.deletedNodeIds=['a-different-deletion'];remote.edges=[{from:'n',to:'x'}];
  const result=mergeCanvasExecutionState(before,remote)!;
  expect(result.revision).toBe(before.revision);expect(result.layout).toBe(before.layout);
  expect(result.edges).toBe(before.edges);expect(result.deletedNodeIds).toBe(before.deletedNodeIds);
  expect(result.nodes[0].position).toBe(before.nodes[0].position);expect(result.nodes[0].config).toBe(before.nodes[0].config);
  expect(result.nodes[0].binding).toBe(before.nodes[0].binding);expect(result.nodes[0].config_revision).toBe(1);
  expect(result.nodes[0].last_execution?.status).toBe('succeeded');expect(result.nodes[0].asset?.id).toBe('asset-new');
 });
 it('keeps the prior successful image while a new execution is queued or failed',()=>{
  const prior={id:'old-image',kind:'image'},previous=receipt({execution_id:'old',created_at:50,status:'succeeded',active:false,asset:prior,asset_id:'old-image'});
  const before=saved(previous,prior),accepted=receipt();
  const queued=mergeCanvasExecutionState(before,null,accepted)!;
  expect(queued.nodes[0].asset).toEqual(prior);
  expect(queued.nodes[0].last_execution?.last_successful_execution).toMatchObject({execution_id:'old',status:'succeeded'});
  const failed=mergeCanvasExecutionState(queued,saved(receipt({status:'failed',active:false,error:{code:'GENERATION_FAILED',message:'请重试'}})))!;
  expect(failed.nodes[0].asset).toEqual(prior);expect(failed.nodes[0].last_execution?.status).toBe('failed');
 });
 it('does not inject unknown/deleted nodes or roll back a completed execution from a stale controller read',()=>{
  const completed=saved(receipt({status:'succeeded',active:false,progress:1,finished_at:150}),{id:'done',kind:'image'});
  const stale=saved(receipt({status:'running',progress:.3}));stale.nodes.push({...stale.nodes[0],node_id:'remote-only'});
  const result=mergeCanvasExecutionState(completed,stale,receipt())!;
  expect(result.nodes).toHaveLength(1);expect(result.nodes[0].last_execution?.status).toBe('succeeded');expect(result.nodes[0].asset?.id).toBe('done');
  expect(mergeCanvasExecutionState({...completed,nodes:[]},stale,receipt())?.nodes).toEqual([]);
 });
 it('does not attach a prior incarnation task to a newer empty node with the same ID',()=>{
  const recreated=saved();recreated.revision=7;
  const old=saved(receipt());old.revision=3;
  expect(mergeCanvasExecutionState(recreated,old)?.nodes[0].last_execution).toBeNull();
 });
 it('keeps current progress when an older same-task read arrives, regardless of layout saves',()=>{
  const current=saved(receipt({status:'running',progress:.7}));current.revision=8;
  const earlier=saved(receipt({status:'queued',progress:0}));
  expect(mergeCanvasExecutionState(current,earlier)?.nodes[0].last_execution?.progress).toBe(.7);
  const later=saved(receipt({status:'running',progress:.9}));
  expect(mergeCanvasExecutionState(current,later)?.nodes[0].last_execution?.progress).toBe(.9);
 });
 it('accepts newer executions without allowing a prior success to replace them',()=>{
  const before=saved(receipt({execution_id:'older',created_at:50,status:'succeeded',active:false}));before.revision=9;
  const current=saved(receipt());current.revision=8;
  const updated=mergeCanvasExecutionState(before,current)!;
  expect(updated.nodes[0].last_execution?.execution_id).toBe('e');
  expect(mergeCanvasExecutionState(updated,before)?.nodes[0].last_execution?.execution_id).toBe('e');
 });
 it('does not clear the receipt for an older execution, but finishes display recovery after deletion',()=>{
  const accepted=receipt(),old=saved(receipt({execution_id:'old',created_at:50}));
  expect(hasObservedExecution(old,accepted,3)).toBe(false);
  const removed:SavedCanvasLayout={...old,nodes:[],deletedNodeIds:['n']};
  expect(hasObservedExecution(removed,accepted,3)).toBe(true);
  expect(hasObservedExecution({...removed,projectId:'other'},accepted,3)).toBe(false);
 });
});
