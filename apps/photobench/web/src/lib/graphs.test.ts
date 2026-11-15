// `src/lib/graphs.ts` 的单测。夹具的职责是**让两种口径给出不同答案**：
//   - `memoryLayerMetrics` / `memoryValidityArchitecture` 各层喂不同的数，若哪层写错取了
//     别的层的字段，断言就会红；
//   - `memoryRecallMatrices` 命中与未命中并存，逐格断言，全 0 / 全满的矩阵证明不了划分；
//   - `chainDetailMedia` / `chainDetailStatements` 给 13 个输入，断言截断。

import { describe, expect, it } from "vitest";
import type {
  KeyframeAnalysis,
  ManifestSummary,
  MemoryEffectiveness,
  Run,
  RunItemDetail,
  RunItemSummary,
  RunSummary,
} from "../api/types";
import {
  activeEvidenceGraphView,
  chainDetailFor,
  chainDetailMedia,
  chainDetailStatements,
  effectiveEvidenceGraph,
  itemLayerChain,
  memoryEvidenceGraph,
  memoryLayerMetrics,
  memoryRecallMatrices,
  memoryValidityArchitecture,
  type GraphLayer,
  type QaDetailMap,
} from "./graphs";

// ── 夹具 ─────────────────────────────────────────────────────────────────

function preview(mediaKind: string, assetId: string, eventId?: string) {
  return {
    media_kind: mediaKind,
    asset_id: assetId,
    media_id: assetId,
    event_context: eventId ? { id: eventId, summary: `${eventId} 摘要` } : null,
  };
}

const KEYFRAME_ANALYSIS = {
  status: "ready",
  assets: { total: 9, source_videos: 2, keyframes: 7, videos_with_keyframes: 2 },
  processing: { realtime_factor: 1.5, retained_frame_ratio: 0.1 },
  retrieval: {
    video_gt_targets: 4,
    video_gt_targets_hit_by_keyframe: 3,
    predicted_keyframes: 8,
    predicted_relevant_keyframes: 5,
  },
  temporal: { scene_count: 4 },
  usefulness: { event_context_count: 9 },
} as unknown as KeyframeAnalysis;

const MEMORY_EFFECTIVENESS = {
  status: "ready",
  data_quality: { graph_ready: true },
  details: {
    visual_calls: { total: 10, effective: 8 },
    ocr_calls: { total: 5, effective: 5 },
  },
} as unknown as MemoryEffectiveness;

const RUN = {
  run_id: "r1",
  album_id: "album-a",
  item_count: 99,
  qa_count: 88,
  input_integrity: { files_checked: 42 },
  phases: { qa_eval: { progress: { completed: 6, total: 9 } } },
} as unknown as Run;

const MANIFESTS = [
  { album_id: "album-a", album_name: "A", face_count: 0, photo_count: 3, video_count: 2, qa_sets: [] },
] as unknown as ManifestSummary[];

const SUMMARY = {
  completed: 1,
  total: 2,
  answer_quality_mean: 1.5,
  exact_accuracy: 0.5,
  agent2_trace: { planner_decision_count: 3, requirement_status_counts: { satisfied: 3, open: 1 } },
  tool_performance: { get_core_memory: { calls: 4, ok_rate: 0.5 } },
} as unknown as RunSummary;

// 展开的这一条 QA：2 个关键帧 + 1 条带事件上下文的证据行。
const EXPANDED_ITEM = {
  index: 0,
  question: "这条问题",
  tool_trace: [
    {
      tool_name: "inspect_photo",
      observation: {
        preview: [preview("video_keyframe", "kf-1"), preview("video_keyframe", "kf-2"), preview("image", "img-1", "ev-1")],
      },
    },
  ],
} as unknown as RunItemDetail;

const QA_DETAILS: QaDetailMap = { 0: EXPANDED_ITEM };

const QA_PAGE_ITEMS = [
  { index: 0, task_type: "T5_event_summary" },
  { index: 1, task_type: "T1_single" },
] as unknown as RunItemSummary[];

// ── memoryLayerMetrics：五层各取不同的数 ──────────────────────────────────

