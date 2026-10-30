import { describe, expect, it } from "vitest";

import {
  conversationContextLabel,
  conversationIdLabel,
  conversationTurns,
  isDirectEvidence,
  itemEvidenceMedia,
  itemMedia,
  itemParseRate,
  itemRetrievalMetrics,
  toolRecallMedia,
  turnScore,
} from "./evidence.ts";

const ALBUM = "alb1";

describe("toolRecallMedia vs itemEvidenceMedia：两条口径必须分开（1.0 :694-717）", () => {
  // 夹具：候选**有**、模型交付**无**。两种口径必须给出**不同**答案。
  const candidateOnly = {
    retrieved_candidate_media: [{ media_id: "album1/photos/a.jpg", media_type: "image" }],
    retrieved_file_names: ["c.jpg"],
  };

  it("候选有、交付无 → toolRecallMedia 给出候选", () => {
    const recall = toolRecallMedia(candidateOnly, ALBUM);
    expect(recall.map((m) => m.media_url)).toEqual(["/api/v1/albums/alb1/photos/a.jpg"]);
  });

  it("候选有、交付无 → itemEvidenceMedia 回**空**，绝不回落到候选", () => {
    expect(itemEvidenceMedia(candidateOnly, ALBUM)).toEqual([]);
  });

  it("同一夹具下 itemMedia（交付口径 E）**会**回落到候选窗口——与 itemEvidenceMedia 不同", () => {
    expect(itemMedia(candidateOnly, ALBUM).map((m) => m.media_url)).toEqual([
      "/api/v1/albums/alb1/photos/a.jpg",
    ]);
  });

  it("两者都提供时各取各的（交付取 predicted，候选取 retrieved）", () => {
    const both = {
      predicted_media: [{ media_id: "album1/photos/p.jpg", media_type: "image" }],
      retrieved_candidate_media: [{ media_id: "album1/photos/c.jpg", media_type: "image" }],
    };
    expect(itemEvidenceMedia(both, ALBUM).map((m) => m.media_url)).toEqual([
      "/api/v1/albums/alb1/photos/p.jpg",
    ]);
    expect(toolRecallMedia(both, ALBUM).map((m) => m.media_url)).toEqual([
      "/api/v1/albums/alb1/photos/c.jpg",
    ]);
  });

  it("retrieved_file_names 也能撑起候选，但仍不进交付", () => {
    const namesOnly = { retrieved_file_names: ["n1.jpg", "n2.jpg"] };
    expect(toolRecallMedia(namesOnly, ALBUM).map((m) => m.media_url)).toEqual([
      "/api/v1/albums/alb1/photos/n1.jpg",
      "/api/v1/albums/alb1/photos/n2.jpg",
    ]);
    expect(itemEvidenceMedia(namesOnly, ALBUM)).toEqual([]);
  });
});

describe("itemEvidenceMedia（1.0 :703-711）", () => {
  it("predicted_media 直接给 URL", () => {
    expect(
      itemEvidenceMedia(
        { predicted_media: [{ media_id: "album1/photos/p.jpg", media_type: "image" }] },
        ALBUM,
      ).map((m) => m.media_url),
    ).toEqual(["/api/v1/albums/alb1/photos/p.jpg"]);
  });

  it("predicted_file_names 走 decorateMedia 补 URL", () => {
    expect(itemEvidenceMedia({ predicted_file_names: ["n.jpg"] }, ALBUM).map((m) => m.media_url)).toEqual([
      "/api/v1/albums/alb1/photos/n.jpg",
    ]);
  });

  it("空的 predicted_media（[] 是 truthy）**不**落到 predicted_images——照搬 1.0 的 `||`", () => {
    const emptyFirst = {
      predicted_media: [],
      predicted_images: [{ media_id: "album1/photos/i.jpg", media_type: "image" }],
    };
    expect(itemEvidenceMedia(emptyFirst, ALBUM)).toEqual([]);
  });
});

describe("itemMedia gt 分支（1.0 :660-676）", () => {
  it("gt_media 优先", () => {
    const item = {
      gt_media: [{ media_id: "album1/photos/g.jpg", media_type: "image" }],
      retrieval_image_ids: ["x.jpg"],
    };
    expect(itemMedia(item, ALBUM, true).map((m) => m.media_url)).toEqual(["/api/v1/albums/alb1/photos/g.jpg"]);
  });

  it("gt_images 只在没有 retrieval_media_refs 时用", () => {
    const withRefs = {
      gt_images: [{ media_id: "album1/photos/gi.jpg", media_type: "image" }],
      retrieval_media_refs: [{ media_id: "album1/photos/r.jpg", media_type: "image" }],
    };
    // 有 retrieval_media_refs → 不用 gt_images，改由 mediaRefs 合成
    expect(itemMedia(withRefs, ALBUM, true).map((m) => m.media_url)).toEqual(["/api/v1/albums/alb1/photos/r.jpg"]);
    const withoutRefs = { gt_images: [{ media_id: "album1/photos/gi.jpg", media_type: "image" }] };
    expect(itemMedia(withoutRefs, ALBUM, true).map((m) => m.media_url)).toEqual(["/api/v1/albums/alb1/photos/gi.jpg"]);
  });
});

