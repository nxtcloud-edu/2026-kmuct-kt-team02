"""Provider-agnostic chat pipeline boundary used by the SSE transport."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated, Protocol
from uuid import UUID

from pydantic import ConfigDict, Field

from server.pii import PiiType
from server.schemas import ContractModel, Profile
from server.sse import SSEEventName


class ChatPipelineRequest(ContractModel):
    """Safe pipeline input: only the PII-masked message is represented."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: UUID
    client_message_id: Annotated[str, Field(min_length=1, max_length=128)]
    masked_message: Annotated[str, Field(min_length=1)]
    profile: Profile
    detected_pii_types: tuple[PiiType, ...] = ()
    pii_notice: str | None = None


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
