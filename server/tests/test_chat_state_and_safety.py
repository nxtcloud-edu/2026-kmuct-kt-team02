import asyncio
import json
import logging
from uuid import uuid4

from fastapi.testclient import TestClient

from server.api.chat import _stream_chat
from server.chat_service import ChatPipelineRequest, ChatServiceEvent
from server.config import Settings
from server.main import create_app
from server.metrics import record_chat_metric
from server.schemas import FollowupQuestion, Profile, ProfileField
from server.session_store import PendingProfileChange, SessionStore, TurnSummary
from server.sse import (
    RelatedChip,
    RelatedEventData,
    SSEEventName,
    StatusEventData,
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


def settings(*, rate_limit: int = 10, demo_mode: bool = False) -> Settings:
    return Settings(
        localhost_cors_origins=(),
        s3_cors_origins=(),
        llm_adapter_name=None,
        session_ttl_seconds=1800,
        chat_rate_limit_per_minute=rate_limit,
        demo_mode=demo_mode,
    )


def parse_sse(body: str) -> list[tuple[str, dict[str, object]]]:
    return [
        (
            lines[0].removeprefix("event: "),
            json.loads(lines[1].removeprefix("data: ")),
        )
        for lines in (frame.splitlines() for frame in body.strip().split("\n\n"))
    ]


class RecordingService:
    def __init__(self, *, fail: bool = False) -> None:
        self.requests: list[ChatPipelineRequest] = []
        self.fail = fail

    async def stream(self, request: ChatPipelineRequest):
        self.requests.append(request)
        yield ChatServiceEvent(SSEEventName.STATUS, StatusEventData(stage="searching"))
        if self.fail:
            raise RuntimeError("safe failure")


def make_client(
    service: RecordingService,
    *,
    store: SessionStore | None = None,
    app_settings: Settings | None = None,
) -> tuple[TestClient, SessionStore, str]:
    session_store = store or SessionStore(ttl_seconds=1800)
    session = session_store.create(profile())
    app = create_app(
        settings=app_settings or settings(),
        session_store=session_store,
        chat_service=service,
    )
    return TestClient(app), session_store, str(session.session_id)


def message_body(session_id: str, message_id: str = "message-1") -> dict[str, object]:
    return {
        "session_id": session_id,
        "client_message_id": message_id,
        "turn": {"type": "message", "message": "정책을 알려줘"},
    }


def test_direct_followup_answer_updates_profile_without_message_interpretation() -> None:
    service = RecordingService()
    client, store, session_id = make_client(service)

    response = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "client_message_id": "followup-1",
            "turn": {
                "type": "followup_answer",
                "field": "housing_type",
                "value": "monthly_rent",
            },
        },
    )

    assert response.status_code == 200
    assert service.requests[0].turn_type == "followup_answer"
    assert service.requests[0].masked_message is None
    assert service.requests[0].profile.housing_type == "monthly_rent"
    session = store.get(session_id)
    assert session.profile.housing_type == "monthly_rent"
    assert "housing_type" in session.asked_fields
    assert session.recent_turns[-1].confirmed_values == (("housing_type", "monthly_rent"),)


def test_planned_basis_overlays_pending_change_but_keeps_current_profile() -> None:
    service = RecordingService()
    client, store, session_id = make_client(service)
    store.set_pending_planned_changes(
        session_id,
        (PendingProfileChange(field=ProfileField.STATUS, value="on_leave"),),
    )

    response = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "client_message_id": "planned-1",
            "turn": {
                "type": "followup_answer",
                "field": "planned_basis",
                "value": "planned",
            },
        },
    )

    assert response.status_code == 200
    assert service.requests[0].profile.status == "on_leave"
    session = store.get(session_id)
    assert session.profile.status == "enrolled"
    assert session.planned_basis == "planned"
    assert "planned_basis" in session.asked_fields


def test_planned_basis_skip_discards_pending_change_and_blocks_reprompt() -> None:
    service = RecordingService()
    client, store, session_id = make_client(service)
    store.set_pending_planned_changes(
        session_id,
        (PendingProfileChange(field=ProfileField.STATUS, value="on_leave"),),
    )

    response = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "client_message_id": "planned-skip-1",
            "turn": {"type": "followup_skip", "field": "planned_basis"},
        },
    )

    assert response.status_code == 200
    session = store.get(session_id)
    assert session.planned_basis == "current"
    assert session.pending_planned_changes == ()
    assert "planned_basis" in session.skipped_fields
    assert service.requests[0].profile.status == "enrolled"


def test_duplicate_message_and_rate_limit_are_json_errors_before_sse() -> None:
    service = RecordingService()
    client, _store, session_id = make_client(service, app_settings=settings(rate_limit=1))

    first = client.post("/chat", json=message_body(session_id, "one"))
    duplicate = client.post("/chat", json=message_body(session_id, "one"))
    limited = client.post("/chat", json=message_body(session_id, "two"))

    assert first.status_code == 200
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "duplicate_request"
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "rate_limited"
    assert limited.headers["retry-after"]


