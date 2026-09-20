import asyncio
import json
from collections.abc import AsyncIterator

from fastapi.testclient import TestClient

from server.config import Settings
from server.main import create_app
from server.orchestrator import (
    BackendBOrchestrator,
    ExceptionJudgeRequest,
    OrchestratorTimeouts,
    StructuredLLMRequest,
    TextLLMRequest,
)
from server.rule_engine import RuleEngineResult
from server.schemas import ConditionEvaluation, Policy, PolicyEvaluation, Profile
from server.session_store import SessionStore

RULE_EXCERPT = "만 19세부터 39세까지 신청할 수 있습니다."
EXCEPTION_EXCERPT = "다른 지원을 받고 있으면 신청 전에 확인이 필요합니다."
RAW_TEXT = f"{RULE_EXCERPT} {EXCEPTION_EXCERPT}"
CLOSING = "최종 신청 전 공식 공고에서 다시 확인하세요."
LIKELY_ANSWER = (
    "신청 가능성이 높은 제도 1개, 확인이 필요한 제도 0개를 찾았어요. "
    f"나이 조건을 충족해요[1]. {CLOSING}"
)
CHECK_ANSWER = (
    "신청 가능성이 높은 제도 0개, 확인이 필요한 제도 1개를 찾았어요. "
    f"나이 조건을 충족해요[1]. {CLOSING}"
)


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


def policy_evaluation() -> PolicyEvaluation:
    return PolicyEvaluation.model_validate(
        {
            "policy_id": "SEOUL-001",
            "title": "검수 정책",
            "agency": "서울특별시",
            "categories": ["scholarship"],
            "status": "likely",
            "status_label": "신청 가능성이 높아요",
            "benefit": "검수된 혜택",
            "conditions": [
                {
                    "name": "나이 조건",
                    "result": "met",
                    "judged_by": "rule",
                    "excerpt": RULE_EXCERPT,
                    "source_url": "https://youth.seoul.go.kr/policy/1",
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
            "documents": ["신청서"],
            "steps": ["공식 페이지에서 신청"],
            "source_url": "https://youth.seoul.go.kr/policy/1",
            "apply_url": "https://youth.seoul.go.kr/apply/1",
            "checked_at": "2026-09-20",
            "data_status": "verified",
        }
    )


def policy_source(*, with_exception: bool = True) -> Policy:
    return Policy.model_validate(
        {
            "id": "SEOUL-001",
            "title": "검수 정책",
            "agency": "서울특별시",
            "categories": ["scholarship"],
            "source_url": "https://youth.seoul.go.kr/policy/1",
            "checked_at": "2026-09-20",
            "exceptions_text": EXCEPTION_EXCERPT if with_exception else "",
            "raw_text": RAW_TEXT,
        }
    )


class FakeRuleEngine:
    def __init__(self) -> None:
        self.received_profiles: list[Profile] = []

    def evaluate(self, profile: Profile, *, limit: int = 5) -> RuleEngineResult:
        self.received_profiles.append(profile.model_copy(deep=True))
        return RuleEngineResult(
            policies=[policy_evaluation()],
            hidden_unlikely_count=0,
        )


class FakePolicySources:
    def __init__(self, source: Policy) -> None:
        self.source = source

    def get(self, policy_id: str) -> Policy | None:
        return self.source if policy_id == self.source.id else None


class FakeExceptionJudge:
    def __init__(self, output: object, *, delay_s: float = 0) -> None:
        self.output = output
        self.delay_s = delay_s
        self.requests: list[ExceptionJudgeRequest] = []

    async def judge(self, request: ExceptionJudgeRequest) -> object:
        self.requests.append(request)
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        return self.output


class FakeLLMClient:
    def __init__(
        self,
        *,
        interpretation: object | None = None,
        answer_text: str = LIKELY_ANSWER,
        interpretation_delay_s: float = 0,
        answer_delay_s: float = 0,
    ) -> None:
        self.interpretation = interpretation or {"intent": "find_policy"}
        self.answer_text = answer_text
        self.interpretation_delay_s = interpretation_delay_s
        self.answer_delay_s = answer_delay_s
        self.structured_requests: list[StructuredLLMRequest] = []
        self.text_requests: list[TextLLMRequest] = []

    async def complete_json(self, request: StructuredLLMRequest) -> object:
        self.structured_requests.append(request)
        if self.interpretation_delay_s:
            await asyncio.sleep(self.interpretation_delay_s)
        return self.interpretation

    async def stream_text(self, request: TextLLMRequest) -> AsyncIterator[str]:
        self.text_requests.append(request)
        if self.answer_delay_s:
            await asyncio.sleep(self.answer_delay_s)
        midpoint = len(self.answer_text) // 2
        yield self.answer_text[:midpoint]
        yield self.answer_text[midpoint:]


def parse_sse(body: str) -> list[tuple[str, dict[str, object]]]:
    events: list[tuple[str, dict[str, object]]] = []
    for frame in body.strip().split("\n\n"):
        lines = frame.splitlines()
        events.append(
            (
                lines[0].removeprefix("event: "),
                json.loads(lines[1].removeprefix("data: ")),
            )
        )
    return events


def build_client(
    *,
    llm: FakeLLMClient,
    judge: FakeExceptionJudge | None,
    source: Policy,
    timeouts: OrchestratorTimeouts | None = None,
) -> tuple[TestClient, SessionStore, FakeRuleEngine, str]:
    store = SessionStore(ttl_seconds=1800)
    session = store.create(profile())
    rules = FakeRuleEngine()
    orchestrator = BackendBOrchestrator(
        llm_client=llm,
        rule_engine=rules,
        policy_sources=FakePolicySources(source),
        exception_judge=judge,
        session_store=store,
        timeouts=timeouts,
    )
    app = create_app(
        settings=settings(),
        session_store=store,
        chat_service=orchestrator,
    )
    return TestClient(app), store, rules, str(session.session_id)


def post_chat(client: TestClient, session_id: str):
    return client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "제 상황에 맞는 정책을 알려줘",
            "client_message_id": "orchestrator-test-message",
        },
    )


