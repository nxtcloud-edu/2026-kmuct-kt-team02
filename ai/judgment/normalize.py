"""공고 원문과 AI 발췌를 비교할 수 있는 형태로 맞추는 정규화.

왜 필요한가
-----------
인용 검증은 "AI가 낸 발췌가 공고 원문에 그대로 있는지"를 문자열 포함으로 확인한다.
그런데 사람 눈에 똑같이 보이는 두 문자열이 코드에서는 다른 경우가 많다.
정규화가 부족하면 검증이 전건 실패하고, 모든 조건이 미확인으로 떨어져
카드가 전부 check가 된다. 즉 서비스의 핵심 가치가 화면에서 사라진다.

macOS 와 Windows 를 번갈아 쓰면서 생기는 차이가 특히 위험하다.

1. 유니코드 정규형
   macOS 는 한글을 자모 분해형(NFD)으로 다루는 경로가 많다.
   파일명, 클립보드, 일부 브라우저 복사가 그렇다.
   Windows 는 완성형(NFC)이다.
   "휴학" 한 단어도 NFC 2글자 / NFD 4글자로 저장되어 포함 검사가 무조건 실패한다.

2. 줄바꿈
   Windows 는 CRLF, macOS 는 LF. .gitattributes 로 1차 방어하지만
   스프레드시트 붙여넣기나 API 응답으로 직접 들어오는 값은 git 을 거치지 않는다.

3. 보이지 않는 문자
   Windows Excel 이 UTF-8 로 저장하면 선두에 BOM(U+FEFF)이 붙는다.
   웹 공고를 복사하면 비분리 공백(U+00A0), 전각 공백(U+3000),
   제로폭 공백(U+200B)이 섞인다. 화면에는 보이지 않지만 비교는 깨진다.

설계 원칙
---------
- 비교용으로만 정규화하고, 화면에 내보내는 발췌는 **원문에서 되찾은 구간**을 쓴다.
  AI 가 공백을 다르게 쓴 발췌를 보내도, 사용자에게 보이는 문장은 항상 공고 원문 쪽이다.
  이래야 "원문 우선" 원칙이 지켜진다 (`docs/05-interfaces.md` 6장).
- 문장 부호(대시, 따옴표 종류)는 건드리지 않는다.
  근거의 내용을 바꾸는 정규화는 하지 않는다.

관련 문서
---------
- `ai/judgment/README.md` 6장 — 인용 검증 절차
- `docs/04-data-schema.md` 2장 — `raw_text` 작성 규칙 (표를 한 줄씩 풀어 쓰기)
"""

from __future__ import annotations

import unicodedata
from typing import List, Tuple

# 폭이 없어 눈에 보이지 않으면서 문자열 비교를 깨뜨리는 문자. 통째로 제거한다.
ZERO_WIDTH = frozenset(
    {
        "\ufeff",  # BOM / ZWNBSP. 윈도우 Excel UTF-8 저장 시 선두에 붙는다
        "\u200b",  # ZERO WIDTH SPACE. 웹 공고의 줄바꿈 힌트
        "\u200c",  # ZERO WIDTH NON-JOINER
        "\u200d",  # ZERO WIDTH JOINER
        "\u2060",  # WORD JOINER
        "\u00ad",  # SOFT HYPHEN. HTML 하이픈 힌트
    }
)

# 공백 하나로 치환할 문자. 줄바꿈도 여기 포함해서 CRLF / LF / CR 차이를 함께 흡수한다.
SPACE_LIKE = frozenset(
    {
        " ",
        "\t",
        "\n",
        "\r",
        "\v",
        "\f",
        "\u00a0",  # NO-BREAK SPACE. HTML &nbsp; 복붙
        "\u1680",
        "\u2000",
        "\u2001",
        "\u2002",
        "\u2003",
        "\u2004",
        "\u2005",
        "\u2006",
        "\u2007",
        "\u2008",
        "\u2009",
        "\u200a",
        "\u202f",  # NARROW NO-BREAK SPACE
        "\u205f",
        "\u3000",  # IDEOGRAPHIC SPACE. 한글 공고 들여쓰기에 흔히 쓰인다
    }
)


def canonical(text: str) -> str:
    """플랫폼에 따라 갈리는 표현을 하나로 맞춘 표준형.

    줄바꿈과 공백 구조는 그대로 두고, 유니코드 정규형만 NFC 로 통일한다.
    저장·표시·비교 어디서든 이 형태를 기준으로 삼는다.
    """
    return unicodedata.normalize("NFC", text)


def normalize_with_map(text: str) -> Tuple[str, List[int]]:
    """비교용 정규화 결과와, 각 글자가 표준형에서 몇 번째였는지 기록한 표.

    표(``index_map``)가 있으면 정규화된 문자열에서 찾은 위치를
    원문 위치로 되돌릴 수 있다. 화면에 원문 그대로를 보여주기 위해 쓴다.

    반환값의 ``index_map[i]`` 는 정규화 결과 i 번째 글자가
    ``canonical(text)`` 의 몇 번째 글자에서 왔는지를 가리킨다.
    """
    source = canonical(text)

    chars: List[str] = []
    index_map: List[int] = []
    prev_is_space = False

    for position, char in enumerate(source):
        if char in ZERO_WIDTH:
            continue

        if char in SPACE_LIKE:
            # 연속 공백은 하나로 접는다. 접힌 공백은 첫 글자 위치를 대표로 삼는다.
            if prev_is_space:
                continue
            chars.append(" ")
            index_map.append(position)
            prev_is_space = True
            continue

        chars.append(char)
        index_map.append(position)
        prev_is_space = False

    # 앞뒤 공백 제거
    start = 0
    end = len(chars)
    while start < end and chars[start] == " ":
        start += 1
    while end > start and chars[end - 1] == " ":
        end -= 1

    return "".join(chars[start:end]), index_map[start:end]


def normalize(text: str) -> str:
    """비교에 쓰는 정규화 문자열."""
    return normalize_with_map(text)[0]
