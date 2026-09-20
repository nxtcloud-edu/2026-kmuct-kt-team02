import asyncio
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi.testclient import TestClient

from server.api.chat import _stream_chat

from server.chat_service import (
    ChatPipelineRequest,
    ChatPipelineResult,
    RecoverableChatError,
)
from server.config import Settings
from server.main import create_app
from server.pii import PiiType
from server.schemas import FollowupQuestion, PolicyEvaluation, Profile
from server.session_store import SessionStore
from server.sse import (
    AnswerDeltaEventData,
    Footnote,
    FootnotesEventData,
    PoliciesEventData,
    ProfileUpdateEventData,
    RelatedEventData,
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
            "categories": ["scholarship", "housing"],
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
            "deadline": {
                "apply_start": "2026-09-01",
                "apply_end": "2026-10-01",
                "d_day": 11,
                "badge": "D-11",
                "is_imminent": False,
            },
            "documents": ["신청서"],
            "steps": ["공식 페이지에서 신청"],
            "source_url": "https://youth.seoul.go.kr/policy/1",
            "apply_url": "https://youth.seoul.go.kr/apply/1",
            "checked_at": "2026-09-20",
            "data_status": "verified",
        }
    )


def pipeline_result(*, include_optional: bool = True) -> ChatPipelineResult:
    current_profile = profile()
    updated_profile = Profile.model_validate(
        {**current_profile.model_dump(), "housing_type": "monthly_rent"}
    )
    return ChatPipelineResult(
        profile_update=(
            ProfileUpdateEventData(
                changed_fields={"housing_type": "monthly_rent"},
                message="주거 형태를 갱신했어요.",
                profile=updated_profile,
            )
            if include_optional
            else None
        ),
        policies=PoliciesEventData(
            policies=[policy()],
            hidden_unlikely_count=0,
        ),
        answer_deltas=[
            AnswerDeltaEventData(delta="가능성이 높은 정책이 있어요."),
            AnswerDeltaEventData(delta="공식 공고를 확인해 주세요."),
        ],
        footnotes=FootnotesEventData(
            footnotes=[
                Footnote(
                    footnote_id=1,
                    policy_id="SEOUL-001",
                    excerpt="만 19세부터 39세까지 신청할 수 있습니다.",
                    agency="서울특별시",
                    checked_at="2026-09-20",
                    source_url="https://youth.seoul.go.kr/policy/1",
                )
            ]
        ),
        followup=(
            FollowupQuestion.model_validate(
                {
                    "field": "housing_type",
                    "question": "현재 주거 형태는 무엇인가요?",
                    "reason": "주거 정책 판정을 확인할 수 있어요.",
                    "options": [{"value": "monthly_rent", "label": "월세"}],
                    "allow_free_text": True,
                    "allow_skip": True,
                }
            )
            if include_optional
            else None
        ),
        related=(
            RelatedEventData(questions=["다른 장학금도 보여줘"])
            if include_optional
            else None
        ),
    )


class FakeChatService:
    def __init__(
        self,
        result: ChatPipelineResult,
        *,
        recoverable: bool = False,
    ) -> None:
        self.result = result
        self.recoverable = recoverable
        self.requests: list[ChatPipelineRequest] = []

    async def run(self, request: ChatPipelineRequest) -> ChatPipelineResult:
        self.requests.append(request)
        if self.recoverable:
            raise RecoverableChatError(self.result)
        return self.result


def parse_sse(body: str) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []
    for frame in body.strip().split("\n\n"):
        lines = frame.splitlines()
        event_name = lines[0].removeprefix("event: ")
        data = json.loads(lines[1].removeprefix("data: "))
        events.append((event_name, data))
    return events


def make_client(
    service: FakeChatService,
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


def test_chat_stream_has_fixed_order_sequence_headers_and_masked_input() -> None:
    service = FakeChatService(pipeline_result())
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

    events = parse_sse(response.text)
    event_names = [name for name, _data in events]
    assert event_names == [
        "status",
        "profile_update",
        "policies",
        "status",
        "answer_delta",
        "answer_delta",
        "footnotes",
        "followup",
        "related",
        "done",
    ]
    assert [data["seq"] for _name, data in events] == list(
        range(1, len(events) + 1)
    )
    request_ids = {data["request_id"] for _name, data in events}
    assert len(request_ids) == 1
    assert UUID(str(request_ids.pop())).version == 4
    assert event_names.index("policies") < event_names.index("answer_delta")
    assert event_names[-1] == "done"

    pipeline_request = service.requests[0]
    assert pipeline_request.client_message_id == "frontend-message-1"
    assert resident_number not in pipeline_request.masked_message
    assert "[주민등록번호 마스킹]" in pipeline_request.masked_message
    assert pipeline_request.detected_pii_types == (
        PiiType.RESIDENT_REGISTRATION_NUMBER,
    )


def test_recoverable_fallback_still_ends_with_done() -> None:
    service = FakeChatService(
        pipeline_result(include_optional=False),
        recoverable=True,
    )
    client, _store, session_id = make_client(service)

    response = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "장학금 알려줘",
            "client_message_id": "frontend-message-2",
        },
    )

    event_names = [name for name, _data in parse_sse(response.text)]
    assert response.status_code == 200
    assert event_names == [
        "status",
        "policies",
        "status",
        "answer_delta",
        "answer_delta",
        "footnotes",
        "done",
    ]
    assert event_names[-1] == "done"


def test_missing_session_returns_json_404_before_stream() -> None:
    service = FakeChatService(pipeline_result())
    app = create_app(settings=settings(), chat_service=service)
    client = TestClient(app)

    response = client.post(
        "/chat",
        json={
            "session_id": "00000000-0000-4000-8000-000000000000",
            "message": "장학금 알려줘",
            "client_message_id": "frontend-message-3",
        },
    )

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["code"] == "session_expired"
    assert service.requests == []


def test_expired_session_is_removed_and_returns_404_before_stream() -> None:
    clock = MutableClock(datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc))
    store = SessionStore(ttl_seconds=60, clock=clock)
    service = FakeChatService(pipeline_result())
    client, session_store, session_id = make_client(service, store=store)
    clock.advance(seconds=60)

    response = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "장학금 알려줘",
            "client_message_id": "frontend-message-4",
        },
    )

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["code"] == "session_expired"
    assert len(session_store) == 0
    assert service.requests == []


def test_stream_cancellation_cancels_pending_pipeline_task() -> None:
    class BlockingChatService:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.cancelled = asyncio.Event()

        async def run(self, _request: ChatPipelineRequest) -> ChatPipelineResult:
            self.started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                raise

    async def scenario() -> None:
        service = BlockingChatService()
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

        first_frame = await anext(stream)
        assert first_frame.startswith("event: status\n")

        pending_frame = asyncio.create_task(anext(stream))
        await service.started.wait()
        pending_frame.cancel()
        try:
            await pending_frame
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("cancelled stream iteration must propagate cancellation")

        assert service.cancelled.is_set()
        await stream.aclose()

    asyncio.run(scenario())