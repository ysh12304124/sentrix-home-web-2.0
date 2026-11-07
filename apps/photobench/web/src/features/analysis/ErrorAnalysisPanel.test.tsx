import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { api, post } from "../../api/client.ts";
import type { AnalysisReport, AnalysisView } from "../../api/analysis.ts";
import { ErrorAnalysisPanel } from "./ErrorAnalysisPanel.tsx";
vi.mock("../../api/client.ts", () => ({
  BASE: "/api/v1",
  api: vi.fn(),
  post: vi.fn(),
}));
vi.mock("./AnalysisCharts.tsx", () => ({
  AnalysisChart: ({ label }: { label: string }) => (
    <div role="img" aria-label={label} />
  ),
}));
const scrollIntoView = vi.fn();
beforeEach(() => {
  HTMLElement.prototype.scrollIntoView = scrollIntoView;
  window.history.replaceState({}, "", "/");
});
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
const report: AnalysisReport = {
  schema_version: 1,
  rule_version: 9,
  generated_at: 1,
  item_count: 36,
  source: { status: "completed" },
  result_fingerprint: "r",
  gt_fingerprint: "g",
  counts: { score_0: 30, score_2: 6, affected_items: 30 },
  denominators: { quality_valid: 36 },
  errors: [],
  groups: {
    ability_family: [
      { value: "perception", total: 36, score_0: 30, score_2: 6 },
    ],
  },
  heatmap: [],
  behavior_matrix: [{ expected: "answer", actual: "refuse", count: 3 }],
  definitions: ["恢复有来源"],
};
const view: AnalysisView = {
  items: [
    {
      index: 0,
      qa_id: "q1",
      question: "Q",
      answer: "A",
      reference_answer: "GT",
      score: 0,
      execution: "normal",
      dimensions: {},
      events: [
        {
          event_id: "i0:e0",
          code: "evidence_unavailable",
          label: "证据不可用",
          turn: 0,
          source: "tool_trace",
          step_id: "s",
          recovery: "unknown",
          basis: "recorded_fact",
          tool_name: "read_photo_text",
          failure_reason: "no_text_detected",
          evidence_refs: [
            { kind: "tool", index: 0, turn: 0, path: "tool_trace[0]" },
          ],
        },
      ],
      turns: [],
      missing: [],
      signals: [],
      text_truncated: false,
    },
  ],
  total: 36,
  page: 1,
  page_size: 20,
  summary: report,
  facets: {
    tool_name: [{ value: "read_photo_text", count: 30 }],
    failure_reason: [{ value: "no_text_detected", count: 30 }],
    ability_family: [{ value: "perception", count: 36 }],
    ability_type: [{ value: "visual_detail", count: 20 }],
  },
  ability_tree: { perception: ["visual_detail"] },
  issues: [
    {
      group_id: "g0",
      tool_name: "read_photo_text",
      code: "evidence_unavailable",
      label: "证据不可用",
      failure_reason: "no_text_detected",
      issue_kind: "evidence_unavailable",
      affected_items: 30,
      affected_calls: 30,
      low_score_items: 30,
      indices: [0],
      event_ids: ["i0:e0"],
      events: 30,
      recovery: { unknown: 30 },
    },
  ],
  tools: [
    {
      tool_name: "read_photo_text",
      recorded_calls: 40,
      using_items: 36,
      affected_items: 30,
      affected_calls: 30,
    },
  ],
};
const job = {
  job_id: "j",
  run_id: "r",
  kind: "rules",
  status: "completed",
  processed: 36,
  report,
};
function setup(stale = false) {
  vi.mocked(api).mockImplementation(async (path) => {
    if (path.endsWith("/analyses")) return [] as never;
    if (path === "/config")
      return {
        judge_providers: [{ id: "test", label: "诊断", model: "J" }],
      } as never;
    if (path.startsWith("/runs?"))
      return {
        runs: [
          { run_id: "r", status: "completed" },
          {
            run_id: "other",
            status: "completed",
            model_name: "model",
            qa_set: "sample",
          },
        ],
        has_next: false,
      } as never;
    if (path.includes("/view?")) return view as never;
    if (path.includes("/evidence/"))
      return {
        item: {
          qa_id: "q1",
          tool_trace: [
            {
              tool: "read_photo_text",
              result: { failure_reason: "no_text_detected" },
            },
          ],
        },
      } as never;
    return { ...job, stale } as never;
  });
}
async function open() {
  render(<ErrorAnalysisPanel runId="r" terminal />);
  fireEvent.click(screen.getByText("错误分析 展开"));
  await screen.findByText("题目列表");
  await screen.findByRole("option", { name: "Judge（默认）" });
  await screen.findByRole("option", { name: /model.*sample.*other/ });
  fireEvent.click(screen.getByText("题目列表"));
}

