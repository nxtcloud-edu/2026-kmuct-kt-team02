"""AI B 내부 조건을 API 조건 모양으로 바꾸는 경계.

왜 필요한가
-----------
AI B 내부 조건에는 안전한 실패를 위한 값이 더 있다.

- 판정 실패 자리표시: ``placeholder=True``, ``name=""``, ``excerpt=None``
- 인용 검증 실패: ``result="unknown"``, ``name=""``, ``excerpt=None``
- 검증 기록: ``excerpt_verified``, ``excerpt_truncated``

이 값들은 **정책 상태 계산에는 필요하다.** 없애면 조건 0개가 되어 ``likely`` 로 갈 수
있다. 하지만 API의 ``ConditionEvaluation`` 은 이름 1자 이상, 출처와 각주 번호를
요구하고 추가 필드를 금지한다. 내부 조건을 그대로 응답에 넣으면 Pydantic 검증이
실패한다.

그래서 경계에서 두 묶음으로 나눈다.

- ``internal_conditions``: 상태 계산용. 자리표시와 실패 조건을 유지한다
- ``public_conditions``: 화면 전송용. **인용 검증을 통과한 조건만** API 모양으로 만든다

인용 검증에 실패한 조건 설명은 화면에 내보내지 않는다는 FR07 규칙과도 일치한다.
출처나 확인일이 없는 정책은 결과에서 제외한다는 FR12 판단은 정책 단위이므로
백엔드B가 한다. 이 함수는 ``source_url`` 이 없으면 공개 조건을 만들지 않고 문제를
기록한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence
from urllib.parse import urlparse

from ai.judgment.normalize import normalize
from ai.judgment.values import (
    ALLOWED_NEEDED_FIELDS,
    BY_AI,
    BY_RULE,
    MAX_EXCERPT_LEN,
    MAX_NAME_LEN,
    MET,
    MIN_EXCERPT_LEN,
    RESULTS,
    UNKNOWN,
    UNMET,
)


@dataclass
class PublicConditionBatch:
    """API로 보낼 수 있는 조건 묶음.

    conditions       ``docs/03-api-contract.md`` 4-1 필드만 가진 조건
    next_footnote_id 다음 정책이 이어서 쓸 각주 번호
    hidden_count     자리표시·검증 실패·계약 위반으로 화면에서 숨긴 조건 수
    issues           변환 중 발견한 문제 코드. 사용자 화면이 아니라 통합 점검용
    """

    conditions: List[Dict[str, Any]] = field(default_factory=list)
    next_footnote_id: int = 1
    hidden_count: int = 0
    issues: List[str] = field(default_factory=list)


ISSUE_SOURCE_URL_MISSING = "source_url_missing"
ISSUE_NOT_VERIFIED = "excerpt_not_verified"
ISSUE_INVALID_SHAPE = "invalid_condition_shape"
ISSUE_INVALID_VALUE = "invalid_condition_value"
ISSUE_UNGROUNDED = "ungrounded_condition"

_RESULT_ORDER = {UNMET: 0, UNKNOWN: 1, MET: 2}


def _valid_http_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _grounded(condition: Dict[str, Any], raw_text: str) -> bool:
    """조건 요약이 원문에 없는 숫자를 말하지 않는지.

    `grounding` 을 함수 안에서 import 한다. 그 모듈은 ``python3 -m`` 으로
    실행하는 진입점이 있어서, 패키지 로드 시점에 끌어오면 ``-m`` 실행이
    같은 모듈을 두 번 읽는다.
    """
    from ai.judgment.grounding import check_condition

    return check_condition(condition, raw_text).ok


def public_conditions(
    conditions: Sequence[Dict[str, Any]],
    *,
    source_url: Optional[str],
    start_footnote_id: int = 1,
    raw_text: Optional[str] = None,
) -> PublicConditionBatch:
    """인용 검증을 통과한 조건만 API 모양으로 만든다.

    각주 번호는 한 응답 안에서 1부터 순서대로다. **규칙 조건과 AI 조건을 최종
    병합한 목록**을 이 함수에 넘긴다. 함수가 API 계약 순서(`unmet → unknown → met`)로
    정렬한 뒤 번호를 한 번만 붙인다. 여러 정책을 변환할 때 반환된
    ``next_footnote_id`` 를 다음 호출의 ``start_footnote_id`` 로 넘긴다.

    안전 규칙
    ---------
    - ``excerpt_verified is True`` 인 조건만 공개한다. 키가 없으면 아직 검증 전이다
    - 자리표시는 내부에만 남긴다
    - 이름·발췌가 비면 공개하지 않는다
    - 결과 값이 ``met/unmet/unknown`` 이 아니면 공개하지 않는다
    - ``unknown`` 이 아닐 때 ``needed_field`` 은 항상 ``None``
    - 내부용 키는 응답에 넣지 않는다
    - 값이 서버 계약(`server/schemas.py` ``ConditionEvaluation``) 범위를 벗어나면 공개하지 않는다
    - ``raw_text`` 를 주면 조건 요약에 원문에 없는 숫자가 있는 조건도 공개하지 않는다

    마지막 항목이 필요한 이유는 인용 검증이 발췌만 본다는 점이다. 발췌는 원문에
    있는데 요약이 "소득 180% 이하" 처럼 지어낸 숫자를 말할 수 있다. 근거 없는 조건은
    목표가 0건이므로 화면 전송 직전에 한 번 더 막는다 (README 11장).
    """
    batch = PublicConditionBatch(next_footnote_id=max(1, int(start_footnote_id)))

    if not source_url or not str(source_url).strip() or not _valid_http_url(str(source_url).strip()):
        batch.hidden_count = len(conditions or ())
        batch.issues.append(ISSUE_SOURCE_URL_MISSING)
        return batch

    url = str(source_url).strip()
    visible: List[Dict[str, Any]] = []

    for condition in conditions or ():
        name = str(condition.get("name") or "").strip()
        excerpt = condition.get("excerpt")
        excerpt_text = str(excerpt).strip() if excerpt is not None else ""
        result = str(condition.get("result") or "")

        if condition.get("placeholder") or condition.get("excerpt_verified") is not True:
            batch.hidden_count += 1
            batch.issues.append(ISSUE_NOT_VERIFIED)
            continue

        if (
            not name
            or len(name) > MAX_NAME_LEN
            or not excerpt_text
            or not MIN_EXCERPT_LEN <= len(normalize(excerpt_text)) <= MAX_EXCERPT_LEN
            or result not in RESULTS
        ):
            batch.hidden_count += 1
            batch.issues.append(ISSUE_INVALID_SHAPE)
            continue

        judged_by = str(condition.get("judged_by") or "")
        needed_field = condition.get("needed_field") if result == UNKNOWN else None
        if judged_by not in {BY_RULE, BY_AI} or (
            result == UNKNOWN
            and needed_field is not None
            and str(needed_field) not in ALLOWED_NEEDED_FIELDS
        ):
            batch.hidden_count += 1
            batch.issues.append(ISSUE_INVALID_VALUE)
            continue

        if raw_text is not None and not _grounded(condition, raw_text):
            # 발췌는 원문에 있지만 요약이 원문에 없는 숫자를 말한다.
            batch.hidden_count += 1
            batch.issues.append(ISSUE_UNGROUNDED)
            continue

        visible.append(
            {
                # 요약 키는 `name` 하나다. `docs/03-api-contract.md` 4-1 이 응답
                # 항목으로 한 이름만 둔다. `citation.py` 가 내부 경계에서 `summary` 를
                # 같이 채우는 것과 구분한다. 응답 본문에 두 키를 다 넣으면 프론트가
                # 어느 쪽을 읽어야 하는지 모호해진다.
                "name": name,
                "result": result,
                "judged_by": judged_by,
                "excerpt": excerpt_text,
                "source_url": url,
                "needed_field": needed_field,
            }
        )

    # API 계약의 화면 순서(unmet → unknown → met)를 적용한 뒤 각주 번호를 붙인다.
    # 이 함수에는 규칙 조건과 AI 조건을 **최종 병합한 목록**을 넘겨야 한다.
    visible.sort(key=lambda item: _RESULT_ORDER[str(item["result"])])
    for item in visible:
        item["footnote_id"] = batch.next_footnote_id
        batch.next_footnote_id += 1
        batch.conditions.append(item)

    return batch
