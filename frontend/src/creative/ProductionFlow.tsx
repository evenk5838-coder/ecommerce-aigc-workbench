import { useEffect, useRef, useState } from 'react';
import { ArrowDown, ArrowUp, Check, Clapperboard, Download, Film as FilmIcon, Image as ImageIcon, Layers3, LoaderCircle, Plus, RefreshCw, Save, Trash2, X } from 'lucide-react';
import { api, ApiError, mediaUrl } from '../api';
import { getDurationPolicy, getModelUnavailableReason, getSupportedImageSizes, getSupportedRatios, getSupportedResolutions } from '../capabilities';
import { inspectGenerationEstimates } from '../estimates';
import type { Asset, Model, Task, TaskInput } from '../types';
import type { CreativeProjectDetail, CreativeScript } from './types';
import { productionApi } from './production-api';
import { inspectFailedSelection, inspectScope } from './production-guards';
import type { Film, ProductionStatus, ProjectExport, ScopeInput, ScopeQuote, Shot, Storyboard, StoryboardCreation, StoryboardInput } from './production-types';
import { draftFromShot, failedShotIds, hasActiveProduction, inspectShotDraft, isActiveStatus, parseProductionEditor, planOrderChanged, productionFingerprint, restoreShotDraft, shotDraftChanged, submitAfterScopeConfirmation, successfulTakes, unionFilms } from './production-state';
import type { ProductionEditorState, ShotDraft } from './production-state';
import { displayDate } from './state';
import './ProductionFlow.css';

export interface ProductionFlowProps {
  panel?: 'brief'|'script'|'storyboard'|'generation'|'delivery';
  project: CreativeProjectDetail; script: CreativeScript | null; blocked?: boolean;
  onDirtyChange?: (dirty: boolean) => void; onPendingChange?: (pending: boolean) => void; refreshKey?: number;
  onSnapshot?: (data: { shots: Shot[]; status: ProductionStatus | null; loaded: boolean; error?: string }) => void;
  onPlanActions?: (actions: { add: () => void; edit: (id: string) => void; remove: (id: string) => void; move: (id: string, direction: 'up' | 'down') => void }) => void;
}
const EDIT_KEY = 'material-studio-production-edit-v1:';
const PENDING_KEY = 'material-studio-production-pending-v1:';
const names: Record<string, string> = { queued:'排队中', pending:'等待处理', loading:'加载中', running:'生成中', postprocess:'保存结果', remote_queued:'云端排队', remote_running:'云端生成', cancel_requested:'已请求取消', succeeded:'成功', failed:'失败', canceled:'已取消', interrupted:'已中断', composing:'合成中', ready:'可预览', draft:'待审核', approved:'已通过审核', rejected:'已驳回', not_started:'尚未生成视频', partial:'部分成片' };
const message = (error: unknown) => error instanceof Error ? error.message : '请求未完成，请重新读取核对。';
const dateNow = () => Date.now();
const money = (value: number) => `¥${value.toFixed(2)}`;
const failedSelection = (value: ProductionStatus | null, ids: string[]) => value ? inspectFailedSelection(value,ids) : {ok:false,reason:'尚未读取到可验证的失败镜头状态。'};
function readEdit(id: string): ProductionEditorState | null { try { return parseProductionEditor(localStorage.getItem(EDIT_KEY + id)); } catch { return null; } }
function readPending(id: string): string { try { return localStorage.getItem(PENDING_KEY + id) || ''; } catch { return ''; } }
function writePending(id: string, value: string): void { try { localStorage.setItem(PENDING_KEY + id, value); } catch { /* The visible navigation guard remains active. */ } }
const isRecord = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
function Loading() { return <LoaderCircle size={16} className="pf-spin" aria-hidden="true" />; }
function durationLabel(ms?: number | null): string { return typeof ms === 'number' && Number.isFinite(ms) && ms >= 0 ? `${(ms/1000).toFixed(2)} 秒（实测）` : '实际时长待探测'; }

/** The key prevents late responses from a previous project from entering the next project. */
export default function ProductionFlow(props: ProductionFlowProps) { return <ProductionProject key={props.project.project_id} {...props} />; }