describe("错误分析工作台", () => {
  it("自动恢复同来源的已保存分析，不启动模型请求", async () => {
    setup();
    const original = vi.mocked(api).getMockImplementation()!;
    vi.mocked(api).mockImplementation(async (path, options) => {
      if (path.endsWith("/analyses")) return [{kind: "ai", job_id: "saved", options: {parent_job: job.job_id}}] as never;
      if (path === "/analyses/saved") return {status: "completed", job_id: "saved", report: {requested: 1, covered: 1, model: "J", provider_id: "test", note: "已保存的分析", cases: []}} as never;
      return original(path, options);
    });
    await open();
    await waitFor(() => expect(api).toHaveBeenCalledWith("/analyses/saved", expect.anything()));
    expect(post).not.toHaveBeenCalled();
  });

  it("顶部只保留HTML导出，题目列表默认折叠并可展开收起", async () => {
    setup();
    const { container } = render(<ErrorAnalysisPanel runId="r" terminal />);
    fireEvent.click(screen.getByText("错误分析 展开"));
    const heading = await screen.findByText("题目列表");
    const list = heading.closest("details")!;
    expect(list.open).toBe(false);
    expect(screen.queryByText("历史分析任务")).toBeNull();
    expect(screen.queryByText("导出 JSON")).toBeNull();
    expect(screen.queryByText("导出 CSV")).toBeNull();
    expect(screen.queryByText("全选本页")).toBeNull();
    expect(screen.queryByText("清空选择")).toBeNull();
    expect(container.querySelector(".analysis-toolbar")?.contains(screen.getByText("导出 HTML"))).toBe(true);
    expect(list.querySelector("a[download]")).toBeNull();
    fireEvent.click(heading);
    await waitFor(() => expect(list.open).toBe(true));
    fireEvent.click(heading);
    await waitFor(() => expect(list.open).toBe(false));
    expect(post).not.toHaveBeenCalled();
  });
  it("点击统计板块筛选时自动展开题目列表，仍可手动收起", async () => {
    setup();
    render(<ErrorAnalysisPanel runId="r" terminal />);
    fireEvent.click(screen.getByText("错误分析 展开"));
    const list = (await screen.findByText("题目列表")).closest("details")!;
    expect(list.open).toBe(false);
    fireEvent.click(await screen.findByRole("tab", { name: "执行问题" }));
    fireEvent.click(screen.getByText("相关题"));
    await waitFor(() => expect(list.open).toBe(true));
    expect(window.location.search).toContain("ea_tool_name=read_photo_text");
    fireEvent.click(screen.getByText("题目列表"));
    await waitFor(() => expect(list.open).toBe(false));
    expect(screen.getByText("两次测评 AI 对比")).toBeTruthy();
    expect(post).not.toHaveBeenCalled();
  });

  it("折叠时不取数据；四视图直接切换且统计涵盖所有分页", async () => {
    setup();
    render(<ErrorAnalysisPanel runId="r" terminal />);
    expect(api).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("错误分析 展开"));
    await screen.findByLabelText("一级能力");
    expect(screen.getAllByRole("tab")).toHaveLength(4);
    expect(screen.getByText(/当前范围 36\/36/)).toBeTruthy();
    fireEvent.click(await screen.findByRole("tab", { name: "执行问题" }));
    expect(screen.getByText("按工具定位")).toBeTruthy();
    expect(screen.getByText("未满分题数")).toBeTruthy();
    expect(screen.queryByText("应用筛选")).toBeNull();
  });
  it("页面筛选保留已有条件，顶部HTML始终导出完整报告", async () => {
    setup();
    await open();
    fireEvent.change(screen.getByLabelText("答案正确性"), {
      target: { value: "0" },
    });
    fireEvent.click(await screen.findByRole("tab", { name: "执行问题" }));
    fireEvent.click(screen.getByText("相关题"));
    await waitFor(() =>
      expect(api).toHaveBeenCalledWith(
        expect.stringMatching(
          /\/view\?.*score=0.*tool_name=read_photo_text.*failure_reason=no_text_detected/,
        ),
        expect.anything(),
      ),
    );
    expect(screen.getByText("导出 HTML").getAttribute("href")).toBe(
      "/api/v1/analyses/j/export?format=html",
    );
    expect(window.location.search).toContain("ea_tool_name=read_photo_text");
    expect(post).not.toHaveBeenCalled();
  });
  it("一级和二级联动；清空筛选恢复列表与URL，浏览器后退可恢复", async () => {
    setup();
    await open();
    fireEvent.change(screen.getByLabelText("一级能力"), {
      target: { value: "perception" },
    });
    expect(
      (screen.getByLabelText("二级能力") as HTMLSelectElement).options,
    ).toHaveLength(2);
    fireEvent.change(screen.getByLabelText("二级能力"), {
      target: { value: "visual_detail" },
    });
    fireEvent.click(screen.getByText("清空筛选"));
    expect(window.location.search).toBe("");
    window.history.replaceState({}, "", "/?ea_score=1");
    fireEvent(window, new PopStateEvent("popstate"));
    await waitFor(() =>
      expect(
        (screen.getByLabelText("答案正确性") as HTMLSelectElement).value,
      ).toBe("1"),
    );
  });
  it("只按需加载固定报告的证据，不请求当前run的可变轨迹", async () => {
    setup();
    await open();
    expect(
      vi.mocked(api).mock.calls.some(([p]) => p.includes("/evidence/")),
    ).toBe(false);
    fireEvent.click(screen.getByText(/查看证据 · 第1轮/));
    await waitFor(() =>
      expect(api).toHaveBeenCalledWith("/analyses/j/evidence/0"),
    );
    expect(vi.mocked(api).mock.calls.some(([p]) => p.includes("/items/"))).toBe(
      false,
    );
    expect(await screen.findByText(/^关联调用 ·/)).toBeTruthy();
  });
  it("AI仅显式启动；默认范围是完整筛选集，预算和模式传到服务端", async () => {
    setup();
    vi.mocked(post).mockResolvedValue({
      job_id: "ai",
      status: "completed",
      processed: 0,
    });
    await open();
    fireEvent.change(screen.getByLabelText("分析模型"), {
      target: { value: "judge" },
    });
    expect(post).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText("分析所选题目并汇总"));
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith(
        "/runs/r/analysis",
        expect.objectContaining({
          kind: "ai",
          scope: "filter",
          mode: "overview",
          parent_job: "j",
          model_role: "judge",
        }),
      ),
    );
  });
  it("范围只有当前筛选和全部题目，模型只有Judge和主模型", async () => {
    setup();
    vi.mocked(post).mockResolvedValue({ job_id: "ai", status: "completed", processed: 0 });
    await open();
    const scope = screen.getByLabelText("分析范围") as HTMLSelectElement;
    expect([...scope.options].map(o => o.value)).toEqual(["filter", "run"]);
    const model = screen.getByLabelText("分析模型") as HTMLSelectElement;
    expect([...model.options].map(o => o.value)).toEqual(["judge", "main"]);
    expect(model.value).toBe("judge");
    expect(screen.queryByLabelText("选择 q1")).toBeNull();
    fireEvent.change(scope, { target: { value: "run" } });
    fireEvent.change(model, { target: { value: "main" } });
    fireEvent.click(screen.getByText("仅逐题分析"));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/runs/r/analysis", expect.objectContaining({ scope: "run", model_role: "main", mode: "cases" })));
  });
  it("过期来源禁止新AI与对比", async () => {
    setup(true);
    await open();
    fireEvent.change(screen.getByLabelText("分析模型"), {
      target: { value: "judge" },
    });
    fireEvent.change(screen.getByLabelText("选择对比运行"), {
      target: { value: "other" },
    });
    expect(
      (screen.getByText("分析所选题目并汇总") as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(
      (screen.getByText("开始 AI 对比") as HTMLButtonElement).disabled,
    ).toBe(true);
  });
  it("仍支持选运行创建对比", async () => {
    setup();
    vi.mocked(post).mockResolvedValue({
      job_id: "compare",
      status: "completed",
      processed: 0,
    });
    await open();
    fireEvent.change(screen.getByLabelText("选择对比运行"), {
      target: { value: "other" },
    });
    fireEvent.click(screen.getByText("开始 AI 对比"));
    await waitFor(() =>
      expect(post).toHaveBeenCalledWith("/runs/r/analysis", {
        kind: "ai_compare",
        comparison_filter: "all",
        model_role: "judge",
        scope: "filter",
        filters: {},
        left_job: "j",
        right_job: "j",
      }),
    );
  });
  it("对比可叠加仅得分不同与全部范围，沿用主模型", async () => {
    setup();
    vi.mocked(post).mockResolvedValue({ job_id: "compare", status: "completed", processed: 0 });
    await open();
    fireEvent.change(screen.getByLabelText("分析范围"), { target: { value: "run" } });
    fireEvent.change(screen.getByLabelText("分析模型"), { target: { value: "main" } });
    fireEvent.change(screen.getByLabelText("对比题目"), { target: { value: "score_changed" } });
    fireEvent.change(screen.getByLabelText("选择对比运行"), { target: { value: "other" } });
    fireEvent.click(screen.getByText("开始 AI 对比"));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/runs/r/analysis", expect.objectContaining({ kind: "ai_compare", scope: "run", comparison_filter: "score_changed", model_role: "main" })));
  });
  it("AI失败和抽样覆盖清楚展示，证据链接可跨页定位", async () => {
    setup();
    vi.mocked(post).mockResolvedValue({
      job_id: "ai",
      status: "completed",
      processed: 2,
      report: {
        schema_version: 3,
        model: "J",
        provider_id: "test",
        requested: 36,
        covered: 1,
        coverage: { invalid: 0, failed: 1, unprocessed: 34 },
        scope: { indices: [0, 25], kind: "filter" },
        stage: "completed",
        summary_status: "failed",
        summary_error: "timeout",
        note: "引用仅说明来源",
        cases: [
          {
            index: 25,
            qa_id: "q25",
            valid: true,
            result: {
              claims: [
                {
                  kind: "hypothesis",
                  text: "需要确认文字导入情况",
                  limitations: "只查了一题",
                  evidence_refs: ["i25:case"],
                },
              ],
              missing_evidence: [],
            },
            raw: "",
          },
        ],
      },
    });
    await open();
    fireEvent.change(screen.getByLabelText("分析模型"), {
      target: { value: "judge" },
    });
    fireEvent.click(screen.getByText("分析所选题目并汇总"));
    await screen.findByText(/汇总生成失败/);
    expect(screen.getByText(/未尝试分析 34/)).toBeTruthy();
    fireEvent.click(screen.getByText("q25"));
    await waitFor(() => expect(api).toHaveBeenCalledWith("/analyses/j/evidence/25", expect.anything()));
    fireEvent.click(screen.getByText("关键分析依据（1条）"));
    fireEvent.click(screen.getAllByRole("button", { name: "题目与回答" })[0]);
    expect(screen.getByLabelText("节点详情").textContent).toContain("此引用未唯一关联到调用节点");
  });
});

it("案例默认只显示命中问题，可展开其余调用", async () => {
  setup();
  const filtered = structuredClone(view);
  filtered.items[0].events.push({
    ...filtered.items[0].events[0],
    event_id: "i0:e1",
    label: "评分调用故障",
    tool_name: undefined,
    component: "judge",
  });
  filtered.items[0].matched_event_ids = ["i0:e0"];
  const original = vi.mocked(api).getMockImplementation()!;
  vi.mocked(api).mockImplementation((path, options) =>
    path.includes("/view?")
      ? Promise.resolve(filtered as never)
      : original(path, options),
  );
  await open();
  expect(screen.getByText(/命中 1 \/ 共 2 条执行问题/)).toBeTruthy();
  expect(screen.queryByText("评分调用故障")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "展开其余问题" }));
  expect(screen.getByText("评分调用故障")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "仅看命中问题" }));
  expect(screen.queryByText("评分调用故障")).toBeNull();
});
