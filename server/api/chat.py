"""POST /chat transport with ordered server-sent events."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import suppress
from copy import deepcopy
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from server.chat_service import ChatPipelineRequest, ChatService, ChatServiceEvent
from server.demo_cache import DemoEventCache
from server.errors import AppError, ErrorCode, ErrorResponse
from server.metrics import record_chat_metric
from server.pii import mask_pii
from server.rate_limit import SessionRateLimiter
from server.schemas import (
    ChatRequest,
    FollowupAnswerTurn,
    FollowupSkipTurn,
    MessageTurn,
    PlannedBasis,
    Profile,
)
from server.session_store import (
    DuplicateMessageError,
    SessionNotFoundError,
    SessionStore,
)
from server.sse import (
    ErrorEventData,
    PoliciesEventData,
    RelatedEventData,
    SSEEventName,
    build_sse_event,
    encode_sse,
)

router = APIRouter(prefix="/chat", tags=["chat"])
_CHAT_SERVICE_UNAVAILABLE_MESSAGE = "채팅 서비스 의존성이 연결되지 않았습니다"


def _frame_builder(request_id: UUID):
    seq = 0

    def build(event: SSEEventName, payload: object) -> str:
        nonlocal seq
        seq += 1
        return encode_sse(
            build_sse_event(
                event=event,
                request_id=request_id,
                seq=seq,
                payload=payload,
            )
        )

    return build


async def _stream_chat(
    *,
    request_id: UUID,
    pipeline_request: ChatPipelineRequest,
    chat_service: ChatService,
    session_store: SessionStore | None = None,
    total_timeout_seconds: int = 20,
    demo_cache: DemoEventCache | None = None,
    demo_cache_key: str | None = None,
    cache_hit: bool = False,
) -> AsyncIterator[str]:
    """Envelope semantic events and cancel the active producer on disconnect."""

    started_at = time.perf_counter()
    deadline = asyncio.get_running_loop().time() + total_timeout_seconds
    build_frame = _frame_builder(request_id)
    service_stream: AsyncIterator[ChatServiceEvent] | None = None
    next_task: asyncio.Task[ChatServiceEvent] | None = None
    completed = False
    terminal_error: ErrorEventData | None = None
    error_sent = False
    emitted_events: list[ChatServiceEvent] = []

    def record_event_state(event: ChatServiceEvent) -> None:
        if session_store is None:
            return
        try:
            if event.event == SSEEventName.POLICIES and isinstance(
                event.payload, PoliciesEventData
            ):
                session_store.record_policies(
                    pipeline_request.session_id,
                    tuple(policy.policy_id for policy in event.payload.policies),
                )
            elif event.event == SSEEventName.FOLLOWUP:
                field = getattr(event.payload, "field", None)
                if field is not None:
                    session_store.record_followup(pipeline_request.session_id, field)
            elif event.event == SSEEventName.RELATED and isinstance(
                event.payload, RelatedEventData
            ):
                session_store.record_shown_chips(
                    pipeline_request.session_id,
                    tuple(chip.id for chip in event.payload.chips),
                )
        except SessionNotFoundError:
            # The request was valid when the stream opened; expiry during an open
            # stream must not erase already-generated cards.
            return

    try:
        # Keep creation in the recovery boundary as well: a fake or future
        # provider may fail before returning its async iterator.
        service_stream = chat_service.stream(pipeline_request)
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                raise TimeoutError
            next_task = asyncio.create_task(
                anext(service_stream),
                name=f"chat-event-{request_id}",
            )
            try:
                event = await asyncio.wait_for(next_task, timeout=remaining)
            except StopAsyncIteration:
                next_task = None
                completed = True
                break
            except asyncio.TimeoutError as exc:
                next_task = None
                raise TimeoutError from exc
            next_task = None
            record_event_state(event)
            emitted_events.append(deepcopy(event))
            if event.event == SSEEventName.ERROR:
                error_sent = True
                if isinstance(event.payload, ErrorEventData):
                    terminal_error = event.payload
            yield build_frame(event.event, event.payload)
            # `error` is terminal at the public transport boundary.  A
            # producer cannot accidentally append stale cards, deltas, or a
            # second error after a recoverable failure.
            if event.event == SSEEventName.ERROR:
                break
    except asyncio.CancelledError:
        raise
    except TimeoutError:
        terminal_error = ErrorEventData(
            code=ErrorCode.TIMEOUT,
            message="응답이 오래 걸려 여기까지의 결과를 보여드려요.",
        )
    except Exception:
        terminal_error = ErrorEventData(
            code=ErrorCode.SERVER_ERROR,
            message="잠시 문제가 생겼어요. 다시 시도해 주세요.",
        )
    finally:
        if next_task is not None and not next_task.done():
            next_task.cancel()
            with suppress(asyncio.CancelledError):
                await next_task
        close_stream = getattr(service_stream, "aclose", None)
        if close_stream is not None:
            with suppress(asyncio.CancelledError, RuntimeError):
                await close_stream()

    if terminal_error is not None and not error_sent:
        yield build_frame(SSEEventName.ERROR, terminal_error)
        error_sent = True

    duration_ms = max(0, int((time.perf_counter() - started_at) * 1000))
    yield build_frame(SSEEventName.DONE, {"total_duration_ms": duration_ms})

    if completed and not error_sent and demo_cache is not None and demo_cache_key is not None:
        demo_cache.put(demo_cache_key, tuple(emitted_events))
    record_chat_metric(
        {
            "request_id": str(request_id),
            "duration_ms": duration_ms,
            "outcome": "completed" if completed and not error_sent else "recovered_error",
            "error_code": terminal_error.code.value if terminal_error else None,
            "cache_hit": cache_hit,
            "event_count": len(emitted_events),
        }
    )


class _ReplayChatService:
    """Replays cached semantic events while the transport makes a fresh SSE envelope."""

    def __init__(self, events: tuple[ChatServiceEvent, ...]) -> None:
        self._events = tuple(deepcopy(events))

    async def stream(self, _request: ChatPipelineRequest) -> AsyncIterator[ChatServiceEvent]:
        for event in self._events:
            yield deepcopy(event)


def _followup_profile(
    *,
    store: SessionStore,
    session_id: UUID,
    turn: FollowupAnswerTurn | FollowupSkipTurn,
) -> Profile:
    """Validate a direct follow-up response before it can affect the pipeline."""

    if isinstance(turn, FollowupSkipTurn):
        stored = store.apply_followup_answer(
            session_id,
            field=turn.field,
            skipped=True,
        )
        return store.effective_profile(stored)

    if turn.field == "planned_basis":
        if not isinstance(turn.value, str):
            raise ValueError("planned_basis must be a string")
        try:
            basis = PlannedBasis(turn.value)
        except ValueError as exc:
            raise ValueError("invalid planned_basis") from exc
        stored = store.apply_followup_answer(
            session_id,
            field=turn.field,
            planned_basis=basis,
            confirmed_value=basis.value,
        )
        return store.effective_profile(stored)

    if turn.value is None:
        raise ValueError("follow-up answer must not be null")
    session = store.get(session_id)
    values = session.profile.model_dump(mode="json")
    values[str(turn.field)] = turn.value
    try:
        updated_profile = Profile.model_validate(values)
    except Exception as exc:
        raise ValueError("invalid follow-up answer") from exc
    stored = store.apply_followup_answer(
        session_id,
        field=turn.field,
        profile=updated_profile,
        confirmed_value=turn.value,
    )
    return store.effective_profile(stored)


@router.post(
    "",
    response_class=StreamingResponse,
    responses={
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        429: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
async def create_chat_stream(
    request: Request,
    chat_request: ChatRequest,
) -> StreamingResponse:
    """Validate session/PII before opening an SSE response."""

    session_store: SessionStore = request.app.state.session_store
    try:
        session = session_store.get(chat_request.session_id)
    except SessionNotFoundError as exc:
        raise AppError(
            code=ErrorCode.SESSION_EXPIRED,
            status_code=404,
        ) from exc

    chat_service: ChatService | None = request.app.state.chat_service
    if chat_service is None:
        raise AppError(
            code=ErrorCode.SERVER_ERROR,
            status_code=503,
            message=_CHAT_SERVICE_UNAVAILABLE_MESSAGE,
        )
    if chat_request.client_message_id in session.processed_message_ids:
        raise AppError(code=ErrorCode.DUPLICATE_REQUEST, status_code=409)

    limiter: SessionRateLimiter = request.app.state.chat_rate_limiter
    limit = limiter.check(session.session_id)
    if not limit.allowed:
        raise AppError(
            code=ErrorCode.RATE_LIMITED,
            status_code=429,
            headers={"Retry-After": str(limit.retry_after_seconds)},
        )
    try:
        session = session_store.begin_turn(
            session.session_id, chat_request.client_message_id
        )
    except DuplicateMessageError as exc:
        raise AppError(code=ErrorCode.DUPLICATE_REQUEST, status_code=409) from exc

    turn = chat_request.turn
    if isinstance(turn, MessageTurn):
        masking = mask_pii(turn.message)
        effective_profile = session_store.effective_profile(session)
        pipeline_request = ChatPipelineRequest(
            session_id=session.session_id,
            client_message_id=chat_request.client_message_id,
            turn_type="message",
            masked_message=masking.masked_text,
            profile=effective_profile,
            detected_pii_types=masking.detected_types,
            pii_notice=masking.notice,
        )
    else:
        try:
            effective_profile = _followup_profile(
                store=session_store,
                session_id=session.session_id,
                turn=turn,
            )
        except ValueError as exc:
            raise AppError(code=ErrorCode.INVALID_INPUT, status_code=422) from exc
        pipeline_request = ChatPipelineRequest(
            session_id=session.session_id,
            client_message_id=chat_request.client_message_id,
            turn_type=turn.type,
            followup_field=turn.field,
            followup_value=getattr(turn, "value", None),
            followup_skipped=isinstance(turn, FollowupSkipTurn),
            profile=effective_profile,
        )

    demo_cache: DemoEventCache | None = request.app.state.demo_event_cache
    demo_cache_key = (
        DemoEventCache.key(effective_profile, pipeline_request)
        if demo_cache is not None
        else None
    )
    cached_events = demo_cache.get(demo_cache_key) if demo_cache_key else None
    active_service: ChatService = (
        _ReplayChatService(cached_events) if cached_events is not None else chat_service
    )
    request_id = uuid4()

    return StreamingResponse(
        _stream_chat(
            request_id=request_id,
            pipeline_request=pipeline_request,
            chat_service=active_service,
            session_store=session_store,
            total_timeout_seconds=request.app.state.settings.chat_total_timeout_seconds,
            demo_cache=demo_cache,
            demo_cache_key=demo_cache_key,
            cache_hit=cached_events is not None,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
