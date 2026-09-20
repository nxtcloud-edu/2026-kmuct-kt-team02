"""``python -m ai.judgment.run_eval`` — AI B 완료 기준을 한 번에 확인한다.

게이트웨이 연결이 끝난 뒤 **이 명령 하나로** 판정 평가와 지표 정리가 끝난다.
따로 조립할 것이 없게 만드는 것이 이 파일의 목적이다.

하는 일
-------

1. 설정을 먼저 읽어 게이트웨이에 붙는 판정기를 만든다
2. J1~J8 (`cases.py`) 을 **인용 검증까지 통과한 결과로** 채점한다 (`scoring.py`)
3. 완료 기준 두 개를 판정한다 — 8건 중 7건 이상, **미충족 오판 0건**
4. 지표를 발표 슬라이드 표로 모은다 (`metrics.py`)

사용법
------

    set -a; source .env; set +a
    .venv/bin/python -m ai.judgment.run_eval

케이스 몇 개만 보려면 번호를 준다. 프롬프트를 고친 뒤 빠르게 확인할 때 쓴다.

    .venv/bin/python -m ai.judgment.run_eval J1 J3

## 종료 코드

| 코드 | 뜻 |
| --- | --- |
| 0 | 완료 기준 충족 |
| 1 | 완료 기준 미달 |
| 2 | 설정이 없거나 호출이 전부 실패해서 **측정 자체가 안 됐다** |

`python -m ...` 이 에러 없이 끝난 것은 성공의 증거가 아니다. 그래서 완료 기준을
종료 코드로 드러낸다.

## 왜 실패 건수를 따로 보는가

`ClaudeClient` 는 설정을 첫 호출까지 미룬다. 키가 없어도 객체는 만들어진다. 그
상태로 8건을 돌리면 전부 실패해 자리표시 `unknown` 이 되는데, **기대가 `unknown`
인 케이스가 우연히 맞아서 "3/8 통과, 미충족 오판 0건" 같은 그럴듯한 점수가
나온다.** 연결이 끊긴 것을 측정 결과로 착각하게 된다.

그래서 두 겹으로 막는다. 돌리기 전에 `load_config()` 로 설정을 확인하고, 돌린 뒤에
`JudgeStats.failures` 를 보고 **호출이 한 건이라도 실패하면 점수를 발표 수치로
쓰지 말라고 출력한다.** 전부 실패했으면 점수를 아예 내지 않는다.

## 기록하지 않는 것

프로필과 사용자 메시지 원문은 남기지 않는다 (`docs/03-api-contract.md` 11장).
키 값과 모델 이름도 출력하지 않는다. 케이스는 번호로만 부른다.
"""

from __future__ import annotations

import sys
import time
from typing import List, Optional, Sequence, Tuple

from ai.judgment import metrics
from ai.judgment.cases import CASES, ExceptionCase
from ai.judgment.client import ClaudeClient, LLMError, load_config
from ai.judgment.judge import ExceptionJudge
from ai.judgment.scoring import Report, evaluate

#: 완료 기준 (`ai/judgment/README.md` 14장): 8건 중 7건 이상
MIN_PASSED = 7

EXIT_OK = 0
EXIT_CRITERIA_NOT_MET = 1
EXIT_NOT_MEASURED = 2


def pick_cases(ids: Sequence[str]) -> List[ExceptionCase]:
    """번호로 케이스를 고른다. 비우면 전부."""
    if not ids:
        return list(CASES)
    wanted = {value.strip().upper() for value in ids if value.strip()}
    chosen = [case for case in CASES if case.id.upper() in wanted]
    missing = wanted - {case.id.upper() for case in chosen}
    if missing:
        raise LookupError(f"없는 케이스 번호: {', '.join(sorted(missing))}")
    return chosen


