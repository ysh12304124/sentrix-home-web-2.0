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
    const button=screen.getByRole("button",{name:"继续剩余 QA 测评"}) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    fireEvent.click(screen.getByLabelText("选择当前产品版本续跑，并记录版本变化"));
    fireEvent.click(screen.getByLabelText("使用 GT 图片辅助评分（仅给 Judge；不附视频）"));
    expect(button.disabled).toBe(false);
    fireEvent.click(button);
    expect(resume).toHaveBeenCalledExactlyOnceWith({repair_missing_judges:true,judge_use_gt_images:true,product_build_revision:"new-rev"});
  });
  it("取消确认不启动任务，已有任务运行时禁用",()=>{
    const resume=vi.fn();
    vi.spyOn(window,"confirm").mockReturnValue(false);
    const {rerender}=render(<ResumeCard plan={{...plan,product_version_changed:false}} busy={false} onResume={resume}/>);
    fireEvent.click(screen.getByRole("button",{name:"继续剩余 QA 测评"}));
    expect(resume).not.toHaveBeenCalled();
    rerender(<ResumeCard plan={plan} busy={true} onResume={resume}/>);
    expect((screen.getByRole("button",{name:"任务处理中…"}) as HTMLButtonElement).disabled).toBe(true);
  });
  it("补评显示准确题/轮/项计数，并可切全量",()=>{
    const scope=vi.fn(), images=vi.fn();
    const {rerender}=render(<RejudgeCard {...base} recoveryPlan={plan} scope="missing" onScopeChange={scope} onJudgeUseGtImagesChange={images}/>);
    expect(screen.getByText("所选范围：197 题 · 229 轮 · 456 项评分")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("评分范围"),{target:{value:"all"}});
    expect(scope).toHaveBeenCalledWith("all");
    fireEvent.click(screen.getByLabelText("使用 GT 图片辅助评分"));
    expect(images).toHaveBeenCalledWith(true);
    rerender(<RejudgeCard {...base} scope="all"/>);
    expect(screen.getByRole("button",{name:"重新评分全部 QA"})).toBeTruthy();
  });
  it("无目标时不允许补评",()=>{
    render(<RejudgeCard {...base} recoveryPlan={{...plan,scoring:{...plan.scoring,missing:{qa_count:0,turn_count:0,subscore_count:0}}}}/>);
    expect((screen.getByRole("button",{name:"补评所选范围"}) as HTMLButtonElement).disabled).toBe(true);
  });
});
