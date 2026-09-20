"""Process-local, in-memory session storage for the single-instance MVP."""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from threading import Lock
from uuid import UUID, uuid4

from pydantic import JsonValue

from server.schemas import FollowupField, PlannedBasis, Profile, ProfileField

Clock = Callable[[], datetime]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SessionStoreError(LookupError):
    """Base class for session lookup failures."""


class SessionNotFoundError(SessionStoreError):
    """Raised when a session ID is unknown."""


class SessionExpiredError(SessionNotFoundError):
    """Raised after an expired session has been removed."""


class DuplicateMessageError(SessionStoreError):
    """Raised when a client retries a turn with the same client message ID."""


@dataclass(frozen=True, slots=True)
class PendingProfileChange:
    """A valid future profile change kept outside the current profile."""

    field: ProfileField
    value: JsonValue


@dataclass(frozen=True, slots=True)
class TurnSummary:
    """PII-free conversational context; user-message text is never retained."""

    kind: str
    intent: str | None = None
    confirmed_values: tuple[tuple[str, JsonValue], ...] = ()
    policy_ids: tuple[str, ...] = ()


_MAX_RECENT_TURNS = 6
_MAX_POLICY_IDS = 5
_MAX_PROCESSED_MESSAGE_IDS = 32
_MAX_SHOWN_CHIP_IDS = 100


