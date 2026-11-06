import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";
import { EvaluationResult, EvaluationSummary, EvaluationTypeBadge } from "./EvaluationResult.tsx";
import { scoreChoices } from "../features/analysis/presentation.ts";
afterEach(cleanup);
it("二分制满分与筛选类别一致", () => {
  expect(scoreChoices({evaluation_summary: {count: 1, scales: ["1"], by_type: {choice: {}}}}).map(s => s.value)).toEqual(["2", "0", "None"]);
  render(<EvaluationResult value={{scorer: "choice_judge", score: 1, max_score: 1, normalized_score: 1, matched_option: "B", comparisons: [{option_id: "B", matches: true, reason: "语义一致"}]}} definition={{correct_option: "B", options: [{id: "B", text: "无法确认"}]}} />);
  expect(screen.getByText(/主分 1\/1 · 归一化 100.0%/)).toBeTruthy();
  expect(screen.getByText(/匹配：B · 正确选项：B/)).toBeTruthy();
  expect(screen.getByText("无法确认")).toBeTruthy();
  expect(screen.getByText("语义一致")).toBeTruthy();
});
it("集合保留连续分，未知工具轨迹保留未评分", () => {
  expect(scoreChoices({evaluation_summary: {count: 1, scales: ["1"], by_type: {media_list: {}}}}).find(s => s.value === "1")?.label).toContain("集合未满分");
  render(<EvaluationResult value={{scorer: "media_set", score: 0.25, max_score: 1, normalized_score: 0.25, media_metrics: {delivery: {recall: 0.5, jaccard: 0.25, precision: 0.333, f1: 0.4}, tools: {recall: null, jaccard: null, precision: null, f1: null}}}} />);
  expect(screen.getByText(/主分 0.25\/1 · 归一化 25.0%/)).toBeTruthy();
  expect(within(screen.getByText("工具累计召回").closest("tr")!).getAllByText("未评分")).toHaveLength(4);
});
it("待复核与评分失败可区分，汇总披露覆盖", () => {
  const {rerender} = render(<EvaluationResult value={{scorer: "choice_judge", score: null, judge_status: "needs_review"}} />);
  expect(screen.getByText(/题目待复核/)).toBeTruthy();
  rerender(<EvaluationResult value={{scorer: "choice_judge", score: null, judge_status: "failed"}} />);
  expect(screen.getByText(/评分失败/)).toBeTruthy();
  rerender(<EvaluationSummary value={{count: 4, scored: 3, coverage: 0.75, mean: 0.5, by_type: {choice: {count: 4, scored: 3, mean: 0.5}}}} />);
  expect(screen.getByText(/覆盖 3\/4（75.0%）/)).toBeTruthy();
});

it("ATM 数值、列表和开放题独立标注，列表说明交付集合", () => {
  const {rerender} = render(<EvaluationTypeBadge value={{source_qtype: "list_recall", answer_format: "media_list"}} />);
  expect(screen.getByText("题型 · ATM 列表召回")).toBeTruthy();
  rerender(<EvaluationTypeBadge value={{source_qtype: "number", answer_format: "open"}} />);
  expect(screen.getByText("题型 · ATM 数值")).toBeTruthy();
  rerender(<EvaluationResult value={{scorer: "atm_open", source_qtype: "open_end", answer_format: "open"}} />);
  expect(screen.getByText("ATM 开放问答")).toBeTruthy();
  expect(screen.getByText(/Judge 按回答内容判/)).toBeTruthy();
  rerender(<EvaluationResult value={{scorer: "atm_list", source_qtype: "list_recall", answer_format: "media_list"}} />);
  expect(screen.getByText(/按实际交付的图片 \/ 视频 ID 集合/)).toBeTruthy();
});

it("评分失败时仍显示完整选项和正确项", () => {
  render(<EvaluationResult value={{scorer: "choice_judge", answer_format: "choice", score: null, judge_status: "failed"}} definition={{correct_option: "A", options: [{id: "A", text: "紫纱幔和红床单"}, {id: "B", text: "其他布置"}]}} />);
  const correct = screen.getByText("紫纱幔和红床单").closest("li")!;
  expect(within(correct).getByText("正确项")).toBeTruthy();
  expect(within(correct).getByText("未记录逐项比较")).toBeTruthy();
  expect(screen.getByText("其他布置")).toBeTruthy();
  expect(screen.getByText(/选项仅供评测端使用/)).toBeTruthy();
});

