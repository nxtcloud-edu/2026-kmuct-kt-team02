"""후속 질문 선택 (ai/conversation/README.md 4장).

**무엇을 물을지는 코드가 고르고, 모델은 문장만 다듬는다.**
선택을 모델에 맡기면 같은 상황에서 다른 질문이 나와 리허설과 데모가 어긋난다.
그리고 "판정이 바뀌는 것만 묻는다"는 P0 규칙을 지켰는지 확인할 수 없게 된다.

입력은 규칙 엔진이 넘긴 미확인 항목 목록이다 (rules/README.md 8장).
이 모듈은 프로필 값을 해석하지 않는다. 항목 이름과 영향 범위만 보고 고른다.
그래서 프로필 허용 값이 바뀌어도 이 모듈은 영향을 받지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from typing import Dict, Iterable, List, Optional, Sequence, Set

from . import fields, questions


@dataclass(frozen=True)
class UnknownItem:
    """규칙 엔진이 넘긴 미확인 항목 하나.

    field        프로필 항목 이름 (docs/01-glossary-profile.md 3장)
    policy_id    이 항목 때문에 판정이 미확인으로 남은 정책
    policy_rank  화면 표시 순서. 0 이 가장 위
    policy_title 이유 문구에 넣을 정책명. 없으면 개수만 말한다
    """

    field: str
    policy_id: str
    policy_rank: int
    policy_title: str = ""


@dataclass(frozen=True)
class AskedState:
    """이미 물어본 것과 건너뛴 것.

    세션이 기억하는 값이다 (docs/03-api-contract.md 11장).
    같은 질문을 두 번 하면 대화형이라는 인상이 한순간에 깨진다. 그래서 이 상태를
    선택 단계에서 먼저 걸러낸다. 규칙 엔진은 이걸 모르고 미확인 목록을 계속 낸다.
    """

    asked: Set[str] = dataclass_field(default_factory=set)
    skipped: Set[str] = dataclass_field(default_factory=set)

    def is_done(self, name: str) -> bool:
        return name in self.asked or name in self.skipped

    def with_asked(self, name: str) -> "AskedState":
        return AskedState(asked=self.asked | {name}, skipped=set(self.skipped))

    def with_skipped(self, name: str) -> "AskedState":
        return AskedState(asked=set(self.asked), skipped=self.skipped | {name})


@dataclass(frozen=True)
class Candidate:
    """후보 항목 하나와 그 영향 범위."""

    field: str
    policy_count: int
    best_rank: int
    policy_titles: Sequence[str]

    @property
    def sort_key(self):
        """README 4장의 우선순위를 그대로 옮긴 정렬 키.

        3번 영향 정책 수가 많은 것 → 음수로 뒤집어 내림차순
        4번 더 위에 있는 정책에 영향을 주는 것 → best_rank 오름차순
        5번 표 순서 → field_order 오름차순
        """
        return (-self.policy_count, self.best_rank, fields.field_order(self.field))


def collect_candidates(
    items: Iterable[UnknownItem],
    state: Optional[AskedState] = None,
) -> List[Candidate]:
    """미확인 항목 목록을 후보로 묶는다.

    같은 항목이 여러 정책에서 나오면 하나로 합치고 정책 수를 센다.
    이미 물었거나 건너뛴 항목, 질문 틀이 없는 항목, 물을 수 없는 항목은 뺀다.
    """
    state = state or AskedState()
    grouped: Dict[str, List[UnknownItem]] = {}

    for item in items:
        name = item.field
        if state.is_done(name):
            continue
        if name == fields.ASK_NOTICE or not fields.is_askable(name):
            # 추가 항목 표에 없는 정보는 물을 수 없다. 조건부 문장으로만 안내한다.
            continue
        if questions.get(name) is None:
            continue
        grouped.setdefault(name, []).append(item)

    candidates: List[Candidate] = []
    for name, group in grouped.items():
        policy_ids = {entry.policy_id for entry in group}
        titles = [entry.policy_title for entry in group if entry.policy_title]
        # 중복 제목 제거. 순서는 화면 표시 순서를 따른다.
        ordered = sorted(group, key=lambda entry: entry.policy_rank)
        seen: Set[str] = set()
        unique_titles: List[str] = []
        for entry in ordered:
            if entry.policy_title and entry.policy_title not in seen:
                seen.add(entry.policy_title)
                unique_titles.append(entry.policy_title)

        candidates.append(
            Candidate(
                field=name,
                policy_count=len(policy_ids),
                best_rank=min(entry.policy_rank for entry in group),
                policy_titles=unique_titles or titles,
            )
        )

    candidates.sort(key=lambda candidate: candidate.sort_key)
    return candidates


def choose(
    items: Iterable[UnknownItem],
    state: Optional[AskedState] = None,
    intent: str = fields.FIND_POLICY,
) -> Optional[Candidate]:
    """물을 항목 하나를 고른다. 물을 것이 없으면 None.

    None 을 돌려주는 경우가 정상 동작이다. 판정을 바꾸는 미확인 정보가 없으면
    묻지 않는다 (FR05). 억지로 질문을 만들어 내면 심문이 된다.
    """
    if intent in fields.NO_FOLLOWUP_INTENTS:
        return None

    candidates = collect_candidates(items, state)
    return candidates[0] if candidates else None


def next_question(
    items: Iterable[UnknownItem],
    state: Optional[AskedState] = None,
    intent: str = fields.FIND_POLICY,
) -> Optional[Dict[str, object]]:
    """스트리밍 이벤트에 실을 후속 질문 본문. 물을 것이 없으면 None.

    형식은 docs/03-api-contract.md 6장을 따른다.
    """
    chosen = choose(items, state, intent)
    if chosen is None:
        return None

    return questions.build(
        chosen.field,
        policy_titles=chosen.policy_titles,
        affected_count=chosen.policy_count,
    )
