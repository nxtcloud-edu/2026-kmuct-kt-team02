"""인용 검증 (설계서 6-4).

AI 가 낸 발췌가 공고 원문에 실제로 있는지 확인한다.
확인은 **로직으로만** 한다. LLM 에 재확인을 맡기지 않는다.

통과하면 원문에서 되찾은 구간을 돌려준다. 화면에는 이 값을 쓴다.
AI 가 공백이나 줄바꿈을 다르게 쓴 발췌를 보내도 사용자가 보는 문장은 항상 원문 쪽이다.

실패하면 해당 조건을 미확인으로 내리고 발췌와 조건 요약을 화면에 내보내지 않는다.
제거 건수는 호출한 쪽에서 기록한다 (지표: 인용 검증 통과율).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .normalize import canonical, normalize, normalize_with_map

# 발췌 길이 기준 (설계서 6-3)
MIN_EXCERPT_LEN = 10
MAX_EXCERPT_LEN = 150

# 조건 결과 값 (설계서 1-1)
MET = "충족"
UNMET = "미충족"
UNKNOWN = "미확인"


@dataclass(frozen=True)
class VerifyResult:
    """검증 결과.

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
    excerpt: str,
    raw_text: str,
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
    if not excerpt or not excerpt.strip():
        return VerifyResult(False, None, "empty_excerpt")
    if not raw_text or not raw_text.strip():
        return VerifyResult(False, None, "empty_raw")

    needle = normalize(excerpt)
    if not needle:
        return VerifyResult(False, None, "empty_excerpt")

    if len(needle) < min_len:
        return VerifyResult(False, None, "too_short")

    # 상한 초과는 실패로 보지 않고 앞에서 잘라 검증한다.
    # 포함된 문자열의 앞부분도 포함되므로 안전하고, 맞는 판정을 길이 때문에 버리지 않는다.
    truncated = False
    if len(needle) > max_len:
        needle = needle[:max_len].rstrip()
        truncated = True
        if len(needle) < min_len:
            return VerifyResult(False, None, "too_short", truncated=True)

    haystack, index_map = normalize_with_map(raw_text)
    found_at = haystack.find(needle)
    if found_at == -1:
        return VerifyResult(False, None, "not_found", truncated=truncated)

    # 정규화 위치를 원문(표준형) 위치로 되돌려 원문 그대로의 구간을 꺼낸다.
    source = canonical(raw_text)
    start = index_map[found_at]
    end = index_map[found_at + len(needle) - 1] + 1
    return VerifyResult(True, source[start:end], "ok", truncated=truncated)


def contains(excerpt: str, raw_text: str) -> bool:
    """통과 여부만 필요할 때 쓰는 간단한 형태."""
    return verify_excerpt(excerpt, raw_text).ok


def verify_conditions(
    conditions: List[Dict[str, Any]],
    raw_text: str,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    """판정 결과 목록을 통째로 검증한다 (설계서 6-4 실패 처리).

    조건 항목 형식 (설계서 6-3)
        summary       조건 요약 (20자 이내 명사형)
        result        충족 / 미충족 / 미확인
        excerpt       원문 발췌
        needed_field  미확인일 때 필요한 추가 항목

    검증에 실패한 조건은
      - result 를 미확인으로 내리고
      - summary 와 excerpt 를 비워 화면에 나가지 않게 한다.
    조건 자체는 목록에 남긴다. 판정 상태 계산(설계서 1-3)에서 미확인으로 세어야 하기 때문이다.

    반환값은 (검증 후 조건 목록, 제거 기록)이다.
    제거 기록은 지표용이며 사용자 화면에 쓰지 않는다.
    """
    checked: List[Dict[str, Any]] = []
    removed: List[Dict[str, str]] = []

    for condition in conditions:
        item = dict(condition)
        result = verify_excerpt(item.get("excerpt") or "", raw_text)

        if result.ok:
            item["excerpt"] = result.excerpt
            item["excerpt_verified"] = True
            if result.truncated:
                item["excerpt_truncated"] = True
            checked.append(item)
            continue

        removed.append(
            {
                "summary": str(item.get("summary") or ""),
                "excerpt": str(item.get("excerpt") or ""),
                "reason": result.reason,
            }
        )

        item["result"] = UNKNOWN
        item["summary"] = ""
        item["excerpt"] = None
        item["excerpt_verified"] = False
        if not item.get("needed_field"):
            item["needed_field"] = "공고 확인 필요"
        checked.append(item)

    return checked, removed


def raw_text_covers(exceptions_text: str, raw_text: str) -> bool:
    """데이터 검수용. exceptions_text 가 raw_text 안에 있는지 확인한다.

    판정 입력은 exceptions_text 인데 검증 대조 대상은 raw_text 다.
    둘이 어긋나 있으면 그 정책의 모든 발췌가 not_found 로 떨어진다.
    정책 데이터를 적재할 때 이 함수로 미리 걸러야 한다.
    """
    if not exceptions_text or not exceptions_text.strip():
        return True  # 예외 조건이 없으면 판정을 생략하므로 문제되지 않는다
    return normalize(exceptions_text) in normalize(raw_text)
