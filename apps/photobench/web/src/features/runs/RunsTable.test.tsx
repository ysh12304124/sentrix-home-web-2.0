import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Mock } from "vitest";

import type { Run } from "../../api/types.ts";
import type { RunPageState } from "../../hooks/useRuns.ts";
import { RunsTable } from "./RunsTable.tsx";

// 夹具口径：每组的两种输入必须给出不同答案。
// `status` 两行取 done / failed —— 1.0 `:3023-3025` 的 `<span class="phase-status" :class="run.status">`
// 一个渲染成 `phase-status done`「完成」，一个 `phase-status failed`「失败」。

afterEach(cleanup);

const PAGE: RunPageState = {
  page: 1,
  page_size: 20,
  total: 1,
  pages: 1,
  has_previous: false,
  has_next: false,
  active_count: 0,
};

function run(overrides: Partial<Run> = {}): Run {
  return { run_id: "r1", status: "done", model_name: "模型 A", ...overrides };
}

interface Handlers {
  onSelectRun: Mock<(run: Run) => void>;
  onDeleteRun: Mock<(run: Run) => void>;
  onChangeRunPage: Mock<(page: number) => void>;
}

function renderTable(runs: Run[], page: Partial<RunPageState> = {}, handlers?: Partial<Handlers>) {
  const merged: Handlers = {
    onSelectRun: vi.fn<(run: Run) => void>(),
    onDeleteRun: vi.fn<(run: Run) => void>(),
    onChangeRunPage: vi.fn<(page: number) => void>(),
    ...handlers,
  };
  const view = render(
    <RunsTable
      runs={runs}
      runPage={{ ...PAGE, total: runs.length, ...page }}
      activeRunId={null}
      regionRef={{ current: null }}
      {...merged}
    />,
  );
  return { ...view, ...merged };
}

