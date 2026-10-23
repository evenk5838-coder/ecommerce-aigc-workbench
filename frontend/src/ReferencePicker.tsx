import { useMemo, useState } from 'react';
import { Check, Clock3, Image as ImageIcon, LoaderCircle, RefreshCw, Search, Upload, X } from 'lucide-react';
import { mediaUrl } from './api';
import { metadata } from './editor';
import type { Asset } from './types';
import './reference-picker.css';

export interface ReferencePickerProps {
  assets: Asset[];
  selectedId: string | null;
  onSelect: (asset: Asset) => void;
  onUpload: () => void;
  onClose: () => void;
  uploading: boolean;
  error?: string;
  onRefresh?: () => void;
}

type SourceFilter = 'all' | 'upload' | 'generated';
const MAX_REFERENCE_BYTES = 10 * 1024 * 1024;
const sourceFilters: { id: SourceFilter; label: string }[] = [
  { id: 'all', label: '全部来源' },
  { id: 'upload', label: '上传原图' },
  { id: 'generated', label: '生成成果' },
];
const isGenerated = (asset: Asset) => ['generated', 'cloud_generated'].includes(asset.source);
const sourceName = (asset: Asset) => asset.source === 'upload' ? '上传原图' : isGenerated(asset) ? '生成成果' : '本机素材';

function assetDate(createdAt: number): { label: string; full: string; iso?: string } {
  const date = new Date(createdAt > 1e12 ? createdAt : createdAt * 1000);
  if (!Number.isFinite(createdAt) || createdAt <= 0 || Number.isNaN(date.getTime())) return { label: '时间未记录', full: '素材入库时间未记录' };
  return {
    label: date.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false }),
    full: date.toLocaleString('zh-CN', { hour12: false }),
    iso: date.toISOString(),
  };
}

