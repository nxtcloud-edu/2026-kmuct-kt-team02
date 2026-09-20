"""규칙 조건 판정 (docs/04-data-schema.md 5장, rules/README.md 4장).

표로 표현된 조건만 다룬다. `exceptions_text` 처럼 문장으로만 남은 조건은 건드리지 않고
AI B 에 넘긴다.

거주지는 `seoul` 고정이므로 "서울 외 거주" 분기는 없다
(`docs/01-glossary-profile.md` 2장).
"""

from __future__ import annotations

from . import constants as c


def _excerpt(policy: dict, condition_key: str) -> tuple[str | None, str | None]:
    """condition_sources 에서 발췌를 꺼낸다.

    ConditionEvaluation.excerpt 는 10~150자 또는 None 이다. 범위를 벗어난 문장을
    그대로 내보내면 계약 검증에서 걸리므로 None 으로 두고 검수 필요로 기록한다.
    발췌가 비면 그 조건은 화면에 나갈 수 없다 (각주 없는 판정 문장 금지).
    """
    sources = policy.get("condition_sources") or {}
    for alias in c.CONDITION_SOURCE_ALIASES[condition_key]:
        text = sources.get(alias)
        if not text:
            continue
        text = text.strip()
        if len(text) < c.EXCERPT_MIN:
            return None, f"{condition_key}: 발췌가 {c.EXCERPT_MIN}자 미만"
        if len(text) > c.EXCERPT_MAX:
            return None, f"{condition_key}: 발췌가 {c.EXCERPT_MAX}자 초과"
        return text, None
    return None, f"{condition_key}: 근거 문장 없음"


def _condition(
    policy: dict, key: str, result: str, needed_field: str | None = None
) -> tuple[dict, str | None]:
    excerpt, issue = _excerpt(policy, key)
    condition = {
        "name": c.CONDITION_NAMES[key],
        "result": result,
        "judged_by": c.JUDGED_BY_RULE,
        "excerpt": excerpt,
        "source_url": policy.get("source_url"),
        "needed_field": needed_field if result == c.UNKNOWN else None,
    }
    return condition, issue


def evaluate_age(profile: dict, policy: dict) -> tuple[dict, str | None] | None:
    """나이 조건. age_min·age_max 둘 다 비면 조건을 만들지 않는다.

    `unknown` 분기는 만들지 않는다. 경계 나이를 판별할 필드가 데이터에 없고
    AskableProfileField 에 age 가 없어 물을 방법도 없다 (notes/judgment-tables.md 2장).
    """
    low, high = policy.get("age_min"), policy.get("age_max")
    if low is None and high is None:
        return None
    age = profile.get("age")
    lower_ok = low is None or age >= low
    upper_ok = high is None or age <= high
    result = c.MET if lower_ok and upper_ok else c.UNMET
    return _condition(policy, c.CONDITION_AGE, result)


def evaluate_region(profile: dict, policy: dict) -> tuple[dict, str | None]:
    """지역 조건. 자치구 한정 정책은 사용자 자치구와 비교한다."""
    regions = policy.get("regions") or []
    districts = [r for r in regions if r in c.SEOUL_DISTRICTS]

    if districts:
        user_district = profile.get("district")
        if not user_district:
            return _condition(
                policy,
                c.CONDITION_REGION,
                c.UNKNOWN,
                c.NEEDED_FIELD_BY_CONDITION[c.CONDITION_REGION],
            )
        result = c.MET if user_district in districts else c.UNMET
        return _condition(policy, c.CONDITION_REGION, result)

    result = c.MET if set(regions) & c.NATIONWIDE_REGIONS else c.UNMET
    return _condition(policy, c.CONDITION_REGION, result)


def evaluate_status(profile: dict, policy: dict) -> tuple[dict, str | None] | None:
    """신분 조건. statuses 가 비면 조건을 만들지 않는다. unknown 은 없다."""
    statuses = policy.get("statuses") or []
    if not statuses:
        return None
    result = c.MET if profile.get("status") in statuses else c.UNMET
    return _condition(policy, c.CONDITION_STATUS, result)