def test_transport_records_shown_chip_and_sent_followup() -> None:
    class StateService(RecordingService):
        async def stream(self, request: ChatPipelineRequest):
            self.requests.append(request)
            yield ChatServiceEvent(
                SSEEventName.FOLLOWUP,
                FollowupQuestion(
                    field="income_bracket",
                    question="소득 구간을 알려주세요.",
                    reason="판정이 달라져요.",
                    options=[],
                    allow_free_text=False,
                ),
            )
            yield ChatServiceEvent(
                SSEEventName.RELATED,
                RelatedEventData(chips=[RelatedChip(id="income_help", text="소득 기준이 궁금해요")]),
            )

    service = StateService()
    client, store, session_id = make_client(service)
    response = client.post("/chat", json=message_body(session_id))

    assert response.status_code == 200
    session = store.get(session_id)
    assert "income_bracket" in session.asked_fields
    assert "income_help" in session.shown_chip_ids


def test_stream_failure_and_timeout_emit_error_then_done() -> None:
    service = RecordingService(fail=True)
    client, _store, session_id = make_client(service)
    failed = parse_sse(client.post("/chat", json=message_body(session_id)).text)
    assert [name for name, _data in failed] == ["status", "error", "done"]
    assert failed[1][1]["payload"]["code"] == "server_error"

    class BlockingService:
        async def stream(self, _request: ChatPipelineRequest):
            yield ChatServiceEvent(SSEEventName.STATUS, StatusEventData(stage="searching"))
            await asyncio.Event().wait()

    async def scenario() -> list[str]:
        request = ChatPipelineRequest(
            session_id=uuid4(),
            client_message_id="timeout-1",
            turn_type="message",
            masked_message="정책을 알려줘",
            profile=profile(),
        )
        frames = [
            frame
            async for frame in _stream_chat(
                request_id=uuid4(),
                pipeline_request=request,
                chat_service=BlockingService(),
                total_timeout_seconds=1,
            )
        ]
        return [frame.splitlines()[0].removeprefix("event: ") for frame in frames]

    assert asyncio.run(scenario()) == ["status", "error", "done"]


def test_service_error_is_terminal_before_transport_done() -> None:
    class ErrorThenStaleService:
        async def stream(self, _request: ChatPipelineRequest):
            from server.sse import ErrorEventData

            yield ChatServiceEvent(
                SSEEventName.ERROR,
                ErrorEventData(code="answer_failed", message="안전한 답변이 없어요."),
            )
            yield ChatServiceEvent(SSEEventName.STATUS, StatusEventData(stage="searching"))

    async def scenario() -> list[str]:
        request = ChatPipelineRequest(
            session_id=uuid4(),
            client_message_id="terminal-error-1",
            turn_type="message",
            masked_message="정책을 알려줘",
            profile=profile(),
        )
        return [
            frame.splitlines()[0].removeprefix("event: ")
            async for frame in _stream_chat(
                request_id=uuid4(),
                pipeline_request=request,
                chat_service=ErrorThenStaleService(),
            )
        ]

    assert asyncio.run(scenario()) == ["error", "done"]


def test_demo_cache_replays_safe_events_without_reinvoking_service() -> None:
    service = RecordingService()
    client, _store, session_id = make_client(service, app_settings=settings(demo_mode=True))

    first = client.post("/chat", json=message_body(session_id, "cache-1"))
    second = client.post("/chat", json=message_body(session_id, "cache-2"))

    assert first.status_code == 200
    assert second.status_code == 200
    assert len(service.requests) == 1


def test_session_caps_summaries_and_message_ids() -> None:
    store = SessionStore(ttl_seconds=1800)
    session = store.create(profile())
    for index in range(40):
        store.begin_turn(session.session_id, f"message-{index}")
    for index in range(8):
        store.record_turn_summary(
            session.session_id,
            TurnSummary(kind="message", policy_ids=(f"P-{index}",)),
        )

    stored = store.get(session.session_id)
    assert len(stored.processed_message_ids) == 32
    assert stored.processed_message_ids[0] == "message-8"
    assert len(stored.recent_turns) == 6
    assert stored.recent_turns[0].policy_ids == ("P-2",)


def test_metrics_logger_drops_unallowlisted_user_content(caplog) -> None:
    secret = "900101-1234567"
    caplog.set_level(logging.INFO, logger="server.metrics")
    record_chat_metric(
        {
            "duration_ms": 12,
            "outcome": "completed",
            "message": secret,
            "profile": {"district": "마포구"},
        }
    )

    record = caplog.records[-1]
    assert secret not in str(record.__dict__)
    assert "profile" not in str(record.__dict__)
