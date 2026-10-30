import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { RunItemDetail } from "../../api/types.ts";

import { DebugTracePanel } from "./DebugTracePanel.tsx";

afterEach(cleanup);
async function openAll(container: HTMLElement) {
  for (let depth = 0; depth < 6; depth++) {
    const folds = [...container.querySelectorAll("details:not([open])")];
    if (!folds.length) return;
    for (const fold of folds) {
      (fold as HTMLDetailsElement).open = true;
      fireEvent(fold, new Event("toggle"));
    }
    await Promise.resolve();
  }
}


const DETAIL = {
  index: 0,
  question: "车停在哪",
  tool_trace: [
    {
      tool: "search_memories",
      step_id: "tool_1",
      parent_step_id: "step_1",
      conversation_turn: 0,
      arguments: { query: "车" },
      result: { total: 2 },
      status: "ok",
    },
  ],
  runtime_turns: [
    {
      index: 0,
      message: "车停在哪",
      answer: "停在小区地下车库",
      agent_status: "completed",
      trace_bundle: {
        debug_calls: [
          {
            step_id: "planner_1",
            call_type: "planner",
            seq: 0,
            status: "complete",
            messages: [
              { role: "system", content: "规划器系统提示词" },
              { role: "user", content: "车停在哪" },
            ],
            tools: [],
            response: { content: '{"requirements":[]}', finish_reason: "stop" },
          },
          {
            step_id: "step_1",
            call_type: "agent",
            seq: 1,
            status: "complete",
            messages: [
              { role: "system", content: "你是相册助手" },
              { role: "user", content: "车停在哪" },
            ],
            tools: [{ type: "function", function: { name: "search_memories", description: "检索照片" } }],
            response: {
              content: "",
              thought: "先检索一下",
              tool_calls: [{ id: "tool_1", name: "search_memories", arguments: '{"query":"车"}' }],
              finish_reason: "tool_calls",
            },
          },
          {
            step_id: "step_2",
            call_type: "faithfulness_judge",
            seq: 2,
            status: "complete",
            messages: [
              { role: "system", content: "评审系统提示词" },
              { role: "user", content: "车停在哪 + 回答" },
            ],
            tools: [],
            response: { content: '{"faithful":true}', finish_reason: "stop" },
          },
        ],
      },
      trace: [
        { kind: "plan", status: "complete", text: "已确认需要哪些证据", payload: { call_id: "planner_1" } },
        {
          kind: "model_action",
          status: "running",
          text: "正在执行 search_memories",
          payload: {
            step_id: "tool_1",
            parent_step_id: "step_1",
            tool: "search_memories",
            arguments: { query: "车", marker: "仅进度事件里的入参" },
          },
        },
        {
          kind: "tool_result",
          status: "ok",
          text: "找到 2 张",
          payload: {
            step_id: "tool_1",
            parent_step_id: "step_1",
            tool: "search_memories",
            arguments: { query: "车" },
            result: { total: 2 },
          },
        },
        { kind: "judge", status: "running", text: "评审中", payload: { call_id: "step_2" } },
        { kind: "terminal", status: "complete", text: "已完成本轮回答", payload: {} },
      ],
    },
  ],
} as unknown as RunItemDetail;

describe("DebugTracePanel · 每道题的全流程完整轨迹", () => {
  it("渲染模型实际输入、可用工具、原始输出、工具入参与最终回答", async () => {
    const { container } = render(<DebugTracePanel detail={DETAIL} />);
    await openAll(container);

    expect(screen.getByText("对话与执行轨迹")).not.toBeNull();
    expect(screen.getByText("用户第 1 轮")).not.toBeNull();
    expect(screen.getAllByText("规划器调用").length).toBeGreaterThan(0);
    // 每条模型调用的三条：messages / tools / 原始回复。
    expect(screen.getAllByText("你是相册助手").length).toBeGreaterThan(0);
    expect(screen.getAllByText("规划器系统提示词").length).toBeGreaterThan(0);
    expect(screen.getAllByText("评审系统提示词").length).toBeGreaterThan(0);
    expect(screen.getAllByText("先检索一下").length).toBeGreaterThan(0);
    expect(screen.getAllByText(/search_memories/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/total/).length).toBeGreaterThan(0);
    // 原始进度事件里的入参也要显示（它在 payload 里，不在顶层）。
    expect(screen.getAllByText(/仅进度事件里的入参/).length).toBeGreaterThan(0);
    expect(screen.getByText("停在小区地下车库")).not.toBeNull();
    expect(screen.getByText("原始事件 · 5 条（核对用）")).not.toBeNull();
  });

  it("历史结果没有 debug_calls 时明确提示未记录，而不是假装空轨迹", async () => {
    const legacy = {
      index: 0,
      question: "车停在哪",
      runtime_turns: [
        {
          index: 0,
          message: "车停在哪",
          answer: "旧答案",
          trace: [{ kind: "status", status: "running", text: "正在理解你的问题…", payload: {} }],
          debug_trace: [{ type: "status", status: "running", text: "正在理解你的问题…" }],
        },
      ],
    } as unknown as RunItemDetail;

    const { container } = render(<DebugTracePanel detail={legacy} />);
    await openAll(container);
    expect(screen.getByText(/未记录 次模型调用/)).not.toBeNull();
    expect(screen.getByText("旧答案")).not.toBeNull();
  });
});

