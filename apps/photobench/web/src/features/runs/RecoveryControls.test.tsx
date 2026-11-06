import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { defaultScoring, OPEN_PROFILE } from '../../lib/scoring.ts';
import { ResumeCard } from "./ResumeCard.tsx";
import { RejudgeCard, type RejudgeCardProps } from "./RejudgeCard.tsx";
import type { RecoveryPlan } from "../../hooks/useRunDetail.ts";

const plan: RecoveryPlan = {
  run_id:"old", can_resume:true, inherited_qa_count:697, remaining_qa_count:474,
  product_version_changed:true, source_product_build_revision:"old-rev", current_product_build_revision:"new-rev",
  judge_use_gt_images:false,
  scoring: {
    missing:{qa_count:197,turn_count:229,subscore_count:456},
    failed:{qa_count:20,turn_count:20,subscore_count:20},
    unscored:{qa_count:177,turn_count:209,subscore_count:436},
    all:{qa_count:693,turn_count:800,subscore_count:1600},
    needs_review:{qa_count:3,turn_count:3,subscore_count:3},
  },
};
const base: RejudgeCardProps = {
  activeRejudge:null, judgePromptKinds:[],activePromptKind:"answer_quality",onSwitchPromptKind:vi.fn(),
  rejudgePrompt:"p",onRejudgePromptChange:vi.fn(),onResetPrompt:vi.fn(),canRejudge:true,
  rejudgeSubmitting:false,rejudgeConfirmation:null,onRequestRejudge:vi.fn(()=>true),onConfirmRejudge:vi.fn(),
  onCancelRejudge:vi.fn(),rejudgePercent:0,
};
afterEach(()=>{cleanup();vi.restoreAllMocks();});

describe("恢复与评分范围",()=>{
  it("产品版本变化须明确选择，确认后仅提交续跑参数",()=>{
    const resume=vi.fn();
    vi.spyOn(window,"confirm").mockReturnValue(true);
    render(<ResumeCard plan={plan} busy={false} onResume={resume}/>);
    const button=screen.getByRole("button",{name:"继续剩余 QA 答题"}) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.click(screen.getByLabelText("选择当前产品版本续跑，并记录版本变化"));
    fireEvent.click(screen.getByRole("checkbox", {name:"GT 图片辅助评分"}));
    expect(button.disabled).toBe(false);
    fireEvent.click(button);
    expect(resume).toHaveBeenCalledExactlyOnceWith({repair_missing_judges:true,judge_use_gt_images:true,product_build_revision:"new-rev"});
  });
  it("取消确认不启动任务，已有任务运行时禁用",()=>{
    const resume=vi.fn();
    vi.spyOn(window,"confirm").mockReturnValue(false);
    const {rerender}=render(<ResumeCard plan={{...plan,product_version_changed:false}} busy={false} onResume={resume}/>);
    fireEvent.click(screen.getByRole("button",{name:"继续剩余 QA 答题"}));
    expect(resume).not.toHaveBeenCalled();
    rerender(<ResumeCard plan={plan} busy={true} onResume={resume}/>);
    expect((screen.getByRole("button",{name:"已有任务运行，暂不可续跑"}) as HTMLButtonElement).disabled).toBe(true);
  });
  it("补评显示准确题/轮/项计数，并可切全量",()=>{
    const scope=vi.fn(), images=vi.fn();
    const {rerender}=render(<RejudgeCard {...base} scoringPreview={{...plan.scoring.missing,profiles:{}}} scope="missing" onScopeChange={scope} onScoringChange={images}/>);
    expect(screen.getByLabelText("本次评分数量").textContent).toBe("197 题229 轮对话456 项评分");
    fireEvent.change(screen.getByLabelText("评分范围"),{target:{value:"all"}});
    expect(scope).toHaveBeenCalledWith("all");
    fireEvent.click(screen.getByRole("checkbox", {name:/开放式回答.*GT图片辅助/}));
    expect(images).toHaveBeenCalledWith(expect.objectContaining({configs:expect.objectContaining({[OPEN_PROFILE]:{use_gt_images:true,gt_metadata_mode:'image_metadata_v1'}})}));
    rerender(<RejudgeCard {...base} scope="all"/>);
    expect(screen.getByRole("button",{name:"补齐全部回答的未完成评分"})).toBeTruthy();
  });
  it("无目标时不允许补评",()=>{
    render(<RejudgeCard {...base} scoringPreview={{qa_count:0,turn_count:0,subscore_count:0,profiles:{}}}/>);
    expect((screen.getByRole("button",{name:"补齐缺失评分"}) as HTMLButtonElement).disabled).toBe(true);
  });
  it("待复核可以单独选择并显示数量",()=>{
    const scope=vi.fn();
    render(<RejudgeCard {...base} scoringPreview={{...plan.scoring.needs_review,profiles:{}}} scope="needs_review" onScopeChange={scope}/>);
    expect(screen.getByLabelText("本次评分数量").textContent).toBe("3 题3 轮对话3 项评分");
    expect(screen.getByRole("button",{name:"重新评估待复核回答"})).toBeTruthy();
    fireEvent.change(screen.getByLabelText("评分范围"),{target:{value:"missing"}});
    expect(scope).toHaveBeenCalledWith("missing");
  });
});