function ProductionProject({ panel, project, script, blocked = false, onDirtyChange, onPendingChange, refreshKey = 0, onSnapshot, onPlanActions }: ProductionFlowProps) {
  const projectId = project.project_id;
  const [local] = useState(() => readEdit(projectId));
  const [rows, setRows] = useState<ShotDraft[]>(local?.rows ?? []);
  const [shots, setShots] = useState<Shot[]>([]);
  const [shotsLoaded, setShotsLoaded] = useState(false);
  const [retryOnly, setRetryOnly] = useState(false);
  const [models, setModels] = useState<Model[]>([]);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [status, setStatus] = useState<ProductionStatus | null>(null);
  const [films, setFilms] = useState<Film[]>([]);
  const [videoModelId, setVideoModelId] = useState(local?.videoModelId ?? '');
  const [ratio, setRatio] = useState(local?.ratio ?? '');
  const [resolution, setResolution] = useState(local?.resolution ?? '');
  const [imageModelId, setImageModelId] = useState(local?.imageModelId ?? '');
  const [imageSize, setImageSize] = useState(local?.imageSize ?? '');
  const [mode, setMode] = useState<'full'|'clips'>(local?.mode ?? 'full');
  const [selectedIds, setSelectedIds] = useState<string[]>(local?.selectedIds ?? []);
  const [selectedTakes, setSelectedTakes] = useState<Record<string,string>>({});
  const [rejections, setRejections] = useState<Record<string,string>>({});
  const [createdBoards, setCreatedBoards] = useState<StoryboardCreation[]>([]);
  const [trackedTaskIds, setTrackedTaskIds] = useState<string[]>([]);
  const [shotError, setShotError] = useState('');
  const [statusError, setStatusError] = useState('');
  const [historyError, setHistoryError] = useState('');
  const [resourceError, setResourceError] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [loading, setLoading] = useState(true);
  const [initialized, setInitialized] = useState(false);
  const [busy, setBusy] = useState('');
  const [uncertain, setUncertain] = useState(() => readPending(projectId));
  const [recovered, setRecovered] = useState(false);
  const [visible, setVisible] = useState(() => document.visibilityState === 'visible');
  const [pollAllowed, setPollAllowed] = useState(true);
  const [storageError, setStorageError] = useState(false);
  const [scope, setScope] = useState<{ quote: ScopeQuote; input: ScopeInput; fingerprint: string; retryOnly: boolean } | null>(null);
  const currentScope = useRef(scope); currentScope.current = scope;
  const clearScope = () => { currentScope.current = null; setScope(null); };
  const [storyQuote, setStoryQuote] = useState<{ shotId: string; input: StoryboardInput; price: number; fingerprint: string; modelId: string } | null>(null);
  const [filmConfirm, setFilmConfirm] = useState<{ takeIds: string[]; partial: boolean; fingerprint: string; missing: string[] } | null>(null);
  const [partialAccepted, setPartialAccepted] = useState(false);
  const [exportResult, setExportResult] = useState<ProjectExport | null>(null);
  const [clock, setClock] = useState(dateNow);
  const pausedReads = useRef(new Set<string>());
  const snapshotCallback = useRef(onSnapshot); snapshotCallback.current = onSnapshot;
  const pendingCallback = useRef(onPendingChange); pendingCallback.current = onPendingChange;
  const actionsCallback = useRef(onPlanActions); actionsCallback.current = onPlanActions;
  const planActions = useRef<NonNullable<ProductionFlowProps['onPlanActions']> extends (actions: infer A) => void ? A : never | null>(null);
  const mounted = useRef(true), mutationLock = useRef(false), loadSequence = useRef(0), operation = useRef<AbortController | null>(null);
  const rowsRef = useRef(rows), shotsRef = useRef(shots); rowsRef.current = rows; shotsRef.current = shots;
  const videoModel = models.find(model => model.id === videoModelId), imageModel = models.find(model => model.id === imageModelId);
  const dirty = rows.some(row => shotDraftChanged(row, shots.find(shot => shot.shot_id === row.shot_id))) || planOrderChanged(rows,shots);
  const disabled = blocked || !!busy || !!uncertain;
  const scopeInput: ScopeInput = { mode, shot_ids: rows.filter(row => row.shot_id && selectedIds.includes(row.shot_id)).map(row => row.shot_id!), specs: { model_id: videoModelId, ratio, resolution } };
  const snapshotShots = shots.map(shot=>({...shot,storyboards:status?.shots.find(item=>item.shot_id===shot.shot_id)?.storyboards??shot.storyboards}));
  const fingerprint = productionFingerprint(projectId, script?.script_id ?? null, project.brief, rows, snapshotShots, scopeInput);
  const latestStatus = useRef(status); latestStatus.current = status;
  const retryOnlyRef = useRef(retryOnly); retryOnlyRef.current = retryOnly;
  const liveFingerprint = useRef(fingerprint); liveFingerprint.current = fingerprint;
  const storyFingerprint = JSON.stringify({fingerprint,imageModelId,imageSize});
  const liveStoryFingerprint = useRef(storyFingerprint); liveStoryFingerprint.current = storyFingerprint;
  const videoError = getModelUnavailableReason(videoModel) || (!getSupportedRatios(videoModel).includes(ratio) ? '请选择当前模型支持的比例。' : !getSupportedResolutions(videoModel).includes(resolution) ? '请选择当前模型支持的清晰度。' : null);
  const submitBlocked = useRef(blocked || !!videoError || dirty); submitBlocked.current = blocked || !!videoError || dirty;
  const selectedTakeIds = shots.map(shot => { const options = successfulTakes(shot,status); const chosen=selectedTakes[shot.shot_id]; return options.find(take=>take.take_id===chosen)?.take_id ?? options.at(-1)?.take_id ?? ''; });
  const missingShots = shots.filter((_,index)=>!selectedTakeIds[index]).map(shot=>shot.shot_id);
  const filmFingerprint = JSON.stringify({projectId, order:shots.map(shot=>shot.shot_id),takes:selectedTakeIds});
  const filmFingerprintRef = useRef(filmFingerprint); filmFingerprintRef.current = filmFingerprint;
  const invalidate = () => { clearScope(); setStoryQuote(null); setFilmConfirm(null); setPartialAccepted(false); setExportResult(null); };
  useEffect(() => { snapshotCallback.current?.({ shots, status, loaded: shotsLoaded, ...(shotError || statusError ? { error: [shotError,statusError].filter(Boolean).join('；') } : {}) }); }, [shots,status,shotsLoaded,shotError,statusError]);
  useEffect(() => { actionsCallback.current?.({ add:()=>planActions.current?.add(), edit:id=>planActions.current?.edit(id), remove:id=>planActions.current?.remove(id), move:(id,direction)=>planActions.current?.move(id,direction) }); }, []);
  useEffect(() => { pendingCallback.current?.(!!busy || !!uncertain); }, [busy,uncertain]);
  useEffect(() => () => pendingCallback.current?.(false), []);
  useEffect(() => { onDirtyChange?.(dirty || !!busy || !!uncertain); }, [dirty,busy,uncertain,onDirtyChange]);
  useEffect(() => () => onDirtyChange?.(false), [onDirtyChange]);
  useEffect(() => { if (!initialized) return; const data:ProductionEditorState={version:1,rows,videoModelId,ratio,resolution,imageModelId,imageSize,mode,selectedIds}; try { localStorage.setItem(EDIT_KEY+projectId,JSON.stringify(data)); } catch { setStorageError(true); } },[initialized,rows,videoModelId,ratio,resolution,imageModelId,imageSize,mode,selectedIds,projectId]);
  useEffect(() => { if (blocked) { clearScope(); setStoryQuote(null); setFilmConfirm(null); setPartialAccepted(false); } }, [blocked]);
  useEffect(() => { if (scope && scope.fingerprint !== fingerprint) clearScope(); if (storyQuote && storyQuote.fingerprint !== storyFingerprint) setStoryQuote(null); },[fingerprint,storyFingerprint,scope,storyQuote]);
  useEffect(() => { if (filmConfirm && filmConfirm.fingerprint !== filmFingerprint) {setFilmConfirm(null);setPartialAccepted(false);} },[filmFingerprint,filmConfirm]);
  useEffect(() => { if (!scope) return; const timer=setInterval(()=>setClock(Date.now()),1000); return()=>clearInterval(timer); },[scope]);
  useEffect(() => { const onVisible=()=>setVisible(document.visibilityState==='visible');document.addEventListener('visibilitychange',onVisible);return()=>document.removeEventListener('visibilitychange',onVisible); },[]);

  const readResources = async () => {
    const results=await Promise.allSettled([api.models(),api.assets(),api.tasks()]);if(!mounted.current)return;
    const failures:string[]=[];
    if(results[0].status==='fulfilled'){const list=results[0].value.items;setModels(list);const vm=list.find(item=>item.kind==='video'&&!getModelUnavailableReason(item));const im=list.find(item=>item.kind==='image'&&!getModelUnavailableReason(item));if(!videoModelId&&vm){setVideoModelId(vm.id);if(!ratio)setRatio(getSupportedRatios(vm)[0]??'');if(!resolution)setResolution(getSupportedResolutions(vm)[0]??'');}if(!imageModelId&&im){setImageModelId(im.id);if(!imageSize)setImageSize(getSupportedImageSizes(im)[0]??'');}}else failures.push('模型能力读取失败');
    if(results[1].status==='fulfilled')setAssets(results[1].value.items);else failures.push('参考素材读取失败');
    if(results[2].status==='fulfilled')setTasks(results[2].value.items);else failures.push('任务状态读取失败');
    setResourceError(failures.join('；'));
  };
  const readProduction = async (initial=false) => {
    const sequence=++loadSequence.current;setLoading(true);setRecovered(false);pausedReads.current.clear();
    const results=await Promise.allSettled([productionApi.listShots(projectId),productionApi.getStatus(projectId),productionApi.listFilms(projectId)]);
    if(!mounted.current||sequence!==loadSequence.current)return;
    if(results[0].status==='fulfilled'){const list=results[0].value.items.slice().sort((a,b)=>a.seq-b.seq);const previous=shotsRef.current;const currentRows=rowsRef.current;const hasChanges=currentRows.some(row=>shotDraftChanged(row,previous.find(shot=>shot.shot_id===row.shot_id)))||planOrderChanged(currentRows,previous);setShots(list);setShotsLoaded(true);setShotError('');if((initial&&!local)||(!initial&&!hasChanges)){setRows(list.map(shot=>restoreShotDraft(shot,currentRows.find(row=>row.shot_id===shot.shot_id))));if(initial)setSelectedIds(list.map(shot=>shot.shot_id));}else if(!initial){setRows(current=>current.map(row=>{const incoming=list.find(shot=>shot.shot_id===row.shot_id);return incoming&&!shotDraftChanged(row,previous.find(shot=>shot.shot_id===row.shot_id))?restoreShotDraft(incoming,row):row;}));}setTrackedTaskIds(current=>[...new Set([...current,...list.flatMap(shot=>(shot.storyboards??[]).map(board=>board.task_id).filter((id):id is string=>!!id))])]);setInitialized(true);}else{setShotError(message(results[0].reason));setShotsLoaded(false);setInitialized(true);}
    if(results[1].status==='fulfilled'){const next=results[1].value;setStatus(next);setFilms(current=>unionFilms(current,next));setStatusError('');setPollAllowed(true);}else{setStatusError(message(results[1].reason));setPollAllowed(false);}
    if(results[2].status==='fulfilled'){const history=results[2].value.items;setFilms(current=>unionFilms([...current,...history],null));setHistoryError('');}else setHistoryError(message(results[2].reason));
    setRecovered(results[0].status==='fulfilled'&&(results[1].status==='fulfilled'||results[2].status==='fulfilled'));setLoading(false);
  };
  useEffect(()=>{mounted.current=true;void readResources();void readProduction(true);return()=>{mounted.current=false;loadSequence.current+=1;operation.current?.abort();};},[projectId]);
  const refreshSeen=useRef(refreshKey);
  useEffect(()=>{if(refreshSeen.current!==refreshKey){refreshSeen.current=refreshKey;invalidate();void readProduction(false);}},[refreshKey]);
  const active=hasActiveProduction(pollAllowed?status:null,tasks,trackedTaskIds.filter(id=>!pausedReads.current.has(id)))||films.some(film=>isActiveStatus(film.status)&&!pausedReads.current.has(film.film_id));
  useEffect(()=>{
    if(!visible||!active)return;let stopped=false,inFlight=false;
    const tick=async()=>{if(inFlight||stopped)return;inFlight=true;
      const ids=[...new Set([...trackedTaskIds,...shots.flatMap(shot=>(shot.storyboards??[]).map(board=>board.task_id).filter((id):id is string=>!!id))])].filter(id=>{const task=tasks.find(item=>item.id===id);return !pausedReads.current.has(id)&&(!task||isActiveStatus(task.status));});
      const reads:Promise<unknown>[]=[];
      if(pollAllowed)reads.push(productionApi.getStatus(projectId).then(next=>{if(!stopped){setStatus(next);setFilms(current=>unionFilms(current,next));setStatusError('');}}).catch(caught=>{if(!stopped){setStatusError(message(caught));setPollAllowed(false);}}));
      for(const id of ids)reads.push(api.task(id).then(task=>{if(!stopped)setTasks(current=>[...current.filter(item=>item.id!==id),task]);}).catch(caught=>{if(!stopped){pausedReads.current.add(id);setResourceError(message(caught));setTrackedTaskIds(current=>current.filter(item=>item!==id));}}));
      for(const film of films.filter(item=>isActiveStatus(item.status)&&!pausedReads.current.has(item.film_id)))reads.push(productionApi.getFilm(film.film_id).then(next=>{if(!stopped)setFilms(current=>[...current.filter(item=>item.film_id!==next.film_id),next]);}).catch(caught=>{if(!stopped){pausedReads.current.add(film.film_id);setHistoryError(message(caught));}}));
      await Promise.allSettled(reads);inFlight=false;
    };const timer=setInterval(()=>void tick(),3500);return()=>{stopped=true;clearInterval(timer);};
  },[visible,active,pollAllowed,projectId,tasks,trackedTaskIds,shots,films]);

  const perform=async<T,>(label:string,write:boolean,action:(signal:AbortSignal)=>Promise<T>,done:(result:T)=>void|Promise<void>)=>{
    if(mutationLock.current||(write&&(blocked||uncertain)))return;mutationLock.current=true;setBusy(label);setError('');setNotice('');setRecovered(false);
    const controller=new AbortController();operation.current=controller;if(write){setUncertain(label);writePending(projectId,label);}
    try{const result=await action(controller.signal);if(!mounted.current)return;await done(result);if(write){setUncertain('');writePending(projectId,'');}}
    catch(caught){if(!mounted.current)return;setError(message(caught));if(write&&!(caught instanceof ApiError&&caught.code==='CREATIVE_RESULT_UNKNOWN')){setUncertain('');writePending(projectId,'');}if(caught instanceof ApiError&&caught.status===409){invalidate();setNotice('服务器已有变化。你的编辑仍保留，请重新读取最新镜头并核对。');}}
    finally{mutationLock.current=false;if(mounted.current)setBusy('');}
  };
  const updateRow=(key:string,patch:Partial<ShotDraft>)=>{invalidate();setRows(current=>current.map(row=>row.key===key?{...row,...patch}:row));};
  const addRow=(description='')=>{invalidate();setRows(current=>[...current,{key:crypto.randomUUID(),description,motion:'',prompt:'',input_asset_id:'',duration:''}]);};
  const saveRow=async(row:ShotDraft,index:number)=>{
    if(!script){setError('请先选择已保存的脚本版本，再保存镜头计划。');return;}
    const checked=inspectShotDraft(row,videoModel,index+1);if(!checked.input){setError(checked.error??'镜头参数不完整');return;}invalidate();
    await perform('保存镜头',true,async signal=>{
      if(row.shot_id)return productionApi.updateShot(row.shot_id,{...checked.input!,expected_revision:row.revision??0},{signal});
      const receipt=await productionApi.createShots(projectId,{script_id:script.script_id,shots:[checked.input!],model_id:videoModelId},{signal});
      const createdId=receipt.shot_ids[0];
      // Persist the acknowledged ID before the follow-up GET. A read failure must never cause another POST.
      if(mounted.current)setRows(current=>current.map(item=>item.key===row.key?{...item,shot_id:createdId}:item));
      try{const latest=await productionApi.listShots(projectId,{signal});const written=latest.items.find(shot=>shot.shot_id===createdId);if(!written)throw new Error('新镜头尚未出现在读取结果中');return written;}
      catch(cause){throw new ApiError('CREATIVE_RESULT_UNKNOWN','镜头已创建，但读取保存结果未完成。请先刷新镜头核对，不要再次创建。',0,{created_shot_id:createdId,reason:'read_after_write',message:message(cause)});}
    },written=>{
      setShots(current=>[...current.filter(shot=>shot.shot_id!==written.shot_id),written].sort((a,b)=>a.seq-b.seq));setRows(current=>current.map(item=>item.key===row.key?restoreShotDraft(written,item):item));setSelectedIds(current=>[...new Set([...current,written.shot_id])]);setNotice('镜头的画面描述、运镜和时长已保存。分镜提示词、参考图与模型选择保存在此浏览器，生成前仍需报价确认。');
    });
  };
  const removeRow=async(row:ShotDraft)=>{if(!window.confirm('删除这条镜头计划吗？已有生成产物的保留情况以服务端记录为准。'))return;invalidate();if(!row.shot_id){setRows(current=>current.filter(item=>item.key!==row.key));return;}await perform('删除镜头',true,signal=>productionApi.deleteShot(row.shot_id!,{signal}),()=>{setRows(current=>current.filter(item=>item.key!==row.key));setShots(current=>current.filter(shot=>shot.shot_id!==row.shot_id));setSelectedIds(current=>current.filter(id=>id!==row.shot_id));setNotice('镜头计划已删除。');});};
  const moveRow=(index:number,direction:number)=>{const destination=index+direction;if(destination<0||destination>=rows.length)return;invalidate();setRows(current=>{const next=[...current];[next[index],next[destination]]=[next[destination],next[index]];return next;});};
  const saveOrder=()=>void perform('保存镜头顺序',true,signal=>productionApi.reorderShots(projectId,{order:rows.map(row=>row.shot_id!)},{signal}),result=>{setShots(current=>result.order.map((id,index)=>({...current.find(shot=>shot.shot_id===id)!,seq:index+1})));setNotice('拼接顺序已保存；没有触发生成。');});
  const estimateBoard=async(row:ShotDraft)=>{
    if(!row.shot_id)return;if(getModelUnavailableReason(imageModel)){setError(getModelUnavailableReason(imageModel)??'请选择图片模型');return;}
    if(!getSupportedImageSizes(imageModel).includes(imageSize)||!row.prompt.trim()){setError('请填写生成提示词，并选择图片模型支持的尺寸。');return;}
    if(shotDraftChanged(row,shots.find(shot=>shot.shot_id===row.shot_id))){setError('请先保存这条镜头的编辑。');return;}
    const input:StoryboardInput={model_id:imageModelId,size:imageSize,prompt:row.prompt,input_asset_id:row.input_asset_id||null};
    const task:TaskInput={kind:'image',model_id:imageModelId,input_asset_id:input.input_asset_id,params:{prompt:input.prompt,size:input.size,generation_mode:input.input_asset_id?'image_to_image':'text_to_image'},client_request_id:crypto.randomUUID()};
    const snapshot=liveStoryFingerprint.current;await perform('免费读取分镜估价',false,signal=>new Promise((resolve,reject)=>{const abort=()=>reject(new Error('已停止等待免费估价。'));signal.addEventListener('abort',abort,{once:true});api.estimateTask(task).then(resolve,reject).finally(()=>signal.removeEventListener('abort',abort));}),estimate=>{const check=inspectGenerationEstimates([estimate],1);if(check.error||!check.costs)throw new Error(check.error??'估价不完整');if(snapshot!==liveStoryFingerprint.current)throw new Error('镜头内容已改变，请重新估价。');setStoryQuote({shotId:row.shot_id!,input,price:check.costs[0],fingerprint:snapshot,modelId:imageModelId});clearScope();});
  };
  const submitBoard=async()=>{const frozen=storyQuote;if(!frozen||frozen.fingerprint!==liveStoryFingerprint.current||getModelUnavailableReason(models.find(model=>model.id===frozen.modelId))||!getSupportedImageSizes(models.find(model=>model.id===frozen.modelId)).includes(frozen.input.size))return;await perform('提交分镜生成',true,signal=>productionApi.createStoryboard(frozen.shotId,frozen.input,{signal}),result=>{setCreatedBoards(current=>[...current,result]);setTrackedTaskIds(current=>[...new Set([...current,result.task_id])]);setStoryQuote(null);setNotice('分镜生成任务已提交。审核前请等待真实图片结果。');});};
  const boardReview=(board:Storyboard,approve:boolean)=>{invalidate();return perform(approve?'批准分镜版本':'驳回分镜版本',true,signal=>approve?productionApi.approveStoryboard(board.storyboard_id,{signal}):productionApi.rejectStoryboard(board.storyboard_id,rejections[board.storyboard_id]??'',{signal}),result=>{const patch=(shot:Shot)=>({...shot,storyboards:shot.storyboards?.map(item=>item.storyboard_id===result.storyboard_id?{...item,...result}:item)});setShots(current=>current.map(patch));setStatus(current=>current?{...current,shots:current.shots.map(item=>({...item,storyboards:item.storyboards?.map(sb=>sb.storyboard_id===result.storyboard_id?{...sb,...result}:sb)}))}:current);setNotice(`分镜 v${board.version}${approve?'已批准':'已驳回'}。`);void readProduction(false);});};
  const quoteScope=async()=>{if(retryOnly){const check=failedSelection(status,scopeInput.shot_ids);if(!check.ok){setError(check.reason??'失败镜头状态已变化，请重新选择。');return;}}if(videoError||dirty||!scopeInput.shot_ids.length){setError(videoError??(dirty?'请先保存全部镜头编辑与顺序。':'请选择需要生成的镜头。'));return;}for(const row of rows.filter(item=>item.shot_id&&scopeInput.shot_ids.includes(item.shot_id))){const check=inspectShotDraft(row,videoModel,rows.indexOf(row)+1);if(check.error){setError(check.error);return;}}const snapshot=liveFingerprint.current;const input=scopeInput;await perform('免费读取整组报价',false,signal=>productionApi.createScope(projectId,input,{signal}),quote=>{const check=inspectScope(quote,input);if(!check.ok)throw new Error(check.reason??'报价数据不完整');if(snapshot!==liveFingerprint.current)throw new Error('输入已变化，请重新获取报价。');setClock(Date.now());const next={quote,input,fingerprint:snapshot,retryOnly:retryOnlyRef.current};currentScope.current=next;setScope(next);setStoryQuote(null);});};
  const submitScope=async()=>{const frozen=scope;if(!frozen||submitBlocked.current||frozen.fingerprint!==liveFingerprint.current||!inspectScope(frozen.quote,frozen.input).ok)return;if(retryOnlyRef.current&&!failedSelection(latestStatus.current,frozen.input.shot_ids).ok){setError('所选失败镜头的状态已变化，请重新读取并报价。');clearScope();return;}await perform('确认范围并提交生成',true,signal=>submitAfterScopeConfirmation(frozen,()=>productionApi.confirmScope(frozen.quote,{signal}),()=>({currentQuote:currentScope.current?.quote??null,fingerprint:liveFingerprint.current,blocked:submitBlocked.current,retryOnly:retryOnlyRef.current,selectedStillFailed:failedSelection(latestStatus.current,frozen.input.shot_ids).ok,mounted:mounted.current,aborted:signal.aborted}),approval=>productionApi.submitScope(projectId,frozen.quote,approval,{signal})),result=>{setTrackedTaskIds(current=>[...new Set([...current,...result.tasks.map(task=>task.task_id)])]);clearScope();setNotice(`已提交 ${result.tasks.length} 个镜头任务，按队列顺序执行。${result.mode==='full'?'整组成功后由服务端自动合成。':'成功片段将独立保留，可稍后手工合成。'}`);void readProduction(false);});};
  const prepareFilm=(partial:boolean)=>{setPartialAccepted(false);setFilmConfirm({takeIds:selectedTakeIds.filter(Boolean),partial,fingerprint:filmFingerprint,missing:missingShots});};
  const composeFilm=async()=>{const frozen=filmConfirm;if(!frozen||frozen.fingerprint!==filmFingerprintRef.current||(frozen.partial&&!partialAccepted))return;await perform('提交成片拼接',true,signal=>productionApi.createFilm(projectId,{take_ids:frozen.takeIds,confirm_partial:frozen.partial},{signal}),result=>{setFilms(current=>unionFilms([...current,{...result,project_id:projectId}],null));setFilmConfirm(null);setNotice(`已提交成片 v${result.version} 的${frozen.partial?'部分':'完整'}拼接，不裁剪原片段。`);void readProduction(false);});};
  const requestExport=()=>void perform('打包导出项目',true,signal=>productionApi.exportProject(projectId,{include:['film','clips','storyboards','manifest']},{signal}),result=>{setExportResult(result);setNotice(result.missing.length?'导出包已准备好，但包含缺失项，请核对清单后下载。':'导出包已准备好，点击下载保存。');});
  const boardItems=(shot:Shot):Storyboard[]=>{
    const server=[...(shot.storyboards??[]),...(status?.shots.find(item=>item.shot_id===shot.shot_id)?.storyboards??[])];
    for(const receipt of createdBoards.filter(item=>item.shot_id===shot.shot_id)){const task=tasks.find(item=>item.id===receipt.task_id);const existing=server.findIndex(item=>item.storyboard_id===receipt.storyboard_id);if(existing>=0){server[existing]={...server[existing],task_id:server[existing].task_id??receipt.task_id,asset_id:server[existing].asset_id??task?.output_asset_id};continue;}server.push({storyboard_id:receipt.storyboard_id,shot_id:receipt.shot_id,version:receipt.version,status:task?.status??'unknown',task_id:receipt.task_id,asset_id:task?.output_asset_id});}
    return [...new Map(server.map(item=>[item.storyboard_id,item])).values()].sort((a,b)=>b.version-a.version);
  };
  planActions.current = {
    add:()=>{if(disabled)return;addRow();document.getElementById('pf-plan-title')?.scrollIntoView({block:'start'});setNotice('已在镜头计划末尾添加一条本地镜头，请填写并保存。');},
    edit:id=>{const element=document.getElementById('pf-shot-'+id);element?.scrollIntoView({block:'center'});element?.focus();},
    remove:id=>{if(disabled)return;const row=rowsRef.current.find(item=>item.shot_id===id||item.key===id);if(row)void removeRow(row);},
    move:(id,direction)=>{if(disabled)return;const index=rowsRef.current.findIndex(item=>item.shot_id===id||item.key===id);if(index>=0){moveRow(index,direction==='up'?-1:1);setNotice('镜头顺序已在本地调整，请在计划列表点击保存拼接顺序。');}},
  };
  const scopeCheck=scope?inspectScope(scope.quote,scope.input,clock):null;
  return <div className="production-flow">
    <header className="pf-header" hidden={!!panel}><div><span className="pf-eyebrow">从脚本到成片</span><h2>镜头、分镜与视频制作</h2><p>按镜头保存计划、审核画面，再一次确认生成范围。</p></div><button className="pf-button" disabled={loading||!!busy} onClick={()=>{invalidate();void readResources();void readProduction(false);}}>{loading?<Loading/>:<RefreshCw size={15}/>}重新读取</button></header>
    {blocked&&<div className="pf-notice">上方需求或脚本尚在编辑、等待处理。请先完成保存，再提交制作操作。</div>}
    {error&&<div className="pf-notice pf-error" role="alert">{error}</div>}{notice&&<div className="pf-notice" role="status">{notice}</div>}
    {resourceError&&<div className="pf-notice pf-error" role="alert">{resourceError}。现有输入已保留，请重新读取。</div>}
    {storageError&&<div className="pf-notice pf-warning">浏览器不能保存本地编辑备份，请显式保存后再离开。</div>}
    {uncertain&&!busy&&<div className="pf-notice pf-warning" role="alert"><strong>“{uncertain}”结果尚需核对</strong><p>服务端可能已接受请求；不会自动重试或自动重新生成。请先读取项目状态、镜头与历史结果。</p><div className="pf-actions"><button className="pf-button" onClick={()=>void readProduction(false)} disabled={loading}>读取结果核对</button><button className="pf-button" disabled={!recovered||loading} onClick={()=>{if(window.confirm('已核对最新结果了吗？原请求仍可能完成。解除后也不会自动重发；请避免重复生成。')){setUncertain('');writePending(projectId,'');setError('');}}}>已核对，继续操作</button></div></div>}
    {busy&&<div className="pf-notice" role="status"><Loading/>{busy}… <button className="pf-link" onClick={()=>operation.current?.abort()}>停止等待</button><span>停止等待不会取消服务端执行。</span></div>}
    <section className="pf-section" hidden={!!panel&&panel!=='storyboard'} aria-labelledby="pf-plan-title"><div className="pf-section-heading"><h3 id="pf-plan-title"><span>03</span>镜头计划与分镜</h3><span className="pf-small">当前脚本：{script?`v${script.version}`:'尚未选择'}</span></div>
      {shotError&&<div className="pf-notice pf-warning">镜头接口暂不可用：{shotError}。可以继续编辑本地计划，保存需要接口恢复。</div>}
      <fieldset className="pf-fields" disabled={disabled}><div className="pf-settings"><label>本组视频模型<select value={videoModelId} onChange={event=>{invalidate();setVideoModelId(event.target.value);}}><option value="">选择模型</option>{models.filter(model=>model.kind==='video').map(model=><option key={model.id} value={model.id} disabled={!!getModelUnavailableReason(model)}>{model.name}{getModelUnavailableReason(model)?' · 暂不可提交':''}</option>)}</select></label><label>视频比例<select value={ratio} onChange={event=>{invalidate();setRatio(event.target.value);}}><option value="">选择比例</option>{getSupportedRatios(videoModel).map(option=><option key={option}>{option}</option>)}</select></label><label>清晰度<select value={resolution} onChange={event=>{invalidate();setResolution(event.target.value);}}><option value="">选择清晰度</option>{getSupportedResolutions(videoModel).map(option=><option key={option}>{option}</option>)}</select></label><label>分镜图片模型<select value={imageModelId} onChange={event=>{invalidate();setImageModelId(event.target.value);setImageSize('');}}><option value="">选择模型</option>{models.filter(model=>model.kind==='image').map(model=><option key={model.id} value={model.id} disabled={!!getModelUnavailableReason(model)}>{model.name}</option>)}</select></label><label>分镜尺寸<select value={imageSize} onChange={event=>{invalidate();setImageSize(event.target.value);}}><option value="">选择尺寸</option>{getSupportedImageSizes(imageModel).map(size=><option key={size}>{size}</option>)}</select></label></div></fieldset>
      {videoError&&<p className="pf-small pf-warning-text">{videoError}</p>}
      <div className="pf-plan-toolbar"><div className="pf-actions"><button className="pf-button" disabled={disabled} onClick={()=>addRow()}><Plus size={15}/>添加镜头</button><button className="pf-button" disabled={disabled||!script||!Array.isArray(script.content.shots)} onClick={()=>{const hints=(script?.content.shots??[]).filter(item=>isRecord(item)&&typeof item.hint==='string'&&item.hint.trim()).map(item=>(item as {hint:string}).hint);if(!hints.length){setError('该脚本没有可用镜头建议，请手工添加。');return;}invalidate();setRows(current=>[...current,...hints.map(description=>({key:crypto.randomUUID(),description,motion:'',prompt:'',input_asset_id:'',duration:''}))]);}}>从当前脚本添加建议</button></div><button className="pf-button" disabled={disabled||!!shotError||!rows.length||rows.some(row=>!row.shot_id)||!planOrderChanged(rows,shots)} onClick={saveOrder}><Save size={15}/>保存拼接顺序</button></div>
      <p className="pf-small">保存镜头只保存画面描述、运镜和时长。分镜提示词、参考图和模型选择仅保存在当前浏览器，确认生成时才提交。</p>{dirty&&<div className="pf-unsaved">镜头计划有未保存内容。生成前请逐镜保存，并保存调整后的顺序。{shots.length>0&&<button className="pf-link" disabled={disabled} onClick={()=>{if(window.confirm('放弃当前本地镜头编辑，载入刚读取的服务端计划吗？')){invalidate();setRows(shots.map(draftFromShot));}}}>载入服务端计划</button>}</div>}
      {!rows.length&&<div className="pf-empty"><Clapperboard size={28}/><p>添加第一条镜头，填写画面、参考图与原生时长。</p></div>}
      <div className="pf-shot-list">{rows.map((row,index)=>{const saved=shots.find(shot=>shot.shot_id===row.shot_id),changed=shotDraftChanged(row,saved),policy=getDurationPolicy(videoModel);const boards=saved?boardItems(saved):[];const latestStatus=status?.shots.find(item=>item.shot_id===row.shot_id);return <article className="pf-shot" key={row.key} id={"pf-shot-"+(row.shot_id??row.key)} tabIndex={-1}>
        <header><div><span className="pf-shot-index">{String(index+1).padStart(2,'0')}</span><strong>镜头 {index+1}</strong><span className="pf-badge">{!row.shot_id?'尚未保存':changed?'编辑未保存':`修订 ${row.revision}`}</span>{latestStatus&&<span className="pf-badge">{names[latestStatus.status]??latestStatus.status}</span>}</div><div className="pf-actions"><button className="pf-icon" aria-label={`上移镜头${index+1}`} disabled={disabled||index===0} onClick={()=>moveRow(index,-1)}><ArrowUp size={15}/></button><button className="pf-icon" aria-label={`下移镜头${index+1}`} disabled={disabled||index===rows.length-1} onClick={()=>moveRow(index,1)}><ArrowDown size={15}/></button><button className="pf-icon" aria-label={`删除镜头${index+1}`} disabled={disabled} onClick={()=>void removeRow(row)}><Trash2 size={15}/></button></div></header>
        <fieldset className="pf-fields" disabled={disabled}><div className="pf-shot-grid"><label className="pf-wide">画面描述<textarea rows={2} value={row.description} onChange={event=>updateRow(row.key,{description:event.target.value})} placeholder="这一镜展示什么商品事实、画面与动作"/></label><label>原生时长（秒）{policy.kind==='enum'?<select value={row.duration} onChange={event=>updateRow(row.key,{duration:event.target.value})}><option value="">选择时长</option>{policy.allowed.map(value=><option key={value} value={value}>{value} 秒</option>)}</select>:<input type="number" value={row.duration} min={policy.kind==='range'?policy.min:undefined} max={policy.kind==='range'?policy.max:undefined} step={policy.kind==='range'?policy.step:1} onChange={event=>updateRow(row.key,{duration:event.target.value})} placeholder={policy.kind==='range'?`${policy.min}–${policy.max} 秒，步长 ${policy.step}`:'模型时长能力未就绪'}/>}</label><label>运镜<input value={row.motion} onChange={event=>updateRow(row.key,{motion:event.target.value})} placeholder="如：缓慢推近"/></label><label className="pf-wide">参考图<select value={row.input_asset_id} onChange={event=>updateRow(row.key,{input_asset_id:event.target.value})}><option value="">不使用参考图</option>{row.input_asset_id&&!assets.some(asset=>asset.id===row.input_asset_id)&&<option value={row.input_asset_id}>已保存的参考图（不在本次素材列表）</option>}{assets.filter(asset=>asset.kind==='image').map(asset=><option key={asset.id} value={asset.id}>{asset.source==='upload'?'上传原图':'生成图片'} · {asset.width}×{asset.height} · {displayDate(asset.created_at)}</option>)}</select><span className="pf-small">从素材库选择；需要新图片时，可先在快捷工作台上传。</span></label>{row.input_asset_id&&<img className="pf-reference" src={mediaUrl(row.input_asset_id)} alt={`镜头 ${index+1} 参考图`}/>}
        <label className="pf-wide">生成提示词<textarea rows={3} value={row.prompt} onChange={event=>updateRow(row.key,{prompt:event.target.value})} placeholder="保存后供分镜与视频生成使用"/><button type="button" className="pf-link" onClick={()=>updateRow(row.key,{prompt:row.description})}>用画面描述作为提示词</button></label></div></fieldset>
        <footer><span className="pf-small">不裁剪视频；片段实际时长以生成产物探测为准。</span><div className="pf-actions"><button className="pf-button" disabled={disabled||!!shotError||!changed||!script} onClick={()=>void saveRow(row,index)}><Save size={15}/>保存镜头</button><button className="pf-button pf-primary" disabled={disabled||!row.shot_id||changed||!row.prompt.trim()} onClick={()=>void estimateBoard(row)}><ImageIcon size={15}/>估价并生成分镜</button></div></footer>
        {boards.length>0&&<div className="pf-boards">{boards.map(board=>{const task=tasks.find(item=>item.id===board.task_id),assetId=board.asset_id??task?.output_asset_id,working=task?isActiveStatus(task.status):isActiveStatus(board.status);return <section className="pf-board" key={board.storyboard_id}><div className="pf-board-media">{assetId?<a href={mediaUrl(assetId)} target="_blank" rel="noreferrer"><img src={mediaUrl(assetId)} alt={`镜头 ${index+1} 分镜 v${board.version}`}/></a>:<span>{working?<Loading/>:<ImageIcon size={25}/>} {task?names[task.status]??task.status:names[board.status]??'等待读取图片结果'}</span>}</div><div><strong>分镜 v{board.version}</strong><p className="pf-small">{names[board.status]??board.status}{task?.error_message?` · ${task.error_message}`:''}</p>{board.reject_reason&&<p className="pf-small">驳回原因：{board.reject_reason}</p>}<div className="pf-actions"><button className="pf-button" disabled={disabled||!assetId||working||board.status==='approved'} onClick={()=>void boardReview(board,true)}><Check size={14}/>审核通过</button></div><label>驳回原因<input value={rejections[board.storyboard_id]??''} onChange={event=>setRejections(current=>({...current,[board.storyboard_id]:event.target.value}))} disabled={disabled} placeholder="说明画面哪里不符合要求"/></label><button className="pf-link" disabled={disabled||!assetId||!rejections[board.storyboard_id]?.trim()} onClick={()=>void boardReview(board,false)}><X size={14}/>驳回此版本</button></div></section>;})}</div>}
      </article>;})}</div>
      {storyQuote&&<div className="pf-confirm" role="region" aria-label="分镜生成费用确认"><h4>确认这一版分镜生成</h4><p>{models.find(model=>model.id===storyQuote.modelId)?.name} · {storyQuote.input.size} · 预估 <strong>{money(storyQuote.price)}</strong></p><p className="pf-small">预估不等于账单。点击后才创建图片生成任务；镜头输入变化后需要重新估价。</p><div className="pf-actions"><button className="pf-button" disabled={!!busy} onClick={()=>setStoryQuote(null)}>暂不生成</button><button className="pf-button pf-primary" disabled={disabled||storyQuote.fingerprint!==storyFingerprint||!!getModelUnavailableReason(imageModel)} onClick={()=>void submitBoard()}>确认费用并生成分镜</button></div></div>}
    </section>
    <section className="pf-section" hidden={!!panel&&panel!=='generation'} aria-labelledby="pf-scope-title"><div className="pf-section-heading"><h3 id="pf-scope-title"><span>04</span>整组视频生成</h3><span className="pf-small">先估价，再明确确认</span></div><fieldset className="pf-fields" disabled={disabled}><div className="pf-modes"><label><input type="radio" name={`production-mode-${projectId}`} checked={mode==='full'} disabled={retryOnly} onChange={()=>{invalidate();setMode('full');}}/><span><strong>完整视频</strong><small>整组成功后，服务端自动按顺序拼接。</small></span></label><label><input type="radio" name={`production-mode-${projectId}`} checked={mode==='clips'} onChange={()=>{invalidate();setMode('clips');}}/><span><strong>分镜视频</strong><small>保留独立片段，稍后选择成功版本合成。</small></span></label></div><div className="pf-checks">{shots.map((shot,index)=><label key={shot.shot_id}><input type="checkbox" checked={selectedIds.includes(shot.shot_id)} disabled={retryOnly&&!failedShotIds(status).includes(shot.shot_id)&&!selectedIds.includes(shot.shot_id)} onChange={event=>{if(event.target.checked&&retryOnly&&!failedShotIds(status).includes(shot.shot_id))return;invalidate();setSelectedIds(current=>event.target.checked?[...current,shot.shot_id]:current.filter(id=>id!==shot.shot_id));}}/>镜头 {index+1} · {shot.duration_s} 秒（计划）</label>)}</div></fieldset>
      <div className="pf-actions"><button className="pf-button pf-primary" disabled={disabled||dirty||!selectedIds.length||!!videoError} onClick={()=>void quoteScope()}>获取整组报价（免费）</button><button className="pf-button" disabled={disabled||!failedShotIds(status).length} onClick={()=>{invalidate();setSelectedIds(failedShotIds(status));setRetryOnly(true);setMode('clips');setNotice('只选择失败镜头生成独立片段。成功片段保留，重试也需要重新报价和确认。');}}>仅对失败镜生成片段</button></div>
      {retryOnly&&<div className="pf-notice"><span>当前仅重试失败镜头，可取消勾选缩小范围；状态变化后需重新核对。成功片段会保留。</span><button className="pf-link" disabled={disabled} onClick={()=>{invalidate();setRetryOnly(false);}}>退出失败镜重试，改为普通生成</button></div>}
      {scope&&<div className="pf-confirm" aria-label="整组生成报价确认"><h4>{scope.quote.mode==='full'?'完整视频':'分镜视频'} · {scope.quote.shots.length} 个镜头</h4><table><thead><tr><th>镜头</th><th>计划时长</th><th>费用预估</th></tr></thead><tbody>{scope.quote.shots.map(item=><tr key={item.shot_id}><td>镜头 {shots.findIndex(shot=>shot.shot_id===item.shot_id)+1}</td><td>{item.duration_s} 秒</td><td>{money(item.estimate_cny)}</td></tr>)}</tbody></table><div className="pf-quote-total"><span>计划总时长 {scope.quote.planned_film_duration_s} 秒</span><strong>合计预估 {money(scope.quote.total_estimate_cny)}</strong></div><p className="pf-small">有效至 {displayDate(scope.quote.expires_at)}；实际时长和账单以完成结果为准。任务使用现有队列顺序处理。</p>{scopeCheck&&!scopeCheck.ok&&<p className="pf-warning-text">{scopeCheck.reason}</p>}<div className="pf-actions"><button className="pf-button" disabled={!!busy} onClick={clearScope}>返回修改</button><button className="pf-button pf-primary" disabled={disabled||dirty||!!videoError||!scopeCheck?.ok||scope.fingerprint!==fingerprint} onClick={()=>void submitScope()}>确认报价并开始生成</button></div></div>}
      {statusError&&<div className="pf-notice pf-warning">运行状态暂不可用：{statusError}。不会循环请求未上线接口。</div>}
      {status&&<div className="pf-status"><h4>逐镜运行状态</h4>{status.batch&&<p className="pf-small">排队 {status.batch.queued} · 执行 {status.batch.running} · 成功 {status.batch.succeeded} · 失败 {status.batch.failed}</p>}{status.shots.map(item=><div className="pf-status-row" key={item.shot_id}><span>镜头 {shots.findIndex(shot=>shot.shot_id===item.shot_id)+1}</span><strong>{names[item.status]??item.status}</strong><span>{durationLabel(item.actual_duration_ms)}</span>{item.error_message||item.error_code?<small>{item.error_message??item.error_code}</small>:null}</div>)}<p className="pf-small">仅页面可见且存在活动任务时轮询；失败任务不会自动重新生成。</p></div>}
    </section>
    <section className="pf-section" hidden={!!panel&&panel!=='delivery'} aria-labelledby="pf-film-title"><div className="pf-section-heading"><h3 id="pf-film-title"><span>05</span>成功片段与成片</h3><button className="pf-button" disabled={disabled||!shots.length} onClick={requestExport}><Download size={15}/>导出项目包</button></div>
      <p className="pf-small">当前接口只返回各镜最新片段，更早版本未列出。已有成功片段可能来自历史生成。修改计划不会改写旧片段，请逐个预览核对，再选择用于本次合成的版本。</p><div className="pf-clips">{shots.map((shot,index)=>{const takes=successfulTakes(shot,status),take=takes.find(item=>item.take_id===selectedTakeIds[index]);return <article key={shot.shot_id}><header><strong>镜头 {index+1}</strong><span className="pf-small">计划 {shot.duration_s} 秒</span></header>{take?.asset_id?<video src={mediaUrl(take.asset_id)} controls preload="metadata"/>:<div className="pf-clip-empty">尚无可用的成功片段</div>}<label>用于合成的片段<select value={selectedTakeIds[index]} disabled={disabled||!takes.length} onChange={event=>{setFilmConfirm(null);setPartialAccepted(false);setSelectedTakes(current=>({...current,[shot.shot_id]:event.target.value}));}}><option value="">无可用片段</option>{takes.map((item,takeIndex)=><option key={item.take_id} value={item.take_id}>成功片段 {takeIndex+1} · {durationLabel(item.actual_duration_ms)}</option>)}</select></label>{take&&<p className="pf-small">{durationLabel(take.actual_duration_ms)}</p>}</article>;})}</div>
      <div className="pf-actions"><button className="pf-button pf-primary" disabled={disabled||!shots.length||missingShots.length>0} onClick={()=>prepareFilm(false)}><FilmIcon size={15}/>确认完整合成</button><button className="pf-button" disabled={disabled||!selectedTakeIds.some(Boolean)||missingShots.length===0} onClick={()=>prepareFilm(true)}>确认部分合成（缺 {missingShots.length} 镜）</button></div>
      {filmConfirm&&<div className="pf-confirm" aria-label="成片拼接确认"><h4>{filmConfirm.partial?'部分合成':'完整合成'} · {filmConfirm.takeIds.length} 个成功片段</h4><p>按当前镜头顺序拼接，不裁剪、不重新生成已有片段。{filmConfirm.partial?`本次缺少 ${filmConfirm.missing.length} 个镜头，结果将标记为部分成片。`:'本次包含所有镜头。'}</p>{filmConfirm.partial&&<label className="pf-checkbox"><input type="checkbox" checked={partialAccepted} onChange={event=>setPartialAccepted(event.target.checked)}/>我接受缺镜，并明确同意生成部分成片</label>}<div className="pf-actions"><button className="pf-button" disabled={!!busy} onClick={()=>setFilmConfirm(null)}>取消</button><button className="pf-button pf-primary" disabled={disabled||(filmConfirm.partial&&!partialAccepted)} onClick={()=>void composeFilm()}>确认并开始拼接</button></div></div>}
      <div className="pf-film-history"><h4><Layers3 size={16}/>成片历史</h4>{historyError&&<p className="pf-small pf-warning-text">完整历史列表暂不可用：{historyError}。下方仅展示服务端已返回的成片。</p>}{films.length?films.map(film=><article className="pf-film" key={film.film_id}><div><h4>{film.version?`成片 v${film.version}`:'成片'}<span className="pf-badge">{film.partial?'部分成片':film.partial===false?'完整成片':'完整性待核对'}</span></h4><p className="pf-small">{names[film.status]??film.status} · 计划 {film.planned_duration_s??'—'} 秒 · {durationLabel(film.actual_duration_ms)}</p>{film.error_message&&<p className="pf-warning-text">{film.error_message}</p>}{film.missing_shot_ids?.length? <p className="pf-small">缺少 {film.missing_shot_ids.length} 个镜头</p>:null}</div>{(film.status==='ready'||film.status==='partial')&&film.asset_id?<><video src={productionApi.filmFileUrl(film)} controls preload="metadata"/><a className="pf-button" href={productionApi.filmFileUrl(film)} download><Download size={15}/>下载此版成片</a></>:<button className="pf-button" disabled={!!busy} onClick={()=>void perform('读取成片状态',false,signal=>productionApi.getFilm(film.film_id,{signal}),next=>setFilms(current=>[...current.filter(item=>item.film_id!==next.film_id),next]))}>读取成片状态</button>}</article>):<p className="pf-small">尚未读取到成片结果。</p>}</div>
      {exportResult&&<div className="pf-notice"><a className="pf-button" href={exportResult.download_url} download={exportResult.file_name}><Download size={15}/>下载 {exportResult.file_name}</a><p className="pf-small">导出已有成片、片段、分镜与清单，不重新生成。</p>{exportResult.missing.length>0&&<p className="pf-warning-text">此包有 {exportResult.missing.length} 个缺失项：{exportResult.missing.map(item=>`${item.kind}（${item.reason}）`).join('、')}。详细情况请查看包内 manifest.json。</p>}</div>}
    </section>
  </div>;
}
