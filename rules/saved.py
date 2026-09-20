"""저장 정책 조회 (관심 정책 저장, notes/judgment-tables.md 10장).

추천 목록과 **경로가 다르다.** 후보 제외 규칙을 그대로 쓰면 저장해둔 정책이 마감되는
순간 목록에서 사라져 사용자가 당황한다. 본인이 저장한 것이므로 관심 분야로도 걸러내지
않는다.

| 상황 | 추천 목록 | 저장 목록 |
| --- | --- | --- |
| 마감 | 제외 | 표시. 배지는 마감, 맨 뒤로 정렬 |
| 관심 분야 불일치 | 제외 | 표시 |
| 출처·확인일 없음 | 제외 | 제외 (FR12 는 양쪽 동일) |
| 데이터에 없는 id | 해당 없음 | 제거하고 건수만 기록 |

로그인이 빠지면 이 모듈도 함께 빠진다. 다른 모듈이 여기에 의존하지 않게 분리해 두었다.
"""

from __future__ import annotations

from datetime import date

from . import constants as c
from . import deadline as dl
from . import engine, sorting


def _saved_exclusion_reason(policy: dict) -> str | None:
    """저장 목록에서도 빼야 할 이유. FR12 만 적용한다."""
    if not policy.get("source_url") or not policy.get("checked_at"):
        return c.EXCLUDED_NO_SOURCE
    if not (policy.get("raw_text") or "").strip():
        return c.EXCLUDED_NO_RAW_TEXT
    if policy.get("source_kind") == c.SOURCE_CRAWLED:
        return c.EXCLUDED_CRAWLED
    return None


def _sort_key(item: dict) -> tuple:
    """추천과 같은 키를 쓰되 마감을 맨 뒤로 보낸다."""
    is_closed = item["data_status"] == c.CLOSED
    return (1 if is_closed else 0, *sorting.sort_key(item))


def evaluate_by_ids(
    profile: dict,
    policies: list[dict],
    today: date,
    policy_ids: list[str],
) -> dict:
    """저장된 정책 번호로만 판정한다.

    반환 키
        policies    판정 결과. 마감 정책은 맨 뒤
        missing_ids 데이터에서 사라진 번호
        excluded    출처·확인일이 없어 빠진 정책과 사유
        issues      검수 필요 기록
    """
    by_id = {policy.get("id"): policy for policy in policies}

    evaluations: list[dict] = []
    missing_ids: list[str] = []
    excluded: list[dict] = []
    issues: list[str] = []

    for policy_id in dict.fromkeys(policy_ids):
        policy = by_id.get(policy_id)
        if policy is None:
            # 정책 번호는 사용자 데이터에 박히는 영속 식별자다. 사라진 번호는
            # 조용히 지우고 건수만 남긴다 (notes/judgment-tables.md 11장).
            missing_ids.append(policy_id)
            continue

        reason = _saved_exclusion_reason(policy)
        if reason is not None:
            excluded.append({"policy_id": policy_id, "reason": reason})
            continue

        evaluation, policy_issues = engine.evaluate_policy(profile, policy, today)
        evaluations.append(evaluation)
        issues.extend(policy_issues)

    evaluations.sort(key=_sort_key)
    engine._assign_footnote_ids(evaluations)
    for evaluation in evaluations:
        evaluation.pop("_match_count", None)

    return {
        "policies": evaluations,
        "missing_ids": missing_ids,
        "excluded": excluded,
        "issues": issues,
    }


def closed_ids(policies: list[dict], today: date) -> list[str]:
    """마감된 정책 번호. 저장 목록 화면에서 마감 표시를 미리 세는 데 쓴다."""
    return [
        policy["id"]
        for policy in policies
        if policy.get("id") and dl.is_closed(policy, today)
    ]
