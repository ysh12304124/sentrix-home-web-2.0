import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api } from "../../api/client.ts";
import { ComparisonJourney } from "./ComparisonJourney.tsx";
import { alignJourneys, type ComparisonCase, type ComparisonEvidence, type ComparisonEvidenceSide, type Side } from "./comparison.ts";
import type { JourneyNode } from "./journey.ts";
vi.mock("../../api/client.ts",()=>({api:vi.fn()}));
beforeEach(()=>{HTMLElement.prototype.scrollIntoView=vi.fn();});
afterEach(()=>{cleanup();vi.resetAllMocks();});
const refs=["left:i2:item.runtime_turns[0].trace_bundle.debug_calls[0]","right:i3:item.runtime_turns[0].trace_bundle.debug_calls[0]"];
const pair:ComparisonCase={index:2,right_index:3,qa_id:"q2",pairing:"paired",valid:true,result:{similarities:[],evaluation:[],missing_evidence:[],divergences:[{kind:"observation",text:"left轮保留了定位线索，right轮遗漏了线索",limitations:"原因待核",evidence_refs:refs}]}};
function evidence():ComparisonEvidence {
  const make=(side:Side,i:number):ComparisonEvidenceSide=>({
    index:i+2,source:{run_id:`20261102-${side}`,model:`model-${i}`},qa:{answer:"GT"},gt_images:[],
    item:{index:i+2,qa_id:"q2",question:"同一题",answer:`answer-${side}`,reference_answer:"GT",judge:{score:i},runtime_turns:[{index:0,answer:`answer-${side}`,trace_bundle:{debug_calls:[{call_id:side,call_type:"planner",status:"completed",step_id:"plan",messages:[{role:"user",content:`input-${side}`}],response:{content:`plan-${side}`}}]}}]},
    evidence_locations:{[refs[i]]:"item.runtime_turns[0].trace_bundle.debug_calls[0]"},
  });
  return {job_id:"cmp",index:2,sides:{left:make("left",0),right:make("right",1)}};
}
async function open(item=pair){vi.mocked(api).mockResolvedValue(evidence());const view=render(<ComparisonJourney item={item} jobId="cmp"/>);expect(api).not.toHaveBeenCalled();fireEvent.click(screen.getByText("q2"));await screen.findByText("同一题");return view;}
it("按需读取对比时冻结的两边轨迹，展示明确的任务、模型和得分",async()=>{
  await open();expect(api).toHaveBeenCalledWith('/analyses/cmp/evidence/2',expect.anything());
  expect(screen.getByText("20261102-left")).toBeTruthy();expect(screen.getByText("20261102-right")).toBeTruthy();
  expect(screen.getByText("model-0")).toBeTruthy();expect(screen.getByText("0/2 分")).toBeTruthy();
  expect(within(screen.getByRole("tabpanel",{name:"分叉点"})).getByText("本次测评保留了定位线索，对照测评遗漏了线索")).toBeTruthy();
  expect(within(screen.getByRole("tabpanel",{name:"分叉点"})).queryByText(/left轮/)).toBeNull();
});
it("选择同类节点并排查看输入输出，定位AI引用同时高亮两边",async()=>{
  const view=await open();
  fireEvent.click(screen.getByRole("button",{name:"定位这条结论的执行记录 →"}));
  expect(view.container.querySelectorAll('.is-cited').length).toBe(2);
  const left=screen.getByLabelText("本次测评节点详情"),right=screen.getByLabelText("对照测评节点详情");
  expect(left.textContent).toContain("plan-left");expect(right.textContent).toContain("plan-right");
  expect(left.textContent).toContain("AI 对此节点的观察");
  fireEvent.click(screen.getAllByRole("button",{name:/任务规划.*调用完成/})[0]);
  expect(view.container.querySelectorAll('[aria-pressed=true]').length).toBe(2);
});
it("整题引用展示完整来源，不把宽泛引用猜成某个节点",async()=>{
  const copy=structuredClone(pair);copy.result!.divergences[0].evidence_refs=["left:i2:item","right:i3:item"];
  const frozen=evidence();frozen.sides.left.evidence_locations['left:i2:item']='item';
  vi.mocked(api).mockResolvedValue(frozen);
  const view=render(<ComparisonJourney item={copy} jobId="cmp"/>);
  fireEvent.click(screen.getByText('q2'));await screen.findByText('同一题');
  fireEvent.click(screen.getByRole('button',{name:'本次测评 · 完整答题记录'}));
  expect(within(screen.getByLabelText('本次测评节点详情')).getByText('引用的原始记录')).toBeTruthy();
  expect(view.container.querySelectorAll('.is-cited').length).toBe(0);
});
it("总览来源可自动展开对应配对，右侧题号不同仍读取正确pair",async()=>{
  vi.mocked(api).mockResolvedValue(evidence());
  render(<ComparisonJourney item={pair} jobId="cmp" focus={{index:2,refs}}/>);
  await waitFor(()=>expect(screen.getByLabelText('对照测评节点详情').textContent).toContain('plan-right'));
  expect(api).toHaveBeenCalledWith('/analyses/cmp/evidence/2',expect.anything());
});
it("缺少冻结证据明确失败，不读取当前run替代",async()=>{
  vi.mocked(api).mockRejectedValue(new Error('snapshot missing'));
  render(<ComparisonJourney item={pair} jobId="cmp"/>);fireEvent.click(screen.getByText('q2'));
  await screen.findByRole('alert');expect(api).toHaveBeenCalledTimes(1);
});
it("按轮次和调用类型保序对齐，额外调用保留空位而不捏造步骤",()=>{
  const node=(id:string,turn=0):JourneyNode=>({id,turn,title:id,status:'已记录调用',failed:false,refs:[],entry:{key:id,kind:'tool',tool:{tool:id}}});
  const rows=alignJourneys([node('search'),node('retry'),node('answer')],[node('search'),node('answer')]);
  expect(rows.map(r=>[r.left?.id,r.right?.id])).toEqual([['search','search'],['retry',undefined],['answer','answer']]);
  expect(alignJourneys([node('search',0)],[node('search',1)]).length).toBe(2);
});

it("引用校验失败且result为空时保留轨迹，不渲染无效AI结论",async()=>{
  await open({...pair,valid:false,result:{} as ComparisonCase["result"],error:"invalid_references"});
  expect(screen.getByText("AI结论的引用校验未通过，执行轨迹仍可查看",{selector:"p"})).toBeTruthy();
  expect(screen.queryByRole("tablist",{name:"本题对比结论"})).toBeNull();
  expect(screen.getByText("执行轨迹对比")).toBeTruthy();
});
