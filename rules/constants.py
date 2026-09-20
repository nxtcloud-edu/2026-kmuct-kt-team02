"""규칙 엔진 고정 값 (CONTRIBUTING.md 7-1-1, 7-2).

문자열을 코드 곳곳에 직접 쓰지 않고 여기 모은다. 오타가 판정을 조용히 틀리게 만든다.
값은 `docs/01-glossary-profile.md` 2~6장과 `docs/03-api-contract.md` 4장을 그대로 따른다.
"""

from __future__ import annotations

# 조건 결과 (docs/01-glossary-profile.md 1장)
MET = "met"
UNMET = "unmet"
UNKNOWN = "unknown"

# 판정 주체
JUDGED_BY_RULE = "rule"

# 판정 상태와 화면 문구 (docs/01-glossary-profile.md 4장)
LIKELY = "likely"
CHECK = "check"
UNLIKELY = "unlikely"

STATUS_LABELS = {
    LIKELY: "신청 가능성이 높아요",
    CHECK: "확인이 필요해요",
    UNLIKELY: "어려울 수 있어요",
}
STATUS_RANK = {LIKELY: 0, CHECK: 1, UNLIKELY: 2}

# 데이터 상태 (CONTRIBUTING.md 7-2)
VERIFIED = "verified"
RECHECK = "recheck"
CLOSED = "closed"
UPCOMING = "upcoming"

# 수집 방식 (docs/04-data-schema.md 1장)
SOURCE_MANUAL = "manual"
SOURCE_CRAWLED = "crawled"

# 관심 분야
CATEGORY_ALL = "all"

# 지역 판정에서 서울 사용자에게 해당하는 표기 (docs/04-data-schema.md 2장)
NATIONWIDE_REGIONS = frozenset({"서울", "전국"})
SEOUL_DISTRICTS = frozenset(
    {
        "강남구", "강동구", "강북구", "강서구", "관악구", "광진구", "구로구",
        "금천구", "노원구", "도봉구", "동대문구", "동작구", "마포구", "서대문구",
        "서초구", "성동구", "성북구", "송파구", "양천구", "영등포구", "용산구",
        "은평구", "종로구", "중구", "중랑구",
    }
)

# 날짜 경계 (docs/01-glossary-profile.md 5장)
RECHECK_DAYS = 14
IMMINENT_DAYS = 7

# 표시 개수 (docs/01-glossary-profile.md 7장)
BASIC_DISPLAY_LIMIT = 5
HIDDEN_UNLIKELY_LIMIT = 3

# 발췌 길이 (docs/03-api-contract.md 4-1, server/schemas.py ConditionEvaluation)
EXCERPT_MIN = 10
EXCERPT_MAX = 150

# 조건 이름. ConditionEvaluation.name 은 20자 이내다
CONDITION_AGE = "age"
CONDITION_REGION = "region"
CONDITION_STATUS = "status"
CONDITION_INCOME = "income"

CONDITION_NAMES = {
    CONDITION_AGE: "나이",
    CONDITION_REGION: "거주 지역",
    CONDITION_STATUS: "학적 상태",
    CONDITION_INCOME: "가구 소득",
}

# condition_sources 키 별칭. 데이터 작성자가 어느 이름으로 적어도 찾는다
CONDITION_SOURCE_ALIASES = {
    CONDITION_AGE: ("age", "나이"),
    CONDITION_REGION: ("region", "regions", "지역", "거주지"),
    CONDITION_STATUS: ("status", "statuses", "신분", "학적"),
    CONDITION_INCOME: ("income", "income_max_pct", "income_bracket", "소득"),
}

# unknown 일 때 무엇을 물어야 하는지 (server/schemas.py AskableProfileField)
NEEDED_FIELD_BY_CONDITION = {
    CONDITION_REGION: "district",
    CONDITION_INCOME: "income_bracket",
}
ASK_NOTICE = "공고 확인 필요"

ASKABLE_FIELDS = frozenset(
    {
        "district", "income_bracket", "housing_type", "residence_period",
        "remaining_semesters", "job_seeking_period", "employment_insurance",
        "other_benefit", "household_size", "last_gpa",
    }
)

# 소득 구간 하한·상한 (docs/01-glossary-profile.md 2장)
INCOME_BANDS = {
    "under_50": (0, 50),
    "50_100": (50, 100),
    "100_150": (100, 150),
    "over_150": (150, None),
    "unknown": None,
}

# extra_conditions 는 "항목: 필요한 값" 형식이다 (docs/04-data-schema.md 2장).
# 항목 이름은 docs/01-glossary-profile.md 3장 표에 있는 것만 쓴다.
EXTRA_CONDITION_FIELDS = {
    "주거 형태": "housing_type",
    "서울 거주 기간": "residence_period",
    "남은 학기": "remaining_semesters",
    "구직 기간": "job_seeking_period",
    "고용보험 가입 이력": "employment_insurance",
    "다른 지원 수혜 중": "other_benefit",
    "가구원 수": "household_size",
    "직전 학기 성적": "last_gpa",
}

# 추가 항목의 화면 문구 → 값. 공고에 적힌 한국어를 프로필 값과 맞추는 데 쓴다.
VALUE_LABELS = {
    "housing_type": {
        "부모님 집": "parents",
        "월세": "monthly_rent",
        "전세": "jeonse",
        "기숙사": "dormitory",
        "기타": "other",
    },
    "residence_period": {
        "6개월 미만": "under_6m",
        "6개월~1년": "6m_1y",
        "1년 이상": "over_1y",
    },
    "remaining_semesters": {"1학기": "one", "2학기 이상": "two_plus"},
    "job_seeking_period": {"6개월 미만": "under_6m", "6개월 이상": "over_6m"},
    "employment_insurance": {"있음": "yes", "없음": "no", "모름": "unknown"},
    "other_benefit": {"있음": "yes", "없음": "no"},
    "household_size": {"1인": "1", "2인": "2", "3인": "3", "4인 이상": "4_plus"},
    "last_gpa": {"기준 이상": "above", "기준 미만": "below", "모름": "unknown"},
}

# 후보 제외 사유
EXCLUDED_CLOSED = "closed"
EXCLUDED_CRAWLED = "crawled_not_reviewed"
EXCLUDED_NO_SOURCE = "missing_source_or_checked_at"
EXCLUDED_NO_RAW_TEXT = "missing_raw_text"
EXCLUDED_CATEGORY = "category_mismatch"
