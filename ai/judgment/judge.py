"""예외 조건 판정 (`ai/judgment/README.md` 3~5장, 9장).

`prompt.py` · `client.py` · `schema.py` · `citation.py` 를 묶어 정책 하나의
예외 조건을 판정한다. **이 모듈은 절대 예외를 던지지 않는다.**
판정이 전부 실패해도 규칙 엔진 결과로 카드는 이미 화면에 있고, 여기서 예외가
새어 나가면 그 카드까지 사라진다.

시간 예산 (`docs/03-api-contract.md` 9장, `docs/research/04-llm-api-operations.md` 3장)
----------------------------------------------------------------------------------
정책당 8초. 그 안에서 호출 한 번은 6초로 잡고, 남는 2초를 재시도 여유로 둔다.

| 실패 | 재시도 | 이유 |
| --- | --- | --- |
| 형식 깨짐(`schema`) | 1회 | 같은 입력에 형식만 틀린 경우가 있어 한 번은 가치가 있다 |
| 서버 오류(`server`) | 1회 | 공식 권고는 지수 백오프지만 우리 예산에서는 한 번이 한계다 |
| 시간 초과(`timeout`) | **없음** | 재시도하면 또 그만큼 걸려 전체 20초 상한을 넘긴다 |
| 속도 제한(`rate_limit`) | **없음** | 대기 시간이 우리 예산보다 길다. 동시 요청 수를 줄이는 게 빠르다 |
| 용량 부족(`overloaded`) | **없음** | 재시도로 풀리지 않는다. **데모 모드로 넘길 신호다** |

재시도 여부는 `client.LLMError.retryable` 이 정한다. 이 모듈이 다시 판단하지 않는다.
남은 예산이 없으면 재시도할 수 있는 실패라도 넘어간다.

실패는 빈 목록이 아니다
-----------------------
`exceptions_text` 가 있는데 판정이 실패하면 **`unknown` 자리표시 조건**을 돌려준다.
조건 0개를 돌려주면 판정 상태 계산(`docs/01-glossary-profile.md` 4장)이
"모든 조건 `met`"으로 보고 `likely` 를 줄 수 있다. 실패가 사용자에게 유리한
방향으로 잘못 작용하는 것이다.

`exceptions_text` 가 **비어 있으면** 판정을 생략하고 빈 목록을 돌려준다.
이때는 예외 조건이 없는 정책이므로 조건 0개가 맞다.

동시 실행
---------
후보별로 동시에 보내되 상한을 둔다(기본 3). 후보 수만큼 무조건 늘리면 분당 요청
수와 토큰 수 한도에 걸린다(연구 문서 4장).
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from ai.judgment.citation import placeholder_unknown, verify_conditions
from ai.judgment.client import (
    KIND_OVERLOADED,
    KIND_UNKNOWN,
    ClaudeClient,
    LLMClient,
    LLMError,
)
from ai.judgment.prompt import JUDGE_SYSTEM_PROMPT, build_judge_prompt
from ai.judgment.schema import JUDGE_OUTPUT_SCHEMA, parse_conditions

#: 정책 하나에 쓸 수 있는 전체 시간 (`docs/03-api-contract.md` 9장 7단계)
DEFAULT_POLICY_BUDGET = 8.0

#: 호출 한 번의 제한. 남는 시간이 재시도 여유다.
DEFAULT_CALL_TIMEOUT = 6.0

#: 동시 판정 상한. 후보가 5건이어도 이 수만큼만 동시에 보낸다.
DEFAULT_MAX_WORKERS = 3


@dataclass
class JudgeStats:
    """지표용 기록 (`docs/03-api-contract.md` 13장).

    사용자 메시지 원문과 프로필은 남기지 않는다. 숫자만 센다.
    """

    calls: int = 0
    retries: int = 0
    successes: int = 0
    failures: int = 0
    skipped: int = 0
    parse_dropped: int = 0
    failure_kinds: Dict[str, int] = field(default_factory=dict)

    @property
    def overloaded(self) -> int:
        """용량 부족 건수. 0이 아니면 데모 모드로 넘길 신호다."""
        return self.failure_kinds.get(KIND_OVERLOADED, 0)

    def summary(self) -> str:
        lines = [
            f"판정 호출 {self.calls}회 (재시도 {self.retries}회)",
            f"성공 {self.successes} / 실패 {self.failures} / 생략 {self.skipped}",
            f"파싱에서 버린 항목 {self.parse_dropped}건",
        ]
        if self.failure_kinds:
            detail = ", ".join(
                f"{kind} {count}" for kind, count in sorted(self.failure_kinds.items())
            )
            lines.append(f"실패 종류: {detail}")
        if self.overloaded:
            lines.append("주의: 용량 부족이 있었다. 데모는 데모 모드로 돌린다")
        return "\n".join(lines)


class ExceptionJudge:
    """예외 조건 판정기.

    ``judge_policy`` 는 `scoring.Judge` 와 같은 모양이라 평가 하네스에 그대로 넣을 수 있다.

        from ai.judgment.scoring import evaluate
        report = evaluate(ExceptionJudge().judge_policy)

    **운영 경로에서는 ``judge_and_verify`` 를 쓴다.** 인용 검증까지 끝낸 조건을
    돌려주므로 원문에 없는 발췌가 화면으로 나갈 수 없다.
    ``judge_policy`` 만 쓰면 부르는 쪽이 검증을 잊을 수 있다.
    """

    def __init__(
        self,
        client: Optional[LLMClient] = None,
        *,
        policy_budget: float = DEFAULT_POLICY_BUDGET,
        call_timeout: float = DEFAULT_CALL_TIMEOUT,
        max_workers: int = DEFAULT_MAX_WORKERS,
    ) -> None:
        self._client = client if client is not None else ClaudeClient()
        self.policy_budget = policy_budget
        self.call_timeout = call_timeout
        self.max_workers = max(1, max_workers)
        self.stats = JudgeStats()
        self._lock = threading.Lock()

    # -- 기록 ---------------------------------------------------------------

    def _count(self, name: str, amount: int = 1) -> None:
        with self._lock:
            setattr(self.stats, name, getattr(self.stats, name) + amount)

    def _count_failure(self, kind: str) -> None:
        with self._lock:
            self.stats.failures += 1
            self.stats.failure_kinds[kind] = self.stats.failure_kinds.get(kind, 0) + 1

    # -- 판정 ---------------------------------------------------------------

    def judge_policy(
        self,
        exceptions_text: object,
        profile: Optional[Dict[str, Any]],
        raw_text: object,
    ) -> List[Dict[str, Any]]:
        """정책 하나의 예외 조건을 판정한다. **예외를 던지지 않는다.**

        돌려주는 조건은 아직 **인용 검증을 거치지 않았다.** 화면에 내보내기 전에
        `citation.verify_conditions()` 를 통과시켜야 한다. 운영 경로는
        ``judge_and_verify`` 를 쓰면 한 번에 끝난다.
        """
        if not exceptions_text or not str(exceptions_text).strip():
            # 예외 조건이 없는 정책이다. 판정을 생략한다 (README 9장).
            self._count("skipped")
            return []

        system = JUDGE_SYSTEM_PROMPT
        user = build_judge_prompt(str(exceptions_text), profile or {}, str(raw_text or ""))

        remaining = self.policy_budget
        attempt = 0
        last_kind = KIND_UNKNOWN

        while True:
            timeout = min(self.call_timeout, remaining)
            if timeout <= 0:
                break

            attempt += 1
            if attempt > 1:
                self._count("retries")
            self._count("calls")

            try:
                body = self._client.complete(
                    system=system,
                    user=user,
                    timeout=timeout,
                    schema=JUDGE_OUTPUT_SCHEMA,
                )
            except LLMError as exc:
                remaining -= timeout
                last_kind = exc.kind
                if exc.retryable and attempt == 1 and remaining > 0:
                    continue
                break
            except Exception:
                # 클라이언트가 LLMError 가 아닌 것을 던진 경우까지 흡수한다.
                # 판정 하나 때문에 응답 전체를 실패시키지 않는다.
                last_kind = KIND_UNKNOWN
                break

            outcome = parse_conditions(body)
            if outcome.dropped:
                self._count("parse_dropped", len(outcome.dropped))

            if outcome.ok:
                self._count("successes")
                return outcome.conditions

            # 형식이 깨졌다. 한 번은 다시 시도할 가치가 있다 (연구 문서 3장).
            remaining -= timeout
            last_kind = "schema"
            if attempt == 1 and remaining > 0:
                continue
            break

        self._count_failure(last_kind)
        return [placeholder_unknown(last_kind)]

    def judge_and_verify(
        self,
        policy: Dict[str, Any],
        profile: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """판정하고 인용 검증까지 끝낸다. **운영 경로는 이것을 쓴다.**

        ``policy`` 는 `docs/04-data-schema.md` 1장의 정책 데이터다.
        `exceptions_text` 와 `raw_text` 를 본다.

        돌려주는 것
            conditions  검증을 통과한 조건 목록. 발췌는 원문에서 되찾은 구간이다
            removed     인용 검증에서 제거된 발췌 기록. 지표용 (`docs/03-api-contract.md` 13장)
            policy_id   그대로 돌려준다. 여러 정책을 묶을 때 쓴다
        """
        raw_text = policy.get("raw_text") or ""
        conditions = self.judge_policy(
            policy.get("exceptions_text"), profile, raw_text
        )
        checked, removed = verify_conditions(conditions, raw_text)
        return {
            "policy_id": policy.get("id"),
            "conditions": checked,
            "removed": removed,
        }

    def judge_policies(
        self,
        policies: Sequence[Dict[str, Any]],
        profile: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """후보 정책들을 동시에 판정한다. 입력 순서를 유지해 돌려준다.

        동시 실행 수는 `max_workers` 로 제한한다. 후보 수만큼 무조건 늘리면
        분당 요청 수 한도에 걸린다 (연구 문서 4장).

        정책 하나가 실패해도 나머지는 그대로 돌아온다.
        """
        items = list(policies)
        if not items:
            return []
        if len(items) == 1:
            return [self.judge_and_verify(items[0], profile)]

        workers = min(self.max_workers, len(items))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(lambda p: self.judge_and_verify(p, profile), items))
