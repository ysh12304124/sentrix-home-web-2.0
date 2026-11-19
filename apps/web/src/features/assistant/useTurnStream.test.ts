import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ start: vi.fn(), cancel: vi.fn(), report: vi.fn() }));
vi.mock("../../shared/api/client", async (original) => ({
  ...await original<typeof import("../../shared/api/client")>(),
  startTurn: mocks.start,
  cancelTurn: mocks.cancel,
}));
vi.mock("../../shared/clientEvents", () => ({
  reportClientEvent: mocks.report,
  errorCodeFromStatus: () => "network",
}));

import { useTurnStream } from "./useTurnStream";

class FakeEventSource extends EventTarget {
  static instances: FakeEventSource[] = [];
  close = vi.fn();
  onerror: (() => void) | null = null;
  url: string;
  constructor(url: string) { super(); this.url = url; FakeEventSource.instances.push(this); }
  emit(type: string, data: object) { this.dispatchEvent(new MessageEvent(type, { data: JSON.stringify(data) })); }
}

beforeEach(() => {
  vi.clearAllMocks();
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
  mocks.start.mockResolvedValue({ turn_id: "turn-1" });
  mocks.cancel.mockResolvedValue(undefined);
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.useRealTimers(); });

it("接受请求立即返回 true，回答继续经 SSE 完成，不重复创建回合", async () => {
  const { result } = renderHook(useTurnStream);
  await act(async () => { expect(await result.current.ask("c1", "海边照片")).toBe(true); });
  expect(result.current.busy).toBe(true);
  const source = FakeEventSource.instances[0];
  act(() => {
    source.emit("progress", { step_index: 1, message: "正在检索" });
    source.emit("progress", { step_index: 1, message: "重复帧" });
    source.emit("complete", { turn_id: "turn-1", status: "complete", answer: "找到了", attachments: [] });
  });
  expect(result.current.progress).toHaveLength(1);
  expect(result.current.result?.answer).toBe("找到了");
  expect(result.current.busy).toBe(false);
  expect(source.close).toHaveBeenCalled();
  expect(mocks.start).toHaveBeenCalledTimes(1);
});

it("创建失败返回 false，保留错误，不自动重发", async () => {
  mocks.start.mockRejectedValueOnce(new Error("请求未确认"));
  const { result } = renderHook(useTurnStream);
  await act(async () => { expect(await result.current.ask("c1", "问题")).toBe(false); });
  expect(result.current.error).toBe("请求未确认");
  expect(result.current.busy).toBe(false);
  expect(FakeEventSource.instances).toHaveLength(0);
  expect(mocks.start).toHaveBeenCalledTimes(1);
});

it("创建期间取消，拿到回合 ID 后取消同一回合，不建立事件流", async () => {
  let accept!: (value: { turn_id: string }) => void;
  mocks.start.mockReturnValueOnce(new Promise((resolve) => { accept = resolve; }));
  const { result } = renderHook(useTurnStream);
  let asked!: Promise<boolean>;
  act(() => { asked = result.current.ask("c1", "问题"); });
  await act(() => result.current.cancel());
  await act(async () => { accept({ turn_id: "turn-1" }); expect(await asked).toBe(true); });
  expect(mocks.cancel).toHaveBeenCalledExactlyOnceWith("turn-1");
  expect(result.current.cancelled).toBe(true);
  expect(result.current.busy).toBe(false);
  expect(FakeEventSource.instances).toHaveLength(0);
});

it("SSE 断线重连同一回合且带游标，取消后不再重连", async () => {
  vi.useFakeTimers();
  const { result } = renderHook(useTurnStream);
  await act(() => result.current.ask("c1", "问题"));
  const source = FakeEventSource.instances[0];
  act(() => { source.emit("progress", { step_index: 3 }); source.onerror!(); });
  await act(() => vi.advanceTimersByTimeAsync(1600));
  expect(FakeEventSource.instances).toHaveLength(2);
  expect(FakeEventSource.instances[1].url).toContain("turn-1");
  expect(FakeEventSource.instances[1].url).toContain("3");
  await act(() => result.current.cancel());
  await act(() => vi.advanceTimersByTimeAsync(20_000));
  expect(FakeEventSource.instances).toHaveLength(2);
  expect(mocks.start).toHaveBeenCalledTimes(1);
  expect(mocks.cancel).toHaveBeenCalledExactlyOnceWith("turn-1");
});
