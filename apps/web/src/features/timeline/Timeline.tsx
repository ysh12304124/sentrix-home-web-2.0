import { useCallback, useEffect, useRef, useState } from "react";

import {
  fetchEvent,
  fetchTimeline,
  fetchTimelineCalendar,
  timelinePosterUrl,
  type CalendarYear,
  type TimelineDay,
  type TimelineEventSummary,
} from "../../shared/api/client";
import { newOperationId, reportClientEvent } from "../../shared/clientEvents";
import { Lightbox } from "../../shared/ui/Lightbox";
import { TimelineEventDetail } from "./TimelineEventDetail";

const PAGE_SIZE = 200;

interface DayState {
  key: string;
  items: TimelineEventSummary[];
  total: number;
  loading: boolean;
  error: string;
}

interface CalendarState {
  key: string;
  years: CalendarYear[];
  unknownCount: number;
}

interface Props {
  readonly scopeId: string;
  readonly focusEventId: string | null;
  readonly onOpenEvent: (eventId: string | null) => void;
}

function eventAnchorId(eventId: string): string {
  return `timeline-event-${eventId}`;
}

function monthKey(year: number, month: number): string {
  return `${year}-${String(month).padStart(2, "0")}`;
}

function readUrlDate(): string | null {
  if (typeof window === "undefined") return null;
  return new URLSearchParams(window.location.search).get("date");
}

function writeUrlDate(date: string | null): void {
  if (typeof window === "undefined") return;
  const url = new URL(window.location.href);
  url.searchParams.delete("year");
  url.searchParams.delete("month");
  if (date === null) url.searchParams.delete("date");
  else url.searchParams.set("date", date);
  window.history.pushState(null, "", url);
}

function placeLabel(value: string): string {
  const trimmed = value.trim();
  if (trimmed === "" || trimmed === "其他或不确定" || trimmed === "未知") return "地点未记录";
  return trimmed;
}

function eventTime(event: TimelineEventSummary): string {
  if (event.time_start === null || event.time_start === "") return "时间未记录";
  const start = event.time_start.slice(11, 16);
  if (start === "") return "时间未记录";
  if (
    event.time_end !== null &&
    event.time_end !== "" &&
    event.time_end.slice(0, 10) === event.time_start.slice(0, 10)
  ) {
    const end = event.time_end.slice(11, 16);
    if (end !== "" && end !== start) return `${start}–${end}`;
  }
  return start;
}

function expandForDate(date: string): { month: string; date: string } {
  return { month: date.slice(0, 7), date };
}

