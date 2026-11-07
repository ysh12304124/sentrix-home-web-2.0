import { useCallback, useEffect, useRef, useState, type DragEvent } from "react";

import { useTheme } from "../../app/theme/ThemeProvider";
import { THEMES, themeLabel, type ThemePreference } from "../../app/theme/themes";
import { feedbackBody } from "./themeFeedback";
import { newClientId } from "../../shared/id";

import { useAuth } from "../../app/AuthProvider";
import { useScope } from "../../app/ScopeProvider";
import "./feedback.css";
import { readAppearanceDraft, saveAppearanceDraft, clearAppearanceDraft } from "./appearanceDraft";
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

/** 与后端 `control/feedback.py` 的 `MAX_SCREENSHOT` 一致。 */
const MAX_SCREENSHOT_BYTES = 5 * 1024 * 1024;
/** 自动截屏的输出上限；再大就压到 5 MiB 以内。 */
const SCREENSHOT_MAX_DIMENSION = 1600;
const ACCEPTED_IMAGE_TYPES = new Set(["image/png", "image/jpeg", "image/webp"]);
const ACCEPTED_IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".webp"];

export function AppearanceFeedbackPanel({ onClose, appearanceFeedback = false }: { onClose: () => void; appearanceFeedback?: boolean }) {
  const { theme, enabled: themeEnabled } = useTheme();
  const [preference, setPreference] = useState<ThemePreference>("");
  const [draftAccount, setDraftAccount] = useState<string | null>(null);
  const { identity } = useAuth();
  const { scopeId } = useScope();
  const accountId = identity?.account_id ?? null;
  const [kind, setKind] = useState(appearanceFeedback ? "suggestion" : "problem");
  const [body, setBody] = useState("");
  const [screenshot, setScreenshot] = useState<File | null>(null);
  const [preview, setPreview] = useState("");
  const [busy, setBusy] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [message, setMessage] = useState("");
  const [records, setRecords] = useState<FeedbackRecord[]>([]);
  const pendingRef = useRef<{ signature: string; key: string; payload: object } | null>(null);
  const serializedBody = feedbackBody(body, theme, preference, themeEnabled);
  const canSubmit = (body.trim() !== "" || preference !== "") && serializedBody.length <= 4000;
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  // 自动截屏时要排除这个节点：它是反馈弹窗本身，不是用户看到的页面。
  const backdropRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    clearLegacyFeedbackDraft();
    if (accountId === null) {
      // 身份未确认前不读任何用户草稿。
      setBody("");
      setPreference("");
      setDraftAccount(null);
      setRecords([]);
      return;
    }
    let cancelled = false;
    setBody(readFeedbackDraft(accountId));
    const draft = readAppearanceDraft(accountId);
    setPreference(draft.preference);
    setKind(appearanceFeedback ? "suggestion" : draft.kind || "problem");
    setDraftAccount(accountId);
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
  }, [accountId, appearanceFeedback]);

  useEffect(() => { pendingRef.current = null; }, [accountId]);
  useEffect(() => {
    if (accountId === null || draftAccount !== accountId) return;
    if (body === "" && preference === "") { clearFeedbackDraft(accountId); clearAppearanceDraft(accountId); return; }
    saveFeedbackDraft(accountId, body);
    saveAppearanceDraft(accountId, preference, kind);
  }, [accountId, draftAccount, body, preference, kind]);

  useEffect(() => {
    if (screenshot === null) {
      setPreview("");
      return;
    }
    const url = URL.createObjectURL(screenshot);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [screenshot]);

  const acceptFile = useCallback((file: File | null) => {
    if (file === null) return;
    const name = file.name.toLowerCase();
    const typeOk =
      ACCEPTED_IMAGE_TYPES.has(file.type) ||
      (file.type === "" && ACCEPTED_IMAGE_EXTENSIONS.some((ext) => name.endsWith(ext)));
    if (!typeOk) {
      setMessage("截图只支持 PNG、JPEG 或 WebP。");
      return;
    }
    if (file.size > MAX_SCREENSHOT_BYTES) {
      setMessage("截图超过 5 MiB，请换一张或重新截取。");
      return;
    }
    setScreenshot(file);
    setMessage("");
  }, []);

  // 面板打开期间监听整页粘贴。只接图片，文本粘贴仍走输入框。
  useEffect(() => {
    function handlePaste(event: ClipboardEvent) {
      const data = event.clipboardData;
      // 不同浏览器把粘贴的图片放在 files 或 items 里，两处都看。
      const candidates = [
        ...Array.from(data?.files ?? []),
        ...Array.from(data?.items ?? [])
          .filter((item) => item.kind === "file")
          .map((item) => item.getAsFile())
          .filter((item): item is File => item !== null),
      ];
      const file = candidates.find((item) => item.type.startsWith("image/"));
      if (file === undefined) return;
      event.preventDefault();
      acceptFile(file);
    }
    window.addEventListener("paste", handlePaste);
    return () => window.removeEventListener("paste", handlePaste);
  }, [acceptFile]);

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    const file = Array.from(event.dataTransfer.files).find((item) =>
      item.type.startsWith("image/"),
    );
    acceptFile(file ?? null);
  }

  /**
   * 截「用户原本看到的页面」。
   *
   * 反馈面板还在 DOM 里，但它被 `ignoreElements` 排除；页面自身的滚动位置、已加载内容
   * 都保持原样。html2canvas 按需动态加载，不增加首屏包体。
   */
  async function capturePage() {
    if (capturing) return;
    setCapturing(true);
    setMessage("");
    try {
      const { default: html2canvas } = await import("html2canvas");
      const canvas = await html2canvas(document.body, {
        useCORS: true,
        scale: 1,
        logging: false,
        backgroundColor: getComputedStyle(document.body).backgroundColor || "#ffffff",
        ignoreElements: (element) => element === backdropRef.current,
      });
      const file = await screenshotFile(canvas);
      setScreenshot(file);
      setMessage(`已截取当前页面（约 ${Math.max(1, Math.round(file.size / 1024))} KB），可直接提交。`);
    } catch {
      setMessage("自动截屏失败，可以粘贴或拖入图片。");
    } finally {
      setCapturing(false);
    }
  }

  const submit = async () => {
    if (!canSubmit || busy) return;
    const signature = JSON.stringify([accountId, kind, body.trim(), preference, window.location.pathname, scopeId]);
    if (pendingRef.current?.signature !== signature) {
      pendingRef.current = { signature, key: newClientId("fb"), payload: { kind, body: serializedBody, route: window.location.pathname, scope_id: scopeId } };
    }
    const pending = pendingRef.current;
    setBusy(true);
    setMessage("");
    try {
      const response = await fetch("/control/v1/feedback", {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "Idempotency-Key": pending.key,
          "x-csrf-token": csrfToken(),
        },
        body: JSON.stringify(pending.payload),
      });
      if (!response.ok) throw new Error(`提交失败（${response.status}）`);
      const created = (await response.json()) as { id: string };
      let attachmentFailed = false;
      if (screenshot !== null) {
        const form = new FormData();
        form.append("file", screenshot);
        try {
          const upload = await fetch(`/control/v1/feedback/${created.id}/attachment`, {
            method: "POST", credentials: "same-origin", headers: { "x-csrf-token": csrfToken() }, body: form,
          });
          attachmentFailed = !upload.ok;
        } catch { attachmentFailed = true; }
      }
      if (accountId !== null) { clearFeedbackDraft(accountId); clearAppearanceDraft(accountId); }
      setBody("");
      setPreference("");
      setScreenshot(null);
      setMessage(
        attachmentFailed
          ? `反馈已提交（${created.id}），但截图上传失败，可以重新反馈一次截图。`
          : `已收到，反馈编号 ${created.id}`,
      );
      pendingRef.current = null;
      try {
        const list = await fetch("/control/v1/feedback", { credentials: "same-origin" });
        if (list.ok) setRecords(((await list.json()) as { items: FeedbackRecord[] }).items);
      } catch { /* A history refresh failure does not invalidate a submitted feedback. */ }
    } catch (cause) {
      if (accountId !== null) saveFeedbackDraft(accountId, body);
      setMessage(`${(cause as Error).message}；草稿已保存在本机，可重试。`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="feedback-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="反馈问题"
      ref={backdropRef}
    >
      <div className="feedback-panel">
        <header>
          <h2>反馈问题</h2>
          <button type="button" className="text-button" onClick={onClose}>关闭</button>
        </header>
        <label>
          类型
          <select disabled={busy} value={kind} onChange={(event) => setKind(event.target.value)}>
            <option value="problem">问题</option>
            <option value="inaccurate">内容不准确</option>
            <option value="suggestion">体验建议</option>
          </select>
        </label>
        {themeEnabled ? <section className="feedback-appearance">
          <p>当前界面：{themeLabel(theme)} · 提交时会附上风格信息</p>
          <label>你更喜欢哪种界面风格？（可选）
            <select value={preference} disabled={busy} onChange={(event) => setPreference(event.target.value as ThemePreference)}>
              <option value="">请选择</option>
              {THEMES.map((item) => <option key={item.id} value={item.id}>{item.code} {item.name}</option>)}
              <option value="none">暂时没有偏好</option>
            </select>
          </label>
        </section> : null}
        <label>
          发生了什么
          <textarea
            value={body}
            disabled={busy}
            maxLength={3600}
            rows={5}
            onChange={(event) => setBody(event.target.value)}
            placeholder="请描述你看到的情况；不需要复制日志。"
          />
        </label>

        <p className="feedback-body-count">{serializedBody.length > 4000 ? "内容过长，请精简到 4000 字以内（含界面信息）。" : "可只选择风格偏好，也可以写下具体感受。"}</p>
        <div
          className="feedback-attach"
          onDragEnter={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() => setDragging(false)}
          onDrop={handleDrop}
        >
          <span className="feedback-attach-title">截图（可选）</span>
          {preview === "" ? (
            <div
              className={dragging ? "feedback-drop dragging" : "feedback-drop"}
              role="button"
              tabIndex={0}
              onClick={() => fileInputRef.current?.click()}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  fileInputRef.current?.click();
                }
              }}
            >
              <p>把图片拖到这里，或按 Ctrl/⌘+V 粘贴，也可以点击选择文件</p>
            </div>
          ) : (
            <div className="feedback-preview">
              <img src={preview} alt="截图预览" />
              <button type="button" className="text-button" onClick={() => setScreenshot(null)}>
                移除截图
              </button>
            </div>
          )}
          <div className="feedback-attach-actions">
            <button
              type="button"
              className="button ghost small"
              disabled={capturing || busy}
              onClick={() => void capturePage()}
            >
              {capturing ? "正在截屏…" : "错误自动截屏"}
            </button>
            <span>截取的是反馈窗口后面的当前页面</span>
          </div>
          <input
            ref={fileInputRef}
            type="file"
            accept="image/png,image/jpeg,image/webp"
            hidden
            onChange={(event) => {
              acceptFile(event.target.files?.[0] ?? null);
              event.target.value = "";
            }}
          />
        </div>

        <p className="feedback-hint">
          默认不会上传原图、视频、问答正文或浏览器日志。当前账号：{identity?.display_name ?? "本地用户"}。
        </p>
        {message !== "" ? <p role="status">{message}</p> : null}
        <button type="button" disabled={busy || !canSubmit} onClick={() => void submit()}>
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