describe("itemRetrievalMetrics（1.0 :566-574）", () => {
  it("无标准媒体 → 说明文案", () => {
    expect(itemRetrievalMetrics({})).toBe("本题无标准媒体，不计入检索指标");
  });

  it("无 retrieval_media_refs → 追加「历史图片口径」，且不拆图片/视频", () => {
    const item = {
      retrieval_image_ids: ["a.jpg"],
      media_retrieval_precision: 0.5,
      media_retrieval_recall: 0.4,
      media_retrieval_f1: 0.44,
    };
    expect(itemRetrievalMetrics(item)).toBe("总媒体 P 50.0% · R 40.0% · F1 44.0% · 历史图片口径");
  });

  it("有 retrieval_media_refs 且含图 + 视频 → 逐项拆出图片/视频召回", () => {
    const item = {
      retrieval_media_refs: [
        { media_id: "a.jpg", media_type: "image" },
        { media_id: "v.mp4", media_type: "video" },
      ],
      media_retrieval_precision: 1,
      media_retrieval_recall: 1,
      media_retrieval_f1: 1,
      image_retrieval_recall: 0.9,
      video_retrieval_recall: 0.8,
    };
    expect(itemRetrievalMetrics(item)).toBe(
      "总媒体 P 100.0% · R 100.0% · F1 100.0% · 图片 R 90.0% · 视频 R 80.0%",
    );
  });
});

describe("itemParseRate（1.0 :575-579）", () => {
  it("total 缺省 → 未记录；total=0 仍是有效值", () => {
    expect(itemParseRate({})).toBe("未记录");
    expect(itemParseRate({ agent_stability: { json_parse_total: 0 } })).toBe(
      "模型动作 JSON：无解析调用",
    );
  });
  it("有值 → success/total (rate)", () => {
    expect(itemParseRate({ agent_stability: { json_parse_total: 10, json_parse_success: 8, json_parse_rate: 0.8 } })).toBe(
      "模型动作 JSON：8/10 成功（80.0%）",
    );
  });
});

describe("多轮对话（1.0 :611-623）", () => {
  it("conversationTurns 非数组回 []", () => {
    expect(conversationTurns({})).toEqual([]);
    expect(conversationTurns({ conversation: [{ conversation_id: "c1" }] })).toHaveLength(1);
  });

  it("conversationIdLabel 三级回退", () => {
    expect(conversationIdLabel({ conversation_id: "top" })).toBe("top");
    expect(conversationIdLabel({ conversation: [{ conversation_id: "turn" }] })).toBe("turn");
    expect(conversationIdLabel({})).toBe("历史结果未记录会话 ID");
  });

  it("conversationContextLabel：count=0 与 count 缺失走**不同**分支", () => {
    expect(conversationContextLabel({ context_turn_count: 3 }, 0)).toBe("本轮携带前 3 轮对话上下文");
    expect(conversationContextLabel({ context_turn_count: 0 }, 5)).toBe("首轮，无历史上下文");
    // 缺省时用下标当轮数
    expect(conversationContextLabel({}, 5)).toBe("本轮携带前 5 轮对话上下文");
  });

  it("turnScore 只认数字 0/1/2", () => {
    expect(turnScore(0)).toBe("0 分");
    expect(turnScore(2)).toBe("2 分");
    expect(turnScore("2")).toBe("不适用");
    expect(turnScore(undefined)).toBe("不适用");
  });
});

describe("isDirectEvidence（1.0 :712-717）", () => {
  const item = {
    answer_evidence_media_refs: [{ media_id: "album1/photos/a.jpg", media_type: "image" }],
    answer_claims: [{ evidence_media_refs: [{ media_id: "album1/videos/v.mp4", media_type: "video" }] }],
  };

  it("命中 answer_evidence 或 claim evidence 才算直接证据", () => {
    expect(isDirectEvidence(item, "a.jpg")).toBe(true);
    expect(isDirectEvidence(item, "v.mp4")).toBe(true);
    expect(isDirectEvidence(item, { media_id: "album1/photos/a.jpg", media_type: "image" })).toBe(true);
    expect(isDirectEvidence(item, "z.jpg")).toBe(false);
  });
});

it("2.0 实际轮次与题面轮次合并，实际状态和回答优先", () => {
  expect(conversationTurns({ conversation: [{ message: "题面", expected_action: "answer" }], runtime_turns: [{ message: "实际", turn_status: "complete", answer: "已有回答" }] })).toEqual([{ message: "实际", expected_action: "answer", turn_status: "complete", answer: "已有回答" }]);
});

it.each(["unanswerable", " INSUFFICIENT_EVIDENCE "])("%s 带历史GT和零分仍显示不适用", (answerability) => {
  expect(itemRetrievalMetrics({ answerability, retrieval_image_ids: ["a.jpg"], retrieval_recall: 0 })).toBe("不适用：不可回答题不计入检索指标");
});

it('解析统计读取原生工具字段，按已存计数求比率', () => {
  expect(itemParseRate({agent_stability:{tool_argument_json_total:4,tool_argument_json_success:3,tool_argument_json_rate:1}})).toBe('工具参数 JSON：3/4 成功（75.0%）');
  expect(itemParseRate({agent_stability:{tool_argument_json_total:4}})).toBe('工具参数 JSON：共 4 次，成功数未记录');
  expect(itemParseRate({agent_stability:{tool_argument_json_total:0}})).toBe('工具参数 JSON：无解析调用');
  expect(itemParseRate({agent_stability:{tool_argument_json_total:2,tool_argument_json_success:2,json_parse_total:1,json_parse_success:0}})).toBe('工具参数 JSON：2/2 成功（100.0%）；模型动作 JSON：0/1 成功（0.0%）');
});
