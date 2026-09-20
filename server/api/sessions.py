"""Session creation and profile update API."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import ValidationError

from server.errors import AppError, ErrorCode, ErrorResponse
from server.rule_engine import RuleEngine, RuleEngineUnavailableError
from server.schemas import (
    IncomeBracket,
    Profile,
    ProfileInput,
    ProfilePatch,
    SessionCreateResponse,
)
from server.session_store import SessionNotFoundError, SessionStore

router = APIRouter(prefix="/session", tags=["sessions"])
_RULE_ENGINE_UNAVAILABLE_MESSAGE = "규칙 엔진 의존성이 연결되지 않았습니다"


@router.post(
    "",
    response_model=SessionCreateResponse,
    responses={422: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
)
def create_session(request: Request, profile_input: ProfileInput) -> SessionCreateResponse:
    """Validate a profile, persist a session, and return rule-engine results."""

    rule_engine: RuleEngine | None = request.app.state.rule_engine
    if rule_engine is None:
        raise AppError(
            code=ErrorCode.SERVER_ERROR,
            status_code=503,
            message=_RULE_ENGINE_UNAVAILABLE_MESSAGE,
        )

    profile = Profile.from_input(profile_input)
    session_store: SessionStore = request.app.state.session_store
    session = session_store.create(profile)

    try:
        result = rule_engine.evaluate(profile, limit=5)
        response = SessionCreateResponse(
            session_id=str(session.session_id),
            profile=session.profile,
            policies=result.policies,
            hidden_unlikely_count=result.hidden_unlikely_count,
            followup=None,
        )
    except RuleEngineUnavailableError as exc:
        session_store.delete(session.session_id)
        raise AppError(
            code=ErrorCode.SERVER_ERROR,
            status_code=503,
            message=_RULE_ENGINE_UNAVAILABLE_MESSAGE,
        ) from exc
    except Exception:
        session_store.delete(session.session_id)
        raise

    return response


@router.patch(
    "/{session_id}/profile",
    response_model=SessionCreateResponse,
    responses={
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def update_session_profile(
    request: Request,
    session_id: str,
    patch: ProfilePatch,
) -> SessionCreateResponse:
    """Apply only explicit fields, revalidate, recalculate, then persist."""

    session_store: SessionStore = request.app.state.session_store
    try:
        session = session_store.get(session_id)
    except SessionNotFoundError as exc:
        raise AppError(code=ErrorCode.SESSION_EXPIRED, status_code=404) from exc

    updates = patch.model_dump(mode="json", exclude_unset=True)
    if "income_bracket" in updates and updates["income_bracket"] is None:
        updates["income_bracket"] = IncomeBracket.UNKNOWN.value

    merged = session.profile.model_dump(mode="json")
    merged.update(updates)
    try:
        updated_profile = Profile.model_validate(merged)
    except ValidationError as exc:
        raise AppError(code=ErrorCode.INVALID_INPUT, status_code=422) from exc

    rule_engine: RuleEngine | None = request.app.state.rule_engine
    if rule_engine is None:
        raise AppError(
            code=ErrorCode.SERVER_ERROR,
            status_code=503,
            message=_RULE_ENGINE_UNAVAILABLE_MESSAGE,
        )

    try:
        result = rule_engine.evaluate(updated_profile, limit=5)
    except RuleEngineUnavailableError as exc:
        raise AppError(
            code=ErrorCode.SERVER_ERROR,
            status_code=503,
            message=_RULE_ENGINE_UNAVAILABLE_MESSAGE,
        ) from exc

    try:
        stored = session_store.update_profile(session_id, updated_profile)
    except SessionNotFoundError as exc:
        raise AppError(code=ErrorCode.SESSION_EXPIRED, status_code=404) from exc

    return SessionCreateResponse(
        session_id=str(stored.session_id),
        profile=stored.profile,
        policies=result.policies,
        hidden_unlikely_count=result.hidden_unlikely_count,
        followup=None,
    )