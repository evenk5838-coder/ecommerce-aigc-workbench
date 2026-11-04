import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowUpRight, FolderOpen, LoaderCircle, RefreshCw, Search, Trash2, X } from 'lucide-react';
import { ApiError, mediaUrl } from './api';
import { creativeApi } from './creative/api';
import type { CreativeProjectListItem } from './creative/types';
import type { Asset } from './types';
import './ProjectLibrary.css';

export const projectText = (value: unknown): string => typeof value === 'string' ? value.trim() : '';
const message = (error: unknown) => error instanceof Error ? error.message : '请求未完成，请稍后重试。';
const deletionKey = (id: string) => `material-studio-project-deletion-pending-v1:${encodeURIComponent(id)}`;
function deletionPending(id: string): boolean { try { return localStorage.getItem(deletionKey(id)) === 'pending'; } catch { return false; } }
function saveDeletionPending(id: string, pending: boolean) { try { if (pending) localStorage.setItem(deletionKey(id), 'pending'); else localStorage.removeItem(deletionKey(id)); } catch { /* The visible dialog keeps the same guard while open. */ } }
function dateLabel(value?: number | null): string {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return '更新时间未记录';
  const date = new Date(value * 1000);
  return Number.isNaN(date.getTime()) ? '更新时间未记录' : new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(date);
}
/** Covers require an explicit project association. A nearby asset is never used as another project's cover. */
export function projectImages(assets: Asset[]): Map<string, Asset> {
  const result = new Map<string, Asset>();
  for (const asset of [...assets].sort((a, b) => b.created_at - a.created_at)) {
    if (asset.kind !== 'image') continue;
    try {
      const meta: unknown = JSON.parse(asset.meta_json || '{}');
      if (meta && typeof meta === 'object' && !Array.isArray(meta)) {
        const id = projectText((meta as Record<string, unknown>).project_id);
        if (id && !result.has(id)) result.set(id, asset);
      }
    } catch { /* A missing cover does not make a project unavailable. */ }
  }
  return result;
}

export function useProjectList() {
  const [projects, setProjects] = useState<CreativeProjectListItem[]>([]);
  const [loading, setLoading] = useState(true), [error, setError] = useState(''), [notice, setNotice] = useState('');
  const controller = useRef<AbortController | null>(null);
  const reload = useCallback(async () => {
    controller.current?.abort();
    const current = new AbortController(); controller.current = current;
    setLoading(true); setError('');
    try {
      const result = await creativeApi.listProjects(100, { signal: current.signal });
      if (current.signal.aborted) return false;
      setProjects(result.items.slice().sort((a, b) => (b.updated_at ?? b.created_at ?? 0) - (a.updated_at ?? a.created_at ?? 0)));
      return true;
    } catch (caught) { if (!current.signal.aborted) setError(message(caught)); return false; }
    finally { if (!current.signal.aborted) setLoading(false); }
  }, []);
  useEffect(() => { void reload(); return () => controller.current?.abort(); }, [reload]);
  const removed = (project: CreativeProjectListItem) => {
    controller.current?.abort();
    setProjects(current => current.filter(item => item.project_id !== project.project_id));
    setNotice(`“${project.name}”已删除，原有素材仍保留。`);
    void reload();
  };
  return { projects, loading, error, notice, reload, removed };
}

