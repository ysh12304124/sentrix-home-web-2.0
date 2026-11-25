import { SavedItemContent } from "./SavedItemContent.tsx";
import { CandidateMedia, DeliveredMedia, GroundTruthMedia } from "../interactive/UnifiedMedia.tsx";
import { useTraceOverview } from "../../lib/lazyTrace.ts";
import { ScoringReview } from "./ScoringReview.tsx";
import { OPEN_PROFILE, ATM_PROFILE } from '../../lib/scoring.ts';
import { ChoiceMatchingResult, EvaluationResult, EvaluationTypeBadge, evaluationReason } from "../../components/EvaluationResult.tsx";
import { ItemScoringResults } from '../../components/ScoringResults.tsx';
import { taxonomyLabel, abilityLabel } from "../../lib/qaTaxonomy.ts";
/**
 * 单题卡（列表项 + 展开体）。移植 1.0 `App.vue:3460-3674`。
 *
 * 详情先展示回答、媒体和评分；底部按需展开对话、性能与证据账本。
 *
 * **两个媒体区是两件事**（1.0 `:703-711` 的注释原文）：
 *   工具召回候选媒体 `toolRecallMedia`（检索候选） / 模型交付媒体 `itemEvidenceMedia`（显式交付，
 *   未交付即空、**绝不回退到候选**）。两者都直接 import `lib/evidence.ts`，渲染也分开。
 */
import type { MediaLike } from "../../api/media.ts";
import type { RejudgeTask, RunItemDetail, RunItemSummary } from "../../api/types.ts";
import {
  itemRetrievalMetrics,
  conversationTurns,
} from "../../lib/evidence.ts";
import {
  completionLabel,
  esc,
  evidenceScoreLabel,
  fmtMs,
  fmtPct,
  judgeRoundState,
  judgeScoreLabel,
  judgeScoreClass,
  scoreClass,
  turnTerminationLabel,
  taskDecisionLabel,
} from "../../lib/format.ts";
import { itemLayerChain } from "../../lib/graphs.ts";
import { itemTimingBreakdown, qaLatencySegments } from "../../lib/trace.ts";

import { DebugTracePanel } from "./DebugTracePanel.tsx";
import { hasToolTrace, judgeReason, segmentStyle } from "./qaShared.ts";
import type { QaSummaryView, ReviewDraft } from "./qaShared.ts";

type Dict = Record<string, unknown>;

function dict(value: unknown): Dict {
  return typeof value === "object" && value !== null ? (value as Dict) : {};
}

export interface QaItemCardProps {
  summary: RunItemSummary;
  detail: RunItemDetail | undefined;
  loading: boolean;
  open: boolean;
  albumId: string;
  modelLabel: string;
  activeRejudge: RejudgeTask | null;
  onToggle: () => void;
  onOpenImage: (media: MediaLike) => void;
  onOpenJudgeInput: (detail: RunItemDetail) => void;
  onRetest?: () => void;
  review?: ReviewDraft;
  onReviewChange?: (patch: ReviewDraft) => void;
}

