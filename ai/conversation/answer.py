"""답변 검증과 정리 (ai/conversation/README.md 6장).

모델이 쓴 답변을 내보내기 **전에** 코드가 검사한다. 여기가 P0 "원문 각주"를 지키는
마지막 관문이다. 완료 기준(README 11장)의 네 줄 — 금지 표현 0건, 각주 없는 판정 문장
0건, 600자 이내, 고정 문구 — 을 전부 이 모듈이 책임진다.

왜 프롬프트가 아니라 코드인가
-----------------------------
프롬프트로 "금지 표현을 쓰지 마세요"라고 부탁하는 것은 확률이고, 여기서 한 번 새면
사용자에게 "무조건 받을 수 있어요"가 그대로 나간다. 설계서가 모델에게 맡기지 않기로
정한 항목(셈, 날짜, 일관된 판정)은 답변 단계에서도 코드가 잡는다.
``ai/citation/verify.py`` 와 같은 자리다. 그쪽은 발췌가 원문에 있는지를, 이쪽은 판정
문장에 각주가 붙어 있는지를 본다.

이 모듈이 하는 일
-----------------
1. ``validate``  — 내보낼 수 있는 답변인지 판정하고 문제 목록을 돌려준다.
2. ``sanitize``  — 고칠 수 있는 문제는 고치고, 못 고치는 문장은 뺀다.
3. ``build_skeleton`` — 코드가 확정할 수 있는 문장(요약, 마감 강조, 고정 문구)만 만든다.

``unknown_footnote`` 를 반드시 잡아야 하는 이유
-----------------------------------------------
AI B 의 인용 검증이 실패하면 그 조건은 ``unknown`` 으로 내려가고 발췌가 비워진다
(docs/03-api-contract.md 4-3). 그러면 각주 목록에서 그 번호가 빠지는데, 모델이 쓴 본문은
이미 ``[2]`` 를 쓴 상태다. 화면에는 연결되지 않는 각주 번호가 남고, 사용자는 근거가 있는
줄 알고 읽는다. 이게 가장 위험한 실패라서 별도 코드로 잡는다.

알려진 한계 (오탐·미탐)
-----------------------
- 금지 표현은 어미 목록으로 잡는다. "대상이라고 보여요", "받으실 수 있으실 거예요" 처럼
  목록에 없는 변형은 통과한다(미탐). 목록을 더 넓히면 "대상이 아니에요", "받을 수 있는지
  확인이 필요해요" 같은 정상 문장을 잡기 시작해서 여기서 멈췄다.
- ``100%`` 는 "기준 중위소득 100% 이하"가 정상 표현이라 소득 문맥이면 통과시킨다.
  "기준 중위소득 100% 이하라 100% 가능해요" 처럼 한 문장에 둘이 섞이면 미탐이다.
- ``보장`` 은 "국민기초생활보장" 같은 제도명을 피하려고 앞 글자(생활·사회·기초)와 "보장법"을
  제외한다. 제도명에 다른 조합이 들어오면 오탐이 날 수 있다.
- 문장 분리는 마침표·물음표·느낌표·줄바꿈 기준이다. "2026. 3. 1." 같은 날짜와 "3.5%" 는
  따로 막았지만, 목차 번호("1. 첫째")는 여전히 한 문장으로 잘린다.
- ``needs_footnote`` 는 낱말 신호로 판단한다. "조건", "대상" 이 비유로 쓰인 문장은 각주를
  요구해서 오탐이 되고, 신호 낱말 없이 판정을 암시하는 문장("그럼 신청하시면 돼요")은
  미탐이다. 오탐은 문장이 사라지는 쪽이라 손해가 작고, 미탐은 근거 없는 문장이 나가는
  쪽이라 손해가 크다. 그래서 신호는 넓게, 예외는 좁게 두었다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

# ---------------------------------------------------------------------------
# 1. 상수 (README 6장 "말투와 길이", "금지 표현", 5번 고정 문구)
# ---------------------------------------------------------------------------

# 요약부터 고정 문구까지 합친 길이 상한 (README 6장)
MAX_ANSWER_LEN = 600

# 고정 문구. 답변 마지막 줄로 반드시 들어간다 (README 6장 5번)
CLOSING_LINE = "최종 신청 전 공식 공고에서 다시 확인하세요"

# 결과 없음일 때 쓰는 세 줄 (README 6장 "결과 없음일 때")
NO_RESULT_LINE = "지금 조건으로는 맞는 제도를 찾지 못했어요"
BROADEN_LINE = "관심 분야를 넓히거나 소득 구간을 알려주시면 다시 찾아볼게요"
DATA_SCOPE_LINE = "지금 담고 있는 데이터는 서울 지역 청년 정책 중심이에요"

# 모델이 채울 자리 표시 (README 6장 2번·3번은 모델이 쓴다)
SLOT_POLICY_DETAIL = "{{정책별 설명}}"
SLOT_CONDITION_NOTE = "{{조건 안내}}"
# 각주 자리. 대괄호 안이 숫자가 아니면 각주로 세지 않으므로(2장), 모델이 번호를 채우지
# 않으면 그 문장은 missing_footnote 로 잡혀 sanitize 에서 빠진다. 안전한 실패다.
SLOT_FOOTNOTE = "[각주]"

# 정책별 설명 최대 개수 (README 6장 2번)
MAX_POLICY_DETAILS = 5

# 판정 상태 (docs/03-api-contract.md 4장)
LIKELY = "likely"
CHECK = "check"
UNLIKELY = "unlikely"

# 분류하지 못한 상태. 아래 `_normalize_status` 가 돌려준다.
UNCLASSIFIED = ""

# 받는 값은 **계약에 있는 것만**이다 (docs/03-api-contract.md 4장).
# 화면 문구(docs/01-glossary-profile.md 4장)가 status 자리에 실려 오는 경우도 함께 받는다.
# 프론트가 `status_label` 과 `status` 를 섞어 넘기는 실수가 실제로 나올 수 있어서다.
#
# 창작한 값을 넣지 않는다. 전에는 "가능", "확인 필요" 같은 문서에 없는 한국어를 받았는데,
# 그건 어디서도 오지 않는 값이라 방어가 되지 않으면서 실제 값(화면 문구)은 놓쳤다.
_STATUS_ALIASES: Dict[str, str] = {
    "likely": LIKELY,
    "신청 가능성이 높아요": LIKELY,
    "check": CHECK,
    "확인이 필요해요": CHECK,
    "unlikely": UNLIKELY,
    "어려울 수 있어요": UNLIKELY,
}


@dataclass(frozen=True)
class BannedPhrase:
    """금지 표현 하나 (README 6장 "금지 표현").

    label        지표·기록에 쓰는 이름
    pattern      본문에서 찾을 정규식
    replacement  None 이면 문장을 통째로 뺀다. 문자열이면 그것으로 바꿔 살린다
    context_skip 문장 전체에 이 정규식이 걸리면 금지로 보지 않는다 (정상 표현 보호)

    replacement 를 나누는 기준은 하나다. **수식어인가, 판정인가.**
    "확실히", "무조건", "100%", "걱정 마세요" 는 빼도 문장의 정보가 남는다(수식어).
    "대상입니다", "받을 수 있어요", "보장" 은 빼고 나면 판정이 사라지는데, 코드가 판정을
    다시 쓸 수는 없다(README 6장: 답변에서 상태를 새로 판단하지 않는다). 그래서 문장을
    버린다. 설명 한 문장을 잃는 편이 틀린 단정을 내보내는 것보다 낫다.
    """

    label: str
    pattern: re.Pattern[str]
    replacement: Optional[str] = None
    context_skip: Optional[re.Pattern[str]] = None


# 어미 변화를 잡는다. "대상입니다"만 막으면 "대상이에요"가 통과한다.
# 종결 어미를 붙여서 단정할 때만 잡고, "대상이 아니에요" / "대상인지 확인이 필요해요" 는
# 어미가 이어지지 않으므로 걸리지 않는다.
_RE_TARGET = re.compile(r"대상\s*(?:입니다|이에요|이예요|이세요|이십니다|이야|임니다|임)")

# "받을 수 있어요" 계열. "받을 수 있는지", "받을 수 있을지" 는 확인 요청이라 통과시킨다.
_RE_CAN_GET = re.compile(
    r"받(?:을|으실)\s*수\s*있(?:어요|으세요|습니다|네요|죠|겠어요|다(?![가-힣]))"
)

# 수식어 계열. "확실하다"까지만 잡고 "확신" 같은 낱말로 넓히지 않는다.
_RE_SURELY = re.compile(r"확실히|확실하게|확실해요|확실합니다")
_RE_UNCONDITIONAL = re.compile(r"무조건")

# "100%" 는 "기준 중위소득 100% 이하" 가 정상 표현이라 뒤에 비교어가 붙으면 통과시킨다.
_RE_HUNDRED = re.compile(r"100\s*%(?!\s*(?:이하|이상|미만|초과|까지|기준))")
_RE_INCOME_CONTEXT = re.compile(r"중위\s*소득|소득\s*기준")

# "보장". 제도명("국민기초생활보장", "사회보장", "보장법")은 앞뒤를 보고 제외한다.
_RE_GUARANTEE = re.compile(r"(?<!생활)(?<!사회)(?<!기초)보장(?!법|제도|기관|성)")

_RE_NO_WORRY = re.compile(
    r"걱정\s*(?:마세요|말아요|하지\s*마세요|하지\s*않으셔도\s*돼요|안\s*하셔도\s*돼요)"
)

BANNED_PHRASES: Tuple[BannedPhrase, ...] = (
    BannedPhrase("대상입니다", _RE_TARGET),
    BannedPhrase("받을 수 있어요", _RE_CAN_GET),
    BannedPhrase("보장", _RE_GUARANTEE),
    BannedPhrase("확실히", _RE_SURELY, replacement=""),
    BannedPhrase("무조건", _RE_UNCONDITIONAL, replacement=""),
    BannedPhrase("100%", _RE_HUNDRED, replacement="", context_skip=_RE_INCOME_CONTEXT),
    BannedPhrase("걱정 마세요", _RE_NO_WORRY, replacement=""),
)


# ---------------------------------------------------------------------------
# 2. 각주 번호 추출 (docs/03-api-contract.md 4-1 footnote_id, 5장 footnotes 이벤트)
# ---------------------------------------------------------------------------

# 대괄호 안이 숫자인 것만 각주다. "[각주]", "[확인]" 은 각주가 아니다.
# 번호는 서버가 부여한 값이므로 여기서는 읽기만 한다 (README 6장 근거 규칙).
_RE_FOOTNOTE = re.compile(r"\[(\d{1,3})\]")


def footnote_numbers(text: str) -> Tuple[int, ...]:
    """본문에 쓰인 각주 번호를 나온 순서대로 돌려준다. 중복은 한 번만.

    "[1][2]" 처럼 붙여 쓴 경우도 두 개로 읽는다.
    """
    seen: Set[int] = set()
    numbers: List[int] = []
    for match in _RE_FOOTNOTE.finditer(text or ""):
        number = int(match.group(1))
        if number not in seen:
            seen.add(number)
            numbers.append(number)
    return tuple(numbers)


def has_footnote(sentence: str) -> bool:
    """문장에 각주가 하나라도 붙었는지."""
    return bool(_RE_FOOTNOTE.search(sentence or ""))


def known_footnote_ids(footnotes: Any) -> frozenset[int]:
    """각주 목록에서 실제 존재하는 번호 집합을 뽑는다.

    호출 쪽이 넘기는 모양이 통합 전까지 하나로 정해지지 않아 넓게 받는다
    (docs/03-api-contract.md 5장 ``footnotes`` 이벤트: id, policy_id, excerpt, agency,
    checked_at, source_url).

    받는 모양
        - 항목 dict 목록: ``footnote_id`` 또는 ``id`` 키를 읽는다
        - 번호만 있는 집합·목록: ``{1, 2}``, ``[1, "2"]``
        - 번호를 키로 쓰는 dict: ``{1: {...}}``
        - None 또는 빈 값: 빈 집합

    숫자로 읽을 수 없는 값은 조용히 버린다. 각주 하나의 모양이 이상해서 응답 전체를
    실패시키는 것은 손해가 더 크다(``ai/conversation/fields.py`` 의 같은 판단).
    """
    if footnotes is None:
        return frozenset()

    if isinstance(footnotes, Mapping):
        # 이벤트 본문을 그대로 넘긴 경우를 먼저 벗긴다.
        # docs/03-api-contract.md 5장의 `footnotes` 이벤트는 {"footnotes": [...]} 모양이고,
        # 서버가 그걸 그대로 넘기는 것이 가장 자연스러운 호출 방식이다. 이걸 못 벗기면
        # 빈 집합이 되어 **답변의 모든 판정 문장이 조용히 삭제된다.** 오류도 안 남는다.
        for wrapper in ("footnotes", "items", "data"):
            inner = footnotes.get(wrapper)
            if inner is not None:
                return known_footnote_ids(inner)
        # 항목 dict 하나가 그대로 들어온 경우와 번호를 키로 쓴 경우를 구분한다.
        if "footnote_id" in footnotes or "id" in footnotes:
            return _collect_ids([footnotes])
        return _collect_ids(footnotes.keys())

    if isinstance(footnotes, (str, bytes, int)):
        return _collect_ids([footnotes])

    if isinstance(footnotes, Iterable):
        return _collect_ids(footnotes)

    return frozenset()


def _collect_ids(items: Iterable[Any]) -> frozenset[int]:
    """각주 항목 또는 번호에서 정수 번호만 모은다."""
    ids: Set[int] = set()
    for item in items:
        value: Any = item
        if isinstance(item, Mapping):
            value = item.get("footnote_id", item.get("id"))
        number = _as_int(value)
        if number is not None:
            ids.add(number)
    return frozenset(ids)


def _as_int(value: Any) -> Optional[int]:
    """정수로 읽을 수 있으면 정수, 아니면 None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return int(text)
    return None