def payloads(events: list[tuple[str, dict[str, object]]], name: str) -> list[dict]:
    return [data["payload"] for event, data in events if event == name]


def test_normal_flow_emits_initial_then_verified_updated_policies() -> None:
    llm = FakeLLMClient()
    judge = FakeExceptionJudge(
        {
            "conditions": [
                {
                    "summary": "중복 지원 확인",
                    "result": "충족",
                    "excerpt": EXCEPTION_EXCERPT,
                    "needed_field": None,
                }
            ]
        }
    )
    client, _store, _rules, session_id = build_client(
        llm=llm,
        judge=judge,
        source=policy_source(),
    )

    response = post_chat(client, session_id)
    events = parse_sse(response.text)
    names = [name for name, _data in events]

    assert response.status_code == 200
    assert names == [
        "status",
        "policies",
        "status",
        "policies",
        "status",
        "answer_delta",
        "answer_delta",
        "answer_delta",
        "footnotes",
        "done",
    ]
    policy_events = payloads(events, "policies")
    assert len(policy_events[0]["policies"][0]["conditions"]) == 1
    assert len(policy_events[1]["policies"][0]["conditions"]) == 2
    assert policy_events[1]["policies"][0]["status"] == "likely"
    assert names.index("policies") < names.index("answer_delta")
    assert len(payloads(events, "footnotes")[0]["footnotes"]) == 2
    assert names[-1] == "done"


