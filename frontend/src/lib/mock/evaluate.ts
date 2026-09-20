/**
 * 목업 규칙 판정.
 *
 * 실제 판정은 `rules/`(백엔드A 소유)와 서버가 한다. 이 모듈은 서버가 붙기 전까지
 * 화면을 검증하기 위한 것이고, 판정 기준은 docs/04-data-schema.md 5장을 그대로 따른다.
 * 서버 연결 후에는 호출되지 않는다.
 */

import { buildBadge, daysUntil, needsRecheck } from "../deadline";
import { sortPolicies } from "../sort";
import { STATUS_LABEL } from "../labels";
import { conditionName } from "../contract";
import type {
  Category,
  ConditionEvaluation,
  ConditionResult,
  EvaluationStatus,
  IncomeBracket,
  PolicyEvaluation,
  Profile,
} from "../contract";
import { MOCK_POLICIES, type MockPolicy } from "./policies";

/** 소득 구간의 하한·상한 % (docs/01-glossary-profile.md 2장) */
const INCOME_RANGE: Record<IncomeBracket, { min: number; max: number } | null> = {
  under_50: { min: 0, max: 50 },
  "50_100": { min: 50, max: 100 },
  "100_150": { min: 100, max: 150 },
  over_150: { min: 150, max: Number.POSITIVE_INFINITY },
  unknown: null,
};

/** 나이 조건 */
function checkAge(policy: MockPolicy, profile: Profile): ConditionResult | null {
  if (policy.age_min === null && policy.age_max === null) return null;
  const { age } = profile;
  if (policy.age_min !== null && age < policy.age_min) return "unmet";
  if (policy.age_max !== null && age > policy.age_max) return "unmet";
  return "met";
}

/** 신분 조건 */
function checkStatus(policy: MockPolicy, profile: Profile): ConditionResult | null {
  if (policy.statuses.length === 0) return null;
  return policy.statuses.includes(profile.status) ? "met" : "unmet";
}

/**
 * 소득 조건.
 * 사용자 구간 상한 ≤ 기준이면 충족, 하한 ≥ 기준이면 미충족, 걸치거나 모르면 미확인.
 * income_max_pct가 비어 있으면 조건 자체를 표시하지 않는다.
 */
function checkIncome(policy: MockPolicy, profile: Profile): ConditionResult | null {
  if (policy.income_max_pct === null) return null;
  const range = INCOME_RANGE[profile.income_bracket];
  if (!range) return "unknown";
  if (range.max <= policy.income_max_pct) return "met";
  if (range.min >= policy.income_max_pct) return "unmet";
  return "unknown";
}

/** 조건 이름 → 필요한 추가 항목 */
const NEEDED_FIELD: Record<string, ConditionEvaluation["needed_field"]> = {
  소득: "income_bracket",
};

function buildConditions(
  policy: MockPolicy,
  profile: Profile,
  startFootnoteId: number,
): { conditions: ConditionEvaluation[]; nextFootnoteId: number } {
  const checks: Array<[string, ConditionResult | null]> = [
    ["나이", checkAge(policy, profile)],
    ["신분", checkStatus(policy, profile)],
    ["소득", checkIncome(policy, profile)],
  ];

  const conditions: ConditionEvaluation[] = [];
  let footnoteId = startFootnoteId;

  for (const [name, result] of checks) {
    if (result === null) continue;
    const excerpt = policy.condition_sources[name];
    // 근거 문장이 없으면 조건 설명을 내보내지 않는다 (FR07).
    if (!excerpt) continue;

    conditions.push({
      name,
      result,
      judged_by: "rule",
      excerpt,
      source_url: policy.source_url,
      footnote_id: footnoteId,
      needed_field: result === "unknown" ? (NEEDED_FIELD[name] ?? "공고 확인 필요") : null,
    });
    footnoteId += 1;
  }

  return { conditions, nextFootnoteId: footnoteId };
}