describe("memoryLayerMetrics", () => {
  const view = memoryLayerMetrics({
    run: RUN,
    manifests: MANIFESTS,
    qaDetails: QA_DETAILS,
    qaPageItems: QA_PAGE_ITEMS,
    memoryEffectiveness: MEMORY_EFFECTIVENESS,
    keyframeAnalysis: KEYFRAME_ANALYSIS,
    summary: SUMMARY,
  });

  it("L0 取相册清单的图/视频计数与关键帧资产统计", () => {
    expect(view.layers[0].metric).toBe("5 个媒体");
    expect(view.layers[0].detail).toBe("3 图 · 2 视频");
    expect(view.layers[0].evidence).toContain("2 个源视频 → 7 帧");
  });

  it("L1 取 memory-effectiveness 的工具调用，不取 run item 的 tool_performance", () => {
    expect(view.layers[1].metric).toBe("视觉 80.0%");
    expect(view.layers[1].detail).toBe("观察 10 次 · OCR 5 次");
    expect(view.layers[1].evidence).toContain("场景 4 个");
  });

  it("L2 的事件数取当前展开 QA 去重后的 1，而非全量 analysis 的 9", () => {
    expect(view.layers[2].metric).toBe("1 个事件");
    expect(view.layers[2].detail).toBe("1 条证据带事件上下文");
    expect(view.layers[2].evidence).toContain("带回 9 个事件上下文；当前展开 1 个");
  });

  it("L3 用 agent2 需求账本；L4+ 用 get_core_memory 调用与 qa_eval 进度", () => {
    expect(view.layers[3].metric).toBe("3/4 满足");
    expect(view.layers[3].detail).toBe("规划 3 题 · 目标证据需求");
    expect(view.layers[4].metric).toBe("总结访问 4 次");
    expect(view.layers[4].detail).toBe("当前页总结题 1 · 已完成 6/9");
    expect(view.layers[4].evidence).toContain("Judge 均分 1.5 / 2 · exact 50.0%");
  });

  it("五层的 metric 两两不同——同一份数喂五层是证明不了分层的", () => {
    const metrics = view.layers.map((layer) => layer.metric);
    expect(new Set(metrics).size).toBe(5);
  });

  it("汇总字段逐项对应", () => {
    expect(view.rawMediaCount).toBe(5);
    expect(view.keyframes).toBe(2);
    expect(view.expandedCount).toBe(1);
    expect(view.completed).toBe(6);
    expect(view.total).toBe(9);
    expect(view.visual).toEqual({ calls: 10, okRate: 0.8 });
    expect(view.ocr).toEqual({ calls: 5, okRate: 1 });
    expect(view.summaryCalls).toEqual({ calls: 4, okRate: 0.5 });
  });

  it("关键帧未 ready 时 L0 回退文案", () => {
    const fallback = memoryLayerMetrics({
      run: RUN,
      manifests: MANIFESTS,
      qaDetails: {},
      qaPageItems: QA_PAGE_ITEMS,
      memoryEffectiveness: MEMORY_EFFECTIVENESS,
      keyframeAnalysis: { status: "pending" } as KeyframeAnalysis,
      summary: SUMMARY,
    });
    expect(fallback.layers[0].evidence).toBe("等待全量分析");
    // 未展开任何 QA → L2 回退到全量事件上下文数
    expect(fallback.layers[2].metric).toBe("待展开");
  });
});

// ── memoryValidityArchitecture：五层 created/effective 各不相同 ───────────

const VALIDITY_LEVELS = [
  { key: "L0", created: 12, effective: 5, ineffective: 7 },
  { key: "L1", created: 20, effective: 11, ineffective: 9 },
  { key: "L2", created: 9, effective: 6, ineffective: 3 },
  { key: "L3", created: 7, effective: 2, ineffective: 5 },
  { key: "L4+", created: 10, effective: 5, ineffective: 5 },
];

