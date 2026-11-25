import { useEffect, useRef, useState } from 'react';
import { api, post } from '../../api/client.ts';
import { imageUrl } from '../../api/media.ts';
import type { ChatSession, InteractiveModel, ManifestSummary, QaRow, RunItemDetail } from '../../api/types.ts';
import { useQaBrowser } from '../../hooks/useQaBrowser.ts';
import { fmtMs, fmtTimestamp } from '../../lib/format.ts';
import { ModelSelect, type ModelOptions } from '../interactive/ModelSelect.tsx';
import { GroundTruthMedia } from '../interactive/UnifiedMedia.tsx';
import { ExecutionView } from '../interactive/ExecutionView.tsx';
import { InteractiveModal } from '../interactive/InteractiveModal.tsx';

type Space = { id: string; name?: string; created_at?: string };
type Base = { scope_id: string; album_id: string; ready?: boolean };
const active = (status: string) => ['preparing', 'running', 'cancelling'].includes(status);
const stateLabel = (status: string) => ({ preparing: '准备模型', running: '回答中', cancelling: '正在取消', ready: '可继续提问', failed: '执行失败', cancelled: '已取消', outcome_unknown: '等待确认请求状态' }[status] || status);

function ChatExchange({ session, turn }: { session: ChatSession; turn: NonNullable<ChatSession['turns']>[number] }) {
  const [open, setOpen] = useState(false), [retry, setRetry] = useState(0);
  const [result, setResult] = useState<{ key: string; item?: RunItemDetail; error?: string }>();
  const key = `${session.session_id}:${turn.index}:${turn.status}:${String(turn.answer)}:${open}:${retry}`;
  const item = result?.key === key ? result.item : undefined;
  const error = result?.key === key ? result.error : undefined;
  const loading = (open || turn.answer === undefined) && result?.key !== key;
  useEffect(() => {
    if (!open && turn.answer !== undefined) return;
    const controller = new AbortController();
    void api<RunItemDetail>(`/chat/sessions/${encodeURIComponent(session.session_id)}/turns/${turn.index}`, { signal: controller.signal })
      .then(data => { if (!controller.signal.aborted) setResult({ key, item: data }); })
      .catch(reason => { if (!controller.signal.aborted) setResult({ key, error: (reason as Error).message }); });
    return () => controller.abort();
  }, [session.session_id, turn.index, turn.status, turn.answer, open, retry, key]);
  const running = ['pending', 'running'].includes(turn.status);
  return <article className="chat-exchange"><div className="chat-question"><small>提问 {turn.index + 1} · 推理{turn.reasoning === undefined ? '未记录' : turn.reasoning ? '开启' : '关闭'}</small><p>{turn.message}</p></div>
    <div className="chat-answer"><small>{session.model_name || '模型'} · {fmtMs(turn.wall_clock_ms ?? item?.wall_clock_ms)}</small>
      <p className="execution-answer">{String(turn.answer || item?.final_answer || item?.answer || turn.error || (running ? '正在回答…' : loading ? '正在读取回答…' : '未生成回答'))}</p>
      {error && <p role="alert" className="error">此回合读取失败：{error} <button type="button" onClick={() => setRetry(n => n + 1)}>重试</button></p>}
      <details open={open} onToggle={event => { if (event.target === event.currentTarget) setOpen(event.currentTarget.open); }}>
        <summary>执行轨迹与检索结果</summary>
        {open && <>{loading && <p role="status">正在读取轨迹…</p>}{item && <ExecutionView item={item} albumId={session.album_id} modelLabel={session.model_name || ''} running={running} hideAnswer/>}
          {running && !loading && <button type="button" onClick={() => setRetry(n => n + 1)}>刷新轨迹</button>}</>}
      </details>
    </div></article>;
}

