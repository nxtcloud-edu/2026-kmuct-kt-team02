import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from server.config import Settings
from server.main import create_app
from server.schemas import Profile, ProfileInput


VALID_PROFILE_INPUT = {
    "age": 22,
    "district": "마포구",
    "status": "enrolled",
    "categories": ["scholarship", "housing"],
}


def test_profile_defaults_region_to_seoul_without_input() -> None:
    profile_input = ProfileInput.model_validate(VALID_PROFILE_INPUT)
    profile = Profile.from_input(profile_input)

    assert "region" not in profile_input.model_dump()
    assert profile.region == "seoul"
    assert profile.income_bracket == "unknown"


def test_profile_rejects_out_of_range_age() -> None:
    with pytest.raises(ValidationError):
        ProfileInput.model_validate({**VALID_PROFILE_INPUT, "age": 14})


def test_profile_rejects_region_as_client_input() -> None:
    with pytest.raises(ValidationError):
        ProfileInput.model_validate({**VALID_PROFILE_INPUT, "region": "seoul"})


def test_invalid_profile_request_returns_common_422_response() -> None:
    app = create_app(
        settings=Settings(
            localhost_cors_origins=(),
            s3_cors_origins=(),
            llm_adapter_name=None,
        )
    )
    client = TestClient(app)

    response = client.post(
        "/session",
        json={**VALID_PROFILE_INPUT, "age": 40},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "invalid_input"
    assert body["error"]["message"] == "입력한 정보를 다시 확인해 주세요"
    assert body["error"]["details"][0]["location"] == ["body", "age"]


def test_region_request_field_returns_422() -> None:
    app = create_app(
        settings=Settings(
            localhost_cors_origins=(),
            s3_cors_origins=(),
            llm_adapter_name=None,
        )
    )
    client = TestClient(app)

    response = client.post(
        "/session",
        json={**VALID_PROFILE_INPUT, "region": "outside_seoul"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_input"


def test_profile_requires_district() -> None:
    profile_without_district = {
        key: value for key, value in VALID_PROFILE_INPUT.items() if key != "district"
    }

    with pytest.raises(ValidationError):
        ProfileInput.model_validate(profile_without_district)