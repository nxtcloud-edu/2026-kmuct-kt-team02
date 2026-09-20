"""Environment-backed server settings.

Only non-secret configuration is represented here. Credential values must stay in
runtime environment variables and are never included in health responses.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

_DEFAULT_LOCALHOST_ORIGINS = ",".join(
    (
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    )
)
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


class ConfigurationError(ValueError):
    """Raised when an environment setting would make the server unsafe."""


def _normalize_origin(value: str, variable_name: str) -> str:
    origin = value.strip().rstrip("/")
    if not origin:
        raise ConfigurationError(f"{variable_name} contains an empty origin")
    if "*" in origin:
        raise ConfigurationError(f"{variable_name} must not contain wildcard origins")

    try:
        parsed = urlsplit(origin)
        _ = parsed.port
    except ValueError as exc:
        raise ConfigurationError(f"{variable_name} contains an invalid origin") from exc

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ConfigurationError(
            f"{variable_name} origins must include an http or https scheme"
        )
    if parsed.username or parsed.password:
        raise ConfigurationError(f"{variable_name} origins must not contain credentials")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ConfigurationError(
            f"{variable_name} must contain exact origins without paths, queries, or fragments"
        )
    return origin


def _parse_origins(raw_value: str, variable_name: str) -> tuple[str, ...]:
    if not raw_value.strip():
        return ()

    origins = (
        _normalize_origin(value, variable_name) for value in raw_value.split(",")
    )
    return tuple(dict.fromkeys(origins))


def _validate_local_origins(origins: tuple[str, ...]) -> tuple[str, ...]:
    for origin in origins:
        hostname = urlsplit(origin).hostname
        if hostname not in _LOCAL_HOSTS:
            raise ConfigurationError(
                "CORS_LOCALHOST_ORIGINS may contain only localhost or loopback hosts"
            )
    return origins


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated server settings loaded from environment variables."""

    localhost_cors_origins: tuple[str, ...]
    s3_cors_origins: tuple[str, ...]
    llm_adapter_name: str | None
    session_ttl_seconds: int = 1800
    chat_total_timeout_seconds: int = 20
    chat_rate_limit_per_minute: int = 10
    demo_mode: bool = False
    demo_cache_ttl_seconds: int = 300

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "Settings":
        source = os.environ if environ is None else environ
        localhost_origins = _parse_origins(
            source.get("CORS_LOCALHOST_ORIGINS", _DEFAULT_LOCALHOST_ORIGINS),
            "CORS_LOCALHOST_ORIGINS",
        )
        s3_origins = _parse_origins(
            source.get("CORS_S3_ORIGINS", ""),
            "CORS_S3_ORIGINS",
        )
        adapter_name = source.get("LLM_ADAPTER", "").strip() or None
        raw_ttl = source.get("SESSION_TTL_SECONDS", "1800").strip()
        try:
            session_ttl_seconds = int(raw_ttl)
        except ValueError as exc:
            raise ConfigurationError(
                "SESSION_TTL_SECONDS must be a positive integer"
            ) from exc
        if session_ttl_seconds <= 0:
            raise ConfigurationError("SESSION_TTL_SECONDS must be a positive integer")

        def positive_int(variable_name: str, default: str) -> int:
            raw = source.get(variable_name, default).strip()
            try:
                value = int(raw)
            except ValueError as exc:
                raise ConfigurationError(f"{variable_name} must be a positive integer") from exc
            if value <= 0:
                raise ConfigurationError(f"{variable_name} must be a positive integer")
            return value

        demo_raw = source.get("DEMO_MODE", "false").strip().lower()
        if demo_raw not in {"true", "false"}:
            raise ConfigurationError("DEMO_MODE must be true or false")

        return cls(
            localhost_cors_origins=_validate_local_origins(localhost_origins),
            s3_cors_origins=s3_origins,
            llm_adapter_name=adapter_name,
            session_ttl_seconds=session_ttl_seconds,
            chat_total_timeout_seconds=positive_int("CHAT_TOTAL_TIMEOUT_SECONDS", "20"),
            chat_rate_limit_per_minute=positive_int("CHAT_RATE_LIMIT_PER_MINUTE", "10"),
            demo_mode=demo_raw == "true",
            demo_cache_ttl_seconds=positive_int("DEMO_CACHE_TTL_SECONDS", "300"),
        )

    @property
    def cors_origins(self) -> tuple[str, ...]:
        """Return a de-duplicated exact-origin allowlist."""

        return tuple(
            dict.fromkeys((*self.localhost_cors_origins, *self.s3_cors_origins))
        )

    @property
    def llm_adapter_configured(self) -> bool:
        return self.llm_adapter_name is not None
