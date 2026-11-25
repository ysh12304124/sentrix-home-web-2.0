import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../shared/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../shared/api/client")>();
  return {
    ...actual,
    listMemorySpaces: vi.fn(),
    listConversations: vi.fn(),
    createConversation: vi.fn(),
    getConversation: vi.fn(),
    renameConversation: vi.fn(),
    deleteConversation: vi.fn(),
  };
});

import * as client from "../../shared/api/client";
import type { ConversationDetail, ConversationSummary } from "../../shared/api/client";
import { ScopeProvider } from "../../app/ScopeProvider";
import { AssistantPage } from "./AssistantPage";

function conversation(id: string, title: string): ConversationSummary {
  return {
    id,
    scope_id: "album1",
    title,
    created_at: "2026-07-01T10:00:00+00:00",
    updated_at: "2026-07-01T10:00:00+00:00",
    last_message_at: "2026-07-01T10:00:00+00:00",
  };
}

function detail(item: ConversationSummary): ConversationDetail {
  return { conversation: item, messages: [], turns: [] };
}

beforeEach(() => {
  vi.mocked(client.listMemorySpaces).mockResolvedValue([
    { id: "album1", name: "第一册", kind: "benchmark", status: "active" },
  ]);
  vi.mocked(client.listConversations).mockResolvedValue([
    conversation("c1", "第一个对话"),
    conversation("c2", "第二个对话"),
  ]);
  vi.mocked(client.getConversation).mockImplementation(async (id) =>
    detail(conversation(id, id === "c1" ? "第一个对话" : "第二个对话")),
  );
  vi.mocked(client.renameConversation).mockResolvedValue(conversation("c1", "改名后的对话"));
  vi.mocked(client.deleteConversation).mockResolvedValue(undefined);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function renderPage() {
  return render(
    <MemoryRouter>
      <ScopeProvider>
        <AssistantPage />
      </ScopeProvider>
    </MemoryRouter>,
  );
}

describe("会话重命名 / 删除", () => {
  it("行内重命名，并在删除时要求二次确认", async () => {
    renderPage();
    expect(await screen.findByText("第一个对话")).toBeTruthy();

    // ---- 重命名 ------------------------------------------------------------
    fireEvent.click(screen.getByLabelText("更多操作：第一个对话"));
    fireEvent.click(screen.getByRole("button", { name: "重命名" }));
    const input = screen.getByLabelText("对话名称");
    fireEvent.change(input, { target: { value: "改名后的对话" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() =>
      expect(client.renameConversation).toHaveBeenCalledWith("c1", "改名后的对话"),
    );
    expect(await screen.findByText("改名后的对话")).toBeTruthy();

    // ---- 删除：先确认，未确认前不调接口 --------------------------------
    fireEvent.click(screen.getByLabelText("删除对话：改名后的对话"));
    expect(screen.getByText("删除此对话及消息、轨迹？此操作无法撤销。")).toBeTruthy();
    expect(client.deleteConversation).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(client.deleteConversation).toHaveBeenCalledWith("c1"));
    await waitFor(() => expect(screen.queryByText("改名后的对话")).toBeNull());
  });

  it("删除失败保留对话，显示服务端提示", async () => {
    vi.mocked(client.deleteConversation).mockRejectedValueOnce(new Error("请先停止回答"));
    renderPage();
    fireEvent.click(await screen.findByLabelText("删除对话：第一个对话"));
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    expect(await screen.findByText("请先停止回答")).toBeTruthy();
    expect(screen.getByRole("button", { name: "确认删除" })).toBeTruthy();
    expect(client.createConversation).not.toHaveBeenCalled();
  });

  it("删除非选中对话不切换当前会话", async () => {
    renderPage();
    fireEvent.click(await screen.findByLabelText("删除对话：第二个对话"));
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(screen.queryByText("第二个对话")).toBeNull());
    expect(screen.getByText("第一个对话")).toBeTruthy();
    expect(client.deleteConversation).toHaveBeenCalledWith("c2");
    expect(client.createConversation).not.toHaveBeenCalled();
  });
});
