import { ArrowRight, Clapperboard, FileText, Film, Layers3, Package, PanelsTopLeft, Route } from 'lucide-react';
import './CreativeSpace.css';

export interface CreativeSpaceProps { onWorkbench: () => void }

const stages = [
  { name: '需求', detail: '明确商品、卖点与表达目标', icon: Package },
  { name: '脚本', detail: '把创意整理为可确认的故事', icon: FileText },
  { name: '分镜', detail: '逐镜确认画面与商品呈现', icon: PanelsTopLeft },
  { name: '镜头', detail: '围绕已确认的画面生成片段', icon: Clapperboard },
  { name: '成片', detail: '查看片段或按顺序拼接成片', icon: Film },
];

/** S1 navigation destination only. Later creation stages have no live controls yet. */
export default function CreativeSpace({ onWorkbench }: CreativeSpaceProps) {
  return <div className="creative-space">
    <div className="creative-space-inner">
      <header className="creative-space-heading">
        <span className="creative-space-eyebrow"><Route size={15} aria-hidden="true" />引导创作</span>
        <span className="creative-space-status"><span aria-hidden="true" />流程待接入</span>
      </header>

      <section className="creative-space-intro" aria-labelledby="creative-space-title">
        <div className="creative-space-intro-copy">
          <p className="creative-space-kicker">为多镜头电商视频准备的创作空间</p>
          <h1 id="creative-space-title" tabIndex={-1}>从商品需求，<br />到一组连贯镜头。</h1>
          <p className="creative-space-description">沿着需求、脚本与分镜逐步确认，让每个镜头都有清晰的创作依据。</p>
        </div>
        <div className="creative-space-availability">
          <span className="creative-space-availability-icon"><Layers3 size={26} strokeWidth={1.5} aria-hidden="true" /></span>
          <h2>引导流程正在接入</h2>
          <p>这里先展示创作流程。项目、脚本、分镜与成片操作尚未开放。</p>
          <button type="button" onClick={onWorkbench}>返回快捷工作台<ArrowRight size={16} aria-hidden="true" /></button>
          <small>现在可以继续生成商品图片与单条视频。</small>
        </div>
      </section>

      <section className="creative-space-flow" aria-labelledby="creative-flow-title">
        <div className="creative-space-section-heading"><div><span>创作路径</span><h2 id="creative-flow-title">先确认想法，再展开画面。</h2></div><p>流程概览 · 尚不可操作</p></div>
        <ol className="creative-space-stages">
          {stages.map(({ name, detail, icon: Icon }, index) => <li key={name}>
            <div className="creative-space-stage-top"><span className="creative-space-stage-icon"><Icon size={23} strokeWidth={1.6} aria-hidden="true" /></span><span className="creative-space-stage-number" aria-hidden="true">0{index + 1}</span></div>
            <h3>{name}</h3><p>{detail}</p>
            {index < stages.length - 1 && <span className="creative-space-stage-connector" aria-hidden="true"><ArrowRight size={13} /></span>}
          </li>)}
        </ol>
      </section>

      <section className="creative-space-delivery" aria-labelledby="creative-delivery-title">
        <div className="creative-space-section-heading"><div><span>交付方式</span><h2 id="creative-delivery-title">后续将支持两种视频成果。</h2></div></div>
        <div className="creative-space-delivery-grid">
          <article><span className="creative-space-mode-icon"><Film size={23} strokeWidth={1.6} aria-hidden="true" /></span><div><h3>完整视频<span>待接入</span></h3><p>逐镜生成成功后，按已确认的顺序拼接为一支完整视频。</p></div></article>
          <article><span className="creative-space-mode-icon"><Clapperboard size={23} strokeWidth={1.6} aria-hidden="true" /></span><div><h3>分镜视频<span>待接入</span></h3><p>保留各镜头的独立片段，便于挑选、复用与后续合成。</p></div></article>
        </div>
      </section>
      <footer className="creative-space-footer"><span>当前入口仅供了解流程。</span><span>已有素材与创作仍在快捷工作台。</span></footer>
    </div>
  </div>;
}
