import { useEffect, useMemo, useState } from 'react';
import { ArrowRight, ArrowUpRight, Clapperboard, FileImage, FileText, FolderOpen, ImagePlus, Layers3, LoaderCircle, Plus, RefreshCw, Search, Video, WifiOff } from 'lucide-react';
import { mediaUrl } from './api';
import { creativeApi } from './creative/api';
import type { CreativeProjectListItem } from './creative/types';
import type { Asset } from './types';
import './ProjectHome.css';

export type ProjectHomeGenerationMode = 'text_to_image' | 'image_to_image' | 'text_to_video' | 'image_to_video';
export interface ProjectHomeProps {
  onCreateProject: () => void;
  onOpenProject: (id: string) => void;
  onStartGeneration: (mode: ProjectHomeGenerationMode) => void;
  onWorkbench: () => void;
  assets?: Asset[];
}
const modes = [
  { mode: 'text_to_image', title: '文生图', description: '用一段描述，让画面成形', icon: FileImage, tag: '文字 → 图片' },
  { mode: 'image_to_image', title: '图生图', description: '从参考图继续调整与创作', icon: ImagePlus, tag: '图片 → 图片' },
  { mode: 'text_to_video', title: '文生视频', description: '描述画面、动作与镜头', icon: FileText, tag: '文字 → 视频' },
  { mode: 'image_to_video', title: '图生视频', description: '以一张图片作为视频首帧', icon: Video, tag: '图片 → 视频' },
] as const;
const dateFormat = new Intl.DateTimeFormat('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
function projectDate(value?: number | null): Date | null {
  if (typeof value !== 'number' || !Number.isFinite(value) || value <= 0) return null;
  const date = new Date(value * 1000);
  return Number.isNaN(date.getTime()) ? null : date;
}
function briefText(value: unknown): string { return typeof value === 'string' ? value.trim() : ''; }
/** Images are associated only by explicit metadata, never by position in the asset list. */
function projectImages(assets: Asset[]): Map<string, Asset> {
  const result = new Map<string, Asset>();
  for (const asset of [...assets].sort((a, b) => b.created_at - a.created_at)) {
    if (asset.kind !== 'image') continue;
    try {
      const meta: unknown = JSON.parse(asset.meta_json || '{}');
      if (meta && typeof meta === 'object' && !Array.isArray(meta)) {
        const projectId = (meta as Record<string, unknown>).project_id;
        if (typeof projectId === 'string' && projectId.trim() && !result.has(projectId)) result.set(projectId, asset);
      }
    } catch { /* A missing image association does not make the project unavailable. */ }
  }
  return result;
}
function ProjectCard({ project, image, onOpen }: { project: CreativeProjectListItem; image?: Asset; onOpen: () => void }) {
  const [imageFailed, setImageFailed] = useState(false);
  const date = projectDate(project.updated_at) ?? projectDate(project.created_at);
  const subject = briefText(project.brief.subject);
  const platform = briefText(project.brief.target_platform);
  const duration = project.brief.target_duration_s;
  return <button type="button" className="ph-project" onClick={onOpen} aria-label={`打开项目：${project.name}`}>
    <div className={'ph-project-cover' + (image && !imageFailed ? ' ph-has-image' : '')}>
      {image && !imageFailed ? <img src={mediaUrl(image.id)} alt="" loading="lazy" onError={() => setImageFailed(true)} /> : <div className="ph-project-identity"><FolderOpen size={26} strokeWidth={1.4} aria-hidden="true" /><span>{subject || project.name}</span><small>PROJECT / 创作项目</small></div>}
      <span className="ph-project-versions"><Layers3 size={12} aria-hidden="true" />{project.script_count ? `${project.script_count} 个脚本版本` : '尚无脚本版本'}</span>
      <span className="ph-project-open" aria-hidden="true"><ArrowUpRight size={19} /></span>
    </div>
    <div className="ph-project-caption"><h3>{project.name}</h3><div className="ph-project-details">{platform && <span>{platform}</span>}{typeof duration === 'number' && Number.isFinite(duration) && duration > 0 && <span>计划 {duration} 秒</span>}</div><div className="ph-project-time">{date ? <time dateTime={date.toISOString()}>更新于 {dateFormat.format(date)}</time> : <span>更新时间未记录</span>}</div></div>
  </button>;
}
export default function ProjectHome({ onCreateProject, onOpenProject, onStartGeneration, onWorkbench, assets = [] }: ProjectHomeProps) {
  const [projects, setProjects] = useState<CreativeProjectListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [requestVersion, setRequestVersion] = useState(0);
  const [search, setSearch] = useState('');
  const images = useMemo(() => projectImages(assets), [assets]);
  const filtered = projects.filter(project => (project.name + ' ' + briefText(project.brief.subject)).toLocaleLowerCase().includes(search.trim().toLocaleLowerCase()));
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError('');
    creativeApi.listProjects(60, { signal: controller.signal }).then(result => {
      if (!controller.signal.aborted) setProjects([...result.items].sort((a, b) => (b.updated_at ?? b.created_at ?? 0) - (a.updated_at ?? a.created_at ?? 0)));
    }).catch(caught => {
      if (!controller.signal.aborted) setError(caught instanceof Error ? caught.message : '暂时无法读取项目，请重新加载。');
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [requestVersion]);
  const refresh = <button type="button" className="ph-refresh" onClick={() => setRequestVersion(value => value + 1)} disabled={loading}><RefreshCw size={15} className={loading ? 'ph-spin' : ''} />重新加载</button>;
  return <div className="project-home">
    <header className="ph-heading"><div><span className="ph-eyebrow">YOUR CREATIVE SPACE</span><h1>让每一个想法，<br />都有一处开始的地方。</h1><p>直接创作一张图、一段视频，或用项目把想法一步步做成作品。</p></div><div className="ph-heading-note"><span>物料间</span><small>MAKE ROOM FOR IDEAS</small></div></header>
    <div className="ph-start-grid">
      <section className="ph-project-launch" aria-labelledby="ph-launch-title"><div className="ph-launch-copy"><span className="ph-kicker">PROJECT / 项目创作</span><h2 id="ph-launch-title">从需求出发，<br />把过程留在项目里。</h2><p>确认需求、编辑脚本与镜头，<br />再一步步审核画面、生成视频。</p><button type="button" className="ph-primary" onClick={onCreateProject}><Plus size={17} />新建创作项目<ArrowUpRight size={18} /></button></div><div className="ph-launch-art" aria-hidden="true"><div className="ph-art-sheet ph-art-back" /><div className="ph-art-sheet ph-art-front"><span>物料间 / PROJECT</span><div className="ph-art-frame"><Clapperboard size={45} strokeWidth={1.2} /></div><i /><i /><small>让好的想法，有迹可循。</small></div><span className="ph-art-index">01 →</span></div></section>
      <section className="ph-quick" aria-labelledby="ph-quick-title"><div className="ph-quick-heading"><div><span className="ph-kicker">QUICK CREATE</span><h2 id="ph-quick-title">灵感来了，直接做。</h2></div><button type="button" onClick={onWorkbench} className="ph-workbench-link" aria-label="打开快捷工作台"><ArrowUpRight size={19} /></button></div><div className="ph-mode-grid">{modes.map(({ mode, title, description, icon: Icon, tag }) => <button key={mode} type="button" className="ph-mode" onClick={() => onStartGeneration(mode)}><span className="ph-mode-top"><Icon size={20} strokeWidth={1.5} aria-hidden="true" /><small>{tag}</small><ArrowUpRight size={14} className="ph-mode-arrow" aria-hidden="true" /></span><strong>{title}</strong><span>{description}</span></button>)}</div></section>
    </div>
    <section className="ph-projects" aria-labelledby="ph-projects-title" aria-busy={loading}>
      <div className="ph-section-heading"><div><h2 id="ph-projects-title">最近的项目{projects.length > 0 && <span>{projects.length}</span>}</h2><p>回到上次的想法，继续往前一步。</p></div><div className="ph-project-tools"><label className="ph-search"><Search size={15} aria-hidden="true" /><input type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder="搜索项目或商品" aria-label="搜索项目或商品" /></label>{refresh}</div></div>
      {error && <div className="ph-error" role="alert"><WifiOff size={19} /><div><strong>{projects.length ? '项目列表暂未更新' : '暂时无法读取项目'}</strong><p>{error}</p><small>{projects.length ? '下方保留上次成功加载的项目。' : '这不代表已有项目被删除。请重新加载后再查看。'}</small></div></div>}
      {loading && !projects.length ? <div className="ph-state" role="status"><LoaderCircle size={29} className="ph-spin" /><h3>正在读取你的项目</h3><p>项目加载后会显示在这里。</p></div> : projects.length > 0 ? filtered.length > 0 ? <div className="ph-project-grid">{filtered.map(project => <ProjectCard key={project.project_id + ':' + (images.get(project.project_id)?.id ?? '')} project={project} image={images.get(project.project_id)} onOpen={() => onOpenProject(project.project_id)} />)}</div> : <div className="ph-state"><Search size={28} /><h3>没有找到匹配的项目</h3><p>换一个名称，或清除搜索查看已有项目。</p><button type="button" className="ph-secondary" onClick={() => setSearch('')}>清除搜索</button></div> : !error && <div className="ph-state ph-empty"><FolderOpen size={32} strokeWidth={1.4} /><h3>把第一个想法，留在这里。</h3><p>新建项目，从商品需求开始。<br />如果只想快速生成，也可以使用上方四种创作方式。</p><button type="button" className="ph-primary" onClick={onCreateProject}>新建第一个项目<ArrowRight size={16} /></button></div>}
      {loading && projects.length > 0 && <p className="ph-refreshing" role="status"><LoaderCircle size={13} className="ph-spin" />正在更新项目列表</p>}
      <footer className="ph-footer"><span>项目保留创作过程；快捷工作台处理单次创作。</span>{projects.length >= 60 && <span>当前显示最近 60 个项目</span>}</footer>
    </section>
  </div>;
}
