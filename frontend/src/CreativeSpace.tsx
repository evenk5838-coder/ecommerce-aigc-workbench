import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowLeft, Check, LoaderCircle, Plus, RefreshCw, Sparkles, Trash2 } from 'lucide-react';
import { ApiError, mediaUrl } from './api';
import { creativeApi } from './creative/api';
import type { CreativeProjectDetail, CreativeScript, ProjectBrief, ScriptContent } from './creative/types';
import {
  briefEditor, buildBrief, EMPTY_SCRIPT, latestScript, parseScriptText,
  prettyContent, readLocalProject, scriptHasContent, storeLocalProject,
} from './creative/state';
import type { BriefEditor, ProjectEditSnapshot, UncertainOperation } from './creative/state';
import ProductionFlow from './creative/ProductionFlow';
import FreeCreationCanvas from './creative/FreeCreationCanvas';
import type { FreeCreationNode } from './creative/FreeCreationCanvas';
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
    <header className="cs-heading"><span className="cs-eyebrow"><Sparkles size={15}/>导演台 · 自由画布</span><button className="cs-button" onClick={onBack}><ArrowLeft size={16}/>返回导演台</button></header>
    <div className="cs-layout"><main className="cs-main"><ProjectEditor key={initialProjectId} projectId={initialProjectId} onDirtyChange={setDirty} onChanged={changed} onBack={onBack}/></main></div>
  </div></div>;
}

