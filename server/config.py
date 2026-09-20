"""Environment-backed server settings.

Only non-secret configuration is represented here. Credential values must stay in
runtime environment variables and are never included in health responses.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

#: 한 턴의 계약 상한 (`docs/03-api-contract.md` 1장 표와 9장 13단계). 이 값을 넘기면
#: 그때까지의 결과로 `done` 을 보내야 한다.
#:
#: 여기 두는 이유: 단계별 배분은 `server/orchestrator.py` 의 `OrchestratorTimeouts` 가
#: 하지만 **상한 자체는 설정값**이라 환경에서 줄일 수 있어야 한다(느린 회선 시연 등).
#: 오케스트레이터가 이 모듈을 읽는 방향은 안전하다. `config` 는 잎 모듈이다.
TURN_BUDGET_CEILING_S = 20.0

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


def _parse_turn_budget(raw_value: str | None) -> float:
    """한 턴 예산. 비었으면 계약 상한을 쓴다.

    상한보다 **크게** 주는 것은 막는다. 계약이 20초이므로 그보다 긴 값을 허용하면
    설정 한 줄로 계약을 깰 수 있다. 줄이는 것은 허용한다 — 느린 환경에서 폴백을
    빨리 보여 주는 쪽이 사용자에게 낫다.
    """
    text = (raw_value or "").strip()
    if not text:
        return TURN_BUDGET_CEILING_S
    try:
        budget = float(text)
    except ValueError as exc:
        raise ConfigurationError(
            "TURN_BUDGET_SECONDS must be a positive number of seconds"
        ) from exc
    if budget <= 0:
        raise ConfigurationError(
            "TURN_BUDGET_SECONDS must be a positive number of seconds"
        )
    if budget > TURN_BUDGET_CEILING_S:
        raise ConfigurationError(
            f"TURN_BUDGET_SECONDS must not exceed the contract ceiling "
            f"of {TURN_BUDGET_CEILING_S:g}s"
        )
    return budget


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated server settings loaded from environment variables."""

    localhost_cors_origins: tuple[str, ...]
    s3_cors_origins: tuple[str, ...]
    llm_adapter_name: str | None
    session_ttl_seconds: int = 1800
    #: 한 턴 전체 예산. 계약 상한보다 크게 줄 수 없다.
    turn_budget_seconds: float = TURN_BUDGET_CEILING_S

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

        return cls(
            localhost_cors_origins=_validate_local_origins(localhost_origins),
            s3_cors_origins=s3_origins,
            llm_adapter_name=adapter_name,
            session_ttl_seconds=session_ttl_seconds,
            turn_budget_seconds=_parse_turn_budget(source.get("TURN_BUDGET_SECONDS")),
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
