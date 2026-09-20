from dataclasses import fields
from datetime import datetime, timedelta, timezone

import pytest

from server.config import ConfigurationError, Settings
from server.schemas import Profile
from server.session_store import Session, SessionExpiredError, SessionStore


class MutableClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current

    def advance(self, *, seconds: int) -> None:
        self.current += timedelta(seconds=seconds)


def profile() -> Profile:
    return Profile.model_validate(
        {
            "age": 22,
            "district": "마포구",
            "status": "enrolled",
            "categories": ["scholarship"],
        }
    )


def test_session_stores_only_contract_fields_and_uses_sliding_ttl() -> None:
    clock = MutableClock(datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc))
    store = SessionStore(ttl_seconds=60, clock=clock)

    created = store.create(profile())

    assert {field.name for field in fields(Session)} == {
        "session_id",
        "profile",
        "created_at",
        "last_accessed_at",
        "expires_at",
        "asked_fields",
        "skipped_fields",
        "shown_chip_ids",
        "planned_basis",
        "pending_planned_changes",
        "recent_turns",
        "current_policy_ids",
        "processed_message_ids",
    }
    assert created.session_id.version == 4
    assert created.created_at == clock.current
    assert created.last_accessed_at == clock.current
    assert created.expires_at == clock.current + timedelta(seconds=60)

    clock.advance(seconds=30)
    refreshed = store.get(created.session_id)

    assert refreshed.created_at == created.created_at
    assert refreshed.last_accessed_at == clock.current
    assert refreshed.expires_at == clock.current + timedelta(seconds=60)


def test_expired_session_is_removed_when_accessed() -> None:
    clock = MutableClock(datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc))
    store = SessionStore(ttl_seconds=60, clock=clock)
    created = store.create(profile())
    clock.advance(seconds=60)

    with pytest.raises(SessionExpiredError):
        store.get(created.session_id)

    assert len(store) == 0


def test_session_ttl_is_loaded_from_environment() -> None:
    settings = Settings.from_env(
        {
            "CORS_LOCALHOST_ORIGINS": "",
            "CORS_S3_ORIGINS": "",
            "SESSION_TTL_SECONDS": "45",
        }
    )

    assert settings.session_ttl_seconds == 45


def test_chat_safety_settings_have_safe_defaults_and_reject_invalid_values() -> None:
    settings = Settings.from_env(
        {
            "CORS_LOCALHOST_ORIGINS": "",
            "CORS_S3_ORIGINS": "",
            "CHAT_TOTAL_TIMEOUT_SECONDS": "20",
            "CHAT_RATE_LIMIT_PER_MINUTE": "10",
            "DEMO_MODE": "true",
            "DEMO_CACHE_TTL_SECONDS": "300",
        }
    )

    assert settings.chat_total_timeout_seconds == 20
    assert settings.chat_rate_limit_per_minute == 10
    assert settings.demo_mode is True
    assert settings.demo_cache_ttl_seconds == 300

    with pytest.raises(ConfigurationError):
        Settings.from_env(
            {
                "CORS_LOCALHOST_ORIGINS": "",
                "CORS_S3_ORIGINS": "",
                "CHAT_RATE_LIMIT_PER_MINUTE": "0",
            }
        )
