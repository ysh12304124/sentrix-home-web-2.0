import { describe, expect, it } from "vitest";
import {
  agent2DecisionSummary,
  agent2EvidenceHeight,
  agent2EvidenceRows,
  agent2EvidenceTypeLabel,
  agent2RequirementStatusClass,
  agent2RequirementStatusLabel,
  agent2RequirementSummary,
  agentLoopGroups,
  attributionClass,
  attributionLabel,
  attributionSummary,
  callAgentLoopTiming,
  callBudget,
  callIndexForStep,
  callLatencySegments,
  callObservation,
  conversationTurnNumber,
  debugPromptAnnotations,
  debugStepForCall,
  debugStepForCallInGroup,
  debugStepsForCall,
  debugToolsForCall,
  debugTraceForTurn,
  formatAgent2EvidenceValue,
  getAgent2LedgerEntries,
  getAgent2Requirements,
  getAgent2Trace,
  itemCallMetrics,
  itemExecutionTrace,
  itemLlmSummary,
  latencySegmentStyle,
  latencySegmentTitle,
  noToolLabel,
  normalizedToolCallIndex,
  qaLatencySegments,
  itemTimingBreakdown,
  runtimeDebugTurns,
  showAgentLoopGroupHeaders,
  showToolBranch,
  toolDurationMs,
  toolExecutionSteps,
  itemToolTrace,
  toolLatencySegments,
  toolsForGroupedCall,
  turnRecoveryCount,
} from "./trace";

// ---------------------------------------------------------------------------
// 访问器 / 轮次编号
// ---------------------------------------------------------------------------

describe("itemCallMetrics / itemExecutionTrace (:1335-1344)", () => {
  it("非数组回 []；数组里的 null / 非对象被剔除", () => {
    expect(itemCallMetrics(null)).toEqual([]);
    expect(itemCallMetrics({ model_call_metrics: "x" })).toEqual([]);
    const calls = itemCallMetrics({ model_call_metrics: [{ a: 1 }, null, 3, "s"] });
    expect(calls).toEqual([{ a: 1 }]);
    const trace = itemExecutionTrace({ execution_trace: [{ stage: "model" }, null, 7] });
    expect(trace).toEqual([{ stage: "model" }]);
  });
});

describe("conversationTurnNumber (:1345-1348)", () => {
  it("非负整数（含数字字符串）通过，其余回 fallback", () => {
    expect(conversationTurnNumber(3)).toBe(3);
    expect(conversationTurnNumber("2")).toBe(2);
    expect(conversationTurnNumber(0)).toBe(0);
    expect(conversationTurnNumber(-1)).toBe(0);
    expect(conversationTurnNumber(1.5)).toBe(0);
    expect(conversationTurnNumber(undefined)).toBe(0);
    expect(conversationTurnNumber("x", 7)).toBe(7);
  });
});

// ---------------------------------------------------------------------------
// 调用树归并
// ---------------------------------------------------------------------------