describe("memoryValidityArchitecture", () => {
  const layers = memoryValidityArchitecture({
    memoryEffectiveness: {
      status: "ready",
      data_quality: { graph_ready: true },
      levels: VALIDITY_LEVELS,
      details: {
        visual_calls: { total: 10, effective: 8 },
        ocr_calls: { total: 5, effective: 3 },
        answer_correctness: { correct: 4, partial: 3, incorrect: 3 },
      },
    } as unknown as MemoryEffectiveness,
    keyframeAnalysis: KEYFRAME_ANALYSIS,
    run: RUN,
    summary: { image_retrieval_recall_micro: 0.4, image_retrieval_precision_micro: 0.25 } as unknown as RunSummary,
  });

  it("每层的核心比率取自本层的 created/effective", () => {
    expect(layers[0].nodes[0].value).toBe(12); // L0.created
    expect(layers[1].nodes[0].value).toBe("55.0%"); // 11/20
    expect(layers[2].nodes[0].value).toBe("66.7%"); // 6/9
    expect(layers[3].nodes[0].value).toBe("28.6%"); // 2/7
    expect(layers[4].nodes[0].value).toBe("50.0%"); // 5/10
  });

  it("五个核心比率两两不同（分层逻辑真的按层取值）", () => {
    const ratios = [layers[1].nodes[0].value, layers[2].nodes[0].value, layers[3].nodes[0].value, layers[4].nodes[0].value];
    expect(new Set(ratios).size).toBe(4);
  });

  it("L0 的检索指标来自 runSummary 与 keyframeAnalysis.retrieval", () => {
    expect(layers[0].nodes[1].value).toBe("40.0%"); // recall 0.4
    expect(layers[0].nodes[2].value).toBe("75.0%"); // 3/4
    expect(layers[0].nodes[3].value).toBe("62.5%"); // 5/8
  });

  it("L1 的视觉/OCR 取自 details 的两组调用；L3 的未闭合需求取 ineffective", () => {
    expect(layers[1].nodes[1].value).toBe("80.0%");
    expect(layers[1].nodes[2].value).toBe("60.0%");
    expect(layers[3].nodes[1].value).toBe(5); // L3.ineffective
  });

  it("L4+ 的 Exact Accuracy 用 correctness.correct ÷ L4+.created", () => {
    expect(layers[4].nodes[1].value).toBe("40.0%"); // 4/10
    expect(layers[4].nodes[1].note).toContain("正确 4 · 部分 3 · 错误 3");
  });
});

// ── memoryRecallMatrices：命中 / 未命中并存，逐格断言 ─────────────────────

const RECALL_ME = {
  status: "ready",
  details: {
    answer_correctness: { correct: 5, partial: 3, incorrect: 4 },
    memory_answer_attribution: {
      tool: {
        eligible: 10,
        hit_items: 6,
        miss_items: 4,
        matrix: { hit_correct: 4, hit_partial: 1, hit_incorrect: 1, miss_correct: 1, miss_partial: 1, miss_incorrect: 2 },
      },
      usage: {
        eligible: 8,
        hit_items: 3,
        miss_items: 5,
        matrix: { hit_correct: 2, hit_partial: 1, hit_incorrect: 0, miss_correct: 3, miss_partial: 2, miss_incorrect: 0 },
      },
    },
    delivery_statistics: {
      image: {eligible_items: 10, delivered_items: 6, not_delivered_items: 4, extra_delivery_items: 2, unrecorded_items: 0, delivery_rate: 0.6},
      video_keyframe: {candidate_hit_items: 6, candidate_hit_rate: 0.75, candidate_unrecorded_items: 0, eligible_items: 8, delivered_items: 2, not_delivered_items: 6, extra_delivery_items: 1, unrecorded_items: 0, delivery_rate: 0.25},
      event: {status: "not_recorded", reason: "缺少事件交付记录"},
    },
    evidence_effectiveness: {
      tool: {
        image: { effective_items: 3, returned_items: 4, ineffective_items: 1, effective_rate_on_returned: 0.75 },
        video_keyframe: { effective_items: 1, returned_items: 2, ineffective_items: 1, effective_rate_on_returned: 0.5 },
      },
      usage: {
        image: { effective_items: 2, returned_items: 4, ineffective_items: 2, effective_rate_on_returned: 0.5 },
        video_keyframe: { effective_items: 0, returned_items: 2, ineffective_items: 2, effective_rate_on_returned: 0 },
      },
      event: { effective_items: 1, returned_items: 4, ineffective_items: 3, effective_rate_on_returned: 0.25 },
    },
  },
} as unknown as MemoryEffectiveness;

