import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { api, post } from "../../api/client.ts";
import { AIComparison } from "./AIComparison.tsx";
import type { AIComparisonReport } from "./comparison.ts";
vi.mock("../../api/client.ts",()=>({api:vi.fn(),post:vi.fn(),BASE:'/api/v1'}));
vi.mock('./ComparisonPicker.tsx',()=>({ComparisonPicker:({onChange}:{onChange:(id:string)=>void})=><button onClick={()=>onChange('other')}>选择任务</button>}));
beforeEach(()=>{HTMLElement.prototype.scrollIntoView=vi.fn();});
afterEach(()=>{cleanup();vi.resetAllMocks();});
const report:AIComparisonReport={requested:1,coverage:{valid:1,failed:0,unmatched:0,unprocessed:0},summary_status:'completed',model:'J',model_role:'judge',prompt_version:'v',sources:{left:{run_id:'current',model:'M1'},right:{run_id:'other',model:'M2'}},scope:{comparison_filter:'all',scope_count:1},note:'已保存的证据',cases:[{index:3,right_index:9,qa_id:'paired-q',pairing:'paired',valid:true,result:{similarities:[],divergences:[],evaluation:[],missing_evidence:[]}}],overview:{similarities:[],divergences:[],evaluation:[{kind:'observation',text:'两次回答不同',limitations:'',evidence_refs:['right:i9:item']}],missing_evidence:[]}};
const saved={job_id:'saved',kind:'ai_compare',run_id:'current',status:'completed',processed:1,options:{left_job:'rules'},report};
const props={runId:'current',jobId:'rules',scope:'filter',filters:{},modelRole:'judge',disabled:false};
it('刷新恢复同规则来源的已有对比，点击对照引用准确打开配对题',async()=>{
  vi.mocked(api).mockImplementation(async path=>{
    if(String(path).endsWith('/analyses'))return [saved];
    if(String(path)==='/analyses/saved')return saved;
    if(String(path)==='/analyses/saved/evidence/3')return {job_id:'saved',index:3,sides:Object.fromEntries(['left','right'].map((side,i)=>[side,{index:i?9:3,source:report.sources[side as 'left'|'right'],qa:null,gt_images:[],item:{index:i?9:3,qa_id:'paired-q',question:'问题',answer:'答复',judge:{score:0}},evidence_locations:{'right:i9:item':'item'}}]))};
    throw new Error('unexpected '+path);
  });
  render(<AIComparison {...props}/>);
  await screen.findByText('两次回答不同');
  expect(screen.queryByRole('link',{name:'导出对比 HTML'})).toBeNull();
  expect(screen.getByText('current')).toBeTruthy();expect(screen.getByText('other')).toBeTruthy();
  fireEvent.click(screen.getByRole('button',{name:'对照测评 · 完整答题记录'}));
  await screen.findByText('问题');
  expect(api).toHaveBeenCalledWith('/analyses/saved/evidence/3',expect.anything());
  expect(screen.getByLabelText('对照测评节点详情').textContent).toContain('引用的原始记录');
  expect(post).not.toHaveBeenCalled();
});
it('迟到的历史读取不能覆盖用户新启动的对比',async()=>{
  let finish!: (value:unknown)=>void;
  vi.mocked(api).mockImplementation(async path=>{
    if(String(path)==='/runs/current/analyses')return new Promise(resolve=>{finish=resolve;});
    if(String(path)==='/runs/other/analysis')return {job_id:'other-rules',status:'completed',processed:1};
    throw new Error('unexpected '+path);
  });
  vi.mocked(post).mockResolvedValue({...saved,job_id:'new',report:{...report,overview:{...report.overview,evaluation:[{kind:'observation',text:'新对比结果',limitations:'',evidence_refs:[]}]}}});
  render(<AIComparison {...props}/>);fireEvent.click(screen.getByText('选择任务'));fireEvent.click(screen.getByText('开始 AI 对比'));
  await screen.findByText('新对比结果');finish([saved]);
  await waitFor(()=>expect(screen.getByText('新对比结果')).toBeTruthy());
  expect(api).not.toHaveBeenCalledWith('/analyses/saved',expect.anything());
});
