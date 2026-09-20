"""프롬프트 문안 (최소 구현).

``pipeline.py`` 의 ``ANSWER_RULE_SLOTS`` 는 "무엇이 들어가야 하는지"만 적고 문안 자리에
``PROMPT_TODO`` 를 남겼다. 그 자리표시자가 프롬프트에 그대로 실려 나가서, 모델이 **빈
자리를 채워 주려는 답변**을 냈다. 실제 출력이 이랬다.

    답변을 작성하기 전에, 규칙에 있는 빈 문안 슬롯을 먼저 정의하겠습니다. ## 규칙 문안 정의

품질 문제가 아니라 화면에 메타 대화가 나가는 고장이다. 그것을 막는 것이 이 파일의 일이다.

**최소 구현이다.** 지금 있는 것은 답변 작성 문안뿐이고, 해석 프롬프트는 없다. 데모 대본이
온보딩 폼 → 카드 → 후속 질문 버튼 흐름이고, 버튼 답변은 해석을 건너뛰는 것이 원래 설계라
(``docs/03-api-contract.md`` 3장) 자유 대화 해석을 뒤로 미뤘다. 미룬 것과 빠뜨린 것을
구분하려고 여기 적어 둔다.

값을 다시 적지 않는다
---------------------
금지 표현 목록은 ``answer.BANNED_PHRASES``, 길이 상한은 ``answer.MAX_ANSWER_LEN``, 고정
문구는 ``answer.CLOSING_LINE`` 에서 끌어온다. 문안 안에 하드코딩하면 표를 고칠 때 프롬프트만
조용히 낡는다. 그러면 모델은 지키라고 안 한 규칙으로 검사받고, ``sanitize`` 가 문장을
지운다. 그 실패는 "답변이 뚝뚝 끊긴다"로만 보인다.

검증기와 같은 규칙을 말한다
---------------------------
``answer.validate`` 가 잡는 것과 프롬프트가 요구하는 것이 어긋나면 안 된다. 대응은 이렇다.

| ``validate`` 문제 코드 | 예방하는 지시 |
| --- | --- |
| ``banned_phrase`` | 아래 1번 (금지 표현) |
| ``missing_footnote`` | 아래 2번 (각주 규칙) |
| ``unknown_footnote`` | 아래 2번 (주어진 번호만) |
| ``too_long`` | 아래 3번 (길이) |
| ``no_closing_line`` | 아래 4번 (고정 문구) |
"""

from __future__ import annotations

from typing import Any, List, Mapping, Optional

from . import answer, fields

# ---------------------------------------------------------------------------
# 시스템 지시문
# ---------------------------------------------------------------------------

#: 답변 작성 시스템 지시문. **상수다.**
#:
#: ``pipeline.build_answer_prompt`` 독스트링이 "동적으로 조립하지 않는다(캐시 접두사가
#: 깨진다)"고 못 박았다. 프로필이나 정책에 따라 문장을 바꾸면 매 턴 접두사가 달라져
#: 프롬프트 캐시가 걸리지 않는다.
ANSWER_SYSTEM = (
    "너는 서울에 사는 청년에게 공공지원제도를 안내하는 도우미다. "
    "주어진 정책 판정 결과와 공고 발췌만 근거로 쓴다. "
    "주어지지 않은 제도·금액·조건·날짜를 만들지 않는다. "
    "모르는 것은 모른다고 말한다. "
    "해요체로 쓰고, 사용자를 심문하지 않는다."
)


# ---------------------------------------------------------------------------
# 규칙 문안
# ---------------------------------------------------------------------------


