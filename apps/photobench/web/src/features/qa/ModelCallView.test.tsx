import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { RunItemDetail } from "../../api/types.ts";

import { ModelCallView } from "./ModelCallView.tsx";
import { turnModelCalls } from "../../lib/agentTrace.ts";

function UnifiedCall({ detail, modelLabel }: {detail: RunItemDetail; modelLabel: string}) {
  return <ModelCallView call={turnModelCalls(detail, 0)[0]} detail={detail} modelLabel={modelLabel} title="Agent 模型调用" />;
}

afterEach(cleanup);

/** 一条成功的 agent 调用；`agentLoopGroups` 会把它归到第 0 轮。 */
const DETAIL: RunItemDetail = {
  index: 0,
  question: "车停在哪",
  model_call_metrics: [
    {
      call_type: "agent",
      step_id: "s1",
      role: "assistant",
      model: "qwen2.5-vl",
      total_ms: 1200,
      ttft_ms: 200,
      prompt_tokens: 500,
      completion_tokens: 40,
      tokens_per_second: 30,
      streamed: true,
    },
  ],
};

// 1.0 用原生 `<details>` 折叠调用体（:3561）。jsdom 会把子节点一直留在 DOM 里，
// 所以「展开 / 折叠」的可观测真值是 details 的 `open`——夹具必须两边给出不同答案。
describe("统一轨迹 · 调用性能展开 / 折叠", () => {
  it("点 summary 展开（open=true），再点折叠（open=false）；子节点一直在 details 内", () => {
    render(<UnifiedCall detail={DETAIL} modelLabel="qwen" />);

    const summary = screen.getByText("Agent 模型调用").closest("summary");
    expect(summary).not.toBeNull();
    const details = summary!.closest("details");
    expect(details).not.toBeNull();

    expect(details!.open).toBe(false);

    fireEvent.click(summary!);
    expect(details!.open).toBe(true);
    // 展开后子节点（延迟组成 / 用途栏）确实挂在 details 内部。
    expect(details!.contains(screen.getByText("用途"))).toBe(true);

    fireEvent.click(summary!);
    expect(details!.open).toBe(false);
  });
});

describe("统一轨迹 · 每一步的模型完整输入 / 原始输出", () => {
  it("按 step_id 取到 debug_calls，展开调用体就能看到 prompt 与原始回复", () => {
    render(
      <UnifiedCall
        detail={{
          ...DETAIL,
          runtime_turns: [
            {
              index: 0,
              trace_bundle: {
                debug_calls: [
                  {
                    step_id: "s1",
                    call_type: "agent",
                    status: "complete",
                    messages: [{ role: "system", content: "系统提示词-唯一标记" }],
                    tools: [{ type: "function", function: { name: "search_memories" } }],
                    response: { content: "模型原始回答-唯一标记", thought: "思考-唯一标记" },
                  },
                ],
              },
            },
          ],
        }}
        modelLabel="qwen"
      />,
    );

    expect(screen.getByText("模型原始输出")).not.toBeNull();
    expect(screen.getByText("系统提示词-唯一标记")).not.toBeNull();
    expect(screen.getByText("模型原始回答-唯一标记")).not.toBeNull();
    expect(screen.getByText("思考-唯一标记")).not.toBeNull();
  });
});

it('统一调用保留TTFT、吞吐、预算和请求时长，输入预检不冒充实际token', () => {
  render(<ModelCallView call={{call_type:'agent', model:'qwen', status:'complete', streamed:true,
    duration_ms:1200,ttft_ms:200,prompt_tokens:500,completion_tokens:40,preflight_prompt_tokens:520,
    tokens_per_second:30,effective_max_tokens:2048,max_model_len:8192}} />);
  expect(screen.getByText('TTFT 200ms')).toBeTruthy();
  expect(screen.getByText('请求耗时 1.2s')).toBeTruthy();
  expect(screen.getByText('Token 输入 500 / 输出 40')).toBeTruthy();
  expect(screen.getByText('预检输入 520 tokens')).toBeTruthy();
  expect(screen.getByText('30.0 token/s')).toBeTruthy();
  expect(screen.getByText('520 + 2048 / 8192')).toBeTruthy();
});

it('非流式的首token时延显示不适用，实际耗时为零也保留', () => {
  render(<ModelCallView call={{call_type:'agent',status:'complete',streamed:false,duration_ms:0,prompt_tokens:0,completion_tokens:0}} />);
  expect(screen.getByText('TTFT 不适用（非流式）')).toBeTruthy();
  expect(screen.getByText('请求耗时 0ms')).toBeTruthy();
  expect(screen.getByText('Token 输入 0 / 输出 0')).toBeTruthy();
});
