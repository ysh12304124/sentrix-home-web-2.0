import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { AnalysisReport } from "../../api/analysis.ts";
import { AnalysisChart, AnalysisCharts } from "./AnalysisCharts.tsx";

const chart = vi.hoisted(() => ({
  setOption: vi.fn(), on: vi.fn(), resize: vi.fn(), dispose: vi.fn(),
}));
const init = vi.hoisted(() => vi.fn(() => chart));
vi.mock("echarts", () => ({ init }));
beforeEach(() => vi.clearAllMocks());
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it("轮询更新复用图表实例并使用最新点击回调，卸载才释放", () => {
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  const first = vi.fn();
  const next = vi.fn();
  const view = render(<AnalysisChart label="错误" option={{}} onClick={first} />);
  view.rerender(<AnalysisChart label="错误" option={{ series: [] }} onClick={next} />);
  expect(init).toHaveBeenCalledTimes(1);
  expect(chart.setOption).toHaveBeenCalledTimes(2);
  expect(chart.setOption.mock.calls.every(([option]) => option.animation === false)).toBe(true);
  expect(chart.dispose).not.toHaveBeenCalled();
  const click = chart.on.mock.calls[0]?.[1] as (event: {name: string; value: number}) => void;
  act(() => click({name: "超时", value: 2}));
  expect(next).toHaveBeenCalledWith("超时", 2);
  expect(first).not.toHaveBeenCalled();
  view.unmount();
  expect(chart.dispose).toHaveBeenCalledTimes(1);
});

it("轮询相同统计、修改筛选及仅来源变化时不重绘；真实统计变化仍更新且点击回调不滞后", () => {
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  const report: AnalysisReport = {
    schema_version: 1, rule_version: 4, generated_at: 1,
    item_count: 10, source: {}, result_fingerprint: "r", gt_fingerprint: "g",
    counts: { score_0: 6, score_1: 2, score_2: 2, events: 1 },
    denominators: { quality_valid: 10, quality_missing: 0, turns: 10,
      turn_quality_valid: 10, behavior_valid: 10, behavior_missing: 0 },
    errors: [{ code: "context_soft_limit", label: "上下文软预算预检",
      events: 1, affected_items: 1, final_failed_items: 0, recovered_items: 1 }],
    groups: {content_type: [{value: "T5_event_summary", total: 10, score_0: 6, score_1: 2, score_2: 2}]},
    heatmap: [{content_type: "T5_event_summary", code: "context_soft_limit", count: 1}],
    behavior_matrix: [{expected: "answer", actual: "answer", count: 10}], definitions: [],
  };
  const first = vi.fn(), latest = vi.fn();
  const view = render(<AnalysisCharts report={report} filter={first} />);
  expect(init).toHaveBeenCalledTimes(4);
  expect(chart.setOption).toHaveBeenCalledTimes(4);
  for (let poll = 0; poll < 12; poll++) {
    const refreshed = structuredClone(report);
    refreshed.source = { stale: poll % 2 === 0 };
    view.rerender(<AnalysisCharts report={refreshed} filter={latest} />);
  }
  expect(chart.setOption).toHaveBeenCalledTimes(4);
  expect(chart.dispose).not.toHaveBeenCalled();
  const clickHeatmap = chart.on.mock.calls[2]?.[1] as (e: {name: string; value: number[]}) => void;
  act(() => clickHeatmap({name: "", value: [0, 0, 1]}));
  expect(latest).toHaveBeenCalledWith(expect.objectContaining({code: "context_soft_limit", content_type: "T5_event_summary"}));
  expect(first).not.toHaveBeenCalled();
  const changed = structuredClone(report);
  changed.errors[0]!.affected_items = 2;
  changed.heatmap[0]!.count = 2;
  view.rerender(<AnalysisCharts report={changed} filter={latest} />);
  expect(chart.setOption).toHaveBeenCalledTimes(6);
  expect(chart.setOption.mock.calls.at(-1)?.[0].series[0].data).toEqual([[0, 0, 2]]);
  expect(chart.setOption.mock.calls.every(([option]) => option.animation === false)).toBe(true);
});

