import { taxonomyLabel, abilityLabel } from "../../lib/qaTaxonomy.ts";
/**
 * 单条 QA 的审阅卡。逐行移植 1.0 `App.vue:3703-3761`：
 *
 *   - 头部（`:3704-3717`）：序号、行为徽章 / 题型 / 可答性 / 难度 / 标签 / 多轮徽章、qa_id；
 *   - 对话面板（`:3719-3734`）：逐轮 `问 / 答` 气泡，GT 侧标签由 `qaReferenceLabel` 决定；
 *   - 检索 GT 媒体（`:3735-3745`）：`retrieval_media_refs` 的缩略图 / 视频 / 缺失占位；
 *   - 底部（`:3747-3760`）：证据声明 / 人物引用 / 题目元数据三个 `<details>`。
 *
 * 用到的 1.0 局部函数按原口径落在本文件（本切片不新增 lib 模块、也不改现有 lib）：
 *   `qaConversationTurns`(:2816) / `qaEvidenceRefs`(:2798) / `qaIsVideoEvidence`(:2801) /
 *   `qaHasVideoEvidence`(:2805) / `qaClaimMediaRefs`(:2813)。
 *
 *   - `qaEvidenceRefs(item)` 就是 `mediaRefs(item, "retrieval")`、`qaClaimMediaRefs(claim)` 就是
 *     `mediaRefs(claim, "evidence")`，直接复用 `api/media.ts`；
 *   - `isDirectEvidence`(:712) 已移植在 `lib/evidence.ts`，直接 import；
 *   - **`qaMediaUrl`(:2808) 与 `api/media.ts` 的 `imageUrl`(:553) 逻辑逐字相同**——1.0 的
 *     `qaMediaUrl(albumId, item, media)` 连前两个参数都不读，故此处直接用 `imageUrl`，
 *     不重复实现同一个函数。
 */
import { imageUrl, inferMediaType, mediaKey, mediaRefs } from "../../api/media.ts";
import type { ConversationTurn, MediaRef, QaRow } from "../../api/types.ts";
import { isDirectEvidence } from "../../lib/evidence.ts";
import { qaActionBadge, qaAnswerabilityLabel, qaReferenceLabel, qaTypeLabel } from "../../lib/qaBrowser.ts";

export interface QaReviewCardProps {
  item: QaRow;
  /** 列表下标：用于序号 `01` 与 `qa-<n>` 回退（1.0 `:3705` / `:3715`）。 */
  index: number;
  /** 点击 GT 图片时打开 lightbox（1.0 `:3740` 写根级 `lightbox`）。 */
  onOpenMedia?: (url: string, name: string) => void;
}

/** 1.0 `:2816-2820`。有 `conversation` 数组就用它，否则退化成「题干 / 期望行为 / 答案」单轮。 */
function qaConversationTurns(item: QaRow): ConversationTurn[] {
  if (Array.isArray(item.conversation)) return item.conversation;
  return [
    {
      message: item.question,
      expected_action: item.expected_action,
      reference_answer: item.answer,
    },
  ];
}

/** 1.0 `:2798-2800` = `mediaRefs(item, "retrieval")`。 */
function qaEvidenceRefs(item: QaRow): MediaRef[] {
  return mediaRefs(item);
}

/** 1.0 `:2801-2804`：目标视频 id 命中，或媒体类型判为 video。 */
function qaIsVideoEvidence(item: QaRow, media: MediaRef): boolean {
  return item.video_id === media.media_id || inferMediaType(media.media_id, media.media_type) === "video";
}

/** 1.0 `:2805-2807`。 */
function qaHasVideoEvidence(item: QaRow): boolean {
  return qaEvidenceRefs(item).some((ref) => ref.media_type === "video");
}

/** 1.0 `:2813-2815` = `mediaRefs(claim, "evidence")`。 */
function qaClaimMediaRefs(claim: Record<string, unknown>): MediaRef[] {
  return mediaRefs(claim, "evidence");
}

/** 1.0 的 `x && x.length` / `x || []` 语义：非数组一律当空数组。 */
function list<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

