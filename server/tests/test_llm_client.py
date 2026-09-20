"""`server/llm_client.py` 계약 고정.

실제 게이트웨이를 부르지 않는다. `complete` 를 흉내낸 객체를 주입해 무엇이 넘어가고
응답에서 무엇을 꺼내는지만 고정한다. 키가 없는 환경에서도 전부 통과해야 한다.
"""

import asyncio

import pytest

from ai.conversation import llm as conversation_llm
from server.llm_client import (
    GatewayLLMClient,
    LLMOutputError,
    extract_json_object,
    missing_settings,
)
from server.orchestrator import LLMTask, StructuredLLMRequest, TextLLMRequest


class FakeGateway:
    """`ai.gateway.GatewayClient.complete` 만 흉내낸다."""

    def __init__(self, text: str = '{"intent": "find_policy"}') -> None:
        self._text = text
        self.calls: list[dict[str, object]] = []

    def complete(self, **kwargs: object) -> str:
        self.calls.append(kwargs)
        return self._text


class FailingGateway:
    def complete(self, **_kwargs: object) -> str:
        raise RuntimeError("게이트웨이 거부")


def structured_request() -> StructuredLLMRequest:
    return StructuredLLMRequest(
        task=LLMTask.INTERPRET_MESSAGE,
        input={"message": "휴학했어요"},
        output_schema={"type": "object"},
    )


def text_request() -> TextLLMRequest:
    return TextLLMRequest(
        task=LLMTask.COMPOSE_ANSWER, system="안내", user="설명해줘"
    )


def collect(client: GatewayLLMClient, request: TextLLMRequest) -> list[str]:
    async def run() -> list[str]:
        return [chunk async for chunk in client.stream_text(request)]

    return asyncio.run(run())


# --- 설정 -----------------------------------------------------------------


def test_missing_settings_names_the_camp_gateway_values() -> None:
    """`.env.example` 이 정한 이름이어야 한다. 서버 전용 이름을 새로 만들면 설정이 두 곳이 된다."""
    assert missing_settings({}) == ("API_KEY", "LLM_MODEL")


def test_missing_settings_is_empty_when_both_values_exist() -> None:
    assert missing_settings({"API_KEY": "sk-not-real", "LLM_MODEL": "bedrock-haiku"}) == ()


def test_construction_succeeds_without_credentials() -> None:
    """키가 없어도 서버는 떠야 한다. 규칙 기반 카드는 AI 없이도 화면에 남는다."""
    assert GatewayLLMClient(env={}) is not None


def test_rejects_non_positive_timeouts() -> None:
    with pytest.raises(ValueError):
        GatewayLLMClient(FakeGateway(), structured_timeout_s=0)


# --- JSON 추출 ------------------------------------------------------------


def test_extract_json_object_reads_a_plain_object() -> None:
    assert extract_json_object('{"a": 1}') == {"a": 1}


def test_extract_json_object_strips_a_code_fence() -> None:
    assert extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_object_finds_an_object_inside_prose() -> None:
    """게이트웨이에 스키마 강제를 걸 수 없어 파싱이 방어선이다."""
    assert extract_json_object('네, 결과입니다: {"a": 1} 이상입니다.') == {"a": 1}


def test_extract_json_object_rejects_output_without_an_object() -> None:
    with pytest.raises(LLMOutputError):
        extract_json_object("모르겠습니다")


def test_extract_json_object_rejects_a_broken_object() -> None:
    with pytest.raises(LLMOutputError):
        extract_json_object('{"a": }')


# --- 구조화 호출 ----------------------------------------------------------


def test_complete_json_returns_parsed_output() -> None:
    client = GatewayLLMClient(FakeGateway('{"intent": "find_policy"}'))

    assert asyncio.run(client.complete_json(structured_request())) == {
        "intent": "find_policy"
    }


def test_complete_json_sends_the_schema_and_a_zero_temperature() -> None:
    gateway = FakeGateway()
    client = GatewayLLMClient(gateway)

    asyncio.run(client.complete_json(structured_request()))

    call = gateway.calls[0]
    assert "object" in str(call["system"])
    assert call["user"] == '{"message": "휴학했어요"}'
    assert call["temperature"] == conversation_llm.TEMPERATURE_STRUCTURED


