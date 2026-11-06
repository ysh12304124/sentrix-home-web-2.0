import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/client.ts", () => ({ api: vi.fn(), post: vi.fn() }));

import { api } from "../api/client.ts";
import type { Run, RunPage } from "../api/types.ts";
import { useRuns } from "./useRuns.ts";

// 1.0 参考：`services/photobench/frontend/src/App.vue`（只读权威）。
// 轮询夹具要让「开」与「该关」给出不同答案：active_count / 行状态不同即可。

const apiMock = vi.mocked(api);

function pagePayload(activeCount: number, status = "done"): RunPage {
  return {
    runs: [{ run_id: "a", status }],
    page: 1,
    page_size: 20,
    total: 1,
    pages: 1,
    has_previous: false,
    has_next: false,
    active_count: activeCount,
  };
}

beforeEach(() => {
  apiMock.mockReset();
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

describe("loadRuns / changeRunPage（1.0 :2233 / :2249）", () => {
  it("列表旧快照不能覆盖同一补评任务的实时进度和评分汇总", async () => {
    const page = pagePayload(0, "completed_with_errors");
    page.runs[0] = { ...page.runs[0]!, rejudge: {rejudge_id: "score-a", status: "running", completed: 0, total: 1104},
      summary: {scoring: {profiles: {photobench_open_v1: {scored: 0, total: 487}}}} };
    let snapshot: Run = {...page.runs[0]!, rejudge: {...page.runs[0]!.rejudge, completed: 128},
      summary: {scoring: {profiles: {photobench_open_v1: {scored: 115, total: 487}}}}};
    apiMock.mockImplementation(async path => path.startsWith("/runs?") ? page : path.startsWith("/runs/a?") ? snapshot : {});
    const {result} = renderHook(() => useRuns());
    await act(async () => { await result.current.loadRuns(); result.current.selectRun(page.runs[0]!); await vi.advanceTimersByTimeAsync(0); });
    expect(result.current.runs[0]?.rejudge?.completed).toBe(128);
    for (let i = 0; i < 3; i++) {
      await act(async () => { await result.current.loadRuns(); });
      expect(result.current.runs[0]?.rejudge?.completed).toBe(128);
      expect(result.current.runs[0]?.summary?.scoring).toEqual(snapshot.summary?.scoring);
    }
    snapshot = {...snapshot, rejudge: {...snapshot.rejudge, completed: 129}};
    await act(async () => { await result.current.loadActiveRun(); });
    expect(result.current.runs[0]?.rejudge?.completed).toBe(129);
  });

  it("保留同任务终态，新补评ID和切换记录后重新接受列表快照", async () => {
    const page = pagePayload(0, "completed_with_errors");
    page.runs[0] = {...page.runs[0]!, rejudge: {rejudge_id: "score-a", status: "running", completed: 0, total: 1104}};
    const snapshot: Run = {...page.runs[0]!, rejudge: {...page.runs[0]!.rejudge, status: "completed", completed: 1104}, summary: {scoring: {finished: true}}};
    apiMock.mockImplementation(async path => path.startsWith("/runs?") ? page : path.startsWith("/runs/a?") ? snapshot : {});
    const {result} = renderHook(() => useRuns());
    await act(async () => { await result.current.loadRuns(); result.current.selectRun(page.runs[0]!); await vi.advanceTimersByTimeAsync(0); });
    await act(async () => { await result.current.loadRuns(); });
    expect(result.current.runs[0]?.rejudge?.status).toBe("completed");
    expect(result.current.hasRunning).toBe(false);
    page.runs[0] = {...page.runs[0]!, rejudge: {rejudge_id: "score-b", status: "running", completed: 0, total: 487}, summary: {scoring: {finished: false}}};
    await act(async () => { await result.current.loadRuns(); });
    expect(result.current.runs[0]).toEqual(page.runs[0]);
    expect(result.current.hasRunning).toBe(true);
    await act(async () => { result.current.selectRun({run_id: "b"}); await vi.advanceTimersByTimeAsync(0); });
    page.runs[0] = snapshot;
    await act(async () => { await result.current.loadRuns(); });
    expect(result.current.runs[0]).toEqual(snapshot);
  });

  it("loadRuns 照搬 timeout 8s / retries 2", async () => {
    apiMock.mockResolvedValue(pagePayload(0));
    const { result } = renderHook(() => useRuns());

    await act(async () => {
      await result.current.loadRuns(3);
    });

    expect(apiMock).toHaveBeenCalledWith("/runs?page=3&page_size=20&group_tasks=true", {
      timeoutMs: 8000,
      retries: 2,
    });
    expect(result.current.runPage.page).toBe(1);
  });

  it("changeRunPage 滚的是 hook 返回的 ref，不是全局查询", async () => {
    apiMock.mockResolvedValue(pagePayload(0));
    const { result } = renderHook(() => useRuns());
    const scrollIntoView = vi.fn();
    result.current.runsRegionRef.current = { scrollIntoView } as unknown as HTMLElement;

    await act(async () => {
      await result.current.changeRunPage(2);
    });

    expect(scrollIntoView).toHaveBeenCalledWith({ behavior: "smooth", block: "start" });
  });
});

describe("startPolling 的 2 秒轮询（1.0 :2697-2708）", () => {
  it("调用方重建详情 loader 时仍遵守两秒间隔", async () => {
    apiMock.mockResolvedValue(pagePayload(1, "running"));
    const firstLoader = vi.fn().mockResolvedValue(undefined);
    const secondLoader = vi.fn().mockResolvedValue(undefined);
    const { result, rerender } = renderHook(
      ({ loader }) => useRuns({ loadQaPage: loader }),
      { initialProps: { loader: firstLoader } },
    );
    await act(async () => {
      result.current.startPolling();
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(apiMock).toHaveBeenCalledTimes(1);
    await act(async () => { rerender({ loader: secondLoader }); });
    expect(apiMock).toHaveBeenCalledTimes(1);
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(apiMock).toHaveBeenCalledTimes(2);
  });

  it("下一轮使用新的 QA loader，渲染不会让旧请求重叠", async () => {
    apiMock.mockImplementation(async (path) => {
      if (path.startsWith("/runs?")) return pagePayload(1, "running");
      return { run_id: "a", status: "running", summary: {} };
    });
    const firstLoader = vi.fn().mockResolvedValue(undefined);
    const secondLoader = vi.fn().mockResolvedValue(undefined);
    const { result, rerender } = renderHook(
      ({ loader }) => useRuns({ loadQaPage: loader }),
      { initialProps: { loader: firstLoader } },
    );
    await act(async () => {
      result.current.selectRun({ run_id: "a", status: "running" });
      await vi.advanceTimersByTimeAsync(0);
    });
    firstLoader.mockClear();
    await act(async () => {
      result.current.startPolling();
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(firstLoader).toHaveBeenCalledTimes(1);
    await act(async () => { rerender({ loader: secondLoader }); });
    expect(secondLoader).not.toHaveBeenCalled();
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(firstLoader).toHaveBeenCalledTimes(1);
    expect(secondLoader).toHaveBeenCalledTimes(1);
  });

  it("有任务在跑：每 2 秒一轮", async () => {
    apiMock.mockResolvedValue(pagePayload(1, "running"));
    const { result } = renderHook(() => useRuns());

    await act(async () => {
      result.current.startPolling();
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(apiMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1999);
    });
    expect(apiMock).toHaveBeenCalledTimes(1); // 还没到 2s

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(apiMock).toHaveBeenCalledTimes(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(apiMock).toHaveBeenCalledTimes(3);
  });

  it("没有任务在跑：跑完一轮就停，不再续期", async () => {
    apiMock.mockResolvedValue(pagePayload(0, "done"));
    const { result } = renderHook(() => useRuns());

    await act(async () => {
      result.current.startPolling();
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(apiMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(20000);
    });
    expect(apiMock).toHaveBeenCalledTimes(1);

    // 停了之后还能再拉起（1.0 `pollTimer` 归零后可再 startPolling）。
    await act(async () => {
      result.current.startPolling();
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(apiMock).toHaveBeenCalledTimes(2);
  });

  it("卸载后不再触发", async () => {
    apiMock.mockResolvedValue(pagePayload(1, "running"));
    const { result, unmount } = renderHook(() => useRuns());

    await act(async () => {
      result.current.startPolling();
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(apiMock).toHaveBeenCalledTimes(1);

    unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(20000);
    });
    expect(apiMock).toHaveBeenCalledTimes(1);
  });

  it("重复 startPolling 不会起第二条定时器", async () => {
    apiMock.mockResolvedValue(pagePayload(1, "running"));
    const { result } = renderHook(() => useRuns());

    await act(async () => {
      result.current.startPolling();
      await vi.advanceTimersByTimeAsync(0);
    });
    await act(async () => {
      result.current.startPolling();
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(apiMock).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(apiMock).toHaveBeenCalledTimes(2);
  });
});

describe("hasRunning（1.0 :352）", () => {
  it("active_count 或行状态任一命中即为真", async () => {
    apiMock.mockResolvedValue(pagePayload(0, "pending"));
    const { result } = renderHook(() => useRuns());
    await act(async () => {
      await result.current.loadRuns(1);
    });
    expect(result.current.hasRunning).toBe(true);
  });
});


it("评分不足满额但已完成且有错误时停止轮询", async () => {
  const payload = pagePayload(0, "completed_with_errors");
  payload.runs[0] = { ...payload.runs[0]!, qa_count: 10, summary: { judge_valid_count: 9 } };
  apiMock.mockResolvedValue(payload);
  const { result } = renderHook(() => useRuns());
  await act(async () => {
    result.current.startPolling();
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(result.current.hasRunning).toBe(false);
  expect(result.current.runs[0]?.summary?.judge_valid_count).toBe(9);
  await act(async () => { await vi.advanceTimersByTimeAsync(20000); });
  expect(apiMock).toHaveBeenCalledTimes(1);
});


describe("详情读取错误的恢复", () => {
  it("同一任务读取恢复后清除旧错误，保留独立操作错误", async () => {
    let ready = false;
    apiMock.mockImplementation(async (path) => {
      if (path.startsWith("/runs?")) return pagePayload(1, "running");
      if (path.startsWith("/runs/a?")) {
        if (!ready) throw new Error("'a'");
        return { run_id: "a", status: "running", summary: {} };
      }
      return {};
    });
    const { result } = renderHook(() => useRuns());
    await act(async () => {
      result.current.selectRun({ run_id: "a", status: "running" });
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.error).toBe("读取评测详情失败：'a'");
    ready = true;
    await act(async () => {
      result.current.startPolling();
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(result.current.error).toBe("");
    await act(async () => {
      result.current.setError("补评请求失败");
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(result.current.error).toBe("补评请求失败");
  });
});


it.each([
  ["pending", 404, ""],
  ["pending", 500, "读取评测详情失败：server error"],
  ["completed", 404, "读取评测详情失败：server error"],
])("%s任务读取HTTP%d时按初始化状态区分真正的错误", async (status, code, expected) => {
  apiMock.mockImplementation(async (path) => {
    if (path.startsWith("/runs/a?")) throw Object.assign(new Error("server error"), { status: code });
    return {};
  });
  const { result } = renderHook(() => useRuns());
  await act(async () => {
    result.current.selectRun({ run_id: "a", status: String(status) });
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(result.current.error).toBe(expected);
});

it("详情读取成功同时同步列表答题和评分进度，并清除先前读取错误", async () => {
  let ready = false;
  const snapshot = { run_id: "a", status: "cancelled", qa_count: 12, summary: { judge_valid_count: 3 }, rejudge: {status:"running",completed:25,total:1104},
    phases: { qa_eval: { progress: { completed: 9, total: 12, judge_completed: 9 } } } };
  apiMock.mockImplementation(async (path) => {
    if (path.startsWith("/runs?")) return pagePayload(0, "cancelled");
    if (path.startsWith("/runs/a?")) {
      if (!ready) throw new Error("'a'");
      return snapshot;
    }
    return {};
  });
  const { result } = renderHook(() => useRuns());
  await act(async () => {
    await result.current.loadRuns();
    result.current.selectRun({ run_id: "a", status: "running" });
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(result.current.error).toContain("读取评测详情失败");
  ready = true;
  await act(async () => { await result.current.loadActiveRun(); });
  expect(result.current.error).toBe("");
  expect(result.current.runs[0]).toMatchObject(snapshot);
});
