/**
 * 정렬 규칙 (docs/01-glossary-profile.md 6장).
 *
 * 서버가 이미 정렬해 내려주지만, 목업 모드와 정책 모아보기 화면에서 같은 순서를 쓰려면
 * 프론트에도 같은 규칙이 필요하다. 판정 로직이 아니라 표시 순서만 다룬다.
 */

import type { Category, EvaluationStatus, PolicyEvaluation } from "./contract";

const STATUS_RANK: Record<EvaluationStatus, number> = {
  likely: 0,
  check: 1,
  unlikely: 2,
};

/** 마감일 정렬 키. 상시 접수는 뒤, 접수 예정은 그 뒤 */
function deadlineKey(policy: PolicyEvaluation): [number, string] {
  const { apply_end } = policy.deadline;
  if (policy.data_status === "upcoming") return [2, policy.deadline.apply_start ?? ""];
  if (!apply_end) return [1, ""];
  return [0, apply_end];
}

/**
 * 표시 순서:
 * 1. 판정 상태 likely → check → unlikely
 * 2. 마감 임박 먼저
 * 3. 마감일 빠른 순 (상시 접수는 뒤, 접수 예정은 그 뒤)
 * 4. 관심 분야 일치 수 많은 순
 * 5. policy_id 순
 */
export function sortPolicies(
  policies: PolicyEvaluation[],
  interests: Category[] = [],
): PolicyEvaluation[] {
  const interestSet = new Set(interests);
  const matchCount = (policy: PolicyEvaluation) =>
    interestSet.has("all")
      ? policy.categories.length
      : policy.categories.filter((item) => interestSet.has(item)).length;

  return [...policies].sort((a, b) => {
    const byStatus = STATUS_RANK[a.status] - STATUS_RANK[b.status];
    if (byStatus !== 0) return byStatus;

    const byImminent = Number(b.deadline.is_imminent) - Number(a.deadline.is_imminent);
    if (byImminent !== 0) return byImminent;

    const [aGroup, aDate] = deadlineKey(a);
    const [bGroup, bDate] = deadlineKey(b);
    if (aGroup !== bGroup) return aGroup - bGroup;
    if (aDate !== bDate) return aDate.localeCompare(bDate);

    const byMatch = matchCount(b) - matchCount(a);
    if (byMatch !== 0) return byMatch;

    return a.policy_id.localeCompare(b.policy_id);
  });
}

/**
 * 조건 표시 순서: 미충족 → 미확인 → 충족 (docs/03-api-contract.md 4-3)
 */
const CONDITION_RANK = { unmet: 0, unknown: 1, met: 2 } as const;

export function sortConditions(
  conditions: PolicyEvaluation["conditions"],
): PolicyEvaluation["conditions"] {
  return [...conditions].sort(
    (a, b) => CONDITION_RANK[a.result] - CONDITION_RANK[b.result],
  );
}
