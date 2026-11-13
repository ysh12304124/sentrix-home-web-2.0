import { useEffect, useState, type FormEvent } from 'react';
import { request } from '../api/client';
import './memory-correction.css';

type Kind = 'observation' | 'event' | 'entity';
type Memory = { id: string; scope_id: string; revision: number; [key: string]: unknown };

const fields: Record<Kind, Record<string, string>> = {
  observation: {
    caption: '观察描述',
    place: '地点',
    activity: '活动',
    event_type: '事件类型',
    ocr_text: '图中文字',
    objects: '相关物件',
    clothing: '衣着',
  },
  event: {
    title: '事件标题',
    summary: '事件摘要',
    place: '地点',
    activity: '活动',
    event_type: '事件类型',
    time_start: '开始时间',
    time_end: '结束时间',
  },
  entity: { canonical_name: '实体名称', summary: '实体说明' },
};

const longFields = new Set(['caption', 'summary', 'ocr_text', 'objects', 'clothing']);

export function CorrectionHistory({ scopeId }: { scopeId: string }) {
  const [items, setItems] = useState<Record<string, unknown>[] | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function load() {
    setError('');
    try {
      const result = await request<{ items: Record<string, unknown>[] }>(
        `/memories/corrections?scope_id=${encodeURIComponent(scopeId)}`,
      );
      setItems(result.items);
    } catch (cause) {
      setError((cause as Error).message);
    }
  }

  async function retry() {
    setBusy(true);
    setError('');
    try {
      const result = await request<{ error: string }>(
        `/memories/corrections/retry?scope_id=${encodeURIComponent(scopeId)}`,
        { method: 'POST' },
      );
      await load();
      if (result.error) setError(result.error);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <details
      className="memory-correction-history"
      onToggle={(event) => {
        if (event.currentTarget.open && items === null) void load();
      }}
    >
      <summary>
        <span>修正记录</span>
        {items ? <small>{items.length} 条</small> : null}
      </summary>
      <div className="memory-history-content">
        <div className="memory-history-actions">
          <p>查看修改内容及图谱、检索索引的同步状态。</p>
          <div>
            <button type="button" className="memory-button memory-button-quiet" disabled={busy} onClick={() => void load()}>
              刷新
            </button>
            <button type="button" className="memory-button memory-button-quiet" disabled={busy} onClick={() => void retry()}>
              {busy ? '同步中…' : '重试待同步项'}
            </button>
          </div>
        </div>
        {error ? <p className="memory-feedback is-error" role="alert">{error}</p> : null}
        {items?.length === 0 ? <p className="memory-history-empty">这个资料库还没有记忆修正记录。</p> : null}
        {items?.length ? (
          <ol className="memory-history-list">
            {items.map((item) => (
              <li key={String(item.id)}>
                <div className="memory-history-row">
                  <div>
                    <strong>{kindLabel(String(item.target_kind))}修正</strong>
                    <small>{formatDate(String(item.created_at ?? ''))} · {String(item.target_id ?? '')}</small>
                  </div>
                  <span className={`memory-status ${item.status === 'synced' ? 'is-synced' : 'is-pending'}`}>
                    {item.status === 'synced' ? '已同步' : '待同步'}
                  </span>
                </div>
                {item.reason ? <p className="memory-history-reason">原因：{String(item.reason)}</p> : null}
                {item.error ? <p className="memory-history-error">{String(item.error)}</p> : null}
                <details className="memory-history-diff">
                  <summary>查看修改前后</summary>
                  <div className="memory-diff-grid">
                    <div><span>修正前</span><pre>{JSON.stringify(item.before, null, 2)}</pre></div>
                    <div><span>修正后</span><pre>{JSON.stringify(item.after, null, 2)}</pre></div>
                  </div>
                </details>
              </li>
            ))}
          </ol>
        ) : null}
      </div>
    </details>
  );
}

export function MemoryCorrection({
  kind,
  targetId,
  onSaved,
}: {
  kind: Kind;
  targetId: string;
  onSaved?: () => void | Promise<void>;
}) {
  const [open, setOpen] = useState(false);
  const label = kind === 'event' ? '修正事件记忆' : kind === 'entity' ? '修正实体记忆' : '修正观察记忆';

  return (
    <div className={`memory-correction ${open ? 'is-open' : ''}`}>
      <button
        type="button"
        className="memory-correction-toggle"
        aria-expanded={open}
        onClick={() => setOpen((value) => !value)}
      >
        <span className="memory-correction-icon" aria-hidden="true">✎</span>
        <span>{open ? '收起编辑' : label}</span>
        <span className="memory-correction-chevron" aria-hidden="true">{open ? '−' : '+'}</span>
      </button>
      {open ? <CorrectionForm key={`${kind}:${targetId}`} kind={kind} targetId={targetId} onSaved={onSaved} /> : null}
    </div>
  );
}

function CorrectionForm({
  kind,
  targetId,
  onSaved,
}: {
  kind: Kind;
  targetId: string;
  onSaved?: () => void | Promise<void>;
}) {
  const [memory, setMemory] = useState<Memory | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [reason, setReason] = useState('');
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    request<Memory>(`/memories/${kind}/${encodeURIComponent(targetId)}`)
      .then((record) => {
        if (!alive) return;
        setMemory(record);
        setValues(Object.fromEntries(Object.keys(fields[kind]).map((key) => [
          key,
          Array.isArray(record[key]) ? record[key].join('、') : String(record[key] ?? ''),
        ])));
      })
      .catch((cause: Error) => { if (alive) setMessage(cause.message); })
      .finally(() => { if (alive) setLoading(false); });
    return () => { alive = false; };
  }, [kind, targetId]);

  async function save(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!memory) return;
    setBusy(true);
    setMessage('');
    const changes: Record<string, string | string[] | null> = {};
    for (const [key, value] of Object.entries(values)) {
      const parsed = ['objects', 'clothing'].includes(key)
        ? value.split(/[、,，]/).map((part) => part.trim()).filter(Boolean)
        : key.startsWith('time_') && !value ? null : value;
      if (JSON.stringify(parsed) !== JSON.stringify(memory[key] ?? (key.startsWith('time_') ? null : ''))) {
        changes[key] = parsed;
      }
    }
    if (!Object.keys(changes).length) {
      setMessage('内容没有变化');
      setBusy(false);
      return;
    }
    try {
      const result = await request<{ revision: number; sync_status: string; error: string }>(
        `/memories/${kind}/${encodeURIComponent(targetId)}`,
        {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ revision: memory.revision, changes, reason }),
        },
      );
      setMemory({ ...memory, ...changes, revision: result.revision });
      setMessage(result.sync_status === 'synced'
        ? '记忆已保存，图谱与检索索引已同步。'
        : `记忆已保存，派生数据待同步：${result.error}`);
      await onSaved?.();
    } catch (cause) {
      setMessage((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="memory-correction-form" aria-label="修正记忆">
      {loading ? <p className="memory-form-loading">正在读取当前记忆…</p> : null}
      {memory ? (
        <form onSubmit={(event) => void save(event)}>
          <div className="memory-form-heading">
            <div>
              <h3>编辑{kind === 'event' ? '事件' : kind === 'entity' ? '实体' : '观察'}记忆</h3>
              <p>保存后会同步更新关联图谱和检索索引。</p>
            </div>
            <span className="memory-revision">版本 {memory.revision}</span>
          </div>
          <div className="memory-fields">
            {Object.entries(fields[kind]).map(([key, label]) => (
              <label className={longFields.has(key) ? 'is-long' : ''} key={key}>
                <span>{label}</span>
                {longFields.has(key) ? (
                  <textarea
                    value={values[key] ?? ''}
                    disabled={busy}
                    rows={key === 'caption' || key === 'summary' ? 3 : 2}
                    placeholder={key === 'objects' || key === 'clothing' ? '多个内容用顿号分隔' : '填写更正后的内容'}
                    onChange={(event) => setValues({ ...values, [key]: event.target.value })}
                  />
                ) : (
                  <input
                    value={values[key] ?? ''}
                    disabled={busy}
                    placeholder={key.startsWith('time_') ? '例如：2026-11-13T10:30:00+08:00' : '填写更正后的内容'}
                    onChange={(event) => setValues({ ...values, [key]: event.target.value })}
                  />
                )}
              </label>
            ))}
          </div>
          <label className="memory-reason-field">
            <span>修正原因 <small>可选</small></span>
            <input value={reason} disabled={busy} placeholder="例如：照片中实际是……" onChange={(event) => setReason(event.target.value)} />
          </label>
          <div className="memory-form-footer">
            {message ? (
              <p className={`memory-feedback ${message.includes('已保存') && !message.includes('待同步') ? 'is-success' : message.includes('待同步') || message.includes('失败') ? 'is-error' : ''}`} role="status">
                {message}
              </p>
            ) : <span className="memory-form-hint">只提交有变化的字段。</span>}
            <button type="submit" className="memory-button memory-button-primary" disabled={busy}>
              {busy ? '保存并同步中…' : '保存修正'}
            </button>
          </div>
        </form>
      ) : null}
      {!loading && !memory && message ? <p className="memory-feedback is-error" role="alert">{message}</p> : null}
      {memory ? <CorrectionHistory scopeId={memory.scope_id} /> : null}
    </section>
  );
}

export function AssetMemoryCorrection({ assetId, compact = false }: { assetId: string; compact?: boolean }) {
  const [items, setItems] = useState<Memory[] | null>(null);
  const [error, setError] = useState('');
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);

  async function toggle() {
    if (open) {
      setOpen(false);
      return;
    }
    setOpen(true);
    setError('');
    if (items !== null) return;
    setBusy(true);
    try {
      setItems((await request<{ items: Memory[] }>(`/assets/${encodeURIComponent(assetId)}/memories`)).items);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className={`asset-memory-correction ${compact ? 'is-compact' : ''} ${open ? 'is-open' : ''}`}>
      <button type="button" className="asset-memory-correction-toggle" aria-expanded={open} onClick={() => void toggle()}>
        <span className="memory-correction-icon" aria-hidden="true">✎</span>
        <span className="asset-memory-copy">
          <strong>{open ? '收起媒体记忆' : compact ? '修正关联记忆' : '修正媒体记忆'}</strong>
          {!compact ? <small>查看并编辑这张媒体及其关键帧关联的观察</small> : null}
        </span>
        <span className="memory-correction-chevron" aria-hidden="true">{open ? '−' : '+'}</span>
      </button>
      {open ? (
        <div className="asset-memory-content">
          {busy ? <p className="memory-form-loading">正在读取关联记忆…</p> : null}
          {error ? <p className="memory-feedback is-error" role="alert">{error}</p> : null}
          {items?.length === 0 ? <p className="memory-history-empty">此媒体尚未构建观察记忆。</p> : null}
          {items?.map((item, index) => (
            <article className="asset-memory-item" key={item.id}>
              <div className="asset-memory-item-heading">
                <span>观察 {index + 1}</span>
                <small>{String(item.captured_at || item.id)}</small>
              </div>
              <p>{String(item.caption || '暂无描述')}</p>
              <MemoryCorrection kind="observation" targetId={item.id} />
            </article>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function kindLabel(kind: string): string {
  if (kind === 'event') return '事件';
  if (kind === 'entity' || kind === 'face') return '实体';
  if (kind === 'observation') return '观察';
  if (kind === 'family') return '家庭关系';
  return kind;
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
}