# ---------------------------------------------------------------------------
# 3. 문장 분리와 근거 필요 판별 (README 6장 "근거 규칙")
# ---------------------------------------------------------------------------

# 문장 끝 후보: 종결 부호 + 뒤따르는 공백, 또는 줄바꿈.
_RE_BREAK = re.compile(r"[.!?…]+[ \t]*|\n+")

# "2026. 3. 1." 처럼 마침표로 끊은 날짜. 여기서 문장을 자르면 뒤에 오는 마감 문장이
# 각주 없는 문장으로 오인된다.
_RE_DATE_TAIL = re.compile(r"\d{1,4}\s*\.\s*\d{1,2}\s*\.\s*\d{1,2}\s*\.\s*$")


def split_sentences(text: str) -> Tuple[str, ...]:
    """한국어 답변을 문장 단위로 나눈다.

    마침표만으로 나누면 "3.5%" 와 "2026. 3. 1." 에서 깨진다. 그래서 두 가지를 막았다.
      - 마침표 앞뒤가 모두 숫자면 소수점으로 본다 (3.5)
      - 지금까지 모은 조각이 날짜 꼴로 끝나면 문장 끝으로 보지 않는다 (2026. 3. 1.)
    종결 부호는 문장에 붙여서 돌려준다. ``sanitize`` 가 남은 문장을 다시 이어 붙일 때
    부호가 사라지면 안 되기 때문이다.
    """
    if not text:
        return ()

    sentences: List[str] = []
    start = 0
    for match in _RE_BREAK.finditer(text):
        token = match.group(0)
        if "\n" in token:
            boundary = True
        else:
            punct = token.strip()
            before = text[match.start() - 1] if match.start() > 0 else ""
            after = text[match.end()] if match.end() < len(text) else ""
            # 소수점: 마침표뿐이고 앞뒤가 숫자
            decimal = set(punct) <= {"."} and before.isdigit() and after.isdigit()
            # 날짜: 조각이 "2026. 3. 1." 꼴로 끝남
            date_like = set(punct) <= {"."} and bool(
                _RE_DATE_TAIL.search(text[start : match.end()])
            )
            boundary = not (decimal or date_like)

        if not boundary:
            continue

        piece = text[start : match.end()].strip()
        if piece:
            sentences.append(piece)
        start = match.end()

    tail = text[start:].strip()
    if tail:
        sentences.append(tail)
    return tuple(sentences)


