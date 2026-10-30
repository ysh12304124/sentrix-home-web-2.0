import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { RunItemDetail } from "../../api/types.ts";

import { QaItemCard } from "./QaItemCard.tsx";

afterEach(cleanup);

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

    expect(screen.getByText("工具召回关键帧图片（1）")).not.toBeNull();
    expect(screen.getByText("模型使用图片（0）")).not.toBeNull();
    expect(screen.getByText("回答依据图片为空（模型未选择交付图）")).not.toBeNull();

    // 候选图只出现在召回区一次——若交付回退到候选，这里会变成 2。
    expect(screen.getAllByAltText("cand.jpg")).toHaveLength(1);
  });

  it("只有交付图、没有候选：使用区 1 张，召回区为空", () => {
    renderCard({
      index: 0,
      question: "车停在哪",
      predicted_media: [{ media_type: "image", media_id: "used.jpg", asset_id: "used-1", file_name: "used.jpg" }],
    });

    expect(screen.getByText("工具召回关键帧图片（0）")).not.toBeNull();
    expect(screen.getByText("检索未返回可识别媒体")).not.toBeNull();
    expect(screen.getByText("模型使用图片（1）")).not.toBeNull();
    expect(screen.getAllByAltText("used.jpg")).toHaveLength(1);
  });

  it("候选与交付同时存在：两区各自渲染各自的图", () => {
    renderCard({
      index: 0,
      question: "车停在哪",
      retrieved_candidate_media: [{ media_type: "image", media_id: "cand.jpg", asset_id: "cand-1", file_name: "cand.jpg" }],
      predicted_media: [{ media_type: "image", media_id: "used.jpg", asset_id: "used-1", file_name: "used.jpg" }],
    });

    expect(screen.getByText("工具召回关键帧图片（1）")).not.toBeNull();
    expect(screen.getByText("模型使用图片（1）")).not.toBeNull();
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

describe("QaItemCard · 检索证据显示实际关键帧与 ASR 文本", () => {
  it("视频检索结果带关键帧资产时显示真实帧图片", () => {
    renderCard({
      index: 0,
      question: "画面里谁在发言",
      retrieved_candidate_media: [{ media_type: "video", media_id: "meeting_room_01", file_name: "meeting_room_01.mp4", asset_id: "video-1", source_keyframe_asset_id: "keyframe-1", source_timestamp_sec: 1511.5 }],
    });
    expect(screen.getByText("工具召回关键帧图片（1）")).not.toBeNull();
    expect(screen.getByAltText("检索关键帧 1511.5s").getAttribute("src")).toContain("/assets/keyframe-1/file");
  });

  it("只显示检索工具实际返回的 ASR 文本与时间", () => {
    renderCard({
      index: 0,
      question: "会议里说了什么",
      tool_trace: [{ tool: "get_photo", result: { photos: [{ speech_evidence: [{ start_sec: 62, end_sec: 66, text: "我们下周再讨论" }] }] } }],
    });
    expect(screen.getByText("工具召回语音转写（1）")).not.toBeNull();
    expect(screen.getByText("我们下周再讨论")).not.toBeNull();
    expect(screen.getByText("1:02–1:06")).not.toBeNull();
  });

  it("没有工具返回的 ASR 结果时不从 QA 或答案文本伪造转写", () => {
    renderCard({ index: 0, question: "语音里提到什么", answer: "参考答案里不应该出现", reference_answer: "参考答案里不应该出现" });
    expect(screen.getByText("检索工具没有返回 ASR 转写文本")).not.toBeNull();
    expect(screen.queryByText("工具召回语音转写（1）")).toBeNull();
  });
});
