import { describe, expect, it } from "vitest";

import { effectiveRunSummary, nativeCompletion, macroMetrics, microMetrics, nearestRankPercentile } from "./runSummary.ts";
import type { Run } from "../api/types.ts";

// 夹具原则：每一组都要让**两种口径给出不同答案**，否则测不出「照搬了哪一种」。
// 1.0 参考：`services/photobench/frontend/src/App.vue`（只读权威）。

describe("nearestRankPercentile（1.0 :379-383，nearest-rank 而非线性插值）", () => {
  const values = [10, 20, 30, 40];

  it("4 点取 p95 = 最大值 40：线性插值会给 38.5，floor 口径会给 30", () => {
    // ceil(4 * 0.95) - 1 = ceil(3.8) - 1 = 3 → 排序后第 4 个 = 40
    expect(nearestRankPercentile(values, 0.95)).toBe(40);
    // 与线性插值明确不同：
    expect(nearestRankPercentile(values, 0.95)).not.toBe(38.5);
    expect(nearestRankPercentile(values, 0.95)).not.toBe(30);
  });

  it("空集 / 全非数回 null", () => {
    expect(nearestRankPercentile([], 0.95)).toBeNull();
    expect(nearestRankPercentile(["x", undefined, NaN], 0.95)).toBeNull();
    // 缺失样本不参与统计，不能把 null 变成 0。
    expect(nearestRankPercentile([null], 0.95)).toBeNull();
  });
});

describe("micro（按题汇总）vs macro（按题平均）必须给出不同答案", () => {
  // 两题：题1 全中（P=R=F1=1），题2 全不中（P=R=F1=0）。
  const items = [
    { media_retrieval_counts: { gt: 1, predicted: 1, matched: 1 } },
    { media_retrieval_counts: { gt: 3, predicted: 3, matched: 0 } },
  ];

  it("micro 先累加再算：gt=4，matched=1 → F1=0.25", () => {
    expect(microMetrics(items, "media").f1).toBeCloseTo(0.25, 10);
  });

  it("macro 先算每题再平均：F1=(1+0)/2=0.5", () => {
    expect(macroMetrics(items, "media").f1).toBeCloseTo(0.5, 10);
  });

  it("两种口径确实不等（否则这条夹具白造）", () => {
    expect(microMetrics(items, "media").f1).not.toBe(macroMetrics(items, "media").f1);
  });
});

describe("effectiveRunSummary：已存 summary 优先于现算（1.0 的 `saved.X ?? 现算`）", () => {
  it("新版一致性事实保留合法0分并排除缺分", () => {
    const result = effectiveRunSummary({
      items: [
        { judge: { score: 0, consistency_version: 2 } },
        { judge: { score: 2, consistency_version: 2 } },
        { judge: { score: null, consistency_version: 2 } },
      ],
    } as unknown as Run);
    expect(result.judge_valid_count).toBe(2);
    expect(result.answer_quality_mean).toBe(1);
  });

  it("评分只消费后端标准分，不将布尔值或混合文字转成有效分数", () => {
    const result = effectiveRunSummary({ items: [
      { judge: { score: true } }, { judge: { score: "得2分" } },
      { judge: { score: 1, score_derivation: { rule_version: 1, source: "raw_text", score: 1 } } },
    ] } as unknown as Run);
    expect(result.judge_valid_count).toBe(1);
    expect(result.answer_quality_mean).toBe(1);
  });

  // 后端存的值与 items 现算**故意不同**：把 `??` 改成「一律现算」这条必须挂。
  const run = {
    run_id: "saved-wins",
    summary: {
      completed: 999,
      total: 111,
      judge_valid_count: 7,
      answer_quality_mean: 1.75,
      retrieval_recall_macro: 0.123,
      llm_prompt_tokens_p95: 4242,
    },
    items: [
      { answerability: "answerable", judge: { score: 2, consistency_status: "ok" } },
      { answerability: "answerable", judge: { score: 0, consistency_status: "ok" } },
    ],
  } as unknown as Run;
  const summary = effectiveRunSummary(run);

  it("completed / total / judge_valid_count 用已存值（现算会分别给 2 / 2 / 2）", () => {
    expect(summary.completed).toBe(999);
    expect(summary.total).toBe(111);
    expect(summary.judge_valid_count).toBe(7);
    expect(summary.completed).not.toBe(2);
  });

  it("answer_quality_mean 用已存 1.75（现算 (2+0)/2 = 1）", () => {
    expect(summary.answer_quality_mean).toBe(1.75);
    expect(summary.answer_quality_mean).not.toBe(1);
  });

  it("retrieval_recall_macro / llm_prompt_tokens_p95 用已存值", () => {
    expect(summary.retrieval_recall_macro).toBe(0.123);
    expect(summary.llm_prompt_tokens_p95).toBe(4242);
  });

  it("summary 里没有的字段才现算（exact_accuracy = 1/2）", () => {
    expect(summary.exact_accuracy).toBe(0.5);
    expect(summary.judge_distribution).toEqual({ "0": 1, "2": 1 });
  });
});

