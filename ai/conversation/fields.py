"""프로필 항목 이름과 의도 값 (docs/01-glossary-profile.md 2~3장).

이 모듈은 **AI A 가 쓰는 이름만** 모아 둔다. 프로필 허용 값의 정식 정의는
``docs/01-glossary-profile.md`` 이고, 값 자체는 규칙 엔진과 서버가 다룬다.
AI A 는 "어떤 항목을 물을지" 고르고 "그 항목의 질문 문구"를 붙이는 일만 한다.

그래서 여기에는 항목 **이름**과 **질문 순서**만 있고, 각 항목의 허용 값 검증은 없다.
검증을 여기서 또 하면 같은 규칙이 두 곳에 생기고 한쪽만 고쳐져 어긋난다.

주의
----
``CONTRIBUTING.md`` 7-2 의 고정 값은 영문 snake_case 다. 이 모듈도 그것을 따른다.
현재 ``ai/judgment`` 와 ``ai/citation`` 은 한국어 값("충족", "현재 상태")을 쓰고 있어
통합 시점에 한쪽으로 맞춰야 한다. 12:00 통합 때 결정한다.
"""

from __future__ import annotations

# 의도 (CONTRIBUTING.md 7-2)
FIND_POLICY = "find_policy"
POLICY_QUESTION = "policy_question"
COMPARE = "compare"
RESULT_ONLY = "result_only"
OUT_OF_SCOPE = "out_of_scope"
SMALLTALK = "smalltalk"

INTENTS = (
    FIND_POLICY,
    POLICY_QUESTION,
    COMPARE,
    RESULT_ONLY,
    OUT_OF_SCOPE,
    SMALLTALK,
)

# 후속 질문을 하지 않는 의도 (ai/conversation/README.md 4장)
NO_FOLLOWUP_INTENTS = frozenset({RESULT_ONLY, OUT_OF_SCOPE, SMALLTALK})

# 프로필 변경 시점
CURRENT = "current"
PLANNED = "planned"

# 기본 프로필 항목 (docs/01-glossary-profile.md 2장)
AGE = "age"
REGION = "region"
DISTRICT = "district"
STATUS = "status"
CATEGORIES = "categories"
INCOME_BRACKET = "income_bracket"

# 추가 프로필 항목 (docs/01-glossary-profile.md 3장)
HOUSING_TYPE = "housing_type"
RESIDENCE_PERIOD = "residence_period"
REMAINING_SEMESTERS = "remaining_semesters"
JOB_SEEKING_PERIOD = "job_seeking_period"
EMPLOYMENT_INSURANCE = "employment_insurance"
OTHER_BENEFIT = "other_benefit"
HOUSEHOLD_SIZE = "household_size"
LAST_GPA = "last_gpa"

# 필요한 정보가 추가 항목 표에 없을 때 쓰는 값 (docs/01-glossary-profile.md 3장)
ASK_NOTICE = "공고 확인 필요"

# 동점일 때 쓰는 순서 (README 4장 5번).
# docs/01-glossary-profile.md 2~3장의 표 순서를 그대로 따른다.
# 이 순서를 바꾸면 같은 상황에서 다른 질문이 나와 데모가 흔들린다.
FIELD_ORDER = (
    INCOME_BRACKET,
    DISTRICT,
    HOUSING_TYPE,
    RESIDENCE_PERIOD,
    REMAINING_SEMESTERS,
    JOB_SEEKING_PERIOD,
    EMPLOYMENT_INSURANCE,
    OTHER_BENEFIT,
    HOUSEHOLD_SIZE,
    LAST_GPA,
)

_FIELD_RANK = {field: index for index, field in enumerate(FIELD_ORDER)}


def field_order(field: str) -> int:
    """동점 처리용 순서. 표에 없는 항목은 맨 뒤로 보낸다.

    표에 없는 항목이 들어오는 것 자체가 문제다(규칙 엔진이 만들지 않은 항목).
    여기서 예외를 던지지 않는 이유는, 질문 하나 때문에 전체 응답을 실패시키지
    않기 위해서다. 대신 맨 뒤로 밀려 선택되지 않는다.
    """
    return _FIELD_RANK.get(field, len(FIELD_ORDER))


def is_askable(field: str) -> bool:
    """대화로 물을 수 있는 항목인지.

    나이·거주지·현재 상태·관심 분야는 폼에서 이미 받았고 필수라 미확인이 될 수 없다.
    ``ASK_NOTICE`` 는 항목이 아니라 "공고를 직접 보라"는 표시이므로 물을 수 없다.
    """
    return field in _FIELD_RANK
