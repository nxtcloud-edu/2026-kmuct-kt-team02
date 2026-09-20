"""`server.orchestrator.LLMClient` 구현체.

`LLMClient` 는 프로토콜만 있고 구현체가 없었다. 그래서 `BackendBOrchestrator` 를 조립할 수
없었고 `/chat` 은 어떤 경로로도 답변을 만들지 못했다. 이 파일이 그 자리를 채운다.

## 왜 `ai/gateway.py` 를 감싸는가

**캠프가 주는 것은 Anthropic 키가 아니라 OpenAI 호환 게이트웨이다** (`.env.example`).
그래서 `anthropic.Anthropic(...)` 으로 직접 부르면 `api.anthropic.com` 을 때려 401 이 난다.
화면에서는 그 실패가 "AI 가 고장났다"와 구별되지 않는다.

`ai/gateway.py` 의 `GatewayClient` 가 이미 그 게이트웨이를 상대한다. 기본 주소, 키 이름
여러 개(`API_KEY`, `LLM_API_KEY`, `CLAUDE_API_KEY`, …), 모델 별칭, 오류 분류를 모두 들고
있다. 여기서 설정을 다시 읽으면 환경변수 읽는 곳이 두 군데가 되고, 한쪽만 채운 상태로
배포되면 대화는 되는데 예외 판정만 조용히 실패한다. **설정 지점은 하나여야 한다.**

## 남은 두 가지 결정

**동기 호출을 `asyncio.to_thread` 로 감싼다.** `GatewayClient` 는 동기다. 이벤트 루프를
막지 않으려면 스레드로 넘겨야 한다.

**`stream_text` 는 청크를 하나만 내보낸다.** `GatewayClient.stream` 이 실제 스트리밍을
지원하지만 쓰지 않는다. 호출부(`BackendBOrchestrator._generate_answer`)가 `async for` 로
받은 청크를 **전부 모은 뒤** `finalize_answer` 에 넘기기 때문이다. 즉 토큰 단위로 받아도
사용자에게 더 빨리 도달하지 않는다. 화면의 타이핑 효과는 `finalize_answer` 가 문장 단위로
쪼갠 `answer_deltas` 가 만든다. 실제 스트리밍이 이득이 되려면 오케스트레이터가 버퍼링을
멈춰야 하고, 그건 이 파일이 아니라 그쪽에서 결정할 일이다. 없는 이득을 위해 동기 제너레이터를
비동기로 잇는 다리를 놓지 않는다.

## 키가 없을 때

생성은 성공하고 호출만 실패한다. 키 없는 환경에서도 서버는 떠야 하고, AI 가 전부 실패해도
규칙 기반 카드는 화면에 남아야 한다 (`docs/03-api-contract.md` 9장). 호출부가 예외를 삼켜
대체 문구로 넘어가므로, 원인은 로그에만 남는다. 무엇이 없는지는 `missing_settings()` 가
이름으로 알려준다.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Mapping

from ai import gateway
from ai.conversation import llm as conversation_llm
from server.orchestrator import StructuredLLMRequest, TextLLMRequest

_LOGGER = logging.getLogger(__name__)

#: 구조화 호출과 답변 호출에 주는 시간. 오케스트레이터 제한보다 짧게 잡는다.
#: (`OrchestratorTimeouts.interpretation_s` 3초, `answer_s` 15초)
DEFAULT_STRUCTURED_TIMEOUT_S = 2.5
DEFAULT_ANSWER_TIMEOUT_S = 12.0

_JSON_SYSTEM_SUFFIX = (
    "\n\nJSON 객체 하나만 출력한다. 설명, 머리말, 코드 펜스를 붙이지 않는다."
)


class LLMOutputError(RuntimeError):
    """호출은 됐지만 쓸 수 있는 결과를 받지 못했다."""


def missing_settings(env: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """모델을 부르기 위해 아직 없는 설정 **이름만** 돌려준다.

    키를 넣었는데 답변이 계속 대체 문구인 상황에서 가장 먼저 볼 값이다.
    값은 돌려주지 않는다. 로그나 `/health` 에 키가 새는 경로를 만들지 않는다.
    """
    return gateway.missing_settings(env)


def build_gateway_client(
    env: Mapping[str, str] | None = None,
) -> gateway.GatewayClient:
    """게이트웨이 호출기. **SDK 자체 재시도를 끈 것**을 돌려준다.

    설정은 그대로 `ai/gateway.load_config` 에서 온다. 읽는 곳을 늘리지 않는다.
    여기서 정하는 것은 값이 아니라 **전송 정책** 하나다.

    ## 왜 끄는가

    `GatewayClient` 독스트링은 "재시도하지 않는다. 재시도는 부르는 쪽 정책이다.
    여기서 또 하면 20초 예산이 조용히 두 배로 샌다" 라고 적어 두었다. 그런데 그 아래
    `openai` SDK 가 기본으로 `max_retries=2` 다. 그래서 한 번의 호출이 조용히 **최대 세
    번** 나간다.

    측정값이다. 구조화 호출에 2.5초를 주면 응답이 늦은 턴은 2.5초에 끊기고 SDK 가 다시
    부르고, 그 사이 오케스트레이터의 3초 제한이 먼저 터져 결과를 버린다. `asyncio.to_thread`
    는 취소되지 않으므로 버려진 스레드가 계속 게이트웨이를 때린다. 답변 호출도 같다 —
    12초짜리 시도가 한 번 늦으면 15초 제한에 걸려 고정 대체 문구로 끝난다. 실제로 한 턴이
    26초(3+8+15, 세 단계 제한의 합)까지 갔고 답변은 대체 문구였다.

    재시도를 끄면 **우리가 준 타임아웃이 곧 실제 상한**이 된다. 예산을 계산할 수 있게
    된다는 것이 이 함수의 전부다. 일시적 429/5xx 를 한 번 더 부르는 이득은 잃는다. 그
    재시도는 예산을 아는 호출부(`ai/judgment/judge.py`, `llm.CallSitePolicy`)가 할 일이다.

    근본 수정 위치는 `ai/gateway.py` 의 `OpenAI(...)` 생성 한 줄이다. 그 파일은 AI 파트
    소유라 건드리지 않고, 서버가 자기 예산을 지키는 선에서 주입으로 해결한다.
    """
    config = gateway.load_config(env)
    if config is None:
        # 설정이 없으면 호출 시점에 LLMError 가 난다. 그 경로를 그대로 둔다.
        return gateway.GatewayClient(env=env)

    try:
        from openai import OpenAI
    except ImportError:
        # `GatewayClient` 가 설치 안내를 담은 LLMError 를 내도록 그대로 넘긴다.
        return gateway.GatewayClient(config, env=env)

    return gateway.GatewayClient(
        config,
        env=env,
        sdk_client=OpenAI(
            base_url=config.base_url,
            api_key=config.api_key,
            max_retries=0,
        ),
    )


def extract_json_object(text: str) -> object:
    """모델 출력에서 JSON 객체를 꺼낸다.

    프롬프트로 "JSON 만" 지시해도 코드 펜스나 한 줄 설명이 섞여 오는 일이 있다.
    게이트웨이가 구조화 출력을 지원하는지 대회 가이드에 없어서(`ai/gateway.py`
    `complete` 독스트링) 스키마 강제를 걸 수 없고, 그래서 파싱이 방어선이다.
    값 검증은 호출부의 `validate_ai_json` 이 엄격한 Pydantic 모델로 한다.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        without_open = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        stripped = without_open.rsplit("```", 1)[0].strip()

    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end <= start:
        raise LLMOutputError("모델 출력에서 JSON 객체를 찾지 못했습니다")
    try:
        return json.loads(stripped[start : end + 1])
    except json.JSONDecodeError as error:
        raise LLMOutputError("모델 출력이 올바른 JSON 이 아닙니다") from error