describe("状态列（1.0 :3023-3025）", () => {
  it("按完成有效评分的样本数计算，不使用Agent完成数或Judge尝试结束数", () => {
    renderTable([run({ qa_count: 487, summary: { completed: 69, judge_valid_count: 68 }, phases: { qa_eval: {
      agent_completed: 71, judge_completed: 70, judge_total: 487,
      progress: { judge_completed: 72, judge_total: 486, completed: 69, total: 487 },
    } } })]);
    const label = screen.getByText("68/487");
    expect(screen.getByRole("columnheader", { name: "进度" })).toBeTruthy();
    expect(label.hasAttribute("title")).toBe(false);
    expect(label.parentElement?.hasAttribute("title")).toBe(false);
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it("历史有有效评分数即显示实际进度，缺少Judge尝试计数不影响", () => {
    renderTable([run({ qa_count: 487, summary: { completed: 487, judge_valid_count: 484 },
      phases: { qa_eval: { progress: { completed: 487, total: 487 } } } })]);
    expect(screen.getByText("484/487")).toBeTruthy();
    expect(screen.queryByText("487/487")).toBeNull();
  });

  it("0和未知值如实显示，评分失败不当作完成，异常计数不截断", () => {
    renderTable([run({ qa_count: 10, summary: { judge_valid_count: 0 } })]);
    expect(screen.getByText("0/10")).toBeTruthy();
    cleanup();
    renderTable([run({ qa_count: 10, phases: { qa_eval: { judge_completed: 10, judge_total: 10 } } })]);
    expect(screen.getByText("—/10")).toBeTruthy();
    cleanup();
    renderTable([run({ qa_count: 10, summary: { judge_valid_count: 9 }, phases: { qa_eval: {
      progress: { judge_completed: 10, judge_total: 10 },
    } } })]);
    expect(screen.getByText("9/10")).toBeTruthy();
    cleanup();
    renderTable([run({ qa_count: 10, summary: { judge_valid_count: 11 } })]);
    expect(screen.getByText("11/10")).toBeTruthy();
    expect(screen.queryByRole("progressbar")).toBeNull();
  });

  it("未配置Judge时不显示进度", () => {
    renderTable([run({ phases: { qa_eval: { progress: { judge_concurrency: 0 } } } })]);
    expect(screen.getByText("—")).toBeTruthy();
  });

  it("不同 status 渲染出不同的 class 与文案", () => {
    const { container } = renderTable([
      run({ run_id: "a", status: "done" }),
      run({ run_id: "b", status: "failed" }),
    ]);

    const cells = container.querySelectorAll("td .phase-status");
    expect(cells).toHaveLength(2);
    expect(cells[0]?.className).toBe("phase-status done");
    expect(cells[1]?.className).toBe("phase-status failed");
    expect(cells[0]?.textContent).toBe("完成");
    expect(cells[1]?.textContent).toBe("失败");
  });
});

describe("删除按钮（1.0 :3035-3037）", () => {
  it("点删除只触发删除，不触发选中（stopPropagation）", () => {
    const { onDeleteRun, onSelectRun } = renderTable([run({ run_id: "a" })]);

    fireEvent.click(screen.getByRole("button", { name: "删除" }));

    expect(onDeleteRun).toHaveBeenCalledWith(expect.objectContaining({ run_id: "a" }));
    expect(onSelectRun).not.toHaveBeenCalled();
  });

  it("点行内其它位置触发选中，不触发删除", () => {
    const { onDeleteRun, onSelectRun } = renderTable([run({ run_id: "a" })]);

    fireEvent.click(screen.getByText("模型 A"));

    expect(onSelectRun).toHaveBeenCalledWith(expect.objectContaining({ run_id: "a" }));
    expect(onDeleteRun).not.toHaveBeenCalled();
  });
});

describe("分页（1.0 :2992-2997）", () => {
  it("pages > 1 才渲染分页器，页码文案与按钮翻页正确", () => {
    const { onChangeRunPage } = renderTable([run()], {
      page: 2,
      pages: 5,
      total: 42,
      has_previous: true,
      has_next: true,
    });

    expect(screen.getByText("2 / 5")).not.toBeNull();
    expect(screen.getByText("共 42 条")).not.toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "下一页" }));
    expect(onChangeRunPage).toHaveBeenCalledWith(3);
    fireEvent.click(screen.getByRole("button", { name: "上一页" }));
    expect(onChangeRunPage).toHaveBeenCalledWith(1);
  });

  it("pages = 1 不渲染分页器", () => {
    renderTable([run()], { pages: 1, has_next: false, has_previous: false });

    expect(screen.queryByRole("button", { name: "下一页" })).toBeNull();
    expect(screen.getByText("共 1 条")).not.toBeNull();
  });
});

it("初始化失败时保留未知题目数，不显示零题", () => {
  renderTable([run({ status: "failed", qa_count: null })]);
  expect(screen.getByText("—")).toBeTruthy();
  expect(screen.queryByRole("progressbar")).toBeNull();
});

it("历史扫描未完成时明确告知列表不完整", () => {
  renderTable([], { scan: { state: "running", scanned: 120 } });
  expect(screen.getByText(/历史记录正在加载（已读取 120 条），当前列表尚未完整/)).toBeTruthy();
});


it("仅构建模式不显示问答进度", () => {
  renderTable([run({ mode: "build", qa_count: 0, summary: { completed: 0, total: 0 } })]);
  expect(screen.getByText("—")).toBeTruthy();
  expect(screen.queryByRole("progressbar")).toBeNull();
});


it("评分缺失以完成但有错误显示，保留实际评分数", () => {
  renderTable([run({ status: "completed_with_errors", qa_count: 10, summary: { judge_valid_count: 9 } })]);
  expect(screen.getByText("完成但有错误")).toBeTruthy();
  expect(screen.getByText("9/10")).toBeTruthy();
  expect(screen.queryByRole("progressbar")).toBeNull();
});
