"""`server.orchestrator.LLMClient` 구현체 (Claude).

`LLMClient` 는 프로토콜만 있고 구현체가 없었다. 그래서 `BackendBOrchestrator` 를
조립할 수 없었고 `/chat` 은 어떤 경로로도 답변을 만들지 못했다. 이 파일이 그 자리를 채운다.

## 설계 결정

**환경변수 이름을 AI B(`ai/judgment/client.py`)와 공유한다.** `CLAUDE_API_KEY`,
`CLAUDE_MODEL` 을 그대로 읽는다. 서버용 변수를 따로 만들면 같은 키를 두 곳에 넣어야 하고,
한쪽만 채운 상태로 배포되면 대화는 되는데 예외 판정만 조용히 실패한다. 설정 지점은 하나여야 한다.

**`anthropic` 은 호출 시점에 지연 import 한다.** 패키지가 없어도 이 모듈을 import 할 수
있어야 서버 테스트가 SDK 설치 없이 돌아간다. AI B 가 같은 이유로 같은 선택을 했다.

**`stream_text` 는 청크를 하나만 내보낸다.** 스트리밍 API 를 쓰지 않는다. 호출부
(`BackendBOrchestrator._generate_answer`)가 `async for` 로 받은 청크를 **전부 모아서**
`finalize_answer` 에 넘기기 때문이다. 즉 지금 구조에서는 토큰 단위로 받아도 사용자에게
빨리 도달하지 않는다. 화면의 타이핑 효과는 `finalize_answer` 가 쪼갠 `answer_deltas` 가
만든다. 실제 스트리밍이 이득이 되려면 오케스트레이터가 버퍼링을 멈춰야 하고, 그건 이
파일이 아니라 그쪽에서 결정할 일이다. 없는 이득을 위해 스레드-큐 다리를 놓지 않는다.

**동기 SDK 를 `asyncio.to_thread` 로 감싼다.** Claude 의 동기 클라이언트를 쓰되 이벤트
루프를 막지 않는다. 비동기 클라이언트를 쓰지 않는 이유는 AI B 가 이미 동기 클라이언트를
쓰고 있어 두 경로의 오류 처리를 같은 모양으로 유지하기 위해서다.

**오류를 로그로 남긴다.** 호출부가 모든 예외를 삼키고 대체 문구로 넘어간다
(`orchestrator.py` `_interpret`, `_generate_answer`). 로그가 없으면 운영자는 답변이
왜 계속 대체 문구인지 알 수 없다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass

from server.orchestrator import StructuredLLMRequest, TextLLMRequest

_LOGGER = logging.getLogger(__name__)

DEFAULT_MAX_TOKENS = 1024
DEFAULT_TEMPERATURE = 0.0
DEFAULT_TIMEOUT_S = 12.0

_JSON_SYSTEM_SUFFIX = (
    "\n\nJSON 객체 하나만 출력한다. 설명, 머리말, 코드 펜스를 붙이지 않는다."
)


class LLMConfigError(RuntimeError):
    """키나 모델 이름이 없어 호출을 시작할 수 없다."""


class LLMCallError(RuntimeError):
    """호출은 했지만 쓸 수 있는 결과를 받지 못했다."""


@dataclass(frozen=True, slots=True)
class LLMConfig:
    """호출에 필요한 값. 키는 로그와 `/health` 에 절대 싣지 않는다."""

    api_key: str
    model: str
    max_tokens: int = DEFAULT_MAX_TOKENS
    temperature: float = DEFAULT_TEMPERATURE

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "LLMConfig":
        source = os.environ if environ is None else environ
        api_key = (
            source.get("CLAUDE_API_KEY", "").strip()
            or source.get("ANTHROPIC_API_KEY", "").strip()
        )
        if not api_key:
            raise LLMConfigError("CLAUDE_API_KEY 또는 ANTHROPIC_API_KEY 가 필요합니다")

        # 모델 이름에 기본값을 두지 않는다. 기본값을 두면 존재하지 않는 모델로
        # 조용히 호출해 404 를 받고, 원인이 설정 누락임을 알아채기 어렵다.
        model = source.get("CLAUDE_MODEL", "").strip()
        if not model:
            raise LLMConfigError("CLAUDE_MODEL 이 필요합니다")

        return cls(
            api_key=api_key,
            model=model,
            max_tokens=_positive_int(
                source.get("CLAUDE_MAX_TOKENS"), DEFAULT_MAX_TOKENS, "CLAUDE_MAX_TOKENS"
            ),
            temperature=_temperature(source.get("CLAUDE_TEMPERATURE")),
        )


def _positive_int(raw: str | None, default: int, name: str) -> int:
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError as error:
        raise LLMConfigError(f"{name} 는 정수여야 합니다") from error
    if value <= 0:
        raise LLMConfigError(f"{name} 는 1 이상이어야 합니다")
    return value


def _temperature(raw: str | None) -> float:
    if raw is None or not raw.strip():
        return DEFAULT_TEMPERATURE
    try:
        value = float(raw.strip())
    except ValueError as error:
        raise LLMConfigError("CLAUDE_TEMPERATURE 는 실수여야 합니다") from error
    if not 0.0 <= value <= 1.0:
        raise LLMConfigError("CLAUDE_TEMPERATURE 는 0.0 에서 1.0 사이여야 합니다")
    return value


def extract_json_object(text: str) -> object:
    """모델 출력에서 JSON 객체를 꺼낸다.

    프롬프트로 "JSON 만" 지시해도 코드 펜스나 한 줄 설명이 섞여 오는 일이 있다.
    호출부가 검증(`validate_ai_json`)을 하므로 여기서는 파싱까지만 책임진다.
    """
    stripped = text.strip()
    if stripped.startswith("```"):
        # ```json ... ``` 형태에서 첫 줄과 마지막 펜스를 떼어낸다
        without_open = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        stripped = without_open.rsplit("```", 1)[0].strip()

    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        pass

    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end <= start:
        raise LLMCallError("모델 출력에서 JSON 객체를 찾지 못했습니다")
    try:
        return json.loads(stripped[start : end + 1])
    except json.JSONDecodeError as error:
        raise LLMCallError("모델 출력이 올바른 JSON 이 아닙니다") from error


class ClaudeLLMClient:
    """`server.orchestrator.LLMClient` 를 만족하는 Claude 클라이언트.

    `sdk` 를 주입하면 실제 호출 없이 테스트할 수 있다. 주입한 객체는
    `messages.create(...)` 를 제공해야 한다.
    """

    def __init__(
        self,
        *,
        config: LLMConfig | None = None,
        sdk: object | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        if timeout_s <= 0:
            raise ValueError("timeout_s 는 0 보다 커야 합니다")
        self._config = config
        self._sdk = sdk
        self._timeout_s = timeout_s
        self._lock = asyncio.Lock()

    @property
    def config(self) -> LLMConfig:
        """설정을 처음 필요할 때 읽는다. 생성 시점에 키가 없어도 된다."""
        if self._config is None:
            self._config = LLMConfig.from_env()
        return self._config

    def _client(self) -> object:
        if self._sdk is not None:
            return self._sdk
        try:
            import anthropic  # 지연 import: 패키지가 없어도 이 모듈은 import 된다
        except ImportError as error:
            raise LLMConfigError(
                "anthropic 패키지가 필요합니다. 'pip install anthropic' 으로 설치하세요"
            ) from error
        self._sdk = anthropic.Anthropic(api_key=self.config.api_key)
        return self._sdk

    def _complete_text(self, *, system: str, user: str) -> str:
        """동기 호출 한 번. 스레드에서 실행된다."""
        config = self.config
        response = self._client().messages.create(
            model=config.model,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
            system=system,
            messages=[{"role": "user", "content": user}],
            timeout=self._timeout_s,
        )
        return _text_from_response(response)

    async def complete_json(self, request: StructuredLLMRequest) -> object:
        """구조화 출력. 검증하지 않은 값을 그대로 돌려준다.

        `output_schema` 를 프롬프트에 넣어 형태를 유도하되 강제하지는 않는다.
        Claude 메시지 API 에 스키마 강제가 없어서다. 형태 검증은 호출부의
        `validate_ai_json` 이 엄격한 Pydantic 모델로 한다.
        """
        system = (
            f"너는 {request.task.value} 작업을 수행한다. "
            f"아래 JSON Schema 를 만족하는 결과만 만든다.\n"
            f"{json.dumps(request.output_schema, ensure_ascii=False)}"
            f"{_JSON_SYSTEM_SUFFIX}"
        )
        user = json.dumps(request.input, ensure_ascii=False)

        try:
            text = await asyncio.to_thread(
                self._complete_text, system=system, user=user
            )
        except Exception:
            # 호출부가 예외를 삼키고 기본값으로 넘어간다. 원인은 로그에만 남는다.
            _LOGGER.exception("complete_json 호출 실패: task=%s", request.task.value)
            raise
        return extract_json_object(text)

    async def stream_text(self, request: TextLLMRequest) -> AsyncIterator[str]:
        """답변 본문. 청크를 하나만 내보낸다 (모듈 문서화 참고)."""
        try:
            text = await asyncio.to_thread(
                self._complete_text, system=request.system, user=request.user
            )
        except Exception:
            _LOGGER.exception("stream_text 호출 실패: task=%s", request.task.value)
            raise
        if text:
            yield text


def _text_from_response(response: object) -> str:
    """응답에서 텍스트 블록만 이어 붙인다.

    `content` 는 블록 리스트다. 도구 사용 블록 등 텍스트가 아닌 블록이 섞일 수 있어
    `text` 속성이 있는 것만 고른다.
    """
    content = getattr(response, "content", None)
    if content is None:
        raise LLMCallError("모델 응답에 content 가 없습니다")

    parts: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if text is None and isinstance(block, dict):
            text = block.get("text")
        if isinstance(text, str):
            parts.append(text)

    joined = "".join(parts).strip()
    if not joined:
        raise LLMCallError("모델 응답에 텍스트가 없습니다")
    return joined


__all__ = [
    "ClaudeLLMClient",
    "LLMCallError",
    "LLMConfig",
    "LLMConfigError",
    "extract_json_object",
]