function ProjectEditor({ projectId, onDirtyChange, onChanged, onBack }: { projectId: string; onDirtyChange: (dirty: boolean) => void; onChanged: () => Promise<void>; onBack: () => void }) {
  const [productionDirty,setProductionDirty]=useState(false);
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
  const dirty = briefDirty || scriptDirty || !!instruction.trim() || !!busy || productionDirty || templateDirty || chatPending || chatDirty;
  const parsed = parseScriptText(scriptText);
  const content = parsed.content;
  const blocked = loading || !!busy || !!uncertain || templatePending || productionPending || chatPending;
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
      const loaded = await fetchProject(initialized);
      if (!loaded) return;
      if (!initialized) { restoreEditing(loaded.scripts); setInitialized(true); }
      if (conflictPending) setConflict(loaded.latest);
      await refreshDirector(); setProductionRefresh(value=>value+1); setNotice('项目已更新，你的编辑仍保留。');
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
  const generate = async () => {
    if (!project || briefDirty || scriptDirty || blocked) return;
    if (!brief.subject.trim()) { setError('请先在创作资料中补充商品名称。'); return; }
    const duration = brief.target_duration_s.trim() ? Number(brief.target_duration_s) : undefined;
    await mutate('AI 正在写脚本', signal => creativeApi.generateScript(projectId, { instruction: instruction.trim() || undefined, target_duration_s: duration }, { signal }), async result => {
      if (result.assistant_unavailable) { setNotice(result.hint || 'AI 暂不可用，可以直接手工编写脚本。'); return; }
      await fetchProject(false, result.script_id); setInstruction('');
      setSelectedNodeId('script'); setNotice('脚本已准备好，看看是否符合你的想法。'); void onChanged();
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
      setStale(false); persist(undefined,false); setManualScript(false); setSelectedNodeId('plan');
      setNotice('脚本已确认，接下来准备分镜。'); void onChanged();
    });
  };
  const changeContent = (patch: Partial<ScriptContent>) => { if (content) setScriptText(prettyContent({ ...content, ...patch })); };
  const setField = (field: keyof BriefEditor, value: string) => setBrief(current => ({ ...current, [field]: value }));
  if (!project) return <section className="cs-card cs-loading">{loading ? <><Spinner /><p>正在恢复项目与脚本版本…</p></> : <><h2>{missing?'项目已不存在':'项目暂时无法读取'}</h2><p role="alert">{error}</p><button className="cs-button cs-primary" onClick={onBack}>返回导演台</button><button className="cs-button" onClick={() => void recover()}><RefreshCw size={16} />重新读取</button></>}</section>;
  const statuses=productionSnapshot.status?.shots??[];
  const film=productionSnapshot.films?.[0]??productionSnapshot.status?.film??productionSnapshot.status?.films?.[0];
  const nodes:FreeCreationNode[]=[];
  if(selectedScript || manualScript || scriptDirty) nodes.push({id:'script',type:'脚本',title:'创作脚本',summary:typeof content?.summary==='string'?content.summary:'点击查看并调整脚本',status:productionReady&&!scriptDirty?'approved':'draft',x:60,y:80});
  if(productionReady || productionSnapshot.shots.length) nodes.push({id:'plan',type:'分镜设置',title:'镜头计划',summary:`${productionSnapshot.shots.length} 个镜头 · 点击调整画面与生成设置`,status:productionReady?'ready':'stale',x:450,y:80});
  for(const [index,shot] of productionSnapshot.shots.entries()) {
    const status=statuses.find(item=>item.shot_id===shot.shot_id);
    const board=[...(shot.storyboards??status?.storyboards??[])].sort((a,b)=>b.version-a.version)[0];
    nodes.push({id:`image:${shot.shot_id}`,type:'分镜图',title:`镜头 ${shot.seq} · 画面`,summary:shot.description,image:board?.asset_id?mediaUrl(board.asset_id):undefined,status:board?.status??'draft',x:840,y:80+index*380});
    const take=status ? status.takes?.[0] : shot.takes?.[0];
    const asset=status ? status.asset_id??take?.asset_id : take?.asset_id;
    if(status?.task_id || asset || take) nodes.push({id:`video:${shot.shot_id}`,type:'镜头视频',title:`镜头 ${shot.seq} · 视频`,summary:shot.description,video:asset?mediaUrl(asset):undefined,status:status?.status??take?.status??'queued',x:1230,y:80+index*380});
  }
  const allApproved=productionSnapshot.shots.length>0 && productionSnapshot.shots.every(shot=>{
    const status=statuses.find(item=>item.shot_id===shot.shot_id);
    const latest=[...(shot.storyboards??status?.storyboards??[])].sort((a,b)=>b.version-a.version)[0];
    return latest?.status==='approved';
  });
  if(allApproved || productionSnapshot.status?.batch) nodes.push({id:'generation',type:'视频生成',title:'生成全部镜头',summary:'选择直接合成一支视频，或分别生成镜头。',status:productionSnapshot.status?.batch?.running?'running':'ready',x:1230,y:-210});
  if(film || statuses.some(item=>item.asset_id || item.takes?.some(take=>take.asset_id))) nodes.push({id:'delivery',type:'作品',title:film?'成片视频':'镜头与成片',video:film?.asset_id?mediaUrl(film.asset_id):undefined,summary:film?.partial?'部分成片':'预览、合成与下载',status:film?.status??'ready',x:1620,y:80});
  const selected=nodes.find(node=>node.id===selectedNodeId);
  const panel=selectedNodeId==='generation'||selectedNodeId?.startsWith('video:')?'generation':selectedNodeId==='delivery'?'delivery':'storyboard';
  const focusShotId=selectedNodeId?.startsWith('image:')||selectedNodeId?.startsWith('video:')?selectedNodeId.slice(6):null;
  return <>
    <div className="cs-project-heading"><h2>{project.name}</h2><div className="cs-actions"><span className="cs-muted">{briefDirty || scriptDirty ? '编辑中' : '创作项目'}</span><button className="cs-icon" aria-label="刷新当前项目" disabled={blocked} onClick={() => void recover()}>{loading ? <Spinner /> : <RefreshCw size={15} />}</button></div></div>
    <div className="ds-alerts">
      {productionSnapshot.error&&<div className="cs-notice cs-warning" role="alert">制作内容暂未完整读取。<button className="cs-link" disabled={!!busy||productionPending} onClick={()=>setProductionRefresh(value=>value+1)}>重新读取制作内容</button></div>}
      {storageFailed && <div className="cs-notice cs-warning" role="alert">编辑备份暂时无法保存，请保持页面开启。</div>}
      {error && <div className="cs-notice cs-error" role="alert">{error}</div>}
      {notice && <div className="cs-notice" role="status">{notice}<button className="cs-link" onClick={()=>setNotice('')}>收起</button></div>}
      {uncertain && !busy && <div className="cs-notice cs-warning" role="alert"><strong>{uncertain.label}</strong><p>结果暂不明确，请先刷新核对，避免重复提交。</p><div className="cs-actions"><button className="cs-button" disabled={loading} onClick={() => void recover()}>刷新核对</button><button className="cs-button" disabled={!recovered || loading} onClick={() => { if (window.confirm('已核对最新内容？原请求仍可能完成，继续编辑不会自动重发。')) { setUncertain(undefined); persist(undefined, stale); setError(''); } }}>已核对，继续</button></div></div>}
      {directorError&&<div className="cs-notice cs-warning" role="alert">暂时无法核对制作进度。<button className="cs-link" onClick={()=>void refreshDirector()}>重试</button></div>}
    </div>
    <div className="director-workspace">
      <aside className="director-conversation"><DirectorChat projectId={projectId} disabled={loading||!!busy||!!uncertain||productionPending||templatePending||briefDirty||scriptDirty||productionDirty} onBusyChange={setChatPending} onDirtyChange={setChatDirty} onRefresh={async()=>{await fetchProject(briefDirty||scriptDirty||productionDirty);await refreshDirector();setProductionRefresh(value=>value+1);}} onRevealNode={setSelectedNodeId}>
        <div className="director-project-tools">
          <fieldset disabled={blocked} className="cs-fields"><label>商品名称<input value={brief.subject} onChange={event=>setField('subject',event.target.value)} placeholder="这次想拍什么商品？"/></label><label>真实卖点<textarea rows={2} value={brief.selling_points} onChange={event=>setField('selling_points',event.target.value)} placeholder="希望重点展示哪些特点"/></label><div className="director-pair"><label>目标总时长<input type="number" min="1" value={brief.target_duration_s} onChange={event=>setField('target_duration_s',event.target.value)} placeholder="秒"/></label><label>商品数量<input inputMode="numeric" value={brief.quantity} onChange={event=>setField('quantity',event.target.value)} placeholder="选填"/></label></div><label>视频风格<input value={brief.style} onChange={event=>setField('style',event.target.value)} placeholder="例如：简洁棚拍"/></label><details><summary>补充创作要求</summary><label>发布平台<input value={brief.target_platform} onChange={event=>setField('target_platform',event.target.value)}/></label><label>避免出现<textarea rows={2} value={brief.forbidden_elements} onChange={event=>setField('forbidden_elements',event.target.value)}/></label><label>其他要求<textarea rows={2} value={brief.notes} onChange={event=>setField('notes',event.target.value)}/></label></details></fieldset>
          {briefDirty&&<button className="cs-button" disabled={blocked||!brief.subject.trim()} onClick={()=>void saveBrief()}>保存创作资料</button>}
          <label>脚本要求<textarea rows={2} value={instruction} disabled={blocked} onChange={event=>setInstruction(event.target.value)} placeholder="补充节奏、开场方式或修改意见"/></label>
          <button className="cs-button cs-primary" disabled={blocked||briefDirty||scriptDirty||!brief.subject.trim()} onClick={()=>void generate()}>{busy==='AI 正在写脚本'?<Spinner/>:<Sparkles size={16}/>} {scripts.length?'重新起草脚本':'生成脚本'}</button>
          {briefDirty&&<small>先保存商品资料，再起草脚本。</small>}
          {!selectedScript&&<button className="cs-link" disabled={blocked} onClick={()=>{setManualScript(true);setSelectedNodeId('script');}}>直接写脚本</button>}
          {busy&&<button className="cs-link" onClick={()=>controllerRef.current?.abort()}>停止等待</button>}
          <details className="ds-template-tools"><summary>结构模板</summary><StructureTemplates projectId={projectId} disabled={blocked||briefDirty||scriptDirty||productionDirty} snapshot={productionSnapshot.shots.length?productionSnapshot.shots.map(shot=>({seq:shot.seq,role:`镜头 ${shot.seq}`,duration_s:shot.duration_s,motion:shot.motion??'',rhythm:''})):undefined} onDirtyChange={setTemplateDirty} onPendingChange={setTemplatePending} onApplied={async()=>{setDirector(null);setStale(true);persist(undefined,true);await fetchProject(false);setProductionRefresh(value=>value+1);setNotice('结构已套用，请补充商品资料与参考图。');}}/></details>
        </div>
      </DirectorChat></aside>
      <FreeCreationCanvas projectId={projectId} nodes={nodes} selectedNodeId={selectedNodeId} onSelect={setSelectedNodeId}>
        <section className="director-script-settings" hidden={selected?.id!=='script'} aria-label="脚本设置">
          <fieldset className="cs-fields" disabled={blocked}>
            {content&&<><label>脚本内容<textarea aria-label="脚本内容" rows={6} value={typeof content.summary==='string'?content.summary:''} onChange={event=>changeContent({summary:event.target.value})} placeholder="描述开场、卖点与收尾"/></label><div className="cs-shot-heading"><strong>镜头安排</strong><button type="button" className="cs-link" onClick={()=>changeContent({shots:[...(content.shots??[]),{seq:(content.shots?.length??0)+1,hint:''}]})}><Plus size={14}/>添加镜头</button></div><div className="cs-shots">{(content.shots??[]).map((shot,index)=><div className="cs-shot" key={index}><span className="cs-shot-number">{index+1}</span><label className="cs-shot-label"><span className="cs-sr-only">镜头建议 {index+1}</span><textarea rows={2} value={record(shot)&&typeof shot.hint==='string'?shot.hint:record(shot)&&typeof shot.description==='string'?shot.description:''} onChange={event=>changeContent({shots:content.shots?.map((item,i)=>i===index?{...(record(item)?item:{}),hint:event.target.value}:item)})}/></label><button className="cs-icon" aria-label={`删除镜头建议 ${index+1}`} onClick={()=>changeContent({shots:content.shots?.filter((_,i)=>i!==index)})}><Trash2 size={15}/></button></div>)}</div></>}
          </fieldset>
          {!content&&<p role="alert">脚本暂时无法编辑，请重新读取或重新起草；原内容仍保留。</p>}
          {stale&&<p className="cs-muted">需求已调整，请重新确认脚本。</p>}
          {conflictPending&&<div className="cs-notice cs-warning" role="alert"><strong>脚本已有更新，你的修改仍保留。</strong>{conflict?<><p>最新内容：{typeof conflict.content.summary==='string'?conflict.content.summary:'请刷新查看镜头内容'}</p><button className="cs-button" disabled={blocked||briefDirty} onClick={()=>void confirmVersion(conflict.version)}>保留我的修改并确认</button></>:<button className="cs-button" onClick={()=>void recover()}>读取最新内容</button>}</div>}
          <button className="cs-button cs-primary" disabled={blocked||briefDirty||!content||!scriptHasContent(content)||conflictPending||(productionReady&&!scriptDirty)} onClick={()=>void confirmVersion()}><Check size={16}/>{productionReady&&!scriptDirty?'脚本已确认':'确认脚本并继续'}</button>
        </section>
        <div hidden={!selected||selected.id==='script'} className="director-node-settings"><ProductionFlow panel={panel} focusShotId={focusShotId} project={project} script={selectedScript} blocked={loading||!!busy||briefDirty||scriptDirty||!!uncertain||templatePending||chatPending||!productionReady} onDirtyChange={setProductionDirty} onPendingChange={setProductionPending} onSnapshot={receiveSnapshot} refreshKey={productionRefresh}/></div>
      </FreeCreationCanvas>
    </div>
  </>;
}
