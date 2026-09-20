/**
 * 화면 문구 표.
 *
 * 값과 문구를 분리한다 (CONTRIBUTING.md 7-2). 코드 여기저기에 한국어를 직접 쓰지 않는다.
 * 출처: docs/01-glossary-profile.md, frontend/README.md, ai/conversation/README.md 5장
 */

import type {
  AskableProfileField,
  Category,
  ConditionResult,
  EvaluationStatus,
  HouseholdSize,
  HousingType,
  IncomeBracket,
  JobSeekingPeriod,
  LastGpa,
  ProfileField,
  RemainingSemesters,
  ResidencePeriod,
  Stage,
  UserStatus,
  YesNo,
  YesNoUnknown,
} from "./contract";

/* ------------------------------------------------------------------ *
 * 판정 상태 (docs/01-glossary-profile.md 4장)
 * ------------------------------------------------------------------ */

export const STATUS_LABEL: Record<EvaluationStatus, string> = {
  likely: "신청 가능성이 높아요",
  check: "확인이 필요해요",
  unlikely: "어려울 수 있어요",
};

export const CONDITION_LABEL: Record<ConditionResult, string> = {
  met: "충족",
  unmet: "미충족",
  unknown: "미확인",
};

/* ------------------------------------------------------------------ *
 * 진행 단계
 * ------------------------------------------------------------------ */

export const STAGE_LABEL: Record<Stage, string> = {
  searching: "정책 찾는 중",
  checking: "조건 확인 중",
  summarizing: "정리 중",
};

export const STAGE_ORDER: Stage[] = ["searching", "checking", "summarizing"];

/* ------------------------------------------------------------------ *
 * 프로필 값 문구
 * ------------------------------------------------------------------ */

export const USER_STATUS_LABEL: Record<UserStatus, string> = {
  enrolled: "재학",
  on_leave: "휴학",
  final_semester: "졸업예정",
  job_seeking: "졸업 후 구직",
  employed: "재직",
};

export const CATEGORY_LABEL: Record<Category, string> = {
  scholarship: "장학·교육",
  living: "생활비",
  job: "취업·훈련",
  culture: "교통·통신·문화",
  housing: "주거",
  all: "전체",
};

export const INCOME_LABEL: Record<IncomeBracket, string> = {
  under_50: "중위소득 50% 이하",
  "50_100": "50~100%",
  "100_150": "100~150%",
  over_150: "150% 초과",
  unknown: "잘 모르겠어요",
};

export const HOUSING_LABEL: Record<HousingType, string> = {
  parents: "부모님 집",
  monthly_rent: "월세",
  jeonse: "전세",
  dormitory: "기숙사",
  other: "기타",
};

export const RESIDENCE_LABEL: Record<ResidencePeriod, string> = {
  under_6m: "6개월 미만",
  "6m_1y": "6개월~1년",
  over_1y: "1년 이상",
};

export const SEMESTER_LABEL: Record<RemainingSemesters, string> = {
  one: "1학기",
  two_plus: "2학기 이상",
};

export const JOB_SEEKING_LABEL: Record<JobSeekingPeriod, string> = {
  under_6m: "6개월 미만",
  over_6m: "6개월 이상",
};

export const YES_NO_UNKNOWN_LABEL: Record<YesNoUnknown, string> = {
  yes: "있음",
  no: "없음",
  unknown: "모르겠어요",
};

export const YES_NO_LABEL: Record<YesNo, string> = {
  yes: "있음",
  no: "없음",
};

export const HOUSEHOLD_LABEL: Record<HouseholdSize, string> = {
  "1": "1인",
  "2": "2인",
  "3": "3인",
  "4_plus": "4인 이상",
};

export const GPA_LABEL: Record<LastGpa, string> = {
  above: "기준 이상",
  below: "기준 미만",
  unknown: "모름",
};

/** 프로필 항목 이름 */
export const PROFILE_FIELD_LABEL: Record<ProfileField, string> = {
  age: "나이",
  status: "현재 상태",
  categories: "관심 분야",
  district: "자치구",
  income_bracket: "가구 소득",
  housing_type: "주거 형태",
  residence_period: "서울 거주 기간",
  remaining_semesters: "남은 학기",
  job_seeking_period: "구직 기간",
  employment_insurance: "고용보험 가입 이력",
  other_benefit: "다른 지원 수혜",
  household_size: "가구원 수",
  last_gpa: "직전 학기 성적",
};

/**
 * 후속 질문이 묻는 대상 이름.
 * planned_basis는 프로필 항목이 아니라 기준 시점을 묻는 것이다.
 */
