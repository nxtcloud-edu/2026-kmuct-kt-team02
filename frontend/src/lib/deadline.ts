/**
 * 마감 표시 규칙 (docs/01-glossary-profile.md 5장).
 *
 * D-day와 배지 문구는 서버가 계산해 `deadline.badge`로 내려준다.
 * 이 모듈은 목업 모드에서 같은 규칙으로 값을 만들고, 서버 값이 있으면 그대로 쓴다.
 * 모든 계산은 한국 시간 기준이다.
 */

import type { Deadline, PolicyDataStatus } from "./contract";

const MS_PER_DAY = 86_400_000;

/** 한국 시간(UTC+9) 기준 오늘 자정 */
function todayKST(now: Date = new Date()): Date {
  const kstMs = now.getTime() + 9 * 60 * 60 * 1000;
  const kst = new Date(kstMs);
  return new Date(
    Date.UTC(kst.getUTCFullYear(), kst.getUTCMonth(), kst.getUTCDate()),
  );
}

function parseISODate(value: string): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value.trim());
  if (!match) return null;
  const date = new Date(
    Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])),
  );
  return Number.isNaN(date.getTime()) ? null : date;
}

/** 마감일까지 남은 일수. 오늘이 마감이면 0, 지났으면 음수 */
export function daysUntil(endDate: string | null | undefined, now?: Date): number | null {
  if (!endDate) return null;
  const target = parseISODate(endDate);
  if (!target) return null;
  return Math.round((target.getTime() - todayKST(now).getTime()) / MS_PER_DAY);
}

/** 확인일로부터 지난 일수 */
export function daysSince(checkedAt: string, now?: Date): number | null {
  const date = parseISODate(checkedAt);
  if (!date) return null;
  return Math.round((todayKST(now).getTime() - date.getTime()) / MS_PER_DAY);
}

/** 확인일로부터 14일 초과면 재확인 필요 */
export function needsRecheck(
  checkedAt: string,
  dataStatus: PolicyDataStatus,
  now?: Date,
): boolean {
  if (dataStatus === "recheck") return true;
  const elapsed = daysSince(checkedAt, now);
  return elapsed !== null && elapsed > 14;
}

/**
 * 배지 문구를 만든다 (docs/01-glossary-profile.md 5장).
 * 오늘 마감 / 마감 임박 D-n / D-n / 상시 접수 / 접수 예정 (M.D 시작)
 */
export function buildBadge(
  applyStart: string | null | undefined,
  applyEnd: string | null | undefined,
  now?: Date,
): { badge: string; d_day: number | null; is_imminent: boolean } {
  const startDiff = applyStart ? daysUntil(applyStart, now) : null;

  // 접수 시작이 오늘 이후면 접수 예정
  if (startDiff !== null && startDiff > 0) {
    const start = parseISODate(applyStart!);
    const label = start
      ? `${start.getUTCMonth() + 1}.${start.getUTCDate()} 시작`
      : "시작 예정";
    return { badge: `접수 예정 (${label})`, d_day: null, is_imminent: false };
  }

  const dDay = daysUntil(applyEnd, now);

  if (dDay === null) return { badge: "상시 접수", d_day: null, is_imminent: false };
  if (dDay === 0) return { badge: "오늘 마감", d_day: 0, is_imminent: true };
  if (dDay > 0 && dDay <= 7) {
    return { badge: `마감 임박 D-${dDay}`, d_day: dDay, is_imminent: true };
  }
  if (dDay > 7) return { badge: `D-${dDay}`, d_day: dDay, is_imminent: false };

  // 마감이 지난 정책은 기본 결과에서 제외되므로 여기까지 오지 않는 것이 정상이다.
  return { badge: "접수 마감", d_day: dDay, is_imminent: false };
}

/** 서버가 준 deadline을 그대로 쓰되, 비어 있으면 규칙대로 채운다 */
export function resolveDeadline(deadline: Deadline, now?: Date): Deadline {
  if (deadline.badge) return deadline;
  const built = buildBadge(deadline.apply_start, deadline.apply_end, now);
  return { ...deadline, ...built };
}

/** 2026-10-15 → 2026년 10월 15일 */
export function formatKoreanDate(value: string | null | undefined): string {
  if (!value) return "미정";
  const date = parseISODate(value);
  if (!date) return value;
  return `${date.getUTCFullYear()}년 ${date.getUTCMonth() + 1}월 ${date.getUTCDate()}일`;
}

/** 2026-10-15 → 2026.10.15 */
export function formatDotDate(value: string | null | undefined): string {
  if (!value) return "미정";
  return value.slice(0, 10).replace(/-/g, ".");
}