@dataclass(frozen=True, slots=True)
class Session:
    """The complete process-local state; raw user messages are never stored."""

    session_id: UUID
    profile: Profile
    created_at: datetime
    last_accessed_at: datetime
    expires_at: datetime
    asked_fields: frozenset[str] = field(default_factory=frozenset)
    skipped_fields: frozenset[str] = field(default_factory=frozenset)
    shown_chip_ids: frozenset[str] = field(default_factory=frozenset)
    planned_basis: PlannedBasis | None = None
    pending_planned_changes: tuple[PendingProfileChange, ...] = ()
    recent_turns: tuple[TurnSummary, ...] = ()
    current_policy_ids: tuple[str, ...] = ()
    processed_message_ids: tuple[str, ...] = ()


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
        return replace(
            session,
            profile=session.profile.model_copy(deep=True),
            pending_planned_changes=tuple(
                PendingProfileChange(change.field, deepcopy(change.value))
                for change in session.pending_planned_changes
            ),
            recent_turns=tuple(
                TurnSummary(
                    summary.kind,
                    summary.intent,
                    tuple((name, deepcopy(value)) for name, value in summary.confirmed_values),
                    tuple(summary.policy_ids),
                )
                for summary in session.recent_turns
            ),
        )

    def _active_locked(self, parsed_id: UUID, now: datetime) -> Session:
        session = self._sessions.get(parsed_id)
        if session is None:
            raise SessionNotFoundError("session not found")
        if session.expires_at <= now:
            del self._sessions[parsed_id]
            raise SessionExpiredError("session expired")
        return session

    def _store_refreshed_locked(self, session: Session, now: datetime) -> Session:
        refreshed = replace(
            session,
            last_accessed_at=now,
            expires_at=now + self._ttl,
        )
        self._sessions[session.session_id] = refreshed
        return refreshed

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
            session = self._active_locked(parsed_id, now)
            refreshed = self._store_refreshed_locked(session, now)
            return self._copy(refreshed)

    def update_profile(
        self,
        session_id: UUID | str,
        profile: Profile,
        *,
        clear_pending_fields: set[ProfileField] | frozenset[ProfileField] = frozenset(),
    ) -> Session:
        """Replace only the validated profile and refresh the sliding TTL."""

        if not isinstance(profile, Profile):
            raise TypeError("profile must be a validated Profile")
        parsed_id = self._parse_session_id(session_id)
        now = self._now()
        with self._lock:
            session = self._active_locked(parsed_id, now)
            pending = tuple(
                change
                for change in session.pending_planned_changes
                if change.field not in clear_pending_fields
            )
            updated = self._store_refreshed_locked(
                replace(
                session,
                profile=profile.model_copy(deep=True),
                pending_planned_changes=pending,
                ),
                now,
            )
            return self._copy(updated)

    def begin_turn(self, session_id: UUID | str, client_message_id: str) -> Session:
        """Atomically reserve a client turn ID before an SSE response opens."""

        if not client_message_id.strip():
            raise ValueError("client_message_id must not be blank")
        parsed_id = self._parse_session_id(session_id)
        now = self._now()
        with self._lock:
            session = self._active_locked(parsed_id, now)
            if client_message_id in session.processed_message_ids:
                raise DuplicateMessageError("duplicate client message")
            ids = (*session.processed_message_ids, client_message_id)[-_MAX_PROCESSED_MESSAGE_IDS:]
            updated = self._store_refreshed_locked(
                replace(session, processed_message_ids=ids), now
            )
            return self._copy(updated)

    def set_pending_planned_changes(
        self,
        session_id: UUID | str,
        changes: tuple[PendingProfileChange, ...],
    ) -> Session:
        """Replace future-only changes after a message interpretation."""

        parsed_id = self._parse_session_id(session_id)
        now = self._now()
        unique: dict[ProfileField, PendingProfileChange] = {
            change.field: PendingProfileChange(change.field, deepcopy(change.value))
            for change in changes
        }
        with self._lock:
            session = self._active_locked(parsed_id, now)
            updated = self._store_refreshed_locked(
                replace(
                    session,
                    pending_planned_changes=tuple(unique.values()),
                    planned_basis=None if unique else session.planned_basis,
                ),
                now,
            )
            return self._copy(updated)

    def record_followup(
        self,
        session_id: UUID | str,
        field: FollowupField,
        *,
        skipped: bool = False,
        planned_basis: PlannedBasis | None = None,
    ) -> Session:
        """Record a sent, answered, or skipped follow-up without client mutation."""

        parsed_id = self._parse_session_id(session_id)
        now = self._now()
        name = str(field)
        with self._lock:
            session = self._active_locked(parsed_id, now)
            asked = session.asked_fields | {name}
            skipped_fields = session.skipped_fields | ({name} if skipped else set())
            pending = session.pending_planned_changes
            basis = session.planned_basis
            if name == "planned_basis" and (skipped or planned_basis is not None):
                basis = planned_basis or PlannedBasis.CURRENT
                if basis == PlannedBasis.CURRENT:
                    pending = ()
            updated = self._store_refreshed_locked(
                replace(
                    session,
                    asked_fields=frozenset(asked),
                    skipped_fields=frozenset(skipped_fields),
                    planned_basis=basis,
                    pending_planned_changes=pending,
                ),
                now,
            )
            return self._copy(updated)

    def apply_followup_answer(
        self,
        session_id: UUID | str,
        *,
        field: FollowupField,
        profile: Profile | None = None,
        skipped: bool = False,
        planned_basis: PlannedBasis | None = None,
        confirmed_value: JsonValue | None = None,
    ) -> Session:
        """Atomically persist a validated direct answer and its conversation state."""

        parsed_id = self._parse_session_id(session_id)
        now = self._now()
        name = str(field)
        with self._lock:
            session = self._active_locked(parsed_id, now)
            asked = session.asked_fields | {name}
            skipped_fields = session.skipped_fields | ({name} if skipped else set())
            pending = session.pending_planned_changes
            basis = session.planned_basis
            next_profile = session.profile if profile is None else profile.model_copy(deep=True)

            if isinstance(field, ProfileField):
                pending = tuple(change for change in pending if change.field != field)
            if name == "planned_basis":
                basis = planned_basis or PlannedBasis.CURRENT
                if basis == PlannedBasis.CURRENT:
                    pending = ()

            summary = TurnSummary(
                kind="followup_skip" if skipped else "followup_answer",
                confirmed_values=()
                if skipped or confirmed_value is None
                else ((name, deepcopy(confirmed_value)),),
            )
            updated = self._store_refreshed_locked(
                replace(
                    session,
                    profile=next_profile,
                    asked_fields=frozenset(asked),
                    skipped_fields=frozenset(skipped_fields),
                    planned_basis=basis,
                    pending_planned_changes=pending,
                    recent_turns=(*session.recent_turns, summary)[-_MAX_RECENT_TURNS:],
                ),
                now,
            )
            return self._copy(updated)

    def record_shown_chips(
        self, session_id: UUID | str, chip_ids: tuple[str, ...]
    ) -> Session:
        parsed_id = self._parse_session_id(session_id)
        now = self._now()
        cleaned = tuple(chip_id for chip_id in chip_ids if chip_id.strip())
        with self._lock:
            session = self._active_locked(parsed_id, now)
            ids = tuple((*session.shown_chip_ids, *cleaned)[-_MAX_SHOWN_CHIP_IDS:])
            updated = self._store_refreshed_locked(
                replace(session, shown_chip_ids=frozenset(ids)), now
            )
            return self._copy(updated)

    def record_policies(
        self, session_id: UUID | str, policy_ids: tuple[str, ...]
    ) -> Session:
        parsed_id = self._parse_session_id(session_id)
        now = self._now()
        ids = tuple(policy_id for policy_id in policy_ids if policy_id.strip())[:_MAX_POLICY_IDS]
        with self._lock:
            session = self._active_locked(parsed_id, now)
            # Associate the policies delivered for this request with its
            # already-recorded PII-free turn summary.  The transport owns the
            # policy payload, so this is the only point where that association
            # can be made without retaining a raw message.
            recent_turns = session.recent_turns
            if recent_turns:
                last = recent_turns[-1]
                recent_turns = (
                    *recent_turns[:-1],
                    TurnSummary(
                        kind=last.kind,
                        intent=last.intent,
                        confirmed_values=last.confirmed_values,
                        policy_ids=ids,
                    ),
                )
            updated = self._store_refreshed_locked(
                replace(
                    session,
                    current_policy_ids=ids,
                    recent_turns=recent_turns,
                ),
                now,
            )
            return self._copy(updated)

    def record_turn_summary(self, session_id: UUID | str, summary: TurnSummary) -> Session:
        parsed_id = self._parse_session_id(session_id)
        now = self._now()
        safe = TurnSummary(
            kind=summary.kind,
            intent=summary.intent,
            confirmed_values=tuple((name, deepcopy(value)) for name, value in summary.confirmed_values),
            policy_ids=tuple(summary.policy_ids[:_MAX_POLICY_IDS]),
        )
        with self._lock:
            session = self._active_locked(parsed_id, now)
            updated = self._store_refreshed_locked(
                replace(session, recent_turns=(*session.recent_turns, safe)[-_MAX_RECENT_TURNS:]),
                now,
            )
            return self._copy(updated)

    @staticmethod
    def effective_profile(session: Session) -> Profile:
        """Overlay a selected future basis for evaluation without persisting it."""

        if session.planned_basis != PlannedBasis.PLANNED:
            return session.profile.model_copy(deep=True)
        values = session.profile.model_dump(mode="json")
        for change in session.pending_planned_changes:
            values[change.field.value] = deepcopy(change.value)
        return Profile.model_validate(values)

    def delete(self, session_id: UUID | str) -> bool:
        parsed_id = self._parse_session_id(session_id)
        with self._lock:
            return self._sessions.pop(parsed_id, None) is not None

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)
