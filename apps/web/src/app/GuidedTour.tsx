/**
 * 就地指引：透明遮罩挖出真实控件，箭头和说明贴在该控件旁边。
 *
 * 不是第二个说明弹窗；每一步都指向当前页面实际存在的元素。元素缺失时跳过该步，
 * 全部缺失时退化为居中的简短说明。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { TourStep } from "./tours";
import "./GuidedTour.css";

const BOX_WIDTH = 350;
const GAP = 14;

interface Rect {
  top: number;
  left: number;
  width: number;
  height: number;
  bottom: number;
  right: number;
}

function toRect(element: Element): Rect {
  const box = element.getBoundingClientRect();
  return {
    top: box.top,
    left: box.left,
    width: box.width,
    height: box.height,
    bottom: box.bottom,
    right: box.right,
  };
}

export function GuidedTour({
  steps,
  pageLabel,
  onClose,
  onOpenOnboarding,
}: {
  steps: readonly TourStep[];
  pageLabel: string;
  onClose: () => void;
  onOpenOnboarding: () => void;
}) {
  const [available, setAvailable] = useState<readonly TourStep[]>(steps);
  const [index, setIndex] = useState(0);
  const [rect, setRect] = useState<Rect | null>(null);
  const [viewport, setViewport] = useState(() => ({
    width: typeof window === "undefined" ? 1440 : window.innerWidth,
    height: typeof window === "undefined" ? 900 : window.innerHeight,
  }));
  const nextRef = useRef<HTMLButtonElement | null>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  const step = available[index];

  const measure = useCallback(() => {
    if (step === undefined) {
      setRect(null);
      return;
    }
    const element = document.querySelector(step.selector);
    if (element === null) {
      setRect(null);
      return;
    }
    const box = toRect(element);
    // 超大区域（整条时间轴、整页）不滚动居中，否则会把页面拉到中间。
    if (box.height < window.innerHeight * 0.7) {
      element.scrollIntoView({ block: "center", inline: "nearest" });
      setRect(toRect(element));
    } else {
      setRect(box);
    }
  }, [step]);

  useEffect(() => {
    const found = steps.filter((item) => document.querySelector(item.selector) !== null);
    setAvailable(found.length > 0 ? found : steps.slice(0, 1));
    setIndex(0);
  }, [steps]);

  useEffect(() => {
    measure();
    const timer = window.setTimeout(measure, 380);
    const onViewport = () => {
      setViewport({ width: window.innerWidth, height: window.innerHeight });
      measure();
    };
    window.addEventListener("resize", onViewport);
    window.addEventListener("scroll", measure, true);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("resize", onViewport);
      window.removeEventListener("scroll", measure, true);
    };
  }, [measure]);

  useEffect(() => {
    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = previous;
    };
  }, []);

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        closeRef.current();
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, []);

  useEffect(() => {
    nextRef.current?.focus();
  }, [index]);

  const padding = 8;
  const highlight = useMemo(() => {
    if (rect === null) return null;
    return {
      top: Math.max(4, rect.top - padding),
      left: Math.max(4, rect.left - padding),
      width: Math.min(viewport.width - 8, rect.width + padding * 2),
      height: Math.min(viewport.height - 8, rect.height + padding * 2),
    };
  }, [rect, viewport.height, viewport.width]);

  const placement = useMemo(() => {
    if (highlight === null) {
      return { top: viewport.height / 2 - 130, left: viewport.width / 2 - BOX_WIDTH / 2, arrow: null as number | null, side: "center" as const };
    }
    if (highlight.height > viewport.height * 0.55) {
      return {
        top: viewport.height / 2 - 130,
        left: viewport.width / 2 - BOX_WIDTH / 2,
        arrow: null as number | null,
        side: "center" as const,
      };
    }
    const spaceBelow = viewport.height - (highlight.top + highlight.height);
    const side = spaceBelow >= 210 || spaceBelow >= highlight.top ? "bottom" : "top";
    const left = Math.max(
      16,
      Math.min(viewport.width - BOX_WIDTH - 16, highlight.left + highlight.width / 2 - BOX_WIDTH / 2),
    );
    const arrow = Math.max(
      22,
      Math.min(BOX_WIDTH - 22, highlight.left + highlight.width / 2 - left),
    );
    return side === "bottom"
      ? { top: highlight.top + highlight.height + GAP, left, arrow, side }
      : { top: highlight.top - GAP, left, arrow, side };
  }, [highlight, viewport.height, viewport.width]);

  if (step === undefined) {
    return (
      <div className="tour-layer">
        <div className="tour-backdrop" />
        <div className="tour-box tour-box-center" role="dialog" aria-modal="true" aria-label="如何使用">
          <p className="tour-eyebrow">{pageLabel} · 如何使用</p>
          <h3>这一页暂时没有可指出的控件</h3>
          <p>页面还在加载时可以稍后再打开。</p>
          <div className="tour-actions">
            <button type="button" className="button primary" onClick={onClose}>知道了</button>
          </div>
        </div>
      </div>
    );
  }

  const last = index >= available.length - 1;

  return (
    <div className="tour-layer">
      {highlight === null ? (
        <div className="tour-backdrop" />
      ) : (
        <>
          <div className="tour-backdrop" style={{ top: 0, left: 0, right: 0, height: highlight.top }} />
          <div className="tour-backdrop" style={{ top: highlight.top + highlight.height, left: 0, right: 0, bottom: 0 }} />
          <div
            className="tour-backdrop"
            style={{ top: highlight.top, left: 0, width: highlight.left, height: highlight.height }}
          />
          <div
            className="tour-backdrop"
            style={{
              top: highlight.top,
              left: highlight.left + highlight.width,
              right: 0,
              height: highlight.height,
            }}
          />
          <div className="tour-highlight" style={highlight} aria-hidden="true" />
        </>
      )}

      <div
        className={`tour-box tour-${placement.side}`}
        style={{
          top: placement.top,
          left: placement.left,
          transform: placement.side === "top" ? "translateY(-100%)" : undefined,
        }}
        role="dialog"
        aria-modal="true"
        aria-label={`${pageLabel}如何使用：${step.title}`}
      >
        {placement.arrow !== null ? (
          <span
            className={`tour-arrow tour-arrow-${placement.side}`}
            style={{ left: placement.arrow }}
            aria-hidden="true"
          />
        ) : null}
        <p className="tour-eyebrow">
          {pageLabel} · 第 {index + 1} / {available.length} 步
        </p>
        <h3>{step.title}</h3>
        <p>{step.body}</p>
        <div className="tour-actions">
          <button type="button" className="text-button" onClick={onClose}>跳过</button>
          {index > 0 ? (
            <button type="button" className="button ghost" onClick={() => setIndex(index - 1)}>
              上一步
            </button>
          ) : null}
          <button
            ref={nextRef}
            type="button"
            className="button primary"
            onClick={() => {
              if (last) onClose();
              else setIndex(index + 1);
            }}
          >
            {last ? "完成" : "下一步"}
          </button>
        </div>
        <button
          type="button"
          className="tour-more"
          onClick={() => {
            onClose();
            onOpenOnboarding();
          }}
        >
          复习全部功能
        </button>
      </div>
    </div>
  );
}
