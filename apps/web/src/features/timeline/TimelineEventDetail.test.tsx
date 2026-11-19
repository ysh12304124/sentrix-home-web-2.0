import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("../../shared/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../shared/api/client")>();
  return {
    ...actual,
    fetchEvent: vi.fn(),
  };
});

import * as client from "../../shared/api/client";
import type { TimelineEvent } from "../../shared/api/client";
import { TimelineEventDetail } from "./TimelineEventDetail";

const EVENT: TimelineEvent = {
  id: "event1",
  title: "湖边散步",
  event_type: "outing",
  time_start: "2026-01-02T10:00:00+00:00",
  time_end: "2026-01-02T12:00:00+00:00",
  admin_district: "浙江省杭州市",
  place: "杭州市西湖区",
  activity: "散步",
  summary: "一家人沿湖走了一圈。",
  confidence: 0.86,
  photos: [
    {
      asset_id: "asset1",
      file_name: "a.jpg",
      captured_at: "2026-01-02T10:00:00+00:00",
      place: "杭州市西湖区",
    },
    {
      asset_id: "asset2",
      file_name: "b.heic",
      captured_at: "2026-01-02T10:05:00+00:00",
      place: "杭州市西湖区",
    },
  ],
};

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe("事件详情", () => {
  it("记忆修改完成后自动读取最新标题摘要及剩余证据", async () => {
    vi.mocked(client.fetchEvent).mockResolvedValueOnce(EVENT).mockResolvedValue({
      ...EVENT, title: "小禾湖边散步", summary: "小禾在湖边散步。", photos: [EVENT.photos[1]],
    });
    render(<TimelineEventDetail eventId="event1" onClose={vi.fn()} />);
    expect(await screen.findByRole("heading", { name: "湖边散步" })).not.toBeNull();
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("{}", {
      status: 200, headers: { "Content-Type": "application/json" },
    })));
    await client.request("/memories/event/event1", { method: "PATCH", body: "{}" });
    expect(await screen.findByRole("heading", { name: "小禾湖边散步" })).not.toBeNull();
    expect(screen.getByText("小禾在湖边散步。")).not.toBeNull();
    expect(screen.getByRole("heading", { name: "全部资料（1 份）" })).not.toBeNull();
    expect(screen.getAllByRole("img")).toHaveLength(1);
    expect(screen.getByRole("img").getAttribute("src")).toContain("asset2");
  });
  it("用 GET /events/{id} 的数据展示摘要、时间地点、封面与原始照片", async () => {
    vi.mocked(client.fetchEvent).mockResolvedValue(EVENT);
    render(<TimelineEventDetail eventId="event1" onClose={vi.fn()} />);

    expect(await screen.findByRole("heading", { name: "湖边散步" })).not.toBeNull();
    expect(client.fetchEvent).toHaveBeenCalledWith("event1");
    expect(screen.getByText("一家人沿湖走了一圈。")).not.toBeNull();
    expect(screen.getByText("2026-01-02 10:00 → 2026-01-02 12:00")).not.toBeNull();
    expect(screen.getAllByText("杭州市西湖区").length).toBeGreaterThan(0);
    expect(screen.getByText("86%")).not.toBeNull();
    expect(screen.getByRole("img", { name: "湖边散步的封面" })).not.toBeNull();
    expect(screen.getByText("原始证据（2 个媒体）")).not.toBeNull();
    // 后端没有的三块如实说明，而不是渲染空壳。
    expect(screen.getByText(/不返回人物、实体或关联事实/)).not.toBeNull();
  });

  it("关闭按钮与 Escape 都能关闭", async () => {
    vi.mocked(client.fetchEvent).mockResolvedValue(EVENT);
    const onClose = vi.fn();
    render(<TimelineEventDetail eventId="event1" onClose={onClose} />);
    await screen.findByRole("heading", { name: "湖边散步" });

    fireEvent.click(screen.getByRole("button", { name: "关闭事件详情" }));
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("读取失败时显示后端错误", async () => {
    vi.mocked(client.fetchEvent).mockRejectedValue(new Error("event not found"));
    render(<TimelineEventDetail eventId="missing" onClose={vi.fn()} />);

    await waitFor(() => expect(screen.getByRole("alert").textContent).toContain("event not found"));
  });
});