export function Timeline({ scopeId, focusEventId, onOpenEvent }: Props) {
  const [place, setPlace] = useState("");
  const [queryPlace, setQueryPlace] = useState("");
  const [hasPlace, setHasPlace] = useState(false);
  const [calendar, setCalendar] = useState<CalendarState | null>(null);
  const [calendarError, setCalendarError] = useState("");
  const [expandedMonths, setExpandedMonths] = useState<Set<string>>(new Set());
  const [expandedDays, setExpandedDays] = useState<Set<string>>(new Set());
  const [dayStates, setDayStates] = useState<Record<string, DayState>>({});
  const [focusIssue, setFocusIssue] = useState("");
  const [lightbox, setLightbox] = useState<{ src: string; caption: string } | null>(null);
  const [nonce, setNonce] = useState(0);
  const pendingFocus = useRef<string | null>(null);
  const previewOpened = useRef<string | null>(null);
  const loadingKeys = useRef<Set<string>>(new Set());

  const calendarKey = `${scopeId}\u0000${queryPlace}\u0000${hasPlace ? "1" : "0"}\u0000${nonce}`;
  const calendarLoading = calendar === null || calendar.key !== calendarKey;
  const years = calendar !== null && calendar.key === calendarKey ? calendar.years : [];
  const unknownCount =
    calendar !== null && calendar.key === calendarKey ? calendar.unknownCount : 0;
  const totalEvents =
    years.reduce((sum, year) => sum + year.count, 0) + unknownCount;
  const recordedDays = years.reduce(
    (sum, year) => sum + year.months.reduce((count, month) => count + month.days.length, 0),
    0,
  );

  const stateKey = (date: string) => `${calendarKey}\u0000${date}`;

  // 地点输入停顿后再重建索引，避免每按一个字就请求一次。
  useEffect(() => {
    const timer = window.setTimeout(() => setQueryPlace(place.trim()), 450);
    return () => window.clearTimeout(timer);
  }, [place]);

  useEffect(() => {
    let alive = true;
    fetchTimelineCalendar(scopeId, queryPlace, hasPlace)
      .then((response) => {
        if (alive) {
          setCalendar({
            key: calendarKey,
            years: response.years,
            unknownCount: response.unknown_count ?? 0,
          });
          setCalendarError("");
        }
      })
      .catch((cause: Error) => {
        if (alive) setCalendarError(cause.message);
      });
    return () => {
      alive = false;
    };
  }, [calendarKey, hasPlace, queryPlace, scopeId]);

  useEffect(() => {
    const date = readUrlDate();
    if (date !== null) {
      const next = expandForDate(date);
      setExpandedMonths((previous) => new Set(previous).add(next.month));
      setExpandedDays((previous) => new Set(previous).add(next.date));
    }
  }, []);

  useEffect(() => {
    const onPopState = () => {
      const date = readUrlDate();
      if (date === null) return;
      const next = expandForDate(date);
      setExpandedMonths((previous) => new Set(previous).add(next.month));
      setExpandedDays((previous) => new Set(previous).add(next.date));
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  const loadDay = useCallback(
    async (date: string) => {
      const key = `${calendarKey}\u0000${date}`;
      if (loadingKeys.current.has(key)) return;
      loadingKeys.current.add(key);
      setDayStates((previous) => ({
        ...previous,
        [key]: { key, items: [], total: 0, loading: true, error: "" },
      }));
      try {
        // 一次取完当天事件；接口上限 200，当前数据规模下一次即可覆盖。
        const response = await fetchTimeline(scopeId, 0, PAGE_SIZE, queryPlace, {
          hasPlace,
          date,
        });
        const items = response.days.flatMap((item: TimelineDay) => item.events);
        setDayStates((previous) => ({
          ...previous,
          [key]: { key, items, total: response.total, loading: false, error: "" },
        }));
      } catch (cause) {
        setDayStates((previous) => ({
          ...previous,
          [key]: { key, items: [], total: 0, loading: false, error: (cause as Error).message },
        }));
      } finally {
        loadingKeys.current.delete(key);
      }
    },
    [calendarKey, hasPlace, queryPlace, scopeId],
  );

  // 已展开的日期在筛选变化后重新取数；展开状态本身不因筛选或重新渲染而收起。
  useEffect(() => {
    for (const date of expandedDays) {
      if (dayStates[`${calendarKey}\u0000${date}`] === undefined) {
        void loadDay(date);
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [calendarKey, expandedDays, loadDay]);

  // `?event=`：先查服务端日期，再展开对应月份和日期。
  useEffect(() => {
    if (focusEventId === null) {
      pendingFocus.current = null;
      setFocusIssue("");
      return;
    }
    let alive = true;
    fetchEvent(focusEventId)
      .then((event) => {
        if (!alive) return;
        const date =
          event.timeline_date ||
          (event.time_start !== null ? event.time_start.slice(0, 10) : "");
        if (date === "") {
          setFocusIssue("这个事件还没有可定位的日期。");
          return;
        }
        setFocusIssue("");
        pendingFocus.current = focusEventId;
        const next = expandForDate(date);
        setExpandedMonths((previous) => new Set(previous).add(next.month));
        setExpandedDays((previous) => new Set(previous).add(next.date));
        writeUrlDate(date);
      })
      .catch(() => {
        if (alive) setFocusIssue("没有读到这个事件，可能已被删除或不在当前范围。");
      });
    return () => {
      alive = false;
    };
  }, [focusEventId]);

  // 目标卡出现后滚动并打开详情；同一目标只触发一次。
  useEffect(() => {
    const target = pendingFocus.current;
    if (target === null) return;
    const loaded = Object.entries(dayStates).find(
      ([key, value]) =>
        key.startsWith(`${calendarKey}\u0000`) &&
        value.loading === false &&
        value.items.some((item) => item.id === target),
    );
    if (loaded === undefined) return;
    const node = document.getElementById(eventAnchorId(target));
    if (node === null) return;
    node.scrollIntoView?.({ block: "center" });
    pendingFocus.current = null;
    if (previewOpened.current !== target) {
      previewOpened.current = target;
      onOpenEvent(target);
    }
  }, [calendarKey, dayStates, onOpenEvent]);

  function toggleMonth(key: string) {
    setExpandedMonths((previous) => {
      const next = new Set(previous);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function toggleDay(date: string) {
    setExpandedDays((previous) => {
      const next = new Set(previous);
      if (next.has(date)) {
        next.delete(date);
        writeUrlDate(null);
      } else {
        next.add(date);
        writeUrlDate(date);
      }
      return next;
    });
    setExpandedMonths((previous) =>
      new Set(previous).add(expandForDate(date).month),
    );
    void reportClientEvent({
      eventName: "timeline.date",
      result: "ok",
      operationId: newOperationId(),
      scopeId,
    });
  }

  function openEvent(eventId: string) {
    void reportClientEvent({
      eventName: "timeline.event_open",
      result: "ok",
      operationId: newOperationId(),
      scopeId,
      eventId,
    });
    onOpenEvent(eventId);
  }

  function clearFilters() {
    setPlace("");
    setQueryPlace("");
    setHasPlace(false);
    writeUrlDate(null);
  }

  const filterActive = queryPlace !== "" || hasPlace;

  /** 一天的可展开区块。普通日期与「未知时间」共用同一套加载/展开状态。 */
  function renderDayBlock(date: string, label: string, count: number) {
    const open = expandedDays.has(date);
    const state = dayStates[stateKey(date)];
    return (
      <div
        className={open ? "timeline-day-block open" : "timeline-day-block"}
        key={date}
        id={`timeline-day-${date}`}
      >
        <button
          type="button"
          className="axis-day"
          aria-expanded={open}
          onClick={() => toggleDay(date)}
        >
          <span className="axis-day-label">{label}</span>
          <span className="axis-dot axis-dot-day" aria-hidden="true" />
          <small>{count} 个事件</small>
          <b aria-hidden="true">{open ? "▾" : "▸"}</b>
        </button>
        {open ? (
          <div className="axis-events">
            {state === undefined || state.loading ? (
              <p className="timeline-loading">
                {date === "unknown" ? "正在读取时间未知的事件…" : "正在读取这一天…"}
              </p>
            ) : state.error !== "" ? (
              <p className="error">
                这一天的内容读取失败：{state.error}
                <button
                  type="button"
                  className="text-button"
                  onClick={() => {
                    setDayStates((previous) => {
                      const next = { ...previous };
                      delete next[stateKey(date)];
                      return next;
                    });
                    void loadDay(date);
                  }}
                >
                  重试
                </button>
              </p>
            ) : state.items.length === 0 ? (
              <p className="timeline-loading">这一天暂时没有可显示的事件。</p>
            ) : (
              state.items.map((event) => (
                <div className="axis-event" key={event.id} id={eventAnchorId(event.id)}>
                  <span className="axis-event-time">{eventTime(event)}</span>
                  <span className="axis-dot axis-dot-event" aria-hidden="true" />
                  <TimelineCard
                    event={event}
                    focused={focusEventId === event.id}
                    onOpen={() => openEvent(event.id)}
                    onMediaError={() =>
                      void reportClientEvent({
                        eventName: "timeline.media_error",
                        result: "failed",
                        errorCode: "media_load",
                        operationId: newOperationId(),
                        scopeId,
                        eventId: event.id,
                        assetId: event.representative_asset_id ?? "",
                      })
                    }
                  />
                </div>
              ))
            )}
          </div>
        ) : null}
      </div>
    );
  }

  return (
    <section className="timeline">
      <div className="filter-row">
        <input
          className="filter-chip"
          aria-label="按地点筛选"
          placeholder="按地点查找"
          value={place}
          onChange={(event) => setPlace(event.target.value)}
        />
        <button
          type="button"
          className={hasPlace ? "filter-chip active" : "filter-chip"}
          aria-pressed={hasPlace}
          onClick={() => setHasPlace((value) => !value)}
        >
          只看有地点
        </button>
        {filterActive ? (
          <button type="button" className="text-button" onClick={clearFilters}>
            清除筛选
          </button>
        ) : null}
        <span className="filter-spacer" />
        <span className="timeline-count">
          {calendarLoading
            ? "正在读取记录…"
            : `${totalEvents} 个事件 · ${recordedDays} 天有记录`}
        </span>
        <button
          type="button"
          className="icon-button bordered"
          aria-label="刷新时间线"
          onClick={() => setNonce((value) => value + 1)}
        >
          ↻
        </button>
      </div>

      {calendarError !== "" ? (
        <p className="error">
          时间线读取失败：{calendarError}
          <button type="button" className="text-button" onClick={() => setNonce((value) => value + 1)}>
            重试
          </button>
        </p>
      ) : null}

      {focusIssue !== "" ? (
        <div className="timeline-focus-issue" role="status">
          <span>{focusIssue}</span>
          {filterActive ? (
            <button type="button" className="text-button" onClick={clearFilters}>
              清除筛选
            </button>
          ) : null}
        </div>
      ) : null}

      {calendarLoading ? (
        <p className="timeline-loading">正在读取有记录的日期…</p>
      ) : years.length === 0 && unknownCount === 0 ? (
        <div className="empty-queue">
          <span aria-hidden="true">↕</span>
          <strong>{filterActive ? "这个条件下还没有事件" : "还没有事件"}</strong>
          <p>
            {filterActive
              ? "换一个地点词，或清除筛选查看全部。"
              : "添加资料并整理后，时间线会自动出现。"}
          </p>
          {filterActive ? (
            <button type="button" className="text-button" onClick={clearFilters}>
              清除筛选
            </button>
          ) : (
            <a className="button primary" href="/imports">添加资料</a>
          )}
        </div>
      ) : (
        <>
          {years.length === 0 ? null : (
            <div className="timeline-axis">
              {years.map((year) => {
            const months = [...year.months].sort((a, b) => b.month - a.month);
            return (
              <section className="timeline-year-block" key={year.year}>
                <header className="axis-year">
                  <span className="axis-year-label">{year.year}</span>
                  <span className="axis-dot axis-dot-year" aria-hidden="true" />
                  <span className="axis-year-count">{year.count} 个事件</span>
                </header>
                {months.map((month) => {
                  const mkey = monthKey(year.year, month.month);
                  const monthOpen = expandedMonths.has(mkey);
                  const days = [...month.days].sort((a, b) => b.date.localeCompare(a.date));
                  return (
                    <section className="timeline-month-block" key={mkey}>
                      <button
                        type="button"
                        className="axis-month"
                        aria-expanded={monthOpen}
                        onClick={() => toggleMonth(mkey)}
                      >
                        <span className="axis-month-label">{month.month} 月</span>
                        <span className="axis-dot" aria-hidden="true" />
                        <span className="axis-month-count">{month.count} 个事件</span>
                        <b aria-hidden="true">{monthOpen ? "▾" : "▸"}</b>
                      </button>
                      {monthOpen ? (
                        <div className="timeline-day-list">
                          {days.map((dayItem) =>
                            renderDayBlock(
                              dayItem.date,
                              dayItem.date.slice(5).replace("-", " / "),
                              dayItem.count,
                            ),
                          )}
                        </div>
                      ) : null}
                    </section>
                  );
                })}
              </section>
            );
              })}
            </div>
          )}

          {unknownCount > 0 ? (
            <section className="timeline-unknown-block">
              <header className="axis-year">
                <span className="axis-year-label">时间未知</span>
                <span className="axis-dot axis-dot-year" aria-hidden="true" />
                <span className="axis-year-count">{unknownCount} 个事件</span>
              </header>
              <div className="timeline-day-list">
                {renderDayBlock("unknown", "未知日期", unknownCount)}
              </div>
            </section>
          ) : null}
        </>
      )}

      {focusEventId === null ? null : (
        <TimelineEventDetail
          eventId={focusEventId}
          onClose={() => onOpenEvent(null)}
          onOpenImage={(src, caption) => setLightbox({ src, caption })}
        />
      )}
      {lightbox === null ? null : (
        <Lightbox
          src={lightbox.src}
          caption={lightbox.caption}
          onClose={() => setLightbox(null)}
        />
      )}
    </section>
  );
}

function TimelineCard({
  event,
  focused,
  onOpen,
  onMediaError,
}: {
  event: TimelineEventSummary;
  focused: boolean;
  onOpen: () => void;
  onMediaError: () => void;
}) {
  const [imageFailed, setImageFailed] = useState(false);
  return (
    <article
      className={focused ? "timeline-event focused" : "timeline-event"}
      onClick={onOpen}
    >
      <div className="timeline-event-media">
        {event.representative_asset_id == null || imageFailed ? (
          <span className="timeline-event-placeholder" aria-hidden="true">
            {event.representative_media_type === "video" ? "▶" : "▦"}
          </span>
        ) : (
          <img
            src={timelinePosterUrl(event.representative_asset_id)}
            alt=""
            loading="lazy"
            onError={() => {
              setImageFailed(true);
              onMediaError();
            }}
          />
        )}
        {event.representative_media_type === "video" ? (
          <b className="timeline-event-badge">视频</b>
        ) : null}
      </div>
      <div className="timeline-event-body">
        <header>
          <strong>{event.title.trim() !== "" ? event.title : "一段没有标题的回忆"}</strong>
          <span>{placeLabel(event.place)}</span>
        </header>
        {event.summary.trim() !== "" ? <p>{event.summary}</p> : null}
        <div className="timeline-event-foot">
          <span>共 {event.asset_count ?? 0} 份资料</span>
          <button
            type="button"
            className="text-button"
            onClick={(click) => {
              click.stopPropagation();
              onOpen();
            }}
          >
            查看详情 →
          </button>
        </div>
      </div>
    </article>
  );
}
