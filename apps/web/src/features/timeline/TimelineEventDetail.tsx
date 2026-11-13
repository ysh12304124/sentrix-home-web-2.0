/**
 * 事件详情。数据只来自 `GET /events/{event_id}`，**不补后端没有的字段**。
 *
 * 1.0 的这张卡还有「人物实体」「关联事实」「Observation 列表」三段，它们来自 1.0
 * `evidence/event` 端点；2.0 的 `/events/{id}` 返回的是 `TimelineEvent`（事件本体 +
 * 关联照片），没有那三块，也没有可以按事件反查它们的端点。所以这里明确写出缺什么，
 * 而不是渲染三段空壳让人以为链路通了。
 */

import { useEffect, useState } from "react";

import { fetchEvent, type TimelineEvent } from "../../shared/api/client";
import { isTimelineVideo, timelinePhotoUrl } from "./photoUrl";
import { MemoryCorrection, AssetMemoryCorrection } from '../../shared/components/MemoryCorrection';

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
  }, [eventId]);

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
            <MemoryCorrection kind="event" targetId={event.id} onSaved={async () => {
              setLoaded({ id: eventId, event: await fetchEvent(eventId), error: null });
            }} />

            <dl className="timeline-event-facts">
              <div>
                <dt>时间</dt>
                <dd>{timeRange(event)}</dd>
              </div>
              <div>
                <dt>地点</dt>
                <dd>{event.place || "未解析出地点"}</dd>
              </div>
              <div>
                <dt>行政区</dt>
                <dd>{event.admin_district || "—"}</dd>
              </div>
              <div>
                <dt>类型</dt>
                <dd>{event.event_type || "—"}</dd>
              </div>
              <div>
                <dt>活动</dt>
                <dd>{event.activity || "—"}</dd>
              </div>
              <div>
                <dt>置信度</dt>
                <dd>{Math.round(event.confidence * 100)}%</dd>
              </div>
            </dl>

            <section className="timeline-event-section">
              <h3>封面媒体</h3>
              {event.photos.length === 0 ? (
                <p className="timeline-event-note">这个事件没有关联照片。</p>
              ) : (
                isTimelineVideo(event.photos[0]) ? (
                  <a className="timeline-video-preview is-cover" href={timelinePhotoUrl(event.photos[0])} target="_blank" rel="noreferrer">
                    <span aria-hidden="true">▶</span>
                    <strong>视频证据</strong>
                    <small>{event.photos[0].file_name}</small>
                  </a>
                ) : (
                  <a
                    className="timeline-event-cover"
                    href={timelinePhotoUrl(event.photos[0])}
                    target="_blank"
                    rel="noreferrer"
                  >
                    <img
                      src={timelinePhotoUrl(event.photos[0])}
                      alt={`${event.title}的封面`}
                    />
                  </a>
                )
              )}
            </section>

            <section className="timeline-event-section">
              <h3>原始证据（{event.photos.length} 个媒体）</h3>
              {event.photos.length === 0 ? (
                <p className="timeline-event-note">没有可回溯的照片。</p>
              ) : (
                <ul className="timeline-event-photo-list">
                  {event.photos.map((photo) => (
                    <li key={photo.asset_id}>
                      {isTimelineVideo(photo) ? (
                        <a className="timeline-video-preview" href={timelinePhotoUrl(photo)} target="_blank" rel="noreferrer">
                          <span aria-hidden="true">▶</span>
                          <strong>视频</strong>
                          <small>{photo.file_name}</small>
                        </a>
                      ) : onOpenImage ? (
                        <button
                          type="button"
                          className="timeline-photo-open"
                          onClick={() => onOpenImage(timelinePhotoUrl(photo), photo.file_name)}
                        >
                          <img src={timelinePhotoUrl(photo)} alt={photo.file_name} loading="lazy" />
                        </button>
                      ) : (
                        <a className="timeline-photo-open" href={timelinePhotoUrl(photo)} target="_blank" rel="noreferrer">
                          <img src={timelinePhotoUrl(photo)} alt={photo.file_name} loading="lazy" />
                        </a>
                      )}
                      <div className="timeline-evidence-meta">
                        <strong title={photo.file_name}>{photo.file_name}</strong>
                        <small>{formatDateTime(photo.captured_at)}</small>
                        {photo.place ? <small>{photo.place}</small> : null}
                      </div>
                      <AssetMemoryCorrection assetId={photo.asset_id} compact />
                    </li>
                  ))}
                </ul>
              )}
              <p className="timeline-event-note">展开单个媒体卡片，可查看并修正该媒体及其关键帧关联的观察记忆。</p>
            </section>

            <section className="timeline-event-section">
              <h3>人物、实体与关联事实</h3>
              <p className="timeline-event-note">
                事件详情端点不返回人物、实体或关联事实，2.0 也没有按事件查询它们的端点；
                这里不编造数据。
              </p>
            </section>
          </>
        )}
      </div>
    </div>
  );
}
