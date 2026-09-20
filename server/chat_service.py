"""Provider-agnostic chat pipeline boundary used by the SSE transport."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated, Literal, Protocol
from uuid import UUID

from pydantic import ConfigDict, Field, JsonValue, model_validator

from server.pii import PiiType
from server.schemas import ContractModel, FollowupField, Profile
from server.sse import SSEEventName


class ChatPipelineRequest(ContractModel):
    """Safe pipeline input: only the PII-masked message is represented."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: UUID
    client_message_id: Annotated[str, Field(min_length=1, max_length=128)]
    turn_type: Literal["message", "followup_answer", "followup_skip"]
    masked_message: str | None = None
    followup_field: FollowupField | None = None
    followup_value: JsonValue | None = None
    followup_skipped: bool = False
    profile: Profile
    detected_pii_types: tuple[PiiType, ...] = ()
    pii_notice: str | None = None

    @model_validator(mode="after")
    def validate_safe_turn_shape(self) -> "ChatPipelineRequest":
        if self.turn_type == "message":
            if not self.masked_message or not self.masked_message.strip():
                raise ValueError("message turn requires a masked_message")
            if self.followup_field is not None or self.followup_skipped:
                raise ValueError("message turn cannot carry a follow-up answer")
        elif self.followup_field is None:
            raise ValueError("follow-up turn requires followup_field")
        elif self.turn_type == "followup_answer" and self.followup_skipped:
            raise ValueError("follow-up answer cannot be skipped")
        elif self.turn_type == "followup_skip" and not self.followup_skipped:
            raise ValueError("follow-up skip must be marked skipped")
        return self


@dataclass(frozen=True, slots=True)
class ChatServiceEvent:
    """One validated semantic event before SSE envelope encoding."""

    event: SSEEventName
    payload: object

    def __post_init__(self) -> None:
        if self.event == SSEEventName.DONE:
            raise ValueError("done is owned by the SSE transport")


class ChatService(Protocol):
    """Async semantic stream implemented by Backend B or a test fake."""

    def stream(self, request: ChatPipelineRequest) -> AsyncIterator[ChatServiceEvent]:
        """Yield ordered events without request_id, seq, or done."""
        ...