describe("评分面板的操作语义", () => {
  it("切换图片选项只变更本次选择，不触发评分", () => {
    const images = vi.fn(), request = vi.fn(() => true);
    const { rerender } = render(<RejudgeCard {...base} onRequestRejudge={request}
      onScoringChange={images} scoring={defaultScoring()} />);
    const toggle = screen.getByRole("checkbox", { name: /开放式回答.*GT图片辅助/ });
    expect((toggle as HTMLInputElement).checked).toBe(false);
    fireEvent.click(toggle);
    expect(images).toHaveBeenCalledExactlyOnceWith({...defaultScoring(),configs:{...defaultScoring().configs,[OPEN_PROFILE]:{use_gt_images:true,gt_metadata_mode:'image_metadata_v1'}}});
    expect(request).not.toHaveBeenCalled();
    rerender(<RejudgeCard {...base} onRequestRejudge={request} scoring={{...defaultScoring(),configs:{...defaultScoring().configs,[OPEN_PROFILE]:{use_gt_images:true,use_gt_metadata:false}}}} />);
    expect((toggle as HTMLInputElement).checked).toBe(true);
    expect(screen.getByText(/读取已配置映射或图片失败会明确记录/)).toBeTruthy();
  });
  it("评分运行时不能切图片或再次提交，提示词默认折叠", () => {
    render(<RejudgeCard {...base} activeRejudge={{status:"running"}} />);
    expect((screen.getByRole("checkbox", { name: /开放式回答.*GT图片辅助/ }) as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "评分进行中…" }) as HTMLButtonElement).disabled).toBe(true);
    const prompt = screen.getByText("评分提示词").closest("details") as HTMLDetailsElement;
    expect(prompt.open).toBe(false);
  });
  it('运行进度区分题数、对话轮数和双标准评分项目数', () => {
    render(<RejudgeCard {...base} activeRejudge={{status:'running',completed:25,total:1104,failed:0,qa_count:487,turn_count:552,subscore_count:1104}} rejudgePercent={2}/>);
    expect(screen.getByLabelText('最近评分任务进度').textContent).toContain('评分项目 25/1104 项');
    expect(screen.getByText(/487题 · 552轮对话 · 1104个评分项目/)).toBeTruthy();
    expect(screen.getByText(/同一轮按每套标准各计1个评分项目/)).toBeTruthy();
  });
});

it("续跑提交尚未结束时阻止重复点击", async () => {
  let finish!: () => void;
  const pending = new Promise<void>(resolve => { finish = resolve; });
  const resume = vi.fn(() => pending);
  vi.spyOn(window, "confirm").mockReturnValue(true);
  render(<ResumeCard plan={{...plan, product_version_changed:false}} busy={false} onResume={resume} />);
  fireEvent.click(screen.getByRole("button", {name:"继续剩余 QA 答题"}));
  fireEvent.click(screen.getByRole("button", {name:"正在提交…"}));
  expect(resume).toHaveBeenCalledTimes(1);
  expect((screen.getByRole("button", {name:"正在提交…"}) as HTMLButtonElement).disabled).toBe(true);
  await act(async () => { finish(); await pending; });
});

it("新协议续跑冻结评分配置，只提交答题恢复", async () => {
  const resume=vi.fn();vi.spyOn(window,"confirm").mockReturnValue(true);
  render(<ResumeCard plan={{...plan,product_version_changed:false,frozen_scoring_profiles:[OPEN_PROFILE]}} busy={false} onResume={resume}/>);
  expect(screen.queryByRole("checkbox",{name:/GT/})).toBeNull();
  expect(screen.getByText(/评分沿用原任务冻结/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button",{name:"继续剩余 QA 答题"}));
  expect(resume).toHaveBeenCalledWith({});
});
