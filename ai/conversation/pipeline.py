"""한 턴의 순서 (서버가 부를 유일한 입구).

**서버는 이 파일의 함수만 부르면 된다. 나머지는 내부다.**
``interpret``, ``answer``, ``followup``, ``related``, ``questions``, ``llm`` 의 공개 함수는
30개가 넘지만 서버가 한 턴에 실제로 쓰는 것은 아래 네 개뿐이다. 나머지는 이 파일이 부른다.

    interpret_turn        3~4단계 (해석·반영·조기 종료 판단)
    build_answer_prompt   10단계 준비 (뼈대 → 프롬프트)
    finish_turn           10~12단계 (정리·검증·후속 질문·칩)
    unknown_items_from    규칙 엔진 dict → followup.UnknownItem 경계 어댑터

이 파일이 하는 일은 **순서를 아는 것**이다. 판단은 각 모듈에 맡긴다. 여기에 허용 값 표,
금지 표현, 질문 문구, 칩 문구를 다시 적지 않는다. 한 규칙이 두 곳에 생기면 한쪽만 고쳐진다.

한 턴의 순서 (docs/03-api-contract.md 9장)
------------------------------------------
| 단계 | 하는 일 | 누가 | 이 파일의 무엇 |
| --- | --- | --- | --- |
| 1 | 민감정보 마스킹 | 서버 (12장) | — |
| 2 | 진행 단계 "정책 찾는 중" | 서버 | — |
| 3 | AI A 메시지 해석 + 프로필 반영 | 이 파일 | ``interpret_turn`` |
| 4 | 범위 밖·잡담이면 고정 응답 후 종료 | 이 파일이 판단, 서버가 종료 | ``InterpretedTurn.fixed_reply`` |
| 5 | 규칙 재계산, 후보 선정 | 규칙 엔진 (``rules/``) | ``InterpretedTurn.needs_recalculation`` 으로 필요 여부만 알림 |
| 6 | 진행 단계 "조건 확인 중" | 서버 | — |
| 7 | 예외 조건 판정 (후보별 동시 실행) | AI B (``ai/judgment``) | — |
| 8 | 인용 검증, 상태 확정 | AI B (``ai/citation``) | — |
| 9 | 진행 단계 "정리 중" | 서버 | — |
| 10 | AI A 답변 작성 | 프롬프트는 이 파일, 호출은 서버(``llm.Gateway``), 정리는 이 파일 | ``build_answer_prompt`` → ``finish_turn`` |
| 11 | 후속 질문 선택 | 이 파일 | ``finish_turn`` |
| 12 | 관련 질문 칩 (P1) | 이 파일 | ``finish_turn`` |
| 13 | 종료 (전체 20초 상한) | 서버 | — |

5단계와 7단계가 내놓는 미확인 항목 목록은 dict 목록이고(``rules/README.md`` 8장),
11단계의 ``followup`` 은 dataclass 를 요구한다. 그 사이를 ``unknown_items_from`` 이 잇는다.

``llm.Gateway`` 와 ``BudgetTracker`` 는 턴마다 새로 만든다
-------------------------------------------------------
둘은 **턴 단위 객체**다. ``BudgetTracker`` 는 생성 시점부터 20초를 세고(``llm`` 모듈
``TOTAL_BUDGET_S``), ``Gateway`` 는 그 예산과 지표를 들고 있다. 세션이나 프로세스 단위로
재사용하면 두 번째 턴이 시작부터 "예산 없음"이 되어 모델을 아예 부르지 않는다. 그 실패는
오류 없이 조용히 일어나고 화면에는 "AI 가 실패했다"는 모양으로만 보인다.

    gateway = llm.Gateway(adapter=my_adapter)   # 턴이 시작될 때 한 번
    ...                                        # 해석·판정·답변에서 같은 gateway 를 공유
    gateway.metrics_snapshot()                 # 턴이 끝나면 지표로 기록 (13장)

이 파일은 ``Gateway`` 를 만들지도, 부르지도 않는다. 모델 호출 자체는 서버가 스트리밍
이벤트를 흘리면서 해야 하고(``answer_delta``), 그 자리를 이 파일이 대신 가질 수 없다.

아직 없는 연결 (12:00 통합 전에 채워야 하는 것)
----------------------------------------------
1. **프롬프트 문안.** ``ANSWER_RULE_SLOTS`` 는 "무엇이 들어가야 하는지"만 적어 두고 문안
   자리에 ``PROMPT_TODO`` 를 남겼다. 완성형 지시문은 담당자가 채운다. 여기서 지어내면
   문안 검토 없이 데모에 들어간다.
2. **모델 어댑터.** ``llm.LlmAdapter`` 구현체가 아직 ``StubAdapter`` 뿐이다. 실제 모델
   출력(문자열 또는 dict)을 ``interpret_turn`` 에 그대로 넘기면 된다.
3. **항목 이름 표기.** ``ai/judgment`` 는 ``다른 지원 수혜 중`` 같은 한국어를, 이 패키지는
   영문 snake_case 를 쓴다(``fields.py`` 주의 참고). ``unknown_items_from`` 은 **매핑하지
   않고** 탈락 사실만 드러낸다. 표기를 어느 쪽으로 모을지는 12:00 에 팀이 정한다.
4. **패키지 자동 로딩.** ``ai/conversation/__init__.py`` 의 ``_MODULE_NAMES`` 에 이 모듈이
   없다. ``from ai.conversation import pipeline`` 로 직접 import 한다.

실패 방침
--------
**어떤 입력에도 예외를 던지지 않는다.** AI 가 전부 실패해도 5단계의 규칙 기반 카드는 이미
화면에 있고(9장 실패 처리), 이 파일에서 터지면 그 카드까지 500 에 묻힌다. 그래서 각 공개
함수는 마지막에 넓은 ``except`` 를 두고 "아무 일도 하지 않은 결과"를 돌려준다. 넓은 예외를
잡는 것이 좋은 습관이 아님을 알지만, 이 파일은 서버와 AI 사이의 경계라 여기서 막는 편이
호출하는 쪽마다 ``try`` 를 두는 것보다 안전하다. 호출한 쪽이 ``try`` 를 한 번 빼먹으면
데모 중 500 이 난다.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Set, Tuple

from . import answer, fields, followup, interpret, llm, questions, related

# ---------------------------------------------------------------------------
# 0. 단계 표 (docs/03-api-contract.md 9장)
#    독스트링의 표를 코드로도 둔다. 서버가 진행 단계 로그를 찍을 때 쓰라고 두는 것이며,
#    여기 값으로 분기하지 않는다. 분기가 생기면 표와 코드가 갈라진다.
# ---------------------------------------------------------------------------

TURN_STEPS: Tuple[Tuple[int, str, str], ...] = (
    (1, "민감정보 마스킹", "server"),
    (2, "진행 단계: 정책 찾는 중", "server"),
    (3, "메시지 해석과 프로필 반영", "pipeline.interpret_turn"),
    (4, "범위 밖·잡담 고정 응답 후 종료", "pipeline.interpret_turn"),
    (5, "규칙 재계산, 후보 선정", "rules"),
    (6, "진행 단계: 조건 확인 중", "server"),
    (7, "예외 조건 판정", "ai/judgment"),
    (8, "인용 검증, 상태 확정", "ai/citation"),
    (9, "진행 단계: 정리 중", "server"),
    (10, "답변 작성", "pipeline.build_answer_prompt + llm.Gateway + pipeline.finish_turn"),
    (11, "후속 질문 선택", "pipeline.finish_turn"),
    (12, "관련 질문 칩", "pipeline.finish_turn"),
    (13, "종료 (전체 20초 상한)", "server"),
)


# ---------------------------------------------------------------------------
# 1. 작은 도우미 (모두 예외를 던지지 않는다)
# ---------------------------------------------------------------------------


def _as_list(raw: Any) -> List[Any]:
    """무엇이 오든 목록으로. 문자열은 쪼개지 않고 원소 하나로 본다."""
    if raw is None:
        return []
    if isinstance(raw, Mapping):
        return [raw]
    if isinstance(raw, (str, bytes, int, float, bool)):
        return [raw]
    try:
        return list(raw)
    except TypeError:
        return []


def _as_text(raw: Any) -> str:
    """스칼라만 문자열로. 컨테이너는 빈 문자열."""
    if raw is None or isinstance(raw, (Mapping, list, tuple, set, frozenset)):
        return ""
    if isinstance(raw, bool):
        return ""
    return str(raw).strip()


def _pick(data: Mapping[str, Any], keys: Sequence[str]) -> Any:
    """여러 키 이름 중 먼저 값이 있는 것. 없으면 None."""
    for key in keys:
        if key in data and data[key] is not None:
            return data[key]
    return None


def _as_int(raw: Any) -> Optional[int]:
    """정수로 읽을 수 있으면 정수, 아니면 None. ``True`` 는 정수로 보지 않는다."""
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw) if float(raw).is_integer() else None
    text = _as_text(raw)
    if text.lstrip("-").isdigit():
        try:
            return int(text)
        except ValueError:
            return None
    return None


def _as_name_set(raw: Any) -> Set[str]:
    """항목 이름 집합. 빈 값과 비문자열은 버린다."""
    names: Set[str] = set()
    for entry in _as_list(raw):
        text = _as_text(entry)
        if text:
            names.add(text)
    return names


def _is_name_bag(raw: Any) -> bool:
    """문자열이 아닌 컨테이너인가. (asked, skipped) 쌍 판별에 쓴다."""
    return isinstance(raw, (list, tuple, set, frozenset))


def _as_asked_state(raw: Any) -> followup.AskedState:
    """물어본/건너뛴 상태를 ``followup.AskedState`` 로 맞춘다.

    세션이 이 상태를 어떤 모양으로 들고 있을지 아직 정해지지 않았다
    (``docs/03-api-contract.md`` 11장은 "물어본 항목과 건너뛴 항목"까지만 정한다).
    그래서 넓게 받는다. ``AskedState`` 그대로, ``{"asked": [...], "skipped": [...]}``,
    두 집합의 쌍, 이름 목록 하나까지.

    이름 목록 하나만 오면 **물어본 것**으로 본다. 건너뛴 것으로 오해하면 사용자가 답한
    항목을 다시 묻게 되고, 그건 화면에서 바로 보이는 실패다. 반대 방향 실수가 덜 나쁘다.
    """
    if isinstance(raw, followup.AskedState):
        return raw
    if raw is None:
        return followup.AskedState()
    if isinstance(raw, Mapping):
        return followup.AskedState(
            asked=_as_name_set(_pick(raw, ("asked", "asked_fields"))),
            skipped=_as_name_set(_pick(raw, ("skipped", "skipped_fields"))),
        )
    if (
        isinstance(raw, (list, tuple))
        and len(raw) == 2
        and _is_name_bag(raw[0])
        and _is_name_bag(raw[1])
    ):
        return followup.AskedState(asked=_as_name_set(raw[0]), skipped=_as_name_set(raw[1]))
    return followup.AskedState(asked=_as_name_set(raw))


def _as_intent(raw: Any) -> str:
    """의도 값. 모르는 값은 ``interpret.DEFAULT_INTENT`` 로 떨어진다.

    기본값 선택 근거는 ``interpret.DEFAULT_INTENT`` 독스트링에 있다. 여기서 다시 정하지 않는다.
    """
    text = _as_text(raw).lower()
    return text if text in fields.INTENTS else interpret.DEFAULT_INTENT


# ---------------------------------------------------------------------------
# 2. 경계 어댑터: 규칙 엔진 dict → followup.UnknownItem
# ---------------------------------------------------------------------------

# 받는 키 이름. 규칙 엔진과 AI B 가 같은 개념을 다른 이름으로 낼 수 있어 몇 가지를 받는다.
_FIELD_KEYS = ("field", "needed_field", "field_name", "name")
_POLICY_ID_KEYS = ("policy_id", "policyId", "id")
_RANK_KEYS = ("policy_rank", "rank", "display_rank", "order")
_TITLE_KEYS = ("policy_title", "title", "policyTitle")

#: ``policy_rank`` 가 없을 때 쓰는 값.
#: 0(맨 위)이 아니라 큰 수를 쓴다. ``followup`` 은 ``best_rank`` 가 작은 항목을 먼저 묻는데
#: (README 4장 4번), 없는 값을 0으로 채우면 순위를 모르는 항목이 1위 정책보다 먼저
#: 선택된다. 모르는 값이 우선순위를 **얻는** 방향은 틀린 안내로 이어지므로, 동점 처리에서
#: 뒤로 밀리는 쪽을 택한다.
DEFAULT_POLICY_RANK = 999

# 탈락 이유 코드. 지표 집계에 쓰므로 문자열을 바꾸지 않는다.
DROP_NOT_A_MAPPING = "not_a_mapping"
DROP_NO_FIELD = "missing_field"
DROP_NOT_IN_FIELD_TABLE = "not_in_field_table"
DROP_NO_QUESTION_TEMPLATE = "no_question_template"

# 버리지 않고 관용 처리한 기록.
NOTE_RANK_DEFAULTED = "policy_rank_defaulted"
NOTE_RANK_NOT_INT = "policy_rank_not_int"
NOTE_NO_POLICY_ID = "missing_policy_id"


@dataclass(frozen=True)
class DroppedUnknown:
    """변환에서 탈락한 미확인 항목 하나.

    index  원래 목록에서의 자리. 규칙 엔진 출력과 맞춰 보기 위해 남긴다
    field  읽어낸 항목 이름. 못 읽었으면 빈 문자열
    reason 이유 코드
    raw    원래 값을 문자열로 (지표·로그용)

    **이 기록이 이 어댑터의 요점이다.** ``followup.collect_candidates`` 는 물을 수 없는
    항목을 조용히 걸러낸다. 그 자체는 맞는 동작인데, 항목 이름 표기가 어긋났을 때도 똑같이
    조용하다. 통합 때 "왜 후속 질문이 안 뜨지"로 시간을 태우는 경로가 여기다. 그래서 탈락을
    반환값에 실어 보이게 한다.
    """

    index: int
    field: str
    reason: str
    raw: str = ""


@dataclass(frozen=True)
class UnknownItems:
    """변환 결과. 통과한 항목과 탈락 기록을 함께 담는다.

    ``followup`` 이 요구하는 것은 ``UnknownItem`` 목록이므로, 이 객체를 그대로 넘길 수 있게
    ``__iter__`` 를 둔다 (``followup.next_question(unknown_items_from(raw))``).
    """

    items: Tuple[followup.UnknownItem, ...] = ()
    dropped: Tuple[DroppedUnknown, ...] = ()
    notes: Tuple[str, ...] = ()

    def __iter__(self) -> Iterator[followup.UnknownItem]:
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def dropped_fields(self) -> Tuple[str, ...]:
        """탈락한 항목 이름. 빈 이름은 뺀다. 로그 한 줄로 찍기 위한 것이다."""
        seen: List[str] = []
        for entry in self.dropped:
            if entry.field and entry.field not in seen:
                seen.append(entry.field)
        return tuple(seen)

    def summary(self) -> Dict[str, Any]:
        """지표 요약 (docs/03-api-contract.md 13장). 사용자 화면에 쓰지 않는다."""
        reasons: Dict[str, int] = {}
        for entry in self.dropped:
            reasons[entry.reason] = reasons.get(entry.reason, 0) + 1
        return {
            "kept": len(self.items),
            "dropped": len(self.dropped),
            "dropped_reasons": reasons,
            "dropped_fields": list(self.dropped_fields()),
            "notes": list(self.notes),
        }


def unknown_items_from(raw: Any) -> UnknownItems:
    """규칙 엔진의 미확인 항목 dict 목록을 ``followup.UnknownItem`` 목록으로 바꾼다.

    근거: ``rules/README.md`` 8장 (미확인 항목 목록), ``ai/conversation/README.md`` 4장
    (후속 질문 선택), ``docs/01-glossary-profile.md`` 2~3장 (항목 이름).

    받는 모양
        [{"field": "income_bracket", "policy_id": "P1", "policy_rank": 0, "policy_title": "..."}]
        [{"needed_field": "housing_type", "policy_id": "P2", "rank": 1, "title": "..."}]
        dict 하나, None, 빈 목록, 이상한 타입 — 모두 견딘다

    관용 처리
        - ``policy_rank`` 가 없거나 정수로 읽히지 않으면 ``DEFAULT_POLICY_RANK`` 로 채운다.
          지금 ``UnknownItem`` 에 기본값이 없어 키 하나가 빠지면 ``TypeError`` 가 난다.
          서버가 즉석에서 dataclass 를 만들면 그 예외를 그대로 맞는다.
        - ``policy_id`` 가 없으면 빈 문자열로 둔다. ``collect_candidates`` 는 ``policy_id``
          집합으로 영향 정책 수를 세므로, 빈 문자열끼리는 하나로 합쳐져 **적게** 센다.
          모르는 값으로 개수를 부풀려 우선순위를 얻는 것보다 적게 세는 쪽이 안전하다.

    버리는 경우 (예외를 던지지 않고 ``dropped`` 에 남긴다)
        not_a_mapping         dict 가 아니다
        missing_field         항목 이름을 읽을 수 없다
        not_in_field_table    표에 없는 이름 (``fields.is_askable`` 이 거짓)
        no_question_template  표에는 있는데 질문 문구가 없다 (``questions`` 표 누락)

    **한국어 이름을 영문으로 매핑하지 않는다.** ``ai/judgment`` 는 ``다른 지원 수혜 중``
    같은 한국어 값을 쓰고 이 패키지는 영문 snake_case 를 쓴다(``fields.py`` 주의). 여기서
    임의로 이어 붙이면 표기 결정이 코드에 숨고, 12:00 에 팀이 다른 표기를 정했을 때 아무도
    이 매핑을 찾지 못한다. 그래서 ``not_in_field_table`` 로 **탈락 사실만** 드러낸다.
    ``followup`` 도 같은 항목을 걸러내므로 동작은 같고, 달라지는 것은 보이는지 여부다.
    """
    items: List[followup.UnknownItem] = []
    dropped: List[DroppedUnknown] = []
    notes: List[str] = []

    try:
        for index, entry in enumerate(_as_list(raw)):
            if isinstance(entry, followup.UnknownItem):
                # 이미 변환된 항목이 섞여 있어도 그대로 통과시킨다.
                items.append(entry)
                continue

            if not isinstance(entry, Mapping):
                dropped.append(
                    DroppedUnknown(index, "", DROP_NOT_A_MAPPING, type(entry).__name__)
                )
                continue

            name = _as_text(_pick(entry, _FIELD_KEYS))
            if not name:
                dropped.append(DroppedUnknown(index, "", DROP_NO_FIELD, ""))
                continue

            if name == fields.ASK_NOTICE or not fields.is_askable(name):
                dropped.append(DroppedUnknown(index, name, DROP_NOT_IN_FIELD_TABLE, name))
                continue

            # 질문 문구가 없으면 물을 수 없다. 즉석에서 문장을 만들지 않는다
            # (ai/conversation/README.md 5장, CONTRIBUTING.md 8장).
            if questions.get(name) is None:
                dropped.append(DroppedUnknown(index, name, DROP_NO_QUESTION_TEMPLATE, name))
                continue

            raw_rank = _pick(entry, _RANK_KEYS)
            rank = _as_int(raw_rank)
            if rank is None:
                rank = DEFAULT_POLICY_RANK
                notes.append(
                    f"{NOTE_RANK_NOT_INT if raw_rank is not None else NOTE_RANK_DEFAULTED}:{name}"
                )

            policy_id = _as_text(_pick(entry, _POLICY_ID_KEYS))
            if not policy_id:
                notes.append(f"{NOTE_NO_POLICY_ID}:{name}")

            items.append(
                followup.UnknownItem(
                    field=name,
                    policy_id=policy_id,
                    policy_rank=rank,
                    policy_title=_as_text(_pick(entry, _TITLE_KEYS)),
                )
            )
    except Exception:  # noqa: BLE001 - 이 어댑터가 턴 전체를 죽이지 않게
        notes.append("adapter_error")

    return UnknownItems(items=tuple(items), dropped=tuple(dropped), notes=tuple(notes))


def _coerce_unknown_items(raw: Any) -> UnknownItems:
    """이미 변환된 것과 dict 목록을 함께 받는다. 내부용."""
    if isinstance(raw, UnknownItems):
        return raw
    return unknown_items_from(raw)


# ---------------------------------------------------------------------------
# 3. 3~4단계: 해석·반영·조기 종료
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InterpretedTurn:
    """3~4단계 결과 (docs/03-api-contract.md 9장).

    interpretation       ``interpret.Interpretation`` 그대로
    merged               ``interpret.MergeResult`` 그대로
    profile_update       ``profile_update`` 이벤트 본문 (5장). 바뀐 게 없으면 None
    fixed_reply          고정 응답 문구. 있으면 **여기서 턴을 끝낸다** (9장 4단계)
    needs_recalculation  5단계 규칙 재계산이 필요한가

    반환 타입을 새로 만들지 않고 기존 결과를 그대로 담는 이유는, 서버가 ``applied``,
    ``held``, ``dropped`` 를 필요할 때 직접 읽어야 하기 때문이다. 중간에 새 dict 모양을
    끼우면 그때마다 이 파일을 고쳐야 한다.
    """

    interpretation: interpret.Interpretation
    merged: interpret.MergeResult
    profile_update: Optional[Dict[str, Any]] = None
    fixed_reply: Optional[str] = None
    needs_recalculation: bool = True

    @property
    def profile(self) -> Dict[str, Any]:
        """반영된 새 프로필. 세션에 이 값을 넣는다."""
        return self.merged.profile

    @property
    def intent(self) -> str:
        return self.interpretation.intent

    @property
    def stop_here(self) -> bool:
        """4단계에서 끝내는가. ``fixed_reply`` 를 흘리고 ``done`` 을 보낸다."""
        return self.fixed_reply is not None

    @property
    def needs_planned_confirmation(self) -> bool:
        """"바뀐 뒤 기준으로 볼까요?" 확인이 필요한가 (README 3장)."""
        return self.merged.needs_planned_confirmation

    def notices(self) -> Tuple[str, ...]:
        """화면에 붙일 고정 안내 문구. 표에서 온 값이다."""
        return self.merged.notices()


def interpret_turn(profile: Optional[Mapping[str, Any]], model_output: Any) -> InterpretedTurn:
    """모델 해석 출력을 검증해 프로필에 반영하고, 조기 종료 여부까지 정한다.

    근거: ``docs/03-api-contract.md`` 9장 3~4단계, ``ai/conversation/README.md`` 3장
    (해석 기준·의도별 동작), ``docs/05-interfaces.md`` 3장 (해석 결과 구조).

    ``model_output`` 은 모델이 준 값 그대로 넣는다. 문자열이면 ``interpret.parse_model_json``
    (앞뒤 설명이 붙어 와도 JSON 만 떼어 낸다), 그 밖이면 ``interpret.from_model_output``.
    어느 쪽을 고를지 서버가 판단하지 않게 여기서 나눈다.

    **조기 종료를 드러내는 것이 이 함수의 두 번째 일이다.** ``out_of_scope`` 와
    ``smalltalk`` 는 고정 응답만 내보내고 끝낸다(9장 4단계, README 3장 의도별 동작).
    지금 코드에는 이 경로가 없어서, 서버가 ``interpret.fixed_reply_for`` 를 부를 줄
    모르면 범위 밖 질문에도 전체 흐름이 돈다. 20초 예산을 답할 수 없는 질문에 쓰는 셈이다.

    고정 응답 경로에서도 ``profile_update`` 는 그대로 돌려준다. ``merge`` 가 이미 프로필을
    바꿔 놓았으므로, 이벤트를 숨기면 화면의 프로필 바와 세션 값이 어긋난다. "범위 밖"은
    답변을 줄이라는 뜻이고 프로필을 숨기라는 뜻이 아니다.
    """
    try:
        if isinstance(model_output, str):
            interpretation = interpret.parse_model_json(model_output)
        else:
            interpretation = interpret.from_model_output(model_output)

        merged = interpret.merge(profile, interpretation)
        behavior = interpretation.behavior

        return InterpretedTurn(
            interpretation=interpretation,
            merged=merged,
            profile_update=merged.to_profile_update(),
            fixed_reply=behavior.fixed_reply,
            needs_recalculation=behavior.recalculate,
        )
    except Exception:  # noqa: BLE001 - 해석 실패가 규칙 기반 카드를 지우지 않게
        # 해석을 포기하고 "변경 없음"으로 진행한다. 규칙 재계산은 그대로 한다.
        # 프로필이 그대로여도 후보 선정은 돌아야 카드가 나온다.
        empty = interpret.Interpretation()
        try:
            merged = interpret.merge(profile, empty)
        except Exception:  # noqa: BLE001 - 여기까지 오면 프로필 자체가 이상한 값이다
            merged = interpret.MergeResult(profile={})
        return InterpretedTurn(
            interpretation=empty,
            merged=merged,
            profile_update=None,
            fixed_reply=None,
            needs_recalculation=True,
        )


# ---------------------------------------------------------------------------
# 4. 10단계 준비: 뼈대 → 프롬프트
# ---------------------------------------------------------------------------

#: 문안을 채울 자리. **완성형 지시문을 이 파일에 쓰지 않는다.**
#: 문안은 답변 담당자가 채운다. 여기서 지어내면 검토 없이 데모에 들어가고, 프롬프트 문구가
#: 두 곳(문서와 코드)에 생겨 한쪽만 고쳐진다.
PROMPT_TODO = "{{여기에 문안을 채운다}}"

#: 금지 표현 이름 목록. ``answer.BANNED_PHRASES`` 에서 끌어온다. 목록을 다시 적지 않는다.
BANNED_PHRASE_LABELS: Tuple[str, ...] = tuple(
    phrase.label for phrase in answer.BANNED_PHRASES
)


@dataclass(frozen=True)
class RuleSlot:
    """규칙 블록에 들어갈 항목 하나.

    id      블록 안에서 이 항목을 가리키는 이름
    what    **무엇이 들어가야 하는지.** 지시문 자체가 아니다
    doc_ref 근거 문서와 장 번호
    """

    id: str
    what: str
    doc_ref: str


#: 답변 프롬프트 규칙 블록에 들어가야 하는 것 (ai/conversation/README.md 6장).
#: 각 항목의 문안은 ``PROMPT_TODO`` 자리에 담당자가 채운다.
ANSWER_RULE_SLOTS: Tuple[RuleSlot, ...] = (
    RuleSlot(
        id="banned_phrases",
        what=f"쓰지 말아야 할 표현 {len(BANNED_PHRASE_LABELS)}종: "
        + ", ".join(BANNED_PHRASE_LABELS),
        doc_ref="ai/conversation/README.md 6장 금지 표현",
    ),
    RuleSlot(
        id="footnote_rule",
        what="판정·조건·금액·날짜가 든 문장에는 각주를 붙이고, 주어진 번호만 쓰고, "
        "새 발췌를 만들지 않는다",
        doc_ref="ai/conversation/README.md 6장 근거 규칙, docs/03-api-contract.md 4-1",
    ),
    RuleSlot(
        id="length",
        what=f"요약부터 고정 문구까지 합쳐 {answer.MAX_ANSWER_LEN}자 이내, 해요체",
        doc_ref="ai/conversation/README.md 6장 말투와 길이",
    ),
    RuleSlot(
        id="no_date_math",
        what="남은 일수·마감일을 직접 세지 않고 주어진 배지 값을 인용한다",
        doc_ref="ai/conversation/README.md 2장 표, docs/03-api-contract.md 4-2",
    ),
)


def _rules_text() -> str:
    """규칙 블록 본문. 무엇이 들어갈 자리인지만 적고 문안은 비워 둔다."""
    lines: List[str] = []
    for slot in ANSWER_RULE_SLOTS:
        lines.append(f"[{slot.id}] 들어갈 내용: {slot.what} (근거: {slot.doc_ref})")
        lines.append(f"  {PROMPT_TODO}")
    return "\n".join(lines)


def _profile_text(profile: Optional[Mapping[str, Any]]) -> str:
    """프로필 블록 본문. 항목 순서를 고정한다.

    값을 화면 문구로 바꾸지 않고 **데이터 값 그대로** 넣는다. 프롬프트에 라벨을 쓸지 값을
    쓸지는 문안 담당자가 정할 일이고, 여기서 골라 두면 그 결정이 코드에 숨는다.
    순서만 고정하는 이유는 같은 프로필에 같은 프롬프트가 나와야 캐시가 걸리기 때문이다
    (``llm`` 모듈 ``assemble_prompt`` 참고).
    """
    data = dict(profile) if isinstance(profile, Mapping) else {}
    if not data:
        return ""

    order = getattr(interpret, "PROFILE_FIELD_ORDER", ())
    rank = {name: index for index, name in enumerate(order)}
    lines: List[str] = []
    for key in sorted(data, key=lambda k: (rank.get(k, len(rank)), str(k))):
        value = data[key]
        if isinstance(value, (list, tuple, set, frozenset)):
            shown = ", ".join(str(item) for item in value)
        else:
            shown = "" if value is None else str(value)
        lines.append(f"{key}: {shown}")
    return "\n".join(lines)


def _available_footnote_ids(policies: Any, footnotes: Any) -> Tuple[int, ...]:
    """쓸 수 있는 각주 번호.

    ``footnotes`` 가 있으면 ``answer.known_footnote_ids`` 에 맡긴다(그 함수가 이미 여러
    모양을 받는다). 없으면 정책 판정 결과의 조건에서 ``footnote_id`` 를 모은다
    (``docs/03-api-contract.md`` 4-1). 번호를 **여기서 부여하지 않는다.** 서버가 부여한
    값을 읽기만 한다(README 6장 근거 규칙).
    """
    known = answer.known_footnote_ids(footnotes)
    if known:
        return tuple(sorted(known))

    collected: Set[int] = set()
    for policy in _as_list(policies):
        if not isinstance(policy, Mapping):
            continue
        for condition in _as_list(policy.get("conditions")):
            if not isinstance(condition, Mapping):
                continue
            number = _as_int(condition.get("footnote_id"))
            if number is not None:
                collected.add(number)
    return tuple(sorted(collected))


def _output_rules_text(skeleton: Any, footnote_ids: Sequence[int]) -> str:
    """출력 규칙 블록 본문. **뼈대 줄 목록이 여기 들어간다.**

    ``Skeleton.outline()`` 은 "프롬프트에 그대로 넣는다"고 적혀 있는데 지금 그걸 넣는 코드가
    저장소에 없다. 그 연결을 여기서 만든다. 요약 문장과 마감 문장은 코드가 확정한 값이므로
    모델이 다시 쓰면 개수나 날짜가 어긋난다.
    """
    lines: List[str] = ["[answer_outline] 아래 줄 순서를 그대로 지킨다:"]

    outline = getattr(skeleton, "outline", None)
    rendered: Tuple[str, ...] = ()
    if callable(outline):
        try:
            rendered = tuple(str(line) for line in outline())
        except Exception:  # noqa: BLE001 - 뼈대가 이상해도 프롬프트는 만든다
            rendered = ()
    for position, line in enumerate(rendered, start=1):
        lines.append(f"  {position}. {line}")
    if not rendered:
        lines.append("  (뼈대 없음)")

    if footnote_ids:
        lines.append(
            "[footnote_ids] 쓸 수 있는 각주 번호: "
            + ", ".join(f"[{number}]" for number in footnote_ids)
        )
    else:
        lines.append("[footnote_ids] 쓸 수 있는 각주 번호 없음")

    lines.append(f"[output_format] {PROMPT_TODO}")
    return "\n".join(lines)


def build_answer_prompt(
    skeleton: Any,
    *,
    profile: Optional[Mapping[str, Any]] = None,
    policies: Any = (),
    footnotes: Any = None,
    source_text: str = "",
    system: str = "",
    cache_friendly: bool = False,
) -> llm.AssembledPrompt:
    """답변 작성 프롬프트를 조립한다 (10단계 준비).

    근거: ``docs/03-api-contract.md`` 9장 10단계, ``ai/conversation/README.md`` 6장,
    ``docs/research/04-llm-api-operations.md`` 5~6장 (블록 순서와 캐시).

    받는 것
        skeleton      ``answer.build_skeleton`` 결과. ``outline()`` 이 출력 규칙에 들어간다
        profile       사용자 프로필. 매번 바뀌는 값이라 뒤쪽 블록에 놓인다
        policies      정책 판정 결과 목록. 각주 번호를 모으는 데만 쓴다
        footnotes     각주 목록이 이미 있으면 그것을 우선 쓴다
        source_text   공고 원문 블록. 비우면 그 블록을 건너뛴다
        system        시스템 지시문. **동적으로 조립하지 않는다** (캐시 접두사가 깨진다)
        cache_friendly 캐싱을 쓸 때 True. 블록 순서가 바뀐다

    ``source_text`` 를 인자로 받고 여기서 만들지 않는 이유: 원문 발췌는 AI B 의 인용 검증
    결과에서 오고(4-3) 그 모양이 아직 확정되지 않았다. 지금 형식을 정해 두면 확정된 뒤
    두 곳을 고쳐야 한다.

    **문안을 완성하지 않는다.** 규칙 블록에는 무엇이 들어갈 자리인지와 근거만 있고 문안은
    ``PROMPT_TODO`` 다. 이 상태로도 조립·순서·뼈대 연결은 테스트할 수 있고, 문안이 채워지면
    ``ANSWER_RULE_SLOTS`` 한 곳만 고치면 된다.
    """
    try:
        sections = llm.PromptSections(
            rules=_rules_text(),
            source_text=source_text or "",
            profile=_profile_text(profile),
            output_rules=_output_rules_text(
                skeleton, _available_footnote_ids(policies, footnotes)
            ),
        )
        return llm.assemble_prompt(
            sections, system=system or "", cache_friendly=cache_friendly
        )
    except Exception:  # noqa: BLE001 - 프롬프트 조립 실패가 카드를 지우지 않게
        # 규칙 블록만이라도 살린다. 이 프롬프트로 부르면 답변 품질은 떨어지지만,
        # 답변 실패는 error 이벤트로 흡수되고 카드는 남는다 (9장 10단계).
        return llm.assemble_prompt(
            llm.PromptSections(rules=_rules_text(), source_text="", profile="", output_rules="")
        )


# ---------------------------------------------------------------------------
# 5. 10~12단계: 정리·검증·후속 질문·칩
# ---------------------------------------------------------------------------

#: 정리 후에도 남으면 답변을 내보내지 않는 문제 코드.
#:
#: 판단: **P0 위반이 남으면 답변을 버리고, 그 밖의 문제는 경고만 남기고 내보낸다.**
#:
#: 근거. 카드는 이미 화면에 있다(9장 5단계, 실패 처리). 그래서 두 손해를 비교하면 된다.
#:   - 금지 표현·각주 없는 판정 문장·없는 각주 번호가 남은 답변을 내보내는 손해:
#:     사용자가 근거 없는 단정을 근거 있는 것으로 읽는다. 완료 기준 네 줄 중 두 줄이
#:     깨진다(README 11장). 카드에 있는 정보와 답변이 어긋나는 최악의 경우다.
#:   - 답변을 버리는 손해: 설명이 안 나간다. 그런데 카드에 조건 판정표와 각주가 이미 있다.
#:     ``answer_failed`` 문구도 "카드에서 조건을 확인해 주세요"로 이미 준비돼 있다(10장).
#: 앞쪽이 더 크다. 그래서 P0 위반만 버린다.
#:
#: 길이 초과와 고정 문구 누락은 버리지 않는다. ``sanitize`` 가 이미 줄이고 붙인 뒤에도
#: 남았다는 것은 문장 하나가 통째로 600자를 넘는 드문 경우이고, 그건 읽기 불편할 뿐
#: 틀린 안내가 아니다. 불편함 때문에 설명을 없애지 않는다.
BLOCKING_PROBLEM_CODES = frozenset(
    {answer.BANNED, answer.MISSING_FOOTNOTE, answer.UNKNOWN_FOOTNOTE}
)

#: 정리 후 고정 문구만 남은 경우의 코드. ``answer`` 의 문제 코드가 아니라 이 파일이 붙인다.
#:
#: ``sanitize`` 는 고정 문구가 없으면 항상 붙인다(README 6장 5번). 그래서 모델이 쓴 문장이
#: 전부 제거되어도 결과는 "최종 신청 전 공식 공고에서 다시 확인하세요." 한 줄로 남고,
#: ``validate`` 는 그걸 통과시킨다. 문제가 없는 문장만 남았으니 검증 쪽은 맞다.
#:
#: 그 한 줄을 답변으로 내보내면 사용자는 설명을 요청했는데 주의 문구만 받는다. 실패를
#: 성공처럼 보여 주는 것이라 ``answer_failed`` 문구("설명을 불러오지 못했어요. 카드에서
#: 조건을 확인해 주세요", 10장)보다 나쁘다. 그래서 내용이 남지 않으면 실패로 본다.
EMPTY_AFTER_SANITIZE = "empty_after_sanitize"


def _has_content(text: str) -> bool:
    """고정 문구 말고 남은 문장이 있는가.

    문장 분리와 고정 문구 판별은 ``answer`` 에 맡긴다. 여기서 정규식을 다시 쓰면 고정 문구
    표기를 다듬었을 때 이 함수만 조용히 어긋난다.
    """
    for sentence in answer.split_sentences(text or ""):
        if not answer.has_closing_line(sentence):
            return True
    return False


@dataclass(frozen=True)
class FinishedTurn:
    """10~12단계 결과 (docs/03-api-contract.md 9장).

    answer_text    내보낼 답변 본문. ``answer_failed`` 면 빈 문자열
    check          ``answer.AnswerCheck`` 그대로 (지표용)
    removals       ``answer.Removal`` 목록. 무엇을 왜 뺐는지 (지표용)
    followup       ``followup`` 이벤트 본문 (6장). 물을 것이 없으면 None
    related        ``related`` 이벤트 본문 (5장). 칩이 없으면 빈 목록이 담긴다
    answer_failed  답변을 버렸는가. True 면 ``answer_failed`` 오류를 보내고 카드는 유지
    unknown_items  미확인 항목 변환 결과. 탈락 기록이 여기 있다
    metrics        지표 요약. **사용자 화면에 쓰지 않는다** (13장)

    ``followup`` 이 None 인 것은 정상 동작이다. 판정을 바꾸는 미확인 정보가 없으면 묻지
    않는다(FR05). ``related`` 가 빈 목록인 것도 정상이다(README 7장).
    """

    answer_text: str
    check: answer.AnswerCheck
    removals: Tuple[answer.Removal, ...] = ()
    followup: Optional[Dict[str, Any]] = None
    related: Dict[str, Any] = dataclass_field(default_factory=dict)
    answer_failed: bool = False
    unknown_items: UnknownItems = UnknownItems()
    metrics: Dict[str, Any] = dataclass_field(default_factory=dict)

    @property
    def blocking_codes(self) -> Tuple[str, ...]:
        """답변을 버리게 만든 코드. ``metrics`` 에 기록된 값을 그대로 읽는다."""
        section = self.metrics.get("answer") if isinstance(self.metrics, Mapping) else None
        if isinstance(section, Mapping):
            codes = section.get("blocking_codes")
            if isinstance(codes, (list, tuple)):
                return tuple(str(code) for code in codes)
        return ()


def finish_turn(
    answer_text: str,
    footnotes: Any = None,
    policies: Any = (),
    unknown_items: Any = (),
    *,
    asked_state: Any = None,
    intent: Any = fields.FIND_POLICY,
    shown_chips: Iterable[str] = (),
    allow_compare: bool = True,
) -> FinishedTurn:
    """모델이 쓴 답변을 정리해 내보낼 형태로 만들고, 후속 질문과 칩까지 고른다.

    근거: ``docs/03-api-contract.md`` 9장 10~12단계와 5~6장 (이벤트 본문),
    ``ai/conversation/README.md`` 6장 (답변 구성), 4장 (후속 질문), 7장 (칩).

    문서 순서를 그대로 따른다.
        1. ``answer.sanitize``      고칠 수 있는 것은 고치고, 못 고치는 문장은 뺀다
        2. ``answer.validate``      정리 결과를 다시 검사한다 (지표용, 그리고 마지막 관문)
        3. ``followup.next_question`` 11단계
        4. ``related.build``        12단계 (P1)

    2번을 정리 **후에** 한 번 더 부르는 이유는 두 가지다. 완료 기준 네 줄을 숫자로 기록해야
    하고(README 11장), ``sanitize`` 가 "결과는 ``validate`` 를 통과한다"고 약속한 계약이
    실제로 지켜지는지 확인해야 한다. 지켜지지 않으면 그건 정리 단계의 결함이고, 그때
    무엇을 할지는 ``BLOCKING_PROBLEM_CODES`` 주석에 적었다.

    ``unknown_items`` 는 규칙 엔진 dict 목록이어도 되고 ``UnknownItem`` 목록이어도 된다.
    dict 면 ``unknown_items_from`` 이 변환하고, **탈락 기록을 결과에 실어 보낸다.**
    후속 질문이 뜨지 않을 때 원인이 표기 불일치인지 아닌지 여기서 바로 보인다.
    """
    try:
        resolved_intent = _as_intent(intent)
        state = _as_asked_state(asked_state)
        items = _coerce_unknown_items(unknown_items)

        # 1. 정리
        cleaned = answer.sanitize(answer_text or "", footnotes)

        # 2. 검증 (정리 후 상태를 본다)
        check = answer.validate(cleaned.text, footnotes)
        blocking = [code for code in check.codes() if code in BLOCKING_PROBLEM_CODES]
        if not _has_content(cleaned.text):
            blocking.append(EMPTY_AFTER_SANITIZE)
        failed = bool(blocking)
        final_text = "" if failed else cleaned.text

        # 3. 후속 질문 (11단계)
        question: Optional[Dict[str, Any]] = None
        try:
            question = followup.next_question(items.items, state, resolved_intent)
        except Exception:  # noqa: BLE001 - 질문 하나가 답변을 끌어내리지 않게
            question = None

        # 4. 관련 질문 칩 (12단계, P1)
        try:
            chips = related.build(policies, shown=shown_chips, allow_compare=allow_compare)
        except Exception:  # noqa: BLE001 - 칩은 P1 이다
            chips = related.to_event(())

        return FinishedTurn(
            answer_text=final_text,
            check=check,
            removals=cleaned.removals,
            followup=question,
            related=chips,
            answer_failed=failed,
            unknown_items=items,
            metrics=_finish_metrics(
                cleaned=cleaned,
                check=check,
                blocking=blocking,
                question=question,
                chips=chips,
                items=items,
                intent=resolved_intent,
            ),
        )
    except Exception:  # noqa: BLE001 - 정리 실패가 카드를 지우지 않게
        # 답변을 포기하고 카드만 남긴다. 서버는 answer_failed 오류를 보낸다 (9장 10단계).
        return FinishedTurn(
            answer_text="",
            check=answer.AnswerCheck(ok=False),
            answer_failed=True,
            related=related.to_event(()),
            metrics={"error": "finish_turn_failed"},
        )


def _finish_metrics(
    *,
    cleaned: answer.SanitizeResult,
    check: answer.AnswerCheck,
    blocking: Sequence[str],
    question: Optional[Mapping[str, Any]],
    chips: Mapping[str, Any],
    items: UnknownItems,
    intent: str,
) -> Dict[str, Any]:
    """지표 요약 (docs/03-api-contract.md 13장). 사용자 화면에 쓰지 않는다.

    담는 것은 완료 기준 네 줄(금지 표현 0건, 각주 없는 판정 문장 0건, 600자 이내, 고정 문구)을
    숫자로 확인할 수 있는 값과, 제거·탈락 건수다. 이 모양은 이벤트 계약이 아니라 기록용이라
    여기서 정한다.
    """
    removal_codes: Dict[str, int] = {}
    for removal in cleaned.removals:
        removal_codes[removal.code] = removal_codes.get(removal.code, 0) + 1

    chip_ids: List[str] = []
    for chip in _as_list(chips.get("chips") if isinstance(chips, Mapping) else ()):
        if isinstance(chip, Mapping):
            chip_id = _as_text(chip.get("id"))
            if chip_id:
                chip_ids.append(chip_id)

    return {
        "intent": intent,
        "answer": {
            "original_length": cleaned.original_length,
            "final_length": cleaned.final_length,
            "closing_added": cleaned.closing_added,
            "truncated": cleaned.truncated,
            "removals": removal_codes,
            "validate_ok": check.ok,
            "validate_codes": list(check.codes()),
            "blocking_codes": list(blocking),
            "banned_hits": check.banned_hits,
            "evidence_sentences": check.evidence_sentences,
            "cited_sentences": check.cited_sentences,
            "unknown_footnotes": list(check.unknown_footnotes),
        },
        "followup": {
            "asked_field": _as_text(question.get("field")) if question else "",
            "asked": question is not None,
        },
        "related": {"chip_ids": chip_ids},
        "unknown_items": items.summary(),
    }


__all__ = [
    "TURN_STEPS",
    "DEFAULT_POLICY_RANK",
    "DROP_NOT_A_MAPPING",
    "DROP_NO_FIELD",
    "DROP_NOT_IN_FIELD_TABLE",
    "DROP_NO_QUESTION_TEMPLATE",
    "NOTE_RANK_DEFAULTED",
    "NOTE_RANK_NOT_INT",
    "NOTE_NO_POLICY_ID",
    "PROMPT_TODO",
    "BANNED_PHRASE_LABELS",
    "ANSWER_RULE_SLOTS",
    "BLOCKING_PROBLEM_CODES",
    "EMPTY_AFTER_SANITIZE",
    "DroppedUnknown",
    "UnknownItems",
    "InterpretedTurn",
    "FinishedTurn",
    "RuleSlot",
    "unknown_items_from",
    "interpret_turn",
    "build_answer_prompt",
    "finish_turn",
]
