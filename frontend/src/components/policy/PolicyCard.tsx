import { Building2 } from "lucide-react";
import { cn } from "@/lib/cn";
import type { PolicyEvaluation } from "@/lib/contract";
import { CATEGORY_LABEL, COPY } from "@/lib/labels";
import {
  DeadlineBadge,
  RecheckBadge,
  SourceLine,
  StatusBadge,
} from "@/components/ui/Surface";

/**
 * 정책 카드 (frontend/README.md 3-5).
 * 상태 배지, 제목·기관, 혜택 한 줄, 마감 배지, 조건부 문장, 출처 줄.
 */
export function PolicyCard({
  policy,
  onOpen,
  /** 상태가 방금 바뀌었으면 배지를 강조하고 업데이트됨을 보여 준다 */
  updated = false,
  className,
}: {
  policy: PolicyEvaluation;
  onOpen: (policy: PolicyEvaluation) => void;
  updated?: boolean;
  className?: string;
}) {
  return (
    <article
      className={cn(
        "flex flex-col rounded-2xl border border-line bg-white p-4 shadow-card transition-all duration-200",
        "hover:-translate-y-0.5 hover:border-brand-200 hover:shadow-card-hover",
        className,
      )}
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <StatusBadge status={policy.status} swap={updated} />
        <DeadlineBadge deadline={policy.deadline} />
        <RecheckBadge checkedAt={policy.checked_at} dataStatus={policy.data_status} />
        {updated && (
          <span className="rounded-full bg-brand-50 px-2 py-0.5 text-[0.8125rem] font-bold text-brand-700">
            {COPY.updated}
          </span>
        )}
      </div>

      <h3 className="mt-3">
        <button
          type="button"
          onClick={() => onOpen(policy)}
          className="line-clamp-2 rounded text-left text-[1.0625rem] font-bold leading-snug text-ink-900 transition-colors hover:text-brand-700 focus-ring"
        >
          {policy.title}
        </button>
      </h3>

      <p className="mt-1.5 flex items-center gap-1.5 text-[0.875rem] font-medium text-ink-500">
        <Building2 aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
        {policy.agency}
        <span aria-hidden="true">·</span>
        {policy.categories.map((item) => CATEGORY_LABEL[item]).join(", ")}
      </p>

      <p className="mt-3 rounded-xl border border-line-soft bg-canvas-50 px-3 py-2.5 text-[0.9375rem] font-semibold text-brand-700">
        {policy.benefit}
      </p>

      {policy.conditional_note && (
        <p className="mt-2 text-[0.875rem] font-medium leading-relaxed text-check">
          {policy.conditional_note}
        </p>
      )}

      <div className="mt-auto pt-3">
        <SourceLine
          agency={policy.agency}
          sourceUrl={policy.source_url}
          checkedAt={policy.checked_at}
        />
        <button
          type="button"
          onClick={() => onOpen(policy)}
          className="mt-2.5 w-full rounded-xl border border-line bg-white py-2 text-[0.875rem] font-semibold text-ink-700 transition-colors hover:border-line-strong hover:text-brand-700 focus-ring"
        >
          조건·서류 자세히 보기
        </button>
      </div>
    </article>
  );
}

/** 첫 결과 대기 중 카드 자리에 두는 뼈대 */
export function PolicyCardSkeleton() {
  return (
    <div className="rounded-2xl border border-line bg-white p-4">
      <div className="flex gap-2">
        <div className="skeleton h-6 w-28 rounded-full" />
        <div className="skeleton h-6 w-16 rounded-full" />
      </div>
      <div className="skeleton mt-3 h-5 w-3/4" />
      <div className="skeleton mt-2 h-4 w-1/2" />
      <div className="skeleton mt-3 h-10 w-full rounded-xl" />
      <div className="skeleton mt-3 h-4 w-2/3" />
    </div>
  );
}
