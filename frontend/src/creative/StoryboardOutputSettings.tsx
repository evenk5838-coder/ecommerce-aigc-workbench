import { getDefaultImageSize, getImageSizesForRatio, getSupportedRatios } from '../capabilities';
import { imageSizeTier, imageTierOptions, ratioPreview } from '../image-specs';
import type { SpecSelection } from '../model-specs';
import type { Model } from '../types';
import './StoryboardOutputSettings.css';

export interface StoryboardOutputSettingsProps {
  model: Model | undefined; selection?: SpecSelection;
  size: string;
  onChange: (size: string) => void;
  disabled?: boolean;
}

function ratioLabel(ratio: string): string {
  const [width, height] = ratio.split(':').map(Number);
  if (!Number.isFinite(width) || !Number.isFinite(height)) return '画幅';
  return width === height ? '方形' : width > height ? '横屏' : '竖屏';
}

function isApproximate(size: string, ratio: string): boolean {
  const pixels = /^(\d+)x(\d+)$/.exec(size);
  const aspect = /^(\d+):(\d+)$/.exec(ratio);
  return !!pixels && !!aspect && Number(pixels[1]) * Number(aspect[2]) !== Number(pixels[2]) * Number(aspect[1]);
}

/** Every visible option maps to an actual model size; rendering never changes the saved selection. */
export default function StoryboardOutputSettings({ model, selection, size, onChange, disabled = false }: StoryboardOutputSettingsProps) {
  const imageModel = model?.kind === 'image' ? model : undefined;
  const ratios = getSupportedRatios(imageModel, selection).filter(ratio => getImageSizesForRatio(imageModel, ratio, selection).length > 0);
  const ratio = ratios.find(value => getImageSizesForRatio(imageModel, value, selection).includes(size));
  const tiers = ratio ? imageTierOptions(imageModel, ratio, selection) : [];
  const currentTier = tiers.find(tier => tier.sizes.includes(size));
  const currentSizes = ratio ? getImageSizesForRatio(imageModel, ratio, selection) : [];

  function selectRatio(nextRatio: string) {
    if (disabled || !ratios.includes(nextRatio)) return;
    const sizes = getImageSizesForRatio(imageModel, nextRatio, selection);
    if (sizes.includes(size)) return;
    const sameTier = imageTierOptions(imageModel, nextRatio, selection).find(tier => tier.label === imageSizeTier(size));
    const nextSize = sameTier?.sizes[0] ?? getDefaultImageSize(imageModel, nextRatio, selection) ?? sizes[0];
    if (nextSize) onChange(nextSize);
  }

  const missing = !model
    ? '模型信息尚未加载，暂时无法选择画幅。'
    : model.kind !== 'image'
      ? '请选择用于分镜生成的图片模型。'
      : '此模型尚未提供可对应的画幅与尺寸，请刷新模型信息。';

  return <fieldset className="storyboard-output-settings" disabled={disabled}>
    <legend>分镜画面</legend>
    {!ratios.length ? <p className="sos-note" role="status">{missing}</p> : <>
      <div className="sos-heading">画面比例</div>
      <div className="sos-ratios" role="group" aria-label="分镜画面比例">
        {ratios.map(value => <button type="button" key={value} aria-pressed={ratio === value} className={ratio === value ? 'selected' : ''} onClick={() => selectRatio(value)}>
          <span className="sos-ratio-preview" aria-hidden="true"><i style={ratioPreview(value)} /></span>
          <strong>{value}</strong><small>{ratioLabel(value)}</small>
        </button>)}
      </div>
      {ratio ? <>
        <div className="sos-heading">清晰度</div>
        <div className="sos-tiers" role="group" aria-label="分镜清晰度">
          {tiers.map(tier => <button type="button" key={tier.label} aria-pressed={currentTier?.label === tier.label} className={currentTier?.label === tier.label ? 'selected' : ''} onClick={() => {
            if (!disabled && tier.sizes.length && !tier.sizes.includes(size)) onChange(tier.sizes[0]);
          }}>{tier.label}</button>)}
        </div>
      </> : <p className="sos-note sos-attention" role="status">当前尺寸无法对应此模型的画幅，请重新选择一个比例。</p>}
      <details className="sos-details">
        <summary>查看尺寸详情</summary>
        {ratio ? <p>当前输出 {size.replace('x', ' × ')} 像素{isApproximate(size, ratio) ? `，接近 ${ratio} 比例。` : `，${ratio} 比例。`}</p> : size ? <p>当前保存的尺寸：{size.replace('x', ' × ')}，未作为可用选项。</p> : <p>尚未选择输出尺寸。</p>}
        <p>清晰度档位按像素量标记，具体规格以当前模型支持为准。</p>
        {currentTier && currentTier.sizes.length > 1 && <label>此档位的实际尺寸
          <select aria-label="分镜此档位的实际尺寸" value={size} onChange={event => {
            const nextSize = event.target.value;
            if (!disabled && currentSizes.includes(nextSize) && currentTier.sizes.includes(nextSize)) onChange(nextSize);
          }}>{currentTier.sizes.map(value => <option value={value} key={value}>{value.replace('x', ' × ')} 像素</option>)}</select>
        </label>}
      </details>
    </>}
  </fieldset>;
}
