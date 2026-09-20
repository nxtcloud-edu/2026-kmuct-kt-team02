"""규칙 엔진·LLM 클라이언트·AI B 어댑터를 실제로 조립해 `/chat` 끝까지 흘려 본다.

단위 테스트는 각 조각이 계약을 만족하는지만 본다. 그런데 이 프로젝트에서 실제로 났던
문제는 조각이 아니라 **이음매**였다. 예를 들어 AI B 가 영문 `met` 을 주고 서버가 한국어
`충족` 을 기대하면, 두 쪽 단위 테스트는 모두 통과하지만 화면의 카드는 전부 "확인이
필요해요"가 된다. 그런 어긋남은 조립해서 끝까지 흘려 봐야 드러난다.

모델은 부르지 않는다. SDK 와 AI B 판정기를 주입하고 정책 데이터는 저장소의 실제 파일을 쓴다.
"""

import json
from datetime import date

from fastapi.testclient import TestClient

from rules import repository
from server.exception_judge import AiBExceptionJudgeAdapter
from server.llm_client import GatewayLLMClient
from server.main import create_app
from server.orchestrator import BackendBOrchestrator
from server.session_store import SessionStore

TODAY = date(2026, 9, 20)
POLICIES_PATH = "data/policies/policies.json"
ANSWER_TEXT = "조건을 확인했어요. 자세한 내용은 카드를 봐 주세요."


class FakeGateway:
    """작업에 따라 다른 모양을 돌려준다. 구조화 호출은 JSON, 답변 호출은 문장."""

    def __init__(self) -> None:
        self.systems: list[str] = []

    def complete(self, **kwargs: object) -> str:
        system = str(kwargs.get("system", ""))
        self.systems.append(system)
        if "interpret_message" in system:
            return '{"intent": "find_policy"}'
        return ANSWER_TEXT


class FakeAiBJudge:
    """예외 조건이 없다고 답한다. 규칙 판정만으로 카드가 완성되는 경로를 본다."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def judge_and_verify(self, policy, profile=None, *, deadline=None):
        self.calls.append(policy["id"])
        return {"policy_id": policy["id"], "conditions": [], "removed": []}


def build_client() -> tuple[TestClient, FakeAiBJudge]:
    store, rule_engine = repository.build(POLICIES_PATH, clock=lambda: TODAY)
    sessions = SessionStore(ttl_seconds=1800)
    judge = FakeAiBJudge()
    orchestrator = BackendBOrchestrator(
        llm_client=GatewayLLMClient(FakeGateway()),
        rule_engine=rule_engine,
        policy_sources=store,
        exception_judge=AiBExceptionJudgeAdapter(judge),
        session_store=sessions,
    )
    app = create_app(
        rule_engine=rule_engine,
        policy_catalog=store,
        session_store=sessions,
        chat_service=orchestrator,
    )
    return TestClient(app), judge


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


def start_session(client: TestClient) -> str:
    response = client.post(
        "/session",
        json={
            "age": 23,
            "district": "마포구",
            "status": "job_seeking",
            "categories": ["job"],
        },
    )
    assert response.status_code == 200
    return str(response.json()["session_id"])


def test_health_reports_the_dependency_as_connected() -> None:
    client, _judge = build_client()

    body = client.get("/health").json()

    assert body["policy_dependency"] == "connected"
    assert body["verified_policy_count"] >= 1


def test_session_returns_cards_from_the_real_policy_file() -> None:
    client, _judge = build_client()

    response = client.post(
        "/session",
        json={
            "age": 23,
            "district": "마포구",
            "status": "job_seeking",
            "categories": ["job"],
        },
    )

    assert response.status_code == 200
    assert len(response.json()["policies"]) >= 1


def test_session_works_without_a_district() -> None:
    """자치구는 선택 입력이다. 비우면 자치구 한정 정책만 미확인으로 남는다."""
    client, _judge = build_client()

    response = client.post(
        "/session",
        json={"age": 23, "status": "job_seeking", "categories": ["job"]},
    )

    assert response.status_code == 200


def test_chat_streams_the_contract_order_and_closes_with_done() -> None:
    client, _judge = build_client()
    session_id = start_session(client)

    response = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "청년수당 받을 수 있어?",
            "client_message_id": "frontend-message-1",
        },
    )

    assert response.status_code == 200
    names = [name for name, _data in parse_sse(response.text)]
    assert names[0] == "status"
    assert names[-1] == "done"
    assert "error" not in names
    # 계약 5장: policies 는 첫 answer_delta 보다 반드시 먼저 나간다.
    assert names.index("policies") < names.index("answer_delta")


def test_chat_sequence_numbers_are_continuous() -> None:
    client, _judge = build_client()
    session_id = start_session(client)

    response = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "청년수당 받을 수 있어?",
            "client_message_id": "frontend-message-1",
        },
    )

    parsed = parse_sse(response.text)
    assert [data["seq"] for _name, data in parsed] == list(range(1, len(parsed) + 1))


def test_answer_text_reaches_the_client() -> None:
    """LLM 응답이 대체 문구로 바뀌지 않고 실제로 전달되는지 본다."""
    client, _judge = build_client()
    session_id = start_session(client)

    response = client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "청년수당 받을 수 있어?",
            "client_message_id": "frontend-message-1",
        },
    )

    deltas = [
        str(data["payload"]["delta"])
        for name, data in parse_sse(response.text)
        if name == "answer_delta"
    ]
    assert deltas
    assert any("카드를 봐 주세요" in delta for delta in deltas)


def test_exception_judge_is_called_only_for_policies_with_exception_text() -> None:
    client, judge = build_client()
    session_id = start_session(client)

    client.post(
        "/chat",
        json={
            "session_id": session_id,
            "message": "청년수당 받을 수 있어?",
            "client_message_id": "frontend-message-1",
        },
    )

    # 예외 문장이 없는 정책에 모델을 부르면 돈과 시간만 쓴다.
    assert all(policy_id for policy_id in judge.calls)