export const FOLLOWUP_FIELD_LABEL: Record<string, string> = {
  ...PROFILE_FIELD_LABEL,
  planned_basis: "기준 시점",
};

/** 추가 항목 값을 화면 문구로 바꾼다 */
const ASKABLE_VALUE_LABEL: Record<AskableProfileField, Record<string, string>> = {
  district: {},
  income_bracket: INCOME_LABEL,
  housing_type: HOUSING_LABEL,
  residence_period: RESIDENCE_LABEL,
  remaining_semesters: SEMESTER_LABEL,
  job_seeking_period: JOB_SEEKING_LABEL,
  employment_insurance: YES_NO_UNKNOWN_LABEL,
  other_benefit: YES_NO_LABEL,
  household_size: HOUSEHOLD_LABEL,
  last_gpa: GPA_LABEL,
};

/** 프로필 항목과 값을 사람이 읽는 문구로 (자치구는 값 그대로) */
export function formatProfileValue(
  field: ProfileField,
  value: unknown,
): string {
  if (value === null || value === undefined || value === "") return "미확인";

  if (field === "age") return `만 ${String(value)}세`;
  if (field === "status") return USER_STATUS_LABEL[value as UserStatus] ?? String(value);
  if (field === "categories") {
    const list = Array.isArray(value) ? (value as Category[]) : [];
    return list.map((item) => CATEGORY_LABEL[item] ?? item).join(", ");
  }

  const table = ASKABLE_VALUE_LABEL[field as AskableProfileField];
  if (table && table[String(value)]) return table[String(value)];
  return String(value);
}

/* ------------------------------------------------------------------ *
 * 화면 고정 문구
 * ------------------------------------------------------------------ */

export const COPY = {
  /** 온보딩 (frontend/README.md 3-2) */
  onboardingTitle: "지금 받을 수 있는 청년 지원, 30초면 찾아요",
  onboardingDescription:
    "기본 정보만 알려주시면 바로 찾아볼게요. 나머지는 대화하면서 여쭤볼게요.",
  incomeHelp:
    "몰라도 괜찮아요. 소득 기준이 있는 제도는 조건을 알려드려요.",
  privacyNotice:
    "입력한 정보는 맞춤 안내를 위해 AI 모델로 전송되며 저장되지 않아요.",
  onboardingSubmit: "시작하기",

  /** 홈 (frontend/README.md 3-3) */
  chatPlaceholder: "지금 상황이나 필요한 걸 편하게 말해 주세요",
  browseWithoutQuestion: "바로 찾아보기",

  /** 민감정보 안내 (FR15) */
  piiNotice: "주민등록번호나 계좌번호는 입력하지 않아도 돼요",

  /** 결과 없음 (frontend/README.md 4장) */
  emptyTitle: "지금 조건으로는 찾은 제도가 없어요",
  emptyHintAll: "관심 분야를 전체로 넓혀 보세요",
  emptyHintDistrict: "자치구를 선택하면 구에서 하는 지원도 찾아볼 수 있어요",
  emptyPortalLabel: "온통청년에서 전체 공고 보기",
  emptyPortalUrl: "https://www.youthcenter.go.kr",

  /** 고정 안내 (docs/01-glossary-profile.md 9장) */
  finalCheck: "최종 신청 전 공식 공고에서 다시 확인하세요",

  /** 오류 (server/errors.py와 같은 문구) */
  answerFailed: "설명을 불러오지 못했어요. 카드에서 조건을 확인해 주세요",
  disconnected: "연결이 끊겼어요",
  retry: "다시 시도",
  sessionExpired: "시간이 지나 처음부터 다시 시작할게요",

  /** 접힌 영역 (frontend/README.md 3-4) */
  hiddenUnlikely: (count: number) =>
    `조건이 맞지 않을 수 있는 제도 ${count}개`,

  /** 각주 버튼 이름 (접근성) */
  footnoteButton: (id: number) => `근거 ${id}번 보기`,

  /** 카드 갱신 표시 */
  updated: "업데이트됨",
} as const;

/** 홈 예시 칩 4개 (frontend/README.md 3-3의 고정 문구) */
export const EXAMPLE_CHIPS = [
  "방학 동안 생활비 지원 받을 수 있는 거 있어?",
  "취업 준비하는데 도움 되는 제도 알려줘",
  "자취 시작했는데 월세 지원 있어?",
  "교통비 아낄 수 있는 방법 있을까?",
] as const;
