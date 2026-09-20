"""`server.orchestrator.ExceptionJudge` 구현체 (AI B 연결).

프로토콜 docstring 이 "no production implementation exists in the repository yet" 이라고
적어 둔 그 자리를 채운다. AI B(`ai/judgment/`)는 완성돼 있었지만 서버 계약과 모양이 달라
아무도 부를 수 없었다.

## 맞춰야 하는 차이 네 가지

**1. 동기 대 비동기.** AI B 는 전부 동기다. `judge_policies` 는 내부에서
`ThreadPoolExecutor` 를 쓴다. 프로토콜은 `async def judge` 를 요구하므로
`asyncio.to_thread` 로 감싼다. 서버는 이미 `asyncio.gather` 로 정책별 병렬 호출을 하니
(`orchestrator._apply_exception_judgments`) 여기서는 한 건씩 처리한다.

**2. 결과 값 표기.** AI B 는 영문 `met`/`unmet`/`unknown` 을 쓴다
(`ai/judgment/values.py`). 서버의 AI 입력 경계 `AIExceptionCondition` 은 한국어
`충족`/`미충족`/`미확인` 만 받는다. 최종 DTO 는 다시 영문이라 헷갈리기 쉽지만, 이 어댑터가
넘겨야 하는 값은 **한국어**다. 변환하지 않으면 전건 `ValidationError` 가 나고
`orchestrator._judge_one` 의 `except` 가 모두 미확인으로 떨궈 카드 전체가 "확인이 필요해요"
가 된다. 동작은 하는데 결론이 없는 상태가 최악이다.

**3. 요약 키 이름.** AI B 내부 표준은 `name` 이고 `summary` 도 같이 채워 준다.
서버는 `summary` 를 읽는다. 그래서 `summary` 를 우선 보되 없으면 `name` 으로 대체한다.

**4. 남는 키.** `ContractModel` 은 `extra="forbid"` 다. AI B 가 붙이는 `judged_by`,
`excerpt_verified`, `excerpt_truncated`, `placeholder`, `placeholder_reason`,
`name_trimmed` 를 그대로 넘기면 검증에서 막힌다. 필요한 네 개만 골라 새 dict 를 만든다.

## 예산을 서버 제한보다 짧게 잡는다

서버는 판정에 8초 제한을 건다 (`OrchestratorTimeouts.judgment_s`). AI B 기본값도 8초라
동시에 만료되면 서버 타임아웃이 먼저 터져 AI B 가 만든 부분 결과까지 버려진다. 예산을
6초로 줄여 AI B 가 스스로 미확인 자리표시자를 돌려줄 시간을 남긴다. 빈손보다 낫다.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from ai.gateway import GatewayClient
from ai.judgment.judge import ExceptionJudge as AiBExceptionJudge
from server.orchestrator import ExceptionJudgeRequest

_LOGGER = logging.getLogger(__name__)

ASK_NOTICE = "공고 확인 필요"
DEFAULT_POLICY_BUDGET_S = 6.0

#: AI B 영문 결과 → 서버 AI 경계의 한국어 결과
_RESULT_TO_KOREAN = {
    "met": "충족",
    "unmet": "미충족",
    "unknown": "미확인",
}

#: `AIExceptionCondition` 의 제약. 넘기기 전에 여기서 맞춰 둔다.
_SUMMARY_MAX = 20
_EXCERPT_MIN = 10
_EXCERPT_MAX = 150
_NEEDED_FIELD_MAX = 64


def _to_contract_condition(item: dict[str, Any]) -> dict[str, Any]:
    """AI B 조건 한 건을 `AIExceptionCondition` 이 받는 모양으로 바꾼다."""
    raw_summary = item.get("summary") or item.get("name") or ""
    # 자리표시자 조건은 이름이 빈 문자열이다. `min_length=1` 에 걸리므로 대체 문구를 넣는다.
    summary = str(raw_summary).strip()[:_SUMMARY_MAX] or ASK_NOTICE

    result = _RESULT_TO_KOREAN.get(str(item.get("result", "")).strip(), "미확인")

    excerpt = _clean_excerpt(item)

    raw_needed = item.get("needed_field")
    needed_field = str(raw_needed).strip()[:_NEEDED_FIELD_MAX] if raw_needed else None
    # 미확인인데 물어볼 항목이 없으면 후속 질문을 만들 수 없다. 공고를 보라고 알린다.
    if result == "미확인" and not needed_field:
        needed_field = ASK_NOTICE

    return {
        "summary": summary,
        "result": result,
        "excerpt": excerpt,
        "needed_field": needed_field,
    }


def _clean_excerpt(item: dict[str, Any]) -> str | None:
    """쓸 수 없는 발췌는 버린다.

    발췌가 빠져도 조건 자체는 살아남고 각주만 사라진다. 반대로 잘못된 발췌를 넘기면
    근거로 제시된 문장이 공고에 없는 상태가 되므로, 의심스러우면 버리는 쪽을 택한다.

    길이를 다시 재는 이유: AI B 는 공백을 정규화한 문자열로 10~150 자를 확인하지만
    돌려주는 값은 원문 구간이다. 줄바꿈이 많은 공고에서는 원문 구간이 150 자를 넘길 수 있다.
    """
    raw = item.get("excerpt")
    if not raw:
        return None
    # `judge_and_verify` 는 대조 결과를 함께 준다. 실패한 발췌는 근거가 될 수 없다.
    if "excerpt_verified" in item and not item["excerpt_verified"]:
        return None
    excerpt = str(raw)
    if not _EXCERPT_MIN <= len(excerpt) <= _EXCERPT_MAX:
        return None
    return excerpt


class AiBExceptionJudgeAdapter:
    """`server.orchestrator.ExceptionJudge` 프로토콜 구현.

    `judge` 를 주입하면 실제 모델 호출 없이 테스트할 수 있다. 주입한 객체는
    `judge_and_verify(policy, profile, *, deadline=None)` 을 제공해야 한다.
    """

    def __init__(
        self,
        judge: Any | None = None,
        *,
        policy_budget: float = DEFAULT_POLICY_BUDGET_S,
    ) -> None:
        if judge is not None:
            self._judge = judge
            return
        # 클라이언트를 반드시 명시한다. 생략하면 AI B 가 `ClaudeClient` 를 만들어
        # api.anthropic.com 으로 직접 붙는다. 캠프가 주는 것은 OpenAI 호환
        # 게이트웨이라(.env.example) 그 경로는 401 로 끝나고, 화면에서는 그 실패가
        # "AI 가 고장났다"와 구별되지 않는다. 답변과 판정이 같은 게이트웨이를 써야 한다.
        self._judge = AiBExceptionJudge(
            GatewayClient(),
            policy_budget=policy_budget,
            batch_budget=policy_budget,
        )

    async def judge(self, request: ExceptionJudgeRequest) -> object:
        """정책 한 건의 예외 조건을 판정한다.

        `request.output_schema` 는 쓰지 않는다. AI B 의 Claude 클라이언트가
        `supports_schema_enforcement = False` 라 스키마를 적용하지 않고, 형태 확인은
        AI B 내부 파서가 한다. 서버는 그 결과를 다시 엄격하게 검증한다.
        """
        policy = {
            "id": request.policy_id,
            "exceptions_text": request.exceptions_text,
            "raw_text": request.raw_text,
        }

        outcome = await asyncio.to_thread(
            self._judge.judge_and_verify,
            policy,
            request.profile,  # 영문 키 dict 그대로. AI B 가 같은 키를 읽는다
        )

        conditions = outcome.get("conditions") or []
        removed = outcome.get("removed") or []
        if removed:
            # 발췌 대조에 실패한 조건 수. 근거 없는 문장이 화면에 나가지 않았다는 기록이다.
            _LOGGER.info(
                "발췌 대조 실패로 제외: policy_id=%s count=%d",
                request.policy_id,
                len(removed),
            )

        return {
            "conditions": [
                _to_contract_condition(item)
                for item in conditions
                if isinstance(item, dict)
            ]
        }


__all__ = ["ASK_NOTICE", "AiBExceptionJudgeAdapter"]