export function ChatView({ sentrixUrl, manifests, models, busy, onOpenMedia }: {
  sentrixUrl: string; manifests: ManifestSummary[]; models: ModelOptions; busy: boolean;
  onOpenMedia: (target: { url: string; name: string }) => void;
}) {
  const [spaces, setSpaces] = useState<Space[]>([]), [bases, setBases] = useState<Base[]>([]);
  const [scope, setScope] = useState(''), [model, setModel] = useState<InteractiveModel>({});
  const [modelReady, setModelReady] = useState(true);
  const [reasoning, setReasoning] = useState(true);
  const [sessions, setSessions] = useState<ChatSession[]>([]), [session, setSession] = useState<ChatSession | null>(null);
  const [message, setMessage] = useState(''), [error, setError] = useState(''), [sending, setSending] = useState(false);
  const [conversationOpen, setConversationOpen] = useState(false), [closing, setClosing] = useState(false);
  const [loadingSession, setLoadingSession] = useState(false);
  const selectionRequest = useRef<AbortController | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState(''), [deletingId, setDeletingId] = useState('');
  const deletePending = useRef(false);
  const closeRequested = useRef(false), sendPromise = useRef<Promise<void> | null>(null);
  const sessionRef = useRef<ChatSession | null>(null);
  useEffect(() => { sessionRef.current = session; }, [session]);
  const [picker, setPicker] = useState(false), [selectedQa, setSelectedQa] = useState<QaRow | null>(null);
  const browser = useQaBrowser({ sentrixUrl, manifests });
  const selectedId = useRef('');
  const loadSessions = async () => { const data = await api<{ items: ChatSession[] }>('/chat/sessions'); setSessions(data.items); return data.items; };
  const select = async (id: string, openConversation = true) => {
    selectionRequest.current?.abort();
    const controller = new AbortController(); selectionRequest.current = controller;
    selectedId.current = id;
    setError(''); setLoadingSession(true); setSession(null);
    if (openConversation) setConversationOpen(true);
    try {
      const data = await api<ChatSession>(`/chat/sessions/${encodeURIComponent(id)}?include_items=false`, { signal: controller.signal });
      if (controller.signal.aborted || selectedId.current !== id) return;
      setReasoning(data.reasoning === true);
      setSession(data); setScope(data.scope_id); setModel({ profile: data.model_profile, base_url: String(data.runtime_snapshot?.model_base_url || ''), manager_url: String(data.runtime_snapshot?.vllm_manager_url || ''), endpoint_model: data.endpoint_model || data.model_name }); localStorage.setItem('photobench-chat-session', id);
    } catch (e) { if (!controller.signal.aborted && selectedId.current === id) setError((e as Error).message); }
    finally { if (!controller.signal.aborted && selectedId.current === id) setLoadingSession(false); }
  };
  useEffect(() => {
    let disposed = false;
    void (async () => {
      try {
        const [data, history] = await Promise.all([
          api<{ spaces: Space[]; reuse_bases: Base[] }>(`/memory-spaces?sentrix_url=${encodeURIComponent(sentrixUrl)}`), loadSessions(),
        ]);
        if (disposed) return;
        setSpaces(data.spaces); setBases(data.reuse_bases); setScope(data.spaces[0]?.id || '');
        const remembered = localStorage.getItem('photobench-chat-session');
        if (remembered && history.some(s => s.session_id === remembered)) void select(remembered, false);
      } catch (e) { if (!disposed) setError((e as Error).message); }
    })();
    return () => { disposed = true; selectedId.current = ''; selectionRequest.current?.abort(); };
  }, [sentrixUrl]); // oxlint-disable-line react-hooks/exhaustive-deps
  const id = session?.session_id;
  const status = session?.status || '';
  useEffect(() => {
    if (!id || !active(status)) return;
    let pending = false, disposed = false;
    const controller = new AbortController();
    const timer = window.setInterval(() => {
      if (pending) return;
      pending = true;
      void api<ChatSession>(`/chat/sessions/${encodeURIComponent(id)}?include_items=false`, { signal: controller.signal }).then(data => {
        if (!disposed && selectedId.current === id) { setSession(data); setError(''); }
        if (!active(data.status)) void loadSessions();
      }).catch((e: Error) => { if (!disposed) setError(e.message); }).finally(() => { pending = false; });
    }, 1200);
    return () => { disposed = true; controller.abort(); window.clearInterval(timer); };
  }, [id, status]); // oxlint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (picker && browser.qaBrowserAlbum && browser.qaBrowserOptions.includes(browser.qaBrowserSet)) void browser.loadQaBrowser();
  }, [picker, browser.qaBrowserAlbum, browser.qaBrowserSet]); // oxlint-disable-line react-hooks/exhaustive-deps
  const reset = () => { selectionRequest.current?.abort(); setLoadingSession(false); selectedId.current = ''; sessionRef.current = null; setSession(null); setSelectedQa(null); setReasoning(true); localStorage.removeItem('photobench-chat-session'); };
  const deleteSession = async (target: string) => {
    if (deletePending.current) return;
    deletePending.current = true; setDeletingId(target); setError('');
    try {
      await api(`/chat/sessions/${encodeURIComponent(target)}`, { method: 'DELETE' });
      setSessions(previous => previous.filter(s => s.session_id !== target));
      if (selectedId.current === target || sessionRef.current?.session_id === target) {
        reset(); setMessage(''); setConversationOpen(false);
      } else if (localStorage.getItem('photobench-chat-session') === target) {
        localStorage.removeItem('photobench-chat-session');
      }
      setConfirmDeleteId('');
    } catch (e) { setError(`删除失败：${(e as Error).message}`); }
    finally { deletePending.current = false; setDeletingId(''); }
  };
  const send = (row?: QaRow) => {
    if (sending || closing || deletingId || loadingSession || (!session && !modelReady)) return;
    setSending(true); setError(''); setPicker(false); setConversationOpen(true);
    sendPromise.current = (async () => {
    try {
      const result = session && !row
        ? await post<ChatSession>(`/chat/sessions/${encodeURIComponent(session.session_id)}/turns`, { message, reasoning })
        : await post<ChatSession>('/chat/sessions', { scope_id: scope, model, reasoning, ...(row ? {
          album_id: browser.qaBrowserAlbum, qa_set: browser.qaBrowserSet, qa_id: row.qa_id,
        } : { message }) });
      sessionRef.current = result; selectedId.current = result.session_id; setSession(result); setMessage(''); setPicker(false); setSelectedQa(null);
      localStorage.setItem('photobench-chat-session', result.session_id); await loadSessions();
    } catch (e) { setError((e as Error).message); }
    finally { setSending(false); }
    })();
  };
  const closeConversation = async () => {
    if (closeRequested.current) return;
    closeRequested.current = true; setClosing(true);
    try {
      await sendPromise.current;
      selectionRequest.current?.abort(); setLoadingSession(false);
      const current = sessionRef.current;
      if (current && active(current.status)) {
        await post(`/chat/sessions/${encodeURIComponent(current.session_id)}/cancel`, {});
        setSession({ ...current, status: 'cancelling' });
      }
      setConversationOpen(false);
    } catch (e) { setError(`取消失败：${(e as Error).message}`); }
    finally { closeRequested.current = false; setClosing(false); }
  };
  const sourceAlbum = bases.find(b => b.scope_id === scope)?.album_id;
  const source = session?.source;
  const openPicker = () => {
    const album = sourceAlbum || browser.qaBrowserAlbum;
    const sets = manifests.find(m => m.album_id === album)?.qa_sets || browser.qaBrowserOptions;
    const preferred = source?.album_id === album ? source.qa_set : browser.qaBrowserSet;
    browser.setQaBrowserAlbum(album);
    browser.setQaBrowserSet(sets.includes(preferred) ? preferred : sets[0] || '');
    setSelectedQa(null); setPicker(true);
  };
  const ownActive = Boolean(session && active(session.status));
  const conversation = <>
        {loadingSession && <p role="status">正在读取对话消息…</p>}
        {conversationOpen && error && <p className="error" role="alert">{error}</p>}
        {session && <div className="chat-state"><strong>{session.model_name || '模型准备中'}</strong><span>{stateLabel(session.status)}</span><details><summary>运行配置</summary><pre>{JSON.stringify({ model: session.model_identity, configuration: session.runtime_snapshot, preparation: session.model_preparation }, null, 2)}</pre></details></div>}
        {session?.error && <p role="alert" className="error">{session.error}</p>}
        {source && <details className="chat-source" open><summary>题集对照 · {source.row.qa_id} · {source.qa_set}</summary><p>{source.row.question}</p><h4>标准答案</h4><p>{source.row.answer}</p><GroundTruthMedia item={source.row as RunItemDetail} albumId={source.album_id} fromDataset onOpenMedia={media => onOpenMedia({ url: imageUrl(media), name: String(media.file_name || media.media_id || '') })}/></details>}
        {busy && !ownActive && <p className="muted small">测评任务正在使用服务，可查看历史；空闲后可继续提问。</p>}
        <div className="chat-messages">{session?.turns?.map(turn => <ChatExchange key={`${session.session_id}:${turn.turn_id}`} session={session} turn={turn}/>)}{!session && !loadingSession && <p className="chat-empty">输入问题开始对话，也可以从题集中选择一道题。</p>}</div>
        <form className="chat-composer" onSubmit={e => { e.preventDefault(); void send(); }}>
          <textarea aria-label="聊天问题" placeholder="输入问题，支持连续追问…" maxLength={2000} value={message} disabled={loadingSession || ownActive || sending || closing || Boolean(deletingId)} onChange={e => setMessage(e.target.value)}/>
          <div className="chat-composer-actions">{conversationOpen && <label><input type="checkbox" aria-label="聊天推理模式" checked={reasoning} disabled={ownActive || sending || closing} onChange={e => setReasoning(e.target.checked)}/> 开启推理模式</label>}<button className="btn ghost" type="button" disabled={!scope || ownActive || sending || busy || closing} onClick={openPicker}>从题集选题</button>
            {ownActive ? <button type="button" className="btn ghost" onClick={() => { void post(`/chat/sessions/${encodeURIComponent(id!)}/cancel`, {}).then(() => select(id!)).catch(e => setError((e as Error).message)); }}>停止回答</button> : <button className="btn" disabled={loadingSession || !scope || !message.trim() || sending || busy || closing || Boolean(deletingId) || (!session && !modelReady)}>{sending ? '正在发送…' : '发送'}</button>}
          </div>
        </form>
  </>;
  return <section className="chat-view">
    <div className="section-head"><div><h2>聊天对话</h2><p className="muted">选择相册与模型，自由提问或运行题集中的一道题。</p></div><button className="btn ghost" disabled={ownActive || sending || Boolean(deletingId)} onClick={reset}>新建对话</button></div>
    {!conversationOpen && error && <p className="error" role="alert">{error}</p>}
    <div className="chat-controls">
      <label>相册<select aria-label="聊天相册" value={scope} disabled={ownActive || sending || Boolean(deletingId)} onChange={e => { reset(); setScope(e.target.value); }}><option value="">选择相册</option>{spaces.map(s => <option key={s.id} value={s.id}>{s.name || s.id}{s.created_at ? ` · ${fmtTimestamp(s.created_at)}` : ''}</option>)}</select></label>
      {loadingSession ? null : session ? <p className="muted small">会话模型：{session.model_name || session.endpoint_model || session.model_profile || '已保存的运行配置'}</p> : <ModelSelect options={models} value={model} onChange={value => { reset(); setModel(value); }} onReadyChange={setModelReady} disabled={ownActive || sending || Boolean(deletingId)}/>}
      {!conversationOpen && <label><span>推理模式</span><span><input type="checkbox" aria-label="聊天推理模式" checked={reasoning} disabled={ownActive || sending}
        onChange={e => setReasoning(e.target.checked)}/> 开启推理模式</span></label>}
    </div>
    <div className="chat-layout"><aside className="chat-history"><h3>会话记录</h3>{sessions.map(s => <div key={s.session_id} className="chat-history-entry">
      <button className={'chat-history-item' + (s.session_id === id ? ' selected' : '')} disabled={sending || ownActive || Boolean(deletingId)} onClick={() => { setConfirmDeleteId(''); void select(s.session_id); }}><strong>{s.title}</strong><small>{s.model_name || '当前模型'} · {stateLabel(s.status)}</small></button>
      <button type="button" className="chat-history-delete" aria-label={`删除对话：${s.title}`} title={active(s.status) || s.status === 'outcome_unknown' ? '请先停止回答，等待回合结束后再删除' : '删除对话'} disabled={sending || Boolean(deletingId) || active(s.status) || s.status === 'outcome_unknown'} onClick={() => { setConfirmDeleteId(s.session_id); setError(''); }}>删除</button>
      {confirmDeleteId === s.session_id && <div className="chat-delete-confirm" role="group" aria-label={`确认删除对话：${s.title}`}><p>删除此对话及消息、轨迹？此操作无法撤销。</p><div><button type="button" className="btn danger" disabled={Boolean(deletingId)} onClick={() => void deleteSession(s.session_id)}>{deletingId === s.session_id ? '正在删除…' : '确认删除'}</button><button type="button" className="btn ghost" disabled={Boolean(deletingId)} onClick={() => setConfirmDeleteId('')}>取消</button></div></div>}
    </div>)}{!sessions.length && <p className="muted">暂无记录</p>}</aside>
      <div className="chat-main">
        {!conversationOpen && (session ? <button className="btn ghost" onClick={() => setConversationOpen(true)}>打开对话与轨迹</button> : conversation)}
      </div>
    </div>
    {conversationOpen && <InteractiveModal title="聊天对话与执行轨迹" onClose={() => void closeConversation()} closing={closing}><p className="muted small">关闭弹窗会取消当前未完成的回答，保留对话与日志。</p>{closing && <p role="status">正在取消并关闭…</p>}{conversation}</InteractiveModal>}
    {picker && <InteractiveModal title="从题集选题" onClose={() => setPicker(false)}>
      <div className="chat-controls"><label>数据集<select value={browser.qaBrowserAlbum} onChange={e => { browser.setQaBrowserAlbum(e.target.value); setSelectedQa(null); }}>{manifests.map(m => <option key={m.album_id} value={m.album_id}>{m.album_name || m.album_id}</option>)}</select></label><label>题集<select value={browser.qaBrowserSet} onChange={e => { browser.setQaBrowserSet(e.target.value); setSelectedQa(null); }}>{browser.qaBrowserOptions.map(s => <option key={s} value={s}>{s}</option>)}</select></label></div>
      <input aria-label="搜索聊天题目" placeholder="搜索题号、问题、答案或标签" value={browser.qaBrowserSearch} onChange={e => browser.setQaBrowserSearch(e.target.value)}/>
      {browser.qaBrowserError && <p role="alert">{browser.qaBrowserError}</p>}
      {sourceAlbum !== browser.qaBrowserAlbum && <p>请先选择与该数据集对应的相册。</p>}
      <div className="chat-qa-list">{browser.qaBrowserLoading ? <p>加载题集…</p> : browser.visibleQaBrowserItems.map(row => <button className={'chat-qa-option' + (selectedQa?.qa_id === row.qa_id ? ' selected' : '')} key={row.qa_id} onClick={() => setSelectedQa(row)}><strong>{row.qa_id}</strong> {row.question}</button>)}</div>
      {selectedQa && <div className="chat-qa-preview"><h4>{selectedQa.question}</h4>{selectedQa.conversation && <pre>{JSON.stringify(selectedQa.conversation, null, 2)}</pre>}<p>标准答案：{selectedQa.answer}</p><GroundTruthMedia item={selectedQa as RunItemDetail} albumId={browser.qaBrowserAlbum} fromDataset onOpenMedia={media => onOpenMedia({ url: imageUrl(media), name: String(media.file_name || media.media_id || '') })}/></div>}
      {error && <p className="error" role="alert">{error}</p>}<button className="btn" disabled={!selectedQa || sourceAlbum !== browser.qaBrowserAlbum || sending || busy || !modelReady} onClick={() => void send(selectedQa!)}>运行此题（新对话）</button>
    </InteractiveModal>}
  </section>;
}