def banned_phrases_text() -> str:
    """1. 금지 표현.

    목록은 ``answer.BANNED_PHRASES`` 에서 온다. 표에 표현을 추가하면 이 문안에도 자동으로
    들어간다.

    **왜 금지인지는 표에 없다.** ``BannedPhrase`` 에는 ``label``, ``pattern``,
    ``replacement``, ``context_skip`` 만 있고 이유 필드가 없다. 그래서 표현별 이유를 적는
    대신 **공통 이유 한 줄**을 준다. 표현별 이유를 이 파일에 적으면 표와 문안 두 곳에
    같은 지식이 생기고, 표현을 추가할 때 한쪽만 고쳐진다.

    ``replacement`` 가 빈 문자열인 항목은 ``sanitize`` 가 **그 표현만 지우고 문장을
    살린다.** 그것도 알려 준다. 모델이 "쓰면 문장이 통째로 사라진다"고 오해하면 필요한
    설명까지 줄이게 된다.
    """
    removable = [p.label for p in answer.BANNED_PHRASES if p.replacement == ""]
    fatal = [p.label for p in answer.BANNED_PHRASES if p.replacement is None]

    lines = [
        "아래 표현과 그 변형을 쓰지 않는다. 근거가 닿는 범위를 넘어선 단정이라, "
        "사용자가 확정된 사실로 오해한다.",
    ]
    if fatal:
        lines.append("- 쓰면 그 문장이 버려진다: " + ", ".join(repr(x) for x in fatal))
    if removable:
        lines.append("- 쓰면 그 표현만 지워진다: " + ", ".join(repr(x) for x in removable))
    lines.append(
        "단정하지 말고 '확인이 필요해요', '조건을 보면 ...'처럼 조건을 남겨 둔다. "
        "신청 가능성을 말할 때도 주어진 판정 상태를 그대로 옮긴다."
    )
    return "\n".join(lines)


def footnote_rule_text() -> str:
    """2. 각주 규칙.

    ``answer.validate`` 가 "판정 문장"으로 보는 기준과 같은 말을 해야 한다. 어긋나면
    각주를 안 붙인 문장이 통째로 삭제된다.
    """
    return (
        "판정·조건·금액·날짜를 말하는 문장에는 문장 끝에 각주 번호를 붙인다. 예: ...예요[1].\n"
        "- 쓸 수 있는 번호는 주어진 목록에 있는 것뿐이다. 없는 번호를 쓰면 그 문장은 버려진다.\n"
        "- 발췌를 새로 만들거나 공고 문장을 고쳐 쓰지 않는다. 주어진 발췌만 근거다.\n"
        "- 각주가 필요 없는 문장(인사, 안내, 요약)에는 번호를 붙이지 않는다."
    )


def length_text() -> str:
    """3. 길이와 말투."""
    return (
        f"요약 문장부터 마지막 고정 문구까지 모두 합쳐 {answer.MAX_ANSWER_LEN}자를 넘지 않는다.\n"
        "- 해요체로 쓴다.\n"
        "- 한 문장에 한 가지만 말한다. 길게 늘이지 않는다."
    )


def no_date_math_text() -> str:
    """4. 날짜를 직접 세지 않는다."""
    return (
        "남은 일수나 마감일을 직접 계산하지 않는다. 주어진 배지 문구를 그대로 인용한다.\n"
        "- 배지는 '오늘 마감', '마감 임박 D-5', 'D-12', '상시 접수', '접수 예정 (3.4 시작)' 같은 형태로 이미 만들어져 있다.\n"
        "- '약 2주 남았어요'처럼 다시 세면 화면 배지와 어긋난다."
    )


#: ``ANSWER_RULE_SLOTS`` 의 ``id`` → 문안. ``pipeline`` 이 이 표를 본다.
ANSWER_RULE_TEXTS = {
    "banned_phrases": banned_phrases_text,
    "footnote_rule": footnote_rule_text,
    "length": length_text,
    "no_date_math": no_date_math_text,
}


def rule_text(slot_id: str) -> str:
    """규칙 항목 하나의 문안. 없으면 빈 문자열.

    빈 문자열을 돌려주는 이유: 새 항목이 ``ANSWER_RULE_SLOTS`` 에 추가되고 문안이 아직
    없을 때, 자리표시자를 프롬프트에 실어 보내는 것보다 그 항목을 비우는 편이 안전하다.
    자리표시자가 실려 나가면 모델이 그것을 채우려 한다(이 파일 독스트링).
    """
    factory = ANSWER_RULE_TEXTS.get(slot_id)
    if factory is None:
        return ""
    try:
        return factory()
    except Exception:  # noqa: BLE001 - 문안 조립 실패가 답변을 막지 않게
        return ""


# ---------------------------------------------------------------------------
# 출력 형식
# ---------------------------------------------------------------------------