# 고정 문구 검사용. 목록 위치에 의존하지 않도록 이름을 붙여 따로 둔다.
_RE_CLOSING = re.compile(r"최종\s*신청\s*전\s*공식\s*공고에서\s*다시\s*확인")

# 근거가 필요한 신호 (README 6장: 판정·조건·금액·날짜가 든 문장에는 반드시 각주)
_EVIDENCE_SIGNALS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    # 판정 표현
    ("판정", re.compile(r"신청\s*가능성|가능해요|가능합니다|확인이\s*필요|어려울\s*수\s*있")),
    ("판정", re.compile(r"대상|조건|충족|해당")),
    # 금액: 숫자 + 원/만원
    ("금액", re.compile(r"\d[\d,]*\s*(?:만원|원)")),
    # 날짜·기간: 숫자 + 월/일/개월/학기, D-n, 마감
    ("기간", re.compile(r"\d+\s*(?:월|일|개월|학기)|D-\s*\d+|마감")),
    # 자격 요건: 비교어와 소득 기준
    ("요건", re.compile(r"이상|이하|미만|초과|기준\s*중위소득|\d\s*%")),
)

# 각주가 필요 없는 문장. 좁게 둔다. 넓히면 판정 문장이 여기로 새어 나간다.
#
# 왜 이 넷만 예외인가
#   1) 요약 문장은 표시 중인 카드 개수를 센 결과다. 공고에서 나온 값이 아니라 코드가
#      센 값이므로 연결할 발췌가 애초에 없다 (README 6장 1번).
#   2) 고정 문구는 안내지 판정이 아니다 (README 6장 5번).
#   3) 결과 없음·조건 넓히기 안내는 우리 데이터 범위를 말하는 문장이다. 공고와 무관하다
#      (README 6장 "결과 없음일 때").
#   4) 범위 밖 안내는 판정 자체를 하지 않는 경로다 (README 3장 의도별 동작).
# 넷 다 "공고 원문에 대응하는 발췌가 존재할 수 없는 문장" 이라는 공통점이 있다. 각주를
# 요구하면 모델은 없는 발췌를 만들거나 엉뚱한 번호를 붙인다. 그게 더 나쁘다.
_EXEMPT_PATTERNS: Tuple[Tuple[str, re.Pattern[str]], ...] = (
    # 1) 개수만 말하는 요약 문장
    ("summary", re.compile(r"제도\s*\d+\s*개.*(?:찾았어요|찾았습니다|있어요)")),
    # 2) 고정 문구
    ("closing", _RE_CLOSING),
    # 3) 결과 없음 + 조건 넓히기 + 데이터 범위.
    #    **코드가 만든 고정 문구와 거의 일치할 때만** 면제한다.
    #    전에는 "알려주시면", "넓히" 같은 조각이 문장 어디에든 있으면 면제했는데, 그러면
    #    "소득 구간을 알려주시면 월 20만원 지원 대상인지 확인해요" 처럼 금액과 판정이 든
    #    문장이 각주 없이 통과했다. P0(각주 없는 판정 문장 0건)가 새는 경로였다.
    ("no_result", re.compile(rf"^{re.escape(NO_RESULT_LINE)}[.!]?$")),
    ("broaden", re.compile(rf"^{re.escape(BROADEN_LINE)}[.!]?$")),
    ("scope", re.compile(rf"^{re.escape(DATA_SCOPE_LINE)}[.!]?$")),
    # 4) 범위 밖·잡담 안내
    ("out_of_scope", re.compile(r"도와드리기\s*어려워요|도와드릴\s*수\s*없어요")),
)


