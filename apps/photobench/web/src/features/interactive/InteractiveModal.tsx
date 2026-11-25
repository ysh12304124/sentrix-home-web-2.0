import { useEffect, useRef, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

const dialogs: HTMLElement[] = [];
let bodyOverflow = '';

/** Only the explicit close control closes an interactive task. */
export function InteractiveModal({ title, onClose, closing = false, children }: {
  title: string; onClose: () => void; closing?: boolean; children: ReactNode;
}) {
  const dialog = useRef<HTMLElement>(null);
  useEffect(() => {
    const previousFocus = document.activeElement as HTMLElement | null;
    const element = dialog.current!;
    if (!dialogs.length) { bodyOverflow = document.body.style.overflow; document.body.style.overflow = 'hidden'; }
    dialogs.push(element); element.focus();
    return () => {
      dialogs.splice(dialogs.indexOf(element), 1);
      if (!dialogs.length) document.body.style.overflow = bodyOverflow;
      previousFocus?.focus();
    };
  }, []);
  return createPortal(<div className="interactive-modal-backdrop" data-testid="interactive-modal-backdrop">
    <section ref={dialog} tabIndex={-1} className="interactive-dialog" role="dialog" aria-modal="true" aria-label={title}
      onKeyDown={event => {
        if (event.key !== 'Tab' || dialogs.at(-1) !== dialog.current) return;
        const focusable = Array.from(event.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), summary, [tabindex="0"]'));
        const first = focusable[0], last = focusable[focusable.length - 1];
        if (event.shiftKey && (document.activeElement === first || document.activeElement === dialog.current)) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }}>
      <header className="interactive-modal-header"><h3>{title}</h3><button className="judge-modal-close" type="button" aria-label="关闭" disabled={closing} onClick={onClose}>×</button></header>
      <div className="interactive-modal-content">{children}</div>
    </section>
  </div>, document.body);
}
