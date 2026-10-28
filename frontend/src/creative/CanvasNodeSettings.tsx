import { useEffect, useRef, useState } from 'react';
import { ArrowUp, Check, ChevronDown, Download, Ellipsis, FileText, ImagePlus, LoaderCircle, Plus, Save, SlidersHorizontal, Trash2, Undo2, Upload, Video, X } from 'lucide-react';
import { mediaUrl } from '../api';
import type { Asset, GenerationMode, Model } from '../types';
import { getImageSizesForRatio, getModelUnavailableReason, getSupportedRatios, getSupportedResolutions } from '../capabilities';
import { imageTierOptions, ratioPreview } from '../image-specs';
import { durationsFor, modeLabels, nodeGenerationIssue, nodeModes, objectValue, sameNodeConfig, textValue, withNodeMode, withNodeModel, type NodeConfig } from './node-config';
import type { CanvasNodeRecord } from './canvas-layout-sync';
import './CanvasNodeSettings.css';

interface Props {
  projectId: string; node: CanvasNodeRecord; assets: Asset[]; models: Model[]; disabled?: boolean;
  onSave: (config: NodeConfig, revision: number) => Promise<void>; onDelete: () => Promise<void>;
  onGenerate?: (config: NodeConfig, expectedRevision: number) => Promise<void>;
  onUploadReference?: (file: File) => Promise<Asset>;
  onDirtyChange: (dirty: boolean) => void; onUseAsset?: (kind: 'image'|'video', assetId: string) => void;
}
const key = (projectId: string, id: string) => `material-node-draft-v1:${encodeURIComponent(projectId)}:${encodeURIComponent(id)}`;
function readDraft(projectId: string, node: CanvasNodeRecord) {
  try {
    const value = JSON.parse(localStorage.getItem(key(projectId, node.node_id)) || 'null');
    if (value && typeof value.config === 'object' && value.config && !Array.isArray(value.config) && Number.isInteger(value.revision)) {
      return { config: value.config as NodeConfig, revision: value.revision as number };
    }
  } catch { /* A broken local draft never replaces server data. */ }
  return { config: node.config, revision: node.config_revision };
}
const ratioName = (ratio: string) => ({ '16:9': '横屏', '9:16': '竖屏', '1:1': '方形', '4:3': '横向', '3:4': '竖向' })[ratio] || '画面';
export default function CanvasNodeSettings(props: Props) { return <NodeEditor key={props.projectId + props.node.node_id} {...props}/>; }

