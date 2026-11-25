import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { AssistantProgressEvent } from "../../shared/api/client";
import { buildCards, TurnTimeline } from "./TurnTimeline";

function event(overrides: Partial<AssistantProgressEvent>): AssistantProgressEvent {
  return {
    text: "",
    status: "ok",
    stage: "thinking",
    step_index: 1,
    timestamp: "2026-07-01T10:00:00+00:00",
    kind: "status",
    payload: {},
    ...overrides,
  };
}

const EVENTS: AssistantProgressEvent[] = [
  event({
    step_index: 1,
    stage: "planning",
    kind: "plan",
    status: "complete",
    text: "已确认需要哪些证据。",
    payload: {
      requirements: [
        {
          id: "req1",
          evidence_type: "time",
          description: "照片的时间范围",
          status: "open",
          satisfied_by: [],
          unmet_reason: "",
        },
      ],
    },
  }),
  event({
    step_index: 2,
    stage: "action",
    kind: "model_action",
    status: "running",
    text: "正在执行 search_memories…",
    payload: {
      tool: "search_memories",
      tool_call_id: "call1",
      arguments: { query: "海边" },
    },
  }),
  event({
    step_index: 3,
    stage: "tool_result",
    kind: "tool_result",
    status: "ok",
    text: "结果集里共有 7 张候选照片。",
    payload: {
      tool: "search_memories",
      tool_call_id: "call1",
      duration_ms: 12,
      result: {
        result_set_id: "rs_1",
        total: 7,
        has_more: true,
        asset_ids: ["a1", "a2"],
      },
    },
  }),
  event({
    step_index: 4,
    stage: "terminal",
    kind: "terminal",
    status: "complete",
    text: "已完成本轮回答。",
    payload: { status: "complete", termination_reason: "answered" },
  }),
];

afterEach(() => cleanup());

describe("推理时间线", () => {
  it("把 action 与同一次 tool_result 合成一张卡片", () => {
    const cards = buildCards(EVENTS);
    expect(cards.map((card) => card.kind)).toEqual([
      "planning",
      "tool",
      "final",
    ]);
    expect(cards[1].events.map((item) => item.step_index)).toEqual([2, 3]);
  });

  it("过程默认收起，展开显示三张阶段卡和结果摘要", () => {
    render(<TurnTimeline events={EVENTS} />);

    const toggle = screen.getByRole("button", { name: "思考过程 · 4 步" });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(screen.queryByText("查找资料")).toBeNull();

    fireEvent.click(toggle);

    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("先想一想")).toBeTruthy();
    expect(screen.getByText("查找资料")).toBeTruthy();
    expect(screen.getByText("整理回答")).toBeTruthy();
    expect(screen.getByText(/照片的时间范围/)).toBeTruthy();
    expect(screen.getByText("正在查找相关照片…")).toBeTruthy();
    expect(screen.getByText("找到 7 张相关照片；候选 2 张")).toBeTruthy();
    expect(screen.getByText("回答完成。")).toBeTruthy();
    expect(document.querySelectorAll(".timeline-card")).toHaveLength(3);
    expect(screen.queryByText(/rs_1|"query"|耗时 12 ms/)).toBeNull();
    fireEvent.click(toggle);
    expect(screen.queryByText("查找资料")).toBeNull();
  });
});