function ProjectCard({ project, image, onOpen, onDelete, disabled }: { project: CreativeProjectListItem; image?: Asset; onOpen: () => void; onDelete: () => void; disabled: boolean }) {
  const [failedImage, setFailedImage] = useState<string | null>(null);
  const subject = projectText(project.brief.subject), platform = projectText(project.brief.target_platform);
  return <article className="pl-card">
    <button type="button" className="pl-card-open" disabled={disabled} onClick={onOpen} aria-label={`打开项目：${project.name}`}>
      <div className="pl-cover">
        {image && failedImage !== image.id ? <img src={mediaUrl(image.id)} alt="" loading="lazy" onError={() => setFailedImage(image.id)} /> : <div className="pl-cover-empty"><FolderOpen size={35} strokeWidth={1.1} /><span>{subject || project.name}</span><small>项目封面待添加</small></div>}
        <span className="pl-cover-label">{project.script_count > 0 ? `${project.script_count} 个脚本版本` : '从创作需求开始'}</span>
      </div>
      <div className="pl-caption"><div><h3>{project.name}</h3><p>{platform || subject || '创作项目'}</p></div><ArrowUpRight size={20} aria-hidden="true" /></div>
    </button>
    <div className="pl-card-foot"><span>更新于 {dateLabel(project.updated_at ?? project.created_at)}</span><button type="button" className="pl-delete" onClick={onDelete} disabled={disabled} aria-label={`删除项目：${project.name}`} title="删除项目"><Trash2 size={14} /><span>删除</span></button></div>
  </article>;
}

function ProjectDeleteDialog({ project, onClose, onDeleted, onPendingChange }: { project: CreativeProjectListItem; onClose: () => void; onDeleted: () => void; onPendingChange?: (pending: boolean) => void }) {
  const dialog = useRef<HTMLDialogElement>(null), lock = useRef(false), mounted = useRef(true);
  const [busy, setBusy] = useState(false), [unknown, setUnknown] = useState(() => deletionPending(project.project_id));
  const [error, setError] = useState(''), [checked, setChecked] = useState(false);
  const pendingCallback = useRef(onPendingChange); pendingCallback.current = onPendingChange;
  useEffect(() => {
    mounted.current = true;
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null, element = dialog.current;
    element?.showModal();
    return () => { mounted.current = false; element?.close(); previous?.focus({ preventScroll: true }); pendingCallback.current?.(false); };
  }, []);
  useEffect(() => { pendingCallback.current?.(busy); }, [busy]);
  const finish = () => { saveDeletionPending(project.project_id, false); onDeleted(); };
  const remove = async () => {
    if (lock.current || (unknown && !checked)) return;
    lock.current = true; setBusy(true); setError(''); setChecked(false); saveDeletionPending(project.project_id, true);
    try {
      await creativeApi.deleteProject(project.project_id);
      if (mounted.current) finish();
    } catch (caught) {
      if (!mounted.current) return;
      if (caught instanceof ApiError && caught.code === 'PROJECT_DELETED') { finish(); return; }
      const uncertain = caught instanceof ApiError && caught.code === 'CREATIVE_RESULT_UNKNOWN';
      setUnknown(uncertain); saveDeletionPending(project.project_id, uncertain);
      setError(caught instanceof ApiError && caught.code === 'PROJECT_BUSY' ? '这个项目还有正在处理的任务，暂时不能删除。请等待任务结束后再试。' : message(caught));
    } finally { lock.current = false; if (mounted.current) setBusy(false); }
  };
  const check = async () => {
    if (lock.current) return;
    lock.current = true; setBusy(true); setError('');
    try { await creativeApi.getProject(project.project_id); if (mounted.current) { setChecked(true); setError('项目仍可读取。可以稍后再次核对，或明确再次请求删除。'); } }
    catch (caught) { if (!mounted.current) return; if (caught instanceof ApiError && caught.code === 'PROJECT_DELETED') finish(); else setError(message(caught)); }
    finally { lock.current = false; if (mounted.current) setBusy(false); }
  };
  return <dialog ref={dialog} className="pl-delete-dialog" aria-labelledby="pl-delete-title" aria-describedby="pl-delete-description" onCancel={event => { event.preventDefault(); if (!busy) onClose(); }}>
    <div className="pl-dialog-top"><span>删除项目</span><button type="button" aria-label="关闭删除确认" disabled={busy} onClick={onClose}><X size={19} /></button></div>
    <Trash2 className="pl-dialog-symbol" size={27} strokeWidth={1.4} />
    <h2 id="pl-delete-title">删除“{project.name}”？</h2><p id="pl-delete-description">删除后，这个项目将不再出现在项目列表中。已上传和已生成的素材会保留。</p>
    {unknown && <p className="pl-warning" role="status">上次删除结果尚未确认，请先重新核对。不会自动重复请求。</p>}
    {error && <p className="pl-error" role="alert">{error}</p>}
    <div className="pl-dialog-actions"><button type="button" className="pl-secondary" autoFocus disabled={busy} onClick={onClose}>{unknown ? '关闭' : '保留项目'}</button>{unknown && <button type="button" className="pl-secondary" disabled={busy} onClick={() => void check()}>重新核对</button>}<button type="button" className="pl-danger" disabled={busy || (unknown && !checked)} onClick={() => void remove()}>{busy ? <LoaderCircle size={15} className="pl-spin" /> : <Trash2 size={15} />}{unknown ? '再次请求删除' : '确认删除项目'}</button></div>
  </dialog>;
}

