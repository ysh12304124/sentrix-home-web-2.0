/**
 * 单题级证据派生。移植 1.0 `services/photobench/frontend/src/App.vue`
 * （只读，唯一权威）中**与 media URL 构造无关**的那部分：
 *
 *   `itemRetrievalMetrics`(:566) / `itemParseRate`(:575) / `conversationTurns`(:611) /
 *   `conversationIdLabel`(:614) / `conversationContextLabel`(:617) / `turnScore`(:621) /
 *   `itemMedia`(:660) / `toolRecallMedia`(:694) / `itemEvidenceMedia`(:703) / `isDirectEvidence`(:712)。
 *
 * URL 构造（`inferMediaType` / `mediaKey` / `mediaRefs` / `imageUrl` / `albumLocalUrl` /
 * `decorateMedia` / `isVideoMedia`）全在 `../api/media.ts`，这里**只 import 不重复实现**。
 * `albumId` 是 1.0 模块级 `activeRun.album_id` 的显式化参数（与 `media.ts` 同口径）。
 *
 * ────────────────────────────────────────────────────────────────────────
 *  **最重要的口径约束（1.0 `:703-711` 注释原文）**：
 *
 *    `toolRecallMedia`  = 工具召回候选（`retrieved_candidate_*` / `retrieved_file_names`），
 *                         即 search 等找图工具返回的检索候选集。
 *    `itemEvidenceMedia`= 模型**显式交付**图（`predicted_*`）；**未交付 → 空**，
 *                         **绝不回退到 evidence/retrieved 候选**。
 *
 *  两者是**两件事**，本模块**不合并**、`itemEvidenceMedia` 也**不在空时回落到**
 *  `toolRecallMedia`。（`itemMedia` 的交付口径是另一条：它按 1.0 的顺序**会**最终
 *  回落到候选窗口——那是 1.0 的既定行为，照搬，别和上面这条混淆。）
 * ────────────────────────────────────────────────────────────────────────
 */
import { albumLocalUrl, decorateMedia, inferMediaType, mediaKey, mediaRefs } from "../api/media.ts";
import type { MediaLike } from "../api/media.ts";
import { finiteMetric, fmtPct } from "./format.ts";
import { retrievalMetricEligible } from "./retrieval.ts";
import type { ConversationTurn } from "../api/types.ts";

type Dict = Record<string, unknown>;

function dict(value: unknown): Dict {
  return typeof value === "object" && value !== null ? (value as Dict) : {};
}

/** 1.0 的 `x || []` / `x?.length` 语义：非数组一律当空数组。 */
function list<T>(value: unknown): T[] {
  return Array.isArray(value) ? (value as T[]) : [];
}

// ── 1.0 :566-579 检索指标 / 解析率 ──────────────────────────────────────

/** 1.0 `:566-574`。无标准媒体直接给说明；是否带 `retrieval_media_refs` 决定「历史图片口径」。 */
export function itemRetrievalMetrics(item: MediaLike | null | undefined): string {
  if (!retrievalMetricEligible(item)) return "不适用：不可回答题不计入检索指标";
  if (!mediaRefs(item).length) return "本题无标准媒体，不计入检索指标";
  const total = `总媒体 P ${fmtPct(item?.media_retrieval_precision ?? item?.retrieval_precision)} · R ${fmtPct(item?.media_retrieval_recall ?? item?.retrieval_recall)} · F1 ${fmtPct(item?.media_retrieval_f1 ?? item?.retrieval_f1)}`;
  if (!Object.prototype.hasOwnProperty.call(item || {}, "retrieval_media_refs")) return `${total} · 历史图片口径`;
  const parts = [total];
  if (mediaRefs(item).some((ref) => ref.media_type === "image")) parts.push(`图片 R ${fmtPct(item?.image_retrieval_recall)}`);
  if (mediaRefs(item).some((ref) => ref.media_type === "video")) parts.push(`视频 R ${fmtPct(item?.video_retrieval_recall)}`);
  return parts.join(" · ");
}

/** 原生工具参数和旧版模型动作分开计数；成功数缺失时不补零。 */
export function itemParseRate(item: MediaLike | null | undefined): string {
  const stability = dict(item?.agent_stability);
  const parts: string[] = [];
  for (const [prefix, label] of [["tool_argument_json", "工具参数 JSON"], ["json_parse", "模型动作 JSON"]]) {
    const total = finiteMetric(stability[`${prefix}_total`]);
    if (total == null || total < 0) continue;
    if (total === 0) { parts.push(`${label}：无解析调用`); continue; }
    const success = finiteMetric(stability[`${prefix}_success`]);
    if (success == null || success < 0 || success > total) { parts.push(`${label}：共 ${total} 次，成功数未记录`); continue; }
    parts.push(`${label}：${success}/${total} 成功（${fmtPct(success / total)}）`);
  }
  return parts.join("；") || "未记录";
}

