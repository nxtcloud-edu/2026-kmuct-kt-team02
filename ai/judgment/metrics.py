"""지표 정리와 발표 슬라이드 (`ai/judgment/README.md` 12장, 13장 마지막 항목).

14:30 기능 동결 이후 30분 안에 발표 슬라이드 한 장을 만들어야 한다.
그런데 수치가 세 군데에 흩어져 있다.

| 어디 | 무엇 |
| --- | --- |
| `scoring.Report` | 판정 평가 J1~J8 통과 수, `unmet` 오판 수 |
| `judge.JudgeStats` | 판정 호출·실패·용량 부족 건수 |
| 인용 검증 기록 | 통과한 발췌 수와 제거된 발췌 수 (백엔드B가 기록) |

여기에 남이 채점하는 값(추천 정확도는 백엔드A, 응답 시간은 백엔드B)과
사람이 손으로 센 값(근거 없는 조건 건수)을 더해 한 장으로 모은다.

**이 모듈은 아무것도 다시 세지 않는다.** `Report` 와 `JudgeStats` 가 이미 센 것을
그대로 읽고, 나머지는 인자로 받는다. 같은 것을 두 군데서 세면 두 수치가
어긋나는 순간 어느 쪽이 맞는지 알 수 없다.

가장 중요한 규칙: 측정하지 않은 것은 "미측정"이다
--------------------------------------------------
값이 없는 칸을 0이나 100%로 채우면 안 된다. 특히 "근거 없는 조건 0건"과
"인용 검증 통과율 100%"는 채우지 않아도 달성한 것처럼 보이는 모양이라 위험하다.
점검을 돌리지 않고 0건이라고 말하면 발표에서 하는 거짓말이 된다.

그래서 값이 없으면 `value` 는 `"미측정"`, `met` 은 `None` 이다.
`met=False`(측정했고 목표 미달)와 `met=None`(측정하지 않음)은 다른 상태다.

쓰는 방법
--------
    from ai.judgment.metrics import collect

    report = collect(
        judgment_report=scoring_report,
        judge_stats=judge.stats,
        verified_excerpts=42,
        removed_excerpts=0,
        ungrounded_conditions=0,
        recommendation_accuracy=0.8,
        first_card_seconds=1.2,
        answer_seconds=12.4,
    )
    print(report.to_slide())

코드를 못 돌리는 상황을 대비해 같은 구조의 빈 틀을 `metrics-slide.md` 에 둔다.

`grounding.py` 를 부르지 않는다
-------------------------------
근거 없는 조건 건수는 사람이 점검표(README 11장)로 세는 값이라 여기서는 숫자로만
받는다. 점검 도구가 따로 있어도 이 모듈이 그 도구에 묶이면 도구가 바뀔 때마다
지표 정리가 같이 깨진다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional, Sequence, Tuple, Union

#: 측정하지 않은 칸에 넣는 문구. 0이나 100%로 채우지 않는다.
UNMEASURED = "미측정"

#: 부동소수 비교 여유. 12/15 같은 나눗셈 결과가 목표에 딱 걸릴 때 쓴다.
_TOL = 1e-9

# 목표값 (README 12장 표). **여기서 새로 만들지 않는다.**
TARGET_UNGROUNDED = 0
TARGET_CITATION_PASS_RATE = 1.0
TARGET_JUDGMENT_PASSED = 7
TARGET_RECOMMENDATION_ACCURACY = 0.8
TARGET_FIRST_CARD_SECONDS = 2.0
TARGET_ANSWER_SECONDS = 15.0

#: 한계 표 (README 12장). 문구를 새로 쓰지 않고 그대로 옮긴 것이다.
#: 슬라이드에서 지표와 같은 장에 둔다.
LIMITATIONS: Tuple[Tuple[str, str], ...] = (
    ("커버리지", "검수된 20~30건뿐. 전국 실시간 수집이 아니다"),
    ("판정의 성격", "최종 자격 판정이 아니다. 신청 가능성과 근거를 보여줄 뿐이다"),
    ("설문", "29명 편의표집. 비율을 시장 전체로 일반화하지 않는다"),
    (
        "데이터 갱신",
        "데모는 수동 검수본만 쓴다. 크롤링 산출물도 사람 검수 전에는 결과에서 제외하며, 확인일로부터 14일이 지나면 재확인 필요로 표시한다",
    ),
    ("접근성", "기준을 지켰지만 보조기술 수동 테스트와 전문가 검토는 하지 못했다"),
)

#: 한계를 지표와 같은 장에 적는 이유. 슬라이드 아래에 한 줄로 남긴다.
LIMITATION_REASON = (
    "한계를 먼저 말하는 쪽이 질의응답에서 유리하다. "
    "심사위원이 먼저 지적하면 방어가 되고, 우리가 먼저 말하면 판단으로 보인다"
)


@dataclass(frozen=True)
class MetricRow:
    """슬라이드 지표 표의 한 줄.

    name    지표 이름 (README 12장 표 그대로)
    value   표시할 값. 측정하지 않았으면 "미측정"
    target  목표
    met     달성 여부. **측정하지 않았으면 None** (False 와 구분한다)
    source  누가 주는 값인지. 당일 누구에게 물어야 하는지가 여기 적혀 있다
    """

    name: str
    value: str
    target: str
    met: Optional[bool]
    source: str

    @property
    def unmeasured(self) -> bool:
        """값이 아직 없는 줄인지. 일부만 측정한 경우도 포함한다."""
        return UNMEASURED in self.value

    @property
    def mark(self) -> str:
        """표에 넣는 달성 표시. 미측정은 달성도 미달도 아니다."""
        if self.met is None:
            return "—"
        return "달성" if self.met else "미달"


@dataclass
class MetricsReport:
    rows: List[MetricRow]
    notes: List[str] = field(default_factory=list)

    @property
    def unmeasured_rows(self) -> List[MetricRow]:
        return [row for row in self.rows if row.unmeasured]

    @property
    def all_met(self) -> Optional[bool]:
        """전체 달성 여부.

        미달이 하나라도 있으면 `False` 다. 미측정이 남아 있어도 미달은 이미
        확정된 사실이라 감추지 않는다. 미달이 없고 미측정이 남아 있으면 `None`,
        전부 달성이면 `True`.
        """
        if any(row.met is False for row in self.rows):
            return False
        if any(row.met is None for row in self.rows):
            return None
        return True

    def to_table(self) -> str:
        """지표 표만 마크다운으로."""
        lines = [
            "| 지표 | 값 | 목표 | 달성 | 출처 |",
            "| --- | --- | --- | --- | --- |",
        ]
        for row in self.rows:
            lines.append(
                f"| {row.name} | {row.value} | {row.target} | {row.mark} | {row.source} |"
            )
        return "\n".join(lines)

    def limitations_table(self) -> str:
        lines = ["| 한계 | 내용 |", "| --- | --- |"]
        for name, text in LIMITATIONS:
            lines.append(f"| {name} | {text} |")
        return "\n".join(lines)

    def to_slide(self) -> str:
        """지표와 한계를 한 장으로 (README 12장).

        발표 구성 5번 "지표와 한계"에 그대로 쓴다 (`docs/06-demo-and-metrics.md` 6장).
        """
        parts = [
            "## 지표와 한계",
            "",
            "### 지표",
            "",
            self.to_table(),
            "",
            "### 한계",
            "",
            self.limitations_table(),
        ]
        if self.notes:
            parts += ["", "### 주의", ""]
            parts += [f"- {note}" for note in self.notes]
        parts += ["", f"> {LIMITATION_REASON}."]
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# 입력 정리
# ---------------------------------------------------------------------------


def _count(value: Union[int, Sequence[Any], None]) -> Optional[int]:
    """개수로 넘어온 값과 기록 목록으로 넘어온 값을 모두 받는다.

    인용 검증 제거 기록은 `verify_conditions` 가 돌려주는 목록이고,
    백엔드B가 집계한 숫자만 받을 때도 있다. 둘 다 같은 자리에 들어온다.
    """
    if value is None:
        return None
    if isinstance(value, bool):  # True/False 를 1/0 으로 세지 않는다
        return None
    if isinstance(value, int):
        return value
    try:
        return len(value)  # type: ignore[arg-type]
    except TypeError:
        return None


def _pct(ratio: float) -> str:
    """측정값 표시. 소수 한 자리까지 남긴다 (92.3% 와 100% 를 구분해야 한다)."""
    return f"{ratio * 100:.1f}%"


def _seconds(value: float) -> str:
    return f"{value:.1f}초"


def _trim(text: str) -> str:
    """목표 문구용. 소수점 아래가 0이면 떼어 README 12장 표기와 맞춘다."""
    return text.replace(".0", "")


# ---------------------------------------------------------------------------
# 지표 다섯 줄 (README 12장 표 순서 그대로)
# ---------------------------------------------------------------------------


def _row_ungrounded(count: Optional[int]) -> MetricRow:
    """근거 없는 조건 건수. 사람이 점검표에서 센 값만 쓴다 (README 11장)."""
    if count is None:
        value, met = UNMEASURED, None
    else:
        value = f"{count}건"
        met = count <= TARGET_UNGROUNDED
    return MetricRow(
        name="근거 없는 조건 건수",
        value=value,
        target=f"{TARGET_UNGROUNDED}건",
        met=met,
        source="점검표 수동 점검 (AI B)",
    )


def _row_citation(verified: Optional[int], removed: Optional[int]) -> MetricRow:
    """인용 검증 통과율과 제거 건수.

    분모는 통과 수 + 제거 수다. 둘 다 0이면 검증을 한 번도 돌리지 않은 것이므로
    "미측정"이다. **0으로 나누지 않는다.**
    한쪽만 들어오면 분모를 만들 수 없다. 없는 쪽을 0으로 가정하면 통과율이
    100%나 0%로 보이므로, 아는 값만 적고 통과율은 미측정으로 둔다.
    """
    target = f"통과율 {_trim(_pct(TARGET_CITATION_PASS_RATE))}"
    known = f"제거 {removed}건" if removed is not None else ""

    if verified is None and removed is None:
        value, met = UNMEASURED, None
    elif verified is None or removed is None:
        parts = [f"통과율 {UNMEASURED}"]
        if known:
            parts.append(known)
        if verified is not None:
            parts.append(f"통과 {verified}건")
        value, met = " / ".join(parts), None
    else:
        total = verified + removed
        if total <= 0:
            value, met = UNMEASURED, None
        else:
            rate = verified / total
            value = f"통과율 {_pct(rate)} (통과 {verified}건 / 제거 {removed}건)"
            met = rate >= TARGET_CITATION_PASS_RATE - _TOL
    return MetricRow(
        name="인용 검증 통과율과 제거 건수",
        value=value,
        target=target,
        met=met,
        source="인용 검증 기록 (백엔드B)",
    )


def _row_judgment(report: Any) -> MetricRow:
    """판정 평가 J1~J8. `scoring.Report` 가 센 것을 그대로 읽는다.

    완료 기준 판단도 `Report.meets_completion_criteria()` 를 그대로 쓴다.
    `unmet` 오판이 있으면 통과 수와 무관하게 미달이라는 규칙이 거기 들어 있다.
    """
    target = f"{TARGET_JUDGMENT_PASSED}개 이상, unmet 오판 0건"
    if report is None:
        return MetricRow(
            name="판정 평가 J1~J8 결과",
            value=UNMEASURED,
            target=target,
            met=None,
            source="평가 표 scoring.Report (AI B)",
        )

    passed = getattr(report, "passed", None)
    total = getattr(report, "total", None)
    false_unmet = getattr(report, "false_unmet", None)
    if passed is None or total is None or false_unmet is None:
        return MetricRow(
            name="판정 평가 J1~J8 결과",
            value=UNMEASURED,
            target=target,
            met=None,
            source="평가 표 scoring.Report (AI B)",
        )

    # 완료 기준 판단은 `Report` 가 가진 규칙을 그대로 쓴다. 여기서 다시 계산하지 않는다.
    criteria = getattr(report, "meets_completion_criteria", None)
    if not callable(criteria):
        return MetricRow(
            name="판정 평가 J1~J8 결과",
            value=UNMEASURED,
            target=target,
            met=None,
            source="평가 표 scoring.Report (AI B)",
        )
    met = criteria(min_passed=TARGET_JUDGMENT_PASSED)

    return MetricRow(
        name="판정 평가 J1~J8 결과",
        value=f"{passed}/{total} 통과, unmet 오판 {false_unmet}건",
        target=target,
        met=bool(met),
        source="평가 표 scoring.Report (AI B)",
    )


def _row_recommendation(accuracy: Optional[float]) -> MetricRow:
    """추천 정확도. 백엔드A가 정답셋으로 채점한다. 우리가 계산하지 않는다."""
    if accuracy is None:
        value, met = UNMEASURED, None
    else:
        value = _pct(float(accuracy))
        met = float(accuracy) >= TARGET_RECOMMENDATION_ACCURACY - _TOL
    return MetricRow(
        name="추천 정확도",
        value=value,
        target=f"{_trim(_pct(TARGET_RECOMMENDATION_ACCURACY))} 이상",
        met=met,
        source="정답셋 채점 (백엔드A)",
    )


def _row_latency(
    first_card: Optional[float], answer: Optional[float]
) -> MetricRow:
    """응답 시간. 첫 카드와 AI 설명 두 값이 다 있어야 달성 여부를 말할 수 있다."""
    target = (
        f"첫 카드 {_trim(_seconds(TARGET_FIRST_CARD_SECONDS))}, "
        f"AI 설명 {_trim(_seconds(TARGET_ANSWER_SECONDS))}"
    )
    first_text = _seconds(float(first_card)) if first_card is not None else UNMEASURED
    answer_text = _seconds(float(answer)) if answer is not None else UNMEASURED

    if first_card is None and answer is None:
        value, met = UNMEASURED, None
    else:
        value = f"첫 카드 {first_text} / AI 설명 {answer_text}"
        if first_card is None or answer is None:
            met = None
        else:
            met = (
                float(first_card) <= TARGET_FIRST_CARD_SECONDS + _TOL
                and float(answer) <= TARGET_ANSWER_SECONDS + _TOL
            )
    return MetricRow(
        name="응답 시간",
        value=value,
        target=target,
        met=met,
        source="데모 경로 실측 (백엔드B)",
    )


# ---------------------------------------------------------------------------
# 모으기
# ---------------------------------------------------------------------------


def collect(
    *,
    judgment_report: Any = None,
    judge_stats: Any = None,
    removed_excerpts: Union[int, Sequence[Any], None] = None,
    verified_excerpts: Optional[int] = None,
    ungrounded_conditions: Optional[int] = None,
    recommendation_accuracy: Optional[float] = None,
    first_card_seconds: Optional[float] = None,
    answer_seconds: Optional[float] = None,
) -> MetricsReport:
    """흩어진 수치를 슬라이드 한 장으로 모은다.

    인자를 하나도 주지 않으면 다섯 줄이 전부 "미측정"이고 `met` 은 전부 `None` 이다.
    그게 옳은 초기 상태다. 아직 아무것도 측정하지 않았으니까.

    judgment_report         `ai.judgment.scoring.Report`
    judge_stats             `ai.judgment.judge.JudgeStats`. 주의 사항 판단에만 쓴다
    removed_excerpts        인용 검증 제거 기록 목록 또는 건수
    verified_excerpts       검증을 통과한 발췌 수 (통과율 분모용)
    ungrounded_conditions   근거 없는 조건 건수. 사람이 점검표에서 센 값
    recommendation_accuracy 백엔드A 채점값 (0.0~1.0)
    first_card_seconds      폼 제출부터 첫 카드까지 (백엔드B 실측)
    answer_seconds          메시지 전송부터 답변 완료까지, 5회 최댓값 (백엔드B 실측)
    """
    removed = _count(removed_excerpts)
    verified = _count(verified_excerpts)

    rows = [
        _row_ungrounded(_count(ungrounded_conditions)),
        _row_citation(verified, removed),
        _row_judgment(judgment_report),
        _row_recommendation(recommendation_accuracy),
        _row_latency(first_card_seconds, answer_seconds),
    ]

    report = MetricsReport(rows=rows, notes=_build_notes(rows, judgment_report, judge_stats))
    return report


def _build_notes(
    rows: Sequence[MetricRow], judgment_report: Any, judge_stats: Any
) -> List[str]:
    """발표에서 잘못된 수치를 말하는 것을 막는 경고들.

    세 가지를 자동으로 붙인다. 셋 다 "이 숫자를 그대로 말하면 안 된다"는 신호다.
    """
    notes: List[str] = []

    placeholder = getattr(judgment_report, "placeholder_cases", 0) or 0
    if placeholder:
        notes.append(
            f"판정 평가 {placeholder}건이 아직 실제 공고 문장이 아니다. "
            "이 상태의 J1~J8 수치는 발표에 쓰지 않는다"
        )

    overloaded = getattr(judge_stats, "overloaded", 0) or 0
    if overloaded:
        notes.append(
            f"용량 부족 {overloaded}건이 있었다. 데모는 데모 모드로 돌린다"
        )

    unmeasured = [row.name for row in rows if row.unmeasured]
    if unmeasured:
        notes.append(
            f"{len(unmeasured)}개 지표가 미측정이다: {', '.join(unmeasured)}. "
            "미측정은 달성으로 말하지 않는다"
        )

    return notes