it("默认任务类型展示正确/部分/错误/未评分，点击类别与分数共同筛选；无评分不显示0%", () => {
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  const report = { schema_version: 1, rule_version: 4, generated_at: 1, item_count: 4,
    source: {}, result_fingerprint: "r", gt_fingerprint: "g", counts: {score_2: 1, score_1: 1, score_0: 1, score_None: 1},
    denominators: {quality_valid: 3, quality_missing: 1}, errors: [], heatmap: [], behavior_matrix: [], definitions: [],
    groups: {content_type: [{value: "T5_event_summary", total: 2, score_2: 1, score_1: 1}, {value: "T3_enumeration", total: 1, score_0: 1}, {value: "unknown", total: 1, score_None: 1}],
      content_subtype: [{value: "fact_time", total: 4, score_2: 1, score_1: 1, score_0: 1, score_None: 1}]},
  } as AnalysisReport;
  const filter = vi.fn();
  render(<AnalysisCharts report={report} filter={filter} />);
  expect(screen.getByText("内容概述 ›")).toBeTruthy();
  expect(screen.getByText("统计整理 ›")).toBeTruthy();
  expect(screen.getByText("未覆盖 / 0题")).toBeTruthy();
  const distribution = chart.setOption.mock.calls.find(([option]) => option.series?.[0]?.stack === "scores")?.[0];
  expect(distribution.series[0].data[0].label.show).toBe(true);
  expect(distribution.series[0].data[1].label.show).toBe(false);
  fireEvent.click(screen.getByLabelText("内容概述 满分（2分）"));
  expect(filter).toHaveBeenLastCalledWith(expect.objectContaining({content_type: "T5_event_summary", score: "2", code: ""}));
  fireEvent.change(screen.getByLabelText("分布分组方式"), {target: {value: "content_subtype"}});
  fireEvent.click(screen.getByLabelText("时间 部分完成（1分）"));
  expect(filter).toHaveBeenLastCalledWith(expect.objectContaining({content_subtype: "fact_time", score: "1", content_type: ""}));
});

it("合并后的媒体检索与行为分类显示中文，窄柱段数字保留在表格", () => {
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  const report = {
    item_count: 174, counts: {}, denominators: {turns: 174, turn_quality_valid: 174},
    groups: {content_type: [
      {value: "unknown", total: 129, score_2: 28, score_1: 40, score_0: 60, score_None: 1},
      {value: "T1_retrieve_media", total: 45, score_2: 2, score_1: 3, score_0: 40},
    ]}, errors: [], heatmap: [], behavior_matrix: [],
  } as unknown as AnalysisReport;
  const filter = vi.fn();
  render(<AnalysisCharts report={report} filter={filter} />);
  expect(screen.getAllByText("媒体检索 ›")).toHaveLength(1);
  expect(screen.getByText("未标注内容题型")).toBeTruthy();
  expect(screen.queryByText("behavior")).toBeNull();
  expect(screen.queryByText("怎么读这份报告")).toBeNull();
  const option = chart.setOption.mock.calls[0]?.[0];
  expect(option.yAxis.data).toEqual(["未标注内容题型", "媒体检索"]);
  expect(option.series[0].data[1].label.show).toBe(false);
  expect(screen.getByLabelText("媒体检索 满分（2分）").textContent).toBe("2");
  fireEvent.click(screen.getByLabelText("媒体检索 未完成（0分）"));
  expect(filter).toHaveBeenLastCalledWith(expect.objectContaining({content_type: "T1_retrieve_media", score: "0"}));
});