it("选项只显示一组，逐项合并正确项、匹配状态和理由", () => {
  const options = [{id: "A", text: "无法确认日期"}, {id: "B", text: "2017年10月4日"}, {id: "C", text: "2017年10月2日"}, {id: "D", text: "2017年10月3日"}];
  render(<EvaluationResult definition={{options, correct_option: "B"}} value={{scorer: "choice_judge", matched_option: "A", comparisons: options.map(o => ({option_id: o.id, matches: o.id === "A", reason: o.id + "的比较理由"}))}} />);
  const list = screen.getByRole("list");
  expect(within(list).getAllByRole("listitem")).toHaveLength(4);
  for (const o of options) {
    const row = within(list).getByText(o.text).closest("li")!;
    expect(screen.getAllByText(o.text)).toHaveLength(1);
    expect(within(row).getByText(o.id + "的比较理由")).toBeTruthy();
    expect(within(row).getByText(o.id === "A" ? "匹配" : "不匹配")).toBeTruthy();
    expect(within(row).queryByText("正确项") !== null).toBe(o.id === "B");
  }
});

it("未记录的比较不冒充不匹配，已有选项仍完整展示", () => {
  render(<EvaluationResult definition={{options: [{id: "A", text: "日期甲"}, {id: "B", text: "日期乙"}]}} value={{scorer: "choice_judge", comparisons: [{option_id: "A", matches: false, reason: "日期不同"}]}} />);
  const row = screen.getByText("日期乙").closest("li")!;
  expect(within(row).getByText("未记录逐项比较")).toBeTruthy();
  expect(within(row).queryByText("不匹配")).toBeNull();
});

it("历史缺少选项定义时保留已存的匹配理由", () => {
  render(<EvaluationResult value={{scorer: "choice_judge", comparisons: [{option_id: "D", matches: true, reason: "日期一致"}]}} />);
  expect(screen.getAllByRole("listitem")).toHaveLength(1);
  expect(screen.getByText("D")).toBeTruthy();
  expect(screen.getByText("选项原文未记录")).toBeTruthy();
  expect(screen.getByText("日期一致")).toBeTruthy();
});

it("独立QS批次的choice评分显示其他及逐项比较，沿用定义说明", () => {
  render(<EvaluationResult definition={{answer_format: "choice", correct_option: "B", options: [
    {id: "A", text: "行为甲"}, {id: "B", text: "行为乙"},
  ]}} value={{scorer: "choice", profile_id: "atm_qs_v1", rule_version: "scoring-v1", judge_model: "judge-real",
    score: 0, max_score: 1, normalized_score: 0, judge_status: "completed", matched_option: "other",
    comparisons: [{option_id: "A", matches: false, reason: "未提及甲"},
      {option_id: "B", matches: false, reason: "未提及乙"}], reason: "给定选项均未匹配"}} />);
  expect(screen.getByText("选择题语义匹配")).toBeTruthy();
  expect(screen.getByText(/匹配：其他（给定选项之外） · 正确选项：B/)).toBeTruthy();
  expect(screen.getByText(/选项仅供评测端使用/)).toBeTruthy();
  expect(screen.getAllByText("不匹配")).toHaveLength(2);
  expect(screen.queryByText("未记录逐项比较")).toBeNull();
  expect(screen.getByText("未提及甲")).toBeTruthy();
  expect(screen.getByText(/协议 scoring-v1 · 模型 judge-real/)).toBeTruthy();
  expect(screen.queryByText(/undefined|确定性评分/)).toBeNull();
});


it("开放式满分不推导出选项匹配，也不沿用选择题评分说明", () => {
  render(<EvaluationResult value={{scorer: "answer_quality", score: 2, max_score: 2, normalized_score: 1}}
    definition={{answer_format: "choice", correct_option: "A", options: [{id: "A", text: "正确选项"}]}} />);
  expect(screen.getByText(/按 0 \/ 1 \/ 2 分评价回答质量/)).toBeTruthy();
  expect(screen.queryByRole("list")).toBeNull();
  expect(screen.queryByText(/未记录逐项比较/)).toBeNull();
});
