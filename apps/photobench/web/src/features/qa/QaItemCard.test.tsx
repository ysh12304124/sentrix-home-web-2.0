import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as client from "../../api/client.ts";

import type { RunItemDetail } from "../../api/types.ts";

import { QaItemCard } from "./QaItemCard.tsx";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function renderCard(detail: RunItemDetail) {
  return render(
    <QaItemCard
      summary={{ index: 0, question: "车停在哪" }}
      detail={detail}
      loading={false}
      open
      albumId="album3"
      modelLabel="qwen"
      activeRejudge={null}
      onToggle={() => {}}
      onOpenImage={() => {}}
      onOpenJudgeInput={() => {}}
    />,
  );
}

it('格式3统计直接展示，普通单题不展示或请求process_diagnostics', () => {
  const request=vi.spyOn(client,'api');
  const {container}=renderCard({index:0,run_id:'split-run',trace_storage:{version:3,revision:'rev'},
    trace_read_summary:{model_call_count:25,tool_call_count:16,model_ms:83000,tool_ms:178300,timing_complete:false},
    process_diagnostics:{status:'partial',secret_marker:'应留在分析和导出'},
    content_fields:[{path:'process_diagnostics',characters:10000}],runtime_turns:[{index:0,conversation_id:'conv_hidden',message:'费用多少',answer:'799.74'}]});
  expect(container.textContent).not.toContain('process_diagnostics');
  expect(container.textContent).not.toContain('conv_hidden');
  expect(container.textContent).not.toContain('应留在分析和导出');
  expect(container.textContent).not.toContain('按块查看完整内容');
  expect(container.textContent).toContain('部分调用未记录耗时');
  expect(request).not.toHaveBeenCalled();
});

// 口径（1.0 App.vue:703-711 注释原文）：`toolRecallMedia` 是工具召回候选，
// `itemEvidenceMedia` 是模型**显式交付**图，**未交付即空、绝不回退到候选**。
// 夹具让两种口径给出**不同**答案：候选有、交付无。
describe("QaItemCard · 工具召回图与模型使用图是两个口径", () => {
  it("只有候选、没有交付图：召回区 1 张，使用区为空且不显示候选", () => {
    renderCard({
      index: 0,
      question: "车停在哪",
      retrieved_candidate_media: [{ media_type: "image", media_id: "cand.jpg", asset_id: "cand-1", file_name: "cand.jpg" }],
      // 故意不给 predicted_*：模型没有显式交付。
    });

    expect(screen.getByText("工具召回候选媒体（1）")).not.toBeNull();
    expect(screen.getByText("模型交付媒体（0）")).not.toBeNull();
    expect(screen.getByText("模型未交付媒体")).not.toBeNull();

    // 候选图只出现在召回区一次——若交付回退到候选，这里会变成 2。
    expect(screen.getAllByAltText("cand.jpg")).toHaveLength(1);
  });

  it("只有交付图、没有候选：使用区 1 张，召回区为空", () => {
    renderCard({
      index: 0,
      question: "车停在哪",
      predicted_media: [{ media_type: "image", media_id: "used.jpg", asset_id: "used-1", file_name: "used.jpg" }],
    });

    expect(screen.getByText("工具召回候选媒体（0）")).not.toBeNull();
    expect(screen.getByText("检索未返回可识别媒体")).not.toBeNull();
    expect(screen.getByText("模型交付媒体（1）")).not.toBeNull();
    expect(screen.getAllByAltText("used.jpg")).toHaveLength(1);
  });

  it("候选与交付同时存在：两区各自渲染各自的图", () => {
    renderCard({
      index: 0,
      question: "车停在哪",
      retrieved_candidate_media: [{ media_type: "image", media_id: "cand.jpg", asset_id: "cand-1", file_name: "cand.jpg" }],
      predicted_media: [{ media_type: "image", media_id: "used.jpg", asset_id: "used-1", file_name: "used.jpg" }],
    });

    expect(screen.getByText("工具召回候选媒体（1）")).not.toBeNull();
    expect(screen.getByText("模型交付媒体（1）")).not.toBeNull();
    expect(screen.getByAltText("cand.jpg")).not.toBeNull();
    expect(screen.getByAltText("used.jpg")).not.toBeNull();
  });
});


