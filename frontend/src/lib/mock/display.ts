/**
 * 목업 정책을 판정 없이 표시용으로 바꾼다.
 *
 * 로그인하지 않은 상태와 정책 모아보기 화면에서 쓴다.
 * 프로필이 없으면 자격을 판정할 수 없으므로 조건은 모두 미확인으로 두고
 * 공고 원문 발췌만 각주로 연결한다. 근거 없는 판정을 내보내지 않는다.
 */

import { buildBadge, daysUntil, needsRecheck } from "../deadline";
import type { ConditionEvaluation, PolicyInfo } from "../contract";
import { MOCK_POLICIES, type MockPolicy } from "./policies";

/** 각주 번호를 이어 붙이며 정보용 정책으로 변환 */
export function toPolicyInfo(
  policy: MockPolicy,
  startFootnoteId = 1,
  now: Date = new Date(),
): { info: PolicyInfo; nextFootnoteId: number } {
  let footnoteId = startFootnoteId;

  const conditions: ConditionEvaluation[] = Object.entries(policy.condition_sources).map(
    ([name, excerpt]) => {
      const condition: ConditionEvaluation = {
        name,
        // 프로필이 없어 판정할 수 없다. 공고 기준만 보여 준다
        result: "unknown",
        judged_by: "rule",
        excerpt,
        source_url: policy.source_url,
        footnote_id: footnoteId,
        needed_field: "공고 확인 필요",
      };
      footnoteId += 1;
      return condition;
    },
  );

  const badge = buildBadge(policy.apply_start, policy.apply_end, now);
  const upcoming = policy.apply_start
    ? (daysUntil(policy.apply_start, now) ?? 0) > 0
    : false;

  return {
    info: {
      policy_id: policy.policy_id,
      title: policy.title,
      agency: policy.agency,
      categories: policy.categories,
      benefit: policy.benefit,
      conditions,
      deadline: {
        apply_start: policy.apply_start,
        apply_end: policy.apply_end,
        ...badge,
      },
      documents: policy.documents,
      steps: policy.steps,
      source_url: policy.source_url,
      apply_url: policy.apply_url,
      checked_at: policy.checked_at,
      data_status: upcoming
        ? "upcoming"
        : needsRecheck(policy.checked_at, "verified", now)
          ? "recheck"
          : "verified",
    },
    nextFootnoteId: footnoteId,
  };
}

/** 전체 정책을 정보용으로 변환 (정책 모아보기) */
export function allPolicyInfo(now: Date = new Date()): PolicyInfo[] {
  let footnoteId = 1;
  const items: PolicyInfo[] = [];
  for (const policy of MOCK_POLICIES) {
    const { info, nextFootnoteId } = toPolicyInfo(policy, footnoteId, now);
    footnoteId = nextFootnoteId;
    items.push(info);
  }
  return items;
}