def exempt_reason(sentence: str) -> Optional[str]:
    """각주를 요구하지 않는 문장이면 그 이유 코드를, 아니면 None."""
    for code, pattern in _EXEMPT_PATTERNS:
        if pattern.search(sentence):
            return code
    return None


def needs_footnote(sentence: str) -> bool:
    """이 문장이 각주를 필수로 요구하는지 (README 6장 근거 규칙).

    예외 문장을 먼저 걸러낸 뒤 신호를 본다. 순서가 중요하다. 요약 문장에는 "확인이 필요한
    제도" 가 들어가고, 조건 넓히기 안내에는 "조건" 이 들어간다. 신호를 먼저 보면 둘 다
    각주를 요구하게 되고, 코드가 만든 문장이 코드 검사에 걸리는 모순이 생긴다.
    """
    if not sentence or not sentence.strip():
        return False
    if exempt_reason(sentence) is not None:
        return False
    return any(pattern.search(sentence) for _, pattern in _EVIDENCE_SIGNALS)


def evidence_signals(sentence: str) -> Tuple[str, ...]:
    """어떤 신호 때문에 각주가 필요한지. 기록과 디버깅용."""
    if exempt_reason(sentence) is not None:
        return ()
    found: List[str] = []
    for label, pattern in _EVIDENCE_SIGNALS:
        if pattern.search(sentence) and label not in found:
            found.append(label)
    return tuple(found)