def run(
    cases: Sequence[ExceptionCase],
    *,
    judge: Optional[ExceptionJudge] = None,
) -> Tuple[Report, ExceptionJudge, float]:
    """평가를 돌리고 (보고서, 판정기, 걸린 시간)을 돌려준다."""
    worker = judge if judge is not None else ExceptionJudge(ClaudeClient())
    started = time.monotonic()
    report = evaluate(worker.judge_policy, cases)
    return report, worker, time.monotonic() - started


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    try:
        cases = pick_cases(args)
    except LookupError as exc:
        print(str(exc))
        print(f"쓸 수 있는 번호: {', '.join(case.id for case in CASES)}")
        return EXIT_NOT_MEASURED

    # 설정을 **먼저** 읽는다. 생성만으로는 키가 없는 것을 알 수 없다.
    try:
        config = load_config()
    except LLMError as exc:
        print(f"설정이 없어 돌릴 수 없다 (kind={exc.kind}): {exc}")
        print("  set -a; source .env; set +a")
        print("  .venv/bin/python -m ai.judgment.readiness")
        return EXIT_NOT_MEASURED

    endpoint = config.base_url or "SDK 기본 엔드포인트"
    print(f"판정 평가 {len(cases)}건 실행 (주소 {endpoint})\n")

    report, judge, elapsed = run(cases, judge=ExceptionJudge(ClaudeClient()))

    print(report.detail())
    print()
    print(report.summary())
    print()
    print(judge.stats.summary())
    print(f"걸린 시간 {elapsed:.1f}초 (케이스당 평균 {elapsed / max(1, len(cases)):.1f}초)")

    failures = judge.stats.failures
    if failures >= len(cases):
        print(
            f"\n측정 실패: {len(cases)}건 전부 호출이 실패했다. "
            "위 점수는 자리표시 unknown 이 우연히 맞은 것이므로 수치가 아니다"
        )
        print("  .venv/bin/python -m ai.judgment.readiness 로 설정을 확인한다")
        return EXIT_NOT_MEASURED

    # 인용 검증 통과율의 분모. 케이스당 발췌 1개이므로 "제거되지 않은 케이스 수"가
    # 통과한 발췌 수다 (설계서 6-3: 조건당 발췌 1개).
    verified = sum(1 for outcome in report.outcomes if not outcome.removed_excerpts)
    slide = metrics.collect(
        judgment_report=report,
        judge_stats=judge.stats,
        removed_excerpts=report.removed_excerpt_count,
        verified_excerpts=verified,
    )
    print()
    print(slide.to_slide())

    # 일부만 골라 돌릴 때 "7건 이상"을 적용하면 항상 미달이 된다. 완료 기준은 전체
    # 8건에 대한 것이므로, 부분 실행에서는 미충족 오판만 본다.
    full_run = len(cases) == len(CASES)
    print()
    if full_run:
        met = report.meets_completion_criteria(min_passed=MIN_PASSED)
        if met:
            print(f"완료 기준 충족: 통과 {report.passed}/{report.total}, 미충족 오판 0건")
        else:
            print(
                f"완료 기준 미달: 통과 {report.passed}/{report.total} "
                f"(기준 {MIN_PASSED}), 미충족 오판 {report.false_unmet}건 (기준 0)"
            )
    else:
        met = report.false_unmet == 0
        print(
            f"부분 실행 {report.passed}/{report.total} 통과, "
            f"미충족 오판 {report.false_unmet}건. "
            f"완료 기준(7/{len(CASES)})은 전체 실행에서만 판정한다"
        )

    if report.false_unmet:
        print("  미충족 오판은 사용자에게 '안 된다'고 잘못 말하는 것이다. 가장 먼저 고친다")

    if failures:
        met = False
        print(
            f"\n주의: 호출 {failures}건이 실패했다. 그 케이스는 자리표시 unknown 으로 "
            "채점됐으므로 이 점수는 발표 수치로 쓰지 않는다"
        )

    if report.placeholder_cases:
        met = False
        print(
            f"\n경고: {report.placeholder_cases}건이 아직 대체 문장이다. "
            "이 점수는 발표 수치로 쓰지 않는다 (실제 공고 문장 필요)"
        )

    return EXIT_OK if met else EXIT_CRITERIA_NOT_MET


if __name__ == "__main__":
    raise SystemExit(main())