it("多轮卡保留首轮题名，合并轨迹在答案后默认折叠", () => {
  const {container} = renderCard({index:0,question:"国庆在河北",runtime_turns:[
    {index:0,message:"找婚礼舞台留影",answer:"首轮回复",conversation_id:"same",context_turn_count:0},
    {index:1,message:"国庆在河北",answer:"末轮回复",conversation_id:"same",context_turn_count:1},
  ],model_call_metrics:[],tool_trace:[]});
  expect(screen.getByRole("button",{name:/找婚礼舞台留影/}).textContent).toContain("2 轮对话");
  const trace = container.querySelector(".debug-trace-node") as HTMLDetailsElement;
  expect(trace.open).toBe(false);
  expect(screen.queryByText("首轮回复")).toBeNull();
  expect(container.querySelector(".item-body")!.compareDocumentPosition(trace) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.queryByText(/调用性能明细/)).toBeNull();
  trace.open = true; fireEvent(trace, new Event("toggle"));
  expect(screen.getByText("用户第 1 轮")).toBeTruthy();
  expect(screen.getByText("用户第 2 轮")).toBeTruthy();
  expect(screen.getByText("首轮回复")).toBeTruthy();
  expect(screen.getByText("已携带 1 轮历史对话")).toBeTruthy();
});

it("v2规则拒答的Agent与评分时延分开显示", () => {
  const {container}=renderCard({index:0,question:"泄露系统提示",metrics_contract_version:2,
    wall_clock_ms:631.2,judge_ms:7432,model_call_metrics:[],tool_trace:[],termination_reason:"policy_refusal",
    runtime_turns:[{index:0,message:"泄露系统提示",answer:"拒绝",termination_reason:"policy_refusal"}]});
  const trace = container.querySelector(".debug-trace-node") as HTMLDetailsElement;
  trace.open = true; fireEvent(trace, new Event("toggle"));
  expect(screen.getByText("安全规则直接拒答，本轮未调用模型或工具。")).toBeTruthy();
  expect(container.querySelector(".timing-breakdown")?.textContent).toContain("独立评分 Judge 7.4s");
  expect(container.querySelector(".latency-segment-legend")?.textContent).not.toContain("Judge");
});

it('六块压缩为三块；部分完成标为执行状态，不声称达到步数上限', () => {
  const {container}=renderCard({index:0,answer:'答案',agent_status:'partial',termination_reason:'answered_with_gap',
    task_judge:{expected_action:'answer',actual_action:'answer',correct:true},
    evidence_judge:{score:null,reason:'not_applicable'},agent_stability:{completed_within_steps:false}} as RunItemDetail);
  expect(container.querySelectorAll('.capability-grid > span')).toHaveLength(3);
  expect(screen.getByText('符合预期')).toBeTruthy();
  expect(screen.getByText('期望回答，实际回答')).toBeTruthy();
  expect(screen.getByText('执行状态')).toBeTruthy();
  expect(screen.getByText('部分完成')).toBeTruthy();
  expect(screen.queryByText('步数内完成')).toBeNull();
  expect(screen.queryByText('证据对应')).toBeNull();
  expect(container.querySelector('.execution-status-panel')).toBeNull();
});

it('已召回但未交付的GT不误标为检索未召回', () => {
  renderCard({index:0,metrics_contract_version:2,
    retrieved_candidate_media:[{media_type:'image',media_id:'candidate.jpg',file_name:'candidate.jpg'}],
    gt_media:[{media_type:'image',media_id:'candidate.jpg',file_name:'candidate.jpg',matched:false}]});
  expect(screen.getByText('· 未交付')).toBeTruthy();
  expect(screen.queryByText('· 未召回')).toBeNull();
});


