import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import type { UseRunsApi } from "../../hooks/useRuns.ts";
import type { RecoveryPlan } from "../../hooks/useRunDetail.ts";
import { RunsView } from "./RunsView.tsx";

vi.mock("./RunDetail.tsx", () => ({ RunDetail: ({ recovery }: { recovery?: ReactNode }) => <>{recovery}</> }));
vi.mock("./RunsTable.tsx", () => ({ RunsTable: () => null }));

const plan: RecoveryPlan = {
  run_id: "old1171", can_resume: true, inherited_qa_count: 697, remaining_qa_count: 474,
  product_version_changed: false, judge_use_gt_images: false,
  // Review scores have a separate scope and do not enter missing-score recovery.
  scoring: { needs_review: { qa_count: 3, turn_count: 3, subscore_count: 3 }, missing: { qa_count: 0, turn_count: 0, subscore_count: 0 }, failed: { qa_count: 0, turn_count: 0, subscore_count: 0 }, unscored: { qa_count: 0, turn_count: 0, subscore_count: 0 }, all: { qa_count: 0, turn_count: 0, subscore_count: 0 } },
};
const resumeRun = vi.fn();
function view(runId: string, status: string, recoveryPlan: RecoveryPlan | null, selectedId = runId) {
  const runs = { activeRun: { run_id: runId, status }, activeRunId: selectedId, recoveryPlan,
    resumeRun, hasRunning: false, resuming: false } as unknown as UseRunsApi;
  return <RunsView runs={runs} judgeModel="" saveJudgePrompt={() => {}} />;
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); resumeRun.mockReset(); });

describe("续跑卡片归属", () => {
  it("从失败1171切到完成12QA，旧恢复信息立即消失", () => {
    const { rerender } = render(view("old1171", "failed", plan));
    expect((screen.getByRole("button", {name: "继续剩余 QA 答题"}) as HTMLButtonElement).disabled).toBe(false);
    rerender(view("smoke12", "completed", plan));
    expect((screen.getByRole("button", {name: "继续剩余 QA 答题"}) as HTMLButtonElement).disabled).toBe(true);
    rerender(view("smoke12", "completed", { ...plan, run_id: "smoke12" }));
    expect((screen.getByRole("button", {name: "继续剩余 QA 答题"}) as HTMLButtonElement).disabled).toBe(true);
    expect(resumeRun).not.toHaveBeenCalled();
  });
  it("选择已切换但详情尚未返回时，不提供旧任务按钮", () => {
    render(view("old1171", "failed", plan, "smoke12"));
    expect((screen.getByRole("button", {name: "继续剩余 QA 答题"}) as HTMLButtonElement).disabled).toBe(true);
  });
  it("无剩余题时禁用；有效卡片只提交本卡任务", () => {
    const { rerender } = render(view("old1171", "failed", { ...plan, remaining_qa_count: 0 }));
    expect((screen.getByRole("button", {name: "继续剩余 QA 答题"}) as HTMLButtonElement).disabled).toBe(true);
    rerender(view("old1171", "failed", plan));
    vi.spyOn(window, "confirm").mockReturnValue(true);
    fireEvent.click(screen.getByRole("button", { name: "继续剩余 QA 答题" }));
    expect(resumeRun).toHaveBeenCalledExactlyOnceWith("old1171", { repair_missing_judges: true, judge_use_gt_images: false });
  });
});

it("运行中即便收到旧的可续跑计划也不能启动第二份答题", () => {
  render(view("old1171", "running", plan));
  expect((screen.getByRole("button", {name: "继续剩余 QA 答题"}) as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByText(/任务正在运行/)).toBeTruthy();
  expect(resumeRun).not.toHaveBeenCalled();
});
