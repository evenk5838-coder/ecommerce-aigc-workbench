import { useState } from 'react';
import { ArrowRight, Clapperboard, Image as ImageIcon } from 'lucide-react';
import type { Asset, Task } from './types';
import { mediaUrl } from './api';
import { homeCovers } from './home-covers';
import { ProjectLibrary, useProjectList } from './ProjectLibrary';
import directorScene from './assets/home/director-scene.png';
import quickScene from './assets/home/quick-scene.png';
import './ProjectHome.css';

export type ProjectHomeGenerationMode = 'text_to_image' | 'image_to_image' | 'text_to_video' | 'image_to_video';
export interface ProjectHomeProps {
  onCreateProject: () => void;
  onOpenProject: (id: string) => void;
  onStartGeneration: (mode: ProjectHomeGenerationMode) => void;
  onWorkbench: () => void;
  assets?: Asset[];
  tasks?: Task[];
}

function EntryCover({ asset, fallback, title }: { asset?: Asset; fallback: string; title: string }) {
  const [failedImage, setFailedImage] = useState<string | null>(null);
  const generated = asset && failedImage !== asset.id;
  return <div className={`ph-entry-image${generated ? ' ph-entry-image-generated' : ''}`}>
    <img src={generated ? mediaUrl(asset.id) : fallback} alt={generated ? `${title}最近生成的图片` : ''}
      width={1024} height={1024} decoding="async" draggable={false}
      onError={generated ? () => setFailedImage(asset.id) : undefined} />
  </div>;
}

export default function ProjectHome({ onCreateProject, onOpenProject, onWorkbench, assets = [], tasks = [] }: ProjectHomeProps) {
  const list = useProjectList();
  const covers = homeCovers(assets, tasks, list.projects.map(project => project.project_id));
  const entries = [
    { id: 'director', title: 'AI 导演台', Icon: Clapperboard, lines: ['从一句想法，到完整成片。', '对话推进，自由画布创作。'], action: '新建创作项目', onClick: onCreateProject, image: directorScene, cover: covers.director },
    { id: 'quick', title: '快捷工作台', Icon: ImageIcon, lines: ['一张图片，或一段视频。', '选择模型，直接开始创作。'], action: '进入快捷工作台', onClick: onWorkbench, image: quickScene, cover: covers.quick },
  ];
  return <div className="project-home">
    <header className="ph-heading"><h1>每个好作品，<br />从一个清晰的想法开始。</h1></header>
    <div className="ph-launch">
      {entries.map(({ id, title, Icon, lines, action, onClick, image, cover }) => <section key={id} className="ph-entry" aria-labelledby={`ph-${id}-title`}>
        <div className="ph-entry-copy">
          <Icon className="ph-entry-icon" size={34} strokeWidth={1.35} aria-hidden="true" />
          <h2 id={`ph-${id}-title`}>{title}</h2>
          <p>{lines.map(line => <span key={line}>{line}</span>)}</p>
          <button type="button" className="ph-entry-button" onClick={onClick}>{action}<ArrowRight size={19} aria-hidden="true" /></button>
        </div>
        <EntryCover key={cover?.id ?? 'default'} asset={cover} fallback={image} title={title} />
      </section>)}
    </div>
    <ProjectLibrary list={list} assets={assets} onOpenProject={onOpenProject} onCreateProject={onCreateProject} />
  </div>;
}
