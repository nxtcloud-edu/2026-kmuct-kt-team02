"""POST /chat transport with ordered server-sent events."""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import suppress
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from server.chat_service import (
    ChatPipelineRequest,
    ChatPipelineResult,
    ChatService,
    RecoverableChatError,
)
from server.errors import AppError, ErrorCode, ErrorResponse
from server.pii import mask_pii
from server.schemas import ChatRequest
from server.session_store import SessionNotFoundError, SessionStore
from server.sse import SSEEventName, build_sse_event, encode_sse

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
) -> AsyncIterator[str]:
    """Emit the fixed event order and cancel pipeline work on disconnect.

    Starlette cancels the response body iterator when its ASGI disconnect
    listener fires. This generator propagates that cancellation to the only
    child task it creates and awaits cleanup before exiting.
    """

    started_at = time.perf_counter()
    build_frame = _frame_builder(request_id)
    service_task: asyncio.Task[ChatPipelineResult] | None = None

    try:
        yield build_frame(SSEEventName.STATUS, {"stage": "searching"})

        service_task = asyncio.create_task(
            chat_service.run(pipeline_request),
            name=f"chat-pipeline-{request_id}",
        )
        try:
            result = await service_task
        except RecoverableChatError as exc:
            result = exc.fallback

        if result.profile_update is not None:
            yield build_frame(SSEEventName.PROFILE_UPDATE, result.profile_update)

        yield build_frame(SSEEventName.POLICIES, result.policies)
        yield build_frame(SSEEventName.STATUS, {"stage": "summarizing"})

        for delta in result.answer_deltas:
            yield build_frame(SSEEventName.ANSWER_DELTA, delta)

        yield build_frame(SSEEventName.FOOTNOTES, result.footnotes)

        if result.followup is not None:
            yield build_frame(SSEEventName.FOLLOWUP, result.followup)
        if result.related is not None:
            yield build_frame(SSEEventName.RELATED, result.related)

        duration_ms = max(0, int((time.perf_counter() - started_at) * 1000))
        yield build_frame(
            SSEEventName.DONE,
            {"total_duration_ms": duration_ms},
        )
    finally:
        if service_task is not None and not service_task.done():
            service_task.cancel()
            with suppress(asyncio.CancelledError):
                await service_task


@router.post(
    "",
    response_class=StreamingResponse,
    responses={
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
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

    masking = mask_pii(chat_request.message)
    pipeline_request = ChatPipelineRequest(
        session_id=session.session_id,
        client_message_id=chat_request.client_message_id,
        masked_message=masking.masked_text,
        profile=session.profile,
        detected_pii_types=masking.detected_types,
        pii_notice=masking.notice,
    )
    request_id = uuid4()

    return StreamingResponse(
        _stream_chat(
            request_id=request_id,
            pipeline_request=pipeline_request,
            chat_service=chat_service,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
