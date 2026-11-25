import { describe, expect, it } from "vitest";
import type { RunItemDetail } from "../api/types.ts";
import { turnModelCalls, turnTimeline, toolsForDebugCall } from "./agentTrace.ts";
import { normalizedToolCallIndex, callAgentLoopTiming } from "./trace.ts";
import { repeatedMessagePrefix, sameTraceValue, traceDurationLabel, traceNodeDuration, traceNodeSources } from "./traceNodes.ts";

const messages = [{role:"system",content:"系统",future:{zero:0,empty:"",no:false,nil:null}}, {role:"user",content:"题干"}];
function fixture() {
  return {index:0,metrics_contract_version:2,
    model_call_metrics:[{call_id:"a",step_id:"s1",call_type:"agent",conversation_turn:0,duration_ms:1200,streamed:false}],
    runtime_turns:[{index:0,answer:"",trace_bundle:{debug_calls:[
      {call_id:"a",step_id:"s1",seq:0,call_type:"agent",status:"complete",messages:structuredClone(messages),tools:[],duration_ms:1199,unknown:null,response:{content:"",future:0}},
      {call_id:"b",step_id:"s2",seq:1,call_type:"agent",status:"complete",messages:[...structuredClone(messages),{role:"assistant",content:"",tool_calls:[{arguments:"{invalid"}]}],tools:[],response:{content:"结果"}},
    ]}}],tool_trace:[{tool:"final_answer",step_id:"t",parent_call_id:"a",conversation_turn:0,status:"ok",duration_ms:125,arguments:{answer:""},result:{nil:null}}]};
}

describe("节点数据来源与耗时", () => {
  it("原文与指标分别引用保存对象，冲突值和未知字段完整保留，不污染源对象", () => {
    const item=fixture(), before=JSON.stringify(item);
    const entry=turnTimeline(item,0).find(e=>e.call?.call_id==="a")!;
    const sources=traceNodeSources(item,0,entry);
    const debug=(item.runtime_turns![0].trace_bundle as {debug_calls:unknown[]}).debug_calls[0];
    expect(sources.find(s=>s.kind==="debug")).toEqual({path:"runtime_turns[0].trace_bundle.debug_calls[0]",kind:"debug",value:debug});
    expect(sources.find(s=>s.kind==="debug")!.value).toBe(debug);
    expect(sources.find(s=>s.kind==="metrics")!.value).toBe(item.model_call_metrics![0]);
    expect(traceNodeDuration(entry)).toBe(1200);
    expect(JSON.stringify(item)).toBe(before);
  });
  it("零毫秒和缺失分别展示，结果节点不重复计算调用时长", () => {
    expect(traceNodeDuration({kind:"model",key:"a",call:{duration_ms:0}})).toBe(0);
    expect(traceDurationLabel({kind:"tool",key:"a",tool:{}})).toBe("耗时未记录");
    expect(traceDurationLabel({kind:"answer",key:"a",answer:""})).toBe("回合结果");
    expect(turnTimeline(fixture(),0).at(-1)!.answer).toBe("");
    expect(turnTimeline({runtime_turns:[{index:0}]},0).at(-1)!.answer).toBeUndefined();
  });
  it("相同前缀比较完整对象，未知字段、类型、空值和工具参数改变都能识别", () => {
    const item=fixture();
    const calls=(item.runtime_turns![0].trace_bundle as {debug_calls:Record<string,unknown>[]}).debug_calls;
    expect(repeatedMessagePrefix(item,0,calls[1]).count).toBe(2);
    const altered=structuredClone(messages);altered[0].future!.no=true;
    calls[1].messages=altered;
    expect(repeatedMessagePrefix(item,0,calls[1]).count).toBe(0);
    expect(sameTraceValue({a:0},{a:"0"})).toBe(false);
    expect(sameTraceValue({a:null},{})).toBe(false);
    expect(sameTraceValue({x:{arguments:"{}"}},{x:{arguments:"{ }"}})).toBe(false);
  });
  it("没有身份的真实调试调用仍能按保存位置查原文，不猜其他来源", () => {
    const item:RunItemDetail={index:0,runtime_turns:[{index:0,trace_bundle:{debug_calls:[{messages:[],response:null,future:false},{messages:[],response:{content:"第二次"}}]}}]};
    const entry=turnTimeline(item,0).filter(e=>e.call)[1];
    const source=traceNodeSources(item,0,entry)[0];
    expect(source.path).toBe("runtime_turns[0].trace_bundle.debug_calls[1]");
    expect(source.value).toBe((item.runtime_turns![0].trace_bundle as {debug_calls:unknown[]}).debug_calls[1]);
  });
  it("同轮两个成功尝试的父步骤有歧义时工具不重复绑定", () => {
    const item:RunItemDetail={index:0,runtime_turns:[{index:0,trace_bundle:{debug_calls:[
      {call_type:"agent",step_id:"s",seq:0,attempt:1,status:"complete"},
      {call_type:"agent",step_id:"s",seq:1,attempt:2,status:"complete"},
    ]}}],tool_trace:[{tool:"search",parent_step_id:"s"}]};
    const nodes=turnTimeline(item,0);
    expect(nodes.filter(n=>n.tool)).toHaveLength(1);
    expect(nodes.find(n=>n.tool)!.unbound).toBe(true);
  });
  it("精确父ID也必须在同轮唯一，不跨轮次、不借用重复ID", () => {
    const item=fixture();
    item.model_call_metrics!.push({...item.model_call_metrics![0]});
    expect(normalizedToolCallIndex(item,item.tool_trace![0],0)).toBeNull();
    expect(turnModelCalls(item,0).every(c=>!toolsForDebugCall(item,0,c).length)).toBe(true);
    item.model_call_metrics![1].conversation_turn=1;
    expect(normalizedToolCallIndex(item,item.tool_trace![0],0)).toBe(0);
  });
  it("只有debug的调用仍能绑定实际工具耗时，缺失工具时长不计成已采集零值", () => {
    const item=fixture();item.model_call_metrics=[];
    const call=turnModelCalls(item,0)[0];
    const tools=toolsForDebugCall(item,0,call);
    expect(tools).toHaveLength(1);
    expect(callAgentLoopTiming(item,call,tools).toolMs).toBe(125);
    expect(callAgentLoopTiming(item,call,tools).totalMs).toBe(1324);
  });
});
