import { cleanup, fireEvent, render, screen, within, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { RunItemDetail } from "../../api/types.ts";
import { TraceNodeBrowser } from "./TraceNodeBrowser.tsx";
import * as client from "../../api/client.ts";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it('懒加载节点按需恢复本轮模型与工具耗时，含工具内部耗时', async () => {
  const request = vi.spyOn(client, 'api').mockImplementation(async path => {
    if (String(path).includes('view=timing')) return { node: { call_type: 'agent', duration_ms: 1000, ttft_ms: 100 }, tools: [{ tool: 'search_memories', duration_ms: 500, retrieval_timing: { channels: { text: { latency_ms: 200 } } }, model_calls: [{ duration_ms: 150, prompt_tokens: 12 }] }] };
    return { nodes: [{ node_id: 't0_gcalls_i0', kind: 'model', turn_index: 0, metadata: { step_id: 's1' }, fields: ['messages'] }], total: 1 };
  });
  render(<TraceNodeBrowser detail={{ index: 0, run_id: 'r', trace_storage: { version: 3, revision: 'rev', base_url: '/chat/sessions/chat1/turns/2' } }} turnIndex={0}/>);
  await screen.findByRole('button', { name: /s1/ }); choose(/s1/);
  expect(request).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('tab', { name: '耗时与指标' }));
  expect(await screen.findByText('本步调用累计 · 模型与关联工具')).toBeTruthy();
  expect(screen.getByText('1.5s')).toBeTruthy();
  expect(screen.getByText('text 200ms')).toBeTruthy();
  expect(screen.getByText('内部模型 1 · 150ms')).toBeTruthy();
  expect(request).toHaveBeenCalledTimes(2);
  expect(String(request.mock.calls[1][0])).toContain('/chat/sessions/chat1/turns/2/trace/t0_gcalls_i0?revision=rev&view=timing');
});
const context=[{role:"system",content:"系统原文",extension:{zero:0,no:false,nil:null,empty:""}},{role:"user",content:"问题"}];
const first={call_id:"c1",call_type:"agent",step_id:"step_1",seq:0,status:"complete",duration_ms:1200,streamed:false,model:"model-current",messages:context,tools:[{function:{name:"search"},future:null}],response:{content:"",thought:"原思考",tool_calls:[{arguments:"{broken"}],future:false}};
const second={...first,call_id:"c2",step_id:"step_2",seq:1,attempt:2,duration_ms:0,messages:[...structuredClone(context),{role:"tool",content:"新增观察"}],response:{content:"答案"}};
const item:RunItemDetail={index:0,runtime_turns:[{index:0,answer:"答案",trace_bundle:{debug_calls:[first,second]},trace:[{kind:"terminal",payload:{new_field:false}}]}],tool_trace:[]};

async function expand(label:string|RegExp){const summary=screen.getByText(label,{selector:"summary"});if(!summary.parentElement?.hasAttribute("open")) fireEvent.click(summary);await waitFor(()=>expect(screen.getByText(label,{selector:"summary"}).parentElement?.querySelector("pre")).toBeTruthy());}
function choose(name:RegExp){fireEvent.click(within(screen.getByRole("navigation",{name:"调用节点"})).getByRole("button",{name}));}