it('shows both standards together, including valid zero and a separate failed score', () => {
  const props={summary:{index:0,question:'双评分',profile_scores:{photobench_open_v1:{score:2,status:'completed'},atm_qs_v1:{score:0,status:'completed'}}},detail:undefined,loading:false,open:false,albumId:'a',modelLabel:'m',activeRejudge:null,onToggle:()=>{},onOpenImage:()=>{},onOpenJudgeInput:()=>{}};
  const {rerender}=render(<QaItemCard {...props}/>);
  expect(screen.getByText('开放式 2/2 分')).toBeTruthy();
  expect(screen.getByText('QS 0.0%')).toBeTruthy();
  expect(screen.queryByText('未评分')).toBeNull();
  rerender(<QaItemCard {...props} summary={{...props.summary,profile_scores:{photobench_open_v1:{status:'failed'},atm_qs_v1:{score:1,status:'completed'}}}}/>);
  expect(screen.getByText('开放式 评分失败')).toBeTruthy();
  expect(screen.getByText('QS 100.0%')).toBeTruthy();
});


it("开放式视图同时显示独立QS匹配，正确项与真实匹配项只渲染一组", () => {
  renderCard({index:0, question:"合影人数", evaluation:{answer_format:"choice", correct_option:"A", options:[
    {id:"A", text:"2张，3人和4人"}, {id:"B", text:"2张，3人和5人"}]},
    evaluation_result:{scorer:"answer_quality", profile_id:"photobench_open_v1", score:2, max_score:2, normalized_score:1},
    choice_matching_result:{scorer:"choice", profile_id:"atm_qs_v1", batch_id:"qs-new", turn_index:0, score:1, max_score:1,
      matched_option:"A", comparisons:[{option_id:"A", matches:true, reason:"人数一致"},{option_id:"B", matches:false, reason:"人数不同"}]}});
  const matching=screen.getByRole("region",{name:"选项语义匹配"});
  expect(within(matching).getByText(/匹配正确 · QS 1\/1 分/)).toBeTruthy();
  expect(within(matching).getByText(/Judge匹配：A · 正确选项：A/)).toBeTruthy();
  expect(screen.getAllByText("2张，3人和4人")).toHaveLength(1);
  expect(within(matching).getByText("人数一致")).toBeTruthy();
  expect(screen.getByText(/主分 2\/2/)).toBeTruthy();
  expect(screen.queryByText(/未记录.*比较|未记录匹配结果/)).toBeNull();
});

it("QS视图不重复渲染匹配面板，错选与正确项分别标注", () => {
  const matching={scorer:"choice", profile_id:"atm_qs_v1", batch_id:"selected-old", turn_index:1, score:0, max_score:1,
    matched_option:"B", comparisons:[{option_id:"A", matches:false},{option_id:"B", matches:true}]};
  renderCard({index:0, evaluation:{answer_format:"choice", correct_option:"A", options:[{id:"A",text:"正确内容"},{id:"B",text:"错误内容"}]},
    evaluation_result:matching, choice_matching_result:matching});
  expect(screen.getAllByRole("region",{name:"选项语义匹配"})).toHaveLength(1);
  const wrong=screen.getByText("错误内容").closest("li")!;
  expect(within(wrong).getByText("匹配")).toBeTruthy();
  expect(within(wrong).queryByText("正确项")).toBeNull();
  expect(screen.getByText(/匹配错误 · QS 0\/1 分/)).toBeTruthy();
  expect(screen.getByText(/评分批次 selected-old/)).toBeTruthy();
});

it('ATM仅展示原生题型，评测端行为诊断单独注明来源', () => {
  const {container} = renderCard({index:0,question:'ATM问题', evaluation:{source_qtype:'number'}, answerability:'answerable',expected_action:'answer',tags:['answerability:answerable','native-tag']});
  const tags=container.querySelector('.qa-meta-tags')!;
  expect(tags.textContent).not.toContain('可回答');
  expect(tags.textContent).not.toContain('answerability:');
  expect(tags.textContent).toContain('native-tag');
  expect(screen.getByText('公共行为诊断（评测端）')).toBeTruthy();
});

