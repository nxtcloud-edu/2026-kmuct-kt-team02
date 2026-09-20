"""POST /chat transport with ordered server-sent events."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from contextlib import suppress
from uuid import UUID, uuid4

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from server.chat_service import ChatPipelineRequest, ChatService, ChatServiceEvent
from server.errors import AppError, ErrorCode, ErrorResponse, default_message
from server.pii import mask_pii
from server.schemas import ChatRequest
from server.session_store import SessionNotFoundError, SessionStore
from server.sse import SSEEventName, build_sse_event, encode_sse

router = APIRouter(prefix="/chat", tags=["chat"])
_LOGGER = logging.getLogger(__name__)
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
    """Envelope semantic events and cancel the active producer on disconnect."""

    started_at = time.perf_counter()
    build_frame = _frame_builder(request_id)
    service_stream = chat_service.stream(pipeline_request)
    next_task: asyncio.Task[ChatServiceEvent] | None = None
    completed = False
    failed = False

    try:
        try:
            while True:
                next_task = asyncio.create_task(
                    anext(service_stream),
                    name=f"chat-event-{request_id}",
                )
                try:
                    event = await next_task
                except StopAsyncIteration:
                    next_task = None
                    completed = True
                    break
                next_task = None
                yield build_frame(event.event, event.payload)
        except asyncio.CancelledError:
            # 클라이언트가 끊은 경우다. 프레임을 더 만들지 않고 정리 단계로 넘긴다.
            raise
        except Exception:
            # 여기까지 온 예외는 200 과 text/event-stream 헤더가 이미 나간 뒤에 생긴 것이다.
            # server/errors.py 의 핸들러는 JSONResponse 를 만들므로 이 경로에서는 아무것도
            # 전달하지 못한다. 그래서 오류를 스트림 안에서 알린다.
            _LOGGER.exception("채팅 스트림 실패: request_id=%s", request_id)
            failed = True
            yield build_frame(
                SSEEventName.ERROR,
                {
                    "code": ErrorCode.SERVER_ERROR,
                    "message": default_message(ErrorCode.SERVER_ERROR),
                },
            )

        if completed or failed:
            # 실패해도 done 을 보낸다. 계약이 "복구 가능한 fallback 종료"도 done 으로 정했고,
            # done 이 없으면 프론트가 진행 표시를 지우고 입력창을 다시 열 신호를 못 받는다.
            duration_ms = max(0, int((time.perf_counter() - started_at) * 1000))
            yield build_frame(
                SSEEventName.DONE,
                {"total_duration_ms": duration_ms},
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