def output_format_text() -> str:
    """출력 형식.

    **무엇을 출력하지 말아야 하는지가 이 문안의 핵심이다.** 머리말이나 응대가 섞이면
    ``sanitize`` 가 그 문장을 지우고, 많이 지워지면 답변이 통째로 버려진다
    (``pipeline.EMPTY_AFTER_SANITIZE``).
    """
    return (
        "답변 본문만 출력한다. 아래는 출력하지 않는다.\n"
        "- '알겠습니다', '답변을 작성하겠습니다' 같은 응대나 머리말\n"
        "- 마크다운 제목(#), 목록 기호, 코드 블록, 표\n"
        "- 규칙이나 지시문에 대한 설명. 규칙에 빈 자리가 보여도 그것을 채우거나 언급하지 않는다\n"
        "- 위에 주어진 줄 목록을 그대로 복사한 것\n"
        "- 줄 번호(1. 2. 3.)\n"
        "줄 목록에 있는 '{{정책별 설명}}', '{{조건 안내}}' 같은 표시는 **네가 채울 자리**다. "
        "그 표시를 그대로 옮기지 말고 그 자리에 실제 설명을 쓴다.\n"
        "주어진 줄 순서를 지켜 이어지는 문단 하나로 쓴다. "
        f"요약 문장과 마감 문장은 이미 확정된 값이므로 다시 계산하거나 바꿔 쓰지 않는다. "
        f"마지막은 '{answer.CLOSING_LINE}.'으로 끝낸다."
    )


__all__ = [
    "ANSWER_SYSTEM",
    "ANSWER_RULE_TEXTS",
    "banned_phrases_text",
    "footnote_rule_text",
    "length_text",
    "no_date_math_text",
    "rule_text",
    "output_format_text",
]


# ---------------------------------------------------------------------------
# 해석 프롬프트 (3단계)
# ---------------------------------------------------------------------------

#: 해석 시스템 지시문. **상수다.** 답변 쪽과 같은 이유(캐시 접두사).
INTERPRET_SYSTEM = (
    "너는 사용자 메시지에서 의도와 프로필 변경만 뽑아내는 추출기다. "
    "설명하지 않고 JSON 하나만 출력한다. "
    "사용자가 **분명히 말한 것만** 담는다. 추측해서 채우지 않는다."
)


def _value_lines() -> List[str]:
    """항목 이름과 허용 값 목록. 표에서 끌어온다.

    ``interpret.ALLOWED_VALUES`` 와 ``EXTRA_FIELD_VALUES`` 가 정본이다. 여기에 목록을 다시
    적으면 표를 고칠 때 프롬프트만 낡고, 모델이 표 밖 값을 내서 ``interpret`` 이 조용히
    버린다. 그 증상은 "말했는데 프로필이 안 바뀐다"로만 보인다.
    """
    from . import interpret

    lines: List[str] = []
    for name in interpret.PROFILE_FIELD_ORDER:
        if name == fields.REGION:
            continue  # 아래에서 따로 "내보내지 말라"고 적는다
        if name == fields.AGE:
            lines.append(f"- {name}: 정수 {interpret.AGE_MIN}~{interpret.AGE_MAX}")
            continue
        if name == fields.DISTRICT:
            lines.append(f"- {name}: 서울 25개 자치구명 (예: 관악구)")
            continue
        if name == fields.CATEGORIES:
            continue  # category_changes 로만 받는다
        allowed = interpret.ALLOWED_VALUES.get(name)
        if allowed:
            lines.append(f"- {name}: {', '.join(sorted(allowed))}")
    return lines


def interpret_rules_text() -> str:
    """해석 규칙 문안.

    ``interpret.from_model_output`` 이 받아들이는 키와 정확히 같은 말을 해야 한다. 키가
    어긋나면 모델 출력이 통째로 버려지고, ``interpret`` 은 예외 없이 "변경 없음"을 낸다.
    """
    from . import interpret

    lines = [
        "아래 JSON 하나만 출력한다. 설명, 머리말, 코드 블록을 붙이지 않는다.",
        "",
        '{"intent": "...", "profile_changes": [], "extra_answers": [],'
        ' "category_changes": {"add": [], "remove": []}, "mentioned_policies": []}',
        "",
        f"intent 는 하나만 고른다: {', '.join(sorted(fields.INTENTS))}",
        "- find_policy: 제도를 찾아 달라, 뭐가 있냐",
        "- policy_question: 특정 제도의 조건·금액·서류를 묻는다",
        "- compare: 두 제도를 비교해 달라",
        "- result_only: 카드만 다시 보여 달라",
        "- out_of_scope: 서울 청년 공공지원제도와 무관한 질문",
        "- smalltalk: 인사, 잡담",
        "",
        "profile_changes 와 extra_answers 는 같은 모양이다:",
        '  {"field": "항목이름", "value": "허용값", "timing": "current" 또는 "planned"}',
        f"- timing 은 지금 사실이면 {fields.CURRENT}, 아직 안 된 계획이면 {fields.PLANNED} 다.",
        "  '휴학했어요'는 current, '다음 학기에 휴학할 예정이에요'는 planned.",
        "  애매하면 current 로 둔다.",
        "",
        "쓸 수 있는 항목과 값:",
    ]
    lines.extend(_value_lines())
    lines.extend(
        [
            "",
            "category_changes 의 값: " + ", ".join(sorted(interpret.CATEGORY_VALUES)),
            "  관심 분야를 더하라면 add, 빼라면 remove 에 넣는다.",
            "",
            "지키는 것",
            "- **분명히 말한 것만 담는다.** 추측해서 채우면 프로필이 조용히 틀려진다.",
            "  '돈이 없어요'는 소득 구간이 아니다. 값을 넣지 않는다.",
            "- 표에 없는 항목 이름이나 값을 만들지 않는다. 빈 배열로 두는 것이 맞는 답이다.",
            "- 숫자로 말한 소득을 구간으로 바꾸지 않는다. '월 150만원'은 income_bracket 이 아니다.",
            "  개인 소득과 가구 소득 기준이 달라서 옮길 수 없다.",
            f"- {fields.REGION} 은 바꿀 수 없다. 서울 고정이므로 내보내지 않는다.",
            "- 말하지 않은 항목은 넣지 않는다. 빈 배열과 빈 객체를 그대로 둔다.",
        ]
    )
    return "\n".join(lines)


