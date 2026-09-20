"""AI A dict ↔ 서버 pydantic 모델 사이의 **유일한 변환 지점**.

서버는 이 파일의 함수 결과를 그대로 모델에 넣으면 된다.

    ProfileUpdateEventData(**handoff.profile_update_event(merged))
    RelatedEventData(**handoff.related_event(chips))        # None 이면 이벤트를 보내지 않는다
    FollowupQuestion(**handoff.followup_event(question))    # None 이면 보내지 않는다
    AnswerDeltaEventData(delta=piece) for piece in handoff.delta_lines(answer_text)
    known_footnote_ids(handoff.as_plain(footnotes_event))   # 모델 객체를 dict 로 먼저 바꾼다

근거: ``docs/10-ai-a-server-handoff.md`` 전체(2장 쌍별 대조표, 3장 답변 실패, 4장 서버에
요청하는 변경, 5장 AI A 가 지키는 것, 6장 통합 체크리스트 3·4·7·9번),
``docs/03-api-contract.md`` 5장(이벤트 본문)·10장(오류 코드와 문구)·11장(세션 기록).

왜 이 파일이 필요한가
---------------------
서버 이벤트 payload 모델은 전부 ``extra="forbid"`` 이고(``server/schemas.py`` 의
``ContractModel``), 검증은 **200 헤더가 나간 뒤**에 일어난다. 그래서 키 이름이 하나만
달라도 4xx 가 아니라 **이유 없이 끊긴 스트림**이 된다. ``done`` 이 오지 않으므로 프론트는
입력창을 다시 열지 못한다. 변환을 호출하는 쪽마다 손으로 적으면 그 실수가 여러 곳에 생기고,
어느 쪽이 맞는 모양인지 아무도 모르게 된다. 그래서 한 곳에 모은다.

왜 ``server`` 를 import 하지 않는가
-----------------------------------
**이 파일은 ``server`` 도 ``pydantic`` 도 import 하지 않는다.** dict 만 내고, 모델로 만드는
일은 서버가 한다. 의존 방향이 ai→server 로 생기면 AI 파트 테스트가 fastapi 설치를 요구하고
(지금 이 환경에 fastapi 가 없다), ``interpret.py`` 가 선언한 "LLM 도 프레임워크도 없이
로직으로만 검증한다"가 깨진다 (``docs/10-ai-a-server-handoff.md`` 5장, 4장 5번 "대안: 없다").
같은 이유로 서버 enum 값을 여기 베껴 두지 않는다. 항목 이름은 ``fields``, 칩 상한은
``related.MAX_CHIPS``, planned 항목 이름은 ``questions.PLANNED_BASIS_FIELD`` 에서 끌어온다.

실패 방침
--------
**어떤 입력에도 예외를 던지지 않는다.** ``pipeline.py`` 와 같은 방침이다. 이 파일은 서버와
AI 사이의 경계이고, 여기서 터지면 이미 화면에 있는 규칙 기반 카드까지 500 에 묻힌다
(``docs/03-api-contract.md`` 9장 실패 처리). 그래서 각 공개 함수는 넓은 ``except`` 로 막고
"안전한 빈 결과"(``None`` 또는 빈 튜플)를 돌려준다. 넓은 예외를 잡는 것이 좋은 습관이 아님을
알지만, 호출하는 쪽마다 ``try`` 를 두는 것보다 안전하다. 한 번 빼먹으면 데모 중 스트림이
끊긴다.

버리는 것을 숨기지 않는다
-------------------------
변환에서 사라지는 정보가 있다. ``before``·``label``·항목별 ``notice``(서버 모델에 자리가
없다), ``region``(``ProfileField`` enum 에 없다), 칩 ``id``(``RelatedEventData`` 에 자리가
없다), planned 확인 질문(``AskableProfileField`` enum 에 없다). 전부 ``HandoffNotes`` 에
기록으로 남고 ``summary()`` 로 센다. **지표용이고 사용자 화면에 쓰지 않는다**
(``docs/03-api-contract.md`` 13장).

기록을 어디에 두는가 (선택과 이유)
----------------------------------
``pipeline.UnknownItems`` 처럼 ``(payload, notes)`` 를 한 객체에 담는 방식을 골랐다. 다만
서버가 ``ProfileUpdateEventData(**payload)`` 로 바로 쓸 수 있어야 하므로, payload 는 dict
그대로 두고 기록은 **키가 아니라 속성**으로 붙인다(``Payload.notes``). 기록을 키로 넣으면
``extra="forbid"`` 에 걸려 이 파일이 막으려는 바로 그 ValidationError 를 이 파일이 만든다.

모듈 수준에 기록을 모으는 방식은 버렸다. 서버는 async 이고 턴이 겹쳐 돌아가므로 모듈 수준
누적은 다른 턴의 기록과 섞인다. 기록이 **없는** 것보다 **틀린** 것이 나쁘다.

남는 한계: ``None`` 을 돌려주는 경로(보낼 이벤트가 없는 경우)는 기록을 실어 보낼 자리가
없다. 서버가 ``is not None`` 으로만 판단하므로(``server/api/chat.py``) 빈 payload 를 대신
줄 수 없기 때문이다. 그 경로에서 잃는 것은 "아무것도 안 보냈다"는 사실뿐이고 그것은 서버가
이미 안다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Tuple

from . import answer, fields, questions, related

# ---------------------------------------------------------------------------
# 0. 기록 코드
#    지표 집계에 쓰므로 문자열을 바꾸지 않는다 (pipeline.py 의 DROP_* 와 같은 규칙).
# ---------------------------------------------------------------------------

# 버린 것 (그 값이 와이어에 나가지 못한다)
DROP_REGION_NOT_IN_ENUM = "region_not_in_profile_field_enum"
DROP_CHANGE_NOT_A_MAPPING = "change_not_a_mapping"
DROP_CHANGE_NO_FIELD = "change_missing_field"
DROP_CHIP_NOT_READABLE = "chip_not_readable"
DROP_CHIP_NO_TEXT = "chip_missing_text"
DROP_CHIP_OVER_LIMIT = "chip_over_limit"
DROP_PLANNED_BASIS = "planned_basis_not_in_askable_enum"
DROP_FOLLOWUP_NOT_A_MAPPING = "followup_not_a_mapping"
DROP_PROFILE_NOT_A_MAPPING = "profile_not_a_mapping"

# 버리지 않고 관용 처리하거나, 자리가 없어 접은 기록
NOTE_BEFORE_NO_SERVER_FIELD = "before_no_server_field"
NOTE_LABEL_NO_SERVER_FIELD = "label_no_server_field"
NOTE_ITEM_NOTICE_NO_SERVER_FIELD = "item_notice_no_server_field"
NOTE_CHIP_ID_NOT_ON_WIRE = "chip_id_not_on_wire"
NOTE_DUPLICATE_FIELD = "duplicate_field_last_wins"

#: ``changed_fields`` 에서 빼는 항목 이름.
#:
#: 판단: **``region`` 은 빼고 기록만 남긴다.** ``ProfileField`` enum 에 ``region`` 이 없어
#: 그대로 넣으면 스트림 중간 ValidationError 다. 서울 거주는 제품 전제이고
#: (``docs/03-api-contract.md`` 2장: 요청에서 받지 않고 서버가 ``seoul`` 로 고정한다),
#: ``docs/10-ai-a-server-handoff.md`` 5장이 AI A 가 버리는 쪽으로 이미 정했다.
#: 이름을 여기 적지 않고 ``fields.REGION`` 에서 끌어온다.
EXCLUDED_PROFILE_FIELDS = frozenset({fields.REGION})

#: ``related`` 이벤트에 담을 문구 최대 개수. ``related.MAX_CHIPS`` 에서 끌어온다.
#: 서버 쪽 상한(``Field(max_length=3)``)과 같은 값이지만 숫자를 여기 다시 적지 않는다.
MAX_RELATED_QUESTIONS = related.MAX_CHIPS

#: 답변 실패 오류 코드와 문구.
#:
#: **여기서 지어낸 값이 아니다.** 코드는 ``docs/03-api-contract.md`` 10장 오류 표의
#: ``answer_failed`` 이고(서버 ``errors.ErrorCode.ANSWER_FAILED`` 와 같은 문자열), 문구는
#: 같은 표의 "답변 실패" 줄을 그대로 옮긴 것이다(서버 ``errors._DEFAULT_MESSAGES`` 와 같은
#: 문장, ``frontend/README.md`` 실패 화면 표에도 같은 문장이 있다).
#:
#: 서버 상수를 import 하지 않는 이유는 모듈 독스트링과 같다(``server`` 의존 금지).
#: 그래서 이 두 값은 **사본**이다. 문구를 다듬을 때 세 곳을 함께 고쳐야 한다는 뜻이고,
#: 그 위험은 ``docs/09-value-naming-decision.md`` 6장이 경고한 것과 같다. 서버가 4장 1번의
#: ``error`` 이벤트를 추가하면 이 사본은 지우고 서버 상수만 남기는 것이 맞다.
ANSWER_FAILED_CODE = "answer_failed"
ANSWER_FAILED_MESSAGE = "설명을 불러오지 못했어요. 카드에서 조건을 확인해 주세요"

#: ``as_plain`` 재귀 깊이 상한. 자기 자신을 담은 목록 같은 값에서 RecursionError 를 막는다.
_MAX_PLAIN_DEPTH = 20


# ---------------------------------------------------------------------------
# 1. 기록 (pipeline.UnknownItems / DroppedUnknown 패턴)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DroppedValue:
    """변환에서 와이어에 오르지 못한 값 하나.

    field  읽어낸 이름(프로필 항목 이름, 칩 번호 등). 못 읽었으면 빈 문자열
    reason 이유 코드
    raw    원래 값을 문자열로 (지표·로그용, 화면에 쓰지 않는다)
    """

    field: str
    reason: str
    raw: str = ""


@dataclass(frozen=True)
class HandoffNotes:
    """변환 한 번의 기록. **지표용이고 사용자 화면에 쓰지 않는다** (13장).

    dropped 와이어에 오르지 못한 값
    notes   버리지 않고 접거나 관용 처리한 기록. ``"{코드}:{이름}"`` 꼴
    """

    dropped: Tuple[DroppedValue, ...] = ()
    notes: Tuple[str, ...] = ()

    def dropped_fields(self) -> Tuple[str, ...]:
        """버린 값의 이름. 빈 이름과 중복은 뺀다. 로그 한 줄로 찍기 위한 것이다."""
        seen: List[str] = []
        for entry in self.dropped:
            if entry.field and entry.field not in seen:
                seen.append(entry.field)
        return tuple(seen)

    def summary(self) -> Dict[str, Any]:
        """지표 요약. ``pipeline.UnknownItems.summary`` 와 같은 모양으로 둔다."""
        reasons: Dict[str, int] = {}
        for entry in self.dropped:
            reasons[entry.reason] = reasons.get(entry.reason, 0) + 1

        note_reasons: Dict[str, int] = {}
        for note in self.notes:
            code = note.split(":", 1)[0]
            note_reasons[code] = note_reasons.get(code, 0) + 1

        return {
            "dropped": len(self.dropped),
            "dropped_reasons": reasons,
            "dropped_fields": list(self.dropped_fields()),
            "notes": list(self.notes),
            "note_reasons": note_reasons,
        }


class _NoteBook:
    """기록을 모으는 내부 도구. 공개 API 는 얼린 ``HandoffNotes`` 만 내보낸다."""

    def __init__(self) -> None:
        self._dropped: List[DroppedValue] = []
        self._notes: List[str] = []

    def drop(self, field: Any, reason: str, raw: Any = "") -> None:
        self._dropped.append(DroppedValue(_as_text(field), reason, _as_text(raw)))

    def note(self, code: str, name: Any = "") -> None:
        text = _as_text(name)
        self._notes.append(f"{code}:{text}" if text else code)

    def frozen(self) -> HandoffNotes:
        return HandoffNotes(dropped=tuple(self._dropped), notes=tuple(self._notes))


class Payload(dict):
    """서버 모델에 그대로 넣을 dict + 기록.

    ``ProfileUpdateEventData(**payload)`` 가 되어야 하므로 dict 를 그대로 쓴다. 기록은
    ``payload.notes`` 속성으로 붙는다. 키로 넣으면 ``extra="forbid"`` 에 걸린다
    (모듈 독스트링 "기록을 어디에 두는가").
    """

    #: dict 를 복사해도 속성이 따라오지 않으므로 기본값을 클래스에 둔다.
    notes: HandoffNotes = HandoffNotes()

    def __init__(self, data: Mapping[str, Any], notes: HandoffNotes) -> None:
        super().__init__(data)
        self.notes = notes


# ---------------------------------------------------------------------------
# 2. 작은 도우미 (모두 예외를 던지지 않는다)
# ---------------------------------------------------------------------------


def _as_text(raw: Any) -> str:
    """스칼라만 문자열로. 컨테이너와 불리언은 빈 문자열 (pipeline._as_text 와 같은 규칙)."""
    if raw is None or isinstance(raw, (Mapping, list, tuple, set, frozenset)):
        return ""
    if isinstance(raw, bool):
        return ""
    try:
        return str(raw).strip()
    except Exception:  # noqa: BLE001 - __str__ 이 터지는 객체도 온다
        return ""


def _as_rows(raw: Any) -> List[Any]:
    """무엇이 오든 목록으로. 문자열과 dict 는 원소 하나로 본다."""
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


def _unwrap(value: Any, method: str) -> Any:
    """``to_profile_update`` 처럼 결과를 내는 메서드가 있으면 불러 준다.

    서버가 ``MergeResult`` 를 그대로 넘겨도, 이미 ``to_profile_update()`` 한 dict 를
    넘겨도 같게 동작해야 한다. 어느 쪽인지 서버가 판단하지 않게 여기서 나눈다.
    """
    caller = getattr(value, method, None)
    if callable(caller):
        try:
            return caller()
        except Exception:  # noqa: BLE001 - 남의 객체가 터져도 변환은 계속한다
            return None
    return value


# ---------------------------------------------------------------------------
# 3. as_plain: 정책·각주를 dict 로 강제한다
# ---------------------------------------------------------------------------


def as_plain(value: Any, _depth: int = 0) -> Any:
    """pydantic 모델 객체를 dict 로 바꾼다. 목록·중첩도 재귀로 처리한다.

    근거: ``docs/10-ai-a-server-handoff.md`` 2-4·2-5, 4장 5번("결정 없이 구현만 하면 된다.
    가장 싸고 가장 위험하다"), 6장 체크리스트 2번.

    **이 함수가 없으면 답변이 통째로, 예외도 로그도 없이 사라진다.**

    pydantic v2 ``BaseModel`` 은 ``__iter__`` 를 가진다. 그래서 ``Footnote`` 객체나
    ``FootnotesEventData`` 객체를 ``answer.known_footnote_ids`` 에 넘기면 ``Mapping`` 갈래로
    들어가지 못하고 Iterable 로 읽혀 ``frozenset()`` 이 나온다. 쓸 수 있는 각주 번호가
    하나도 없으므로 각주가 붙은 문장이 전부 ``unknown_footnote`` 로 잡혀 ``answer.sanitize``
    가 삭제하고, 남은 고정 문구 한 줄을 ``pipeline.finish_turn`` 이 ``EMPTY_AFTER_SANITIZE``
    로 잡아 ``answer_failed=True`` 로 바꾼다. **예외도 로그도 없다.**

    같은 이유로 두 가지가 더 조용히 사라진다. ``answer._deadline_line`` 과
    ``answer.build_skeleton`` 은 ``isinstance(policy, Mapping)`` 검사를 통과한 원소만 보므로
    ``PolicyEvaluation`` 객체 목록을 넘기면 정책이 0개가 되고, 카드가 5장 떠 있는데 답변은
    "찾지 못했어요"로 나간다. ``related._as_policies`` 도 같은 검사를 하므로 1순위 칩인
    마감 칩(``CHIP_DEADLINE_ORDER``)이 예외 없이 사라진다.

    ``model_dump(mode="json")`` 을 쓰는 이유는 ``checked_at`` 이 ``date`` 이고
    ``source_url`` 이 ``HttpUrl`` 이기 때문이다. 모델이 그 값을 문자열로 바꿔 준다. 그래서
    **모델 밖의 값은 손대지 않는다.** ``date``/``UUID`` 가 모델 없이 맨손으로 들어오면 그대로
    둔다. 여기서 직렬화 규칙을 따로 만들면 pydantic 과 두 가지 규칙이 생긴다.

    ``hasattr(value, "model_dump")`` 로 덕 타이핑한다. pydantic 을 import 하지 않는 이유는
    모듈 독스트링에 있다.
    """
    try:
        if _depth > _MAX_PLAIN_DEPTH:
            # 자기 자신을 담은 값. 더 들어가면 RecursionError 다. 그 자리는 그대로 둔다.
            return value

        dump = getattr(value, "model_dump", None)
        if callable(dump) and not isinstance(value, type):
            try:
                dumped = dump(mode="json")
            except TypeError:
                # ``mode`` 를 모르는 객체(가짜 객체, pydantic v1 모델). 기본 호출로 물러난다.
                dumped = dump()
            return as_plain(dumped, _depth + 1)

        if isinstance(value, Mapping):
            return {key: as_plain(item, _depth + 1) for key, item in value.items()}

        if isinstance(value, (list, tuple, set, frozenset)):
            return [as_plain(item, _depth + 1) for item in value]

        return value
    except Exception:  # noqa: BLE001 - 변환 실패가 턴을 죽이지 않게
        return value


# ---------------------------------------------------------------------------
# 4. profile_update 이벤트
# ---------------------------------------------------------------------------


def profile_update_event(merge_result_or_dict: Any) -> Optional[Payload]:
    """``interpret.MergeResult`` → 서버 ``ProfileUpdateEventData`` 모양.

    근거: ``docs/10-ai-a-server-handoff.md`` 2-1 과 6장 체크리스트 3·9번,
    ``docs/03-api-contract.md`` 5-3.

    ``MergeResult`` 객체를 그대로 넘겨도 되고 ``to_profile_update()`` 결과 dict 를 넘겨도 된다.

    키를 세 개로 접는다
        ``changes`` (dict 목록) → ``changed_fields`` (항목 이름 → 바뀐 값). 값은 ``after``
        ``notice``              → ``message``
        ``profile``             → ``profile`` (이름 그대로)

    **버려지는 것 (서버 모델에 자리가 없다)**
        ``before``          "휴학 → 재학" 의 왼쪽. 화면이 무엇이 무엇으로 바뀌었는지 보여주는 값
        ``label``           값의 화면 문구
        항목별 ``notice``   합친 문구는 ``message`` 에 남는다

    버리는 쪽으로 정한 근거는 2-1 에 있다. ``ProfileUpdateEventData`` 를 ``changes`` 구조로
    바꾸는 것이 계약(5-3)과 구현을 되붙이는 옳은 방향이지만, 프론트가 이미 확정된 모델로
    프로필 바를 만들고 있고 잃는 것은 "휴학 → 재학" 표시 하나다. ``message`` 에 "휴학으로
    바꿨어요"가 그대로 들어가므로 사용자는 무엇이 바뀌었는지 안다. 세 곳이 동시에 움직여야
    하는 비용이 그 표시보다 크다. **버린 건수는 기록에 남는다** (``notes`` 의
    ``before_no_server_field`` 등).

    ``region`` 은 ``changed_fields`` 에서 뺀다. ``ProfileField`` enum 에 없어 그대로 넣으면
    스트림 중간 ValidationError 다 (``EXCLUDED_PROFILE_FIELDS`` 주석).

    바뀐 것이 없으면 ``None``. 서버는 ``None`` 일 때 이벤트를 보내지 않는다. ``region`` 만
    바뀐 턴도 ``None`` 이다. 서버 프로필 관점에서 바뀐 것이 없고, 받아들이지 못할 변경을
    안내 문구로만 알리면 화면과 세션이 어긋난다.

    남는 위험(이 함수가 막지 못하는 것): ``profile`` 안의 ``region`` 값이 ``seoul`` 이 아니면
    서버 ``Profile`` 이 거부한다(``Literal[Region.SEOUL]``). 값을 여기서 고쳐 쓰지 않는다 —
    화면·판정에 쓰는 값을 변환 계층이 조용히 바꾸는 것은 ``docs/09-value-naming-decision.md``
    가 버린 방향이다. 서울 밖 거주는 애초에 제품 범위 밖이다(``docs/03-api-contract.md`` 2장).
    """
    book = _NoteBook()
    try:
        payload = _unwrap(merge_result_or_dict, "to_profile_update")
        if not isinstance(payload, Mapping):
            return None

        changed: Dict[str, Any] = {}
        for entry in _as_rows(payload.get("changes")):
            if not isinstance(entry, Mapping):
                book.drop("", DROP_CHANGE_NOT_A_MAPPING, type(entry).__name__)
                continue

            name = _as_text(entry.get("field"))
            if not name:
                book.drop("", DROP_CHANGE_NO_FIELD, "")
                continue

            if name in EXCLUDED_PROFILE_FIELDS:
                book.drop(name, DROP_REGION_NOT_IN_ENUM, _as_text(entry.get("after")))
                continue

            if name in changed:
                # 같은 항목이 두 번 오면 나중 값이 이긴다. 반영 순서가 그 순서였으므로
                # 마지막 값이 새 프로필의 값과 같다. 다르면 화면과 프로필이 어긋난다.
                book.note(NOTE_DUPLICATE_FIELD, name)

            changed[name] = as_plain(entry.get("after"))

            # 자리가 없어 접는 값. 무엇을 몇 건 잃었는지 세어 둔다.
            if "before" in entry:
                book.note(NOTE_BEFORE_NO_SERVER_FIELD, name)
            if _as_text(entry.get("label")):
                book.note(NOTE_LABEL_NO_SERVER_FIELD, name)
            if _as_text(entry.get("notice")):
                book.note(NOTE_ITEM_NOTICE_NO_SERVER_FIELD, name)

        if not changed:
            return None

        profile = as_plain(payload.get("profile"))
        if not isinstance(profile, Mapping):
            # 프로필을 읽을 수 없으면 서버 ``Profile`` 검증이 어차피 실패한다. 빈 dict 를
            # 넣어 실패 지점을 서버 모델로 옮기고, 이유는 기록에 남긴다.
            book.drop("profile", DROP_PROFILE_NOT_A_MAPPING, type(profile).__name__)
            profile = {}

        return Payload(
            {
                "changed_fields": changed,
                "message": _as_text(payload.get("notice")),
                "profile": dict(profile),
            },
            book.frozen(),
        )
    except Exception:  # noqa: BLE001 - 변환 실패가 카드를 지우지 않게
        # 이벤트를 보내지 않는다. 프로필 바가 한 턴 늦게 갱신되는 것이 스트림이 끊기는
        # 것보다 낫다. 다음 턴의 profile_update 가 같은 프로필을 다시 싣는다.
        return None


# ---------------------------------------------------------------------------
# 5. related 이벤트 (칩 문구는 이벤트로, 칩 번호는 세션으로)
# ---------------------------------------------------------------------------


def _chip_rows(value: Any, book: _NoteBook) -> Tuple[Tuple[str, str], ...]:
    """칩을 ``(id, text)`` 쌍으로 읽는다. 상한까지 자른다. ``related_event`` 와 공용."""
    raw: Any = value
    if isinstance(value, Mapping):
        inner = value.get("chips")
        if inner is None:
            inner = value.get("items")
        if inner is None:
            inner = [value] if ("text" in value or "id" in value) else []
        raw = inner

    rows: List[Tuple[str, str]] = []
    for entry in _as_rows(raw):
        if isinstance(entry, Mapping):
            chip_id = _as_text(entry.get("id"))
            text = _as_text(entry.get("text"))
        elif hasattr(entry, "text") or hasattr(entry, "id"):
            # ``related.RelatedChip`` 을 ``to_event()`` 없이 그대로 넘긴 경우.
            chip_id = _as_text(getattr(entry, "id", ""))
            text = _as_text(getattr(entry, "text", ""))
        else:
            book.drop("", DROP_CHIP_NOT_READABLE, type(entry).__name__)
            continue

        if not text:
            book.drop(chip_id, DROP_CHIP_NO_TEXT, "")
            continue

        if len(rows) >= MAX_RELATED_QUESTIONS:
            book.drop(chip_id, DROP_CHIP_OVER_LIMIT, text)
            continue

        rows.append((chip_id, text))

    return tuple(rows)


def related_event(chips_event_or_result: Any) -> Optional[Dict[str, Any]]:
    """``related.build()`` / ``to_event()`` → 서버 ``RelatedEventData`` 모양.

    근거: ``docs/10-ai-a-server-handoff.md`` 2-3 과 4장 2번, 6장 체크리스트 4번,
    ``docs/03-api-contract.md`` 5-2.

    ``{"chips": [{"id", "text"}]}`` → ``{"questions": [문구, ...]}``. 상한은
    ``related.MAX_CHIPS`` 에서 끌어온다(``MAX_RELATED_QUESTIONS``). 숫자를 다시 적지 않는다.

    **칩이 없으면 ``None``.** ``related.build`` 는 상황에 맞는 칩이 없을 때
    ``{"chips": []}`` 를 돌려주고 그것이 정상 동작인데, ``RelatedEventData(questions=[])``
    는 검증을 통과하고 ``server/api/chat.py`` 는 ``is not None`` 으로만 판단한다. 그대로
    넘기면 매 턴 빈 ``related`` 이벤트가 나가고 프론트는 칩 영역을 그렸다 비운다.

    칩 ``id`` 는 이 이벤트에 실을 자리가 없다. 그래서 ``related_chip_ids`` 로 **따로** 낸다.
    이유는 그 함수 독스트링에 있다.
    """
    book = _NoteBook()
    try:
        rows = _chip_rows(chips_event_or_result, book)
        if not rows:
            return None

        for chip_id, _text in rows:
            # 번호가 와이어에서 사라진다는 사실을 턴마다 기록한다. 이 기록이 비어 있지 않은
            # 동안에는 4장 2번(서버가 id 를 보존하기)이 아직 안 된 상태라는 뜻이다.
            book.note(NOTE_CHIP_ID_NOT_ON_WIRE, chip_id)

        return Payload({"questions": [text for _chip_id, text in rows]}, book.frozen())
    except Exception:  # noqa: BLE001 - 칩은 P1 이다. 답변을 끌어내리지 않는다
        return None


def related_chip_ids(chips_event_or_result: Any) -> Tuple[str, ...]:
    """이벤트에 실린 칩의 **번호**. 세션이 다음 턴 ``shown`` 으로 되돌릴 값이다.

    근거: ``docs/10-ai-a-server-handoff.md`` 2-3("`id` 가 전송 경로에서 사라지면
    ``related.select(shown=...)`` 의 중복 제거가 영구히 작동하지 않는다")과 4장 2번,
    ``docs/03-api-contract.md`` 5-2·11장.

    **이벤트 본문과 세션 부기값을 왜 나누는가.** ``RelatedEventData`` 에는 ``questions``
    (문구 목록)만 있고 ``id`` 자리가 없다. 그런데 ``related.select`` 는 **문구가 아니라
    번호**로 이미 보여준 칩을 거른다. 문구로 거르면 문구를 다듬는 순간 이미 보여준 칩이 새
    칩으로 되살아나기 때문이다(``related.RelatedChip`` 주석). 그래서 번호를 이벤트에 섞지
    않고 따로 낸다. 서버는 이 값을 세션에 쌓아 두고 다음 턴 ``shown`` 으로 되돌리면 된다.

        body = handoff.related_event(chips)
        if body is not None:
            session.shown_chips.update(handoff.related_chip_ids(chips))

    번호를 payload 에 끼워 넣는 선택은 버렸다. ``extra="forbid"`` 라 스트림 중간
    ValidationError 가 된다. 서버가 문구→번호 역매핑 표를 드는 선택도 버렸다. 칩 문구가 두
    곳에 생겨 문구를 다듬으면 매핑이 조용히 깨진다(4장 2번 "대안" 칸).

    ``related_event`` 가 ``None`` 을 돌려주는 입력에는 빈 튜플을 돌려준다. 보여준 것이
    없으면 기록할 것도 없다. 번호가 빈 칩은 빈 문자열이 아니라 **자리에서 빠진다.** 빈
    문자열을 ``shown`` 에 넣으면 세션이 "번호가 빈 칩을 이미 보여줬다"고 기억한다.
    """
    book = _NoteBook()
    try:
        return tuple(
            chip_id for chip_id, _text in _chip_rows(chips_event_or_result, book) if chip_id
        )
    except Exception:  # noqa: BLE001
        return ()


# ---------------------------------------------------------------------------
# 6. followup 이벤트
# ---------------------------------------------------------------------------


def followup_event(question_or_none: Any) -> Optional[Dict[str, Any]]:
    """``followup.next_question()`` 결과를 서버 ``FollowupQuestion`` 으로 넘긴다.

    근거: ``docs/10-ai-a-server-handoff.md`` 2-2 와 4장 3번, 6장 체크리스트 5번.

    **거의 통과시키기만 한다.** 이 쌍은 키 6개(``field``, ``question``, ``reason``,
    ``options``, ``allow_free_text``, ``allow_skip``)가 이미 정확히 일치한다. 2장 대조표에서
    유일하게 이름 협상이 필요 없는 쌍이다. 그래서 이 함수는 이름을 바꾸지 않고, 이미 맞는
    쌍을 변환하는 척하지도 않는다. 키를 걸러내지도 않는다 — 언젠가 한쪽 키 이름이 흘러가면
    그 사실은 테스트에서 **보여야** 하고, 여기서 걸러내면 숨는다.

    딱 하나만 검사한다. ``field`` 가 ``questions.PLANNED_BASIS_FIELD``(``planned_basis``)면
    ``None`` 을 돌려주고 기록에 남긴다. 그 값은 서버 ``AskableProfileField`` enum 에 없어
    그대로 보내면 스트림 중간 ValidationError 다.

    그 이름은 AI A 의 실수가 아니다. ``questions.py`` 와 ``docs/03-api-contract.md`` 3장·11장이
    "프로필 항목이 아닌 질문의 답은 프로필에 넣지 않는다"를 지키려고 일부러 프로필 항목 표
    밖의 이름을 골랐다(전에는 ``status`` 였고, 그래서 ``status="planned"`` 라는 허용 값 밖
    값이 프로필에 박혔다).

    **planned 확인 질문을 다른 경로로 내보내는 결정은 12:00 통합 안건이다**(4장 3번,
    6장 5번). 여기서 지어내지 않는다. 지금 할 수 있는 것은 "보내면 스트림이 끊긴다"를 아는
    것이고, 대신 무엇을 보낼지는 서버 흐름과 함께 정해야 한다. 임시로 ``field`` 를 다른
    항목 이름으로 바꿔 보내는 선택은 특히 나쁘다. 그 답이 프로필에 박힌다.
    """
    book = _NoteBook()
    try:
        question = _unwrap(question_or_none, "to_dict")
        if not isinstance(question, Mapping):
            if question is not None:
                book.drop("", DROP_FOLLOWUP_NOT_A_MAPPING, type(question).__name__)
            return None

        if _as_text(question.get("field")) == questions.PLANNED_BASIS_FIELD:
            book.drop(
                questions.PLANNED_BASIS_FIELD,
                DROP_PLANNED_BASIS,
                _as_text(question.get("question")),
            )
            return None

        return Payload(as_plain(question), book.frozen())
    except Exception:  # noqa: BLE001 - 질문 하나가 답변을 끌어내리지 않게
        return None


# ---------------------------------------------------------------------------
# 7. 답변 실패
# ---------------------------------------------------------------------------


def answer_failure_payload() -> Dict[str, Any]:
    """``pipeline.finish_turn`` 이 ``answer_failed=True`` 를 낼 때 서버가 보낼 것.

    근거: ``docs/10-ai-a-server-handoff.md`` 3장과 4장 1번, ``docs/03-api-contract.md``
    10장(오류 코드와 문구), 5장(``error`` 이벤트 행).

    **지금 서버 계약은 답변 실패를 표현할 수 없다.**
        ``ChatPipelineResult.answer_deltas``  ``min_length=1`` — 델타 0개를 만들 수 없다
        ``AnswerDeltaEventData.delta``        ``min_length=1`` + ``str_strip_whitespace=True``
        ``SSEEventName``                     ``error`` 가 없다
    ``server/errors.py`` 의 ``ANSWER_FAILED`` 는 HTTP JSON 엔벨로프 전용이고 스트림에서
    쓰이는 곳이 0건이다. 스트림은 이미 200 을 보낸 뒤라 JSON 엔벨로프로 돌아갈 수도 없다.

    그래서 이 함수는 **계약이 바뀔 때까지 쓸 수 있는 유일한 표현**을 돌려준다: 실패 안내
    문구 한 줄을 델타로 쓸 수 있게 만든 dict.

        {"code": "answer_failed", "delta": "설명을 불러오지 못했어요. ..."}

        payload = handoff.answer_failure_payload()
        deltas = [AnswerDeltaEventData(delta=payload["delta"])]   # 계약이 바뀌기 전
        # 계약이 바뀐 뒤: error 이벤트에 payload["code"] 를 싣는다

    문구는 지어낸 값이 아니다. ``ANSWER_FAILED_MESSAGE`` 주석에 출처를 적었다.

    **한계: 이 표현으로는 프론트가 성공과 실패를 구분할 수 없다.** ``code`` 는 와이어에 실을
    자리가 없어 ``delta`` 문자열만 나가고, 프론트는 본문을 오류 문구와 글자로 비교해야
    실패를 안다. 문구를 다듬는 순간 그 판정이 조용히 깨진다. 실패를 성공처럼 보여 주는 것은
    ``pipeline.BLOCKING_PROBLEM_CODES`` 주석이 이미 거부한 방향이고, 그래서 이것은 임시
    표현이다.

    서버에 요청하는 최소 변경 (4장 1번, 권고는 (가))
        (가) ``SSEEventName`` 에 ``error`` 추가 + payload 모델(code, message) + 이벤트 래퍼
             + ``SSEEvent`` union 1줄, ``ChatPipelineResult.answer_deltas`` 의
             ``min_length=1`` 제거. 3개 파일 약 13줄. 프론트가 실패를 **구분할 수 있다**
        (나) ``ChatPipelineResult`` 에 ``answer_failed: bool`` 추가 + 고정 문구 델타.
             2개 파일 약 4줄. 프론트가 실패를 구분할 수 **없다** (그래서 권고하지 않는다)
    """
    return {"code": ANSWER_FAILED_CODE, "delta": ANSWER_FAILED_MESSAGE}


# ---------------------------------------------------------------------------
# 8. 답변 델타
# ---------------------------------------------------------------------------


def delta_lines(text: Any) -> Tuple[str, ...]:
    """답변 본문을 ``answer_delta`` 조각으로 쪼갠다. **공백 조각을 반드시 버린다.**

    근거: ``docs/10-ai-a-server-handoff.md`` 3-2 와 6장 체크리스트 7번,
    ``docs/03-api-contract.md`` 5장 ``answer_delta`` 행.

    공백만 있는 조각 하나가 스트림 중간 ``ValidationError`` 를 낸다.
    ``ContractModel`` 의 ``str_strip_whitespace=True`` 가 먼저 공백을 벗기고
    ``AnswerDeltaEventData.delta`` 의 ``min_length=1`` 이 그 빈 문자열을 거부한다. 터지는
    자리는 델타 루프 안이고, 그때는 ``status``·``policies`` 프레임이 이미 나간 뒤다.
    클라이언트는 절반 그려진 답변과 끊긴 연결만 본다. 문장 단위로 자르면 종결 부호 뒤 공백이
    조각으로 남기 쉬워서 이 실패는 정상 입력에서도 난다.

    문장 분리는 ``answer.split_sentences`` 에 맡긴다. 정규식을 다시 쓰지 않는다. 그 함수는
    "3.5%" 와 "2026. 3. 1." 에서 자르지 않도록 이미 손질돼 있고, 여기서 비슷한 규칙을 또
    만들면 한쪽만 고쳐진다.

    본문이 비면 빈 튜플이다. 그때는 답변 실패이고(``pipeline.EMPTY_AFTER_SANITIZE``),
    무엇을 보낼지는 ``answer_failure_payload`` 가 다룬다. 빈 튜플을 그대로 넘기면
    ``answer_deltas`` 의 ``min_length=1`` 에 걸리므로 서버가 그 분기를 두어야 한다.

    버린 공백 조각은 세지 않는다. 공백 조각에는 잃을 정보가 없다 — 다른 기록과 달리
    "무엇을 버렸는지"가 없는 유일한 경우다.
    """
    try:
        if not isinstance(text, str):
            return ()
        pieces = tuple(
            stripped for piece in answer.split_sentences(text) if (stripped := piece.strip())
        )
        if pieces:
            return pieces
        # ``split_sentences`` 가 빈 결과를 내는데 본문에 글자가 있는 경우(종결 부호 없는 한
        # 줄 등)에는 본문 전체를 한 조각으로 보낸다. 버리면 답변이 사라진다.
        whole = text.strip()
        return (whole,) if whole else ()
    except Exception:  # noqa: BLE001 - 쪼개기 실패로 답변을 잃지 않게
        whole = text.strip() if isinstance(text, str) else ""
        return (whole,) if whole else ()


__all__ = [
    "DROP_REGION_NOT_IN_ENUM",
    "DROP_CHANGE_NOT_A_MAPPING",
    "DROP_CHANGE_NO_FIELD",
    "DROP_CHIP_NOT_READABLE",
    "DROP_CHIP_NO_TEXT",
    "DROP_CHIP_OVER_LIMIT",
    "DROP_PLANNED_BASIS",
    "DROP_FOLLOWUP_NOT_A_MAPPING",
    "DROP_PROFILE_NOT_A_MAPPING",
    "NOTE_BEFORE_NO_SERVER_FIELD",
    "NOTE_LABEL_NO_SERVER_FIELD",
    "NOTE_ITEM_NOTICE_NO_SERVER_FIELD",
    "NOTE_CHIP_ID_NOT_ON_WIRE",
    "NOTE_DUPLICATE_FIELD",
    "EXCLUDED_PROFILE_FIELDS",
    "MAX_RELATED_QUESTIONS",
    "ANSWER_FAILED_CODE",
    "ANSWER_FAILED_MESSAGE",
    "DroppedValue",
    "HandoffNotes",
    "Payload",
    "as_plain",
    "profile_update_event",
    "related_event",
    "related_chip_ids",
    "followup_event",
    "answer_failure_payload",
    "delta_lines",
]
