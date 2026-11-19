import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { QaRow } from "../../api/types.ts";
import { QaReviewCard } from "./QaReviewCard.tsx";

afterEach(cleanup);

// 夹具的口径：**有 GT / 无 GT 两份必须给出不同答案**——有 GT 才有检索证据面板与图片，
// 无 GT 走 `no-evidence` 布局，且参考回答回退到占位文案。
const WITH_GT: QaRow = {
  qa_id: "q-gt",
  question: "谁在厨房？",
  answer: "妈妈",
  question_type: "single_evidence_memory_qa",
  answerability: "answerable",
  expected_action: "answer",
  retrieval_media_refs: [
    { media_type: "image", media_id: "album3/photos/kitchen.jpg", asset_id: "asset-1" },
  ],
};

const WITHOUT_GT: QaRow = {
  qa_id: "q-nogt",
  question: "现在几点？",
  answer: "",
  expected_action: "refuse",
};

describe("QaReviewCard · GT 媒体与问答气泡（1.0 App.vue:3703-3745）", () => {
  it("有 GT：渲染检索 GT 媒体面板、图片与「直接证据」标；布局不带 no-evidence", () => {
    const { container } = render(<QaReviewCard item={WITH_GT} index={0} />);

    expect(screen.getByText("检索 GT 媒体")).not.toBeNull();
    expect(container.querySelector(".qa-evidence-panel")).not.toBeNull();
    expect(container.querySelector(".qa-review-layout")?.className).not.toContain("no-evidence");

    const img = container.querySelector(".gt-image-button img") as HTMLImageElement | null;
    expect(img?.getAttribute("src")).toBe("/api/v1/assets/asset-1/file");

    // 问答气泡：题干在用户侧，参考回答在 GT 侧。
    expect(container.querySelector(".user-bubble")?.textContent).toContain("谁在厨房？");
    expect(container.querySelector(".answer-bubble")?.textContent).toContain("妈妈");
  });

  it("邮件 QA 保留证据 ID，显示待接入提示而非图片", () => {
    const { container } = render(<QaReviewCard item={{
      ...WITH_GT,
      retrieval_media_refs: [{ media_type: "email", media_id: "email202401010001" }],
    }} index={0} />);
    expect(screen.getByText("邮件证据 · 邮件记忆待接入")).not.toBeNull();
    expect(screen.getByText("email202401010001")).not.toBeNull();
    expect(container.querySelector("img, video")).toBeNull();
  });

  it("无 GT：不渲染媒体面板，布局带 no-evidence，参考回答回退占位", () => {
    const { container } = render(<QaReviewCard item={WITHOUT_GT} index={0} />);

    expect(container.querySelector(".qa-evidence-panel")).toBeNull();
    expect(screen.queryByText("检索 GT 媒体")).toBeNull();
    expect(container.querySelector(".qa-review-layout")?.className).toContain("no-evidence");

    expect(container.querySelector(".user-bubble")?.textContent).toContain("现在几点？");
    expect(container.querySelector(".answer-bubble")?.textContent).toContain("（无参考答案）");
    // 行为徽章照 `expected_action` 走（拒答）。
    expect(container.querySelector(".qa-badge")?.className).toContain("badge-refuse");
  });
});
