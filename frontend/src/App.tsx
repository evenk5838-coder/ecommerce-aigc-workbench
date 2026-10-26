import { useCallback, useEffect, useRef, useState } from 'react';
import type { PointerEvent as ReactPointerEvent, ReactNode } from 'react';
import { ArrowDownToLine, ArrowUpRight, Check, ChevronDown, Clock3, Copy, Download, Film, Image as ImageIcon, Layers, Loader2, Minus, MousePointer2, Plus, RefreshCw, Save, Search, SlidersHorizontal, Sparkles, Square, Trash2, Type, Undo2, Upload, X } from 'lucide-react';
import JSZip from 'jszip';
import Home from './Home';
import ProjectHome from './ProjectHome';
import StudioThemeToggle from './StudioTheme';
import CreativeSpace from './CreativeSpace';
import DirectorHome from './DirectorHome';
import './CStudioShell.css';
import ImageOutputSettings from './ImageOutputSettings';
import SingleTemplatePanel from './SingleTemplatePanel';
import { templateInput } from './single-templates';
import { imageSizeTier } from './image-specs';
import GenerationConfirm from './GenerationConfirm';
import PromptOptimizer from './assistant/PromptOptimizer';
import { getDefaultImageSize, getDurationPolicy, getImageSizesForRatio, getModelUnavailableReason, getSupportedRatios, getSupportedResolutions, validateModelSelection, validateVideoDuration } from './capabilities';
import './S1Workbench.css';
import './FourModeWorkbench.css';
import { GENERATION_MODES, getVideoPrompt, modeKind, modeNeedsReference, modesForKind, supportedGenerationModes, templateGenerationMode, validateGenerationMode, workbenchModeIntegrationError } from './generation-modes';
import ReferencePicker from './ReferencePicker';
import { buildGenerationInput, getImagePrompt } from './generation';
import { inspectGenerationEstimates } from './estimates';
import type { GenerationMode, ImageMode, ImagePurpose, VideoMode } from './generation';
import { api, ApiError, mediaUrl } from './api';
import { downloadBlob, fitAsset, metadata, moveLayer, newProject, realAssets, renderCanvas, restoreGenerationSettings, safeName } from './editor';
import type { Asset, GenerationSettings, Layer, Model, Project, SystemStatus, Task, TaskEstimate, TaskInput } from './types';

const STORAGE = 'material-studio-project-v1';
const ACTIVE = ['queued','loading','running','postprocess','remote_queued','remote_running','cancel_requested'];
const NAMES: Record<string,string> = {queued:'排队中',loading:'加载中',running:'生成中',postprocess:'保存结果',remote_queued:'云端排队',remote_running:'云端生成',cancel_requested:'已请求取消',succeeded:'已完成',failed:'生成失败',canceled:'已取消',interrupted:'已中断'};
const ERRORS: Record<string,string> = {AUTH_MISSING:'尚未配置云端 Key，请在后端配置后重试。',AUTH_FAILED:'模型鉴权失败，请检查 Key 与模型开通权限。',BUDGET_NOT_APPROVED:'后端尚未批准云端预算。',BUDGET_EXCEEDED:'已达到本次预算上限，请先核对用量。',MODEL_NOT_DOWNLOADED:'本地模型权重未就位。',MODEL_NOT_OPENED:'当前账号尚未开通该模型，请核对火山控制台。',CREATE_RESULT_UNKNOWN:'创建结果暂不明确，请先核对云端任务，避免重复扣费。'};
type PendingGeneration = { id:string; items:TaskInput[]; kind:'image'|'video'; modelName:string; specLabel:string; costs:number[]|null; estimates:TaskEstimate[]|null; prompts:string[]; error:string };
const time = (ms?: number) => ms == null ? '—' : ms < 60000 ? `${(ms/1000).toFixed(1)}秒` : `${(ms/60000).toFixed(1)}分钟`;
function IconButton({ children, label, onClick, disabled = false, active = false }: {children:ReactNode;label:string;onClick:()=>void;disabled?:boolean;active?:boolean}) {
  return <button className={`icon-button ${active?'active':''}`} aria-label={label} title={label} onClick={onClick} disabled={disabled}>{children}</button>;
}
function assetName(a: Asset) { const m=metadata(a); return typeof m.filename==='string'?m.filename:a.kind==='video'?'生成视频':`生成图片 · ${a.width}×${a.height}`; }

