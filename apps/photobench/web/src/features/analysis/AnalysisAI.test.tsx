import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AnalysisAI } from "./AnalysisAI.tsx";
import { post } from "../../api/client.ts";
import type { DiagnosisReport } from "../../api/analysis.ts";
vi.mock("../../api/client.ts", () => ({ BASE: "/api/v1", post: vi.fn() }));
vi.mock("./AIComparison.tsx", () => ({ AIComparison: () => null }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });
function show(version?: number, isStale = false, covered = 1, requested = 1, hasSummary = true) {
  const report: DiagnosisReport = {
    schema_version: version, requested, covered, model: "test", provider_id: "judge",
    note: "存档中的结论", prompt_version: "analysis-diagnosis-7", scope: {kind:"filter",indices:Array.from({length:requested},(_,i)=>i),filters:{},result_fingerprint:"f"},
    overview: hasSummary ? {claims:[{kind:"observation",text:"存档中的结论",evidence_refs:["i0:case"],limitations:""}],missing_evidence:[]} : undefined,
    cases: [],
  };
  const view=render(<AnalysisAI runId="r" jobId="rules" stale={false} filters={{}} total={1}
    ai={{job_id:"saved",status:"completed",processed:1,stale:isStale,report}} setAi={vi.fn()} navigate={vi.fn()}/>);
  const panel=view.container.querySelector("details.analysis-ai-card") as HTMLDetailsElement;
  expect(panel.open).toBe(false);
  fireEvent.click(screen.getByText("AI 辅助分析"));
  expect(panel.open).toBe(true);
}
it.each([undefined, 1, 2])("旧格式%s不展示结论、导出和续分析，重新生成使用新请求", async version => {
  show(version);
  expect(screen.getByText("此分析为旧版格式，请重新生成分析。")).toBeTruthy();
  expect(screen.queryByText("存档中的结论")).toBeNull();
  expect(screen.queryByText(/已处理题目 1 题/)).toBeNull();
  expect(screen.queryByText("逐题诊断（0）")).toBeNull();
  expect(screen.queryByRole("button",{name:/继续分析未完成/})).toBeNull();
  expect(screen.queryByRole("link", {name:"导出 HTML"})).toBeNull();
  expect(post).not.toHaveBeenCalled();
  vi.mocked(post).mockResolvedValue({job_id:"new",status:"queued",processed:0});
  fireEvent.click(screen.getByText("分析所选题目并汇总"));
  await waitFor(() => expect(post).toHaveBeenCalledWith("/runs/r/analysis", {
    kind:"ai",parent_job:"rules",model_role:"judge",mode:"overview",scope:"filter",filters:{},
  }));
});
it("当前格式展示诊断和导出", () => {
  show(3);
  expect(screen.queryByText("此分析为旧版格式，请重新生成分析。")).toBeNull();
  expect(screen.getByRole("heading",{name:"分析总结"})).toBeTruthy();
  expect(screen.getByRole("link",{name:"导出 HTML"})).toBeTruthy();
});

it("分析范围直接提交，无二次取样控件；全部题目与仅逐题模式保持独立", async () => {
  show(3);
  expect(screen.queryByRole("combobox", {name:"每批样本上限"})).toBeNull();
  vi.mocked(post).mockResolvedValue({job_id:"new",status:"queued",processed:0});
  fireEvent.change(screen.getByRole("combobox", {name:"分析范围"}), {target:{value:"run"}});
  fireEvent.click(screen.getByRole("button", {name:"仅逐题分析"}));
  await waitFor(() => expect(post).toHaveBeenCalledWith("/runs/r/analysis", {
    kind:"ai",parent_job:"rules",model_role:"judge",mode:"cases",scope:"run",filters:{},
  }));
});

it("GT说明明确无图可正常文字分析，不引入旧版专用说明", () => {
  show(3);
  expect(screen.getByText("GT 图片与分析说明")).toBeTruthy();
  expect(screen.getByText(/有图题需使用支持图片输入的分析模型/)).toBeTruthy();
  expect(screen.getByText(/未标注图片时按文字与轨迹分析/)).toBeTruthy();
  expect(screen.queryByText(/原文字分析结果/)).toBeNull();
  expect(post).not.toHaveBeenCalled();
});
it("过期带图结果要求重新分析，隐藏继续操作", () => {
  show(3, true);
  expect(screen.getByText("此分析的来源或提示词已更新，请重新生成分析。")).toBeTruthy();
  expect(screen.queryByRole("button",{name:/继续分析未完成/})).toBeNull();
});

it("全部逐题失败时不展示汇总结论，直接提示失败", () => {
  show(3, true, 0);
  expect(screen.queryByRole("heading",{name:"分析总结"})).toBeNull();
  expect(screen.getByText(/本次没有有效逐题诊断，无法形成分析结论/)).toBeTruthy();
  expect(screen.getByText(/分析失败 · 已处理题目 1 题/)).toBeTruthy();
  expect(post).not.toHaveBeenCalled();
});

it("全部题目分析完成后隐藏继续按钮，仅保留重新生成总结", () => {
  show(3, false, 2, 2);
  expect(screen.queryByRole("button", {name:/继续分析未完成/})).toBeNull();
  expect(screen.getByRole("button", {name:"重新生成总结"})).toBeTruthy();
  expect(screen.getByText(/本次范围的逐题分析已全部完成/)).toBeTruthy();
  expect(post).not.toHaveBeenCalled();
});

it("部分完成时显示未完成数量，继续沿用原范围", async () => {
  show(3, false, 1, 2);
  vi.mocked(post).mockResolvedValue({job_id:"new",status:"queued",processed:0});
  fireEvent.click(screen.getByRole("button", {name:"继续分析未完成的 1 题并汇总"}));
  await waitFor(()=>expect(post).toHaveBeenCalledWith("/runs/r/analysis", {
    kind:"ai",parent_job:"rules",model_role:"judge",mode:"overview",scope:"selection",filters:{},
    indices:[0,1],resume_job:"saved",summary_only:false,
  }));
});

it("没有有效诊断时只显示继续分析，隐藏生成总结", () => {
  show(3, false, 0, 2, false);
  expect(screen.getByRole("button", {name:"继续分析未完成的 2 题并汇总"})).toBeTruthy();
  expect(screen.queryByRole("button", {name:/^(重新)?生成总结$/})).toBeNull();
});

it("已有逐题结果但未生成总结时显示生成总结，调用仅汇总模式", async () => {
  show(3, false, 2, 2, false);
  vi.mocked(post).mockResolvedValue({job_id:"new",status:"queued",processed:0});
  fireEvent.click(screen.getByRole("button", {name:"生成总结"}));
  await waitFor(()=>expect(post).toHaveBeenCalledWith("/runs/r/analysis", {
    kind:"ai",parent_job:"rules",model_role:"judge",mode:"overview",scope:"selection",filters:{},
    indices:[0,1],resume_job:"saved",summary_only:true,
  }));
});
