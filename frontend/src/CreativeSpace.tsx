import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowLeft, ArrowRight, Check, FileText, FolderOpen, History, LoaderCircle, Plus, RefreshCw, Save, Sparkles, Trash2 } from 'lucide-react';
import { ApiError } from './api';
import { creativeApi } from './creative/api';
import type { CreativeProjectDetail, CreativeProjectListItem, CreativeScript, ProjectBrief, ScriptContent } from './creative/types';
import {
  briefEditor, buildBrief, displayDate, EMPTY_SCRIPT, latestScript, parseScriptText,
  prettyContent, readLocalProject, scriptHasContent, SELECTED_PROJECT_KEY, storeLocalProject,
} from './creative/state';
import type { BriefEditor, ProjectEditSnapshot, UncertainOperation } from './creative/state';
import ProductionFlow from './creative/ProductionFlow';
import WorkflowCanvas from './creative/WorkflowCanvas';
import StructureTemplates from './creative/StructureTemplates';
import type { Shot, ProductionStatus } from './creative/production-types';
import './CreativeSpace.css';

export interface CreativeSpaceProps {
  onWorkbench: () => void;
  /** The parent should guard every route that leaves this editor. */
  onDirtyChange?: (dirty: boolean) => void;
}
const CREATE_KEY = 'material-studio-creative-create-v1';
function readCreateDraft(): { name: string; subject: string; uncertain: boolean } {
  try { const value: unknown = JSON.parse(localStorage.getItem(CREATE_KEY) || '{}'); if (value && typeof value === 'object') { const item = value as Record<string, unknown>; return { name: typeof item.name === 'string' ? item.name : '', subject: typeof item.subject === 'string' ? item.subject : '', uncertain: item.uncertain === true }; } } catch { /* Keep the entry available for manual recovery if storage is unavailable. */ }
  return { name: '', subject: '', uncertain: false };
}
const errorMessage = (error: unknown) => error instanceof Error ? error.message : '读取失败，请稍后重新读取。';
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
function initialProject(): string { try { return localStorage.getItem(SELECTED_PROJECT_KEY) || ''; } catch { return ''; } }
function rememberProject(id: string): void { try { localStorage.setItem(SELECTED_PROJECT_KEY, id); } catch { /* The server remains the source of saved content. */ } }
function Spinner() { return <LoaderCircle size={16} className="cs-spin" aria-hidden="true" />; }