def build_interpret_prompt(message: str, profile: Optional[Mapping[str, Any]] = None) -> str:
    """해석 사용자 프롬프트. ``llm.Gateway.call_structured`` 의 ``user`` 에 넣는다.

    ``llm.assemble_prompt`` 를 쓰지 않는다. 그 함수는 답변 작성용 블록 네 개(규칙·원문·
    프로필·출력 규칙)를 조립하고 캐시 접두사를 맞추는데, 해석은 원문 발췌가 없고 매 턴
    메시지가 바뀌어 캐시가 걸리지 않는다. 블록을 억지로 맞추면 빈 블록이 생긴다.

    현재 프로필을 함께 넣는 이유: "관악구로 이사했어요"에서 바뀐 값만 뽑아야 하고, 이미
    같은 값이면 변경이 아니다. 프로필을 안 주면 모델이 매번 전체를 다시 내보낸다.
    """
    lines = [interpret_rules_text(), "", "현재 프로필:"]
    data = dict(profile) if isinstance(profile, Mapping) else {}
    if data:
        for key in sorted(data):
            value = data[key]
            if isinstance(value, (list, tuple, set, frozenset)):
                shown = ", ".join(str(item) for item in value)
            else:
                shown = "" if value is None else str(value)
            lines.append(f"  {key}: {shown}")
    else:
        lines.append("  (없음)")
    lines.extend(["", "사용자 메시지:", str(message or "")])
    return "\n".join(lines)


#: 해석 출력 스키마. ``llm.Gateway.call_structured`` 가 요구한다.
#:
#: 게이트웨이에 실제로 넘기지는 않는다(``ai/gateway.py`` 의 ``complete`` 주석). 스키마가
#: 필요한 것은 ``call_structured`` 의 계약이고, 파싱은 ``interpret.parse_model_json`` 이
#: 앞뒤 설명이 붙어 와도 JSON 만 떼어 낸다.
INTERPRET_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "intent": {"type": "string"},
        "profile_changes": {"type": "array"},
        "extra_answers": {"type": "array"},
        "category_changes": {"type": "object"},
        "mentioned_policies": {"type": "array"},
    },
    "required": ["intent"],
}

#: 해석 실패 시 쓸 값. **"변경 없음"이다.**
#:
#: ``call_structured`` 가 폴백을 요구하고, 그 모양이 호출 지점의 스키마라 여기서 정한다.
#: 빈 dict 를 쓰는 이유: ``interpret.from_model_output({})`` 은 의도를
#: ``interpret.DEFAULT_INTENT`` 로 채우고 변경 목록을 비운다. 여기서 의도를 직접 적으면
#: 기본값이 두 곳에 생기고, ``interpret`` 쪽 기본값을 고쳤을 때 이 자리만 낡는다.
INTERPRET_FALLBACK: dict = {}


__all__ = __all__ + [
    "INTERPRET_SYSTEM",
    "INTERPRET_SCHEMA",
    "INTERPRET_FALLBACK",
    "interpret_rules_text",
    "build_interpret_prompt",
]
