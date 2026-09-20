import { Check, Loader2 } from "lucide-react";
import { cn } from "@/lib/cn";
import { STAGE_LABEL, STAGE_ORDER } from "@/lib/labels";
import type { Stage } from "@/lib/contract";

/**
 * 진행 단계 (frontend/README.md 3-4).
 * 정책 찾는 중 → 조건 확인 중 → 정리 중. 현재 단계 강조, 완료 단계 체크.
 * 낭독은 LiveRegion이 담당하므로 여기서는 aria-live를 쓰지 않는다.
 */
export function StatusSteps({ stage }: { stage: Stage }) {
  const activeIndex = STAGE_ORDER.indexOf(stage);

  return (
    <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5 rounded-xl border border-line-soft bg-canvas-50 px-3 py-2">
      {STAGE_ORDER.map((step, index) => {
        const done = activeIndex > index;
        const active = activeIndex === index;

        return (
          <span key={step} className="flex items-center gap-2">
            <span
              className={cn(
                "inline-flex items-center gap-1.5 text-[0.875rem] font-semibold",
                done && "text-likely",
                active && "text-brand-700",
                !done && !active && "text-ink-400",
              )}
            >
              {done ? (
                <Check aria-hidden="true" className="h-3.5 w-3.5" />
              ) : active ? (
                <Loader2 aria-hidden="true" className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-current" />
              )}
              {STAGE_LABEL[step]}
            </span>
            {index < STAGE_ORDER.length - 1 && (
              <span aria-hidden="true" className="text-ink-400">
                ›
              </span>
            )}
          </span>
        );
      })}
    </div>
  );
}