describe("memoryRecallMatrices", () => {
  const matrices = memoryRecallMatrices(RECALL_ME);
  const tool = matrices[0];

  it("两个维度各一张，工具与模型使用的划分不同", () => {
    expect(matrices.map((m) => m.key)).toEqual(["tool", "usage"]);
    expect(tool).toMatchObject({ eligible: 10, hit: 6, miss: 4 });
    expect(matrices[1]).toMatchObject({ eligible: 8, hit: 3, miss: 5 });
  });

  it("L0：可归因题占比 = eligible / judgedTotal", () => {
    expect(tool.layers[0].nodes[0].value).toBe("100.0%"); // 12/12
    expect(tool.layers[0].nodes[0].note).toContain("正确 5 · 部分 3 · 错误 4");
    expect(tool.layers[0].nodes[1].value).toBe("83.3%"); // 10/12
  });

  it("L1 的行划分：命中分支取 hit_items，未命中分支取 miss_items", () => {
    const [hitNode, missNode] = tool.layers[1].nodes;
    expect(hitNode.label).toBe("工具召回命中");
    expect(hitNode.note).toBe("6/10 题");
    expect(missNode.label).toBe("工具召回未命中");
    expect(missNode.note).toBe("4/10 题");
  });

  it("L2 的每一格分别落在正确的行列上（命中 × Judge 与未命中 × Judge）", () => {
    const nodes = tool.layers[2].nodes;
    expect(nodes.map((n) => n.label)).toEqual([
      "命中 → 正确",
      "命中 → 部分",
      "命中 → 错误",
      "未命中 → 正确",
      "未命中 → 部分",
      "未命中 → 错误",
    ]);
    expect(nodes[0].value).toBe(4); // hit_correct
    expect(nodes[0].note).toBe("40.0% 总体 · 66.7% 命中分支"); // 4/10 · 4/6
    expect(nodes[2].value).toBe(1); // hit_incorrect
    expect(nodes[3].value).toBe(1); // miss_correct
    expect(nodes[5].value).toBe(2); // miss_incorrect
    expect(nodes[5].note).toBe("20.0% 总体 · 50.0% 未命中分支"); // 2/10 · 2/4
  });

  it("L2 的两个分支组：total 分别取 hit/miss，节点是各分支占比", () => {
    const groups = tool.layers[2].groups;
    expect(groups?.[0].label).toBe("工具召回命中");
    expect(groups?.[0].total).toBe(6);
    expect(groups?.[0].nodes[0].value).toBe("40.0%"); // 4/10
    expect(groups?.[1].label).toBe("工具召回未命中");
    expect(groups?.[1].total).toBe(4);
    expect(groups?.[1].nodes[2].value).toBe("20.0%"); // 2/10
    expect(groups?.[1].nodes[2].note).toContain("50.0% 本分支"); // 2/4
  });

  it("L3 只展示本次视频的候选命中与最终交付两项", () => {
    expect(tool.layers[3].name).toBe("视频召回与交付");
    expect(tool.layers[3].nodes).toHaveLength(2);
    expect(tool.layers[3].nodes[0].label).toBe("候选中找到对应视频关键帧");
    expect(tool.layers[3].nodes[0].value).toBe("6/8 题");
    expect(tool.layers[3].nodes[0].note).toContain("75.0%");
    expect(tool.layers[3].nodes[1].label).toBe("最终交付正确视频素材");
    expect(tool.layers[3].nodes[1].value).toBe("2/8 题");
    expect(tool.layers[3].nodes[1].note).toContain("25.0%");
    expect(matrices[1].layers[3]).toEqual(tool.layers[3]);
  });

  it("usage 维度的 L2 与 tool 不同（划分真的换了维度）", () => {
    expect(matrices[1].layers[2].nodes[0].value).toBe(2); // usage.hit_correct
    expect(matrices[1].layers[2].nodes[3].value).toBe(3); // usage.miss_correct
  });
});

// ── chainDetailMedia / chainDetailStatements：截断到 12 ────────────────────

