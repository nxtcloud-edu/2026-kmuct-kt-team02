"""``python3 -m ai.judgment`` 로 평가 케이스 상태를 점검한다.

판정기가 정해지기 전에도 케이스 데이터가 쓸 수 있는 상태인지 확인할 수 있다.
"""

from ai.judgment.cases import CASES
from ai.judgment.citation import raw_text_covers


def main() -> None:
    print(f"예외 조건 판정 평가 케이스 {len(CASES)}건\n")

    broken = []
    for case in CASES:
        if not raw_text_covers(case.exceptions_text, case.raw_text):
            broken.append(case.id)
        flag = "실제 문장 아님" if case.is_placeholder else str(case.source_url)
        print(
            f"  {case.id}  {case.pattern:<26}"
            f" 기대 {case.expected_result:<8} {flag}"
        )

    print()
    if broken:
        print(f"문제: exceptions_text 가 raw_text 안에 없는 케이스 {broken}")
    else:
        print("불변식 확인: 모든 케이스의 exceptions_text 가 raw_text 안에 있음")

    placeholders = [c.id for c in CASES if c.is_placeholder]
    if placeholders:
        print(
            f"할 일: {len(placeholders)}건을 실제 공고 문장으로 교체 "
            f"({', '.join(placeholders)}). 정책 데이터 필요"
        )


main()
