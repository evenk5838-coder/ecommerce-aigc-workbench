import { useEffect, useRef, useState } from 'react';
import { Check, Copy, FileStack, LoaderCircle, Plus, RefreshCw, Save, Trash2 } from 'lucide-react';
import { ApiError } from '../api';
import { productionApi } from './production-api';
import { inspectStructureSnapshot } from './production-guards';
import type { AppliedStructure, StructureShot, StructureTemplate, StructureTemplateCreation, StructureTemplateInput, StructureTemplateSummary } from './production-types';
import './StructureTemplates.css';

export interface StructureTemplatesProps {
  projectId: string;
  /** Explicit structure only. Never pass product descriptions, prompts or asset identifiers. */
  snapshot?: StructureShot[];
  disabled?: boolean;
  onApplied?: (result: AppliedStructure) => void | Promise<void>;
  onSaved?: (result: StructureTemplateCreation) => void | Promise<void>;
  onDirtyChange?: (dirty: boolean) => void;
  onPendingChange?: (pending: boolean) => void;
}
export interface StructureDraftRow { role: string; duration_s: string; motion: string; rhythm: string }
interface PendingTemplateOperation { kind: 'save' | 'apply'; label: string; acknowledged?: boolean }
interface TemplateLocalDraft { name: string; rows: StructureDraftRow[]; pending?: PendingTemplateOperation }
const emptyRow = (): StructureDraftRow => ({ role: '', duration_s: '', motion: '', rhythm: '' });
const localKey = (projectId: string) => 'material-studio-structure-template-draft-v1:' + encodeURIComponent(projectId);
const message = (error: unknown) => error instanceof Error ? error.message : '结构模板请求失败，请刷新核对。';
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
const rowsFrom = (snapshot: StructureShot[]): StructureDraftRow[] => snapshot.map(shot => ({ role: shot.role, duration_s: String(shot.duration_s), motion: shot.motion, rhythm: shot.rhythm }));

/** A dedicated whitelist prevents product fields or references from entering the request. */
export function buildStructureTemplateDraft(name: string, rows: StructureDraftRow[]): { input: StructureTemplateInput | null; error: string | null } {
  if (!name.trim()) return { input: null, error: '请填写结构模板名称。' };
  if (!rows.length) return { input: null, error: '请至少填写一个镜头结构。' };
  for (const row of rows) {
    if (!/^\d+$/.test(row.duration_s.trim()) || !Number.isSafeInteger(Number(row.duration_s)) || Number(row.duration_s) <= 0) {
      return { input: null, error: '每镜时长需为正整数秒；不自动取整或补默认值。' };
    }
  }
  const snapshot = rows.map((row, index) => ({ seq: index + 1, role: row.role.trim(), duration_s: Number(row.duration_s), motion: row.motion, rhythm: row.rhythm }));
  const check = inspectStructureSnapshot(snapshot);
  return check.ok ? { input: { name: name.trim(), snapshot }, error: null } : { input: null, error: check.reason ?? '镜头结构格式无效。' };
}
function readDraft(projectId: string): TemplateLocalDraft {
  try {
    const raw: unknown = JSON.parse(localStorage.getItem(localKey(projectId)) || 'null');
    if (record(raw) && typeof raw.name === 'string' && Array.isArray(raw.rows) && raw.rows.every(row => record(row) && ['role', 'duration_s', 'motion', 'rhythm'].every(key => typeof row[key] === 'string'))) {
      const pending: PendingTemplateOperation | undefined = record(raw.pending) && (raw.pending.kind === 'save' || raw.pending.kind === 'apply') && typeof raw.pending.label === 'string'
        ? { kind: raw.pending.kind, label: raw.pending.label, acknowledged: raw.pending.acknowledged === true } : undefined;
      return { name: raw.name, rows: raw.rows.map(row => ({ role: row.role as string, duration_s: row.duration_s as string, motion: row.motion as string, rhythm: row.rhythm as string })), pending };
    }
  } catch { /* Local drafts cannot substitute for server results. */ }
  return { name: '', rows: [emptyRow()] };
}

