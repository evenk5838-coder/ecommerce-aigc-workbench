import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { ChevronDown, LoaderCircle, RefreshCw, Sparkles } from 'lucide-react';
import { ApiError } from '../api';
import { assistantApi } from './api';
import { assistantAvailability } from './guards';
import type { AssistantSession, AssistantStatus, SessionRecovery, StructuredReply } from './types';
import './PromptOptimizer.css';

export interface PromptOptimizerProps {
  projectId: string;
  prompt: string;
  kind: 'image' | 'video';
  onApply: (text: string) => void;
}
type Phase = 'checking' | 'recovering' | 'sending' | 'idle';
interface InputSnapshot { prompt: string; kind: 'image' | 'video' | null }
interface OptimizationResult {
  reply: string;
  optimized: string | null;
  explanation: string | null;
  degraded: boolean;
  input: InputSnapshot;
  recovered: boolean;
}
interface PendingRequest {
  input: InputSnapshot;
  history: string;
  turns: number | null;
}
const validText = (value: unknown): value is string => typeof value === 'string' && value.trim().length > 0;
function resultFrom(reply: string, structured: StructuredReply, input: InputSnapshot, recovered: boolean): OptimizationResult {
  // Only the server-designated pure prompt is adoptable. Never extract text from reply.
  const refused = structured.reply_type === 'refusal' || structured.refusal?.detected === true || structured.refusal?.structured === true;
  return {
    reply, optimized: !refused && validText(structured.optimized_prompt) ? structured.optimized_prompt : null,
    explanation: validText(structured.explanation) ? structured.explanation : null,
    degraded: structured.degraded === true, input, recovered,
  };
}
function latestResult(recovery: SessionRecovery): OptimizationResult | null {
  if (recovery.session?.intent !== 'optimize_only') return null;
  let lastUser = -1, lastAssistant = -1;
  recovery.history.forEach((message, index) => {
    if (message.role === 'user') lastUser = index;
    if (message.role === 'assistant') lastAssistant = index;
  });
  // A final user message can still be running; do not label the previous answer as its result.
  if (lastAssistant < 0 || lastAssistant < lastUser) return null;
  const message = recovery.history[lastAssistant];
  const input = lastUser < 0 ? '' : recovery.history[lastUser].content;
  return resultFrom(message.content, message.structured ?? {}, { prompt: input, kind: null }, true);
}
function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '助手连接失败，请恢复会话后核对。';
}
function validateRecovery(recovery: SessionRecovery, projectId: string): void {
  if (recovery.project_id !== projectId ||
    (recovery.session?.project_id != null && recovery.session.project_id !== projectId)) {
    throw new ApiError('ASSISTANT_PROJECT_MISMATCH', '返回的会话不属于当前草稿，已停止采用。', 0);
  }
}

/** A project change remounts the stateful component and aborts all old browser waits. */
export default function PromptOptimizer(props: PromptOptimizerProps) {
  return <PromptOptimizerSession key={props.projectId} {...props} />;
}

