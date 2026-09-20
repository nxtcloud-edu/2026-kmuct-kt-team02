"""Opt-in in-memory replay cache for local demo streams."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass
from threading import Lock

from server.chat_service import ChatPipelineRequest, ChatServiceEvent
from server.schemas import Profile


Clock = Callable[[], float]


@dataclass(frozen=True, slots=True)
class _Entry:
    expires_at: float
    events: tuple[ChatServiceEvent, ...]


class DemoEventCache:
    """Caches only already-safe semantic events; it never stores a message string as a key."""

    def __init__(self, *, ttl_seconds: int, clock: Clock = time.monotonic) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._ttl_seconds = ttl_seconds
        self._clock = clock
        self._entries: dict[str, _Entry] = {}
        self._lock = Lock()

    @staticmethod
    def key(profile: Profile, request: ChatPipelineRequest) -> str:
        """Hash normalized, PII-masked input without retaining its plaintext form."""

        material = {
            "profile": profile.model_dump(mode="json"),
            "turn_type": request.turn_type,
            "message": request.masked_message,
            "followup_field": request.followup_field,
            "followup_value": request.followup_value,
            "followup_skipped": request.followup_skipped,
        }
        encoded = json.dumps(material, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def get(self, key: str) -> tuple[ChatServiceEvent, ...] | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if entry.expires_at <= self._clock():
                self._entries.pop(key, None)
                return None
            return tuple(deepcopy(entry.events))

    def put(self, key: str, events: tuple[ChatServiceEvent, ...]) -> None:
        with self._lock:
            self._entries[key] = _Entry(
                expires_at=self._clock() + self._ttl_seconds,
                events=tuple(deepcopy(events)),
            )
