"""대화 평가 케이스 데이터 (ai/conversation/README.md 8장·9장).

언제 쓰는가
-----------
**프롬프트를 고칠 때마다 다시 돌려 회귀를 잡는다.** 해석 프롬프트의 한 줄을 고치면
고치려던 입력만 바뀌지 않는다. "월 150 벌어요" 를 잘 읽게 만든 문장이 "다음 학기에
휴학할 거예요" 를 프로필 변경으로 만들어 버리는 식으로 다른 입력이 함께 움직인다.
그래서 한 케이스만 눈으로 확인하는 방식은 쓰지 않는다. 10개와 C1~C8 을 한 번에 돌린다.

완료 기준 (README 11장)
-----------------------
- 해석 테스트 10개 중 **9개 이상** 기대대로
- 대화 평가 **C1~C8 전부** 통과

이 모듈에는 채점 로직이 없다
----------------------------
데이터만 둔다. 채점은 평가를 실행하는 쪽에서 한다. 기대값과 채점이 같은 파일에 있으면
실패했을 때 채점이 아니라 기대값을 고치게 된다. 기대값을 고치는 건 회귀 검사를 끄는 것과
같다. 그래서 각 케이스의 ``note`` 에 **왜 그 기대값인지**를 남겼다. 기대값을 고치려면
먼저 그 이유를 반박해야 한다.

12:00 통합 때 맞춰야 할 것 — 값 표기 충돌
-----------------------------------------
같은 개념에 두 가지 표기가 돌아다닌다.

| 개념 | 이 파일 (문서 기준) | ``ai/judgment``, ``ai/citation`` |
| --- | --- | --- |
| 프로필 항목 이름 | ``status``, ``income_bracket`` | ``"현재 상태"``, ``"가구 소득"`` |
| 조건 결과 | ``met``, ``unmet``, ``unknown`` | ``"충족"``, ``"미충족"``, ``"미확인"`` |

이 파일은 **문서 쪽(영문 snake_case)** 을 따른다. ``CONTRIBUTING.md`` 7-2 와
``docs/01-glossary-profile.md`` 2~3장이 고정 값을 그렇게 정했고, 문서와 코드가 다르면
문서가 기준이라는 규칙(``CONTRIBUTING.md`` 8장)이 있다.

**12:00 통합 때 한쪽으로 모아야 한다.** 그대로 두면 AI A 가 만든 프로필 변경
(``{"status": "on_leave"}``)이 AI B 의 판정 입력(``{"현재 상태": "휴학"}``)에 들어가지 않고
조용히 무시된다. 값이 비어 보이면 예외 조건은 전부 ``unknown`` 이 되고, 모든 카드가
"확인이 필요해요" 로 떨어진다. 예외가 나지 않아서 통합 직전까지 안 보일 종류의 실패다.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field as dataclass_field
from typing import Dict, List, Optional, Tuple

from . import fields

# --------------------------------------------------------------------------
# 통과 기준 문구
#
# README 9장의 통과 기준을 **하나씩 확인 가능한 항목**으로 쪼갠 것이다.
# "요약 문장 있음, 각주 있음, 600자 이내, 금지 표현 없음" 을 한 문자열로 두면
# 셋은 맞고 하나가 틀린 경우를 부분 실패로 기록할 수 없고, 무엇이 깨졌는지도 남지 않는다.
# 문구를 상수로 두는 이유는 케이스마다 같은 기준을 다르게 적는 것을 막기 위해서다.
# --------------------------------------------------------------------------

HAS_SUMMARY = "요약 문장 있음"
HAS_FOOTNOTE = "각주 있음"
WITHIN_600 = "600자 이내"
NO_BANNED_PHRASE = "금지 표현 없음"
HAS_CLOSING = "고정 마무리 문구 있음"

NO_REPEATED_QUESTION = "같은 질문 다시 안 함"
MOVES_TO_NEXT_FIELD = "다음 항목으로 넘어감"
NO_FOLLOWUP = "후속 질문 없음"
FOLLOWUP_STOPS = "더 이상 질문하지 않음"
HAS_CONFIRM_QUESTION = "기준 확인 질문 있음"

HAS_PROFILE_UPDATE = "프로필 변경 표시 있음"
PROFILE_UNCHANGED = "프로필 값 그대로 유지"
HOUSING_POLICY_APPEARS = "주거 분야 정책이 결과에 등장"

OUT_OF_SCOPE_ONE_LINE = "범위 밖 안내 한 줄"
HAS_CAPABILITY_HINT = "할 수 있는 것 안내 있음"
NO_RECALCULATION = "규칙 재계산·판정을 건너뜀"

EMPTY_RESULT_NOTICE = "결과 없음 안내 있음"
HAS_WIDEN_HINT = "조건 넓히는 방법 안내 있음"
HAS_DATA_SCOPE_LINE = "데이터 범위 한 줄 있음"

CHECK_SUMMARY = "check 상태 정리 있음"
HAS_CONDITIONAL_NOTE = "조건부 문장 있음"

# 답변을 만드는 모든 케이스에 공통으로 적용되는 기준 (README 11장 완료 기준).
# C1 에만 적혀 있지만 길이와 금지 표현은 답변이 나가는 모든 턴에 걸린다.
_ANSWER_BASELINE: Tuple[str, ...] = (WITHIN_600, NO_BANNED_PHRASE)


# --------------------------------------------------------------------------
# 프로필
# --------------------------------------------------------------------------

# 대표 프로필 P1 (data/profiles/README.md 2장, docs/06-demo-and-metrics.md 1장).
# 데모 프로필과 같은 값이다. P1 에서 흔들리면 데모가 그대로 흔들린다.
# 값은 docs/01-glossary-profile.md 2장의 허용 값을 그대로 쓴다.
BASE_PROFILE: Dict[str, object] = {
    fields.AGE: 23,
    fields.REGION: "seoul",
    fields.DISTRICT: None,
    fields.STATUS: "enrolled",
    fields.CATEGORIES: ["job", "living"],
    fields.INCOME_BRACKET: "unknown",
}


def profile(**overrides: object) -> Dict[str, object]:
    """P1 프로필에 필요한 항목만 덮어쓴다 (``ai/judgment/cases.py`` 와 같은 방식).

    케이스마다 프로필 여섯 줄을 다시 적으면, 나중에 P1 이 바뀌었을 때 일부 케이스만
    고쳐진다. 기준 프로필은 한 곳에만 둔다.
    """
    # 깊은 복사를 쓴다. `dict(BASE_PROFILE)` 는 얕은 복사라 `categories` 리스트를
    # 모든 케이스가 공유했다. 평가 실행기가 프로필을 제자리에서 손대는 순간 케이스 10개와
    # C1~C8 이 조용히 서로 다른 입력으로 돌아간다. 회귀 검사가 회귀를 못 잡는 최악의 형태다.
    merged = copy.deepcopy(BASE_PROFILE)
    merged.update(copy.deepcopy(overrides))
    return merged


# --------------------------------------------------------------------------
# 해석 테스트 T1~T10 (README 8장)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class ProfileChange:
    """기대하는 프로필 변경 하나.

    field    프로필 항목 이름 (docs/01-glossary-profile.md 2장)
    value    허용 값
    timing   ``current`` 또는 ``planned``
    applied  해석 결과에 담기되 **프로필에 반영해야 하는지**

    ``applied`` 를 따로 둔 이유는 ``planned`` 변경 때문이다. "다음 학기에 휴학할 거예요"
    는 해석 결과에는 들어와야 한다(그래야 확인 질문을 붙일 수 있다). 하지만 프로필은
    바뀌지 않는다. 둘을 한 항목으로 두면 "해석이 안 됐다" 와 "해석은 됐는데 잘못 반영했다"
    를 구분할 수 없다. 이 둘은 원인도 고치는 곳도 다르다.
    """

    field: str
    value: object
    timing: str = fields.CURRENT
    applied: bool = True


@dataclass(frozen=True)
class CategoryChange:
    """관심 분야 변경. 추가할 분야와 제외할 분야."""

    add: Tuple[str, ...] = ()
    remove: Tuple[str, ...] = ()


@dataclass(frozen=True)
class InterpretationCase:
    """해석 테스트 케이스 하나.

    id                        T1~T10
    message                   사용자 입력. README 8장 표의 문장 **그대로**
    profile                   해석 시점의 프로필. 기본은 대표 프로필 P1
    expected_intent           기대 의도 (하나만)
    expected_profile_changes  기대 프로필 변경. 항목·값·시점
    expected_extra_answers    기대 추가 항목 답변. 묻지 않았어도 말했으면 반영한다
    expected_category_changes 기대 관심 분야 변경
    expected_policy_ranks     기대하는 언급 정책. 화면 표시 순서(0 이 맨 위)
    expects_confirm_question  planned 변경에 붙는 확인 질문이 나와야 하는지
    expects_income_notice     가구 소득 구간 안내로 이어져야 하는지
    note                      **왜 그 기대값인지**

    ``message`` 를 다듬지 않는 이유는, 다듬은 문장으로 통과시켜 놓고 본선에서 원래
    문장을 받으면 다르게 동작하기 때문이다. 띄어쓰기가 어긋난 문장, 조사가 빠진 문장이
    실제 입력이다.

    언급된 정책을 ``policy_id`` 가 아니라 **표시 순서**로 적는 이유는 "두 번째 거랑 세
    번째 거" 라는 말이 가리키는 대상이 화면 순서이기 때문이다. 정책 번호로 적으면 검수
    데이터가 바뀔 때마다 기대값을 고쳐야 하고, 고치는 사이에 "순서로 특정한다" 는 규칙이
    맞는지 확인하지 못하게 된다.
    """

    id: str
    message: str
    profile: Dict[str, object]
    expected_intent: str
    expected_profile_changes: Tuple[ProfileChange, ...] = ()
    expected_extra_answers: Dict[str, object] = dataclass_field(default_factory=dict)
    expected_category_changes: CategoryChange = CategoryChange()
    expected_policy_ranks: Tuple[int, ...] = ()
    expects_confirm_question: bool = False
    expects_income_notice: bool = False
    note: str = ""


INTERPRETATION_CASES: List[InterpretationCase] = [
    InterpretationCase(
        id="T1",
        message="방학 동안 취업 준비하면서 생활비 지원 받을 수 있는 거 있어?",
        profile=profile(),
        expected_intent=fields.FIND_POLICY,
        expected_category_changes=CategoryChange(add=("job", "living")),
        note=(
            "데모 2단계의 첫 문장이다(docs/06-demo-and-metrics.md 2장). 한 문장에 분야가"
            " 두 개 들어 있고 둘 다 잡아야 한다. '방학 동안' 은 기간 표현이지 프로필"
            " 항목이 아니므로 아무 항목에도 넣지 않는다. 표에 없는 항목을 만들지 않는다."
        ),
    ),
    InterpretationCase(
        id="T2",
        message="저 이번 달부터 자취해요 월세로",
        profile=profile(),
        expected_intent=fields.FIND_POLICY,
        expected_extra_answers={fields.HOUSING_TYPE: "monthly_rent"},
        expected_category_changes=CategoryChange(add=("housing",)),
        note=(
            "주거 형태는 추가 프로필 항목이라 '프로필 변경' 이 아니라 '추가 항목 답변'"
            " 으로 들어온다(README 3장 뽑아낼 것 표). 묻지 않았어도 말했으면 반영한다."
            " '자취' 만으로는 월세인지 전세인지 모르지만 '월세로' 를 붙여 말했으므로"
            " monthly_rent 로 확정할 수 있다. 이 말이 없었다면 값을 넣지 않아야 한다."
            " 주거 분야를 추가하는 것은 사용자가 주거 지원을 찾아 달라고 한 게 아니라,"
            " 상황이 바뀌면 받을 수 있는 것이 달라진다는 게 제품의 전제이기 때문이다."
        ),
    ),
    InterpretationCase(
        id="T3",
        message="다음 학기에 휴학할 거예요",
        profile=profile(),
        expected_intent=fields.FIND_POLICY,
        expected_profile_changes=(
            ProfileChange(
                field=fields.STATUS,
                value="on_leave",
                timing=fields.PLANNED,
                applied=False,
            ),
        ),
        expects_confirm_question=True,
        note=(
            "**프로필을 바꾸지 않는다.** 지금 신청할 수 있는 제도를 찾는 것이 목적이라,"
            " 아직 재학생인 사람에게 휴학생 전용 제도를 지금 신청 가능한 것처럼 보여주면"
            " 틀린 안내가 된다. 반대로 재학생 대상 제도를 화면에서 빼 버리면 지금 신청할"
            " 수 있는 것을 놓치게 한다. 어느 쪽으로 틀려도 손해가 사용자에게 간다."
            " 그래서 값을 확정하지 않고 '휴학 후 기준으로 볼까요, 지금 기준으로 볼까요?'"
            " 를 묻는다(questions.PLANNED_CHANGE_QUESTION). 해석 결과에는 변경이 담기고"
            " (applied=False) 반영만 하지 않는 것이다. 해석 자체를 버리면 확인 질문을"
            " 만들 근거가 없어진다."
        ),
    ),
    InterpretationCase(
        id="T4",
        message="사실 저 졸업했어요",
        profile=profile(),
        expected_intent=fields.FIND_POLICY,
        expected_profile_changes=(
            ProfileChange(field=fields.STATUS, value="job_seeking"),
        ),
        note=(
            "T3 과 짝이다. 이미 일어난 일이므로 시점이 current 이고 프로필을 바꾼다."
            " 허용 값에 '졸업' 자체는 없다. 졸업한 사람은 job_seeking(졸업 후 구직)"
            " 또는 employed(재직)인데, 이 문장에는 취업했다는 말이 없으므로 job_seeking"
            " 으로 둔다. employed 로 넘겨짚으면 취업준비생이 취업 지원 제도에서 빠진다."
            " 폼에 재학이라고 적혀 있어도 대화 값을 따른다(README 3장)."
        ),
    ),
    InterpretationCase(
        id="T5",
        message="두 번째 거랑 세 번째 거 비교해줘",
        profile=profile(),
        expected_intent=fields.COMPARE,
        expected_policy_ranks=(1, 2),
        note=(
            "정책을 화면 표시 순서로 특정한다. 사용자는 정책 번호를 모르고 카드 순서만"
            " 본다. 순서는 0 부터 세므로 두 번째·세 번째는 1, 2 다. 여기서 순서를 잘못"
            " 세면 엉뚱한 두 제도를 비교하는 문단이 나가고, 그게 각주까지 달고 나가면"
            " 틀린 근거가 된다. 비교는 P1 이고 빼는 순서 1번이라, 기능이 빠지면 이"
            " 케이스는 의도만 맞히면 통과다(설명 문단은 만들지 않는다)."
        ),
    ),
    InterpretationCase(
        id="T6",
        message="그냥 결과만 보여줘",
        profile=profile(),
        expected_intent=fields.RESULT_ONLY,
        note=(
            "이 의도가 잡히면 그 세션에서는 먼저 묻지 않는다(README 4장)."
            " '그냥' 처럼 앞에 붙는 말에 휘둘려 smalltalk 로 읽으면 결과가 안 나간다."
        ),
    ),
    InterpretationCase(
        id="T7",
        message="전입신고 어떻게 해?",
        profile=profile(),
        expected_intent=fields.OUT_OF_SCOPE,
        note=(
            "행정 의무 안내는 비목표다(docs/00-overview.md). 주거·거주지와 말이 겹쳐"
            " find_policy 로 읽히기 쉬운 문장이라 일부러 넣었다. 범위 밖으로 읽어야"
            " 규칙 재계산과 판정을 건너뛰고 시간 예산을 쓰지 않는다."
        ),
    ),
    InterpretationCase(
        id="T8",
        message="부모님이랑 같이 살아요",
        profile=profile(),
        expected_intent=fields.FIND_POLICY,
        expected_extra_answers={fields.HOUSING_TYPE: "parents"},
        note=(
            "T2 의 반대 값. 주거 형태를 말했지만 주거 지원을 찾는 말은 아니므로"
            " 관심 분야는 건드리지 않는다. 월세 지원 조건이 주거 형태로 갈리므로"
            " 이 값 하나로 판정이 바뀐다. 가구원 수(household_size)로 옮겨 적지 않는다."
            " 함께 산다는 말이 몇 명인지는 알려주지 않는다."
        ),
    ),
    InterpretationCase(
        id="T9",
        message="월 150 벌어요",
        profile=profile(),
        expected_intent=fields.FIND_POLICY,
        expects_income_notice=True,
        note=(
            "**프로필 변경이 없는 것이 정답이다.** 숫자 소득을 구간으로 바꾸지 않는다"
            " (README 3장). 이유는 두 개다. 첫째, 이 문장은 개인 소득이고 기준은 가구"
            " 소득이다. 가구원 수와 다른 소득을 모르는 상태에서 구간을 정할 수 없다."
            " 둘째, 소득 구간은 기준 중위소득 대비 비율이라 월 금액에서 바로 나오지"
            " 않는다. 여기서 100_150 같은 값을 넣으면 규칙 엔진이 그 값을 근거로 소득"
            " 조건을 met/unmet 으로 확정한다. 사용자가 말하지도 않은 값 때문에 '어려울"
            " 수 있어요' 가 나오는 것이 가장 나쁜 실패다. 대신 가구 소득 구간을 묻는"
            " 쪽으로 연결한다(expects_income_notice)."
        ),
    ),
    InterpretationCase(
        id="T10",
        message="고마워!",
        profile=profile(),
        expected_intent=fields.SMALLTALK,
        note=(
            "한 줄 응대 후 정책 찾기로 유도하고 끝낸다. 판정을 다시 돌리면 20초 예산을"
            " 인사말에 쓰게 된다. 느낌표와 짧은 문장을 find_policy 로 읽지 않아야 한다."
        ),
    ),
]


# --------------------------------------------------------------------------
# 대화 평가 C1~C8 (README 9장)
# --------------------------------------------------------------------------

# 턴 종류. /chat 이 받는 세 경우와 같다 (docs/03-api-contract.md 3장).
MESSAGE = "message"
ANSWER = "answer"
SKIP = "skip"
SKIP_ALL = "skip_all"


@dataclass(frozen=True)
class Turn:
    """대화 한 턴.

    kind     ``message``(자유 메시지), ``answer``(후속 질문 답변),
             ``skip``(항목 하나 건너뛰기), ``skip_all``(묻는 것마다 계속 건너뛰기)
    message  자유 메시지 본문
    field    답변·건너뛰기 대상 항목
    value    답변 값

    턴을 문자열 목록으로 두지 않은 이유는, 후속 질문 답변이 자유 메시지와 다른 경로로
    처리되기 때문이다. 답변이 들어오면 해석 단계를 건너뛰고 값을 바로 프로필에 넣는다
    (docs/03-api-contract.md 3장). C2 는 그 경로를 확인하는 케이스라 "모르겠어요" 를
    메시지로 보내면 확인하려던 것과 다른 것을 확인하게 된다.

    ``skip_all`` 은 C8 을 위해 둔다. 무엇을 물을지는 정책 데이터에 따라 달라지므로
    건너뛸 항목을 미리 적을 수 없다. "물어보는 게 무엇이든 전부 건너뛴다" 가 C8 의
    조건이다.
    """

    kind: str
    message: str = ""
    field: Optional[str] = None
    value: Optional[str] = None


def msg(text: str) -> Turn:
    """자유 메시지 턴."""
    return Turn(kind=MESSAGE, message=text)


def answer(field: str, value: str) -> Turn:
    """후속 질문 답변 턴."""
    return Turn(kind=ANSWER, field=field, value=value)


def skip(field: str) -> Turn:
    """후속 질문 건너뛰기 턴."""
    return Turn(kind=SKIP, field=field)


def skip_all() -> Turn:
    """물어보는 항목마다 계속 건너뛰는 턴."""
    return Turn(kind=SKIP_ALL)


@dataclass(frozen=True)
class Setup:
    """대화 시작 상태.

    profile  프로필 값
    asked    이미 물어본 항목
    skipped  건너뛴 항목

    세션이 기억하는 값과 같다(docs/03-api-contract.md 11장). 평가를 케이스 하나 단위로
    돌릴 수 있게 하려고 시작 상태를 명시한다. C2 를 "C1 을 먼저 돌린 뒤" 로만 적어 두면
    C1 이 실패할 때 C2 의 결과가 무슨 뜻인지 알 수 없다.
    """

    profile: Dict[str, object] = dataclass_field(default_factory=lambda: copy.deepcopy(BASE_PROFILE))
    asked: Tuple[str, ...] = ()
    skipped: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ConversationCase:
    """대화 평가 케이스 하나.

    id                       C1~C8
    setup                    시작 상태
    turns                    사용자 입력 순서
    pass_criteria            통과 기준. 각각 독립적으로 확인 가능한 항목
    depends_on_policy_data   검수 정책 데이터가 확정돼야 판정할 수 있는 케이스
    note                     이 케이스가 무엇을 지키는지
    """

    id: str
    setup: Setup
    turns: Tuple[Turn, ...]
    pass_criteria: Tuple[str, ...]
    depends_on_policy_data: bool = False
    note: str = ""


_DEMO_FIRST_MESSAGE = "방학 동안 취업 준비하면서 생활비 지원 받을 수 있는 거 있어?"


CONVERSATION_CASES: List[ConversationCase] = [
    ConversationCase(
        id="C1",
        setup=Setup(profile=profile()),
        turns=(msg(_DEMO_FIRST_MESSAGE),),
        pass_criteria=(
            HAS_SUMMARY,
            HAS_FOOTNOTE,
            WITHIN_600,
            NO_BANNED_PHRASE,
            HAS_CLOSING,
        ),
        note=(
            "데모 2~3단계 그대로다. 여기가 깨지면 뒤의 모든 케이스가 같이 깨지므로 가장"
            " 먼저 돌린다. 요약 문장과 각주는 각각 다른 것을 지킨다. 요약은 '결론 먼저',"
            " 각주는 '근거 없는 판정 문장 없음' 이다. 한쪽만 되는 상태를 부분 실패로"
            " 남기려고 기준을 쪼갰다."
        ),
    ),
    ConversationCase(
        id="C2",
        setup=Setup(profile=profile(), asked=(fields.INCOME_BRACKET,)),
        turns=(answer(fields.INCOME_BRACKET, "unknown"),),
        pass_criteria=(
            NO_REPEATED_QUESTION,
            MOVES_TO_NEXT_FIELD,
            HAS_CONDITIONAL_NOTE,
            *_ANSWER_BASELINE,
        ),
        note=(
            "**C8 과 함께 가장 중요하다.** 이 두 개가 깨지면 후속 질문이 심문이 된다."
            " 사용자가 '모르겠어요' 라고 답한 항목을 다시 물으면, 대화형이라는 인상이"
            " 한순간에 깨진다. 답을 못 하는 질문을 두 번 받는 경험은 서비스가 내 말을"
            " 듣지 않는다는 뜻으로 읽힌다. unknown 은 건너뛴 것과 같게 다루되, 질문을"
            " 멈추는 게 아니라 **다음 항목으로 넘어가야** 한다. 소득을 몰라도 조건부"
            " 문장으로 안내가 이어지는지도 함께 본다. 소득 모름에서 결과가 비는 것을"
            " 막는 것이 조건부 문장을 P0 로 둔 이유다."
        ),
    ),
    ConversationCase(
        id="C3",
        setup=Setup(profile=profile()),
        turns=(msg("저 이번 달부터 자취해요 월세로"),),
        pass_criteria=(
            HAS_PROFILE_UPDATE,
            HOUSING_POLICY_APPEARS,
            HAS_FOOTNOTE,
            *_ANSWER_BASELINE,
        ),
        depends_on_policy_data=True,
        note=(
            "데모 6단계. 한마디로 프로필이 바뀌고 카드가 새로 등장하는 장면이라 관객"
            " 반응이 나오는 지점이다. 프로필 변경 표시와 새 카드 등장은 따로 확인한다."
            " 변경은 반영됐는데 재계산이 안 되는 실패와, 카드는 바뀌었는데 사용자에게"
            " 왜 바뀌었는지 안 알려주는 실패가 서로 다른 문제다."
        ),
    ),
    ConversationCase(
        id="C4",
        setup=Setup(profile=profile()),
        turns=(msg("다음 학기에 휴학할 거예요"),),
        pass_criteria=(
            PROFILE_UNCHANGED,
            HAS_CONFIRM_QUESTION,
            *_ANSWER_BASELINE,
        ),
        note=(
            "T3 의 대화 쪽 확인이다. 해석은 맞게 하고 반영에서 틀리는 경우를 잡는다."
            " 프로필이 그대로인지를 기준으로 두는 이유는, 여기서 상태가 바뀌면 재학생"
            " 대상 제도가 화면에서 사라지기 때문이다. 사용자는 아직 재학생이다."
        ),
    ),
    ConversationCase(
        id="C5",
        setup=Setup(profile=profile()),
        turns=(msg("결과만 보여줘"),),
        pass_criteria=(
            NO_FOLLOWUP,
            HAS_SUMMARY,
            *_ANSWER_BASELINE,
        ),
        note=(
            "묻지 않는 것을 확인한다. 후속 질문은 우리 차별점이라 넣고 싶어지는 쪽으로"
            " 실패한다. 사용자가 그만 물으라고 했는데 한 번 더 묻는 것은 기능이 아니라"
            " 무시다. 대신 결과 정리는 나가야 하므로 요약 문장은 함께 본다."
        ),
    ),
    ConversationCase(
        id="C6",
        setup=Setup(profile=profile()),
        turns=(msg("전입신고 어떻게 해?"),),
        pass_criteria=(
            OUT_OF_SCOPE_ONE_LINE,
            HAS_CAPABILITY_HINT,
            NO_RECALCULATION,
            NO_FOLLOWUP,
        ),
        note=(
            "범위 밖을 짧게 끊는다. 아는 척해서 행정 절차를 설명하면 틀린 안내가 되고,"
            " 그 문장에는 붙일 각주도 없다. 재계산을 건너뛰는지까지 보는 이유는 시간"
            " 예산이다. 범위 밖 질문에 20초를 쓰면 다음 질문이 느려진다."
            " 할 수 있는 것 안내를 함께 요구해 막다른 응답이 되지 않게 한다."
        ),
    ),
    ConversationCase(
        id="C7",
        setup=Setup(profile=profile()),
        turns=(msg("창업 자금 지원되는 거 있어?"),),
        pass_criteria=(
            EMPTY_RESULT_NOTICE,
            HAS_WIDEN_HINT,
            HAS_DATA_SCOPE_LINE,
            NO_BANNED_PHRASE,
        ),
        note=(
            "데모 9단계와 같은 입력이다. 창업은 관심 분야 표에 없어 후보가 비는 것이"
            " 정상 동작이다. 결과가 없을 때 빈 화면을 내밀지 않고, 데이터 범위가 좁다는"
            " 것을 먼저 밝히고 넓히는 방법을 준다. 커버리지가 좁은 것은 인정한 약점이라"
            " 숨기면 오히려 신뢰를 잃는다. 없는 제도를 지어내지 않는지도 여기서 걸린다."
        ),
    ),
    ConversationCase(
        id="C8",
        setup=Setup(profile=profile()),
        turns=(msg(_DEMO_FIRST_MESSAGE), skip_all()),
        pass_criteria=(
            FOLLOWUP_STOPS,
            NO_REPEATED_QUESTION,
            CHECK_SUMMARY,
            HAS_CONDITIONAL_NOTE,
            HAS_FOOTNOTE,
            *_ANSWER_BASELINE,
        ),
        depends_on_policy_data=True,
        note=(
            "**C2 와 함께 가장 중요하다.** 전부 건너뛴 사용자에게 계속 묻는 것이 심문"
            " 그 자체다. 건너뛴 항목은 세션에 기록되고 다시 묻지 않는다. 물을 것이"
            " 없으면 묻지 않는 것이 정상 동작이고, 질문을 만들어 내는 쪽이 실패다."
            " 동시에 아무것도 몰라도 결과는 남아야 한다. 미확인 항목이 많으면 대부분"
            " check 로 떨어지는데, 그 상태를 정리해 주지 않으면 화면에 물음표만 남는다."
            " check 정리와 조건부 문장을 함께 요구하는 이유다."
        ),
    ),
]


# --------------------------------------------------------------------------
# 찾기와 상태 확인
# --------------------------------------------------------------------------


def get_interpretation(case_id: str) -> Optional[InterpretationCase]:
    """id 로 해석 테스트 케이스를 찾는다. 없으면 None."""
    for case in INTERPRETATION_CASES:
        if case.id == case_id:
            return case
    return None


def get_conversation(case_id: str) -> Optional[ConversationCase]:
    """id 로 대화 평가 케이스를 찾는다. 없으면 None."""
    for case in CONVERSATION_CASES:
        if case.id == case_id:
            return case
    return None


def policy_data_dependent_cases() -> List[ConversationCase]:
    """검수 정책 데이터가 확정돼야 판정할 수 있는 대화 케이스.

    ``ai/judgment/cases.py`` 의 ``is_placeholder`` 와 성격이 다르다. 그쪽은 케이스 자체가
    아직 실제 공고 문장이 아니라서 **미완성**이라는 표시다. 예외 조건 판정은 공고 원문
    문장이 입력이기 때문에 문장이 대체물이면 평가가 의미를 잃는다.

    해석 테스트에는 이런 표시를 두지 않았다. 입력이 사용자 문장이고 README 8장 표로
    이미 확정돼 있어 정책 데이터가 없어도 그대로 돌릴 수 있다. 채울 것이 남아 있지 않다.

    대화 평가는 중간이다. 대부분의 기준(길이, 금지 표현, 질문 반복, 프로필 반영)은 어떤
    데이터로도 확인되지만, "주거 분야 정책이 등장" 이나 "check 상태 정리" 처럼 결과에
    특정 분야·상태의 정책이 있어야 하는 기준은 데이터가 확정되기 전에는 통과·실패를
    단정할 수 없다. 미완성이 아니라 **판정을 보류해야 하는 항목**이라서 이름을
    ``placeholder`` 로 두지 않았다. 데이터가 붙기 전에 실패로 기록하면, 고칠 것이 없는데
    프롬프트를 고치게 된다.
    """
    return [case for case in CONVERSATION_CASES if case.depends_on_policy_data]


def all_criteria() -> List[str]:
    """대화 평가에 쓰인 통과 기준 전체. 중복 없이, 처음 나온 순서대로.

    채점기를 만들 때 확인 함수를 빠뜨리지 않았는지 대조하는 데 쓴다.
    """
    seen: List[str] = []
    for case in CONVERSATION_CASES:
        for criterion in case.pass_criteria:
            if criterion not in seen:
                seen.append(criterion)
    return seen