export default function StructureTemplates(props: StructureTemplatesProps) {
  return <StructureTemplatesSession key={props.projectId} {...props} />;
}
function StructureTemplatesSession({ projectId, snapshot, disabled = false, onApplied, onSaved, onDirtyChange, onPendingChange }: StructureTemplatesProps) {
  const [initial] = useState(() => readDraft(projectId));
  const [name, setName] = useState(initial.name);
  const [rows, setRows] = useState(initial.rows);
  const [pending, setPending] = useState(initial.pending);
  const [templates, setTemplates] = useState<StructureTemplateSummary[]>([]);
  const [selectedId, setSelectedId] = useState('');
  const [version, setVersion] = useState('');
  const [selected, setSelected] = useState<StructureTemplate | null>(null);
  const [factFree, setFactFree] = useState(false);
  const [applyAcknowledged, setApplyAcknowledged] = useState(false);
  const [loadingList, setLoadingList] = useState(false);
  const [loadingTemplate, setLoadingTemplate] = useState(false);
  const [busy, setBusy] = useState('');
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [storageError, setStorageError] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const [checkedAfterPending, setCheckedAfterPending] = useState(false);
  const [applyResult, setApplyResult] = useState<AppliedStructure | null>(null);
  const alive = useRef(true);
  const listRequest = useRef<AbortController | null>(null);
  const detailRequest = useRef<AbortController | null>(null);
  const writeRequest = useRef<AbortController | null>(null);
  const lock = useRef(false);
  const notificationCallbacks = useRef({ onDirtyChange, onPendingChange });
  notificationCallbacks.current = { onDirtyChange, onPendingChange };
  const stored = useRef<TemplateLocalDraft>(initial);
  stored.current = { name, rows, pending };
  const mutationPending = !!busy || !!pending;
  const dirty = !!name.trim() || rows.some(row => Object.values(row).some(value => value.trim())) || mutationPending;
  const blocked = disabled || mutationPending || unavailable || !projectId.trim();
  const draft = buildStructureTemplateDraft(name, rows);
  const canCopy = !!snapshot && inspectStructureSnapshot(snapshot).ok;
  const persist = (next: TemplateLocalDraft) => {
    stored.current = next;
    try { if (projectId.trim()) localStorage.setItem(localKey(projectId), JSON.stringify(next)); }
    catch { if (alive.current) setStorageError(true); }
  };
  useEffect(() => { persist({ name, rows, pending }); }, [name, rows, pending]);
  useEffect(() => { notificationCallbacks.current.onDirtyChange?.(dirty); }, [dirty]);
  useEffect(() => { notificationCallbacks.current.onPendingChange?.(mutationPending); }, [mutationPending]);
  const loadList = async (): Promise<boolean> => {
    listRequest.current?.abort(); const controller = new AbortController(); listRequest.current = controller;
    setLoadingList(true); setError('');
    try {
      const result = await productionApi.listStructureTemplates({ signal: controller.signal });
      if (!alive.current || controller.signal.aborted) return false;
      setTemplates(result.items); setUnavailable(false); setCheckedAfterPending(true); return true;
    } catch (caught) {
      if (alive.current && !controller.signal.aborted) { setError(message(caught)); setUnavailable(caught instanceof ApiError && caught.code === 'PRODUCTION_UNAVAILABLE'); }
      return false;
    } finally { if (alive.current && !controller.signal.aborted) setLoadingList(false); }
  };
  useEffect(() => {
    alive.current = true; void loadList();
    return () => { alive.current = false; listRequest.current?.abort(); detailRequest.current?.abort(); writeRequest.current?.abort(); notificationCallbacks.current.onDirtyChange?.(false); notificationCallbacks.current.onPendingChange?.(false); };
  }, [projectId]);
  const loadTemplate = async () => {
    if (!selectedId || !/^\d+$/.test(version) || !Number.isSafeInteger(Number(version)) || Number(version) < 1) { setError('请明确选择模板及正整数版本号。'); return; }
    detailRequest.current?.abort(); const controller = new AbortController(); detailRequest.current = controller;
    setLoadingTemplate(true); setError(''); setSelected(null); setApplyAcknowledged(false);
    try {
      const result = await productionApi.getStructureTemplate(selectedId, Number(version), { signal: controller.signal });
      if (alive.current && !controller.signal.aborted) setSelected(result);
    } catch (caught) { if (alive.current && !controller.signal.aborted) setError(message(caught)); }
    finally { if (alive.current && !controller.signal.aborted) setLoadingTemplate(false); }
  };
  const run = async <T,>(kind: 'save' | 'apply', label: string, action: (signal: AbortSignal) => Promise<T>, success: (result: T) => Promise<boolean | void>) => {
    if (blocked || lock.current) return;
    // Only a read started after this mutation may release recovery protection.
    listRequest.current?.abort(); detailRequest.current?.abort(); setLoadingList(false); setLoadingTemplate(false);
    lock.current = true; setBusy(label); setError(''); setNotice(''); setCheckedAfterPending(false);
    const marker: PendingTemplateOperation = { kind, label }; setPending(marker); persist({ ...stored.current, pending: marker });
    const controller = new AbortController(); writeRequest.current = controller;
    try {
      const result = await action(controller.signal);
      if (!alive.current) return;
      const acknowledged = { ...marker, acknowledged: true }; setPending(acknowledged); persist({ ...stored.current, pending: acknowledged });
      try {
        const complete = await success(result);
        if (alive.current && complete !== false) { setPending(undefined); persist({ ...stored.current, pending: undefined }); }
      } catch (caught) { if (alive.current) setError(`操作已成功，但后续读取失败：${message(caught)}请先核对，不要重复提交。`); }
    } catch (caught) {
      if (!alive.current) return;
      setError(message(caught));
      if (!(caught instanceof ApiError && caught.code === 'CREATIVE_RESULT_UNKNOWN')) { setPending(undefined); persist({ ...stored.current, pending: undefined }); }
      if (caught instanceof ApiError && caught.code === 'PRODUCTION_UNAVAILABLE') setUnavailable(true);
    } finally { lock.current = false; if (alive.current) setBusy(''); }
  };
  const saveTemplate = async () => {
    if (!factFree || !draft.input) { setError(draft.error || '请先确认结构中不含商品事实或素材。'); return; }
    await run('save', '保存结构模板', signal => productionApi.createStructureTemplate(draft.input!, { signal }), async result => {
      setNotice(`已保存结构模板 ${result.template_id} · v${result.version}。`);
      setSelectedId(result.template_id); setVersion(String(result.version)); setSelected(null); setApplyAcknowledged(false);
      await onSaved?.(result);
      return loadList();
    });
  };
  const applyTemplate = async () => {
    if (!selected || !applyAcknowledged) return;
    const target = selected;
    await run('apply', `套用 ${target.name} v${target.version}`, signal => productionApi.applyStructure(projectId, { template_id: target.template_id, version: target.version }, { signal }), async result => {
      setApplyResult(result); setApplyAcknowledged(false);
      setNotice(`服务端已返回套用结果：创建 ${result.created_shots} 个镜头骨架。请重新填写商品资料、绑定素材并核对模型规格。`);
      await onApplied?.(result);
    });
  };
  const changeRow = (index: number, key: keyof StructureDraftRow, value: string) => { setRows(current => current.map((row, i) => i === index ? { ...row, [key]: value } : row)); setFactFree(false); };
  return <section className="structure-templates" aria-label="结构模板">
    <header className="st-heading"><div><span className="st-eyebrow">可复用的叙事结构</span><h2><FileStack size={20} />结构模板</h2><p>保留镜头职能、节奏、运镜与时长。每次套用都重新填写商品资料、绑定素材，并核对模型能力。</p></div><button type="button" disabled={loadingList || !!busy} onClick={() => void loadList()}><RefreshCw size={15} />{loadingList ? '读取中' : '刷新模板'}</button></header>
    {error && <p className="st-message st-error" role="alert">{error}</p>}{notice && <p className="st-message" role="status">{notice}</p>}{storageError && <p className="st-message st-warning">浏览器无法保存本地草稿与待核对标记，请保持页面打开并核对操作结果。</p>}
    {pending && !busy && <div className="st-message st-warning" role="alert"><strong>{pending.label}{pending.acknowledged ? '已收到成功回执，仍需核对后续状态。' : '结果尚需核对；服务端可能仍在处理。'}</strong><p>{pending.kind === 'save' ? '先刷新模板列表，确认是否已有新版本。' : '先回到项目重新读取镜头计划和商品资料。'}不会自动重试。</p><button type="button" disabled={pending.kind === 'save' && !checkedAfterPending} onClick={() => { if (window.confirm('请确认已经核对服务端结果。解除保护后不会自动重发；再次操作仍可能产生新版本或再次改变计划。')) { setPending(undefined); persist({ ...stored.current, pending: undefined }); } }}>已核对，解除本地保护</button></div>}
    <div className="st-columns"><section className="st-library" aria-label="查看和套用结构模板"><h3>查看已保存版本</h3>{!loadingList && !templates.length && !unavailable && <p className="st-empty">尚未读取到结构模板。可以在右侧手工整理结构并保存。</p>}
      <div className="st-select-row"><label>模板<select value={selectedId} disabled={!!busy || loadingTemplate} onChange={event => { setSelectedId(event.target.value); const item = templates.find(template => template.template_id === event.target.value); setVersion(item ? String(item.latest_version) : ''); setSelected(null); setApplyAcknowledged(false); }}><option value="">选择结构模板</option>{templates.map(template => <option key={template.template_id} value={template.template_id}>{template.name} · 最新 v{template.latest_version}</option>)}</select></label><label>指定版本<input value={version} inputMode="numeric" disabled={!!busy || loadingTemplate} onChange={event => { setVersion(event.target.value); setSelected(null); setApplyAcknowledged(false); }} placeholder="版本号" /></label></div>
      <button type="button" disabled={!selectedId || loadingTemplate || !!busy} onClick={() => void loadTemplate()}>{loadingTemplate ? <LoaderCircle size={15} className="st-spin" /> : <FileStack size={15} />}查看此版本</button>
      {selected && <div className="st-preview"><h4>{selected.name}<span>v{selected.version}</span></h4><ol>{selected.snapshot.map(shot => <li key={shot.seq}><span>{shot.seq}</span><div><strong>{shot.role}</strong><p>{shot.duration_s} 秒 · 运镜：{shot.motion || '未填写'} · 节奏：{shot.rhythm || '未填写'}</p></div></li>)}</ol><p className="st-version-note">套用绑定此版本；模板发布新版本后，当前项目不会自动跟随。</p><label className="st-checkbox"><input type="checkbox" checked={applyAcknowledged} disabled={blocked} onChange={event => setApplyAcknowledged(event.target.checked)} /><span>我已确认：套用将改变当前镜头计划；商品资料需要重填，素材需要重新绑定，模型与时长需要重新校验。</span></label><button type="button" className="st-primary" disabled={blocked || !applyAcknowledged} onClick={() => void applyTemplate()}><Check size={15} />套用此结构 · v{selected.version}</button></div>}
      {applyResult && <div className={`st-message${applyResult.model_revalidation.ok ? '' : ' st-warning'}`}><strong>{applyResult.model_revalidation.ok ? '服务端模型校验通过' : '服务端模型校验尚未通过'}</strong><p>仍需处理：{applyResult.requires.length ? applyResult.requires.join('、') : '请人工核对商品资料与素材绑定'}</p>{applyResult.model_revalidation.notes.map((note, index) => <p key={index}>{note}</p>)}</div>}
    </section><section className="st-compose" aria-label="保存新的结构模板"><div className="st-compose-heading"><h3>另存一份结构</h3>{snapshot && <button type="button" disabled={blocked || !canCopy} onClick={() => { if (!snapshot || !canCopy) return; if ((name.trim() || rows.some(row => Object.values(row).some(value => value.trim()))) && !window.confirm('用传入的镜头结构替换当前模板编辑内容？')) return; setRows(rowsFrom(snapshot)); setFactFree(false); }}><Copy size={14} />带入当前结构</button>}</div>{snapshot && !canCopy && <p className="st-message st-warning">传入内容不是完整的纯结构，未复制商品、提示词或素材字段。请在下方手工整理。</p>}
      <fieldset disabled={disabled || !!busy}><label>模板名称<input value={name} onChange={event => { setName(event.target.value); setFactFree(false); }} placeholder="如：细节开场 · 场景展开 · 收尾" /></label><div className="st-draft-rows">{rows.map((row, index) => <div className="st-draft-shot" key={index}><div className="st-row-title"><strong>镜头 {index + 1}</strong><button type="button" aria-label={`删除模板中的镜头 ${index + 1}`} onClick={() => { setRows(current => current.filter((_, i) => i !== index)); setFactFree(false); }}><Trash2 size={14} /></button></div><label>镜头职能<input value={row.role} onChange={event => changeRow(index, 'role', event.target.value)} placeholder="如：开场特写，不填商品名称或卖点" /></label><div className="st-three"><label>时长（秒）<input inputMode="numeric" value={row.duration_s} onChange={event => changeRow(index, 'duration_s', event.target.value)} placeholder="整数秒" /></label><label>运镜<input value={row.motion} onChange={event => changeRow(index, 'motion', event.target.value)} placeholder="如：缓慢推近" /></label><label>节奏<input value={row.rhythm} onChange={event => changeRow(index, 'rhythm', event.target.value)} placeholder="如：舒缓" /></label></div></div>)}</div><button type="button" onClick={() => { setRows(current => [...current, emptyRow()]); setFactFree(false); }}><Plus size={14} />添加镜头结构</button></fieldset>
      <label className="st-checkbox"><input type="checkbox" checked={factFree} disabled={blocked} onChange={event => setFactFree(event.target.checked)} /><span>我已检查以上仅包含镜头结构，不含商品名称、数量、卖点、实际画面描述或素材引用。</span></label>
      {!draft.input && <p className="st-validation">{draft.error}</p>}<button type="button" className="st-primary" disabled={blocked || !draft.input || !factFree} onClick={() => void saveTemplate()}><Save size={15} />保存结构模板</button>
    </section></div>{busy && <div className="st-busy" role="status"><LoaderCircle size={16} className="st-spin" /><span>{busy}…</span><button type="button" onClick={() => writeRequest.current?.abort()}>停止等待</button></div>}
  </section>;
}
