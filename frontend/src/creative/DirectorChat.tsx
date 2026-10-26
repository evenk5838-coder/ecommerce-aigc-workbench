import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { ArrowUp, ChevronDown, LoaderCircle, MessageSquare, RefreshCw, Sparkles } from 'lucide-react';
import { ApiError } from '../api';
import { creativeApi } from './api';
import { projectDirectorApi, type DirectorAction, type DirectorMessages, type DirectorPendingRequest, type DirectorTurn, type DirectorProposal } from './director-project-api';
import { mergeFailedDirectorDraft, hasUnsavedDirectorDraft } from './director-draft-state';
import './DirectorChat.css';
interface DirectorChatProps {projectId:string;disabled?:boolean;onBusyChange?:(busy:boolean)=>void;onDirtyChange?:(dirty:boolean)=>void;onRefresh?:()=>void|Promise<void>;onRevealNode?:(id:string)=>void;children?:ReactNode}
interface StoredChat {projectId:string;draft:string;pending:DirectorPendingRequest|null;actionPending:boolean}
const key=(id:string)=>'material-project-director-v2:'+encodeURIComponent(id);
function initial(id:string):StoredChat {try{const v=JSON.parse(localStorage.getItem(key(id))||'null');if(v?.projectId===id&&typeof v.draft==='string'&&(v.pending===null||typeof v.pending?.id==='string'&&typeof v.pending?.message==='string'))return {...v,actionPending:v.actionPending===true};}catch{/* Retain unreadable cache; a fresh state is never an authorization. */}return {projectId:id,draft:'',pending:null,actionPending:false};}
const actionLabel=(a:DirectorAction)=>({confirm_script:'确认脚本，继续分镜',apply_shot_plan:'确认镜头计划',apply_brief:'采用这份创作需求',quote_generation:'选择视频生成方式'})[a.type];
const sameAction=(a:DirectorAction,b:DirectorAction)=>a.type===b.type&&a.target_id===b.target_id&&a.target_version===b.target_version&&a.proposal_id===b.proposal_id&&a.proposal_version===b.proposal_version;
export default function DirectorChat(props:DirectorChatProps){return <ProjectConversation key={props.projectId} {...props}/>;}
function ProjectConversation({projectId,disabled=false,onBusyChange,onDirtyChange,onRefresh,onRevealNode,children}:DirectorChatProps){
 const [stored,setStored]=useState(()=>initial(projectId)),ref=useRef(stored);
 const [data,setData]=useState<DirectorMessages|null>(null),[older,setOlder]=useState<DirectorTurn[]>([]);
 const [phase,setPhase]=useState('loading'),[error,setError]=useState(''),[storageError,setStorageError]=useState('');
 const [requestMissing,setRequestMissing]=useState(false);
 const [proposals,setProposals]=useState<DirectorProposal[]>([]);
 const operation=useRef<AbortController|null>(null),callbacks=useRef({onRefresh,onBusyChange,onDirtyChange,onRevealNode});
 useEffect(()=>{callbacks.current={onRefresh,onBusyChange,onDirtyChange,onRevealNode};},[onRefresh,onBusyChange,onDirtyChange,onRevealNode]);
 const store=useCallback((next:StoredChat)=>{ref.current=next;setStored(next);try{localStorage.setItem(key(projectId),JSON.stringify(next));setStorageError('');return true;}catch{setStorageError('暂时无法保存对话状态，请保持页面开启。');return false;}},[projectId]);
 useEffect(()=>{callbacks.current.onBusyChange?.(phase!=='idle'||!!stored.pending||stored.actionPending);},[phase,stored.pending,stored.actionPending]);
 const unsavedDraft=hasUnsavedDirectorDraft(stored.draft,!!storageError);
 useEffect(()=>{callbacks.current.onDirtyChange?.(unsavedDraft);},[unsavedDraft]);
 useEffect(()=>()=>{callbacks.current.onBusyChange?.(false);callbacks.current.onDirtyChange?.(false);},[]);
 const load=useCallback(async(signal?:AbortSignal)=>{const fresh=await projectDirectorApi.messages(projectId,undefined,{signal});setData(fresh);setOlder([]);try{setProposals(await projectDirectorApi.proposals(projectId,{signal}));}catch{setProposals([]);}return fresh;},[projectId]);
 const recover=useCallback(async(refreshProject=true)=>{
  if(operation.current)return;const c=new AbortController();operation.current=c;setPhase('loading');setError('');setRequestMissing(false);
  try{
   const pending=ref.current.pending;
   if(pending){
    try{const receipt=await projectDirectorApi.receipt(projectId,pending.id,{signal:c.signal});if(receipt.status==='completed'||receipt.status==='failed'){store({...ref.current,pending:null,...(receipt.status==='failed'?{draft:mergeFailedDirectorDraft(ref.current.draft,pending.message)}:{})});if(receipt.status==='failed')setError('上一条消息未完成，内容已与新草稿一起保留，请检查后重新发送。');}}
    catch(e){if(e instanceof ApiError&&e.code==='DIRECTOR_REQUEST_NOT_FOUND'){setRequestMissing(true);setError('尚未查到这条消息的处理记录。请稍后再恢复核对。');}else throw e;}
   }
   await load(c.signal);
   // This refresh never replays an action. The server is authoritative after a lost acknowledgement.
   if(ref.current.actionPending)store({...ref.current,actionPending:false});
   if(refreshProject)await callbacks.current.onRefresh?.();
  }catch(e){if(!c.signal.aborted)setError(e instanceof Error?e.message:'暂时无法恢复会话。');}
  finally{if(operation.current===c){operation.current=null;setPhase('idle');}}
 },[projectId,store,load]);
 useEffect(()=>{void recover(false);return()=>{operation.current?.abort();operation.current=null;};},[recover]);
 async function send(){
  if(disabled||phase!=='idle'||!data||ref.current.pending||ref.current.actionPending||!ref.current.draft.trim()||operation.current)return;
  const pending:DirectorPendingRequest={id:crypto.randomUUID(),message:ref.current.draft.trim(),...(data.session_id?{session_id:data.session_id}:{}),revision:data.workflow_revision};
  const originalDraft=ref.current.draft;
  if(!store({...ref.current,pending,draft:''})){store({...ref.current,pending:null,draft:originalDraft});return;}
  const c=new AbortController();operation.current=c;setPhase('sending');setError('');let acknowledged=false;
  try{
   const response=await projectDirectorApi.chat(projectId,pending,{signal:c.signal});
   if(c.signal.aborted)return;
   acknowledged=true;store({...ref.current,pending:null});
   // Keep the acknowledged reply visible even if the follow-up read fails.
   setData(prev=>prev?{...prev,workflow_revision:response.workflow_revision,session_id:response.session_id,actions:response.actions,items:[{turn_id:response.request_id,request_id:response.request_id,user:pending.message,reply:response.reply,reply_type:response.reply_type,created_at:Date.now()/1000,structured:{artifacts:response.artifacts}},...prev.items]}:prev);
   await callbacks.current.onRefresh?.();await load(c.signal);
   if(response.artifacts.some(a=>a.type==='script'))callbacks.current.onRevealNode?.('script');
  }catch(e){
   if(!acknowledged&&e instanceof ApiError&&e.status>=400&&e.status<500&&e.code!=='DIRECTOR_REQUEST_PENDING')store({...ref.current,pending:null,draft:mergeFailedDirectorDraft(ref.current.draft,pending.message)});
   setError(e instanceof Error?e.message:'还未收到回复，请恢复会话核对。');
  }finally{if(operation.current===c){operation.current=null;setPhase('idle');}}
 }
 async function act(action:DirectorAction){
  if(disabled||phase!=='idle'||ref.current.pending||ref.current.actionPending||operation.current)return;
  if(action.type==='quote_generation'){callbacks.current.onRevealNode?.('generation');return;}
  if(!store({...ref.current,actionPending:true})){store({...ref.current,actionPending:false});return;}
  const c=new AbortController();operation.current=c;setPhase('acting');setError('');
  try{
   // Re-read immediately: historical or superseded actions never authorize a write.
   const fresh=await load(c.signal);if(!fresh.actions.some(a=>sameAction(a,action)))throw new ApiError('DIRECTOR_ACTION_STALE','内容已有更新，请查看最新建议。',409);
   if(action.type==='confirm_script'){
    const result=await creativeApi.confirmScript(action.target_id!,{signal:c.signal});
    if(result.script_id!==action.target_id||result.version!==action.target_version)throw new ApiError('DIRECTOR_ACTION_MISMATCH','脚本确认结果需要重新核对。',0);
   }else await projectDirectorApi.apply(projectId,action,crypto.randomUUID(),{signal:c.signal});
   store({...ref.current,actionPending:false});await callbacks.current.onRefresh?.();await load(c.signal);
   callbacks.current.onRevealNode?.(action.type==='apply_shot_plan'?'plan':'script');
  }catch(e){if(e instanceof ApiError&&e.status>=400&&e.status<500)store({...ref.current,actionPending:false});setError(e instanceof Error?e.message:'请恢复会话核对操作结果。');}
  finally{if(operation.current===c){operation.current=null;setPhase('idle');}}
 }
 async function more(){if(phase!=='idle'||!data)return;const cursor=older.length?older.at(-1)?.turn_id:data.next_cursor;if(!cursor)return;setPhase('history');try{const previous=await projectDirectorApi.messages(projectId,cursor);setOlder(rows=>[...rows,...previous.items.filter(t=>!rows.some(r=>r.turn_id===t.turn_id))]);setData(v=>v?{...v,has_more:previous.has_more}:v);}catch(e){setError(e instanceof Error?e.message:'较早的对话暂时无法读取。');}finally{setPhase('idle');}}
 const turns=[...(data?.items??[]),...older].sort((a,b)=>a.created_at-b.created_at);
 const busy=disabled||phase!=='idle'||!!stored.pending||stored.actionPending;
 return <section className="director-chat" aria-label="AI 导演对话">
  <header className="dc-header"><div className="dc-title"><span><Sparkles size={17}/></span><div><h2>AI 导演</h2><p>从想法，到你的作品</p></div></div><button type="button" className="dc-recover" disabled={phase!=='idle'} onClick={()=>void recover()} aria-label="恢复导演会话"><RefreshCw size={14}/></button></header>
  <div className="dc-scroll"><div className="dc-messages" role="log" aria-label="项目对话" aria-live="polite">
   {!!data?.has_more&&<button className="cs-link" disabled={phase!=='idle'} onClick={()=>void more()}>查看更早的对话</button>}
   {!turns.length&&!stored.pending&&<div className="dc-empty"><span><MessageSquare size={27} strokeWidth={1.4}/></span><h3>这次，想拍些什么？</h3><p>告诉我你的商品与想法，<br/>一起完成脚本、分镜和视频。</p></div>}
   {turns.map(t=><div key={t.turn_id}><div className="dc-message dc-user"><span className="dc-speaker">我</span><p>{t.user}</p></div><div className="dc-message dc-assistant"><span className="dc-speaker">AI 导演</span><p>{t.reply||((t.structured.artifacts as unknown[])?.length?'本次草案已保存，可以查看内容并继续。':'本次未返回文字建议。')}</p></div></div>)}
   {stored.pending&&!turns.some(t=>t.request_id===stored.pending?.id)&&<div className="dc-message dc-user"><span className="dc-speaker">我</span><p>{stored.pending.message}</p></div>}
   {phase==='sending'&&<p className="dc-working" role="status"><LoaderCircle size={14} className="dc-spin"/>导演正在整理思路…</p>}
  </div>
  {error&&<p className="dc-error" role="alert">{error}</p>}{storageError&&<p className="dc-error" role="alert">{storageError}</p>}
  {(stored.pending||stored.actionPending)&&phase==='idle'&&<div className="dc-pending"><p>正在核对上次操作，请先恢复会话。</p><button type="button" onClick={()=>void recover()}>恢复会话</button>{requestMissing&&<button type="button" onClick={()=>{if(window.confirm('尚未查到上次消息。解除等待后可修改内容重新发送；上次请求仍可能完成。')){store({...ref.current,draft:mergeFailedDirectorDraft(ref.current.draft,ref.current.pending?.message??''),pending:null});setRequestMissing(false);}}}>核对后解除等待</button>}</div>}
  {!!data?.actions.length&&<div className="dc-next-actions">{proposals.filter(p=>p.status==='draft'&&p.kind!=='script'&&data.actions.some(a=>a.proposal_id===p.proposal_id)).map(p=><div key={p.proposal_id} className="dc-proposal"><strong>{p.kind==='shot_plan'?'镜头计划':'创作需求'}</strong>{p.kind==='shot_plan'&&Array.isArray(p.content.shots)?<ol>{(p.content.shots as Record<string,unknown>[]).map((shot,i)=><li key={i}>{String(shot.description??shot.hint??'镜头 '+(i+1))}{shot.duration_s!=null&&<small> · {String(shot.duration_s)} 秒</small>}</li>)}</ol>:<dl>{Object.entries(p.content).map(([k,v])=><div key={k}><dt>{({subject:'商品',selling_points:'卖点',target_duration_s:'总时长',style:'风格',quantity:'数量',target_platform:'平台',notes:'补充要求',forbidden_elements:'避免出现'} as Record<string,string>)[k]??'创作信息'}</dt><dd>{Array.isArray(v)?v.join('、'):String(v??'')}</dd></div>)}</dl>}</div>)}{data.actions.map((a,i)=><button key={i} type="button" className="dc-next-action" disabled={busy||((a.type==='apply_brief'||a.type==='apply_shot_plan')&&!proposals.some(p=>p.proposal_id===a.proposal_id))} onClick={()=>void act(a)}>{actionLabel(a)}</button>)}</div>}
  {children&&<details className="dc-materials"><summary>创作资料与手动调整<ChevronDown size={15}/></summary><div className="dc-materials-content">{children}</div></details>}
  </div>
  <form className="dc-composer" onSubmit={e=>{e.preventDefault();void send();}}><label className="dc-input-label" htmlFor={'director-message-'+projectId}>你的拍摄想法</label><textarea id={'director-message-'+projectId} rows={3} value={stored.draft} placeholder="描述商品、想法，或告诉我哪里需要调整…" onChange={e=>store({...ref.current,draft:e.target.value})} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();void send();}}}/><div className="dc-composer-actions"><span>Enter 发送 · Shift 换行</span>{phase==='sending'?<button type="button" onClick={()=>{operation.current?.abort();setError('已停止等待，请恢复会话核对。');}}>停止等待</button>:<button type="submit" className="dc-send" disabled={busy||!data||!stored.draft.trim()||!!storageError}>发送<ArrowUp size={16}/></button>}</div></form>
 </section>;
}