def find_banned(sentence: str) -> Tuple[BannedPhrase, ...]:
    """문장에서 걸린 금지 표현 목록 (README 6장 "금지 표현")."""
    hits: List[BannedPhrase] = []
    for banned in BANNED_PHRASES:
        if banned.context_skip is not None and banned.context_skip.search(sentence):
            continue
        if banned.pattern.search(sentence):
            hits.append(banned)
    return tuple(hits)


def has_closing_line(text: str) -> bool:
    """고정 문구가 들어 있는지. 문장 부호와 공백 차이는 무시한다.

    전에는 `_EXEMPT_PATTERNS[1][1]` 로 목록 위치에 의존했다. 누가 목록 맨 앞에 예외를
    하나 추가하면 이 함수가 엉뚱한 패턴을 쓰게 되고, **P0인 고정 문구 검사가 조용히
    깨진다.** `validate`, `sanitize`, `_trim` 이 모두 이 함수를 쓰므로 한 곳이 깨지면 넷이 깨진다.
    """
    return bool(_RE_CLOSING.search(text or ""))


# ---------------------------------------------------------------------------
# 4. 검증 (README 11장 완료 기준)
# ---------------------------------------------------------------------------

# 문제 코드
TOO_LONG = "too_long"
BANNED = "banned_phrase"
MISSING_FOOTNOTE = "missing_footnote"
UNKNOWN_FOOTNOTE = "unknown_footnote"
NO_CLOSING_LINE = "no_closing_line"


@dataclass(frozen=True)
class Problem:
    """검증에서 걸린 문제 하나.

    code     문제 코드
    sentence 문제가 된 문장. 길이·고정 문구 문제처럼 문장이 특정되지 않으면 빈 문자열
    detail   지표와 기록에 쓰는 보충 설명 (금지 표현 이름, 문제 각주 번호 등)
    """

    code: str
    sentence: str = ""
    detail: str = ""


@dataclass(frozen=True)
class AnswerCheck:
    """검증 결과. ``ai/citation/verify.py`` 의 VerifyResult 와 같은 자리다.

    ok                   내보낼 수 있는지
    problems             문제 목록. 빈 튜플이면 통과
    length               본문 길이 (README 6장 600자 기준)
    sentence_count       문장 수
    evidence_sentences   각주가 필요한 문장 수
    cited_sentences      각주가 필요하고 실제로 붙은 문장 수
    banned_hits          금지 표현이 걸린 횟수 (지표: 금지 표현 0건)
    used_footnotes       본문이 쓴 각주 번호
    unknown_footnotes    각주 목록에 없는데 본문이 쓴 번호
    """

    ok: bool
    problems: Tuple[Problem, ...] = ()
    length: int = 0
    sentence_count: int = 0
    evidence_sentences: int = 0
    cited_sentences: int = 0
    banned_hits: int = 0
    used_footnotes: Tuple[int, ...] = ()
    unknown_footnotes: Tuple[int, ...] = ()

    def codes(self) -> Tuple[str, ...]:
        """걸린 문제 코드만. 지표 집계용."""
        return tuple(problem.code for problem in self.problems)


def validate(
    answer_text: str,
    footnotes: Any = None,
    *,
    max_len: int = MAX_ANSWER_LEN,
) -> AnswerCheck:
    """내보낼 답변을 검사한다 (README 6장, 11장 완료 기준).

    ``footnotes`` 는 실제 존재하는 각주 목록이다(docs/03-api-contract.md 5장). 번호 집합만
    넘겨도 동작한다. ``known_footnote_ids`` 를 보라.

    문제 코드
        too_long          600자 초과
        banned_phrase     금지 표현
        missing_footnote  각주가 필요한 문장에 각주가 없음
        unknown_footnote  본문이 쓴 번호가 각주 목록에 없음
        no_closing_line   고정 문구 없음
    """
    text = answer_text or ""
    known = known_footnote_ids(footnotes)
    sentences = split_sentences(text)
    used = footnote_numbers(text)

    problems: List[Problem] = []
    evidence_count = 0
    cited_count = 0
    banned_hits = 0

    if len(text) > max_len:
        problems.append(
            Problem(TOO_LONG, "", f"{len(text)}자 / 상한 {max_len}자")
        )

    for sentence in sentences:
        for banned in find_banned(sentence):
            banned_hits += 1
            problems.append(Problem(BANNED, sentence, banned.label))

        numbers = footnote_numbers(sentence)
        unknown_in_sentence = [n for n in numbers if n not in known]
        if unknown_in_sentence:
            problems.append(
                Problem(
                    UNKNOWN_FOOTNOTE,
                    sentence,
                    ", ".join(f"[{n}]" for n in unknown_in_sentence),
                )
            )

        if needs_footnote(sentence):
            evidence_count += 1
            # 존재하지 않는 번호는 근거로 세지 않는다. 화면에서 연결되지 않기 때문이다.
            valid = [n for n in numbers if n in known]
            if valid:
                cited_count += 1
            else:
                problems.append(
                    Problem(
                        MISSING_FOOTNOTE,
                        sentence,
                        ",".join(label for label in evidence_signals(sentence)),
                    )
                )

    if not has_closing_line(text):
        problems.append(Problem(NO_CLOSING_LINE, "", CLOSING_LINE))

    return AnswerCheck(
        ok=not problems,
        problems=tuple(problems),
        length=len(text),
        sentence_count=len(sentences),
        evidence_sentences=evidence_count,
        cited_sentences=cited_count,
        banned_hits=banned_hits,
        used_footnotes=used,
        unknown_footnotes=tuple(n for n in used if n not in known),
    )


