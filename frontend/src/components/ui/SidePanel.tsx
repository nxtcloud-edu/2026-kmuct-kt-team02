import { useEffect, useRef, type ReactNode } from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/cn";

/**
 * 상세 패널.
 * 데스크톱 1024px 이상에서는 오른쪽 400px 패널, 그 아래에서는 하단 시트(높이 90%까지).
 * (frontend/README.md 3-1)
 */
export function SidePanel({
  open,
  onClose,
  title,
  children,
  footer,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
  footer?: ReactNode;
}) {
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKeyDown);
    panelRef.current?.focus();

    const previous = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previous;
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex lg:justify-end">
      <div
        aria-hidden="true"
        onClick={onClose}
        className="absolute inset-0 animate-fade-in bg-ink-900/35"
      />

      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
        className={cn(
          "relative z-10 flex w-full flex-col bg-white shadow-panel focus:outline-none",
          "mt-auto max-h-[90vh] animate-slide-up-sheet rounded-t-3xl",
          "lg:mt-0 lg:h-full lg:max-h-none lg:w-[25rem] lg:animate-slide-in-right lg:rounded-none lg:border-l lg:border-line",
        )}
      >
        <div className="flex items-start justify-between gap-3 border-b border-line-soft px-5 py-4">
          <h2 className="text-[1.0625rem] font-bold text-ink-900">{title}</h2>
          <button
            type="button"
            onClick={onClose}
            aria-label="상세 닫기"
            className="-mr-1.5 -mt-1 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl text-ink-500 transition-colors hover:bg-canvas-200 hover:text-ink-800 focus-ring"
          >
            <X aria-hidden="true" className="h-5 w-5" />
          </button>
        </div>

        <div className="scrollbar-slim flex-1 overflow-y-auto px-5 py-5">{children}</div>

        {footer && (
          <div className="border-t border-line-soft bg-canvas-50 px-5 py-4">{footer}</div>
        )}
      </div>
    </div>
  );
}
