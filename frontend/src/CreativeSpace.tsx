import { withDefaultNodeModel } from './creative/node-config';
import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowLeft, Check, FileStack, FileText, FolderOpen, LoaderCircle, MessageSquare, Plus, RefreshCw, Trash2, X } from 'lucide-react';
import { api, ApiError, mediaUrl } from './api';
import { creativeApi } from './creative/api';
import type { CreativeProjectDetail, CreativeScript, ProjectBrief, ScriptContent } from './creative/types';
import {
  briefEditor, buildBrief, EMPTY_SCRIPT, latestScript, parseScriptText,
  prettyContent, readLocalProject, scriptHasContent, storeLocalProject,
} from './creative/state';
import type { BriefEditor, ProjectEditSnapshot, UncertainOperation } from './creative/state';
import ProductionFlow from './creative/ProductionFlow';
import FreeCreationCanvas from './creative/FreeCreationCanvas';
import type { FreeCreationNode, FreeCanvasController } from './creative/FreeCreationCanvas';
import type { CanvasNodeRecord, SavedCanvasLayout } from './creative/canvas-layout-sync';
import type { FreePoint } from './creative/free-canvas-state';
import type { Asset, Model } from './types';
import CanvasNodeSettings from './creative/CanvasNodeSettings';
import { uploadCanvasFile } from './creative/canvas-upload';
import { sameNodeConfig, textValue, type NodeConfig } from './creative/node-config';
import { buildReferenceConnection, referenceImageId } from './creative/canvas-reference-connection';
import { legacyCanvasShots } from './creative/director-canvas-projection';
import { CanvasProgressTracker } from './creative/canvas-progress-sync';
import { deriveCanvasReferenceEdges, hasObservedExecution, mergeCanvasExecutionState, nodeActions, readPendingGeneration, storePendingGeneration } from './creative/canvas-node-actions';
import type { DeletionOperation, NodeExecution, NodeQuote, PendingNodeGeneration } from './creative/canvas-node-actions';
import StudioDialog from './creative/StudioDialog';
import DirectorChat from './creative/DirectorChat';
import { confirmScriptDraft } from './creative/confirmScriptDraft';
import StructureTemplates from './creative/StructureTemplates';
import type { Shot, ProductionStatus, Film } from './creative/production-types';
import './CreativeSpace.css';
import './DirectorStudio.css';
import { readDirector, type DirectorReadiness } from './creative/director';

export interface CreativeSpaceProps {
  onBack: () => void;
  initialProjectId: string;
  onDirtyChange?: (dirty: boolean) => void;
}
const errorMessage = (error: unknown) => error instanceof Error ? error.message : '读取失败，请稍后重新读取。';
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
function Spinner() { return <LoaderCircle size={16} className="cs-spin" aria-hidden="true" />; }

export default function CreativeSpace({ onBack, onDirtyChange, initialProjectId }: CreativeSpaceProps) {
  const changed=useCallback(async()=>{},[]);
  const setDirty=useCallback((dirty:boolean)=>onDirtyChange?.(dirty),[onDirtyChange]);
  useEffect(()=>()=>onDirtyChange?.(false),[onDirtyChange]);
  return <div className="creative-space"><div className="cs-shell">
    <div className="cs-layout"><main className="cs-main"><ProjectEditor key={initialProjectId} projectId={initialProjectId} onDirtyChange={setDirty} onChanged={changed} onBack={onBack}/></main></div>
  </div></div>;
}