describe("chainDetailMedia", () => {
  it("13 个不同媒体 → 去重后截断到 12", () => {
    const item = {
      gt_media: Array.from({ length: 13 }, (_, i) => ({ media_type: "image", media_id: `gt-${i}.png` })),
    };
    const media = chainDetailMedia(item, [], "album-a");
    expect(media).toHaveLength(12);
    expect(media[0]).toMatchObject({ media_id: "gt-0.png", media_url: "/api/v1/albums/album-a/photos/gt-0.png" });
    expect(media.map((m) => m.media_id)).toEqual(Array.from({ length: 12 }, (_, i) => `gt-${i}.png`));
    expect(media.map((m) => m.media_id)).not.toContain("gt-12.png");
  });

  it("跨来源去重：同一 media_id 只留一份，且预览行会被降级成 image/video", () => {
    const item = {
      gt_media: [{ media_type: "image", media_id: "x.png" }],
      evidence_source_media: [{ media_type: "image", media_id: "x.png" }],
      predicted_media: [{ media_type: "image", media_id: "y.png" }],
    };
    const previews = [{ media_kind: "video_keyframe", asset_id: "y.png", media_id: "y.png" }];
    const media = chainDetailMedia(item, previews);
    // x.png 出现两次、y.png 出现在 predicted 与 preview 中 → 只剩 x 与 y
    expect(media.map((m) => m.media_id)).toEqual(["x.png", "y.png"]);
  });
});

describe("chainDetailStatements", () => {
  const layer = { key: "L1" } as Pick<GraphLayer, "key">;
  const node = { kind: "观察", evidence: "节点证据" };

  it("13 条观察输入 → 输出 12 条（问题 + 节点证据 + 8 条观察 + 回答 + Judge）", () => {
    const item = { question: "问题?", answer: "回答", judge: { reason: "理由" } };
    const previews = Array.from({ length: 13 }, (_, i) => ({ media_id: `m-${i}`, evidence_summary: `t-${i}` }));
    const statements = chainDetailStatements(item, previews, layer, node);
    expect(statements).toHaveLength(12);
    expect(statements[0]).toEqual({ label: "用户问题", value: "问题?" });
    expect(statements[1]).toEqual({ label: "L1 · 观察", value: "节点证据" });
    expect(statements[2]).toEqual({ label: "m-0 · 观察", value: "t-0" });
    // previews 先被 slice(0, 8)，因此只到 m-7；第 13 条输入永远不会出现
    expect(statements.some((s) => s.label.startsWith("m-8"))).toBe(false);
    expect(statements.at(-1)).toEqual({ label: "Judge 理由", value: "理由" });
  });

  it("没有 question/answer/judge 时只有节点证据 + 8 条观察", () => {
    const previews = Array.from({ length: 13 }, (_, i) => ({ file_name: `f-${i}.png`, text: `x-${i}` }));
    const statements = chainDetailStatements({}, previews, layer, node);
    expect(statements).toHaveLength(9);
    expect(statements[1]).toEqual({ label: "f-0.png · 观察", value: "x-0" });
  });

  it("没有可读文本的预览行被跳过", () => {
    const previews = [{ media_id: "m" }, { media_id: "n", event_context: { summary: "事件摘要" } }];
    const statements = chainDetailStatements({}, previews, layer, node);
    expect(statements).toHaveLength(2);
    expect(statements[1]).toEqual({ label: "n · 观察", value: "事件摘要" });
  });
});

// ── itemLayerChain：pass / fail / na 三分 ─────────────────────────────────