it("按得分看题型按题数排序，占比以该得分全部题为分母，图表和表格可查看案例", () => {
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  const report = {
    item_count: 20, counts: {score_2: 5, score_0: 15}, denominators: {},
    groups: {content_type: [
      {value: "T1_retrieve_media", total: 11, score_2: 1, score_0: 10},
      {value: "unknown", total: 8, score_2: 3, score_0: 5},
      {value: "T5_event_summary", total: 1, score_2: 1},
    ]}, errors: [], heatmap: [], behavior_matrix: [],
  } as unknown as AnalysisReport;
  const filter = vi.fn();
  render(<AnalysisCharts report={report} filter={filter} />);
  fireEvent.click(screen.getByRole("button", {name: "按得分看题型"}));
  expect(screen.getByText(/满分（2分） · 共5题/)).toBeTruthy();
  expect(screen.getAllByText("50.0%")).toHaveLength(2);
  const scoreOption = () => chart.setOption.mock.calls.filter(([option]) => option.legend?.show === false).at(-1)![0];
  expect(scoreOption().yAxis.data).toEqual(["媒体检索", "内容概述"]);
  expect(scoreOption().series[0].data.map((x: {value: number}) => x.value)).toEqual([1, 1]);
  fireEvent.click(screen.getByRole("button", {name: "媒体检索 满分（2分）案例"}));
  expect(filter).toHaveBeenLastCalledWith(expect.objectContaining({content_type: "T1_retrieve_media", score: "2"}));
  fireEvent.change(screen.getByLabelText("分布显示方式"), {target: {value: "ratio"}});
  expect(scoreOption().series[0].data.map((x: {value: number}) => x.value)).toEqual([50, 50]);
  fireEvent.change(screen.getByLabelText("分布得分"), {target: {value: "0"}});
  expect(scoreOption().yAxis.data).toEqual(["媒体检索"]);
  expect(screen.getAllByText("100.0%").length).toBeGreaterThan(0);
  const click = chart.on.mock.calls.at(-1)![1] as (e: {name: string; value: number}) => void;
  act(() => click({name: "T1_retrieve_media", value: 66.7}));
  expect(filter).toHaveBeenLastCalledWith(expect.objectContaining({content_type: "T1_retrieve_media", score: "0"}));
  expect(chart.setOption.mock.calls.every(([, options]) => options.replaceMerge.includes("series"))).toBe(true);
  fireEvent.click(screen.getByRole("button", {name: "按题型看得分"}));
  expect(screen.queryByLabelText("分布得分")).toBeNull();
  expect(chart.setOption.mock.calls.at(-1)![0].series).toHaveLength(4);
});