describe("agentLoopGroups (:1349-1380)", () => {
  it("已存调用 + 失败 trace 步 → 补出 _traceOnly 合成调用", () => {
    const item = {
      conversation: [{ message: "q1" }],
      model_call_metrics: [{ step_id: "m1", call_type: "agent", conversation_turn: 0, total_ms: 10 }],
      execution_trace: [{ type: "model", step_id: "m2", conversation_turn: 0, status: "error" }],
    };
    const groups = agentLoopGroups(item);
    expect(groups).toHaveLength(1);
    expect(groups[0].turnIndex).toBe(0);
    expect(groups[0].calls).toHaveLength(2);
    const saved = groups[0].calls[0];
    expect(saved._globalCallIndex).toBe(0);
    expect(saved._traceOnly).toBeUndefined();
    const synth = groups[0].calls[1];
    expect(synth._traceOnly).toBe(true);
    expect(synth._globalCallIndex).toBe("trace-0");
    expect(synth.status).toBe("error");
    expect(synth.call_type).toBe("agent");
    expect(synth.turn_outcome).toBe("model_error");
    const observation = synth.call_observation as { source?: string; purpose?: string };
    expect(observation.source).toBe("execution_trace_only");
    expect(observation.purpose).toBe("模型调用在生成性能指标前失败");
  });

  it("已有同名指标键 / 未失败 / tool_internal 的 trace 步都不合成", () => {
    const item = {
      conversation: [{}],
      model_call_metrics: [{ step_id: "m1", call_type: "agent", conversation_turn: 0 }],
      execution_trace: [
        { type: "model", step_id: "m1", conversation_turn: 0, status: "error" },
        { type: "model", step_id: "m3", conversation_turn: 0, status: "complete" },
        { type: "model", step_id: "m4", conversation_turn: 0, status: "error", call_type: "tool_internal" },
      ],
    };
    const groups = agentLoopGroups(item);
    expect(groups[0].calls).toHaveLength(1);
    expect(groups[0].calls[0].step_id).toBe("m1");
  });

  it("tool_internal 的已存调用被剔除；按轮分组并按轮号升序", () => {
    const item = {
      conversation: [{}, {}],
      model_call_metrics: [
        { step_id: "t", call_type: "tool_internal", conversation_turn: 0 },
        { step_id: "a", call_type: "agent", conversation_turn: 1 },
      ],
      execution_trace: [],
    };
    const groups = agentLoopGroups(item);
    expect(groups.map((group) => group.turnIndex)).toEqual([0, 1]);
    expect(groups[0].calls).toEqual([]);
    expect(groups[1].calls.map((call) => call.step_id)).toEqual(["a"]);
  });

  it("无任何来源时至少建出第 0 轮", () => {
    const groups = agentLoopGroups({});
    expect(groups).toHaveLength(1);
    expect(groups[0].turnIndex).toBe(0);
    expect(groups[0].calls).toEqual([]);
  });
});

describe("showAgentLoopGroupHeaders / turnRecoveryCount (:1381-1401)", () => {
  it("多于一轮 / 多于一组才显示表头", () => {
    expect(showAgentLoopGroupHeaders({ conversation: [{}, {}] })).toBe(true);
    expect(showAgentLoopGroupHeaders({})).toBe(false);
  });
  it("只数 recovery 调用", () => {
    const group = { calls: [{ call_type: "recovery" }, { call_type: "agent" }, { call_type: "recovery" }] };
    expect(turnRecoveryCount(group)).toBe(2);
    expect(turnRecoveryCount(null)).toBe(0);
  });
});

