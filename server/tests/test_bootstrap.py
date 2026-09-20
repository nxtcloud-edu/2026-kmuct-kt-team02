"""`server/bootstrap.py` 조립 규칙 고정.

여기서 잡으려는 실패는 "오류 없이 동작만 틀어지는" 종류다. 예를 들어 앱과 오케스트레이터가
서로 다른 세션 저장소를 쓰면 대화 중 프로필 변경이 `PATCH /session` 결과와 어긋나는데,
예외는 나지 않는다. 그런 규칙은 테스트로 고정해야 남는다.
"""

import json
from datetime import date

from fastapi.testclient import TestClient

from server.bootstrap import (
    DEFAULT_POLICIES_PATH,
    POLICIES_PATH_ENV,
    build_dependencies,
    create_default_app,
    resolve_policies_path,
)

TODAY = date(2026, 9, 20)
BASE_ENV = {"CORS_LOCALHOST_ORIGINS": "http://localhost:5173"}
WITH_KEYS = {**BASE_ENV, "API_KEY": "sk-not-real", "LLM_MODEL": "bedrock-haiku"}


def build(env=None, policies_path=None):
    return build_dependencies(
        env=BASE_ENV if env is None else env,
        policies_path=policies_path,
        clock=lambda: TODAY,
    )


# --- 경로 ------------------------------------------------------------------


def test_default_policies_path_is_used_when_nothing_is_given() -> None:
    assert str(resolve_policies_path(None, {})) == DEFAULT_POLICIES_PATH


def test_env_overrides_the_policies_path() -> None:
    resolved = resolve_policies_path(None, {POLICIES_PATH_ENV: "other/policies.json"})

    assert str(resolved) == "other/policies.json"


def test_explicit_path_wins_over_env() -> None:
    resolved = resolve_policies_path(
        "explicit.json", {POLICIES_PATH_ENV: "from-env.json"}
    )

    assert str(resolved) == "explicit.json"


# --- 정상 조립 -------------------------------------------------------------


def test_real_policy_file_is_connected() -> None:
    dependencies = build()

    assert dependencies.policies_connected
    assert dependencies.rule_engine is not None
    assert dependencies.chat_service is not None
    assert dependencies.policy_catalog.count_verified() >= 1


def test_app_and_chat_service_share_one_session_store() -> None:
    """다른 저장소를 주면 대화 중 프로필 변경이 PATCH 결과와 어긋난다. 조용히 어긋난다."""
    dependencies = build()

    assert dependencies.chat_service._session_store is dependencies.session_store


def test_session_ttl_comes_from_settings() -> None:
    dependencies = build({**BASE_ENV, "SESSION_TTL_SECONDS": "60"})

    assert dependencies.settings.session_ttl_seconds == 60


# --- 정책 데이터가 없을 때 -------------------------------------------------


def test_missing_policy_file_stays_unconnected(tmp_path) -> None:
    """0건을 connected 로 보고하면 원인을 데이터가 아니라 판정에서 찾게 된다."""
    dependencies = build(policies_path=tmp_path / "없는파일.json")

    assert not dependencies.policies_connected
    assert dependencies.rule_engine is None
    assert dependencies.chat_service is None
    assert dependencies.data_issues


def test_missing_policy_file_does_not_raise(tmp_path) -> None:
    """서버는 떠야 한다. 떠야 /health 로 무엇이 잘못됐는지 알릴 수 있다."""
    app = create_default_app(
        env=BASE_ENV, policies_path=tmp_path / "없는파일.json"
    )

    body = TestClient(app).get("/health").json()
    assert body["policy_dependency"] == "unconnected"
    assert body["verified_policy_count"] is None


def test_unconnected_session_returns_503_with_a_clear_message(tmp_path) -> None:
    app = create_default_app(env=BASE_ENV, policies_path=tmp_path / "없는파일.json")

    response = TestClient(app).post(
        "/session",
        json={"age": 23, "status": "job_seeking", "categories": ["job"]},
    )

    assert response.status_code == 503


def test_broken_json_stays_unconnected(tmp_path) -> None:
    path = tmp_path / "policies.json"
    path.write_text("{ 망가진", encoding="utf-8")

    dependencies = build(policies_path=path)

    assert not dependencies.policies_connected
    assert dependencies.data_issues


def test_invalid_rows_are_dropped_but_the_rest_connects(tmp_path) -> None:
    """정책 한 건이 잘못돼도 나머지 요청은 정상으로 처리돼야 한다."""
    good = {
        "id": "SEOUL-001",
        "title": "정상 정책",
        "agency": "서울특별시",
        "categories": ["job"],
        "statuses": [],
        "data_status": "verified",
        "source_kind": "manual",
    }
    path = tmp_path / "policies.json"
    path.write_text(
        json.dumps({"policies": [good, {"id": "깨진아이디"}]}, ensure_ascii=False),
        encoding="utf-8",
    )

    dependencies = build(policies_path=path)

    assert dependencies.policies_connected
    assert len(dependencies.policy_catalog) == 1
    assert dependencies.data_issues


# --- 모델 설정이 없을 때 ---------------------------------------------------


def test_missing_model_settings_still_connects_chat() -> None:
    """여기서 chat_service 를 비우면 /chat 이 503 이 되어 카드까지 사라진다."""
    dependencies = build(BASE_ENV)

    assert dependencies.missing_llm_settings == ("API_KEY", "LLM_MODEL")
    assert dependencies.chat_service is not None


def test_model_settings_present_reports_no_gaps() -> None:
    dependencies = build(WITH_KEYS)

    assert dependencies.missing_llm_settings == ()


def test_chat_completes_without_model_settings() -> None:
    """AI 는 건너뛰고 규칙 카드와 대체 문구로 done 까지 간다."""
    app = create_default_app(env=BASE_ENV)
    client = TestClient(app)
    session = client.post(
        "/session",
        json={"age": 23, "status": "job_seeking", "categories": ["job"]},
    )

    response = client.post(
        "/chat",
        json={
            "session_id": session.json()["session_id"],
            "message": "청년수당 받을 수 있어?",
            "client_message_id": "frontend-message-1",
        },
    )

    assert response.status_code == 200
    names = [
        frame.splitlines()[0].removeprefix("event: ")
        for frame in response.text.strip().split("\n\n")
    ]
    assert names[-1] == "done"
    assert "policies" in names


def test_dependencies_never_hold_a_key_value() -> None:
    """조립 결과가 로그나 디버깅 출력에 실려도 키가 새지 않아야 한다."""
    dependencies = build(WITH_KEYS)

    assert "sk-not-real" not in repr(dependencies)