export function QaItemCard({
  summary,
  detail,
  loading,
  open,
  albumId,
  modelLabel,
  activeRejudge,
  onToggle,
  onRetest,
  onOpenImage,
  onOpenJudgeInput,
}: QaItemCardProps) {
  const s = summary as QaSummaryView;
  return (
    <article className={`qa-item${open ? " open" : ""}`}>
      <button className="item-head qa-toggle" type="button" onClick={onToggle}>
        <span className="item-idx">{String(s.index + 1).padStart(2, "0")}</span>
        <strong>{s.display_question || conversationTurns(detail)[0]?.message || s.question}</strong>
        {Number(s.conversation_turn_count || conversationTurns(detail).length) > 1 ? <span className="qa-dialogue-badge">{s.conversation_turn_count || conversationTurns(detail).length} 轮对话</span> : null}
        <EvaluationTypeBadge value={summary.evaluation || summary.evaluation_result || summary.judge} />
        {summary.profile_scores && Object.keys(summary.profile_scores).length ? Object.entries(summary.profile_scores).filter(([, judge]) => judge.status !== "not_collected").map(([profile, judge]) => {
          const value = typeof judge.score === 'number' && Number.isFinite(judge.score) ? judge.score : null;
          const label = profile === OPEN_PROFILE ? '开放式' : profile === ATM_PROFILE ? 'QS' : profile;
          const status = value !== null ? profile === OPEN_PROFILE ? value + '/2 分' : (value * 100).toFixed(1) + '%'
            : judge.status === 'failed' ? '评分失败' : judge.status === 'running' ? '评分中' : judge.status === 'not_collected' ? '未运行采集' : '待评分';
          return <span key={profile} className="score" title={String(judge.reason || '')}>{label} {status}</span>;
        }) : <span className={'score ' + judgeScoreClass(s) + ' judge-round-' + judgeRoundState(s, activeRejudge)}>
          {judgeScoreLabel(s, activeRejudge)}
        </span>}
        {Number(s.ground_truth_count) > 0 && summary.retrieval_metrics_applicable !== false && (
          <span className="muted small">
            精确率 {fmtPct(s.retrieval_precision)} · 召回率 {fmtPct(s.retrieval_recall)} · F1{" "}
            {fmtPct(s.retrieval_f1)} · 命中 {esc(s.matched_count)}/{esc(s.ground_truth_count)}
          </span>
        )}
        {s.evidence_judge?.score != null && (
          <span className={`score ${scoreClass(s.evidence_judge.score)}`}>证据 {esc(s.evidence_judge.score)}</span>
        )}
        {summary.text_retrieval_metrics && (
          <span className="muted small">
            邮件检索 P {fmtPct(summary.text_retrieval_metrics.retrieval.precision)} · R {fmtPct(summary.text_retrieval_metrics.retrieval.recall)}
            {' '}· F1 {fmtPct(summary.text_retrieval_metrics.retrieval.f1)}
            {' '}· R@5 {fmtPct(summary.text_retrieval_metrics.recall_at_k['5'])}
            {' '}· 引用 P {fmtPct(summary.text_retrieval_metrics.citation.precision)}
            {' '}· R {fmtPct(summary.text_retrieval_metrics.citation.recall)}
            {' '}· F1 {fmtPct(summary.text_retrieval_metrics.citation.f1)}
            {' '}· 引用邮件：{summary.text_retrieval_metrics.citation.status === 'available'
              ? (summary.text_retrieval_metrics.cited_evidence_ids?.join('、') || '无') : '未记录'}
            {summary.text_retrieval_metrics.reason ? ' · 取证事实未完整记录' : ''}
          </span>
        )}
        <span className="muted small">
          模型 {esc(s.model_call_count)} · 工具 {esc(s.tool_call_count)}
        </span>
        <span className="qa-chevron" aria-hidden="true">
          ⌄
        </span>
      </button>
      {open && (
        <div className="qa-expanded">
          {loading ? (
            <div className="qa-results-empty">正在加载该题完整记录…</div>
          ) : detail ? (
            <QaItemDetail
              detail={detail}
              albumId={albumId}
              modelLabel={modelLabel}
              onOpenImage={onOpenImage}
              onOpenJudgeInput={onOpenJudgeInput}
              onRetest={onRetest}
              review={undefined}
              onReviewChange={undefined}
            />
          ) : null}
        </div>
      )}
    </article>
  );
}

interface QaItemDetailProps {
  detail: RunItemDetail;
  albumId: string;
  modelLabel: string;
  onOpenImage: (media: MediaLike) => void;
  onOpenJudgeInput: (detail: RunItemDetail) => void;
  onRetest?: () => void;
  review?: ReviewDraft;
  onReviewChange?: (patch: ReviewDraft) => void;
}

