"""Explicit boundary for backend A's rule engine.

The server owns only this protocol and its result shape. Eligibility rules,
deadline calculation, filtering, and ordering must be implemented by backend A.
"""

from __future__ import annotations

from typing import Annotated, Protocol

from pydantic import Field, field_validator

from server.schemas import (
    ContractModel,
    Policy,
    PolicyDataStatus,
    PolicyEvaluation,
    Profile,
)


class RuleEngineUnavailableError(RuntimeError):
    """Raised by an adapter when backend A's engine cannot be called."""


class RuleEngineResult(ContractModel):
    """Already-filtered and ordered recommendations returned by backend A."""

    policies: Annotated[list[PolicyEvaluation], Field(max_length=5)]
    hidden_unlikely_count: Annotated[int, Field(strict=True, ge=0)] = 0

    @field_validator("policies")
    @classmethod
    def reject_closed_policies(
        cls, policies: list[PolicyEvaluation]
    ) -> list[PolicyEvaluation]:
        if any(policy.data_status == PolicyDataStatus.CLOSED for policy in policies):
            raise ValueError("rule engine result must not include closed policies")
        return policies


class RuleEngine(Protocol):
    """Minimal synchronous adapter implemented by backend A or a test fake."""

    def evaluate(self, profile: Profile, *, limit: int = 5) -> RuleEngineResult:
        """Return up to ``limit`` pre-filtered, ordered recommendations."""
        ...

    def evaluate_policy(self, profile: Profile, policy: Policy) -> PolicyEvaluation:
        """Evaluate one repository policy without recommendation-list filtering."""
        ...
