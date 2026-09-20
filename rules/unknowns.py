"""미확인 항목 목록 (rules/README.md 8장).

AI A 가 이 목록을 보고 어떤 질문을 할지 고른다 (`ai/conversation/followup.py` UnknownItem).
**질문 선택은 하지 않는다.** 선택·문구·우선순위는 AI A 의 일이다.

이미 물어본 항목과 건너뛴 항목 필터링도 AI A 가 한다. 규칙 엔진은 그것을 모르고
미확인 목록을 계속 낸다.
"""

from __future__ import annotations

from . import constants as c


def unknown_items(evaluations: list[dict]) -> list[dict]:
    """표시 순서대로 미확인 항목을 모은다.

    `policy_rank` 는 화면 표시 순서이고 0 이 가장 위다.
    `policy_id` 를 빈 문자열로 내보내지 않는다. AI A 가 영향 정책 수를 세는 기준이라
    빈 값이 섞이면 개수가 부풀고 엉뚱한 항목을 먼저 묻게 된다.
    """
    items: list[dict] = []

    for rank, evaluation in enumerate(evaluations):
        policy_id = evaluation.get("policy_id")
        if not policy_id:
            continue

        for condition in evaluation.get("conditions") or []:
            if condition["result"] != c.UNKNOWN:
                continue
            field = condition.get("needed_field")
            # 표에 없는 항목은 물을 수 없다. 조건부 문장으로만 안내한다.
            if not field or field not in c.ASKABLE_FIELDS:
                continue
            items.append(
                {
                    "field": field,
                    "policy_id": policy_id,
                    "policy_rank": rank,
                    "policy_title": evaluation.get("title") or "",
                }
            )

    return items