describe("effectiveRunSummary：typedRetrieval 分支走 media micro/macro（1.0 :487-488 / :481）", () => {
  const run = {
    run_id: "typed",
    summary: {},
    items: [
      {
        answerability: "answerable",
        retrieval_media_refs: [{ media_type: "image", media_id: "a.jpg" }],
        media_retrieval_counts: { gt: 1, predicted: 1, matched: 1 },
      },
      {
        answerability: "answerable",
        retrieval_media_refs: [{ media_type: "image", media_id: "b.jpg" }],
        media_retrieval_counts: { gt: 3, predicted: 3, matched: 0 },
      },
    ],
  } as unknown as Run;
  const summary = effectiveRunSummary(run);

  it("micro 口径 recall = 1/4", () => {
    expect(summary.retrieval_recall_micro).toBeCloseTo(0.25, 10);
  });

  it("macro 口径 recall = (1+0)/2", () => {
    expect(summary.retrieval_recall_macro).toBeCloseTo(0.5, 10);
  });

  it("scope 标为 all_media，且两种口径不等", () => {
    expect(summary.retrieval_metric_scope).toBe("all_media");
    expect(summary.retrieval_recall_micro).not.toBe(summary.retrieval_recall_macro);
  });
});


it("build 的运行有效性为未适用，缺失样本不进入分位数", () => {
  expect(effectiveRunSummary({ run_id: "build", mode: "build", qa_count: 0, items: [], summary: { run_valid: null } }).run_valid).toBeNull();
  expect(nearestRankPercentile([null, undefined, "", false, 10, 20], 0.5)).toBe(10);
});

it.each(["unanswerable", " INSUFFICIENT_EVIDENCE "])("%s 排除图视频指标，部分可答及拒答质量保留", (answerability) => {
  const counts = { gt: 1, predicted: 1, matched: 1 };
  const refusal = { answerability, retrieval_media_refs: [{ media_type: "image", media_id: "a.jpg" }], media_retrieval_counts: { gt: 100, predicted: 100, matched: 0 }, judge: { score: 2 } };
  const partial = { answerability: "partially_answerable", retrieval_media_refs: [{ media_type: "image", media_id: "b.jpg" }], media_retrieval_counts: counts, image_retrieval_counts: counts, video_retrieval_counts: counts, judge: { score: 0 } };
  const summary = effectiveRunSummary({ items: [refusal, partial] } as unknown as Run);
  expect(summary.retrieval_excluded_unanswerable_count).toBe(1);
  expect(summary.retrieval_recall_micro).toBe(1);
  expect(summary.retrieval_recall_macro).toBe(1);
  expect(summary.image_retrieval_recall_micro).toBe(1);
  expect(summary.video_retrieval_recall_micro).toBe(1);
  expect(summary.judge_valid_count).toBe(2);
  expect(summary.answer_quality_mean).toBe(1);
  const refusalOnly = effectiveRunSummary({ items: [refusal] } as unknown as Run);
  expect(refusalOnly.retrieval_recall_micro).toBeNull();
  expect(refusalOnly.retrieval_precision_micro).toBeNull();
  expect(refusalOnly.retrieval_f1_micro).toBeNull();
});


it("预算内 partial 回答按流程完成计入，重算旧版错误的0%；多轮、超预算、未知保留", () => {
  const turn = { termination_reason: "answered_with_gap", agent_status: "partial", answer: "answer",
    agent2_trace: { budget_outcome: { model_steps: 3, max_steps: 8 } } };
  expect(nativeCompletion(turn)).toBe(true);
  expect(nativeCompletion({ ...turn, termination_reason: "policy_refusal", agent_status: "complete", agent2_trace: { budget_outcome: { model_steps: 0, max_steps: 8 } } })).toBe(true);
  expect(nativeCompletion({ ...turn, runtime_turns: [turn, { ...turn, termination_reason: "timeout" }] })).toBe(false);
  expect(nativeCompletion({ ...turn, agent2_trace: { budget_outcome: { model_steps: 9, max_steps: 8 } } })).toBe(false);
  expect(nativeCompletion({ ...turn, agent2_trace: {} })).toBeNull();
  const result = effectiveRunSummary({ summary: { qa_completion_valid_count: 10, qa_completion_within_steps_rate: 0 },
    items: [{ ...turn, metrics_contract_version: 2 }] } as unknown as Run);
  expect(result.qa_completion_within_steps_rate).toBe(1);
  expect(result.qa_completion_valid_count).toBe(1);
});

it("JSON对象新指标汇总真实解析分母，零成功保持0，历史无事实保持空", () => {
  const result = effectiveRunSummary({ items: [{ agent_stability: {
    structured_json_requested: 3, structured_json_total: 2, structured_json_success: 0,
  } }] } as unknown as Run);
  expect(result.structured_json_requested).toBe(3);
  expect(result.structured_json_rate).toBe(0);
  expect(effectiveRunSummary({ items: [] } as unknown as Run).structured_json_rate).toBeNull();
});
