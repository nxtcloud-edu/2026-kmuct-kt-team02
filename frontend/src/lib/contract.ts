/**
 * API 계약 타입.
 *
 * 출처: docs/03-api-contract.md, server/schemas.py, server/sse.py
 * 서버와 주고받는 필드는 snake_case를 그대로 쓴다. 이름을 새로 만들지 않는다.
 */

/* ------------------------------------------------------------------ *
 * 고정 값 (CONTRIBUTING.md 7-2)
 * ------------------------------------------------------------------ */

export type UserStatus =
  | "enrolled"
  | "on_leave"
  | "final_semester"
  | "job_seeking"
  | "employed";

export type Category =
  | "scholarship"
  | "living"
  | "job"
  | "culture"
  | "housing"
  | "all";

export type IncomeBracket =
  | "under_50"
  | "50_100"
  | "100_150"
  | "over_150"
  | "unknown";

export type HousingType =
  | "parents"
  | "monthly_rent"
  | "jeonse"
  | "dormitory"
  | "other";

export type ResidencePeriod = "under_6m" | "6m_1y" | "over_1y";
export type RemainingSemesters = "one" | "two_plus";
export type JobSeekingPeriod = "under_6m" | "over_6m";
export type YesNoUnknown = "yes" | "no" | "unknown";
export type YesNo = "yes" | "no";
export type HouseholdSize = "1" | "2" | "3" | "4_plus";
export type LastGpa = "above" | "below" | "unknown";

export type EvaluationStatus = "likely" | "check" | "unlikely";
export type ConditionResult = "met" | "unmet" | "unknown";
export type JudgedBy = "rule" | "ai";
export type PolicyDataStatus = "verified" | "recheck" | "closed" | "upcoming";
export type Stage = "searching" | "checking" | "summarizing";

/** 대화로 물을 수 있는 항목 (server/schemas.py AskableProfileField) */
export type AskableProfileField =
  | "district"
  | "income_bracket"
  | "housing_type"
  | "residence_period"
  | "remaining_semesters"
  | "job_seeking_period"
  | "employment_insurance"
  | "other_benefit"
  | "household_size"
  | "last_gpa";

export type ProfileField = "age" | "status" | "categories" | AskableProfileField;

/** 서울 25개 자치구 */
export const DISTRICTS = [
  "강남구",
  "강동구",
  "강북구",
  "강서구",
  "관악구",
  "광진구",
  "구로구",
  "금천구",
  "노원구",
  "도봉구",
  "동대문구",
  "동작구",
  "마포구",
  "서대문구",
  "서초구",
  "성동구",
  "성북구",
  "송파구",
  "양천구",
  "영등포구",
  "용산구",
  "은평구",
  "종로구",
  "중구",
  "중랑구",
] as const;

export type District = (typeof DISTRICTS)[number];

/* ------------------------------------------------------------------ *
 * 프로필
 * ------------------------------------------------------------------ */

/**
 * POST /session 요청 본문.
 * region은 서버가 seoul로 고정하므로 클라이언트가 보내지 않는다.
 * (server/schemas.py ProfileInput: "region is deliberately not client-provided")
 */
export interface ProfileInput {
  age: number;
  district?: District | null;
  status: UserStatus;
  categories: Category[];
  income_bracket: IncomeBracket;
}

/** 서버가 정규화해 돌려주는 프로필 */
export interface Profile extends ProfileInput {
  region: "seoul";
  housing_type?: HousingType | null;
  residence_period?: ResidencePeriod | null;
  remaining_semesters?: RemainingSemesters | null;
  job_seeking_period?: JobSeekingPeriod | null;
  employment_insurance?: YesNoUnknown | null;
  other_benefit?: YesNo | null;
  household_size?: HouseholdSize | null;
  last_gpa?: LastGpa | null;
}

/** 프로필에서 항목 값을 안전하게 꺼낸다 */
export function readProfileField(profile: Profile, field: ProfileField): unknown {
  return (profile as unknown as Record<string, unknown>)[field];
}

/* ------------------------------------------------------------------ *
 * 정책 판정 결과 (docs/03-api-contract.md 4장)
 * ------------------------------------------------------------------ */

export interface ConditionEvaluation {
  /**
   * 조건 요약. 20자 이내 명사형.
   * 이름이 아직 확정되지 않아 `name`과 `summary`가 둘 다 올 수 있다
   * (docs/03-api-contract.md 4-1: "조건 요약 키 이름은 12:00 통합 때 확정된다").
   * 읽을 때는 conditionName()을 쓴다.
   */
  name?: string;
  summary?: string;
  result: ConditionResult;
  judged_by: JudgedBy;
  /** 공고 원문 발췌 10~150자. 인용 검증 통과한 것만 */
  excerpt?: string | null;
  source_url: string;
  /** 각주 번호. 답변 본문의 번호와 같다 */
  footnote_id: number;
  /** result가 unknown일 때만. 기본 항목과 추가 항목 모두 올 수 있다 */
  needed_field?: ProfileField | "공고 확인 필요" | null;
}

/** 조건 요약을 읽는다. 두 키 중 온 것을 쓴다. */
export function conditionName(condition: ConditionEvaluation): string {
  return condition.name ?? condition.summary ?? "조건";
}

export interface Deadline {
  apply_start?: string | null;
  apply_end?: string | null;
  /** 남은 일수. 코드가 계산한 값 */
  d_day?: number | null;
  /** 화면 배지 문구. 서버가 붙인다 */
  badge: string;
  is_imminent: boolean;
}

