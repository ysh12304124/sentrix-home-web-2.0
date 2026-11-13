/**
 * 设备与隐私。对应 1.0 `src/app.js:756-772` 的 `settingsView()`。
 *
 * 1.0 这一页有四块：健康网格、OCR 设置、待确认事实、审计动作。2.0 现在能落地三块：
 *
 * 1. **服务网格**：字段只取 `/health` 真实返回的 `status` / `database` / `embeddings`
 *    / `version`。**模型端点没有暴露**——`routes/health.py` 的返回体里没有它，
 *    页面上如实写「未暴露」，不从别处猜一个填进去。
 * 2. **待确认事实**：`GET /facts?status=pending` + `POST /facts/{id}/confirm|reject`
 *    （`routes/facts.py`）。1.0 的两个按钮在 2.0 一度是禁用的，因为当时没有端点；
 *    现在端点在了，按钮就接上。
 * 3. **审计动作**：全局重聚类（已有端点）；OCR 设置仍然没有 `/settings/ocr`，
 *    相册新建/删除也仍然没有写端点（`/memory-spaces` 只有 GET），两条都保持禁用
 *    并写明原因。
 */

import { useCallback, useEffect, useState } from "react";

import { useScope } from "../../app/ScopeProvider";
import { scopeDisplayName } from "../../shared/spaceName";
import { PageHeader } from "../../shared/ui/PageHeader";
import { Section } from "../../shared/ui/Section";
import { useToast } from "../../shared/ui/Toast";
import {
  confirmFact,
  fetchHealth,
  listFacts,
  reclusterFaces,
  rejectFact,
  type FactPayload,
  type HealthResponse,
  type ReclusterFacesResponse,
} from "../../shared/api/client";
import "../../styles/settings.css";
import "./settings-extra.css";

function factLabel(fact: FactPayload): string {
  return `${fact.subject} ${fact.predicate} ${fact.object}`;
}

