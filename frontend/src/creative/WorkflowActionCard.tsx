import { ArrowDownToLine, ArrowUpRight, Check, Clapperboard, FileText, Film, Image, LoaderCircle } from 'lucide-react';
import { mediaUrl } from '../api';
import type { WorkflowAction } from './director-workflow-api';
import { workflowActionDraftScope, workflowActionRenewalMessage, type WorkflowDraftScope } from './workflow-draft-scope';
import { actionCanConfirm, actionStatusLabel, actionTitle, availableExecution, estimatedCostLabel, executionLabel, finalComposition, quotePrompt, quoteSpecs } from './director-action-view';

interface Props {
  action: WorkflowAction; disabled: boolean; confirming?: boolean; recovering?: boolean;
  nodeTitles?: Readonly<Record<string, string>>;
  onConfirm: (action: WorkflowAction) => void; onReveal: (nodeId: string) => void;
  onSuggest: (message: string, scope?: WorkflowDraftScope) => void;
}

export default function WorkflowActionCard({ action, disabled, confirming, recovering, nodeTitles, onConfirm, onReveal, onSuggest }: Props) {
  const imageBatch = action.kind === 'generation' && action.quotes?.every(quote => quote.input.kind === 'image');
  const film = action.output_mode === 'film', final = finalComposition(action);
  const Icon = action.kind === 'confirm_script' ? FileText : action.kind === 'composition' || film ? Clapperboard : imageBatch ? Image : Film;
  const canConfirm = actionCanConfirm(action);
  const suggest = (message: string) => onSuggest(message, workflowActionDraftScope(action));
  const refreshable = action.action_status === 'proposed' && !canConfirm;
  const error = action.error || action.asset_error || action.composition?.error || action.composition?.asset_error || action.job?.error;
  const count = action.node_ids?.length || action.sources?.length || 0;
  const confirmLabel = action.kind === 'confirm_script' ? '确认脚本' : action.kind === 'composition' ? '确认并合成' : film ? '生成视频并合成' : `生成 ${count} ${imageBatch ? '张分镜' : '条视频'}`;
  return <article className={`dc-action-card dc-action-${action.status}`} aria-label={actionTitle(action)}>
    <header><span className="dc-action-icon"><Icon size={16}/></span><strong>{actionTitle(action)}</strong><span className="dc-action-status">{actionStatusLabel(action)}</span></header>
    {action.kind === 'confirm_script' && <div className="dc-script-ready"><p>{action.status === 'completed' ? '脚本已确认，接下来一起安排分镜。' : '脚本已放入画布。看看内容，再决定下一步。'}</p><button type="button" onClick={() => onReveal(action.node_ids?.[0] || 'script')}>查看脚本<ArrowUpRight size={13}/></button></div>}
    {film && <p className="dc-action-scope">生成以下视频，全部完成后按此顺序合成一条成片。保留每个片段的完整长度。</p>}
    {action.kind === 'generation' && <ol className="dc-shot-list">{action.quotes?.map((quote, index) => {
      const execution = action.executions?.find(item => item.node_id === quote.node_id), available = execution && availableExecution(execution);
      return <li key={quote.node_id}>
        <div className="dc-shot-heading"><button type="button" onClick={() => onReveal(quote.node_id)}><span>{String(index + 1).padStart(2, '0')}</span>镜头 {index + 1}<ArrowUpRight size={12}/></button>{execution && <span className={available ? 'dc-shot-ready' : ''}>{executionLabel(execution)}</span>}</div>
        {available && execution.asset_id && (quote.input.kind === 'image' ? <button type="button" className="dc-shot-image" onClick={() => onReveal(quote.node_id)} aria-label={`查看镜头 ${index + 1} 图片`}><img src={mediaUrl(execution.asset_id)} alt={`镜头 ${index + 1} 生成画面`} loading="lazy"/></button> : <video controls playsInline preload="metadata" src={mediaUrl(execution.asset_id)}/>)}
        <p className="dc-shot-prompt">{quotePrompt(quote) || '在画布查看镜头内容'}</p>
        <div className="dc-spec-pills">{quoteSpecs(quote).map(spec => <span key={spec}>{spec}</span>)}</div>
        <details className="dc-quote-details"><summary>模型与生成设置</summary><p>{quote.input.model_id}</p><p>{quotePrompt(quote)}</p>{typeof quote.input.params.size === 'string' && <p>画面尺寸 {quote.input.params.size}</p>}{quote.input.params.generate_audio === true && <p>生成声音</p>}{quote.input.input_asset_id && <div className="dc-reference"><img src={mediaUrl(quote.input.input_asset_id)} alt={`镜头 ${index + 1} 参考图`} loading="lazy"/><span>参考图</span></div>}</details>
        {(execution?.error || execution?.asset_error) && <p className="dc-inline-error">{(execution.asset_error || execution.error)?.message}</p>}
        {available && execution.asset_id && <a className="dc-result-link" href={mediaUrl(execution.asset_id)} download><ArrowDownToLine size={12}/>下载{quote.input.kind === 'image' ? '图片' : '片段'}</a>}
      </li>;
    })}</ol>}
    {action.kind === 'composition' && <><p className="dc-action-scope">按以下顺序完整拼接，保留原有片段。</p><ol className="dc-composition-order">{action.sources?.map((source, index) => {
      const title = source.node_id ? nodeTitles?.[source.node_id]?.trim() : undefined;
      return <li key={`${source.asset_id}-${index}`}><span aria-label={`合成顺序 ${index + 1}`}>{index + 1}</span><div className="dc-composition-source">
        <div className="dc-composition-source-heading">{source.node_id ? <button type="button" onClick={() => onReveal(source.node_id!)}>{title || '在画布查看'}<ArrowUpRight size={12}/></button> : <span>视频素材</span>}<small>{(source.metadata.duration_ms / 1000).toFixed(1)} 秒</small></div>
        {!title && <video controls playsInline preload="metadata" src={mediaUrl(source.asset_id)} aria-label={`预览第 ${index + 1} 个合成来源`}/>}
      </div></li>;
    })}</ol></>}
    {film && action.action_status === 'submitted' && !final && <p className="dc-film-progress" role="status">{action.composition?.status === 'blocked' ? '镜头尚未齐备，已保留完成的片段' : action.status === 'failed' ? '成片未完成，已保留可用片段' : action.media_status !== 'completed' ? '等待本组镜头完成' : '镜头已完成，正在合成成片'}{action.status === 'running' && <LoaderCircle size={13} className="dc-spin"/>}</p>}
    {error && <p className="dc-inline-error" role="status">{error.message}</p>}
    {final && <div className="dc-final-result"><strong><Check size={14}/>成片已就绪</strong><video controls playsInline preload="metadata" src={mediaUrl(final.asset_id!)}/><div>{final.result_node_id && <button type="button" onClick={() => onReveal(final.result_node_id!)}>在画布查看<ArrowUpRight size={12}/></button>}<a href={mediaUrl(final.asset_id!)} download><ArrowDownToLine size={13}/>下载成片</a></div><small>{final.actual_duration_ms != null ? `${(final.actual_duration_ms / 1000).toFixed(1)} 秒` : ''}</small></div>}
    {canConfirm && <footer className="dc-action-footer">{action.kind !== 'confirm_script' && <span>{action.kind === 'composition' ? '本地合成' : estimatedCostLabel(action.estimated_cost_cny)}</span>}<button type="button" className="dc-confirm" disabled={disabled} onClick={() => onConfirm(action)}>{confirming ? <LoaderCircle size={14} className="dc-spin"/> : <Check size={14}/>} {recovering ? '继续核对并确认原方案' : confirmLabel}</button></footer>}
    {refreshable && <button type="button" className="dc-next-suggestion" disabled={disabled} onClick={() => suggest(workflowActionRenewalMessage(action))}>重新准备方案<ArrowUpRight size={12}/></button>}
    {action.status === 'completed' && action.kind === 'confirm_script' && <button type="button" className="dc-next-suggestion" disabled={disabled} onClick={() => suggest('请根据已确认的脚本，规划分镜并准备图片生成方案。')}>接下来，安排分镜<ArrowUpRight size={12}/></button>}
    {action.status === 'completed' && imageBatch && <div className="dc-followups"><button type="button" disabled={disabled} onClick={() => suggest('请使用刚才完成的分镜图片，准备逐镜视频生成方案。')}>制作分镜视频<ArrowUpRight size={12}/></button><button type="button" disabled={disabled} onClick={() => suggest('请使用刚才完成的分镜图片，按镜头顺序准备视频生成和完整成片方案。')}>制作完整成片<ArrowUpRight size={12}/></button></div>}
    {action.status === 'completed' && action.kind === 'generation' && !film && !imageBatch && <button type="button" className="dc-next-suggestion" disabled={disabled} onClick={() => suggest('请将刚才完成的视频按镜头顺序准备合成方案。')}>把片段合成作品<ArrowUpRight size={12}/></button>}
  </article>;
}