def test_structured_timeout_stays_under_the_orchestrator_limit() -> None:
    """오케스트레이터가 3초에 끊는다. 더 길게 잡으면 부분 결과까지 버려진다."""
    gateway = FakeGateway()

    asyncio.run(GatewayLLMClient(gateway).complete_json(structured_request()))

    assert float(gateway.calls[0]["timeout"]) < 3.0


def test_complete_json_propagates_failures_for_the_caller_to_absorb() -> None:
    """호출부가 예외를 삼켜 기본값으로 넘어간다. 여기서 숨기면 두 번 숨기는 셈이다."""
    client = GatewayLLMClient(FailingGateway())

    with pytest.raises(RuntimeError):
        asyncio.run(client.complete_json(structured_request()))


# --- 답변 호출 ------------------------------------------------------------


def test_stream_text_yields_one_chunk_with_the_whole_answer() -> None:
    """호출부가 청크를 전부 모아 쓰므로 토큰 단위로 쪼갤 이유가 없다."""
    client = GatewayLLMClient(FakeGateway("서울 청년수당은 미취업 청년이 대상이에요."))

    assert collect(client, text_request()) == ["서울 청년수당은 미취업 청년이 대상이에요."]


def test_stream_text_uses_the_answer_temperature() -> None:
    """답변은 구조화 출력보다 온도를 올린다. 같은 값을 쓰면 문장이 딱딱해진다."""
    gateway = FakeGateway("답변")
    client = GatewayLLMClient(gateway)

    collect(client, text_request())

    assert gateway.calls[0]["temperature"] == conversation_llm.TEMPERATURE_ANSWER


def test_answer_timeout_stays_under_the_orchestrator_limit() -> None:
    gateway = FakeGateway("답변")

    collect(GatewayLLMClient(gateway), text_request())

    assert float(gateway.calls[0]["timeout"]) < 15.0


def test_stream_text_yields_nothing_for_an_empty_answer() -> None:
    """`AnswerDeltaEventData.delta` 는 min_length=1 이다. 빈 조각을 보내면 검증에서 막힌다."""
    client = GatewayLLMClient(FakeGateway(""))

    assert collect(client, text_request()) == []


def test_stream_text_propagates_failures() -> None:
    client = GatewayLLMClient(FailingGateway())

    with pytest.raises(RuntimeError):
        collect(client, text_request())


# --- 설정 누락과 실제 오류를 로그에서 구분한다 -----------------------------


def test_missing_settings_logs_a_warning_without_a_traceback(caplog) -> None:
    """설정 누락은 예상된 상태다. 매 턴 스택 트레이스를 찍으면 진짜 오류가 묻힌다."""
    client = GatewayLLMClient(env={})

    with caplog.at_level("WARNING", logger="server.llm_client"):
        with pytest.raises(Exception):
            asyncio.run(client.complete_json(structured_request()))

    records = [record for record in caplog.records if record.name == "server.llm_client"]
    assert len(records) == 1
    assert records[0].levelname == "WARNING"
    assert records[0].exc_info is None
    assert "API_KEY" in records[0].getMessage()


def test_a_real_failure_is_logged_with_its_traceback(caplog) -> None:
    """설정이 갖춰졌는데 실패한 것은 조사할 대상이다."""
    client = GatewayLLMClient(
        FailingGateway(), env={"API_KEY": "sk-not-real", "LLM_MODEL": "bedrock-haiku"}
    )

    with caplog.at_level("ERROR", logger="server.llm_client"):
        with pytest.raises(RuntimeError):
            asyncio.run(client.complete_json(structured_request()))

    records = [record for record in caplog.records if record.name == "server.llm_client"]
    assert records
    assert records[0].exc_info is not None


def test_no_log_message_contains_a_key_value(caplog) -> None:
    """키가 로그로 새면 저장소나 배포 로그에 남는다."""
    client = GatewayLLMClient(
        FailingGateway(), env={"API_KEY": "sk-secret-value", "LLM_MODEL": "bedrock-haiku"}
    )

    with caplog.at_level("WARNING", logger="server.llm_client"):
        with pytest.raises(RuntimeError):
            asyncio.run(client.complete_json(structured_request()))

    assert all("sk-secret-value" not in record.getMessage() for record in caplog.records)
