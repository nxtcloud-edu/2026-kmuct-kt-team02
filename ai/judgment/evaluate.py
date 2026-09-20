"""예외 조건 판정 채점 (설계서 6-7, 6-8).

완료 기준
---------
- 8개 중 7개 이상 기대대로
- **미충족 오판 0건**

미충족 오판은 기대가 충족이나 미확인인데 미충족으로 판정한 경우다.
사용자에게 "안 된다"고 잘못 말하는 것이라 가장 위험하고, 그래서 따로 센다.
반대 방향(미충족인데 미확인으로 본 것)은 안전한 쪽으로 틀린 것이므로 구분해 기록한다.

채점 대상
---------
판정기 출력을 그대로 채점하지 않는다. **인용 검증을 통과한 뒤의 결과**를 채점한다.
판정이 맞아도 발췌가 원문에 없으면 실제로는 미확인으로 강등되므로,
검증 전 출력으로 채점하면 본선 동작과 어긋난다.

사용법
------
LLM 판정기가 준비되면 그 함수를 넘긴다.

    from ai.judgment.evaluate import evaluate
    report = evaluate(my_judge)
    print(report.summary())

판정기는 다음 형태여야 한다.

    judge(exceptions_text: str, profile: dict, raw_text: str) -> list[dict]

돌려주는 각 항목은 설계서 6-3 형식이다.
``{"summary": str, "result": str, "excerpt": str, "needed_field": str | None}``
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence

from ai.citation import verify_conditions
from ai.judgment.cases import (
    CASES,
    MET,
    UNKNOWN,
    UNMET,
    ExceptionCase,
)

Judge = Callable[[str, Dict[str, object], str], List[Dict[str, object]]]

# 심각한 쪽이 앞. 조건이 여러 개일 때 대표를 고르는 순서이자,
# 판정 상태 계산(설계서 1-3)에서 미충족이 우선하는 것과 같은 방향이다.
_SEVERITY = {UNMET: 0, UNKNOWN: 1, MET: 2}


@dataclass
class CaseOutcome:
    """케이스 하나의 채점 결과."""

    case: ExceptionCase
    actual_result: str
    actual_needed_field: Optional[str]
    result_ok: bool
    needed_field_ok: bool
    removed_excerpts: List[Dict[str, str]] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def passed(self) -> bool:
        return self.result_ok and self.needed_field_ok

    @property
    def is_false_unmet(self) -> bool:
        """미충족 오판. 기대는 미충족이 아닌데 미충족으로 판정한 경우."""
        return self.case.expected_result != UNMET and self.actual_result == UNMET

    @property
    def is_safe_miss(self) -> bool:
        """안전한 쪽으로 틀린 경우. 미충족을 미확인으로 본 것."""
        return self.case.expected_result == UNMET and self.actual_result == UNKNOWN


@dataclass
class Report:
    outcomes: List[CaseOutcome]

    @property
    def total(self) -> int:
        return len(self.outcomes)

    @property
    def passed(self) -> int:
        return sum(1 for o in self.outcomes if o.passed)

    @property
    def result_only_passed(self) -> int:
        """결과는 맞았는데 필요한 추가 항목이 틀린 건수."""
        return sum(1 for o in self.outcomes if o.result_ok and not o.needed_field_ok)

    @property
    def false_unmet(self) -> int:
        return sum(1 for o in self.outcomes if o.is_false_unmet)

    @property
    def safe_misses(self) -> int:
        return sum(1 for o in self.outcomes if o.is_safe_miss)

    @property
    def removed_excerpt_count(self) -> int:
        """인용 검증에서 제거된 발췌 수. 지표 6-8 항목."""
        return sum(len(o.removed_excerpts) for o in self.outcomes)

    @property
    def errors(self) -> int:
        return sum(1 for o in self.outcomes if o.error)

    @property
    def placeholder_cases(self) -> int:
        return sum(1 for o in self.outcomes if o.case.is_placeholder)

    def meets_completion_criteria(self, *, min_passed: int = 7) -> bool:
        """설계서 6-7 완료 기준 충족 여부."""
        return self.passed >= min_passed and self.false_unmet == 0

    def failures(self) -> List[CaseOutcome]:
        return [o for o in self.outcomes if not o.passed]

    def summary(self) -> str:
        """지표 슬라이드에 그대로 옮길 수 있는 형태 (설계서 6-8)."""
        lines = [
            f"예외 조건 판정 평가: {self.passed}/{self.total} 통과",
            f"미충족 오판: {self.false_unmet}건 (완료 기준 0건)",
            f"안전한 쪽 오차(미충족→미확인): {self.safe_misses}건",
            f"결과는 맞고 필요 항목만 틀림: {self.result_only_passed}건",
            f"인용 검증 제거 발췌: {self.removed_excerpt_count}건",
        ]
        if self.errors:
            lines.append(f"판정기 오류: {self.errors}건")
        if self.placeholder_cases:
            lines.append(
                f"주의: {self.placeholder_cases}건이 아직 실제 공고 문장이 아님 "
                "(source_url 미기입). 이 상태의 수치는 참고용이다"
            )
        verdict = "충족" if self.meets_completion_criteria() else "미충족"
        lines.append(f"완료 기준: {verdict}")
        return "\n".join(lines)

    def detail(self) -> str:
        rows = []
        for o in self.outcomes:
            mark = "통과" if o.passed else "실패"
            row = (
                f"[{mark}] {o.case.id} {o.case.pattern}\n"
                f"       기대 {o.case.expected_result}"
                f" / 실제 {o.actual_result}"
            )
            if o.case.expected_needed_field or o.actual_needed_field:
                row += (
                    f"\n       필요 항목 기대 {o.case.expected_needed_field!r}"
                    f" / 실제 {o.actual_needed_field!r}"
                )
            if o.is_false_unmet:
                row += "\n       >> 미충족 오판. 사용자에게 잘못 안 된다고 말하는 경우"
            if o.removed_excerpts:
                reasons = ", ".join(r["reason"] for r in o.removed_excerpts)
                row += f"\n       인용 검증 제거: {reasons}"
            if o.error:
                row += f"\n       오류: {o.error}"
            rows.append(row)
        return "\n".join(rows)


def _representative(conditions: Sequence[Dict[str, object]]) -> Dict[str, object]:
    """조건이 여러 개일 때 대표 하나를 고른다.

    평가 케이스는 예외 문장이 하나지만 판정기가 쪼개서 여러 개를 낼 수 있다.
    가장 심각한 결과를 대표로 삼는다. 판정 상태 계산에서 미충족이 우선하는 것과
    같은 방향이라, 실제 카드에 나타날 상태와 일치한다.
    """
    if not conditions:
        return {"result": UNKNOWN, "needed_field": None}
    return min(conditions, key=lambda c: _SEVERITY.get(str(c.get("result")), 1))


def evaluate_case(judge: Judge, case: ExceptionCase) -> CaseOutcome:
    try:
        raw_conditions = judge(case.exceptions_text, case.profile, case.raw_text)
    except Exception as exc:  # 판정기 실패도 평가 대상이다 (설계서 6-6)
        return CaseOutcome(
            case=case,
            actual_result=UNKNOWN,
            actual_needed_field="공고 확인 필요",
            result_ok=case.expected_result == UNKNOWN,
            needed_field_ok=False,
            error=f"{type(exc).__name__}: {exc}",
        )

    # 실제 동작과 같게, 인용 검증을 통과한 결과로 채점한다.
    checked, removed = verify_conditions(list(raw_conditions), case.raw_text)
    representative = _representative(checked)

    actual_result = str(representative.get("result") or UNKNOWN)
    actual_needed = representative.get("needed_field")
    actual_needed = str(actual_needed) if actual_needed else None

    result_ok = actual_result == case.expected_result
    if case.expected_needed_field is None:
        # 미확인이 아니면 필요한 추가 항목은 의미가 없다.
        needed_field_ok = True
    else:
        needed_field_ok = actual_needed == case.expected_needed_field

    return CaseOutcome(
        case=case,
        actual_result=actual_result,
        actual_needed_field=actual_needed,
        result_ok=result_ok,
        needed_field_ok=needed_field_ok,
        removed_excerpts=removed,
    )


def evaluate(judge: Judge, cases: Sequence[ExceptionCase] = tuple(CASES)) -> Report:
    return Report([evaluate_case(judge, case) for case in cases])


def main() -> None:
    """판정기 없이 케이스 상태만 점검한다.

    LLM 이 정해지기 전에도 케이스 데이터가 쓸 수 있는 상태인지 확인할 수 있다.
    """
    from ai.citation import raw_text_covers

    print(f"예외 조건 판정 평가 케이스 {len(CASES)}건\n")

    broken = []
    for case in CASES:
        if not raw_text_covers(case.exceptions_text, case.raw_text):
            broken.append(case.id)
        flag = "실제 문장 아님" if case.is_placeholder else case.source_url
        print(f"  {case.id}  {case.pattern:<28} 기대 {case.expected_result:<4} {flag}")

    print()
    if broken:
        print(f"문제: exceptions_text 가 raw_text 안에 없는 케이스 {broken}")
    else:
        print("불변식 확인: 모든 케이스의 exceptions_text 가 raw_text 안에 있음")

    placeholders = [c.id for c in CASES if c.is_placeholder]
    if placeholders:
        print(
            f"할 일: {len(placeholders)}건을 실제 공고 문장으로 교체 "
            f"({', '.join(placeholders)}). 정책 스프레드시트 필요"
        )
