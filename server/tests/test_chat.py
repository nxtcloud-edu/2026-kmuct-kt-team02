import asyncio
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from server.api.chat import _stream_chat
from server.chat_service import ChatPipelineRequest, ChatServiceEvent
from server.config import Settings
from server.main import create_app
from server.pii import PiiType
from server.schemas import PolicyEvaluation, Profile
from server.session_store import SessionStore
from server.sse import (
    AnswerDeltaEventData,
    FootnotesEventData,
    PoliciesEventData,
    SSEEventName,
    StatusEventData,
)


class MutableClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current

    def advance(self, *, seconds: int) -> None:
        self.current += timedelta(seconds=seconds)


def settings() -> Settings:
    return Settings(
        localhost_cors_origins=(),
        s3_cors_origins=(),
        llm_adapter_name=None,
        session_ttl_seconds=1800,
    )


def profile() -> Profile:
    return Profile.model_validate(
        {
            "age": 22,
            "district": "마포구",
            "status": "enrolled",
            "categories": ["scholarship"],
        }
    )


def policy() -> PolicyEvaluation:
    return PolicyEvaluation.model_validate(
        {
            "policy_id": "SEOUL-001",
            "title": "검수 정책",
            "agency": "서울특별시",
            "categories": ["scholarship"],
            "status": "likely",
            "status_label": "신청 가능성이 높아요",
            "benefit": "검수된 혜택",
            "conditions": [],
            "deadline": {"badge": "D-11", "is_imminent": False, "d_day": 11},
            "documents": [],
            "steps": [],
            "source_url": "https://youth.seoul.go.kr/policy/1",
            "checked_at": "2026-09-20",
            "data_status": "verified",
        }
    )


def events() -> list[ChatServiceEvent]:
    return [
        ChatServiceEvent(SSEEventName.STATUS, StatusEventData(stage="searching")),
        ChatServiceEvent(
            SSEEventName.POLICIES,
            PoliciesEventData(policies=[policy()], hidden_unlikely_count=0),
        ),
        ChatServiceEvent(SSEEventName.STATUS, StatusEventData(stage="summarizing")),
        ChatServiceEvent(
            SSEEventName.ANSWER_DELTA,
            AnswerDeltaEventData(delta="정책을 확인했어요."),
        ),
        ChatServiceEvent(
            SSEEventName.FOOTNOTES,
            FootnotesEventData(footnotes=[]),
        ),
    ]


class FakeStreamService:
    def __init__(self) -> None:
        self.requests: list[ChatPipelineRequest] = []

    async def stream(self, request: ChatPipelineRequest):
        self.requests.append(request)
        for event in events():
            yield event


def parse_sse(body: str) -> list[tuple[str, dict[str, object]]]:
    parsed: list[tuple[str, dict[str, object]]] = []
    for frame in body.strip().split("\n\n"):
        lines = frame.splitlines()
        parsed.append(
            (
                lines[0].removeprefix("event: "),
                json.loads(lines[1].removeprefix("data: ")),
            )
        )
    return parsed


def make_client(
    service: FakeStreamService,
    *,
    store: SessionStore | None = None,
) -> tuple[TestClient, SessionStore, str]:
    session_store = store if store is not None else SessionStore(ttl_seconds=1800)
    session = session_store.create(profile())
    app = create_app(
        settings=settings(),
        session_store=session_store,
        chat_service=service,
    )
    return TestClient(app), session_store, str(session.session_id)


def test_chat_transport_envelopes_service_events_and_masks_input() -> None:
    service = FakeStreamService()
    client, _store, session_id = make_client(service)
    resident_number = "900101-1234567"

    response = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": f"제 주민번호는 {resident_number}입니다.",
            "client_message_id": "frontend-message-1",
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers["x-accel-buffering"] == "no"
    parsed = parse_sse(response.text)
    names = [name for name, _data in parsed]
    assert names == ["status", "policies", "status", "answer_delta", "footnotes", "done"]
    assert [data["seq"] for _name, data in parsed] == list(range(1, 7))
    request_ids = {data["request_id"] for _name, data in parsed}
    assert len(request_ids) == 1
    assert UUID(str(request_ids.pop())).version == 4
    assert names.index("policies") < names.index("answer_delta")

    pipeline_request = service.requests[0]
    assert resident_number not in pipeline_request.masked_message
    assert pipeline_request.detected_pii_types == (
        PiiType.RESIDENT_REGISTRATION_NUMBER,
    )


def test_missing_session_returns_json_404_before_service_stream() -> None:
    service = FakeStreamService()
    app = create_app(settings=settings(), chat_service=service)
    response = TestClient(app).post(
        "/chat",
        json={
            "session_id": "00000000-0000-4000-8000-000000000000",
            "message": "장학금 알려줘",
            "client_message_id": "frontend-message-2",
        },
    )

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["code"] == "session_expired"
    assert service.requests == []


def test_expired_session_is_removed_before_service_stream() -> None:
    clock = MutableClock(datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc))
    store = SessionStore(ttl_seconds=60, clock=clock)
    service = FakeStreamService()
    client, session_store, session_id = make_client(service, store=store)
    clock.advance(seconds=60)

    response = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "장학금 알려줘",
            "client_message_id": "frontend-message-3",
        },
    )

    assert response.status_code == 404
    assert len(session_store) == 0
    assert service.requests == []


def test_transport_cancellation_closes_pending_service_stream() -> None:
    class BlockingService:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.cancelled = asyncio.Event()

        async def stream(self, _request: ChatPipelineRequest):
            yield ChatServiceEvent(
                SSEEventName.STATUS,
                StatusEventData(stage="searching"),
            )
            self.started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise

    async def scenario() -> None:
        service = BlockingService()
        pipeline_request = ChatPipelineRequest(
            session_id=uuid4(),
            client_message_id="disconnect-test",
            masked_message="장학금 알려줘",
            profile=profile(),
        )
        stream = _stream_chat(
            request_id=uuid4(),
            pipeline_request=pipeline_request,
            chat_service=service,
        )
        assert (await anext(stream)).startswith("event: status\n")

        pending = asyncio.create_task(anext(stream))
        await service.started.wait()
        pending.cancel()
        try:
            await pending
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("stream cancellation must propagate")

        assert service.cancelled.is_set()
        await stream.aclose()

    asyncio.run(scenario())
