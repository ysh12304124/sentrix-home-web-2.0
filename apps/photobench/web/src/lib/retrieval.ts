/** Ordinary media metrics exclude questions whose target has no answer evidence. */
export function retrievalMetricEligible(item: Record<string, unknown> | null | undefined): boolean {
  return !["unanswerable", "insufficient_evidence"].includes(String(item?.answerability || "").trim().toLowerCase());
}