export default function App() {
  const [project,setProject]=useState<Project>(()=>({...newProject(),assistantProjectId:`workbench-${crypto.randomUUID()}`}));
  const projectRef=useRef(project); const draftRef=useRef<{id:string;version:number}|null>(null);
  const dirty=useRef(0), saved=useRef(0), saving=useRef(false), blocked=useRef(false);
  const [ready,setReady]=useState(false), [saveState,setSaveState]=useState('尚未保存');
  const [models,setModels]=useState<Model[]>([]), [assets,setAssets]=useState<Asset[]>([]), [tasks,setTasks]=useState<Task[]>([]);
  const [,setSystem]=useState<SystemStatus|null>(null), [,setUsage]=useState({budget_cny:0,spent_today_cny:0});
  const [connected,setConnected]=useState(false), [notice,setNotice]=useState('');
  const [resourceErrors,setResourceErrors]=useState({assets:'',tasks:'',models:'',usage:''});
  const [saveError,setSaveError]=useState(''),[submitError,setSubmitError]=useState('');
  const [validation,setValidation]=useState<{prompt?:string;reference?:string}>({});
  const [propertiesOpen,setPropertiesOpen]=useState(false);
  const [view,setView]=useState<'projects'|'home'|'director'|'creative'|'canvas'|'library'>('projects'),[taskDrawer,setTaskDrawer]=useState(false),[preview,setPreview]=useState<Asset|null>(null);
  const creativeDirty=useRef(false);
  const [creativeProjectId,setCreativeProjectId]=useState<string|null>(null),[creativeCreateNonce,setCreativeCreateNonce]=useState(0);
  const onCreativeDirtyChange=useCallback((value:boolean)=>{creativeDirty.current=value;},[]);
  const canLeaveCreative=()=>!['creative','director'].includes(view)||!creativeDirty.current||window.confirm('引导创作还有未保存内容或正在处理的请求，确定离开吗？');
  const changeView=(next:typeof view)=>{if(next!=='director')setCreativeCreateNonce(0);setView(next);};
  const navigate=(next:typeof view)=>{if(next===view||!canLeaveCreative())return;creativeDirty.current=false;changeView(next);};
  const [selected,setSelected]=useState<string|null>(null),[zoom,setZoom]=useState(1),[undo,setUndo]=useState<Project[]>([]);
  const [modelId,setModelId]=useState(''),[referencePicker,setReferencePicker]=useState(false);
  const settings=restoreGenerationSettings(project.generation);
  const {kind,imageMode,purpose,batchCount,duration}=settings;
  const videoMode=settings.videoMode??'image_to_video';
  const activeTemplate=settings.template?.template.kind===kind?settings.template:null;
  const prompt=kind==='image'?settings.imagePrompt:settings.videoPrompt;
  const ratio=kind==='image'?settings.imageRatio:settings.videoRatio;
  const reference=kind==='image'?settings.imageReference:settings.videoReference;
  const templateMode=activeTemplate?templateGenerationMode(activeTemplate,reference):null;
  const generationMode:GenerationMode=templateMode??(kind==='image'?imageMode:videoMode);
  const templateModeError=activeTemplate&&!templateMode?'模板的输入方式尚未配置，请退出模板后手动创作。':null;
  const needsReference=modeNeedsReference(generationMode)||activeTemplate?.template.template_id==='tpl_season_theme';
  const requiresReference=activeTemplate?activeTemplate.template.template_id!=='tpl_season_theme'&&(kind==='video'||activeTemplate.template.required_assets.some(a=>a.required)):modeNeedsReference(generationMode);
  const [pendingGeneration,setPendingGeneration]=useState<PendingGeneration|null>(null);
  const pendingRef=useRef<string|null>(null);
  const modelSelection=useRef<{kind:'image'|'video';initialized:boolean}>({kind:'image',initialized:false});
  const [busy,setBusy]=useState(false),[uploading,setUploading]=useState(false),[exporting,setExporting]=useState(false),[query,setQuery]=useState('');
  const [marked,setMarked]=useState<Set<string>>(new Set()),[assetTab,setAssetTab]=useState('all');
  const svgRef=useRef<SVGSVGElement>(null), fileRef=useRef<HTMLInputElement>(null);
  const uploadIntent=useRef<'image'|'video'|'library'|'canvas'>('library'),submitLock=useRef(false);
  const drag=useRef<{id:string;x:number;y:number;layer:Layer;resize:boolean}|null>(null);
  const pollBusy=useRef(false), initialized=useRef(false);

  const notify=useCallback((message:string)=>setNotice(message),[]);
  const commit=useCallback((next:Project, history=true)=>{
    if(history) setUndo(list=>[...list.slice(-24),structuredClone(projectRef.current)]);
    projectRef.current=next;dirty.current++;setProject(next);setSaveState('待保存');
  },[]);
  const mutate=useCallback((fn:(p:Project)=>Project,history=true)=>commit(fn(projectRef.current),history),[commit]);
  const updateGeneration=(patch:Partial<GenerationSettings>)=>{setValidation({});setSubmitError('');mutate(p=>({...p,generation:{...restoreGenerationSettings(p.generation),...patch}}),false);};
  const setPrompt=(value:string)=>updateGeneration(kind==='image'?{imagePrompt:value}:{videoPrompt:value});
  const setRatio=(value:string)=>updateGeneration(kind==='image'?{imageRatio:value,imageSize:''}:{videoRatio:value});
  const setGenerationMode=(mode:GenerationMode)=>{
    if(activeTemplate&&!window.confirm('切换输入方式会退出当前模板，保留已填写的描述。是否继续？'))return false;
    const target=modeKind(mode);
    if(target==='image'){const next=mode as ImageMode;const isPreset=!settings.imagePrompt.trim()||settings.imagePrompt===getImagePrompt(imageMode,purpose);updateGeneration({kind:target,imageMode:next,template:null,...(isPreset?{imagePrompt:getImagePrompt(next,purpose)}:{})});}
    else{const next=mode as VideoMode;const isPreset=!settings.videoPrompt.trim()||settings.videoPrompt===getVideoPrompt(videoMode);updateGeneration({kind:target,videoMode:next,template:null,...(isPreset?{videoPrompt:getVideoPrompt(next)}:{})});}
    return true;
  };
  const openCreativeProject=(id:string)=>{if(!canLeaveCreative())return;creativeDirty.current=false;setCreativeCreateNonce(0);setCreativeProjectId(id);changeView('creative');};
  const createCreativeProject=()=>{if(!canLeaveCreative())return;creativeDirty.current=false;setCreativeProjectId(null);setCreativeCreateNonce(value=>value+1);changeView('director');};
  const startGeneration=(mode:GenerationMode)=>{if(!canLeaveCreative()||!setGenerationMode(mode))return;creativeDirty.current=false;changeView('home');};
  const selectPurpose=(value:ImagePurpose)=>{
    const preset=getImagePrompt(imageMode,value);
    if(value!=='custom'&&prompt.trim()&&prompt!==getImagePrompt(imageMode,purpose)&&!window.confirm('切换用途将替换当前图片描述，是否使用新的预设？'))return;
    updateGeneration({purpose:value,...(value==='custom'?{}:{imagePrompt:preset})});
  };
  const saveProject=useCallback(async()=>{
    if(saving.current||blocked.current)return;
    saving.current=true;setSaveState('保存中…'); const revision=dirty.current; let completed=false;
    const p=structuredClone(projectRef.current);
    try {
      const content={canvas:p.canvas,assetGroups:p.assetGroups,generation:p.generation,meta:{name:p.name,assistantProjectId:p.assistantProjectId}};
      const r=draftRef.current?await api.saveDraft(draftRef.current.id,draftRef.current.version,content):await api.createDraft(p.name,content);
      draftRef.current={id:r.draft_id,version:r.version};saved.current=revision;completed=true;setSaveError('');
      localStorage.setItem(STORAGE,JSON.stringify({project:projectRef.current,draft:draftRef.current,pending:dirty.current!==revision}));
      setSaveState(dirty.current===revision?'已保存到本机':'待保存');
    } catch(e) {
      setSaveState('保存失败');
      if(e instanceof ApiError&&e.code==='DRAFT_VERSION_CONFLICT')blocked.current=true;
      setSaveError(blocked.current?'草稿已在其他页面更新。当前内容保存在浏览器，可另存为新草稿继续。':'草稿未能保存到服务。请重试保存；当前编辑仍保留在页面。');
    } finally {saving.current=false;if(completed&&dirty.current!==revision)setProject({...projectRef.current});}
  },[notify]);
  const refresh=useCallback(async()=>{
    if(pollBusy.current)return;pollBusy.current=true;
    const result=await Promise.allSettled([api.models(),api.assets(),api.tasks(),api.system(),api.usage()]);
    if(result[0].status==='fulfilled')setModels(result[0].value.items.filter(m=>m.provider==='ark'));
    if(result[1].status==='fulfilled')setAssets(realAssets(result[1].value.items));
    if(result[2].status==='fulfilled')setTasks(result[2].value.items.filter(t=>t.provider==='ark'||t.model_id.startsWith('doubao')));
    if(result[3].status==='fulfilled'){setSystem(result[3].value);setConnected(true);}else setConnected(false);
    if(result[4].status==='fulfilled')setUsage(result[4].value);
    setResourceErrors({assets:result[1].status==='rejected'?'素材读取失败，已加载的素材仍保留。':'',tasks:result[2].status==='rejected'?'任务状态暂时无法更新，请刷新确认进度。':'',models:result[0].status==='rejected'?'模型列表暂时无法更新，请稍后重试。':'',usage:result[4].status==='rejected'?'费用估算暂时无法更新。':''});
    pollBusy.current=false;
  },[]);
  useEffect(()=>{
    if(initialized.current)return;initialized.current=true;
    (async()=>{
      try {
        const raw=localStorage.getItem(STORAGE);
        if(raw){const local=JSON.parse(raw) as {project:Project;draft:{id:string;version:number}|null;pending:boolean};
          if(local.project?.canvas?.layers){local.project.generation=restoreGenerationSettings(local.project.generation);local.project.assistantProjectId ||= local.draft?.id||`workbench-${crypto.randomUUID()}`;projectRef.current=local.project;setProject(local.project);draftRef.current=local.draft;
            if(local.pending){dirty.current=1;setSaveState('已恢复未保存修改');}
            else if(local.draft){const d=await api.draft(local.draft.id);const p={name:d.content.meta?.name||d.name,canvas:d.content.canvas,assetGroups:d.content.assetGroups||{},generation:restoreGenerationSettings(d.content.generation),assistantProjectId:d.content.meta?.assistantProjectId||d.id};
              projectRef.current=p;setProject(p);draftRef.current={id:d.id,version:d.version};setSaveState('已保存到本机');}
          }
        } else {
          const d=(await api.drafts()).items.find(d=>Array.isArray(d.content?.canvas?.layers));
          if(d){const p={name:d.content.meta?.name||d.name,canvas:d.content.canvas,assetGroups:d.content.assetGroups||{},generation:restoreGenerationSettings(d.content.generation),assistantProjectId:d.content.meta?.assistantProjectId||d.id};
            projectRef.current=p;setProject(p);draftRef.current={id:d.id,version:d.version};setSaveState('已保存到本机');}
        }
      }catch{setSaveError('暂时无法恢复上次服务端草稿。已保留当前页面内容，请检查服务后再保存。');}
      await refresh();setReady(true);
    })();
    const timer=window.setInterval(refresh,2500);return()=>window.clearInterval(timer);
  },[notify,refresh]);
  useEffect(()=>{if(!ready)return;
    localStorage.setItem(STORAGE,JSON.stringify({project,draft:draftRef.current,pending:dirty.current!==saved.current}));
    if(dirty.current===saved.current)return;
    const timer=window.setTimeout(()=>void saveProject(),1100);return()=>window.clearTimeout(timer);
  },[project,ready,saveProject]);
  useEffect(()=>{
    if(modelSelection.current.kind!==kind)modelSelection.current={kind,initialized:false};
    if(modelSelection.current.initialized){
      if(modelId&&!models.some(m=>m.id===modelId&&m.kind===kind))setModelId('');
      return;
    }
    if(!models.length)return;
    const list=models.filter(m=>m.kind===kind&&!getModelUnavailableReason(m)&&supportedGenerationModes(m).includes(generationMode));
    modelSelection.current.initialized=true;
    setModelId((list.find(m=>m.id.includes('flash'))||list[0])?.id||'');
  },[models,kind,modelId,generationMode]);
  useEffect(()=>{if(!notice)return;const timer=window.setTimeout(()=>setNotice(''),8000);return()=>window.clearTimeout(timer);},[notice]);
  useEffect(()=>{
    if(!preview&&!referencePicker&&!taskDrawer)return;
    const previous=document.activeElement as HTMLElement|null;
    const dialog=document.querySelector<HTMLElement>(preview?'.preview-modal':referencePicker?'.reference-modal':'.task-drawer');
    const focusable=()=>Array.from(dialog?.querySelectorAll<HTMLElement>('button:not(:disabled),a[href],input:not(:disabled),textarea,select,[tabindex="0"]')||[]).filter(el=>el.getClientRects().length>0);
    focusable()[0]?.focus();
    const handle=(event:KeyboardEvent)=>{
      if(event.key==='Escape'){if(preview)setPreview(null);else if(referencePicker)setReferencePicker(false);else setTaskDrawer(false);}
      if(event.key==='Tab'){const items=focusable(),first=items[0],last=items.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}}
    };
    document.addEventListener('keydown',handle);
    return()=>{document.removeEventListener('keydown',handle);if(previous?.isConnected)previous.focus();};
  },[preview,referencePicker,taskDrawer]);

  const selectedLayer=project.canvas.layers.find(l=>l.id===selected);
  const activeTasks=tasks.filter(t=>ACTIVE.includes(t.status));
  const selectedModel=models.find(m=>m.id===modelId);
  const supportedRatios=getSupportedRatios(selectedModel);
  const supportedResolutions=getSupportedResolutions(selectedModel);
  const imageSizes=getImageSizesForRatio(selectedModel,ratio);
  const durationPolicy=getDurationPolicy(selectedModel);
  const durations=durationPolicy.kind==='enum'?durationPolicy.allowed:durationPolicy.kind==='range'&&durationPolicy.max-durationPolicy.min<=300?Array.from({length:(durationPolicy.max-durationPolicy.min)/durationPolicy.step+1},(_,i)=>durationPolicy.min+i*durationPolicy.step):[];
  const resolution=settings.videoResolution??'720p';
  const imageSize=project.generation?.imageSize??getDefaultImageSize(selectedModel,ratio)??'';
  const modeError=validateGenerationMode(selectedModel,generationMode);
  const integrationError=workbenchModeIntegrationError(generationMode);
  const selectionError=validateModelSelection(selectedModel,kind)||templateModeError||modeError||integrationError||(!supportedRatios.includes(ratio)?'请重新选择当前模型支持的画面比例。':kind==='video'?(validateVideoDuration(selectedModel,duration)||(!supportedResolutions.includes(resolution)?'请重新选择视频清晰度。':null)):(!imageSizes.includes(imageSize)?'请为当前比例选择清晰度。':null));
  const pendingPermissionError=pendingGeneration?getModelUnavailableReason(models.find(model=>model.id===pendingGeneration.items[0]?.model_id)):null;
  const pendingEstimateError=pendingGeneration?.estimates?inspectGenerationEstimates(pendingGeneration.estimates,pendingGeneration.items.length).error:null;
  const assistantProjectId=project.assistantProjectId??draftRef.current?.id??'';
  const refAsset=assets.find(a=>a.id===reference);
  const filteredAssets=assets.filter(a=>(assetTab==='all'||(assetTab==='upload'?a.source==='upload':a.kind===assetTab))&&(assetName(a)+' '+String(metadata(a).prompt||'')+' '+(project.assetGroups[a.id]||'')).toLowerCase().includes(query.toLowerCase()));
  const updateLayer=(patch:Partial<Layer>)=>mutate(p=>({...p,canvas:{...p.canvas,layers:p.canvas.layers.map(l=>l.id===selected?{...l,...patch}:l)}}));
  const addAsset=(a:Asset)=>{
    if(a.kind==='video'){setPreview(a);return;}
    if(!canLeaveCreative())return;creativeDirty.current=false;
    const layer=fitAsset(a,projectRef.current.canvas.width,projectRef.current.canvas.height);
    mutate(p=>({...p,canvas:{...p.canvas,layers:[...p.canvas.layers,layer]}}));setSelected(layer.id);setPropertiesOpen(true);changeView('canvas');
  };
  const addElement=(type:'text'|'shape')=>{
    const id=crypto.randomUUID(), d=projectRef.current.canvas;
    const layer:Layer={id,type,name:type==='text'?'卖点文案':'背景色块',x:d.width*.12,y:d.height*.12,w:d.width*.7,h:type==='text'?100:240,rotation:0,opacity:1,
      ...(type==='text'?{text:'写下你的产品亮点',fontSize:64,color:'#202238'}:{fill:'#ecebff'})};
    mutate(p=>({...p,canvas:{...p.canvas,layers:[...p.canvas.layers,layer]}}));setSelected(id);setPropertiesOpen(true);
  };
  const deleteLayer=()=>{if(!selected)return;mutate(p=>({...p,canvas:{...p.canvas,layers:p.canvas.layers.filter(l=>l.id!==selected)}}));setSelected(null);};
  const startDrag=(e:ReactPointerEvent<SVGElement>,layer:Layer,resize=false)=>{
    e.stopPropagation();e.currentTarget.setPointerCapture(e.pointerId);
    setSelected(layer.id);setUndo(list=>[...list.slice(-24),structuredClone(projectRef.current)]);
    drag.current={id:layer.id,x:e.clientX,y:e.clientY,layer:{...layer},resize};
  };
  const onMove=(e:ReactPointerEvent<SVGSVGElement>)=>{
    const g=drag.current,bounds=svgRef.current?.getBoundingClientRect();if(!g||!bounds)return;
    const dx=(e.clientX-g.x)*project.canvas.width/bounds.width,dy=(e.clientY-g.y)*project.canvas.height/bounds.height;
    const next=g.resize?{...g.layer,w:Math.max(40,Math.round(g.layer.w+dx)),h:Math.max(40,Math.round(g.layer.h+dy))}:moveLayer(g.layer,dx,dy,project.canvas.width,project.canvas.height);
    mutate(p=>({...p,canvas:{...p.canvas,layers:p.canvas.layers.map(l=>l.id===g.id?next:l)}}),false);
  };
  const undoEdit=()=>{const last=undo.at(-1);if(!last)return;setUndo(undo.slice(0,-1));commit({...last,generation:projectRef.current.generation},false);setSelected(null);};
  const selectReference=(asset:Asset,target:'image'|'video')=>{
    if(asset.kind!=='image')return notify('请选择图片素材。');
    if(!canLeaveCreative())return;creativeDirty.current=false;
    if(asset.size>10*1024*1024)return notify('用于生成的参考图请控制在 10MB 以内。');
    const isPreset=settings.imagePrompt===getImagePrompt(imageMode,purpose);
    updateGeneration(target==='image'?{kind:'image',imageMode:'image_to_image',imageReference:asset.id,...(isPreset?{imagePrompt:getImagePrompt('image_to_image',purpose)}:{})}:{kind:'video',videoMode:'image_to_video',videoReference:asset.id});
    setReferencePicker(false);setPreview(null);changeView('home');
    notify(target==='image'?'已选为图生图参考，填写修改要求后即可生成。':'已选为视频首帧，填写动作描述后即可生成。');
  };
  const openUpload=(intent:'image'|'video'|'library'|'canvas')=>{uploadIntent.current=intent;if(fileRef.current){fileRef.current.multiple=intent==='library'||intent==='canvas';fileRef.current.click();}};
  const upload=async(files:FileList|null)=>{if(!files?.length)return;setUploading(true);
    const intent=uploadIntent.current,asReference=intent==='image'||intent==='video';
    try{let first='';for(const file of Array.from(files)){if(file.size>(asReference?10:20)*1024*1024)throw new Error(`单张图片不能超过 ${asReference?10:20}MB`);const r=await api.upload(file);first ||= r.asset_id;}
      await refresh();
      if(asReference){updateGeneration(intent==='image'?{kind:'image',imageMode:'image_to_image',imageReference:first}:{kind:'video',videoMode:'image_to_video',videoReference:first});setReferencePicker(false);}
      notify(`已上传 ${files.length} 张素材${asReference?'，第一张已选作参考图':'，原件保存在本机'}。`);
    }catch(e){notify(e instanceof Error?e.message:'上传失败');}finally{setUploading(false);if(fileRef.current)fileRef.current.value='';}};
  const submit=async()=>{
    if(submitLock.current||pendingRef.current)return;
    setSubmitError('');
    const fields:{prompt?:string;reference?:string}={};
    if(!activeTemplate&&!prompt.trim())fields.prompt='请填写商品描述或修改要求。';
    if((requiresReference||activeTemplate&&reference)&&!refAsset)fields.reference=reference?'所选参考图暂时无法读取，请重新选择，避免把概念图当成商品图。':'请先上传或选择一张商品参考图。';
    setValidation(fields);
    if(fields.reference||fields.prompt){document.getElementById(fields.reference?'reference-select':'prompt')?.focus();return;}
    let base;
    try{if(selectionError)throw new Error(selectionError);base=activeTemplate?templateInput(activeTemplate,selectedModel,{ratio,imageSize,duration,resolution,reference:modeNeedsReference(generationMode)?refAsset?.id??null:null,prompt}):buildGenerationInput({kind,imageMode,videoMode,modelId,prompt,reference:refAsset,ratio,duration,model:selectedModel,resolution,imageSize});}
    catch(e){setSubmitError(e instanceof Error?e.message:'请检查生成参数。');return;}
    const id=crypto.randomUUID();
    const items=Array.from({length:batchCount},()=>({...base,params:{...base.params},client_request_id:crypto.randomUUID()}));
    pendingRef.current=id;
    setPendingGeneration({id,items,kind,modelName:selectedModel?.name||modelId,specLabel:kind==='video'?`${GENERATION_MODES[generationMode].label} · ${ratio} · ${resolution} · ${duration} 秒`:`${GENERATION_MODES[generationMode].label} · ${ratio} · ${imageSize.replace('x',' × ')}`,costs:null,estimates:null,prompts:[],error:''});
    try{
      const quotes=await Promise.all(items.map(item=>api.estimateTask(item)));
      const estimateCheck=inspectGenerationEstimates(quotes,items.length);
      const prompts=quotes.map(quote=>quote.params_preview?.prompt).filter((value):value is string=>typeof value==='string'&&value.trim().length>0);
      if(activeTemplate&&(prompts.length!==items.length||quotes.some(quote=>quote.template_id!==activeTemplate.template.template_id||quote.template_version!==activeTemplate.template.latest_version)))throw new Error('模板解析结果或版本不完整，请重新获取费用预估。');
      if(pendingRef.current===id)setPendingGeneration(current=>current?.id===id?{...current,estimates:quotes,prompts,costs:estimateCheck.costs,error:estimateCheck.error??''}:current);
    }catch(e){if(pendingRef.current===id)setPendingGeneration(current=>current?.id===id?{...current,error:e instanceof Error?e.message:'费用预估失败，请返回修改。'}:current);}
  };
  const closeGeneration=()=>{if(busy)return;pendingRef.current=null;setPendingGeneration(null);};
  const confirmGeneration=async()=>{
    const frozen=pendingGeneration;
    if(submitLock.current||!frozen||frozen.error)return;
    const estimateCheck=inspectGenerationEstimates(frozen.estimates,frozen.items.length);
    if(estimateCheck.error){setPendingGeneration({...frozen,error:estimateCheck.error});return;}
    const model=models.find(item=>item.id===frozen.items[0].model_id);
    const frozenMode=(frozen.items[0].params.generation_mode??(frozen.kind==='video'?'image_to_video':frozen.items[0].input_asset_id?'image_to_image':'text_to_image')) as GenerationMode;
    const unavailable=getModelUnavailableReason(model)||validateGenerationMode(model,frozenMode)||workbenchModeIntegrationError(frozenMode);
    if(unavailable){setPendingGeneration({...frozen,error:unavailable});return;}
    submitLock.current=true;setBusy(true);
    try{
      if(frozen.items.length===1)await api.createTask(frozen.items[0]);
      else await api.createBatch(`${project.name} · 批量生成`,frozen.items);
      pendingRef.current=null;setPendingGeneration(null);changeView('home');
      await refresh();notify(`已提交 ${frozen.items.length} 个任务，完成后会显示在生成结果中。`);
    }catch(e){setPendingGeneration(current=>current?{...current,error:`${e instanceof ApiError?(ERRORS[e.code]||e.message):e instanceof Error?e.message:'提交结果暂时未知。'} 请先检查任务中心，勿连续重复提交。`}:current);}
    finally{submitLock.current=false;setBusy(false);}
  };
  const taskAction=async(t:Task,action:'cancel'|'retry')=>{
    if(action==='retry'&&(t.provider==='ark'||t.model_id.startsWith('doubao'))&&!window.confirm('从头重试可能产生新的云端费用，是否继续？'))return;
    try{if(action==='cancel'){const r=await api.cancelTask(t.id);notify(r.message||(r.result==='cancel_requested'?'已请求停止，将在安全点处理。':'任务已取消'));}else{await api.retryTask(t.id);notify('已提交从头重试任务。');}await refresh();}catch(e){notify(e instanceof Error?e.message:'操作失败');}
  };
  const exportPng=async()=>{setExporting(true);try{downloadBlob(await renderCanvas(projectRef.current),`${safeName(project.name)}.png`);notify('画布 PNG 已导出。');}catch(e){notify(e instanceof Error?e.message:'导出失败');}finally{setExporting(false);}};
  const exportSelected=async()=>{if(!marked.size)return;setExporting(true);try{const zip=new JSZip();const list=assets.filter(a=>marked.has(a.id));
    for(let i=0;i<list.length;i++){const a=list[i],response=await fetch(mediaUrl(a.id));if(!response.ok)throw new Error('素材下载失败，未生成不完整压缩包');zip.file(`${String(i+1).padStart(2,'0')}-${a.id}.${a.kind==='video'?'mp4':a.mime.includes('jpeg')?'jpg':'png'}`,await response.blob());}
    zip.file('素材记录.json',JSON.stringify(list.map(a=>({id:a.id,name:assetName(a),width:a.width,height:a.height,sku:project.assetGroups[a.id]||'',metadata:metadata(a)})),null,2));
    downloadBlob(await zip.generateAsync({type:'blob'}),`${safeName(project.name)}-素材.zip`);notify('选中素材已打包导出。');
    }catch(e){notify(e instanceof Error?e.message:'打包失败');}finally{setExporting(false);}};

  const gallery=(compact=false)=><div className={compact?'asset-grid compact':'asset-grid'}>{filteredAssets.map(a=><article className="asset-card" key={a.id}>
    <button className="asset-visual" onClick={()=>setPreview(a)} aria-label={`预览${assetName(a)}`}>{a.kind==='image'?<img src={mediaUrl(a.id)} loading="lazy" alt={assetName(a)}/>:<video src={mediaUrl(a.id)} preload="metadata"/>}<span className="asset-kind">{a.kind==='video'?<Film size={12}/>:<ImageIcon size={12}/>}</span></button>
    <div className="asset-caption"><span>{assetName(a)}</span><button className="mini-button" title="加入画布" onClick={()=>addAsset(a)}><Plus size={14}/></button></div>
    {!compact&&<div className="asset-bottom"><label><input type="checkbox" checked={marked.has(a.id)} onChange={()=>setMarked(old=>{const n=new Set(old);if(n.has(a.id))n.delete(a.id);else n.add(a.id);return n;})}/>选择</label><span>{a.source==='upload'?'已上传':'已生成'}</span></div>}
  </article>)}{!filteredAssets.length&&!resourceErrors.assets&&<div className="empty-state"><ImageIcon size={26}/><strong>{query||assetTab!=='all'?'没有符合筛选条件的素材':'素材从这里开始'}</strong><span>{query||assetTab!=='all'?'试试其他关键词，或切换筛选条件。':'上传商品图，或生成第一张图片。'}</span><button className="secondary" onClick={()=>openUpload(view==='canvas'?'canvas':'library')}><Upload size={14}/>上传素材</button></div>}</div>;

  const generationForm=<fieldset disabled={!ready||busy||!!pendingGeneration} className="composer-content">
    <div className="kind-switch" aria-label="生成类型">
      <button aria-pressed={kind==='image'} className={kind==='image'?'selected':''} onClick={()=>updateGeneration({kind:'image'})}><ImageIcon size={18}/>图片生成</button>
      <button aria-pressed={kind==='video'} className={kind==='video'?'selected':''} onClick={()=>updateGeneration({kind:'video'})}><Film size={18}/>视频生成</button>
    </div>
    <div className="composer-fields"><SingleTemplatePanel kind={kind} modelId={modelId} value={activeTemplate} onChange={value=>updateGeneration({template:value})} onSelect={template=>{if(prompt.trim()&&!window.confirm('套用模板将清空当前描述，由模板生成新描述，是否继续？'))return;updateGeneration({template:{template,fields:{},conceptAcknowledged:false},...(kind==='image'?{imagePrompt:'',imageMode:template.default_params.generation_mode==='image_to_image'?'image_to_image':'text_to_image',imageSize:typeof template.default_params.size==='string'?template.default_params.size:undefined,imageRatio:'1:1'}:{videoPrompt:'',duration:typeof template.default_params.duration==='number'?template.default_params.duration:duration})});}}/><p className="composer-section-heading"><span>01</span> 创作内容</p>
    <div className="four-mode-switch" role="group" aria-label={kind==='image'?'图片输入方式':'视频输入方式'}>{modesForKind(kind).map(mode=><button type="button" key={mode} aria-pressed={generationMode===mode} className={generationMode===mode?'selected':''} disabled={!!models.length&&!models.some(model=>model.kind===kind&&supportedGenerationModes(model).includes(mode))} onClick={()=>setGenerationMode(mode)}><strong>{GENERATION_MODES[mode].label}</strong><span>{modeNeedsReference(mode)?'使用商品原图':'仅使用文字描述'}</span></button>)}</div>
    <p className="four-mode-description">{GENERATION_MODES[generationMode].description}</p>
    {activeTemplate&&<p className="four-mode-template-note">{activeTemplate.template.template_id==='tpl_season_theme'?'此主题模板：有参考图时图生图；无参考图时生成概念图。':'当前输入方式由模板要求决定。'}切换方式会退出模板。</p>}
    {integrationError&&<div className="four-mode-pending" role="status">{integrationError}</div>}
    {kind==='image'&&!activeTemplate&&<><label className="field-label">图片用途</label><div className="purpose-options">{([['white','白底商品图'],['scene','氛围场景图'],['custom','自定义']] as const).map(([value,label])=><button key={value} aria-pressed={purpose===value} className={purpose===value?'selected':''} onClick={()=>selectPurpose(value)}>{label}</button>)}</div></>}
    {needsReference&&<div className="reference-field">
      <label className="field-label">{kind==='image'?(activeTemplate?.template.template_id==='tpl_season_theme'?'商品参考图（可选）':'商品参考图'):'视频首帧图'}<span>单张 · 最大 10MB</span></label>
      {refAsset?<div className="selected-reference"><img src={mediaUrl(refAsset.id)} alt={kind==='image'?'已选商品参考图':'已选视频首帧图'}/><div><strong>已选择 1 张图片</strong><span>{refAsset.width} × {refAsset.height} · {(refAsset.size/1e6).toFixed(1)} MB</span><button onClick={()=>setReferencePicker(true)}>更换图片</button></div><IconButton label="移除参考图" onClick={()=>updateGeneration(kind==='image'?{imageReference:null}:{videoReference:null})}><X size={15}/></IconButton></div>:<button id="reference-select" aria-invalid={!!validation.reference} aria-describedby={validation.reference?'reference-error':undefined} className="reference-empty" onClick={()=>setReferencePicker(true)}><Upload size={23}/><strong>上传或选择商品图</strong><span>从素材库选择，也可以上传新图片</span></button>}
      {validation.reference&&<p className="field-error" id="reference-error" role="alert">{validation.reference}</p>}
    </div>}
    <label className="field-label" htmlFor="prompt">{activeTemplate?'覆盖模板描述（选填）':kind==='video'?'镜头与动作描述':imageMode==='image_to_image'?'你想修改什么？':'描述你想生成的商品图'}<Sparkles size={14}/></label>
    <textarea id="prompt" aria-invalid={!!validation.prompt} aria-describedby={validation.prompt?'prompt-error':undefined} value={prompt} onChange={e=>setPrompt(e.target.value)} rows={5} placeholder={kind==='image'?'描述商品、背景、光线和构图…':'例如：镜头缓慢推近，商品保持静止…'}/>
    {validation.prompt&&<p className="field-error" id="prompt-error" role="alert">{validation.prompt}</p>}
    {kind==='image'&&generationMode==='image_to_image'&&<p className="microcopy reference-tip">以参考图中的实际商品为准，请勿描述图中没有的商品。生成后请核对数量、包装与外形。</p>}
    <PromptOptimizer projectId={assistantProjectId} prompt={prompt} kind={kind} onApply={setPrompt}/>
    <details open className="composer-parameters"><summary><span className="composer-section-heading"><span>02</span> 生成设置</span><span>{ratio} · {kind==='image'?(imageSizes.includes(imageSize)?imageSizeTier(imageSize):'请选择清晰度'):`${duration}秒 · ${resolution}`} · {batchCount}{kind==='image'?'张':'条'}</span><ChevronDown size={16}/></summary>
      <label className="field-label" htmlFor="generation-model">生成模型<span>云端推理</span></label>
      <select id="generation-model" aria-label="生成模型" value={modelId} onChange={e=>setModelId(e.target.value)}><option value="" disabled>请选择可用模型</option>{models.filter(m=>m.kind===kind).map(m=>{const error=getModelUnavailableReason(m)||validateGenerationMode(m,generationMode);return <option key={m.id} value={m.id} disabled={!!error}>{m.name}{error?` · ${error}`:''}</option>;})}</select>
      {selectedModel?.lifecycle?.status==='eos_scheduled'&&<p className="microcopy model-lifecycle">此模型计划于 {selectedModel.lifecycle.eos_at?new Date(selectedModel.lifecycle.eos_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}):'近期'} 下线，请提前准备替代模型。</p>}
      {kind==='image'?<ImageOutputSettings model={selectedModel} ratio={ratio} imageSize={imageSize} count={batchCount} onRatioChange={setRatio} onSizeChange={value=>updateGeneration({imageSize:value})} onCountChange={value=>updateGeneration({batchCount:value})}/>:<>
      <label className="field-label">画面比例</label><div className="ratio-options">{supportedRatios.map(r=><button key={r} aria-pressed={ratio===r} onClick={()=>setRatio(r)} className={ratio===r?'selected':''}>{r}</button>)}</div>
      <div className="field-pair"><label>生成数量<input aria-label="生成数量" type="number" min={1} max={6} value={batchCount} onChange={e=>updateGeneration({batchCount:Math.max(1,Math.min(6,Math.trunc(Number(e.target.value))||1))})}/></label><label>视频时长<select aria-label="视频时长" value={durations.includes(duration)?duration:''} onChange={e=>updateGeneration({duration:Number(e.target.value)})}><option value="" disabled>请选择原生时长</option>{durations.map(d=><option key={d} value={d}>{d} 秒</option>)}</select></label></div>
      <label className="field-label resolution-field">视频清晰度<select aria-label="视频清晰度" value={supportedResolutions.includes(resolution)?resolution:''} onChange={e=>updateGeneration({videoResolution:e.target.value})}><option value="" disabled>请选择清晰度</option>{supportedResolutions.map(value=><option key={value} value={value}>{value}</option>)}</select></label></>}
      {selectionError&&<p className="field-error" role="alert">{selectionError}</p>}

    </details></div>
    <div className="generation-footer">{submitError&&<p className="field-error" role="alert">{submitError}</p>}{resourceErrors.models&&<p className="field-error">{resourceErrors.models}</p>}<button className="generate-button" disabled={busy||!ready||uploading||!!selectionError||!connected} onClick={()=>void submit()}>{busy?<Loader2 size={18} className="spin"/>:<Sparkles size={18}/>} {busy?'正在提交…':`生成 ${batchCount} ${kind==='image'?'张图片':'条视频'}`}<ArrowUpRight size={17}/></button></div>
  </fieldset>;
  return <div className={`app-shell view-${view}`}>
    <div className="app-main">
      <header className="c-topbar"><button className="c-brand" aria-label="物料间首页" onClick={()=>navigate('projects')}><span className="c-brand-mark"><i/><i/><i/></span><span>物料间<small>MATERIAL STUDIO</small></span></button>
        <nav aria-label="主导航">{([['projects','项目首页'],['home','快捷工作台'],['director','导演台'],['library','素材库']] as const).map(([id,label])=><button key={id} className={view===id||(id==='director'&&view==='creative')?'active':''} aria-current={view===id||(id==='director'&&view==='creative')?'page':undefined} onClick={()=>navigate(id)}>{label}</button>)}</nav>
        <div className="c-top-tools"><StudioThemeToggle/><span className={`connection ${connected?'online':''}`} title={connected?'服务已连接':'连接暂时中断'}><i/></span><button className="task-top" aria-label="任务中心" onClick={()=>setTaskDrawer(true)}><Clock3 size={16}/><span>任务中心</span>{activeTasks.length>0&&<b>{activeTasks.length}</b>}</button><button className="c-layout-link" aria-label="打开排版编辑" onClick={()=>navigate('canvas')}><Layers size={17}/></button><span className="avatar">我</span></div>
      </header>
      {(view==='home'||view==='canvas')&&<div className="projectbar"><div className="project-name"><span className="project-icon"><Layers size={17}/></span><input disabled={!ready} aria-label="项目名称" value={project.name} onChange={e=>mutate(p=>({...p,name:e.target.value}),false)}/><ChevronDown size={14}/><span className="save-status">{saveState==='已保存到本机'?<Check size={12}/>:saveState==='保存中…'?<Loader2 size={12} className="spin"/>:<span className="small-dot"/>}{saveState}</span></div><div className="project-actions"><button className="secondary" disabled={!ready} onClick={()=>void saveProject()}><Save size={14}/>保存草稿</button>{blocked.current&&<button className="secondary" onClick={()=>{draftRef.current=null;blocked.current=false;dirty.current++;void saveProject();}}>另存为新草稿</button>}{view==='canvas'?<button className="primary" disabled={exporting} onClick={()=>void exportPng()}>{exporting?<Loader2 size={15} className="spin"/>:<Download size={15}/>}导出画布<ChevronDown size={12}/></button>:<button className="secondary" onClick={()=>navigate('canvas')}><Layers size={14}/>排版编辑</button>}</div></div>}
      {saveError&&(view==='home'||view==='canvas')&&<div className="persistent-notice" role="alert"><span>{saveError}</span><button className="secondary" onClick={()=>{if(blocked.current){draftRef.current=null;blocked.current=false;dirty.current++;}void saveProject();}}>{blocked.current?'另存为新草稿':'重试保存'}</button></div>}
      {notice&&<div className="toast" role="status"><span>{notice}</span><button aria-label="关闭提示" onClick={()=>setNotice('')}><X size={15}/></button></div>}
      <div className="workspace">
        {view==='canvas'&&<aside className="creation-panel"><div className="asset-panel-content"><button className="secondary" onClick={()=>navigate('home')}>返回工作台</button><button className="upload-zone" onClick={()=>openUpload('canvas')}>{uploading?<Loader2 size={24} className="spin"/>:<Upload size={24}/>}<strong>上传排版素材</strong><span>JPG / PNG / WebP · 最大 20MB</span></button><label className="searchbox"><Search size={14}/><input placeholder="搜索素材或 SKU" value={query} onChange={e=>setQuery(e.target.value)}/></label><p className="microcopy">点击图片预览，点 ＋ 加入排版画布。</p>{gallery(true)}</div></aside>}
        <main className="editor-main">
          {view==='projects'?<ProjectHome assets={assets} onCreateProject={createCreativeProject} onOpenProject={openCreativeProject} onStartGeneration={startGeneration} onWorkbench={()=>navigate('home')}/>:view==='director'?<DirectorHome assets={assets} onOpenProject={openCreativeProject} createRequest={creativeCreateNonce} onDirtyChange={onCreativeDirtyChange}/>:view==='creative'&&creativeProjectId?<CreativeSpace initialProjectId={creativeProjectId} onBack={()=>navigate('director')} onDirtyChange={onCreativeDirtyChange}/>:view==='home'?<Home assetsError={resourceErrors.assets} onRefresh={()=>void refresh()} assets={assets} activeCount={activeTasks.length} connected={connected} loading={!ready} kind={kind} onPreview={setPreview} onReference={selectReference} onEdit={addAsset} onLibrary={()=>changeView('library')} onTasks={()=>setTaskDrawer(true)}>{generationForm}</Home>:view==='library'?<><div className="library-heading"><div><span className="section-kicker">ASSET LIBRARY</span><h1>让每一次创作，都有迹可循。</h1><p>{assets.length} 份本机素材，随时回到画布。</p></div><button className="secondary" disabled={!marked.size||exporting} onClick={()=>void exportSelected()}><Download size={14}/>打包导出{marked.size?` (${marked.size})`:''}</button></div><div className="library-filters">{[['all','全部素材'],['image','图片'],['video','视频'],['upload','上传原图']].map(([id,label])=><button className={assetTab===id?'selected':''} onClick={()=>setAssetTab(id)} key={id}>{label}</button>)}<label className="searchbox"><Search size={14}/><input placeholder="搜索素材" value={query} onChange={e=>setQuery(e.target.value)}/></label></div><div className="library-scroll">{resourceErrors.assets&&<div className="persistent-notice" role="alert"><span>{resourceErrors.assets}</span><button className="secondary" onClick={()=>void refresh()}>重新读取</button></div>}{gallery()}</div></>:<>
            <div className="canvas-heading"><div><span className="section-kicker">LAYOUT EDITOR</span><h1>排版编辑<span>为商品图添加文案与布局。</span></h1></div><span className="canvas-size-label">{project.canvas.width} × {project.canvas.height} px</span></div>
            <div className="canvas-stage" onClick={()=>setSelected(null)}><div className="floating-tools" onClick={e=>e.stopPropagation()}><IconButton label="选择与移动" active onClick={()=>undefined}><MousePointer2 size={18}/></IconButton><i/><IconButton label="添加文字" onClick={()=>addElement('text')}><Type size={18}/></IconButton><IconButton label="添加色块" onClick={()=>addElement('shape')}><Square size={17}/></IconButton><IconButton label="添加图片" onClick={()=>openUpload('canvas')}><ImageIcon size={18}/></IconButton><i/><IconButton label="撤销" disabled={!undo.length} onClick={undoEdit}><Undo2 size={17}/></IconButton></div>
              <div className="canvas-paper" style={{aspectRatio:`${project.canvas.width}/${project.canvas.height}`,transform:`scale(${zoom})`}} onClick={e=>e.stopPropagation()}>
                <svg ref={svgRef} viewBox={`0 0 ${project.canvas.width} ${project.canvas.height}`} role="img" aria-label="商品排版画布" onPointerMove={onMove} onPointerUp={()=>{drag.current=null;}} onPointerCancel={()=>{drag.current=null;}} onPointerDown={()=>setSelected(null)} style={{background:project.canvas.background}}>
                  {project.canvas.layers.map(l=><g key={l.id} opacity={l.opacity} transform={`translate(${l.x} ${l.y}) rotate(${l.rotation} ${l.w/2} ${l.h/2})`} onPointerDown={e=>startDrag(e,l)} className="canvas-layer">
                    {l.type==='image'?<image href={mediaUrl(l.assetId!)} width={l.w} height={l.h} preserveAspectRatio="none"/>:l.type==='shape'?<rect width={l.w} height={l.h} fill={l.fill||'#ecebff'}/>:<><rect width={l.w} height={l.h} fill="transparent"/><text fill={l.color||'#202238'} fontSize={l.fontSize||64} fontWeight="600" dominantBaseline="hanging">{(l.text||'').split('\n').map((line,i)=><tspan key={i} x={0} y={i*(l.fontSize||64)*1.3}>{line}</tspan>)}</text></>}
                    {selected===l.id&&<><rect width={l.w} height={l.h} fill="none" stroke="#6965e8" strokeWidth={2} vectorEffect="non-scaling-stroke" pointerEvents="none"/><rect x={l.w-12} y={l.h-12} width={24} height={24} fill="white" stroke="#6965e8" strokeWidth={2} vectorEffect="non-scaling-stroke" className="resize-handle" onPointerDown={e=>startDrag(e,l,true)}/></>}
                  </g>)}
                </svg>
                {!project.canvas.layers.length&&<div className="canvas-empty"><div className="empty-illustration"><ImageIcon size={34}/><span>✦</span></div><strong>你的下一张主图，从这里开始</strong><p>添加一张商品图，或让 AI 帮你创造灵感。</p><button className="secondary" onClick={()=>navigate('library')}><Plus size={14}/>添加素材</button></div>}
              </div>
              <div className="zoom-controls" onClick={e=>e.stopPropagation()}><IconButton label="缩小画布" onClick={()=>setZoom(Math.max(.5,zoom-.1))}><Minus size={14}/></IconButton><button onClick={()=>setZoom(1)}>{Math.round(zoom*100)}%</button><IconButton label="放大画布" onClick={()=>setZoom(Math.min(1.3,zoom+.1))}><Plus size={14}/></IconButton><span/><button onClick={()=>setZoom(1)}>适应画布</button></div>
            </div>
            <div className="recent-strip"><div className="strip-heading"><strong>最近的灵感<span>{assets.filter(a=>a.source==='generated'||a.source==='cloud_generated').length}</span></strong><button onClick={()=>navigate('library')}>查看全部<ChevronDown size={12}/></button></div><div className="recent-items">{assets.filter(a=>(a.source==='generated'||a.source==='cloud_generated')&&a.size>2048).slice(0,8).map(a=><button key={a.id} className="recent-item" onClick={()=>addAsset(a)} aria-label="将最近素材加入画布">{a.kind==='image'?<img src={mediaUrl(a.id)} alt="真实生成素材"/>:<Film size={24}/>}<span><Plus size={12}/></span></button>)}<button className="recent-upload" onClick={()=>openUpload(view==='canvas'?'canvas':'library')}><Upload size={18}/><span>上传素材</span></button></div></div>
          </>}
        </main>
        {view==='canvas'&&<button className="properties-toggle secondary" aria-expanded={propertiesOpen} aria-controls="layout-properties" onClick={()=>setPropertiesOpen(!propertiesOpen)}><SlidersHorizontal size={16}/>属性与图层</button>}
        {view==='canvas'&&<aside id="layout-properties" aria-label="排版属性与图层" className={`properties ${propertiesOpen?'properties-open':''}`}><div className="properties-title"><SlidersHorizontal size={16}/> {selectedLayer?'图层属性':'画布设置'}<button className="properties-close icon-button" aria-label="收起属性与图层" onClick={()=>setPropertiesOpen(false)}><X size={18}/></button></div>
          {selectedLayer?<div className="property-content"><label>图层名称<input value={selectedLayer.name} onChange={e=>updateLayer({name:e.target.value})}/></label>{selectedLayer.type==='text'&&<><label>文案<textarea rows={3} value={selectedLayer.text} onChange={e=>updateLayer({text:e.target.value})}/></label><div className="field-pair"><label>字号<input type="number" min={8} value={selectedLayer.fontSize} onChange={e=>updateLayer({fontSize:Math.max(8,Number(e.target.value))})}/></label><label>颜色<input type="color" value={selectedLayer.color} onChange={e=>updateLayer({color:e.target.value})}/></label></div></>}{selectedLayer.type==='shape'&&<label>填充颜色<input type="color" value={selectedLayer.fill} onChange={e=>updateLayer({fill:e.target.value})}/></label>}
            <div className="field-pair">{(['x','y','w','h'] as const).map(k=><label key={k}>{({x:'X 位置',y:'Y 位置',w:'宽度',h:'高度'})[k]}<input aria-label={`图层${k}`} type="number" value={Math.round(selectedLayer[k])} onChange={e=>updateLayer({[k]:k==='w'||k==='h'?Math.max(20,Number(e.target.value)):Number(e.target.value)})}/></label>)}</div><label>不透明度 <span>{Math.round(selectedLayer.opacity*100)}%</span><input type="range" min={0} max={1} step={.05} value={selectedLayer.opacity} onChange={e=>updateLayer({opacity:Number(e.target.value)})}/></label><label>旋转<input type="number" value={selectedLayer.rotation} onChange={e=>updateLayer({rotation:Number(e.target.value)})}/></label><div className="layer-actions"><button className="secondary" onClick={()=>{const copy={...selectedLayer,id:crypto.randomUUID(),x:selectedLayer.x+30,y:selectedLayer.y+30,name:selectedLayer.name+' 副本'};mutate(p=>({...p,canvas:{...p.canvas,layers:[...p.canvas.layers,copy]}}));setSelected(copy.id);}}><Copy size={13}/>复制</button><button className="secondary" onClick={deleteLayer}><Trash2 size={13}/>移除</button></div>
          </div>:<div className="property-content"><label>画布比例<select aria-label="画布比例" value={`${project.canvas.width}:${project.canvas.height}`} onChange={e=>{const [width,height]=e.target.value.split(':').map(Number);mutate(p=>({...p,canvas:{...p.canvas,width,height}}));}}>{[[1080,1080,'1:1 · 商品主图'],[1080,1440,'3:4 · 内容封面'],[1080,1920,'9:16 · 竖版内容'],[1920,1080,'16:9 · 横版内容']].map(([w,h,name])=><option key={name} value={`${w}:${h}`}>{name}</option>)}</select></label><div className="field-pair"><label>宽度<input readOnly value={project.canvas.width}/></label><label>高度<input readOnly value={project.canvas.height}/></label></div><label>背景颜色<input aria-label="画布背景颜色" type="color" value={project.canvas.background} onChange={e=>mutate(p=>({...p,canvas:{...p.canvas,background:e.target.value}}))}/></label><div className="tip-card"><Sparkles size={17}/><strong>给商品，多一点表达</strong><p>将素材加入画布，叠加文案与色块。生成期间也能继续编辑。</p></div></div>}
          <div className="layer-section"><div className="properties-title"><Layers size={15}/>图层<span>{project.canvas.layers.length}</span></div>{project.canvas.layers.length?<div className="layer-list">{[...project.canvas.layers].reverse().map((l,i)=><div className={l.id===selected?'layer-row selected':'layer-row'} key={l.id}><button onClick={()=>setSelected(l.id)}>{l.type==='image'?<ImageIcon size={15}/>:l.type==='text'?<Type size={15}/>:<Square size={15}/>}<span>{l.name}</span></button><button title="上移一层" disabled={i===0} onClick={()=>mutate(p=>{const layers=[...p.canvas.layers],n=layers.findIndex(x=>x.id===l.id);[layers[n],layers[n+1]]=[layers[n+1],layers[n]];return {...p,canvas:{...p.canvas,layers}};})}>↑</button></div>)}</div>:<p className="empty-layers">添加素材后，图层会显示在这里。</p>}</div><div className="properties-footer"><span className="green-dot"/>素材与草稿保存在本机</div>
        </aside>}
      </div>

    </div>
    {pendingGeneration&&<GenerationConfirm prompts={pendingGeneration.prompts} modelName={pendingGeneration.modelName} kind={pendingGeneration.kind} count={pendingGeneration.items.length} specLabel={pendingGeneration.specLabel} costs={pendingGeneration.costs} error={pendingGeneration.error||pendingPermissionError||pendingEstimateError||''} busy={busy} hasReference={!!pendingGeneration.items[0].input_asset_id} onConfirm={()=>void confirmGeneration()} onClose={closeGeneration}/>}
    <input ref={fileRef} className="hidden" type="file" accept="image/png,image/jpeg,image/webp" multiple onChange={e=>void upload(e.target.files)}/>
    {referencePicker&&<ReferencePicker assets={assets} selectedId={reference} onSelect={a=>selectReference(a,kind)} onUpload={()=>openUpload(kind)} onClose={()=>setReferencePicker(false)} uploading={uploading} error={resourceErrors.assets} onRefresh={()=>void refresh()}/>}
    {preview&&<div className="modal-backdrop" onClick={()=>setPreview(null)}><section className="preview-modal" role="dialog" aria-modal="true" aria-label="素材预览" onClick={e=>e.stopPropagation()}><header><div><strong>{assetName(preview)}</strong><span>{preview.width}×{preview.height} · {(preview.size/1e6).toFixed(2)} MB</span></div><IconButton label="关闭预览" onClick={()=>setPreview(null)}><X size={20}/></IconButton></header><div className="preview-media">{preview.kind==='image'?<img src={mediaUrl(preview.id)} alt={assetName(preview)}/>:<video src={mediaUrl(preview.id)} controls autoPlay/>}</div><div className="preview-actions"><a className="secondary" href={mediaUrl(preview.id)} download><ArrowDownToLine size={15}/>下载原文件</a>{preview.kind==='image'&&<><button className="secondary" onClick={()=>selectReference(preview,'image')}><ImageIcon size={15}/>用作图生图参考</button><button className="secondary" onClick={()=>selectReference(preview,'video')}><Film size={15}/>用作视频首帧</button><button className="primary" onClick={()=>{addAsset(preview);setPreview(null);}}>加入排版<Plus size={15}/></button></>}</div><label className="sku-label">SKU 分组（仅用于项目内管理）<input placeholder="例如：面霜系列 / 白色款" value={project.assetGroups[preview.id]||''} onChange={e=>mutate(p=>({...p,assetGroups:{...p.assetGroups,[preview.id]:e.target.value}}),false)}/></label></section></div>}
    {taskDrawer&&<div className="drawer-backdrop" onClick={()=>setTaskDrawer(false)}><aside className="task-drawer" role="dialog" aria-label="任务中心" onClick={e=>e.stopPropagation()}><header><div><span className="section-kicker">TASK CENTER</span><h2>让创作持续发生</h2></div><IconButton label="关闭任务中心" onClick={()=>setTaskDrawer(false)}><X size={20}/></IconButton></header><div className="queue-summary"><span><b>{activeTasks.length}</b>进行中</span><span><b>{tasks.filter(t=>t.status==='succeeded').length}</b>已完成</span><button className="secondary" onClick={()=>void refresh()}><RefreshCw size={13}/>刷新</button></div><p className="drawer-hint">真实后端状态 · 生成结果不会覆盖画布编辑</p>{resourceErrors.tasks&&<p className="persistent-notice" role="alert">{resourceErrors.tasks}</p>}<div className="task-list">{tasks.map(t=>{const a=assets.find(a=>a.id===t.output_asset_id);return <article className="task-card" key={t.id}><div className="task-card-top"><div className="task-symbol">{t.kind==='image'?<ImageIcon size={17}/>:<Film size={17}/>}</div><div><strong>{t.kind==='image'?'图片生成':'商品视频'}</strong><small>{models.find(m=>m.id===t.model_id)?.name||'云端生成模型'}</small></div><span className={`task-badge ${t.status}`}>{ACTIVE.includes(t.status)&&<Loader2 size={11} className="spin"/>}{NAMES[t.status]||t.status}</span></div>{ACTIVE.includes(t.status)&&<div className="progress-track"><span style={{width:`${Math.max(3,t.progress*100)}%`}}/></div>}{t.error_message&&<p className="task-error">{ERRORS[t.error_code||'']||'本次任务未完成，请查看详情后处理。'}</p>}{t.error_message&&<details className="task-error-detail"><summary>查看错误详情</summary><p>{t.error_message}</p></details>}{a&&<button className="task-result" onClick={()=>setPreview(a)}>{a.kind==='image'?<img src={mediaUrl(a.id)} alt="任务真实结果"/>:<Film size={22}/>}<span>查看生成结果<ArrowUpRight size={14}/></span></button>}<div className="task-meta"><span>{time(t.latency_ms)}</span><span>{t.provider==='ark'||t.model_id.startsWith('doubao')?'云端':'本地'}{t.cost_cny!=null?` · 估算 ¥${t.cost_cny.toFixed(2)}`:''}</span></div><div className="task-actions">{ACTIVE.includes(t.status)&&<button className="secondary" onClick={()=>void taskAction(t,'cancel')}>请求取消</button>}{['failed','interrupted','canceled'].includes(t.status)&&t.error_code!=='CREATE_RESULT_UNKNOWN'&&<button className="secondary" onClick={()=>void taskAction(t,'retry')}><RefreshCw size={12}/>从头重试</button>}{a?.kind==='image'&&<button className="secondary" onClick={()=>{addAsset(a);setTaskDrawer(false);}}>加入画布<Plus size={12}/></button>}</div></article>;})}{!tasks.length&&!resourceErrors.tasks&&<div className="empty-state"><Clock3 size={26}/><strong>还没有生成任务</strong><span>在左侧描述画面，开始第一次生成。</span></div>}</div></aside></div>}
  </div>;
}