def test_conversation_housing_change_updates_session_before_rules() -> None:
    llm = FakeLLMClient(
        interpretation={
            "intent": "find_policy",
            "extra_answers": [
                {
                    "field": "housing_type",
                    "value": "monthly_rent",
                    "timing": "current",
                }
            ],
        }
    )
    client, store, rules, session_id = build_client(
        llm=llm,
        judge=None,
        source=policy_source(with_exception=False),
    )

    response = post_chat(client, session_id)
    events = parse_sse(response.text)
    names = [name for name, _data in events]

    assert response.status_code == 200
    assert names.index("profile_update") < names.index("policies")
    update = payloads(events, "profile_update")[0]
    assert update["changed_fields"] == {"housing_type": "monthly_rent"}
    assert rules.received_profiles[0].housing_type == "monthly_rent"
    assert store.get(session_id).profile.housing_type == "monthly_rent"


def test_failed_citation_is_unknown_and_never_reaches_answer_or_footnotes() -> None:
    hallucinated = "원문에 존재하지 않는 새로운 자격 조건 문장입니다."
    llm = FakeLLMClient(answer_text=CHECK_ANSWER)
    judge = FakeExceptionJudge(
        {
            "conditions": [
                {
                    "summary": "새로운 자격 조건",
                    "result": "미충족",
                    "excerpt": hallucinated,
                    "needed_field": None,
                }
            ]
        }
    )
    client, _store, _rules, session_id = build_client(
        llm=llm,
        judge=judge,
        source=policy_source(),
    )

    events = parse_sse(post_chat(client, session_id).text)
    final_policy = payloads(events, "policies")[-1]["policies"][0]
    ai_condition = final_policy["conditions"][-1]
    answer_text = " ".join(item["delta"] for item in payloads(events, "answer_delta"))
    footnotes = payloads(events, "footnotes")[0]["footnotes"]

    assert final_policy["status"] == "check"
    assert final_policy["status_label"] == "확인이 필요해요"
    assert ai_condition["result"] == "unknown"
    assert ai_condition["excerpt"] is None
    assert hallucinated not in answer_text
    assert all(item["excerpt"] != hallucinated for item in footnotes)


def test_invalid_ai_json_is_downgraded_instead_of_trusted() -> None:
    llm = FakeLLMClient(answer_text=CHECK_ANSWER)
    judge = FakeExceptionJudge(
        {
            "conditions": [
                {
                    "summary": "중복 지원 확인",
                    "result": "미충족",
                    "excerpt": EXCEPTION_EXCERPT,
                    "needed_field": None,
                    "status": "unlikely",
                    "source_url": "https://untrusted.example/fake",
                }
            ]
        }
    )
    client, _store, _rules, session_id = build_client(
        llm=llm,
        judge=judge,
        source=policy_source(),
    )

    events = parse_sse(post_chat(client, session_id).text)
    final_policy = payloads(events, "policies")[-1]["policies"][0]
    ai_condition = final_policy["conditions"][-1]

    assert final_policy["status"] == "check"
    assert final_policy["status_label"] == "확인이 필요해요"
    assert ai_condition["result"] == "unknown"
    assert ai_condition["source_url"].startswith("https://youth.seoul.go.kr/")
    assert "untrusted.example" not in json.dumps(events, ensure_ascii=False)


def test_answer_timeout_keeps_policies_and_finishes_with_fixed_fallback() -> None:
    llm = FakeLLMClient(answer_delay_s=0.05)
    client, _store, _rules, session_id = build_client(
        llm=llm,
        judge=None,
        source=policy_source(with_exception=False),
        timeouts=OrchestratorTimeouts(
            interpretation_s=0.1,
            rule_engine_s=0.1,
            judgment_s=0.1,
            answer_s=0.001,
        ),
    )

    events = parse_sse(post_chat(client, session_id).text)
    names = [name for name, _data in events]
    answer_text = " ".join(item["delta"] for item in payloads(events, "answer_delta"))

    assert "policies" in names
    assert answer_text == "설명을 불러오지 못했어요. 카드에서 조건을 확인해 주세요."
    assert names[-1] == "done"
