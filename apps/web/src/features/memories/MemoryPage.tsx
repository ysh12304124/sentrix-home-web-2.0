import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router";

import { useScope } from "../../app/ScopeProvider";
import { fetchRotation, markRotationViewed, type RotationCard } from "../../shared/api/client";
import { reportClientEvent } from "../../shared/clientEvents";
import { Modal } from "../../shared/ui/Modal";
import "./memory.css";

const SLIDE_MS = 8000;
const VIEWED_AFTER_MS = 1000;

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

export function MemoryPage() {
  const { scopeId } = useScope();
  const [cards, setCards] = useState<RotationCard[]>([]);
  const [cursor, setCursor] = useState<number | undefined>(undefined);
  const [hasMore, setHasMore] = useState(false);
  const [sequenceId, setSequenceId] = useState("");
  const [emptyReason, setEmptyReason] = useState("");
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">("loading");
  const [listError, setListError] = useState("");
  const [index, setIndex] = useState(0);
  const [paused, setPaused] = useState(() => prefersReducedMotion());
  const [hovered, setHovered] = useState(false);
  const [focusWithin, setFocusWithin] = useState(false);
  const [visible, setVisible] = useState(
    () => typeof document === "undefined" || !document.hidden,
  );
  const [mediaReady, setMediaReady] = useState(false);
  const [mediaError, setMediaError] = useState(false);
  const [mediaAttempt, setMediaAttempt] = useState(0);
  const [videoOpen, setVideoOpen] = useState(false);
  const [videoError, setVideoError] = useState(false);
  const [detailOpen, setDetailOpen] = useState(false);
  const [showEnd, setShowEnd] = useState(false);
  const [fetching, setFetching] = useState(false);
  const [liveMode, setLiveMode] = useState<"off" | "polite">("off");
  const touchStart = useRef<number | null>(null);
  const card = cards[index];
  const atLastCard = index >= cards.length - 1;

  const applyPage = useCallback((page: Awaited<ReturnType<typeof fetchRotation>>, append: boolean) => {
    setSequenceId(page.sequence_id);
    setCursor(page.cursor);
    setHasMore(page.has_more);
    setEmptyReason(page.empty_reason);
    setCards((previous) => (append ? [...previous, ...page.media] : page.media));
    if (!append) {
      setIndex(0);
      setShowEnd(false);
    }
  }, []);

  const load = useCallback(async () => {
    setLoadState("loading");
    setListError("");
    setPaused(prefersReducedMotion());
    try {
      const page = await fetchRotation(scopeId);
      applyPage(page, false);
      setLoadState("ready");
    } catch (cause) {
      setListError((cause as Error).message);
      setLoadState("error");
    }
  }, [applyPage, scopeId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    const onVisibility = () => setVisible(!document.hidden);
    onVisibility();
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  // 换卡时重置媒体状态；画面未成功载入前不开始 8 秒计时，也不记“已看过”。
  useEffect(() => {
    setMediaReady(false);
    setMediaError(false);
    setMediaAttempt(0);
    setVideoOpen(false);
    setVideoError(false);
  }, [card?.event_id]);

  useEffect(() => {
    if (!mediaReady || !visible || card === undefined || sequenceId === "") return;
    const eventId = card.event_id;
    const sequence = sequenceId;
    const timer = window.setTimeout(() => {
      if (document.visibilityState === "visible") {
        void markRotationViewed(sequence, eventId);
      }
    }, VIEWED_AFTER_MS);
    return () => window.clearTimeout(timer);
  }, [mediaReady, visible, card, sequenceId]);

  const loadMore = useCallback(async (): Promise<boolean> => {
    if (fetching || !hasMore) return false;
    setFetching(true);
    try {
      const page = await fetchRotation(scopeId, cursor);
      applyPage(page, true);
      return page.media.length > 0;
    } catch (cause) {
      setListError((cause as Error).message);
      return false;
    } finally {
      setFetching(false);
    }
  }, [applyPage, cursor, fetching, hasMore, scopeId]);

  const move = useCallback(
    (delta: number, manual: boolean) => {
      if (manual) {
        setPaused(true);
        setLiveMode("polite");
        void reportClientEvent({
          eventName: delta > 0 ? "memory.next" : "memory.prev",
          result: "ok",
          scopeId,
          eventId: card?.event_id,
          assetId: card?.asset_id,
        });
      } else {
        setLiveMode("off");
      }
      setVideoOpen(false);
      setShowEnd(false);
      const next = index + delta;
      if (next >= 0 && next < cards.length) {
        setIndex(next);
        return;
      }
      if (delta > 0 && hasMore) {
        void loadMore().then((appended) => {
          if (appended) setIndex((current) => current + 1);
        });
        return;
      }
      if (delta > 0 && atLastCard) {
        setShowEnd(true);
        setPaused(true);
      }
    },
    [atLastCard, card, cards.length, hasMore, index, loadMore, scopeId],
  );

  const autoAdvance = !paused && !hovered && !focusWithin && visible && mediaReady;
  useEffect(() => {
    if (!autoAdvance || videoOpen || detailOpen || showEnd || card === undefined) return;
    const timer = window.setTimeout(() => move(1, false), SLIDE_MS);
    return () => window.clearTimeout(timer);
  }, [autoAdvance, videoOpen, detailOpen, showEnd, card, move]);

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) return;
      if (event.target instanceof HTMLVideoElement) return;
      if (detailOpen) return;
      if (event.key === "ArrowRight") move(1, true);
      if (event.key === "ArrowLeft") move(-1, true);
      if (event.key === " ") {
        event.preventDefault();
        togglePause();
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  });

  const togglePause = useCallback(() => {
    setPaused((value) => {
      const next = !value;
      setLiveMode(next ? "off" : "polite");
      void reportClientEvent({
        eventName: next ? "memory.pause" : "memory.resume",
        result: "ok",
        scopeId,
        eventId: card?.event_id,
      });
      return next;
    });
  }, [card?.event_id, scopeId]);

  const playVideo = () => {
    if (card === undefined || card.play_url === null) return;
    setPaused(true);
    setVideoOpen(true);
    setVideoError(false);
    void reportClientEvent({
      eventName: "memory.video_play",
      result: "started",
      scopeId,
      eventId: card.event_id,
      assetId: card.asset_id,
    });
  };

  const retryMedia = () => {
    setMediaError(false);
    setMediaReady(false);
    setMediaAttempt((value) => value + 1);
  };

  const restart = async () => {
    setShowEnd(false);
    setPaused(false);
    try {
      const page = await fetchRotation(scopeId, cursor);
      applyPage(page, false);
      if (page.media.length === 0) {
        setLoadState("ready");
      }
    } catch (cause) {
      setListError((cause as Error).message);
      setLoadState("error");
    }
  };

  if (loadState === "loading") {
    return (
      <section className="memory-stage">
        <p className="memory-note">正在准备回忆…</p>
      </section>
    );
  }

  if (loadState === "error") {
    return (
      <section className="memory-stage">
        <div className="memory-empty">
          <h1>回忆暂时读不到</h1>
          <p>可能是服务正在恢复。可以重试，其他页面仍可正常使用。</p>
          {listError !== "" ? <p className="memory-error-detail">{listError}</p> : null}
          <button
            type="button"
            className="button primary memory-toggle"
            onClick={() => void load()}
          >
            重试
          </button>
        </div>
      </section>
    );
  }

  if (showEnd) {
    return (
      <section className="memory-stage">
        <div className="memory-empty">
          <h1>这一轮看完了</h1>
          <p>换一轮会重新挑选今天还没看过的画面。</p>
          <button
            type="button"
            className="button primary memory-toggle"
            onClick={() => void restart()}
          >
            再看看
          </button>
        </div>
      </section>
    );
  }

  if (card === undefined) {
    return (
      <section className="memory-stage">
        <div className="memory-empty">
          <h1>{emptyTitle(emptyReason)}</h1>
          <p>{emptyBody(emptyReason)}</p>
          <div className="memory-actions">
            {emptyReason === "no_media" ? <Link to="/imports">去添加资料</Link> : null}
            {emptyReason === "processing" ? <Link to="/imports">查看整理进度</Link> : null}
            {emptyReason === "no_eligible_event" ? (
              <>
                <Link to="/library">查看资料库</Link>
                <Link to="/timeline">查看时间线</Link>
              </>
            ) : null}
          </div>
        </div>
      </section>
    );
  }

  const reduced = prefersReducedMotion();
  const posterSrc =
    mediaAttempt > 0
      ? `${card.poster_url}${card.poster_url.includes("?") ? "&" : "?"}retry=${mediaAttempt}`
      : card.poster_url;
  const isVideo = card.media_kind === "video" && card.play_url !== null;

  return (
    <section
      className="memory-stage"
      onTouchStart={(event) => {
        touchStart.current = event.touches[0]?.clientX ?? null;
      }}
      onTouchEnd={(event) => {
        if (touchStart.current === null) return;
        const delta = (event.changedTouches[0]?.clientX ?? touchStart.current) - touchStart.current;
        touchStart.current = null;
        if (Math.abs(delta) > 40) move(delta < 0 ? 1 : -1, true);
      }}
    >
      <div
        className={reduced ? "memory-frame memory-frame-reduced" : "memory-frame"}
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        onFocusCapture={() => setFocusWithin(true)}
        onBlurCapture={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
            setFocusWithin(false);
          }
        }}
      >
        <div className="memory-visual">
          <div
            className="memory-backdrop"
            style={{ backgroundImage: `url(${card.poster_url})` }}
            aria-hidden="true"
          />
          {videoOpen ? (
            <video
              key={`video-${card.asset_id}`}
              className="memory-media"
              src={card.play_url ?? undefined}
              controls
              autoPlay
              onEnded={() => {
                // 播完不自动恢复翻页；用户显式点“继续”才继续。
                setVideoOpen(false);
                setPaused(true);
                setLiveMode("polite");
              }}
              onPause={() => setPaused(true)}
              onError={() => {
                setVideoOpen(false);
                setVideoError(true);
                setPaused(true);
                void reportClientEvent({
                  eventName: "memory.media_error",
                  result: "failed",
                  errorCode: "video_play",
                  scopeId,
                  eventId: card.event_id,
                  assetId: card.asset_id,
                });
              }}
            />
          ) : mediaError ? (
            <div className="memory-media-error" role="alert">
              <strong>这张画面临时打不开</strong>
              <span>位置不会自动跳走，可以重试这张，或看下一段。</span>
              <div>
                <button type="button" className="memory-button" onClick={retryMedia}>
                  重试这张
                </button>
                <button type="button" className="memory-button" onClick={() => move(1, true)}>
                  下一段
                </button>
              </div>
            </div>
          ) : (
            <img
              key={`${card.asset_id}-${mediaAttempt}`}
              className="memory-media"
              src={posterSrc}
              alt={card.title || "回忆画面"}
              onLoad={() => {
                setMediaReady(true);
                setMediaError(false);
              }}
              onError={() => {
                setMediaReady(false);
                setMediaError(true);
                setPaused(true);
                void reportClientEvent({
                  eventName: "memory.media_error",
                  result: "failed",
                  errorCode: "media_load",
                  scopeId,
                  eventId: card.event_id,
                  assetId: card.asset_id,
                });
              }}
              onClick={() => {
                if (isVideo) playVideo();
              }}
            />
          )}
        </div>

        <div className="memory-caption" aria-live={liveMode}>
          <div className="memory-meta">
            <span>{card.time_label !== "" ? card.time_label : "时间未记录"}</span>
            <span>{card.place_label !== "" ? card.place_label : "地点未记录"}</span>
            <span>共 {card.related_asset_count} 份相关资料</span>
          </div>
          <div className="memory-caption-main">
            <div>
              <h1>{card.title !== "" ? card.title : "一段没有标题的回忆"}</h1>
              {card.summary !== "" ? <p>{card.summary}</p> : null}
              {videoError ? (
                <p className="memory-inline-error" role="alert">
                  视频暂时无法播放，可以回到封面或看下一段。
                </p>
              ) : null}
            </div>
            {card.summary !== "" ? (
              <button
                type="button"
                className="memory-detail-button"
                onClick={() => {
                  setPaused(true);
                  setDetailOpen(true);
                  setLiveMode("polite");
                }}
              >
                查看完整描述
              </button>
            ) : null}
          </div>
        </div>

        <div className="memory-footer">
          <button
            type="button"
            className="memory-button"
            onClick={() => move(-1, true)}
            disabled={index === 0}
          >
            上一段
          </button>
          <button
            type="button"
            className="memory-button"
            onClick={() => move(1, true)}
            disabled={fetching && atLastCard}
          >
            下一段
          </button>
          <button
            type="button"
            className="button primary memory-toggle"
            aria-pressed={!paused}
            onClick={togglePause}
          >
            {paused ? "继续" : "暂停"}
          </button>
          {isVideo && !videoOpen ? (
            <button type="button" className="memory-button" onClick={playVideo}>
              播放视频
            </button>
          ) : null}
          <span className="memory-position">
            {index + 1} / {cards.length}
            {hasMore ? "+" : ""}
          </span>
          <span className="memory-hint">
            左右方向键切换 · 空格{paused ? "继续" : "暂停"} · 鼠标停留时不会自动翻页
          </span>
        </div>
      </div>

      {detailOpen ? (
        <Modal kicker="回忆详情" title={card.title !== "" ? card.title : "这一段回忆"} onClose={() => setDetailOpen(false)}>
          <p className="memory-detail-meta">
            {card.time_label !== "" ? card.time_label : "时间未记录"} ·{" "}
            {card.place_label !== "" ? card.place_label : "地点未记录"} · 共{" "}
            {card.related_asset_count} 份相关资料
          </p>
          <p className="memory-detail-body">
            {card.summary !== "" ? card.summary : "这一段还没有更完整的描述。"}
          </p>
          <div className="modal-actions">
            <button type="button" className="button primary" onClick={() => setDetailOpen(false)}>
              关闭
            </button>
          </div>
        </Modal>
      ) : null}
    </section>
  );
}

function emptyTitle(reason: string): string {
  if (reason === "no_media") return "这里还没有回忆";
  if (reason === "processing") return "素材正在整理";
  return "还没有可展示的事件";
}

function emptyBody(reason: string): string {
  if (reason === "no_media") return "添加照片或视频后，整理完成就会出现在这里。";
  if (reason === "processing") return "资料仍在整理，完成后回忆会自动出现，不需要重新上传。";
  return "已有资料，但还不足以形成可展示的事件或代表画面。";
}
