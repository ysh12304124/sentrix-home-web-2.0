import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ ask: vi.fn(), resume: vi.fn(), start: vi.fn(), stop: vi.fn(), cancel: vi.fn(),
  phase: "idle", active: false, transcript: null as null | ((text: string, autoSend: boolean) => Promise<string>) }));
vi.mock("./useSpeechInput", () => ({ useSpeechInput: (options: { onTranscript: (text: string, autoSend: boolean) => Promise<string> }) => {
  mocks.transcript = options.onTranscript;
  return { ...mocks, message: "", error: null, unavailable: null };
} }));
vi.mock("../useTurnStream", () => ({ waitForTurnAcceptance: async () => {}, useTurnStream: () => ({
  progress: [], result: null, busy: false, error: null, ask: mocks.ask,
  cancel: vi.fn(), resume: mocks.resume, cancelled: false, executionState: "", attemptNo: 0, turnId: "",
}) }));
vi.mock("../../../shared/api/client", async (original) => ({
  ...await original<typeof import("../../../shared/api/client")>(),
  getConversation: vi.fn().mockResolvedValue({ messages: [], turns: [], has_more: false }),
}));

import { AssistantPanel } from "../AssistantPanel";

beforeEach(() => { vi.clearAllMocks(); mocks.phase = "idle"; mocks.active = false; mocks.ask.mockResolvedValue(true); });
afterEach(cleanup);

it("手动停止后自动发送合成草稿，成功后清空", async () => {
  render(<AssistantPanel conversationId="c1" scopeId="album1" />);
  const input = screen.getByRole("textbox", { name: "向助手提问" }) as HTMLInputElement;
  await waitFor(() => expect(input.disabled).toBe(false));
  fireEvent.change(input, { target: { value: "请帮我" } });
  fireEvent.click(screen.getByRole("button", { name: "语音输入" }));
  expect(mocks.start).toHaveBeenCalledTimes(1);
  expect(mocks.ask).not.toHaveBeenCalled();
  await act(async () => { await mocks.transcript!("找一下去年旅行的照片。", true); });
  expect(input.value).toBe("");
  expect(mocks.ask).toHaveBeenCalledExactlyOnceWith("c1", "请帮我 找一下去年旅行的照片。", false);
});

it("录音时阻止 Enter/表单提交，支持停止和取消", async () => {
  mocks.phase = "recording"; mocks.active = true;
  render(<AssistantPanel conversationId="c1" scopeId="album1" />);
  await waitFor(() => expect(screen.queryByText("正在加载历史消息…")).toBeNull());
  const input = screen.getByRole("textbox", { name: "向助手提问" }) as HTMLInputElement;
  expect(input.disabled).toBe(true);
  fireEvent.submit(input.closest("form")!);
  expect(mocks.ask).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "停止并识别" }));
  fireEvent.click(screen.getByRole("button", { name: "取消语音" }));
  expect(mocks.stop).toHaveBeenCalledTimes(1);
  expect(mocks.cancel).toHaveBeenCalledTimes(1);
});

it("语音自动发送使用当前推理模式开关", async () => {
  render(<AssistantPanel conversationId="c1" scopeId="album1" />);
  const input = screen.getByRole("textbox", { name: "向助手提问" }) as HTMLInputElement;
  await waitFor(() => expect(input.disabled).toBe(false));
  fireEvent.click(screen.getByRole("button", { name: "推理模式：关" }));
  await act(async () => { await mocks.transcript!("查找旅行照片", true); });
  expect(mocks.ask).toHaveBeenCalledExactlyOnceWith("c1", "查找旅行照片", true);
});

it("超时只填回草稿，发送失败保留文字，同步重复提交只请求一次", async () => {
  render(<AssistantPanel conversationId="c1" scopeId="album1" />);
  const input = screen.getByRole("textbox", { name: "向助手提问" }) as HTMLInputElement;
  await waitFor(() => expect(input.disabled).toBe(false));
  await act(async () => { await mocks.transcript!("超时文字", false); });
  expect(input.value).toBe("超时文字");
  expect(mocks.ask).not.toHaveBeenCalled();
  mocks.ask.mockResolvedValue(false);
  await act(async () => {
    fireEvent.submit(input.closest("form")!);
    fireEvent.submit(input.closest("form")!);
  });
  expect(mocks.ask).toHaveBeenCalledTimes(1);
  expect(input.value).toBe("超时文字");
});

it("旧请求完成不清空用户后来编辑的草稿", async () => {
  let accept!: (accepted: boolean) => void;
  mocks.ask.mockReturnValue(new Promise<boolean>((resolve) => { accept = resolve; }));
  render(<AssistantPanel conversationId="c1" scopeId="album1" />);
  const input = screen.getByRole("textbox", { name: "向助手提问" }) as HTMLInputElement;
  await waitFor(() => expect(input.disabled).toBe(false));
  let submitted!: Promise<string>;
  act(() => { submitted = mocks.transcript!("已发送的问题", true); });
  fireEvent.change(input, { target: { value: "下一条草稿" } });
  await act(async () => { accept(true); await submitted; });
  expect(input.value).toBe("下一条草稿");
});

it("自动发送失败时保留识别文字并返回明确提示", async () => {
  mocks.ask.mockResolvedValue(false);
  render(<AssistantPanel conversationId="c1" scopeId="album1" />);
  const input = screen.getByRole("textbox", { name: "向助手提问" }) as HTMLInputElement;
  await waitFor(() => expect(input.disabled).toBe(false));
  let message = "";
  await act(async () => { message = await mocks.transcript!("保留这个问题", true); });
  expect(mocks.ask).toHaveBeenCalledExactlyOnceWith("c1", "保留这个问题", false);
  expect(input.value).toBe("保留这个问题");
  expect(message).toContain("发送失败或状态未确认");
});
