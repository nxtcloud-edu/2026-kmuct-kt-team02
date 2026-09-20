/** 온보딩·마이페이지 폼에서 쓰는 선택지 목록. 값은 contract의 고정값만 쓴다. */

import {
  CATEGORY_LABEL,
  INCOME_LABEL,
  USER_STATUS_LABEL,
} from "./labels";
import { DISTRICTS, type Category, type IncomeBracket, type UserStatus } from "./contract";

export const STATUS_OPTIONS: ReadonlyArray<{ value: UserStatus; label: string }> = (
  ["enrolled", "on_leave", "final_semester", "job_seeking", "employed"] as const
).map((value) => ({ value, label: USER_STATUS_LABEL[value] }));

/** 관심 분야. 전체는 마지막에 둔다 */
export const CATEGORY_OPTIONS: ReadonlyArray<{ value: Category; label: string }> = (
  ["scholarship", "living", "job", "culture", "housing", "all"] as const
).map((value) => ({ value, label: CATEGORY_LABEL[value] }));

export const INCOME_OPTIONS: ReadonlyArray<{ value: IncomeBracket; label: string }> = (
  ["under_50", "50_100", "100_150", "over_150", "unknown"] as const
).map((value) => ({ value, label: INCOME_LABEL[value] }));

export const DISTRICT_OPTIONS = DISTRICTS.map((value) => ({ value, label: value }));

export const AGE_MIN = 15;
export const AGE_MAX = 39;
