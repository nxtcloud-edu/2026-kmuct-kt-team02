"""지표 정리 테스트 (`ai/judgment/metrics.py`).

이 모듈에서 가장 중요한 성질은 **측정하지 않은 것을 달성으로 보이게 하지 않는 것**이다.
그래서 "값이 있을 때 맞게 계산하는지"보다 "값이 없을 때 미측정으로 남는지"를 먼저 본다.

특히 목표가 0건인 지표(근거 없는 조건)와 100%인 지표(인용 검증)는
채우지 않아도 달성처럼 보이는 모양이라 따로 확인한다.
"""

import unittest

from ai.judgment.metrics import (
    LIMITATIONS,
    UNMEASURED,
    MetricsReport,
    collect,
)


class _FakeJudgmentReport:
    """`scoring.Report` 의 최소 대역. 채점기 자체는 별 테스트에서 검증한다."""

    def __init__(self, passed=8, total=8, false_unmet=0, placeholder_cases=0):
        self.passed = passed
        self.total = total
        self.false_unmet = false_unmet
        self.placeholder_cases = placeholder_cases

    def meets_completion_criteria(self, *, min_passed=7):
        return self.passed >= min_passed and self.false_unmet == 0


class _FakeJudgeStats:
    def __init__(self, overloaded=0):
        self.overloaded = overloaded


class TestUnmeasuredDefault(unittest.TestCase):
    """아무 값도 없을 때가 이 모듈의 기본 상태다."""

    def test_아무것도_주지_않으면_다섯_줄이_전부_미측정이다(self):
        report = collect()
        self.assertEqual(len(report.rows), 5)
        for row in report.rows:
            with self.subTest(metric=row.name):
                self.assertEqual(row.value, UNMEASURED)
                self.assertIsNone(row.met, "미측정은 달성도 미달도 아니다")

    def test_미측정이면_all_met은_None이다(self):
        self.assertIsNone(collect().all_met)

    def test_미측정_줄에는_달성_표시가_없다(self):
        for row in collect().rows:
            with self.subTest(metric=row.name):
                self.assertEqual(row.mark, "—")

    def test_목표가_0건인_지표를_0건으로_채우지_않는다(self):
        """근거 없는 조건은 점검을 돌리지 않으면 0건이 아니라 미측정이다."""
        row = collect().rows[0]
        self.assertEqual(row.name, "근거 없는 조건 건수")
        self.assertNotIn("0건", row.value)
        self.assertIsNone(row.met)

    def test_모든_줄에_출처가_적혀_있다(self):
        """당일 누구에게 물어야 하는지가 슬라이드에 남아야 한다."""
        for row in collect().rows:
            with self.subTest(metric=row.name):
                self.assertTrue(row.source.strip())

    def test_미측정_개수를_주의_사항에_알린다(self):
        notes = collect().notes
        self.assertTrue(any("5개 지표가 미측정" in n for n in notes), notes)


class TestUngrounded(unittest.TestCase):
    def test_0건을_주면_달성이다(self):
        row = collect(ungrounded_conditions=0).rows[0]
        self.assertEqual(row.value, "0건")
        self.assertTrue(row.met)

    def test_1건이라도_있으면_미달이다(self):
        row = collect(ungrounded_conditions=1).rows[0]
        self.assertEqual(row.value, "1건")
        self.assertFalse(row.met)

    def test_점검_목록을_그대로_줘도_건수로_센다(self):
        row = collect(ungrounded_conditions=["SEOUL-001 소득 조건", "SEOUL-003 학점"]).rows[0]
        self.assertEqual(row.value, "2건")
        self.assertFalse(row.met)


