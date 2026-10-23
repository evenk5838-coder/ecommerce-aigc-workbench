import type { ReactNode } from 'react';
import { ArrowRight, ArrowUpRight, Check, Film, Image as ImageIcon, Layers, LoaderCircle, Sparkles, WifiOff } from 'lucide-react';
import { mediaUrl } from './api';
import type { Asset } from './types';

type Props = {
  assets: Asset[];
  activeCount: number;
  connected: boolean;
  loading: boolean;
  kind: 'image' | 'video';
  children: ReactNode;
  onPreview: (asset: Asset) => void;
  onReference: (asset: Asset, kind: 'image' | 'video') => void;
  onEdit: (asset: Asset) => void;
  onLibrary: () => void;
  onTasks: () => void;
};

export default function Home({ assets, activeCount, connected, loading, kind, children, onPreview, onReference, onEdit, onLibrary, onTasks }: Props) {
  const results = assets.filter(asset => asset.kind === kind && ['generated', 'cloud_generated'].includes(asset.source)).slice(0, 24);
  const mediaLabel = kind === 'image' ? '图片' : '视频';
  return <div className="workbench-content">
    <header className="workbench-welcome">
      <div><span className="workbench-eyebrow"><Sparkles size={13} /> 电商内容创作</span><h1>好商品，值得被看见。</h1><p>生成商品图片与视频，让每一份素材接着创作。</p></div>
      <div className="workbench-promise"><Check size={14} /><span>素材自动保存到本机</span></div>
    </header>

    <div className="workbench-layout">
      <section className="workbench-composer" aria-label="生成设置">{children}</section>

      <section className="workbench-results" aria-label="生成结果">
        <div className="workbench-results-heading"><div><h2>{mediaLabel}成果<span>{results.length}</span></h2><p>预览、复用参考图，或继续制作商品视频。</p></div><button className="workbench-library-link" onClick={onLibrary}>全部素材<ArrowUpRight size={15} /></button></div>
        {!connected && !loading && <div className="workbench-connection-note" role="status"><WifiOff size={16} /><span>暂时无法连接服务，正在尝试重新连接。已有成果仍可查看。</span></div>}
        {activeCount > 0 && <button className="workbench-running" onClick={onTasks}><LoaderCircle size={17} className="spin" /><span><strong>{activeCount} 个任务处理中</strong><small>你可以继续调整描述、编辑素材，或提交下一组任务。</small></span><ArrowRight size={16} /></button>}
        <div className="workbench-result-grid">
          {results.map(asset => <article className="workbench-result" key={asset.id}>
            <button className="workbench-result-media" onClick={() => onPreview(asset)} aria-label={'预览' + (asset.kind === 'image' ? '商品图片' : '商品视频')}>
              {asset.kind === 'image' ? <img src={mediaUrl(asset.id)} alt="生成的商品图片" loading="lazy" /> : <video src={mediaUrl(asset.id) + '#t=0.1'} muted playsInline preload="metadata" />}
              <span className="workbench-media-badge">{asset.kind === 'image' ? <ImageIcon size={12} /> : <Film size={12} />}{asset.kind === 'image' ? '商品图片' : '商品视频'}</span>
              <span className="workbench-preview-label">{asset.kind === 'video' ? '播放视频' : '查看大图'}<ArrowUpRight size={14} /></span>
            </button>
            <div className="workbench-result-details"><strong>{asset.kind === 'image' ? '商品视觉' : '动态商品展示'}</strong><span>{asset.kind === 'video' ? ((asset.duration_ms || 0) / 1000).toFixed(1) + ' 秒' : asset.width + ' × ' + asset.height}</span></div>
            {asset.kind === 'image' ? <div className="workbench-result-actions">
              <button onClick={() => onReference(asset, 'image')} title="使用这张图片作为图生图参考图"><ImageIcon size={13} />参考生图</button>
              <button onClick={() => onReference(asset, 'video')} title="使用这张图片作为视频首帧"><Film size={13} />生视频</button>
              <button onClick={() => onEdit(asset)} title="进入固定尺寸排版编辑器"><Layers size={13} />排版</button>
            </div> : <button className="workbench-video-action" onClick={() => onPreview(asset)}><Film size={14} />预览与下载<ArrowRight size={14} /></button>}
          </article>)}
          {!results.length && loading && <div className="workbench-empty" role="status"><LoaderCircle size={30} className="spin" /><strong>正在读取创作成果</strong><p>已有素材加载后会显示在这里。</p></div>}
          {!results.length && !loading && !connected && <div className="workbench-empty" role="status"><WifiOff size={30} /><strong>暂时无法读取素材</strong><p>服务恢复后，已保存的成果会自动显示。</p></div>}
          {!results.length && !loading && connected && <div className="workbench-empty"><span className="workbench-empty-icon">{kind === 'image' ? <ImageIcon size={30} /> : <Film size={30} />}</span><strong>第一份{mediaLabel}成果，从这里开始</strong><p>{kind === 'image' ? '描述你的商品，或选择参考图，生成适合你的商品图片。' : '选一张商品图作为首帧，描述动作和镜头，生成动态展示。'}</p><span className="workbench-empty-hint">生成完成后，结果会自动出现在这里</span></div>}
        </div>
      </section>
    </div>
  </div>;
}