it("所选得分无题、分类缺失及多标签重叠如实展示，不用全部题数计算比例", () => {
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  const report = {item_count: 5, counts: {score_2: 2, score_1: 1}, denominators: {},
    groups: {content_type: [{value: "T2_fact_qa", total: 2, score_2: 2}, {value: "T5_event_summary", total: 1, score_2: 1}], angle: []},
    errors: [], heatmap: [], behavior_matrix: [],
  } as unknown as AnalysisReport;
  render(<AnalysisCharts report={report} filter={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", {name: "按得分看题型"}));
  expect(screen.getAllByText("100.0%").length).toBeGreaterThan(0);
  expect(screen.getByText("50.0%")).toBeTruthy();
  expect(screen.getByText("一题可属于多类，各类占比合计可能超过100%。")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("分布得分"), {target: {value: "None"}});
  expect(screen.getByText("当前范围没有未评分的题目。")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("分布得分"), {target: {value: "1"}});
  expect(screen.getByText("这些题未标注内容题型。")).toBeTruthy();
  fireEvent.change(screen.getByLabelText("分布分组方式"), {target: {value: "content_subtype"}});
  expect(screen.getByText("这些题未标注细分题型。")).toBeTruthy();
});


it("行为分布按轮单独组成100%，行为符合与质量分分别筛选", () => {
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  const report = {rule_version: 4, item_count: 3, counts: {score_0: 2, score_2: 1},
    groups: {content_type: [{value: "T2_fact_qa", total: 1, score_0: 1}, {value: "unknown", total: 2, score_0: 1, score_2: 1}]},
    denominators: {turns: 4, behavior_valid: 4, behavior_missing: 0, turn_quality_valid: 4},
    behavior_groups: [{value: "answer", total: 2, correct: 2, incorrect: 0, valid: 2, missing: 0},
      {value: "clarify", total: 1, correct: 0, incorrect: 1, valid: 1, missing: 0},
      {value: "refuse", total: 1, correct: 1, incorrect: 0, valid: 1, missing: 0}],
    behavior_scenarios: [{value: "prompt_injection", total: 1, correct: 1, incorrect: 0, valid: 1, missing: 0}],
    errors: [], heatmap: [], behavior_matrix: [],
  } as unknown as AnalysisReport;
  const filter = vi.fn();
  render(<AnalysisCharts report={report} filter={filter} />);
  const composition = screen.getByLabelText("预期行为占比");
  expect([...composition.querySelectorAll("button")].map(x => x.style.width)).toEqual(["50%", "25%", "25%"]);
  fireEvent.click(screen.getByTitle("澄清 1轮 · 25.0%"));
  expect(filter).toHaveBeenLastCalledWith(expect.objectContaining({expected_action: "clarify", score: "", content_type: ""}));
  fireEvent.click(screen.getByRole("button", {name: "按得分看题型"}));
  fireEvent.change(screen.getByLabelText("分布得分"), {target: {value: "0"}});
  expect(screen.getAllByText("100.0%").length).toBeGreaterThan(0);
  expect(screen.getByText("未标注内容题型 1题")).toBeTruthy();
  expect(screen.queryByRole("button", {name: "未标注内容题型 未完成（0分）案例"})).toBeNull();
});

it("旧报告要求重生成，不再使用混合的任务标签绘图", () => {
  render(<AnalysisCharts report={{rule_version: 3} as AnalysisReport} filter={vi.fn()} />);
  expect(screen.getByText(/此报告使用旧分类口径/)).toBeTruthy();
  expect(init).not.toHaveBeenCalled();
});

it("两级题型下钻保留得分筛选，纯安全和待核排除占比，轮级筛选使用同一轮", () => {
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  const report = {rule_version: 5, item_count: 4, counts: {score_2: 4, turn_score_2: 5},
    groups: {content_type: [
      {value: "T2_fact_qa", total: 2, score_2: 2}, {value: "not_applicable", total: 1, score_2: 1},
      {value: "pending_review", total: 1, score_2: 1}],
      content_subtype: [{value: "fact_place", total: 1, score_2: 1}, {value: "fact_time", total: 1, score_2: 1}]},
    turn_groups: {content_type: [{value: "T2_fact_qa", total: 5, score_2: 5}],
      content_subtype: [{value: "fact_place", total: 2, score_2: 2}, {value: "fact_time", total: 3, score_2: 3}]},
    denominators: {turns: 5, turn_quality_valid: 5}, errors: [], heatmap: [], behavior_matrix: [],
  } as unknown as AnalysisReport;
  const filter = vi.fn();
  render(<AnalysisCharts report={report} filter={filter} />);
  fireEvent.click(screen.getByRole("button", {name: "按得分看题型"}));
  expect(screen.getByText("100.0%")).toBeTruthy();
  expect(screen.getByRole("button", {name: "不适用 1题"})).toBeTruthy();
  fireEvent.click(screen.getByRole("button", {name: "事实查询 ›"}));
  expect(screen.getByLabelText("分析内容大类")).toHaveProperty("value", "T2_fact_qa");
  fireEvent.click(screen.getByRole("button", {name: "地点 满分（2分）案例"}));
  expect(filter).toHaveBeenLastCalledWith(expect.objectContaining({content_subtype: "fact_place", score: "2", content_unit: "qa"}));
  fireEvent.change(screen.getByLabelText("内容统计单位"), {target: {value: "turn"}});
  fireEvent.click(screen.getByRole("button", {name: "展开细分题型"}));
  fireEvent.click(screen.getByRole("button", {name: "时间 满分（2分）案例"}));
  expect(filter).toHaveBeenLastCalledWith(expect.objectContaining({content_subtype: "fact_time", score: "2", content_unit: "turn"}));
});

it("新版能力分布按题去重；二级下钻的分母不重复累加同题多个标签", () => {
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  const filter = vi.fn();
  const report = { rule_version: 7, item_count: 3,
    denominators: {turns: 3, turn_quality_valid: 0, behavior_valid: 0, behavior_missing: 3},
    counts: {score_2: 3, qa_ability_score_2: 2, qa_ability_family_P_score_2: 2},
    groups: {ability_family: [{value: "P", total: 2, score_2: 2}, {value: "not_applicable", total: 1, score_2: 1}],
      ability_type: [{value: "P1", total: 2, score_2: 2}, {value: "P3", total: 1, score_2: 1}],
      content_type: []}, errors: [], heatmap: [], behavior_matrix: [],
  } as unknown as AnalysisReport;
  render(<AnalysisCharts report={report} filter={filter} />);
  expect((screen.getByLabelText("分布分组方式") as HTMLSelectElement).value).toBe("ability_family");
  fireEvent.click(screen.getByText("展开二级能力"));
  fireEvent.change(screen.getByLabelText("分析一级能力"), {target: {value: "P"}});
  fireEvent.click(screen.getByText("按得分看题型"));
  fireEvent.change(screen.getByLabelText("分布显示方式"), {target: {value: "ratio"}});
  const option = chart.setOption.mock.calls.map(([x]) => x).reverse().find(x => x.series?.[0]?.data?.some((d: {name?: string}) => d.name === "P1"));
  expect(option.series[0].data).toEqual([{name: "P1", value: 100}, {name: "P3", value: 50}]);
  expect(screen.getByText(/各类占比合计可能超过100%/)).toBeTruthy();
  const click = chart.on.mock.calls.at(-1)?.[1] as (event: {name: string; value: number}) => void;
  act(() => click({name: "P1", value: 100}));
  expect(filter).toHaveBeenLastCalledWith(expect.objectContaining({ability_type: "P1", score: "2"}));
});

it("新版异常热力图使用一级能力并把点击传给同一能力筛选", () => {
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  const filter = vi.fn();
  const report = {rule_version: 7, item_count: 1, counts: {}, denominators: {turns: 1},
    groups: {ability_family: [{value: "P", total: 1}], content_type: [{value: "unknown", total: 1}]},
    errors: [{code: "request_timeout", label: "请求超时", affected_items: 1, events: 2}],
    heatmap: [{content_type: "unknown", code: "request_timeout", count: 1}],
    ability_heatmap: [{ability_family: "P", code: "request_timeout", count: 1}],
    behavior_matrix: [],
  } as unknown as AnalysisReport;
  render(<AnalysisCharts report={report} filter={filter} />);
  expect(screen.getByText("一级能力与运行异常（按题）")).toBeTruthy();
  const heat = chart.setOption.mock.calls[2]![0];
  expect(heat.yAxis.data).toEqual(["信息获取与感知"]);
  const click = chart.on.mock.calls[2]![1] as (e: {name: string; value: number[]}) => void;
  act(() => click({name: "", value: [0, 0, 1]}));
  expect(filter).toHaveBeenCalledWith(expect.objectContaining({ability_family: "P", content_type: "", code: "request_timeout", content_unit: "qa"}));
});

it("五类一级分布包含抗幻觉和抗注入，分母按题去重，二级保持普通题分母", () => {
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  const filter = vi.fn();
  const report = {rule_version: 8, item_count: 3,
    ability_labels: {P: "信息获取与感知", H: "抗幻觉", I: "抗注入攻击", P1: "媒体定位与筛选"},
    counts: {score_2: 3, qa_ability_family_score_2: 3, qa_ability_score_2: 1},
    denominators: {turns: 3},
    groups: {ability_family: [{value: "P", total: 1, score_2: 1}, {value: "H", total: 1, score_2: 1}, {value: "I", total: 1, score_2: 1}],
      ability_type: [{value: "P1", total: 1, score_2: 1}, {value: "not_applicable", total: 2, score_2: 2}]},
    errors: [], heatmap: [], behavior_matrix: [],
  } as unknown as AnalysisReport;
  render(<AnalysisCharts report={report} filter={filter} />);
  fireEvent.click(screen.getByText("按得分看题型"));
  fireEvent.change(screen.getByLabelText("分布显示方式"), {target: {value: "ratio"}});
  const option = chart.setOption.mock.calls.map(([x]) => x).reverse().find(x => x.series?.[0]?.data?.some((d: {name?: string}) => d.name === "H"));
  expect(option.series[0].data).toEqual([{name: "P", value: 33.3}, {name: "H", value: 33.3}, {name: "I", value: 33.3}]);
  fireEvent.click(screen.getByRole("button", {name: "抗幻觉 满分（2分）案例"}));
  expect(filter).toHaveBeenLastCalledWith(expect.objectContaining({ability_family: "H", score: "2"}));
  fireEvent.click(screen.getByRole("button", {name: "展开二级能力"}));
  expect(screen.getByText(/抗幻觉、抗注入攻击暂不细分/)).toBeTruthy();
  expect(screen.getByRole("button", {name: "媒体定位与筛选 满分（2分）案例"})).toBeTruthy();
  expect(screen.getByText("100.0%")).toBeTruthy();
  expect(screen.queryByRole("option", {name: "抗幻觉"})).toBeNull();
});

it("规则7保存的旧报告继续显示v1名称，不随新版字典静默更名", () => {
  vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
  const report = {rule_version: 7, item_count: 1, ability_labels: {}, counts: {}, denominators: {turns: 1},
    groups: {ability_family: [{value: "P", total: 1}], ability_type: [{value: "P1", total: 1}, {value: "S4", total: 1}]},
    errors: [], heatmap: [], behavior_matrix: [],
  } as unknown as AnalysisReport;
  render(<AnalysisCharts report={report} filter={vi.fn()} />);
  fireEvent.click(screen.getByRole("button", {name: "展开二级能力"}));
  expect(screen.getByText("记录定位与筛选")).toBeTruthy();
  expect(screen.getByText("过程与时间线整理")).toBeTruthy();
  expect(screen.queryByText("媒体定位与筛选")).toBeNull();
});