describe("itemLlmSummary (:1406-1420)", () => {
  it("无调用回 null", () => {
    expect(itemLlmSummary({ model_call_metrics: [] })).toBeNull();
    expect(itemLlmSummary(null)).toBeNull();
  });
  it("已存 llm_summary 优先（含 0），缺项现算", () => {
    const item = {
      llm_summary: { call_count: 99, ttft_ms_avg: 0 },
      model_call_metrics: [
        { streamed: true, ttft_ms: 50, total_ms: 10, prompt_tokens: 5, completion_tokens: 6, tokens_per_second: 2 },
      ],
    };
    const summary = itemLlmSummary(item);
    expect(summary).not.toBeNull();
    expect(summary?.call_count).toBe(99);
    expect(summary?.ttft_ms_avg).toBe(0);
    expect(summary?.streamed_count).toBe(1);
    expect(summary?.total_ms_sum).toBe(10);
    expect(summary?.prompt_tokens_total).toBe(5);
    expect(summary?.completion_tokens_total).toBe(6);
    expect(summary?.tokens_per_second_avg).toBe(2);
  });
  it("无 llm_summary 时全部现算（无值回 null）", () => {
    const summary = itemLlmSummary({ model_call_metrics: [{ total_ms: 4 }] });
    expect(summary?.ttft_ms_avg).toBeNull();
    expect(summary?.tokens_per_second_avg).toBeNull();
    expect(summary?.streamed_count).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// normalizedToolCallIndex 的四级回退（每级单独可辨）
// ---------------------------------------------------------------------------

describe("normalizedToolCallIndex (:1524-1538)", () => {
  it("第 1 级：parent_step_id 直接命中，且优先级高于 execution_trace 的 parent", () => {
    const item = {
      model_call_metrics: [
        { step_id: "A", conversation_turn: 0 },
        { step_id: "B", conversation_turn: 0 },
      ],
      tool_trace: [{ conversation_turn: 0, parent_step_id: "A", model_call_index: 1 }],
      execution_trace: [{ stage: "tool", conversation_turn: 0, parent_step_id: "B" }],
    };
    const trace = item.tool_trace[0];
    // L1 命中 A→0；若顺序被改成先走 execution parent 会得到 B→1。
    expect(normalizedToolCallIndex(item, trace, 0)).toBe(0);
  });

  it("显式父步骤不匹配时保持未绑定，不根据工具顺序猜测", () => {
    const item = {
      model_call_metrics: [
        { step_id: "s0", conversation_turn: 0 },
        { step_id: "s1", conversation_turn: 0 },
        { step_id: "s2", conversation_turn: 0 },
      ],
      tool_trace: [
        { conversation_turn: 5, parent_step_id: "s2" },
        { conversation_turn: 0, parent_step_id: "NOPE" },
        { conversation_turn: 0, parent_step_id: "NOPE", model_call_index: 2 },
      ],
      execution_trace: [
        { stage: "tool", conversation_turn: 0, parent_step_id: "s1" },
        { stage: "tool", conversation_turn: 0, parent_step_id: "s0" },
      ],
    };
    const trace = item.tool_trace[2];
    expect(normalizedToolCallIndex(item, trace, 2)).toBeNull();
  });

  it("无父步骤时保留已有同轮绑定，不按无标识的执行序列覆盖", () => {
    const item = {
      model_call_metrics: [
        { step_id: "s0", conversation_turn: 0 },
        { step_id: "s9", conversation_turn: 0 },
      ],
      tool_trace: [{ conversation_turn: 0, model_call_index: 0 }],
      execution_trace: [{ stage: "tool", conversation_turn: 0, parent_step_id: "s9" }],
    };
    const trace = item.tool_trace[0];
    expect(normalizedToolCallIndex(item, trace, 0)).toBe(0);
  });

  it("第 4 级：model_call_index（整数才认）", () => {
    const item = {
      model_call_metrics: [
        { step_id: "s0", conversation_turn: 0 },
        { step_id: "s1", conversation_turn: 0 },
        { step_id: "s2", conversation_turn: 0 },
      ],
      tool_trace: [{ conversation_turn: 0, model_call_index: 2 }],
      execution_trace: [],
    };
    const trace = item.tool_trace[0];
    expect(normalizedToolCallIndex(item, trace, 0)).toBe(2);
  });

  it("四级都不匹配回 null（非整数 model_call_index 不算）", () => {
    const item = { model_call_metrics: [], tool_trace: [{ conversation_turn: 0, model_call_index: "x" }], execution_trace: [] };
    expect(normalizedToolCallIndex(item, item.tool_trace[0], 0)).toBeNull();
  });
});

describe("callIndexForStep (:1514-1523)", () => {
  it("只匹配同轮 step_id，不跨用户轮次绑定", () => {
    const item = { model_call_metrics: [{ step_id: "s", conversation_turn: 2 }] };
    expect(callIndexForStep(item, "s", 2)).toBe(0);
    expect(callIndexForStep(item, "s", 0)).toBeNull();
    expect(callIndexForStep(item, "missing", 0)).toBeNull();
    expect(callIndexForStep(item, "", 0)).toBeNull();
    expect(callIndexForStep(item, null, 0)).toBeNull();
  });
});

describe("toolsForGroupedCall (:1539-1545)", () => {
  it("只收同轮且绑定到该调用 _globalCallIndex 的工具", () => {
    const item = {
      model_call_metrics: [
        { step_id: "m1", conversation_turn: 0 },
        { step_id: "m2", conversation_turn: 0 },
      ],
      tool_trace: [
        { conversation_turn: 0, model_call_index: 1, tool: "a" },
        { conversation_turn: 0, model_call_index: 0, tool: "b" },
        { conversation_turn: 3, model_call_index: 0, tool: "c" },
      ],
      execution_trace: [],
    };
    const tools = toolsForGroupedCall(item, { conversation_turn: 0, _globalCallIndex: 0 });
    expect(tools.map((trace) => trace.tool)).toEqual(["b"]);
  });
});

// ---------------------------------------------------------------------------
// 时延归因
// ---------------------------------------------------------------------------

describe("toolDurationMs (:1547-1553)", () => {
  it("优先 duration_ms，其次 latency_s×1000，都不行回 null", () => {
    expect(toolDurationMs({ duration_ms: 5 })).toBe(5);
    expect(toolDurationMs({ duration_ms: -1, latency_s: 1.5 })).toBe(1500);
    expect(toolDurationMs({ latency_s: 2 })).toBe(2000);
    expect(toolDurationMs({})).toBeNull();
  });
});

describe("toolLatencySegments (:1554-1572)", () => {
  it("拆出查询构建 / 通道 / 融合 / 后处理，差额归工具其他", () => {
    const segments = toolLatencySegments({
      retrieval_timing: {
        query_build_ms: 10,
        channels: { visual: { latency_ms: 20 }, ocr: { latency_ms: 30 } },
        fusion_ms: 5,
        postprocess_ms: 0,
        total_ms: 100,
      },
    });
    expect(segments.map((segment) => segment.label)).toEqual(["查询构建", "visual", "ocr", "融合", "工具其他"]);
    expect(segments[4]).toMatchObject({ kind: "other", ms: 35 });
  });
  it("无 retrieval_timing 回 []", () => {
    expect(toolLatencySegments({})).toEqual([]);
  });
});

describe("callAgentLoopTiming (:1573-1591)", () => {
  it("工具耗时优先取已存 agent_loop_timing.tool_ms，否则累加工具", () => {
    const call = {
      step_id: "m1",
      call_type: "agent",
      conversation_turn: 0,
      total_ms: 100,
      ttft_ms: 20,
      agent_loop_timing: { tool_ms: 30 },
      _globalCallIndex: 0,
    };
    const item = {
      model_call_metrics: [call],
      tool_trace: [{ conversation_turn: 0, model_call_index: 0, tool: "visual", duration_ms: 5 }],
      execution_trace: [],
    };
    expect(callAgentLoopTiming(item, call).toolMs).toBe(30);
    const withoutSaved = { ...call, agent_loop_timing: {} };
    expect(callAgentLoopTiming(item, withoutSaved).toolMs).toBe(5);
  });
  it("total_ms 缺省（NaN/负）时 modelMs 回 null", () => {
    expect(callAgentLoopTiming({}, { total_ms: -3 }).modelMs).toBeNull();
    expect(callAgentLoopTiming({}, {}).totalMs).toBeNull();
  });
});

describe("callLatencySegments / latencySegmentTitle (:1592-1613)", () => {
  it("ttft / 生成 / 差额归 Agent 编排", () => {
    const call = {
      step_id: "m1",
      call_type: "agent",
      conversation_turn: 0,
      total_ms: 100,
      ttft_ms: 20,
      agent_loop_total_ms: 120,
      _globalCallIndex: 0,
    };
    const item = { model_call_metrics: [call], tool_trace: [], execution_trace: [] };
    const segments = callLatencySegments(item, call);
    expect(segments.map((segment) => segment.kind)).toEqual(["ttft", "generation", "other"]);
    expect(segments[1].ms).toBe(80);
    expect(segments[2]).toMatchObject({ label: "Agent 编排", ms: 20 });
  });

  it("title 带工具内部检索耗时明细", () => {
    expect(latencySegmentTitle({ kind: "tool", label: "视觉", ms: 1500 })).toBe("视觉 · 1.5s");
    expect(
      latencySegmentTitle({ kind: "tool", label: "视觉", ms: 1500, trace: { retrieval_timing: { total_ms: 2000 } } }),
    ).toBe("视觉 · 1.5s\n检索内部耗时 2.0s");
  });
});

describe("qaLatencySegments (:1614-1640)", () => {
  const loopItem = (breakdown: Record<string, unknown>) => ({
    conversation: [{}],
    model_call_metrics: [
      { step_id: "m1", call_type: "agent", conversation_turn: 0, total_ms: 100, agent_loop_total_ms: 100 },
    ],
    tool_trace: [],
    execution_trace: [],
    timing_breakdown: breakdown,
  });

  it("未归因差额落到「其他未归因时延」一格", () => {
    const segments = qaLatencySegments(loopItem({ wall_clock_ms: 150 }));
    expect(segments.map((segment) => segment.kind)).toEqual(["agent-loop", "other"]);
    expect(segments[1]).toMatchObject({ kind: "other", label: "其他未归因时延", ms: 50 });
  });

  it("judge_ms 单列一段，并从差额里扣除", () => {
    const segments = qaLatencySegments(loopItem({ wall_clock_ms: 200, judge_ms: 30 }));
    expect(segments.map((segment) => segment.kind)).toEqual(["agent-loop", "judge", "other"]);
    expect(segments[2].ms).toBe(70);
  });

  it("1.0 既有行为：itemTimingBreakdown 不返回 judge_queue_wait_ms，排队段永不产生", () => {
    const segments = qaLatencySegments(loopItem({ wall_clock_ms: 200, judge_queue_wait_ms: 40 }));
    expect(segments.map((segment) => segment.kind)).toEqual(["agent-loop", "other"]);
    expect(segments[1].ms).toBe(100);
  });

  it("wall_clock_ms 缺省被 Number(null)=0 认作有限值，差额为 0 不产段", () => {
    const segments = qaLatencySegments(loopItem({}));
    expect(segments.map((segment) => segment.kind)).toEqual(["agent-loop"]);
  });
});

describe("latencySegmentStyle (:1641-1645)", () => {
  it("flex 有最小 0.1，占比写进 --segment-share", () => {
    const segments = [
      { kind: "a", label: "a", ms: 1 },
      { kind: "b", label: "b", ms: 3 },
    ];
    expect(latencySegmentStyle(segments[0], segments)).toEqual({ flex: "1 1 0%", "--segment-share": "25%" });
    expect(latencySegmentStyle({ kind: "z", label: "z", ms: 0 }, segments)).toEqual({
      flex: "0.1 1 0%",
      "--segment-share": "0%",
    });
  });
});

// ---------------------------------------------------------------------------
// 调试轨迹定位
// ---------------------------------------------------------------------------

describe("runtimeDebugTurns / debugTraceForTurn (:1646-1655)", () => {
  it("只保留 debug_trace 为数组的轮，按 index 取值", () => {
    const item = {
      runtime_turns: [
        { index: 0, debug_trace: [{ type: "model" }] },
        { index: 1 },
        null,
        { index: 2, debug_trace: "x" },
      ],
    };
    expect(runtimeDebugTurns(item)).toHaveLength(1);
    expect(debugTraceForTurn(item, 0)).toEqual([{ type: "model" }]);
    expect(debugTraceForTurn(item, 1)).toEqual([]);
  });
});

describe("debugStepForCallInGroup 的三级定位 (:1680-1706)", () => {
  it("第 1 级：按 call.step_id 精确命中（优先于类型序号）", () => {
    const item = {
      runtime_turns: [
        {
          index: 0,
          debug_trace: [
            { step_id: "m1", type: "model", call_type: "agent" },
            { step_id: "m2", type: "model", call_type: "agent" },
          ],
        },
      ],
    };
    const group = {
      calls: [
        { call_type: "agent", conversation_turn: 0 },
        { call_type: "agent", conversation_turn: 0, step_id: "m2" },
      ],
    };
    // 第 2 级会按「第 2 个 agent」给出 m2，第 1 级也给出 m2；用 m1 才能区分。
    expect(debugStepForCallInGroup(item, group, { call_type: "agent", conversation_turn: 0, step_id: "m1" })?.step_id).toBe(
      "m1",
    );
  });

  it("第 2 级：无 step_id 时按调用类型取第 N 个", () => {
    const item = {
      runtime_turns: [
        {
          index: 0,
          debug_trace: [
            { step_id: "p", type: "planner" },
            { step_id: "m1", type: "model", call_type: "agent" },
            { step_id: "m2", type: "model", call_type: "agent" },
          ],
        },
      ],
    };
    const group = {
      calls: [
        { call_type: "agent", conversation_turn: 0 },
        { call_type: "agent", conversation_turn: 0 },
      ],
    };
    expect(debugStepForCallInGroup(item, group, group.calls[1])?.step_id).toBe("m2");
  });

  it("第 3 级：legacy 调用按全量序号兜底", () => {
    const item = {
      runtime_turns: [
        {
          index: 0,
          debug_trace: [
            { step_id: "p", type: "planner" },
            { step_id: "m1", type: "model", call_type: "agent" },
          ],
        },
      ],
    };
    const call = { call_type: "legacy", conversation_turn: 0 };
    const group = { calls: [call, { call_type: "agent", conversation_turn: 0 }] };
    expect(debugStepForCallInGroup(item, group, call)?.step_id).toBe("p");
  });
});

describe("debugStepForCall / debugStepsForCall (:1656-1679)", () => {
  const item = {
    runtime_turns: [
      {
        index: 0,
        debug_trace: [
          { step_id: "m1", type: "model", call_type: "agent" },
          { step_id: "m2", type: "model", call_type: "agent" },
          { step_id: "j1", type: "judge" },
        ],
      },
    ],
  };
  it("judge 调用数与 judge 步对应；agent 调用数与 model 步对应", () => {
    expect(debugStepForCall(item, { call_type: "faithfulness_judge", conversation_turn: 0 }, 0)?.step_id).toBe("j1");
    expect(debugStepForCall(item, { call_type: "agent", conversation_turn: 0 }, 1)?.step_id).toBe("m2");
    expect(debugStepForCall(item, { call_type: "agent", conversation_turn: 0 }, 5)).toBeNull();
    expect(debugStepsForCall(item, { call_type: "agent", conversation_turn: 0 }).map((step) => step.step_id)).toEqual([
      "m1",
      "m2",
    ]);
    expect(debugStepsForCall(item, { call_type: "planner", conversation_turn: 0 })).toEqual([]);
  });
});

describe("debugToolsForCall / debugPromptAnnotations (:1707-1717)", () => {
  const item = {
    runtime_turns: [
      {
        index: 0,
        debug_trace: [
          { step_id: "m1", type: "model", call_type: "agent", prompt_annotations: ["a", "b"] },
          { step_id: "t1", type: "tool", parent_step_id: "m1" },
          { step_id: "t2", type: "tool", parent_step_id: "m2" },
        ],
      },
    ],
  };
  const group = { calls: [{ call_type: "agent", conversation_turn: 0, step_id: "m1" }] };
  it("只取 parent_step_id 等于模型步 step_id 的工具步", () => {
    expect(debugToolsForCall(item, group, group.calls[0]).map((step) => step.step_id)).toEqual(["t1"]);
  });
  it("注解缺省回 []", () => {
    expect(debugPromptAnnotations(item, group, group.calls[0])).toEqual(["a", "b"]);
    expect(debugPromptAnnotations(item, group, { call_type: "agent", conversation_turn: 0, step_id: "nope" })).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// Agent2 / 归因
// ---------------------------------------------------------------------------

describe("getAgent2Trace / Requirements / LedgerEntries (:1718-1741)", () => {
  it("item 级优先；否则用 turn 级", () => {
    const itemLevel = { agent2_trace: { task_declaration: { requirements: [{ status: "satisfied" }] } } };
    expect(getAgent2Trace(itemLevel)).toBe(itemLevel.agent2_trace);

    const turnLevel = {
      runtime_turns: [{ agent2_trace: { task_state: { requirements: [{ status: "open" }] } } }],
    };
    expect(getAgent2Trace(turnLevel)).toBe(turnLevel.runtime_turns[0].agent2_trace);
    expect(getAgent2Requirements(turnLevel)).toEqual([{ status: "open" }]);
  });

  it("空 trace 回 null / []", () => {
    expect(getAgent2Trace(null)).toBeNull();
    expect(getAgent2Trace({})).toBeNull();
    expect(getAgent2Requirements({})).toEqual([]);
    expect(getAgent2LedgerEntries({})).toEqual([]);
  });

  it("ledger entries 从 evidence_ledger 取", () => {
    const item = { agent2_trace: { evidence_ledger: { entries: [{ id: 1 }] } } };
    expect(getAgent2LedgerEntries(item)).toEqual([{ id: 1 }]);
  });
});

describe("formatAgent2EvidenceValue / rows / height (:1742-1762)", () => {
  it("空值 / 空白字符串有专门文案", () => {
    expect(formatAgent2EvidenceValue(null)).toBe("已记录证据，但没有提取出的文本值");
    expect(formatAgent2EvidenceValue("   ")).toBe("已记录证据，但没有提取出的文本值");
  });
  it("JSON 字符串美化；非 JSON 原样；对象美化", () => {
    expect(formatAgent2EvidenceValue('{"a":1}')).toBe('{\n  "a": 1\n}');
    expect(formatAgent2EvidenceValue("not json")).toBe("not json");
    expect(formatAgent2EvidenceValue({ a: 1 })).toBe('{\n  "a": 1\n}');
  });
  it("行数至少 3；高度 = 行数×15.5+16", () => {
    expect(agent2EvidenceRows(null)).toBe(3);
    expect(agent2EvidenceRows("a\nb\nc\nd\ne")).toBe(5);
    expect(agent2EvidenceHeight(null)).toBe("62.5px");
    expect(agent2EvidenceHeight("a\nb\nc\nd\ne")).toBe("93.5px");
  });
});

describe("agent2 标签 / 汇总 (:1763-1793)", () => {
  it("状态标签与类名", () => {
    expect(agent2RequirementStatusLabel("satisfied")).toBe("已满足");
    expect(agent2RequirementStatusLabel("zzz")).toBe("zzz");
    expect(agent2RequirementStatusLabel(null)).toBe("未记录");
    expect(agent2RequirementStatusClass("satisfied")).toBe("status-complete");
    expect(agent2RequirementStatusClass("partially_supported")).toBe("status-partial");
    expect(agent2RequirementStatusClass(null)).toBe("status-failed");
  });
  it("证据类型标签回退到原值 / 证据", () => {
    expect(agent2EvidenceTypeLabel("visual_observation")).toBe("视觉观察");
    expect(agent2EvidenceTypeLabel("zzz")).toBe("zzz");
    expect(agent2EvidenceTypeLabel(null)).toBe("证据");
  });
  it("需求汇总与决策汇总", () => {
    const item = {
      agent2_trace: {
        task_state: { requirements: [{ status: "satisfied" }, { status: "satisfied" }, { status: "partially_supported" }] },
        planner_decisions: [{ status: "fallback" }, { status: "accepted" }],
      },
    };
    expect(agent2RequirementSummary(item)).toBe("2/3 项满足，1 项部分满足");
    expect(agent2DecisionSummary(item)).toBe("规划已接受");
    expect(agent2DecisionSummary({ agent2_trace: { planner_decisions: [{ status: "fallback" }] } })).toBe(
      "规划失败，已回退主流程",
    );
    expect(agent2DecisionSummary({})).toBe("已记录");
  });
});

describe("attributionSummary / Label / Class (:1794-1805)", () => {
  it("fail 层用 ` / ` 连接，空回 无", () => {
    expect(attributionSummary({ attribution: { primary: "检索失败", layers: { R: "fail", V: "pass", O: "fail" } } })).toEqual({
      primary: "检索失败",
      failed: "R / O",
    });
    expect(attributionSummary({})).toEqual({ primary: "未归因", failed: "无" });
  });
  it("层标签与类名", () => {
    expect(attributionLabel("R")).toBe("检索");
    expect(attributionLabel("Z")).toBe("Z");
    expect(attributionClass("fail")).toBe("score-0");
    expect(attributionClass("pass")).toBe("score-2");
    expect(attributionClass("other")).toBe("score-none");
  });
});

// ---------------------------------------------------------------------------
// 调用呈现
// ---------------------------------------------------------------------------

describe("showToolBranch / noToolLabel / callBudget / callObservation (:1471-1502)", () => {
  it("四类不展示工具分支", () => {
    expect(showToolBranch({ call_type: "planner" })).toBe(false);
    expect(showToolBranch({ call_type: "faithfulness_judge" })).toBe(false);
    expect(showToolBranch({ call_type: "agent" })).toBe(true);
  });
  it("无工具文案按类型", () => {
    expect(noToolLabel({ call_type: "agent" })).toBe("该调用直接生成回答，未触发工具。");
    expect(noToolLabel({ call_type: "recovery" })).toBe("该恢复调用未触发工具。");
    expect(noToolLabel({ call_type: "legacy" })).toBe("该历史调用没有可绑定的工具记录。");
  });
  it("预算三数齐全才出串", () => {
    expect(callBudget({ preflight_prompt_tokens: 100, effective_max_tokens: 50, max_model_len: 1000 })).toBe(
      "100 + 50 / 1000",
    );
    expect(callBudget({ prompt_tokens: 100, max_model_len: 1000 })).toBe("-");
  });
  it("观察值回退与来源映射", () => {
    const observation = callObservation({
      call_observation: { source: "backend_recorded", related_tool: "ocr" },
      parent_step_id: "p1",
    });
    expect(observation).toEqual({
      purpose: "未记录",
      trigger: "未记录",
      outcome: "未记录",
      source: "后端直接记录",
      relatedTool: "ocr",
      parentStep: "p1",
    });
    expect(callObservation({}).source).toBe("未记录");
  });
});

describe("itemToolTrace / toolExecutionSteps (:1503-1512)", () => {
  it("tool_trace 与 stage/type=tool 的执行步", () => {
    expect(itemToolTrace({ tool_trace: [{ a: 1 }, null] })).toEqual([{ a: 1 }]);
    expect(
      toolExecutionSteps({ execution_trace: [{ stage: "tool" }, { type: "tool" }, { type: "model" }] }),
    ).toHaveLength(2);
  });
});

describe("2.0 已存事实与缺失时延", () => {
  it("请求墙钟不会冒充生成耗时，缺失 TTFT 不会变成0", () => {
    const call = { call_type: "agent", duration_ms: 250, ttft_ms: null };
    const timing = callAgentLoopTiming({}, call);
    expect(timing.modelMs).toBe(250);
    expect(timing.ttftMs).toBeNull();
    expect(timing.generationMs).toBeNull();
    expect(callLatencySegments({}, call)).toEqual([{ kind: "model-request", label: "模型请求墙钟（未拆 TTFT）", ms: 250 }]);
    expect(callAgentLoopTiming({}, { duration_ms: null }).totalMs).toBeNull();
  });
  it("展示带版本的确定性派生来源", () => {
    expect(callObservation({ call_observation: { source: "saved_call_facts_v1", trigger: "步骤: s1" } }).source).toBe("已存调用事实（规则 v1）");
  });
  it("空工具索引不绑定到首轮调用", () => {
    expect(normalizedToolCallIndex({ model_call_metrics: [{}] }, { model_call_index: null }, 0)).toBeNull();
  });
});


describe("2.0 调用对齐与时延", () => {
  it("重试工具绑定成功尝试，不绑定失败调用", () => {
    const item = {model_call_metrics: [
      {step_id:"s", conversation_turn:0, status:"error"},
      {step_id:"s", conversation_turn:0, status:"complete"},
    ]};
    expect(callIndexForStep(item,"s",0)).toBe(1);
    expect(normalizedToolCallIndex(item,{parent_step_id:"s",conversation_turn:1,model_call_index:1},0)).toBeNull();
  });
  it("只依据相同工具ID从执行轨迹补父步骤", () => {
    const item = {model_call_metrics:[{step_id:"s",conversation_turn:0}],
      execution_trace:[{stage:"tool",step_id:"tool-1",parent_step_id:"s",conversation_turn:0}]};
    expect(normalizedToolCallIndex(item,{step_id:"tool-1",conversation_turn:0},0)).toBe(0);
    expect(normalizedToolCallIndex(item,{step_id:"other",conversation_turn:0},0)).toBeNull();
  });
  it("规则拒答的631ms不包含7.4秒Judge", () => {
    const item = {metrics_contract_version:2,wall_clock_ms:631.2,judge_ms:7432,
      termination_reason:"policy_refusal",model_call_metrics:[],tool_trace:[]};
    expect(itemTimingBreakdown(item)).toMatchObject({agent_wall_ms:631.2,model_ms:0,tool_ms:0,judge_ms:7432,other_ms:631.2});
    expect(qaLatencySegments(item)).toEqual([{kind:"other",label:"其他未归因时延",shortLabel:"其他",ms:631.2}]);
  });
  it("工具缺失耗时保留未知，不能把null当0", () => {
    expect(toolDurationMs({duration_ms:null,latency_s:null})).toBeNull();
    expect(toolDurationMs({duration_ms:0})).toBe(0);
  });
  it("重叠累计时间不伪造为墙钟组成", () => {
    const item={metrics_contract_version:2,wall_clock_ms:100,model_call_metrics:[{step_id:"s",duration_ms:150}],tool_trace:[]};
    expect(qaLatencySegments(item)).toEqual([]);
  });
});
