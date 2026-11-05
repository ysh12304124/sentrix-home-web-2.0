import { describe, expect, it } from "vitest";

import {
  callContent,
  callMessages,
  callThought,
  debugCallForCall,
  debugCallForStep,
  turnDebugCalls,
  turnTimeline,
  turnModelCalls,
} from "./agentTrace.ts";

const CALL = (overrides: Record<string, unknown> = {}) => ({
  step_id: "step_1",
  call_type: "agent",
  seq: 1,
  messages: [
    { role: "system", content: "你是相册助手" },
    { role: "user", content: "车停在哪" },
  ],
  tools: [{ type: "function", function: { name: "search_memories", description: "检索" } }],
  response: {
    content: "",
    thought: "先检索",
    tool_calls: [{ id: "tool_1", name: "search_memories", arguments: '{"query":"车"}' }],
    finish_reason: "tool_calls",
    prompt_tokens: 100,
    completion_tokens: 20,
  },
  status: "complete",
  ...overrides,
});

const ITEM = {
  runtime_turns: [
    {
      index: 0,
      message: "车停在哪",
      answer: "停在小区地下车库",
      trace_bundle: {
        debug_calls: [
          {
            step_id: "planner_1",
            call_type: "planner",
            seq: 0,
            messages: [{ role: "user", content: "车停在哪" }],
            tools: [],
            response: { content: '{"requirements":[]}', finish_reason: "stop" },
            status: "complete",
          },
          CALL(),
          {
            step_id: "step_2",
            call_type: "faithfulness_judge",
            seq: 2,
            messages: [{ role: "system", content: "你是评审" }],
            tools: [],
            response: { content: '{"faithful":true}', finish_reason: "stop" },
            status: "complete",
          },
        ],
      },
      trace: [
        { kind: "plan", status: "complete", text: "已确认", payload: { call_id: "planner_1" } },
        {
          kind: "model_action",
          status: "running",
          text: "正在执行 search_memories",
          payload: { step_id: "tool_1", parent_step_id: "step_1", tool: "search_memories" },
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
      ],
    },
  ],
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
};

describe("agentTrace · 完整模型轨迹访问器", () => {
  it("按 step_id + call_type 把 Agent Loop 调用挂到完整模型输入上", () => {
    const call = debugCallForCall(ITEM, { step_id: "step_1", call_type: "agent", conversation_turn: 0 });
    expect(call).not.toBeNull();
    expect(call?.step_id).toBe("step_1");
  });

  it("step_id 缺失时不猜（老 run 回退由 debug_trace 负责）", () => {
    expect(debugCallForCall(ITEM, { call_type: "agent", conversation_turn: 0 })).toBeNull();
  });

  it("进度步骤按 parent_step_id / call_id 找到对应调用", () => {
    const turn = ITEM.runtime_turns[0];
    expect(debugCallForStep(turn, turn.trace[0])?.call_type).toBe("planner");
    expect(debugCallForStep(turn, turn.trace[1])?.step_id).toBe("step_1");
    expect(debugCallForStep(turn, turn.trace[3])?.call_type).toBe("faithfulness_judge");
  });

  it("时间线按 planner → model → 它发起的工具 → judge → answer 排列", () => {
    const timeline = turnTimeline(ITEM, 0);
    expect(timeline.filter(entry => entry.kind !== "event").map((entry) => entry.kind)).toEqual([
      "planner",
      "model",
      "tool",
      "judge",
      "answer",
    ]);
    expect(timeline.find(entry => entry.kind === "tool")?.tool?.tool).toBe("search_memories");
    expect(timeline.at(-1)?.answer).toBe("停在小区地下车库");
  });

  it("老 run 没有 debug_calls 时回退到 debug_trace 的步", () => {
    const legacy = {
      runtime_turns: [
        {
          index: 0,
          answer: "旧答案",
          debug_trace: [
            { type: "planner", prompt: [{ role: "user", content: "旧问题" }], raw_full: "旧规划" },
            { type: "model", prompt: [{ role: "user", content: "旧问题" }], raw_full: "旧回答" },
          ],
        },
      ],
    };
    expect(turnDebugCalls(legacy.runtime_turns[0])).toEqual([]);
    expect(turnTimeline(legacy, 0).map((entry) => entry.kind)).toEqual([
      "planner",
      "model",
      "answer",
    ]);
  });

  it("重试调用按attempt匹配自身IO，工具只挂成功尝试一次", () => {
    const retry = {
      runtime_turns: [
        {
          index: 0,
          message: "车停在哪",
          answer: "地下车库",
          trace_bundle: {
            debug_calls: [
              {
                step_id: "step_1",
                call_type: "agent",
                seq: 0,
                status: "error",
                attempt: 1,
                messages: [{ role: "system", content: "失败尝试" }],
                tools: [],
                response: null,
                error: "prompt too long",
              },
              { ...CALL(), seq: 1, attempt: 2 },
            ],
          },
          trace: [],
        },
      ],
      tool_trace: ITEM.tool_trace,
    };
    const bound = debugCallForCall(retry, {
      step_id: "step_1",
      call_type: "agent",
      conversation_turn: 0,
    });
    expect(bound).toBeNull();
    expect(callMessages(debugCallForCall(retry,{step_id:"step_1",call_type:"agent",conversation_turn:0,attempt:1}))[0]?.content).toBe("失败尝试");
    expect(callMessages(debugCallForCall(retry,{step_id:"step_1",call_type:"agent",conversation_turn:0,attempt:2}))[0]?.content).toBe("你是相册助手");
    expect(turnTimeline(retry, 0).filter((entry) => entry.kind === "tool")).toHaveLength(1);
  });

  it("消息、正文、思考通道都原样取出", () => {
    const call = CALL();
    expect(callMessages(call)).toHaveLength(2);
    expect(callContent(call)).toBe("");
    expect(callThought(call)).toBe("先检索");
  });
});


it("仅有指标也展示完整调用数，未绑定工具保留，运行时证据核对就近展示", () => {
  const item = {model_call_metrics:[{step_id:"step_1",call_type:"agent",conversation_turn:0,status:"complete",duration_ms:50}],
    tool_trace:[{step_id:"t1",parent_step_id:"step_1",tool:"search_memories",conversation_turn:0},
                {step_id:"unknown",parent_step_id:"absent",tool:"read_photo_text",conversation_turn:0}],
    runtime_turns:[{index:0,answer:"答案",trace:[
      {kind:"model_action",payload:{step_id:"t1",parent_step_id:"step_1"}},
      {kind:"tool_result",payload:{step_id:"t1",parent_step_id:"step_1"}},
      {kind:"evidence_binding",text:"证据匹配",payload:{}},
      {kind:"terminal",payload:{}}]}]};
  expect(turnModelCalls(item,0)).toHaveLength(1);
  const timeline=turnTimeline(item,0);
  expect(timeline.filter(entry=>entry.kind==="model")).toHaveLength(1);
  expect(timeline.filter(entry=>entry.kind==="tool")).toHaveLength(2);
  expect(timeline.find(entry=>entry.tool?.tool==="read_photo_text")?.unbound).toBe(true);
  expect(timeline.findIndex(entry=>entry.event?.kind==="evidence_binding")).toBeGreaterThan(timeline.findIndex(entry=>entry.tool?.tool==="search_memories"));
  expect(timeline.at(-1)?.kind).toBe("answer");
});

it("两轮重复step_id不会合并或错配输入，final_answer只记工具", () => {
  const item={model_call_metrics:[{step_id:"step_1",conversation_turn:0,attempt:1},{step_id:"step_1",conversation_turn:1,attempt:1}],
    runtime_turns:[0,1].map(index=>({index,message:"问题"+index,answer:"回答"+index,trace_bundle:{debug_calls:[CALL({attempt:1,messages:[{role:"user",content:"问题"+index}]})]}})),
    tool_trace:[{step_id:"f",tool:"final_answer",parent_step_id:"step_1",conversation_turn:1}]};
  expect(callMessages(debugCallForCall(item,item.model_call_metrics[1]))[0].content).toBe("问题1");
  expect(turnTimeline(item,0).filter(entry=>entry.kind==="tool")).toHaveLength(0);
  expect(turnTimeline(item,1).filter(entry=>entry.kind==="model")).toHaveLength(1);
  expect(turnTimeline(item,1).filter(entry=>entry.kind==="tool")).toHaveLength(1);
});


it("有真实调用开始时间时，最终模型调用展示在证据核对之前", () => {
  const item={runtime_turns:[{index:0,answer:"回答",trace_bundle:{debug_calls:[
    CALL({started_at:"2026-10-28T15:04:37.301Z",duration_ms:3045,response:{content:"回答"}})
  ]},trace:[{kind:"status",timestamp:"2026-10-28T23:04:37+08:00"},
    {kind:"evidence_binding",timestamp:"2026-10-28T23:04:40+08:00"},
    {kind:"terminal",timestamp:"2026-10-28T23:04:40+08:00"}]}]};
  const timeline=turnTimeline(item,0);
  expect(timeline.map(entry=>entry.kind==="event"?entry.event?.kind:entry.kind)).toEqual(["status","model","evidence_binding","terminal","answer"]);
});


it("不同call_id不能仅凭相同step_id借用输入", () => {
 const item={runtime_turns:[{index:0,trace_bundle:{debug_calls:[CALL({call_id:"b",attempt:1})]}}]};
 expect(debugCallForCall(item,{call_id:"a",step_id:"step_1",attempt:1,call_type:"agent",conversation_turn:0})).toBeNull();
});
