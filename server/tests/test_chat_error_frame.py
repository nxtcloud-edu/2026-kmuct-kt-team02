"""스트림 도중 실패가 사용자에게 보이는지 고정한다.

`error` 프레임은 `docs/03-api-contract.md` 5장에 진작 정의돼 있었지만 구현이 없었다.
그동안 파이프라인 중간 예외는 이렇게 흘렀다. `StreamingResponse` 가 이미 200 과
`text/event-stream` 헤더를 보냈으므로 `server/errors.py` 의 핸들러는 `JSONResponse` 를
만들어도 전달할 곳이 없다. 결과는 `done` 도 없이 끊긴 스트림이다. 프론트는 진행 표시를
지울 신호를 못 받아 "검색 중" 에서 멈춘다. 오류 화면보다 나쁘다.
"""

import json

from fastapi.testclient import TestClient

from server.chat_service import ChatPipelineRequest, ChatServiceEvent
from server.config import Settings
from server.main import create_app
from server.schemas import Profile
from server.session_store import SessionStore
from server.sse import SSEEventName, StatusEventData


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


class FailingAfterStatusService:
    """상태 프레임 하나를 보낸 뒤 터진다. 판정 조립 실패를 흉내낸다."""

    async def stream(self, request: ChatPipelineRequest):
        yield ChatServiceEvent(SSEEventName.STATUS, StatusEventData(stage="searching"))
        raise RuntimeError("판정 결과가 계약과 어긋납니다")


class FailingImmediatelyService:
    async def stream(self, request: ChatPipelineRequest):
        raise RuntimeError("첫 프레임 전에 실패")
        yield  # pragma: no cover - 제너레이터로 만들기 위한 구문


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


def post_chat(service: object) -> tuple[int, list[tuple[str, dict[str, object]]]]:
    store = SessionStore(ttl_seconds=1800)
    session = store.create(profile())
    app = create_app(settings=settings(), session_store=store, chat_service=service)
    response = TestClient(app).post(
        "/chat",
        json={
            "session_id": str(session.session_id),
            "message": "청년수당 알려줘",
            "client_message_id": "frontend-message-1",
        },
    )
    return response.status_code, parse_sse(response.text)


def test_midstream_failure_sends_error_then_done() -> None:
    status_code, parsed = post_chat(FailingAfterStatusService())

    assert status_code == 200
    names = [name for name, _data in parsed]
    assert names == ["status", "error", "done"]


def test_error_frame_carries_a_code_and_a_user_facing_message() -> None:
    _status, parsed = post_chat(FailingAfterStatusService())

    payload = dict(parsed)["error"]["payload"]
    assert payload["code"] == "server_error"
    # JSON 오류 응답과 같은 문구를 쓴다. 전송 방식에 따라 다른 말이 나오면 다른 문제로 읽힌다.
    assert payload["message"] == "잠시 문제가 생겼어요. 다시 시도해 주세요"


def test_sequence_numbers_stay_continuous_through_the_error() -> None:
    _status, parsed = post_chat(FailingAfterStatusService())

    assert [data["seq"] for _name, data in parsed] == [1, 2, 3]


def test_request_id_is_the_same_across_the_error_frames() -> None:
    _status, parsed = post_chat(FailingAfterStatusService())

    request_ids = {data["request_id"] for _name, data in parsed}
    assert len(request_ids) == 1


def test_failure_before_any_event_still_closes_the_stream() -> None:
    """첫 프레임도 못 보낸 경우에도 done 으로 닫아 입력창을 돌려준다."""
    _status, parsed = post_chat(FailingImmediatelyService())

    assert [name for name, _data in parsed] == ["error", "done"]


def test_done_payload_still_reports_duration_on_failure() -> None:
    _status, parsed = post_chat(FailingAfterStatusService())

    assert dict(parsed)["done"]["payload"]["total_duration_ms"] >= 0


def test_pipeline_may_not_emit_transport_owned_events() -> None:
    """양쪽에서 만들면 한 요청에 오류 프레임이 두 번 나갈 수 있다."""
    for event in (SSEEventName.ERROR, SSEEventName.DONE):
        try:
            ChatServiceEvent(event, {"code": "server_error", "message": "x"})
        except ValueError as error:
            assert "transport" in str(error)
        else:  # pragma: no cover - 실패 시에만 도달
            raise AssertionError(f"{event.value} 를 파이프라인이 만들 수 있으면 안 된다")
