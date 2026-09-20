"""근거 없는 조건 점검 (`ai/judgment/README.md` 11장).

왜 인용 검증만으로는 부족한가
-----------------------------
인용 검증(`citation.py`)은 **발췌가 원문에 있는지**만 본다. 그래서 다음이 남는다.

- 발췌는 원문에 있는데 **조건 요약(`name`)이 원문에 없는 내용을 말하는** 경우
- 조건 요약에 원문에 없는 **금액·비율·기간·나이** 같은 숫자가 들어간 경우

조건 요약은 20자 이내 명사형 **요약**이라 원문과 문자열이 일치하지 않는다.
그래서 문자열 대조로는 잡을 수 없다. 이게 인용 검증의 구조적 한계이고,
README 11장 점검표가 그 빈칸을 메운다.

이 모듈은 그 점검 중 **자동으로 잡을 수 있는 부분만** 맡는다.
숫자는 기계가 정확히 잡을 수 있고 가장 위험하다. 조건 요약이
"소득 150% 이하"라고 했는데 원문에 `150` 이 없으면 근거 없는 조건이다
(`CONTRIBUTING.md` 9장: 공고에 없는 금액·날짜·조건을 화면에 보여주지 않는다).

**뜻이 어긋난 요약은 사람이 본다.** `grounding-checklist.md` 양식을 쓴다.

여기서 하지 않는 것 (중복하면 규칙이 두 곳에 생긴다)
----------------------------------------------------
| 하지 않는 것 | 하는 쪽 |
| --- | --- |
| 발췌를 원문과 대조 | `citation.verify_excerpt` |
| 발췌 길이 확인 | `citation.py` |
| 조건 요약 길이 확인 | `schema.py` |
| 뜻이 맞는지 판단 | 사람 (`grounding-checklist.md`) |

LLM 을 호출하지 않는다. 검증과 같은 이유로, 판정을 만든 것과 같은 종류의
실패를 공유하는 도구로는 점검이 되지 않는다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence, Set

from ai.judgment.normalize import normalize
from ai.judgment.values import UNKNOWN

# ---------------------------------------------------------------------------
# 검출 종류
# ---------------------------------------------------------------------------

UNGROUNDED_NUMBER = "ungrounded_number"
"""조건 요약의 숫자가 원문에 없음. 금액·비율·나이·기간을 지어낸 것."""

NAME_WITHOUT_EXCERPT = "name_without_excerpt"
"""조건 요약은 있는데 발췌가 비었거나 검증에 실패함. 근거 없이 문구가 화면에 나간다."""

KINDS = (UNGROUNDED_NUMBER, NAME_WITHOUT_EXCERPT)

# ---------------------------------------------------------------------------
# 숫자 추출
#
# 쉼표가 섞인 것(1,000)과 소수점(1.5)을 하나의 숫자로 본다.
# 쉼표는 세 자리 묶음만 인정한다. "1,2" 를 12 로 읽으면 없는 숫자를 만들어
# 대조가 조용히 틀어진다.
# ---------------------------------------------------------------------------

_NUMBER = re.compile(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")

# 한 자리 수(0~9)는 건너뛴다.
#
# "1개", "2년차", "3단계" 처럼 요약을 다듬는 과정에서 흔히 생기고, 원문에 그
# 숫자가 없어도 근거 문제가 아닌 경우가 많다. 이걸 잡으면 점검표가 잡음으로
# 가득 차고, 그러면 아무도 점검표를 보지 않는다. 사람이 볼 목록을 짧게 유지하는
# 쪽이 실제로 근거 없는 조건을 잡을 확률이 높다.
#
# 금액·비율·나이·기간처럼 위험한 숫자는 거의 두 자리 이상이다(19, 34, 150, 1,000).
# 그래서 놓치는 위험이 작다.
_MIN_DIGITS = 2


def numbers_in(text: object) -> Set[str]:
    """문자열에서 대조용 숫자를 뽑는다.

    쉼표를 뗀 형태로 돌려주므로 원문의 `1,000` 과 요약의 `1000` 이 같은 것으로 본다.
    한 자리 수는 담지 않는다(위 `_MIN_DIGITS` 주석).

    전각 숫자(１５０)는 `normalize()` 가 NFC 만 적용하므로 반각으로 바뀌지 않는다.
    원문이 전각이면 반각 요약과 매칭되지 않아 **없는 문제를 잡는 쪽**으로 기울어진다.
    사람이 점검표에서 걸러내면 되므로 놓치는 쪽보다 안전하다.
    """
    found: Set[str] = set()
    for match in _NUMBER.finditer(str(text or "")):
        token = match.group(0).replace(",", "")
        # 소수점이 붙은 값(1.5)은 한 자리 수가 아니다. 그대로 대조한다.
        if "." not in token and len(token) < _MIN_DIGITS:
            continue
        found.add(token)
    return found


# ---------------------------------------------------------------------------
# 결과 형식
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GroundingIssue:
    """근거 없는 조건 1건.

    condition_name  문제가 된 조건 요약
    kind            `KINDS` 중 하나
    token           문제가 된 부분 (숫자 또는 조건 요약)
    detail          사람이 점검표에 옮길 때 쓰는 설명
    """

    condition_name: str
    kind: str
    token: str
    detail: str = ""


@dataclass
class GroundingReport:
    """점검 결과.

    issues   검출된 건
    checked  검사한 조건 수. `placeholder` 로 건너뛴 조건은 세지 않는다
    """

    issues: List[GroundingIssue] = field(default_factory=list)
    checked: int = 0

    @property
    def ok(self) -> bool:
        """목표는 0건이다 (README 11장)."""
        return not self.issues

    @property
    def count(self) -> int:
        return len(self.issues)

    def by_kind(self) -> Dict[str, int]:
        """종류별 건수. 지표 슬라이드에 그대로 쓴다."""
        counts: Dict[str, int] = {}
        for issue in self.issues:
            counts[issue.kind] = counts.get(issue.kind, 0) + 1
        return counts

    def summary(self) -> str:
        """지표용 한 줄 (README 12장)."""
        line = f"근거 없는 조건 {self.count}건 / 검사한 조건 {self.checked}개"
        counts = self.by_kind()
        if counts:
            breakdown = ", ".join(f"{kind} {counts[kind]}" for kind in KINDS if kind in counts)
            line += f" ({breakdown})"
        return line


# ---------------------------------------------------------------------------
# 점검
# ---------------------------------------------------------------------------


def check_condition(condition: Dict[str, Any], raw_text: object) -> GroundingReport:
    """조건 하나를 점검한다.

    조건 항목 형식은 `docs/03-api-contract.md` 4-1 을 따른다.
    (`name`, `result`, `judged_by`, `excerpt`, `needed_field`)

    `placeholder` 표시가 있는 조건은 건너뛴다. 판정 실패를 나타내려고 만든
    자리표시이므로 `name` 이 빈 채로 오는 것이 정상이고, 건수에 세면 지표가 흐려진다.
    """
    if condition.get("placeholder"):
        return GroundingReport()

    name = str(condition.get("name") or "").strip()
    issues: List[GroundingIssue] = []

    if name:
        issues.extend(_number_issues(name, raw_text))
        issues.extend(_excerpt_issues(name, condition))

    return GroundingReport(issues=issues, checked=1)


def check_conditions(
    conditions: Sequence[Dict[str, Any]],
    raw_text: object,
) -> GroundingReport:
    """조건 목록을 통째로 점검한다.

    빈 목록이면 0건이다. 예외 조건이 없는 정책은 판정을 생략하므로 정상이다.
    """
    report = GroundingReport()
    for condition in conditions or ():
        one = check_condition(condition, raw_text)
        report.issues.extend(one.issues)
        report.checked += one.checked
    return report


def _number_issues(name: str, raw_text: object) -> List[GroundingIssue]:
    """조건 요약의 숫자를 원문 숫자 집합과 대조한다.

    비교는 `normalize()` 를 거친 양쪽에 대해 한다. 직접 정규화하지 않는다.
    보이지 않는 문자 처리 규칙이 두 곳에 생기면 한쪽만 고쳐질 수 있다.
    (숫자 사이에 제로폭 공백(U+200B)이 끼면 1과 50 두 숫자로 읽혀 150 과 어긋난다.)

    원문이 비어 있어도 건너뛰지 않는다. 대조할 근거가 아예 없다는 뜻이므로
    요약의 숫자는 근거가 없다. 데이터 문제이기도 해서 눈에 보이는 편이 낫다.
    """
    wanted = numbers_in(normalize(name))
    if not wanted:
        return []

    grounded = numbers_in(normalize(str(raw_text or "")))
    return [
        GroundingIssue(
            condition_name=name,
            kind=UNGROUNDED_NUMBER,
            token=token,
            detail="조건 요약의 숫자가 공고 원문에 없음",
        )
        for token in sorted(wanted - grounded)
    ]


def _excerpt_issues(name: str, condition: Dict[str, Any]) -> List[GroundingIssue]:
    """조건 요약이 근거 없이 남아 있는지 본다.

    발췌를 원문과 대조하지는 않는다. 그건 `citation.verify_excerpt` 몫이다.
    여기서는 **인용 검증이 남긴 흔적**만 본다.

    `excerpt_verified` 키가 없으면(아직 검증하지 않은 조건) 판단하지 않는다.
    검증 전 상태를 문제로 세면 건수가 실제와 달라진다.
    """
    excerpt = condition.get("excerpt")
    excerpt_text = "" if excerpt is None else str(excerpt).strip()

    if not excerpt_text:
        return [
            GroundingIssue(
                condition_name=name,
                kind=NAME_WITHOUT_EXCERPT,
                token=name,
                detail="발췌 없이 조건 요약만 있음",
            )
        ]

    if condition.get("excerpt_verified") is False:
        result = str(condition.get("result") or "")
        return [
            GroundingIssue(
                condition_name=name,
                kind=NAME_WITHOUT_EXCERPT,
                token=name,
                detail=(
                    "인용 검증에 실패했는데 조건 요약이 남아 있음 "
                    f"(result={result or '없음'}, {UNKNOWN} 으로 내리고 요약을 비워야 함)"
                ),
            )
        ]

    return []


# ---------------------------------------------------------------------------
# 실행 진입점
# ---------------------------------------------------------------------------

USAGE = """근거 없는 조건 점검 (ai/judgment/README.md 11장)

이 도구는 자동으로 잡을 수 있는 두 가지만 본다.
  ungrounded_number      조건 요약의 숫자가 공고 원문에 없음
  name_without_excerpt   근거 없이 조건 요약만 화면에 나감

정책 데이터가 아직 저장소에 없다 (data/ 는 백엔드A 소유). 데이터가 들어오면 이렇게 쓴다.

    from ai.judgment.grounding import check_conditions
    report = check_conditions(result["conditions"], policy["raw_text"])
    print(report.summary())
    for issue in report.issues:
        print(issue.kind, issue.token, issue.condition_name, issue.detail)

뜻이 어긋난 요약은 자동으로 잡지 못한다. 조건 요약은 원문을 줄인 문장이라
문자열이 일치하지 않기 때문이다. 그건 사람이 본다.

    ai/judgment/grounding-checklist.md   대표 프로필 5개 × 예외 흐름 3개, 13:00~14:30, 목표 0건
"""


def main() -> int:
    """정책 데이터가 없으므로 안내와 사용법만 출력한다."""
    print(USAGE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
