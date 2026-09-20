"""`server/llm_client.py` 계약 고정.

실제 모델을 부르지 않는다. SDK 를 주입해 `messages.create` 에 무엇이 넘어가고 응답에서
무엇을 꺼내는지만 고정한다. `anthropic` 패키지가 없는 환경에서도 전부 통과해야 한다.
"""

import asyncio

import pytest

from server.llm_client import (
    DEFAULT_MAX_TOKENS,
    ClaudeLLMClient,
    LLMCallError,
    LLMConfig,
    LLMConfigError,
    extract_json_object,
)
from server.orchestrator import LLMTask, StructuredLLMRequest, TextLLMRequest


class Block:
    """`messages.create` 응답의 텍스트 블록."""

    def __init__(self, text: str) -> None:
        self.text = text


class Response:
    def __init__(self, content: list[object]) -> None:
        self.content = content


class FakeMessages:
    def __init__(self, content: list[object]) -> None:
        self._content = content
        self.calls: list[dict[str, object]] = []

    def create(self, **kwargs: object) -> Response:
        self.calls.append(kwargs)
        return Response(self._content)


class FakeSDK:
    def __init__(self, text: str = '{"intent": "find_policy"}') -> None:
        self.messages = FakeMessages([Block(text)])


def config() -> LLMConfig:
    return LLMConfig(api_key="key-not-real", model="claude-test", max_tokens=256)


def structured_request() -> StructuredLLMRequest:
    return StructuredLLMRequest(
        task=LLMTask.INTERPRET_MESSAGE,
        input={"message": "휴학했어요"},
        output_schema={"type": "object"},
    )


# --- 설정 -----------------------------------------------------------------


def test_config_requires_api_key() -> None:
    with pytest.raises(LLMConfigError):
        LLMConfig.from_env({"CLAUDE_MODEL": "claude-test"})


def test_config_requires_model_without_falling_back_to_a_default() -> None:
    """모델 이름에 기본값을 두면 없는 모델로 조용히 호출해 원인을 숨긴다."""
    with pytest.raises(LLMConfigError):
        LLMConfig.from_env({"CLAUDE_API_KEY": "key-not-real"})


def test_config_accepts_anthropic_api_key_as_an_alias() -> None:
    resolved = LLMConfig.from_env(
        {"ANTHROPIC_API_KEY": "key-not-real", "CLAUDE_MODEL": "claude-test"}
    )
    assert resolved.model == "claude-test"
    assert resolved.max_tokens == DEFAULT_MAX_TOKENS
    assert resolved.temperature == 0.0


def test_config_rejects_out_of_range_temperature() -> None:
    with pytest.raises(LLMConfigError):
        LLMConfig.from_env(
            {
                "CLAUDE_API_KEY": "key-not-real",
                "CLAUDE_MODEL": "claude-test",
                "CLAUDE_TEMPERATURE": "1.5",
            }
        )


def test_config_rejects_non_integer_max_tokens() -> None:
    with pytest.raises(LLMConfigError):
        LLMConfig.from_env(
            {
                "CLAUDE_API_KEY": "key-not-real",
                "CLAUDE_MODEL": "claude-test",
                "CLAUDE_MAX_TOKENS": "많이",
            }
        )


# --- JSON 추출 ------------------------------------------------------------


def test_extract_json_object_reads_a_plain_object() -> None:
    assert extract_json_object('{"a": 1}') == {"a": 1}


def test_extract_json_object_strips_a_code_fence() -> None:
    fenced = '```json\n{"a": 1}\n```'
    assert extract_json_object(fenced) == {"a": 1}


def test_extract_json_object_finds_an_object_inside_prose() -> None:
    """JSON 만 달라고 해도 한 줄 설명이 섞여 오는 일이 있다."""
    assert extract_json_object('네, 결과입니다: {"a": 1} 이상입니다.') == {"a": 1}


def test_extract_json_object_rejects_output_without_an_object() -> None:
    with pytest.raises(LLMCallError):
        extract_json_object("모르겠습니다")


# --- 호출 -----------------------------------------------------------------


def test_complete_json_returns_parsed_output_and_sends_config_values() -> None:
    sdk = FakeSDK('{"intent": "find_policy"}')
    client = ClaudeLLMClient(config=config(), sdk=sdk)

    result = asyncio.run(client.complete_json(structured_request()))

    assert result == {"intent": "find_policy"}
    call = sdk.messages.calls[0]
    assert call["model"] == "claude-test"
    assert call["max_tokens"] == 256
    assert call["temperature"] == 0.0
    # 스키마를 프롬프트에 실어 형태를 유도한다. 강제는 호출부 검증이 한다.
    assert "object" in str(call["system"])
    assert call["messages"] == [{"role": "user", "content": '{"message": "휴학했어요"}'}]


def test_complete_json_propagates_failures_for_the_caller_to_absorb() -> None:
    """호출부가 예외를 삼키고 기본값으로 넘어간다. 여기서 숨기면 두 번 숨기는 셈이다."""

    class Failing:
        class messages:  # noqa: N801 - SDK 모양을 흉내낸다
            @staticmethod
            def create(**_kwargs: object) -> Response:
                raise RuntimeError("상한 응답")

    client = ClaudeLLMClient(config=config(), sdk=Failing())
    with pytest.raises(RuntimeError):
        asyncio.run(client.complete_json(structured_request()))


def test_stream_text_yields_one_chunk_with_the_whole_answer() -> None:
    """호출부가 청크를 전부 모아 쓰므로 토큰 단위로 쪼갤 이유가 없다."""
    sdk = FakeSDK("서울 청년수당은 미취업 청년을 대상으로 해요.")
    client = ClaudeLLMClient(config=config(), sdk=sdk)

    async def collect() -> list[str]:
        request = TextLLMRequest(
            task=LLMTask.COMPOSE_ANSWER, system="안내", user="설명해줘"
        )
        return [chunk async for chunk in client.stream_text(request)]

    assert asyncio.run(collect()) == ["서울 청년수당은 미취업 청년을 대상으로 해요."]


def test_response_text_skips_blocks_without_text() -> None:
    sdk = FakeSDK()
    sdk.messages = FakeMessages([object(), {"text": "본문"}, Block("추가")])
    client = ClaudeLLMClient(config=config(), sdk=sdk)

    async def collect() -> list[str]:
        request = TextLLMRequest(task=LLMTask.COMPOSE_ANSWER, user="설명해줘")
        return [chunk async for chunk in client.stream_text(request)]

    assert asyncio.run(collect()) == ["본문추가"]


def test_empty_response_is_an_error_rather_than_an_empty_answer() -> None:
    sdk = FakeSDK()
    sdk.messages = FakeMessages([Block("   ")])
    client = ClaudeLLMClient(config=config(), sdk=sdk)

    async def collect() -> list[str]:
        request = TextLLMRequest(task=LLMTask.COMPOSE_ANSWER, user="설명해줘")
        return [chunk async for chunk in client.stream_text(request)]

    with pytest.raises(LLMCallError):
        asyncio.run(collect())


def test_client_construction_does_not_need_credentials() -> None:
    """생성 시점에 키를 읽으면 키 없는 환경에서 서버가 아예 뜨지 않는다."""
    client = ClaudeLLMClient()
    assert client is not None