export default function ReferencePicker({ assets, selectedId, onSelect, onUpload, onClose, uploading, error, onRefresh }: ReferencePickerProps) {
  const [query, setQuery] = useState('');
  const [source, setSource] = useState<SourceFilter>('all');
  const [failedPreviews, setFailedPreviews] = useState<Set<string>>(new Set());
  const [previewRevision, setPreviewRevision] = useState(0);
  const images = useMemo(() => assets.filter(asset => asset.kind === 'image').map(asset => {
    const info = metadata(asset);
    const filename = typeof info.filename === 'string' && info.filename.trim() ? info.filename : null;
    const name = filename || `${isGenerated(asset) ? '生成图片' : '商品图片'} · ${asset.id.slice(-6)}`;
    const prompt = typeof info.prompt === 'string' ? info.prompt : '';
    return { asset, name, search: `${name} ${asset.id} ${prompt}`.toLocaleLowerCase(), date: assetDate(asset.created_at) };
  }), [assets]);
  const searchTerm = query.trim().toLocaleLowerCase();
  const visibleImages = images.filter(({ asset, search }) =>
    (source === 'all' || (source === 'upload' ? asset.source === 'upload' : isGenerated(asset))) &&
    (!searchTerm || search.includes(searchTerm)));
  const resetFilters = () => { setQuery(''); setSource('all'); };
  const refresh = () => { setFailedPreviews(new Set()); setPreviewRevision(value => value + 1); onRefresh?.(); };

  return <div className="modal-backdrop reference-picker-backdrop" onClick={onClose}>
    <section className="reference-modal reference-picker-dialog" role="dialog" aria-modal="true" aria-labelledby="reference-picker-title" aria-describedby="reference-picker-description" onClick={event => event.stopPropagation()}>
      <header className="reference-picker-header">
        <div className="reference-picker-heading"><span className="reference-picker-heading-icon"><ImageIcon size={21} /></span><div><h2 id="reference-picker-title">选择商品参考图</h2><p id="reference-picker-description">选择一张清晰的商品图，作为这次创作的起点。</p></div></div>
        <button className="reference-picker-icon-button" type="button" aria-label="关闭参考图选择" onClick={onClose}><X size={20} /></button>
      </header>

      <div className="reference-picker-controls">
        <button className="reference-picker-upload" type="button" onClick={onUpload} disabled={uploading}>
          <span className="reference-picker-upload-icon">{uploading ? <LoaderCircle size={22} className="spin" /> : <Upload size={22} />}</span>
          <span><strong>{uploading ? '正在上传商品图…' : '上传新的商品图'}</strong><small>JPG、PNG 或 WebP · 单张不超过 10 MB</small></span>
          <span className="reference-picker-upload-action">{uploading ? '上传中' : '选择文件'}</span>
        </button>
        {error && <div className="reference-picker-error" role="alert"><span>{error}</span>{onRefresh && <button type="button" onClick={refresh}><RefreshCw size={14} />重新加载</button>}</div>}
        <label className="reference-picker-search"><Search size={17} /><input aria-label="搜索参考图片" placeholder="搜索文件名、素材 ID 或生成描述" value={query} onChange={event => setQuery(event.target.value)} />{query && <button type="button" aria-label="清空参考图搜索" onClick={() => setQuery('')}><X size={15} /></button>}</label>
        <div className="reference-picker-filter-row"><div className="reference-picker-filters" role="group" aria-label="参考图片来源">{sourceFilters.map(filter => <button type="button" key={filter.id} className={source === filter.id ? 'selected' : ''} aria-pressed={source === filter.id} onClick={() => setSource(filter.id)}>{filter.label}</button>)}</div>{onRefresh && <button className="reference-picker-refresh" type="button" onClick={refresh} aria-label="刷新参考图片"><RefreshCw size={14} /><span>刷新</span></button>}</div>
      </div>

      <div className="reference-picker-body">
        <p className="reference-picker-count" role="status">{visibleImages.length} 张图片{searchTerm || source !== 'all' ? ` · 共 ${images.length} 张` : ''}<span>点击图片即可选用</span></p>
        <div className="reference-picker-grid">
          {visibleImages.map(({ asset, name, date }) => {
            const oversized = asset.size > MAX_REFERENCE_BYTES;
            const failed = failedPreviews.has(asset.id);
            const selected = selectedId === asset.id;
            return <button key={asset.id} type="button" className={`reference-picker-card${selected ? ' selected' : ''}`} disabled={oversized || failed || uploading} aria-label={`选择参考图 ${name} ${asset.id}`} aria-pressed={selected} onClick={() => onSelect(asset)} title={oversized ? '此图片超过 10 MB，请压缩后重新上传' : failed ? '暂时无法预览，请刷新后重试' : name}>
              <span className="reference-picker-media">{failed ? <span className="reference-picker-image-error"><ImageIcon size={27} /><span>暂时无法预览</span></span> : <img key={`${asset.id}-${previewRevision}`} src={mediaUrl(asset.id)} loading="lazy" alt={name} onError={() => setFailedPreviews(previous => new Set(previous).add(asset.id))} />}{selected && <span className="reference-picker-selected"><Check size={14} />已选</span>}</span>
              <span className="reference-picker-card-info"><strong>{name}</strong><span className="reference-picker-card-spec"><span>{asset.width} × {asset.height}</span><span>{(asset.size / 1024 / 1024).toFixed(1)} MB</span></span><span className="reference-picker-card-meta"><span className={`reference-picker-source ${asset.source === 'upload' ? 'uploaded' : ''}`}>{sourceName(asset)}</span><time dateTime={date.iso} title={`入库时间：${date.full}`}><Clock3 size={11} />{date.label}</time></span>{oversized && <span className="reference-picker-unavailable">超过 10 MB，压缩后可用</span>}{failed && !oversized && <span className="reference-picker-unavailable">请刷新素材后重试</span>}</span>
            </button>;
          })}
        </div>
        {!visibleImages.length && <div className="reference-picker-empty"><span><Search size={29} /></span><h3>{images.length ? '没有找到匹配的图片' : error ? '暂时无法读取参考图' : '还没有商品参考图'}</h3><p>{images.length ? '试试其他关键词，或查看全部来源。' : error ? '请检查服务连接，再重新加载已保存的素材。' : '先上传一张商品图，也可以稍后从生成成果中选用。'}</p>{images.length ? <button type="button" onClick={resetFilters}>清除筛选，查看全部</button> : error ? onRefresh && <button type="button" onClick={refresh}>重新加载</button> : <button type="button" disabled={uploading} onClick={onUpload}>{uploading ? '正在上传…' : '上传商品图'}</button>}</div>}
      </div>
      <footer className="reference-picker-footer"><span>上传仅保存素材，选图不会发起生成。</span><button type="button" onClick={onClose}>取消</button></footer>
    </section>
  </div>;
}
