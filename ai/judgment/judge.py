"""예외 조건 판정 (`ai/judgment/README.md` 3~5장, 9장).

모델 호출은 **`ai/conversation/llm.py` 의 `Gateway` 로 한다.** 이 모듈에 호출부를
따로 두지 않는다.

왜 그쪽을 쓰는가
----------------
모델을 부르는 곳은 세 곳뿐이고(메시지 해석·예외 조건 판정·답변 작성) 그 셋이 전체
20초를 나눠 쓴다. 호출부를 담당자별로 두면 두 가지가 깨진다.

- **예산을 각자 계산한다.** 해석 3초 + 판정 8초 + 답변이 각각 자기 시계를 보면
  합쳐서 20초를 넘길 수 있다. `BudgetTracker` 하나를 공유해야 마지막 호출이
  남은 예산만큼만 쓴다
- 모델 이름과 환경 변수가 두 벌이 된다. 키를 한쪽만 설정하면 다른 쪽이 조용히 실패한다

`CALL_SITE_POLICIES[CallSite.JUDGE]` 에 우리 예산이 이미 들어 있다.
정책당 8초, 스키마 강제, 낮은 온도, 형식 오류만 1회 재시도, 폴백은 "그 정책의 예외
조건 전체를 미확인". 그래서 이 모듈은 **재시도도 타임아웃도 직접 다루지 않는다.**
`Gateway` 가 실패를 흡수해 폴백으로 돌려주므로 예외도 올라오지 않는다.

이 모듈이 하는 일
-----------------
1. 프롬프트를 만든다 (`prompt.py`)
2. `Gateway.call_structured(CallSite.JUDGE, ...)` 를 부른다
3. 결과를 조건 목록으로 바꾼다 (`schema.py`)
4. 실패를 `unknown` 자리표시로 바꾼다 (`citation.placeholder_unknown`)
5. 운영 경로에서는 인용 검증까지 끝낸다 (`citation.verify_conditions`)

실패는 빈 목록이 아니다
-----------------------
`exceptions_text` 가 있는데 판정이 실패하면 **`unknown` 자리표시 조건**을 돌려준다.
조건 0개를 돌려주면 판정 상태 계산(`docs/01-glossary-profile.md` 4장)이
"모든 조건 `met`"으로 보고 `likely` 를 줄 수 있다. 실패가 사용자에게 유리한
방향으로 잘못 작용하는 것이다.

`exceptions_text` 가 **비어 있으면** 판정을 생략하고 빈 목록을 돌려준다.
이때는 예외 조건이 없는 정책이므로 조건 0개가 맞다.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from ai.conversation.llm import (
    CallSite,
    Gateway,
    ModelCapacityError,
    run_parallel,
)
from ai.judgment.citation import placeholder_unknown, verify_conditions
from ai.judgment.prompt import JUDGE_SYSTEM_PROMPT, build_judge_prompt
from ai.judgment.schema import JUDGE_OUTPUT_SCHEMA, conditions_from_data

#: 판정 실패 시 `Gateway` 가 돌려줄 값. 조건이 없는 모양이고, 이 모듈이 자리표시로 바꾼다.
#: 폴백 값의 모양은 호출 지점마다 다르므로 `Gateway` 가 아니라 우리가 준다.
JUDGE_FALLBACK: Dict[str, Any] = {"conditions": []}

#: 모델은 성공했다고 했지만 조건을 하나도 못 뽑은 경우의 사유 코드.
REASON_NO_CONDITIONS = "no_conditions"

#: 어댑터가 형식을 지키지 않아 조건으로 바꿀 수 없는 경우.
REASON_BAD_SHAPE = "bad_shape"


@dataclass
class JudgeStats:
    """지표용 기록 (`docs/03-api-contract.md` 13장).

    호출 수·재시도·실패 종류는 `Gateway.metrics` 가 이미 센다. 여기서는 **그쪽이
    세지 않는 것만** 센다. 같은 것을 두 군데서 세면 어긋나는 순간 어느 쪽이 맞는지 모른다.

    사용자 메시지 원문과 프로필은 남기지 않는다. 숫자만 센다.
    """

    #: `exceptions_text` 가 비어 판정을 생략한 정책 수
    skipped: int = 0
    #: 조건을 돌려받은 정책 수
    judged: int = 0
    #: 자리표시로 떨어진 정책 수
    placeholders: int = 0
    #: 파싱에서 버린 항목 수
    parse_dropped: int = 0
    #: 자리표시 사유별 건수
    failure_reasons: Dict[str, int] = field(default_factory=dict)

    #: `Gateway` 지표를 함께 보기 위한 참조. 없으면 None
    gateway_metrics: Optional[Dict[str, Any]] = None

    @property
    def overloaded(self) -> int:
        """용량 부족 건수. 0이 아니면 데모 모드로 넘길 신호다.

        `Gateway` 가 센 값을 읽는다. 우리가 따로 세지 않는다.
        """
        direct = self.failure_reasons.get(ModelCapacityError.reason, 0)
        if self.gateway_metrics:
            failures = self.gateway_metrics.get("failures") or {}
            return failures.get(ModelCapacityError.reason, direct)
        return direct

    def summary(self) -> str:
        lines = [
            f"판정한 정책 {self.judged} / 자리표시 {self.placeholders} / 생략 {self.skipped}",
            f"파싱에서 버린 항목 {self.parse_dropped}건",
        ]
        if self.failure_reasons:
            detail = ", ".join(
                f"{reason} {count}"
                for reason, count in sorted(self.failure_reasons.items())
            )
            lines.append(f"실패 사유: {detail}")
        if self.gateway_metrics:
            lines.append(
                "모델 호출 {calls}회 (재시도 {retries}, 건너뜀 {skipped})".format(
                    calls=self.gateway_metrics.get("calls", 0),
                    retries=self.gateway_metrics.get("retries", 0),
                    skipped=self.gateway_metrics.get("skipped", 0),
                )
            )
        if self.overloaded:
            lines.append("주의: 용량 부족이 있었다. 데모는 데모 모드로 돌린다")
        return "\n".join(lines)


class ExceptionJudge:
    """예외 조건 판정기.

    ``judge_policy`` 는 `scoring.Judge` 와 같은 모양이라 평가 하네스에 그대로 넣을 수 있다.

        from ai.judgment.scoring import evaluate
        report = evaluate(ExceptionJudge(gateway).judge_policy)

    **운영 경로에서는 ``judge_and_verify`` 를 쓴다.** 인용 검증까지 끝낸 조건을
    돌려주므로 원문에 없는 발췌가 화면으로 나갈 수 없다.

    `Gateway` 는 **턴마다 새로 만든 것을 받는다.** 예산 추적기를 공유하기 때문이다.
    서버가 `/chat` 한 번에 하나를 만들어 해석·판정·답변에 같이 넘긴다.
    넘기지 않으면 어댑터 없는 `Gateway` 를 만들어 쓰고, 그때는 모든 정책이
    자리표시(`no_adapter`)로 떨어진다. 카드는 규칙 엔진 결과로 그대로 서 있다.
    """

    def __init__(
        self,
        gateway: Optional[Gateway] = None,
        *,
        max_workers: Optional[int] = None,
    ) -> None:
        self.gateway = gateway if gateway is not None else Gateway(adapter=None)
        self.max_workers = max_workers
        self.stats = JudgeStats()
        self._lock = threading.Lock()

    # -- 기록 ---------------------------------------------------------------

    def _count(self, name: str, amount: int = 1) -> None:
        with self._lock:
            setattr(self.stats, name, getattr(self.stats, name) + amount)

    def _count_placeholder(self, reason: str) -> None:
        with self._lock:
            self.stats.placeholders += 1
            self.stats.failure_reasons[reason] = (
                self.stats.failure_reasons.get(reason, 0) + 1
            )

    def stats_snapshot(self) -> JudgeStats:
        """`Gateway` 지표를 붙인 기록. 14:30 이후 지표 정리에 쓴다."""
        with self._lock:
            self.stats.gateway_metrics = self.gateway.metrics_snapshot()
            return self.stats

    # -- 판정 ---------------------------------------------------------------

    def judge_policy(
        self,
        exceptions_text: object,
        profile: Optional[Dict[str, Any]],
        raw_text: object,
    ) -> List[Dict[str, Any]]:
        """정책 하나의 예외 조건을 판정한다. **예외를 던지지 않는다.**

        재시도와 타임아웃은 `Gateway` 가 정책표대로 처리한다. 이 함수는 결과를
        조건 목록으로 바꾸고 실패를 자리표시로 흡수하는 일만 한다.

        돌려주는 조건은 아직 **인용 검증을 거치지 않았다.** 화면에 내보내기 전에
        `citation.verify_conditions()` 를 통과시켜야 한다. 운영 경로는
        ``judge_and_verify`` 를 쓰면 한 번에 끝난다.
        """
        if not exceptions_text or not str(exceptions_text).strip():
            # 예외 조건이 없는 정책이다. 판정을 생략한다 (README 9장).
            self._count("skipped")
            return []

        user = build_judge_prompt(
            str(exceptions_text), profile or {}, str(raw_text or "")
        )

        try:
            outcome = self.gateway.call_structured(
                CallSite.JUDGE,
                system=JUDGE_SYSTEM_PROMPT,
                user=user,
                schema=JUDGE_OUTPUT_SCHEMA,
                fallback=JUDGE_FALLBACK,
            )
        except Exception:
            # Gateway 는 실패를 흡수하도록 만들어져 있지만, 그래도 새어 나오는 경우까지
            # 막는다. 판정 하나 때문에 규칙 엔진이 띄운 카드가 사라지면 안 된다.
            return self._placeholder(REASON_BAD_SHAPE)

        if not outcome.ok:
            return self._placeholder(outcome.failure or REASON_BAD_SHAPE)

        parsed = conditions_from_data(outcome.data)
        if parsed.dropped:
            self._count("parse_dropped", len(parsed.dropped))

        if not parsed.ok:
            return self._placeholder(parsed.error or REASON_BAD_SHAPE)

        if not parsed.conditions:
            # 예외 문장이 있는데 조건을 하나도 못 뽑았다. 빈 목록으로 두면 조건 0개가 되어
            # 판정 상태가 likely 로 갈 수 있다. 판단이 갈릴 때는 unknown 쪽으로 둔다.
            return self._placeholder(REASON_NO_CONDITIONS)

        self._count("judged")
        return parsed.conditions

    def _placeholder(self, reason: str) -> List[Dict[str, Any]]:
        self._count_placeholder(reason)
        return [placeholder_unknown(reason)]

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

        동시 실행 수는 `ai/conversation/llm.py` 의 `run_parallel` 이 제한한다.
        기본값은 설정(`KMUCT_LLM_MAX_CONCURRENCY`)에서 읽고, 없으면 3이다.
        후보 수만큼 무조건 늘리면 분당 요청 수와 토큰 수 한도에 걸린다.

        정책 하나가 실패해도 나머지는 그대로 돌아온다. `judge_policy` 가 예외를
        던지지 않으므로 `run_parallel` 의 예외 처리까지 갈 일은 없다.
        """
        items = list(policies)
        if not items:
            return []
        if len(items) == 1:
            return [self.judge_and_verify(items[0], profile)]

        # 같은 정책 번호가 두 번 들어와도 결과를 잃지 않게 위치로 키를 만든다.
        tasks = {
            str(index): (lambda p=policy: self.judge_and_verify(p, profile))
            for index, policy in enumerate(items)
        }
        results = run_parallel(tasks, max_workers=self.max_workers)

        out: List[Dict[str, Any]] = []
        for index, policy in enumerate(items):
            item = results.get(str(index))
            if item is not None and item.ok and item.value is not None:
                out.append(item.value)
                continue
            # run_parallel 이 시간 초과로 결과를 못 받은 경우. 카드는 유지되어야 한다.
            reason = (item.failure if item is not None else None) or REASON_BAD_SHAPE
            self._count_placeholder(reason)
            out.append(
                {
                    "policy_id": policy.get("id"),
                    "conditions": [placeholder_unknown(reason)],
                    "removed": [],
                }
            )
        return out
