"""규칙 엔진 진입점 (rules/README.md 1·3장).

**AI 없이도 정확한 결과를 내는 것이 이 모듈의 존재 이유다.** AI 가 전부 실패하거나
늦어도 이 출력만으로 카드가 화면에 보여야 한다.

`today` 를 인자로 받는 순수 함수다. 외부 의존성 없이 표준 라이브러리만 쓴다.
백엔드B 의 웹 프레임워크·인증·저장 수단을 알지 않는다.
"""

from __future__ import annotations

from datetime import date

from . import candidates, conditions, constants as c, deadline as dl, sorting, unknowns


def evaluate_policy(profile: dict, policy: dict, today: date) -> tuple[dict, list[str]]:
    """정책 하나를 판정해 PolicyEvaluation 모양의 dict 를 만든다.

    `footnote_id` 는 응답 전체에서 1부터 매겨야 하므로 여기서 넣지 않는다
    (docs/03-api-contract.md 4-1).
    """
    condition_list, issues = conditions.evaluate_conditions(profile, policy)
    status = candidates.decide_status(condition_list)

    evaluation = {
        "policy_id": policy.get("id"),
        "title": policy.get("title"),
        "agency": policy.get("agency"),
        "categories": policy.get("categories") or [],
        "status": status,
        "status_label": c.STATUS_LABELS[status],
        "benefit": policy.get("benefit") or "",
        "conditions": condition_list,
        # 조건부 문장은 AI B 가 고정 형식으로 채운다 (docs/03-api-contract.md 4장).
        "conditional_note": None,
        "deadline": dl.compute_deadline(policy, today),
        "documents": policy.get("documents") or [],
        "steps": policy.get("steps") or [],
        "source_url": policy.get("source_url"),
        "apply_url": policy.get("apply_url"),
        "checked_at": policy.get("checked_at"),
        "data_status": dl.resolve_data_status(policy, today),
        "_match_count": candidates.category_match_count(profile, policy),
    }
    return evaluation, issues


def _assign_footnote_ids(evaluations: list[dict]) -> None:
    """응답 안에서 1부터 순서대로 각주 번호를 매긴다.

    발췌가 없는 조건은 화면에 나갈 수 없으므로 번호를 주지 않는다.
    """
    next_id = 1
    for evaluation in evaluations:
        for condition in evaluation["conditions"]:
            if condition.get("excerpt"):
                condition["footnote_id"] = next_id
                next_id += 1
            else:
                condition["footnote_id"] = None


def evaluate_policies(
    profile: dict,
    policies: list[dict],
    today: date,
    limit: int = c.BASIC_DISPLAY_LIMIT,
) -> dict:
    """후보를 고르고 판정하고 정렬해 돌려준다.

    반환 키
        policies              기본 표시. likely·check 중 상위 `limit` 개
        hidden_unlikely       접힌 영역. unlikely 중 상위 3개
        hidden_unlikely_count 접힌 영역 개수
        unknown_items         AI A 가 후속 질문을 고를 재료
        excluded              후보에서 빠진 정책과 사유
        issues                검수 필요 기록
        no_result             likely·check 가 0개인가
    """
    evaluated: list[dict] = []
    excluded: list[dict] = []
    issues: list[str] = []

    for policy in policies:
        reason = candidates.exclusion_reason(profile, policy, today)
        if reason is not None:
            excluded.append({"policy_id": policy.get("id"), "reason": reason})
            continue
        evaluation, policy_issues = evaluate_policy(profile, policy, today)
        evaluated.append(evaluation)
        issues.extend(policy_issues)

    basic, hidden = sorting.split_display(evaluated)
    basic = basic[:limit]

    _assign_footnote_ids(basic)
    _assign_footnote_ids(hidden)

    for evaluation in basic + hidden:
        evaluation.pop("_match_count", None)

    return {
        "policies": basic,
        "hidden_unlikely": hidden,
        "hidden_unlikely_count": len(hidden),
        "unknown_items": unknowns.unknown_items(basic),
        "excluded": excluded,
        "issues": issues,
        # likely 와 check 가 0개면 결과 없음 흐름이다. unlikely 가 남아 있어도 그렇다
        # (docs/01-glossary-profile.md 7장).
        "no_result": not basic,
    }
