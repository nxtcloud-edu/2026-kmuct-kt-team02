"""관련 질문 칩 (ai/conversation/README.md 7장, P1).

칩 문구는 **상수 표에서만 나온다. 모델이 만들지 않는다.** 이유는 질문 문구와 같다
(``questions.py``). 칩은 화면 문구이고, 화면 문구는 표에서 관리한다
(``CONTRIBUTING.md`` 8장). 매번 다른 문구가 나오면 리허설이 의미를 잃고, 무엇보다
모델이 만든 칩은 우리가 답할 수 없는 것을 물을 수 있다. "작년 경쟁률 알려줘" 같은 칩이
나오면 누른 사용자는 답을 못 받는다.

이 모듈이 하는 일은 **상황을 보고 어떤 칩을 내보낼지 고르는 것**뿐이다. 판단 재료는
정책 판정 결과 목록(``docs/03-api-contract.md`` 4장)이고, 결과는 ``related`` 이벤트에
그대로 실린다(같은 문서 5장).

빼는 순서 대비
--------------
관련 질문 칩은 P1 이고, 일정이 밀리면 **고정 문구 3개로 대체**한다
(``docs/00-overview.md`` 빼는 순서 2번). 그래서 상황을 보는 ``select`` 와 보지 않는
``fixed`` 를 같은 모듈에 나란히 둔다. 부르는 쪽은 함수 이름만 바꾸면 되고, 지울 코드가
없다. 대체 모드로 내려갈 때 파일을 지우거나 조건문을 손대게 만들면, 급할 때 손대야 하는
코드가 되어 버린다.

값 표기
-------
판정 상태와 조건 결과 값은 ``CONTRIBUTING.md`` 7-2 의 영문 값을 쓴다. ``ai/judgment`` 는
같은 개념을 한국어 값으로 두고 있어 12:00 통합 때 한쪽으로 모아야 한다
(``cases.py`` 독스트링에 같은 내용을 적어 뒀다).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Set, Tuple

from . import fields

# 한 번에 보여줄 칩 최대 개수 (README 7장).
MAX_CHIPS = 3

# 상황 판단에 쓰는 고정 값 (CONTRIBUTING.md 7-2).
# 이 모듈 안에서만 쓰는 사본이라 밑줄로 시작한다. 고정 값의 기준은 문서이고,
# 통합 후 공용 상수 모듈이 생기면 그것을 가져다 쓴다.
_STATUS_CHECK = "check"
_STATUS_UNLIKELY = "unlikely"
_RESULT_UNKNOWN = "unknown"

# 칩 번호. 세션이 "이미 보여준 칩" 을 이 번호로 기억한다.
CHIP_INCOME_HOWTO = "income_check_howto"
CHIP_UNLIKELY_REASON = "unlikely_reason"
CHIP_COMPARE_TOP2 = "compare_top2"
CHIP_DEADLINE_ORDER = "deadline_order"

CHIP_DOCUMENTS = "documents"
CHIP_DEADLINE_WHEN = "deadline_when"
CHIP_SIMILAR = "similar_policies"


@dataclass(frozen=True)
class RelatedChip:
    """칩 하나.

    id    세션이 중복을 걸러내는 데 쓰는 번호
    text  화면에 그대로 나가는 문구

    문구가 아니라 번호로 중복을 거르는 이유는, 문구를 다듬었을 때 이미 보여준 칩이
    새 칩으로 되살아나는 것을 막기 위해서다.
    """

    id: str
    text: str

    def to_event(self) -> Dict[str, str]:
        """``related`` 이벤트에 실을 형태."""
        return {"id": self.id, "text": self.text}


# 문구 표 (README 7장). 여기 없는 문구는 내보내지 않는다.
CHIPS: Dict[str, RelatedChip] = {
    CHIP_INCOME_HOWTO: RelatedChip(
        id=CHIP_INCOME_HOWTO,
        text="소득 기준 확인하는 방법 알려줘",
    ),
    CHIP_UNLIKELY_REASON: RelatedChip(
        id=CHIP_UNLIKELY_REASON,
        text="조건이 안 맞는 이유가 뭐야?",
    ),
    CHIP_COMPARE_TOP2: RelatedChip(
        id=CHIP_COMPARE_TOP2,
        text="상위 두 개 비교해줘",
    ),
    CHIP_DEADLINE_ORDER: RelatedChip(
        id=CHIP_DEADLINE_ORDER,
        text="마감 임박한 것부터 준비 순서 알려줘",
    ),
    # 아래 세 개는 상황을 보지 않는 대체 모드 전용이다 (fixed 참고).
    CHIP_DOCUMENTS: RelatedChip(
        id=CHIP_DOCUMENTS,
        text="신청할 때 필요한 서류 알려줘",
    ),
    CHIP_DEADLINE_WHEN: RelatedChip(
        id=CHIP_DEADLINE_WHEN,
        text="마감이 언제까지야?",
    ),
    CHIP_SIMILAR: RelatedChip(
        id=CHIP_SIMILAR,
        text="비슷한 다른 지원도 있어?",
    ),
}


# 네 가지 상황이 한꺼번에 맞으면 칩이 4개가 되는데 최대는 3개다. 남길 순서를 고정한다.
#
# 1 마감 임박: 되돌릴 수 없는 손실이다. 다른 칩은 나중에 물어도 답이 같지만, 마감은
#   지나면 답이 사라진다. 설문에서 신청 미완료 이유 1위가 마감(60.0%)이었다.
# 2 소득 기준 확인: 누르면 **결과가 실제로 바뀐다.** check 가 likely 나 unlikely 로
#   확정되는 쪽으로 이어지는 유일한 칩이다.
# 3 미충족 이유: 납득에는 필요하지만 판정은 바뀌지 않는다. 그리고 unlikely 는 접힌
#   영역에 있어 사용자가 아직 보지 않았을 수도 있다.
# 4 비교: P1 기능이고 빼는 순서 1번이다. 가장 먼저 사라질 기능을 가리키는 칩을 남겨
#   두면, 기능이 빠졌을 때 답할 수 없는 칩이 화면에 남는다.
CHIP_PRIORITY: Tuple[str, ...] = (
    CHIP_DEADLINE_ORDER,
    CHIP_INCOME_HOWTO,
    CHIP_UNLIKELY_REASON,
    CHIP_COMPARE_TOP2,
)

# 대체 모드에서 쓰는 고정 3개.
#
# 상황을 보지 않으므로 **카드 내용과 어긋날 수 없는 문구만** 골랐다. 서류·마감·비슷한
# 제도는 카드에 이미 들어 있는 정보라(``documents``, ``deadline``, 같은 분야 후보)
# 어떤 결과가 나와도 답할 수 있다.
#
# 반대로 상황 판단이 필요한 문구는 쓸 수 없다. "조건이 안 맞는 이유가 뭐야?" 를 unlikely
# 가 하나도 없을 때 띄우면 안 맞는 조건이 있다는 뜻으로 읽힌다. 대체 모드는 판단을
# 포기한 모드이므로, 문구도 판단을 요구하지 않는 것으로 바꾸는 것이 맞다.
FIXED_CHIP_IDS: Tuple[str, ...] = (
    CHIP_DOCUMENTS,
    CHIP_DEADLINE_WHEN,
    CHIP_SIMILAR,
)


# --------------------------------------------------------------------------
# 상황 판단
#
# 정책 판정 결과는 서버가 만든 dict 다(docs/03-api-contract.md 4장). 키가 빠져 있어도
# 예외를 던지지 않는다. 칩은 P1 이고, 칩 하나 때문에 답변 전체가 실패하면 안 된다.
# 판단할 수 없으면 그 칩을 내보내지 않는 쪽으로 기운다.
# --------------------------------------------------------------------------


def _as_policies(policies: Any) -> List[Mapping[str, Any]]:
    """정책 목록을 안전하게 만든다.

    독스트링이 "키가 빠져 있어도 예외를 던지지 않는다"고 약속한 범위를 실제로 지키려면
    키뿐 아니라 **컨테이너 모양**도 봐야 한다. 전에는 `select(None)` 과 `select([None])`
    에서 터졌다. 칩은 P1 이고, 칩 하나 때문에 답변 전체가 실패하면 안 된다.
    """
    if policies is None or isinstance(policies, (str, bytes, Mapping)):
        return []
    try:
        items = list(policies)
    except TypeError:
        return []
    return [item for item in items if isinstance(item, Mapping)]


def _conditions(policy: Mapping[str, Any]) -> List[Mapping[str, Any]]:
    """정책의 조건 목록. 없거나 모양이 다르면 빈 목록."""
    raw = policy.get("conditions")
    if not isinstance(raw, (list, tuple)):
        return []
    return [item for item in raw if isinstance(item, Mapping)]


def _has_status(policies: Sequence[Mapping[str, Any]], status: str) -> bool:
    return any(policy.get("status") == status for policy in _as_policies(policies))


def income_is_the_blocker(policies: Sequence[Mapping[str, Any]]) -> bool:
    """확인이 필요한 이유가 **소득**인 정책이 있는지.

    ``check`` 가 있다는 것만으로 소득 칩을 띄우지 않는다. ``needed_field`` 를 본다.

    이유: ``check`` 의 원인은 소득만이 아니다. 거주 기간, 고용보험 이력, 성적일 수도 있다.
    고용보험 때문에 확인이 필요한 사람에게 "소득 기준 확인하는 방법 알려줘" 를 띄우면,
    사용자는 소득이 문제라고 이해하고 엉뚱한 서류를 찾는다. 칩은 제안이 아니라 안내로
    읽히기 때문에 틀린 칩은 틀린 답변과 비슷한 비용을 낸다.

    ``needed_field`` 는 이미 계약에 있는 값이라(4-1) 따로 얻어 올 것이 없다. 다만 키가
    없거나 비어 있으면 원인을 모르는 것이므로 **띄우지 않는다.** 칩이 하나 줄어드는 손해가
    잘못된 방향을 가리키는 손해보다 작다.
    """
    for policy in _as_policies(policies):
        if policy.get("status") != _STATUS_CHECK:
            continue
        for condition in _conditions(policy):
            if condition.get("result") != _RESULT_UNKNOWN:
                continue
            if condition.get("needed_field") == fields.INCOME_BRACKET:
                return True
    return False


def _has_imminent_deadline(policies: Sequence[Mapping[str, Any]]) -> bool:
    """마감 임박(7일 이내) 정책이 있는지.

    ``is_imminent`` 는 코드가 계산한 값이다(4-2). 여기서 날짜를 다시 계산하지 않는다.
    AI 쪽 코드가 D-day 를 계산하기 시작하면 화면과 답변의 남은 일수가 어긋난다.
    """
    for policy in _as_policies(policies):
        deadline = policy.get("deadline")
        if isinstance(deadline, Mapping) and deadline.get("is_imminent") is True:
            return True
    return False


# --------------------------------------------------------------------------
# 고르기
# --------------------------------------------------------------------------


def candidate_ids(
    policies: Sequence[Mapping[str, Any]],
    allow_compare: bool = True,
) -> List[str]:
    """상황에 맞는 칩 번호. 우선순위 순서로, 개수 제한 전 상태.

    ``allow_compare`` 는 정책 비교(P1)가 빠졌을 때 False 로 둔다. 비교 기능 없이 비교
    칩을 띄우면 누른 사용자가 답을 못 받는다. 조건문을 고치지 않고 인자로 끌 수 있게 했다.
    """
    policies = _as_policies(policies)
    matched: Set[str] = set()

    if _has_imminent_deadline(policies):
        matched.add(CHIP_DEADLINE_ORDER)
    if income_is_the_blocker(policies):
        matched.add(CHIP_INCOME_HOWTO)
    if _has_status(policies, _STATUS_UNLIKELY):
        matched.add(CHIP_UNLIKELY_REASON)
    if allow_compare and len(policies) >= 2:  # _as_policies 로 걸러진 목록이다
        matched.add(CHIP_COMPARE_TOP2)

    return [chip_id for chip_id in CHIP_PRIORITY if chip_id in matched]


def select(
    policies: Sequence[Mapping[str, Any]],
    shown: Iterable[str] = (),
    allow_compare: bool = True,
) -> List[RelatedChip]:
    """보여줄 칩을 고른다. 최대 3개, 없으면 빈 목록.

    ``shown`` 에는 **이미 보여준 칩과 사용자가 누른 칩 번호**를 함께 넣는다. 둘을 구분하지
    않는 이유는 처리가 같기 때문이다. 이미 본 칩을 다시 띄우면 대화가 앞으로 가지 않는
    느낌을 주고, 누른 칩을 다시 띄우면 방금 받은 답을 또 물으라고 권하는 셈이 된다.

    빈 목록이 정상 동작이다. 상황에 맞는 칩이 없으면 칩을 띄우지 않는다.
    """
    seen = set(shown)
    chosen = [
        CHIPS[chip_id]
        for chip_id in candidate_ids(policies, allow_compare=allow_compare)
        if chip_id not in seen and chip_id in CHIPS
    ]
    return chosen[:MAX_CHIPS]


def fixed(shown: Iterable[str] = ()) -> List[RelatedChip]:
    """상황을 보지 않는 고정 칩 (빼는 순서 2번 대체안).

    정책 목록을 받지 않는다. 그것이 이 함수의 요점이다. 일정이 밀렸을 때 줄이는 것은
    문구가 아니라 **판단**이다.

    ``shown`` 은 선택이다. 넣으면 이미 보여준 것을 걸러내므로 결과가 3개보다 적을 수
    있다. 대체 모드라도 같은 칩을 매 턴 다시 내미는 것보다는 적게 내미는 게 낫다.
    """
    seen = set(shown)
    return [CHIPS[chip_id] for chip_id in FIXED_CHIP_IDS if chip_id not in seen]


# --------------------------------------------------------------------------
# 이벤트 본문
# --------------------------------------------------------------------------


def to_event(chips: Sequence[RelatedChip]) -> Dict[str, object]:
    """``related`` 이벤트 본문 (docs/03-api-contract.md 5장).

    프론트는 ``text`` 만 화면에 쓰고 ``id`` 는 다시 보내 줄 값이다. 그 번호로 "누른 칩"
    을 세션에 기록하고, 다음 턴의 ``shown`` 으로 돌아온다.
    """
    return {"chips": [chip.to_event() for chip in chips[:MAX_CHIPS]]}


def build(
    policies: Sequence[Mapping[str, Any]],
    shown: Iterable[str] = (),
    allow_compare: bool = True,
) -> Dict[str, object]:
    """상황에 맞는 칩을 골라 ``related`` 이벤트 본문으로 만든다."""
    return to_event(select(policies, shown=shown, allow_compare=allow_compare))


def build_fixed(shown: Iterable[str] = ()) -> Dict[str, object]:
    """고정 칩을 ``related`` 이벤트 본문으로 만든다 (대체 모드)."""
    return to_event(fixed(shown=shown))