export function SettingsPage() {
  const { scopeId, spaces } = useScope();
  const { notify } = useToast();
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [facts, setFacts] = useState<readonly FactPayload[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [factsLoadError, setFactsLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [busyFact, setBusyFact] = useState<string | null>(null);
  const [result, setResult] = useState<ReclusterFacesResponse | null>(null);
  const scopeName = scopeDisplayName(spaces, scopeId);

  const loadHealth = useCallback(async () => {
    try {
      setHealth(await fetchHealth());
      setError(null);
    } catch (cause) {
      setError((cause as Error).message);
    }
  }, []);

  const loadFacts = useCallback(async () => {
    try {
      setFacts(await listFacts(scopeId, "pending"));
      setFactsLoadError(null);
    } catch (cause) {
      setFacts([]);
      setFactsLoadError((cause as Error).message);
    }
  }, [scopeId]);

  useEffect(() => {
    void loadHealth();
  }, [loadHealth]);

  useEffect(() => {
    void loadFacts();
  }, [loadFacts]);

  const recluster = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      // 当前范围为空（全部相册）时传 null：那是**跨空间**重算，不是「某个默认空间」。
      setResult(await reclusterFaces(scopeId === "" ? null : scopeId));
      notify("人脸聚类重算完成");
    } catch (cause) {
      setError((cause as Error).message);
      setResult(null);
      notify(`重算失败：${(cause as Error).message}`, "error");
    } finally {
      setBusy(false);
    }
  }, [notify, scopeId]);

  const reviewFact = useCallback(
    async (fact: FactPayload, action: "confirm" | "reject") => {
      setBusyFact(fact.id);
      try {
        await (action === "confirm" ? confirmFact(fact.id) : rejectFact(fact.id));
        notify(
          action === "confirm"
            ? `已确认事实：${factLabel(fact)}`
            : `已驳回事实：${factLabel(fact)}`,
        );
        // 重新拉而不是本地删行：`superseded` 的旧版本由后端处理，前端不自己推断。
        await loadFacts();
      } catch (cause) {
        notify(`事实${action === "confirm" ? "确认" : "驳回"}失败：${(cause as Error).message}`, "error");
      } finally {
        setBusyFact(null);
      }
    },
    [loadFacts, notify],
  );

  const healthy = health?.status === "ok";

  return (
    <>
      <PageHeader
        kicker="系统 / 本地状态"
        title="你的记忆，运行在自己的家里。"
        description="服务、模型、存储状态都来自当前本地后端。"
        action={
          <button
            type="button"
            className="button ghost"
            onClick={() => {
              void loadHealth();
              void loadFacts();
            }}
          >
            刷新服务状态 ↻
          </button>
        }
      />
      {error ? <p className="error">{error}</p> : null}

      <section className="health-grid">
        <article className="health-card dark">
          <div className="health-title">
            <span>SENTRIX HOME</span>
            <span className="online-pill" data-state={health === null ? "unknown" : healthy ? "ok" : "degraded"}>
              <i />
              {health === null ? "读取中" : healthy ? "在线" : "降级"}
            </span>
          </div>
          <strong>{health === null ? "正在读取…" : healthy ? "本地服务正常" : "本地服务降级"}</strong>
          <p>GET /health · {health?.status ?? "—"}</p>
          <div className="health-line">
            <span>版本</span>
            <b>{health?.version ?? "—"}</b>
          </div>
          <div className="health-bar">
            <i style={{ width: health === null ? "0%" : healthy ? "100%" : "55%" }} />
          </div>
        </article>

        <article className="health-card">
          <div className="health-title"><span>依赖与服务</span></div>
          <div className="service-row">
            <span>数据库</span>
            <strong>{health?.database ?? "—"}</strong>
            <small>本地 SQLite</small>
          </div>
          <div className="service-row">
            <span>嵌入</span>
            <strong>{health?.embeddings ?? "—"}</strong>
            <small>
              {health?.embeddings_error
                ? `原因：${health.embeddings_error}`
                : "检索时的向量通道；不可用时自动退回词法/元数据"}
            </small>
          </div>
          <div className="service-row">
            <span>模型端点</span>
            <strong className="muted-value">未暴露</strong>
            <small>当前 <code>/health</code> 不返回模型端点字段，前端不臆造</small>
          </div>
          <div className="service-row">
            <span>版本</span>
            <strong>{health?.version ?? "—"}</strong>
            <small>Sentrix Home API</small>
          </div>
        </article>
      </section>

      <Section
        kicker="语义记忆 / 版本维护"
        title="需要确认的事实"
        action={<span className="result-count">{facts.length} 条</span>}
      >
        {factsLoadError ? <p className="error">读取事实失败：{factsLoadError}</p> : null}
        {facts.length === 0 ? (
          <p className="empty">没有待确认事实。冲突事实出现后会进入这里，旧版本不会被删除。</p>
        ) : (
          <div className="fact-review-list">
            {facts.map((fact) => (
              <div key={fact.id} className="fact-review-row">
                <div>
                  <strong>{factLabel(fact)}</strong>
                  <small>
                    {fact.id} · 置信度 {Math.round((fact.confidence || 0) * 100)}% · 证据{" "}
                    {fact.evidence_ids.length > 0 ? fact.evidence_ids.join(", ") : "无"}
                  </small>
                </div>
                <div className="review-actions">
                  <button
                    type="button"
                    className="button small primary"
                    disabled={busyFact !== null}
                    onClick={() => void reviewFact(fact, "confirm")}
                  >
                    {busyFact === fact.id ? "处理中…" : "✓ 确认"}
                  </button>
                  <button
                    type="button"
                    className="button small ghost"
                    disabled={busyFact !== null}
                    onClick={() => void reviewFact(fact, "reject")}
                  >
                    × 驳回
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </Section>

      <Section kicker="隐私边界" title="数据只在本地流动">
        <div className="privacy-list">
          <div><span>原始媒体</span><b>本地存储</b></div>
          <div><span>人物特征</span><b>本地处理</b></div>
          <div><span>记忆索引</span><b>本地实体与向量检索</b></div>
        </div>
      </Section>

      <Section kicker="审计入口" title="可操作的系统动作">
        <div className="audit-list">
          <div>
            <button type="button" className="button small ghost" disabled={busy} onClick={() => void recluster()}>
              {busy ? "重算中…" : "全局重算人脸聚类 →"}
            </button>
            <small>
              当前范围：{scopeName}。这是显式维护入口，不在导入链上——正常导入不需要它，
              它的用途是换过阈值或修过数据之后整表重算。
            </small>
          </div>
          <div>
            <button type="button" className="button small ghost" disabled title="2.0 没有 /settings/ocr 端点">
              OCR 设置
            </button>
            <small>尚未开放：2.0 没有 <code>/settings/ocr</code> 端点，后端也没有 OCR 开关字段。</small>
          </div>
          <div>
            <button type="button" className="button small ghost" disabled title="2.0 的 /memory-spaces 只有 GET">
              新建 / 删除相册
            </button>
            <small>
              尚未开放：<code>/memory-spaces</code> 只有 GET（`apps/api/src/sentrix/api/routes/spaces.py`），
              没有创建或删除端点，因此相册管理保持只读。
            </small>
          </div>
        </div>
        {result ? (
          <div className="audit-result">
            <strong>重算完成</strong>
            <span>实例 {result.instances} · 簇 {result.clusters} · 噪声 {result.noise}</span>
            <span>
              认领不确定 {result.attached_uncertain} · 撤下簇 {result.retired_clusters} ·
              撤下候选 {result.retired_candidates}
            </span>
            <small>口径：阈值 {result.threshold} / 最低质量 {result.minimum_quality}</small>
          </div>
        ) : null}
      </Section>
    </>
  );
}
