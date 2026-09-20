"""판정 출력 스키마와 방어적 파싱 (`ai/judgment/README.md` 5장·9장).

이 모듈이 하는 일은 두 가지다.

1. **스키마 강제용 정의** (`JUDGE_OUTPUT_SCHEMA`) — 모델에게 넘길 JSON Schema.
2. **방어적 파싱** (`parse_conditions`) — 그래도 어긋난 출력을 항목 단위로 구제한다.

이 모듈이 하지 않는 일 (경계를 흐리면 규칙이 두 곳에 생긴다):

| 하지 않는 것 | 하는 곳 |
| --- | --- |
| LLM 호출 | 호출부 (`docs/research/04-llm-api-operations.md` "모델 교체에 대비하는 방법") |
| 발췌를 원문과 대조, 발췌 길이(10~150자) 검증 | `citation.py` |
| 판정 상태(`likely`/`check`/`unlikely`) 계산 | 서버 |
| 조건부 문장 작성 | `conditional_note.py` |

## 왜 스키마를 강제해도 파서가 필요한가

제약 디코딩은 스키마를 위반하는 토큰이 나오지 않게 만들지만, 우리가 그 기능을
실제로 켤 수 있는지는 당일 받는 모델과 호출 방식에 달려 있다
(`docs/research/04-llm-api-operations.md` 1장). 강제가 걸리지 않은 경로에서는
앞뒤에 설명 문장이 붙거나 코드펜스로 감싸인 출력이 그대로 온다.
그때 전체를 버리면 쓸 수 있는 판정까지 잃는다. **항목 단위로 구제한다.**

## 빈 결과와 실패의 구분 (호출부가 알아야 하는 것)

- 쓸 수 있는 JSON을 못 찾았거나 항목이 전부 깨졌으면 `error`가 채워진다 → 호출부는
  `citation.placeholder_unknown()`으로 예외 조건 전체를 `unknown`으로 둔다 (README 9장).
- 모델이 조건을 하나도 찾지 못해 **빈 목록**을 정상 형식으로 낸 경우는 `error`가
  `None`이고 `conditions`도 빈 목록이다. 파싱은 성공했으므로 실패로 보지 않는다.
  예외 조건이 있는 정책에서 이 결과를 그대로 쓰면 조건 0개가 되어 판정 상태가
  `likely`로 갈 수 있다. **그 판단은 호출부가 한다.**
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ai.judgment.values import (
    ALLOWED_NEEDED_FIELDS,
    ASK_NOTICE,
    BY_AI,
    FIELD_LABELS,
    MAX_EXCERPT_LEN,
    MAX_NAME_LEN,
    MET,
    MIN_EXCERPT_LEN,
    UNKNOWN,
    UNMET,
    normalize_needed_field,
)

# ---------------------------------------------------------------------------
# 1. 스키마 강제용 정의
# ---------------------------------------------------------------------------
#
# 설계 제약 (`docs/research/04-llm-api-operations.md` 1장)
#
#   - 엄격 모드는 **모든 속성을 필수 목록에 넣으라**고 요구한다. 그래서
#     `needed_field` 를 선택 항목으로 빼지 않고, 빈 문자열을 허용하는 필수 항목으로 둔다.
#   - `additionalProperties` 를 막는다. 모델이 없는 항목을 덧붙이지 못하게 한다.
#   - 중첩을 얕게 유지한다. 목록과 복합 객체가 들어가면 추론 난도가 올라간다
#     (`docs/research/02-grounding-and-citation.md` 3장).
#   - 길이 제약 키워드(`minLength`/`maxLength`)는 지원되지 않는 경우가 있어 쓰지 않는다.
#     길이는 설명으로 알려 주고, 실제 처리는 코드가 한다
#     (조건 요약은 이 모듈이 자르고, 발췌 길이는 `citation.py` 가 본다).
#
# 값은 전부 `values.py` 에서 가져온다. 문자열을 여기에 다시 쓰지 않는다.

#: `needed_field` 에 쓸 수 있는 값. 빈 문자열은 "해당 없음"을 뜻한다.
#: `unknown` 이 아닌 조건에서는 빈 문자열이어야 한다.
NEEDED_FIELD_ENUM: Tuple[str, ...] = ("",) + tuple(sorted(ALLOWED_NEEDED_FIELDS))

CONDITION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["name", "result", "excerpt", "needed_field"],
    "properties": {
        "name": {
            "type": "string",
            "description": (
                f"조건 요약. {MAX_NAME_LEN}자 이내 명사형. 예: 타 청년 지원금 중복 불가"
            ),
        },
        "result": {
            "type": "string",
            "enum": [MET, UNMET, UNKNOWN],
            "description": (
                f"{MET}: 프로필에 명시된 값으로 조건을 만족하는 것이 분명함. "
                f"{UNMET}: 프로필에 명시된 값이 제외 대상에 분명히 해당함. "
                f"{UNKNOWN}: 프로필에 정보가 없거나 원문 표현이 모호함. "
                f"조금이라도 애매하면 {UNKNOWN}."
            ),
        },
        "excerpt": {
            "type": "string",
            "description": (
                f"공고 원문의 연속된 부분을 그대로. {MIN_EXCERPT_LEN}~{MAX_EXCERPT_LEN}자. "
                "조건당 하나. 요약하거나 고쳐 쓰지 않는다."
            ),
        },
        "needed_field": {
            "type": "string",
            "enum": list(NEEDED_FIELD_ENUM),
            "description": (
                f"{UNKNOWN} 일 때만 채운다. 프로필 추가 항목 이름 또는 "
                f'"{ASK_NOTICE}". 그 밖의 경우는 빈 문자열.'
            ),
        },
    },
}

JUDGE_OUTPUT_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["conditions"],
    "properties": {
        "conditions": {
            "type": "array",
            "items": CONDITION_SCHEMA,
            "description": (
                "공고 원문의 예외 조건 목록. 원문에 없는 조건을 만들지 않는다. "
                "판정할 문장이 없으면 빈 목록."
            ),
        },
    },
}

# ---------------------------------------------------------------------------
# 2. 실패 사유 코드
# ---------------------------------------------------------------------------
# 지표용이다. 사용자 화면에 쓰지 않는다.
# `citation.py` 의 실패 사유 코드와 같은 성격이고, 이름이 겹치지 않게 두었다.

#: 전체 실패 (`ParseOutcome.error`)
ERROR_EMPTY_INPUT = "empty_input"  # 입력이 비었거나 문자열이 아님
ERROR_NO_JSON = "no_json_found"  # JSON 덩어리를 찾지 못함
ERROR_INVALID_JSON = "invalid_json"  # 덩어리는 찾았지만 해석 실패
ERROR_UNEXPECTED_SHAPE = "unexpected_shape"  # 해석은 됐지만 조건 목록이 아님
ERROR_ALL_DROPPED = "all_items_dropped"  # 항목이 있었지만 전부 버림

#: 항목 단위 실패 (`ParseOutcome.dropped` 의 `reason`)
DROP_NOT_OBJECT = "not_an_object"
DROP_MISSING_EXCERPT = "missing_excerpt"
DROP_INVALID_RESULT = "invalid_result"

# ---------------------------------------------------------------------------
# 3. 값·키 별칭표
# ---------------------------------------------------------------------------
# 스키마 강제가 걸리지 않은 경로에서 모델은 같은 뜻을 다른 말로 쓴다.
# 별칭은 **정확히 일치**시킨다. 부분 일치로 맞추면 "not_met" 이 "met" 으로 읽힌다.

_RESULT_ALIASES: Dict[str, str] = {
    # met
    "met": MET,
    "충족": MET,
    "satisfied": MET,
    "satisfy": MET,
    "eligible": MET,
    "pass": MET,
    "passed": MET,
    "ok": MET,
    "yes": MET,
    "true": MET,
    "만족": MET,
    "해당": MET,
    "적격": MET,
    # unmet
    "unmet": UNMET,
    "notmet": UNMET,  # not_met, not-met, "not met"
    "미충족": UNMET,
    "불충족": UNMET,
    "unsatisfied": UNMET,
    "notsatisfied": UNMET,
    "ineligible": UNMET,
    "excluded": UNMET,
    "fail": UNMET,
    "failed": UNMET,
    "no": UNMET,
    "false": UNMET,
    "제외": UNMET,
    "부적격": UNMET,
    # unknown
    "unknown": UNKNOWN,
    "unclear": UNKNOWN,
    "미확인": UNKNOWN,
    "uncertain": UNKNOWN,
    "unsure": UNKNOWN,
    "undetermined": UNKNOWN,
    "indeterminate": UNKNOWN,
    "확인필요": UNKNOWN,
    "확인불가": UNKNOWN,
    "알수없음": UNKNOWN,
    "판단불가": UNKNOWN,
    "정보없음": UNKNOWN,
    "na": UNKNOWN,
}

_NAME_ALIASES: Tuple[str, ...] = (
    "name",
    "summary",
    "condition",
    "conditionname",
    "conditionsummary",
    "title",
    "label",
    "조건",
    "조건요약",
    "요약",
    "조건명",
)

_RESULT_KEY_ALIASES: Tuple[str, ...] = (
    "result",
    "results",
    "judgment",
    "judgement",
    "verdict",
    "decision",
    "status",
    "결과",
    "판정",
    "판정결과",
)

_EXCERPT_ALIASES: Tuple[str, ...] = (
    "excerpt",
    "quote",
    "evidence",
    "citation",
    "sourcetext",
    "rawtext",
    "originaltext",
    "text",
    "발췌",
    "원문",
    "원문발췌",
    "근거",
    "근거문장",
    "인용",
)

_NEEDED_ALIASES: Tuple[str, ...] = (
    "neededfield",
    "needed",
    "need",
    "neededinfo",
    "neededinformation",
    "requiredfield",
    "missingfield",
    "askfor",
    "필요한추가항목",
    "필요항목",
    "추가항목",
    "필요한정보",
)

#: 배열 대신 객체로 감싸 보낼 때 쓰는 키
_WRAPPER_ALIASES: Tuple[str, ...] = (
    "conditions",
    "조건",
    "조건목록",
    "results",
    "items",
    "data",
)

#: 표준 키 이름 -> 그 키를 뜻하는 별칭들
_KEY_ALIASES: Dict[str, Tuple[str, ...]] = {
    "name": _NAME_ALIASES,
    "result": _RESULT_KEY_ALIASES,
    "excerpt": _EXCERPT_ALIASES,
    "needed_field": _NEEDED_ALIASES,
}

def _fold(value: object) -> str:
    """별칭 대조용으로 접은 문자열.

    NFC 정규화 → 소문자 → 공백·밑줄·하이픈·괄호 등 제거.
    macOS 경로에서 자모 분해형으로 들어온 한글 키도 같은 값으로 접힌다
    (`ai/judgment/README.md` 6장의 이유와 같다).

    발췌에는 쓰지 않는다. 발췌는 원문 그대로여야 한다.
    """
    text = unicodedata.normalize("NFC", str(value)).strip().lower()
    for ch in (" ", "\t", "\n", "\r", "_", "-", ".", "'", '"', "(", ")", "[", "]"):
        text = text.replace(ch, "")
    return text


#: 허용 목록의 값 자체를 접어 둔 것. "공고 확인필요" 처럼 띄어쓰기만 다른 경우를 흡수한다.
_FOLDED_ALLOWED: Dict[str, str] = {_fold(name): name for name in ALLOWED_NEEDED_FIELDS}

#: 화면 문구 -> 프로필 항목 이름.
#: 모델이 `needed_field` 에 "직전 학기 성적" 같은 화면 문구를 써 보내는 경우가 있다.
#: `values.FIELD_LABELS` 를 거꾸로 본 것이고, 새 항목을 만드는 것이 아니다.
#: 여기서 못 맞춰도 `normalize_needed_field()` 가 "공고 확인 필요"로 흡수한다.
_LABEL_TO_FIELD: Dict[str, str] = {
    _fold(label): name for name, label in FIELD_LABELS.items()
}


def normalize_result(value: object) -> Optional[str]:
    """모델이 쓴 결과 값을 표준 값으로 맞춘다. 알아볼 수 없으면 None.

    None 을 `unknown` 으로 바꾸지 않는다. 알아보지 못한 값은 버려야 한다.
    모르는 값을 조용히 `unknown` 으로 만들면 실제로는 `unmet` 인 판정이
    미확인으로 섞여 들어와도 알 수 없다.
    """
    return _RESULT_ALIASES.get(_fold(value))


# ---------------------------------------------------------------------------
# 4. JSON 덩어리 꺼내기
# ---------------------------------------------------------------------------


def _fenced_blocks(text: str) -> List[str]:
    """코드펜스 안쪽만 모은다. 펜스가 여러 개면 긴 것부터.

    닫는 펜스가 없으면 그 뒤 전체를 내용으로 본다. 출력이 잘려 끝난 경우다.
    """
    blocks: List[str] = []
    parts = text.split("```")
    # 0, 2, 4... 는 펜스 바깥이다
    for index in range(1, len(parts), 2):
        block = parts[index]
        # 첫 줄에 언어 표시(json 등)가 붙어 있으면 떼어 낸다
        head, newline, rest = block.partition("\n")
        if newline and head.strip().lower() in ("json", "json5", "javascript", ""):
            block = rest
        blocks.append(block)
    if len(parts) % 2 == 0:
        # 닫는 펜스가 없어 마지막 조각이 짝을 못 찾은 경우
        blocks.append(parts[-1])
    return sorted((b for b in blocks if b.strip()), key=len, reverse=True)


def _balanced_chunks(text: str) -> List[str]:
    """괄호 깊이를 세어 균형이 맞는 JSON 덩어리를 꺼낸다.

    **문자열 안의 괄호에 속지 않는다.** 발췌에는 "(1) 신청 기간 [필수]" 같은
    괄호가 자주 들어간다. 이걸 세면 덩어리가 엉뚱한 데서 끝난다.
    이스케이프된 따옴표(`\\"`)도 문자열을 끝내지 않는다.
    """
    chunks: List[str] = []
    depth = 0
    start = -1
    in_string = False
    escaped = False

    for index, ch in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            # 덩어리 바깥의 따옴표는 설명 문장의 것이므로 무시한다
            if depth > 0:
                in_string = True
            continue

        if ch in "[{":
            if depth == 0:
                start = index
            depth += 1
        elif ch in "]}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    chunks.append(text[start : index + 1])
                    start = -1

    return chunks


def _strip_trailing_commas(chunk: str) -> str:
    """`,` 뒤에 바로 `]` 나 `}` 가 오는 경우만 지운다 (문자열 안은 건드리지 않는다).

    형식 오류 중 가장 흔하고 내용을 바꾸지 않는 한 가지만 고친다.
    따옴표 종류 교정이나 주석 제거까지 손대면 근거 문장을 변형할 위험이 생긴다.
    """
    out: List[str] = []
    in_string = False
    escaped = False
    for index, ch in enumerate(chunk):
        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            continue
        if ch == ",":
            rest = chunk[index + 1 :].lstrip()
            if rest[:1] in ("]", "}"):
                continue  # 버린다
        out.append(ch)
    return "".join(out)


def _candidate_chunks(text: str) -> List[str]:
    """해석해 볼 JSON 덩어리 후보. 코드펜스 안쪽을 먼저 본다."""
    candidates: List[str] = []
    seen = set()

    sources = _fenced_blocks(text)
    sources.append(text)

    for source in sources:
        for chunk in _balanced_chunks(source):
            key = chunk.strip()
            if key and key not in seen:
                seen.add(key)
                candidates.append(chunk)
    return candidates


def _load(chunk: str) -> Optional[Any]:
    """JSON 해석. 실패하면 흔한 형식 오류 하나만 고쳐 다시 시도한다."""
    try:
        return json.loads(chunk)
    except (ValueError, TypeError):
        pass
    repaired = _strip_trailing_commas(chunk)
    if repaired != chunk:
        try:
            return json.loads(repaired)
        except (ValueError, TypeError):
            pass
    return None


def _as_item_list(parsed: Any) -> Optional[List[Any]]:
    """해석된 값에서 조건 목록을 꺼낸다. 조건 목록으로 볼 수 없으면 None.

    받아내는 형태
        [ {...}, {...} ]              배열 그대로
        {"conditions": [ ... ]}       감싼 객체 (`_WRAPPER_ALIASES`)
        {"conditions": {...}}         감싼 객체 안에 항목이 하나
        {"name": ..., "result": ...}  조건 하나를 배열로 감싸지 않고 보낸 경우
    """
    if isinstance(parsed, list):
        return parsed

    if isinstance(parsed, dict):
        folded = {_fold(key): value for key, value in parsed.items()}
        for alias in _WRAPPER_ALIASES:
            if alias in folded:
                value = folded[alias]
                if isinstance(value, list):
                    return value
                if isinstance(value, dict):
                    return [value]
        # 감싸는 키가 없지만 조건 항목처럼 보이면 항목 하나로 본다
        if any(alias in folded for alias in _RESULT_KEY_ALIASES) and any(
            alias in folded for alias in _EXCERPT_ALIASES
        ):
            return [parsed]

    return None


def _extract_items(text: str) -> Tuple[Optional[List[Any]], Optional[str]]:
    """본문에서 조건 목록을 꺼낸다. 실패하면 (None, 실패 사유)."""
    candidates = _candidate_chunks(text)
    if not candidates:
        return None, ERROR_NO_JSON

    best: Optional[List[Any]] = None
    best_score: Tuple[int, int, int] = (-1, -1, -1)
    parsed_any = False
    shaped_any = False

    for chunk in candidates:
        parsed = _load(chunk)
        if parsed is None:
            continue
        parsed_any = True
        items = _as_item_list(parsed)
        if items is None:
            continue
        shaped_any = True
        # 항목이 많은 덩어리를 고른다. 설명 문장 안의 "[1]" 같은 것에 끌리지 않게
        # 사전형 항목 수를 먼저 본다.
        score = (
            sum(1 for item in items if isinstance(item, dict)),
            len(items),
            len(chunk),
        )
        if score > best_score:
            best_score = score
            best = items

    if best is not None:
        return best, None
    if shaped_any:  # 도달하지 않는다 (모양이 맞으면 best 가 채워진다)
        return None, ERROR_UNEXPECTED_SHAPE
    if parsed_any:
        return None, ERROR_UNEXPECTED_SHAPE
    return None, ERROR_INVALID_JSON


# ---------------------------------------------------------------------------
# 5. 항목 정규화
# ---------------------------------------------------------------------------


def _pick(raw: Dict[Any, Any], slot: str) -> Any:
    """별칭을 훑어 표준 키의 값을 찾는다. 표준 이름을 가장 먼저 본다."""
    folded = {}
    for key, value in raw.items():
        folded.setdefault(_fold(key), value)
    for alias in _KEY_ALIASES[slot]:
        if alias in folded:
            value = folded[alias]
            if value is not None and value != "":
                return value
    return None


def _as_text(value: object) -> str:
    """값을 문자열로 만든다.

    목록으로 보내는 경우가 있다(발췌 여러 개). 조건당 발췌는 하나이므로
    첫 번째 것만 쓴다. 이어 붙이면 원문에 없는 문장이 만들어진다.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        for element in value:
            text = _as_text(element)
            if text:
                return text
        return ""
    if isinstance(value, bool):
        return ""
    return str(value).strip()


