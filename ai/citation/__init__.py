"""인용 검증 (AI B 담당).

공고 원문에 없는 근거가 화면에 나가지 않게 막는다.
"""

from .normalize import canonical, normalize, normalize_with_map
from .verify import (
    MAX_EXCERPT_LEN,
    MET,
    MIN_EXCERPT_LEN,
    UNKNOWN,
    UNMET,
    VerifyResult,
    contains,
    raw_text_covers,
    verify_conditions,
    verify_excerpt,
)

__all__ = [
    "canonical",
    "normalize",
    "normalize_with_map",
    "verify_excerpt",
    "verify_conditions",
    "contains",
    "raw_text_covers",
    "VerifyResult",
    "MIN_EXCERPT_LEN",
    "MAX_EXCERPT_LEN",
    "MET",
    "UNMET",
    "UNKNOWN",
]