# ---------------------------------------------------------------------------
# 5. 정리 (검증 실패를 버리지 않고 고친다)
# ---------------------------------------------------------------------------

# 문장을 뺀 뒤 남는 자잘한 흔적을 치운다: 겹공백, 문장 앞에 남은 조사 없는 부호.
_RE_MULTISPACE = re.compile(r"[ \t]{2,}")
_RE_LEADING_PUNCT = re.compile(r"^[,·\s]+")

# 수식어를 뺀 뒤 이만큼도 안 남으면 문장으로 볼 수 없다.
_MIN_SENTENCE_LEN = 4


@dataclass(frozen=True)
class Removal:
    """정리 기록 하나. 지표용이며 사용자 화면에 쓰지 않는다.

    ``ai/citation/verify.py`` 의 제거 기록과 같은 성격이다.
    """

    code: str
    sentence: str
    detail: str = ""


@dataclass(frozen=True)
class SanitizeResult:
    """정리 결과.

    text             정리된 본문. 이 값을 내보낸다
    removals         무엇을 왜 뺐는지 (지표)
    original_length  들어온 본문 길이
    final_length     정리 후 길이
    closing_added    고정 문구를 붙였는지
    truncated        길이 때문에 문장을 뺐는지
    """

    text: str
    removals: Tuple[Removal, ...] = ()
    original_length: int = 0
    final_length: int = 0
    closing_added: bool = False
    truncated: bool = False


def sanitize(answer_text: str, footnotes: Any = None) -> SanitizeResult:
    """고칠 수 있는 문제는 고치고, 못 고치는 문장은 뺀다.

    검증 실패를 그냥 버리면 사용자에게 설명이 아예 나가지 않는다. 카드는 이미 화면에
    있으니(docs/03-api-contract.md 9장) 설명을 통째로 날리는 것보다 문제 문장만 빼고
    나머지를 내보내는 편이 낫다.

    순서
        1. 존재하지 않는 각주를 참조하는 문장 제거
        2. 금지 표현 처리 (수식어는 삭제, 단정은 문장 제거)
        3. 각주 없는 판정 문장 제거
        4. 고정 문구 보장
        5. 600자로 줄이기

    1번에서 번호만 지우지 않고 문장을 버리는 이유
        번호만 지우면 근거 없는 판정 문장이 남는다. 그건 P0 위반이고(README 6장, 11장),
        각주가 빠진 원인은 애초에 인용 검증 실패라 그 문장의 판정 자체를 더 이상 믿을 수
        없다(docs/03-api-contract.md 4-3). 문장을 지우는 쪽이 정직하다.

    3번도 같은 이유로 문장을 버린다. 코드가 각주를 새로 붙일 수는 없다. 각주는 규칙 엔진과
    AI B 가 넘긴 발췌에만 연결하고, 새 발췌를 만들지 않는다(README 6장 근거 규칙).
    """
    original = answer_text or ""
    known = known_footnote_ids(footnotes)
    removals: List[Removal] = []

    kept: List[str] = []
    for sentence in split_sentences(original):
        # 1. 없는 각주를 참조하는 문장
        unknown = [n for n in footnote_numbers(sentence) if n not in known]
        if unknown:
            removals.append(
                Removal(
                    UNKNOWN_FOOTNOTE,
                    sentence,
                    ", ".join(f"[{n}]" for n in unknown),
                )
            )
            continue

        # 2. 금지 표현
        current = sentence
        dropped = False
        for banned in find_banned(current):
            if banned.replacement is None:
                removals.append(Removal(BANNED, sentence, banned.label))
                dropped = True
                break
            current = _tidy(banned.pattern.sub(banned.replacement, current))
            removals.append(
                Removal("banned_phrase_rewritten", sentence, banned.label)
            )
        if dropped:
            continue

        # 수식어를 빼고 남은 것이 없거나, 빼도 여전히 금지 표현이 남으면 문장을 버린다.
        if len(_strip_punct(current)) < _MIN_SENTENCE_LEN or find_banned(current):
            removals.append(Removal(BANNED, sentence, "정리 후에도 남음"))
            continue

        # 3. 각주 없는 판정 문장
        if needs_footnote(current) and not any(
            n in known for n in footnote_numbers(current)
        ):
            removals.append(
                Removal(
                    MISSING_FOOTNOTE,
                    sentence,
                    ",".join(evidence_signals(current)),
                )
            )
            continue

        kept.append(current)

    # 4. 고정 문구
    closing_added = False
    if not any(has_closing_line(sentence) for sentence in kept):
        kept.append(CLOSING_LINE + ".")
        closing_added = True

    # 5. 길이 줄이기
    kept, truncated, length_removals = _trim(kept, MAX_ANSWER_LEN)
    removals.extend(length_removals)

    text = _tidy(" ".join(kept))
    return SanitizeResult(
        text=text,
        removals=tuple(removals),
        original_length=len(original),
        final_length=len(text),
        closing_added=closing_added,
        truncated=truncated,
    )


