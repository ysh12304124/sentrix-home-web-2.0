import { useState } from 'react';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ScoringSettings } from './ScoringSettings.tsx';
import { ScoringResults, ItemScoringResults } from './ScoringResults.tsx';
import { ScoringPromptEditor } from './ScoringPromptEditor.tsx';
import { ATM_PROFILE, OPEN_PROFILE, PROFILE_LABELS, defaultScoring, scoringPayload } from '../lib/scoring.ts';

afterEach(cleanup);
describe('独立评分标准', () => {
  it('默认只选开放式，双选后GT独立，说明默认折叠，至少保留一个标准', () => {
    function Settings() {const [value, setValue] = useState(defaultScoring());return <ScoringSettings value={value} onChange={setValue} />;}
    const {container} = render(<Settings />);
    const open = screen.getByRole('checkbox', {name: PROFILE_LABELS[OPEN_PROFILE]}) as HTMLInputElement;
    const atm = screen.getByRole('checkbox', {name: PROFILE_LABELS[ATM_PROFILE]}) as HTMLInputElement;
    expect(open.checked).toBe(true); expect(atm.checked).toBe(false);
    expect(screen.getByText(/可只选一套，也可同时选择两套/)).toBeTruthy();
    expect(container.querySelector('details')?.open).toBe(false);
    fireEvent.click(open); expect(open.checked).toBe(true);
    fireEvent.click(atm); fireEvent.click(screen.getByLabelText(PROFILE_LABELS[OPEN_PROFILE]+' · GT图片辅助'));
    expect((screen.getByLabelText(PROFILE_LABELS[ATM_PROFILE]+' · GT图片辅助') as HTMLInputElement).checked).toBe(false);
    expect(screen.queryByLabelText(/GT元数据辅助/)).toBeNull();
    fireEvent.click(open);
    expect(open.checked).toBe(false); expect(atm.checked).toBe(true);
    expect((screen.getByLabelText(PROFILE_LABELS[OPEN_PROFILE]+' · GT图片辅助') as HTMLInputElement).disabled).toBe(true);
    fireEvent.click(atm); expect(atm.checked).toBe(true);
  });
  it('提交保留不同标准的草稿，主动清空也交给后端校验', () => {
    const selection=defaultScoring(); selection.profiles.push(ATM_PROFILE);
    const body=scoringPayload(selection, {[OPEN_PROFILE+':answer_quality']:'open', [ATM_PROFILE+':choice']:'', [ATM_PROFILE+':number']:'number', task_decision:'diagnostic'});
    expect(body.scoring_configs[OPEN_PROFILE].prompts).toEqual({answer_quality:'open'});
    expect(body.scoring_configs[ATM_PROFILE].prompts).toEqual({choice:'', number:'number'});
  });
  it('新评分由图片开关控制整套材料，不提交旧元数据开关', () => {
    const selection=defaultScoring(); selection.profiles.push(ATM_PROFILE);
    selection.configs[OPEN_PROFILE].use_gt_metadata=false;
    selection.configs[OPEN_PROFILE].use_gt_images=true;
    const body=scoringPayload(selection);
    expect(body.scoring_configs[OPEN_PROFILE].use_gt_images).toBe(true);
    for (const profile of [OPEN_PROFILE,ATM_PROFILE]) {
      expect(body.scoring_configs[profile]).toEqual({use_gt_images:profile===OPEN_PROFILE,gt_metadata_mode:'image_metadata_v1',prompts:{}});
    }
  });
  it('图片开关同时控制对应拍摄资料，界面只保留一个选项', () => {
    function Settings() {const [value,setValue]=useState(defaultScoring()); return <ScoringSettings value={value} onChange={setValue}/>;}
    render(<Settings/>);
    expect(screen.queryByRole('checkbox',{name:/GT元数据/})).toBeNull();
    fireEvent.click(screen.getByLabelText(PROFILE_LABELS[OPEN_PROFILE]+' · GT图片辅助'));
    fireEvent.click(screen.getByRole('checkbox',{name:PROFILE_LABELS[ATM_PROFILE]}));
    expect(screen.getAllByText('勾选后附加图片及对应的拍摄时间、可读地点。')).toHaveLength(2);
    expect(screen.getByText(/地点按冻结的媒体标注映射读取/)).toBeTruthy();
  });
  it('结果配置区分整套图片资料与历史文字协议', () => {
    render(<ScoringResults summary={{scoring:{profiles:{
      [OPEN_PROFILE]:{status:'completed',gt_config:{use_gt_images:true,use_gt_metadata:true,gt_metadata_mode:'image_metadata_v1'}},
      [ATM_PROFILE]:{status:'completed',gt_config:{use_gt_images:false,use_gt_metadata:true,gt_metadata_mode:'readable_v1'}},
    }}}}/>);
    expect(screen.getByText('图片＋对应拍摄时间与可读地点')).toBeTruthy();
    expect(screen.getByText(/拍摄时间与可读地点（历史文字协议）/)).toBeTruthy();
  });
  it('未执行标准固定显示未运行采集，部分评分只展示均值和覆盖率', () => {
    render(<ScoringResults summary={{scoring:{profiles:{[OPEN_PROFILE]:{status:'incomplete',scored:1,total:2,coverage:.5,scored_mean:1,normalized_mean:.5,total_score:null,diagnostic_pending:1}}}}} />);
    expect(screen.getByText('未运行采集')).toBeTruthy();
    expect(screen.getByText('未完成')).toBeTruthy();
    expect(screen.getByText('已评分回答质量均分')).toBeTruthy();
    expect(screen.getByText('1.00 / 2.00 分')).toBeTruthy();
    expect(screen.queryByText('50.0%')).toBeNull();
    expect(screen.getByText('覆盖率 1/2 · 50.0%')).toBeTruthy();
    expect(screen.getByText(/公共诊断待完成 1 项/)).toBeTruthy();
  });
  it('完整合法0分显示0%，不显示未运行采集', () => {
    render(<ScoringResults summary={{scoring:{profiles:Object.fromEntries([OPEN_PROFILE,ATM_PROFILE].map(p=>[p,{status:'completed',scored:2,total:2,coverage:1,scored_mean:0,normalized_mean:0,total_score:0}]))}}} />);
    expect(screen.queryByText('未运行采集')).toBeNull();
    expect(screen.getByText('0.00 / 2.00 分')).toBeTruthy();
    expect(screen.getByText('0.0%')).toBeTruthy();
    expect(screen.getByText('回答质量均分')).toBeTruthy();
    expect(screen.getByText('QS总分')).toBeTruthy();
  });
  it('开放式以2分制显示原始均分，QS独立显示百分比和待评分状态', () => {
    render(<ScoringResults summary={{scoring:{profiles:{
      [OPEN_PROFILE]:{status:'incomplete',scored:163,total:487,coverage:163/487,scored_mean:1.288,normalized_mean:.644,total_score:null},
      [ATM_PROFILE]:{status:'incomplete',scored:0,total:487,coverage:0,scored_mean:null,normalized_mean:null,total_score:null},
    }}}}/>);
    const open=screen.getByRole('article',{name:'开放式回答（0/1/2 LLM as Judge评分）'});
    expect(within(open).getByText('1.29 / 2.00 分')).toBeTruthy();
    expect(within(open).queryByText('64.4%')).toBeNull();
    const atm=screen.getByRole('article',{name:PROFILE_LABELS[ATM_PROFILE]});
    expect(within(atm).getByText('—')).toBeTruthy();
    expect(within(atm).getByText('已评分QS均值')).toBeTruthy();
  });
  it('部分QS得分仍展示已评分均值百分比', () => {
    render(<ScoringResults summary={{scoring:{profiles:{[ATM_PROFILE]:{status:'incomplete',scored:2,total:3,coverage:2/3,scored_mean:.75,normalized_mean:.75,total_score:null}}}}}/>);
    expect(screen.getByText('75.0%')).toBeTruthy();
    expect(screen.getByText('已评分QS均值')).toBeTruthy();
  });
  it('两套结果使用独立卡片，配置默认收起并保留完整快照标识', () => {
    const hash='df903991fe7f2c2581d3d2d9e4841b56770996396737af032b39acfc42f06876';
    const {container}=render(<ScoringResults summary={{scoring:{profiles:{[OPEN_PROFILE]:{status:'completed',scored:487,total:487,coverage:1,scored_mean:1,normalized_mean:.5,total_score:.5,qa_definition_sha256:hash,batch_id:'score-c40f42e6280967d9c50c79e7'}}}}}/>);
    expect(screen.getAllByRole('article')).toHaveLength(2);
    expect(container.querySelector('.key-metric-list')).toBeNull();
    expect(container.querySelector('details')?.open).toBe(false);
    expect(screen.getByText(hash).tagName).toBe('CODE');
    expect((screen.getByLabelText(PROFILE_LABELS[OPEN_PROFILE]+'评分覆盖率') as HTMLProgressElement).value).toBe(1);
  });
  it('提示词按标准切换用途，列表只读，恢复仅触发当前项', () => {
    const change=vi.fn(), reset=vi.fn();
    const kinds=[OPEN_PROFILE+':answer_quality', ATM_PROFILE+':choice',ATM_PROFILE+':number',ATM_PROFILE+':open','task_decision','evidence'].map(kind=>({kind}));
    render(<ScoringPromptEditor judgePromptKinds={kinds} activePromptKind={ATM_PROFILE+':number'} onSwitchPromptKind={change} rejudgePrompt='numeric' onRejudgePromptChange={()=>{}} onResetPrompt={reset}/>);
    expect(within(screen.getByRole('combobox',{name:'提示词用途'})).getAllByRole('option')).toHaveLength(3);
    expect(screen.getByText(/列表题固定使用 Jaccard/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button',{name:'恢复默认'}));expect(reset).toHaveBeenCalledOnce();
    fireEvent.change(screen.getByRole('combobox',{name:'提示词所属标准'}),{target:{value:'shared'}});expect(change).toHaveBeenCalledWith('task_decision');
  });
  it('双评分卡片突出分数，批次和理由默认折叠，公共诊断独立显示', () => {
    const {container}=render(<ItemScoringResults results={[
      {key:'open',profile_id:OPEN_PROFILE,batch_id:'open-batch',turn_index:0,status:'completed',score:2,max_score:2,is_evaluation_turn:true,reason:'开放式理由'},
      {key:'qs',profile_id:ATM_PROFILE,batch_id:'qs-batch',turn_index:0,status:'completed',score:0,max_score:1,is_evaluation_turn:true,reason:'QS理由'},
    ]}/>);
    const cards=screen.getAllByRole('article');
    expect(cards).toHaveLength(2);
    expect(within(cards[0]).getByText('开放式回答')).toBeTruthy();
    expect(within(cards[1]).getByText('ATM-Bench式QS')).toBeTruthy();
    expect(within(cards[1]).getByText('0',{exact:false})).toBeTruthy();
    expect(screen.getAllByText('已评分')).toHaveLength(2);
    expect(container.querySelectorAll('.item-score-record')).toHaveLength(2);
    expect(Array.from(container.querySelectorAll('.item-score-record')).every(d=>!(d as HTMLDetailsElement).open)).toBe(true);
    expect(container.querySelector('.item-scoring-diagnostics')).toBeTruthy();
  });
  it('逐题记录显示所属标准、轮次和真实请求，不丢弃合法0分', () => {
    const {container}=render(<ItemScoringResults results={[{key:'k',profile_id:ATM_PROFILE,batch_id:'batch-test',turn_index:0,status:'completed',score:0,max_score:1,requests:[{payload:{messages:[{role:'user',content:'exact saved input'}]},raw_response:{score:0}}]}]}/>);
    const request=Array.from(container.querySelectorAll('details')).find(d=>d.querySelector('summary')?.textContent==='真实Judge输入与响应（逐次请求）');
    expect(request).toBeTruthy();
    request!.open=true;fireEvent(request!,new Event('toggle'));
    expect(screen.getByText(/exact saved input/)).toBeTruthy();
    expect(screen.getByText(/batch-test/)).toBeTruthy();
  });
});
