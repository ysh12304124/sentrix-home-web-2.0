import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
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
    const {rerender}=render(<RejudgeCard {...base} recoveryPlan={plan} scope="missing" onScopeChange={scope} onJudgeUseGtImagesChange={images}/>);
    expect(screen.getByLabelText("本次评分数量").textContent).toBe("197 题229 轮对话456 项评分");
    fireEvent.change(screen.getByLabelText("评分范围"),{target:{value:"all"}});
    expect(scope).toHaveBeenCalledWith("all");
    fireEvent.click(screen.getByRole("checkbox", {name:"GT 图片辅助评分"}));
    expect(images).toHaveBeenCalledWith(true);
    rerender(<RejudgeCard {...base} scope="all"/>);
    expect(screen.getByRole("button",{name:"重新评分全部回答"})).toBeTruthy();
  });
  it("无目标时不允许补评",()=>{
    render(<RejudgeCard {...base} recoveryPlan={{...plan,scoring:{...plan.scoring,missing:{qa_count:0,turn_count:0,subscore_count:0}}}}/>);
    expect((screen.getByRole("button",{name:"补齐缺失评分"}) as HTMLButtonElement).disabled).toBe(true);
  });
});

describe("评分面板的操作语义", () => {
  it("切换图片选项只变更本次选择，不触发评分", () => {
    const images = vi.fn(), request = vi.fn(() => true);
    const { rerender } = render(<RejudgeCard {...base} onRequestRejudge={request}
      onJudgeUseGtImagesChange={images} judgeUseGtImages={false} />);
    const toggle = screen.getByRole("checkbox", { name: "GT 图片辅助评分" });
    expect((toggle as HTMLInputElement).checked).toBe(false);
    fireEvent.click(toggle);
    expect(images).toHaveBeenCalledExactlyOnceWith(true);
    expect(request).not.toHaveBeenCalled();
    rerender(<RejudgeCard {...base} onRequestRejudge={request} judgeUseGtImages={true} />);
    expect((toggle as HTMLInputElement).checked).toBe(true);
    expect(screen.getByText("向评分模型提供标注图片；需支持图片输入")).toBeTruthy();
  });
  it("评分运行时不能切图片或再次提交，提示词默认折叠", () => {
    render(<RejudgeCard {...base} activeRejudge={{status:"running"}} />);
    expect((screen.getByRole("checkbox", { name: "GT 图片辅助评分" }) as HTMLInputElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "评分进行中…" }) as HTMLButtonElement).disabled).toBe(true);
    const prompt = screen.getByText("评分提示词").closest("details") as HTMLDetailsElement;
    expect(prompt.open).toBe(false);
  });
});
