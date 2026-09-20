import { useEffect, useRef, useState, type ReactNode } from "react";
import { cn } from "@/lib/cn";

/**
 * 가벼운 팝오버. 각주에 쓴다.
 * 바깥 클릭과 ESC로 닫히고 트리거로 포커스를 되돌린다 (frontend/README.md 3-6).
 * 이미 받아 둔 데이터로 즉시 열어야 하므로 여기서 네트워크 호출을 하지 않는다.
 */
export function Popover({
  trigger,
  triggerLabel,
  triggerClassName,
  disabled,
  children,
  className,
}: {
  trigger: ReactNode;
  triggerLabel: string;
  triggerClassName?: string;
  disabled?: boolean;
  children: ReactNode;
  className?: string;
}) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLSpanElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;

    const onPointerDown = (event: MouseEvent | TouchEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };

    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("touchstart", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("touchstart", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <span ref={containerRef} className="relative inline-block">
      <button
        ref={triggerRef}
        type="button"
        disabled={disabled}
        aria-expanded={open}
        aria-label={triggerLabel}
        onClick={() => setOpen((value) => !value)}
        className={cn("rounded focus-ring", triggerClassName)}
      >
        {trigger}
      </button>

      {open && (
        <span
          role="dialog"
          aria-label={triggerLabel}
          className={cn(
            "absolute bottom-[calc(100%+0.5rem)] left-1/2 z-30 block w-[min(20rem,calc(100vw-3rem))] -translate-x-1/2",
            "animate-fade-up rounded-2xl border border-line bg-white p-4 text-left shadow-panel",
            className,
          )}
        >
          {children}
        </span>
      )}
    </span>
  );
}
