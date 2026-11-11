/**
 * 全屏看图。点图片放大，不下载；支持多图左右滑动和关闭。
 *
 * 用 portal 挂到 body：助手结果集在滚动容器里，固定定位如果落在一个有
 * transform/overflow 的祖先里，会被裁切或盖住关闭按钮。portal 后层级和点击
 * 不受宿主布局影响。
 */
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

export interface LightboxItem {
  src: string;
  alt?: string;
  caption?: string;
  kind?: "image" | "video";
}

export interface LightboxProps {
  /** 单图用法；多图优先用 items。 */
  src?: string;
  alt?: string;
  caption?: string;
  items?: readonly LightboxItem[];
  index?: number;
  onIndexChange?: (index: number) => void;
  onClose: () => void;
}

function clampIndex(value: number, length: number): number {
  if (length <= 0) return 0;
  return Math.max(0, Math.min(length - 1, value));
}

export function Lightbox({
  src = "",
  alt = "",
  caption = "",
  items,
  index,
  onIndexChange,
  onClose,
}: LightboxProps) {
  const photos: readonly LightboxItem[] =
    items !== undefined && items.length > 0
      ? items
      : src !== ""
        ? [{ src, alt, caption }]
        : [];
  const [internalIndex, setInternalIndex] = useState(index ?? 0);
  const activeIndex = clampIndex(index ?? internalIndex, photos.length);
  const active = photos[activeIndex];
  const touchStart = useRef<number | null>(null);
  const swiped = useRef(false);

  const move = (delta: number) => {
    if (photos.length <= 1) return;
    const next = clampIndex(activeIndex + delta, photos.length);
    if (next === activeIndex) return;
    if (onIndexChange !== undefined) onIndexChange(next);
    else setInternalIndex(next);
  };

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
      if (event.key === "ArrowLeft") move(-1);
      if (event.key === "ArrowRight") move(1);
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });

  if (active === undefined) return null;

  const overlay = (
    <div
      className="lightbox"
      role="dialog"
      aria-modal="true"
      aria-label={active.alt || active.caption || "图片预览"}
      onClick={() => {
        if (swiped.current) {
          swiped.current = false;
          return;
        }
        onClose();
      }}
      onTouchStart={(event) => {
        touchStart.current = event.touches[0]?.clientX ?? null;
        swiped.current = false;
      }}
      onTouchEnd={(event) => {
        const start = touchStart.current;
        touchStart.current = null;
        const end = event.changedTouches[0]?.clientX;
        if (start === null || end === undefined) return;
        const delta = end - start;
        if (Math.abs(delta) < 45) return;
        swiped.current = true;
        move(delta < 0 ? 1 : -1);
      }}
    >
      <button
        type="button"
        className="lightbox-close"
        aria-label="关闭图片预览"
        onPointerDown={(event) => event.stopPropagation()}
        onClick={(event) => {
          event.stopPropagation();
          onClose();
        }}
      >
        <span aria-hidden="true">×</span>
        关闭
      </button>
      {photos.length > 1 ? (
        <>
          <button
            type="button"
            className="lightbox-nav prev"
            aria-label="上一张"
            disabled={activeIndex === 0}
            onClick={(event) => {
              event.stopPropagation();
              move(-1);
            }}
          >
            ‹
          </button>
          <button
            type="button"
            className="lightbox-nav next"
            aria-label="下一张"
            disabled={activeIndex === photos.length - 1}
            onClick={(event) => {
              event.stopPropagation();
              move(1);
            }}
          >
            ›
          </button>
        </>
      ) : null}
      {active.kind === "video" ? (
        <video
          className="lightbox-video"
          src={active.src}
          controls
          autoPlay
          playsInline
          preload="metadata"
          aria-label={active.alt || active.caption || "视频预览"}
          onClick={(event) => event.stopPropagation()}
        />
      ) : (
        <img
          src={active.src}
          alt={active.alt || ""}
          draggable={false}
          onClick={(event) => event.stopPropagation()}
        />
      )}
      {active.caption !== undefined && active.caption !== "" ? (
        <p className="lightbox-caption">
          {active.caption}
          {photos.length > 1 ? ` · ${activeIndex + 1} / ${photos.length}` : ""}
        </p>
      ) : photos.length > 1 ? (
        <p className="lightbox-caption">{`${activeIndex + 1} / ${photos.length}`}</p>
      ) : null}
    </div>
  );

  if (typeof document === "undefined") return overlay;
  return createPortal(overlay, document.body);
}