/**
 * 판정 없이 공고 내용만 담은 정책 정보.
 *
 * 로그인하지 않으면 프로필이 없어 판정할 수 없다. 그때 이 모양으로 보여 준다.
 * 근거 없는 판정을 내보내지 않기 위해 status를 아예 두지 않는다.
 */
export interface PolicyInfo {
  policy_id: string;
  title: string;
  agency: string;
  categories: Category[];
  benefit: string;
  conditions: ConditionEvaluation[];
  deadline: Deadline;
  documents: string[];
  steps: string[];
  source_url: string;
  apply_url?: string | null;
  checked_at: string;
  data_status: PolicyDataStatus;
}

/** 프로필 기준 판정까지 붙은 정책 (docs/03-api-contract.md 4장) */
export interface PolicyEvaluation extends PolicyInfo {
  status: EvaluationStatus;
  /** 화면 문구. 코드가 붙인다 */
  status_label: string;
  /** check일 때만 채워진다 */
  conditional_note?: string | null;
}

/** 판정이 붙어 있는지 */
export function isEvaluated(
  policy: PolicyInfo | PolicyEvaluation,
): policy is PolicyEvaluation {
  return "status" in policy && typeof (policy as PolicyEvaluation).status === "string";
}

/* ------------------------------------------------------------------ *
 * 후속 질문 (docs/03-api-contract.md 6장)
 * ------------------------------------------------------------------ */

export interface FollowupOption {
  value: string | number | boolean | null;
  label: string;
}

/**
 * 후속 질문이 묻는 대상.
 * 프로필 항목이거나, 기준 시점을 묻는 `planned_basis`다.
 * `planned_basis`는 프로필 항목이 아니라 세션에만 기록된다
 * (docs/03-api-contract.md 3장).
 */
export type FollowupField = AskableProfileField | "planned_basis";

export interface FollowupQuestion {
  field: FollowupField;
  question: string;
  /** 왜 묻는지 한 줄 */
  reason: string;
  options: FollowupOption[];
  allow_free_text: boolean;
  allow_skip: true;
}

/* ------------------------------------------------------------------ *
 * 세션
 * ------------------------------------------------------------------ */

export interface SessionCreateResponse {
  session_id: string;
  profile: Profile;
  policies: PolicyEvaluation[];
  hidden_unlikely_count: number;
  followup?: FollowupQuestion | null;
}

export interface ChatRequestBody {
  session_id: string;
  message: string;
  client_message_id: string;
}

/* ------------------------------------------------------------------ *
 * 스트리밍 이벤트 (server/sse.py)
 * 모든 이벤트 data는 {request_id, seq, payload} 봉투에 담긴다.
 * ------------------------------------------------------------------ */

export interface SSEEnvelope<TPayload> {
  request_id: string;
  seq: number;
  payload: TPayload;
}

export interface StatusPayload {
  stage: Stage;
}

/** 프로필 변경 한 건 (docs/03-api-contract.md 5-3) */
export interface ProfileChange {
  field: ProfileField;
  /** 바뀌기 전 값. 없던 항목이면 비어 있다 */
  before?: unknown;
  after: unknown;
  /** after의 화면 문구. categories는 비어 있다 */
  label?: string | null;
  /** 이 변경에 붙는 안내 문구 */
  notice?: string | null;
}

export interface ProfileUpdatePayload {
  changes: ProfileChange[];
  /** 대화에 붙일 안내 한 줄 */
  notice: string;
  profile: Profile;
}

export interface PoliciesPayload {
  policies: PolicyEvaluation[];
  hidden_unlikely_count: number;
}

export interface AnswerDeltaPayload {
  delta: string;
}

export interface Footnote {
  footnote_id: number;
  policy_id: string;
  excerpt: string;
  agency: string;
  checked_at: string;
  source_url: string;
}

export interface FootnotesPayload {
  footnotes: Footnote[];
}

/** 관련 질문 칩 (docs/03-api-contract.md 5-2). 화면에는 text만 쓴다 */
export interface RelatedChip {
  id: string;
  text: string;
}

export interface RelatedPayload {
  chips: RelatedChip[];
}

/** 부분 실패 이벤트 */
export interface ErrorEventPayload {
  code: ErrorCode;
  message: string;
}

export interface DonePayload {
  total_duration_ms: number;
}

/* ------------------------------------------------------------------ *
 * 오류 (server/errors.py)
 * ------------------------------------------------------------------ */

export type ErrorCode =
  | "invalid_input"
  | "session_expired"
  | "server_error"
  | "answer_failed";

export interface ValidationIssue {
  location: Array<string | number>;
  message: string;
  error_type: string;
}

export interface ErrorPayload {
  code: ErrorCode;
  message: string;
  details: ValidationIssue[];
}

export interface ErrorResponse {
  error: ErrorPayload;
}

/** 서버 기본 오류 문구 (server/errors.py와 동일하게 유지) */
export const ERROR_MESSAGE: Record<ErrorCode, string> = {
  invalid_input: "입력한 정보를 다시 확인해 주세요",
  session_expired: "시간이 지나 처음부터 다시 시작할게요",
  server_error: "잠시 문제가 생겼어요. 다시 시도해 주세요",
  answer_failed: "설명을 불러오지 못했어요. 카드에서 조건을 확인해 주세요",
};
