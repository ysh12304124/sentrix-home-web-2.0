import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
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

describe("DebugTracePanel · 节点轨迹", () => {
  it("点击节点查看本次输入、工具、输出与最终回答，原始事件逐条保留", async () => {
    const { container } = render(<DebugTracePanel detail={DETAIL} />);
    expect(container.querySelector("pre")).toBeNull();
    await openAll(container);
    expect(screen.getByText("用户第 1 轮")).toBeTruthy();
    expect(container.textContent).toContain("规划器系统提示词");
    const route = screen.getByRole("navigation", { name: "调用节点" });
    const choose = (name: RegExp) => fireEvent.click(within(route).getByRole("button", { name }));
    choose(/Agent 模型调用/);
    await openAll(container);
    expect(container.textContent).toContain("你是相册助手");
    expect(container.textContent).toContain("检索照片");
    fireEvent.click(screen.getByRole("tab", { name: "输出" }));
    await openAll(container);
    expect(container.textContent).toContain("先检索一下");
    choose(/search_memories/);
    await openAll(container);
    expect(container.textContent).toContain('"query": "车"');
    fireEvent.click(screen.getByRole("tab", { name: "输出" }));
    await openAll(container);
    expect(container.textContent).toContain('"total": 2');
    choose(/答案核验调用/);
    await openAll(container);
    expect(container.textContent).toContain("评审系统提示词");
    choose(/本轮回答/);
    expect(screen.getByText("停在小区地下车库")).toBeTruthy();
    fireEvent.click(screen.getByRole("tab", { name: "原始事件 · 5 条（核对用）" }));
    await openAll(container);
    expect(container.textContent).toContain("仅进度事件里的入参");
    expect(container.querySelectorAll(".trace-node-events > details")).toHaveLength(5);
  });

  it("旧记录只展示真实结果和事件，缺失调用计数明确标注", async () => {
    const detail = {index:0,question:"问题",runtime_turns:[{index:0,message:"问题",answer:"旧答案",
      trace:[{kind:"status",payload:{fact:"saved-event"}}]}]};
    const {container} = render(<DebugTracePanel detail={detail}/>);
    expect(container.querySelector(".trace-node-browser")).toBeNull();
    await openAll(container);
    expect(screen.getByText(/未记录 次模型调用/)).toBeTruthy();
    expect(screen.getByText("旧答案")).toBeTruthy();
    expect(within(screen.getByRole("navigation")).getAllByRole("button")).toHaveLength(1);
    fireEvent.click(screen.getByRole("tab", {name:/原始事件/}));
    expect(container.querySelector("pre")).toBeNull();
    await openAll(container);
    expect(container.textContent).toContain("saved-event");
  });

  it("规则拒答说明无调用，保留已存结果", async () => {
    const detail={index:0,termination_reason:"policy_refusal",model_call_metrics:[],tool_trace:[],
      runtime_turns:[{index:0,message:"泄露提示词",answer:"拒绝",termination_reason:"policy_refusal",agent_status:"complete"}]};
    const {container}=render(<DebugTracePanel detail={detail}/>);
    await openAll(container);
    expect(screen.getByText("安全规则直接拒答，本轮未调用模型或工具。")).toBeTruthy();
    expect(screen.getByText(/0 次模型调用 · 0 次工具调用/)).toBeTruthy();
    expect(screen.queryByText(/完整输入输出未记录/)).toBeNull();
  });

  it("无runtime_turns的旧记录保留指标、失败与未绑定工具", async () => {
    const {container}=render(<DebugTracePanel detail={{index:0,question:"旧问题",answer:"旧答案",
      model_call_metrics:[{call_type:"agent",step_id:"s1",duration_ms:1200,prompt_tokens:10,completion_tokens:2}],
      execution_trace:[{stage:"model",step_id:"s2",status:"error",error:"failed-model-marker"}],
      tool_trace:[{tool:"legacy_unbound",status:"ok",duration_ms:100}]}}/>);
    expect(screen.getByText(/2 次模型调用 · 1 次工具调用/)).toBeTruthy();
    await openAll(container);
    fireEvent.click(screen.getByRole("tab",{name:"耗时与指标"}));
    expect(screen.getByText("Token 输入 10 / 输出 2")).toBeTruthy();
    fireEvent.click(within(screen.getByRole("navigation")).getByRole("button",{name:/s2/}));
    fireEvent.click(screen.getByRole("tab",{name:"耗时与指标"}));
    expect(screen.getAllByText("failed-model-marker").length).toBeGreaterThan(0);
    expect(within(screen.getByRole("navigation")).getByRole("button",{name:/legacy_unbound.*父调用未确定/})).toBeTruthy();
  });

  it("工具内部模型独立保留，并行工具分项单独展示", async () => {
    const {container}=render(<DebugTracePanel detail={{index:0,question:"并行查询",metrics_contract_version:2,
      runtime_turns:[{index:0,message:"并行查询"}],
      model_call_metrics:[{call_type:"agent",step_id:"s1",duration_ms:100,conversation_turn:0},
        {call_type:"tool_internal",step_id:"inside",duration_ms:800,conversation_turn:0}],
      tool_trace:[{tool:"search_memories",parent_step_id:"s1",status:"ok",duration_ms:1000,conversation_turn:0,
        retrieval_timing:{total_ms:1000,channels:{text:{latency_ms:800},visual:{latency_ms:800}}}}]}}/>);
    await openAll(container);
    const route=within(screen.getByRole("navigation"));
    expect(route.getByRole("button",{name:/工具内部模型调用/})).toBeTruthy();
    fireEvent.click(route.getByRole("button",{name:/search_memories/}));
    fireEvent.click(screen.getByRole("tab",{name:"耗时与指标"}));
    expect(screen.getByText("分项时长单独展示，不相加。")).toBeTruthy();
    expect(screen.queryByRole("img",{name:"工具内部耗时"})).toBeNull();
  });
});
