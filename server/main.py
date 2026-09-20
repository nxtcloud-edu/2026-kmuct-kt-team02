"""FastAPI application entry point."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from server.ai_gateway import AIGateway
from server.api import api_router
from server.api.health import PolicyCatalog
from server.chat_service import ChatService
from server.config import Settings
from server.errors import install_error_handlers
from server.rule_engine import RuleEngine
from server.session_store import SessionStore


def create_app(
    *,
    settings: Settings | None = None,
    policy_catalog: PolicyCatalog | None = None,
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


app = create_app()
