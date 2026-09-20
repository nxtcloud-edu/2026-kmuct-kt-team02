"""고정 값과 화면 문구를 모은 곳.

문자열을 여러 파일에 흩지 않는다. 오타가 판정을 조용히 틀리게 만든다
(`CONTRIBUTING.md` 7-1-1).

값의 기준은 `docs/01-glossary-profile.md` 2~3장과 `CONTRIBUTING.md` 7-2다.
여기 있는 값을 바꿔야 하면 그 문서를 먼저 고치고 전원에게 공지한다.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

# ---------------------------------------------------------------------------
# 조건 결과 (CONTRIBUTING 7-2)
# ---------------------------------------------------------------------------

MET = "met"
UNMET = "unmet"
UNKNOWN = "unknown"

RESULTS: Tuple[str, ...] = (MET, UNMET, UNKNOWN)

# ---------------------------------------------------------------------------
# 판정 주체 (CONTRIBUTING 7-2)
# ---------------------------------------------------------------------------

BY_RULE = "rule"
BY_AI = "ai"

# ---------------------------------------------------------------------------
# 판정 상태 (CONTRIBUTING 7-2)
# 이 모듈은 상태를 계산하지 않는다. 상태는 서버가 확정한다.
# 조건부 문장을 채울지 판단할 때만 참조한다.
# ---------------------------------------------------------------------------

LIKELY = "likely"
CHECK = "check"
UNLIKELY = "unlikely"

# ---------------------------------------------------------------------------
# 발췌와 조건 요약 제약 (ai/judgment/README.md 5장)
# ---------------------------------------------------------------------------

MIN_EXCERPT_LEN = 10
MAX_EXCERPT_LEN = 150
MAX_NAME_LEN = 20

# ---------------------------------------------------------------------------
# 필요한 추가 항목으로 지정할 수 없을 때 쓰는 값
# ai/judgment/README.md 5장이 문자 그대로 "공고 확인 필요"로 적고 있다.
# ---------------------------------------------------------------------------

ASK_NOTICE = "공고 확인 필요"

# ---------------------------------------------------------------------------
# 프로필 추가 항목 (docs/01-glossary-profile.md 3장)
# 필요한 추가 항목에 쓸 수 있는 값은 이 목록과 ASK_NOTICE 뿐이다.
# 나이·거주지·신분·소득은 규칙 엔진이 판정하므로 여기 없다.
# ---------------------------------------------------------------------------

EXTRA_FIELD_VALUES: Dict[str, Tuple[str, ...]] = {
    "housing_type": ("parents", "monthly_rent", "jeonse", "dormitory", "other"),
    "residence_period": ("under_6m", "6m_1y", "over_1y"),
    "remaining_semesters": ("one", "two_plus"),
    "job_seeking_period": ("under_6m", "over_6m"),
    "employment_insurance": ("yes", "no", "unknown"),
    "other_benefit": ("yes", "no"),
    "household_size": ("1", "2", "3", "4_plus"),
    "last_gpa": ("above", "below", "unknown"),
}

ALLOWED_NEEDED_FIELDS = frozenset(EXTRA_FIELD_VALUES) | {ASK_NOTICE}

# ---------------------------------------------------------------------------
# 기본 프로필 항목 (docs/01-glossary-profile.md 2장)
# 판정에 쓰지는 않지만 평가 케이스가 이 값을 쓴다.
# ---------------------------------------------------------------------------

BASE_FIELD_VALUES: Dict[str, Tuple[str, ...]] = {
    "age": (),  # 15~39 정수
    "region": ("seoul", "outside_seoul"),
    "district": (),  # 자치구명 또는 None
    "status": (
        "enrolled",
        "on_leave",
        "final_semester",
        "job_seeking",
        "employed",
    ),
    "categories": (
        "scholarship",
        "living",
        "job",
        "culture",
        "housing",
        "all",
    ),
    "income_bracket": ("under_50", "50_100", "100_150", "over_150", "unknown"),
}

ALL_PROFILE_FIELDS = frozenset(BASE_FIELD_VALUES) | frozenset(EXTRA_FIELD_VALUES)

# ---------------------------------------------------------------------------
# 화면 문구 (docs/01-glossary-profile.md 2~3장의 괄호 표기를 옮긴 것)
# 조건부 문장에 값을 끼워 넣을 때 쓴다. 새 문구를 만들지 않는다.
# ---------------------------------------------------------------------------

FIELD_LABELS: Dict[str, str] = {
    "age": "나이",
    "region": "거주지",
    "district": "자치구",
    "status": "현재 상태",
    "categories": "관심 분야",
    "income_bracket": "가구 소득",
    "housing_type": "주거 형태",
    "residence_period": "서울 거주 기간",
    "remaining_semesters": "남은 학기",
    "job_seeking_period": "구직 기간",
    "employment_insurance": "고용보험 가입 이력",
    "other_benefit": "다른 지원 수혜",
    "household_size": "가구원 수",
    "last_gpa": "직전 학기 성적",
}

VALUE_LABELS: Dict[str, Dict[str, str]] = {
    "region": {"seoul": "서울", "outside_seoul": "서울 외"},
    "status": {
        "enrolled": "재학",
        "on_leave": "휴학",
        "final_semester": "졸업예정",
        "job_seeking": "졸업 후 구직",
        "employed": "재직",
    },
    "income_bracket": {
        "under_50": "기준 중위소득 50% 이하",
        "50_100": "50~100%",
        "100_150": "100~150%",
        "over_150": "150% 초과",
        "unknown": "잘 모르겠어요",
    },
    "housing_type": {
        "parents": "부모님 집",
        "monthly_rent": "월세",
        "jeonse": "전세",
        "dormitory": "기숙사",
        "other": "기타",
    },
    "residence_period": {
        "under_6m": "6개월 미만",
        "6m_1y": "6개월~1년",
        "over_1y": "1년 이상",
    },
    "remaining_semesters": {"one": "1학기", "two_plus": "2학기 이상"},
    "job_seeking_period": {"under_6m": "6개월 미만", "over_6m": "6개월 이상"},
    "employment_insurance": {"yes": "있음", "no": "없음", "unknown": "모르겠어요"},
    "other_benefit": {"yes": "있음", "no": "없음"},
    "household_size": {"1": "1인", "2": "2인", "3": "3인", "4_plus": "4인 이상"},
    "last_gpa": {"above": "기준 이상", "below": "기준 미만", "unknown": "모름"},
}


# ---------------------------------------------------------------------------
# 조건부 문장의 조건절 문구 표
#
# "(항목)이 (값)이라면" 을 기계적으로 만들면 어색해진다.
# 예: "다른 지원 수혜가 없음이라면", "남은 학기가 1학기라면"
#
# 그래서 항목·값 조합마다 읽히는 조건절을 표로 적어 둔다.
# **AI 가 문장을 쓰는 것이 아니라 이 표에서 고른다.** 고정 형식 원칙은 그대로다
# (`ai/judgment/README.md` 7장).
#
# 표에 없는 조합은 조사를 맞춘 기본 형태로 만든다. 문구가 빠져도 문장은 나온다.
# ---------------------------------------------------------------------------

EXTRA_CONDITION_CLAUSES: Dict[str, Dict[str, str]] = {
    "housing_type": {
        "parents": "부모님 집에 살고 있다면",
        "monthly_rent": "월세로 살고 있다면",
        "jeonse": "전세로 살고 있다면",
        "dormitory": "기숙사에 살고 있다면",
        "other": "그 밖의 주거 형태라면",
    },
    "residence_period": {
        "under_6m": "서울에 산 지 6개월이 안 됐다면",
        "6m_1y": "서울에 산 지 6개월에서 1년 사이라면",
        "over_1y": "서울에 산 지 1년이 넘었다면",
    },
    "remaining_semesters": {
        "one": "졸업까지 1학기 남았다면",
        "two_plus": "졸업까지 2학기 이상 남았다면",
    },
    "job_seeking_period": {
        "under_6m": "구직 활동을 시작한 지 6개월이 안 됐다면",
        "over_6m": "구직 활동을 시작한 지 6개월이 넘었다면",
    },
    "employment_insurance": {
        "yes": "고용보험에 가입한 적이 있다면",
        "no": "고용보험에 가입한 적이 없다면",
    },
    "other_benefit": {
        "yes": "다른 청년 지원금을 받고 있다면",
        "no": "다른 청년 지원금을 받고 있지 않다면",
    },
    "household_size": {
        "1": "혼자 살고 있다면",
        "2": "가구원이 2인이라면",
        "3": "가구원이 3인이라면",
        "4_plus": "가구원이 4인 이상이라면",
    },
    "last_gpa": {
        "above": "직전 학기 성적이 공고 기준 이상이라면",
        "below": "직전 학기 성적이 공고 기준 미만이라면",
    },
}


def extra_condition_clause(field: str, value: object) -> Optional[str]:
    """조건절 문구. 표에 없으면 None (호출한 쪽이 기본 형태로 만든다)."""
    return EXTRA_CONDITION_CLAUSES.get(field, {}).get(str(value))


def field_label(field: str) -> str:
    """항목의 화면 문구. 표에 없으면 필드명을 그대로 돌려준다."""
    return FIELD_LABELS.get(field, field)


def value_label(field: str, value: object) -> str:
    """값의 화면 문구. 표에 없으면 값을 그대로 돌려준다."""
    return VALUE_LABELS.get(field, {}).get(str(value), str(value))


def normalize_needed_field(value: object) -> str:
    """판정기가 낸 값을 허용 목록 안으로 밀어넣는다.

    목록에 없는 항목을 만들어 냈으면 "공고 확인 필요"로 바꾼다.
    새 항목을 조용히 통과시키면 후속 질문이 그 항목을 물을 수 없고,
    사용자는 영원히 미확인 상태를 보게 된다.
    """
    if not value:
        return ASK_NOTICE
    text = str(value).strip()
    if text in ALLOWED_NEEDED_FIELDS:
        return text
    return ASK_NOTICE
