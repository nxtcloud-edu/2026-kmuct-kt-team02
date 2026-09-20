"""AI 한 턴 전체 (서버가 부를 유일한 입구).

**서버는 이 파일의 ``run_turn`` 만 부르면 된다.**
지금까지 서버는 ``ai.conversation.pipeline`` 의 네 함수와 ``ai.judgment`` 의
``ExceptionJudge``·``public_conditions`` 를 각각 따로 불러 **순서를 직접 알아야** 했다.
그 순서 지식이 서버에 생기면 같은 규칙이 문서·AI A·AI B·서버 네 곳에 있게 되고,
한 곳만 고쳐진다. 이 파일이 그 순서를 한 곳에 모은다.

이 파일이 하는 일은 **순서를 아는 것**이다. 판단은 각 모듈에 맡긴다. 허용 값 표, 금지
표현, 질문 문구, 배지 문구, 조건 공개 규칙을 여기에 다시 적지 않는다.

한 턴의 순서 (docs/03-api-contract.md 9장)
------------------------------------------
| 단계 | 하는 일 | 이 파일의 무엇 |
| --- | --- | --- |
| 1~2 | 마스킹, 진행 단계 | 서버 (12장) |
| 3 | 메시지 해석 + 프로필 반영 | ``pipeline.interpret_turn`` |
| 4 | 범위 밖·잡담이면 고정 응답 후 종료 | ``TurnResult.stop_here`` |
| 5 | 규칙 재계산, 후보 선정 | 서버 + ``rules/``. ``needs_recalculation`` 으로 알림 |
| 6 | 진행 단계 | 서버 |
| 7 | 예외 조건 판정 (후보별 동시 실행) | ``ExceptionJudge.judge_policies`` |
| 8 | 인용 검증, 각주 번호 부여 | ``judge_and_verify`` 안에서 검증 → ``public_conditions`` |
| 9 | 진행 단계 | 서버 |
| 10 | 답변 작성 | ``pipeline.build_answer_prompt`` → ``answer_writer`` → ``pipeline.finish_turn`` |
| 11 | 후속 질문 선택 | ``pipeline.finish_turn`` |
| 12 | 관련 질문 칩 | ``pipeline.finish_turn`` |
| 13 | 종료 (전체 20초 상한) | 서버 |

경계에서 실제로 하는 일 네 가지
-------------------------------
1. **AI B 판정 결과를 AI A 가 읽는 모양으로 옮긴다.** ``judge_and_verify`` 는 정책별로
   ``{"policy_id", "conditions", "removed"}`` 를 내고, ``answer.build_skeleton`` 은
   ``docs/03-api-contract.md`` 4장 모양의 정책 dict 목록을 읽는다. 그 사이를 잇는다.
2. **각주 번호를 정책들에 걸쳐 이어서 붙인다.** ``public_conditions`` 는 한 정책 분량만
   번호를 붙이고 ``next_footnote_id`` 를 돌려준다. 그 값을 다음 정책에 넘기는 것이
   "한 응답 안에서 1부터 순서대로"(4-1, 5-1)를 지키는 유일한 방법이다. 이 파일이
   빼먹으면 모든 정책의 각주가 1번부터 다시 시작해 답변의 ``[1]`` 이 엉뚱한 발췌를 가리킨다.
3. **미확인 조건에서 후속 질문 후보를 만든다.** ``followup`` 은 ``UnknownItem`` 목록을
   요구하고, AI 판정은 ``result="unknown"`` + ``needed_field`` 를 낸다. 규칙 엔진이 내는
   미확인 목록과 **합쳐서** 넘긴다. AI 판정에서만 나온 미확인을 빼면, 예외 조건 때문에
   ``check`` 인 정책에서 후속 질문이 뜨지 않는다.
4. **각주 이벤트 본문을 만든다.** 공개된 조건의 발췌를 ``footnotes`` 모양으로 모은다
   (5-1). ``answer.sanitize`` 는 이 목록에 없는 번호를 쓴 문장을 삭제하므로, 여기서
   빠뜨린 각주는 답변 문장을 조용히 사라지게 만든다.

실패 방침
--------
**어떤 입력에도 예외를 던지지 않는다.** ``pipeline.py`` 와 같은 이유다. AI 가 전부
실패해도 5단계의 규칙 기반 카드는 이미 화면에 있고, 여기서 터지면 그 카드까지 500 에
묻힌다. 단계별로 무엇을 잃고 무엇을 남기는지는 아래와 같다.

| 실패한 단계 | 잃는 것 | 남는 것 |
| --- | --- | --- |
| 3 해석 | 프로필 갱신 | 카드, 답변, 후속 질문 (프로필은 그대로) |
| 7 판정 | AI 조건 (전부 미확인으로) | 카드, 규칙 조건, 답변 |
| 8 검증·번호 | 근거 없는 조건과 그 각주 | 카드, 검증 통과한 조건 |
| 10 답변 | 설명 본문 | 카드, 각주, 후속 질문, 칩 |
| 11~12 질문·칩 | 후속 질문 또는 칩 | 나머지 전부 |

모델 호출을 이 파일이 하지 않는 이유
------------------------------------
답변은 스트리밍이어야 한다(``answer_delta``). 그 조각을 서버가 흘려야 하므로, 이 파일은
**답변 작성을 콜러블로 받는다**(``answer_writer``). 프롬프트는 이 파일이 만들고, 호출과
스트리밍은 서버가 한다. 판정은 스트리밍이 아니라서 ``ExceptionJudge`` 를 직접 부른다.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from ai.conversation import answer, fields, handoff, interpret, pipeline

# AI B 는 선택적으로 불러온다. ai/conversation/__init__.py 가 import 실패를 삼키는 것과
# 같은 이유다. 판정 모듈 하나가 못 불러와졌다고 이 파일 전체가 죽으면, 규칙 기반 카드까지
# 화면에서 사라진다. 없으면 예외 조건은 전부 미확인이 되고 턴은 계속 돈다.
MISSING: List[str] = []

try:  # pragma: no cover - import 경로는 테스트에서 직접 확인한다
    from ai.judgment import ASK_NOTICE, UNKNOWN, public_conditions
except Exception as _exc:  # noqa: BLE001
    MISSING.append(f"ai.judgment: {type(_exc).__name__}: {_exc}")
    ASK_NOTICE = "공고 확인 필요"  # docs/03-api-contract.md 4-1 의 고정 문자열
    UNKNOWN = "unknown"  # server/schemas.py ConditionResult
    public_conditions = None  # type: ignore[assignment]

try:  # pragma: no cover
    from ai.judgment import ExceptionJudge
except Exception as _exc:  # noqa: BLE001
    MISSING.append(f"ai.judgment.ExceptionJudge: {type(_exc).__name__}: {_exc}")
    ExceptionJudge = None  # type: ignore[assignment]


#: 판정기 자리. ``ExceptionJudge.judge_policies`` 와 같은 모양이면 무엇이든 받는다.
#: 서버가 모델 키 없이 띄운 환경에서는 ``None`` 을 넘기면 된다.
JudgePolicies = Callable[[Sequence[Dict[str, Any]], Optional[Dict[str, Any]]], List[Dict[str, Any]]]

#: 답변 작성 자리. 프롬프트를 받아 본문 문자열을 돌려준다.
#: 스트리밍 조각을 서버가 흘려야 하므로 호출 자체는 서버 몫이다(모듈 독스트링 마지막 절).
AnswerWriter = Callable[[Any], str]

# 판정 실패·부재를 구분하는 기록 코드. 지표 집계에 쓰므로 문자열을 바꾸지 않는다.
NOTE_NO_JUDGE = "no_judge"
NOTE_JUDGE_FAILED = "judge_failed"
NOTE_NO_PUBLIC_CONDITIONS = "no_public_conditions"
NOTE_FOOTNOTE_NUMBERING_SKIPPED = "footnote_numbering_skipped"
NOTE_NO_ANSWER_WRITER = "no_answer_writer"
NOTE_ANSWER_WRITER_FAILED = "answer_writer_failed"
NOTE_PROMPT_FAILED = "prompt_failed"


# ---------------------------------------------------------------------------
# 1. 작은 도우미 (모두 예외를 던지지 않는다)
# ---------------------------------------------------------------------------


def _as_rows(raw: Any) -> List[Any]:
    """무엇이 오든 목록으로. Mapping 하나는 원소 하나로 본다."""
    if raw is None:
        return []
    if isinstance(raw, Mapping):
        return [raw]
    if isinstance(raw, (str, bytes, int, float, bool)):
        return []
    try:
        return list(raw)
    except TypeError:
        return []


def _mappings(raw: Any) -> List[Mapping[str, Any]]:
    """Mapping 만 남긴다. 규칙 결과와 AI 결과가 합쳐지는 중간 상태에서 None 이 섞인다."""
    return [row for row in _as_rows(raw) if isinstance(row, Mapping)]


def _text(raw: Any) -> str:
    if raw is None or isinstance(raw, (Mapping, list, tuple, set, frozenset, bool)):
        return ""
    return str(raw).strip()


def _policy_id_of(policy: Mapping[str, Any]) -> str:
    """정책 번호. ``policy_id`` 와 ``id`` 를 모두 받는다.

    ``docs/03-api-contract.md`` 4장은 ``policy_id``, ``docs/04-data-schema.md`` 1장은
    ``id`` 를 쓴다. 두 문서가 갈라져 있고(``docs/09`` 2-2장이 기록해 뒀다) 판정 입력은
    데이터 쪽에서, 화면 출력은 API 쪽에서 온다. 그래서 둘 다 읽는다.
    """
    return _text(policy.get("policy_id")) or _text(policy.get("id"))


# ---------------------------------------------------------------------------
# 2. 7~8단계: 판정 → 검증 → 각주 번호 → 정책 목록 합치기
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class JudgedPolicies:
    """판정과 인용 검증을 끝낸 결과.

    policies       화면·답변에 쓸 정책 목록 (docs/03-api-contract.md 4장 모양)
    footnotes      각주 이벤트 본문에 실을 항목 목록 (5-1)
    unknown_items  AI 판정에서 나온 미확인 항목 (규칙 엔진 것과 합쳐 쓴다)
    removed        인용 검증 제거 기록. 지표용
    issues         조건 공개 단계에서 걸러낸 이유 코드. 지표용
    notes          관용 처리·건너뛴 단계 기록. 지표용
    """

    policies: Tuple[Dict[str, Any], ...] = ()
    footnotes: Tuple[Dict[str, Any], ...] = ()
    unknown_items: Tuple[Dict[str, Any], ...] = ()
    removed: Tuple[Dict[str, Any], ...] = ()
    issues: Tuple[str, ...] = ()
    notes: Tuple[str, ...] = ()

    def summary(self) -> Dict[str, Any]:
        """지표 요약 (docs/03-api-contract.md 13장). 사용자 화면에 쓰지 않는다."""
        issue_counts: Dict[str, int] = {}
        for code in self.issues:
            issue_counts[code] = issue_counts.get(code, 0) + 1
        return {
            "policies": len(self.policies),
            "footnotes": len(self.footnotes),
            "unknown_items": len(self.unknown_items),
            "removed_excerpts": len(self.removed),
            "issues": issue_counts,
            "notes": list(self.notes),
        }


def _unknown_items_from_conditions(
    conditions: Sequence[Mapping[str, Any]],
    *,
    policy_id: str,
    policy_rank: int,
    policy_title: str,
) -> List[Dict[str, Any]]:
    """미확인 조건에서 후속 질문 후보를 만든다.

    ``needed_field`` 가 ``ASK_NOTICE`` 인 것은 넣지 않는다. 물을 수 있는 항목이 아니라
    "공고를 확인해야 한다"는 표시이고, ``fields.is_askable`` 이 거짓이라
    ``pipeline.unknown_items_from`` 이 어차피 탈락시킨다. 여기서 넣으면 탈락 기록만
    늘어나 진짜 표기 불일치를 덮는다.

    ``result`` 가 미확인이 아닌 조건은 건너뛴다. 충족된 조건의 ``needed_field`` 가 남아
    있어도(판정기가 지우지 않았을 수 있다) 물을 이유가 없다.
    """
    items: List[Dict[str, Any]] = []
    for condition in conditions:
        if _text(condition.get("result")) != UNKNOWN:
            continue
        needed = _text(condition.get("needed_field"))
        if not needed or needed == ASK_NOTICE:
            continue
        items.append(
            {
                "field": needed,
                "policy_id": policy_id,
                "policy_rank": policy_rank,
                "policy_title": policy_title,
            }
        )
    return items


def _footnotes_from_conditions(
    conditions: Sequence[Mapping[str, Any]],
    *,
    policy: Mapping[str, Any],
) -> List[Dict[str, Any]]:
    """각주 이벤트 항목을 만든다 (docs/03-api-contract.md 5-1).

    번호는 ``public_conditions`` 가 이미 붙였다. **여기서 부여하지 않는다.** 번호를 두
    곳에서 붙이면 답변 문장의 ``[1]`` 과 각주 목록의 1번이 어긋나고, 그 어긋남은 오류 없이
    화면에 나간다.
    """
    rows: List[Dict[str, Any]] = []
    for condition in conditions:
        number = condition.get("footnote_id")
        excerpt = _text(condition.get("excerpt"))
        if not isinstance(number, int) or not excerpt:
            continue
        rows.append(
            {
                "footnote_id": number,
                "policy_id": _policy_id_of(policy),
                "excerpt": excerpt,
                "source_url": _text(condition.get("source_url"))
                or _text(policy.get("source_url")),
                "agency": _text(policy.get("agency")),
                "checked_at": policy.get("checked_at"),
            }
        )
    return rows


def judge_and_publish(
    policies: Any,
    profile: Optional[Mapping[str, Any]] = None,
    *,
    judge_policies: Optional[JudgePolicies] = None,
    start_footnote_id: int = 1,
) -> JudgedPolicies:
    """7~8단계. 후보 정책의 예외 조건을 판정하고 화면에 낼 수 있는 조건만 남긴다.

    근거: ``docs/03-api-contract.md`` 9장 7~8단계와 4-1·4-3·5-1,
    ``ai/judgment/README.md``, ``ai/judgment/integration.py`` 의 ``public_conditions``.

    ``judge_policies`` 가 ``None`` 이면 판정을 건너뛴다. 모델 키가 없는 환경에서도 턴은
    끝까지 돌아야 하고, 그때 정책은 규칙 조건만 가진 상태로 남는다. **예외 조건을 판정하지
    못한 것과 예외 조건이 없는 것은 다르다.** 앞의 경우는 ``notes`` 에 남아 지표에서 보인다.

    각주 번호는 정책들에 걸쳐 이어 붙인다. 정책마다 1번부터 다시 시작하면 답변의 각주가
    엉뚱한 발췌를 가리킨다 (모듈 독스트링 2번).
    """
    rows = _mappings(policies)
    if not rows:
        return JudgedPolicies()

    notes: List[str] = []
    verdicts: Dict[str, Dict[str, Any]] = {}
    removed: List[Dict[str, Any]] = []

    if judge_policies is None:
        notes.append(NOTE_NO_JUDGE)
    else:
        try:
            for verdict in _mappings(
                judge_policies(list(rows), dict(profile) if isinstance(profile, Mapping) else None)
            ):
                key = _text(verdict.get("policy_id"))
                verdicts[key] = dict(verdict)
                removed.extend(_mappings(verdict.get("removed")))
        except Exception:  # noqa: BLE001 - 판정 실패가 카드를 지우지 않게
            # 판정기는 자기 실패를 흡수하도록 만들어져 있다(ExceptionJudge 는 예외를
            # 던지지 않는다). 그래도 여기서 한 번 더 막는다. 주입된 판정기가 그 계약을
            # 지키지 않을 수 있고, 그때 잃는 것이 규칙 기반 카드 전체다.
            notes.append(NOTE_JUDGE_FAILED)
            verdicts = {}

    out_policies: List[Dict[str, Any]] = []
    footnotes: List[Dict[str, Any]] = []
    unknown_items: List[Dict[str, Any]] = []
    issues: List[str] = []
    next_id = max(1, int(start_footnote_id)) if isinstance(start_footnote_id, int) else 1

    if public_conditions is None:
        notes.append(NOTE_NO_PUBLIC_CONDITIONS)

    for rank, policy in enumerate(rows):
        merged = dict(policy)
        policy_id = _policy_id_of(policy)
        title = _text(policy.get("title"))

        rule_conditions = _mappings(policy.get("conditions"))
        ai_conditions = _mappings(verdicts.get(policy_id, {}).get("conditions"))

        # 규칙 조건과 AI 조건을 **합친 목록**을 한 번에 공개 변환한다.
        # public_conditions 가 계약 순서(unmet → unknown → met)로 정렬한 뒤 번호를 한 번만
        # 붙이기 때문이다. 따로 부르면 정렬이 두 번 일어나 순서와 번호가 어긋난다.
        combined = list(rule_conditions) + list(ai_conditions)

        if public_conditions is None or not combined:
            if public_conditions is None and combined:
                notes.append(NOTE_FOOTNOTE_NUMBERING_SKIPPED)
            merged["conditions"] = list(combined)
            visible: Sequence[Mapping[str, Any]] = ()
        else:
            try:
                batch = public_conditions(
                    combined,
                    source_url=_text(policy.get("source_url")) or None,
                    start_footnote_id=next_id,
                    raw_text=_text(policy.get("raw_text")) or None,
                )
                visible = _mappings(batch.conditions)
                merged["conditions"] = [dict(item) for item in visible]
                next_id = max(next_id, int(getattr(batch, "next_footnote_id", next_id) or next_id))
                issues.extend(str(code) for code in getattr(batch, "issues", ()) or ())
            except Exception:  # noqa: BLE001 - 조건 변환 실패가 카드를 지우지 않게
                notes.append(NOTE_FOOTNOTE_NUMBERING_SKIPPED)
                merged["conditions"] = []
                visible = ()

        footnotes.extend(_footnotes_from_conditions(visible, policy=merged))

        # 미확인 항목은 **공개된 조건**에서 모은다. 공개되지 않은 조건은 화면에 없으므로
        # 그것 때문에 질문하면 사용자는 왜 묻는지 알 수 없다.
        unknown_items.extend(
            _unknown_items_from_conditions(
                visible,
                policy_id=policy_id,
                policy_rank=rank,
                policy_title=title,
            )
        )
        out_policies.append(merged)

    return JudgedPolicies(
        policies=tuple(out_policies),
        footnotes=tuple(footnotes),
        unknown_items=tuple(unknown_items),
        removed=tuple(removed),
        issues=tuple(issues),
        notes=tuple(notes),
    )


# ---------------------------------------------------------------------------
# 3. 한 턴 전체
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TurnResult:
    """한 턴의 결과. 서버는 이 값만 읽어 이벤트를 만든다.

    이벤트 본문은 모두 **서버 모델 키 이름**이다 (``handoff`` 를 거쳤다).
    ``docs/10-ai-a-server-handoff.md`` 2장의 대조표를 따른다.

    profile_update  ``profile_update`` 이벤트 본문. 바뀐 게 없으면 None
    profile         갱신된 프로필. 세션에 이 값을 넣는다
    fixed_reply     범위 밖·잡담 고정 응답. 있으면 **여기서 턴을 끝낸다**
    policies        판정·검증을 끝낸 정책 목록
    footnotes       ``footnotes`` 이벤트에 실을 항목 목록
    answer_text     답변 본문. 실패면 빈 문자열
    answer_deltas   ``answer_delta`` 로 흘릴 조각. 공백 조각은 이미 걸러졌다
    answer_failed   답변을 버렸는가. True 면 카드는 유지하고 실패 문구를 쓴다
    followup        ``followup`` 이벤트 본문. 물을 것이 없으면 None
    related         ``related`` 이벤트 본문. 칩이 없으면 None
    related_chip_ids 세션에 저장할 칩 번호. 다음 턴 ``shown`` 으로 되돌린다
    metrics         지표 요약. **사용자 화면에 쓰지 않는다**
    """

    profile_update: Optional[Dict[str, Any]] = None
    profile: Dict[str, Any] = dataclass_field(default_factory=dict)
    fixed_reply: Optional[str] = None
    needs_recalculation: bool = True
    needs_planned_confirmation: bool = False
    policies: Tuple[Dict[str, Any], ...] = ()
    footnotes: Tuple[Dict[str, Any], ...] = ()
    answer_text: str = ""
    answer_deltas: Tuple[str, ...] = ()
    answer_failed: bool = False
    followup: Optional[Dict[str, Any]] = None
    related: Optional[Dict[str, Any]] = None
    related_chip_ids: Tuple[str, ...] = ()
    metrics: Dict[str, Any] = dataclass_field(default_factory=dict)

    @property
    def stop_here(self) -> bool:
        """4단계에서 끝내는가. ``fixed_reply`` 를 흘리고 ``done`` 을 보낸다."""
        return self.fixed_reply is not None


def run_turn(
    profile: Optional[Mapping[str, Any]] = None,
    interpretation_output: Any = None,
    *,
    policies: Any = (),
    rule_unknown_items: Any = (),
    judge_policies: Optional[JudgePolicies] = None,
    answer_writer: Optional[AnswerWriter] = None,
    asked_state: Any = None,
    shown_chips: Any = (),
    allow_compare: bool = True,
    start_footnote_id: int = 1,
) -> TurnResult:
    """AI 한 턴을 끝까지 돌린다. **어떤 입력에도 예외를 던지지 않는다.**

    근거: ``docs/03-api-contract.md`` 9장 전체, ``ai/conversation/README.md``,
    ``ai/judgment/README.md``, ``docs/10-ai-a-server-handoff.md``.

    받는 것
        profile               세션의 현재 프로필
        interpretation_output 해석 모델이 준 값 그대로 (문자열 또는 dict)
        policies              규칙 엔진이 고른 후보 정책 목록 (5단계 결과)
        rule_unknown_items    규칙 엔진이 낸 미확인 항목 목록 (rules/README.md 8장)
        judge_policies        예외 조건 판정기. 없으면 판정을 건너뛴다
        answer_writer         프롬프트를 받아 답변 본문을 돌려주는 콜러블
        asked_state           물어본·건너뛴 항목 (세션)
        shown_chips           이미 보여준 칩 번호 (세션)
        start_footnote_id     각주 시작 번호. 보통 1

    ``rule_unknown_items`` 를 따로 받는 이유: 규칙 엔진이 낸 미확인(소득·자치구 등)과 AI
    판정이 낸 미확인(예외 조건)은 **출처가 다르고 둘 다 필요하다.** 한쪽만 넘기면 그쪽
    원인으로만 질문이 뜬다. 합치는 일은 이 파일이 한다.
    """
    try:
        # --- 3~4단계: 해석과 프로필 반영 ---------------------------------
        interpreted = pipeline.interpret_turn(profile, interpretation_output)
        new_profile = interpreted.profile
        update_event = handoff.profile_update_event(interpreted.merged)

        if interpreted.stop_here:
            # 범위 밖·잡담. 판정도 답변도 하지 않는다. 20 초 예산을 답할 수 없는 질문에
            # 쓰지 않는 것이 계약이다 (9장 4단계). 프로필 갱신은 그대로 내보낸다 —
            # "범위 밖"은 답변을 줄이라는 뜻이고 프로필을 숨기라는 뜻이 아니다.
            return TurnResult(
                profile_update=update_event,
                profile=new_profile,
                fixed_reply=interpreted.fixed_reply,
                needs_recalculation=interpreted.needs_recalculation,
                needs_planned_confirmation=interpreted.needs_planned_confirmation,
                metrics={
                    "stopped_at_step": 4,
                    "intent": interpreted.intent,
                    "interpretation": {
                        "applied": len(interpreted.merged.applied),
                        "held": len(interpreted.merged.held),
                        "dropped": len(interpreted.merged.dropped),
                    },
                },
            )

        # --- 7~8단계: 판정, 인용 검증, 각주 번호 -------------------------
        judged = judge_and_publish(
            policies,
            new_profile,
            judge_policies=judge_policies,
            start_footnote_id=start_footnote_id,
        )

        # --- 10단계 준비: 뼈대 → 프롬프트 --------------------------------
        notes: List[str] = list(judged.notes)
        answer_text = ""
        try:
            skeleton = answer.build_skeleton(judged.policies)
            prompt = pipeline.build_answer_prompt(
                skeleton,
                profile=new_profile,
                policies=judged.policies,
                footnotes=judged.footnotes,
            )
        except Exception:  # noqa: BLE001 - 프롬프트 실패가 카드를 지우지 않게
            notes.append(NOTE_PROMPT_FAILED)
            prompt = None

        if answer_writer is None:
            notes.append(NOTE_NO_ANSWER_WRITER)
        elif prompt is not None:
            try:
                answer_text = answer_writer(prompt) or ""
            except Exception:  # noqa: BLE001 - 답변 실패는 error 이벤트로 흡수된다
                notes.append(NOTE_ANSWER_WRITER_FAILED)
                answer_text = ""

        # --- 10~12단계: 정리, 검증, 후속 질문, 칩 ------------------------
        # 미확인 항목은 규칙 엔진 것과 AI 판정 것을 **합쳐서** 넘긴다.
        merged_unknown = list(_as_rows(rule_unknown_items)) + list(judged.unknown_items)

        finished = pipeline.finish_turn(
            answer_text,
            footnotes=judged.footnotes,
            policies=judged.policies,
            unknown_items=merged_unknown,
            asked_state=asked_state,
            intent=interpreted.intent,
            shown_chips=shown_chips,
            allow_compare=allow_compare,
        )

        # --- 서버 모델 모양으로 변환 (docs/10 2장) -----------------------
        return TurnResult(
            profile_update=update_event,
            profile=new_profile,
            fixed_reply=None,
            needs_recalculation=interpreted.needs_recalculation,
            needs_planned_confirmation=interpreted.needs_planned_confirmation,
            policies=judged.policies,
            footnotes=judged.footnotes,
            answer_text=finished.answer_text,
            answer_deltas=handoff.delta_lines(finished.answer_text),
            answer_failed=finished.answer_failed,
            followup=handoff.followup_event(finished.followup),
            related=handoff.related_event(finished.related),
            related_chip_ids=handoff.related_chip_ids(finished.related),
            metrics={
                "intent": interpreted.intent,
                "interpretation": {
                    "applied": len(interpreted.merged.applied),
                    "held": len(interpreted.merged.held),
                    "dropped": len(interpreted.merged.dropped),
                },
                "judgment": judged.summary(),
                "answer": finished.metrics,
                "notes": notes,
            },
        )
    except Exception:  # noqa: BLE001 - 이 파일이 규칙 기반 카드를 지우지 않게
        # 여기까지 오면 AI 전부를 포기하고 카드만 남긴다. 규칙 엔진 결과는 서버가 이미
        # 5단계에서 내보냈으므로 화면은 비지 않는다 (9장 실패 처리).
        return TurnResult(
            profile=dict(profile) if isinstance(profile, Mapping) else {},
            answer_failed=True,
            metrics={"error": "run_turn_failed"},
        )


__all__ = [
    "MISSING",
    "JudgePolicies",
    "AnswerWriter",
    "NOTE_NO_JUDGE",
    "NOTE_JUDGE_FAILED",
    "NOTE_NO_PUBLIC_CONDITIONS",
    "NOTE_FOOTNOTE_NUMBERING_SKIPPED",
    "NOTE_NO_ANSWER_WRITER",
    "NOTE_ANSWER_WRITER_FAILED",
    "NOTE_PROMPT_FAILED",
    "JudgedPolicies",
    "TurnResult",
    "judge_and_publish",
    "run_turn",
]
