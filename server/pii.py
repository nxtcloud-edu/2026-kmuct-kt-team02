"""Pure PII detection and irreversible masking.

The module keeps no state, performs no logging, and never returns detected raw
values. Raw text should exist only in the caller's local request scope until
``mask_pii`` or ``mask_structure`` returns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from pydantic import ConfigDict, JsonValue

from server.schemas import ContractModel

MASKING_NOTICE = "민감정보는 입력하지 말아 주세요"


class PiiType(StrEnum):
    RESIDENT_REGISTRATION_NUMBER = "resident_registration_number"
    ACCOUNT_NUMBER = "account_number"
    PHONE_NUMBER = "phone_number"
    EMAIL = "email"


_MASK_TOKENS: dict[PiiType, str] = {
    PiiType.RESIDENT_REGISTRATION_NUMBER: "[주민등록번호 마스킹]",
    PiiType.ACCOUNT_NUMBER: "[계좌번호 마스킹]",
    PiiType.PHONE_NUMBER: "[전화번호 마스킹]",
    PiiType.EMAIL: "[이메일 마스킹]",
}
_TYPE_PRIORITY: dict[PiiType, int] = {
    PiiType.RESIDENT_REGISTRATION_NUMBER: 4,
    PiiType.PHONE_NUMBER: 3,
    PiiType.EMAIL: 2,
    PiiType.ACCOUNT_NUMBER: 1,
}

_RRN_RE = re.compile(
    r"(?<!\d)(?P<birth>\d{6})(?:[- ]?)(?P<rest>[1-8]\d{6})(?!\d)"
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:01[016789](?:[- ]?\d{3,4})[- ]?\d{4})(?!\d)"
)
_EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9.!#$%&'*+/=?^_`{|}~-])"
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+"
    r"@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"
    r"(?![A-Za-z0-9.-])"
)
_ACCOUNT_CANDIDATE_RE = re.compile(r"(?<!\d)\d(?:[\d -]{8,22}\d)(?!\d)")
_ACCOUNT_CONTEXT_RE = re.compile(
    r"계좌(?:\s*번호)?|은행|입금|송금|이체|예금주|통장|account|bank",
    re.IGNORECASE,
)
_ACCOUNT_CONTEXT_RADIUS = 32


class MaskingResult(ContractModel):
    """Safe-to-forward text and non-sensitive detection metadata."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=False,
        frozen=True,
    )

    masked_text: str
    detected_types: tuple[PiiType, ...] = ()
    notice: str | None = None

    @property
    def was_masked(self) -> bool:
        return bool(self.detected_types)


class StructuredMaskingResult(ContractModel):
    """Recursively masked JSON-compatible data for AI request context."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    masked_value: JsonValue
    detected_types: tuple[PiiType, ...] = ()
    notice: str | None = None

    @property
    def was_masked(self) -> bool:
        return bool(self.detected_types)


@dataclass(frozen=True, slots=True)
class _Detection:
    start: int
    end: int
    pii_type: PiiType

    @property
    def length(self) -> int:
        return self.end - self.start


def _valid_rrn_birth_date(match: re.Match[str]) -> bool:
    birth = match.group("birth")
    century_code = match.group("rest")[0]
    year = int(birth[:2])
    month = int(birth[2:4])
    day = int(birth[4:6])
    century = 1900 if century_code in {"1", "2", "5", "6"} else 2000
    try:
        date(century + year, month, day)
    except ValueError:
        return False
    return True


def _has_account_context(text: str, start: int, end: int) -> bool:
    window_start = max(0, start - _ACCOUNT_CONTEXT_RADIUS)
    window_end = min(len(text), end + _ACCOUNT_CONTEXT_RADIUS)
    return _ACCOUNT_CONTEXT_RE.search(text[window_start:window_end]) is not None


def _account_detections(text: str) -> list[_Detection]:
    detections: list[_Detection] = []
    for match in _ACCOUNT_CANDIDATE_RE.finditer(text):
        candidate = match.group(0)
        digit_count = sum(character.isdigit() for character in candidate)
        if not 10 <= digit_count <= 16:
            continue
        if not _has_account_context(text, match.start(), match.end()):
            continue
        detections.append(
            _Detection(match.start(), match.end(), PiiType.ACCOUNT_NUMBER)
        )
    return detections


def _collect_detections(text: str) -> list[_Detection]:
    candidates: list[_Detection] = []
    candidates.extend(
        _Detection(match.start(), match.end(), PiiType.RESIDENT_REGISTRATION_NUMBER)
        for match in _RRN_RE.finditer(text)
        if _valid_rrn_birth_date(match)
    )
    candidates.extend(
        _Detection(match.start(), match.end(), PiiType.PHONE_NUMBER)
        for match in _PHONE_RE.finditer(text)
    )
    candidates.extend(
        _Detection(match.start(), match.end(), PiiType.EMAIL)
        for match in _EMAIL_RE.finditer(text)
    )
    candidates.extend(_account_detections(text))

    # Resolve overlaps by sensitivity-specific priority, then keep output order.
    selected: list[_Detection] = []
    prioritized = sorted(
        candidates,
        key=lambda item: (
            -_TYPE_PRIORITY[item.pii_type],
            -item.length,
            item.start,
        ),
    )
    for candidate in prioritized:
        overlaps = any(
            candidate.start < current.end and current.start < candidate.end
            for current in selected
        )
        if not overlaps:
            selected.append(candidate)
    return sorted(selected, key=lambda item: item.start)


def _ordered_types(detections: list[_Detection]) -> tuple[PiiType, ...]:
    return tuple(dict.fromkeys(detection.pii_type for detection in detections))


def mask_pii(text: str) -> MaskingResult:
    """Mask supported PII without retaining or returning the original matches."""

    if not isinstance(text, str):
        raise TypeError("text must be a string")

    detections = _collect_detections(text)
    if not detections:
        return MaskingResult(masked_text=text)

    parts: list[str] = []
    cursor = 0
    for detection in detections:
        parts.append(text[cursor : detection.start])
        parts.append(_MASK_TOKENS[detection.pii_type])
        cursor = detection.end
    parts.append(text[cursor:])

    return MaskingResult(
        masked_text="".join(parts),
        detected_types=_ordered_types(detections),
        notice=MASKING_NOTICE,
    )


def mask_text(text: str) -> MaskingResult:
    """Readable alias for callers that do not use the PII terminology."""

    return mask_pii(text)


def _merge_types(
    existing: list[PiiType], incoming: tuple[PiiType, ...]
) -> None:
    for pii_type in incoming:
        if pii_type not in existing:
            existing.append(pii_type)


def _mask_value(value: JsonValue, detected_types: list[PiiType]) -> JsonValue:
    if isinstance(value, str):
        result = mask_pii(value)
        _merge_types(detected_types, result.detected_types)
        return result.masked_text
    if isinstance(value, list):
        return [_mask_value(item, detected_types) for item in value]
    if isinstance(value, dict):
        masked: dict[str, JsonValue] = {}
        for key, item in value.items():
            key_result = mask_pii(key)
            _merge_types(detected_types, key_result.detected_types)
            masked[key_result.masked_text] = _mask_value(item, detected_types)
        return masked
    return value


def mask_structure(value: JsonValue) -> StructuredMaskingResult:
    """Recursively mask every string in a JSON-compatible value."""

    detected_types: list[PiiType] = []
    masked_value = _mask_value(value, detected_types)
    return StructuredMaskingResult(
        masked_value=masked_value,
        detected_types=tuple(detected_types),
        notice=MASKING_NOTICE if detected_types else None,
    )
