"""Operational health endpoint with no secret-bearing output."""

from __future__ import annotations

from fastapi import APIRouter, Request

from server.policy_repository import PolicyRepository
from server.schemas import HealthResponse, PolicyDependencyStatus

router = APIRouter(tags=["operations"])


@router.get("/health", response_model=HealthResponse)
def get_health(request: Request) -> HealthResponse:
    catalog: PolicyRepository | None = request.app.state.policy_catalog
    if catalog is None:
        verified_policy_count = None
        policy_dependency = PolicyDependencyStatus.UNCONNECTED
    else:
        verified_policy_count = catalog.count_verified()
        if isinstance(verified_policy_count, bool) or verified_policy_count < 0:
            raise ValueError("count_verified() must return a non-negative integer")
        policy_dependency = PolicyDependencyStatus.CONNECTED

    return HealthResponse(
        verified_policy_count=verified_policy_count,
        policy_dependency=policy_dependency,
        llm_adapter_configured=request.app.state.settings.llm_adapter_configured,
    )
