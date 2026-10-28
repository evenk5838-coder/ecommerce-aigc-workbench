import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowUp, ArrowUpRight, Check, FileText, LoaderCircle, MessageSquare, PanelLeftClose, RefreshCw, Sparkles } from 'lucide-react';
import { ApiError } from '../api';
import { workflowDirectorApi, workflowNodeIds, workflowUserAction, type PendingWorkflowRequest, type WorkflowAction, type WorkflowActionPending, type WorkflowPendingRequest, type WorkflowReply, type WorkflowRole, type WorkflowTurn } from './director-workflow-api';
import { parseStoredWorkflowConversation, presentWorkflowTurn, readWorkflowConversation, workflowRoleLabel, writeWorkflowConversation, type StoredWorkflowConversation } from './workflow-conversation-state';
import { mergeFailedDirectorDraft, directorNavigationState } from './director-draft-state';
import { actionCanConfirm, actionSubmissionState, actionWithinPendingRequest, isMissingWorkflowRequest, mergeWorkflowActions, retryMissingWorkflowConversation } from './director-action-view';
import WorkflowActionCard from './WorkflowActionCard';
import { workflowFailedDraftScope, workflowMessageNodeIds, type WorkflowDraftScope } from './workflow-draft-scope';
import './DirectorChat.css';

interface DirectorChatProps {
  projectId: string; disabled?: boolean; selectedNodeIds?: string[];
  nodeTitles?: Readonly<Record<string, string>>;
  /** Increment only for a user's explicit canvas selection, never an automatic reveal. */
  selectionVersion?: number;
  /** Kept for an older workspace caller; the actual role always comes from the server. */
  role?: WorkflowRole;
  onBusyChange?: (busy: boolean) => void; onDirtyChange?: (dirty: boolean) => void;
  onRoleChange?: (role: WorkflowRole) => void; onProgress?: () => void | Promise<void>;
  onRefresh?: () => void | Promise<void>; onRevealNode?: (id: string) => void; onCollapse?: () => void;
}
const mergeTurns = (current: WorkflowTurn[], incoming: WorkflowTurn[]) => {
  const items = new Map(current.map(turn => [turn.request_id, turn]));
  for (const turn of incoming) items.set(turn.request_id, turn);
  return [...items.values()].sort((a, b) => a.created_at - b.created_at);
};
const message = (error: unknown, fallback: string) => error instanceof Error ? error.message : fallback;
const knownRejection = (error: unknown) => error instanceof ApiError && error.status >= 400 && error.status < 500
  && !['DIRECTOR_REQUEST_PENDING', 'IDEMPOTENCY_CONFLICT', 'DIRECTOR_ACTION_CONFLICT'].includes(error.code);
const roleDescription: Record<WorkflowRole, string> = { writer: '把想法写成故事', director: '一起打磨镜头与画面', cinematographer: '让分镜里的画面动起来', editor: '把镜头连成你的作品' };
function initial(projectId: string) {
  try { return { value: readWorkflowConversation(localStorage, projectId), error: '' }; }
  catch { return { value: parseStoredWorkflowConversation(null, projectId), error: '暂时无法读取本地对话，请保持页面开启并重新读取。' }; }
}
export default function DirectorChat(props: DirectorChatProps) { return <ProjectConversation key={props.projectId} {...props}/>; }

