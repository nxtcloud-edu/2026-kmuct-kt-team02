/**
 * 목업 답변과 후속 질문.
 *
 * 실제 답변은 AI A(`ai/conversation/`)가 만든다. 이 모듈은 서버가 붙기 전까지
 * 화면을 검증하기 위한 것이고, 문구 규칙은 아래를 따른다.
 *
 * - 후속 질문 문구: ai/conversation/README.md 5장 고정 문구 표
 * - 답변 구성: 요약 → 정책별 설명 → 조건 안내 → 마감 강조 → 고정 문구
 * - 판정·조건·금액·날짜가 든 문장에는 반드시 각주를 붙인다
 * - 금지 표현(대상입니다, 받을 수 있어요, 확실히, 무조건, 100%, 보장, 걱정 마세요)을 쓰지 않는다
 */

import { COPY, INCOME_LABEL, STATUS_LABEL } from "../labels";
import { formatKoreanDate } from "../deadline";
import { conditionName, readProfileField } from "../contract";
import type {
  AskableProfileField,
  Footnote,
  FollowupField,
  FollowupQuestion,
  PolicyEvaluation,
  Profile,
  ProfileField,
  RelatedChip,
} from "../contract";

/* ------------------------------------------------------------------ *
 * 후속 질문 고정 문구 (ai/conversation/README.md 5장)
 * ------------------------------------------------------------------ */

interface FollowupTemplate {
  question: string;
  /** ○○와 n을 채워 넣는다 */
  reason: (policyTitle: string, count: number) => string;
  options: FollowupQuestion["options"];
  allow_free_text: boolean;
}

const FOLLOWUP_TEMPLATES: Partial<Record<AskableProfileField, FollowupTemplate>> = {
  income_bracket: {
    question: "가구 소득이 어느 정도인지 아세요?",
    reason: (title, count) => `${title} 등 ${count}개 제도가 소득 기준으로 갈려요`,
    options: (
      ["under_50", "50_100", "100_150", "over_150", "unknown"] as const
    ).map((value) => ({ value, label: INCOME_LABEL[value] })),
    allow_free_text: false,
  },
  district: {
    question: "어느 구에 사세요?",
    reason: () => "구에서 하는 지원이 있어요",
    options: ["관악구", "동작구", "성북구", "마포구", "광진구"].map((value) => ({
      value,
      label: value,
    })),
    allow_free_text: true,
  },
  housing_type: {
    question: "지금 어떻게 지내고 계세요?",
    reason: () => "월세 지원은 주거 형태에 따라 달라요",
    options: [
      { value: "parents", label: "부모님 집" },
      { value: "monthly_rent", label: "월세" },
      { value: "jeonse", label: "전세" },
      { value: "dormitory", label: "기숙사" },
    ],
    allow_free_text: false,
  },
  residence_period: {
    question: "서울에 산 지 얼마나 되셨어요?",
    reason: () => "거주 기간 조건이 있는 제도가 있어요",
    options: [
      { value: "under_6m", label: "6개월 미만" },
      { value: "6m_1y", label: "6개월~1년" },
      { value: "over_1y", label: "1년 이상" },
    ],
    allow_free_text: false,
  },
  remaining_semesters: {
    question: "졸업까지 몇 학기 남았어요?",
    reason: () => "졸업예정자 대상 제도가 있어요",
    options: [
      { value: "one", label: "1학기" },
      { value: "two_plus", label: "2학기 이상" },
    ],
    allow_free_text: false,
  },
  job_seeking_period: {
    question: "구직 활동한 지 얼마나 됐어요?",
    reason: () => "구직 기간 조건이 있어요",
    options: [
      { value: "under_6m", label: "6개월 미만" },
      { value: "over_6m", label: "6개월 이상" },
    ],
    allow_free_text: false,
  },
  employment_insurance: {
    question: "일하면서 고용보험에 가입한 적 있어요?",
    reason: () => "훈련 지원 조건과 관련 있어요",
    options: [
      { value: "yes", label: "있음" },
      { value: "no", label: "없음" },
      { value: "unknown", label: "모르겠어요" },
    ],
    allow_free_text: false,
  },
  other_benefit: {
    question: "지금 받고 있는 다른 청년 지원금이 있나요?",
    reason: () => "중복으로 못 받는 제도가 있어요",
    options: [
      { value: "yes", label: "있음" },
      { value: "no", label: "없음" },
    ],
    allow_free_text: false,
  },
  household_size: {
    question: "함께 사는 가족은 몇 명이에요?",
    reason: () => "소득 기준이 가구원 수로 달라져요",
    options: [
      { value: "1", label: "1인" },
      { value: "2", label: "2인" },
      { value: "3", label: "3인" },
      { value: "4_plus", label: "4인 이상" },
    ],
    allow_free_text: false,
  },
  last_gpa: {
    question: "직전 학기 성적이 공고 기준 이상인가요?",
    reason: () => "성적 조건이 있는 장학금이 있어요",
    options: [
      { value: "above", label: "기준 이상" },
      { value: "below", label: "기준 미만" },
      { value: "unknown", label: "모르겠어요" },
    ],
    allow_free_text: false,
  },
};