class TestCitation(unittest.TestCase):
    def test_통과와_제거가_둘_다_0이면_미측정이다(self):
        """0으로 나누지 않는다. 검증을 한 번도 돌리지 않은 상태다."""
        row = collect(verified_excerpts=0, removed_excerpts=0).rows[1]
        self.assertEqual(row.value, UNMEASURED)
        self.assertIsNone(row.met)

    def test_전부_통과하면_100퍼센트_달성이다(self):
        row = collect(verified_excerpts=12, removed_excerpts=0).rows[1]
        self.assertIn("100.0%", row.value)
        self.assertIn("제거 0건", row.value)
        self.assertTrue(row.met)

    def test_하나라도_제거되면_미달이다(self):
        row = collect(verified_excerpts=11, removed_excerpts=1).rows[1]
        self.assertFalse(row.met)
        self.assertIn("제거 1건", row.value)

    def test_제거_기록_목록을_그대로_줘도_건수로_센다(self):
        removed = [
            {"name": "소득 기준", "excerpt": "지어낸 문장", "reason": "not_found"},
            {"name": "", "excerpt": "짧음", "reason": "too_short"},
        ]
        row = collect(verified_excerpts=8, removed_excerpts=removed).rows[1]
        self.assertIn("제거 2건", row.value)
        self.assertFalse(row.met)

    def test_한쪽만_알면_통과율은_미측정으로_둔다(self):
        """분모를 만들 수 없다. 없는 쪽을 0으로 가정하면 100%로 보인다."""
        row = collect(removed_excerpts=0).rows[1]
        self.assertIn(UNMEASURED, row.value)
        self.assertIsNone(row.met)
        self.assertIn("제거 0건", row.value)


class TestJudgmentRow(unittest.TestCase):
    def test_여덟개_통과하면_달성이다(self):
        row = collect(judgment_report=_FakeJudgmentReport(passed=8)).rows[2]
        self.assertIn("8/8", row.value)
        self.assertTrue(row.met)

    def test_일곱개_통과도_달성이다(self):
        row = collect(judgment_report=_FakeJudgmentReport(passed=7)).rows[2]
        self.assertTrue(row.met)

    def test_여섯개면_미달이다(self):
        row = collect(judgment_report=_FakeJudgmentReport(passed=6)).rows[2]
        self.assertFalse(row.met)

    def test_unmet_오판이_있으면_통과_수와_무관하게_미달이다(self):
        row = collect(judgment_report=_FakeJudgmentReport(passed=8, false_unmet=1)).rows[2]
        self.assertFalse(row.met, "받을 수 있는 제도를 접어 버리는 오판이다")
        self.assertIn("unmet 오판 1건", row.value)

    def test_실제_채점기_결과를_그대로_받는다(self):
        """`scoring.Report` 와 실제로 맞물리는지 확인한다."""
        from ai.judgment.cases import CASES
        from ai.judgment.scoring import evaluate
        from ai.judgment.values import BY_AI

        def oracle(exceptions_text, profile, raw_text):
            case = next(
                c
                for c in CASES
                if c.exceptions_text == exceptions_text and c.profile == profile
            )
            return [
                {
                    "name": case.pattern,
                    "result": case.expected_result,
                    "judged_by": BY_AI,
                    "excerpt": case.exceptions_text,
                    "needed_field": case.expected_needed_field,
                }
            ]

        scoring_report = evaluate(oracle)
        row = collect(judgment_report=scoring_report).rows[2]
        self.assertEqual(
            row.met, scoring_report.meets_completion_criteria(), row.value
        )
        self.assertIn(f"{scoring_report.passed}/{scoring_report.total}", row.value)


class TestRecommendationAndLatency(unittest.TestCase):
    def test_추천_정확도_80퍼센트는_달성이다(self):
        row = collect(recommendation_accuracy=12 / 15).rows[3]
        self.assertEqual(row.value, "80.0%")
        self.assertTrue(row.met, "12/15 는 목표에 딱 걸린다. 부동소수로 떨어뜨리지 않는다")

    def test_추천_정확도_79퍼센트는_미달이다(self):
        row = collect(recommendation_accuracy=0.79).rows[3]
        self.assertFalse(row.met)

    def test_응답_시간_두_값이_목표_안이면_달성이다(self):
        row = collect(first_card_seconds=1.2, answer_seconds=12.4).rows[4]
        self.assertIn("1.2초", row.value)
        self.assertIn("12.4초", row.value)
        self.assertTrue(row.met)

    def test_응답_시간_한쪽이_넘으면_미달이다(self):
        row = collect(first_card_seconds=1.0, answer_seconds=18.0).rows[4]
        self.assertFalse(row.met)

    def test_응답_시간_경계값은_달성으로_본다(self):
        row = collect(first_card_seconds=2.0, answer_seconds=15.0).rows[4]
        self.assertTrue(row.met, "목표는 '2초 이내'다")

    def test_응답_시간_한쪽만_측정했으면_달성_여부를_말하지_않는다(self):
        row = collect(first_card_seconds=1.0).rows[4]
        self.assertIsNone(row.met)
        self.assertIn(UNMEASURED, row.value)
        self.assertTrue(row.unmeasured)