it("节点显示自身耗时；指标保留TTFT、模型、Token与拆分，切换不累计原文DOM", async()=>{
  const {container}=render(<TraceNodeBrowser detail={item} turnIndex={0}/>);
  expect(container.querySelector("pre")).toBeNull();
  expect(within(screen.getByRole("navigation")).getByRole("button",{name:/step_1.*1.2s/})).toBeTruthy();
  await expand("messages[0] · system");
  expect(JSON.parse(container.querySelector("pre")!.textContent!)).toEqual(context[0]);
  choose(/step_2/);
  expect(container.querySelector("pre")).toBeNull();
  fireEvent.click(screen.getByRole("tab",{name:"耗时与指标"}));
  expect(screen.getByText("请求耗时 0ms")).toBeTruthy();
  expect(screen.getByText("TTFT 不适用（非流式）")).toBeTruthy();
  expect(screen.getByText("模型 model-current")).toBeTruthy();
  expect(screen.getByText("Token 输入 未记录 / 输出 未记录")).toBeTruthy();
});
it("重复前缀只折叠本次原文，新增消息保留原索引，输出未知字段和无效参数串不变",async()=>{
  const {container}=render(<TraceNodeBrowser detail={item} turnIndex={0}/>);
  choose(/step_2/);
  expect(screen.getByText(/前 2 条与 step_1 完全一致 · 本次仍携带/)).toBeTruthy();
  expect(screen.getByText("messages[2] · tool")).toBeTruthy();
  await expand("messages[0] · system");
  expect(JSON.parse(container.querySelector("pre")!.textContent!)).toEqual(context[0]);
  choose(/step_1/);
  fireEvent.click(screen.getByRole("tab",{name:"输出"}));
  await expand("已保存 response · 全部字段");
  expect(JSON.parse(container.querySelector("pre")!.textContent!)).toEqual(first.response);
});
it("完整记录可还原全部字段和值，新增数据不抢走用户选择", async()=>{
  const {container,rerender}=render(<TraceNodeBrowser detail={item} turnIndex={0}/>);
  choose(/step_2/);
  rerender(<TraceNodeBrowser detail={{...item,tool_trace:[{tool:"new-tool",status:"running"}]}} turnIndex={0}/>);
  expect(within(screen.getByRole("navigation")).getByRole("button",{name:/step_2/}).getAttribute("aria-pressed")).toBe("true");
  fireEvent.click(screen.getByRole("tab",{name:"完整记录"}));
  await expand("本题完整已保存对象 · 全部字段");
  expect(JSON.parse(container.querySelector("pre")!.textContent!).runtime_turns).toEqual(item.runtime_turns);
});
it("每个用户轮次的相同步骤与重复前缀独立",()=>{
  const copy:RunItemDetail={...item,runtime_turns:[...item.runtime_turns!,{index:1,trace_bundle:{debug_calls:[{...second,messages:[{role:"user",content:"第二轮"}]}]}}]};
  const {container}=render(<TraceNodeBrowser detail={copy} turnIndex={1}/>);
  expect(container.textContent).not.toContain("完全一致");
  expect(within(screen.getByRole("navigation")).getAllByRole("button")).toHaveLength(2);
});


it("懒加载保留左右分栏与输入输出页签，仅展开字段才取正文，翻页不累计节点", async () => {
  const request = vi.spyOn(client, "api").mockImplementation(async (path) => {
    const url = String(path);
    if (url.includes("/trace/t0_gcalls_i0")) return { text: JSON.stringify([{role:"user",content:"完整输入"}]), offset: 0, next_offset: null };
    if (url.includes("offset=50")) return { nodes: [{ node_id: "t0_gcalls_i50", kind: "tool", turn_index: 0, metadata:{tool:"search_memories"} }], total: 51, offset:50, next_offset: null };
    return { nodes: [{ node_id: "t0_gcalls_i0", kind: "model", turn_index: 0, metadata: { step_id: "step-1",duration_ms:1200 }, fields:["messages","response"] }], total: 51, next_offset: 50 };
  });
  const detail = { index: 0, run_id: "split-run", trace_storage: { version: 3, revision: "abc" }, runtime_turns: [{ index: 0 }] } as RunItemDetail;
  const {container}=render(<TraceNodeBrowser detail={detail} turnIndex={0} />);
  await screen.findByRole("button", { name: /step-1/ });
  expect(container.querySelector('.trace-node-workspace')?.children).toHaveLength(2);
  expect(container.querySelector('.trace-view-tabs')?.parentElement?.className).toBe('trace-node-browser');
  expect(request).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: /step-1/ }));
  expect(screen.getByRole('tab',{name:'输入'})).toBeTruthy();
  expect(screen.getByRole('tab',{name:'输出'})).toBeTruthy();
  expect(screen.getByRole('tab',{name:'耗时与指标'})).toBeTruthy();
  expect(request).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByText('本次保存输入 · messages',{selector:'summary'}));
  await screen.findByText('messages[0] · user');
  expect(request).toHaveBeenCalledTimes(2);
  expect(String(request.mock.calls[1][0])).toContain('field=messages');
  expect(container.querySelector('pre')).toBeNull();
  await expand('messages[0] · user');
  expect(container.textContent).toContain('完整输入');
  fireEvent.click(screen.getByRole("button", { name: "下一页" }));
  await screen.findByRole("button", { name: /search_memories/ });
  expect(request).toHaveBeenCalledTimes(3);
  expect(container.querySelector('pre')).toBeNull();
  expect(screen.queryByText('完整输入')).toBeNull();
});