// ── 1.0 :611-621 多轮对话 ──────────────────────────────────────────────

/** 1.0 `:611-613`。 */
export function conversationTurns(item: MediaLike | null | undefined): ConversationTurn[] {
  const declared = Array.isArray(item?.conversation) ? (item.conversation as ConversationTurn[]) : [];
  const runtime = Array.isArray(item?.runtime_turns) ? (item.runtime_turns as ConversationTurn[]) : [];
  return Array.from({ length: Math.max(declared.length, runtime.length) }, (_, index) => ({
    ...(declared[index] || {}), ...(runtime[index] || {}),
  }));
}

/** 1.0 `:614-616`。优先 item 的 `conversation_id`，再找轮次里的，最后给历史文案。 */
export function conversationIdLabel(item: MediaLike | null | undefined): string {
  return (
    (item?.conversation_id as string) ||
    conversationTurns(item).find((turn) => turn?.conversation_id)?.conversation_id ||
    "历史结果未记录会话 ID"
  );
}

/** 1.0 `:617-620`。取不到 `context_turn_count` 时**用下标**当轮数。 */
export function conversationContextLabel(turn: MediaLike | null | undefined, index: number): string {
  const raw = dict(turn).context_turn_count;
  const count = finiteMetric(raw) ?? index;
  return count > 0 ? `本轮携带前 ${count} 轮对话上下文` : "首轮，无历史上下文";
}

/** 1.0 `:621-623`。只有 `0/1/2` 给分，其余（含 `"2"` 字符串）回 `不适用`。 */
export function turnScore(score: unknown): string {
  return ([0, 1, 2] as unknown[]).includes(score) ? `${score} 分` : "不适用";
}

// ── 1.0 :660-693 交付口径 `itemMedia`（gt / 非 gt） ────────────────────

/**
 * 1.0 `:660-693`。
 *
 * **gt 分支**：`gt_media` → （有 `gt_images` 且 item **没有** `retrieval_media_refs` 时）
 * `gt_images` → 由 `mediaRefs` 合成（视频补 `.mp4`，标 `matched`）。
 * **交付分支**：`predicted_*` → `evidence_source_*` → **最后回落到候选窗口**
 * （`retrieved_candidate_*` 截前 6，`retrieved_file_names` 截前 6）。
 *
 * 注意本函数的 `predicted_file_names` / `retrieved_file_names` 分支**不套 `decorateMedia`**
 * （直接 map），而 `evidence_source_file_names` 套——1.0 三处不一致，照搬。
 */
export function itemMedia(item: MediaLike | null | undefined, albumId: string, gt = false): MediaLike[] {
  if (gt) {
    const gtMedia = list<MediaLike>(item?.gt_media);
    if (gtMedia.length) return decorateMedia(gtMedia, albumId);
    const gtImages = list<MediaLike>(item?.gt_images);
    if (gtImages.length && !Object.prototype.hasOwnProperty.call(item || {}, "retrieval_media_refs")) {
      return decorateMedia(gtImages, albumId);
    }
    return decorateMedia(
      mediaRefs(item).map((ref) => {
        const fileName =
          ref.media_type === "video" && !/\.[^.]+$/.test(ref.media_id) ? `${ref.media_id}.mp4` : ref.media_id.split("/").pop();
        return {
          ...ref,
          file_name: fileName,
          matched: list<unknown>(item?.matched_file_names).includes(fileName),
        };
      }),
      albumId,
    );
  }
  // 交付口径（E）：模型"召回/回答来源"= 模型显式交付的图（selected/predicted），
  // 不再把上游 evidence 全量候选冒充回答来源。历史 run 只有 evidence/retrieved 字段时再回退。
  const predicted = list<MediaLike>(item?.predicted_media);
  if (predicted.length) return decorateMedia(predicted, albumId);
  const predictedImages = list<MediaLike>(item?.predicted_images);
  if (predictedImages.length) return decorateMedia(predictedImages, albumId);
  const predictedNames = list<string>(item?.predicted_file_names);
  if (predictedNames.length) {
    return predictedNames.map((file_name) => ({ file_name, media_type: inferMediaType(file_name), media_url: albumLocalUrl(albumId, file_name) }));
  }
  const evidenceSource = list<MediaLike>(item?.evidence_source_media);
  if (evidenceSource.length) return decorateMedia(evidenceSource, albumId);
  const evidenceSourceImages = list<MediaLike>(item?.evidence_source_images);
  if (evidenceSourceImages.length) return decorateMedia(evidenceSourceImages, albumId);
  const evidenceSourceNames = list<string>(item?.evidence_source_file_names);
  if (evidenceSourceNames.length) {
    return decorateMedia(
      evidenceSourceNames.map((file_name) => ({ file_name, media_type: inferMediaType(file_name), media_url: albumLocalUrl(albumId, file_name) })),
      albumId,
    );
  }
  // A validator may leave all candidates as candidate_only.  They are not
  // answer evidence, but hiding them makes a healthy retrieval look empty
  // in the evaluation UI.  Show a bounded representative window here; the
  // full candidate set remains in retrieval metrics and the trace.
  const candidateMedia = list<MediaLike>(item?.retrieved_candidate_media);
  if (candidateMedia.length) return decorateMedia(candidateMedia.slice(0, 6), albumId);
  const candidateImages = list<MediaLike>(item?.retrieved_candidate_images);
  if (candidateImages.length) return decorateMedia(candidateImages.slice(0, 6), albumId);
  return list<string>(item?.retrieved_file_names)
    .slice(0, 6)
    .map((file_name) => ({ file_name, media_type: inferMediaType(file_name), media_url: albumLocalUrl(albumId, file_name) }));
}