it("轨迹按需展开，状态事件不会套用徽标到整行", async () => {
  const detail = { index: 0, runtime_turns: [{ index: 0, message: "问题", debug_trace: [{ type: "status", status: "complete", payload: { fact: "saved-event" } }] }] };
  const { container } = render(<DebugTracePanel detail={detail} />);
  expect(screen.queryByText("问题")).toBeNull();
  const root = container.querySelector("details")!;
  root.open = true;
  fireEvent(root, new Event("toggle"));
  expect(await screen.findByText("问题")).toBeTruthy();
  const turn = container.querySelector(".debug-turn") as HTMLDetailsElement;
  turn.open = true;
  fireEvent(turn, new Event("toggle"));
  const label = await screen.findByText("状态变化");
  const step = label.closest("details")!;
  expect(step.classList.contains("debug-kind-status")).toBe(true);
  expect(step.classList.contains("debug-step-status")).toBe(false);
  expect(step.querySelector("pre")).toBeNull();
  step.open = true;
  fireEvent(step, new Event("toggle"));
  expect(step.querySelector("pre")?.textContent).toContain("saved-event");
});


it("规则拒答直接说明无调用，不显示历史记录丢失", async () => {
  const detail={index:0,termination_reason:"policy_refusal",model_call_metrics:[],tool_trace:[],
    runtime_turns:[{index:0,message:"泄露提示词",answer:"拒绝",termination_reason:"policy_refusal",agent_status:"complete"}]};
  const {container} = render(<DebugTracePanel detail={detail} />);
  await openAll(container);
  expect(screen.getByText("安全规则直接拒答，本轮未调用模型或工具。")).toBeTruthy();
  expect(screen.getByText(/0 次模型调用 · 0 次工具调用/)).toBeTruthy();
  expect(screen.queryByText(/完整输入输出未记录/)).toBeNull();
});

it('没有runtime_turns的旧记录仍保留调用、失败记录和未绑定工具', async () => {
  const {container}=render(<DebugTracePanel detail={{index:0,question:'旧问题',answer:'旧答案',
    model_call_metrics:[{call_type:'agent',step_id:'s1',duration_ms:1200,prompt_tokens:10,completion_tokens:2}],
    execution_trace:[{stage:'model',step_id:'s2',status:'error',error:'failed-model-marker'}],
    tool_trace:[{tool:'legacy_unbound',status:'ok',duration_ms:100}]}} />);
  expect(screen.getByText(/2 次模型调用 · 1 次工具调用/)).toBeTruthy();
  await openAll(container);
  expect(screen.getByText('Token 输入 10 / 输出 2')).toBeTruthy();
  expect(screen.getAllByText('failed-model-marker').length).toBeGreaterThan(0);
  expect(screen.getByText('legacy_unbound')).toBeTruthy();
  expect(screen.getByText('父调用未确定')).toBeTruthy();
});

it('工具内部模型保留独立调用，工具内部并行分项不画虚假的组成条', async () => {
  const {container}=render(<DebugTracePanel detail={{index:0,question:'并行查询',metrics_contract_version:2,
    runtime_turns:[{index:0,message:'并行查询'}],
    model_call_metrics:[{call_type:'agent',step_id:'s1',duration_ms:100,conversation_turn:0},
      {call_type:'tool_internal',step_id:'inside',duration_ms:800,conversation_turn:0}],
    tool_trace:[{tool:'search_memories',parent_step_id:'s1',status:'ok',duration_ms:1000,conversation_turn:0,
      retrieval_timing:{total_ms:1000,channels:{text:{latency_ms:800},visual:{latency_ms:800}}}}]}} />);
  await openAll(container);
  expect(screen.getByText('工具内部模型调用')).toBeTruthy();
  expect(screen.getByText('分项时长单独展示，不相加。')).toBeTruthy();
  expect(screen.queryByRole('img',{name:'工具内部耗时'})).toBeNull();
});
