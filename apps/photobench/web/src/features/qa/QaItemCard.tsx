import { taxonomyLabel, abilityLabel } from "../../lib/qaTaxonomy.ts";
/**
 * 单题卡（列表项 + 展开体）。移植 1.0 `App.vue:3460-3674`。
 *
 * 详情先展示回答、媒体和评分；底部按需展开对话、性能与证据账本。
 *
 * **两个媒体区是两件事**（1.0 `:703-711` 的注释原文）：
 *   工具召回图片 `toolRecallMedia`（检索候选） / 模型使用图片 `itemEvidenceMedia`（显式交付，
 *   未交付即空、**绝不回退到候选**）。两者都直接 import `lib/evidence.ts`，渲染也分开。
 */
import type { MediaLike } from "../../api/media.ts";
import type { RejudgeTask, RunItemDetail, RunItemSummary } from "../../api/types.ts";
import {
  itemMedia,
  itemRetrievalMetrics,
  conversationTurns,
  itemEvidenceMedia,
  toolRecallMedia,
} from "../../lib/evidence.ts";
import {
  completionLabel,
  esc,
  evidenceScoreLabel,
  fmtMs,
  fmtPct,
  judgeRoundState,
  judgeScoreLabel,
  scoreClass,
  turnTerminationLabel,
  taskDecisionLabel,
} from "../../lib/format.ts";
import { isVideoMedia, imageUrl, pauseVideoAtEvidenceFrame } from "../../api/media.ts";
import { itemLayerChain } from "../../lib/graphs.ts";
import { itemTimingBreakdown, qaLatencySegments } from "../../lib/trace.ts";

import { DebugTracePanel } from "./DebugTracePanel.tsx";
import { hasToolTrace, judgeReason, segmentStyle } from "./qaShared.ts";
import type { QaSummaryView, ReviewDraft } from "./qaShared.ts";

type Dict = Record<string, unknown>;

function dict(value: unknown): Dict {
  return typeof value === "object" && value !== null ? (value as Dict) : {};
}

/** Only show ASR text returned by an executed retrieval tool; never infer it from QA labels. */
function retrievedSpeechEvidence(item: RunItemDetail): Array<{ text: string; start?: number; end?: number }> {
  const found: Array<{ text: string; start?: number; end?: number }> = [];
  const seen = new Set<string>();
  const visitResult = (value: unknown): void => {
    if (Array.isArray(value)) { value.forEach(visitResult); return; }
    const node = dict(value);
    const segments = node.speech_evidence;
    if (Array.isArray(segments)) {
      for (const raw of segments) {
        const segment = dict(raw);
        const text = String(segment.text || "").trim();
        if (!text || seen.has(`${segment.start_sec}|${segment.end_sec}|${text}`)) continue;
        seen.add(`${segment.start_sec}|${segment.end_sec}|${text}`);
        found.push({ text, start: Number.isFinite(Number(segment.start_sec)) ? Number(segment.start_sec) : undefined, end: Number.isFinite(Number(segment.end_sec)) ? Number(segment.end_sec) : undefined });
      }
    }
    Object.values(node).forEach(visitResult);
  };
  const visitCalls = (value: unknown): void => {
    if (Array.isArray(value)) { value.forEach(visitCalls); return; }
    const call = dict(value);
    if (call.result != null) visitResult(call.result);
  };
  // Only tool results count: question text and model prose are excluded.
  visitCalls(item.tool_trace);
  return found;
}

function evidenceTime(seconds: number | undefined): string {
  if (seconds == null) return "";
  const minutes = Math.floor(seconds / 60);
  const remainder = Math.floor(seconds % 60);
  return `${minutes}:${String(remainder).padStart(2, "0")}`;
}

/** 1.0 `media.file_name || media.media_id || media.image_id`。 */
function mediaLabel(media: MediaLike): string {
  return esc(media.file_name) || esc(media.media_id) || esc(media.image_id);
}

