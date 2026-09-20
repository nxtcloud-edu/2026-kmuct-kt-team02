"""정책 데이터가 인용 검증을 통과할 수 있는 상태인지 점검한다.

왜 이 점검이 먼저인가
---------------------
인용 검증은 발췌를 ``raw_text`` 와 문자열로 대조한다. 그래서 **데이터가 어긋나면
판정 품질과 무관하게 전건 실패한다.** 그때 화면은 모든 카드가 "확인이 필요해요"가
되고, "조건마다 공고 원문 각주"라는 핵심 차별점이 사라진다.

실패는 판정이 아니라 데이터에서 온다. 그러니 데이터부터 본다.

무엇을 보는가
-------------
| 검사 | 왜 |
| --- | --- |
| ``raw_text`` 가 있는지 | 대조할 원문이 없으면 모든 발췌가 ``empty_raw`` 로 떨어진다 |
| ``exceptions_text`` ⊂ ``raw_text`` | 예외 문장을 요약·수정해 옮기면 그 정책의 모든 발췌가 ``not_found`` 다 |
| ``condition_sources`` 각 문장 ⊂ ``raw_text`` | 규칙 조건 각주도 같은 방식으로 검증된다 |
| ``source_url`` · ``checked_at`` | 둘 중 하나가 없으면 결과에서 제외된다 (FR12) |

여기서 하지 않는 것: 나이·소득 같은 값이 공고와 맞는지 판단. 그건 백엔드A의 검수
체크리스트(`docs/04-data-schema.md` 4장) 몫이고 사람이 원문을 봐야 한다.

쓰는 방법
--------

    python3 -m ai.judgment.data_check data/policies/staging.json

문제가 있으면 종료 코드가 1이다.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from ai.judgment.citation import raw_text_covers, verify_condition_sources

#: 검사 항목 코드
ISSUE_RAW_TEXT_MISSING = "raw_text_missing"
ISSUE_EXCEPTIONS_NOT_IN_RAW = "exceptions_text_not_in_raw_text"
ISSUE_CONDITION_SOURCE_NOT_IN_RAW = "condition_source_not_in_raw_text"
ISSUE_SOURCE_URL_MISSING = "source_url_missing"
ISSUE_CHECKED_AT_MISSING = "checked_at_missing"


@dataclass(frozen=True)
class DataIssue:
    policy_id: str
    code: str
    detail: str


@dataclass
class DataCheckReport:
    """정책별 점검 결과.

    checked          검사한 정책 수
    judgeable        예외 조건 판정 대상이 있는 정책 수
    issues           발견한 문제
    """

    checked: int = 0
    judgeable: int = 0
    issues: List[DataIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues

    def by_code(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for issue in self.issues:
            counts[issue.code] = counts.get(issue.code, 0) + 1
        return counts

    def summary(self) -> str:
        lines = [
            f"정책 {self.checked}건 점검 / 판정 대상 {self.judgeable}건",
            f"문제 {len(self.issues)}건",
        ]
        counts = self.by_code()
        if counts:
            detail = ", ".join(f"{code} {count}" for code, count in sorted(counts.items()))
            lines.append(f"항목별: {detail}")
        if self.judgeable == 0:
            lines.append(
                "주의: exceptions_text 가 있는 정책이 없다. "
                "예외 조건 판정과 원문 각주가 아직 동작할 수 없다"
            )
        return "\n".join(lines)


def check_policies(policies: Iterable[Mapping[str, Any]]) -> DataCheckReport:
    """정책 목록을 점검한다."""
    report = DataCheckReport()

    for policy in policies:
        report.checked += 1
        pid = str(policy.get("id") or "(id 없음)")
        raw_text = str(policy.get("raw_text") or "").strip()
        exceptions_text = str(policy.get("exceptions_text") or "").strip()

        if not str(policy.get("source_url") or "").strip():
            report.issues.append(
                DataIssue(pid, ISSUE_SOURCE_URL_MISSING, "출처가 없으면 결과에서 제외된다 (FR12)")
            )
        if not str(policy.get("checked_at") or "").strip():
            report.issues.append(
                DataIssue(pid, ISSUE_CHECKED_AT_MISSING, "확인일이 없으면 결과에서 제외된다 (FR12)")
            )

        if not raw_text:
            report.issues.append(
                DataIssue(
                    pid,
                    ISSUE_RAW_TEXT_MISSING,
                    "대조할 원문이 없어 모든 발췌가 empty_raw 로 떨어진다",
                )
            )
            continue

        if exceptions_text:
            report.judgeable += 1
            if not raw_text_covers(exceptions_text, raw_text):
                report.issues.append(
                    DataIssue(
                        pid,
                        ISSUE_EXCEPTIONS_NOT_IN_RAW,
                        "예외 문장이 원문에 그대로 없다. 이 정책의 모든 발췌가 not_found 가 된다",
                    )
                )

        sources = policy.get("condition_sources")
        if isinstance(sources, Mapping) and sources:
            for name, reason in verify_condition_sources(dict(sources), raw_text).items():
                report.issues.append(
                    DataIssue(
                        pid,
                        ISSUE_CONDITION_SOURCE_NOT_IN_RAW,
                        f"{name} 근거 문장이 원문에 없다 ({reason})",
                    )
                )

    return report


def load_policies(path: str) -> List[Dict[str, Any]]:
    """정책 파일을 읽는다. 목록이거나 ``policies`` 키를 가진 객체를 받는다."""
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    if isinstance(data, list):
        return list(data)
    if isinstance(data, Mapping):
        items = data.get("policies")
        if isinstance(items, list):
            return list(items)
    raise ValueError("정책 목록을 찾지 못했다. 목록이거나 policies 키가 있어야 한다")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    path = args[0] if args else "data/policies/staging.json"

    try:
        policies = load_policies(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"정책 파일을 읽지 못했다: {path}\n  {type(exc).__name__}: {exc}")
        return 1

    report = check_policies(policies)
    print(report.summary())

    if report.issues:
        print()
        print("| 정책 | 항목 | 설명 |")
        print("| --- | --- | --- |")
        for issue in report.issues:
            print(f"| {issue.policy_id} | {issue.code} | {issue.detail} |")

    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
