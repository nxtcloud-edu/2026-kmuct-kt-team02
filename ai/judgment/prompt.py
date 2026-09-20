"""예외 조건 판정 프롬프트 조립 (`ai/judgment/README.md` 3~5장, 9장).

이 모듈은 **문자열만 만든다.** API 호출, 모델 이름, 스키마 정의는 여기 없다
(호출은 `client.py`, 출력 형식 강제는 `schema.py` 담당).

프롬프트 구조 (`docs/research/04-llm-api-operations.md` 6장 "우리 방침")
----------------------------------------------------------------------
사용자 메시지는 네 부분이다.

1. 판정 규칙 (지시문)
2. 정책 원문 — `<policy_text>`
3. 프로필 — `<profile>`
4. 출력 형태와 금지 사항 (규칙 재확인)

판정 대상 예외 문장은 `<exception_condition>` 으로 따로 감싸 원문과 구분한다.
원문 전체를 주면서 "이 문장을 판정하라"고 가리켜야 발췌를 원문에서 뽑을 수 있다.

**규칙을 앞과 끝에 두 번 둔다.** 원문이 가운데 있으면 중간 정보가 묻힌다
(`docs/research/02-grounding-and-citation.md` 5절). 그래서 같은 규칙이
머리와 꼬리에 중복으로 적혀 있다. 중복은 실수가 아니라 의도다.

**프로필은 원문보다 뒤에 둔다.** 프롬프트 캐싱은 접두사가 한 토큰이라도 다르면
적용되지 않는다. 매 호출 바뀌는 값(프로필)이 앞에 오면 고정 부분(규칙 + 원문)의
캐시가 깨진다 (연구 문서 5장).

`JUDGE_SYSTEM_PROMPT` 는 상수다. 호출마다 달라지는 입력이 섞이지 않는다.
`values.py` 의 고정 값만 끼워 모듈을 읽을 때 한 번 만들어지므로, 같은 실행에서도
다른 실행에서도 같은 문자열이다. 숫자를 손으로 적지 않는 이유는 `values.py` 와
어긋나는 것을 막기 위해서다.

여기서 만드는 문구는 전부 `ai/judgment/README.md` 4~5장을 옮긴 것이다.
**새 판정 기준을 만들지 않는다.** 기준을 바꿔야 하면 README 를 먼저 고친다.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ai.judgment.values import (
    ASK_NOTICE,
    BASE_FIELD_VALUES,
    EXTRA_FIELD_VALUES,
    MAX_EXCERPT_LEN,
    MAX_NAME_LEN,
    MET,
    MIN_EXCERPT_LEN,
    UNKNOWN,
    UNMET,
    field_label,
    value_label,
)

# ---------------------------------------------------------------------------
# 프로필 서술
# ---------------------------------------------------------------------------

#: 값이 없는 항목에 적는 말. 항목을 **빼지 않고** 없다고 적는다.
#: 빼면 모델이 그 항목을 상식으로 추측한다 (README 4장 추정 금지).
NO_VALUE = "정보 없음"

#: 프로필에 적는 항목과 순서. `docs/01-glossary-profile.md` 2~3장 순서를
#: 그대로 따른다. 여기 없는 키가 프로필 dict 에 들어와도 적지 않는다
#: (표에 없는 항목은 쓰지 않는다).
_PROFILE_FIELDS = tuple(BASE_FIELD_VALUES) + tuple(EXTRA_FIELD_VALUES)


def _as_text(value: object) -> str:
    """None 을 빈 문자열로. 문자열은 **바꾸지 않는다.**

    원문과 예외 문장은 정규화하지 않는다. 앞뒤 공백을 떼거나 줄바꿈을 접으면
    모델이 인용한 발췌와 `raw_text` 가 어긋나 인용 검증이 깨진다.
    정규화는 검증 쪽(`normalize.py`)에서만 한다.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return str(value)


def _describe_value(field: str, value: object) -> str:
    """항목 하나의 값을 사람이 읽는 형태로.

    문구는 `values.value_label()` 에서만 가져온다. 새 문구를 만들지 않는다.
    표에 문구가 없는 항목(`age`, `district`, `categories`)은 값이 그대로 나간다.
    """
    if value is None or value == "" or value == []:
        return NO_VALUE

    # 관심 분야는 복수 선택이다. 값 문구 표가 없어 코드 값이 그대로 나간다.
    # 판정 대상이 아닌 축(후보 선정용)이라 그대로 둔다.
    if isinstance(value, (list, tuple, set)):
        labels = [value_label(field, item) for item in value if item not in (None, "")]
        if not labels:
            return NO_VALUE
        return ", ".join(labels)

    return value_label(field, value)


