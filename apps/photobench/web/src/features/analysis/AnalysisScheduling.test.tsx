import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { api } from "../../api/client.ts";
import { AnalysisScheduling } from "./AnalysisScheduling.tsx";
vi.mock("../../api/client.ts", () => ({ api: vi.fn() }));
afterEach(() => { cleanup(); vi.useRealTimers(); vi.resetAllMocks(); });

it("显示阻塞测评及自动继续，实时清除已结束的阻塞", async () => {
  vi.useFakeTimers();
  vi.mocked(api).mockResolvedValueOnce({ work: [
    { id: "run-487", kind: "run", worker_alive: true },
    { id: "ai-10", kind: "analysis", worker_alive: false },
  ], cleanup_status: "pending" }).mockResolvedValue({ work: [
    { id: "ai-10", kind: "analysis", worker_alive: true },
  ], cleanup_status: "pending" });
  await act(async () => { render(<AnalysisScheduling jobId="ai-10" status="queued" />); });
  expect(screen.getByText(/AI 分析会在测评及后台任务结束后自动继续/)).toBeTruthy();
  expect(screen.getByText("当前任务：run-487")).toBeTruthy();
  expect(screen.getByText("本次分析尚未调用模型。")).toBeTruthy();
  await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
  expect(screen.queryByText(/当前任务/)).toBeNull();
  expect(screen.getByText("分析已排队，等待工作进程启动。")).toBeTruthy();
  cleanup();
  await act(async () => { await vi.advanceTimersByTimeAsync(6000); });
  expect(api).toHaveBeenCalledTimes(2);
});

it("区分分析任务排队与测评阻塞", async () => {
  vi.mocked(api).mockResolvedValue({ work: [
    { id: "second", kind: "analysis", worker_alive: false },
    { id: "first", kind: "analysis", worker_alive: true },
  ], cleanup_status: "pending" });
  await act(async () => { render(<AnalysisScheduling jobId="second" status="queued" />); });
  expect(screen.getByText("等待前面的 1 个分析任务结束，随后自动开始。")).toBeTruthy();
  expect(screen.queryByText(/等待测评结束/)).toBeNull();
});

it("读取失败时提示无法确认原因", async () => {
  vi.mocked(api).mockRejectedValue(new Error("offline"));
  await act(async () => { render(<AnalysisScheduling jobId="ai" status="queued" />); });
  expect(screen.getByRole("alert").textContent).toContain("等待原因读取失败");
  expect(screen.queryByText(/本次分析尚未调用模型/)).toBeNull();
});