/** 물을 수 있는 항목이고 질문 문구가 준비된 항목인지 */
function isAskable(field: ProfileField): field is AskableProfileField {
  return Object.prototype.hasOwnProperty.call(FOLLOWUP_TEMPLATES, field);
}

/**
 * 후속 질문 선택 (docs/01-glossary-profile.md 8장).
 * 미확인 조건이 가리키는 항목 중, 가장 많은 정책의 판정에 영향을 주는 것을 고른다.
 * 이미 물었거나 건너뛴 항목은 제외한다. 고를 항목이 없으면 묻지 않는다.
 */
export function pickFollowup(
  policies: PolicyEvaluation[],
  profile: Profile,
  askedFields: FollowupField[],
): FollowupQuestion | null {
  const counts = new Map<AskableProfileField, { count: number; title: string }>();

  for (const policy of policies) {
    for (const condition of policy.conditions) {
      if (condition.result !== "unknown") continue;

      const needed = condition.needed_field;
      // 공고를 직접 확인해야 하는 조건은 물을 수 없다
      if (!needed || needed === "공고 확인 필요") continue;
      // needed_field에는 기본 항목도 올 수 있지만, 물을 수 있는 항목만 후속 질문으로 낸다
      if (!isAskable(needed)) continue;
      if (askedFields.includes(needed)) continue;
      // 이미 값이 있는 항목은 묻지 않는다
      if (readProfileField(profile, needed)) continue;

      const entry = counts.get(needed);
      if (entry) entry.count += 1;
      else counts.set(needed, { count: 1, title: policy.title });
    }
  }

  if (counts.size === 0) return null;

  const [field, info] = [...counts.entries()].sort((a, b) => b[1].count - a[1].count)[0];
  const template = FOLLOWUP_TEMPLATES[field]!;

  return {
    field,
    question: template.question,
    reason: template.reason(info.title, info.count),
    options: template.options,
    allow_free_text: template.allow_free_text,
    allow_skip: true,
  };
}

/* ------------------------------------------------------------------ *
 * 답변 작성
 * ------------------------------------------------------------------ */

/** 각주 목록. 발췌가 있는 조건만 (인용 검증 통과한 것만 내보낸다) */
export function buildFootnotes(policies: PolicyEvaluation[]): Footnote[] {
  const notes: Footnote[] = [];
  for (const policy of policies) {
    for (const condition of policy.conditions) {
      if (!condition.excerpt) continue;
      notes.push({
        footnote_id: condition.footnote_id,
        policy_id: policy.policy_id,
        excerpt: condition.excerpt,
        agency: policy.agency,
        checked_at: policy.checked_at,
        source_url: condition.source_url,
      });
    }
  }
  return notes;
}