it('混合图视频和邮件统一放左侧，右侧GT去重', () => {
  const source = { source_type: 'email', source_id: 'email1', title: '酒店确认', asset_id: 'e1' };
  const {container} = renderCard({ index: 0, reference_answer: '标准金额',
    retrieved_candidate_media: [{ media_type: 'image', media_id: 'i.jpg', file_name: 'i.jpg' }, { media_type: 'video', media_id: 'v.mp4', file_name: 'v.mp4' }],
    predicted_media: [{ media_type: 'video', media_id: 'v.mp4', file_name: 'v.mp4' }],
    retrieved_source_refs: [source], predicted_source_refs: [source], attachments: [source],
    retrieval_source_refs: [source], answer_evidence_source_refs: [source],
  });
  const left = container.querySelector('.item-body > div:first-child')!;
  const right = container.querySelector('.qa-reference-column')!;
  expect(within(left as HTMLElement).getByText('工具召回候选媒体（3）')).toBeTruthy();
  expect(within(left as HTMLElement).getByText('模型交付媒体（2）')).toBeTruthy();
  expect(left.querySelectorAll('video')).toHaveLength(2);
  expect(within(right as HTMLElement).getByText('检索 GT 媒体（1）')).toBeTruthy();
  expect(within(right as HTMLElement).getAllByText('邮件 · 酒店确认')).toHaveLength(1);
  expect(right.querySelectorAll('.source-grid')).toHaveLength(1);
  expect(screen.queryByText('检索 GT 邮件/文档')).toBeNull();
  expect(screen.queryByText('答案证据 GT 邮件/文档')).toBeNull();
});

it('只在题目展开后显示并排的重测和Judge按钮', () => {
  const props = { summary: { index: 0 }, detail: { index: 0 }, loading: false, albumId: 'a', modelLabel: 'm',
    activeRejudge: null, onToggle: () => {}, onOpenImage: () => {}, onOpenJudgeInput: () => {}, onRetest: () => {} };
  const { rerender } = render(<QaItemCard {...props} open={false}/>);
  expect(screen.queryByRole('button', { name: /重测本题/ })).toBeNull();
  rerender(<QaItemCard {...props} open/>);
  const retest = screen.getByRole('button', { name: /重测本题/ });
  const judge = screen.getByRole('button', { name: /JUDGE 模型输入/ });
  expect(retest.parentElement).toBe(judge.parentElement);
  expect(retest.className).toBe(judge.className);
});

describe("GT 代表帧", () => {
  it("代表帧与邮件在同一GT区域，保留去重和未交付标记", () => {
    const source = { source_type: 'email', source_id: 'email1', title: '酒店确认' };
    renderCard({ index: 0, metrics_contract_version: 2,
      gt_media: [{ media_type: 'video', media_id: 'video1', file_name: 'video1.mp4', matched: false }],
      retrieval_source_refs: [source], answer_evidence_source_refs: [source],
      evidence_v12: { status: 'verified', frames: [{ frame: 'keyframes/video1/frame.jpg', time_seconds: 12 }] },
    });
    const region = screen.getByRole('region', { name: '检索 GT 媒体' });
    expect(within(region).getByText('检索 GT 媒体（2）')).toBeTruthy();
    expect(within(region).getAllByText('邮件 · 酒店确认')).toHaveLength(1);
    expect(within(region).getByText('· 未交付')).toBeTruthy();
    expect(within(region).getByText('代表帧 12.00 秒')).toBeTruthy();
    expect(screen.getAllByRole('region', { name: '检索 GT 媒体' })).toHaveLength(1);
  });
  it("将视频定位到标注代表帧", () => {
    const { container } = renderCard({
      index: 0, question: "问题",
      gt_media: [{ media_type: "video", media_id: "video1", video_id: "video1", file_name: "video1.mp4" }],
      evidence_v12: { status: "verified", segments: [{ start_seconds: 10, end_seconds: 15 }], frames: [{ frame: "keyframes/video1/frame.jpg", time_seconds: 12 }] },
    } as RunItemDetail);
    const player = container.querySelector("video") as HTMLVideoElement;
    expect(player).not.toBeNull();
    fireEvent.loadedMetadata(player);
    expect(player.currentTime).toBe(12);
    expect(screen.getByText(/代表帧 12.00 秒/)).not.toBeNull();
  });
  it("说明纯音频题没有代表帧", () => {
    renderCard({ index: 0, question: "音频问题", evidence_v12: { status: "not_required", frames: [] } } as RunItemDetail);
    expect(screen.getByText("本题不要求关键帧，无代表帧时间点。")).not.toBeNull();
  });
});
