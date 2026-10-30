import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../api/client.ts", () => ({ api: vi.fn(), post: vi.fn() }));

import { api, post } from "../api/client.ts";
import type { Run } from "../api/types.ts";
import { useRunDetail } from "./useRunDetail.ts";
import type { ActiveRunIdRef } from "./useRunDetail.ts";

// 1.0 参考：`services/photobench/frontend/src/App.vue`（只读权威）。
// 夹具原则：每组的两种口径必须给出不同答案（例如「并发」与「串行」、「可重评」与「不可重评」）。

const apiMock = vi.mocked(api);
const postMock = vi.mocked(post);

function runDetail(overrides: Partial<Run> = {}): Run {
  return { run_id: "r1", item_count: 3, status: "done", ...overrides };
}

function mount(activeRunId: string | null = "r1") {
  const ref: ActiveRunIdRef = { current: activeRunId };
  const hook = renderHook(() =>
    useRunDetail({ activeRunId, activeRunIdRef: ref, setError: () => {} }),
  );
  return { ...hook, ref };
}

beforeEach(() => {
  apiMock.mockReset();
  apiMock.mockResolvedValue({});
  postMock.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("loadActiveRun 的并发关系（1.0 :2310-2332）", () => {
  it("run 详情**串行**在前：详情失败时 keyframe / memory 根本不发", async () => {
    apiMock.mockImplementation(async (path: string) => {
      if (path === "/runs/r2?include_history=true") throw new Error("detail boom");
      throw new Error(`不该请求：${path}`);
    });
    const { result, ref } = mount("r1");
    ref.current = "r2";

    await act(async () => {
      await expect(result.current.loadActiveRun()).rejects.toThrow("detail boom");
    });

    // 只有一条请求：串行闸门挡住了后面的并发集。若改成 Promise.all 全并发，这里会是 3 条。
    expect(apiMock).toHaveBeenCalledTimes(1);
    expect(apiMock.mock.calls[0]?.[0]).toBe("/runs/r2?include_history=true");
  });

  it("keyframe 失败**不影响** memory（两者是 Promise.all，各自吞异常）", async () => {
    apiMock.mockImplementation(async (path: string) => {
      if (path.startsWith("/runs/r1?include_history=")) return runDetail();
      if (path.endsWith("/keyframe-analysis")) throw new Error("kf boom");
      if (path.endsWith("/memory-effectiveness")) return { status: "ready", levels: [] };
      throw new Error(`不该请求：${path}`);
    });
    const { result } = mount("r1");

    await act(async () => {
      result.current.setAnalysisOpen(true);
      await result.current.loadActiveRun();
    });

    expect(result.current.keyframeAnalysisError).toBe("kf boom");
    expect(result.current.keyframeAnalysis).toBeNull();
    // 若 keyframe 的异常决定性地中断了流程，memory 就不会被写上。
    expect(result.current.memoryEffectiveness).toEqual({ status: "ready", levels: [] });
    // 并发集确实都发了。
    expect(apiMock.mock.calls.map((call) => call[0])).toEqual([
      "/runs/r1/keyframe-analysis",
      "/runs/r1/memory-effectiveness",
      "/runs/r1/graph-quality",
      "/runs/r1?include_history=true",
      "/runs/r1/keyframe-analysis",
      "/runs/r1/memory-effectiveness",
      "/runs/r1/graph-quality",
    ]);
  });

  it("memory 失败也不影响 keyframe（反方向，且失败落成 unavailable 包体）", async () => {
    apiMock.mockImplementation(async (path: string) => {
      if (path.startsWith("/runs/r1?include_history=")) return runDetail();
      if (path.endsWith("/keyframe-analysis")) return { status: "ready" };
      if (path.endsWith("/memory-effectiveness")) throw new Error("mem boom");
      throw new Error(`不该请求：${path}`);
    });
    const { result } = mount("r1");

    await act(async () => {
      result.current.setAnalysisOpen(true);
      await result.current.loadActiveRun();
    });

    expect(result.current.keyframeAnalysis).toEqual({ status: "ready" });
    expect(result.current.memoryEffectiveness).toEqual({
      status: "unavailable",
      error: "mem boom",
    });
  });

  it("run 详情走 60s 超时（1.0 :2313）", async () => {
    apiMock.mockImplementation(async () => runDetail());
    const { result } = mount("r1");
    await act(async () => {
      await result.current.loadActiveRun();
    });
    expect(apiMock.mock.calls[0]?.[1]).toEqual(expect.objectContaining({ timeoutMs: 60000 }));
  });

  it("resetPage 串行拉 reviews 并填充 reviewDrafts", async () => {
    apiMock.mockImplementation(async (path: string) => {
      if (path.startsWith("/runs/r1?include_history=")) return runDetail();
      if (path === "/runs/r1/reviews") return { reviews: { q1: { verdict: "correct" } } };
      if (path.endsWith("/keyframe-analysis")) return {};
      if (path.endsWith("/memory-effectiveness")) return {};
      throw new Error(`不该请求：${path}`);
    });
    const { result } = mount("r1");

    await act(async () => {
      await result.current.loadActiveRun({ resetPage: true });
    });

    expect(result.current.reviewFor("q1")).toEqual({ verdict: "correct" });
    // reviews 在并发集之前（串行）。
    expect(apiMock.mock.calls[1]?.[0]).toBe("/runs/r1/reviews");
  });
});

describe("resetQaState / loadQaPage 接线（1.0 :2322-2331）", () => {
  function mountWithQa() {
    apiMock.mockImplementation(async (path: string) => {
      if (path.startsWith("/runs/r1?include_history=")) return runDetail();
      if (path === "/runs/r1/reviews") return { reviews: {} };
      if (path.endsWith("/keyframe-analysis")) return {};
      if (path.endsWith("/memory-effectiveness")) return {};
      throw new Error(`不该请求：${path}`);
    });
    const ref: ActiveRunIdRef = { current: "r1" };
    const resetQaState = vi.fn();
    const loadQaPage = vi.fn(async (_page?: number) => {});
    const hook = renderHook(() =>
      useRunDetail({ activeRunId: "r1", activeRunIdRef: ref, setError: () => {}, resetQaState, loadQaPage }),
    );
    return { ...hook, resetQaState, loadQaPage };
  }

  it("resetPage:true 调 resetQaState 且 loadQaPage(1)；不带 resetPage 时不 reset", async () => {
    const { result, resetQaState, loadQaPage } = mountWithQa();

    await act(async () => {
      await result.current.loadActiveRun({ resetPage: true });
    });
    expect(resetQaState).toHaveBeenCalledTimes(1);
    expect(loadQaPage).toHaveBeenCalledWith(1);

    await act(async () => {
      await result.current.loadActiveRun();
    });
    expect(resetQaState).toHaveBeenCalledTimes(1); // 没再 reset
    expect(loadQaPage).toHaveBeenLastCalledWith(undefined); // 当前页
  });
});

describe("exportTraces / exportSftTraces（1.0 :2567 / backup :1401）", () => {
  it("window.open 带正确的 scores 参数", () => {
    const openSpy = vi.spyOn(window, "open").mockReturnValue(null);
    const { result } = mount("r1");

    act(() => {
      result.current.setExportScores(["1", "2"]);
    });
    act(() => {
      result.current.exportTraces();
    });
    expect(openSpy).toHaveBeenCalledWith("/api/v1/runs/r1/export-trace?scores=1,2", "_blank");

    openSpy.mockClear();
    act(() => {
      result.current.exportSftTraces();
    });
    expect(openSpy).toHaveBeenCalledWith("/api/v1/runs/r1/export-sft?scores=1,2", "_blank");
  });

  it("默认评分是 0,1,2（1.0 :2487）", () => {
    const openSpy = vi.spyOn(window, "open").mockReturnValue(null);
    const { result } = mount("r1");
    act(() => {
      result.current.exportTraces();
    });
    expect(openSpy).toHaveBeenCalledWith("/api/v1/runs/r1/export-trace?scores=0,1,2", "_blank");
  });

  it("一个都没勾选时不下载，改为提示", () => {
    const openSpy = vi.spyOn(window, "open").mockReturnValue(null);
    const alertSpy = vi.spyOn(window, "alert").mockImplementation(() => {});
    const { result } = mount("r1");
    act(() => {
      result.current.setExportScores([]);
    });
    act(() => {
      result.current.exportTraces();
    });
    expect(openSpy).not.toHaveBeenCalled();
    expect(alertSpy).toHaveBeenCalledWith("请至少勾选一个评分再导出");
  });

  it("没有 activeRunId 时什么都不做", () => {
    const openSpy = vi.spyOn(window, "open").mockReturnValue(null);
    const { result } = mount(null);
    act(() => {
      result.current.exportTraces();
      result.current.exportSftTraces();
    });
    expect(openSpy).not.toHaveBeenCalled();
  });
});

describe("canRejudge / rejudgePercent（1.0 :365-374）", () => {
  it("可重评：有 item_count + 非 running + 提示词非空", () => {
    const { result } = mount("r1");
    act(() => {
      result.current.setActiveRun(
        runDetail({ item_count: 5, status: "done", rejudge: { status: "completed", total: 4, completed: 1 } }),
      );
      result.current.setRejudgePrompt("你是 judge");
    });
    expect(result.current.canRejudge).toBe(true);
    expect(result.current.rejudgePercent).toBe(25);
  });

  it("不可重评：run 正在 running", () => {
    const { result } = mount("r1");
    act(() => {
      result.current.setActiveRun(runDetail({ item_count: 5, status: "running" }));
      result.current.setRejudgePrompt("你是 judge");
    });
    expect(result.current.canRejudge).toBe(false);
  });

  it("不可重评：重评任务正在 running（即便 run 本身 done）", () => {
    const { result } = mount("r1");
    act(() => {
      result.current.setActiveRun(
        runDetail({ item_count: 5, status: "done", rejudge: { status: "running", total: 4, completed: 2 } }),
      );
      result.current.setRejudgePrompt("你是 judge");
    });
    expect(result.current.canRejudge).toBe(false);
    expect(result.current.rejudgePercent).toBe(50);
  });

  it("不可重评：提示词为空（这一条单独就能否掉）", () => {
    const { result } = mount("r1");
    act(() => {
      result.current.setActiveRun(runDetail({ item_count: 5, status: "done" }));
      result.current.setRejudgePrompt("");
    });
    expect(result.current.canRejudge).toBe(false);
  });

  it("requestRejudge 只置「待确认动作」，confirmRejudge 才 POST", async () => {
    postMock.mockResolvedValue({});
    apiMock.mockImplementation(async () => runDetail());
    const { result } = mount("r1");
    act(() => {
      result.current.setActiveRun(runDetail({ item_count: 7, status: "done" }));
      result.current.setRejudgePrompt("p");
    });

    act(() => {
      result.current.requestRejudge();
    });
    expect(result.current.rejudgeConfirmation?.message).toContain("7 条 Agent 回答");
    expect(postMock).not.toHaveBeenCalled();

    await act(async () => {
      await result.current.confirmRejudge({ judge_url: " u ", judge_model: "m" });
    });
    expect(result.current.rejudgeConfirmation).toBeNull();
    expect(postMock).toHaveBeenCalledWith(
      "/runs/r1/rejudge",
      expect.objectContaining({ judge_url: "u", judge_model: "m", system_prompt: "p" }),
    );
  });
});

describe("reviewFor / saveReviews（1.0 :2333-2345）", () => {
  it("reviewFor 缺省返回空草稿；saveReviews 只提交有 verdict 的", async () => {
    postMock.mockResolvedValue({});
    apiMock.mockImplementation(async () => runDetail());
    const { result } = mount("r1");

    expect(result.current.reviewFor({ qa_id: "q9" })).toEqual({ verdict: "", note: "" });

    act(() => {
      result.current.setReview("q1", { verdict: "correct" });
      result.current.setReview("q2", { note: "只写备注" });
    });

    await act(async () => {
      await result.current.saveReviews();
    });

    expect(postMock).toHaveBeenCalledWith("/runs/r1/reviews", {
      reviews: { q1: { verdict: "correct", note: "" } },
    });
  });
});

it("首次自动读取全程，不触发按需分析", async () => {
  apiMock.mockResolvedValue(runDetail({ history_mode: "full" }));
  const { result } = mount("r1");
  await act(async () => { await result.current.loadActiveRun(); });
  expect(apiMock.mock.calls.map(([path]) => path)).toEqual(["/runs/r1?include_history=true"]);
});

it("主动刷新覆盖先发详情，旧请求晚返回也不能改回页面", async () => {
  let finishFirst!: (value: Run) => void;
  apiMock.mockImplementationOnce(() => new Promise<Run>((resolve) => { finishFirst = resolve; }))
    .mockResolvedValueOnce(runDetail({ status: "completed", history_mode: "full" }));
  const { result } = mount("r1");
  let first!: Promise<void>;
  await act(async () => {
    first = result.current.loadActiveRun();
    await result.current.loadActiveRun({ includeHistory: true });
  });
  await act(async () => { finishFirst(runDetail({ status: "running" })); await first; });
  expect(result.current.activeRun?.status).toBe("completed");
});

it("轮询不取消正在读取的全程曲线", async () => {
  let finish!: (value: Run) => void;
  apiMock.mockImplementationOnce(() => new Promise<Run>((resolve) => { finish = resolve; }));
  const { result } = mount("r1");
  let loading!: Promise<void>;
  await act(async () => { loading = result.current.loadActiveRun(); await result.current.loadActiveRun(); });
  expect(apiMock).toHaveBeenCalledTimes(1);
  await act(async () => { finish(runDetail({ history_mode: "full" })); await loading; });
  expect(result.current.activeRun?.history_mode).toBe("full");
});

it("跨版本轮询保留开头，追加新点与真实缺口，卡片和阶段保持最新", async () => {
  apiMock.mockResolvedValueOnce(runDetail({ status: "running", history_mode: "full", read_revision: "one",
    telemetry_live: { samples_count: 500, history: [{ t: 10 }, { t: 500 }] } }));
  const { result } = mount();
  await act(async () => { await result.current.loadActiveRun(); });
  apiMock.mockResolvedValueOnce(runDetail({ status: "running", history_mode: "checkpoint", read_revision: "two",
    phases: { qa_eval: { status: "running" } }, telemetry_live: { samples_count: 502,
      latest: { system_memory_used_mib: 42 }, history: [{ t: 499 }, { t: 500 }, { t: 501 }, { t: 505 }] } }));
  await act(async () => { await result.current.loadActiveRun(); });
  expect(apiMock.mock.calls[1]?.[0]).toBe("/runs/r1?include_history=false");
  const value = result.current.activeRun!;
  expect(value.history_mode).toBe("full");
  expect(value.read_revision).toBe("two");
  expect(value.telemetry_live?.history).toEqual([{ t: 10 }, { t: 500 }, { t: 501 }, { t: 505, gap_before: true }]);
  expect(value.telemetry_live?.latest?.system_memory_used_mib).toBe(42);
  expect(value.phases?.qa_eval?.status).toBe("running");
});

it("定期重读全程控制曲线点数，终态自动补齐", async () => {
  const clock = vi.spyOn(Date, "now").mockReturnValue(1000);
  const { result } = mount();
  apiMock.mockResolvedValue(runDetail({ status: "running", history_mode: "full", telemetry_live: { history: [{ t: 0 }, { t: 9 }] } }));
  await act(async () => { await result.current.loadActiveRun(); });
  clock.mockReturnValue(16000);
  await act(async () => { await result.current.loadActiveRun(); });
  expect(apiMock.mock.calls[1]?.[0]).toBe("/runs/r1?include_history=true");
  apiMock.mockResolvedValueOnce(runDetail({ status: "completed", history_mode: "checkpoint" }))
    .mockResolvedValueOnce(runDetail({ status: "completed", history_mode: "full", telemetry_live: { history: [{ t: 0 }, { t: 20 }] } }));
  await act(async () => { await result.current.loadActiveRun(); });
  expect(apiMock.mock.calls.filter(([path]) => !path.endsWith("/recovery-plan")).slice(-2).map(([path]) => path)).toEqual(["/runs/r1?include_history=false", "/runs/r1?include_history=true"]);
  expect(result.current.activeRun?.telemetry_live?.history).toEqual([{ t: 0 }, { t: 20 }]);
});

it("后台暂停错过窗口时补读全程，切换测评不会混入旧曲线", async () => {
  const { result, ref } = mount();
  apiMock.mockResolvedValueOnce(runDetail({ status: "running", history_mode: "full", telemetry_live: { history: [{ t: 0 }, { t: 20 }] } }));
  await act(async () => { await result.current.loadActiveRun(); });
  apiMock.mockResolvedValueOnce(runDetail({ status: "running", history_mode: "checkpoint", telemetry_live: { history: [{ t: 200 }, { t: 300 }] } }))
    .mockResolvedValueOnce(runDetail({ status: "running", history_mode: "full", telemetry_live: { history: [{ t: 0 }, { t: 300 }] } }));
  await act(async () => { await result.current.loadActiveRun(); });
  expect(apiMock.mock.calls.filter(([path]) => !path.endsWith("/recovery-plan")).slice(-2).map(([path]) => path)).toEqual(["/runs/r1?include_history=false", "/runs/r1?include_history=true"]);
  ref.current = "r2";
  apiMock.mockResolvedValueOnce(runDetail({ run_id: "r2", status: "running", history_mode: "full", telemetry_live: { history: [{ t: 50 }, { t: 60 }] } }));
  await act(async () => { await result.current.loadActiveRun(); });
  expect(apiMock.mock.calls.at(-1)?.[0]).toBe("/runs/r2?include_history=true");
  expect(result.current.activeRun?.telemetry_live?.history).toEqual([{ t: 50 }, { t: 60 }]);
});

it("切换测评后迟到的旧详情不会覆盖新测评或污染后续历史读取", async () => {
  let finish!: (value: Run) => void;
  apiMock.mockImplementationOnce(() => new Promise<Run>(resolve => { finish = resolve; }));
  const { result, ref } = mount();
  let first!: Promise<void>;
  await act(async () => { first = result.current.loadActiveRun(); });
  ref.current = "r2";
  apiMock.mockResolvedValueOnce(runDetail({ run_id: "r2", status: "running", history_mode: "full",
    telemetry_live: { history: [{ t: 100 }, { t: 200 }] } }));
  await act(async () => { await result.current.loadActiveRun(); });
  await act(async () => {
    finish(runDetail({ status: "running", history_mode: "full", telemetry_live: { history: [{ t: 0 }, { t: 999 }] } }));
    await first;
  });
  expect(result.current.activeRun?.run_id).toBe("r2");
  apiMock.mockResolvedValueOnce(runDetail({ run_id: "r2", status: "running", history_mode: "checkpoint",
    telemetry_live: { history: [{ t: 200 }, { t: 201 }] } }));
  await act(async () => { await result.current.loadActiveRun(); });
  expect(apiMock.mock.calls.at(-1)?.[0]).toBe("/runs/r2?include_history=false");
  expect(result.current.activeRun?.telemetry_live?.history).toEqual([{ t: 100 }, { t: 200 }, { t: 201 }]);
});

it("确认时使用已冻结的范围/GT模式/提示词，重复确认只发一次", async () => {
  apiMock.mockResolvedValue(runDetail());
  postMock.mockResolvedValue({});
  const {result}=mount();
  await act(async()=>{await result.current.loadActiveRun(); result.current.setRejudgePrompt("original");});
  act(()=>{result.current.setRejudgeScope("failed");result.current.setJudgeUseGtImages(true);});
  act(()=>{result.current.requestRejudge({judge_model:" old-model "});});
  act(()=>{result.current.setRejudgeScope("all");result.current.setJudgeUseGtImages(false);result.current.setRejudgePrompt("changed");});
  await act(async()=>{await Promise.all([result.current.confirmRejudge({judge_model:"new-model"}),result.current.confirmRejudge()]);});
  expect(postMock).toHaveBeenCalledTimes(1);
  expect(postMock).toHaveBeenCalledWith("/runs/r1/rejudge",expect.objectContaining({scope:"failed",judge_use_gt_images:true,system_prompt:"original",judge_model:"old-model"}));
});

it("切换任务后不能把旧确认提交给新任务", async()=>{
  apiMock.mockResolvedValue(runDetail());
  const {result,ref}=mount();
  await act(async()=>{await result.current.loadActiveRun(); result.current.setRejudgePrompt("p");});
  act(()=>{result.current.requestRejudge();});
  ref.current="r2";
  await act(async()=>{await result.current.confirmRejudge();});
  expect(postMock).not.toHaveBeenCalled();
});
