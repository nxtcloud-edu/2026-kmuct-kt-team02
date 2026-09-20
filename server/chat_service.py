"""Provider-agnostic chat pipeline boundary used by the SSE transport."""

from __future__ import annotations

from typing import Annotated, Protocol
from uuid import UUID

from pydantic import ConfigDict, Field

from server.pii import PiiType
from server.schemas import ContractModel, FollowupQuestion, Profile
from server.sse import (
    AnswerDeltaEventData,
    FootnotesEventData,
    PoliciesEventData,
    ProfileUpdateEventData,
    RelatedEventData,
)


class ChatPipelineRequest(ContractModel):
    """Safe pipeline input: only the PII-masked message is represented."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: UUID
    client_message_id: Annotated[str, Field(min_length=1, max_length=128)]
    masked_message: Annotated[str, Field(min_length=1)]
    profile: Profile
    detected_pii_types: tuple[PiiType, ...] = ()
    pii_notice: str | None = None


class ChatPipelineResult(ContractModel):
    """Semantic result plan; the API layer owns event ordering and sequence IDs."""

    profile_update: ProfileUpdateEventData | None = None
    policies: PoliciesEventData
    answer_deltas: Annotated[list[AnswerDeltaEventData], Field(min_length=1)]
    footnotes: FootnotesEventData = Field(
        default_factory=lambda: FootnotesEventData(footnotes=[])
    )
    followup: FollowupQuestion | None = None
    related: RelatedEventData | None = None


class RecoverableChatError(RuntimeError):
    """Signals that the supplied fallback plan can safely finish with done."""

    def __init__(self, fallback: ChatPipelineResult) -> None:
        super().__init__("chat pipeline used a recoverable fallback")
        self.fallback = fallback


class ChatService(Protocol):
    """Async pipeline implemented by integrations or a deterministic test fake."""

    async def run(self, request: ChatPipelineRequest) -> ChatPipelineResult:
        """Return a result plan without controlling transport event order."""
        ...
