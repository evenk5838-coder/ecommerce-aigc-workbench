import type { ReactNode } from 'react';
import { ArrowRight, ArrowUpRight, Check, ChevronDown, Film, Image as ImageIcon, Layers, LoaderCircle, RefreshCw, SlidersHorizontal, Sparkles, WifiOff } from 'lucide-react';
import { mediaUrl } from './api';
import type { Asset } from './types';

type Props = {
  assets: Asset[];
  activeCount: number;
  connected: boolean;
  loading: boolean;
  kind: 'image' | 'video';
  assetsError?: string;
  children: ReactNode;
  onPreview: (asset: Asset) => void;
  onReference: (asset: Asset, kind: 'image' | 'video') => void;
  onEdit?: (asset: Asset) => void;
  onLibrary: () => void;
  onTasks: () => void;
  onRefresh?: () => void;
};

function resultTitle(asset: Asset): string {
  try {
    const meta: unknown = JSON.parse(asset.meta_json || '{}');
    if (meta && typeof meta === 'object' && !Array.isArray(meta)) {
      const values = meta as Record<string, unknown>;
      for (const key of ['title', 'name', 'filename', 'prompt']) {
        if (typeof values[key] === 'string' && values[key].trim()) return values[key].trim();
      }
    }
  } catch { /* Missing metadata does not prevent an existing asset from being viewed. */ }
  return (asset.kind === 'image' ? '图片' : '视频') + ' · ' + asset.id.slice(-8);
}

function resultDate(createdAt: number): Date | null {
  if (!Number.isFinite(createdAt) || createdAt <= 0) return null;
  const date = new Date(createdAt * 1000);
  return Number.isNaN(date.getTime()) ? null : date;
}

const dateFormatter = new Intl.DateTimeFormat('zh-CN', {
  year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
});

