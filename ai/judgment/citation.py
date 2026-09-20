"""인용 검증 (`ai/judgment/README.md` 6장).

AI 가 낸 발췌가 공고 원문에 실제로 있는지 확인한다.
**검증은 AI 가 아니라 로직으로 한다.** 모델에게 재확인을 맡기면
검증 대상과 같은 종류의 실패를 공유한다.

통과하면 원문에서 되찾은 구간을 돌려준다. 화면에는 이 값을 쓴다.
AI 가 공백이나 줄바꿈을 다르게 쓴 발췌를 보내도 사용자가 보는 문장은 항상 원문 쪽이다.

실패하면 그 조건을 `unknown` 으로 바꾸고 발췌와 조건 요약을 화면에 내보내지 않는다.
제거 건수는 지표로 기록한다 (`docs/03-api-contract.md` 13장).

통과율이 낮게 나와도 부분 일치나 유사도로 풀지 않는다. 그건 의도된 동작이다.
계속 낮으면 백엔드A와 `raw_text` 형태를 먼저 본다 (표가 섞이면 대조가 자주 깨진다).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from ai.judgment.normalize import canonical, normalize, normalize_with_map
from ai.judgment.values import (
    ASK_NOTICE,
    BY_AI,
    MAX_EXCERPT_LEN,
    MIN_EXCERPT_LEN,
    UNKNOWN,
)


@dataclass(frozen=True)
class VerifyResult:
    """발췌 하나의 검증 결과.

    ok        통과 여부
    excerpt   통과 시 원문에서 되찾은 구간. 실패 시 None
    reason    실패 사유 코드. 통과 시 "ok"
    truncated 발췌가 상한을 넘어 잘렸는지
    """

    ok: bool
    excerpt: Optional[str]
    reason: str
    truncated: bool = False


def verify_excerpt(
    excerpt: object,
    raw_text: object,
    *,
    min_len: int = MIN_EXCERPT_LEN,
    max_len: int = MAX_EXCERPT_LEN,
) -> VerifyResult:
    """발췌 하나를 원문과 대조한다.

    실패 사유 코드
        empty_excerpt  발췌가 비었거나 공백뿐
        empty_raw      대조할 원문이 없음 (데이터 문제)
        too_short      너무 짧아 근거로 쓸 수 없음
        not_found      원문에 없음 (환각 또는 원문 변형)
    """
    if not excerpt or not str(excerpt).strip():
        return VerifyResult(False, None, "empty_excerpt")
    if not raw_text or not str(raw_text).strip():
        return VerifyResult(False, None, "empty_raw")

    needle = normalize(str(excerpt))
    if not needle:
        return VerifyResult(False, None, "empty_excerpt")

    if len(needle) < min_len:
        return VerifyResult(False, None, "too_short")

    # 상한 초과는 실패로 보지 않고 앞에서 잘라 검증한다.
    # 포함된 문자열의 앞부분도 포함되므로 안전하고,
    # 길이 때문에 맞는 판정을 버리지 않는다.
    truncated = False
    if len(needle) > max_len:
        needle = needle[:max_len].rstrip()
        truncated = True
        if len(needle) < min_len:
            return VerifyResult(False, None, "too_short", truncated=True)

    haystack, index_map = normalize_with_map(str(raw_text))
    found_at = haystack.find(needle)
    if found_at == -1:
        return VerifyResult(False, None, "not_found", truncated=truncated)

    # 정규화 위치를 원문 위치로 되돌려 원문 그대로의 구간을 꺼낸다.
    source = canonical(str(raw_text))
    start = index_map[found_at]
    end = index_map[found_at + len(needle) - 1] + 1
    return VerifyResult(True, source[start:end], "ok", truncated=truncated)


def contains(excerpt: object, raw_text: object) -> bool:
    """통과 여부만 필요할 때 쓰는 간단한 형태."""
    return verify_excerpt(excerpt, raw_text).ok


def verify_conditions(
    conditions: List[Dict[str, Any]],
    raw_text: object,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """조건 목록을 통째로 검증한다.

    조건 항목 형식은 `docs/03-api-contract.md` 4-1 을 따른다.
        name          조건 요약 (20자 이내 명사형)
        result        met / unmet / unknown
        judged_by     rule / ai
        excerpt       공고 원문 발췌
        needed_field  unknown 일 때만. 추가 항목 이름 또는 "공고 확인 필요"

    검증에 실패한 조건은
      - `result` 를 `unknown` 으로 내리고
      - `name` 과 `excerpt` 를 비워 화면에 나가지 않게 한다.

    조건 자체는 목록에 남긴다. 판정 상태 계산(`docs/01-glossary-profile.md` 4장)에서
    `unknown` 으로 세어야 하기 때문이다. 조건을 지우면 예외 조건이 없는 정책처럼
    보여서 `likely` 가 될 수 있다. 실패가 사용자에게 유리한 방향으로 잘못 작용한다.

    ``placeholder`` 표시가 있는 조건은 검증을 건너뛴다. 판정 실패를 나타내려고
    만든 자리표시이므로 근거가 없는 것이 정상이고, 제거 건수에 세면 지표가 흐려진다.

    반환값은 (검증 후 조건 목록, 제거 기록)이다.
    제거 기록은 지표용이며 사용자 화면에 쓰지 않는다.
    """
    checked: List[Dict[str, Any]] = []
    removed: List[Dict[str, str]] = []

    for condition in conditions:
        item = dict(condition)

        if item.get("placeholder"):
            item["excerpt_verified"] = False
            checked.append(item)
            continue

        result = verify_excerpt(item.get("excerpt"), raw_text)

        if result.ok:
            item["excerpt"] = result.excerpt
            item["excerpt_verified"] = True
            if result.truncated:
                item["excerpt_truncated"] = True
            checked.append(item)
            continue

        removed.append(
            {
                "name": str(item.get("name") or ""),
                "excerpt": str(item.get("excerpt") or ""),
                "reason": result.reason,
            }
        )

        item["result"] = UNKNOWN
        item["name"] = ""
        item["excerpt"] = None
        item["excerpt_verified"] = False
        if not item.get("needed_field"):
            item["needed_field"] = ASK_NOTICE
        checked.append(item)

    return checked, removed


def placeholder_unknown(reason: str) -> Dict[str, Any]:
    """판정에 실패했을 때 쓰는 `unknown` 자리표시 조건 (README 9장).

    빈 목록을 돌려주면 안 된다. 예외 조건이 있는 정책인데 조건이 0개면
    판정 상태 계산이 "모든 조건 met"으로 보고 `likely` 를 줄 수 있다.
    실패가 사용자에게 유리한 방향으로 잘못 작용하는 것이다.
    """
    return {
        "name": "",
        "result": UNKNOWN,
        "judged_by": BY_AI,
        "excerpt": None,
        "needed_field": ASK_NOTICE,
        "placeholder": True,
        "placeholder_reason": reason,
    }


# ---------------------------------------------------------------------------
# 데이터 검수용
# ---------------------------------------------------------------------------


def raw_text_covers(exceptions_text: object, raw_text: object) -> bool:
    """`exceptions_text` 가 `raw_text` 안에 있는지 확인한다.

    판정 입력은 `exceptions_text` 인데 검증 대조 대상은 `raw_text` 다.
    둘이 어긋나 있으면 그 정책의 **모든 발췌가 not_found 로 떨어진다.**
    정책 데이터를 적재할 때 이 함수로 미리 걸러야 한다.

    `docs/04-data-schema.md` 4장 검수 체크리스트의 마지막 두 항목을 코드로 옮긴 것이다.
    """
    if not exceptions_text or not str(exceptions_text).strip():
        return True  # 예외 조건이 없으면 판정을 생략하므로 문제되지 않는다
    return normalize(str(exceptions_text)) in normalize(str(raw_text or ""))


def verify_condition_sources(
    condition_sources: Dict[str, str],
    raw_text: object,
) -> Dict[str, str]:
    """규칙 조건의 근거 문장도 같은 방식으로 한 번 검증한다 (README 6장).

    백엔드A가 스프레드시트에 손으로 옮긴 문장에도 오차가 생길 수 있다.
    반환값은 실패한 조건 이름과 사유다. 비어 있으면 전부 통과한 것이다.
    """
    failures: Dict[str, str] = {}
    for name, sentence in (condition_sources or {}).items():
        result = verify_excerpt(sentence, raw_text)
        if not result.ok:
            failures[str(name)] = result.reason
    return failures
