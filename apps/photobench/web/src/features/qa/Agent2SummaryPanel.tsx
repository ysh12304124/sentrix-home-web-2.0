/**
 * Agent 2.0 首轮规划与证据账本。移植 1.0 `App.vue:3626-3636`（`getAgent2Trace` 有值才挂）。
 * 取值 / 文案函数全在 `lib/trace.ts`（1.0 `:1718-1793` 的移植），这里只做渲染。
 */
import type { RunItemDetail } from "../../api/types.ts";
import { esc } from "../../lib/format.ts";
import {
  agent2DecisionSummary,
  agent2EvidenceHeight,
  agent2EvidenceRows,
  agent2EvidenceTypeLabel,
  agent2RequirementStatusClass,
  agent2RequirementStatusLabel,
  agent2RequirementSummary,
  formatAgent2EvidenceValue,
  getAgent2LedgerEntries,
  getAgent2Requirements,
  getAgent2Trace,
} from "../../lib/trace.ts";

type Dict = Record<string, unknown>;

function dict(value: unknown): Dict {
  return typeof value === "object" && value !== null ? (value as Dict) : {};
}

/** 1.0 `(x || []).join("、")`：取第一个真值，非数组当空。 */
function joinRefs(value: unknown): string {
  return Array.isArray(value) ? value.map((item) => String(item)).join("、") : "";
}

export interface Agent2SummaryPanelProps {
  detail: RunItemDetail;
}

export function Agent2SummaryPanel({ detail }: Agent2SummaryPanelProps) {
  const trace = getAgent2Trace(detail);
  if (!trace) return null;

  const requirements = getAgent2Requirements(detail);
  const entries = getAgent2LedgerEntries(detail);
  const declaration = dict(trace.task_declaration);
  return (
    <details className="agent2-summary-panel">
      <summary className="agent2-summary-toggle">
        <span className="agent2-toggle-copy">
          <small>本轮规划与证据</small>
          <strong>目标分解与证据账本详情</strong>
          <span>查看本轮需求、证据与完成状态</span>
        </span>
        <span className="agent2-counters">
          <span>{agent2RequirementSummary(detail)}</span>
          <span>{entries.length} 条证据</span>
          <span>{agent2DecisionSummary(detail)}</span>
        </span>
      </summary>
      <div className="agent2-summary-body">
        <div className="agent2-overview-grid">
          <span>
            <small>目标</small>
            <b>{esc(declaration.goal) || "未记录"}</b>
          </span>
          <span>
            <small>作用域</small>
            <b>{esc(declaration.scope_id) || "未记录"}</b>
          </span>
          <span>
            <small>需求数</small>
            <b>{requirements.length} 项证据需求</b>
          </span>
          <span>
            <small>证据数</small>
            <b>{entries.length} 条账本记录</b>
          </span>
        </div>
        {requirements.length > 0 && (
          <div className="agent2-detail-section">
            <div className="agent2-section-label">证据需求</div>
            <div className="agent2-requirement-list">
              {requirements.map((req, rIdx) => (
                <div key={rIdx} className="agent2-requirement">
                  <div className="agent2-requirement-head">
                    <b>{esc(req.id)}</b>
                    <span>{agent2EvidenceTypeLabel(req.evidence_type)}</span>
                    <em className={agent2RequirementStatusClass(req.status)}>
                      {agent2RequirementStatusLabel(req.status)}
                    </em>
                  </div>
                  <p>{esc(req.description) || "未记录需求描述"}</p>
                  <small>
                    证据引用：{joinRefs(req.evidence_refs) || "暂无"}
                    {req.unmet_reason ? <> · 未满足原因：{esc(req.unmet_reason)}</> : null}
                  </small>
                </div>
              ))}
            </div>
          </div>
        )}
        {entries.length > 0 && (
          <div className="agent2-detail-section">
            <div className="agent2-section-label">证据账本</div>
            <div className="agent2-evidence-list">
              {entries.map((entry, eIdx) => {
                const value = entry.extracted_value ?? entry.value ?? null;
                // 1.0 `(entry.provenance_refs || entry.input_refs || []).join("、")`：
                // 空的 provenance_refs 是 truthy，不会落到 input_refs——照搬这个 `||` 链。
                const refs = entry.provenance_refs || entry.input_refs || [];
                const source = joinRefs(refs) || esc(entry.tool_call_id) || "未记录";
                return (
                  <div key={eIdx} className="agent2-evidence">
                    <div className="agent2-evidence-head">
                      <b>{esc(entry.capability) || agent2EvidenceTypeLabel(entry.evidence_type)}</b>
                      <span>{agent2EvidenceTypeLabel(entry.evidence_type)}</span>
                      <em>{esc(entry.certainty) || "未定"}</em>
                    </div>
                    <textarea
                      className="agent2-json-view"
                      readOnly
                      wrap="off"
                      rows={agent2EvidenceRows(value)}
                      style={{ height: agent2EvidenceHeight(value) }}
                      value={formatAgent2EvidenceValue(value)}
                      aria-label="证据账本 JSON 内容"
                    />
                    <small>
                      来源：{source}
                      {entry.asset_id ? <> · 图片：{esc(entry.asset_id)}</> : null}
                      {entry.unmatched_reason ? (
                        <>
                          {" · "}
                          {entry.unmatched_reason === "evidence_incompatible"
                            ? "非当前需求证据"
                            : esc(entry.unmatched_reason)}
                        </>
                      ) : null}
                    </small>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </details>
  );
}
