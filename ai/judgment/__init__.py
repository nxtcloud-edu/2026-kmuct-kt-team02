"""예외 조건 판정과 인용 검증 (담당: AI B).

상세 명세는 `ai/judgment/README.md`.

| 모듈 | 역할 |
| --- | --- |
| `values` | 고정 값과 화면 문구 표 |
| `normalize` | 원문·발췌 비교용 정규화 (macOS · Windows 혼용 대응) |
| `citation` | 인용 검증. 원문에 없는 근거를 막는다 |
| `conditional_note` | 조건부 문장 고정 형식 |
| `checklist` | 공고 원문에서 서류·신청 단계 추출 |
| `cases` | 판정 평가 케이스 J1~J8 |
| `scoring` | 판정 평가 채점 |
"""

from ai.judgment.cases import CASES, ExceptionCase, placeholder_cases
from ai.judgment.checklist import extract_checklist
from ai.judgment.citation import (
    VerifyResult,
    contains,
    placeholder_unknown,
    raw_text_covers,
    verify_condition_sources,
    verify_conditions,
    verify_excerpt,
)
from ai.judgment.conditional_note import build_conditional_note
from ai.judgment.normalize import canonical, normalize, normalize_with_map
from ai.judgment.scoring import (
    CaseOutcome,
    Judge,
    Report,
    evaluate,
    evaluate_case,
)
from ai.judgment.values import (
    ASK_NOTICE,
    BY_AI,
    BY_RULE,
    MAX_EXCERPT_LEN,
    MAX_NAME_LEN,
    MET,
    MIN_EXCERPT_LEN,
    UNKNOWN,
    UNMET,
)

__all__ = [
    # 고정 값
    "MET",
    "UNMET",
    "UNKNOWN",
    "BY_RULE",
    "BY_AI",
    "ASK_NOTICE",
    "MIN_EXCERPT_LEN",
    "MAX_EXCERPT_LEN",
    "MAX_NAME_LEN",
    # 정규화
    "canonical",
    "normalize",
    "normalize_with_map",
    # 인용 검증
    "VerifyResult",
    "verify_excerpt",
    "verify_conditions",
    "verify_condition_sources",
    "contains",
    "raw_text_covers",
    "placeholder_unknown",
    # 조건부 문장
    "build_conditional_note",
    # 체크리스트
    "extract_checklist",
    # 평가
    "CASES",
    "ExceptionCase",
    "placeholder_cases",
    "evaluate",
    "evaluate_case",
    "Report",
    "CaseOutcome",
    "Judge",
]