/** 판정 상태 결정 (docs/01-glossary-profile.md 4장) */
function decideStatus(conditions: ConditionEvaluation[]): EvaluationStatus {
  if (conditions.some((item) => item.result === "unmet")) return "unlikely";
  if (conditions.some((item) => item.result === "unknown")) return "check";
  return "likely";
}

/** 조건부 문장 (FR08). 고정 형식에 값만 채운다 */
function buildConditionalNote(conditions: ConditionEvaluation[]): string | null {
  const unknown = conditions.filter((item) => item.result === "unknown");
  if (unknown.length === 0) return null;
  return `${unknown.map(conditionName).join(", ")} 조건을 확인하면 신청 가능해요`;
}

/** 관심 분야가 하나 이상 겹치는지 */
function matchesInterest(policy: MockPolicy, categories: Category[]): boolean {
  if (categories.includes("all")) return true;
  return policy.categories.some((item) => categories.includes(item));
}

/** 키워드로 관련도 점수. 대화 메시지가 있을 때만 쓴다 */
function relevance(policy: MockPolicy, query: string): number {
  if (!query) return 0;
  const text = query.toLowerCase();
  let score = 0;
  for (const keyword of policy.keywords) {
    if (text.includes(keyword)) score += 1;
  }
  return score;
}

function evaluateOne(
  policy: MockPolicy,
  profile: Profile,
  startFootnoteId: number,
): { evaluation: PolicyEvaluation; nextFootnoteId: number } {
  const { conditions, nextFootnoteId } = buildConditions(
    policy,
    profile,
    startFootnoteId,
  );
  const status = decideStatus(conditions);
  const badge = buildBadge(policy.apply_start, policy.apply_end);
  const recheck = needsRecheck(policy.checked_at, "verified");
  const upcoming = policy.apply_start ? (daysUntil(policy.apply_start) ?? 0) > 0 : false;

  return {
    evaluation: {
      policy_id: policy.policy_id,
      title: policy.title,
      agency: policy.agency,
      categories: policy.categories,
      status,
      status_label: STATUS_LABEL[status],
      benefit: policy.benefit,
      conditions,
      conditional_note: status === "check" ? buildConditionalNote(conditions) : null,
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
      data_status: upcoming ? "upcoming" : recheck ? "recheck" : "verified",
    },
    nextFootnoteId,
  };
}

export interface MockEvaluateResult {
  policies: PolicyEvaluation[];
  hidden_unlikely: PolicyEvaluation[];
  hidden_unlikely_count: number;
}

/**
 * 후보 선정과 판정 (docs/01-glossary-profile.md 7장).
 * 기본 표시: likely·check 상위 5개. 접힌 영역: unlikely 상위 3개.
 */
export function mockEvaluate(
  profile: Profile,
  query = "",
  now: Date = new Date(),
): MockEvaluateResult {
  const candidates = MOCK_POLICIES.filter((policy) => {
    // 마감이 지난 정책은 후보에서 뺀다
    const dDay = daysUntil(policy.apply_end, now);
    if (dDay !== null && dDay < 0) return false;
    return matchesInterest(policy, profile.categories);
  });

  // 대화 메시지가 있으면 관련 있는 정책을 먼저 둔다
  const ordered = query
    ? [...candidates].sort((a, b) => relevance(b, query) - relevance(a, query))
    : candidates;

  let footnoteId = 1;
  const evaluated: PolicyEvaluation[] = [];
  for (const policy of ordered) {
    const { evaluation, nextFootnoteId } = evaluateOne(policy, profile, footnoteId);
    footnoteId = nextFootnoteId;
    evaluated.push(evaluation);
  }

  const visible = sortPolicies(
    evaluated.filter((item) => item.status !== "unlikely"),
    profile.categories,
  ).slice(0, 5);

  const hidden = sortPolicies(
    evaluated.filter((item) => item.status === "unlikely"),
    profile.categories,
  ).slice(0, 3);

  return {
    policies: visible,
    hidden_unlikely: hidden,
    hidden_unlikely_count: hidden.length,
  };
}
