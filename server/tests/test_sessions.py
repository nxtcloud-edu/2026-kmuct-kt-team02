import json
import logging
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from server.config import Settings
from server.main import create_app
from server.rule_engine import RuleEngineResult
from server.schemas import PolicyEvaluation, Profile
from server.session_store import SessionStore


VALID_PROFILE_INPUT = {
    "age": 22,
    "district": "마포구",
    "status": "enrolled",
    "categories": ["scholarship", "housing"],
}


def make_policy(index: int) -> PolicyEvaluation:
    return PolicyEvaluation.model_validate(
        {
            "policy_id": f"SEOUL-{index:03d}",
            "title": f"검수 정책 {index}",
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
            "source_url": f"https://youth.seoul.go.kr/policy/{index}",
            "apply_url": f"https://youth.seoul.go.kr/apply/{index}",
            "checked_at": "2026-09-20",
            "data_status": "verified",
        }
    )


class FakeRuleEngine:
    def __init__(self) -> None:
        self.received_profiles: list[Profile] = []
        self.received_limits: list[int] = []
        self.result = RuleEngineResult(
            policies=[make_policy(index) for index in range(1, 4)],
            hidden_unlikely_count=2,
        )

    def evaluate(self, profile: Profile, *, limit: int = 5) -> RuleEngineResult:
        self.received_profiles.append(profile.model_copy(deep=True))
        self.received_limits.append(limit)
        return self.result


def settings() -> Settings:
    return Settings(
        localhost_cors_origins=(),
        s3_cors_origins=(),
        llm_adapter_name=None,
        session_ttl_seconds=1800,
    )


def client_with_fake() -> tuple[TestClient, FakeRuleEngine, SessionStore]:
    engine = FakeRuleEngine()
    store = SessionStore(ttl_seconds=1800)
    app = create_app(
        settings=settings(),
        rule_engine=engine,
        session_store=store,
    )
    return TestClient(app), engine, store


def test_create_session_returns_uuid_normalized_profile_and_rule_results() -> None:
    client, engine, store = client_with_fake()

    response = client.post("/session", json=VALID_PROFILE_INPUT)

    assert response.status_code == 200
    body = response.json()
    session_id = UUID(body["session_id"])
    assert session_id.version == 4
    assert body["profile"]["region"] == "seoul"
    assert body["profile"]["income_bracket"] == "unknown"
    assert [policy["policy_id"] for policy in body["policies"]] == [
        "SEOUL-001",
        "SEOUL-002",
        "SEOUL-003",
    ]
    assert body["hidden_unlikely_count"] == 2
    assert body["followup"] is None
    assert engine.received_limits == [5]
    assert engine.received_profiles[0].region == "seoul"
    assert store.get(session_id).profile == engine.received_profiles[0]


def test_invalid_district_returns_422() -> None:
    client, _engine, _store = client_with_fake()

    response = client.post(
        "/session",
        json={**VALID_PROFILE_INPUT, "district": "서울특별시"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_input"


@pytest.mark.parametrize("age", [14, 40])
def test_out_of_range_age_returns_422(age: int) -> None:
    client, _engine, _store = client_with_fake()

    response = client.post("/session", json={**VALID_PROFILE_INPUT, "age": age})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_input"


def test_income_bracket_unknown_is_allowed_but_income_pct_is_not_in_contract() -> None:
    client, _engine, _store = client_with_fake()

    accepted = client.post(
        "/session",
        json={**VALID_PROFILE_INPUT, "income_bracket": "unknown"},
    )
    rejected = client.post(
        "/session",
        json={**VALID_PROFILE_INPUT, "income_pct": None},
    )

    assert accepted.status_code == 200
    assert accepted.json()["profile"]["income_bracket"] == "unknown"
    assert rejected.status_code == 422
    assert rejected.json()["error"]["code"] == "invalid_input"


def test_unconnected_rule_engine_returns_explicit_503_without_creating_session() -> None:
    store = SessionStore(ttl_seconds=1800)
    app = create_app(settings=settings(), session_store=store)
    client = TestClient(app)

    response = client.post("/session", json=VALID_PROFILE_INPUT)

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "server_error",
            "message": "규칙 엔진 의존성이 연결되지 않았습니다",
            "details": [],
        }
    }
    assert len(store) == 0


def test_full_profile_is_not_written_to_logs(caplog: pytest.LogCaptureFixture) -> None:
    client, _engine, _store = client_with_fake()
    caplog.set_level(logging.DEBUG)

    response = client.post("/session", json=VALID_PROFILE_INPUT)

    assert response.status_code == 200
    serialized_profile = json.dumps(VALID_PROFILE_INPUT, ensure_ascii=False, sort_keys=True)
    assert serialized_profile not in caplog.text
    assert repr(VALID_PROFILE_INPUT) not in caplog.text