// ── 1.0 :694-717 候选 vs 交付（两条**不同**的口径） ─────────────────────

/**
 * 1.0 `:694-702`。**工具召回候选**：`retrieved_candidate_media` → `_images` →
 * `retrieved_file_names`；**没有**才 `[]`。这是「工具找回来的候选」，不是「模型用了什么」。
 */
export function toolRecallMedia(item: MediaLike | null | undefined, albumId: string): MediaLike[] {
  const candidateMedia = list<MediaLike>(item?.retrieved_candidate_media);
  if (candidateMedia.length) return decorateMedia(candidateMedia, albumId);
  const candidateImages = list<MediaLike>(item?.retrieved_candidate_images);
  if (candidateImages.length) return decorateMedia(candidateImages, albumId);
  const names = list<string>(item?.retrieved_file_names);
  if (names.length) {
    return names.map((file_name) => ({ file_name, media_type: inferMediaType(file_name), media_url: albumLocalUrl(albumId, file_name) }));
  }
  return [];
}

/**
 * 1.0 `:703-711`。**模型使用图片** = 模型显式交付图（`predicted_media` / `predicted_images`
 * / `predicted_file_names`）；未显式交付 → **空**（"回答依据图片为空"）。
 *
 * **绝不回退到 evidence/retrieved 候选**——否则又变成「回答来源 == 完整候选集」。
 * 这里空就是空，不许回落到 `toolRecallMedia`。
 */
export function itemEvidenceMedia(item: MediaLike | null | undefined, albumId: string): MediaLike[] {
  // 1.0 用 `a || b || []` 的**引用**回退：空的 `predicted_media`（`[]` 是 truthy）**不会**
  // 落到 `predicted_images`，随后 length 为 0 直接转去 names。别改成 `length` 判空回退。
  const media = (item?.predicted_media || item?.predicted_images || []) as MediaLike[];
  if (media.length) return decorateMedia(media, albumId);
  const names = (item?.predicted_file_names || []) as string[];
  if (names.length) {
    return decorateMedia(
      names.map((file_name) => ({ file_name, media_type: inferMediaType(file_name), media_url: albumLocalUrl(albumId, file_name) })),
      albumId,
    );
  }
  return [];
}

/**
 * 1.0 `:712-717`。`media` 是字符串或引用；与 `answer_evidence_media_refs` 及
 * `answer_claims[].evidence_media_refs` 的 key 逐一比对，命中即「直接证据」。
 */
export function isDirectEvidence(item: MediaLike | null | undefined, media: string | MediaLike): boolean {
  const ref = typeof media === "string" ? { media_id: media, media_type: inferMediaType(media) } : media;
  const key = mediaKey(ref);
  const answerRefs = mediaRefs(item, "answer_evidence");
  const claimRefs = list<MediaLike>(item?.answer_claims).flatMap((claim) => mediaRefs(claim, "evidence"));
  return [...answerRefs, ...claimRefs].some((candidate) => mediaKey(candidate) === key);
}
