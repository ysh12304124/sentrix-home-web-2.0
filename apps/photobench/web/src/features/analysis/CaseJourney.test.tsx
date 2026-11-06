import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CaseJourney } from "./CaseJourney.tsx";
import { journeyNodes, cleanNotes } from "./journey.ts";
import { api } from "../../api/client.ts";
import type { DiagnosisReport } from "../../api/analysis.ts";
import type { RunItemDetail } from "../../api/types.ts";
vi.mock("../../api/client.ts",()=>({api:vi.fn()}));
afterEach(()=>{cleanup();vi.resetAllMocks();});
const item:RunItemDetail={index:0,qa_id:"q1",question:"这次旅行去了哪里？",answer:"回答正文",reference_answer:"参考正文",judge:{score:0},runtime_turns:[{index:0,answer:"回答正文",trace_bundle:{debug_calls:[{call_id:"p",call_type:"planner",step_id:"plan",attempt:1,status:"completed",messages:[{role:"user",content:"输入"}],response:{content:"规划正文"}},{call_id:"a",call_type:"agent",step_id:"s1",attempt:1,status:"error",error:"请求失败"},{call_id:"b",call_type:"agent",step_id:"s1",attempt:2,status:"completed",response:{content:"成功重试"}}]}}]};
const locations={"i0:turn0":"runtime_turns[0]","i0:turn0:debug0":"runtime_turns[0].trace_bundle.debug_calls[0]","i0:turn0:debug1":"runtime_turns[0].trace_bundle.debug_calls[1]","i0:turn0:debug2":"runtime_turns[0].trace_bundle.debug_calls[2]","i0:case":"case"};
const diagnosis:DiagnosisReport["cases"][number]={index:0,qa_id:"q1",valid:true,raw:"",result:{diagnosis:{error_types:["incorrect_answer"],summary:"最终回答与参考内容不一致",cause:"回答缺少参考内容",certainty:"confirmed",next_step:"修正遗漏内容",evidence_refs:["i0:turn0"],limitations:""},claims:[{kind:"observation",text:"最终回答与参考内容不一致",limitations:"无",evidence_refs:["i0:turn0"]},{kind:"hypothesis",text:"需要核查规划是否遗漏定位条件",limitations:"原因未确认",evidence_refs:["i0:turn0:debug0"]}],missing_evidence:["无"]}};
async function open(){vi.mocked(api).mockResolvedValue({item,evidence_locations:locations});const navigate=vi.fn();const result=render(<CaseJourney diagnosis={diagnosis} jobId="frozen" navigate={navigate}/>);fireEvent.click(screen.getByText("q1"));await screen.findByText(item.question!);return {...result,navigate};}
describe("QA轨迹与分析",()=>{
 it("顶层服务错误作为真实错误节点展示和定位",()=>{const nodes=journeyNodes({index:0,qa_id:'q',error:'HTTP 500 Internal Server Error'},{'i0:execution':''});expect(nodes[0].title).toBe('答题请求失败');expect(nodes[0].failed).toBe(true);expect(nodes[0].refs).toContain('i0:execution');});

 it("收起时不请求原始轨迹，展开使用分析的冻结来源",async()=>{vi.mocked(api).mockResolvedValue({item,evidence_locations:locations});render(<CaseJourney diagnosis={diagnosis} jobId="frozen" navigate={vi.fn()}/>);expect(api).not.toHaveBeenCalled();fireEvent.click(screen.getByText("q1"));await waitFor(()=>expect(api).toHaveBeenCalledWith('/analyses/frozen/evidence/0',expect.anything()));});
 it("默认展示最终回答，点击规划在本题内查看对应分析",async()=>{const {navigate,container}=await open();expect(screen.getByLabelText("节点详情").textContent).toContain("回答正文");fireEvent.click(screen.getByRole('button',{name:/^1任务规划/}));expect(screen.getByLabelText('节点详情').textContent).toContain('需要核查规划是否遗漏定位条件');expect(screen.getByLabelText('节点详情').textContent).toContain('规划正文');expect(navigate).not.toHaveBeenCalled();expect(container.querySelector('.journey-all-findings')?.hasAttribute('open')).toBe(false);expect(screen.queryByText('无')).toBeNull();});
 it("回答引用可读，点击精确选中回答节点",async()=>{await open();fireEvent.click(screen.getByRole('button',{name:/^1任务规划/}));fireEvent.click(screen.getAllByRole('button',{name:'第1轮 · 最终回答'})[0]);expect(screen.getByRole('button',{name:/最终回答.*已记录回答/}).getAttribute('aria-pressed')).toBe('true');expect(screen.getByLabelText('节点详情').textContent).toContain('AI 对此节点的分析');});
 it("请求失败保留AI结论并提供原题入口",async()=>{vi.mocked(api).mockRejectedValue(new Error('不可读取'));const nav=vi.fn();render(<CaseJourney diagnosis={diagnosis} jobId="old" navigate={nav}/>);fireEvent.click(screen.getByText('q1'));await screen.findByRole('alert');fireEvent.click(screen.getByText('打开原题记录'));expect(nav).toHaveBeenCalledWith('i0:case');});
 it("重试按唯一调用身份绑定，异常不借用后续成功状态",()=>{const nodes=journeyNodes(item,locations);const bad=nodes.find(n=>n.refs.includes('i0:turn0:debug1'))!,good=nodes.find(n=>n.refs.includes('i0:turn0:debug2'))!;expect(bad.failed).toBe(true);expect(good.failed).toBe(false);expect(bad.id).not.toBe(good.id);});
 it("相同步骤的两次尝试无身份时不猜引用归属",()=>{const copy=structuredClone(item);copy.runtime_turns![0].trace_bundle={debug_calls:[{step_id:'s',call_type:'agent',status:'completed'},{step_id:'s',call_type:'agent',status:'completed'}]};const nodes=journeyNodes(copy,{'ambiguous':'runtime_turns[0].trace_bundle.debug_calls[0]'});expect(nodes.every(n=>!n.refs.includes('ambiguous'))).toBe(true);});
 it("不同轮次的同名步骤保持分离",()=>{const copy=structuredClone(item);copy.runtime_turns!.push({...structuredClone(copy.runtime_turns![0]),index:1});const nodes=journeyNodes(copy,{'second':'runtime_turns[1].trace_bundle.debug_calls[0]'});expect(nodes.find(n=>n.refs.includes('second'))?.turn).toBe(1);});
 it("无轨迹不生成虚构模型步骤，未知限制保留",()=>{expect(journeyNodes({index:0,qa_id:'q'},{})).toEqual([]);expect(cleanNotes(['无','unknown','恢复关系未记录'])).toEqual(['unknown','恢复关系未记录']);});
});

