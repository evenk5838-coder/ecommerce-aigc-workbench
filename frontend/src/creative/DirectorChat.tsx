import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { ArrowUp, Check, ChevronDown, LoaderCircle, MessageSquare, RefreshCw, Sparkles } from 'lucide-react';
import { ApiError } from '../api';
import { assistantApi } from '../assistant/api';
import { assistantAvailability } from '../assistant/guards';
import type { AssistantStatus } from '../assistant/types';
import {
  assertDirectorRecovery as assertProject, directorChatInput, directorCount as count, directorHistory as visibleHistory,
  directorStorageKey as storageKey, directorText as validText, emptyDirectorCache as blankCache, parseDirectorCache,
  recoveredDirectorReply as recoveredReply, type DirectorChatCache as ChatCache, type DirectorPending as Pending,
} from './director-chat-state';
import './DirectorChat.css';

export interface DirectorChatProps {
  projectId: string;
  disabled?: boolean;
  onBusyChange?: (busy: boolean) => void;
  onRefresh?: () => void;
  children?: ReactNode;
}
type Phase = 'checking' | 'recovering' | 'sending' | 'idle';
const errorText = (error: unknown) => error instanceof Error ? error.message : '\u8fde\u63a5\u6682\u65f6\u4e2d\u65ad\uff0c\u8bf7\u6062\u590d\u4f1a\u8bdd\u6838\u5bf9\u3002';

function readCache(projectId: string): { cache: ChatCache; error: string } {
  const empty = { cache: blankCache(projectId), error: '' };
  if (!validText(projectId)) return empty;
  try {
    return { cache: parseDirectorCache(localStorage.getItem(storageKey(projectId)), projectId), error: '' };
  } catch {
    return { cache: empty.cache, error: '本机聊天记录暂时无法读取。请先恢复会话，再继续发送。' };
  }
}
export default function DirectorChat(props: DirectorChatProps) {
  return <DirectorChatSession key={props.projectId} {...props} />;
}

