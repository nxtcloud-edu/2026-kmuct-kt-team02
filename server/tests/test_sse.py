from uuid import UUID

import pytest
from pydantic import ValidationError

from server.sse import StatusEvent, encode_sse, validate_sse_event

REQUEST_ID = UUID("00000000-0000-4000-8000-000000000001")


def status_event(*, seq: int = 1) -> dict[str, object]:
    return {
        "event": "status",
        "data": {
            "request_id": REQUEST_ID,
            "seq": seq,
            "payload": {"stage": "searching"},
        },
    }


def test_sse_validates_common_envelope_and_matching_payload() -> None:
    event = validate_sse_event(status_event())

    assert isinstance(event, StatusEvent)
    assert event.data.request_id == REQUEST_ID
    assert event.data.seq == 1
    assert event.data.payload.stage == "searching"


def test_sse_rejects_payload_for_a_different_event_shape() -> None:
    invalid = status_event()
    invalid["data"]["payload"] = {"delta": "정책을 찾았어요."}  # type: ignore[index]

    with pytest.raises(ValidationError):
        validate_sse_event(invalid)


def test_sse_rejects_zero_sequence_and_non_contract_event() -> None:
    with pytest.raises(ValidationError):
        validate_sse_event(status_event(seq=0))

    with pytest.raises(ValidationError):
        validate_sse_event(
            {
                "event": "error",
                "data": {
                    "request_id": REQUEST_ID,
                    "seq": 1,
                    "payload": {},
                },
            }
        )


def test_sse_encoder_emits_common_data_envelope() -> None:
    frame = encode_sse(status_event())

    assert frame == (
        "event: status\n"
        'data: {"request_id":"00000000-0000-4000-8000-000000000001",'
        '"seq":1,"payload":{"stage":"searching"}}\n\n'
    )
