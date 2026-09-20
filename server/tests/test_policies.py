from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from server.config import Settings
from server.main import create_app
from server.rule_engine import RuleEngineResult
from server.schemas import Policy, PolicyEvaluation, Profile
from server.session_store import SessionStore

RULE_EXCERPT = "만 19세부터 39세까지 신청할 수 있습니다."


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


def source_policy(*, checked: bool = True) -> Policy:
    return Policy.model_validate(
        {
            "id": "SEOUL-DETAIL-001",
            "title": "서울 청년 장학 지원",
            "agency": "서울특별시",
            "categories": ["scholarship"],
            "source_url": "https://youth.seoul.go.kr/policies/detail-001",
            "apply_url": "https://youth.seoul.go.kr/apply/detail-001",
            "checked_at": "2026-09-20" if checked else None,
            "benefit": "학기당 100만원",
            "documents": ["신청서", "재학증명서"],
            "steps": ["온라인 신청", "서류 심사"],
            "raw_text": RULE_EXCERPT,
        }
    )


def detail(policy: Policy) -> PolicyEvaluation:
    return PolicyEvaluation.model_validate(
        {
            "policy_id": policy.id,
            "title": policy.title,
            "agency": policy.agency,
            "categories": [item.value for item in policy.categories],
            "status": "likely",
            "status_label": "신청 가능성이 높아요",
            "benefit": policy.benefit,
            "conditions": [
                {
                    "name": "나이 조건",
                    "result": "met",
                    "judged_by": "rule",
                    "excerpt": RULE_EXCERPT,
                    "source_url": str(policy.source_url),
                    "footnote_id": 1,
                    "needed_field": None,
                }
            ],
            "deadline": {
                "apply_start": "2026-09-01",
                "apply_end": "2026-10-01",
                "d_day": 11,
                "badge": "D-11",
                "is_imminent": False,
            },
            "documents": policy.documents,
            "steps": policy.steps,
            "source_url": str(policy.source_url),
            "apply_url": str(policy.apply_url) if policy.apply_url else None,
            "checked_at": policy.checked_at,
            "data_status": "verified",
        }
    )


class FakePolicyRepository:
    def __init__(self, policies: list[Policy]) -> None:
        self.policies = {policy.id: policy for policy in policies}

    def count_verified(self) -> int:
        return sum(policy.checked_at is not None for policy in self.policies.values())

    def get(self, policy_id: str) -> Policy | None:
        return self.policies.get(policy_id)


class DetailRuleEngine:
    def __init__(self) -> None:
        self.detail_calls: list[tuple[Profile, Policy]] = []

    def evaluate(self, profile: Profile, *, limit: int = 5) -> RuleEngineResult:
        return RuleEngineResult(policies=[])

    def evaluate_policy(self, profile: Profile, policy: Policy) -> PolicyEvaluation:
        self.detail_calls.append((profile.model_copy(deep=True), policy))
        return detail(policy)


def make_client(
    policies: list[Policy],
    *,
    store: SessionStore | None = None,
) -> tuple[TestClient, SessionStore, DetailRuleEngine, str]:
    session_store = store if store is not None else SessionStore(ttl_seconds=1800)
    session = session_store.create(profile())
    rules = DetailRuleEngine()
    app = create_app(
        settings=settings(),
        session_store=session_store,
        policy_catalog=FakePolicyRepository(policies),
        rule_engine=rules,
    )
    return TestClient(app), session_store, rules, str(session.session_id)


def test_policy_detail_contains_required_panel_fields_and_personal_evaluation() -> None:
    policy = source_policy()
    client, _store, rules, session_id = make_client([policy])

    response = client.get(
        f"/policies/{policy.id}",
        params={"session_id": session_id},
    )

    assert response.status_code == 200
    body = response.json()
    assert {
        "policy_id",
        "title",
        "agency",
        "conditions",
        "benefit",
        "deadline",
        "documents",
        "steps",
        "source_url",
        "apply_url",
        "checked_at",
    }.issubset(body)
    assert body["conditions"][0]["result"] == "met"
    assert body["benefit"] == "학기당 100만원"
    assert body["documents"] == ["신청서", "재학증명서"]
    assert body["steps"] == ["온라인 신청", "서류 심사"]
    assert body["agency"] == "서울특별시"
    assert body["source_url"].startswith("https://youth.seoul.go.kr/")
    assert body["checked_at"] == "2026-09-20"
    assert rules.detail_calls[0][0].district == "마포구"
    assert rules.detail_calls[0][1] == policy


def test_missing_policy_returns_policy_not_found_404() -> None:
    client, _store, rules, session_id = make_client([])

    response = client.get(
        "/policies/UNKNOWN",
        params={"session_id": session_id},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "policy_not_found"
    assert rules.detail_calls == []


def test_policy_without_checked_at_is_excluded_from_detail() -> None:
    policy = source_policy(checked=False)
    client, _store, rules, session_id = make_client([policy])

    response = client.get(
        f"/policies/{policy.id}",
        params={"session_id": session_id},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "policy_not_found"
    assert rules.detail_calls == []


def test_expired_session_returns_404_before_policy_lookup() -> None:
    clock = MutableClock(datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc))
    store = SessionStore(ttl_seconds=60, clock=clock)
    policy = source_policy()
    client, session_store, rules, session_id = make_client([policy], store=store)
    clock.advance(seconds=60)

    response = client.get(
        f"/policies/{policy.id}",
        params={"session_id": session_id},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_expired"
    assert len(session_store) == 0
    assert rules.detail_calls == []
