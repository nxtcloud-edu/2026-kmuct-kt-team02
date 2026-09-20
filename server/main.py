"""FastAPI application entry point."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from server.ai_gateway import AIGateway
from server.api import api_router
from server.chat_service import ChatService
from server.config import Settings
from server.errors import install_error_handlers
from server.policy_repository import PolicyRepository
from server.rule_engine import RuleEngine
from server.session_store import SessionStore


def create_app(
    *,
    settings: Settings | None = None,
    policy_catalog: PolicyRepository | None = None,
    rule_engine: RuleEngine | None = None,
    session_store: SessionStore | None = None,
    ai_gateway: AIGateway | None = None,
    chat_service: ChatService | None = None,
) -> FastAPI:
    """Build an application with explicit, replaceable integration dependencies."""

    resolved_settings = settings or Settings.from_env()
    resolved_session_store = (
        session_store
        if session_store is not None
        else SessionStore(ttl_seconds=resolved_settings.session_ttl_seconds)
    )
    application = FastAPI(
        title="SodaCookie API",
        version="0.1.0",
    )
    application.state.settings = resolved_settings
    application.state.policy_catalog = policy_catalog
    application.state.rule_engine = rule_engine
    application.state.session_store = resolved_session_store
    application.state.ai_gateway = ai_gateway
    application.state.chat_service = chat_service

    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved_settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
        allow_headers=["Accept", "Content-Type"],
        max_age=600,
    )
    install_error_handlers(application)
    application.include_router(api_router)
    return application


def _create_default_app() -> FastAPI:
    """환경에서 실제 의존성을 읽어 조립한다.

    지연 import 다. `server.bootstrap` 이 위의 `create_app` 을 쓰기 때문에, 최상단에서
    서로를 import 하면 순환이 된다.
    """
    from server.bootstrap import create_default_app

    return create_default_app()


#: `uvicorn server.main:app` 이 띄우는 앱.
#:
#: 여기가 오랫동안 `create_app()` 이었다. 의존성이 전부 None 으로 떠서 `/session` 은 503,
#: `/health` 는 unconnected 로만 응답했다. 실제 값을 꽂는 곳이 테스트뿐이었다.
#: 조립 규칙과 실패 처리는 `server/bootstrap.py` 에 있다.
app = _create_default_app()
