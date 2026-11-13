/**
 * 模态框：遮罩 + 面板 + 右上角关闭。
 *
 * 键盘与焦点行为集中在这里，引导与帮助共用一套：打开时焦点进入面板，Tab 限在
 * 面板内，Escape 关闭，关闭后焦点回到触发位置。`role="dialog"` / `aria-modal`
 * 也是 `useAutoRefresh` 判断“用户正在操作模态”的依据。
 */

import { useEffect, useId, useRef, type ReactNode } from "react";

import "./Modal.css";

interface Props {
  readonly kicker?: string;
  readonly title: string;
  readonly onClose: () => void;
  readonly children: ReactNode;
}

const FOCUSABLE =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function Modal({ kicker, title, onClose, children }: Props) {
  const titleId = useId();
  const panelRef = useRef<HTMLDivElement | null>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const panel = panelRef.current;
    const first = panel?.querySelector<HTMLElement>(FOCUSABLE);
    (first ?? panel)?.focus();

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        closeRef.current();
        return;
      }
      if (event.key !== "Tab" || panel === null) return;
      const nodes = Array.from(panel.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (nodes.length === 0) return;
      const firstNode = nodes[0];
      const lastNode = nodes[nodes.length - 1];
      if (event.shiftKey && document.activeElement === firstNode) {
        event.preventDefault();
        lastNode.focus();
      } else if (!event.shiftKey && document.activeElement === lastNode) {
        event.preventDefault();
        firstNode.focus();
      }
    }

    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      previous?.focus?.();
    };
  }, []);

  return (
    <div
      className="modal-backdrop"
      onMouseDown={(event) => {
        // 只有按在遮罩本身才关；从面板里拖选文字滑到遮罩上松手不该关。
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div
        ref={panelRef}
        className="modal-panel"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
      >
        <button type="button" className="modal-close" aria-label="关闭对话框" onClick={onClose}>
          <span aria-hidden="true">×</span>
          关闭
        </button>
        {kicker ? <div className="modal-kicker">{kicker}</div> : null}
        <h2 id={titleId}>{title}</h2>
        {children}
      </div>
    </div>
  );
}
