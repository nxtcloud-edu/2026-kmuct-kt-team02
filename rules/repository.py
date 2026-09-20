"""백엔드B 프로토콜 어댑터 (server/rule_engine.py, server/policy_repository.py).

`rules/` 의 나머지 모듈은 표준 라이브러리만 쓴다. Pydantic 과 `server` 를 아는 곳은
이 파일 하나다. 그래서 규칙 판정은 웹 프레임워크·인증·저장 수단이 바뀌어도 그대로 남는다.

여기서 해결하는 계약 차이 두 가지 (notes/open-items.md 1-1, 1-2)

1. `RuleEngine.evaluate(profile, *, limit)` 에는 `today` 와 정책 데이터가 없다.
   판정 함수는 `today` 를 인자로 받아야 재현 가능하므로, 주입은 이 어댑터가 한다.
   `clock` 을 바꿔 끼우면 마감 경계 테스트를 날짜에 상관없이 돌릴 수 있다.
2. `RuleEngineResult` 에는 `unlikely` 목록을 담을 자리가 없다. 계약대로 개수만 넘기고,
   목록이 필요해지면 `latest_hidden_unlikely()` 로 꺼내 쓴다. 계약을 임의로 바꾸지 않는다.
"""

from __future__ import annotations

import threading
from datetime import date
from pathlib import Path
from typing import Callable

from pydantic import ValidationError

from server.rule_engine import RuleEngineResult, RuleEngineUnavailableError
from server.schemas import Policy, PolicyEvaluation, Profile

from . import constants as c
from . import engine, loader
from .deadline import today_kst


class PolicyStore:
    """정책 데이터를 한 번 읽어 들고 있는다.

    `server.policy_repository.PolicyRepository` 를 만족한다.
    프로세스가 재시작되면 다시 로드된다. 사용자 데이터는 담지 않는다.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.policies, self.issues = loader.load_policies(self.path)
        self._by_id = {policy["id"]: policy for policy in self.policies}

    def get(self, policy_id: str) -> Policy | None:
        """정책 상세. 계약을 만족하지 못하는 행은 None 으로 본다.

        미검수 데이터는 `source_url` 이 비어 있어 `Policy` 검증을 통과하지 못한다.
        예외를 올리지 않고 None 을 돌려주는 이유는, 정책 한 건이 잘못돼 있어도
        나머지 요청이 정상으로 처리돼야 하기 때문이다 (rules/README.md 9장).
        """
        raw = self._by_id.get(policy_id)
        if raw is None:
            return None
        try:
            return Policy.model_validate(raw)
        except ValidationError:
            return None

    def raw(self, policy_id: str) -> dict | None:
        """판정에 넘길 원본 dict. 규칙 엔진은 Pydantic 모델을 모른다."""
        return self._by_id.get(policy_id)

    def count_verified(self) -> int:
        """`/health` 의 verified_policy_count."""
        return loader.count_verified(self.policies)

    def __len__(self) -> int:
        return len(self.policies)


class RuleEngineAdapter:
    """`server.rule_engine.RuleEngine` 프로토콜 구현."""

    def __init__(
        self,
        store: PolicyStore,
        *,
        clock: Callable[[], date] = today_kst,
    ) -> None:
        self._store = store
        self._clock = clock
        self._lock = threading.Lock()
        self._hidden_unlikely: list[PolicyEvaluation] = []
        self._last_result: dict | None = None

    def evaluate(self, profile: Profile, *, limit: int = 5) -> RuleEngineResult:
        """프로필을 받아 이미 걸러지고 정렬된 추천을 돌려준다.

        정책 데이터를 읽지 못했으면 `RuleEngineUnavailableError` 를 올린다.
        서버가 이것을 503 으로 바꿔 "규칙 엔진 의존성이 연결되지 않았습니다"를 보여준다.
        빈 결과와 데이터 없음은 사용자에게 다른 뜻이라 구분한다.
        """
        if not self._store.policies:
            raise RuleEngineUnavailableError(
                f"정책 데이터를 읽지 못했습니다: {self._store.path}"
            )

        outcome = engine.evaluate_policies(
            profile.model_dump(mode="json"),
            self._store.policies,
            self._clock(),
            limit=limit,
        )

        try:
            policies = [PolicyEvaluation.model_validate(item) for item in outcome["policies"]]
            hidden = [
                PolicyEvaluation.model_validate(item) for item in outcome["hidden_unlikely"]
            ]
        except ValidationError as error:
            # 판정 결과가 계약을 벗어나면 잘못된 카드를 내보내는 대신 멈춘다.
            raise RuleEngineUnavailableError(f"판정 결과가 계약과 어긋납니다: {error}") from error

        with self._lock:
            self._hidden_unlikely = hidden
            self._last_result = outcome

        return RuleEngineResult(
            policies=policies,
            hidden_unlikely_count=outcome["hidden_unlikely_count"],
        )

    def evaluate_policy(self, profile: Profile, policy: Policy) -> PolicyEvaluation:
        """정책 한 건을 판정한다. `/policies/{id}` 상세가 부른다.

        `server/api/policies.py` 가 이 메서드를 쓰지만 `RuleEngine` Protocol 에는
        선언돼 있지 않다. Protocol 에 추가해 달라고 백엔드B 에 알려야 한다.
        여기서는 호출부가 이미 있으므로 구현부터 맞춘다.

        후보 제외 규칙을 적용하지 않는다. 사용자가 카드를 눌러 상세를 여는 경로이므로
        관심 분야가 달라도, 마감됐어도 판정을 보여준다 (저장 목록과 같은 취급).
        """
        raw = self._store.raw(policy.id)
        if raw is None:
            raise RuleEngineUnavailableError(f"정책을 찾을 수 없습니다: {policy.id}")

        evaluation, _ = engine.evaluate_policy(
            profile.model_dump(mode="json"), raw, self._clock()
        )
        evaluation.pop("_match_count", None)
        engine._assign_footnote_ids([evaluation])

        try:
            return PolicyEvaluation.model_validate(evaluation)
        except ValidationError as error:
            raise RuleEngineUnavailableError(
                f"판정 결과가 계약과 어긋납니다: {error}"
            ) from error

    def latest_hidden_unlikely(self) -> list[PolicyEvaluation]:
        """마지막 판정의 접힌 `unlikely` 목록.

        `RuleEngineResult` 에 담을 자리가 없어 따로 꺼낸다. 계약이 바뀌면 이 함수는 없어진다.
        """
        with self._lock:
            return list(self._hidden_unlikely)

    def latest_unknown_items(self) -> list[dict]:
        """마지막 판정의 미확인 항목 목록. AI A 의 후속 질문 입력이다."""
        with self._lock:
            return list((self._last_result or {}).get("unknown_items", []))


def build(
    path: str | Path,
    *,
    clock: Callable[[], date] = today_kst,
) -> tuple[PolicyStore, RuleEngineAdapter]:
    """서버가 한 줄로 붙일 수 있게 묶어 돌려준다.

        store, rule_engine = rules.repository.build("data/policies/policies.json")
        app = create_app(rule_engine=rule_engine, policy_catalog=store)
    """
    store = PolicyStore(path)
    return store, RuleEngineAdapter(store, clock=clock)


__all__ = ["PolicyStore", "RuleEngineAdapter", "build"]
