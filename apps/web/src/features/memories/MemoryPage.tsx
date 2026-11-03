import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router";

import { useScope } from "../../app/ScopeProvider";
import { fetchRotation, markRotationViewed, type RotationCard } from "../../shared/api/client";
import "./memory.css";

const SLIDE_MS = 8000;

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export function MemoryPage() {
  const { scopeId } = useScope();
  const [cards, setCards] = useState<RotationCard[]>([]);
  const [cursor, setCursor] = useState<number | undefined>(undefined);
  const [hasMore, setHasMore] = useState(false);
  const [sequenceId, setSequenceId] = useState("");
  const [emptyReason, setEmptyReason] = useState("");
  const [error, setError] = useState("");
  const [index, setIndex] = useState(0);
  const [paused, setPaused] = useState(false);
  const [loading, setLoading] = useState(true);
  const [fetching, setFetching] = useState(false);
  const [videoOpen, setVideoOpen] = useState(false);
  const [mediaReady, setMediaReady] = useState(false);
  const touchStart = useRef<number | null>(null);
  const card = cards[index];

  const applyPage = useCallback((page: Awaited<ReturnType<typeof fetchRotation>>, append: boolean) => {
    setSequenceId(page.sequence_id);
    setCursor(page.cursor);
    setHasMore(page.has_more);
    setEmptyReason(page.empty_reason);
    setCards((previous) => (append ? [...previous, ...page.media] : page.media));
    if (!append) setIndex(0);
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError("");
    setVideoOpen(false);
    setPaused(false);
    fetchRotation(scopeId)
      .then((page) => {
        if (!cancelled) applyPage(page, false);
      })
      .catch((cause: Error) => {
        if (!cancelled) setError(cause.message);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [scopeId, applyPage]);

  useEffect(() => {
    setMediaReady(false);
    setVideoOpen(false);
    if (card !== undefined && sequenceId !== "") {
      void markRotationViewed(sequenceId, card.event_id);
    }
  }, [card, sequenceId]);

  const loadMore = useCallback(async (): Promise<boolean> => {
    if (fetching || !hasMore) return false;
    setFetching(true);
    try {
      const page = await fetchRotation(scopeId, cursor);
      applyPage(page, true);
      return page.media.length > 0;
    } catch (cause) {
      setError((cause as Error).message);
      return false;
    } finally {
      setFetching(false);
    }
  }, [applyPage, cursor, fetching, hasMore, scopeId]);

  const move = useCallback(
    (delta: number, manual: boolean) => {
      if (manual) setPaused(true);
      setVideoOpen(false);
      const next = index + delta;
      if (next >= 0 && next < cards.length) {
        setIndex(next);
        return;
      }
      if (delta > 0 && hasMore) {
        void loadMore().then((appended) => {
          if (appended) setIndex((current) => current + 1);
        });
      }
    },
    [cards.length, hasMore, index, loadMore],
  );

  useEffect(() => {
    if (paused || loading || videoOpen || !mediaReady || card === undefined) return;
    if (typeof document !== "undefined" && document.hidden) return;
    const timer = window.setTimeout(() => move(1, false), SLIDE_MS);
    return () => window.clearTimeout(timer);
  }, [paused, loading, videoOpen, mediaReady, card, move]);

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.target instanceof HTMLInputElement || event.target instanceof HTMLTextAreaElement) return;
      if (event.target instanceof HTMLVideoElement) return;
      if (event.key === "ArrowRight") move(1, true);
      if (event.key === "ArrowLeft") move(-1, true);
      if (event.key === " ") {
        event.preventDefault();
        setPaused((value) => !value);
      }
      if (event.key === "Escape") setVideoOpen(false);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [move]);

  if (loading) return <section className="memory-stage"><p className="memory-note">正在准备回忆…</p></section>;

  if (error !== "") {
    return (
      <section className="memory-stage">
        <p className="memory-note">回忆暂时无法读取，正在恢复。</p>
        <button type="button" onClick={() => setError("")}>重试</button>
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
            {emptyReason === "no_media" ? <Link to="/imports">去导入照片或视频</Link> : null}
            {emptyReason === "processing" ? <Link to="/imports">查看导入进度</Link> : null}
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
        key={card.event_id}
      >
        <div className="memory-visual" onClick={() => setPaused(true)}>
          <div
            className="memory-backdrop"
            style={{ backgroundImage: `url(${card.poster_url})` }}
            aria-hidden="true"
          />
          {videoOpen && card.play_url !== null ? (
            <video
              className="memory-media"
              src={card.play_url}
              controls
              autoPlay
              onEnded={() => setVideoOpen(false)}
              onPause={() => setPaused(true)}
            />
          ) : (
            <img
              className="memory-media"
              src={card.poster_url}
              alt={card.title || "回忆画面"}
              onLoad={() => setMediaReady(true)}
              onError={() => move(1, false)}
              onClick={() => {
                setPaused(true);
                if (card.media_kind === "video" && card.play_url !== null) {
                  setVideoOpen(true);
                }
              }}
            />
          )}
        </div>

        <div className="memory-caption" aria-live="polite">
          <div className="memory-meta">
            {card.time_label !== "" ? <span>{card.time_label}</span> : null}
            {card.place_label !== "" ? <span>{card.place_label}</span> : null}
            <span>
              {index + 1} / {cards.length}
              {hasMore ? "+" : ""}
            </span>
          </div>
          {card.title !== "" ? <h1>{card.title}</h1> : null}
          {card.summary !== "" ? <p>{card.summary}</p> : null}
        </div>

        <div className="memory-footer">
          <button
            type="button"
            className="button primary memory-toggle"
            aria-pressed={!paused}
            onClick={() => setPaused((value) => !value)}
          >
            {paused ? "开始" : "暂停"}
          </button>
          <span className="memory-hint">
            {card.media_kind === "video" && card.play_url !== null && !videoOpen
              ? "点击画面播放原视频 · 键盘 ← → 切换 · 空格暂停"
              : "键盘 ← → 切换 · 空格暂停"}
          </span>
          <div className="memory-dots" aria-hidden="true">
            {cards.slice(0, 12).map((item, position) => (
              <span
                key={item.event_id}
                className={position === index ? "memory-dot active" : "memory-dot"}
              />
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}

function emptyTitle(reason: string): string {
  if (reason === "no_media") return "这里还没有回忆";
  if (reason === "processing") return "素材正在处理";
  return "还没有可展示的事件";
}

function emptyBody(reason: string): string {
  if (reason === "no_media") return "导入照片或视频后，处理完成就会出现在这里。";
  if (reason === "processing") return "导入仍在进行，完成后回忆会自动出现，不需要重新上传。";
  return "已有素材，但还不足以形成可展示的事件或代表画面。";
}