export default function CreativeSpace({ onWorkbench, onDirtyChange }: CreativeSpaceProps) {
  const [projects, setProjects] = useState<CreativeProjectListItem[]>([]);
  const [selectedId, setSelectedId] = useState(initialProject);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [showCreate, setShowCreate] = useState(() => { const saved = readCreateDraft(); return !!saved.name || !!saved.subject || saved.uncertain; });
  const [name, setName] = useState(() => readCreateDraft().name);
  const [subject, setSubject] = useState(() => readCreateDraft().subject);
  const [creating, setCreating] = useState(false);
  const [createUnknown, setCreateUnknown] = useState(() => readCreateDraft().uncertain);
  const [editorDirty, setEditorDirty] = useState(false);
  const selectedRef = useRef(selectedId);
  const listController = useRef<AbortController | null>(null);
  const createController = useRef<AbortController | null>(null);
  const createLock = useRef(false);
  const mounted = useRef(true);
  const dirty = editorDirty || creating || !!name.trim() || !!subject.trim();
  useEffect(() => { try { localStorage.setItem(CREATE_KEY, JSON.stringify({ name, subject, uncertain: createUnknown })); } catch { /* Unsaved-navigation protection still applies. */ } }, [name, subject, createUnknown]);
  useEffect(() => { onDirtyChange?.(dirty); }, [dirty, onDirtyChange]);
  useEffect(() => { const warn = (event: BeforeUnloadEvent) => { if (dirty) { event.preventDefault(); event.returnValue = ''; } }; window.addEventListener('beforeunload', warn); return () => window.removeEventListener('beforeunload', warn); }, [dirty]);
  useEffect(() => () => { onDirtyChange?.(false); }, [onDirtyChange]);
  const reloadList = useCallback(async () => {
    listController.current?.abort();
    const controller = new AbortController(); listController.current = controller;
    setLoading(true); setError('');
    try {
      const result = await creativeApi.listProjects(100, { signal: controller.signal });
      if (controller.signal.aborted || !mounted.current) return;
      setProjects(result.items);
      if (!selectedRef.current && result.items[0]) { selectedRef.current = result.items[0].project_id; setSelectedId(selectedRef.current); rememberProject(selectedRef.current); }
    } catch (caught) { if (!controller.signal.aborted && mounted.current) setError(errorMessage(caught)); }
    finally { if (!controller.signal.aborted && mounted.current) setLoading(false); }
  }, []);
  useEffect(() => { mounted.current = true; void reloadList(); return () => { mounted.current = false; listController.current?.abort(); createController.current?.abort(); }; }, [reloadList]);
  const mayLeave = () => !dirty || window.confirm('有未保存的内容或正在等待的请求。编辑会尽量保留在此浏览器中，离开不会取消服务端处理。仍要离开吗？');
  const selectProject = (id: string) => {
    if (id === selectedId || creating || !mayLeave()) return;
    selectedRef.current = id; setSelectedId(id); rememberProject(id); setEditorDirty(false);
  };
  const createProject = async () => {
    if (createLock.current || createUnknown || !name.trim()) return;
    if (editorDirty && !window.confirm('当前项目有未保存的编辑，将保留在此浏览器中。继续新建并切换项目吗？')) return;
    createLock.current = true; setCreating(true); setCreateUnknown(true); setError('');
    try { localStorage.setItem(CREATE_KEY, JSON.stringify({ name, subject, uncertain: true })); } catch { /* Keep the current page guarded. */ }
    const controller = new AbortController(); createController.current = controller;
    try {
      const result = await creativeApi.createProject(name.trim(), subject.trim() ? { subject: subject.trim() } : {}, { signal: controller.signal });
      if (!mounted.current) return;
      setName(''); setSubject(''); setCreateUnknown(false); setShowCreate(false); selectedRef.current = result.project_id;
      setSelectedId(result.project_id); rememberProject(result.project_id); setEditorDirty(false); await reloadList();
    } catch (caught) { if (mounted.current) { setError(errorMessage(caught)); setCreateUnknown(caught instanceof ApiError && caught.code === 'CREATIVE_RESULT_UNKNOWN'); } }
    finally { createLock.current = false; if (mounted.current) setCreating(false); }
  };
  return <div className="creative-space"><div className="cs-shell">
    <header className="cs-heading"><div><span className="cs-eyebrow"><Sparkles size={15} aria-hidden="true" />引导创作</span><h1>从商品想法，到一支完整短片。</h1><p>确认需求与脚本，逐镜核对画面，再生成片段或完整视频。</p></div><button className="cs-button" onClick={() => { if (mayLeave()) onWorkbench(); }}><ArrowLeft size={16} />快捷工作台</button></header>
    <ol className="cs-progress" aria-label="创作进度"><li className="current"><span>01</span>需求</li><li className="current"><span>02</span>脚本</li><li><span>03</span>分镜</li><li><span>04</span>镜头</li><li><span>05</span>成片</li></ol>
    <div className="cs-layout"><aside className="cs-projects" aria-label="创作项目">
      <div className="cs-section-title"><h2><FolderOpen size={17} />我的项目</h2><button className="cs-icon" aria-label="刷新项目列表" disabled={loading} onClick={() => void reloadList()}><RefreshCw size={16} /></button></div>
      <button className="cs-button cs-new" aria-expanded={showCreate} onClick={() => setShowCreate(!showCreate)}><Plus size={16} />新建项目</button>
      {showCreate && <form className="cs-create" onSubmit={event => { event.preventDefault(); void createProject(); }}><label>项目名称<input value={name} onChange={event => setName(event.target.value)} placeholder="如：保温杯新品短片" disabled={creating} /></label><label>商品名称<span className="cs-optional">选填</span><input value={subject} onChange={event => setSubject(event.target.value)} placeholder="稍后可补充完整需求" disabled={creating} /></label><button className="cs-button cs-primary" disabled={creating || createUnknown || !name.trim()}>{creating ? <Spinner /> : <Plus size={15} />}创建项目</button></form>}
      {error && <div className="cs-notice cs-error" role="alert">{error}</div>}
      {createUnknown && !creating && <div className="cs-notice">新建结果暂不明确。先刷新列表核对是否已创建。<button className="cs-link" disabled={loading} onClick={() => { if (window.confirm('请确认已核对项目列表。继续后可主动重新创建，可能产生重复项目。')) setCreateUnknown(false); }}>已核对列表</button></div>}
      {loading && <p className="cs-muted" role="status"><Spinner />正在读取项目…</p>}
      {!loading && !error && !projects.length && <p className="cs-muted">还没有项目。先为这次创作取个名字。</p>}
      <nav className="cs-project-list" aria-label="切换项目">{projects.map(project => <button key={project.project_id} className={selectedId === project.project_id ? 'selected' : ''} aria-current={selectedId === project.project_id ? 'page' : undefined} disabled={creating} onClick={() => selectProject(project.project_id)}><strong>{project.name}</strong><span>{project.script_count ?? 0} 个脚本版本</span><small>{displayDate(project.updated_at)}</small></button>)}</nav>
      <p className="cs-local-note">项目与已保存版本保留在本机服务；尚未保存的编辑仅保留在当前浏览器。</p>
    </aside>
    <main className="cs-main">{selectedId ? <ProjectEditor key={selectedId} projectId={selectedId} onDirtyChange={setEditorDirty} onChanged={reloadList} /> : <section className="cs-empty"><FileText size={34} strokeWidth={1.4} /><h2>从一个商品、一支短片开始</h2><p>新建项目后，填写商品资料和目标片长。你可以请 AI 写脚本，也可以直接手工编写。</p><button className="cs-button cs-primary" onClick={() => setShowCreate(true)}><Plus size={16} />新建第一个项目</button></section>}</main></div>
    <footer className="cs-footer">同一套素材和任务贯穿需求、脚本、分镜与成片。未开通模型与未就绪接口会明确提示。</footer>
  </div></div>;
}