class GatewayLLMClient:
    """`server.orchestrator.LLMClient` 를 만족한다.

    `client` 를 주입하면 실제 호출 없이 테스트할 수 있다. 주입한 객체는
    `complete(system=, user=, timeout=, temperature=)` 를 제공해야 한다.
    """

    def __init__(
        self,
        client: object | None = None,
        *,
        env: Mapping[str, str] | None = None,
        structured_timeout_s: float = DEFAULT_STRUCTURED_TIMEOUT_S,
        answer_timeout_s: float = DEFAULT_ANSWER_TIMEOUT_S,
    ) -> None:
        if structured_timeout_s <= 0 or answer_timeout_s <= 0:
            raise ValueError("타임아웃은 0 보다 커야 합니다")
        # 설정을 읽지 못해도 생성은 성공한다. 부족하면 호출 시점에 LLMError 가 난다.
        self._client = client if client is not None else build_gateway_client(env)
        # 실패를 기록할 때 같은 곳을 다시 본다. 여기서 실제 환경을 새로 읽으면
        # 주입한 설정과 로그의 판단이 어긋나, 테스트가 개발자 로컬 환경에 따라 갈린다.
        self._env = env
        self._structured_timeout_s = structured_timeout_s
        self._answer_timeout_s = answer_timeout_s

    async def complete_json(self, request: StructuredLLMRequest) -> object:
        """구조화 출력. 검증하지 않은 값을 그대로 돌려준다."""
        system = (
            f"너는 {request.task.value} 작업을 수행한다. "
            f"아래 JSON Schema 를 만족하는 결과만 만든다.\n"
            f"{json.dumps(request.output_schema, ensure_ascii=False)}"
            f"{_JSON_SYSTEM_SUFFIX}"
        )
        user = json.dumps(request.input, ensure_ascii=False)

        text = await self._complete(
            system=system,
            user=user,
            timeout=self._structured_timeout_s,
            temperature=conversation_llm.TEMPERATURE_STRUCTURED,
            task=request.task.value,
        )
        return extract_json_object(text)

    async def stream_text(self, request: TextLLMRequest) -> AsyncIterator[str]:
        """답변 본문. 청크를 하나만 내보낸다 (모듈 문서화 참고)."""
        text = await self._complete(
            system=request.system,
            user=request.user,
            timeout=self._answer_timeout_s,
            temperature=conversation_llm.TEMPERATURE_ANSWER,
            task=request.task.value,
        )
        if text:
            yield text

    async def _complete(
        self,
        *,
        system: str,
        user: str,
        timeout: float,
        temperature: float,
        task: str,
    ) -> str:
        try:
            return await asyncio.to_thread(
                self._client.complete,
                system=system,
                user=user,
                timeout=timeout,
                temperature=temperature,
            )
        except Exception:
            # 호출부가 예외를 삼키고 기본값·대체 문구로 넘어간다. 로그가 없으면 운영자는
            # 답변이 왜 계속 대체 문구인지 알 수 없다. 키 값은 남기지 않는다.
            gaps = missing_settings(self._env)
            if gaps:
                # 설정 누락은 예상된 상태다. 매 턴 스택 트레이스를 찍으면 진짜 오류가
                # 로그에 묻힌다. 무엇이 없는지 한 줄로 알린다.
                _LOGGER.warning(
                    "모델을 건너뜁니다: task=%s 없는 설정=%s", task, ", ".join(gaps)
                )
            else:
                _LOGGER.exception("모델 호출 실패: task=%s", task)
            raise


__all__ = [
    "GatewayLLMClient",
    "LLMOutputError",
    "build_gateway_client",
    "extract_json_object",
    "missing_settings",
]
