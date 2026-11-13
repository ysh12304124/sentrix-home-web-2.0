import { useCallback, useEffect, useRef, useState } from "react";

import { fetchTimeline, type TimelineDay, type TimelinePhoto } from "../../shared/api/client";
import { Lightbox } from "../../shared/ui/Lightbox";
import { TimelineEventDetail } from "./TimelineEventDetail";
import { isTimelineVideo, timelinePhotoUrl } from "./photoUrl";

const PAGE_SIZE = 20;

/** 相邻两页可能落在同一天，合并而不是让同一天出现两次。 */
function mergeDays(current: TimelineDay[], incoming: TimelineDay[]): TimelineDay[] {
  const merged = current.map((day) => ({ ...day, events: [...day.events] }));
  for (const day of incoming) {
    const last = merged[merged.length - 1];
    if (last && last.date === day.date) {
      last.events.push(...day.events);
    } else {
      merged.push({ ...day, events: [...day.events] });
    }
  }
  return merged;
}

/** 事件卡片在 DOM 里的锚点。`?event=` 定位与高亮都按它找。 */
function eventAnchorId(eventId: string): string {
  return `timeline-event-${eventId}`;
}

interface Props {
  readonly scopeId: string;
  /** 从概览页用 `?event=<id>` 带进来；非空时展开详情并滚动到卡片。 */
  readonly focusEventId: string | null;
  /** 打开 / 关闭详情。由页面层写 URL，刷新和前进后退都能还原。 */
  readonly onOpenEvent: (eventId: string | null) => void;
}