describe("itemLayerChain", () => {
  it("全通：媒体、观察、事件、需求、回答齐备", () => {
    const item = {
      predicted_media: [{ media_type: "image", media_id: "p1.png" }, { media_type: "image", media_id: "p2.png" }],
      tool_trace: [
        { observation: { preview: [preview("video_keyframe", "kf-1"), preview("image", "img-1", "ev-1")] } },
      ],
      agent2_trace: {
        task_state: { requirements: [{ id: "req-1", status: "satisfied" }, { id: "req-2", status: "satisfied" }] },
      },
      final_answer: "答案",
      answer_claims: [1, 2],
      judge: { score: 2 },
    } as unknown as RunItemDetail;
    const chain = itemLayerChain(item);
    expect(chain.map((n) => n.status)).toEqual(["pass", "pass", "pass", "pass", "pass"]);
    expect(chain[0].detail).toBe("2 媒体 · 关键帧 1");
    expect(chain[1].detail).toBe("2 条视觉/文字观察");
    expect(chain[2].detail).toBe("1 个事件上下文");
    expect(chain[3].detail).toBe("2/2 项证据需求");
    expect(chain[4].evidence).toContain("answer_claims 2 · Judge 2");
  });

  it("需求未全满足 → L3 fail；无回答 → L4+ fail；空题 → 前四层 na", () => {
    const partial = {
      predicted_media: [{ media_type: "image", media_id: "p.png" }],
      tool_trace: [{ observation: { preview: [preview("image", "img-1", "ev-1")] } }],
      agent2_trace: {
        task_state: { requirements: [{ id: "req-1", status: "satisfied" }, { id: "req-2", status: "open" }] },
      },
      final_answer: "",
    } as unknown as RunItemDetail;
    const chain = itemLayerChain(partial);
    expect(chain[0].status).toBe("pass");
    expect(chain[2].status).toBe("pass");
    expect(chain[3].status).toBe("fail");
    expect(chain[3].detail).toBe("1/2 项证据需求");
    expect(chain[4].status).toBe("fail");

    const empty = itemLayerChain({} as RunItemDetail);
    expect(empty.map((n) => n.status)).toEqual(["na", "na", "na", "na", "fail"]);
    expect(empty[0].evidence).toBe("无 asset_id / media_id");
  });
});

// ── effectiveEvidenceGraph / activeEvidenceGraphView ─────────────────────

describe("effectiveEvidenceGraph + activeEvidenceGraphView", () => {
  const item = {
    question: "问题",
    gt_media: [{ media_type: "image", media_id: "g1.png" }],
    tool_trace: [{ tool_name: "search", observation: { preview: [preview("image", "img-1", "ev-1")] } }],
    retrieved_candidate_media: [{ media_type: "image", media_id: "c1.png" }],
    evidence_source_media: [{ media_type: "image", media_id: "e1.png" }],
    query_anchors: "时间=周三",
    agent2_trace: { task_state: { requirements: [{ id: "req-1", status: "satisfied" }] } },
    final_answer: "答案",
    answer_claims: [1],
    judge: { score: 2, reason: "很好" },
  } as unknown as RunItemDetail;

  it("五层的值来自这一条 QA", () => {
    const graph = effectiveEvidenceGraph(item);
    expect(graph.map((layer) => layer.key)).toEqual(["L0", "L1", "L2", "L3", "L4+"]);
    expect(graph[0].nodes[0].value).toBe("1 条");
    expect(graph[1].nodes[0].value).toBe("1");
    expect(graph[1].nodes[1].value).toBe("1");
    expect(graph[1].nodes[2].value).toBe("1");
    expect(graph[2].nodes[0].value).toBe("1");
    expect(graph[3].nodes[1].evidence).toBe("req-1:已满足");
    expect(graph[4].nodes[0].value).toBe("已形成");
    expect(graph[4].nodes[2].value).toBe("2 分");
  });

  it("未展开 QA 时回退文案", () => {
    const graph = effectiveEvidenceGraph({} as RunItemDetail);
    expect(graph[0].nodes[0].value).toBe("未展开 QA");
    expect(graph[1].nodes[0].value).toBe("未记录"); // 1.0 这里显式传了 fallback，不是 recorded 的默认「未展开 QA」
    expect(graph[4].nodes[0].value).toBe("未形成");
  });

  it("activeEvidenceGraphView 按 chainMode 二选一", () => {
    const memory = memoryEvidenceGraph({
      run: RUN,
      qaDetails: QA_DETAILS,
      keyframeAnalysis: KEYFRAME_ANALYSIS,
      view: memoryLayerMetrics({
        run: RUN,
        manifests: MANIFESTS,
        qaDetails: QA_DETAILS,
        qaPageItems: QA_PAGE_ITEMS,
        memoryEffectiveness: MEMORY_EFFECTIVENESS,
        keyframeAnalysis: KEYFRAME_ANALYSIS,
        summary: SUMMARY,
      }),
      summary: SUMMARY,
    });
    const effective = effectiveEvidenceGraph(item);
    expect(activeEvidenceGraphView("effective", memory, effective)).toBe(effective);
    expect(activeEvidenceGraphView("creation", memory, effective)).toBe(memory);
  });
});

