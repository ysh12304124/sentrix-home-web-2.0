import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useEffect, useRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Run } from "../../api/types.ts";

const { mockApi } = vi.hoisted(() => ({ mockApi: vi.fn() }));
vi.mock("../../api/client.ts", () => ({ api: mockApi, post: vi.fn() }));

import { useQaPage } from "../../hooks/useQaPage.ts";
import { QaResults } from "./QaResults.tsx";

afterEach(cleanup);

const RUN: Run = { run_id: "run1", album_id: "album3", status: "completed", rejudge: null };

/**
 * `QaResults` 现在只消费组合层传入的 `qa`（1.0 是单组件，状态天然共享）。测试用这个
 * harness 复刻组合层的最小职责：起一个 `useQaPage`，切 run 时回第 1 页重拉
 * （对应 `App.tsx` 里 `useRuns` 的 `loadQaPage`）。口径与 `RunsView.test.tsx` 的
 * `Harness`（起 `useRuns`）一致。
 */
function Harness({ activeRun }: { activeRun: Run }) {
  const activeRunId = activeRun?.run_id ?? null;
  const qa = useQaPage({ activeRunId, activeRejudge: activeRun?.rejudge ?? null });
  const loadRef = useRef(qa.loadQaPage);
  useEffect(() => {
    loadRef.current = qa.loadQaPage;
  });
  useEffect(() => {
    if (activeRunId) void loadRef.current(1);
  }, [activeRunId]);
  return <QaResults activeRun={activeRun} qa={qa} />;
}

const FACETS = {
  task_types: ["event_memory_qa"],
  tags: ["夜晚"],
  angles: ["object"],
  difficulties: ["hard"],
  answerabilities: ["answerable"],
  agent_statuses: ["completed"],
  attribution_layers: ["tool"],
};

/** 只有**全部 9 个条件**同时命中才返回那一条——少一个都不成立。 */
const REQUIRED: Record<string, string> = {
  search: "电池",
  score: "2",
  task_type: "event_memory_qa",
  tag: "夜晚",
  angle: "object",
  difficulty: "hard",
  answerability: "answerable",
  agent_status: "completed",
  primary: "tool",
};

beforeEach(() => {
  mockApi.mockReset();
  mockApi.mockImplementation((path: string) => {
    if (!path.includes("/items?")) return Promise.reject(new Error(path));
    const params = new URL(path, "http://localhost").searchParams;
    const all = Object.entries(REQUIRED).every(([key, value]) => params.get(key) === value);
    const items = all ? [{ index: 1, question: "电池续航够吗" }] : [];
    return Promise.resolve({
      items,
      page: 1,
      page_size: 20,
      total: items.length,
      pages: 1,
      unfiltered_total: 42,
      facets: FACETS,
    });
  });
});

function setSelect(placeholderOption: string, value: string): void {
  const select = screen.getByText(placeholderOption).closest("select");
  expect(select).not.toBeNull();
  fireEvent.change(select!, { target: { value } });
}

describe("QaResults · 筛选表单的 9 个条件", () => {
  it("只有 8 个下拉 + 搜索全部命中才出现结果；少一个就不出现", async () => {
    render(<Harness activeRun={RUN} />);
    await screen.findByText("展开逐题结果（0 条）");

    fireEvent.change(screen.getByPlaceholderText("搜索题号或问题"), { target: { value: "电池" } });
    setSelect("全部 Judge 分数", "2");
    setSelect("全部任务类型", "event_memory_qa");
    setSelect("全部标签", "夜晚");
    setSelect("全部问题角度", "object");
    setSelect("全部难度", "hard");
    setSelect("全部可回答性", "answerable");
    setSelect("全部 Agent 状态", "completed");
    setSelect("全部归因层", "tool");

    const form = screen.getByText("筛选").closest("form");
    expect(form).not.toBeNull();
    fireEvent.submit(form!);

    await screen.findByText("电池续航够吗");

    // 对照：撤回一个条件（难度），组合不再成立 → 结果消失。
    setSelect("全部难度", "");
    fireEvent.submit(form!);
    await waitFor(() => expect(screen.queryByText("电池续航够吗")).toBeNull());
  });
});

// 缺口 2：`activeRun?.rejudge` 必须接进 `useQaPage` 的 `activeRejudge`，否则
// `visibleQaItems`（1.0 `:355-364`）的「重评进行中」过滤不生效。两条夹具给出不同答案。
describe("QaResults · activeRejudge 过滤（1.0 visibleQaItems :355-364）", () => {
  // 两条题的 judge 都来自上一轮（`rj0`），本轮重评是 `rj1` → 都该被过滤掉。
  const STALE_ITEMS = [
    { index: 1, question: "第一题", judge: { rejudge_id: "rj0", status: "completed" } },
    { index: 2, question: "第二题", judge: { rejudge_id: "rj0", status: "completed" } },
  ];

  beforeEach(() => {
    mockApi.mockImplementation(() =>
      Promise.resolve({ items: STALE_ITEMS, page: 1, page_size: 20, total: 2, pages: 1 }),
    );
  });

  it("重评进行中且本轮暂无结果：全部被过滤 → 显示等待首条结果的提示", async () => {
    render(
      <Harness
        activeRun={{
          run_id: "run1",
          status: "running",
          rejudge: { status: "running", rejudge_id: "rj1", completed: 0, total: 2 },
        }}
      />,
    );

    expect(await screen.findByText("正在等待本轮第一条 Judge 评分结果…")).not.toBeNull();
  });

  it("没有进行中的重评：两条题都在（不显示等待提示）", async () => {
    render(<Harness activeRun={{ run_id: "run1", status: "running", rejudge: null }} />);

    await screen.findByText("展开逐题结果（2 条）");
    expect(screen.queryByText("正在等待本轮第一条 Judge 评分结果…")).toBeNull();
  });
});

it('ATM逐题保留原生题型，不展示标准筛选与PhotoBench可回答性筛选', async () => {
  render(<Harness activeRun={{...RUN,album_id:'atm-bench-smoke10-current'}}/>);
  await screen.findByText('展开逐题结果（0 条）');
  expect(screen.queryByLabelText('逐题筛分标准')).toBeNull();
  expect(screen.queryByText('全部可回答性')).toBeNull();
  expect(screen.queryByText('全部任务类型')).toBeNull();
  expect(screen.getByLabelText('评分题型')).toBeTruthy();
});
