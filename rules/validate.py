"""프로필 값 검증 (rules/README.md 9장).

1차 방어는 서버의 입력 검증(`server/schemas.py` Profile)이다. 이 모듈은 2차 방어다.

필요한 이유는 프로필이 들어오는 경로가 하나가 아니기 때문이다. 폼 입력 외에
대화 해석 결과와 (관심 정책 저장이 붙으면) 저장소에서 복원된 값이 들어온다. 저장된
프로필은 그 당시 스키마로 만들어졌을 수 있어 지금 허용 값과 어긋날 수 있다.

허용 값 밖의 값은 **버리고 미확인으로 되돌린다.** 모르는 값을 그대로 판정에 넣으면
조건이 조용히 어긋나고, 답을 받은 것처럼 보이지만 판정은 미확인으로 남는다.
"""

from __future__ import annotations

from . import constants as c

AGE_MIN = 15
AGE_MAX = 39

# 판정에 쓰는 세 축. 이 값이 없으면 판정 자체가 성립하지 않는다.
REQUIRED_FIELDS = ("age", "status", "categories")

ENUM_VALUES = {
    "region": frozenset({"seoul"}),
    "district": c.SEOUL_DISTRICTS,
    "status": frozenset(
        {"enrolled", "on_leave", "final_semester", "job_seeking", "employed"}
    ),
    "income_bracket": frozenset(c.INCOME_BANDS),
    "housing_type": frozenset({"parents", "monthly_rent", "jeonse", "dormitory", "other"}),
    "residence_period": frozenset({"under_6m", "6m_1y", "over_1y"}),
    "remaining_semesters": frozenset({"one", "two_plus"}),
    "job_seeking_period": frozenset({"under_6m", "over_6m"}),
    "employment_insurance": frozenset({"yes", "no", "unknown"}),
    "other_benefit": frozenset({"yes", "no"}),
    "household_size": frozenset({"1", "2", "3", "4_plus"}),
    "last_gpa": frozenset({"above", "below", "unknown"}),
}

CATEGORY_VALUES = frozenset({"scholarship", "living", "job", "culture", "housing", "all"})

ALLOWED_FIELDS = frozenset({"age", "categories", *ENUM_VALUES})


def normalize_profile(raw: dict) -> tuple[dict, list[str]]:
    """판정에 쓸 수 있는 프로필과 기록 목록을 돌려준다.

    - 표에 없는 필드는 버린다 (`docs/01-glossary-profile.md` 2~3장이 유일한 기준)
    - 허용 값 밖의 값은 None 으로 되돌린다
    - `region` 은 서울 고정이므로 다른 값이 와도 seoul 로 맞춘다
    """
    profile: dict = {}
    issues: list[str] = []

    for field, value in (raw or {}).items():
        if field not in ALLOWED_FIELDS:
            issues.append(f"프로필 항목 표에 없는 필드를 버렸다: {field}")
            continue

        if field == "age":
            if isinstance(value, bool) or not isinstance(value, int):
                issues.append(f"age 가 정수가 아니다: {value!r}")
                continue
            if not AGE_MIN <= value <= AGE_MAX:
                issues.append(f"age 가 허용 범위({AGE_MIN}~{AGE_MAX}) 밖이다: {value}")
                continue
            profile["age"] = value
            continue

        if field == "categories":
            if not isinstance(value, list) or not value:
                issues.append(f"categories 가 비었거나 목록이 아니다: {value!r}")
                continue
            unknown = [item for item in value if item not in CATEGORY_VALUES]
            if unknown:
                issues.append(f"categories 허용 값 밖: {unknown}")
            kept = [item for item in value if item in CATEGORY_VALUES]
            # all 은 단독으로만 쓴다 (server/schemas.py _check_categories)
            if c.CATEGORY_ALL in kept:
                kept = [c.CATEGORY_ALL]
            profile["categories"] = list(dict.fromkeys(kept))
            continue

        if field == "region":
            if value != "seoul":
                issues.append(f"region 은 seoul 고정이다. 받은 값을 무시했다: {value!r}")
            profile["region"] = "seoul"
            continue

        if value is None:
            profile[field] = None
            continue

        if value not in ENUM_VALUES[field]:
            issues.append(f"{field} 허용 값 밖이라 미확인으로 되돌렸다: {value!r}")
            profile[field] = None
            continue

        profile[field] = value

    profile.setdefault("region", "seoul")
    profile.setdefault("district", None)
    profile.setdefault("income_bracket", "unknown")

    return profile, issues


def blocking_issues(profile: dict) -> list[str]:
    """판정을 시작할 수 없는 문제만 골라낸다.

    나이·학적 상태·관심 분야가 없으면 조건을 만들 수 없다. 이 상태로 판정하면
    조건이 조용히 사라져 근거 없는 likely 가 나온다. 그래서 따로 구분한다.
    """
    return [f"{field} 가 없어 판정할 수 없다" for field in REQUIRED_FIELDS if not profile.get(field)]
