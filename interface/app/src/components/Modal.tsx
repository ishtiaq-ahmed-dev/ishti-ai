import { useEffect, useRef, type MouseEventHandler, type ReactNode } from "react";
import { cn } from "@/lib/utils";

/** A native <dialog> shown modally while mounted: focus moves in and is trapped, the page behind is inert,
 *  Esc calls onClose (no onClose = Esc does nothing, e.g. a shutdown progress screen), focus returns on close.
 *  The dialog itself is the full-screen overlay, so callers keep their own overlay classes. */
export function Modal({ label, onClose, onClick, className, children }: {
  label: string;
  onClose?: () => void;
  onClick?: MouseEventHandler<HTMLDialogElement>;
  className?: string;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (d && !d.open) d.showModal();
    return () => d?.close();
  }, []);
  return (
    <dialog ref={ref} aria-label={label} onClick={onClick}
      onCancel={(e) => { e.preventDefault(); onClose?.(); }}
      className={cn("fixed inset-0 m-0 h-full max-h-none w-full max-w-none text-foreground backdrop:bg-transparent open:flex",
        className)}>
      {children}
    </dialog>
  );
}
