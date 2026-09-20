"""Session-aware policy detail endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from server.errors import AppError, ErrorCode, ErrorResponse
from server.policy_repository import PolicyRepository
from server.rule_engine import RuleEngine, RuleEngineUnavailableError
from server.schemas import PolicyEvaluation
from server.session_store import SessionNotFoundError, SessionStore

router = APIRouter(prefix="/policies", tags=["policies"])
_BACKEND_A_UNAVAILABLE_MESSAGE = "정책 저장소 또는 규칙 엔진 의존성이 연결되지 않았습니다"


@router.get(
    "/{policy_id}",
    response_model=PolicyEvaluation,
    responses={
        404: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        503: {"model": ErrorResponse},
    },
)
def get_policy_detail(
    request: Request,
    policy_id: str,
    session_id: str = Query(min_length=1),
) -> PolicyEvaluation:
    """Return Backend A's single-policy evaluation for the session profile."""

    session_store: SessionStore = request.app.state.session_store
    try:
        session = session_store.get(session_id)
    except SessionNotFoundError as exc:
        raise AppError(code=ErrorCode.SESSION_EXPIRED, status_code=404) from exc

    repository: PolicyRepository | None = request.app.state.policy_catalog
    rule_engine: RuleEngine | None = request.app.state.rule_engine
    if repository is None or rule_engine is None:
        raise AppError(
            code=ErrorCode.SERVER_ERROR,
            status_code=503,
            message=_BACKEND_A_UNAVAILABLE_MESSAGE,
        )

    policy = repository.get(policy_id)
    if policy is None or policy.checked_at is None or not policy.source_url:
        raise AppError(code=ErrorCode.POLICY_NOT_FOUND, status_code=404)

    try:
        evaluation = rule_engine.evaluate_policy(session.profile, policy)
    except RuleEngineUnavailableError as exc:
        raise AppError(
            code=ErrorCode.SERVER_ERROR,
            status_code=503,
            message=_BACKEND_A_UNAVAILABLE_MESSAGE,
        ) from exc

    if evaluation.policy_id != policy.id:
        raise ValueError("rule engine returned a different policy_id")
    return evaluation
