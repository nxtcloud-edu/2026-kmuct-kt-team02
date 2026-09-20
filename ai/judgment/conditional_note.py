"""조건부 문장 (`ai/judgment/README.md` 7장).

`check` 상태일 때 남은 조건을 "○○라면 신청 가능해요"로 풀어쓴 문장.

왜 필요한가
-----------
조건부 문장과 `unknown` 판정은 짝이다. 하나만 있으면 제품이 무용해진다.
소득을 모르는 사용자에게 카드 전부가 "확인이 필요해요"로만 보이면 아무 정보를
주지 못한다. 조건부 문장이 그 빈칸을 메운다 (FR08).

고정 형식
---------
| 미확인 원인 | 문장 형태 |
| --- | --- |
| 소득 | 가구 소득이 기준 중위소득 n% 이하라면 신청 가능해요 |
| 추가 항목 | (항목)이 (값)이라면 신청 가능해요 |
| 예외 조건 | (조건 요약)에 해당하지 않는다면 신청 가능해요 |
| 미확인 2개 이상 | 가장 위의 하나만 문장으로, 나머지 개수를 덧붙인다 |

**형식에 값만 넣는다. AI 가 자유롭게 쓰지 않는다.**
값을 채울 수 없으면 문장을 만들지 않고 비운다. 지어내지 않는다.

추가 항목은 문구 표(`values.EXTRA_CONDITION_CLAUSES`)에서 조건절을 고른다.
"(항목)이 (값)이라면" 을 기계적으로 만들면 "다른 지원 수혜가 없음이라면" 처럼
어색해진다. 표에 없는 조합만 조사를 맞춘 기본 형태로 만든다.
표에서 고르는 것이므로 고정 형식 원칙은 그대로다.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

from ai.judgment.values import (
    BY_AI,
    EXTRA_FIELD_VALUES,
    UNKNOWN,
    UNMET,
    extra_condition_clause,
    field_label,
    value_label,
)

_TAIL = " 신청 가능해요"


def _has_final_consonant(text: str) -> bool:
    """마지막 글자에 종성이 있는지. 조사를 고르는 데 쓴다.

    한글 음절은 0xAC00 부터 28개 종성 단위로 배열되어 있다.
    나머지가 0이면 종성이 없다.
    한글이 아니면(숫자·영문) 종성이 있는 것으로 보고 "이"를 붙인다.
    """
    if not text:
        return False
    last = text[-1]
    code = ord(last)
    if 0xAC00 <= code <= 0xD7A3:
        return (code - 0xAC00) % 28 != 0
    return True


def _subject_particle(text: str) -> str:
    """주격 조사. 종성이 있으면 "이", 없으면 "가"."""
    return "이" if _has_final_consonant(text) else "가"


def _copula(text: str) -> str:
    """"이라면" / "라면". 종성이 있으면 "이라면"."""
    return "이라면" if _has_final_consonant(text) else "라면"


# ---------------------------------------------------------------------------
# 문장 형태별 조립
# ---------------------------------------------------------------------------


def income_phrase(income_max_pct: object) -> Optional[str]:
    """소득 형태. 상한 %를 모르면 만들지 않는다."""
    if income_max_pct in (None, ""):
        return None
    try:
        pct = int(income_max_pct)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return f"가구 소득이 기준 중위소득 {pct}% 이하라면{_TAIL}"


def extra_field_phrase(field: str, required_value: object) -> Optional[str]:
    """추가 항목 형태. 항목과 필요한 값을 둘 다 알아야 만든다.

    문구 표(`values.EXTRA_CONDITION_CLAUSES`)를 먼저 본다. 표에 있는 조합은
    읽히는 문장이 나온다. 예: "다른 청년 지원금을 받고 있지 않다면 신청 가능해요"

    표에 없는 조합은 조사를 맞춘 기본 형태로 만든다.
    예: "(항목)이 (값)이라면 신청 가능해요"
    기본 형태는 어색할 수 있지만, 문구가 빠져도 문장이 나오는 쪽이 낫다.
    조건부 문장이 비면 카드에 상태 문구만 남는다.
    """
    if field not in EXTRA_FIELD_VALUES:
        return None
    if required_value in (None, ""):
        return None

    # 정책이 "모름"을 **필요한 값**으로 요구하는 일은 없다. 그런 값이 들어왔다면
    # 정책 데이터 쪽 오류다. "직전 학기 성적이 모름이라면" 같은 말이 되지 않는
    # 문장을 내보내는 것보다, 문장을 만들지 않고 다음 조건으로 넘기는 쪽이 낫다.
    if str(required_value) == "unknown":
        return None

    clause = extra_condition_clause(field, required_value)
    if clause:
        return f"{clause}{_TAIL}"

    label = field_label(field)
    value = value_label(field, required_value)
    return f"{label}{_subject_particle(label)} {value}{_copula(value)}{_TAIL}"


def exception_phrase(name: object) -> Optional[str]:
    """예외 조건 형태. 조건 요약이 없으면 만들지 않는다.

    인용 검증에 실패한 조건은 요약이 비어 있다. 그 조건으로는 문장을 만들 수 없다.
    """
    text = str(name or "").strip()
    if not text:
        return None
    return f"{text}에 해당하지 않는다면{_TAIL}"


# ---------------------------------------------------------------------------
# 조건 목록에서 문장 만들기
# ---------------------------------------------------------------------------


def _phrase_for(
    condition: Dict[str, Any],
    *,
    income_max_pct: object,
    extra_conditions: Dict[str, Any],
) -> Optional[str]:
    """조건 하나를 문장으로. 값을 채울 수 없으면 None."""
    needed = condition.get("needed_field")

    # 추가 항목: 필요한 값은 정책 데이터의 extra_conditions 에 있다.
    if needed in EXTRA_FIELD_VALUES:
        phrase = extra_field_phrase(str(needed), extra_conditions.get(str(needed)))
        if phrase:
            return phrase

    # 소득: 규칙 엔진이 판정하는 축이다. 조건에 field 표시가 있으면 그것으로,
    # 없으면 조건 요약에 "소득"이 들어있는지로 알아낸다.
    # field 는 문서에 없는 선택 항목이다. 백엔드A가 넣어주면 더 정확해진다.
    name = str(condition.get("name") or "")
    is_income = condition.get("field") == "income_bracket" or "소득" in name
    if is_income:
        phrase = income_phrase(income_max_pct)
        if phrase:
            return phrase

    # 예외 조건: AI 가 판정한 제외 대상 문장.
    if condition.get("judged_by") == BY_AI:
        return exception_phrase(name)

    return None


def build_conditional_note(
    conditions: Sequence[Dict[str, Any]],
    *,
    income_max_pct: object = None,
    extra_conditions: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    """조건 목록에서 조건부 문장을 만든다.

    `check` 상태가 아니면 None 을 돌려준다. 즉 `unmet` 이 하나라도 있거나
    `unknown` 이 하나도 없으면 문장을 만들지 않는다
    (`docs/03-api-contract.md` 4-3: conditional_note 는 check 일 때만).

    미확인이 여러 개면 문장으로 만들 수 있는 첫 조건을 쓰고, 나머지 개수를
    "외 n개 조건 확인 필요"로 덧붙인다.

    값을 채울 수 없는 조건은 건너뛴다. 전부 채울 수 없으면 None 이다.
    억지로 문장을 만들어 근거 없는 내용을 화면에 내보내지 않는다.

    extra_conditions 는 정책 데이터의 `extra_conditions` 를
    ``{"housing_type": "monthly_rent"}`` 형태로 정리한 것이다
    (`docs/04-data-schema.md` 2장의 "항목: 필요한 값").
    """
    items = list(conditions)

    if any(str(c.get("result")) == UNMET for c in items):
        return None

    unknowns: List[Dict[str, Any]] = [
        c for c in items if str(c.get("result")) == UNKNOWN
    ]
    if not unknowns:
        return None

    extras = extra_conditions or {}

    phrase = None
    for candidate in unknowns:
        phrase = _phrase_for(
            candidate, income_max_pct=income_max_pct, extra_conditions=extras
        )
        if phrase:
            break

    if not phrase:
        return None

    remaining = len(unknowns) - 1
    if remaining > 0:
        # "신청 가능해요 외 n개 조건 확인 필요" 는 두 문장이 붙어 읽기 어렵다.
        # 뜻은 같게 두고 괄호로 덧붙인다.
        return f"{phrase} (확인할 조건 {remaining}개 더)"
    return phrase
