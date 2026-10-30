import { taxonomyLabels } from "./qaTaxonomy.ts";
// 移植自 1.0 `services/photobench/frontend/src/App.vue`（只读，唯一权威）中
// QA 数据集浏览视图用到的标签函数。每个函数尾注了 1.0 的行号。
//
// 口径纪律：映射未命中时各处回退**各不相同**（原样返回 / `-` / `未分类`），别统一。

type Dict = Record<string, unknown>;

function dict(value: unknown): Dict {
  return typeof value === "object" && value !== null ? (value as Dict) : {};
}

/** 1.0 `:2789-2791` —— 未命中回**原值**，空/缺省回 `未分类`。 */
export function qaTypeLabel(t: unknown): string {
  const labels: Record<string, string> = {
    event_memory_qa: "事件记忆",
    single_evidence_memory_qa: "单图证据",
    relationship_qa: "关系问答",
    multi_turn_clarify: "多轮澄清",
    multi_turn_disambiguation: "多轮消歧",
    ambiguous_retrieval: "模糊检索",
    evidence_insufficient: "证据不足",
    unsupported_retrieval: "无依据检索",
    instruction_injection: "指令注入",
    prompt_injection: "提示注入",
    data_exfiltration: "数据泄露",
    authority_impersonation: "权限伪造",
    mixed_injection: "混合注入",
    indirect_injection: "间接注入",
    jailbreak_attempt: "越狱尝试",
  };
  const key = typeof t === "string" ? t : "";
  return taxonomyLabels[key] || labels[key] || key || "未分类";
}

/** 1.0 `:2792-2794` —— 未命中回**原值**，空/缺省回 `-`（与 `qaTypeLabel` 的 `未分类` 不同）。 */
export function qaActionBadge(a: unknown): string {
  const labels: Record<string, string> = { answer: "回答", refuse: "拒答", clarify: "澄清" };
  const key = typeof a === "string" ? a : "";
  return taxonomyLabels[key] || labels[key] || key || "-";
}

/** 1.0 `:2795-2797` —— 未命中回**原值**，空/缺省回 `-`。 */
export function qaAnswerabilityLabel(v: unknown): string {
  const labels: Record<string, string> = {
    answerable: "可回答",
    unanswerable: "不可回答",
    ambiguous: "有歧义",
    unsafe_request: "不安全请求",
    answerable_after_clarification: "澄清后可回答",
    mixed: "混合",
  };
  const key = typeof v === "string" ? v : "";
  return taxonomyLabels[key] || labels[key] || key || "-";
}

/** 1.0 `:2821-2823` —— 只有 `clarify` 走「澄清示例」，其余（含缺省）都是「参考回答」。 */
export function qaReferenceLabel(turn: unknown): string {
  return dict(turn).expected_action === "clarify" ? "参考澄清示例" : "参考回答";
}

/** 1.0 `:2565` —— 缺省/空串都当 `full`；未命中回**原值**。 */
export function modeLabel(mode: unknown): string {
  const labels: Record<string, string> = { full: "全链路", reuse: "复用测评", build: "构建相册" };
  const key = typeof mode === "string" && mode ? mode : "full";
  return labels[key] || key;
}

/** 1.0 `:2566` —— 缺省/空串都当 `full`；未命中回 `mode-full`。 */
export function modeBadgeClass(mode: unknown): string {
  const classes: Record<string, string> = { full: "mode-full", reuse: "mode-reuse", build: "mode-build" };
  const key = typeof mode === "string" && mode ? mode : "full";
  return classes[key] || "mode-full";
}
