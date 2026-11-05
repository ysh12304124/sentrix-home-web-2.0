import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { AnalysisReport, GroupPartition } from "../../api/analysis.ts";
import { QualityRing, TagAnalysis } from "./AnalysisBreakdown.tsx";
import { analysisValue } from "./presentation.ts";
const captured = vi.hoisted(() => ({
  option: {} as Record<string, unknown>,
  click: undefined as undefined | ((name: string, value: unknown) => void),
}));
vi.mock("./AnalysisCharts.tsx", () => ({
  AnalysisChart: ({
    label,
    option,
    onClick,
  }: {
    label: string;
    option: Record<string, unknown>;
    onClick?: (name: string, value: unknown) => void;
  }) => {
    captured.option = option;
    captured.click = onClick;
    return <div role="img" aria-label={label} />;
  },
}));
afterEach(cleanup);
const partition: GroupPartition = {
  total: 4,
  labeled: 3,
  unknown: 1,
  not_applicable: 0,
  multi_labeled: 0,
  exclusive: true,
  exhaustive: true,
};
function report(patch: Partial<AnalysisReport> = {}): AnalysisReport {
  return {
    schema_version: 2,
    rule_version: 10,
    generated_at: 1,
    item_count: 4,
    source: {},
    result_fingerprint: "r",
    gt_fingerprint: "g",
    ability_taxonomy_versions: ["ability-2026.10.28-v2"],
    ability_labels: { P: "信息获取与感知", P3: "视觉内容理解", H: "抗幻觉" },
    counts: {
      score_2: 2,
      score_0: 1,
      score_None: 1,
      turn_score_2: 3,
      turn_score_0: 1,
      turn_score_None: 1,
    },
    denominators: { turns: 5 },
    groups: {
      ability_family: [
        { value: "P", total: 2, score_2: 1, score_0: 1, affected: 1 },
        { value: "H", total: 1, score_2: 1 },
        { value: "unknown", total: 1, score_None: 1 },
      ],
      ability_type: [{ value: "P3", total: 2, score_2: 1, score_0: 1 }],
    },
    group_partitions: {
      qa: { ability_family: partition },
      turn: { ability_family: { ...partition, total: 5 } },
    },
    group_errors: [
      {
        unit: "qa",
        dimension: "ability_family",
        value: "P",
        code: "evidence_unavailable",
        count: 1,
      },
    ],
    errors: [
      {
        code: "evidence_unavailable",
        label: "未取得所需记录",
        affected_items: 1,
        events: 2,
        final_failed_items: 0,
        recovered_items: 0,
      },
    ],
    behavior_matrix: [],
    heatmap: [],
    definitions: [],
    ...patch,
  };
}
it("质量环形图覆盖缺分并保持轮级筛选", () => {
  expect(analysisValue("None")).toBe("未评分");
  const filter = vi.fn();
  render(<QualityRing report={report()} filter={filter} unit="turn" />);
  const series = captured.option.series as { data: { value: number }[] }[];
  expect(series[0].data.map((x) => x.value)).toEqual([3, 0, 1, 1]);
  fireEvent.click(screen.getByText("未评分"));
  expect(filter).toHaveBeenCalledWith({ score: "None", content_unit: "turn" });
});
it("v2题型按得分和执行问题下钻，图表同样带上标签条件", () => {
  const filter = vi.fn();
  render(<TagAnalysis report={report()} filter={filter} />);
  fireEvent.click(screen.getByLabelText("信息获取与感知 未完成（0分）"));
  expect(filter).toHaveBeenLastCalledWith({
    ability_family: "P",
    ability_type: "",
    score: "0",
    content_unit: "qa",
  });
  fireEvent.click(screen.getByLabelText("信息获取与感知 执行问题"));
  expect(filter).toHaveBeenLastCalledWith(
    expect.objectContaining({ ability_family: "P", anomaly: "yes" }),
  );
  captured.click?.(JSON.stringify(["H", "2"]), 1);
  expect(filter).toHaveBeenLastCalledWith(
    expect.objectContaining({ ability_family: "H", score: "2" }),
  );
});
it("互斥全集允许环形图和条形图切换，并按所选得分下钻", () => {
  const filter = vi.fn();
  render(<TagAnalysis report={report()} filter={filter} />);
  fireEvent.click(screen.getByRole("button", { name: "题目构成" }));
  expect(screen.getByRole("img", { name: "标签构成环形图" })).toBeTruthy();
  fireEvent.change(screen.getByLabelText("构成得分范围"), {
    target: { value: "0" },
  });
  captured.click?.("P", 1);
  expect(filter).toHaveBeenLastCalledWith(
    expect.objectContaining({ ability_family: "P", score: "0" }),
  );
  fireEvent.change(screen.getByLabelText("构成图形"), {
    target: { value: "bar" },
  });
  expect(screen.getByRole("img", { name: "标签构成条形图" })).toBeTruthy();
});
it("重叠与缺标不能相抵，旧报告未验证也不能画环形图", () => {
  const v = render(
    <TagAnalysis
      report={report({
        group_partitions: {
          qa: {
            ability_family: {
              ...partition,
              multi_labeled: 1,
              exclusive: false,
            },
          },
        },
      })}
      filter={vi.fn()}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "题目构成" }));
  expect(screen.queryByLabelText("构成图形")).toBeNull();
  expect(screen.getByRole("img", { name: "标签构成条形图" })).toBeTruthy();
  v.rerender(
    <TagAnalysis report={report({ group_partitions: {} })} filter={vi.fn()} />,
  );
  expect(screen.queryByLabelText("构成图形")).toBeNull();
  expect(screen.getByText(/旧报告未保存逐项分组检查/)).toBeTruthy();
});
it("混合能力版本必须先筛选版本，不误合并同名代码", () => {
  render(
    <TagAnalysis
      report={report({
        ability_taxonomy_versions: [
          "ability-2026.10.28-v1",
          "ability-2026.10.28-v2",
        ],
      })}
      filter={vi.fn()}
    />,
  );
  expect(screen.getByRole("status").textContent).toContain("标签版本");
  expect(screen.queryByRole("img")).toBeNull();
});
it("矩阵同时筛选标签与具体问题，并提供可访问数值表", () => {
  const filter = vi.fn();
  render(<TagAnalysis report={report()} filter={filter} />);
  fireEvent.click(screen.getByRole("button", { name: "执行问题" }));
  expect(screen.getByRole("img", { name: "标签与执行问题矩阵" })).toBeTruthy();
  captured.click?.("", [0, 0, 1]);
  expect(filter).toHaveBeenLastCalledWith(
    expect.objectContaining({
      ability_family: "P",
      code: "evidence_unavailable",
    }),
  );
  expect(screen.getByText("查看矩阵数值")).toBeTruthy();
});
it("特殊题无二级能力，轮级与题级得分独立", () => {
  const filter = vi.fn();
  const r = report();
  r.turn_groups = {
    ability_family: [
      { value: "P", total: 5, score_2: 3, score_0: 1, score_None: 1 },
    ],
    ability_type: r.groups.ability_type,
  };
  render(<TagAnalysis report={r} filter={filter} unit="turn" />);
  fireEvent.click(screen.getByLabelText("信息获取与感知 满分（2分）"));
  expect(filter).toHaveBeenLastCalledWith(
    expect.objectContaining({ content_unit: "turn", score: "2" }),
  );
  fireEvent.change(screen.getByLabelText("标签分析分组"), {
    target: { value: "ability_type" },
  });
  expect(screen.getByText(/抗幻觉、抗注入及普通澄清轮无二级能力/)).toBeTruthy();
  expect(screen.getByRole("button", { name: "视觉内容理解" })).toBeTruthy();
});

it("v3安全二级同名展示并按H1/I1筛选", () => {
  const filter = vi.fn();
  const r = report({
    ability_taxonomy_versions: ["ability-2026.10.29-v3"],
    ability_labels: {H: "抗幻觉", H1: "抗幻觉", I: "抗注入攻击", I1: "抗注入攻击"},
    groups: {ability_type: [{value: "H1", total: 130}, {value: "I1", total: 18}]},
  });
  render(<TagAnalysis report={r} filter={filter} unit="qa" />);
  fireEvent.change(screen.getByLabelText("标签分析分组"), {target: {value: "ability_type"}});
  expect(screen.getByText(/v3二级包含抗幻觉和抗注入攻击/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", {name: "抗幻觉"}));
  expect(filter).toHaveBeenLastCalledWith(expect.objectContaining({ability_type: "H1"}));
  fireEvent.click(screen.getByRole("button", {name: "抗注入攻击"}));
  expect(filter).toHaveBeenLastCalledWith(expect.objectContaining({ability_type: "I1"}));
});