/** 1.0 `:3529-3537` 共用的图片瓦片（含视频证据帧）。 */
function MediaTile({
  media,
  onOpenImage,
  unmatched = false,
  unmatchedLabel = "未命中",
}: {
  media: MediaLike;
  onOpenImage: (media: MediaLike) => void;
  unmatched?: boolean;
  unmatchedLabel?: string;
}) {
  const src = imageUrl(media);
  return (
    <div className="image-tile">
      {isVideoMedia(media) && src ? (
        <video
          src={src}
          controls
          playsInline
          preload="metadata"
          onLoadedMetadata={(event) => pauseVideoAtEvidenceFrame(media, { target: event.currentTarget })}
        ></video>
      ) : src ? (
        <img src={src} alt={esc(media.file_name)} loading="lazy" onClick={() => onOpenImage(media)} />
      ) : (
        <span className="image-empty">无媒体</span>
      )}
      <span className="image-label">
        {mediaLabel(media)}
        {unmatched ? <em> · {unmatchedLabel}</em> : null}
      </span>
    </div>
  );
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
        <span className={`score ${scoreClass(s.judge?.score)} judge-round-${judgeRoundState(s, activeRejudge)}`}>
          {judgeScoreLabel(s, activeRejudge)}
        </span>
        {Number(s.ground_truth_count) > 0 && summary.retrieval_metrics_applicable !== false && (
          <span className="muted small">
            精确率 {fmtPct(s.retrieval_precision)} · 召回率 {fmtPct(s.retrieval_recall)} · F1{" "}
            {fmtPct(s.retrieval_f1)} · 命中 {esc(s.matched_count)}/{esc(s.ground_truth_count)}
          </span>
        )}
        {s.evidence_judge?.score != null && (
          <span className={`score ${scoreClass(s.evidence_judge.score)}`}>证据 {esc(s.evidence_judge.score)}</span>
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
  review?: ReviewDraft;
  onReviewChange?: (patch: ReviewDraft) => void;
}

function QaItemDetail({
  detail,
  albumId,
  modelLabel,
  onOpenImage,
  onOpenJudgeInput,
}: QaItemDetailProps) {
  const turns = conversationTurns(detail);
  const chain = itemLayerChain(detail, albumId);
  const taskJudge = dict(detail.task_judge);
  const evidenceJudge = dict(detail.evidence_judge);
  // 两个媒体区：候选 vs 显式交付，分开取、分开渲染。
  const recallMedia = toolRecallMedia(detail, albumId).map((media) => {
    const keyframeId = String(media.source_keyframe_asset_id || "");
    if (!keyframeId) return media;
    const seconds = Number(media.source_timestamp_sec);
    return {
      ...media,
      asset_id: keyframeId,
      media_id: keyframeId,
      media_type: "image",
      media_url: undefined,
      file_name: Number.isFinite(seconds) ? `检索关键帧 ${seconds.toFixed(1)}s` : "检索关键帧",
    };
  });
  const speechEvidence = retrievedSpeechEvidence(detail);
  const evidenceMedia = itemEvidenceMedia(detail, albumId);
  const gtMedia = itemMedia(detail, albumId, true);
  const timing = itemTimingBreakdown(detail);
  const qaSegments = qaLatencySegments(detail);
  const toolTrace = hasToolTrace(detail);
  const qualityReason = judgeReason(detail.judge);
  const behaviorReason = judgeReason(detail.task_judge);
  const evidenceReason = judgeReason(detail.evidence_judge);

  return (
    <>
      <div className="qa-meta-tags">
        {detail.benchmark_group && !detail.ability_family_ids?.some(id => id === "H" || id === "I") ? <span>{taxonomyLabel(detail.benchmark_group)}</span> : null}
        {(detail.ability_family_ids || []).filter(id => id === "H" || id === "I").map(id => <span key={id}>{abilityLabel(id, detail.ability_taxonomy_version)}</span>)}
        {(detail.ability_primary_ids || []).map(id => <span key={id}>{abilityLabel(id[0], detail.ability_taxonomy_version)} · {abilityLabel(id, detail.ability_taxonomy_version)}</span>)}
        {detail.task_type ? <span>{taxonomyLabel(detail.task_type)}</span> : null}
        {detail.question_type ? <span>{taxonomyLabel(detail.question_type)}</span> : null}
        {detail.angle ? <span>{detail.angle}</span> : null}
        {detail.difficulty ? <span>{taxonomyLabel(detail.difficulty)}</span> : null}
        {detail.answerability ? <span>{taxonomyLabel(detail.answerability)}</span> : null}
        {(detail.tags ?? []).map((tag) => (
          <span key={tag} className="qa-run-tag">
            {tag}
          </span>
        ))}
      </div>
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
          <div className="capability-grid">
            <span>
              <small>{turns.length > 1 ? "最终一轮回答行为" : "回答行为"}</small>
              <b>{taskJudge.correct === true ? "符合预期" : taskJudge.correct === false ? "不符合预期" : "未评分"}</b>
              <small>{taskDecisionLabel(detail)}</small>
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
          <h4>工具召回关键帧图片（{recallMedia.length}）</h4>
          <div className="image-grid">
            {recallMedia.map((media, mediaIndex) => (
              <MediaTile
                key={`${esc(media.asset_id) || esc(media.file_name)}-${mediaIndex}`}
                media={media}
                onOpenImage={onOpenImage}
              />
            ))}
            {recallMedia.length === 0 && <span className="muted small">检索未返回可识别媒体</span>}
          </div>
          <h4>工具召回语音转写（{speechEvidence.length}）</h4>
          {speechEvidence.length ? (
            <ul className="qa-speech-evidence">
              {speechEvidence.map((entry, index) => (
                <li key={`${entry.start ?? "na"}-${index}`}>
                  {entry.start != null ? <time>{evidenceTime(entry.start)}{entry.end != null ? `–${evidenceTime(entry.end)}` : ""}</time> : null}
                  <span>{esc(entry.text)}</span>
                </li>
              ))}
            </ul>
          ) : <p className="muted small">检索工具没有返回 ASR 转写文本</p>}
          <h4>模型使用图片（{evidenceMedia.length}）</h4>
          <div className="image-grid">
            {evidenceMedia.slice(0, 3).map((media, mediaIndex) => (
              <MediaTile
                key={`used-${esc(media.asset_id) || esc(media.file_name)}-${mediaIndex}`}
                media={media}
                onOpenImage={onOpenImage}
              />
            ))}
            {evidenceMedia.length === 0 && (
              <span className="muted small">回答依据图片为空（模型未选择交付图）</span>
            )}
          </div>
          {evidenceMedia.length > 3 && (
            <details className="qa-detail-block">
              <summary>查看更多使用图片（{evidenceMedia.length - 3}）</summary>
              <div className="image-grid">
                {evidenceMedia.slice(3).map((media, mediaIndex) => (
                  <MediaTile
                    key={`used-more-${esc(media.asset_id) || esc(media.file_name)}-${mediaIndex}`}
                    media={media}
                    onOpenImage={onOpenImage}
                  />
                ))}
              </div>
            </details>
          )}
        </div>
        <div>
          <h4>正确答案</h4>
          <p>{esc(detail.reference_answer)}</p>
          <h4>检索 GT 媒体（{gtMedia.length}）</h4>
          <div className="image-grid">
            {gtMedia.map((media, mediaIndex) => (
              <MediaTile
                key={`${esc(media.asset_id) || `${esc(media.media_type)}-${esc(media.media_id)}`}-${mediaIndex}`}
                media={media}
                unmatched={media.matched === false}
                unmatchedLabel={Number(detail.metrics_contract_version) === 2 ? "未交付" : "未命中"}
                onOpenImage={onOpenImage}
              />
            ))}
          </div>
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
        <DebugTracePanel detail={detail} modelLabel={modelLabel} />
      </section>
      <div className="item-footer">
        <button className="judge-details-trigger" type="button" onClick={() => onOpenJudgeInput(detail)}>
          JUDGE 模型输入 <span>↗</span>
        </button>
      </div>

    </>
  );
}
