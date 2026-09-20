"""Process-local, in-memory session storage for the single-instance MVP."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from threading import Lock
from uuid import UUID, uuid4

from server.schemas import Profile

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SessionStoreError(LookupError):
    """Base class for session lookup failures."""


class SessionNotFoundError(SessionStoreError):
    """Raised when a session ID is unknown."""


class SessionExpiredError(SessionNotFoundError):
    """Raised after an expired session has been removed."""


@dataclass(frozen=True, slots=True)
class Session:
    """The complete persisted session shape; no messages or PII are stored."""

    session_id: UUID
    profile: Profile
    created_at: datetime
    last_accessed_at: datetime
    expires_at: datetime


class SessionStore:
    """Thread-safe sliding-TTL store for one application process.

    There is intentionally no background cleanup task. An expired record is
    removed when its ID is accessed, matching the single-process MVP contract.
    """

    def __init__(self, *, ttl_seconds: int, clock: Clock = _utc_now) -> None:
        if isinstance(ttl_seconds, bool) or ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be a positive integer")
        self._ttl = timedelta(seconds=ttl_seconds)
        self._clock = clock
        self._sessions: dict[UUID, Session] = {}
        self._lock = Lock()

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("session clock must return a timezone-aware datetime")
        return now.astimezone(timezone.utc)

    @staticmethod
    def _parse_session_id(session_id: UUID | str) -> UUID:
        if isinstance(session_id, UUID):
            return session_id
        try:
            return UUID(session_id)
        except (TypeError, ValueError, AttributeError) as exc:
            raise SessionNotFoundError("session not found") from exc

    @staticmethod
    def _copy(session: Session) -> Session:
        return replace(session, profile=session.profile.model_copy(deep=True))

    def create(self, profile: Profile) -> Session:
        if not isinstance(profile, Profile):
            raise TypeError("profile must be a validated Profile")

        now = self._now()
        session = Session(
            session_id=uuid4(),
            profile=profile.model_copy(deep=True),
            created_at=now,
            last_accessed_at=now,
            expires_at=now + self._ttl,
        )
        with self._lock:
            self._sessions[session.session_id] = session
        return self._copy(session)

    def get(self, session_id: UUID | str) -> Session:
        """Return a session and extend its expiry, or remove it if expired."""

        parsed_id = self._parse_session_id(session_id)
        now = self._now()
        with self._lock:
            session = self._sessions.get(parsed_id)
            if session is None:
                raise SessionNotFoundError("session not found")
            if session.expires_at <= now:
                del self._sessions[parsed_id]
                raise SessionExpiredError("session expired")

            refreshed = replace(
                session,
                last_accessed_at=now,
                expires_at=now + self._ttl,
            )
            self._sessions[parsed_id] = refreshed
            return self._copy(refreshed)

    def update_profile(self, session_id: UUID | str, profile: Profile) -> Session:
        """Replace only the validated profile and refresh the sliding TTL."""

        if not isinstance(profile, Profile):
            raise TypeError("profile must be a validated Profile")
        parsed_id = self._parse_session_id(session_id)
        now = self._now()
        with self._lock:
            session = self._sessions.get(parsed_id)
            if session is None:
                raise SessionNotFoundError("session not found")
            if session.expires_at <= now:
                del self._sessions[parsed_id]
                raise SessionExpiredError("session expired")
            updated = replace(
                session,
                profile=profile.model_copy(deep=True),
                last_accessed_at=now,
                expires_at=now + self._ttl,
            )
            self._sessions[parsed_id] = updated
            return self._copy(updated)

    def delete(self, session_id: UUID | str) -> bool:
        parsed_id = self._parse_session_id(session_id)
        with self._lock:
            return self._sessions.pop(parsed_id, None) is not None

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)
