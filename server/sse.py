"""Typed validation and wire encoding for server-sent events."""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated, Generic, Literal, TypeAlias, TypeVar
from uuid import UUID

from pydantic import Field, HttpUrl, JsonValue, TypeAdapter

from server.schemas import (
    ContractModel,
    FollowupQuestion,
    PolicyEvaluation,
    Profile,
    ProfileField,
)

PayloadT = TypeVar("PayloadT")


class SSEEventName(StrEnum):
    STATUS = "status"
    PROFILE_UPDATE = "profile_update"
    POLICIES = "policies"
    ANSWER_DELTA = "answer_delta"
    FOOTNOTES = "footnotes"
    FOLLOWUP = "followup"
    RELATED = "related"
    DONE = "done"


class SSEEnvelope(ContractModel, Generic[PayloadT]):
    request_id: UUID
    seq: Annotated[int, Field(strict=True, ge=1)]
    payload: PayloadT


class StatusEventData(ContractModel):
    stage: Literal["searching", "checking", "summarizing"]


class ProfileUpdateEventData(ContractModel):
    changed_fields: dict[ProfileField, JsonValue]
    message: str
    profile: Profile


class PoliciesEventData(ContractModel):
    policies: list[PolicyEvaluation]
    hidden_unlikely_count: Annotated[int, Field(strict=True, ge=0)]


class AnswerDeltaEventData(ContractModel):
    delta: Annotated[str, Field(min_length=1)]


class Footnote(ContractModel):
    footnote_id: Annotated[int, Field(strict=True, ge=1)]
    policy_id: Annotated[str, Field(min_length=1)]
    excerpt: Annotated[str, Field(min_length=10, max_length=150)]
    agency: Annotated[str, Field(min_length=1)]
    checked_at: date
    source_url: HttpUrl


class FootnotesEventData(ContractModel):
    footnotes: list[Footnote]


class RelatedEventData(ContractModel):
    questions: Annotated[list[str], Field(max_length=3)]


class DoneEventData(ContractModel):
    total_duration_ms: Annotated[int, Field(strict=True, ge=0)]


class StatusEvent(ContractModel):
    event: Literal["status"] = "status"
    data: SSEEnvelope[StatusEventData]


class ProfileUpdateEvent(ContractModel):
    event: Literal["profile_update"] = "profile_update"
    data: SSEEnvelope[ProfileUpdateEventData]


class PoliciesEvent(ContractModel):
    event: Literal["policies"] = "policies"
    data: SSEEnvelope[PoliciesEventData]


class AnswerDeltaEvent(ContractModel):
    event: Literal["answer_delta"] = "answer_delta"
    data: SSEEnvelope[AnswerDeltaEventData]


class FootnotesEvent(ContractModel):
    event: Literal["footnotes"] = "footnotes"
    data: SSEEnvelope[FootnotesEventData]


class FollowupEvent(ContractModel):
    event: Literal["followup"] = "followup"
    data: SSEEnvelope[FollowupQuestion]


class RelatedEvent(ContractModel):
    event: Literal["related"] = "related"
    data: SSEEnvelope[RelatedEventData]


class DoneEvent(ContractModel):
    event: Literal["done"] = "done"
    data: SSEEnvelope[DoneEventData]


SSEEvent: TypeAlias = Annotated[
    StatusEvent
    | ProfileUpdateEvent
    | PoliciesEvent
    | AnswerDeltaEvent
    | FootnotesEvent
    | FollowupEvent
    | RelatedEvent
    | DoneEvent,
    Field(discriminator="event"),
]
SSE_EVENT_ADAPTER = TypeAdapter(SSEEvent)


def validate_sse_event(value: object) -> SSEEvent:
    """Validate an event name, common envelope, and event-specific payload."""

    return SSE_EVENT_ADAPTER.validate_python(value)


def build_sse_event(
    *,
    event: SSEEventName | str,
    request_id: UUID,
    seq: int,
    payload: object,
) -> SSEEvent:
    """Build and validate one event from transport-level values."""

    event_name = event.value if isinstance(event, SSEEventName) else event
    return validate_sse_event(
        {
            "event": event_name,
            "data": {
                "request_id": request_id,
                "seq": seq,
                "payload": payload,
            },
        }
    )


def encode_sse(value: SSEEvent | object) -> str:
    """Validate and encode one event as an SSE frame."""

    event = validate_sse_event(value)
    data_json = event.data.model_dump_json()
    return f"event: {event.event}\ndata: {data_json}\n\n"
