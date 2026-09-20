import logging

import pytest

from server.ai_gateway import (
    AIGateway,
    AIProviderError,
    AIProviderRequest,
    AIPurpose,
)
from server.pii import PiiType


class LoggingSpyProvider:
    def __init__(self) -> None:
        self.requests: list[AIProviderRequest] = []

    def invoke(self, request: AIProviderRequest) -> dict[str, bool]:
        self.requests.append(request)
        logging.getLogger("test.ai_provider").info(
            "masked AI request text=%s context=%s",
            request.text,
            request.context,
        )
        return {"ok": True}


@pytest.mark.parametrize(
    ("method_name", "purpose"),
    [
        ("interpret_message", AIPurpose.INTERPRET_MESSAGE),
        ("judge_exceptions", AIPurpose.JUDGE_EXCEPTIONS),
        ("compose_answer", AIPurpose.COMPOSE_ANSWER),
    ],
)
def test_every_ai_operation_masks_text_and_nested_context_before_provider(
    method_name: str,
    purpose: AIPurpose,
    caplog: pytest.LogCaptureFixture,
) -> None:
    resident_number = "900101-1234567"
    account_number = "110-123-456789"
    phone_number = "010-9876-5432"
    email = "private@example.com"
    provider = LoggingSpyProvider()
    gateway = AIGateway(provider)
    caplog.set_level(logging.INFO, logger="test.ai_provider")

    method = getattr(gateway, method_name)
    result = method(
        f"제 주민번호는 {resident_number}입니다.",
        context={
            "nested": {
                "payment": f"입금 계좌 {account_number}",
                "contacts": [phone_number, email],
            }
        },
    )

    request = provider.requests[0]
    assert request.purpose == purpose
    assert result.output == {"ok": True}
    assert result.masking.notice == "민감정보는 입력하지 말아 주세요"
    assert set(result.masking.detected_types) == {
        PiiType.RESIDENT_REGISTRATION_NUMBER,
        PiiType.ACCOUNT_NUMBER,
        PiiType.PHONE_NUMBER,
        PiiType.EMAIL,
    }
    for original in (resident_number, account_number, phone_number, email):
        assert original not in request.text
        assert original not in str(request.context)
        assert original not in caplog.text


def test_provider_error_and_logs_do_not_expose_original_pii(
    caplog: pytest.LogCaptureFixture,
) -> None:
    resident_number = "900101-1234567"

    class LeakyFailingProvider:
        def invoke(self, _request: AIProviderRequest) -> None:
            raise RuntimeError(f"provider payload contained {resident_number}")

    gateway = AIGateway(LeakyFailingProvider())
    caplog.set_level(logging.ERROR, logger="test.gateway")

    try:
        gateway.interpret_message(f"주민번호 {resident_number}")
    except AIProviderError as exc:
        logging.getLogger("test.gateway").exception("AI gateway failed")
        assert str(exc) == "AI provider call failed"
        assert exc.__suppress_context__ is True
    else:
        raise AssertionError("provider failure must be converted")

    assert resident_number not in caplog.text
