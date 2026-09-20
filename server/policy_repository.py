"""Backend A policy repository ports; no policy data is duplicated here."""

from __future__ import annotations

from typing import Protocol

from server.schemas import Policy


class PolicySourceCatalog(Protocol):
    """Read-only source policy lookup needed by detail and citation flows."""

    def get(self, policy_id: str) -> Policy | None:
        ...


class PolicyRepository(PolicySourceCatalog, Protocol):
    """Full app-level Backend A repository boundary."""

    def count_verified(self) -> int:
        """Return the number of manually verified policies."""
        ...