function ProjectConversation({ projectId, disabled = false, selectedNodeIds = [], nodeTitles, selectionVersion = 0, onBusyChange, onDirtyChange, onRefresh, onRevealNode, onCollapse, onRoleChange, onProgress }: DirectorChatProps) {
  const [start] = useState(() => initial(projectId));
  const [stored, setStored] = useState(start.value), current = useRef(stored);
  const [phase, setPhase] = useState<'idle' | 'loading' | 'sending' | 'confirming' | 'history'>('loading');
  const [ready, setReady] = useState(false), [error, setError] = useState(''), [storageError, setStorageError] = useState(start.error);
  const [syncNotice, setSyncNotice] = useState(''), [cursor, setCursor] = useState<string | null>(null), [requestMissing, setRequestMissing] = useState(false);
  const storageReadFailed = useRef(!!start.error);
  const scrollRef = useRef<HTMLDivElement>(null), inputRef = useRef<HTMLTextAreaElement>(null), followConversation = useRef(true);
  const operation = useRef<AbortController | null>(null), poll = useRef<AbortController | null>(null), epoch = useRef(0), mounted = useRef(true);
  const snapshotSequence = useRef(0);
  const selection = useRef(selectedNodeIds), disabledRef = useRef(disabled);
  const draftScopeVersion = useRef(selectionVersion);
  const revealedArtifacts = useRef(new Set(stored.turns.flatMap(turn => workflowNodeIds(turn.structured))));
  const selectionKey = JSON.stringify(selectedNodeIds);
  selection.current = selectedNodeIds; disabledRef.current = disabled;
  const callbacks = useRef({ onBusyChange, onDirtyChange, onRefresh, onRevealNode, onRoleChange, onProgress });
  callbacks.current = { onBusyChange, onDirtyChange, onRefresh, onRevealNode, onRoleChange, onProgress };
  const store = useCallback((value: StoredWorkflowConversation) => {
    current.current = value; setStored(value);
    // If the existing cache could not be read, do not overwrite an unseen draft.
    if (storageReadFailed.current) return false;
    try { writeWorkflowConversation(localStorage, value); setStorageError(''); return true; }
    catch { setStorageError('对话暂未备份，请保持页面开启；恢复保存前不会发送新操作。'); return false; }
  }, []);
  const refreshCanvas = useCallback(async (nodeIds: string[] = [], signal?: AbortSignal) => {
    const revealSelection = JSON.stringify(selection.current);
    try {
      await callbacks.current.onRefresh?.();
      if (mounted.current && !signal?.aborted && !disabledRef.current && revealSelection === JSON.stringify(selection.current) && nodeIds[0]) callbacks.current.onRevealNode?.(nodeIds[0]);
    } catch { if (mounted.current) setSyncNotice('内容已保留，画布暂未同步。点击重新读取即可继续查看。'); }
  }, []);
  const acknowledge = useCallback((reply: WorkflowReply, pending: PendingWorkflowRequest) => {
    const turn: WorkflowTurn = { turn_id: reply.request_id, request_id: reply.request_id, user: pending.message, reply: reply.reply,
      reply_type: reply.reply_type, created_at: Date.now() / 1000, structured: { flow_version: pending.endpoint === 'conversation' ? 2 : 1,
        artifacts: reply.artifacts, actions: reply.actions, outcome: reply.outcome, ...(reply.current_role ? { current_role: reply.current_role } : {}) } };
    const sameSelection = pending.endpoint === 'conversation' && JSON.stringify(pending.selected_node_ids) === JSON.stringify(selection.current);
    store({ ...current.current, pending: null, turns: mergeTurns(current.current.turns, [turn]),
      actions: mergeWorkflowActions(current.current.actions, reply.actions), sessionId: reply.session_id ?? current.current.sessionId,
      workflowRevision: reply.workflow_revision, currentRole: sameSelection && reply.current_role ? reply.current_role : current.current.currentRole,
      ...(reply.outcome.status === 'failed' ? { draft: mergeFailedDirectorDraft(current.current.draft, pending.message), draftScope: workflowFailedDraftScope(pending, current.current.draftScope) } : {}) });
    epoch.current += 1;
  }, [store]);
  const acceptAction = useCallback((action: WorkflowAction) => {
    const pending = current.current.actionPending;
    const resolved = pending && actionSubmissionState(action, pending) !== 'unconfirmed';
    store({ ...current.current, actions: mergeWorkflowActions(current.current.actions, [action]),
      ...(resolved ? { actionPending: null } : {}),
      ...(!selection.current.length && action.current_role ? { currentRole: action.current_role } : {}),
      ...(action.workflow_revision !== undefined ? { workflowRevision: action.workflow_revision } : {}) });
    if (resolved && pending && actionSubmissionState(action, pending) === 'other') setSyncNotice('此方案已在另一处确认，已读取原任务。');
  }, [store]);
  const readSnapshot = useCallback(async (signal?: AbortSignal) => {
    const selected = [...selection.current], readEpoch = epoch.current, sequence = ++snapshotSequence.current;
    const results = await Promise.allSettled([
      workflowDirectorApi.state(projectId, selected, { signal }),
      workflowDirectorApi.messages(projectId, undefined, { signal }),
    ]);
    if (signal?.aborted || !mounted.current || readEpoch !== epoch.current || sequence !== snapshotSequence.current) return;
    const state = results[0], history = results[1];
    let value = current.current;
    if (state.status === 'fulfilled') {
      const data = state.value;
      value = { ...value, actions: mergeWorkflowActions(value.actions, data.actions), sessionId: data.session_id ?? value.sessionId, workflowRevision: data.workflow_revision,
        ...(JSON.stringify(selected) === JSON.stringify(selection.current) ? { currentRole: data.current_role } : {}) };
      if (JSON.stringify(selected) === JSON.stringify(selection.current)) setReady(true);
    }
    if (history.status === 'fulfilled') {
      value = { ...value, turns: mergeTurns(value.turns, history.value.items) };
      setCursor(history.value.has_more ? history.value.next_cursor : null);
    }
    store(value);
    if (state.status === 'rejected' || history.status === 'rejected') {
      const failure = state.status === 'rejected' ? state.reason : history.status === 'rejected' ? history.reason : null;
      setSyncNotice(message(failure, '会话暂未同步，已保留现有内容。'));
    } else setSyncNotice('');
  }, [projectId, store]);
  const readProgress = useCallback(async (signal: AbortSignal) => {
    const pending = current.current.pending;
    let changed = false, savedContent = false, reveal: string[] = [];
    if (pending) {
      try {
        const receipt = await (pending.endpoint === 'legacy' ? workflowDirectorApi.legacyReceipt : workflowDirectorApi.receipt)(projectId, pending.id, { signal });
        if (signal.aborted || !mounted.current) return;
        setRequestMissing(false);
        store({ ...current.current, actions: mergeWorkflowActions(current.current.actions, receipt.actions) });
        if (receipt.status !== 'pending') {
          if (receipt.response) { acknowledge(receipt.response, pending); reveal = workflowNodeIds(receipt.response); savedContent = true; }
          else { store({ ...current.current, pending: null, draft: mergeFailedDirectorDraft(current.current.draft, pending.message), draftScope: workflowFailedDraftScope(pending, current.current.draftScope) }); setError('上一条消息未完成，内容已保留，可以修改后再发送。'); }
          changed = true;
        } else if (receipt.artifacts.length) {
          reveal = workflowNodeIds({ artifacts: receipt.artifacts }).filter(id => !revealedArtifacts.current.has(id));
          for (const id of reveal) revealedArtifacts.current.add(id);
          if (reveal.length) { changed = true; savedContent = true; }
        }
        if (pending.endpoint === 'conversation' && JSON.stringify(pending.selected_node_ids) !== JSON.stringify(selection.current)) reveal = [];
      } catch (caught) {
        if (signal.aborted) return;
        setRequestMissing(isMissingWorkflowRequest(caught));
        setSyncNotice(message(caught, '上次消息还需核对，请稍后重新读取。'));
      }
    }
    const actionIds = [...new Set([
      ...current.current.actions.filter(action => action.status === 'running').map(action => action.action_id),
      ...(current.current.actionPending ? [current.current.actionPending.action_id] : []),
    ])];
    const results = await Promise.allSettled(actionIds.map(id => workflowDirectorApi.action(projectId, id, { signal })));
    if (signal.aborted || !mounted.current) return;
    for (const result of results) {
      if (result.status === 'fulfilled') {
        const before = current.current.actions.find(action => action.action_id === result.value.action_id);
        if (JSON.stringify(before) !== JSON.stringify(result.value)) changed = true;
        acceptAction(result.value);
      } else setSyncNotice(message(result.reason, '进度暂未更新，已保留原任务。'));
    }
    if (changed) {
      if (savedContent) await refreshCanvas(reveal, signal);
      else { try { await callbacks.current.onProgress?.(); } catch { setSyncNotice('画布进度暂未同步，原任务会继续保留。'); } }
      if (!signal.aborted) await readSnapshot(signal);
    }
  }, [projectId, store, acknowledge, acceptAction, refreshCanvas, readSnapshot]);
  const recover = useCallback(async () => {
    if (operation.current) return;
    poll.current?.abort(); const controller = new AbortController(); operation.current = controller; epoch.current += 1;
    setPhase('loading'); setError('');
    try { await readProgress(controller.signal); if (!controller.signal.aborted) { await readSnapshot(controller.signal); await refreshCanvas(); } }
    catch (caught) { if (!controller.signal.aborted) setError(message(caught, '会话暂时无法读取，请稍后重试。')); }
    finally { if (operation.current === controller) { operation.current = null; if (mounted.current) setPhase('idle'); } }
  }, [readProgress, readSnapshot, refreshCanvas]);

  useEffect(() => { mounted.current = true; void recover(); return () => { mounted.current = false; operation.current?.abort(); poll.current?.abort(); operation.current = null; }; }, [recover]);
  useEffect(() => {
    setReady(false);
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const refreshSelection = () => {
      if (controller.signal.aborted) return;
      if (operation.current) timer = setTimeout(refreshSelection, 300);
      else void readSnapshot(controller.signal);
    };
    timer = setTimeout(refreshSelection, 100);
    return () => { clearTimeout(timer); controller.abort(); };
  }, [selectionKey, readSnapshot]);
  useEffect(() => {
    if (draftScopeVersion.current === selectionVersion) return;
    draftScopeVersion.current = selectionVersion;
    if (current.current.draftScope) {
      store({ ...current.current, draftScope: undefined });
      setSyncNotice('已改用你在画布中选择的节点，输入内容仍保留。');
    }
  }, [selectionVersion, store]);
  const { busy: pendingWrite, dirty } = directorNavigationState(stored, !!storageError);
  useEffect(() => { callbacks.current.onBusyChange?.(pendingWrite); }, [pendingWrite]);
  useEffect(() => { if (stored.currentRole) callbacks.current.onRoleChange?.(stored.currentRole); }, [stored.currentRole]);
  useEffect(() => { callbacks.current.onDirtyChange?.(dirty); }, [dirty]);
  useEffect(() => () => { callbacks.current.onBusyChange?.(false); callbacks.current.onDirtyChange?.(false); }, []);
  useEffect(() => { if (followConversation.current && scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight; }, [stored.turns, stored.pending, stored.actions.length, phase]);
  const hasRunning = stored.actions.some(action => action.status === 'running');
  useEffect(() => {
    if (!stored.pending && !stored.actionPending && !hasRunning) return;
    let canceled = false, timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      if (canceled) return;
      if (!operation.current) {
        const controller = new AbortController(); poll.current = controller;
        try { await readProgress(controller.signal); } catch { if (!controller.signal.aborted) setSyncNotice('进度暂未同步，稍后会继续查询。'); }
        finally { if (poll.current === controller) poll.current = null; }
      }
      if (!canceled) timer = setTimeout(() => void tick(), 3500);
    };
    timer = setTimeout(() => void tick(), 2500);
    return () => { canceled = true; clearTimeout(timer); poll.current?.abort(); };
  }, [!!stored.pending, !!stored.actionPending, hasRunning, readProgress]);

  async function send() {
    const value = current.current;
    if (disabled || phase !== 'idle' || !ready || value.pending || value.actionPending || value.legacyActionPending || !value.draft.trim() || operation.current || storageError) return;
    const pending: WorkflowPendingRequest = { endpoint: 'conversation', id: crypto.randomUUID(), message: value.draft.trim(), selected_node_ids: workflowMessageNodeIds(null, draftScopeVersion.current === selectionVersion ? value.draftScope : undefined, selection.current),
      ...(value.sessionId ? { session_id: value.sessionId } : {}), ...(value.workflowRevision !== null ? { revision: value.workflowRevision } : {}) };
    if (!store({ ...value, pending, draft: '', draftScope: undefined })) { store({ ...value }); return; }
    poll.current?.abort(); epoch.current += 1; followConversation.current = true;
    const controller = new AbortController(); operation.current = controller; setPhase('sending'); setError(''); setSyncNotice('');
    let acknowledged = false;
    try {
      const reply = await workflowDirectorApi.conversation(projectId, pending, { signal: controller.signal });
      if (controller.signal.aborted || !mounted.current) return;
      acknowledge(reply, pending); acknowledged = true;
      await refreshCanvas(JSON.stringify(pending.selected_node_ids) === JSON.stringify(selection.current) ? workflowNodeIds(reply) : [], controller.signal);
      if (!controller.signal.aborted) await readSnapshot(controller.signal);
    } catch (caught) {
      if (controller.signal.aborted) return;
      if (!acknowledged && knownRejection(caught)) store({ ...current.current, pending: null, draft: mergeFailedDirectorDraft(current.current.draft, pending.message), draftScope: workflowFailedDraftScope(pending, current.current.draftScope) });
      setError(message(caught, '结果还需核对，请读取原操作。'));
    } finally { if (operation.current === controller) { operation.current = null; if (mounted.current) setPhase('idle'); } }
  }
  async function retryOriginalMessage() {
    const pending = current.current.pending;
    if (disabled || phase !== 'idle' || !requestMissing || pending?.endpoint !== 'conversation' || current.current.actionPending || current.current.legacyActionPending || operation.current || storageError) return;
    poll.current?.abort(); epoch.current += 1;
    const controller = new AbortController(); operation.current = controller; setPhase('sending'); setError('');
    let posted = false;
    try {
      const result = await retryMissingWorkflowConversation({ ...workflowDirectorApi,
        conversation: (id, original, options) => { posted = true; return workflowDirectorApi.conversation(id, original, options); },
      }, projectId, pending, controller.signal);
      if (controller.signal.aborted || !mounted.current) return;
      setRequestMissing(false);
      let nodeIds: string[] = [];
      if (result.kind === 'reply') { acknowledge(result.reply, pending); nodeIds = workflowNodeIds(result.reply); }
      else {
        const receipt = result.receipt;
        store({ ...current.current, actions: mergeWorkflowActions(current.current.actions, receipt.actions) });
        if (receipt.response && receipt.status !== 'pending') { acknowledge(receipt.response, pending); nodeIds = workflowNodeIds(receipt.response); }
        else if (receipt.status === 'failed') {
          store({ ...current.current, pending: null, draft: mergeFailedDirectorDraft(current.current.draft, pending.message), draftScope: workflowFailedDraftScope(pending, current.current.draftScope) });
          setError('上一条消息未完成，内容已保留，可以修改后再发送。');
        } else {
          nodeIds = workflowNodeIds({ artifacts: receipt.artifacts });
          setSyncNotice('已找到原消息，继续读取它的结果。');
        }
      }
      await refreshCanvas(JSON.stringify(pending.selected_node_ids) === JSON.stringify(selection.current) ? nodeIds : [], controller.signal);
      if (!controller.signal.aborted) await readSnapshot(controller.signal);
    } catch (caught) {
      if (controller.signal.aborted) return;
      // Read errors never prove that the original write was rejected.
      if (posted && knownRejection(caught)) {
        store({ ...current.current, pending: null, draft: mergeFailedDirectorDraft(current.current.draft, pending.message), draftScope: workflowFailedDraftScope(pending, current.current.draftScope) });
        setRequestMissing(false);
      } else if (!posted) setRequestMissing(false);
      setError(message(caught, '原消息还需核对，已保留输入和请求记录。'));
    } finally { if (operation.current === controller) { operation.current = null; if (mounted.current) setPhase('idle'); } }
  }
  async function confirm(action: WorkflowAction, replay = false) {
    if (disabled || phase !== 'idle' || !actionWithinPendingRequest(action, current.current.pending) || current.current.legacyActionPending || operation.current || storageError) return;
    const existing = current.current.actionPending;
    if (existing && (!replay || existing.action_id !== action.action_id)) return;
    if (!existing && !actionCanConfirm(action)) return;
    const pending: WorkflowActionPending = existing ?? { action_id: action.action_id, kind: action.kind, client_request_id: crypto.randomUUID(), user_action: workflowUserAction(action.kind) };
    poll.current?.abort(); epoch.current += 1;
    const controller = new AbortController(); operation.current = controller; setPhase('confirming'); setError('');
    let posted = false;
    try {
      const fresh = await workflowDirectorApi.action(projectId, action.action_id, { signal: controller.signal });
      if (controller.signal.aborted) return;
      acceptAction(fresh);
      if (fresh.action_status === 'submitted') { await refreshCanvas(); await readSnapshot(controller.signal); return; }
      if (!existing && !actionCanConfirm(fresh)) { setError('方案已有变化，请查看当前内容后重新准备。'); return; }
      // Freeze the original confirmation ID before the only write. A replay is explicit and uses this same ID.
      if (!store({ ...current.current, actionPending: pending })) return;
      posted = true;
      const result = await workflowDirectorApi.confirm(projectId, pending, { signal: controller.signal });
      if (controller.signal.aborted) return;
      acceptAction(result); epoch.current += 1;
      await refreshCanvas(result.kind === 'confirm_script' ? result.node_ids : [], controller.signal);
      await readSnapshot(controller.signal);
    } catch (caught) {
      if (controller.signal.aborted) return;
      if (posted && knownRejection(caught)) store({ ...current.current, actionPending: null });
      setError(message(caught, '确认结果还需核对，请重新读取原方案。'));
    } finally { if (operation.current === controller) { operation.current = null; if (mounted.current) setPhase('idle'); } }
  }
  async function more() {
    if (!cursor || operation.current) return;
    const controller = new AbortController(); operation.current = controller; setPhase('history');
    try {
      const history = await workflowDirectorApi.messages(projectId, cursor, { signal: controller.signal });
      if (!controller.signal.aborted) { followConversation.current = false; store({ ...current.current, turns: mergeTurns(current.current.turns, history.items) }); setCursor(history.has_more ? history.next_cursor : null); }
    } catch (caught) { if (!controller.signal.aborted) setError(message(caught, '暂时无法读取更早的对话。')); }
    finally { if (operation.current === controller) { operation.current = null; if (mounted.current) setPhase('idle'); } }
  }
  function suggest(text: string, scope?: WorkflowDraftScope) {
    draftScopeVersion.current = selectionVersion;
    store({ ...current.current, draft: current.current.draft.trim() ? `${current.current.draft}\n${text}` : text, draftScope: scope }); inputRef.current?.focus();
  }
  function retryStorage() {
    if (!storageReadFailed.current) { store({ ...current.current }); return; }
    try {
      const recovered = readWorkflowConversation(localStorage, projectId);
      storageReadFailed.current = false;
      store({ ...current.current, draft: mergeFailedDirectorDraft(current.current.draft, recovered.draft),
        draftScope: current.current.draftScope ?? recovered.draftScope,
        pending: recovered.pending ?? current.current.pending, actionPending: recovered.actionPending ?? current.current.actionPending,
        legacyActionPending: recovered.legacyActionPending || current.current.legacyActionPending,
        turns: mergeTurns(recovered.turns, current.current.turns), actions: mergeWorkflowActions(recovered.actions, current.current.actions) });
    } catch { setStorageError('暂时仍无法读取本地对话，已保留页面中的输入。'); }
  }
  const busy = disabled || phase !== 'idle' || pendingWrite || !!storageError;
  const role = stored.currentRole, roleName = workflowRoleLabel(role);
  const ids = new Set(stored.actions.map(action => action.action_id));
  const actions = stored.actions.filter(action => !action.generation_action_id || !ids.has(action.generation_action_id));
  const turnRequests = new Set(stored.turns.map(turn => turn.request_id));
  const pendingAction = stored.actions.find(action => action.action_id === stored.actionPending?.action_id);
  const actionCard = (action: WorkflowAction) => <WorkflowActionCard key={action.action_id} action={action} nodeTitles={nodeTitles} disabled={disabled || phase !== 'idle' || !!stored.actionPending || stored.legacyActionPending || !!storageError || !actionWithinPendingRequest(action, stored.pending)} confirming={phase === 'confirming' && stored.actionPending?.action_id === action.action_id} onConfirm={value => void confirm(value)} onReveal={id => callbacks.current.onRevealNode?.(id)} onSuggest={suggest}/>;
  return <section className="director-chat" aria-label="AI 创作对话">
    <header className="dc-header"><div className="dc-title"><span><Sparkles size={17}/></span><div><h2>{roleName}</h2><p>{role ? roleDescription[role] : '从想法，到你的作品'}</p></div></div><div className="dc-header-actions"><button type="button" className="dc-recover" disabled={phase !== 'idle'} onClick={() => void recover()} aria-label="重新读取创作会话" title="重新读取"><RefreshCw size={15}/></button>{onCollapse && <button type="button" onClick={onCollapse} aria-label="收起创作对话" title="收起对话"><PanelLeftClose size={17}/></button>}</div></header>
    <div className="dc-scroll" ref={scrollRef} onScroll={() => { const element = scrollRef.current; if (element) followConversation.current = element.scrollHeight - element.scrollTop - element.clientHeight < 64; }}>
      <div className="dc-messages" role="log" aria-label="项目对话" aria-live="polite">
        {cursor && <button type="button" className="dc-history" disabled={phase !== 'idle'} onClick={() => void more()}>查看更早的对话</button>}
        {!stored.turns.length && !stored.pending && <div className="dc-empty"><span><MessageSquare size={27} strokeWidth={1.4}/></span><h3>{role === 'cinematographer' ? '让这个画面动起来。' : role === 'editor' ? '把片段连成一个故事。' : '这次，想拍些什么？'}</h3><p>{role === 'cinematographer' ? '说说动作、运镜与节奏，一起准备视频。' : role === 'editor' ? '告诉我片段的顺序，我们一起完成作品。' : '说说你的商品、故事或一个画面。\n我们一步一步把它做出来。'}</p><div className="dc-starters"><button type="button" disabled={busy} onClick={() => suggest(role === 'cinematographer' ? '请根据我选中的图片，准备视频生成方案。' : role === 'editor' ? '请根据我选中的视频，和我确认顺序后准备合成方案。' : '根据目前的想法，帮我写一版脚本。')}><FileText size={14}/>{role === 'cinematographer' ? '从这张图片开始' : role === 'editor' ? '整理已有片段' : '从一个想法开始'}<ArrowUpRight size={13}/></button></div></div>}
        {stored.turns.map(turn => {
          const view = presentWorkflowTurn(turn);
          return <div className="dc-turn" key={turn.request_id}><div className="dc-message dc-user"><span className="dc-speaker">我</span><p>{turn.user}</p></div><div className={`dc-message dc-assistant dc-result-${view.outcome.status}`}><span className="dc-speaker">{view.role}</span><p>{view.text}</p>{!!view.nodeIds.length && <div className="dc-artifacts">{view.nodeIds.map((id, index) => <button type="button" key={id} onClick={() => onRevealNode?.(id)}><Check size={12}/>{id === 'script' ? '查看脚本' : `查看${view.nodeIds.length > 1 ? `第 ${index + 1} 个` : ''}画布内容`}<ArrowUpRight size={12}/></button>)}</div>}</div>{actions.filter(action => action.request_id === turn.request_id).map(actionCard)}</div>;
        })}
        {stored.pending && !turnRequests.has(stored.pending.id) && <div className="dc-message dc-user dc-message-pending"><span className="dc-speaker">我</span><p>{stored.pending.message}</p></div>}
        {phase === 'sending' && <p className="dc-working" role="status"><LoaderCircle size={14} className="dc-spin"/>{roleName}正在处理…</p>}
        {actions.filter(action => !action.request_id || !turnRequests.has(action.request_id)).map(actionCard)}
      </div>
      {error && <p className="dc-error" role="alert">{error}</p>}{storageError && <div className="dc-error" role="alert">{storageError}<button type="button" onClick={retryStorage}>重试保存对话</button></div>}
      {syncNotice && <p className="dc-notice" role="status">{syncNotice}</p>}
      {(stored.pending || stored.actionPending) && phase === 'idle' && <div className="dc-pending"><p>{stored.pending?.endpoint === 'legacy' ? '正在核对旧版对话，不会重新发送。' : requestMissing ? '还未查到上次消息。可以继续读取，或用原消息重试；请求编号和内容都会保持不变。' : '上次操作的结果还需核对。已保存内容和输入会保留。'}</p><button type="button" onClick={() => void recover()}>读取原操作</button>{requestMissing && stored.pending?.endpoint === 'conversation' && <button type="button" disabled={disabled || !!storageError || !!stored.actionPending || stored.legacyActionPending} onClick={() => void retryOriginalMessage()}>用原消息重试</button>}{stored.actionPending && pendingAction?.action_status === 'proposed' && <button type="button" disabled={disabled || !!storageError} onClick={() => void confirm(pendingAction, true)}>用原确认继续核对</button>}</div>}
      {stored.legacyActionPending && <div className="dc-pending"><p>有一项旧版操作需要核对。请查看画布和会话中的当前结果。</p><button type="button" disabled={phase !== 'idle'} onClick={() => void recover()}>读取当前结果</button><button type="button" disabled={phase !== 'idle'} onClick={() => { store({ ...current.current, legacyActionPending: false }); setSyncNotice('已结束旧版等待；之后从当前内容继续。'); }}>已核对，继续对话</button></div>}
    </div>
    <form className="dc-composer" onSubmit={event => { event.preventDefault(); void send(); }}>
      {stored.draftScope ? <div className="dc-selection"><span/>{stored.draftScope.label} · {stored.draftScope.nodeIds.length} 个节点<button type="button" onClick={() => { store({ ...current.current, draftScope: undefined }); setSyncNotice('已改用当前画布选择，输入内容仍保留。'); }}>改用画布选择</button></div> : !!selectedNodeIds.length && <div className="dc-selection"><span/>围绕选中的 {selectedNodeIds.length} 个节点继续</div>}
      <label className="dc-input-label" htmlFor={`director-message-${projectId}`}>你的创作想法</label><textarea ref={inputRef} id={`director-message-${projectId}`} rows={3} maxLength={20000} value={stored.draft} placeholder="说说你的想法，或想调整的细节…" onChange={event => store({ ...current.current, draft: event.target.value, ...(!event.target.value.trim() ? { draftScope: undefined } : {}) })} onKeyDown={event => { if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); void send(); } }}/><div className="dc-composer-actions"><span>Enter 发送 · Shift 换行</span>{phase === 'sending' ? <button type="button" className="dc-stop" onClick={() => { operation.current?.abort(); setError('已停止等待。结果仍会保留，请读取原操作核对。'); }}>停止等待</button> : <button type="submit" className="dc-send" disabled={busy || !ready || !stored.draft.trim()}>发送<ArrowUp size={16}/></button>}</div>
    </form>
  </section>;
}
