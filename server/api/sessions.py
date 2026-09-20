"""Session creation API."""

from __future__ import annotations

from fastapi import APIRouter, Request

from server.errors import AppError, ErrorCode, ErrorResponse
from server.rule_engine import RuleEngine, RuleEngineUnavailableError
from server.schemas import Profile, ProfileInput, SessionCreateResponse
from server.session_store import SessionStore

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