function QaItemDetail({
  detail,
  albumId,
  modelLabel,
  onOpenImage,
  onOpenJudgeInput,
  onRetest,
}: QaItemDetailProps) {
  const turns = conversationTurns(detail);
  const chain = itemLayerChain(detail, albumId);
  const taskJudge = dict(detail.task_judge);
  const isAtm = albumId.startsWith("atm-") || Boolean(dict(detail.evaluation).source_qtype);
  const evidenceJudge = dict(detail.evidence_judge);
  const { overview, loading: metricsLoading, error: metricsError } = useTraceOverview(detail);
  const measuredDetail = overview ? { ...detail, trace_read_summary: overview } : detail;
  const timing = itemTimingBreakdown(measuredDetail);
  const qaSegments = qaLatencySegments(measuredDetail);
  const toolTrace = hasToolTrace(measuredDetail);
  const sourceType = (detail.tags ?? []).find(tag => ["question:audio", "question:visual", "question:audio_visual", "question:summary"].includes(tag));
  const sourceTypeLabel: Record<string, string> = {"question:audio": "纯音频题", "question:visual": "视觉题", "question:audio_visual": "音画题", "question:summary": "总结题"};
  const qualityReason = evaluationReason(judgeReason(detail.judge));
  const behaviorReason = judgeReason(detail.task_judge);
  const evidenceReason = judgeReason(detail.evidence_judge);

  return (
    <>
      <div className="qa-meta-tags">
        {sourceType && <span>QA 来源：{sourceTypeLabel[sourceType]}</span>}
        {detail.benchmark_group && !detail.ability_family_ids?.some(id => id === "H" || id === "I") ? <span>{taxonomyLabel(detail.benchmark_group)}</span> : null}
        {(detail.ability_family_ids || []).filter(id => id === "H" || id === "I").map(id => <span key={id}>{abilityLabel(id, detail.ability_taxonomy_version)}</span>)}
        {(detail.ability_primary_ids || []).map(id => <span key={id}>{abilityLabel(id[0], detail.ability_taxonomy_version)} · {abilityLabel(id, detail.ability_taxonomy_version)}</span>)}
        {!isAtm && detail.task_type ? <span>{taxonomyLabel(detail.task_type)}</span> : null}
        {detail.question_type ? <span>{taxonomyLabel(detail.question_type)}</span> : null}
        {detail.angle ? <span>{detail.angle}</span> : null}
        {detail.difficulty ? <span>{taxonomyLabel(detail.difficulty)}</span> : null}
        {!isAtm && detail.answerability ? <span>{taxonomyLabel(detail.answerability)}</span> : null}
        {(detail.tags ?? []).filter(tag => !isAtm || !/^(action|answerability|task):/.test(tag)).map((tag) => (
          <span key={tag} className="qa-run-tag">
            {tag}
          </span>
        ))}
      </div>
      <ItemScoringResults results={detail.scoring_results} diagnostics={detail.diagnostic_results} current={detail.profile_scores} definition={detail.evaluation} />
      <ScoringReview key={String(detail.run_id) + String(detail.qa_id)} runId={String(detail.run_id || '')} results={detail.scoring_results} reviews={detail.scoring_reviews}/>
      {!detail.profile_scores && <details><summary>历史评分</summary><EvaluationResult value={detail.evaluation_result || detail.judge} definition={detail.evaluation} showChoiceMatching={!detail.choice_matching_result} />{detail.choice_matching_result != null && <ChoiceMatchingResult value={detail.choice_matching_result} definition={detail.evaluation} standalone />}</details>}
      <section className="qa-layer-chain">
        <div className="qa-layer-chain-head">
          <strong>本题证据链</strong>
          <span>关键帧若存在，显示为 L0 媒体；不会直接标成 L2 事件</span>
        </div>
        <div className="qa-layer-chain-row">
          {chain.map((node, nodeIndex) => (
            <div key={node.key} className="qa-layer-node-wrap">
              <div className={`qa-layer-node qa-layer-node-${node.status}`}>
                <b>{node.key}</b>
                <strong>{node.name}</strong>
                <span>{node.detail}</span>
                <em>{node.evidence}</em>
              </div>
              {nodeIndex < chain.length - 1 && (
                <i className="qa-layer-arrow" aria-hidden="true">
                  →
                </i>
              )}
            </div>
          ))}
        </div>
      </section>
      <div className="item-body">
        <div>
          <h4>{turns.length > 1 ? "最终一轮回答" : "模型回答"}</h4>
          <p>{esc(detail.final_answer) || esc(detail.answer) || esc(detail.error) || "未完成"}</p>
          {Array.isArray(detail.content_fields) ? (detail.content_fields as { path: string; characters: number }[]).filter(field => field.path !== "process_diagnostics").map(field =>
            <SavedItemContent key={String(detail.run_id) + String(detail.index) + field.path} detail={detail} path={field.path} label={`完整内容（${field.path}${field.characters ? `，${field.characters}字符` : ""}）`} />) : null}
          <div className="capability-grid">
            <span>
              <small>{isAtm ? "公共行为诊断（评测端）" : turns.length > 1 ? "最终一轮回答行为" : "回答行为"}</small>
              <b>{taskJudge.correct === true ? "符合预期" : taskJudge.correct === false ? "不符合预期" : "未评分"}</b>
              {!isAtm && <small>{taskDecisionLabel(detail)}</small>}
            </span>
            <span>
              <small>媒体检索</small>
              <b>{itemRetrievalMetrics(detail)}</b>
            </span>
            <span>
              <small>{turns.length > 1 ? "最终一轮执行状态" : "执行状态"}</small>
              <b>{completionLabel(detail)}</b>
              {detail.termination_reason ? <small>{turnTerminationLabel(detail)}</small> : null}
            </span>
            {evidenceJudge.score != null || (evidenceJudge.reason && evidenceJudge.reason !== "not_applicable") ? <span>
              <small>证据评分</small>
              <b>{evidenceScoreLabel(detail.evidence_judge)}</b>
            </span> : null}
          </div>
          <CandidateMedia item={detail} albumId={albumId} onOpenMedia={onOpenImage}/>
          <DeliveredMedia item={detail} albumId={albumId} onOpenMedia={onOpenImage}/>
        </div>
        <div className="qa-reference-column">
          <GroundTruthMedia item={detail} albumId={albumId} onOpenMedia={onOpenImage}/>
          <h4>正确答案</h4>
          <p>{esc(detail.reference_answer)}</p>
          {qualityReason && (
            <>
              <h4>回答质量评分说明</h4>
              <p className="muted">{qualityReason}</p>
            </>
          )}
          {behaviorReason && (
            <>
              <h4>任务判断说明</h4>
              <p className="muted">{behaviorReason}</p>
            </>
          )}
          {evidenceReason && (
            <>
              <h4>媒体证据评分说明</h4>
              <p className="muted">{evidenceReason}</p>
            </>
          )}
        </div>
      </div>
      <section className="qa-performance">
        {metricsLoading ? <p className="muted small">正在读取调用统计…</p> : metricsError ? <p className="muted small">调用统计读取失败：{metricsError}</p> : null}
        {overview?.timing_complete === false ? <p className="muted small">部分调用未记录耗时，累计仅含已记录时长，暂不绘制占比。</p> : null}
        <div className="timing-breakdown">
          <span>
            {Number(detail.metrics_contract_version) === 2 ? "Agent 请求墙钟" : "端到端（旧口径）"} <b>{fmtMs(timing.wall_clock_ms)}</b>
          </span>
          {Number(detail.metrics_contract_version) !== 2 ? <span>
            Agent 总耗时 <b>{fmtMs(timing.agent_wall_ms)}</b>
          </span> : null}
          <span>
            模型累计（不含工具内） <b>{fmtMs(timing.model_ms)}</b>
          </span>
          <span>
            工具累计 <b>{toolTrace ? fmtMs(timing.tool_ms) : "未记录"}</b>
          </span>
          <span>
            独立评分 Judge <b>{fmtMs(timing.judge_ms)}</b>
          </span>
          <span>
            其他 <b>{toolTrace ? fmtMs(timing.other_ms) : "未记录"}</b>
          </span>
        </div>
        {qaSegments.length > 0 && (
          <div className="latency-composition qa-latency-composition">
            <div className="latency-composition-head">
              <strong>{Number(detail.metrics_contract_version) === 2 ? "Agent 请求耗时分布" : "QA 样本时延组成（旧口径）"}</strong>
              <span>{fmtMs(timing.wall_clock_ms)}</span>
            </div>
            <div className="latency-bar" role="img" aria-label={Number(detail.metrics_contract_version) === 2 ? "Agent 请求耗时分布" : "QA 样本时延组成"}>
              {qaSegments.map((segment, segmentIndex) => (
                <span
                  key={`${segment.kind}-${segmentIndex}`}
                  className={`latency-segment latency-segment-${segment.kind}`}
                  style={segmentStyle(segment, qaSegments)}
                  data-tooltip={`${segment.label} · ${fmtMs(segment.ms)}`}
                >
                  <b>{fmtMs(segment.ms)}</b>
                </span>
              ))}
            </div>
            <div className="latency-segment-legend">
              {qaSegments.map((segment, segmentIndex) => (
                <span key={`${segment.kind}-legend-${segmentIndex}`}>
                  <i className={`latency-dot latency-dot-${segment.kind}`}></i>
                  {segment.shortLabel} {fmtMs(segment.ms)}
                </span>
              ))}
            </div>
          </div>
        )}
        <div id={`process-log-${detail.qa_id}`}><DebugTracePanel detail={measuredDetail} modelLabel={modelLabel} /></div>
      </section>
      <div className="item-footer">
        {onRetest && <button className="judge-details-trigger" type="button" onClick={onRetest}>重测本题 / 模型比较 <span>↗</span></button>}
        <button className="judge-details-trigger" type="button" onClick={() => onOpenJudgeInput(detail)}>
          JUDGE 模型输入 <span>↗</span>
        </button>
      </div>

    </>
  );
}
