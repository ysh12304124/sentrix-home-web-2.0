/**
 * 反馈草稿按账号隔离。
 *
 * 共用浏览器切换账号时，后一个账号不能读到前一个账号未提交的文字。旧全局键
 * `sentrix.feedback.draft` 不自动分配给任何账号，登录后直接清掉。
 */

const LEGACY_DRAFT_KEY = "sentrix.feedback.draft";
const ACCOUNT_DRAFT_PREFIX = "sentrix.feedback.draft.";

export function feedbackDraftKey(accountId: string): string {
  return `${ACCOUNT_DRAFT_PREFIX}${accountId}`;
}

export function clearLegacyFeedbackDraft(): void {
  try {
    window.localStorage.removeItem(LEGACY_DRAFT_KEY);
  } catch {
    // 存储不可用时主流程不受影响。
  }
}

export function readFeedbackDraft(accountId: string): string {
  try {
    return window.localStorage.getItem(feedbackDraftKey(accountId)) ?? "";
  } catch {
    return "";
  }
}

export function saveFeedbackDraft(accountId: string, body: string): void {
  try {
    window.localStorage.setItem(feedbackDraftKey(accountId), body);
  } catch {
    // 配额满/隐私模式：草稿不落盘，但不阻塞反馈提交。
  }
}

export function clearFeedbackDraft(accountId: string): void {
  try {
    window.localStorage.removeItem(feedbackDraftKey(accountId));
  } catch {
    // 同上。
  }
}