function ProjectEditor({ projectId, onDirtyChange, onChanged, onBack }: { projectId: string; onDirtyChange: (dirty: boolean) => void; onChanged: () => Promise<void>; onBack: () => void }) {
  const [productionDirty,setProductionDirty]=useState(false);
  const [nodeDirty,setNodeDirty]=useState(false),[nodeBusy,setNodeBusy]=useState(false);
  const [canvasSnapshot,setCanvasSnapshot]=useState<SavedCanvasLayout|null>(null);
  const canvasSnapshotRef=useRef(canvasSnapshot);canvasSnapshotRef.current=canvasSnapshot;
  const [progressSyncPending,setProgressSyncPending]=useState(false);
  const progressSync=useRef(new CanvasProgressTracker());
  const receiveProgress=useCallback(()=>{progressSync.current.notify();setProgressSyncPending(true);},[]);
  const [nodeState,setNodeState]=useState<SavedCanvasLayout|null>(null);
  const [acceptedGeneration,setAcceptedGeneration]=useState<{execution:NodeExecution;layoutRevision:number}|null>(null);
  const [nodeStateError,setNodeStateError]=useState('');
  const [deletions,setDeletions]=useState<DeletionOperation[]>([]);
  const [quotePreview,setQuotePreview]=useState<{nodeId:string;title:string;quote:NodeQuote}|null>(null);
  const [pendingGeneration,setPendingGeneration]=useState<PendingNodeGeneration|null>(()=>readPendingGeneration(projectId));
  const refreshDeletions=useCallback(async()=>{setDeletions(await nodeActions.deletions(projectId));},[projectId]);
  useEffect(()=>{let alive=true;void nodeActions.deletions(projectId).then(value=>{if(alive)setDeletions(value);}).catch(()=>{});return()=>{alive=false;};},[projectId]);
  const canvasController=useRef<FreeCanvasController|null>(null);
  const attachCanvas=useCallback((controller:FreeCanvasController|null)=>{canvasController.current=controller;},[]);
  const [chatOpen,setChatOpen]=useState(()=>window.innerWidth>700);
  const [templatesOpen,setTemplatesOpen]=useState(false),[libraryOpen,setLibraryOpen]=useState(false);
  const [assets,setAssets]=useState<Asset[]>([]),[models,setModels]=useState<Model[]>([]),[resourcesError,setResourcesError]=useState('');
  const [unplacedAssets,setUnplacedAssets]=useState<Array<{id:string;name:string}>>([]);
  const [narrow,setNarrow]=useState(()=>window.innerWidth<=700);
  const nodeLock=useRef(false);
  useEffect(()=>{const m=window.matchMedia('(max-width:700px)'),change=()=>setNarrow(m.matches);m.addEventListener('change',change);return()=>m.removeEventListener('change',change);},[]);
  const loadResources=useCallback(async()=>{const values=await Promise.allSettled([api.assets(),api.models()]);if(values[0].status==='fulfilled')setAssets(values[0].value.items);if(values[1].status==='fulfilled')setModels(values[1].value.items);setResourcesError(values.some(v=>v.status==='rejected')?'素材或模型暂未读取完整，请重新读取。':'');},[]);
  useEffect(()=>{void loadResources();},[loadResources]);
  const [templateDirty,setTemplateDirty]=useState(false);
  const [templatePending,setTemplatePending]=useState(false);
  const [productionPending,setProductionPending]=useState(false);
  const [director, setDirector] = useState<DirectorReadiness | null>(null);
  const [directorError, setDirectorError] = useState('');
  const directorRead = useRef<AbortController | null>(null);
  const [productionRefresh,setProductionRefresh]=useState(0);
  const [chatPending,setChatPending]=useState(false);
  const [chatDirty,setChatDirty]=useState(false);
  const [selectedNodeId,setSelectedNodeId]=useState<string|null>(null);
  const [selectedNodeIds,setSelectedNodeIds]=useState<string[]>([]);
  const [conversationSelectionVersion,setConversationSelectionVersion]=useState(0);
  const [conversationRole,setConversationRole]=useState('writer');
  const receiveSelection=useCallback((ids:string[],origin:'user'|'programmatic'='user')=>{
    setSelectedNodeIds(current=>current.length===ids.length&&current.every((id,index)=>id===ids[index])?current:ids);
    if(origin==='user')setConversationSelectionVersion(value=>value+1);
  },[]);
  const [manualScript,setManualScript]=useState(false);
  const [productionSnapshot,setProductionSnapshot]=useState<{shots:Shot[];status:ProductionStatus|null;films?:Film[];loaded?:boolean;error?:string}>({shots:[],status:null});
  const receiveSnapshot=useCallback((data:{shots:Shot[];status:ProductionStatus|null;films?:Film[];loaded?:boolean;error?:string})=>setProductionSnapshot(data),[]);

  const [project, setProject] = useState<CreativeProjectDetail | null>(null);
  const [scripts, setScripts] = useState<CreativeScript[]>([]);
  const [brief, setBrief] = useState<BriefEditor>(() => briefEditor('', {}));
  const [scriptId, setScriptId] = useState<string | null>(null);
  const [scriptText, setScriptText] = useState(EMPTY_SCRIPT);
  const [savedScriptText, setSavedScriptText] = useState(EMPTY_SCRIPT);
  const [baseVersion, setBaseVersion] = useState(0);
  const [instruction, setInstruction] = useState('');
  const [loading, setLoading] = useState(true);
  const [initialized, setInitialized] = useState(false);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [missing,setMissing]=useState(false);
  const [notice, setNotice] = useState('');
  const [storageFailed, setStorageFailed] = useState(false);
  const [stale, setStale] = useState(() => readLocalProject(projectId)?.stale ?? false);
  const [uncertain, setUncertain] = useState<UncertainOperation | undefined>(() => readLocalProject(projectId)?.uncertain);
  const [recovered, setRecovered] = useState(false);
  const [conflict, setConflict] = useState<CreativeScript | null>(null);
  const [conflictPending, setConflictPending] = useState(false);
  const mounted = useRef(true);
  const controllerRef = useRef<AbortController | null>(null);
  const readController = useRef<AbortController | null>(null);
  const mutationLock = useRef(false);
  const editing = useRef<ProjectEditSnapshot>({ brief, scriptId, scriptText, baseVersion, instruction });
  editing.current = { brief, scriptId, scriptText, baseVersion, instruction };
  const newest = latestScript(scripts);
  const selectedScript = scripts.find(script => script.script_id === scriptId) ?? null;
  const briefDirty = initialized && !!project && JSON.stringify(brief) !== JSON.stringify(briefEditor(project.name, project.brief));
  const scriptDirty = initialized && scriptText !== savedScriptText;
  const productionReady=!!director?.current.confirmation.valid && director.current.script?.script_id===selectedScript?.script_id && director.current.script?.version===selectedScript?.version;
  const dirty = briefDirty || scriptDirty || !!instruction.trim() || !!busy || productionDirty || templateDirty || chatPending || chatDirty || nodeDirty || nodeBusy || !!pendingGeneration;
  const parsed = parseScriptText(scriptText);
  const content = parsed.content;
  const blocked = loading || !!busy || !!uncertain || templatePending || productionPending || chatPending;
  const renderedCanvas=mergeCanvasExecutionState(canvasSnapshot,nodeState,acceptedGeneration?.execution??null);
  const hasNodeTasks=progressSyncPending||!!acceptedGeneration||(renderedCanvas?.nodes??[]).some(node=>node.last_execution?.active===true);
  const releaseGeneration=useCallback((requestId:string)=>{
    try{const stored=readPendingGeneration(projectId);if(!stored||stored.requestId===requestId)storePendingGeneration(projectId,null);}
    catch{setStorageFailed(true);}
    setPendingGeneration(current=>current?.requestId===requestId?null:current);
  },[projectId]);
  useEffect(()=>{
    if(!hasNodeTasks){setNodeStateError('');return;}
    let cancelled=false,timer:number|undefined;
    const controller=new AbortController();
    const poll=async()=>{
      try{
        const readToken=progressSync.current.beginRead();
        // Read task/output fields independently. The canvas owns layout reconciliation.
        const state=await nodeActions.readState(projectId,controller.signal);
        if(cancelled)return;
        setNodeState(state);setNodeStateError('');
        // Polling only updates execution fields. A newly published result node
        // needs one deliberate membership read through the layout controller.
        if(state.nodes.some(node=>!canvasSnapshotRef.current?.nodes.some(saved=>saved.node_id===node.node_id))){
          if(nodeLock.current){if(!cancelled)timer=window.setTimeout(()=>void poll(),2500);return;}
          await canvasController.current?.read();
        }
        if(!cancelled)setProgressSyncPending(progressSync.current.completeRead(readToken));
        if(!state.nodes.some(node=>node.last_execution?.active===true))void loadResources();
        if(acceptedGeneration&&hasObservedExecution(state,acceptedGeneration.execution,acceptedGeneration.layoutRevision)){
          releaseGeneration(acceptedGeneration.execution.client_request_id);
          setAcceptedGeneration(current=>current?.execution.execution_id===acceptedGeneration.execution.execution_id?null:current);
        }
      }catch{if(!cancelled)setNodeStateError('任务进度暂未更新，正在重新查询。');}
      if(!cancelled)timer=window.setTimeout(()=>void poll(),2500);
    };
    void poll();
    return()=>{cancelled=true;controller.abort();if(timer!==undefined)clearTimeout(timer);};
  },[hasNodeTasks,projectId,acceptedGeneration,releaseGeneration,loadResources]);
  useEffect(() => { onDirtyChange(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => { const warn=(event:BeforeUnloadEvent)=>{if(dirty){event.preventDefault();event.returnValue='';}};window.addEventListener('beforeunload',warn);return()=>window.removeEventListener('beforeunload',warn); },[dirty]);
  const refreshDirector = useCallback(async () => {
    directorRead.current?.abort(); const controller = new AbortController(); directorRead.current = controller;
    try {
      const next = await readDirector(projectId, { signal: controller.signal });
      if(controller.signal.aborted || !mounted.current) return;
      setDirector(next); setDirectorError('');

    } catch(caught) { if(!controller.signal.aborted&&mounted.current){setDirector(null);setDirectorError(errorMessage(caught));} }
  }, [projectId]);
  useEffect(()=>{void refreshDirector();return()=>directorRead.current?.abort();},[refreshDirector, productionSnapshot, productionRefresh]);
  const persist = useCallback((pending: UncertainOperation | undefined, staleFlag: boolean, keepEdit = true) => {
    const ok = storeLocalProject(projectId, { version: 1, stale: staleFlag, uncertain: pending, ...(keepEdit ? { edit: editing.current } : {}) });
    if (!ok && mounted.current) setStorageFailed(true);
  }, [projectId]);
  useEffect(() => { if (initialized) persist(uncertain, stale, briefDirty || scriptDirty || !!instruction.trim()); }, [initialized, brief, scriptId, scriptText, baseVersion, instruction, uncertain, stale, briefDirty, scriptDirty, persist]);
  const adoptScript = (script: CreativeScript | null, latestVersion: number) => {
    setScriptId(script?.script_id ?? null); setScriptText(script ? prettyContent(script.content) : EMPTY_SCRIPT);
    setSavedScriptText(script ? prettyContent(script.content) : EMPTY_SCRIPT); setBaseVersion(latestVersion); setConflict(null); setConflictPending(false);
  };
  useEffect(()=>{
    const binding=canvasSnapshot?.nodes.find(node=>node.node_id==='script')?.binding;
    if(!initialized||!binding?.script_id||binding.script_id===scriptId)return;
    // A freshly acknowledged script may arrive before its refreshed canvas snapshot.
    // Never let that older snapshot move the editor back to an earlier version.
    const selectedVersion=scripts.find(s=>s.script_id===scriptId)?.version;
    if(selectedVersion!==undefined&&(binding.script_version??0)<selectedVersion)return;
    const controller=new AbortController();
    const adopt=async()=>{
      try{const target=scripts.find(s=>s.script_id===binding.script_id)??await creativeApi.getScript(binding.script_id!,{signal:controller.signal});
        if(controller.signal.aborted)return;
        if(target.project_id!==projectId||target.version!==binding.script_version)throw new Error('脚本内容需要重新读取，请刷新项目。');
        if(scriptDirty){setConflict(target);setConflictPending(true);return;}
        setScripts(items=>items.some(s=>s.script_id===target.script_id)?items:[...items,target]);adoptScript(target,Math.max(target.version,latestScript(scripts)?.version??0));
      }catch(e){if(!controller.signal.aborted)setError(errorMessage(e));}
    };void adopt();return()=>controller.abort();
  },[canvasSnapshot,initialized,projectId,scriptId,scripts,scriptDirty]);
  const fetchProject = async (preserve = true, focusId?: string) => {
    readController.current?.abort(); const controller = new AbortController(); readController.current = controller;
    setLoading(true);
    try {
      const [next, versions] = await Promise.all([creativeApi.getProject(projectId, { signal: controller.signal }), creativeApi.listScripts(projectId, { signal: controller.signal })]);
      if (controller.signal.aborted || !mounted.current) return null;
      if (next.project_id !== projectId || versions.items.some(script => script.project_id !== projectId)) throw new Error('项目响应不匹配，已停止更新页面。');
      setMissing(false); setProject(next); setScripts(versions.items);
      const latest = latestScript(versions.items);
      if (!preserve) { setBrief(briefEditor(next.name, next.brief)); adoptScript(versions.items.find(script => script.script_id === focusId) ?? latest, latest?.version ?? 0); }
      setRecovered(true); return { project: next, scripts: versions.items, latest };
    } finally { if (!controller.signal.aborted && mounted.current) setLoading(false); }
  };
  const restoreEditing = (versions: CreativeScript[]) => {
    const local = readLocalProject(projectId);
    if (!local?.edit) return;
    const edit = local.edit; setBrief(edit.brief); setScriptId(edit.scriptId); setScriptText(edit.scriptText); setBaseVersion(edit.baseVersion); setInstruction(edit.instruction);
    const saved = versions.find(script => script.script_id === edit.scriptId);
    setSavedScriptText(saved ? prettyContent(saved.content) : EMPTY_SCRIPT);
    setNotice('已恢复上次的编辑。');
  };
  useEffect(() => {
    mounted.current = true; let cancelled = false;
    void (async () => {
      try {
        const loaded = await fetchProject(false);
        if (!loaded || !mounted.current || cancelled) return;
        restoreEditing(loaded.scripts); setInitialized(true);
      } catch (caught) { if (mounted.current && !cancelled) { const absent=caught instanceof ApiError && ['PROJECT_NOT_FOUND','PROJECT_DELETED'].includes(caught.code); setMissing(absent); setError(absent?'这个项目已不存在。其他项目和素材仍保留，可返回导演台继续创作。':errorMessage(caught)); } }
    })();
    return () => { cancelled = true; mounted.current = false; readController.current?.abort(); controllerRef.current?.abort(); };
    // A keyed editor owns exactly one project for its entire mounted lifetime.
  }, [projectId]);
  const recover = async () => {
    setError('');
    try {
      const loaded = await fetchProject(initialized&&(briefDirty||scriptDirty||productionDirty));
      if (!loaded) return;
      if (!initialized) { restoreEditing(loaded.scripts); setInitialized(true); }
      if (conflictPending) setConflict(loaded.latest);
      await refreshDirector(); await canvasController.current?.read(); await refreshDeletions(); setProductionRefresh(value=>value+1); setNotice('项目已更新。');
    } catch (caught) { if (mounted.current) setError(errorMessage(caught)); }
  };
  const mutate = async <T,>(label: string, action: (signal: AbortSignal) => Promise<T>, success: (result: T) => Promise<void> | void) => {
    if (mutationLock.current || uncertain) return;
    mutationLock.current = true; setBusy(label); setError(''); setNotice(''); setRecovered(false);
    const marker = { label, startedAt: new Date().toISOString(), beforeVersion: newest?.version ?? 0 };
    setUncertain(marker); persist(marker, stale);
    const controller = new AbortController(); controllerRef.current = controller;
    try {
      const result = await action(controller.signal);
      if (!mounted.current) return;
      // A successful write must not become repeatable just because its follow-up GET fails.
      const acknowledged = { ...marker, label: `${label}已完成，等待核对` };
      setUncertain(acknowledged); persist(acknowledged, stale);
      if (record(result) && typeof result.script_id === 'string' && typeof result.version === 'number' && record(result.content)) {
        const savedText = prettyContent(result.content as ScriptContent);
        setScriptId(result.script_id); setScriptText(savedText); setSavedScriptText(savedText); setBaseVersion(result.version);
        editing.current = { ...editing.current, scriptId: result.script_id, scriptText: savedText, baseVersion: result.version };
        persist(acknowledged, stale);
      }
      try { await success(result); await refreshDirector(); if (mounted.current) { setUncertain(undefined); persist(undefined, stale); } }
      catch (caught) { if (mounted.current) setError(`操作已成功，但刷新结果失败：${errorMessage(caught)}请重新读取，不要重复提交。`); }
    } catch (caught) {
      if (!mounted.current) return;
      setError(errorMessage(caught));
      if (!(caught instanceof ApiError && caught.code === 'CREATIVE_RESULT_UNKNOWN')) { setUncertain(undefined); persist(undefined, stale); }
      if (caught instanceof ApiError && caught.status === 409) {
        setConflictPending(true);
        try { const loaded = await fetchProject(true); if (loaded) setConflict(loaded.latest); } catch { setNotice('编辑已保留；最新版本读取失败，请点击重新读取。'); }
      }
    } finally { mutationLock.current = false; if (mounted.current) setBusy(''); }
  };
  const saveBrief = async () => {
    if (!project) return; const checked = buildBrief(brief, project.brief);
    if (!checked.brief) { setError(checked.error ?? '需求填写有误。'); return; }
    await mutate('保存需求', signal => creativeApi.updateProject(projectId, { name: brief.name.trim(), brief: checked.brief as ProjectBrief }, { signal }), result => {
      setProject(current => current ? { ...current, ...result } : current); setBrief(briefEditor(result.name, result.brief));
      if (result.stale.includes('script_confirm')) { setStale(true); persist(undefined, true); }
      setNotice('需求已保存。生成脚本会使用已保存的商品资料。'); void onChanged();
    });
  };
  const confirmVersion = async (newBase?: number) => {
    if (!content || !scriptHasContent(content) || briefDirty || blocked || (conflictPending && newBase === undefined)) return;
    await mutate('确认脚本', signal => confirmScriptDraft({
      savedScript: selectedScript, dirty: scriptDirty,
      save: () => scriptId
        ? creativeApi.editScript(scriptId, { content, base_version: newBase ?? baseVersion }, { signal })
        : creativeApi.addManualScript(projectId, { content }, { signal }),
      onSaved: result => {
        const text = prettyContent(result.content);
        setScriptId(result.script_id); setScriptText(text); setSavedScriptText(text); setBaseVersion(result.version);
        editing.current = { ...editing.current, scriptId: result.script_id, scriptText: text, baseVersion: result.version };
        setScripts(current => [...current.filter(item => item.script_id !== result.script_id), { ...result, project_id: projectId, confirmed_at: null }]);
        // Retain the successful save even if the following confirmation has an unknown result.
        persist({ label: '脚本已保存，确认结果待核对', startedAt: new Date().toISOString(), beforeVersion: result.version }, true);
      },
      confirm: id => creativeApi.confirmScript(id, { signal }),
    }), async result => {
      const refreshed=await fetchProject(false, result.script_id);
      if(!refreshed?.scripts.some(item=>item.script_id===result.script_id)) throw new Error('确认结果暂未读取到，请刷新核对。');
      setStale(false); persist(undefined,false); setManualScript(false); setSelectedNodeId('script');setChatOpen(true);await canvasController.current?.read();
      setNotice('脚本已确认，接下来准备分镜。'); void onChanged();
    });
  };
  const changeContent = (patch: Partial<ScriptContent>) => { if (content) setScriptText(prettyContent({ ...content, ...patch })); };
  const selectNode=(id:string|null)=>{if(id!==selectedNodeId&&nodeDirty){setError('请先保存或撤销当前节点的修改，再切换节点。');return false;}setSelectedNodeId(id);if(narrow)setChatOpen(false);return true;};
  const requireCanvas=()=>{if(!canvasController.current)throw new Error('画布还在读取，请稍后再试。');return canvasController.current;};
  const createNode=async(kind:'text'|'image'|'video',position?:FreePoint,inputAssetId?:string)=>{
    if(nodeLock.current)return;
    if(nodeDirty){setError('请先保存或撤销当前节点的修改，再新建节点。');return;}
    nodeLock.current=true;setNodeBusy(true);setError('');
    try{const controller=requireCanvas(),id=`${kind}-${crypto.randomUUID()}`;
      const config:NodeConfig={title:kind==='text'?'文本笔记':kind==='image'?'图片创作':'视频创作',...(kind==='text'?{text:''}:{generation_mode:`${inputAssetId?'image':'text'}_to_${kind}`,prompt:'',...(inputAssetId?{input_asset_id:inputAssetId}:{})})};
      await controller.mutate({nodes:[{node_id:id,type:kind,position:position??controller.positionForNewNode(),config:kind==='text'?config:withDefaultNodeModel(config,kind,models)}]});setSelectedNodeId(id);if(narrow)setChatOpen(false);
    }catch(e){setError(errorMessage(e));}finally{nodeLock.current=false;setNodeBusy(false);}
  };
  const placeAsset=async(id:string,name:string,position?:FreePoint)=>{
    const controller=requireCanvas(),nodeId=`asset-${crypto.randomUUID()}`;
    await controller.mutate({nodes:[{node_id:nodeId,type:'asset',position:position??controller.positionForNewNode(),config:{title:name},binding:{asset_id:id}}]});selectNode(nodeId);
    setUnplacedAssets(items=>items.filter(a=>a.id!==id));
  };
  const importFiles=async(files:File[],position:FreePoint)=>{
    if(nodeLock.current)return;nodeLock.current=true;setNodeBusy(true);setError('');
    try{for(const [index,file] of files.entries()){
      const uploaded=await uploadCanvasFile(file);setUnplacedAssets(items=>[...items.filter(a=>a.id!==uploaded.asset_id),{id:uploaded.asset_id,name:file.name}]);
      await placeAsset(uploaded.asset_id,file.name,{x:position.x+index*330,y:position.y});
    }await loadResources();}catch(e){setError(errorMessage(e));}finally{nodeLock.current=false;setNodeBusy(false);}
  };
  const importExisting=async(asset:Asset)=>{if(nodeLock.current)return;nodeLock.current=true;setNodeBusy(true);setError('');try{await placeAsset(asset.id,asset.kind==='image'?'导入图片':'导入视频');setLibraryOpen(false);}catch(e){setError(errorMessage(e));}finally{nodeLock.current=false;setNodeBusy(false);}};
  const recoverAsset=async(asset:{id:string;name:string})=>{
    if(nodeLock.current)return;nodeLock.current=true;setNodeBusy(true);setError('');
    try{const saved=await requireCanvas().read(),existing=saved.nodes.find(node=>node.type==='asset'&&node.binding.asset_id===asset.id);
      if(existing){selectNode(existing.node_id);setUnplacedAssets(items=>items.filter(a=>a.id!==asset.id));}
      else await placeAsset(asset.id,asset.name);
      await loadResources();
    }catch(e){setError(errorMessage(e));}finally{nodeLock.current=false;setNodeBusy(false);}
  };
  const saveNode=async(node:CanvasNodeRecord,config:NodeConfig,revision:number)=>{await requireCanvas().mutate({nodes:[{node_id:node.node_id,config,expected_config_revision:revision}]});};
  const connectReference=async(sourceId:string,targetId:string)=>{
    if(nodeLock.current||chatPending||loading)throw new Error('请等待当前操作完成。');
    if(nodeDirty||scriptDirty||productionDirty)throw new Error('请先保存或撤销当前修改，再连接参考图。');
    nodeLock.current=true;setNodeBusy(true);setError('');
    try{
      const controller=requireCanvas(),saved=await controller.read();
      await controller.mutate(buildReferenceConnection(saved,sourceId,targetId,models));
      selectNode(targetId);setNotice('参考图已连接，可以继续调整提示词。');
    }catch(caught){setError(errorMessage(caught));throw caught;}finally{nodeLock.current=false;setNodeBusy(false);}
  };
  const deleteNodes=async(ids:string[],editorConfirmedClean=false)=>{
    if(nodeLock.current)throw new Error('请等待当前节点操作完成。');
    // NodeEditor checks its actual draft before calling this; its own delete-busy
    // signal also contributes to nodeDirty and must not block that same deletion.
    const ownCleanEditor=editorConfirmedClean&&ids.length===1&&ids[0]===selectedNodeId;
    if(nodeDirty&&!ownCleanEditor||scriptDirty||productionDirty){const message='请先保存或撤销当前修改，再删除节点。';setError(message);throw new Error(message);}
    nodeLock.current=true;setNodeBusy(true);setError('');
    try{const controller=requireCanvas(),saved=await controller.mutate({delete_node_ids:ids});
      if(saved.deletionOperationId)setDeletions(items=>[{operation_id:saved.deletionOperationId!,status:'deleted',node_ids:ids,created_at:Date.now()/1000},...items]);
      setSelectedNodeId(null);setManualScript(false);setNodeDirty(false);setNotice('节点已删除，可以点击撤销恢复。');
      try{await refreshDeletions();}catch{/* The acknowledged operation still permits undo. */}
    }catch(e){setError(errorMessage(e));throw e;}finally{nodeLock.current=false;setNodeBusy(false);}
  };
  const undoDelete=async()=>{
    if(nodeLock.current)return;
    if(nodeDirty||scriptDirty||productionDirty){setError('请先保存或撤销当前修改，再恢复节点。');return;}
    const operation=deletions.find(item=>item.status==='deleted');if(!operation)return;
    nodeLock.current=true;setNodeBusy(true);setError('');let acknowledged=false;
    try{const controller=requireCanvas(),saved=await controller.read();await nodeActions.undo(projectId,operation.operation_id,saved.revision);acknowledged=true;
      setDeletions(items=>items.map(item=>item.operation_id===operation.operation_id?{...item,status:'restored'}:item));
      await controller.read();await refreshDeletions();setNotice('已恢复误删的节点。');
    }catch(e){setError(acknowledged?'节点已恢复，画布暂未更新，请刷新核对。':errorMessage(e));throw e;}finally{nodeLock.current=false;setNodeBusy(false);}
  };
  const prepareGeneration=async(node:CanvasNodeRecord,config:NodeConfig,revision:number)=>{
    if(nodeLock.current||pendingGeneration)throw new Error('请先核对上一条生成请求。');
    if(node.last_execution?.active===true)throw new Error('这个节点仍在生成，请等待当前任务完成。');
    if(revision!==node.config_revision)throw new Error('节点设置已有更新，你的修改仍保留，请先核对当前设置。');
    nodeLock.current=true;setNodeBusy(true);setError('');
    try{const controller=requireCanvas();let saved=canvasSnapshot;
      if(!sameNodeConfig(node.config,config))saved=await controller.mutate({nodes:[{node_id:node.node_id,config,expected_config_revision:revision}]});
      const current=saved?.nodes.find(n=>n.node_id===node.node_id);if(!current)throw new Error('节点暂未读取，请刷新画布。');
      const quote=await nodeActions.quote(projectId,node.node_id,current.config_revision);
      setQuotePreview({nodeId:node.node_id,title:textValue(config.title)||'本次创作',quote});
    }catch(e){setError(errorMessage(e));throw e;}finally{nodeLock.current=false;setNodeBusy(false);}
  };
  const submitGeneration=async(recovery?:PendingNodeGeneration)=>{
    if(nodeLock.current||acceptedGeneration||(!recovery&&pendingGeneration))return;
    const pending=recovery??(quotePreview?{nodeId:quotePreview.nodeId,quoteId:quotePreview.quote.quote_id,requestId:crypto.randomUUID()}:null);if(!pending)return;
    nodeLock.current=true;setNodeBusy(true);setError('');
    try{
      const stored=readPendingGeneration(projectId);
      if(stored&&stored.requestId!==pending.requestId){setPendingGeneration(stored);setQuotePreview(null);throw new Error('这个项目还有一条生成请求需要核对，请先恢复原请求。');}
      try{storePendingGeneration(projectId,pending);}catch{throw new Error('恢复信息暂时无法保存，本次生成尚未提交。请恢复浏览器存储后重试。');}
      setPendingGeneration(pending);setQuotePreview(null);
      const execution=await nodeActions.generate(projectId,pending);
      if(!mounted.current)return;
      // A validated receipt is already accepted even if every subsequent GET fails.
      // Retain its durable recovery ID until a read-only snapshot confirms it.
      setAcceptedGeneration({execution,layoutRevision:canvasSnapshot?.revision??0});setNodeStateError('');
      setNotice(execution.active?'生成任务已提交，结果会出现在节点上。':'已核对原生成任务，正在更新节点。');
    }catch(e){if(!mounted.current)return;
      if(e instanceof ApiError&&e.status>=400&&e.status<500)releaseGeneration(pending.requestId);
      setError(errorMessage(e));
    }finally{nodeLock.current=false;setNodeBusy(false);}
  };
  const uploadReference=async(file:File):Promise<Asset>=>{
    const uploaded=await uploadCanvasFile(file);await loadResources();
    return {id:uploaded.asset_id,kind:uploaded.kind,mime:uploaded.mime,width:uploaded.width,height:uploaded.height,duration_ms:uploaded.duration_ms??undefined} as Asset;
  };
  if (!project) return <section className="cs-card cs-loading">{loading ? <><Spinner /><p>正在恢复项目与脚本版本…</p></> : <><h2>{missing?'项目已不存在':'项目暂时无法读取'}</h2><p role="alert">{error}</p><button className="cs-button cs-primary" onClick={onBack}>返回导演台</button><button className="cs-button" onClick={() => void recover()}><RefreshCw size={16} />重新读取</button></>}</section>;
  const statuses=productionSnapshot.status?.shots??[];
  const legacyShots=legacyCanvasShots(productionSnapshot.shots,statuses,renderedCanvas?.nodes??[]);
  const film=productionSnapshot.films?.[0]??productionSnapshot.status?.film??productionSnapshot.status?.films?.[0];
  const nodes:FreeCreationNode[]=[];
  if(selectedScript || manualScript || scriptDirty) nodes.push({id:'script',type:'脚本',title:'创作脚本',summary:typeof content?.summary==='string'?content.summary:'点击查看并调整脚本',status:productionReady&&!scriptDirty?'approved':'draft',x:60,y:80});
  if(legacyShots.length) nodes.push({id:'plan',type:'分镜设置',title:'镜头计划',summary:`${legacyShots.length} 个镜头 · 点击调整画面与生成设置`,status:productionReady?'ready':'stale',x:450,y:80});
  for(const [index,shot] of legacyShots.entries()) {
    const status=statuses.find(item=>item.shot_id===shot.shot_id);
    const board=[...(shot.storyboards??status?.storyboards??[])].sort((a,b)=>b.version-a.version)[0];
    nodes.push({id:`image:${shot.shot_id}`,type:'分镜图',title:`镜头 ${shot.seq} · 画面`,summary:shot.description,image:board?.asset_id?mediaUrl(board.asset_id):undefined,status:board?.status??'draft',x:840,y:80+index*380});
    const take=status ? status.takes?.[0] : shot.takes?.[0];
    const asset=status ? status.asset_id??take?.asset_id : take?.asset_id;
    if(status?.task_id || asset || take) nodes.push({id:`video:${shot.shot_id}`,type:'镜头视频',title:`镜头 ${shot.seq} · 视频`,summary:shot.description,video:asset?mediaUrl(asset):undefined,status:status?.status??take?.status??'queued',x:1230,y:80+index*380});
  }
  const allApproved=legacyShots.length>0 && legacyShots.every(shot=>{
    const status=statuses.find(item=>item.shot_id===shot.shot_id);
    const latest=[...(shot.storyboards??status?.storyboards??[])].sort((a,b)=>b.version-a.version)[0];
    return latest?.status==='approved';
  });
  if(allApproved || productionSnapshot.status?.batch) nodes.push({id:'generation',type:'视频生成',title:'生成全部镜头',summary:'选择直接合成一支视频，或分别生成镜头。',status:productionSnapshot.status?.batch?.running?'running':'ready',x:1230,y:-210});
  if(film || statuses.some(item=>item.asset_id || item.takes?.some(take=>take.asset_id))) nodes.push({id:'delivery',type:'作品',title:film?'成片视频':'镜头与成片',video:film?.asset_id?mediaUrl(film.asset_id):undefined,summary:film?.partial?'部分成片':'预览、合成与下载',status:film?.status??'ready',x:1620,y:80});
  const derivedIds=new Set(nodes.map(node=>node.id));
  for(const node of renderedCanvas?.nodes??[]){
    if(derivedIds.has(node.node_id)||!['text','image','video','asset'].includes(node.type))continue;
    const asset=node.asset??assets.find(a=>a.id===node.binding.asset_id);
    const displayRecord=asset?{...node,asset}:node,referenceId=referenceImageId(displayRecord);
    const outputMissing=node.last_execution?.status==='succeeded'&&node.last_execution.asset_available===false;
    nodes.push({id:node.node_id,type:node.type==='text'?'文本':node.type==='image'?'图片生成':node.type==='video'?'视频生成':'导入素材',title:textValue(node.config.title)||'未命名节点',summary:textValue(node.config.text)||textValue(node.config.prompt),...(asset&&!outputMissing?{[asset.kind==='video'?'video':'image']:mediaUrl(asset.id)}:{}),...(referenceId?{referenceImageAssetId:referenceId}:{}),...(['image','video'].includes(node.type)?{referenceTargetKind:node.type as 'image'|'video'}:{}),status:outputMissing?'unavailable':textValue(node.last_execution?.status)||'saved',x:node.position.x,y:node.position.y});
  }
  const removedDisplayIds=new Set(canvasSnapshot?.deletedNodeIds??[]);
  const presentNodeIds=new Set(canvasSnapshot?.nodes.map(node=>node.node_id));
  const visibleNodes=nodes.filter(node=>!removedDisplayIds.has(node.id)||presentNodeIds.has(node.id));
  const referenceEdges=deriveCanvasReferenceEdges(renderedCanvas?.nodes??[],new Set(visibleNodes.map(node=>node.id)),renderedCanvas?.edges);
  const conversationSelection=(selectedNodeIds.length?selectedNodeIds:selectedNodeId?[selectedNodeId]:[]).filter(id=>renderedCanvas?.nodes.some(node=>node.node_id===id));
  const selected=nodes.find(node=>node.id===selectedNodeId);
  const selectedRecord=renderedCanvas?.nodes.find(node=>node.node_id===selectedNodeId);
  const independent=selectedRecord&&['text','image','video','asset'].includes(selectedRecord.type)&&!derivedIds.has(selectedRecord.node_id);
  const panel=selectedNodeId==='generation'||selectedNodeId?.startsWith('video:')?'generation':selectedNodeId==='delivery'?'delivery':'storyboard';
  const focusShotId=selectedNodeId?.startsWith('image:')||selectedNodeId?.startsWith('video:')?selectedNodeId.slice(6):null;
  return <>

    <div className="ds-alerts">
      {productionSnapshot.error&&<div className="cs-notice cs-warning" role="alert">制作内容暂未完整读取。<button className="cs-link" disabled={!!busy||productionPending} onClick={()=>setProductionRefresh(value=>value+1)}>重新读取制作内容</button></div>}
      {briefDirty&&<div className="cs-notice" role="status">上次编辑的创作资料已恢复。<button type="button" disabled={blocked} onClick={()=>void saveBrief()}>保存这份资料</button></div>}
      {!!instruction.trim()&&<div className="cs-notice" role="status">上次的脚本补充要求：{instruction}<button type="button" onClick={()=>setInstruction('')}>关闭这份旧草稿</button></div>}
      {unplacedAssets.map(asset=><div key={asset.id} className="cs-notice" role="status">“{asset.name}”已上传，尚未放入画布。<button type="button" disabled={nodeBusy} onClick={()=>void recoverAsset(asset)}>加入画布</button></div>)}
      {pendingGeneration&&!acceptedGeneration&&<div className="cs-notice cs-warning" role="status">上次生成结果需要核对。<button type="button" disabled={nodeBusy} onClick={()=>void submitGeneration(pendingGeneration)}>恢复原生成请求</button></div>}
      {nodeStateError&&<div className="cs-notice cs-warning" role="status">{acceptedGeneration?'生成任务已受理。':''}{nodeStateError}</div>}
      {storageFailed && <div className="cs-notice cs-warning" role="alert">编辑备份暂时无法保存，请保持页面开启。</div>}
      {error && <div className="cs-notice cs-error" role="alert">{error}<button type="button" aria-label="关闭提示" onClick={()=>setError('')}><X size={14}/></button></div>}
      {notice && <div className="cs-notice" role="status">{notice}<button className="cs-link" onClick={()=>setNotice('')}>收起</button></div>}
      {uncertain && !busy && <div className="cs-notice cs-warning" role="alert"><strong>{uncertain.label}</strong><p>结果暂不明确，请先刷新核对，避免重复提交。</p><div className="cs-actions"><button className="cs-button" disabled={loading} onClick={() => void recover()}>刷新核对</button><button className="cs-button" disabled={!recovered || loading} onClick={() => { if (window.confirm('已核对最新内容？原请求仍可能完成，继续编辑不会自动重发。')) { setUncertain(undefined); persist(undefined, stale); setError(''); } }}>已核对，继续</button></div></div>}
      {directorError&&<div className="cs-notice cs-warning" role="alert">暂时无法核对制作进度。<button className="cs-link" onClick={()=>void refreshDirector()}>重试</button></div>}
    </div>
    <div className="director-workspace">
      <div className="director-project-pill"><button type="button" onClick={onBack} aria-label="返回导演台" title="返回导演台"><ArrowLeft size={16}/></button><strong title={project.name}>{project.name}</strong><span className="director-pill-divider"/><button type="button" onClick={()=>setTemplatesOpen(true)} aria-label="结构模板" title="结构模板"><FileStack size={16}/></button><button type="button" onClick={()=>{setLibraryOpen(true);void loadResources();}} aria-label="从素材库添加" title="从素材库添加"><FolderOpen size={16}/></button><button type="button" aria-label="刷新当前项目" title="刷新当前项目" disabled={blocked} onClick={()=>void recover()}><RefreshCw size={15}/></button></div>
      <aside className="director-conversation" hidden={!chatOpen}><DirectorChat projectId={projectId} nodeTitles={Object.fromEntries(visibleNodes.map(node=>[node.id,node.title]))} selectedNodeIds={conversationSelection} selectionVersion={conversationSelectionVersion} onRoleChange={setConversationRole} onProgress={receiveProgress} onCollapse={()=>setChatOpen(false)} disabled={loading||!!busy||!!uncertain||productionPending||templatePending||briefDirty||scriptDirty||productionDirty||nodeDirty||nodeBusy} onBusyChange={setChatPending} onDirtyChange={setChatDirty} onRefresh={async()=>{receiveProgress();await fetchProject(briefDirty||scriptDirty||productionDirty);await refreshDirector();setProductionRefresh(value=>value+1);await canvasController.current?.read();await loadResources();}} onRevealNode={id=>{selectNode(id);}}/></aside>
      {!chatOpen&&<button className="director-chat-launch" type="button" aria-label="打开创作对话" title="打开创作对话" onClick={()=>setChatOpen(true)}><MessageSquare size={17}/><span>{({writer:'AI 编剧',director:'AI 导演',cinematographer:'AI 摄影师',editor:'AI 剪辑师'} as Record<string,string>)[conversationRole]??'创作助手'}</span>{chatPending&&<Spinner/>}</button>}
      <FreeCreationCanvas projectId={projectId} nodes={visibleNodes} referenceEdges={referenceEdges} selectedNodeId={selectedNodeId} onSelect={selectNode} onSelectionChange={receiveSelection} onConnectReference={connectReference} onDeselect={()=>selectNode(null)} onSnapshot={setCanvasSnapshot} onController={attachCanvas} leftInset={chatOpen&&!narrow?340:0} onDeleteNodes={deleteNodes} onUndoDelete={undoDelete} canUndoDelete={deletions.some(operation=>operation.status==='deleted')} deletionBusy={nodeBusy||blocked} onAddNode={(kind,position)=>void createNode(kind,position)} onImport={(files,position)=>void importFiles(files,position)} toolbarContent={<button type="button" title="手写脚本" aria-label="手写脚本" disabled={blocked} onClick={()=>{setManualScript(true);selectNode('script');}}><FileText size={16}/><span>脚本</span></button>}>
        {independent&&selectedRecord&&<CanvasNodeSettings projectId={projectId} node={selectedRecord} assets={assets} models={models} disabled={nodeBusy} onDirtyChange={setNodeDirty} onSave={(config,revision)=>saveNode(selectedRecord,config,revision)} onGenerate={(config,revision)=>prepareGeneration(selectedRecord,config,revision)} onUploadReference={uploadReference} onDelete={()=>deleteNodes([selectedRecord.node_id],true)} onUseAsset={(kind,id)=>void createNode(kind,undefined,id)}/>}
        <section className="director-script-settings" hidden={selected?.id!=='script'} aria-label="脚本设置">
          <fieldset className="cs-fields" disabled={blocked}>
            {content&&<><label>脚本内容<textarea aria-label="脚本内容" rows={6} value={typeof content.summary==='string'?content.summary:''} onChange={event=>changeContent({summary:event.target.value})} placeholder="描述开场、卖点与收尾"/></label><div className="cs-shot-heading"><strong>镜头安排</strong><button type="button" className="cs-link" onClick={()=>changeContent({shots:[...(content.shots??[]),{seq:(content.shots?.length??0)+1,hint:''}]})}><Plus size={14}/>添加镜头</button></div><div className="cs-shots">{(content.shots??[]).map((shot,index)=><div className="cs-shot" key={index}><span className="cs-shot-number">{index+1}</span><label className="cs-shot-label"><span className="cs-sr-only">镜头建议 {index+1}</span><textarea rows={2} value={record(shot)&&typeof shot.hint==='string'?shot.hint:record(shot)&&typeof shot.description==='string'?shot.description:''} onChange={event=>changeContent({shots:content.shots?.map((item,i)=>i===index?{...(record(item)?item:{}),hint:event.target.value}:item)})}/></label><button className="cs-icon" aria-label={`删除镜头建议 ${index+1}`} onClick={()=>changeContent({shots:content.shots?.filter((_,i)=>i!==index)})}><Trash2 size={15}/></button></div>)}</div></>}
          </fieldset>
          {!content&&<p role="alert">脚本暂时无法编辑，请重新读取或重新起草；原内容仍保留。</p>}
          {stale&&<p className="cs-muted">需求已调整，请重新确认脚本。</p>}
          {conflictPending&&<div className="cs-notice cs-warning" role="alert"><strong>脚本已有更新，你的修改仍保留。</strong>{conflict?<><p>最新内容：{typeof conflict.content.summary==='string'?conflict.content.summary:'请刷新查看镜头内容'}</p><button className="cs-button" disabled={blocked||briefDirty} onClick={()=>void confirmVersion(conflict.version)}>保留我的修改并确认</button></>:<button className="cs-button" onClick={()=>void recover()}>读取最新内容</button>}</div>}
          <button className="cs-button cs-primary" disabled={blocked||briefDirty||!content||!scriptHasContent(content)||conflictPending||(productionReady&&!scriptDirty)} onClick={()=>void confirmVersion()}><Check size={16}/>{productionReady&&!scriptDirty?'脚本已确认':'确认脚本并继续'}</button>
        </section>
        <div hidden={!selected||selected.id==='script'||!!independent} className="director-node-settings"><ProductionFlow panel={panel} focusShotId={focusShotId} project={project} script={selectedScript} blocked={loading||!!busy||briefDirty||scriptDirty||!!uncertain||templatePending||chatPending||!productionReady} onDirtyChange={setProductionDirty} onPendingChange={setProductionPending} onSnapshot={receiveSnapshot} refreshKey={productionRefresh}/></div>
      </FreeCreationCanvas>
      {nodeBusy&&<div className="director-operation" role="status"><Spinner/>正在保存素材与节点…</div>}
      <StudioDialog open={!!quotePreview} title="确认本次生成" onClose={()=>{if(!nodeBusy)setQuotePreview(null);}}>{quotePreview&&<div className="director-generation-confirm"><h3>{quotePreview.title}</h3><p>{quotePreview.quote.input.kind==='image'?'生成 1 张图片':'生成 1 条视频'} · {models.find(model=>model.id===quotePreview.quote.input.model_id)?.name??'所选模型'}</p><p>{textValue(quotePreview.quote.input.params.ratio)}{quotePreview.quote.input.params.duration?` · ${String(quotePreview.quote.input.params.duration)} 秒`:''}{quotePreview.quote.input.params.resolution?` · ${String(quotePreview.quote.input.params.resolution)}`:''}</p><blockquote>{textValue(quotePreview.quote.input.params.prompt)}</blockquote><strong>预计 ¥{quotePreview.quote.estimated_cost_cny.toFixed(2)}</strong><div className="cs-actions"><button type="button" className="cs-button" disabled={nodeBusy} onClick={()=>setQuotePreview(null)}>继续调整</button><button type="button" className="cs-button cs-primary" disabled={nodeBusy||!!pendingGeneration} onClick={()=>void submitGeneration()}>{nodeBusy?'正在提交…':'确认生成'}</button></div></div>}</StudioDialog>
      <StudioDialog open={templatesOpen} title="结构模板" onClose={()=>setTemplatesOpen(false)}><StructureTemplates projectId={projectId} disabled={blocked||briefDirty||scriptDirty||productionDirty} snapshot={productionSnapshot.shots.length?productionSnapshot.shots.map(shot=>({seq:shot.seq,role:`镜头 ${shot.seq}`,duration_s:shot.duration_s,motion:shot.motion??'',rhythm:''})):undefined} onDirtyChange={setTemplateDirty} onPendingChange={setTemplatePending} onApplied={async()=>{setDirector(null);setStale(true);persist(undefined,true);await fetchProject(false);setProductionRefresh(value=>value+1);setNotice('结构已套用。');}}/></StudioDialog>
      {libraryOpen&&<StudioDialog title="从素材库添加" onClose={()=>setLibraryOpen(false)}>{resourcesError&&<p role="alert">{resourcesError}<button type="button" onClick={()=>void loadResources()}>重新读取</button></p>}<div className="director-asset-grid">{assets.map(asset=><button key={asset.id} type="button" disabled={nodeBusy} onClick={()=>void importExisting(asset)}>{asset.kind==='image'?<img src={mediaUrl(asset.id)} alt="素材预览"/>:<video src={mediaUrl(asset.id)} muted preload="metadata"/>}<span>{asset.kind==='image'?'图片':'视频'} · {asset.width} × {asset.height}</span></button>)}</div>{!assets.length&&!resourcesError&&<p>素材库还没有内容，可以直接把图片或视频拖入画布。</p>}</StudioDialog>}
    </div>
  </>;
}
