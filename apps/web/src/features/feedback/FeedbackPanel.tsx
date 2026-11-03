import { useEffect, useRef, useState } from "react";

import { newClientId } from "../../shared/id";

import { useAuth } from "../../app/AuthProvider";
import { useScope } from "../../app/ScopeProvider";
import "./feedback.css";
import { csrfToken } from "../../shared/csrf";
import {
  clearFeedbackDraft,
  clearLegacyFeedbackDraft,
  readFeedbackDraft,
  saveFeedbackDraft,
} from "./draft";

interface FeedbackRecord {
  id: string;
  kind: string;
  body: string;
  status: string;
  reply: string;
  created_at: string;
}

export function FeedbackPanel({ onClose }: { onClose: () => void }) {
  const { identity } = useAuth();
  const { scopeId } = useScope();
  const accountId = identity?.account_id ?? null;
  const [kind, setKind] = useState("problem");
  const [body, setBody] = useState("");
  const [screenshot, setScreenshot] = useState<File | null>(null);
  const [preview, setPreview] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [records, setRecords] = useState<FeedbackRecord[]>([]);
  const keyRef = useRef("");

  useEffect(() => {
    clearLegacyFeedbackDraft();
    if (accountId === null) {
      // 身份未确认前不读任何用户草稿。
      setBody("");
      setRecords([]);
      return;
    }
    let cancelled = false;
    setBody(readFeedbackDraft(accountId));
    setMessage("");
    setRecords([]);
    void fetch("/control/v1/feedback", { credentials: "same-origin" })
      .then(async (response) =>
        response.ok ? ((await response.json()) as { items: FeedbackRecord[] }).items : [],
      )
      .then((items) => {
        if (!cancelled) setRecords(items);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [accountId]);

  useEffect(() => {
    keyRef.current = newClientId("fb");
  }, [accountId]);

  useEffect(() => {
    if (screenshot === null) {
      setPreview("");
      return;
    }
    const url = URL.createObjectURL(screenshot);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [screenshot]);

  const submit = async () => {
    if (body.trim() === "") return;
    setBusy(true);
    setMessage("");
    try {
      const response = await fetch("/control/v1/feedback", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": keyRef.current,
          "x-csrf-token": csrfToken(),
        },
        body: JSON.stringify({
          kind,
          body: body.trim(),
          route: window.location.pathname,
          scope_id: scopeId,
        }),
      });
      if (!response.ok) throw new Error(`提交失败（${response.status}）`);
      const created = (await response.json()) as { id: string };
      if (screenshot !== null && confirmed) {
        const form = new FormData();
        form.append("file", screenshot);
        await fetch(`/control/v1/feedback/${created.id}/attachment`, {
          method: "POST",
          credentials: "same-origin",
          headers: { "x-csrf-token": csrfToken() },
          body: form,
        });
      }
      if (accountId !== null) clearFeedbackDraft(accountId);
      setBody("");
      setScreenshot(null);
      setConfirmed(false);
      setMessage(`已收到，反馈编号 ${created.id}`);
      keyRef.current = newClientId("fb");
      const list = await fetch("/control/v1/feedback", { credentials: "same-origin" });
      if (list.ok) setRecords(((await list.json()) as { items: FeedbackRecord[] }).items);
    } catch (cause) {
      if (accountId !== null) saveFeedbackDraft(accountId, body);
      setMessage(`${(cause as Error).message}；草稿已保存在本机，可重试。`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="feedback-backdrop" role="dialog" aria-modal="true" aria-label="反馈问题">
      <div className="feedback-panel">
        <header>
          <h2>反馈问题</h2>
          <button type="button" className="text-button" onClick={onClose}>关闭</button>
        </header>
        <label>
          类型
          <select value={kind} onChange={(event) => setKind(event.target.value)}>
            <option value="problem">问题</option>
            <option value="inaccurate">内容不准确</option>
            <option value="suggestion">体验建议</option>
          </select>
        </label>
        <label>
          发生了什么
          <textarea
            value={body}
            maxLength={4000}
            rows={5}
            onChange={(event) => setBody(event.target.value)}
            placeholder="请描述你看到的情况；不需要复制日志。"
          />
        </label>
        <label>
          附上当前页面截图（可选）
          <input
            type="file"
            accept="image/png,image/jpeg,image/webp"
            onChange={(event) => {
              setScreenshot(event.target.files?.[0] ?? null);
              setConfirmed(false);
            }}
          />
        </label>
        {preview !== "" ? (
          <div className="feedback-preview">
            <img src={preview} alt="截图预览" />
            <label>
              <input
                type="checkbox"
                checked={confirmed}
                onChange={(event) => setConfirmed(event.target.checked)}
              />
              我确认发送这张截图
            </label>
          </div>
        ) : null}
        <p className="feedback-hint">
          默认不会上传原图、视频、问答正文或浏览器日志。当前账号：{identity?.display_name ?? "本地用户"}。
        </p>
        {message !== "" ? <p role="status">{message}</p> : null}
        <button type="button" disabled={busy || body.trim() === ""} onClick={() => void submit()}>
          {busy ? "正在提交…" : "提交反馈"}
        </button>
        {records.length > 0 ? (
          <section>
            <h3>我提交的反馈</h3>
            <ul className="feedback-history">
              {records.slice(0, 10).map((record) => (
                <li key={record.id}>
                  <strong>{feedbackStatus(record.status)}</strong>
                  <span>{record.body.slice(0, 80)}</span>
                  {record.reply !== "" ? <small>回复：{record.reply}</small> : null}
                </li>
              ))}
            </ul>
          </section>
        ) : null}
      </div>
    </div>
  );
}

function feedbackStatus(status: string): string {
  if (status === "triaging") return "处理中";
  if (status === "processed") return "已处理";
  return "已收到";
}
