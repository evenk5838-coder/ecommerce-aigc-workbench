import { useEffect, useMemo, useState } from 'react';
import { ArrowRight, ArrowUpRight, Clapperboard, FolderOpen, Image, Layers3, LayoutGrid, LoaderCircle, MessageSquare, RefreshCw, Search, WandSparkles, WifiOff, Workflow } from 'lucide-react';
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
      <span className="ph-project-versions"><Layers3 size={12} aria-hidden="true" />{project.script_count ? '已有创作脚本' : '待起草脚本'}</span>
      <span className="ph-project-open" aria-hidden="true"><ArrowUpRight size={19} /></span>
    </div>
    <div className="ph-project-caption"><h3>{project.name}</h3><div className="ph-project-details">{platform && <span>{platform}</span>}{typeof duration === 'number' && Number.isFinite(duration) && duration > 0 && <span>计划 {duration} 秒</span>}</div><div className="ph-project-time">{date ? <time dateTime={date.toISOString()}>更新于 {dateFormat.format(date)}</time> : <span>更新时间未记录</span>}</div></div>
  </button>;
}
export default function ProjectHome({ onCreateProject, onOpenProject, onWorkbench, assets = [] }: ProjectHomeProps) {
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
    <header className="ph-heading"><div><span className="ph-eyebrow">YOUR CREATIVE SPACE</span><h1>让每一个想法，<br />都有一处开始的地方。</h1><p>目标明确时，快速生成素材；需要梳理想法时，与 AI 一起完成创作。</p></div><div className="ph-heading-note"><span>物料间</span><small>MAKE ROOM FOR IDEAS</small></div></header>
    <div className="ph-start-grid" aria-label="选择创作方式">
      <section className="ph-entry" aria-labelledby="ph-workbench-title">
        <div className="ph-entry-top"><span className="ph-kicker">01 / QUICK WORKBENCH</span><span className="ph-entry-icon" aria-hidden="true"><LayoutGrid size={27} strokeWidth={1.4} /></span></div>
        <h2 id="ph-workbench-title">快捷工作台</h2>
        <p className="ph-entry-lead">目标明确，直接开始。</p>
        <p className="ph-entry-description">快速完成单张图片或单条视频，<br />把具体想法变成可以使用的素材。</p>
        <ul className="ph-entry-features"><li><Image size={16} aria-hidden="true" /><span>生图与生视频，自由选择输入方式</span></li><li><Layers3 size={16} aria-hidden="true" /><span>用模板快速起步</span></li><li><WandSparkles size={16} aria-hidden="true" /><span>按需润色提示词，保留创作控制</span></li></ul>
        <button type="button" className="ph-primary" onClick={onWorkbench}>进入快捷工作台<ArrowUpRight size={18} /></button>
      </section>
      <section className="ph-entry" aria-labelledby="ph-director-title">
        <div className="ph-entry-top"><span className="ph-kicker">02 / AI DIRECTOR</span><span className="ph-entry-icon" aria-hidden="true"><Clapperboard size={29} strokeWidth={1.4} /></span></div>
        <h2 id="ph-director-title">AI 导演台</h2>
        <p className="ph-entry-lead">从一句需求，展开整个作品。</p>
        <p className="ph-entry-description">先用对话说清想法，<br />再逐步打磨脚本、分镜与视频。</p>
        <ul className="ph-entry-features"><li><MessageSquare size={16} aria-hidden="true" /><span>用对话梳理创作需求</span></li><li><Clapperboard size={16} aria-hidden="true" /><span>脚本 → 分镜 → 视频，逐步确认</span></li><li><Workflow size={16} aria-hidden="true" /><span>在自由画布中调整创作节点</span></li></ul>
        <button type="button" className="ph-primary" onClick={onCreateProject}>新建导演项目<ArrowUpRight size={18} /></button>
      </section>
    </div>
    <section className="ph-projects" aria-labelledby="ph-projects-title" aria-busy={loading}>
      <div className="ph-section-heading"><div><h2 id="ph-projects-title">最近的项目{projects.length > 0 && <span>{projects.length}</span>}</h2><p>回到上次的想法，继续往前一步。</p></div><div className="ph-project-tools"><label className="ph-search"><Search size={15} aria-hidden="true" /><input type="search" value={search} onChange={event => setSearch(event.target.value)} placeholder="搜索项目或商品" aria-label="搜索项目或商品" /></label>{refresh}</div></div>
      {error && <div className="ph-error" role="alert"><WifiOff size={19} /><div><strong>{projects.length ? '项目列表暂未更新' : '暂时无法读取项目'}</strong><p>{error}</p><small>{projects.length ? '下方保留上次成功加载的项目。' : '这不代表已有项目被删除。请重新加载后再查看。'}</small></div></div>}
      {loading && !projects.length ? <div className="ph-state" role="status"><LoaderCircle size={29} className="ph-spin" /><h3>正在读取你的项目</h3><p>项目加载后会显示在这里。</p></div> : projects.length > 0 ? filtered.length > 0 ? <div className="ph-project-grid">{filtered.map(project => <ProjectCard key={project.project_id + ':' + (images.get(project.project_id)?.id ?? '')} project={project} image={images.get(project.project_id)} onOpen={() => onOpenProject(project.project_id)} />)}</div> : <div className="ph-state"><Search size={28} /><h3>没有找到匹配的项目</h3><p>换一个名称，或清除搜索查看已有项目。</p><button type="button" className="ph-secondary" onClick={() => setSearch('')}>清除搜索</button></div> : !error && <div className="ph-state ph-empty"><FolderOpen size={32} strokeWidth={1.4} /><h3>把第一个想法，留在这里。</h3><p>新建项目，从商品需求开始。<br />如果只想快速生成素材，可以直接进入快捷工作台。</p><button type="button" className="ph-primary" onClick={onCreateProject}>新建第一个项目<ArrowRight size={16} /></button></div>}
      {loading && projects.length > 0 && <p className="ph-refreshing" role="status"><LoaderCircle size={13} className="ph-spin" />正在更新项目列表</p>}
      <footer className="ph-footer"><span>项目保留创作过程；快捷工作台处理单次创作。</span>{projects.length >= 60 && <span>当前显示最近 60 个项目</span>}</footer>
    </section>
  </div>;
}
