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

# 영향 정책 수를 모를 때 쓸 이유 문구를 표에 적지 않은 항목의 기본값.
# ``{count}`` 를 쓰는 항목은 ``reason_no_count`` 를 채워야 하고, 빠진 경우에만 여기로 온다.
# 빈 자리("개 제도가")를 화면에 내보내는 것보다 뜻이 같은 짧은 문장이 낫다.
REASON_WITHOUT_COUNT_FALLBACK = "이 답변으로 판정이 갈리는 제도가 있어요"


def _tidy(text: str) -> str:
    """이어 붙인 문구의 공백 정리.

    자리(``{policies}``)를 비우고 채우면 이중 공백이나 앞 공백이 남는다.
    화면 문구라 눈에 보이므로 여기서 정리한다.
    """
    return " ".join(text.split())


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
    reason_no_count 개수를 모를 때 쓸 이유 문구. ``{count}`` 를 쓰는 항목만 채운다

    ``reason_no_count`` 를 따로 두는 이유는 ``/session`` 의 첫 후속 질문처럼 정책 정보 없이
    본문을 만드는 경로가 있기 때문이다. 그때 표의 문장을 그대로 쓰면 "0개 제도"가 화면에
    나간다. 문장을 즉석에서 고쳐 쓰지 않고, **개수 없는 형태도 표에 적어 둔다**
    (CONTRIBUTING.md 8장: 화면 문구는 표에서 관리).
    """

    field: str
    question: str
    reason: str
    options: Tuple[Option, ...]
    allow_free_text: bool = False
    reason_no_count: str = ""

    def countless_reason(self) -> str:
        """영향 정책 수를 모를 때의 이유 문구 (README 5장 표).

        표의 문장을 버리지 않고 **개수 자리만 빼는 방향**을 택했다. 이유 문구의 목적은
        "왜 묻는지" 알리는 것이고(docs/01-glossary-profile.md 8장), 개수는 그 문장을
        더 구체적으로 만드는 덧붙임일 뿐이다. 개수를 모른다고 이유 자체를 지우면
        질문이 심문이 된다.
        """
        if self.reason_no_count:
            return self.reason_no_count
        if "{count}" not in self.reason:
            # 개수 자리가 없는 항목은 표의 문장이 그대로 성립한다.
            return _tidy(self.reason.format(policies="", count=0))
        return REASON_WITHOUT_COUNT_FALLBACK

    def render_reason(self, policy_titles: Sequence[str], count: int = 0) -> str:
        """이유 문구를 채운다.

        정책명이 없으면(규칙 엔진이 제목을 넘기지 않은 경우) **표의 문장을 유지하고
        정책명 자리만 비운다.** 표 밖 문장으로 갈아타지 않는 이유는, 표에 없는 문구가
        화면에 나가는 순간 문구 관리가 두 곳으로 갈리기 때문이다 (CONTRIBUTING.md 8장).

        개수가 0이면(정책 정보 없이 부른 경우) ``countless_reason`` 으로 넘긴다.
        "0개 제도"는 틀린 안내다.
        빈 자리에 "○○" 를 그대로 내보내면 미완성 화면처럼 보인다.
        """
        shown = list(policy_titles[:MAX_REASON_POLICIES])
        # 제목만 오고 개수가 비어 있는 호출도 있다. 제목 수를 하한으로 본다.
        total = count if count > 0 else len(shown)
        if total <= 0:
            return self.countless_reason()

        names = ", ".join(shown)
        if names and total > len(shown):
            names = f"{names} 등"
        return _tidy(self.reason.format(policies=names, count=total))


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


# 직접 입력(``allow_free_text``)을 어디에 열 것인가
# ------------------------------------------------
# README 5장과 docs/01-glossary-profile.md 8장은 "선택지 버튼 + 직접 입력 + 건너뛰기를
# 함께 준다"고 적는다. 그런데 후속 질문 답변은 **해석 단계를 건너뛰고 값을 그대로 프로필에
# 넣는다** (docs/03-api-contract.md 3장). 그래서 직접 입력은 "사용자가 타이핑한 글자가
# 그 항목의 허용 값이 될 수 있는 항목"에서만 뜻이 있다.
#
# - ``district`` 는 허용 값이 자치구 이름 25개다. 사용자가 "은평구"라고 적으면 그대로
#   허용 값이 된다. 버튼은 5개뿐이므로 직접 입력이 유일한 경로다 → 연다.
# - 나머지 항목은 허용 값이 코드 값(``monthly_rent``, ``4_plus`` …)이다. 사용자가 무엇을
#   적어도 허용 값 밖이라 규칙 엔진이 모르는 값이 프로필에 들어간다. 답을 받은 것처럼
#   보이지만 판정은 그대로 미확인이다. **건너뛰기보다 나쁘다** → 닫는다.
#
# 대신 닫는 쪽은 선택지가 허용 값을 **전부** 덮게 한다. 그래서 ``housing_type`` 에
# ``other``(기타)를 넣었다. 버튼에 없는 답을 가진 사용자의 출구는 직접 입력이 아니라
# "기타" 버튼이다. ``other_benefit`` 처럼 값이 있음/없음뿐인 항목은 이미 전부 덮고 있다.
#
# 결론: 명세(README 5장, docs/01-glossary-profile.md 8장)의 "모든 질문에 직접 입력"과
# **다르게 정했다.** 두 문서에 이 예외를 적어야 한다 (보고에 남김).
TEMPLATES: Dict[str, QuestionTemplate] = {
    fields.INCOME_BRACKET: QuestionTemplate(
        field=fields.INCOME_BRACKET,
        question="가구 소득이 어느 정도인지 아세요?",
        reason="{policies} {count}개 제도가 소득 기준으로 갈려요",
        options=_INCOME_OPTIONS,
        # 표의 문장에서 개수 표현만 뺀 형태. 뜻("소득 기준으로 갈린다")은 그대로 둔다.
        reason_no_count="소득 기준으로 판정이 갈리는 제도가 있어요",
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
        # 허용 값 5개를 모두 버튼으로 둔다 (docs/01-glossary-profile.md 3장).
        # README 5장 표에는 "기타"가 없지만 문서가 어긋난 경우이고, 값의 기준은
        # docs/01-glossary-profile.md 다 (CONTRIBUTING.md 8장: 문서가 기준).
        # "기타"가 없으면 고시원·하숙에 사는 사용자는 답할 버튼이 없고, 직접 입력도
        # 닫혀 있어(위 주석) 건너뛰기밖에 남지 않는다.
        options=(
            Option("parents", "부모님 집"),
            Option("monthly_rent", "월세"),
            Option("jeonse", "전세"),
            Option("dormitory", "기숙사"),
            Option("other", "기타"),
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

# ---------------------------------------------------------------------------
# 미래 계획 확인 질문 (README 3장 해석 기준, 8장 T3, 9장 C4)
#
# "다음 학기에 휴학할 거예요" 처럼 시점이 planned 인 변경은 프로필을 바꾸지 않고
# (interpret.MergeResult.held) 어느 기준으로 볼지 사용자에게 묻는다.
# ---------------------------------------------------------------------------

# 이 질문의 항목 이름. **프로필 항목이 아니다.**
#
# 묻는 것은 "휴학 예정을 반영할까"가 아니라 "어느 시점을 기준으로 판정할까"다.
# 그래서 프로필 항목 표(docs/01-glossary-profile.md 2~3장)에 없는 이름을 쓴다.
# 표에 없으므로 ``interpret.merge`` 는 이 값을 프로필에 넣지 않고 ``unknown_field`` 로
# 버린다. 그게 의도한 동작이다. 서버는 이 답변을 프로필이 아니라 **다음 해석의 기준**으로
# 들고 있는다 (세션이 기억하는 값, docs/03-api-contract.md 11장).
#
# 전에는 이 질문의 field 가 ``status`` 였다. 후속 질문 답변은 해석을 건너뛰고 값을 그대로
# 프로필에 넣으므로(docs/03-api-contract.md 3장) ``status="planned"`` 라는 허용 값 밖
# 값이 프로필에 박혔다. 이름을 바꾼 것이 그 구멍을 막는다.
PLANNED_BASIS_FIELD = "planned_basis"

# 항목별 질문 틀. ``{label}`` 은 바뀔 값의 화면 문구(interpret.label_of 결과).
#
# 항목마다 틀을 따로 두는 이유: 라벨만 끼우면 되는 항목("휴학 기준으로 볼까요")과
# 항목 이름이 앞에 있어야 문장이 되는 항목("가구원 수 3인 기준으로 볼까요")이 섞여 있다.
# 하나의 틀로 밀면 "있음 기준으로 볼까요"(고용보험)처럼 무슨 말인지 모르는 문장이 나온다.
# 조사(으로/로)가 필요한 형태는 일부러 피했다. 조사 선택을 여기서 또 구현하면
# ``interpret`` 과 같은 규칙이 두 곳에 생긴다.
#
# ``fields.REGION`` 행은 없다. 거주지는 `seoul` 고정이라(docs/01-glossary-profile.md 2장)
# ``interpret._clean_change`` 가 `region` 변경을 timing 과 무관하게 버린다. planned 로 온
# 거주지 변경도 ``held`` 에 들어가지 않으므로 이 질문은 만들어질 수 없었다.
PLANNED_BASIS_QUESTIONS: Dict[str, str] = {
    fields.AGE: "{label} 기준으로 볼까요, 지금 기준으로 볼까요?",
    fields.DISTRICT: "{label} 기준으로 볼까요, 지금 기준으로 볼까요?",
    fields.STATUS: "{label} 기준으로 볼까요, 지금 기준으로 볼까요?",
    fields.INCOME_BRACKET: "가구 소득 {label} 기준으로 볼까요, 지금 기준으로 볼까요?",
    fields.HOUSING_TYPE: "{label} 기준으로 볼까요, 지금 기준으로 볼까요?",
    fields.RESIDENCE_PERIOD: "서울 거주 기간 {label} 기준으로 볼까요, 지금 기준으로 볼까요?",
    fields.REMAINING_SEMESTERS: "남은 학기 {label} 기준으로 볼까요, 지금 기준으로 볼까요?",
    fields.JOB_SEEKING_PERIOD: "구직 기간 {label} 기준으로 볼까요, 지금 기준으로 볼까요?",
    fields.EMPLOYMENT_INSURANCE: "고용보험 가입 이력이 바뀐 뒤 기준으로 볼까요, 지금 기준으로 볼까요?",
    fields.OTHER_BENEFIT: "다른 지원 수혜 여부가 바뀐 뒤 기준으로 볼까요, 지금 기준으로 볼까요?",
    fields.HOUSEHOLD_SIZE: "가구원 수 {label} 기준으로 볼까요, 지금 기준으로 볼까요?",
    # "직전 학기 성적 기준 이상 기준으로" 가 되어 라벨을 끼울 수 없다. 라벨 없는 틀을 쓴다.
    fields.LAST_GPA: "직전 학기 성적이 바뀐 뒤 기준으로 볼까요, 지금 기준으로 볼까요?",
}

# 라벨을 못 구했거나 표에 없는 항목일 때의 틀. 라벨 자리가 없다.
# 빈 자리("  기준으로 볼까요")가 화면에 나가는 것보다 항목을 말하지 않는 편이 낫다.
# 선택지 문구("바뀐 뒤 기준으로")와 같은 말이라 사용자가 무엇을 고르는지는 알 수 있다.
PLANNED_BASIS_QUESTION_FALLBACK = "바뀐 뒤 기준으로 볼까요, 지금 기준으로 볼까요?"

# 항목별 이유 문구. 항목 이름이 문장에 들어가므로 항목마다 따로 적는다.
# 조사를 코드로 고르지 않기 위해 완성된 문장으로 둔다 (interpret.NOTICE_TEMPLATES 와 같은 방식).
# `region` 행은 위 질문 틀과 같은 이유로 없다.
PLANNED_BASIS_REASONS: Dict[str, str] = {
    fields.AGE: "나이가 바뀌면 맞는 제도가 달라져요",
    fields.DISTRICT: "사는 곳이 바뀌면 맞는 제도가 달라져요",
    fields.STATUS: "신분이 바뀌면 맞는 제도가 달라져요",
    fields.INCOME_BRACKET: "가구 소득이 바뀌면 판정이 달라져요",
    fields.HOUSING_TYPE: "주거 형태가 바뀌면 맞는 제도가 달라져요",
    fields.RESIDENCE_PERIOD: "서울 거주 기간이 바뀌면 판정이 달라져요",
    fields.REMAINING_SEMESTERS: "남은 학기가 바뀌면 맞는 제도가 달라져요",
    fields.JOB_SEEKING_PERIOD: "구직 기간이 바뀌면 판정이 달라져요",
    fields.EMPLOYMENT_INSURANCE: "고용보험 가입 이력이 바뀌면 판정이 달라져요",
    fields.OTHER_BENEFIT: "중복으로 못 받는 제도가 있어요",
    fields.HOUSEHOLD_SIZE: "가구원 수가 바뀌면 소득 기준이 달라져요",
    fields.LAST_GPA: "직전 학기 성적이 바뀌면 판정이 달라져요",
}

PLANNED_BASIS_REASON_FALLBACK = "조건이 바뀌면 판정이 달라져요"

# 시점 선택지. 값은 ``fields.PLANNED`` / ``fields.CURRENT`` 그대로다.
# 이 값은 프로필 값이 아니라 **기준 시점**이고, 받는 쪽이 시점 값으로 읽는다.
PLANNED_BASIS_OPTIONS: Tuple[Option, ...] = (
    Option(fields.PLANNED, "바뀐 뒤 기준으로"),
    Option(fields.CURRENT, "지금 기준으로"),
)


def planned_question(field: str, value_label: str = "") -> str:
    """planned 변경 확인 질문 문구 (README 3장, 8장 T3).

    바뀔 항목 이름과 값 라벨을 받아 **표의 틀에 라벨만 끼운다.** 즉석 작문은 하지 않는다
    (CONTRIBUTING.md 8장: 화면 문구는 표에서 관리).

    ``value_label`` 은 ``interpret.label_of(field, value)`` 의 결과를 넘기면 된다.
    라벨이 비어 있거나(표에 없는 값) 항목이 표에 없으면 라벨 없는 틀을 쓴다.
    빈 자리를 화면에 내보내지 않는다.
    """
    label = (value_label or "").strip()
    template = PLANNED_BASIS_QUESTIONS.get(field)
    if template is None or not label:
        return PLANNED_BASIS_QUESTION_FALLBACK
    return _tidy(template.format(label=label))


def planned_reason(field: str) -> str:
    """planned 변경 확인 질문의 이유 한 줄 (README 5장 방식, docs/01-glossary-profile.md 8장).

    표에 없는 항목은 항목 이름을 말하지 않는 기본 문구를 쓴다.
    """
    return PLANNED_BASIS_REASONS.get(field, PLANNED_BASIS_REASON_FALLBACK)


def planned_template(field: str, value_label: str = "") -> QuestionTemplate:
    """planned 변경 확인 질문의 틀 하나를 만든다.

    ``TEMPLATES`` 에 넣지 않는 이유는 ``PLANNED_BASIS_FIELD`` 가 프로필 항목이 아니기
    때문이다. 표에 넣으면 ``missing_templates`` 와 후속 질문 선택(``followup``)이
    프로필 항목으로 오해한다.
    """
    return QuestionTemplate(
        field=PLANNED_BASIS_FIELD,
        question=planned_question(field, value_label),
        reason=planned_reason(field),
        options=PLANNED_BASIS_OPTIONS,
        # 기준 시점은 둘 중 하나다. 직접 입력을 열면 시점으로 읽을 수 없는 글자가 들어온다.
        allow_free_text=False,
    )


# 항목과 값을 모를 때 쓰는 기본 확인 질문.
# 이름을 유지하는 이유는 이미 참조하는 곳이 있어서다(tests/test_questions.py).
# 내용은 새 구조를 그대로 쓴다. field 가 ``status`` 가 아니라 ``planned_basis`` 다.
PLANNED_CHANGE_QUESTION = planned_template("")


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

    정책 정보 없이 불러도 된다. ``/session`` 의 첫 후속 질문이 그 경로다. 그때는 개수를
    말하지 않는 이유 문구가 나간다 (``QuestionTemplate.countless_reason``).
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


def build_planned_question(field: str, value_label: str = "") -> Dict[str, object]:
    """planned 변경 확인 질문 본문 (README 3장, 9장 C4).

    형식은 ``build`` 와 같은 docs/03-api-contract.md 6장 키를 쓴다. 프론트가 후속 질문
    카드를 하나의 모양으로만 그리기 때문에, 확인 질문이라고 다른 키를 쓰면 화면이 갈린다.

    ``field`` 는 **바뀔 프로필 항목**(예: ``status``)을 넘긴다. 본문에 실리는 ``field`` 는
    ``PLANNED_BASIS_FIELD`` 이고, 프로필 항목이 아니다. 답변은 프로필에 들어가지 않고
    다음 해석의 기준 시점으로 쓰인다 (위 ``PLANNED_BASIS_FIELD`` 설명).

    건너뛰면
    -------
    ``allow_skip`` 은 항상 참이다. 건너뛰면 **지금 기준을 유지한다.** planned 변경은
    애초에 프로필에 반영하지 않았으므로(README 3장) 아무것도 하지 않는 것이 곧 지금 기준이고,
    "지금 신청할 수 있는 제도를 찾는다"는 목적과도 같은 방향이다. 건너뛴 항목을 다시 묻지
    않는 규칙은 ``PLANNED_BASIS_FIELD`` 이름으로 세션이 기록한다
    (docs/03-api-contract.md 11장).
    """
    template = planned_template(field, value_label)
    return {
        "field": template.field,
        "question": template.question,
        "reason": template.reason,
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