it("GT图片引用定位到图片来源，不冒充Agent执行节点",async()=>{
  vi.mocked(api).mockResolvedValue({item,evidence_locations:locations});
  const copy=structuredClone(diagnosis);
  copy.gt_images=[{id:"i0:t0:gt0",media_id:"nested/gt.png",media_type:"image",turn:0,source:"qa.answer_evidence",status:"attached",sha256:"frozen-sha"}];
  copy.result.diagnosis!.evidence_refs=["i0:t0:gt0"];
  const navigate=vi.fn();
  render(<CaseJourney diagnosis={copy} jobId="frozen" navigate={navigate}/>);
  fireEvent.click(screen.getByText("q1"));
  await screen.findByText(item.question!);
  fireEvent.click(screen.getByRole("button",{name:"依据 1"}));
  expect(screen.getByLabelText("节点详情").textContent).toContain("nested/gt.png");
  expect(screen.getByLabelText("节点详情").textContent).toContain("不是 Agent 调用节点");
  expect(navigate).not.toHaveBeenCalled();
});

it("折叠时直接展示GT图片失败原因，无需打开日志",()=>{
  const copy=structuredClone(diagnosis);
  copy.valid=false;copy.status="failed";copy.error="gt_images_unavailable_or_changed";copy.result={};
  render(<CaseJourney diagnosis={copy} jobId="frozen" navigate={vi.fn()}/>);
  expect(screen.getByText("GT 图片读取或校验失败，未请求分析模型")).toBeTruthy();
  expect(screen.getByText("分析失败")).toBeTruthy();
  expect(api).not.toHaveBeenCalled();
});

it("AI输出校验失败直接显示具体状态",()=>{
  const copy=structuredClone(diagnosis);
  copy.valid=false;copy.status="invalid";copy.error="invalid_references";copy.result={};
  render(<CaseJourney diagnosis={copy} jobId="frozen" navigate={vi.fn()}/>);
  expect(screen.getByText("AI 输出格式或引用未通过校验")).toBeTruthy();
});