class TestNotes(unittest.TestCase):
    def test_평가_케이스가_자리표시면_경고가_들어간다(self):
        report = collect(judgment_report=_FakeJudgmentReport(placeholder_cases=8))
        self.assertTrue(
            any("실제 공고 문장이 아니다" in n for n in report.notes), report.notes
        )

    def test_자리표시가_없으면_그_경고는_없다(self):
        report = collect(judgment_report=_FakeJudgmentReport(placeholder_cases=0))
        self.assertFalse(any("실제 공고 문장이 아니다" in n for n in report.notes))

    def test_용량_부족이_있으면_데모_모드_경고가_들어간다(self):
        report = collect(judge_stats=_FakeJudgeStats(overloaded=2))
        self.assertTrue(any("데모 모드" in n for n in report.notes), report.notes)

    def test_용량_부족이_없으면_그_경고는_없다(self):
        report = collect(judge_stats=_FakeJudgeStats(overloaded=0))
        self.assertFalse(any("데모 모드" in n for n in report.notes))

    def test_실제_JudgeStats와_맞물린다(self):
        from ai.judgment.judge import JudgeStats

        stats = JudgeStats()
        stats.failure_kinds["overloaded"] = 1
        report = collect(judge_stats=stats)
        self.assertTrue(any("데모 모드" in n for n in report.notes), report.notes)


class TestAllMet(unittest.TestCase):
    def _full(self, **overrides):
        kwargs = dict(
            judgment_report=_FakeJudgmentReport(),
            verified_excerpts=12,
            removed_excerpts=0,
            ungrounded_conditions=0,
            recommendation_accuracy=0.85,
            first_card_seconds=1.2,
            answer_seconds=12.4,
        )
        kwargs.update(overrides)
        return collect(**kwargs)

    def test_전부_측정하고_전부_달성하면_True다(self):
        report = self._full()
        self.assertTrue(report.all_met, report.to_table())
        self.assertEqual(report.unmeasured_rows, [])

    def test_하나라도_미달이면_False다(self):
        self.assertFalse(self._full(ungrounded_conditions=1).all_met)

    def test_미측정이_남으면_None이다(self):
        self.assertIsNone(self._full(recommendation_accuracy=None).all_met)

    def test_미달이_확정되면_미측정이_남아도_False다(self):
        """미달은 이미 확정된 사실이라 감추지 않는다."""
        report = self._full(ungrounded_conditions=1, recommendation_accuracy=None)
        self.assertFalse(report.all_met)


class TestRendering(unittest.TestCase):
    def test_표에_지표_다섯_개가_모두_들어간다(self):
        table = collect().to_table()
        for name in (
            "근거 없는 조건 건수",
            "인용 검증 통과율과 제거 건수",
            "판정 평가 J1~J8 결과",
            "추천 정확도",
            "응답 시간",
        ):
            with self.subTest(metric=name):
                self.assertIn(name, table)

    def test_표에_목표가_README_12장_표기로_들어간다(self):
        table = collect().to_table()
        self.assertIn("0건", table)
        self.assertIn("통과율 100%", table)
        self.assertIn("80% 이상", table)
        self.assertIn("첫 카드 2초, AI 설명 15초", table)

    def test_슬라이드에_지표_다섯과_한계_다섯이_같은_장에_있다(self):
        slide = collect().to_slide()
        for row in collect().rows:
            with self.subTest(metric=row.name):
                self.assertIn(row.name, slide)
        self.assertEqual(len(LIMITATIONS), 5)
        for name, text in LIMITATIONS:
            with self.subTest(limitation=name):
                self.assertIn(name, slide)
                self.assertIn(text, slide)

    def test_슬라이드에_한계를_같은_장에_적는_이유가_남는다(self):
        self.assertIn("질의응답에서 유리하다", collect().to_slide())

    def test_슬라이드에_주의_사항이_들어간다(self):
        slide = collect(judge_stats=_FakeJudgeStats(overloaded=1)).to_slide()
        self.assertIn("데모 모드", slide)

    def test_마크다운_표의_줄_수가_맞는다(self):
        lines = collect().to_table().splitlines()
        self.assertEqual(len(lines), 2 + 5)  # 머리글 + 구분선 + 지표 5줄

    def test_notes를_직접_비울_수_있다(self):
        """슬라이드에 주의 사항을 빼야 할 때."""
        report = MetricsReport(rows=list(collect().rows), notes=[])
        self.assertNotIn("### 주의", report.to_slide())


if __name__ == "__main__":
    unittest.main(verbosity=2)