it('切换节点或正文段时释放旧内容，迟到响应不能覆盖当前节点', async()=>{
  let resolveFirst: (value: unknown) => void = () => {};
  const request=vi.spyOn(client,'api').mockImplementation(async path=>{
    const url=String(path);
    if(!url.includes('/trace/t')) return {nodes:[0,1].map(i=>({node_id:`t0_gcalls_i${i}`,kind:'model',turn_index:0,metadata:{step_id:`s${i}`},fields:['response']})),total:2};
    if(url.includes('i0')) return new Promise(resolve=>{resolveFirst=resolve});
    if(url.includes('offset=32768')) return {text:'尾段',offset:32768,next_offset:null};
    return {text:'当前节点',offset:0,next_offset:32768};
  });
  const {container}=render(<TraceNodeBrowser detail={{index:0,run_id:'r',trace_storage:{version:3,revision:'rev'}}} turnIndex={0}/>);
  await screen.findByRole('button',{name:/s0/});
  choose(/s0/);fireEvent.click(screen.getByRole('tab',{name:'输出'}));
  await waitFor(()=>expect(request).toHaveBeenCalledTimes(2));
  choose(/s1/);fireEvent.click(screen.getByRole('tab',{name:'输出'}));
  await screen.findByText('当前节点',{selector:'pre'});
  resolveFirst({text:'已过期',offset:0});
  await waitFor(()=>expect(container.textContent).not.toContain('已过期'));
  fireEvent.click(screen.getByRole('button',{name:'下一段'}));
  await screen.findByText('尾段',{selector:'pre'});
  expect(container.textContent).not.toContain('当前节点');
  expect(container.querySelectorAll('pre')).toHaveLength(1);
});

it('消息索引只按展开的一条读取，折叠消息和其他输入均不取正文', async()=>{
  const request=vi.spyOn(client,'api').mockImplementation(async path=>{
    const url=String(path);
    if(url.includes('field=messages.1')) return {text:JSON.stringify({role:'user',content:'仅本条问题'}),offset:0};
    return {nodes:[{node_id:'t0_gcalls_i0',kind:'planner',turn_index:0,metadata:{step_id:'planner_1'},fields:['messages','tools'],message_count:2,message_roles:[{index:0,role:'system'},{index:1,role:'user'}]}],total:1};
  });
  render(<TraceNodeBrowser detail={{index:0,run_id:'r',trace_storage:{version:3,revision:'rev'}}} turnIndex={0}/>);
  await screen.findByRole('button',{name:/planner_1/});
  choose(/planner_1/);
  expect(request).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByText('messages[1] · user',{selector:'summary'}));
  await screen.findByText(/仅本条问题/,{selector:'pre'});
  expect(request).toHaveBeenCalledTimes(2);
  expect(String(request.mock.calls[1][0])).toContain('field=messages.1');
  expect(String(request.mock.calls[1][0])).not.toContain('field=messages&');
});

it('带原始位置的节点直接读取同版本原文，兼容旧存档的节点引用', async()=>{
  const request=vi.spyOn(client,'api').mockImplementation(async path=>{
    const url=String(path);
    if(url.includes('/content?')) return {text:JSON.stringify({content:'真实输入'}),offset:0};
    return {nodes:[{node_id:'t0_gcalls_i0',kind:'model',turn_index:0,metadata:{step_id:'source-node'},source_path:['runtime_turns',0,'trace_bundle','debug_calls',0],fields:['messages'],message_count:1,message_roles:[{index:0,role:'user'}]}],total:1};
  });
  render(<TraceNodeBrowser detail={{index:0,run_id:'r',trace_storage:{version:3,revision:'pinned'}}} turnIndex={0}/>);
  await screen.findByRole('button',{name:/source-node/});choose(/source-node/);
  fireEvent.click(screen.getByText('messages[0] · user',{selector:'summary'}));
  await screen.findByText(/真实输入/,{selector:'pre'});
  expect(String(request.mock.calls[1][0])).toContain('path=runtime_turns.0.trace_bundle.debug_calls.0.messages.0');
  expect(String(request.mock.calls[1][0])).toContain('revision=pinned');
});
