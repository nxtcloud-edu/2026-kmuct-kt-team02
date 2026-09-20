import { useEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { X } from "lucide-react";
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

    // 바깥 클릭은 배경(`onMouseDown`)이 처리한다. 여기서 document 리스너로
    // `containerRef.contains` 를 보면 안 된다 — 내용이 `body` 로 포털돼 있어서
    // **모달 안을 클릭해도 "바깥"으로 판정되어 바로 닫힌다.** 발췌 안의 링크를
    // 누를 수 없게 된다.
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };

    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
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

      {open &&
        createPortal(
          // 화면 가운데 모달로 띄운다.
          //
          // 이전에는 트리거 기준 `absolute` 였다. 각주 버튼이 정책 상세 사이드패널 안에
          // 있으면 그 패널이 오른쪽 끝에 붙어 있어서, 팝오버가 패널 폭 안에 갇히거나
          // 화면 밖으로 밀려 발췌가 보이지 않았다. 각주를 눌러 원문을 확인하는 것이
          // 이 제품의 신뢰 포인트인데(docs/06 데모 4단계) 그게 화면에서 잘렸다.
          //
          // 부모의 `overflow`·`z-index`·`transform` 에 갇히지 않도록 `body` 로
          // 포털한다. 사이드패널 안에서 열려도 항상 앞에 온다.
          <div
            className="fixed inset-0 z-50 flex items-center justify-center p-4"
            onMouseDown={(event) => {
              if (event.target === event.currentTarget) setOpen(false);
            }}
          >
            <div aria-hidden="true" className="absolute inset-0 bg-ink-900/40" />
            <div
              role="dialog"
              aria-modal="true"
              aria-label={triggerLabel}
              className={cn(
                "relative z-10 max-h-[80vh] w-[min(28rem,calc(100vw-2rem))] overflow-y-auto",
                "animate-fade-up rounded-2xl border border-line bg-white p-5 text-left shadow-panel",
                className,
              )}
            >
              <button
                type="button"
                aria-label="닫기"
                onClick={() => {
                  setOpen(false);
                  triggerRef.current?.focus();
                }}
                className="absolute right-3 top-3 rounded-lg p-1 text-ink-500 transition-colors hover:bg-canvas-100 hover:text-ink-800 focus-ring"
              >
                <X aria-hidden="true" className="h-4 w-4" />
              </button>
              {children}
            </div>
          </div>,
          document.body,
        )}
    </span>
  );
}
