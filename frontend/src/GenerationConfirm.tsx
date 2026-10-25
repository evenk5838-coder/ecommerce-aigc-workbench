import { useEffect, useId, useLayoutEffect, useRef } from 'react';
import { ArrowLeft, Cloud, LoaderCircle, Sparkles, X } from 'lucide-react';
import './GenerationConfirm.css';

export interface GenerationConfirmProps {
  modelName: string;
  kind: 'image' | 'video';
  count: number;
  specLabel: string;
  costs: number[] | null;
  error: string;
  busy: boolean;
  hasReference: boolean;
  onConfirm: () => void;
  onClose: () => void;
}

const money = (value: number): string => value.toLocaleString('zh-CN', { minimumFractionDigits: 2, maximumFractionDigits: 4 });

/** Confirms the current manual request snapshot; it does not create a backend approval. */
export default function GenerationConfirm({ modelName, kind, count, specLabel, costs, error, busy, hasReference, onConfirm, onClose }: GenerationConfirmProps) {
  const headingId = useId();
  const descriptionId = useId();
  const dialog = useRef<HTMLElement>(null);
  const current = useRef({ busy, onClose });
  useLayoutEffect(() => { current.current = { busy, onClose }; }, [busy, onClose]);

  const validCount = Number.isSafeInteger(count) && count > 0;
  const validCosts = Array.isArray(costs) && validCount && costs.length === count && costs.every(cost => typeof cost === 'number' && Number.isFinite(cost) && cost >= 0);
  const sum = validCosts ? costs.reduce((total, cost) => total + cost, 0) : null;
  const total = sum !== null && Number.isFinite(sum) ? sum : null;
  const loading = costs === null && !error;
  const effectiveError = error || (!loading && total === null ? '费用估算不完整，请返回修改后重新获取。' : '');
  const canConfirm = !busy && !effectiveError && total !== null;
  const unit = kind === 'image' ? '张图片' : '条视频';

  useEffect(() => {
    const panel = dialog.current;
    if (!panel) return;
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    const focusable = () => Array.from(panel.querySelectorAll<HTMLElement>(
      'button:not(:disabled),a[href],input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex="0"]',
    )).filter(element => !element.hidden && element.getClientRects().length > 0);
    panel.focus({ preventScroll: true });
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        if (!current.current.busy) current.current.onClose();
        return;
      }
      if (event.key !== 'Tab') return;
      const items = focusable(), first = items[0], last = items.at(-1);
      if (!first || !last) {
        event.preventDefault();
        panel.focus({ preventScroll: true });
      } else if (event.shiftKey && (document.activeElement === first || document.activeElement === panel || !panel.contains(document.activeElement))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || document.activeElement === panel || !panel.contains(document.activeElement))) {
        event.preventDefault();
        first.focus();
      }
    };
    const onFocusIn = (event: FocusEvent) => {
      if (event.target instanceof Node && !panel.contains(event.target)) {
        (focusable()[0] ?? panel).focus({ preventScroll: true });
      }
    };
    document.addEventListener('keydown', onKeyDown, true);
    document.addEventListener('focusin', onFocusIn);
    return () => {
      document.removeEventListener('keydown', onKeyDown, true);
      document.removeEventListener('focusin', onFocusIn);
      document.body.style.overflow = previousOverflow;
      if (previousFocus?.isConnected) previousFocus.focus({ preventScroll: true });
    };
  }, []);

  const close = () => { if (!busy) onClose(); };

  return <div className="generation-confirm-backdrop" onClick={event => { if (event.target === event.currentTarget) close(); }}>
    <section ref={dialog} className="generation-confirm-dialog" role="dialog" aria-modal="true" aria-labelledby={headingId} aria-describedby={descriptionId} aria-busy={busy} tabIndex={-1}>
      <header className="generation-confirm-header">
        <span className="generation-confirm-symbol"><Sparkles size={21} aria-hidden="true" /></span>
        <div><h2 id={headingId}>确认本次生成</h2><p id={descriptionId}>核对规格与预计费用，再开始创作。</p></div>
        <button type="button" className="generation-confirm-close" aria-label="返回修改" disabled={busy} onClick={close}><X size={19} aria-hidden="true" /></button>
      </header>

      <dl className="generation-confirm-details">
        <div><dt>生成数量</dt><dd>{validCount ? count : '—'} {unit}</dd></div>
        <div><dt>输出规格</dt><dd>{specLabel || '规格未提供'}</dd></div>
        <div><dt>生成模型</dt><dd>{modelName || '模型未提供'}</dd></div>
      </dl>

      <div className="generation-confirm-cost" aria-live="polite" aria-atomic="true">
        <div className="generation-confirm-cost-heading"><span>本次费用估算</span><small>人民币</small></div>
        {loading && !busy ? <p className="generation-confirm-loading" role="status"><LoaderCircle size={17} className="generation-confirm-spin" aria-hidden="true" />正在获取费用估算…</p> : effectiveError ?
          <p className="generation-confirm-error" role="alert">{effectiveError}</p> : total !== null && <>
            <strong className="generation-confirm-total">¥ {money(total)}</strong>
            <ul className="generation-confirm-cost-items" aria-label="逐项费用估算">{costs?.map((cost, index) => <li key={index}><span>{kind === 'image' ? '图片' : '视频'} {index + 1}</span><span>¥ {money(cost)}</span></li>)}</ul>
          </>}
        <p className="generation-confirm-disclaimer">费用为后端逐项估算的合计，非实际账单。</p>
      </div>

      <p className="generation-confirm-cloud"><Cloud size={17} aria-hidden="true" /><span>{hasReference ? '本次生成会将描述和所选参考图发送至云端模型。' : '本次生成会将文字描述发送至云端模型。'}</span></p>
      {busy && <p className="generation-confirm-pending" role="status">正在提交，请稍候。提交期间暂不能关闭。</p>}
      <footer className="generation-confirm-actions">
        <button type="button" className="generation-confirm-back" disabled={busy} onClick={close}><ArrowLeft size={16} aria-hidden="true" />返回修改</button>
        <button type="button" className="generation-confirm-submit" disabled={!canConfirm} onClick={() => { if (canConfirm) onConfirm(); }}>
          {busy ? <LoaderCircle size={16} className="generation-confirm-spin" aria-hidden="true" /> : <Sparkles size={16} aria-hidden="true" />}{busy ? '正在提交…' : '确认生成'}
        </button>
      </footer>
    </section>
  </div>;
}
