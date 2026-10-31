import { act, cleanup, fireEvent, render, renderHook, screen } from "@testing-library/react";
import { useEffect, useRef } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../api/client.ts", () => ({ api: vi.fn(), post: vi.fn() }));

import { api } from "../../api/client.ts";
import type { Run } from "../../api/types.ts";
import { useRuns } from "../../hooks/useRuns.ts";
import { RunsView } from "./RunsView.tsx";

// 这一组测的是 RunsView 直接消费的数据层 `useRuns`（视图与它同一来源）：
//   - 导出 URL 的分数参数（1.0 `exportTraces` / 最老备份 `exportSftTraces`）；
//   - `deleteRun` 的可注入 `confirm`：确认/取消对数据的影响必须不同。

const apiMock = vi.mocked(api);

function jsonResponse(status: number, body: unknown) {
  const text = JSON.stringify(body);
  return {
    ok: status >= 200 && status < 300,
    status,
    text: async () => text,
    json: async () => JSON.parse(text),
  };
}

function emptyPage() {
  return {
    runs: [] as Run[],
    page: 1,
    page_size: 20,
    total: 0,
    pages: 1,
    has_previous: false,
    has_next: false,
    active_count: 0,
  };
}

beforeEach(() => {
  apiMock.mockReset();
  apiMock.mockImplementation(async (path: string) => {
    if (path.startsWith("/runs?")) return emptyPage();
    return {};
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/** 组合层（`App.tsx`）持有 `useRuns`；视图只接它的数据层。 */
function Harness() {
  const runs = useRuns();
  return <RunsView runs={runs} judgeModel="" saveJudgePrompt={() => {}} />;
}

describe("RunsView 装配", () => {
  it("渲染评测记录表与详情空态", () => {
    render(<Harness />);
    expect(screen.getByText("评测记录")).not.toBeNull();
    expect(screen.getByText("点击上方列表中的某条记录查看详情")).not.toBeNull();
  });

  it("完整详情从中断任务切到完成任务时卸载旧恢复卡片", () => {
    const { result } = renderHook(() => useRuns());
    const interrupted = { run_id: "old1171", status: "failed", item_count: 697, model_name: "old-model" };
    const completed = { run_id: "smoke12", status: "completed", item_count: 12, model_name: "new-model" };
    const recoveryPlan = {
      run_id: "old1171", can_resume: true, remaining_qa_count: 474, inherited_qa_count: 697,
      product_version_changed: false, judge_use_gt_images: false,
      scoring: Object.fromEntries(["missing", "failed", "unscored", "all"].map(key =>
        [key, { qa_count: 0, turn_count: 0, subscore_count: 0 }])) as import("../../hooks/useRunDetail.ts").RecoveryPlan["scoring"],
    };
    const { rerender } = render(<RunsView runs={{ ...result.current, activeRunId: interrupted.run_id,
      activeRun: interrupted, recoveryPlan }} judgeModel="" saveJudgePrompt={() => {}} />);
    expect(screen.getByText("继续中断的测评")).toBeTruthy();
    rerender(<RunsView runs={{ ...result.current, activeRunId: completed.run_id,
      activeRun: completed, recoveryPlan }} judgeModel="" saveJudgePrompt={() => {}} />);
    expect(screen.queryByText("继续中断的测评")).toBeNull();
    expect(screen.getByRole("heading", { level: 2, name: /new-model/ })).toBeTruthy();
  });
});

describe("导出 URL 的分数参数", () => {
  it("0/1/2 三种勾选组合生成三个不同的 URL", async () => {
    const openSpy = vi.spyOn(window, "open").mockImplementation(() => null);
    const { result } = renderHook(() => useRuns());
    await act(async () => {
      result.current.selectRun({ run_id: "r1" });
    });

    const combos: Array<[string[], string]> = [
      [["0"], "/api/v1/runs/r1/export-trace?scores=0"],
      [["1"], "/api/v1/runs/r1/export-trace?scores=1"],
      [["1", "2"], "/api/v1/runs/r1/export-trace?scores=1,2"],
    ];
    for (const [scores, expected] of combos) {
      act(() => result.current.setExportScores(scores));
      act(() => result.current.exportTraces());
      expect(openSpy).toHaveBeenLastCalledWith(expected, "_blank");
    }
    const urls = openSpy.mock.calls.map((call) => call[0]);
    expect(new Set(urls).size).toBe(3);
  });

  it("导出 SFT JSON 走 /export-sft，分数参数与勾选一致", async () => {
    const openSpy = vi.spyOn(window, "open").mockImplementation(() => null);
    const { result } = renderHook(() => useRuns());
    await act(async () => {
      result.current.selectRun({ run_id: "r1" });
    });

    act(() => result.current.setExportScores(["1", "2"]));
    act(() => result.current.exportSftTraces());

    expect(openSpy).toHaveBeenCalledWith("/api/v1/runs/r1/export-sft?scores=1,2", "_blank");
  });
});

// P1：`saveJudgePrompt` 必须拿到**运行详情侧**（用户刚编辑的）那份提示词，而不是某个
// hook 内部恒为默认值的副本。这里走完整链路：RejudgeCard → RunsView → 组合层回调。
describe("保存提示词用的是用户编辑的那份（P1）", () => {
  const PROMPT_KINDS = {
    kinds: [
      { kind: "answer_quality", default: "AQ-DEFAULT", custom: "" },
      { kind: "evidence", default: "EV-DEFAULT", custom: "" },
    ],
  };

  function PromptHarness({ onSave }: { onSave: (kind: string, prompt: string) => void }) {
    const runs = useRuns();
    const started = useRef(false);
    useEffect(() => {
      if (started.current) return;
      started.current = true;
      void runs.loadJudgePrompts();
      runs.selectRun({ run_id: "r1", mode: "reuse", status: "done" });
    }, [runs]);
    return <RunsView runs={runs} judgeModel="jm" saveJudgePrompt={onSave} />;
  }

  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn((url: string) => {
        if (url.endsWith("/api/v1/judge-prompts")) return Promise.resolve(jsonResponse(200, PROMPT_KINDS));
        return Promise.reject(new Error(`unexpected fetch ${url}`));
      }),
    );
  });

  it("编辑后点保存：回调收到的是编辑后的值 + 当前 kind", async () => {
    const onSave = vi.fn();
    render(<PromptHarness onSave={onSave} />);

    const textarea = await screen.findByDisplayValue("AQ-DEFAULT");

    fireEvent.change(textarea, { target: { value: "我编辑过的回答质量提示词" } });
    fireEvent.click(screen.getByRole("button", { name: "保存提示词" }));

    expect(onSave).toHaveBeenCalledWith("answer_quality", "我编辑过的回答质量提示词");
  });

  it("切到证据核验后再保存：kind 与草稿都跟着切", async () => {
    const onSave = vi.fn();
    render(<PromptHarness onSave={onSave} />);
    await screen.findByDisplayValue("AQ-DEFAULT");

    fireEvent.click(screen.getByRole("button", { name: /证据核验/ }));
    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
    expect(textarea.value).toBe("EV-DEFAULT");
    fireEvent.change(textarea, { target: { value: "证据核验改过" } });
    fireEvent.click(screen.getByRole("button", { name: "保存提示词" }));

    expect(onSave).toHaveBeenCalledWith("evidence", "证据核验改过");
  });
});

describe("deleteRun 的可注入 confirm", () => {
  it("confirm 返回 false 不删（零请求）；返回 true 才真删（DELETE + 重载列表）", async () => {
    const { result } = renderHook(() => useRuns());
    await act(async () => {
      result.current.selectRun({ run_id: "r1" });
    });
    apiMock.mockClear();

    await act(async () => {
      await result.current.deleteRun({ run_id: "r1" }, () => false);
    });
    expect(apiMock).not.toHaveBeenCalled();

    await act(async () => {
      await result.current.deleteRun({ run_id: "r1" }, () => true);
    });
    const calls = apiMock.mock.calls;
    expect(
      calls.some(([path, options]) => path === "/runs/r1" && (options as { method?: string })?.method === "DELETE"),
    ).toBe(true);
    expect(calls.some(([path]) => String(path).startsWith("/runs?page="))).toBe(true);
  });
});
