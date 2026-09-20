from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from server.config import Settings
from server.main import create_app
from server.rule_engine import RuleEngineResult
from server.schemas import Policy, PolicyEvaluation, Profile
from server.session_store import SessionStore


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
            "income_bracket": "under_50",
            "housing_type": "monthly_rent",
        }
    )


def evaluation(policy_id: str, category: str) -> PolicyEvaluation:
    return PolicyEvaluation.model_validate(
        {
            "policy_id": policy_id,
            "title": f"{category} 추천 정책",
            "agency": "서울특별시",
            "categories": [category],
            "status": "likely",
            "status_label": "신청 가능성이 높아요",
            "benefit": "검수된 혜택",
            "conditions": [],
            "deadline": {"badge": "D-10", "is_imminent": False, "d_day": 10},
            "documents": [],
            "steps": [],
            "source_url": f"https://youth.seoul.go.kr/{policy_id}",
            "checked_at": "2026-09-20",
            "data_status": "verified",
        }
    )


class DynamicRuleEngine:
    def __init__(self) -> None:
        self.profiles: list[Profile] = []

    def evaluate(self, profile: Profile, *, limit: int = 5) -> RuleEngineResult:
        self.profiles.append(profile.model_copy(deep=True))
        category = profile.categories[0].value
        return RuleEngineResult(
            policies=[evaluation(f"REC-{category.upper()}", category)],
            hidden_unlikely_count=0,
        )

    def evaluate_policy(self, profile: Profile, policy: Policy) -> PolicyEvaluation:
        return evaluation(policy.id, policy.categories[0].value)


def make_client(
    *,
    store: SessionStore | None = None,
) -> tuple[TestClient, SessionStore, DynamicRuleEngine, str]:
    session_store = store if store is not None else SessionStore(ttl_seconds=1800)
    session = session_store.create(profile())
    rules = DynamicRuleEngine()
    app = create_app(
        settings=settings(),
        session_store=session_store,
        rule_engine=rules,
    )
    return TestClient(app), session_store, rules, str(session.session_id)


def test_patch_revalidates_profile_and_returns_changed_recommendations() -> None:
    client, store, rules, session_id = make_client()

    response = client.patch(
        f"/session/{session_id}/profile",
        json={"categories": ["housing"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["profile"]["categories"] == ["housing"]
    assert body["profile"]["income_bracket"] == "under_50"
    assert body["profile"]["housing_type"] == "monthly_rent"
    assert body["policies"][0]["policy_id"] == "REC-HOUSING"
    assert rules.profiles[0].categories[0] == "housing"
    assert store.get(session_id).profile.categories[0] == "housing"


def test_income_null_resets_to_unknown() -> None:
    client, store, rules, session_id = make_client()

    response = client.patch(
        f"/session/{session_id}/profile",
        json={"income_bracket": None},
    )

    assert response.status_code == 200
    assert response.json()["profile"]["income_bracket"] == "unknown"
    assert rules.profiles[0].income_bracket == "unknown"
    assert store.get(session_id).profile.income_bracket == "unknown"


def test_nullable_conversation_field_can_be_cleared() -> None:
    client, store, _rules, session_id = make_client()

    response = client.patch(
        f"/session/{session_id}/profile",
        json={"housing_type": None},
    )

    assert response.status_code == 200
    assert response.json()["profile"]["housing_type"] is None
    assert store.get(session_id).profile.housing_type is None


@pytest.mark.parametrize("district", [None, "서울특별시"])
def test_district_cannot_be_cleared_or_set_to_invalid_value(district: object) -> None:
    client, _store, rules, session_id = make_client()

    response = client.patch(
        f"/session/{session_id}/profile",
        json={"district": district},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_input"
    assert rules.profiles == []


def test_expired_session_returns_404_without_rule_call() -> None:
    clock = MutableClock(datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc))
    store = SessionStore(ttl_seconds=60, clock=clock)
    client, session_store, rules, session_id = make_client(store=store)
    clock.advance(seconds=60)

    response = client.patch(
        f"/session/{session_id}/profile",
        json={"status": "on_leave"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_expired"
    assert len(session_store) == 0
    assert rules.profiles == []
