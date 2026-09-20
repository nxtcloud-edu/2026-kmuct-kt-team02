"""후보 선정과 상태 결정 (docs/01-glossary-profile.md 4·7장, rules/README.md 6장)."""

from __future__ import annotations

from datetime import date

from . import constants as c
from . import deadline as dl


def exclusion_reason(profile: dict, policy: dict, today: date) -> str | None:
    """후보에서 빼야 할 이유. 후보면 None.

    출처와 확인일이 없는 정책은 규칙 엔진 단계에서 걸러야 뒤 단계에서 근거 없는 카드가
    만들어질 여지가 없다 (FR12, rules/README.md 6장).
    """
    if dl.is_closed(policy, today):
        return c.EXCLUDED_CLOSED

    if policy.get("source_kind") == c.SOURCE_CRAWLED:
        # 검수를 통과해 manual 이 되고 checked_at 이 채워진 뒤에만 후보가 된다
        # (docs/04-data-schema.md 2-1).
        return c.EXCLUDED_CRAWLED

    if not policy.get("source_url") or not policy.get("checked_at"):
        return c.EXCLUDED_NO_SOURCE

    if not (policy.get("raw_text") or "").strip():
        return c.EXCLUDED_NO_RAW_TEXT

    interests = set(profile.get("categories") or [])
    categories = set(policy.get("categories") or [])
    if c.CATEGORY_ALL not in interests and not (interests & categories):
        return c.EXCLUDED_CATEGORY

    return None


def decide_status(conditions: list[dict]) -> str:
    """조건 결과 조합으로 판정 상태를 정한다 (docs/01-glossary-profile.md 4장).

    규칙 조건만으로 계산한 상태다. AI 조건을 합친 최종 상태는 백엔드B가 확정한다.
    """
    results = {condition["result"] for condition in conditions}
    if c.UNMET in results:
        return c.UNLIKELY
    if c.UNKNOWN in results:
        return c.CHECK
    return c.LIKELY


def category_match_count(profile: dict, policy: dict) -> int:
    """관심 분야 일치 수. all 을 선택하면 정책의 분야 수를 그대로 센다."""
    interests = set(profile.get("categories") or [])
    categories = policy.get("categories") or []
    if c.CATEGORY_ALL in interests:
        return len(categories)
    return sum(1 for item in categories if item in interests)