/** 1.0 反复出现的 `media_id.split('/').pop()`。 */
function mediaFileName(media: MediaRef): string {
  return media.media_id.split("/").pop() || "";
}

export function QaReviewCard({ item, index, onOpenMedia }: QaReviewCardProps) {
  const turns = qaConversationTurns(item);
  const evidence = qaEvidenceRefs(item);
  const hasVideo = qaHasVideoEvidence(item);
  const claims = list<Record<string, unknown>>(item.answer_claims);
  const persons = list<Record<string, unknown>>(item.person_references);
  const hasMeta = Boolean(
    item.query_anchors || item.scope_anchor || item.required_evidence_sources || item.event_id || item.angle,
  );

  return (
    <article className="qa-review-card">
      <header className="qa-review-header">
        <div className="qa-review-index">{String(index + 1).padStart(2, "0")}</div>
        <div className="qa-review-title">
          <div className="qa-browser-meta">
            {item.benchmark_group && !item.ability_family_ids?.some(id => id === "H" || id === "I") ? <span className="qa-type-tag">{taxonomyLabel(item.benchmark_group)}</span> : null}
            {(item.ability_family_ids || []).filter(id => id === "H" || id === "I").map(id => <span key={id}>{abilityLabel(id, item.ability_taxonomy_version)}</span>)}
        {(item.ability_primary_ids || []).map(id => <span key={id} className="qa-type-tag">{abilityLabel(id[0], item.ability_taxonomy_version)} · {abilityLabel(id, item.ability_taxonomy_version)}</span>)}
            <span className={`qa-badge badge-${item.expected_action || "answer"}`}>
              {qaActionBadge(item.expected_action)}
            </span>
            <span className="qa-type-tag">{item.task_type ? `${taxonomyLabel(item.task_type)} · ` : ""}{qaTypeLabel(item.question_type)}</span>
            <span className="qa-answerability-tag">{qaAnswerabilityLabel(item.answerability)}</span>
            {item.difficulty ? <span className="qa-difficulty-tag">{taxonomyLabel(item.difficulty)}</span> : null}
            {(item.tags || []).map((tag) => (
              <span key={tag} className="qa-data-tag">
                {tag}
              </span>
            ))}
            {turns.length > 1 ? <span className="multi-turn-badge">{turns.length} 轮</span> : null}
          </div>
          <span className="qa-review-id">{item.qa_id || `qa-${index + 1}`}</span>
        </div>
      </header>

      <div className={`qa-review-layout${evidence.length ? "" : " no-evidence"}`}>
        <div className="qa-dialogue-panel">
          {turns.map((turn, ti) => (
            <div key={ti} className="qa-turn-row">
              <div className="qa-turn-side user-side">
                <div className="qa-speaker">
                  <span className="qa-avatar user-avatar">问</span>
                  <b>用户</b>
                  <small>第 {ti + 1} 轮</small>
                </div>
                <div className="qa-bubble user-bubble">
                  <p>{turn.message}</p>
                  {turn.task_type ? <span>{taxonomyLabel(turn.task_type)} · {taxonomyLabel(turn.question_type)}</span> : null}
                  {turn.expected_action ? (
                    <span className={`bubble-action-hint hint-${turn.expected_action}`}>
                      期望行为 · {qaActionBadge(turn.expected_action)}
                    </span>
                  ) : null}
                </div>
              </div>
              <div className="qa-turn-divider">
                <span>{ti + 1}</span>
              </div>
              <div className="qa-turn-side answer-side">
                <div className="qa-speaker answer-speaker">
                  <small>GT</small>
                  <b>{qaReferenceLabel(turn)}</b>
                  <span className="qa-avatar answer-avatar">答</span>
                </div>
                <div className="qa-bubble answer-bubble">
                  <p>{turn.reference_answer || item.answer || "（无参考答案）"}</p>
                </div>
              </div>
            </div>
          ))}
        </div>

        {evidence.length ? (
          <aside className="qa-evidence-panel">
            <div className="qa-evidence-head">
              <div>
                <span>RETRIEVAL GROUND TRUTH</span>
                <strong>检索 GT 媒体</strong>
                <small>问题对应的直接证据</small>
              </div>
              <b>{evidence.length}</b>
            </div>
            <div className={`gt-gallery${hasVideo ? " video-gallery" : ""}`}>
              {evidence.map((media) => {
                const url = imageUrl(media);
                const video = qaIsVideoEvidence(item, media);
                const direct = isDirectEvidence(item, media);
                return (
                  <div
                    key={mediaKey(media)}
                    className={`gt-thumb-card${direct ? " direct-evidence" : ""}${video ? " video-evidence" : ""}`}
                  >
                    {video && url ? (
                      <video src={url} controls playsInline preload="none" />
                    ) : url ? (
                      <button
                        className="gt-image-button"
                        type="button"
                        onClick={() => onOpenMedia?.(url, mediaFileName(media))}
                      >
                        <img src={url} alt={media.media_id} loading="lazy" />
                      </button>
                    ) : (
                      <span className="image-empty">
                        {media.mapping_status === "ambiguous" ? "媒体标识不唯一" : "Sentrix 后端未找到"}
                      </span>
                    )}
                    <span>
                      {mediaFileName(media)}
                      <em>{video ? "视频证据" : direct ? "直接证据" : "事件相关"}</em>
                    </span>
                  </div>
                );
              })}
            </div>
          </aside>
        ) : null}
      </div>

      <div className="qa-review-foot">
        {claims.length ? (
          <details className="qa-detail-block">
            <summary>
              证据声明 <b>{claims.length}</b>
            </summary>
            {claims.map((claim, ci) => (
              <div key={ci} className="qa-claim">
                <span className="claim-type">{String(claim.support_type || claim.claim_id || "")}</span>
                <span className="claim-text">{String(claim.text || "")}</span>
                {qaClaimMediaRefs(claim).length ? (
                  <span className="claim-evidence">
                    {qaClaimMediaRefs(claim)
                      .map((ref) => `${ref.media_type === "video" ? "视频" : "图片"}:${mediaFileName(ref)}`)
                      .join(" · ")}
                  </span>
                ) : null}
              </div>
            ))}
          </details>
        ) : null}

        {persons.length ? (
          <details className="qa-detail-block">
            <summary>
              人物引用 <b>{persons.length}</b>
            </summary>
            <div className="qa-person-grid">
              {persons.map((person, pi) => (
                <div key={pi} className="qa-person-ref">
                  <span className="person-name">{String(person.name || "")}</span>
                  {list<unknown>(person.aliases).length ? (
                    <small>{list<unknown>(person.aliases).join(" / ")}</small>
                  ) : null}
                  {person.face_id ? <small>Face {String(person.face_id)}</small> : null}
                </div>
              ))}
            </div>
          </details>
        ) : null}

        {hasMeta ? (
          <details className="qa-detail-block">
            <summary>题目元数据</summary>
            <div className="qa-meta-grid">
              {item.query_anchors ? (
                <div>
                  <b>查询锚点</b>
                  <span>{String(item.query_anchors)}</span>
                </div>
              ) : null}
              {item.scope_anchor ? (
                <div>
                  <b>范围锚点</b>
                  <span>{String(item.scope_anchor)}</span>
                </div>
              ) : null}
              {item.required_evidence_sources ? (
                <div>
                  <b>证据源</b>
                  <span>
                    {Array.isArray(item.required_evidence_sources)
                      ? item.required_evidence_sources.join(", ")
                      : String(item.required_evidence_sources)}
                  </span>
                </div>
              ) : null}
              {item.event_id ? (
                <div>
                  <b>事件 ID</b>
                  <span>{String(item.event_id)}</span>
                </div>
              ) : null}
              {item.angle ? (
                <div>
                  <b>考察角度</b>
                  <span>{String(item.angle)}</span>
                </div>
              ) : null}
            </div>
          </details>
        ) : null}
      </div>
    </article>
  );
}