def _needed_field_for(value: object) -> str:
    """`needed_field` 를 허용 목록 안으로 밀어넣는다.

    화면 문구로 보낸 경우만 항목 이름으로 되돌리고, 그 뒤는
    `values.normalize_needed_field()` 에 맡긴다. 판단 규칙을 두 곳에 두지 않는다.
    """
    text = _as_text(value)
    if not text:
        return ASK_NOTICE
    folded = _fold(text)
    mapped = _FOLDED_ALLOWED.get(folded) or _LABEL_TO_FIELD.get(folded)
    return normalize_needed_field(mapped or text)


def _normalize_item(raw: Any) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """조건 하나를 표준 형식으로 만든다. 버려야 하면 (None, 사유).

    표준 형식은 `docs/03-api-contract.md` 4-1 이다.
    `source_url` 과 `footnote_id` 는 백엔드B가 붙이므로 여기서 만들지 않는다.
    """
    if not isinstance(raw, dict):
        return None, DROP_NOT_OBJECT

    result = normalize_result(_pick(raw, "result"))
    if result is None:
        # 알아볼 수 없는 결과를 미확인으로 바꾸지 않는다. 판정을 지어내는 셈이 된다.
        return None, DROP_INVALID_RESULT

    excerpt = _as_text(_pick(raw, "excerpt"))
    if not excerpt:
        # 근거 없는 판정은 인용 검증도 통과할 수 없다. 여기서 버린다.
        return None, DROP_MISSING_EXCERPT

    item: Dict[str, Any] = {
        "name": "",
        "result": result,
        "judged_by": BY_AI,
        "excerpt": excerpt,
        "needed_field": _needed_field_for(_pick(raw, "needed_field"))
        if result == UNKNOWN
        else None,
    }

    # 조건 요약은 NFC 로 맞춘 뒤 센다. 자모 분해형은 같은 문장이 두 배 길이가 되어
    # 멀쩡한 요약이 잘리고, 자르는 위치가 자모 중간이 된다 (README 6장).
    name = unicodedata.normalize("NFC", _as_text(_pick(raw, "name")))
    if len(name) > MAX_NAME_LEN:
        name = name[:MAX_NAME_LEN].rstrip()
        item["name_trimmed"] = True
    item["name"] = name

    return item, None


