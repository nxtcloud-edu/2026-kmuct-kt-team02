"""환경에서 실제 의존성을 조립한다.

`create_app` 은 의존성을 인자로만 받는다. 그건 테스트가 가짜를 꽂을 수 있게 하려는
의도된 설계다. 대신 **아무도 진짜를 꽂아 주지 않으면** `uvicorn server.main:app` 으로
띄운 서버는 `/session` 이 503, `/health` 가 `unconnected` 로만 응답한다. 실제로 그
상태였다. 이 파일이 그 빈자리를 채운다.

조립 순서와 규칙을 서버 코드 곳곳에 흩지 않고 여기 한 곳에 둔다. 흩어지면 "세션 저장소는
앱과 오케스트레이터가 같은 것을 써야 한다" 같은 규칙이 숨고, 어겨도 오류 없이 동작만
틀어진다.

## 실패를 다루는 방식

**정책 데이터가 없으면 미연결로 둔다.** `rules.loader.load_policies` 는 파일이 없어도
예외를 올리지 않고 사유를 돌려준다. 서버가 떠서 `/health` 로 상태를 알려야 하기 때문이다
(`docs/03-api-contract.md` 1-1). 그 설계를 살려, 읽은 정책이 0건이면 `policy_catalog` 과
`rule_engine` 을 비워 `/health` 가 `unconnected` 라고 말하게 한다. 0건을 `connected` 로
보고하면 "붙었는데 결과가 없다"로 읽혀 원인을 데이터가 아니라 판정에서 찾게 된다.

**모델 설정이 없어도 채팅은 연결한다.** 키가 없으면 AI 만 건너뛰고 규칙 기반 카드는
그대로 나간다. 여기서 `chat_service` 를 비우면 `/chat` 이 503 이 되어 카드까지 사라진다.
무엇이 없는지는 `Dependencies.missing_llm_settings` 가 이름으로 알려준다.

**CORS 설정이 잘못되면 뜨지 않는다.** `Settings.from_env` 의 `ConfigurationError` 는
잡지 않는다. 허용 출처는 보안 설정이라, 조용히 기본값으로 넘어가면 안 된다.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path

from fastapi import FastAPI

from rules import repository
from rules.deadline import today_kst
from server.config import Settings
from server.exception_judge import AiBExceptionJudgeAdapter
from server.llm_client import GatewayLLMClient, missing_settings
from server.orchestrator import BackendBOrchestrator
from server.policy_repository import PolicyRepository
from server.rule_engine import RuleEngine
from server.session_store import SessionStore

_LOGGER = logging.getLogger(__name__)

#: 정책 데이터 경로. 저장소 루트 기준 상대 경로다.
DEFAULT_POLICIES_PATH = "data/policies/policies.json"
POLICIES_PATH_ENV = "POLICIES_PATH"

#: `LLM_ADAPTER` 가 비었지만 게이트웨이 설정이 다 있을 때 `/health` 가 보고할 이름.
#: 비밀이 아니다. 실제 모델 별칭(`LLM_MODEL`)이나 키는 여기 담지 않는다.
_GATEWAY_ADAPTER_NAME = "gateway"


@dataclass(frozen=True, slots=True)
class Dependencies:
    """조립 결과. 무엇이 왜 비었는지도 함께 들고 있다."""

    settings: Settings
    session_store: SessionStore
    policy_catalog: PolicyRepository | None
    rule_engine: RuleEngine | None
    chat_service: object | None
    policies_path: Path
    #: 정책 데이터 검수 필요 기록. 비어 있지 않으면 그만큼 정책이 빠졌다.
    data_issues: list[str] = field(default_factory=list)
    #: 모델을 부르려면 아직 없는 설정 이름. 값은 담지 않는다.
    missing_llm_settings: tuple[str, ...] = ()

    @property
    def policies_connected(self) -> bool:
        return self.policy_catalog is not None


def resolve_policies_path(
    explicit: str | Path | None = None,
    env: Mapping[str, str] | None = None,
) -> Path:
    """정책 파일 경로. 환경변수로 바꿀 수 있다.

    `Settings` 에 넣지 않은 이유: `Settings` 는 값 검증 규칙이 붙은 고정 dataclass 이고
    지금 필요한 건 경로 하나다. 조립에만 쓰이는 값이라 조립하는 곳에서 읽는다.
    """
    if explicit is not None:
        return Path(explicit)
    source = os.environ if env is None else env
    raw = (source.get(POLICIES_PATH_ENV) or "").strip()
    return Path(raw) if raw else Path(DEFAULT_POLICIES_PATH)


def build_dependencies(
    *,
    settings: Settings | None = None,
    env: Mapping[str, str] | None = None,
    policies_path: str | Path | None = None,
    clock: Callable[[], date] = today_kst,
) -> Dependencies:
    """환경을 읽어 실제 의존성을 만든다."""
    resolved_settings = settings or Settings.from_env(env)
    path = resolve_policies_path(policies_path, env)

    store, rule_engine = repository.build(path, clock=clock)
    session_store = SessionStore(ttl_seconds=resolved_settings.session_ttl_seconds)
    gaps = missing_settings(env)

    if not gaps and resolved_settings.llm_adapter_name is None:
        # `/health` 의 `llm_adapter_configured` 가 `LLM_ADAPTER` 만 본다. 그 이름은
        # `.env.example` 에 남은 옛 설정이고, 실제로 모델을 부를 수 있게 하는 값은
        # `API_KEY` 와 `LLM_MODEL` 이다 (`ai/gateway.py`). 그래서 키를 제대로 넣고
        # 모델이 답을 만들고 있는데도 `/health` 는 false 라고 답했다. 운영자가 "AI 가 왜
        # 안 불리지" 를 볼 때 가장 먼저 보는 값이 반대로 말하면 원인을 엉뚱한 곳에서 찾는다.
        # 부를 수 있으면 부를 수 있다고 말하게 한다. 키 값은 담지 않는다 — 이름만 둔다.
        resolved_settings = replace(resolved_settings, llm_adapter_name=_GATEWAY_ADAPTER_NAME)

    if store.issues:
        # 한 건이 잘못돼도 나머지는 살린다. 몇 건이 빠졌는지는 보여야 한다.
        _LOGGER.warning(
            "정책 데이터 검수 필요 %d건: %s",
            len(store.issues),
            "; ".join(store.issues[:5]),
        )

    if not len(store):
        _LOGGER.error(
            "정책 데이터를 읽지 못해 미연결로 둡니다: %s (%s 로 경로를 지정할 수 있습니다)",
            path,
            POLICIES_PATH_ENV,
        )
        return Dependencies(
            settings=resolved_settings,
            session_store=session_store,
            policy_catalog=None,
            rule_engine=None,
            chat_service=None,
            policies_path=path,
            data_issues=list(store.issues),
            missing_llm_settings=gaps,
        )

    if gaps:
        _LOGGER.warning(
            "모델 설정이 없어 AI 를 건너뜁니다: 없는 설정=%s. 규칙 기반 카드는 그대로 나갑니다",
            ", ".join(gaps),
        )

    chat_service = BackendBOrchestrator(
        llm_client=GatewayLLMClient(env=env),
        rule_engine=rule_engine,
        policy_sources=store,
        # 답변과 같은 환경을 읽어야 한다. `env` 를 빼면 조립하는 쪽이 준 환경을 무시하고
        # 실제 `os.environ` 과 저장소 `.env` 를 읽는다 (`server/exception_judge.py`).
        exception_judge=AiBExceptionJudgeAdapter(env=env),
        # 앱과 같은 저장소를 써야 한다. 다른 것을 주면 대화 중 프로필 변경이
        # PATCH /session 결과와 어긋나고, 그 어긋남은 오류 없이 일어난다.
        session_store=session_store,
    )

    _LOGGER.info(
        "정책 %d건 연결 (검수 완료 %d건): %s",
        len(store),
        store.count_verified(),
        path,
    )

    return Dependencies(
        settings=resolved_settings,
        session_store=session_store,
        policy_catalog=store,
        rule_engine=rule_engine,
        chat_service=chat_service,
        policies_path=path,
        data_issues=list(store.issues),
        missing_llm_settings=gaps,
    )


def create_default_app(
    *,
    env: Mapping[str, str] | None = None,
    policies_path: str | Path | None = None,
) -> FastAPI:
    """`uvicorn server.main:app` 이 띄우는 앱.

    `create_app` 은 그대로 둔다. 명시적 의존성 주입은 테스트가 가짜를 꽂는 통로이므로
    유지하고, 환경에서 실제 값을 읽는 일만 이 함수가 한다.
    """
    from server.main import create_app

    dependencies = build_dependencies(env=env, policies_path=policies_path)
    return create_app(
        settings=dependencies.settings,
        policy_catalog=dependencies.policy_catalog,
        rule_engine=dependencies.rule_engine,
        session_store=dependencies.session_store,
        chat_service=dependencies.chat_service,
    )


__all__ = [
    "DEFAULT_POLICIES_PATH",
    "Dependencies",
    "POLICIES_PATH_ENV",
    "build_dependencies",
    "create_default_app",
    "resolve_policies_path",
]
