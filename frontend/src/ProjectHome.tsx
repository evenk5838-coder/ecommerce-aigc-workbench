import { useMemo, useState } from 'react';
import { ArrowUpRight, Clapperboard, Layers3 } from 'lucide-react';
import { mediaUrl } from './api';
import type { Asset } from './types';
import { ProjectLibrary, useProjectList } from './ProjectLibrary';
import './ProjectHome.css';

export type ProjectHomeGenerationMode = 'text_to_image' | 'image_to_image' | 'text_to_video' | 'image_to_video';
export interface ProjectHomeProps {
  onCreateProject: () => void;
  onOpenProject: (id: string) => void;
  onStartGeneration: (mode: ProjectHomeGenerationMode) => void;
  onWorkbench: () => void;
  assets?: Asset[];
}

export default function ProjectHome({ onCreateProject, onOpenProject, onWorkbench, assets = [] }: ProjectHomeProps) {
  const list = useProjectList();
  const [failedImage, setFailedImage] = useState<string | null>(null);
  const hero = useMemo(() => [...assets].filter(asset => asset.kind === 'image').sort((a, b) => b.created_at - a.created_at)[0], [assets]);
  return <div className="project-home">
    <header className="ph-heading"><div><span className="ph-eyebrow">YOUR CREATIVE SPACE</span><h1>每个好作品，<br />从一个清晰的想法开始。</h1></div><div className="ph-count"><span>A PLACE FOR YOUR NEXT IDEA</span><strong>{list.loading || list.error ? '—' : String(list.projects.length).padStart(2, '0')}</strong><small>PROJECTS / 创作项目</small></div></header>
    <div className="ph-launch">
      <section className="ph-hero" aria-labelledby="ph-create-title"><div className="ph-hero-copy"><span className="ph-kicker">01 / START A PROJECT</span><h2 id="ph-create-title">新建项目<br />一步步，做到成片。</h2><p>从商品需求出发，确认脚本、审核分镜，<br />再生成你想要的版本。</p><button type="button" className="pl-primary" onClick={onCreateProject}>新建创作项目<ArrowUpRight size={19} /></button></div><div className="ph-hero-image">{hero && hero.id !== failedImage ? <img src={mediaUrl(hero.id)} alt="最近的图片素材预览" onError={() => setFailedImage(hero.id)} /> : <div className="ph-hero-placeholder"><Layers3 size={61} strokeWidth={.8} /><strong>一处空间，<br />收好每一次创作。</strong></div>}<div className="ph-image-tag"><span>{hero && hero.id !== failedImage ? 'RECENT MATERIAL / 素材预览' : 'YOUR NEXT PROJECT'}</span><Clapperboard size={13} /></div></div></section>
      <section className="ph-quick" aria-labelledby="ph-quick-title"><div className="ph-quick-top"><span className="ph-quick-sign"><ArrowUpRight size={30} strokeWidth={1.4} /></span><span className="ph-kicker">02 / QUICK CREATE</span></div><h2 id="ph-quick-title">灵感来了，直接做。</h2><p>只需要一张商品图，或一段短视频？<br />在快捷工作台轻装开始。</p><button type="button" className="ph-text-button" onClick={onWorkbench}>打开快捷工作台<ArrowUpRight size={19} /></button></section>
    </div>
    <ProjectLibrary list={list} assets={assets} onOpenProject={onOpenProject} onCreateProject={onCreateProject} />
  </div>;
}
