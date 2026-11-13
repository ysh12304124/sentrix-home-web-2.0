/**
 * 事件详情。数据只来自 `GET /events/{event_id}`，**不补后端没有的字段**。
 *
 * 1.0 的这张卡还有「人物实体」「关联事实」「Observation 列表」三段，它们来自 1.0
 * `evidence/event` 端点；2.0 的 `/events/{id}` 返回的是 `TimelineEvent`（事件本体 +
 * 关联照片），没有那三块，也没有可以按事件反查它们的端点。所以这里明确写出缺什么，
 * 而不是渲染三段空壳让人以为链路通了。
 */

import { useEffect, useState } from "react";

import { AssetMemoryCorrection, MemoryCorrection } from "../../shared/components/MemoryCorrection";
import { fetchEvent, type TimelineEvent } from "../../shared/api/client";
import { timelinePhotoThumbUrl, timelinePhotoUrl } from "./photoUrl";

interface Loaded {
  readonly id: string;
  readonly event: TimelineEvent | null;
  readonly error: string | null;
}

function formatDateTime(value: string | null): string {
  if (value === null || value === "") return "未标注";
  // 后端给 ISO 8601；只做展示裁剪，不做时区换算（时区属于后端语义）。
  return value.replace("T", " ").slice(0, 16);
}

function timeRange(event: TimelineEvent): string {
  const start = formatDateTime(event.time_start);
  if (event.time_end === null || event.time_end === "") return start;
  return `${start} → ${formatDateTime(event.time_end)}`;
}

export function TimelineEventDetail({
  eventId,
  onClose,
  onOpenImage,
}: {
  eventId: string;
  onClose: () => void;
  /** 点照片时交给组合层打开全屏看图；不传就退化成新标签打开（旧行为）。 */
  onOpenImage?: (src: string, caption: string) => void;
}) {
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [reloadToken, setReloadToken] = useState(0);

  // 「这个 id 的详情还没回来」是推导出来的，效果里只写回调，不额外 setState。
  const loading = loaded === null || loaded.id !== eventId;
  const event = loaded !== null && loaded.id === eventId ? loaded.event : null;
  const error = loaded !== null && loaded.id === eventId ? loaded.error : null;

  useEffect(() => {
    let alive = true;
    fetchEvent(eventId)
      .then((response) => {
        if (alive) setLoaded({ id: eventId, event: response, error: null });
      })
      .catch((cause: Error) => {
        if (alive) setLoaded({ id: eventId, event: null, error: cause.message });
      });
    return () => {
      alive = false;
    };
  }, [eventId, reloadToken]);

  useEffect(() => {
    function onKeyDown(key: KeyboardEvent) {
      if (key.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose]);

  return (
    <div className="timeline-event-overlay" role="presentation" onClick={onClose}>
      <div
        className="timeline-event-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby="timeline-event-detail-title"
        onClick={(click) => click.stopPropagation()}
      >
        <header className="timeline-event-panel-head">
          <div>
            <p className="section-kicker">事件详情</p>
            <h2 id="timeline-event-detail-title">{event?.title ?? "正在读取事件…"}</h2>
          </div>
          <button
            type="button"
            className="timeline-event-close"
            aria-label="关闭事件详情"
            onClick={onClose}
          >
            ×
          </button>
        </header>

        {error !== null ? (
          <p className="error" role="alert">
            {error}
          </p>
        ) : null}

        {loading ? (
          <p className="timeline-event-loading">正在读取事件详情…</p>
        ) : event === null ? (
          <p className="timeline-event-loading">没有读到这个事件。</p>
        ) : (
          <>
            <p className="timeline-event-summary">{event.summary || "暂无摘要。"}</p>

            <dl className="timeline-event-facts">
              <div>
                <dt>时间</dt>
                <dd>{timeRange(event)}</dd>
              </div>
              <div>
                <dt>地点</dt>
                <dd>{event.place || "地点未记录"}</dd>
              </div>
              <div>
                <dt>行政区</dt>
                <dd>{event.admin_district || "未记录"}</dd>
              </div>
            </dl>

            <section className="timeline-event-section">
              <h3>修正事件记忆</h3>
              <MemoryCorrection
                kind="event"
                targetId={event.id}
                onSaved={() => setReloadToken((value) => value + 1)}
              />
            </section>

            <section className="timeline-event-section">
              <h3>全部资料（{event.photos.length} 份）</h3>
              {event.photos.length === 0 ? (
                <p className="timeline-event-note">这个事件暂时没有可展示的资料。</p>
              ) : (
                <ul className="timeline-event-photo-list">
                  {event.photos.map((photo) => (
                    <li key={photo.asset_id}>
                      {/* **点图片是放大，不是打开原文件。** 此前这里是
                          `<a href="/assets/{id}/file" target="_blank">`——浏览器遇到
                          HEIC 或不认识的 MIME 会直接下载，用户点「图片」得到的是一个文件。 */}
                      {onOpenImage ? (
                        <button
                          type="button"
                          className="timeline-photo-open"
                          onClick={() =>
                            onOpenImage(timelinePhotoUrl(photo), photo.place || formatDateTime(photo.captured_at))
                          }
                        >
                          <img src={timelinePhotoThumbUrl(photo)} alt="事件照片" loading="lazy" />
                        </button>
                      ) : (
                        <a href={timelinePhotoUrl(photo)} target="_blank" rel="noreferrer">
                          <img src={timelinePhotoThumbUrl(photo)} alt="事件照片" loading="lazy" />
                        </a>
                      )}
                      <small>{formatDateTime(photo.captured_at)}</small>
                      {photo.place ? <small>{photo.place}</small> : null}
                      <AssetMemoryCorrection assetId={photo.asset_id} compact />
                    </li>
                  ))}
                </ul>
              )}
            </section>
          </>
        )}
      </div>
    </div>
  );
}
