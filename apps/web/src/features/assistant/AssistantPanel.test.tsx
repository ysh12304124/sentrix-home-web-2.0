import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../shared/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../shared/api/client")>();
  return {
    ...actual,
    getConversation: vi.fn(),
    fetchTurnEvents: vi.fn(),
  };
});

import * as client from "../../shared/api/client";
import type { ConversationDetail } from "../../shared/api/client";
import { AssistantPanel } from "./AssistantPanel";

/** 一条完整的历史会话：用户消息、助手消息、带交付照片的回合。 */
function detail(
  id: string,
  question: string,
  answer: string,
  assetId: string,
): ConversationDetail {
  return {
    conversation: {
      id,
      scope_id: "album1",
      title: question,
      created_at: "2026-07-01T10:00:00+00:00",
      updated_at: "2026-07-01T10:00:05+00:00",
      last_message_at: "2026-07-01T10:00:05+00:00",
    },
    messages: [
      {
        id: `${id}-m1`,
        role: "user",
        content: question,
        turn_id: `${id}-t1`,
        created_at: "2026-07-01T10:00:00+00:00",
      },
      {
        id: `${id}-m2`,
        role: "assistant",
        content: answer,
        turn_id: `${id}-t1`,
        created_at: "2026-07-01T10:00:05+00:00",
      },
    ],
    turns: [
      {
        id: `${id}-t1`,
        question,
        status: "complete",
        termination_reason: "answered",
        answer,
        attachments: [{ handle: "photo_1", asset_id: assetId, file_name: "beach.jpg" }],
        created_at: "2026-07-01T10:00:00+00:00",
      },
    ],
  };
}

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("助手面板的历史回放", () => {
  it("文本证据显示正文链接而不是图片缩略图", async () => {
    const record = detail("text1", "会议在哪里", "海棠厅", "text-asset");
    record.turns[0].attachments = [{ handle: "photo_1", asset_id: "text-asset",
      file_name: "note.txt", media_type: "text" }];
    vi.mocked(client.getConversation).mockResolvedValueOnce(record);
    render(<AssistantPanel conversationId="text1" scopeId="album1" />);
    const link = await screen.findByRole("link", { name: /查看文本证据/ });
    expect(link.getAttribute("href")).toBe("/api/v1/assets/text-asset/file");
    expect(screen.queryByRole("img", { name: "相关照片" })).toBeNull();
  });
  it("切换会话时加载并渲染后端存下的消息与交付照片", async () => {
    vi.mocked(client.getConversation)
      .mockResolvedValueOnce(detail("c1", "去年夏天在海边", "找到了两张。", "a1"))
      .mockResolvedValueOnce(detail("c2", "奶奶的生日", "找到了蛋糕。", "a2"));

    const { rerender } = render(<AssistantPanel conversationId="c1" scopeId="album1" />);

    expect(await screen.findByText("去年夏天在海边")).toBeTruthy();
    expect(await screen.findByText("找到了两张。")).toBeTruthy();
    const firstPhoto = await screen.findByAltText("相关照片");
    expect(firstPhoto.getAttribute("src")).toBe("/api/v1/assets/a1/thumbnail");
    expect(client.fetchTurnEvents).not.toHaveBeenCalled();
    fireEvent.click(firstPhoto);
    expect(screen.getByRole("dialog").querySelector("img")?.getAttribute("src")).toBe("/api/v1/assets/a1/preview");
    fireEvent.keyDown(window, { key: "Escape" });
    expect(client.getConversation).toHaveBeenCalledWith("c1");

    rerender(<AssistantPanel conversationId="c2" scopeId="album1" />);

    expect(await screen.findByText("奶奶的生日")).toBeTruthy();
    expect(await screen.findByText("找到了蛋糕。")).toBeTruthy();
    expect(client.getConversation).toHaveBeenLastCalledWith("c2");
    expect(screen.getByAltText("相关照片").getAttribute("src")).toBe("/api/v1/assets/a2/thumbnail");
    // 回放的是 c2，c1 的内容不该继续留在面板里。
    expect(screen.queryByText("去年夏天在海边")).toBeNull();
  });
});