describe("memoryEvidenceGraph", () => {
  const view = memoryLayerMetrics({
    run: RUN,
    manifests: MANIFESTS,
    qaDetails: QA_DETAILS,
    qaPageItems: QA_PAGE_ITEMS,
    memoryEffectiveness: MEMORY_EFFECTIVENESS,
    keyframeAnalysis: KEYFRAME_ANALYSIS,
    summary: SUMMARY,
  });
  const graph = memoryEvidenceGraph({
    run: RUN,
    qaDetails: QA_DETAILS,
    keyframeAnalysis: KEYFRAME_ANALYSIS,
    view,
    summary: SUMMARY,
  });

  it("原始媒体先取 view.rawMediaCount，清单为空时回退 run.input_integrity.files_checked", () => {
    expect(graph[0].nodes[0].value).toBe("5");
    expect(graph[0].nodes[1].value).toBe("7");
    const withoutManifest = memoryLayerMetrics({
      run: RUN,
      manifests: [],
      qaDetails: QA_DETAILS,
      qaPageItems: QA_PAGE_ITEMS,
      memoryEffectiveness: MEMORY_EFFECTIVENESS,
      keyframeAnalysis: KEYFRAME_ANALYSIS,
      summary: SUMMARY,
    });
    expect(withoutManifest.rawMediaCount).toBe(0);
    const fallback = memoryEvidenceGraph({
      run: RUN,
      qaDetails: QA_DETAILS,
      keyframeAnalysis: KEYFRAME_ANALYSIS,
      view: withoutManifest,
      summary: SUMMARY,
    });
    expect(fallback[0].nodes[0].value).toBe("42");
  });

  it("L3 的账本项数取 summary.agent2_trace 的 4 项", () => {
    expect(graph[3].nodes[1].value).toBe("4 项");
  });

  it("recorded() 把 0 当未记录——L1 的空场景数会显示未记录", () => {
    const empty = memoryEvidenceGraph({
      run: RUN,
      qaDetails: {},
      keyframeAnalysis: { status: "pending" } as KeyframeAnalysis,
      view,
      summary: SUMMARY,
    });
    expect(empty[1].nodes[2].value).toBe("未记录");
    expect(empty[0].nodes[1].value).toBe("未记录");
  });
});

// ── chainDetailFor：openChainNode 的纯部分 ────────────────────────────────

describe("chainDetailFor", () => {
  it("为每条已加载的 QA 生成一条记录，未加载的跳过", () => {
    const page = {
      items: [{ index: 0, question: "列表里的问题" }, { index: 1 }] as unknown as RunItemSummary[],
      page: 2,
      page_size: 20,
      total: 5,
    };
    const details: QaDetailMap = {
      0: { index: 0, qa_id: "qa-0", question: "详情问题", tool_trace: [] } as unknown as RunItemDetail,
      1: undefined,
    };
    const layer = { key: "L1", name: "场景图", color: "scene", stage: "", nodes: [], transition: "" };
    const node = { kind: "观察", value: "1", evidence: "e" };
    const detail = chainDetailFor({ mode: "creation", layer, node, page, details, albumId: "album-a" });
    expect(detail.mode).toBe("creation");
    expect(detail.records).toHaveLength(1);
    expect(detail.records[0].key).toBe("qa-0-0");
    expect(detail.records[0].question).toBe("详情问题");
    expect(detail.page).toBe(2);
    expect(detail.pageSize).toBe(20);
    expect(detail.total).toBe(5);
  });

  it("详情缺 question 时回退到列表行", () => {
    const page = {
      items: [{ index: 0, question: "列表问题" }] as unknown as RunItemSummary[],
      page: 1,
      page_size: 20,
      total: 1,
    };
    const details: QaDetailMap = { 0: { index: 0 } as unknown as RunItemDetail };
    const layer = { key: "L1", name: "x", color: "scene", stage: "", nodes: [], transition: "" };
    const detail = chainDetailFor({
      mode: "effective",
      layer,
      node: { kind: "k", value: "v", evidence: "e" },
      page,
      details,
    });
    expect(detail.records[0].question).toBe("列表问题");
  });
});
