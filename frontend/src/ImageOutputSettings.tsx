import { getImageSizesForRatio, getSupportedRatios } from './capabilities';
import { imageTierOptions, ratioPreview } from './image-specs';
import type { Model } from './types';
import './ImageOutputSettings.css';

export interface ImageOutputSettingsProps {
  model: Model | undefined; ratio: string; imageSize: string; count: number;
  onRatioChange: (ratio: string) => void; onSizeChange: (size: string) => void; onCountChange: (count: number) => void;
}
export default function ImageOutputSettings({ model, ratio, imageSize, count, onRatioChange, onSizeChange, onCountChange }: ImageOutputSettingsProps) {
  const tiers = imageTierOptions(model,ratio), sizes = getImageSizesForRatio(model,ratio);
  const current = tiers.find(tier => tier.sizes.includes(imageSize));
  return <div className="image-output-settings">
    <div className="image-setting-heading">画面比例</div>
    <div className="image-ratio-cards" role="group" aria-label="图片画面比例">{getSupportedRatios(model).map(value => <button type="button" key={value} className={ratio === value ? 'selected' : ''} aria-pressed={ratio === value} onClick={() => onRatioChange(value)}><span className="image-ratio-preview" aria-hidden="true"><i style={ratioPreview(value)}/></span><span>{value}</span></button>)}</div>
    <div className="image-setting-heading">清晰度</div>
    <div className="image-tier-buttons" style={{gridTemplateColumns:`repeat(${Math.max(1,Math.min(3,tiers.length))},minmax(0,1fr))`}} role="group" aria-label="图片清晰度">{tiers.map(tier => <button type="button" key={tier.label} aria-pressed={current?.label === tier.label} className={current?.label === tier.label ? 'selected' : ''} onClick={() => onSizeChange(tier.sizes.includes(imageSize) ? imageSize : tier.sizes[0])}>{tier.label}</button>)}</div>
    {!tiers.length ? <p className="image-size-detail" role="status">此画幅暂无可用清晰度，请选择其他比例。</p> : sizes.includes(imageSize) ? <p className="image-size-detail">输出尺寸 {imageSize.replace('x',' × ')} 像素{(ratio === '9:16'||ratio === '16:9') && ['1536x2688','2688x1536'].includes(imageSize) ? ' · 接近所选比例' : ''}</p> : <p className="image-size-detail">选择一个清晰度，继续生成。</p>}
    {current && current.sizes.length > 1 && <label className="image-size-detail">档位内尺寸<select aria-label="档位内尺寸" value={imageSize} onChange={event => onSizeChange(event.target.value)}>{current.sizes.map(size => <option key={size} value={size}>{size.replace('x',' × ')}</option>)}</select></label>}
    <div className="image-setting-heading">生成数量</div>
    <div className="image-count-options" role="group" aria-label="图片生成数量">{[1,2,4].map(value => <button key={value} type="button" aria-pressed={count === value} className={count === value ? 'selected' : ''} onClick={() => onCountChange(value)}>{value} 张</button>)}</div>
    <details className="image-count-custom"><summary>其他数量</summary><label>生成数量<input aria-label="生成数量" type="number" min={1} max={6} value={count} onChange={event => { const value=Number(event.target.value); if(Number.isInteger(value)&&value>=1&&value<=6)onCountChange(value); }}/></label></details>
  </div>;
}
