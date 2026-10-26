import { useEffect, useRef, useState } from 'react';
import { ArrowRight, Clapperboard, LoaderCircle, Plus, X } from 'lucide-react';
import { ApiError } from './api';
import { creativeApi } from './creative/api';
import { ProjectLibrary, useProjectList } from './ProjectLibrary';
import type { Asset } from './types';
import './DirectorHome.css';

export interface DirectorHomeProps {
  onOpenProject: (id: string) => void;
  assets?: Asset[];
  createRequest?: number;
  onDirtyChange?: (dirty: boolean) => void;
}
// Keep the existing new-project draft; entering the list does not discard it or automatically open it.
const CREATE_KEY = 'material-studio-creative-create-v1';
function readDraft(): { name: string; subject: string; uncertain: boolean } {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(CREATE_KEY) || '{}');
    if (value && typeof value === 'object' && !Array.isArray(value)) {
      const item = value as Record<string, unknown>;
      return { name: typeof item.name === 'string' ? item.name : '', subject: typeof item.subject === 'string' ? item.subject : '', uncertain: item.uncertain === true };
    }
  } catch { /* Keep creation available when browser storage is unavailable. */ }
  return { name: '', subject: '', uncertain: false };
}
export default function DirectorHome({ onOpenProject, assets = [], createRequest = 0, onDirtyChange }: DirectorHomeProps) {
  const list = useProjectList();
  const [initial] = useState(readDraft);
  const [name, setName] = useState(initial.name), [subject, setSubject] = useState(initial.subject);
  const [unknown, setUnknown] = useState(initial.uncertain), [showCreate, setShowCreate] = useState(createRequest > 0);
  const [busy, setBusy] = useState(false), [error, setError] = useState(''), [storageError, setStorageError] = useState(false);
  const [deletePending, setDeletePending] = useState(false), [checkedList, setCheckedList] = useState(false);
  const mounted = useRef(true), lock = useRef(false), seenRequest = useRef(createRequest), nameInput = useRef<HTMLInputElement>(null);
  const callbacks = useRef({ onOpenProject, onDirtyChange }); callbacks.current = { onOpenProject, onDirtyChange };
  const dirty = busy || deletePending || unknown || ((showCreate || storageError) && (!!name.trim() || !!subject.trim()));
  useEffect(() => { if (createRequest > 0 && createRequest !== seenRequest.current) setShowCreate(true); seenRequest.current = createRequest; }, [createRequest]);
  useEffect(() => { if (showCreate) nameInput.current?.focus({ preventScroll: true }); }, [showCreate]);
  useEffect(() => { try { localStorage.setItem(CREATE_KEY, JSON.stringify({ name, subject, uncertain: unknown })); setStorageError(false); } catch { setStorageError(true); } }, [name, subject, unknown]);
  useEffect(() => { callbacks.current.onDirtyChange?.(dirty); }, [dirty]);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; callbacks.current.onDirtyChange?.(false); }; }, []);
  useEffect(() => { const warn = (event: BeforeUnloadEvent) => { if (dirty) { event.preventDefault(); event.returnValue = ''; } }; window.addEventListener('beforeunload', warn); return () => window.removeEventListener('beforeunload', warn); }, [dirty]);
  const create = async () => {
    if (lock.current || unknown || !name.trim()) return;
    lock.current = true; setBusy(true); setError(''); setUnknown(true); setCheckedList(false);
    try { localStorage.setItem(CREATE_KEY, JSON.stringify({ name, subject, uncertain: true })); } catch { setStorageError(true); }
    try {
      const result = await creativeApi.createProject(name.trim(), subject.trim() ? { subject: subject.trim() } : {});
      if (!mounted.current) return;
      setName(''); setSubject(''); setUnknown(false); setShowCreate(false);
      try { localStorage.setItem(CREATE_KEY, JSON.stringify({ name: '', subject: '', uncertain: false })); } catch { /* The current state is already cleared. */ }
      callbacks.current.onDirtyChange?.(false);
      callbacks.current.onOpenProject(result.project_id);
    } catch (caught) {
      if (!mounted.current) return;
      setUnknown(caught instanceof ApiError && caught.code === 'CREATIVE_RESULT_UNKNOWN');
      setError(caught instanceof Error ? caught.message : '创建未完成，请稍后重试。');
    } finally { lock.current = false; if (mounted.current) setBusy(false); }
  };
  const open = (id: string) => {
    if (busy || deletePending) return;
    if ((showCreate || storageError) && (name.trim() || subject.trim()) && !window.confirm(storageError ? '填写内容暂时无法保存，离开后可能丢失。仍要打开所选项目吗？' : '新建项目的填写内容会保留在此浏览器。现在打开所选项目吗？')) return;
    callbacks.current.onDirtyChange?.(false); callbacks.current.onOpenProject(id);
  };
  return <div className="director-home"><div className="dh-page">
    <header className="dh-heading"><div><span className="dh-eyebrow">DIRECTOR / PROJECT STUDIO</span><h1>你的导演台。</h1><p>选一个项目，回到创作；或从新的想法开始。</p></div><button type="button" className="pl-primary" disabled={busy || deletePending} onClick={() => setShowCreate(true)}><Plus size={17} />新建项目</button></header>
    {showCreate ? <section className="dh-create" aria-labelledby="dh-create-title"><div className="dh-create-intro"><span className="dh-eyebrow">A NEW BEGINNING</span><h2 id="dh-create-title">为这次创作，<br />取一个名字。</h2><p>创建后进入自由画布，<br />继续补充商品需求、脚本和分镜。</p></div><form className="dh-create-form" onSubmit={event => { event.preventDefault(); void create(); }}><button className="dh-close" type="button" aria-label="收起新建项目表单" disabled={busy} onClick={() => setShowCreate(false)}><X size={18} /></button><label>项目名称<input ref={nameInput} value={name} disabled={busy || unknown} maxLength={120} onChange={event => setName(event.target.value)} placeholder="例如：夏季新品 · 商品短片" autoComplete="off" /></label><label>商品名称<span>选填，之后可以补充</span><input value={subject} disabled={busy || unknown} maxLength={160} onChange={event => setSubject(event.target.value)} placeholder="这次要拍什么商品？" autoComplete="off" /></label><button className="pl-primary" type="submit" disabled={busy || unknown || !name.trim()}>{busy ? <LoaderCircle size={16} className="pl-spin" /> : <ArrowRight size={17} />}创建并进入画布</button></form></section> : <section className="dh-start"><div className="dh-start-icon"><Clapperboard size={32} strokeWidth={1.3} /></div><div><h2>每一次创作，都有自己的空间。</h2><p>需求、脚本、画面与视频，都留在同一个项目里。</p></div><button className="dh-start-link" type="button" disabled={busy || deletePending} onClick={() => setShowCreate(true)}>{name || subject ? '继续填写新建项目' : '创建新项目'}<ArrowRight size={17} /></button></section>}
    {storageError && <p className="pl-notice pl-warning" role="alert">浏览器暂时无法保存填写内容，请保持页面开启。</p>}
    {error && <p className="pl-notice pl-error" role="alert">{error}</p>}
    {unknown && !busy && <div className="pl-notice pl-warning" role="alert"><strong>新建结果尚未确认</strong><p>请先刷新下方列表，查看是否已经出现新项目。不会自动重复创建。</p><div className="dh-recovery"><button type="button" className="pl-secondary" disabled={list.loading} onClick={async () => { const loaded = await list.reload(); if (mounted.current) setCheckedList(loaded); }}>刷新项目列表</button><button type="button" className="pl-secondary" disabled={!checkedList || list.loading || !!list.error} onClick={() => { if (window.confirm('已核对列表且未找到刚才创建的项目？继续后可主动重新创建，原请求仍可能完成并产生重复项目。')) { setUnknown(false); setError(''); setShowCreate(true); } }}>已核对，继续填写</button></div></div>}
    <ProjectLibrary list={list} assets={assets} onOpenProject={open} onCreateProject={() => setShowCreate(true)} onPendingChange={setDeletePending} disabled={busy || deletePending} title="我的项目" />
  </div></div>;
}