def _trim(
    sentences: Sequence[str], max_len: int
) -> Tuple[List[str], bool, List[Removal]]:
    """600자를 넘으면 뒤에서부터 문장 단위로 줄인다 (README 6장).

    요약 문장과 고정 문구는 마지막까지 남긴다. 요약은 결론이고 고정 문구는 P0 안내라,
    둘 중 하나가 빠지면 답변이 성립하지 않는다. 중간 설명은 카드에 같은 내용이 있어
    빠져도 정보가 사라지지 않는다.

    글자 수를 중간에서 자르지 않는 이유는 문장이 잘리면 각주 번호나 조건이 반쯤 남아
    오히려 틀린 안내가 되기 때문이다.
    """
    kept = list(sentences)
    removals: List[Removal] = []
    truncated = False

    def total(items: Sequence[str]) -> int:
        return len(" ".join(items))

    protected: Set[int] = set()
    for index, sentence in enumerate(kept):
        if has_closing_line(sentence):
            protected.add(index)
        elif index == 0 and exempt_reason(sentence) in {"summary", "no_result"}:
            protected.add(index)

    # 뒤에서부터, 보호 대상이 아닌 문장을 뺀다.
    for index in range(len(kept) - 1, -1, -1):
        if total(kept) <= max_len:
            break
        if index in protected:
            continue
        removals.append(Removal(TOO_LONG, kept[index], "길이 초과로 제거"))
        kept[index] = ""
        truncated = True
    kept = [sentence for sentence in kept if sentence]

    # 그래도 넘치면 요약을 포기한다. 고정 문구는 끝까지 남긴다.
    if total(kept) > max_len:
        for index, sentence in enumerate(kept):
            if not has_closing_line(sentence):
                removals.append(Removal(TOO_LONG, sentence, "길이 초과로 제거 (요약)"))
                kept[index] = ""
                truncated = True
                if total([s for s in kept if s]) <= max_len:
                    break
        kept = [sentence for sentence in kept if sentence]

    return kept, truncated, removals


def _tidy(text: str) -> str:
    """겹공백과 문장 앞에 남은 부호를 치운다."""
    return _RE_MULTISPACE.sub(" ", _RE_LEADING_PUNCT.sub("", text)).strip()


def _strip_punct(sentence: str) -> str:
    """길이 판단용. 부호와 공백을 뺀 알맹이만 센다."""
    return re.sub(r"[\s.,!?…·\[\]()%\-]", "", sentence)


# ---------------------------------------------------------------------------
# 6. 답변 뼈대 (README 6장 1~5)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicySlot:
    """정책별 설명 자리 하나 (README 6장 2번). 문장은 모델이 쓴다."""

    policy_id: str
    title: str
    status: str


@dataclass(frozen=True)
class Skeleton:
    """코드가 확정할 수 있는 부분만 담은 답변 뼈대.

    summary        요약 문장 (README 6장 1번). 결과가 없으면 결과 없음 문구
    policy_slots   정책별 설명 자리. 표시 순서대로 최대 5개
    deadline_line  마감 강조 문장. 없으면 None
    closing_line   고정 문구
    extra_lines    결과 없음일 때 붙는 안내 (넓히는 방법, 데이터 범위)
    likely_count   표시 중인 likely 개수
    check_count    표시 중인 check 개수

    2번(정책별 설명)과 3번(조건 안내)은 모델이 쓴다. 여기서는 자리만 표시한다.
    """

    summary: str
    policy_slots: Tuple[PolicySlot, ...] = ()
    deadline_line: Optional[str] = None
    closing_line: str = CLOSING_LINE
    extra_lines: Tuple[str, ...] = ()
    likely_count: int = 0
    check_count: int = 0

    def outline(self) -> Tuple[str, ...]:
        """README 6장 순서대로 늘어놓은 줄 목록. 모델 프롬프트에 그대로 넣는다."""
        lines: List[str] = [self.summary]
        for slot in self.policy_slots:
            lines.append(f"{slot.title} ({slot.status}): {SLOT_POLICY_DETAIL}")
        if self.check_count:
            lines.append(SLOT_CONDITION_NOTE)
        if self.deadline_line:
            lines.append(self.deadline_line)
        lines.extend(self.extra_lines)
        lines.append(self.closing_line)
        return tuple(lines)


