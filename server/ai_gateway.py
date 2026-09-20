"""The only approved boundary for calls to an external AI provider.

Routes and application services must depend on ``AIGateway`` rather than a raw
provider client. Every outbound text and JSON context value is passed through
``server.pii`` immediately before provider invocation.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol, cast

from pydantic import ConfigDict, Field, JsonValue

from server.pii import MASKING_NOTICE, MaskingResult, mask_pii, mask_structure
from server.schemas import ContractModel


class AIPurpose(StrEnum):
    INTERPRET_MESSAGE = "interpret_message"
    JUDGE_EXCEPTIONS = "judge_exceptions"
    COMPOSE_ANSWER = "compose_answer"


class AIProviderRequest(ContractModel):
    """Provider input that can only contain already-masked user text."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    purpose: AIPurpose
    text: str
    context: dict[str, JsonValue] = Field(default_factory=dict)


class AIProvider(Protocol):
    """Provider-specific adapter; routes must never depend on this directly."""

    def invoke(self, request: AIProviderRequest) -> JsonValue:
        """Perform one external model call with a masked request."""
        ...


class AIProviderError(RuntimeError):
    """Payload-free error safe to pass to the server error boundary."""


class AIInvocationResult(ContractModel):
    """Provider output plus metadata needed for a frontend privacy notice."""

    output: JsonValue
    masking: MaskingResult


class AIGateway:
    """Facade for the three permitted AI operations."""

    def __init__(self, provider: AIProvider) -> None:
        self._provider = provider

    def interpret_message(
        self,
        text: str,
        *,
        context: dict[str, JsonValue] | None = None,
    ) -> AIInvocationResult:
        return self._invoke(AIPurpose.INTERPRET_MESSAGE, text, context)

    def judge_exceptions(
        self,
        text: str,
        *,
        context: dict[str, JsonValue] | None = None,
    ) -> AIInvocationResult:
        return self._invoke(AIPurpose.JUDGE_EXCEPTIONS, text, context)

    def compose_answer(
        self,
        text: str,
        *,
        context: dict[str, JsonValue] | None = None,
    ) -> AIInvocationResult:
        return self._invoke(AIPurpose.COMPOSE_ANSWER, text, context)

    def _invoke(
        self,
        purpose: AIPurpose,
        text: str,
        context: dict[str, JsonValue] | None,
    ) -> AIInvocationResult:
        text_result = mask_pii(text)
        context_result = mask_structure(context or {})
        detected_types = tuple(
            dict.fromkeys(
                (*text_result.detected_types, *context_result.detected_types)
            )
        )
        masking = MaskingResult(
            masked_text=text_result.masked_text,
            detected_types=detected_types,
            notice=MASKING_NOTICE if detected_types else None,
        )
        masked_context = cast(dict[str, JsonValue], context_result.masked_value)
        request = AIProviderRequest(
            purpose=purpose,
            text=text_result.masked_text,
            context=masked_context,
        )

        try:
            output = self._provider.invoke(request)
        except Exception:
            # Do not chain SDK errors: their messages can contain prompt payloads.
            raise AIProviderError("AI provider call failed") from None

        return AIInvocationResult(output=output, masking=masking)


__all__ = [
    "AIGateway",
    "AIInvocationResult",
    "AIProviderError",
    "AIProviderRequest",
    "AIPurpose",
]