def _describe_profile(profile: Optional[Dict[str, Any]]) -> str:
    """프로필 dict 를 줄마다 한 항목씩 적는다.

    프로필이 비어 있어도(None, {}) 항목 이름은 전부 적고 값을 "정보 없음"으로 둔다.
    빈 블록을 주면 모델이 프로필을 상상한다.
    """
    data = profile if isinstance(profile, dict) else {}
    lines = []
    for field in _PROFILE_FIELDS:
        label = field_label(field)
        lines.append(f"- {label}: {_describe_value(field, data.get(field))}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 판정 규칙 문구 (README 4~5장)
# 머리와 꼬리에 같은 내용이 두 번 들어간다.
# ---------------------------------------------------------------------------

_NO_GUESS = """- 추정 금지. 프로필에 없는 값을 상식으로 채우지 않습니다. 다음은 모두 금지되는 추론입니다.
  - 대학생이니 소득이 낮을 것이다
  - 서울에 산다니 서울 소재 대학일 것이다
  - 재학생이니 다른 지원은 안 받을 것이다
  - 휴학이라고 했으니 구직 중일 것이다"""

_RESULT_VALUES = f"""- 결과는 {MET}, {UNMET}, {UNKNOWN} 세 값 중 하나입니다.
  - {MET}: 프로필에 명시된 값으로 조건을 만족하는 것이 분명함
  - {UNMET}: 프로필에 명시된 값이 제외 대상에 분명히 해당함
  - {UNKNOWN}: 프로필에 해당 정보가 없음, 원문 표현이 모호함("등", "그 밖에"), 판단에 외부 정보가 필요함
- {UNMET} 은 프로필에 **명시된 값**으로 제외 대상에 해당하는 것이 분명할 때만 씁니다. 조금이라도 애매하면 {UNKNOWN} 입니다."""

_EXCERPT_RULE = f"""- 발췌는 <policy_text> 의 연속된 부분을 **그대로 복사**합니다. {MIN_EXCERPT_LEN}자 이상 {MAX_EXCERPT_LEN}자 이하, 조건당 1개입니다. 줄임표, 요약, 문장 고쳐 쓰기는 금지입니다.
- 조건 요약은 {MAX_NAME_LEN}자 이내 명사형입니다.
- <policy_text> 에 없는 조건을 만들지 않습니다."""

_OUT_OF_SCOPE = """- 나이, 지역, 신분, 소득 %, 마감은 판정하지 않습니다. 규칙 엔진이 이미 판정했습니다. 예외 문장에 겹쳐 나와도 다시 판정하지 않습니다."""

#: `needed_field` 에 쓸 수 있는 값. `values.EXTRA_FIELD_VALUES` 의 키와
#: `ASK_NOTICE` 뿐이다. 목록을 손으로 적지 않는 이유는 모델이 새 항목 이름을
#: 만들면 후속 질문이 그 항목을 물을 수 없기 때문이다.
_NEEDED_FIELD_CHOICES = "\n".join(
    f"  - {field} ({field_label(field)})" for field in EXTRA_FIELD_VALUES
) + f'\n  - "{ASK_NOTICE}" (위 항목으로 해결되지 않을 때)'

# ---------------------------------------------------------------------------
# 시스템 지시문 (상수)
# ---------------------------------------------------------------------------

JUDGE_SYSTEM_PROMPT = f"""당신은 서울 청년 지원제도 공고를 읽고 **예외 조건만** 판정합니다.

판정 대상은 공고 원문의 문장형 예외 조건(제외 대상, 중복 수혜 제한, 자격 제한 문장)입니다.
{_OUT_OF_SCOPE}

{_RESULT_VALUES}

{UNMET} 오판은 되돌릴 수 없습니다. 사용자가 받을 수 있는 제도를 포기하게 만듭니다.
{UNKNOWN} 오판은 질문이 하나 늘거나 조건부 문장이 하나 붙을 뿐입니다.
그래서 판단이 갈리면 {UNKNOWN} 쪽으로 둡니다.

{_NO_GUESS}

{_EXCERPT_RULE}

- 결과가 {UNKNOWN} 이면 판정에 필요한 프로필 항목을 적습니다. 허용된 항목 이름만 씁니다. 새 항목 이름을 만들지 않습니다.
- 날짜를 계산하지 않습니다. 마감과 D-day 는 코드가 계산합니다.
- 정책 단위 판정 상태(likely, check, unlikely)를 만들지 않습니다. 상태는 코드가 확정합니다.
- 발췌는 코드가 원문과 문자열로 대조합니다. 원문과 한 글자라도 다르면 그 조건은 화면에서 사라집니다.

출력은 지정된 형식만 내놓습니다. 설명, 인사, 코드 블록 표시를 덧붙이지 않습니다."""

# ---------------------------------------------------------------------------
# 사용자 메시지 앞부분: 판정 규칙
# ---------------------------------------------------------------------------

_RULES_HEAD = f"""[판정 규칙]
아래 <policy_text> 의 공고 원문과 <exception_condition> 의 예외 문장, <profile> 의 사용자 정보를 보고 예외 조건을 판정하세요.
같은 규칙을 이 메시지 끝에 한 번 더 적어 두었습니다. 둘 다 지켜야 합니다.

- 판정 대상은 <exception_condition> 안의 문장형 조건뿐입니다.
{_OUT_OF_SCOPE}
{_RESULT_VALUES}
{_NO_GUESS}
{_EXCERPT_RULE}"""

# ---------------------------------------------------------------------------
# 사용자 메시지 끝부분: 출력 형태와 금지 사항
# ---------------------------------------------------------------------------

_OUTPUT_SHAPE = """[출력 형태]
{"conditions": [{"name": "조건 요약", "result": "met|unmet|unknown", "excerpt": "원문 발췌", "needed_field": ""}]}

- 조건이 여러 개면 목록에 나란히 넣습니다.
- 판정할 예외 문장이 없으면 {"conditions": []} 를 돌려줍니다.
"""

_RULES_TAIL = (
    _OUTPUT_SHAPE
    + f"""- name 은 {MAX_NAME_LEN}자 이내 명사형입니다.
- result 는 {MET}, {UNMET}, {UNKNOWN} 중 하나입니다.
- excerpt 는 <policy_text> 의 연속된 부분 그대로, {MIN_EXCERPT_LEN}~{MAX_EXCERPT_LEN}자, 조건당 1개입니다.
- needed_field 는 result 가 {UNKNOWN} 일 때만 채우고, 그 밖에는 빈 문자열로 둡니다. 쓸 수 있는 값은 다음뿐입니다.
{_NEEDED_FIELD_CHOICES}

[다시 확인할 규칙]
{_RESULT_VALUES}
{_NO_GUESS}
{_EXCERPT_RULE}
{_OUT_OF_SCOPE}
- 날짜를 계산하지 않습니다.
- 판정 상태(likely, check, unlikely)를 만들지 않습니다. 상태는 코드가 정합니다.
- 프로필에 "{NO_VALUE}" 으로 적힌 항목은 값이 없는 것입니다. 그 항목이 필요한 조건은 {UNKNOWN} 입니다."""
)


# ---------------------------------------------------------------------------
# 조립
# ---------------------------------------------------------------------------


def build_judge_prompt(exceptions_text: str, profile: dict, raw_text: str) -> str:
    """예외 조건 판정에 쓸 사용자 메시지 본문을 만든다.

    - `exceptions_text`: 판정 대상 예외 문장 (정책 데이터 `exceptions_text`)
    - `profile`: 세션 프로필. 비어 있어도 된다
    - `raw_text`: 공고 원문. 발췌를 꺼내는 대상이다

    원문과 예외 문장은 **한 글자도 바꾸지 않고** 그대로 넣는다. 발췌는 이 문자열과
    대조되므로 여기서 손대면 인용 검증이 전건 실패한다.

    `exceptions_text` 가 비어 있으면 호출하는 쪽에서 판정을 생략한다
    (README 9장). 이 함수는 그 경우에도 예외를 던지지 않고 문자열을 돌려준다.

    원문에 `</policy_text>` 같은 태그 문자열이 섞여 있으면 구분자가 흐려질 수 있다.
    그래도 원문을 치환하지 않는다. 원문을 바꾸는 쪽이 인용 검증을 깨뜨려 더 위험하다.
    """
    return (
        f"{_RULES_HEAD}\n\n"
        f"<policy_text>\n{_as_text(raw_text)}\n</policy_text>\n\n"
        f"<exception_condition>\n{_as_text(exceptions_text)}\n</exception_condition>\n\n"
        f"<profile>\n{_describe_profile(profile)}\n</profile>\n\n"
        f"{_RULES_TAIL}"
    )