export default function Home({ assets, activeCount, connected, loading, kind, assetsError, children, onPreview, onReference, onEdit, onLibrary, onTasks, onRefresh }: Props) {
  const results = assets.filter(asset => asset.kind === kind && ['generated', 'cloud_generated'].includes(asset.source)).slice(0, 24);
  const mediaLabel = kind === 'image' ? '图片' : '视频';
  const loadFailed = Boolean(assetsError) || (!connected && !loading);
  const failureMessage = assetsError || '暂时无法连接服务，正在尝试重新连接。';
  const refreshButton = onRefresh ? <button type="button" className="workbench-refresh" onClick={onRefresh} disabled={loading}><RefreshCw size={15} className={loading ? 'spin' : ''} />重新加载</button> : null;

  return <div className="workbench-content">
    <header className="workbench-welcome">
      <div><span className="workbench-eyebrow"><Sparkles size={13} /> 电商内容创作</span><h1>好商品，值得被看见。</h1><p>生成商品图片与视频，让每一份素材接着创作。</p></div>
      <div className="workbench-promise"><Check size={14} /><span>素材自动保存到本机</span></div>
    </header>

    <nav className="workbench-jump-nav" aria-label="工作台快速定位">
      <a href="#creation-settings"><SlidersHorizontal size={17} />生成设置</a>
      <a href="#creation-results"><ImageIcon size={17} />查看成果<span>{results.length}</span></a>
    </nav>

    <div className="workbench-layout">
      <section id="creation-settings" tabIndex={-1} className="workbench-composer" aria-label="生成设置">{children}</section>

      <section id="creation-results" tabIndex={-1} className="workbench-results" aria-label="生成结果" aria-busy={loading}>
        <div className="workbench-results-heading"><div><h2>{mediaLabel}成果<span>{results.length}</span></h2><p>完整查看每份成果，再选择下一步创作。</p></div><button className="workbench-library-link" onClick={onLibrary}>全部素材<ArrowUpRight size={15} /></button></div>
        {loadFailed && results.length > 0 && <div className="workbench-connection-note" role="status"><WifiOff size={17} /><div><strong>素材暂未更新</strong><p>{failureMessage}</p><span>正在显示上次成功加载的成果。</span></div>{refreshButton}</div>}
        {activeCount > 0 && <button className="workbench-running" onClick={onTasks}><LoaderCircle size={17} className="spin" /><span><strong>{activeCount} 个任务处理中</strong><small>你可以继续调整描述、编辑素材，或提交下一组任务。</small></span><ArrowRight size={16} /></button>}
        <div className="workbench-result-grid">
          {results.map(asset => {
            const title = resultTitle(asset);
            const createdAt = resultDate(asset.created_at);
            const duration = asset.duration_ms && asset.duration_ms > 0 ? (asset.duration_ms / 1000).toFixed(1) + ' 秒' : null;
            return <article className="workbench-result" key={asset.id}>
              <button className="workbench-result-media" onClick={() => onPreview(asset)} aria-label={'预览：' + title}>
                {asset.kind === 'image' ? <img src={mediaUrl(asset.id)} alt={title} loading="lazy" /> : <video src={mediaUrl(asset.id) + '#t=0.1'} muted playsInline preload="metadata" aria-label={title} />}
                <span className="workbench-media-badge">{asset.kind === 'image' ? <ImageIcon size={12} /> : <Film size={12} />}{asset.kind === 'image' ? '图片' : '视频'}</span>
                <span className="workbench-preview-label">{asset.kind === 'video' ? '播放视频' : '查看原图'}<ArrowUpRight size={14} /></span>
              </button>
              <div className="workbench-result-details">
                <strong title={title}>{title}</strong>
                <div className="workbench-result-spec"><span>{asset.width > 0 && asset.height > 0 ? asset.width + ' × ' + asset.height : '尺寸未记录'}</span>{duration && <span>{duration}</span>}</div>
                {createdAt ? <time dateTime={createdAt.toISOString()} title={'创建于 ' + createdAt.toLocaleString('zh-CN')} aria-label={'创建于 ' + dateFormatter.format(createdAt)}>{dateFormatter.format(createdAt)}</time> : <span className="workbench-date-missing">创建时间未记录</span>}
              </div>
              <div className="workbench-card-footer">
                <button className="workbench-preview-button" onClick={() => onPreview(asset)}>{asset.kind === 'image' ? <ImageIcon size={15} /> : <Film size={15} />}{asset.kind === 'image' ? '预览' : '播放与下载'}</button>
                {asset.kind === 'image' && <details className="workbench-continue">
                  <summary>继续创作<ChevronDown size={15} /></summary>
                  <div className="workbench-result-actions">
                    <button onClick={() => onReference(asset, 'image')}><ImageIcon size={15} /><span>参考生图<small>保留商品，调整背景与呈现</small></span></button>
                    <button onClick={() => onReference(asset, 'video')}><Film size={15} /><span>生视频<small>将这张图片作为视频首帧</small></span></button>
                    {onEdit && <button onClick={() => onEdit(asset)}><Layers size={15} /><span>排版编辑<small>添加文案、色块与布局</small></span></button>}
                  </div>
                </details>}
              </div>
            </article>;
          })}
          {!results.length && loadFailed && <div className="workbench-empty workbench-load-error" role="status"><WifiOff size={30} /><strong>暂时无法读取素材</strong><p>{failureMessage}</p><span className="workbench-empty-hint">这不代表已保存的素材被删除。重新加载后再查看。</span>{refreshButton}</div>}
          {!results.length && loading && !loadFailed && <div className="workbench-empty" role="status"><LoaderCircle size={30} className="spin" /><strong>正在读取创作成果</strong><p>已有素材加载后会显示在这里。</p></div>}
          {!results.length && !loading && !loadFailed && <div className="workbench-empty"><span className="workbench-empty-icon">{kind === 'image' ? <ImageIcon size={30} /> : <Film size={30} />}</span><strong>第一份{mediaLabel}成果，从这里开始</strong><p>{kind === 'image' ? '描述你的商品，或选择参考图，生成适合你的商品图片。' : '选一张商品图作为首帧，描述动作和镜头，生成动态展示。'}</p><span className="workbench-empty-hint">生成完成后，结果会自动出现在这里</span></div>}
        </div>
      </section>
    </div>
  </div>;
}
