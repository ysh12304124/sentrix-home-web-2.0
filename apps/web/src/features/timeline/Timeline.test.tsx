import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../shared/api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../shared/api/client")>();
  return {
    ...actual,
    fetchTimeline: vi.fn(),
    fetchTimelineCalendar: vi.fn(),
    fetchEvent: vi.fn(),
  };
});

import * as client from "../../shared/api/client";
import type { TimelineDay, TimelineEvent, TimelinePhoto } from "../../shared/api/client";
import { Timeline } from "./Timeline";

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

function filteredDays(place: string, hasPlace: boolean) {
  return DAYS.map((day) => ({ ...day, events: day.events.filter((item) =>
    (!hasPlace || item.place !== "") && (place === "" || item.place.includes(place)),
  ) })).filter((day) => day.events.length > 0);
}

beforeEach(() => {
  window.history.replaceState(null, "", "/timeline");
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 204 })));
  vi.mocked(client.fetchTimelineCalendar).mockImplementation(async (scopeId, place = "", hasPlace = false) => {
    const fixtures = filteredDays(place, hasPlace);
    const count = fixtures.reduce((sum, day) => sum + day.events.length, 0);
    return { scope_id: scopeId, unknown_count: 0, years: count === 0 ? [] : [{
      year: 2026, count, months: [{ month: 1, count, days: fixtures.map((day) => ({ date: day.date, count: day.events.length })) }],
    }] };
  });
  vi.mocked(client.fetchTimeline).mockImplementation(async (_scopeId, _offset, _limit, place = "", options = {}) =>
    days(filteredDays(place, options.hasPlace === true).filter((day) => day.date === options.date)),
  );
  vi.mocked(client.fetchEvent).mockResolvedValue(event("event1", "湖边散步", "杭州市", "2026-01-02T10:00:00+00:00"));
  Object.defineProperty(Element.prototype, "scrollIntoView", { configurable: true, value: vi.fn() });
});

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

async function expandDay(label = "01 / 02") {
  if (!screen.queryByRole("button", { name: new RegExp(label) })) {
    fireEvent.click(await screen.findByRole("button", { name: /1 月/ }));
  }
  fireEvent.click(screen.getByRole("button", { name: new RegExp(label) }));
}

describe("时间线", () => {
  it("先加载日期索引，展开日期才取当天事件，再次展开使用已读内容", async () => {
    render(<Timeline scopeId="album1" focusEventId={null} onOpenEvent={vi.fn()} />);
    const month = await screen.findByRole("button", { name: /1 月/ });
    expect(client.fetchTimelineCalendar).toHaveBeenCalledExactlyOnceWith("album1", "", false);
    expect(client.fetchTimeline).not.toHaveBeenCalled();
    expect(month.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(month);
    const day = screen.getByRole("button", { name: /01 \/ 02/ });
    expect(day.getAttribute("aria-expanded")).toBe("false");
    expect(client.fetchTimeline).not.toHaveBeenCalled();
    fireEvent.click(day);
    expect(await screen.findByText("湖边散步")).toBeTruthy();
    expect(client.fetchTimeline).toHaveBeenCalledExactlyOnceWith("album1", 0, 200, "", { hasPlace: false, date: "2026-01-02" });
    expect(screen.queryByText("没有地点的聚会")).toBeNull();
    expect(window.location.search).toBe("?date=2026-01-02");
    fireEvent.click(day);
    expect(screen.queryByText("湖边散步")).toBeNull();
    fireEvent.click(day);
    expect(screen.getByText("湖边散步")).toBeTruthy();
    expect(client.fetchTimeline).toHaveBeenCalledTimes(1);
  });

  it("地点和有地点筛选传给服务端，重读完整索引和已展开日期", async () => {
    render(<Timeline scopeId="album1" focusEventId={null} onOpenEvent={vi.fn()} />);
    await expandDay();
    await screen.findByText("湖边散步");
    await expandDay("01 / 01");
    await screen.findByText("没有地点的聚会");
    fireEvent.click(screen.getByRole("button", { name: "只看有地点" }));
    await waitFor(() => expect(screen.queryByText("没有地点的聚会")).toBeNull());
    await screen.findByText("1 个事件 · 1 天有记录");
    expect(client.fetchTimelineCalendar).toHaveBeenLastCalledWith("album1", "", true);
    expect(client.fetchTimeline).toHaveBeenCalledWith("album1", 0, 200, "", { hasPlace: true, date: "2026-01-02" });
    fireEvent.change(screen.getByLabelText("按地点筛选"), { target: { value: " 杭州 " } });
    await waitFor(() => expect(client.fetchTimelineCalendar).toHaveBeenLastCalledWith("album1", "杭州", true));
    await waitFor(() => expect(client.fetchTimeline).toHaveBeenCalledWith("album1", 0, 200, "杭州", { hasPlace: true, date: "2026-01-02" }));
    expect(await screen.findByText("湖边散步")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "清除筛选" }));
    await screen.findByText("没有地点的聚会");
    expect(client.fetchTimelineCalendar).toHaveBeenLastCalledWith("album1", "", false);
    expect(screen.getByRole("button", { name: "只看有地点" }).getAttribute("aria-pressed")).toBe("false");
  });

  it("点击事件卡片把事件 id 交给页面层打开详情", async () => {
    const onOpenEvent = vi.fn();
    render(<Timeline scopeId="album1" focusEventId={null} onOpenEvent={onOpenEvent} />);
    await expandDay();
    fireEvent.click(await screen.findByText("湖边散步"));
    expect(onOpenEvent).toHaveBeenCalledExactlyOnceWith("event1");
  });

  it("focusEventId 展开目标月份和日期，滚动并高亮该事件", async () => {
    render(<Timeline scopeId="album1" focusEventId="event1" onOpenEvent={vi.fn()} />);
    const dialog = await screen.findByRole("dialog");
    await within(dialog).findByRole("heading", { name: "湖边散步" });
    await waitFor(() => expect(document.getElementById("timeline-event-event1")?.querySelector(".timeline-event.focused")).toBeTruthy());
    expect(screen.getByRole("button", { name: /1 月/ }).getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByRole("button", { name: /01 \/ 02/ }).getAttribute("aria-expanded")).toBe("true");
    expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
  });

  it("索引或当天读取失败后可重试，成功后显示事件", async () => {
    vi.mocked(client.fetchTimelineCalendar).mockRejectedValueOnce(new Error("calendar offline"));
    render(<Timeline scopeId="album1" focusEventId={null} onOpenEvent={vi.fn()} />);
    await screen.findByText(/calendar offline/);
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    vi.mocked(client.fetchTimeline).mockRejectedValueOnce(new Error("day offline"));
    await expandDay();
    await screen.findByText(/day offline/);
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("湖边散步")).toBeTruthy();
    expect(screen.queryByText(/day offline|calendar offline/)).toBeNull();
  });
});
