"""Privacy-safe structured metrics for chat transport outcomes."""

from __future__ import annotations

import logging
from collections.abc import Mapping


logger = logging.getLogger(__name__)


def record_chat_metric(values: Mapping[str, object]) -> None:
    """Log an allowlisted metric mapping; callers must never pass user content."""

    allowed = {
        "request_id",
        "duration_ms",
        "outcome",
        "error_code",
        "cache_hit",
        "event_count",
        "citation_failures",
        "interpretation_ms",
        "rules_ms",
        "judgment_ms",
        "answer_ms",
    }
    logger.info(
        "chat_metric",
        extra={"chat_metric": {key: value for key, value in values.items() if key in allowed}},
    )
