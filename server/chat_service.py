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
        # error 도 transport 소유다. 파이프라인은 예외를 올리기만 하고, 그것을 사용자 문구로
        # 바꾸는 일은 스트림을 닫는 쪽(server/api/chat.py)이 한다. 양쪽에서 만들면 한 요청에
        # 오류 프레임이 두 번 나갈 수 있다.
        if self.event in {SSEEventName.DONE, SSEEventName.ERROR}:
            raise ValueError(f"{self.event.value} is owned by the SSE transport")


class ChatService(Protocol):
    """Async semantic stream implemented by Backend B or a test fake."""

    def stream(self, request: ChatPipelineRequest) -> AsyncIterator[ChatServiceEvent]:
        """Yield ordered events without request_id, seq, or done."""
        ...