# ---------------------------------------------------------------------------
# 6. 파싱 결과
# ---------------------------------------------------------------------------


@dataclass
class ParseOutcome:
    """파싱 결과.

    conditions  살려낸 조건. `docs/03-api-contract.md` 4-1 형식
    dropped     버린 항목과 사유. {"index": 원래 위치, "reason": 사유 코드}. 지표용
    error       전체 실패 사유. 성공이면 None
    """

    conditions: List[Dict[str, Any]] = field(default_factory=list)
    dropped: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


def conditions_from_data(data: Any) -> ParseOutcome:
    """이미 해석된 값에서 조건 목록을 꺼낸다.

    `ai/conversation/llm.py` 의 어댑터는 파싱까지 끝내고 `StructuredResponse.data`
    (매핑)를 돌려준다. 그 경로에서는 문자열 파싱이 필요 없으므로 항목 정규화만 한다.

    문자열을 받으면 `parse_conditions()` 로 넘긴다. 어댑터가 스키마 강제를 걸지
    못해 본문 문자열을 그대로 준 경우까지 같은 함수로 다룰 수 있게 한 것이다.

    항목 단위 구제와 실패 사유 코드는 `parse_conditions()` 와 같다.
    """
    if isinstance(data, str):
        return parse_conditions(data)

    items = _as_item_list(data)
    if items is None:
        return ParseOutcome(error=ERROR_UNEXPECTED_SHAPE)

    return _normalize_items(items)