function ProjectEditor({ projectId, onDirtyChange, onChanged }: { projectId: string; onDirtyChange: (dirty: boolean) => void; onChanged: () => Promise<void> }) {
  const [productionDirty,setProductionDirty]=useState(false);
  const [templateDirty,setTemplateDirty]=useState(false);
  const [templatePending,setTemplatePending]=useState(false);
  const [productionPending,setProductionPending]=useState(false);
  const [confirmedSnapshot,setConfirmedSnapshot]=useState<string|null>(null);
  const [productionRefresh,setProductionRefresh]=useState(0);
  const canvasActions=useRef<{add:()=>void;edit:(id:string)=>void;remove:(id:string)=>void;move:(id:string,direction:'up'|'down')=>void}|null>(null);
  const receiveActions=useCallback((actions:NonNullable<typeof canvasActions.current>)=>{canvasActions.current=actions;},[]);
  const [workspaceMode,setWorkspaceMode]=useState<'form'|'canvas'>('form');
  const [canvasStage,setCanvasStage]=useState('brief');
  const [productionSnapshot,setProductionSnapshot]=useState<{shots:Shot[];status:ProductionStatus|null;loaded?:boolean;error?:string}>({shots:[],status:null});
  const receiveSnapshot=useCallback((data:{shots:Shot[];status:ProductionStatus|null;loaded?:boolean;error?:string})=>setProductionSnapshot(data),[]);
  const selectStage=(stage:string)=>{setCanvasStage(stage);setWorkspaceMode('form');const ids:Record<string,string>={brief:'cs-brief-title',script:'cs-script-title',plan:'pf-plan-title',storyboard:'pf-plan-title',review:'pf-plan-title',generation:'pf-scope-title',results:'pf-film-title',export:'creative-structure-templates'};requestAnimationFrame(()=>document.getElementById(ids[stage]??'pf-plan-title')?.scrollIntoView({behavior:'smooth',block:'start'}));};
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
  const currentConfirmation=project&&selectedScript?JSON.stringify({brief:project.brief,script_id:selectedScript.script_id,version:selectedScript.version,content:selectedScript.content}):null;
  const productionReady=!!currentConfirmation&&confirmedSnapshot===currentConfirmation;
  const dirty = briefDirty || scriptDirty || !!instruction.trim() || !!busy || productionDirty || templateDirty;
  const parsed = parseScriptText(scriptText);
  const content = parsed.content;
  const blocked = loading || !!busy || !!uncertain || templatePending || productionPending;
  useEffect(() => { onDirtyChange(dirty); }, [dirty, onDirtyChange]);
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
      setProject(next); setScripts(versions.items);
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
    setNotice('已恢复此浏览器中的未保存编辑，请核对后显式保存。');
  };
  useEffect(() => {
    mounted.current = true; let cancelled = false;
    void (async () => {
      try {
        const loaded = await fetchProject(false);
        if (!loaded || !mounted.current || cancelled) return;
        restoreEditing(loaded.scripts); setInitialized(true);
      } catch (caught) { if (mounted.current && !cancelled) setError(errorMessage(caught)); }
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
      setNotice('已重新读取服务端项目与版本列表；你的未保存内容仍保留。');
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
      try { await success(result); if (mounted.current) { setUncertain(undefined); persist(undefined, stale); } }
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
    if (!brief.subject.trim()) { setError('请先填写并保存商品名称，再让 AI 写脚本。'); return; }
    const duration = brief.target_duration_s.trim() ? Number(brief.target_duration_s) : undefined;
    await mutate('AI 正在写脚本', signal => creativeApi.generateScript(projectId, { instruction: instruction.trim() || undefined, target_duration_s: duration }, { signal }), async result => {
      if (result.assistant_unavailable) { setNotice(result.hint || 'AI 暂不可用，可以直接手工编写脚本。'); return; }
      await fetchProject(false, result.script_id); setInstruction('');
      const usage = result.llm_usage;
      setNotice(`已保存脚本 v${result.version}。${usage ? `LLM 用量：输入 ${usage.input_tokens}、输出 ${usage.output_tokens} tokens。` : '本次响应未提供 LLM 用量。'}请核对商品事实后再确认。`); void onChanged();
    });
  };
  const saveScript = async (newBase?: number) => {
    if (!content || !scriptHasContent(content)) { setError(parsed.error || '请先填写脚本概述或镜头建议。'); return; }
    if (briefDirty) { setError('请先保存需求，再保存脚本版本。'); return; }
    const base = newBase ?? baseVersion;
    await mutate('保存脚本新版本', signal => scriptId ? creativeApi.editScript(scriptId, { content, base_version: base }, { signal }) : creativeApi.addManualScript(projectId, { content }, { signal }), async result => {
      await fetchProject(false, result.script_id); setNotice(`已保存为 v${result.version}，原版本保持不变。`); void onChanged();
    });
  };
  const chooseVersion = async (id: string) => {
    if (loading || busy || id === scriptId) return;
    if (scriptDirty && !window.confirm('切换版本将替换当前脚本编辑区。放弃这份未保存的脚本编辑吗？')) return;
    const controller = new AbortController(); readController.current?.abort(); readController.current = controller; setLoading(true); setError('');
    try { const script = await creativeApi.getScript(id, { signal: controller.signal }); if (mounted.current && !controller.signal.aborted) { if (script.project_id !== projectId) throw new Error('脚本不属于当前项目，已停止切换。'); adoptScript(script, newest?.version ?? script.version); } }
    catch (caught) { if (mounted.current && !controller.signal.aborted) setError(errorMessage(caught)); }
    finally { if (mounted.current && !controller.signal.aborted) setLoading(false); }
  };
  const confirmVersion = async () => {
    if (!selectedScript || scriptDirty || briefDirty || blocked) return;
    const target = selectedScript;
    await mutate(`确认脚本 v${target.version}`, signal => creativeApi.confirmScript(target.script_id, { signal }), async result => {
      if (result.script_id !== target.script_id || result.version !== target.version) throw new Error('确认回执与所选版本不匹配，请重新读取核对。');
      const refreshed=await fetchProject(false, target.script_id);
      const confirmed=refreshed?.scripts.find(item=>item.script_id===target.script_id);
      if(!refreshed||!confirmed)throw new Error('确认后无法读取当前版本，请重新核对。');
      setConfirmedSnapshot(JSON.stringify({brief:refreshed.project.brief,script_id:confirmed.script_id,version:confirmed.version,content:confirmed.content}));
      setStale(false);persist(undefined,false);setNotice(`已确认脚本 v${result.version}，可继续镜头制作。`); void onChanged();
    });
  };
  const changeContent = (patch: Partial<ScriptContent>) => { if (content) setScriptText(prettyContent({ ...content, ...patch })); };
  const setField = (field: keyof BriefEditor, value: string) => setBrief(current => ({ ...current, [field]: value }));
  if (!project) return <section className="cs-card cs-loading">{loading ? <><Spinner /><p>正在恢复项目与脚本版本…</p></> : <><h2>项目暂时无法读取</h2><p role="alert">{error}</p><button className="cs-button" onClick={() => void recover()}><RefreshCw size={16} />重新读取</button></>}</section>;
  const canvasBoards=productionSnapshot.shots.flatMap(shot=>shot.storyboards??[]);
  const approvedBoards=canvasBoards.filter(board=>board.status==='approved');
  const statuses=productionSnapshot.status?.shots??[];
  const batch=productionSnapshot.status?.batch;
  const film=productionSnapshot.status?.film;
  const canvasStages={
    brief:{status:project.brief.subject?'saved':'draft',summary:String(project.brief.subject??'待填写商品资料')},
    script:{status:productionReady?'approved':selectedScript?'saved':'draft',summary:selectedScript?`v${selectedScript.version} · ${productionReady?'本次制作已确认':'待核对本次制作版本'}`:'待编写脚本'},
    plan:{status:productionSnapshot.loaded?'saved':productionSnapshot.error?'unavailable':'unknown',summary:productionSnapshot.loaded?`${productionSnapshot.shots.length} 个镜头`:productionSnapshot.error??'等待读取真实镜头数据'},
    storyboard:{status:canvasBoards.some(board=>['queued','running'].includes(board.status))?'running':canvasBoards.length?'saved':'not_started',summary:`已读取 ${canvasBoards.length} 个分镜版本`},
    review:{status:approvedBoards.length?'saved':'not_started',summary:`已有 ${approvedBoards.length} 个分镜版本通过审核，生成前仍会校验当前版本`},
    generation:{status:batch?.running?'running':batch?.queued?'queued':statuses.some(item=>item.status==='failed')?'failed':statuses.length&&statuses.every(item=>item.status==='succeeded')?'succeeded':'not_started',summary:productionSnapshot.status?`${statuses.length} 个镜头状态 · ${productionSnapshot.status.mode==='full'?'完整视频':productionSnapshot.status.mode==='clips'?'独立片段':'尚未选模式'}`:'等待读取运行状态'},
    results:{status:film?.status??'unknown',summary:film?`${film.version?`成片 v${film.version}`:'成片'} · ${film.partial===true?'部分成片':film.partial===false?'完整成片':'完整性待核对'}`:'进入查看成功片段与成片历史'},
    export:{status:'unknown',summary:'进入下载项目包或管理结构模板'},
  };
  return <>
    <div className="cs-project-heading"><div><span className="cs-eyebrow">当前项目</span><h2>{project.name}</h2><span className="cs-muted">{scripts.length} 个脚本版本 · {briefDirty || scriptDirty ? '有未保存编辑' : '当前编辑已保存'}</span></div><button className="cs-button" disabled={blocked} onClick={() => void recover()}>{loading ? <Spinner /> : <RefreshCw size={15} />}重新读取</button></div>
    {storageFailed && <div className="cs-notice cs-warning" role="alert">浏览器暂不能保存编辑备份。请先显式保存到本机服务，再离开页面。</div>}
    {error && <div className="cs-notice cs-error" role="alert">{error}</div>}
    {notice && <div className="cs-notice" role="status">{notice}</div>}
    {uncertain && !busy && <div className="cs-notice cs-warning" role="alert"><strong>“{uncertain.label}”结果需要核对</strong><p>{displayDate(uncertain.startedAt)} 发起；发起前最新为 v{uncertain.beforeVersion}。服务端可能仍在处理，停止等待不会取消请求。不会自动重试。</p><div className="cs-actions"><button className="cs-button" disabled={loading} onClick={() => void recover()}>读取项目与历史核对</button><button className="cs-button" disabled={!recovered || loading} onClick={() => { if (window.confirm('请确认已查看最新脚本与项目内容。原请求仍可能完成；继续不会自动重发，后续操作需要你主动点击。')) { setUncertain(undefined); persist(undefined, stale); setError(''); setNotice('已解除本地等待保护。继续操作前请核对最新版本，避免重复请求。'); } }}>已核对，继续编辑</button></div></div>}
    <div className="cs-workspace-modes" role="group" aria-label="引导创作视图"><button className={workspaceMode==='form'?'cs-button cs-primary':'cs-button'} aria-pressed={workspaceMode==='form'} onClick={()=>setWorkspaceMode('form')}>流程表单</button><button className={workspaceMode==='canvas'?'cs-button cs-primary':'cs-button'} aria-pressed={workspaceMode==='canvas'} onClick={()=>setWorkspaceMode('canvas')}>节点画布</button><span className="cs-muted">同一个项目，两种操作视图</span></div>
    {workspaceMode==='canvas'&&<><WorkflowCanvas projectId={projectId} stage={canvasStage} onSelectStage={selectStage} shots={productionSnapshot.shots.map(shot=>({shot_id:shot.shot_id,seq:shot.seq,description:shot.description,status:productionSnapshot.status?.shots.find(item=>item.shot_id===shot.shot_id)?.status??shot.status,asset_id:productionSnapshot.status?.shots.find(item=>item.shot_id===shot.shot_id)?.asset_id}))} stages={canvasStages} onEditShot={id=>{selectStage('plan');requestAnimationFrame(()=>id?canvasActions.current?.edit(id):canvasActions.current?.add());}} onDeleteShot={productionReady&&!blocked?id=>canvasActions.current?.remove(id):undefined} onReorderShot={productionReady&&!blocked?(id,direction)=>canvasActions.current?.move(id,direction):undefined}/>{productionSnapshot.error&&<p className="cs-notice">制作接口尚未就绪：{productionSnapshot.error}</p>}</>}
    <div hidden={workspaceMode==='canvas'}>
    <section className="cs-card" aria-labelledby="cs-brief-title"><div className="cs-section-title"><h2 id="cs-brief-title"><span className="cs-step">01</span>商品与创作需求</h2><span className="cs-muted">显式保存后用于 AI 脚本</span></div>
      <fieldset disabled={blocked} className="cs-fields"><div className="cs-field-grid"><label>项目名称<input value={brief.name} onChange={event => setField('name', event.target.value)} /></label><label>商品名称<input value={brief.subject} onChange={event => setField('subject', event.target.value)} placeholder="明确商品是什么，不添加未经证实的功效" /></label><label>商品数量<input type="text" inputMode="numeric" value={brief.quantity} onChange={event => setField('quantity', event.target.value)} placeholder="选填，正整数" /></label><label>目标总片长（秒）<input type="text" inputMode="numeric" value={brief.target_duration_s} onChange={event => setField('target_duration_s', event.target.value)} placeholder="如 20 或 30，是整支视频时长" /></label><label>发布平台<input value={brief.target_platform} onChange={event => setField('target_platform', event.target.value)} placeholder="如：抖音商品详情、天猫" /></label><label>视觉与表达风格<input value={brief.style} onChange={event => setField('style', event.target.value)} placeholder="如：简洁棚拍、自然生活感" /></label><label>真实卖点<textarea rows={3} value={brief.selling_points} onChange={event => setField('selling_points', event.target.value)} placeholder="只填写已确认的商品事实" /></label><label>禁止出现的内容<textarea rows={3} value={brief.forbidden_elements} onChange={event => setField('forbidden_elements', event.target.value)} placeholder="如：夸张功效、错误包装、无关品牌" /></label><label className="cs-full">补充说明<textarea rows={2} value={brief.notes} onChange={event => setField('notes', event.target.value)} placeholder="受众、叙事方向及其他要求" /></label></div></fieldset>
      <div className="cs-card-footer"><p>总片长用于脚本规划，不受单镜头生成时长限制。</p><button className="cs-button cs-primary" disabled={blocked || !briefDirty} onClick={() => void saveBrief()}><Save size={16} />保存需求</button></div>
      {stale && <p className="cs-stale">需求曾发生变更，脚本与后续确认需要重新核对。以下时间仅是该版本的历史确认记录；此本地提示不代表服务端已验证当前需求。</p>}
    </section>
    <section className="cs-card" aria-labelledby="cs-script-title"><div className="cs-section-title"><h2 id="cs-script-title"><span className="cs-step">02</span>脚本创作</h2><span className="cs-muted">先看清内容，再确认版本</span></div>
      <div className="cs-ai"><div><h3><Sparkles size={17} />让 AI 起草</h3><p>点击后会调用文本模型并记录 LLM 用量，与图片、视频生成费用分开；不会自动生成图片或视频。</p></div><label>本次写作要求<span className="cs-optional">选填</span><textarea rows={2} value={instruction} disabled={blocked} onChange={event => setInstruction(event.target.value)} placeholder="如：以产品细节开场，节奏简洁，不虚构商品信息" /></label><div className="cs-actions"><button className="cs-button cs-primary" disabled={blocked || briefDirty || scriptDirty || !brief.subject.trim()} onClick={() => void generate()}>{busy === 'AI 正在写脚本' ? <Spinner /> : <Sparkles size={16} />}{scripts.length ? 'AI 生成新脚本版本' : 'AI 生成脚本'}</button>{busy && <button className="cs-button" onClick={() => controllerRef.current?.abort()}>停止等待</button>}<span className="cs-muted">{briefDirty ? '请先保存需求' : scriptDirty ? '先保存当前脚本，避免覆盖编辑' : '也可直接在下方手工编写'}</span></div></div>
      <div className="cs-editor-heading"><h3><FileText size={17} />{selectedScript ? `脚本 v${selectedScript.version}` : '手工编写脚本'}</h3><span className="cs-muted">{selectedScript ? `${selectedScript.source === 'assistant' ? 'AI 起草' : selectedScript.source === 'manual' ? '手工保存' : '来源未说明'} · ${displayDate(selectedScript.created_at)}` : '保存后将创建一个脚本版本'}</span></div>
      {selectedScript?.confirmed_at && <p className="cs-confirmed"><Check size={15} />本版历史确认记录：{displayDate(selectedScript.confirmed_at)}</p>}
      {content?.parse_ok === false && <div className="cs-notice cs-warning">AI 返回了非结构化内容，下面保留原文；没有补造镜头建议。请手工检查、整理后保存新版本。</div>}
      <fieldset className="cs-fields cs-script-fields" disabled={blocked}>
        {content && <><label>脚本概述<textarea className="cs-summary" rows={6} value={typeof content.summary === 'string' ? content.summary : ''} onChange={event => changeContent({ summary: event.target.value })} placeholder="描述开场、商品卖点与收尾。你也可以完全手工创作。" /></label><div className="cs-shot-heading"><span>镜头建议<span className="cs-optional">脚本文字，尚非分镜画面</span></span><button type="button" className="cs-link" onClick={() => changeContent({ shots: [...(content.shots ?? []), { seq: (content.shots?.length ?? 0) + 1, hint: '' }] })}><Plus size={14} />添加一条</button></div>
        <div className="cs-shots">{(content.shots ?? []).map((shot, index) => <div className="cs-shot" key={index}><span className="cs-shot-number">{index + 1}</span>{record(shot) && typeof shot.hint === 'string' ? <label className="cs-shot-label"><span className="cs-sr-only">镜头建议 {index + 1}</span><textarea rows={2} value={shot.hint} onChange={event => changeContent({ shots: content.shots?.map((item, itemIndex) => itemIndex === index ? { ...shot, hint: event.target.value } : item) })} /></label> : <pre>{JSON.stringify(shot, null, 2)}</pre>}<button className="cs-icon" aria-label={`删除镜头建议 ${index + 1}`} onClick={() => changeContent({ shots: content.shots?.filter((_, itemIndex) => itemIndex !== index) })}><Trash2 size={15} /></button></div>)}</div></>}
        <details className="cs-raw" open={!content || undefined}><summary>查看或编辑完整脚本数据</summary><p>完整保留额外字段；直接修改时需保持 JSON 格式。</p><textarea rows={10} value={scriptText} onChange={event => setScriptText(event.target.value)} aria-label="完整脚本 JSON" spellCheck={false} /></details>
      </fieldset>
      {parsed.error && <p className="cs-field-error" role="alert">{parsed.error}</p>}
      <div className="cs-card-footer"><p>每次保存会新增版本，旧内容保持不变。</p><div className="cs-actions"><button className="cs-button" disabled={blocked || briefDirty || !content || !scriptHasContent(content) || (!scriptDirty && !!scriptId) || conflictPending} onClick={() => void saveScript()}><Save size={16} />{scriptId ? '保存为新版本' : '保存手工脚本'}</button><button className="cs-button cs-primary" disabled={blocked || briefDirty || scriptDirty || !selectedScript || conflictPending} onClick={() => void confirmVersion()}><Check size={16} />{selectedScript ? `确认脚本（v${selectedScript.version}）` : '保存后确认版本'}</button></div></div>
      {conflictPending && <section className="cs-conflict" role="alert"><h3>已有更新版本，你的编辑已保留</h3><p>请比较后再决定是否以最新版本为基线保存你的内容。这个操作仍会新增版本。</p>{conflict ? <><div className="cs-comparison"><div><strong>你的未保存内容</strong><pre>{scriptText}</pre></div><div><strong>服务端最新 v{conflict.version}</strong><pre>{prettyContent(conflict.content)}</pre></div></div><button className="cs-button" disabled={blocked || briefDirty || !content} onClick={() => void saveScript(conflict.version)}>已比较，以 v{conflict.version} 为基线另存我的编辑</button></> : <button className="cs-button" onClick={() => void recover()}>重新读取最新版本</button>}</section>}
    </section>
    <section className="cs-card cs-history" aria-labelledby="cs-history-title"><div className="cs-section-title"><h2 id="cs-history-title"><History size={17} />版本历史</h2><span className="cs-muted">点击查看具体版本</span></div>{scripts.length ? <div className="cs-history-list">{scripts.map(script => <button className={script.script_id === scriptId ? 'selected' : ''} key={script.script_id} disabled={blocked} onClick={() => void chooseVersion(script.script_id)}><span><strong>v{script.version}</strong><span>{script.source === 'assistant' ? 'AI 起草' : script.source === 'manual' ? '手工保存' : '脚本版本'}</span></span><small>{displayDate(script.created_at)}</small><span>{script.confirmed_at ? '有本版确认记录' : '未记录确认'}<ArrowRight size={14} /></span></button>)}</div> : <p className="cs-muted">尚未保存脚本。AI 不可用时，可手工编写并保存第一版。</p>}</section>
    {!productionReady&&<p className="cs-notice">请先确认当前需求下的脚本版本，再继续镜头制作。历史确认时间仅供查阅；重新打开项目后需要核对本次制作版本。</p>}
    <ProductionFlow project={project} script={selectedScript} blocked={loading||!!busy||briefDirty||scriptDirty||!!uncertain||templatePending||!productionReady} onDirtyChange={setProductionDirty} onPendingChange={setProductionPending} onSnapshot={receiveSnapshot} onPlanActions={receiveActions} refreshKey={productionRefresh}/>
    <div id="creative-structure-templates"><StructureTemplates projectId={projectId} disabled={loading||!!busy||briefDirty||scriptDirty||productionDirty||productionPending||!!uncertain} snapshot={productionSnapshot.shots.length?productionSnapshot.shots.map(shot=>({seq:shot.seq,role:`镜头 ${shot.seq}`,duration_s:shot.duration_s,motion:shot.motion??'',rhythm:''})):undefined} onDirtyChange={setTemplateDirty} onPendingChange={setTemplatePending} onApplied={async()=>{setConfirmedSnapshot(null);setStale(true);persist(undefined,true);await fetchProject(false);setProductionRefresh(value=>value+1);setNotice('结构已套用。请重新填写商品事实、绑定素材并核对模型规格。');}}/></div>
    </div>
  </>;
}
