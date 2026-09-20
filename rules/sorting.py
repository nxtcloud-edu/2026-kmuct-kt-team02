"""정렬과 표시 구분 (docs/01-glossary-profile.md 6~7장).

`frontend/src/lib/sort.ts` 와 같은 순서를 낸다. 구조는 다르지만 비교 결과가 같다.
`policy_id` 까지 내려오면 순서가 하나로 정해진다. 같은 입력에 같은 순서가 나와야
데모에서 카드 자리가 흔들리지 않는다.
"""

from __future__ import annotations

from . import constants as c

_FAR_FUTURE = "9999-12-31"


def _deadline_group(item: dict) -> tuple[int, str]:
    """마감 그룹과 그 안의 정렬 값.

    마감일 있음 0 → 상시 접수 1 → 접수 예정 2. 접수 예정끼리는 시작일 순으로 둔다
    (문서에 없어 자체 결정, notes/judgment-tables.md 7장).
    """
    deadline = item["deadline"]
    if item["data_status"] == c.UPCOMING:
        return 2, deadline.get("apply_start") or _FAR_FUTURE
    if not deadline.get("apply_end"):
        return 1, ""
    return 0, deadline["apply_end"]


def sort_key(item: dict) -> tuple:
    """상태 → 마감 임박 → 마감일 → 관심 분야 일치 수 → id."""
    group, group_value = _deadline_group(item)
    return (
        c.STATUS_RANK[item["status"]],
        0 if item["deadline"]["is_imminent"] else 1,
        group,
        group_value,
        -item["_match_count"],
        item["policy_id"],
    )


def sort_evaluations(items: list[dict]) -> list[dict]:
    return sorted(items, key=sort_key)


def split_display(items: list[dict]) -> tuple[list[dict], list[dict]]:
    """기본 표시와 접힌 영역을 나눈다.

    기본 표시는 likely·check 상위 5개, 접힌 영역은 unlikely 상위 3개다.

    주의: 현재 계약(`server/rule_engine.py` RuleEngineResult)에는 unlikely 목록을 담을
    자리가 없고 `hidden_unlikely_count` 만 있다. 개수만으로는 프론트가 접힌 카드를
    그릴 수 없어 FR13(대안 제시)을 만족할 수 없다. 목록도 함께 돌려주고 어댑터에서
    계약에 맞게 줄인다 (notes/open-items.md 1-2).
    """
    ordered = sort_evaluations(items)
    basic = [item for item in ordered if item["status"] != c.UNLIKELY]
    unlikely = [item for item in ordered if item["status"] == c.UNLIKELY]
    return basic[: c.BASIC_DISPLAY_LIMIT], unlikely[: c.HIDDEN_UNLIKELY_LIMIT]