function NodeEditor({ projectId, node, assets, models, disabled, onSave, onDelete, onGenerate, onUploadReference, onDirtyChange, onUseAsset }: Props) {
  const [initial] = useState(() => readDraft(projectId, node));
  const [draft, setDraft] = useState<NodeConfig>(initial.config), [revision, setRevision] = useState(initial.revision);
  const [operation, setOperation] = useState<'save'|'generate'|'upload'|'delete'|null>(null);
  const [error, setError] = useState(''), [notice, setNotice] = useState('');
  const [panel, setPanel] = useState<'specs'|'references'|'node'|null>(null);
  const [uploadedReferences, setUploadedReferences] = useState<Asset[]>([]);
  const uploadInput = useRef<HTMLInputElement>(null);
  const lock = useRef(false), draftRef = useRef(draft), callbacks = useRef({ onDirtyChange });
  callbacks.current = { onDirtyChange }; draftRef.current = draft;
  const dirty = !sameNodeConfig(draft, node.config), busy = operation !== null, locked = busy || !!disabled;
  const baseConfig = useRef(node.config);
  useEffect(() => { callbacks.current.onDirtyChange(dirty || busy); }, [dirty, busy]);
  useEffect(() => () => callbacks.current.onDirtyChange(false), []);
  useEffect(() => {
    const current = draftRef.current;
    if (sameNodeConfig(current, baseConfig.current) || sameNodeConfig(current, node.config)) {
      setDraft(node.config); setRevision(node.config_revision);
    }
    baseConfig.current = node.config;
  }, [node.config, node.config_revision]);
  const update = (config: NodeConfig) => {
    setDraft(config); setNotice('');
    try { localStorage.setItem(key(projectId, node.node_id), JSON.stringify({ config, revision })); setError(''); }
    catch { setError('修改暂未备份，请保持页面开启并保存。'); }
  };
  const clearBackup = () => { try { localStorage.removeItem(key(projectId, node.node_id)); } catch { /* The server copy remains saved. */ } };
  const save = async () => {
    if (lock.current || disabled || mismatch) return;
    lock.current = true; setOperation('save'); setError('');
    try { await onSave(draftRef.current, revision); setNotice('已保存'); clearBackup(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : '保存未完成，修改已保留。'); }
    finally { lock.current = false; setOperation(null); }
  };
  const generate = async () => {
    if (lock.current || disabled || mismatch || executionActive || !onGenerate) return;
    if (generationIssue) { setError(generationIssue); return; }
    lock.current = true; setOperation('generate'); setError(''); setNotice('');
    try { await onGenerate(draftRef.current, revision); clearBackup(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : '生成未完成，当前设置已保留。'); }
    finally { lock.current = false; setOperation(null); }
  };
  const remove = async () => {
    if (lock.current || disabled || dirty) return;
    lock.current = true; setOperation('delete'); setError('');
    try { await onDelete(); clearBackup(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : '移除未完成，请重新读取画布核对。'); }
    finally { lock.current = false; setOperation(null); }
  };
  const reset = () => { setDraft(node.config); setRevision(node.config_revision); setNotice(''); setError(''); clearBackup(); };
  const isMedia = node.type === 'image' || node.type === 'video', kind = node.type === 'image' ? 'image' : 'video';
  const mode = (textValue(draft.generation_mode) || nodeModes(kind)[0]) as GenerationMode;
  const model = models.find(item => item.id === draft.model_id), params = objectValue(draft.params), ratio = textValue(params.ratio);
  const choices = models.filter(item => item.kind === kind && item.provider === 'ark' && item.capabilities?.input_modes?.includes(mode));
  const tiers = imageTierOptions(model, ratio), currentTier = tiers.find(tier => tier.sizes.includes(textValue(params.size)));
  const resolutions = getSupportedResolutions(model), durations = durationsFor(model);
  const referenceId = textValue(draft.input_asset_id), needsReference = isMedia && mode.startsWith('image_');
  const referenceAssets = [...assets, ...uploadedReferences.filter(item => !assets.some(asset => asset.id === item.id))].filter(item => item.kind === 'image');
  const setParams = (patch: NodeConfig) => update({ ...draftRef.current, params: { ...objectValue(draftRef.current.params), ...patch } });
  const mismatch = dirty && revision !== node.config_revision;
  const generationIssue = isMedia ? nodeGenerationIssue(kind, draft, model) : null;
  const execution = node.last_execution, executionStatus = textValue(execution?.status);
  const executionActive = execution?.active === true || ['queued', 'running', 'processing'].includes(executionStatus);
  const executionLabel = executionStatus === 'queued' ? '排队中' : ['running', 'processing'].includes(executionStatus) ? '生成中' : '等待生成结果';
  const executionError = objectValue(execution?.asset_error || execution?.error), executionErrorMessage = textValue(executionError.message);
  const progress = typeof execution?.progress === 'number' && Number.isFinite(execution.progress) ? Math.round(Math.min(1, Math.max(0, execution.progress)) * 100) : null;
  const generationLabel = execution ? '重新生成' : kind === 'image' ? '生成图片' : '生成视频';
  const specSummary = [ratio || '比例', kind === 'image' ? currentTier?.label || '清晰度' : textValue(params.resolution) || '清晰度', ...(kind === 'video' ? [typeof params.duration === 'number' && Number.isFinite(params.duration) ? `${params.duration}s` : '时长'] : [])].join(' · ');
  const changeMode = (value: GenerationMode, config = draftRef.current) => {
    let next = withNodeMode(config, value);
    if (model && !model.capabilities?.input_modes?.includes(value)) { next = { ...next }; delete next.model_id; delete next.params; }
    return next;
  };
  const chooseReference = (id: string) => { update({ ...changeMode(`image_to_${kind}`), input_asset_id: id }); setPanel(null); };
  const uploadReference = async (file: File) => {
    if (lock.current || disabled || !onUploadReference) return;
    lock.current = true; setOperation('upload'); setError('');
    try {
      const asset = await onUploadReference(file);
      if (asset.kind !== 'image') throw new Error('请选择图片作为参考。');
      setUploadedReferences(items => [...items.filter(item => item.id !== asset.id), asset]);
      chooseReference(asset.id);
    } catch (caught) { setError(caught instanceof Error ? caught.message : '参考图未上传成功，请重试。'); }
    finally { lock.current = false; setOperation(null); }
  };

  return <section className="canvas-node-settings cn-composer" aria-label="独立节点设置" onKeyDown={event => { if (event.key === 'Escape' && panel) { event.stopPropagation(); setPanel(null); } }}>
    <fieldset className="cnc-fields" disabled={locked}>
      <div className="cnc-topline">
        {isMedia ? <div className="cnc-modes" role="group" aria-label="节点输入方式">{nodeModes(kind).map(value => <button key={value} type="button" aria-pressed={mode === value} className={mode === value ? 'is-active' : ''} onClick={() => { update(changeMode(value)); if (value.startsWith('text_')) setPanel(null); }}>{modeLabels[value]}</button>)}</div>
          : <span className="cnc-content-kind"><FileText size={14}/>{node.type === 'text' ? '文本笔记' : '素材设置'}</span>}
        <button type="button" className="cnc-icon" title="节点名称与移除" aria-label="节点名称与移除" aria-expanded={panel === 'node'} onClick={() => setPanel(value => value === 'node' ? null : 'node')}><Ellipsis size={18}/></button>
      </div>
      {panel === 'node' && <div className="cnc-options" aria-label="节点选项"><label>节点名称<input aria-label="节点名称" maxLength={200} value={textValue(draft.title)} placeholder={node.type === 'text' ? '文本笔记' : isMedia ? `${kind === 'image' ? '图片' : '视频'}创作` : '导入素材'} onChange={event => update({ ...draft, title: event.target.value })}/></label><button type="button" className="cnc-remove" disabled={dirty || locked} title={dirty ? '先保存或撤销修改，再移除节点' : '从画布移除节点'} onClick={() => void remove()}><Trash2 size={14}/>移除节点</button></div>}
      {(isMedia || node.type === 'text') && <div className="cnc-prompt-area">
        {needsReference && <div className="cnc-reference-strip">
          {referenceId && <div className="cnc-reference-thumb"><button type="button" title="更换参考图片" aria-label="更换参考图片" onClick={() => setPanel(value => value === 'references' ? null : 'references')}><img src={mediaUrl(referenceId)} alt="节点参考图"/></button><button type="button" className="cnc-reference-remove" title="移除参考图片" aria-label="移除参考图片" onClick={() => { const next = { ...draft }; delete next.input_asset_id; update(next); }}><X size={10}/></button></div>}
          <button type="button" className="cnc-add-reference" aria-label="选择参考图片" aria-expanded={panel === 'references'} onClick={() => setPanel(value => value === 'references' ? null : 'references')}><Plus size={19}/><span>{referenceId ? '更换参考图' : '添加参考图'}</span></button>
          {onUploadReference && <button type="button" className="cnc-reference-upload" onClick={() => uploadInput.current?.click()}>{operation === 'upload' ? <LoaderCircle size={14} className="cnc-spin"/> : <Upload size={14}/>}上传</button>}
        </div>}
        {panel === 'references' && needsReference && <div className="cnc-reference-picker" aria-label="从素材库选择参考图片"><div className="cnc-picker-heading"><span>引用素材</span><button type="button" className="cnc-icon" aria-label="收起参考图片" onClick={() => setPanel(null)}><X size={13}/></button></div>{referenceAssets.length ? <div className="cnc-reference-grid" role="group" aria-label="参考图片">{referenceAssets.map((asset, index) => <button type="button" key={asset.id} aria-label={`引用图片 ${index + 1}`} title={`图片 ${index + 1} · ${asset.width} × ${asset.height}`} aria-pressed={referenceId === asset.id} onClick={() => chooseReference(asset.id)}><img src={mediaUrl(asset.id)} alt={`素材 ${index + 1}`} loading="lazy"/>{referenceId === asset.id && <Check size={12}/>}</button>)}</div> : <p>还没有图片素材{onUploadReference ? '，可以直接上传一张。' : '，请先添加到素材库。'}</p>}</div>}
        <textarea className="cnc-prompt" aria-label={isMedia ? '节点提示词' : '节点文本内容'} rows={isMedia ? 4 : 7} maxLength={isMedia ? 20000 : 100000} value={textValue(isMedia ? draft.prompt : draft.text)} placeholder={isMedia ? kind === 'image' ? '描述你想要的画面，加入风格、光线与细节…' : '描述画面如何运动、镜头如何推进…' : '写下脚本、创意或需要记住的细节…'} onChange={event => update({ ...draft, [isMedia ? 'prompt' : 'text']: event.target.value })}/>
      </div>}
      {isMedia && panel === 'specs' && <div className="cnc-options cnc-specs" aria-label="生成规格"><div className="cnc-picker-heading"><span>画面设置</span><button type="button" className="cnc-icon" aria-label="收起画面设置" onClick={() => setPanel(null)}><X size={13}/></button></div>{model ? <>
        <span className="cnc-option-label">画面比例</span><div className="cnc-ratios" role="group" aria-label="节点画面比例">{getSupportedRatios(model).map(value => <button key={value} type="button" aria-pressed={ratio === value} className={ratio === value ? 'is-active' : ''} onClick={() => { const sizes = getImageSizesForRatio(model, value); setParams({ ratio: value, ...(kind === 'image' && sizes.length ? { size: sizes.includes(textValue(params.size)) ? params.size : sizes[0] } : {}) }); }}><i style={ratioPreview(value)}/><span>{ratioName(value)}<small>{value}</small></span></button>)}</div>
        {kind === 'image' ? <><span className="cnc-option-label">清晰度</span><div className="cnc-choice-row" role="group" aria-label="节点图片清晰度">{tiers.map(tier => <button key={tier.label} type="button" aria-pressed={currentTier?.label === tier.label} className={currentTier?.label === tier.label ? 'is-active' : ''} onClick={() => setParams({ size: tier.sizes[0] })}>{tier.label}</button>)}</div>{!tiers.length && <p className="cnc-hint">请选择支持的画面比例。</p>}</> : <div className="cnc-video-specs"><label>时长<select aria-label="节点视频时长" value={typeof params.duration === 'number' ? params.duration : ''} onChange={event => setParams({ duration: Number(event.target.value) })}><option value="" disabled>选择时长</option>{typeof params.duration === 'number' && !durations.includes(params.duration) && <option value={params.duration} disabled>{params.duration} 秒 · 需调整</option>}{durations.map(duration => <option key={duration} value={duration}>{duration} 秒</option>)}</select></label><label>清晰度<select aria-label="节点视频清晰度" value={textValue(params.resolution)} onChange={event => setParams({ resolution: event.target.value })}><option value="" disabled>选择清晰度</option>{!!params.resolution && !resolutions.includes(textValue(params.resolution)) && <option value={textValue(params.resolution)} disabled>{textValue(params.resolution)} · 需调整</option>}{resolutions.map(value => <option key={value}>{value}</option>)}</select></label></div>}
      </> : <p className="cnc-hint">先在下方选择模型，再调整画面规格。</p>}</div>}
      {node.type === 'asset' && node.asset && <div className="cnc-asset"><p>{node.asset.kind === 'video' ? '视频' : '图片'} · {node.asset.width} × {node.asset.height}{node.asset.duration_ms ? ` · ${(node.asset.duration_ms / 1000).toFixed(1)} 秒` : ''}</p><a href={mediaUrl(node.asset.id)} download><Download size={14}/>下载素材</a>{node.asset.kind === 'image' && onUseAsset && <div className="cnc-asset-actions"><button type="button" onClick={() => onUseAsset('image', node.asset!.id)}><ImagePlus size={15}/>用于图生图</button><button type="button" onClick={() => onUseAsset('video', node.asset!.id)}><Video size={15}/>用于图生视频</button></div>}</div>}
      <div className="cnc-bottom-bar">
        {isMedia ? <><label className="cnc-model"><span className="cnc-sr-only">节点模型</span><select aria-label="节点模型" value={textValue(draft.model_id)} onChange={event => { const selected = choices.find(item => item.id === event.target.value); if (selected) update(withNodeModel(draftRef.current, selected)); }}><option value="" disabled>选择模型</option>{!!draft.model_id && !choices.some(item => item.id === draft.model_id) && <option value={textValue(draft.model_id)} disabled>原模型暂不可用</option>}{choices.map(item => <option key={item.id} value={item.id} disabled={!!getModelUnavailableReason(item)}>{item.name}{getModelUnavailableReason(item) ? ' · 暂不可用' : ''}</option>)}</select><ChevronDown size={12}/></label><button type="button" className={'cnc-spec-trigger' + (panel === 'specs' ? ' is-active' : '')} aria-label={`画面设置：${specSummary}`} aria-expanded={panel === 'specs'} title={specSummary} onClick={() => setPanel(value => value === 'specs' ? null : 'specs')}><SlidersHorizontal size={13}/><span>{specSummary}</span></button></> : <span className="cnc-draft-label">{dirty ? '编辑中' : '已保存'}</span>}
        <div className="cnc-actions">{dirty && <button type="button" className="cnc-icon" title="撤销修改" aria-label="撤销节点修改" onClick={reset}><Undo2 size={15}/></button>}<button type="button" className="cnc-icon cnc-save" title="仅保存设置" aria-label={isMedia ? '仅保存节点设置' : '保存节点内容'} disabled={!dirty || mismatch} onClick={() => void save()}>{operation === 'save' ? <LoaderCircle size={15} className="cnc-spin"/> : <Save size={15}/>}</button>{isMedia && <button type="button" className="cnc-generate" aria-label={executionActive ? '正在生成' : generationLabel} title={executionActive ? '当前任务仍在处理中' : !onGenerate ? '生成暂不可用' : mismatch ? '先处理节点设置冲突' : generationIssue || generationLabel} disabled={!onGenerate || mismatch || executionActive || !!generationIssue} onClick={() => void generate()}>{operation === 'generate' || executionActive ? <LoaderCircle size={19} className="cnc-spin"/> : <ArrowUp size={20}/>}</button>}</div>
      </div>
      {onUploadReference && <input ref={uploadInput} type="file" accept="image/jpeg,image/png,image/webp,image/gif" hidden aria-label="上传节点参考图" onChange={event => { const file = event.currentTarget.files?.[0]; event.currentTarget.value = ''; if (file) void uploadReference(file); }}/>}
    </fieldset>
    {mismatch && <div className="cnc-warning" role="alert"><p>节点已有新设置，你的修改仍保留。</p><button type="button" disabled={locked} onClick={reset}>使用最新设置</button><button type="button" disabled={locked} onClick={() => { setRevision(node.config_revision); setNotice(''); try { localStorage.setItem(key(projectId, node.node_id), JSON.stringify({ config: draft, revision: node.config_revision })); setError(''); } catch { setError('修改暂未备份，请保持页面开启并保存。'); } }}>保留我的修改</button></div>}
    {error && <p className="cnc-warning" role="alert">{error}</p>}
    {executionActive && <div className="cnc-execution" role="status"><LoaderCircle size={12} className="cnc-spin"/><span>{executionLabel}</span>{progress !== null && <><progress max={100} value={progress}/><small>{progress}%</small></>}</div>}
    {!executionActive && executionErrorMessage && <p className="cnc-warning" role="alert">{executionErrorMessage}</p>}
    {!locked && !error && !mismatch && !executionActive && !!generationIssue && (!!draft.prompt || !!draft.model_id) && <p className="cnc-hint">{generationIssue}</p>}
    {notice && !error && <p className="cnc-notice" role="status"><Check size={12}/>{notice}</p>}
  </section>;
}