function DirectorChatSession({ projectId, disabled = false, onBusyChange, onRefresh, children }: DirectorChatProps) {
  const [initial] = useState(() => readCache(projectId));
  const [cache, setCache] = useState(initial.cache);
  const cacheRef = useRef(initial.cache);
  const [storageError, setStorageError] = useState(initial.error);
  const [phase, setPhase] = useState<Phase>('checking');
  const [status, setStatus] = useState<AssistantStatus | null>(null);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [recoveryChecked, setRecoveryChecked] = useState(false);
  const [acknowledged, setAcknowledged] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const operation = useRef<AbortController | null>(null);
  const callbacks = useRef({ onBusyChange, onRefresh });
  const availability = assistantAvailability(status);
  const used = cache.session?.limits?.turns_used, maximum = cache.session?.limits?.turns_max;
  const limitReached = count(used) && count(maximum) && used >= maximum;
  const busy = phase !== 'idle' || !!cache.pending;
  const canSend = !disabled && phase === 'idle' && ready && !cache.pending && !storageError &&
    availability.canChat && !limitReached && validText(cache.draft);

  useEffect(() => { callbacks.current = { onBusyChange, onRefresh }; }, [onBusyChange, onRefresh]);
  useEffect(() => { callbacks.current.onBusyChange?.(busy); }, [busy]);
  useEffect(() => () => { callbacks.current.onBusyChange?.(false); }, []);
  useEffect(() => {
    if (phase !== 'sending') return;
    const started = Date.now();
    const timer = window.setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, [phase]);

  const store = useCallback((next: ChatCache): boolean => {
    cacheRef.current = next;
    setCache(next);
    try {
      localStorage.setItem(storageKey(projectId), JSON.stringify(next));
      setStorageError('');
      return true;
    } catch {
      setStorageError('本机暂时无法保存聊天状态。为避免重复发送，请先恢复会话再试。');
      return false;
    }
  }, [projectId]);

  const recover = useCallback(async (initialLoad = false) => {
    if (operation.current) return;
    if (!validText(projectId)) {
      setError('请先新建或打开一个项目，再与 AI 导演讨论。');
      setPhase('idle');
      return;
    }
    const controller = new AbortController();
    operation.current = controller;
    setPhase(initialLoad ? 'checking' : 'recovering');
    setReady(false); setError(''); setAcknowledged(false); setRecoveryChecked(false);
    try {
      const [providerResult, sessionResult] = await Promise.allSettled([
        assistantApi.status({ signal: controller.signal }),
        assistantApi.recoverSession(projectId, { signal: controller.signal }),
      ]);
      if (operation.current !== controller || controller.signal.aborted) return;
      if (providerResult.status === 'fulfilled') {
        setStatus(providerResult.value);
        if (assistantAvailability(providerResult.value).isStub) throw new ApiError('ASSISTANT_TEST_PROVIDER', '当前为测试替身，未采用会话回复。', 0);
      } else setStatus(null);
      if (sessionResult.status === 'rejected') throw sessionResult.reason;
      const restored = sessionResult.value;
      assertProject(restored, projectId);
      const current = cacheRef.current, history = visibleHistory(restored.history);
      let pending = current.pending;
      if (pending) {
        if (recoveredReply(restored, pending, projectId)) {
          pending = null;
          setNotice('已找回本次回复，可以继续讨论。');
        } else {
          setNotice('尚未确认本次请求有新回复。服务端可能仍在处理，请稍后恢复核对。');
        }
      } else if (history.at(-1)?.role === 'user') {
        // An unfinished server-side turn is also a blocker after a fresh browser load.
        pending = {
          message: history.at(-1)!.content, sentAt: Date.now(),
          sessionId: restored.session?.session_id ?? null,
          turns: count(restored.session?.limits?.turns_used) ? restored.session.limits.turns_used : null,
          before: history,
        };
        setNotice('会话中还有一条未见回复的消息，请先核对处理结果。');
      } else if (!initialLoad) setNotice('已恢复当前项目的最近对话。');
      store({ ...current, session: restored.session, history, pending });
      setRecoveryChecked(true);
      if (providerResult.status === 'rejected') throw providerResult.reason;
      setReady(true);
      if (!initialLoad) callbacks.current.onRefresh?.();
    } catch (caught) {
      if (operation.current !== controller || controller.signal.aborted) return;
      setError(errorText(caught));
    } finally {
      if (operation.current === controller) { operation.current = null; setPhase('idle'); }
    }
  }, [projectId, store]);

  useEffect(() => {
    void recover(true);
    return () => { operation.current?.abort(); operation.current = null; };
  }, [recover]);

  async function send() {
    if (!canSend || operation.current || cacheRef.current.pending) return;
    const current = cacheRef.current;
    let input;
    try { input = directorChatInput(projectId, current); }
    catch (caught) { setReady(false); setError(errorText(caught)); return; }
    const message = input.message, session = current.session;
    const pending: Pending = {
      message, sentAt: Date.now(), sessionId: session?.session_id ?? null,
      turns: session ? (count(session.limits?.turns_used) ? session.limits.turns_used : null) : 0,
      before: current.history,
    };
    // Persist the guard before the only potentially paid request.
    if (!store({ ...current, pending })) return;
    const controller = new AbortController();
    operation.current = controller;
    setPhase('sending'); setElapsed(0); setError(''); setNotice('');
    setRecoveryChecked(false); setAcknowledged(false);
    store({ ...cacheRef.current, draft: '' });
    try {
      const response = await assistantApi.chat(input, { signal: controller.signal });
      if (operation.current !== controller || controller.signal.aborted) return;
      if (response.session.project_id !== projectId || response.session_id !== response.session.session_id ||
        (session && response.session_id !== session.session_id) || response.intent !== 'auto' || response.session.intent !== 'auto') {
        throw new ApiError('DIRECTOR_REPLY_MISMATCH', '回复与当前项目会话不匹配，请恢复核对。', 0);
      }
      if (!validText(response.reply)) throw new ApiError('DIRECTOR_REPLY_EMPTY', '助手未返回可展示的回复，请恢复核对。', 0);
      const latest = cacheRef.current;
      store({ ...latest, session: response.session, history: [...current.history, { role: 'user', content: message }, { role: 'assistant', content: response.reply }], pending: null });
      setNotice(response.degraded ? '本次回复有格式降级，已按文字建议展示。' : '');
      callbacks.current.onRefresh?.();
    } catch (caught) {
      if (operation.current !== controller) return;
      const code = caught instanceof ApiError ? caught.code : '';
      if (code === 'ASSISTANT_TURN_LIMIT' || code === 'ASSISTANT_UNAVAILABLE') {
        const latest = cacheRef.current;
        store({ ...latest, pending: null, draft: latest.draft || message });
        setReady(false);
      }
      setError(errorText(caught));
      if (cacheRef.current.pending) setNotice('未自动重发。请用“恢复会话”核对，停止等待不会取消服务端处理。');
    } finally {
      if (operation.current === controller) { operation.current = null; setPhase('idle'); }
    }
  }

  function releasePending() {
    if (!cacheRef.current.pending || !recoveryChecked || !acknowledged || phase !== 'idle') return;
    store({ ...cacheRef.current, pending: null });
    setAcknowledged(false); setRecoveryChecked(false);
    setNotice('已按你的核对解除等待状态，没有重新发送上一条消息。服务端处理仍可能结束并返回历史。');
    setError('');
  }

  const pendingMessageInHistory = !!cache.pending && cache.history.at(-1)?.role === 'user' && cache.history.at(-1)?.content === cache.pending.message;
  return <section className="director-chat" aria-label="AI 导演对话">
    <header className="dc-header"><div className="dc-title"><span><Sparkles size={17} aria-hidden="true" /></span><div><h2>AI 导演</h2><p>一起把想法说清楚</p></div></div>
      <button type="button" className="dc-recover" disabled={phase !== 'idle' || !validText(projectId)} onClick={() => void recover()}><RefreshCw size={14} className={phase === 'recovering' ? 'dc-spin' : ''} aria-hidden="true" />恢复会话</button>
    </header>
    <p className="dc-boundary">先聊清创作方向，再从下方「创作资料与操作」起草脚本。</p>
    <div className="dc-scroll">
      <div className="dc-messages" role="log" aria-label="当前项目的最近对话" aria-live="polite" aria-relevant="additions">
        {!cache.history.length && !cache.pending && <div className="dc-empty"><span><MessageSquare size={27} strokeWidth={1.4} aria-hidden="true" /></span><h3>你好，有什么拍摄想法？</h3><p>可以从商品、想传达的感觉，<br />或一个还不完整的灵感说起。</p></div>}
        {cache.history.map((message, index) => <div className={'dc-message dc-' + message.role} key={index}><span className="dc-speaker">{message.role === 'user' ? '我' : 'AI 导演'}</span><p>{message.content}</p></div>)}
        {cache.pending && !pendingMessageInHistory && <div className="dc-message dc-user dc-message-pending"><span className="dc-speaker">我 · {phase === 'sending' ? '等待回复' : '待核对'}</span><p>{cache.pending.message}</p></div>}
        {phase === 'sending' && <p className="dc-working" role="status"><LoaderCircle size={14} className="dc-spin" />正在思考{elapsed >= 20 ? ` · 已等待 ${elapsed} 秒` : '…'}</p>}
      </div>
      {phase === 'checking' && <p className="dc-note" role="status">正在检查助手并恢复项目对话…</p>}
      {phase === 'idle' && status && !availability.canChat && <p className="dc-notice" role="status">{availability.reason}</p>}
      {availability.canChat && !availability.connectionVerified && <p className="dc-note">当前连接尚未完成真实验证，请核对建议内容。</p>}
      {limitReached && <p className="dc-notice" role="status">本会话已达轮次上限，可继续使用下方创作操作。</p>}
      {storageError && <p className="dc-error" role="alert">{storageError}</p>}
      {error && <p className="dc-error" role="alert">{error}</p>}
      {notice && <p className="dc-notice" role="status">{notice}</p>}
      {cache.pending && phase === 'idle' && <div className="dc-pending">
        <p>结果未核对前，暂不发送新消息。</p>
        {recoveryChecked && <><label><input type="checkbox" checked={acknowledged} onChange={event => setAcknowledged(event.target.checked)} /><span>我已核对历史，理解旧请求可能仍在执行；解除状态不会重发。</span></label><button type="button" disabled={!acknowledged} onClick={releasePending}><Check size={14} />解除待核对状态</button></>}
      </div>}
      {children && <details className="dc-materials"><summary>创作资料与操作<ChevronDown size={15} aria-hidden="true" /></summary><div className="dc-materials-content">{children}</div></details>}
    </div>
    <form className="dc-composer" onSubmit={event => { event.preventDefault(); void send(); }}>
      <label htmlFor={'director-message-' + projectId} className="dc-input-label">你的拍摄想法</label>
      <textarea id={'director-message-' + projectId} value={cache.draft} rows={3} placeholder="说说你想拍什么…" onChange={event => store({ ...cacheRef.current, draft: event.target.value })} onKeyDown={event => {
        if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send(); }
      }} />
      <div className="dc-composer-actions"><span>{count(used) && count(maximum) ? `会话 ${used} / ${maximum} 轮` : 'Enter 发送 · Shift 换行'}</span>
        {phase === 'sending' ? <button type="button" className="dc-stop" onClick={() => { operation.current?.abort(); setNotice('已停止等待；服务端可能仍在处理，请恢复核对。'); }}>停止等待</button> : <button type="submit" className="dc-send" disabled={!canSend}><span>发送</span><ArrowUp size={16} aria-hidden="true" /></button>}
      </div>
      <p className="dc-cost-note">发送可能产生文本模型用量，不会自动生成图片或视频。</p>
    </form>
  </section>;
}