def parse_conditions(text: str) -> ParseOutcome:
    """모델 출력에서 조건 목록을 꺼낸다.

    받아내는 형태
        - 배열 그대로
        - 코드펜스로 감싼 경우 (펜스가 여러 개면 긴 것부터)
        - 앞뒤에 설명 문장이 붙은 경우
        - `{"conditions": [...]}` 처럼 감싼 경우
        - 결과 값과 키 이름을 다르게 쓴 경우 (별칭표)
        - 목록 끝에 쉼표가 남은 경우

    **항목 단위로 구제한다.** 하나가 깨져도 나머지는 살린다.
    하나도 살리지 못했을 때만 `error` 가 채워진다.
    """
    if not isinstance(text, str) or not text.strip():
        return ParseOutcome(error=ERROR_EMPTY_INPUT)

    items, error = _extract_items(text)
    if items is None:
        return ParseOutcome(error=error)

    return _normalize_items(items)


def _normalize_items(items: Sequence[Any]) -> ParseOutcome:
    """조건 항목들을 표준 형식으로 맞춘다. 항목 단위로 구제한다."""
    conditions: List[Dict[str, Any]] = []
    dropped: List[Dict[str, Any]] = []

    for index, raw in enumerate(items):
        item, reason = _normalize_item(raw)
        if item is None:
            dropped.append({"index": index, "reason": reason})
            continue
        conditions.append(item)

    if not conditions and items:
        # 항목이 있었는데 전부 버렸다. 호출부는 예외 조건 전체를 미확인으로 둔다.
        return ParseOutcome(dropped=dropped, error=ERROR_ALL_DROPPED)

    # 빈 목록을 정상 형식으로 낸 경우는 실패가 아니다. 모듈 설명의 "빈 결과와 실패의 구분" 참고.
    return ParseOutcome(conditions=conditions, dropped=dropped)
