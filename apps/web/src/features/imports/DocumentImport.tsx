import { useState } from "react";
import { importDocumentPackage } from "../../shared/api/client";

export function DocumentImport({ scopeId, onImported }: { scopeId: string; onImported: () => void }) {
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  async function submit() {
    if (!file) return;
    setBusy(true); setMessage("");
    try {
      const result = await importDocumentPackage(file, scopeId);
      setMessage(`共 ${result.total} 条，成功 ${result.total - result.failed} 条，失败 ${result.failed} 条。${result.failed ? "可重新上传，已成功的记录会复用。" : ""}`);
      onImported();
    } catch (error) { setMessage((error as Error).message); }
    finally { setBusy(false); }
  }
  return <section className="import-picker"><div className="import-picker-copy"><strong>导入邮件与文档</strong><p>支持 ATM-Bench 邮件 JSON、标准 JSON/JSONL、TXT、Markdown。每封邮件独立保存并可引用。</p><input aria-label="选择邮件或文档" type="file" accept=".json,.jsonl,.txt,.md" disabled={busy} onChange={e => setFile(e.target.files?.[0] ?? null)}/><button type="button" disabled={!file || busy} onClick={() => void submit()}>{busy ? "正在导入…" : "导入文档"}</button>{message && <p role="status">{message}</p>}</div></section>;
}