export function ProjectLibrary({ list, assets = [], onOpenProject, onCreateProject, onPendingChange, disabled = false, title = '最近的项目' }: {
  list: ReturnType<typeof useProjectList>; assets?: Asset[]; onOpenProject: (id: string) => void; onCreateProject: () => void;
  onPendingChange?: (pending: boolean) => void; disabled?: boolean; title?: string;
}) {
  const [search, setSearch] = useState(''), [deleting, setDeleting] = useState<CreativeProjectListItem | null>(null);
  const images = useMemo(() => projectImages(assets), [assets]);
  const filtered = list.projects.filter(project => `${project.name} ${projectText(project.brief.subject)}`.toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
  return <section className="project-library" aria-label={title} aria-busy={list.loading}>
    <div className="pl-heading"><div><h2>{title}<small>RECENT PROJECTS</small></h2></div><div className="pl-tools"><label className="pl-search"><Search size={15} /><input type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder="搜索项目或商品" aria-label="搜索项目或商品" /></label><button type="button" className="pl-refresh" aria-label="刷新项目" title="刷新项目" onClick={() => void list.reload()} disabled={list.loading}><RefreshCw size={15} className={list.loading ? 'pl-spin' : ''} /><span>刷新</span></button></div></div>
    {list.error && <div className="pl-notice pl-error" role="alert"><strong>{list.projects.length ? '列表未能更新，已保留上次结果' : '暂时无法读取项目'}</strong><p>{list.error}</p></div>}
    {list.notice && <p className="pl-notice" role="status">{list.notice}</p>}
    {list.loading && !list.projects.length ? <div className="pl-empty" role="status"><LoaderCircle size={26} className="pl-spin" /><h3>正在读取项目</h3></div> : filtered.length ? <div className="pl-grid">{filtered.map(project => <ProjectCard key={project.project_id} project={project} image={images.get(project.project_id)} disabled={disabled} onOpen={() => onOpenProject(project.project_id)} onDelete={() => setDeleting(project)} />)}</div> : !list.error && <div className="pl-empty">{search ? <Search size={30} strokeWidth={1.3} /> : <FolderOpen size={32} strokeWidth={1.3} />}<h3>{search ? '没有找到匹配的项目' : '第一个作品，从这里开始。'}</h3><p>{search ? '换个关键词，或清除搜索查看所有项目。' : '为这次创作取个名字，再进入画布。'}</p><button type="button" className="pl-secondary" disabled={disabled} onClick={() => search ? setSearch('') : onCreateProject()}>{search ? '清除搜索' : '新建项目'}</button></div>}
    <footer className="pl-footer"><span>把想法留在项目里，随时回来继续。</span><span>{list.loading ? '正在更新' : list.error ? '列表待更新' : `${list.projects.length} 个项目${list.projects.length >= 100 ? ' · 最近 100 个' : ''}`}</span></footer>
    {deleting && <ProjectDeleteDialog key={deleting.project_id} project={deleting} onClose={() => setDeleting(null)} onPendingChange={onPendingChange} onDeleted={() => { list.removed(deleting); setDeleting(null); }} />}
  </section>;
}