export function Timeline({ scopeId, focusEventId, onOpenEvent }: Props) {
  // 服务端筛选：后端只支持 place。
  const [place, setPlace] = useState("");
  // 客户端筛选：只对已经加载进内存的事件生效。
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [hasPlace, setHasPlace] = useState(false);
  const [loaded, setLoaded] = useState<{ key: string; days: TimelineDay[]; total: number } | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState<TimelinePhoto | null>(null);
  // 事件详情里的照片走全屏看图；与列表的 `open` 分开是因为那个还要拿 place 等字段。
  const [lightbox, setLightbox] = useState<{ src: string; caption: string } | null>(null);
  // 刷新：把 nonce 并进 key，首屏那个 effect 的依赖跟着变，自然重跑。
  // 比另写一条 reload 路径好——两条路径迟早会漂开。
  const [nonce, setNonce] = useState(0);
  const sentinel = useRef<HTMLDivElement | null>(null);

  const key = `${scopeId}\u0000${place}\u0000${nonce}`;
  // 「首屏还在加载」是**推导**出来的：当前这组筛选条件的页面还没到，就是加载中。
  // 用一个 state 去记它就得在 effect 里同步 setState，那会级联渲染
  // （oxlint 的 react(set-state-in-effect) 报的就是这件事，它给的解法正是
  // 「derive the value during render」）。
  const loading = loaded === null || loaded.key !== key;
  const days = loaded && loaded.key === key ? loaded.days : [];
  const total = loaded && loaded.key === key ? loaded.total : 0;
  const shown = days.reduce((sum, day) => sum + day.events.length, 0);
  const clientFiltering = dateFrom !== "" || dateTo !== "" || hasPlace;

  // 日期按 `day.date`（后端已经按 time_start/created_at 分好组）比较；
  // ISO 的 yyyy-mm-dd 直接做字符串比较就是时间顺序。
  const visibleDays = days
    .map((day) => ({
      date: day.date,
      events: day.events.filter(
        (event) =>
          (!hasPlace || event.place.trim() !== "") &&
          (dateFrom === "" || day.date >= dateFrom) &&
          (dateTo === "" || day.date <= dateTo),
      ),
    }))
    .filter((day) => day.events.length > 0);
  const visibleCount = visibleDays.reduce((sum, day) => sum + day.events.length, 0);

  // 首屏与筛选变化：整体替换。setState 只发生在 then/catch 回调里。
  useEffect(() => {
    let alive = true;
    fetchTimeline(scopeId, 0, PAGE_SIZE, place)
      .then((response) => {
        if (alive) {
          setLoaded({ key, days: response.days, total: response.total });
          setError(null);
        }
      })
      .catch((cause: Error) => {
        if (alive) {
          setError(cause.message);
        }
      });
    return () => {
      alive = false;
    };
  }, [scopeId, place, key]);

  // 无限滚动：只追加下一页。这个回调由 IntersectionObserver 触发，不是 effect 体。
  const loadMore = useCallback(async () => {
    if (loaded === null || loaded.key !== key) {
      return;
    }
    setLoadingMore(true);
    try {
      const response = await fetchTimeline(scopeId, shown, PAGE_SIZE, place);
      setLoaded({ key, days: mergeDays(loaded.days, response.days), total: response.total });
      setError(null);
    } catch (cause) {
      setError((cause as Error).message);
    } finally {
      setLoadingMore(false);
    }
  }, [key, loaded, place, scopeId, shown]);

  useEffect(() => {
    const node = sentinel.current;
    if (!node) {
      return;
    }
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting) && !loading && !loadingMore && shown < total) {
        void loadMore();
      }
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [loadMore, loading, loadingMore, shown, total]);

  // `?event=` 定位：卡片出现后展开它所在的整天（用户可能手动折过），再滚到中间。
  // 只做 DOM 操作，不 setState——高亮是按 focusEventId 推导的类名。
  // 依赖 `loaded`（状态对象，新一页到达时引用才变），不是每渲染都新建的 `days`。
  useEffect(() => {
    if (focusEventId === null) {
      return;
    }
    const node = document.getElementById(eventAnchorId(focusEventId));
    if (node === null) {
      return;
    }
    const day = node.closest("details");
    if (day !== null) {
      day.setAttribute("open", "");
    }
    node.scrollIntoView?.({ block: "center" });
  }, [focusEventId, loaded]);

  function clearClientFilters() {
    setDateFrom("");
    setDateTo("");
    setHasPlace(false);
  }

  return (
    <section className="timeline">
      <div className="filter-row">
        {/* 地点筛选走后端：前端只把用户输入原样传过去，不做任何地名计算。 */}
        <input
          className="filter-chip"
          aria-label="按地点筛选"
          placeholder="按地点筛选（后端匹配）"
          value={place}
          onChange={(event) => setPlace(event.target.value)}
        />
        <label className="timeline-date-filter">
          <span>从</span>
          <input
            type="date"
            aria-label="开始日期"
            value={dateFrom}
            max={dateTo || undefined}
            onChange={(event) => setDateFrom(event.target.value)}
          />
        </label>
        <label className="timeline-date-filter">
          <span>到</span>
          <input
            type="date"
            aria-label="结束日期"
            value={dateTo}
            min={dateFrom || undefined}
            onChange={(event) => setDateTo(event.target.value)}
          />
        </label>
        <button
          type="button"
          className={hasPlace ? "filter-chip active" : "filter-chip"}
          aria-pressed={hasPlace}
          onClick={() => setHasPlace((value) => !value)}
        >
          有地点
        </button>
        {/* 后端的事件模型没有人物/实体字段，也没有按事件反查人物的端点。
            留一个禁用项把缺口摆在明面上，比让它「点了没反应」好。 */}
        <button
          type="button"
          className="filter-chip"
          disabled
          title="后端事件数据不含人物关联，暂时无法筛选"
        >
          有人物（暂不可用）
        </button>
        {place || clientFiltering ? (
          <button
            type="button"
            className="text-button"
            onClick={() => {
              setPlace("");
              clearClientFilters();
            }}
          >
            清除筛选
          </button>
        ) : null}
        <span className="filter-spacer" />
        <span className="timeline-count">
          {visibleCount} / {shown} 个已加载事件 · 共 {total}
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
      {/* 1.0 的「有人物 / 有地点 / 某一天」是在**一次全量拉回**的列表上筛。2.0 的
          /timeline 是分页的，后端只有 place 一个参数；日期与「有地点」因此只对
          **已经加载的事件**生效，这一点必须写在用户看得见的地方，否则看起来筛过了、
          其实后面几页没参与。 */}
      <p className="timeline-filter-note">
        日期范围与「有地点」仅过滤当前已加载的 {shown} 个事件；后端 /timeline 目前只支持地点参数，
        人物筛选尚未提供。
      </p>
      {error ? <p className="error">{error}</p> : null}
      {days.length === 0 && !loading ? (
        <div className="empty-queue">
          <span aria-hidden="true">↕</span>
          <strong>{place ? "这个地点还没有事件" : "还没有事件"}</strong>
          <p>{place ? "换一个地点词，或清除筛选查看全部。" : "导入并处理资料后，记忆会自动出现在这里。"}</p>
        </div>
      ) : null}
      {days.length > 0 && visibleDays.length === 0 ? (
        <div className="empty-queue">
          <span aria-hidden="true">↕</span>
          <strong>当前筛选条件下没有事件</strong>
          <p>筛选只作用于已加载的事件，换一个日期范围或清除筛选再试。</p>
          <button type="button" className="text-button" onClick={clearClientFilters}>
            清除日期与地点筛选
          </button>
        </div>
      ) : null}
      {visibleDays.map((day) => (
        <details className="timeline-day" key={day.date} open>
          <summary>
            <span>{day.date}</span>
            <small>{day.events.length} 个事件</small>
            <b aria-hidden="true">⌄</b>
          </summary>
          <div className="timeline-day-events">
            {day.events.map((event) => (
              <article
                className={focusEventId === event.id ? "timeline-event focused" : "timeline-event"}
                key={event.id}
                id={eventAnchorId(event.id)}
                onClick={() => onOpenEvent(event.id)}
              >
                <header>
                  <strong>{event.title}</strong>
                  <span>{event.place}</span>
                </header>
                <p>{event.summary}</p>
                <div className="timeline-photos" onClick={(click) => click.stopPropagation()}>
                  {event.photos.map((photo) => (
                    isTimelineVideo(photo) ? (
                      <a
                        className="timeline-video-thumb"
                        href={timelinePhotoUrl(photo)}
                        target="_blank"
                        rel="noreferrer"
                        key={photo.asset_id}
                        onClick={(click) => click.stopPropagation()}
                        aria-label={`打开视频 ${photo.file_name}`}
                      >
                        <span aria-hidden="true">▶</span>
                        <strong>视频</strong>
                        <small>{photo.file_name}</small>
                      </a>
                    ) : (
                      <button type="button" key={photo.asset_id} onClick={() => setOpen(photo)}>
                        <img src={timelinePhotoUrl(photo)} alt={photo.file_name} loading="lazy" />
                        {photo.place ? <small>{photo.place}</small> : null}
                      </button>
                    )
                  ))}
                </div>
                <button
                  type="button"
                  className="text-button timeline-event-action"
                  onClick={(click) => {
                    click.stopPropagation();
                    onOpenEvent(event.id);
                  }}
                >
                  查看详情 →
                </button>
              </article>
            ))}
          </div>
        </details>
      ))}
      <div ref={sentinel} className="timeline-sentinel">
        {loading || loadingMore ? "加载中…" : null}
      </div>
      {open && !isTimelineVideo(open) ? (
        <div className="timeline-overlay" role="presentation" onClick={() => setOpen(null)}>
          <img src={timelinePhotoUrl(open)} alt={open.file_name} />
          <p>
            {open.file_name} · {open.place || "（未解析出地点）"}
          </p>
        </div>
      ) : null}
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