def build_skeleton(policies: Sequence[Mapping[str, Any]]) -> Skeleton:
    """정책 판정 결과로 답변 뼈대를 만든다 (README 6장 1~5).

    ``policies`` 는 docs/03-api-contract.md 4장 형식 dict 목록이다. 없는 키는 ``.get`` 으로
    넘긴다. 규칙 결과와 AI 결과가 합쳐지는 중간 상태에서도 호출되므로, 키 하나가 비었다고
    예외를 던지면 답변 단계가 통째로 실패한다(docs/03-api-contract.md 9장 10단계).

    **날짜를 계산하지 않는다.** 이미 계산된 ``badge`` 와 ``d_day`` 만 쓴다. 날짜 계산은
    코드가 한 번만 하고(README 2장 표), 그 결과를 여기서 그대로 인용한다. 두 곳에서 계산하면
    화면 배지와 답변 문장이 어긋난다.
    """
    # 목록 원소가 dict 가 아닌 경우를 먼저 걸러낸다. 규칙 결과와 AI 결과가 합쳐지는
    # 중간 상태에서 None 이 섞일 수 있고, 그때 예외를 던지면 답변 단계가 통째로 실패한다.
    items = [policy for policy in (policies or ()) if isinstance(policy, Mapping)]

    likely: List[Mapping[str, Any]] = []
    check: List[Mapping[str, Any]] = []
    for policy in items:
        status = _normalize_status(policy.get("status"))
        if status == LIKELY:
            likely.append(policy)
        elif status == UNLIKELY:
            continue
        else:
            # check 이거나 분류하지 못한 상태. 분류 실패를 check 로 세는 이유는
            # `_normalize_status` 독스트링에 있다. 카드가 있는데 "없다"고 말하지 않는다.
            check.append(policy)

    summary, extra_lines = _summary_line(len(likely), len(check))

    # 표시 순서를 그대로 쓴다. 정렬은 백엔드B 몫이다 (docs/03-api-contract.md 4장).
    # 제목이 없는 항목은 자리를 만들지 않는다. 정책명은 데이터 값 그대로 써야 하므로
    # (README 6장) 빈 제목으로 자리를 열면 모델이 이름을 지어낸다.
    slots: List[PolicySlot] = []
    for policy in likely + check:
        title = str(policy.get("title") or "").strip()
        if not title:
            continue
        slots.append(
            PolicySlot(
                policy_id=str(policy.get("policy_id") or ""),
                title=title,
                status=_normalize_status(policy.get("status")),
            )
        )
        if len(slots) >= MAX_POLICY_DETAILS:
            break

    return Skeleton(
        summary=summary,
        policy_slots=tuple(slots),
        deadline_line=_deadline_line(items),
        closing_line=CLOSING_LINE,
        extra_lines=extra_lines,
        likely_count=len(likely),
        check_count=len(check),
    )


def _normalize_status(value: Any) -> str:
    """판정 상태 값을 하나로 맞춘다. 모르는 값은 `UNCLASSIFIED`.

    전에는 모르는 값을 `unlikely` 로 떨어뜨렸다. "안전한 쪽"처럼 보이지만 실제로는
    **결과를 지우는 쪽**이었다. 카드 5장이 화면에 떠 있는데 상태 값 표기가 어긋나면
    likely·check 가 0개가 되고, 요약 문장이 "찾지 못했어요"로 나간다. 화면과 답변이
    정반대를 말하는 것이 가장 나쁜 실패다.

    그래서 분류 실패를 별도 값으로 구분하고, `build_skeleton` 이 이를 `check`
    (확인이 필요해요)로 세어 준다. 카드가 있으면 "확인이 필요하다"고 말하는 것이
    "없다"고 말하는 것보다 정직하다.
    """
    text = str(value or "").strip()
    return _STATUS_ALIASES.get(text, _STATUS_ALIASES.get(text.lower(), UNCLASSIFIED))


def _summary_line(likely_count: int, check_count: int) -> Tuple[str, Tuple[str, ...]]:
    """요약 문장 (README 6장 1번). 0개인 쪽은 문장에서 뺀다.

    "신청 가능성이 높은 제도 0개" 를 그대로 읽히게 두면 사용자가 먼저 보는 문장이 0이 된다.
    둘 다 0이면 결과 없음 문구와 넓히는 방법, 데이터 범위 한 줄을 함께 돌려준다.
    """
    if likely_count <= 0 and check_count <= 0:
        return NO_RESULT_LINE + ".", (BROADEN_LINE + ".", DATA_SCOPE_LINE + ".")

    parts: List[str] = []
    if likely_count > 0:
        parts.append(f"신청 가능성이 높은 제도 {likely_count}개")
    if check_count > 0:
        parts.append(f"확인이 필요한 제도 {check_count}개")
    return ", ".join(parts) + "를 찾았어요.", ()


def _deadline_line(policies: Sequence[Mapping[str, Any]]) -> Optional[str]:
    """마감 강조 한 문장 (README 6장 4번).

    ``deadline.is_imminent`` 가 참인 정책만 모아, 이미 계산된 ``badge`` 를 인용한다
    (docs/03-api-contract.md 4-2). ``badge`` 가 없으면 ``d_day`` 로 "D-n" 만 만든다.
    남은 일수를 여기서 세지 않는다.

    문장 끝에 각주 자리(``SLOT_FOOTNOTE``)를 둔다. 마감일은 공고에서 온 날짜이므로 각주가
    필요한 문장이고(README 6장 근거 규칙), 번호는 서버가 부여한 값을 모델이 채운다.
    """
    urgent: List[str] = []
    for policy in policies:
        if not isinstance(policy, Mapping):
            continue
        deadline = policy.get("deadline") or {}
        if not isinstance(deadline, Mapping) or not deadline.get("is_imminent"):
            continue
        title = str(policy.get("title") or "").strip()
        badge = str(deadline.get("badge") or "").strip()
        if not badge:
            d_day = _as_int(deadline.get("d_day"))
            badge = f"D-{d_day}" if d_day is not None else ""
        label = f"{title} {badge}".strip() if title else badge
        if label:
            urgent.append(label)

    if not urgent:
        return None

    # 두 개까지만 문장에 담는다. 더 넣으면 한 문장이 길어져 600자 예산을 먹는다.
    shown = urgent[:2]
    listed = ", ".join(shown)
    if len(urgent) > len(shown):
        listed = f"{listed} 등"
    return f"마감이 가까운 제도가 있어요: {listed}{SLOT_FOOTNOTE}."