/**
 * 画布 → 可上传的 JPEG。控制最长边与体积：后端的限额是 5 MiB，
 * 而整页截图在长页面上很容易超。
 */
async function screenshotFile(source: HTMLCanvasElement): Promise<File> {
  let scale = Math.min(1, SCREENSHOT_MAX_DIMENSION / Math.max(source.width, source.height));
  let quality = 0.85;
  for (let attempt = 0; attempt < 4; attempt += 1) {
    const target = document.createElement("canvas");
    target.width = Math.max(1, Math.round(source.width * scale));
    target.height = Math.max(1, Math.round(source.height * scale));
    const context = target.getContext("2d");
    if (context === null) break;
    context.drawImage(source, 0, 0, target.width, target.height);
    const blob = await new Promise<Blob | null>((resolve) =>
      target.toBlob(resolve, "image/jpeg", quality),
    );
    if (blob !== null && blob.size <= MAX_SCREENSHOT_BYTES - 256 * 1024) {
      return new File([blob], "feedback-screenshot.jpg", { type: "image/jpeg" });
    }
    scale *= 0.75;
    quality = Math.max(0.5, quality - 0.1);
  }
  throw new Error("截图压缩后仍然过大");
}

function feedbackStatus(status: string): string {
  if (status === "triaging") return "处理中";
  if (status === "processed") return "已处理";
  return "已收到";
}