/** 정책 하나를 설명하는 문장. 각주 번호를 붙인다 */
function describePolicy(policy: PolicyEvaluation): string {
  const primary = policy.conditions[0];
  const mark = primary ? `[${primary.footnote_id}]` : "";
  const label = STATUS_LABEL[policy.status];

  if (policy.status === "likely") {
    return `${policy.title}은 ${label}. ${policy.benefit}이고 마감은 ${formatKoreanDate(policy.deadline.apply_end)}이에요.${mark}`;
  }
  if (policy.status === "check") {
    const note = policy.conditional_note ? ` ${policy.conditional_note}.` : "";
    return `${policy.title}은 ${label}.${note} ${policy.benefit}이에요.${mark}`;
  }
  const unmet = policy.conditions.find((item) => item.result === "unmet");
  const because = unmet ? ` ${conditionName(unmet)} 조건이 맞지 않아요.` : "";
  return `${policy.title}은 ${label}.${because}${mark}`;
}

/**
 * 답변 본문.
 * 요약 → 정책별 설명 → 조건 안내 → 마감 강조 → 고정 문구. 600자 이내를 목표로 한다.
 */
export function buildAnswer(policies: PolicyEvaluation[]): string[] {
  if (policies.length === 0) {
    return [
      "지금 조건으로는 맞는 제도를 찾지 못했어요.",
      "관심 분야를 전체로 넓히거나 자치구를 알려주시면 다시 찾아볼게요.",
      "쏘다가 다루는 데이터는 검수된 서울시·중앙정부 제도 일부예요.",
      COPY.finalCheck,
    ];
  }

  const likely = policies.filter((item) => item.status === "likely").length;
  const check = policies.filter((item) => item.status === "check").length;

  const sentences: string[] = [];

  const summaryParts: string[] = [];
  if (likely > 0) summaryParts.push(`신청 가능성이 높은 제도 ${likely}개`);
  if (check > 0) summaryParts.push(`확인이 필요한 제도 ${check}개`);
  sentences.push(
    summaryParts.length > 0
      ? `${summaryParts.join(", ")}를 찾았어요.`
      : `제도 ${policies.length}개를 찾았어요.`,
  );

  for (const policy of policies) sentences.push(describePolicy(policy));

  if (check > 0) {
    sentences.push(
      "확인이 필요한 제도는 남은 조건만 채우면 판정이 확정돼요. 아래 질문에 답해 주시면 바로 다시 계산할게요.",
    );
  }

  const imminent = policies.filter((item) => item.deadline.is_imminent);
  if (imminent.length > 0) {
    sentences.push(
      `${imminent.map((item) => item.title).join(", ")}은 마감이 가까워요. 먼저 준비하시면 좋아요.`,
    );
  }

  sentences.push(COPY.finalCheck);
  return sentences;
}

/** 관련 질문 칩 (P1). 최대 3개. id는 화면에 쓰지 않고 중복 방지에만 쓴다 */
export function buildRelated(policies: PolicyEvaluation[]): RelatedChip[] {
  const chips: RelatedChip[] = [];
  if (policies.some((item) => item.status === "check")) {
    chips.push({ id: "income_check_howto", text: "소득 기준 확인하는 방법 알려줘" });
  }
  if (policies.some((item) => item.status === "unlikely")) {
    chips.push({ id: "why_unlikely", text: "조건이 안 맞는 이유가 뭐야?" });
  }
  if (policies.length >= 2) {
    chips.push({ id: "compare_top_two", text: "상위 두 개 비교해줘" });
  }
  if (policies.some((item) => item.deadline.is_imminent)) {
    chips.push({ id: "imminent_order", text: "마감 임박한 것부터 준비 순서 알려줘" });
  }
  return chips.slice(0, 3);
}