def evaluate_income(profile: dict, policy: dict) -> tuple[dict, str | None] | None:
    """소득 조건. 구간 대 상한 비교다.

    `income_max_pct` 가 비면 조건을 **만들지 않는다.** 조건 없는 정책에 "소득 미확인"을
    붙이면 근거 없이 check 로 떨어지고, 소득을 몰라도 likely 를 보여주자는 데이터 설계
    의도와 정면으로 어긋난다 (rules/README.md 4장).
    """
    limit = policy.get("income_max_pct")
    if limit is None:
        return None

    band = c.INCOME_BANDS.get(profile.get("income_bracket"))
    if band is None:
        return _condition(
            policy,
            c.CONDITION_INCOME,
            c.UNKNOWN,
            c.NEEDED_FIELD_BY_CONDITION[c.CONDITION_INCOME],
        )

    low, high = band
    if high is not None and high <= limit:
        result = c.MET
    elif low >= limit:
        result = c.UNMET
    else:
        # 구간이 기준선을 걸친다. 단정할 수 없으므로 미확인이다.
        return _condition(
            policy,
            c.CONDITION_INCOME,
            c.UNKNOWN,
            c.NEEDED_FIELD_BY_CONDITION[c.CONDITION_INCOME],
        )
    return _condition(policy, c.CONDITION_INCOME, result)


def _parse_extra(entry: str) -> tuple[str, str] | None:
    """"항목: 필요한 값" 을 쪼갠다. 형식이 아니면 None."""
    if ":" not in entry:
        return None
    label, _, required = entry.partition(":")
    label, required = label.strip(), required.strip()
    if not label or not required:
        return None
    return label, required


def evaluate_extra_conditions(
    profile: dict, policy: dict
) -> tuple[list[dict], list[str]]:
    """추가 항목 조건. 답을 모르면 미확인으로 두고 물을 항목을 지정한다.

    필요한 값이 프로필 허용 값으로 해석되지 않으면 unmet 으로 단정하지 않는다.
    잘못된 unmet 은 사용자에게 "안 된다"고 잘못 말하는 것이라 가장 나쁜 오류다
    (.kiro/steering/03-dev-method.md).
    """
    conditions: list[dict] = []
    issues: list[str] = []

    for entry in policy.get("extra_conditions") or []:
        parsed = _parse_extra(entry)
        if parsed is None:
            issues.append(f"extra_conditions 형식 오류: {entry!r}")
            continue

        label, required = parsed
        field = c.EXTRA_CONDITION_FIELDS.get(label)
        if field is None:
            issues.append(f"추가 항목 표에 없는 항목: {label!r}")
            continue

        answer = profile.get(field)
        needed = field if field in c.ASKABLE_FIELDS else c.ASK_NOTICE

        if answer is None:
            result, needed_field = c.UNKNOWN, needed
        else:
            expected = c.VALUE_LABELS.get(field, {}).get(required)
            if expected is None:
                issues.append(f"{label}: 필요한 값 {required!r} 을 해석할 수 없음")
                result, needed_field = c.UNKNOWN, needed
            else:
                result = c.MET if answer == expected else c.UNMET
                needed_field = None

        # 추가 항목은 condition_sources 에 전용 키가 없다. 발췌는 비워 두고
        # 검수 단계에서 채운다. 발췌가 없는 조건은 화면에 나갈 수 없다.
        conditions.append(
            {
                "name": label[:20],
                "result": result,
                "judged_by": c.JUDGED_BY_RULE,
                "excerpt": None,
                "source_url": policy.get("source_url"),
                "needed_field": needed_field,
            }
        )

    return conditions, issues


def evaluate_conditions(profile: dict, policy: dict) -> tuple[list[dict], list[str]]:
    """정책 하나의 규칙 조건을 모두 판정한다.

    조건 표시 순서는 unmet → unknown → met 이다 (docs/03-api-contract.md 4-3).
    """
    conditions: list[dict] = []
    issues: list[str] = []

    for evaluate in (evaluate_age, evaluate_region, evaluate_status, evaluate_income):
        outcome = evaluate(profile, policy)
        if outcome is None:
            continue
        condition, issue = outcome
        conditions.append(condition)
        if issue:
            issues.append(f"{policy.get('id')}: {issue}")

    extra, extra_issues = evaluate_extra_conditions(profile, policy)
    conditions.extend(extra)
    issues.extend(f"{policy.get('id')}: {item}" for item in extra_issues)

    order = {c.UNMET: 0, c.UNKNOWN: 1, c.MET: 2}
    conditions.sort(key=lambda item: order[item["result"]])
    return conditions, issues
