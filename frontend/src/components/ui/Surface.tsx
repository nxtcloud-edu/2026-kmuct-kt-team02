import type { ElementType, ReactNode } from "react";
import {
  AlertTriangle,
  CalendarClock,
  CheckCircle2,
  HelpCircle,
  ShieldCheck,
  XCircle,
} from "lucide-react";
import { cn } from "@/lib/cn";
import { CONDITION_LABEL, STATUS_LABEL } from "@/lib/labels";
import { formatDotDate, needsRecheck } from "@/lib/deadline";
import type {
  ConditionResult,
  Deadline,
  EvaluationStatus,
  PolicyDataStatus,
} from "@/lib/contract";

export function Card({
  children,
  className,
  as: Tag = "div",
}: {
  children: ReactNode;
  className?: string;
  as?: ElementType;
}) {
  return <Tag className={cn("card", className)}>{children}</Tag>;
}

/** 배경 그라데이션과 구체 장식 */
export function PageBackground() {
  return (
    <div
      aria-hidden="true"
      className="pointer-events-none fixed inset-0 -z-10 overflow-hidden bg-canvas-100"
    >
      {/* 위쪽에서 은근히 번지는 빛 */}
      <div className="absolute inset-x-0 top-0 h-[28rem] bg-gradient-to-b from-brand-100/60 via-canvas-100/40 to-transparent" />

      <div className="absolute -right-28 -top-36 h-[32rem] w-[32rem] animate-float rounded-full bg-aurora-violet/25 blur-[110px]" />
      <div className="absolute -left-36 top-1/4 h-[28rem] w-[28rem] animate-float-slow rounded-full bg-aurora-blue/22 blur-[110px]" />
      <div className="absolute bottom-[-10rem] right-1/4 h-[26rem] w-[26rem] animate-float rounded-full bg-aurora-sky/18 blur-[120px]" />

      {/* 작은 구체 장식 */}
      <div className="absolute right-[9%] top-[13%] h-16 w-16 animate-float-slow rounded-full bg-gradient-to-br from-brand-300 to-brand-600 opacity-75 shadow-card" />
      <div className="absolute left-[7%] top-[58%] h-9 w-9 animate-float rounded-full bg-gradient-to-br from-aurora-sky to-aurora-blue opacity-65" />
      <div className="absolute left-[28%] top-[18%] h-5 w-5 animate-float rounded-full bg-gradient-to-br from-brand-200 to-brand-400 opacity-60" />
    </div>
  );
}

/* ------------------------------------------------------------------ *
 * 상태 배지 — 색만으로 구분하지 않고 아이콘과 문구를 함께 쓴다
 * ------------------------------------------------------------------ */

const STATUS_STYLE: Record<EvaluationStatus, string> = {
  likely: "border-likely-border bg-likely-bg text-likely",
  check: "border-check-border bg-check-bg text-check",
  unlikely: "border-unlikely-border bg-unlikely-bg text-unlikely",
};

const STATUS_ICON: Record<EvaluationStatus, ElementType> = {
  likely: CheckCircle2,
  check: HelpCircle,
  unlikely: XCircle,
};

export function StatusBadge({
  status,
  className,
  /** 상태가 바뀔 때 잠깐 강조 */
  swap = false,
}: {
  status: EvaluationStatus;
  className?: string;
  swap?: boolean;
}) {
  const Icon = STATUS_ICON[status];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[0.875rem] font-bold",
        STATUS_STYLE[status],
        swap && "animate-badge-swap",
        className,
      )}
    >
      <Icon aria-hidden="true" className="h-3.5 w-3.5" />
      {STATUS_LABEL[status]}
    </span>
  );
}

const CONDITION_STYLE: Record<ConditionResult, string> = {
  met: "text-likely",
  unmet: "text-urgent",
  unknown: "text-check",
};

const CONDITION_ICON: Record<ConditionResult, ElementType> = {
  met: CheckCircle2,
  unmet: XCircle,
  unknown: HelpCircle,
};

export function ConditionIcon({ result }: { result: ConditionResult }) {
  const Icon = CONDITION_ICON[result];
  return (
    <Icon
      aria-hidden="true"
      className={cn("h-4 w-4 shrink-0", CONDITION_STYLE[result])}
    />
  );
}

export function conditionLabelClass(result: ConditionResult): string {
  return CONDITION_STYLE[result];
}

export { CONDITION_LABEL };

/* ------------------------------------------------------------------ *
 * 마감 배지 — 서버가 준 badge 문구를 그대로 쓴다. 빨강은 임박에만
 * ------------------------------------------------------------------ */

export function DeadlineBadge({
  deadline,
  className,
}: {
  deadline: Deadline;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-[0.875rem] font-bold",
        deadline.is_imminent
          ? "border-urgent-border bg-urgent-bg text-urgent"
          : "border-line bg-canvas-100 text-ink-600",
        className,
      )}
    >
      <CalendarClock aria-hidden="true" className="h-3.5 w-3.5" />
      {deadline.badge}
    </span>
  );
}

/** 확인일 14일 초과 시 재확인 필요 표시 */
export function RecheckBadge({
  checkedAt,
  dataStatus,
  className,
}: {
  checkedAt: string;
  dataStatus: PolicyDataStatus;
  className?: string;
}) {
  if (!needsRecheck(checkedAt, dataStatus)) return null;
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border border-check-border bg-check-bg px-2.5 py-1 text-[0.875rem] font-bold text-check",
        className,
      )}
    >
      <AlertTriangle aria-hidden="true" className="h-3.5 w-3.5" />
      재확인 필요
    </span>
  );
}

/** 출처 + 최종 확인일 (FR12) */
export function SourceLine({
  agency,
  sourceUrl,
  checkedAt,
  className,
}: {
  agency: string;
  sourceUrl: string;
  checkedAt: string;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-x-1.5 gap-y-1 text-[0.875rem] text-ink-500",
        className,
      )}
    >
      <ShieldCheck aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
      <span>출처: {agency}</span>
      <span aria-hidden="true">·</span>
      <span>확인일 {formatDotDate(checkedAt)}</span>
      <a
        href={sourceUrl}
        target="_blank"
        rel="noreferrer noopener"
        className="rounded font-semibold text-brand-600 underline decoration-brand-200 decoration-2 underline-offset-2 transition-colors hover:text-brand-700 focus-ring"
      >
        공고 원문 보기
      </a>
    </div>
  );
}

export function SectionHeading({
  eyebrow,
  title,
  description,
  action,
  className,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-wrap items-end justify-between gap-3", className)}>
      <div>
        {eyebrow && (
          <p className="mb-1.5 text-[0.875rem] font-bold uppercase tracking-[0.14em] text-brand-500">
            {eyebrow}
          </p>
        )}
        <h2 className="text-xl font-bold text-ink-900 sm:text-2xl">{title}</h2>
        {description && (
          <p className="mt-2 max-w-3xl text-[0.9375rem] leading-relaxed text-ink-600">
            {description}
          </p>
        )}
      </div>
      {action}
    </div>
  );
}

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  className,
}: {
  icon: ElementType;
  title: string;
  description?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center px-6 py-10 text-center",
        className,
      )}
    >
      <span
        aria-hidden="true"
        className="flex h-14 w-14 items-center justify-center rounded-2xl border border-line bg-canvas-50"
      >
        <Icon className="h-6 w-6 text-brand-500" />
      </span>
      <p className="mt-4 text-[1.0625rem] font-bold text-ink-900">{title}</p>
      {description && (
        <div className="mt-2 max-w-md text-[0.9375rem] leading-relaxed text-ink-600">
          {description}
        </div>
      )}
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}