function PromptOptimizerSession({ projectId, prompt, kind, onApply }: PromptOptimizerProps) {
  const panelId = useId();
  const [expanded, setExpanded] = useState(false);
  const [phase, setPhase] = useState<Phase>('checking');
  const [status, setStatus] = useState<AssistantStatus | null>(null);
  const [session, setSession] = useState<AssistantSession | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [result, setResult] = useState<OptimizationResult | null>(null);
  const [edited, setEdited] = useState('');
  const [applyAcknowledgement, setApplyAcknowledgement] = useState<string | null>(null);
  const [needsRecovery, setNeedsRecovery] = useState(false);
  const [recoveryChecked, setRecoveryChecked] = useState(false);
  const [ready, setReady] = useState(false);
  const operation = useRef<AbortController | null>(null);
  const history = useRef('[]');
  const pending = useRef<PendingRequest | null>(null);
  const availability = assistantAvailability(status);
  const currentInput = JSON.stringify([prompt, kind]);
  const changed = !!result && (result.recovered || result.input.prompt !== prompt || result.input.kind !== kind);
  const busy = phase !== 'idle';
  const limits = session?.limits;
  const limitReached = typeof limits?.turns_max === 'number' && typeof limits.turns_used === 'number' && limits.turns_used >= limits.turns_max;
  const canSend = !busy && ready && availability.canChat && validText(prompt) && !limitReached && (!needsRecovery || recoveryChecked);
  const canApply = !busy && !needsRecovery && !availability.isStub && !!result?.optimized && validText(edited) && (!changed || applyAcknowledgement === currentInput);

  const showResult = useCallback((next: OptimizationResult) => {
    setResult(next);
    setEdited(next.optimized ?? '');
    setApplyAcknowledgement(null);
  }, []);

  const recover = useCallback(async (initial = false) => {
    if (operation.current) return;
    if (!validText(projectId)) {
      setError('请先保存或打开一个草稿，再使用提示词润色。');
      setPhase('idle');
      return;
    }
    const controller = new AbortController();
    operation.current = controller;
    setPhase(initial ? 'checking' : 'recovering');
    setError(null);
    try {
      const [statusResult, sessionResult] = await Promise.allSettled([
        assistantApi.status({ signal: controller.signal }),
        assistantApi.recoverSession(projectId, { signal: controller.signal }),
      ]);
      if (operation.current !== controller || controller.signal.aborted) return;
      if (statusResult.status === 'fulfilled') setStatus(statusResult.value);
      else setStatus(null);
      if (sessionResult.status === 'rejected') throw sessionResult.reason;
      const restored = sessionResult.value;
      validateRecovery(restored, projectId);
      setSession(restored.session);
      const serializedHistory = JSON.stringify(restored.history);
      const candidate = latestResult(restored);
      const waiting = pending.current;
      const restoredTurns = restored.session?.limits?.turns_used;
      const updated = waiting && (waiting.turns !== null && typeof restoredTurns === 'number'
        ? restoredTurns > waiting.turns : serializedHistory !== waiting.history);
      const matchesPending = !!candidate && !!waiting && updated && candidate.input.prompt === waiting.input.prompt;
      history.current = serializedHistory;
      if (waiting) {
        if (matchesPending && candidate) {
          showResult({ ...candidate, input: waiting.input });
          pending.current = null;
          setNeedsRecovery(false);
          setRecoveryChecked(false);
          setNotice('已从会话找回回复，请核对后采用。');
        } else {
          setNeedsRecovery(true);
          setRecoveryChecked(true);
          setNotice('尚未确认本次润色完成，服务端可能仍在处理。可稍后再查看；再次润色可能重复消耗文本模型用量。');
        }
      } else if (candidate) {
        showResult(candidate);
        setNeedsRecovery(false);
        setRecoveryChecked(false);
        setNotice('已恢复最近一次润色回复，请确认适用于当前输入。');
      } else if (restored.session?.intent === 'optimize_only' && restored.history.at(-1)?.role === 'user') {
        setNeedsRecovery(true);
        setRecoveryChecked(true);
        setNotice('会话中有一条尚无回复的消息。可稍后再查看，确认需要后再重新润色。');
      } else {
        setNeedsRecovery(false);
        if (!initial) setNotice('会话已恢复，没有新的润色结果。');
      }
      if (statusResult.status === 'rejected') throw statusResult.reason;
      setReady(true);
    } catch (caught) {
      if (operation.current !== controller || controller.signal.aborted) return;
      setReady(false);
      setError(errorMessage(caught));
    } finally {
      if (operation.current === controller) {
        operation.current = null;
        setPhase('idle');
      }
    }
  }, [projectId, showResult]);

  useEffect(() => {
    void recover(true);
    return () => {
      operation.current?.abort();
      operation.current = null;
    };
  }, [recover]);

  async function optimize() {
    if (!canSend || operation.current) return;
    const controller = new AbortController();
    operation.current = controller;
    const snapshot: InputSnapshot = { prompt, kind };
    setExpanded(true);
    setPhase('sending');
    setError(null);
    setNotice(null);
    setRecoveryChecked(false);
    pending.current = { input: snapshot, history: history.current, turns: session ? (session.limits?.turns_used ?? null) : 0 };
    try {
      let active = session;
      if (active && active.intent !== 'optimize_only') {
        active = await assistantApi.setIntent(active.session_id, 'optimize_only', '用户点击工作台润色提示词', { signal: controller.signal });
        if (operation.current !== controller || controller.signal.aborted) return;
        if (active.intent !== 'optimize_only' || (active.project_id != null && active.project_id !== projectId)) {
          throw new ApiError('ASSISTANT_INTENT_MISMATCH', '助手尚未切换到仅润色模式，请恢复会话核对。', 0);
        }
        setSession(active);
      }
      // Omitting session_id creates an optimize-only session on the backend's first chat.
      const response = await assistantApi.chat({
        project_id: projectId, ...(active ? { session_id: active.session_id } : {}),
        message: snapshot.prompt, intent: 'optimize_only', context: { kind },
      }, { signal: controller.signal });
      if (operation.current !== controller || controller.signal.aborted) return;
      if (response.intent !== 'optimize_only' || response.session.intent !== 'optimize_only' ||
        response.session_id !== response.session.session_id ||
        (active && response.session_id !== active.session_id) ||
        (response.session.project_id != null && response.session.project_id !== projectId)) {
        throw new ApiError('ASSISTANT_RESPONSE_MISMATCH', '回复与当前润色会话不匹配，请恢复会话核对。', 0);
      }
      setSession(response.session);
      showResult(resultFrom(response.reply, response, snapshot, false));
      pending.current = null;
      setNeedsRecovery(false);
      setNotice('润色已返回，采用后仍需由你点击生成。');
    } catch (caught) {
      if (operation.current !== controller) return;
      setError(errorMessage(caught));
      setNeedsRecovery(true);
      setRecoveryChecked(false);
    } finally {
      if (operation.current === controller) {
        operation.current = null;
        setPhase('idle');
      }
    }
  }

  function stopWaiting() {
    operation.current?.abort();
    setNotice('已停止本次等待。服务端可能仍在处理并产生文本模型用量，请先恢复会话核对结果。');
  }

  function applyResult() {
    if (!canApply) return;
    onApply(edited);
    setNotice('已采用到当前描述。你可以继续编辑，生成仍由你手动提交。');
  }

  return <section className="prompt-optimizer" aria-label="提示词润色">
    <div className="prompt-optimizer-header">
      <button type="button" className="prompt-optimizer-toggle" aria-expanded={expanded} aria-controls={panelId} onClick={() => setExpanded(value => !value)}>
        <Sparkles size={15} aria-hidden="true" /><span>提示词助手</span><small>可选</small><ChevronDown className={expanded ? 'is-open' : ''} size={14} aria-hidden="true" />
      </button>
      <button type="button" className="prompt-optimizer-run" disabled={!canSend} onClick={() => void optimize()}>
        {phase === 'sending' && <LoaderCircle size={14} className="spin" aria-hidden="true" />}
        {phase === 'sending' ? '润色中' : phase === 'checking' ? '检查中' : needsRecovery && recoveryChecked ? '仍要重新润色' : '润色提示词'}
      </button>
    </div>
    {!expanded && (needsRecovery || error || (phase === 'idle' && !availability.canChat)) &&
      <button type="button" className="prompt-optimizer-collapsed-note" onClick={() => setExpanded(true)}>{needsRecovery ? '需恢复会话核对结果' : error ? '连接异常，查看详情' : '助手暂不可用，继续手动创作'}<ChevronDown size={13} aria-hidden="true" /></button>}
    <div id={panelId} hidden={!expanded} className="prompt-optimizer-body">
      <p className="prompt-optimizer-help">只润色文字，不读取参考图；可能产生文本模型费用，不会自动生成图片或视频。</p>
      {phase === 'checking' && <p role="status">正在检查助手并读取当前草稿的会话…</p>}
      {status && !availability.canChat && <p className="prompt-optimizer-notice" role="status">{availability.reason}</p>}
      {availability.canChat && !availability.connectionVerified && <p className="prompt-optimizer-help">助手已配置，真实连接或能力尚未完整验证，请核对润色内容。</p>}
      {session?.intent && session.intent !== 'optimize_only' && <p className="prompt-optimizer-help">点击润色会把当前助手会话切换为“仅润色”。</p>}
      {!validText(prompt) && <p className="prompt-optimizer-help">先在描述框写下你的想法，再点击润色。</p>}
      {limitReached && <p className="prompt-optimizer-notice" role="status">当前助手会话已达轮次上限，请继续手动编辑。不会自动新建会话绕过限额。</p>}
      {error && <p className="prompt-optimizer-error" role="alert">{error}</p>}
      {notice && <p className="prompt-optimizer-notice" role="status">{notice}</p>}
      <div className="prompt-optimizer-utility">
        {phase === 'sending' ? <button type="button" onClick={stopWaiting}>停止等待</button> :
          <button type="button" disabled={busy || !validText(projectId)} onClick={() => void recover()}><RefreshCw size={13} className={phase === 'recovering' ? 'spin' : ''} aria-hidden="true" />{phase === 'recovering' ? '恢复会话中' : '恢复会话 / 查看结果'}</button>}
      </div>
      {result && <div className="prompt-optimizer-result">
        {changed && <div className="prompt-optimizer-context">
          <p>{result.recovered ? '这是恢复出的历史回复，输入或创作类型可能已变化。' : '当前描述或创作类型已变化，下方回复仍基于发送时的输入。'}</p>
          <details><summary>查看当时的输入</summary><p>{result.input.prompt || '历史记录未提供原始输入。'}</p></details>
        </div>}
        {result.explanation && <p className="prompt-optimizer-explanation">{result.explanation}</p>}
        {result.degraded && <p className="prompt-optimizer-help">本次回复存在格式降级，请检查内容。</p>}
        {result.optimized ? <>
          <label className="prompt-optimizer-editor-label" htmlFor={`${panelId}-edited`}>润色结果<span>可继续编辑</span></label>
          <textarea id={`${panelId}-edited`} value={edited} onChange={event => setEdited(event.target.value)} rows={5} spellCheck={false} />
          <details className="prompt-optimizer-full-reply"><summary>查看完整回复</summary><p>{result.reply || '助手没有返回说明文字。'}</p></details>
          {changed && <label className="prompt-optimizer-ack"><input type="checkbox" checked={applyAcknowledgement === currentInput} onChange={event => setApplyAcknowledgement(event.target.checked ? currentInput : null)} /><span>我已核对，将此结果替换到当前{kind === 'image' ? '生图' : '视频'}描述</span></label>}
          <button type="button" className="prompt-optimizer-apply" disabled={!canApply} onClick={applyResult}>采用到当前描述</button>
        </> : <>
          <p className="prompt-optimizer-help">本次没有可直接采用的提示词，请查看完整回复后手动完善描述。</p>
          <p className="prompt-optimizer-reply">{result.reply || '助手没有返回可展示的文字，请恢复会话核对。'}</p>
        </>}
      </div>}
    </div>
  </section>;
}
