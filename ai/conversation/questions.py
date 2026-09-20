"""후속 질문 고정 문구 (ai/conversation/README.md 5장).

**문장 자체를 모델이 새로 만들지 않는다.** 이유는 두 가지다.

1. 같은 상황에서 매번 다른 문장이 나오면 데모가 흔들린다. 5번 단계(후속 질문에 답하면
   카드가 바뀜)가 발표의 핵심 장면이라 문구가 고정되어야 리허설이 의미가 있다.
2. 질문 문구는 화면 문구다. 화면 문구는 표에서 관리한다 (CONTRIBUTING.md 8장).

모델이 하는 일은 이유 문구의 ``{policies}`` 와 ``{count}`` 를 채우는 게 아니라,
P1 단계에서 맥락에 맞게 **다듬는** 것뿐이다. 채우는 것은 코드가 한다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from . import fields

# 이유 문구에 넣을 정책명 최대 개수.
# 세 개를 넘기면 질문 한 줄이 길어져 읽히지 않는다.
MAX_REASON_POLICIES = 2


@dataclass(frozen=True)
class Option:
    """선택지 하나. value 는 프로필에 그대로 들어가는 값, label 은 화면 문구."""

    value: str
    label: str


@dataclass(frozen=True)
class QuestionTemplate:
    """항목 하나의 질문 틀.

    field         프로필 항목 이름
    question      질문 문구. 그대로 화면에 나간다
    reason        이유 문구 틀. {policies} 와 {count} 를 코드가 채운다
    options       선택지. 순서도 화면 순서다
    allow_free_text 직접 입력 허용 여부
    """

    field: str
    question: str
    reason: str
    options: Tuple[Option, ...]
    allow_free_text: bool = False

    def render_reason(self, policy_titles: Sequence[str], count: int) -> str:
        """이유 문구를 채운다.

        정책명이 없으면(규칙 엔진이 제목을 넘기지 않은 경우) 개수만으로 말한다.
        빈 자리에 "○○" 를 그대로 내보내면 미완성 화면처럼 보인다.
        """
        if not policy_titles:
            return f"{count}개 제도의 판정이 이 답변으로 갈려요"

        shown = list(policy_titles[:MAX_REASON_POLICIES])
        names = ", ".join(shown)
        if count > len(shown):
            names = f"{names} 등"
        return self.reason.format(policies=names, count=count)


# 소득 구간은 여러 곳에서 쓰므로 한 번만 정의한다.
_INCOME_OPTIONS = (
    Option("under_50", "기준 중위소득 50% 이하"),
    Option("50_100", "50~100%"),
    Option("100_150", "100~150%"),
    Option("over_150", "150% 초과"),
    Option("unknown", "잘 모르겠어요"),
)

# 자치구 선택지. 전체 25개를 버튼으로 깔면 질문 카드가 화면을 덮는다.
# 자주 쓰는 다섯 개만 버튼으로 두고 나머지는 직접 입력으로 받는다.
_FREQUENT_DISTRICTS = ("관악구", "동작구", "성북구", "노원구", "광진구")


TEMPLATES: Dict[str, QuestionTemplate] = {
    fields.INCOME_BRACKET: QuestionTemplate(
        field=fields.INCOME_BRACKET,
        question="가구 소득이 어느 정도인지 아세요?",
        reason="{policies} {count}개 제도가 소득 기준으로 갈려요",
        options=_INCOME_OPTIONS,
    ),
    fields.DISTRICT: QuestionTemplate(
        field=fields.DISTRICT,
        question="어느 구에 사세요?",
        reason="구에서 하는 지원이 있어요",
        options=tuple(Option(name, name) for name in _FREQUENT_DISTRICTS),
        allow_free_text=True,
    ),
    fields.HOUSING_TYPE: QuestionTemplate(
        field=fields.HOUSING_TYPE,
        question="지금 어떻게 지내고 계세요?",
        reason="월세 지원은 주거 형태에 따라 달라요",
        options=(
            Option("parents", "부모님 집"),
            Option("monthly_rent", "월세"),
            Option("jeonse", "전세"),
            Option("dormitory", "기숙사"),
        ),
    ),
    fields.RESIDENCE_PERIOD: QuestionTemplate(
        field=fields.RESIDENCE_PERIOD,
        question="서울에 산 지 얼마나 되셨어요?",
        reason="거주 기간 조건이 있는 제도가 있어요",
        options=(
            Option("under_6m", "6개월 미만"),
            Option("6m_1y", "6개월~1년"),
            Option("over_1y", "1년 이상"),
        ),
    ),
    fields.REMAINING_SEMESTERS: QuestionTemplate(
        field=fields.REMAINING_SEMESTERS,
        question="졸업까지 몇 학기 남았어요?",
        reason="졸업예정자 대상 제도가 있어요",
        options=(
            Option("one", "1학기"),
            Option("two_plus", "2학기 이상"),
        ),
    ),
    fields.JOB_SEEKING_PERIOD: QuestionTemplate(
        field=fields.JOB_SEEKING_PERIOD,
        question="구직 활동한 지 얼마나 됐어요?",
        reason="구직 기간 조건이 있어요",
        options=(
            Option("under_6m", "6개월 미만"),
            Option("over_6m", "6개월 이상"),
        ),
    ),
    fields.EMPLOYMENT_INSURANCE: QuestionTemplate(
        field=fields.EMPLOYMENT_INSURANCE,
        question="일하면서 고용보험에 가입한 적 있어요?",
        reason="훈련 지원 조건과 관련 있어요",
        options=(
            Option("yes", "있음"),
            Option("no", "없음"),
            Option("unknown", "모르겠어요"),
        ),
    ),
    fields.OTHER_BENEFIT: QuestionTemplate(
        field=fields.OTHER_BENEFIT,
        question="지금 받고 있는 다른 청년 지원금이 있나요?",
        reason="중복으로 못 받는 제도가 있어요",
        options=(
            Option("yes", "있음"),
            Option("no", "없음"),
        ),
    ),
    fields.HOUSEHOLD_SIZE: QuestionTemplate(
        field=fields.HOUSEHOLD_SIZE,
        question="함께 사는 가족은 몇 명이에요?",
        reason="소득 기준이 가구원 수로 달라져요",
        options=(
            Option("1", "1인"),
            Option("2", "2인"),
            Option("3", "3인"),
            Option("4_plus", "4인 이상"),
        ),
    ),
    fields.LAST_GPA: QuestionTemplate(
        field=fields.LAST_GPA,
        question="직전 학기 성적이 공고 기준 이상인가요?",
        reason="성적 조건이 있는 장학금이 있어요",
        options=(
            Option("above", "기준 이상"),
            Option("below", "기준 미만"),
            Option("unknown", "모르겠어요"),
        ),
    ),
}

# 미래 계획을 말했을 때 붙이는 확인 질문 (README 3장 해석 기준).
# "다음 학기에 휴학할 거예요" 처럼 시점이 planned 인 변경은 프로필을 바꾸지 않고
# 어느 기준으로 볼지 사용자에게 묻는다.
PLANNED_CHANGE_QUESTION = QuestionTemplate(
    field=fields.STATUS,
    question="휴학 후 기준으로 볼까요, 지금 기준으로 볼까요?",
    reason="신분이 바뀌면 받을 수 있는 제도가 달라져요",
    options=(
        Option("planned", "바뀐 뒤 기준으로"),
        Option("current", "지금 기준으로"),
    ),
)


def get(field: str) -> Optional[QuestionTemplate]:
    """항목의 질문 틀. 표에 없으면 None.

    None 을 받으면 그 항목은 묻지 않는다. 표에 없는 항목을 즉석에서 만들어 묻지 않는다
    (docs/01-glossary-profile.md 3장).
    """
    return TEMPLATES.get(field)


def build(
    field: str,
    policy_titles: Sequence[str] = (),
    affected_count: int = 0,
) -> Optional[Dict[str, object]]:
    """스트리밍 이벤트에 실을 후속 질문 본문을 만든다.

    반환 형식은 docs/03-api-contract.md 6장을 따른다.
    건너뛰기는 항상 허용한다. 건너뛸 수 없는 질문은 심문이 된다.
    """
    template = get(field)
    if template is None:
        return None

    count = affected_count or len(policy_titles)
    return {
        "field": template.field,
        "question": template.question,
        "reason": template.render_reason(policy_titles, count),
        "options": [
            {"value": option.value, "label": option.label}
            for option in template.options
        ],
        "allow_free_text": template.allow_free_text,
        "allow_skip": True,
    }


def missing_templates() -> List[str]:
    """질문 틀이 없는 추가 항목. 있으면 표를 채워야 한다."""
    return [field for field in fields.FIELD_ORDER if field not in TEMPLATES]
