import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../shared/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../shared/api/client")>();
  return {
    ...actual,
    fetchTimeline: vi.fn(),
    fetchEvent: vi.fn(),
  };
});

import * as client from "../../shared/api/client";
import type { TimelineDay, TimelineEvent, TimelinePhoto } from "../../shared/api/client";
import { Timeline } from "./Timeline";

// jsdom 没有 IntersectionObserver，而时间线用它做无限滚动。
const NoopIntersectionObserver = class {
  observe(): void {}
  unobserve(): void {}
  disconnect(): void {}
  takeRecords(): never[] {
    return [];
  }
};

function photo(assetId: string, fileName: string): TimelinePhoto {
  return {
    asset_id: assetId,
    file_name: fileName,
    captured_at: "2026-01-02T10:00:00+00:00",
    place: "杭州市",
  };
}

function event(
  id: string,
  title: string,
  place: string,
  timeStart: string,
  photos: TimelinePhoto[] = [],
): TimelineEvent {
  return {
    id,
    title,
    event_type: "outing",
    time_start: timeStart,
    time_end: null,
    admin_district: place === "" ? "" : "浙江省杭州市",
    place,
    activity: "散步",
    summary: `${title}的摘要`,
    confidence: 0.8,
    photos,
  };
}

function days(fixtures: TimelineDay[], total = fixtures.reduce((sum, day) => sum + day.events.length, 0)) {
  return { scope_id: "album1", offset: 0, limit: 20, total, days: fixtures };
}

const DAYS: TimelineDay[] = [
  { date: "2026-01-02", events: [event("event1", "湖边散步", "杭州市", "2026-01-02T10:00:00+00:00", [photo("asset1", "a.jpg")])] },
  { date: "2026-01-01", events: [event("event2", "没有地点的聚会", "", "2026-01-01T10:00:00+00:00")] },
];

beforeEach(() => {
  vi.stubGlobal("IntersectionObserver", NoopIntersectionObserver);
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe("时间线", () => {
  it("按天渲染成默认展开的 <details>，不再用平铺的 h3", async () => {
    vi.mocked(client.fetchTimeline).mockResolvedValue(days(DAYS));
    render(<Timeline scopeId="album1" focusEventId={null} onOpenEvent={vi.fn()} />);

    await screen.findByText("湖边散步");
    const groups = document.querySelectorAll("details.timeline-day");
    expect(groups).toHaveLength(2);
    for (const group of groups) {
      expect((group as HTMLDetailsElement).open).toBe(true);
      expect(group.querySelector("summary")).not.toBeNull();
      expect(group.querySelector("h3")).toBeNull();
    }
    expect(screen.getByText("2026-01-02")).not.toBeNull();
    expect(screen.getAllByText("1 个事件")).toHaveLength(2);
  });

  it("日期范围与「有地点」只过滤当前已加载的事件", async () => {
    vi.mocked(client.fetchTimeline).mockResolvedValue(days(DAYS));
    render(<Timeline scopeId="album1" focusEventId={null} onOpenEvent={vi.fn()} />);
    await screen.findByText("湖边散步");

    fireEvent.change(screen.getByLabelText("开始日期"), { target: { value: "2026-01-02" } });
    await waitFor(() => expect(screen.queryByText("没有地点的聚会")).toBeNull());
    expect(screen.getByText("湖边散步")).not.toBeNull();

    fireEvent.change(screen.getByLabelText("开始日期"), { target: { value: "" } });
    await screen.findByText("没有地点的聚会");
    fireEvent.click(screen.getByRole("button", { name: "有地点" }));

    await waitFor(() => expect(screen.queryByText("没有地点的聚会")).toBeNull());
    expect(screen.getByText("湖边散步")).not.toBeNull();
    expect(screen.getByText(/仅过滤当前已加载的 2 个事件/)).not.toBeNull();
    // 人物筛选需要后端提供事件-人物关联，当前明确禁用而不是假装能筛。
    expect((screen.getByRole("button", { name: "有人物（暂不可用）" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("点击事件卡片把事件 id 交给页面层打开详情", async () => {
    vi.mocked(client.fetchTimeline).mockResolvedValue(days(DAYS));
    const onOpenEvent = vi.fn();
    render(<Timeline scopeId="album1" focusEventId={null} onOpenEvent={onOpenEvent} />);
    await screen.findByText("湖边散步");

    fireEvent.click(screen.getByText("湖边散步"));
    expect(onOpenEvent).toHaveBeenCalledWith("event1");
  });

  it("focusEventId 会展开所在这一天、滚动并高亮该事件", async () => {
    vi.mocked(client.fetchTimeline).mockResolvedValue(days(DAYS));
    vi.mocked(client.fetchEvent).mockResolvedValue({
      ...DAYS[0].events[0],
      event_type: "outing",
      activity: "",
      confidence: 0.8,
      photos: [],
    });
    const scrollIntoView = vi.fn();
    Element.prototype.scrollIntoView = scrollIntoView;

    render(<Timeline scopeId="album1" focusEventId="event1" onOpenEvent={vi.fn()} />);

    const dialog = await screen.findByRole("dialog");
    await waitFor(() =>
      expect(within(dialog).getByRole("heading", { name: "湖边散步" })).not.toBeNull(),
    );

    const node = document.getElementById("timeline-event-event1");
    expect(node).not.toBeNull();
    expect(node?.className).toContain("focused");
    expect(node?.closest("details")?.hasAttribute("open")).toBe(true);
    expect(scrollIntoView).toHaveBeenCalled();
  });
});
