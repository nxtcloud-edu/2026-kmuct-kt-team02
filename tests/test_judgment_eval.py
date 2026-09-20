"""판정 평가 하네스 테스트.

판정기가 아니라 **채점기**를 검증한다.
채점이 틀리면 지표 전체가 틀리고, 발표에서 잘못된 수치를 말하게 된다.

스텁 판정기로 "완벽한 경우", "위험하게 틀린 경우", "환각한 경우",
"터진 경우"를 만들어 채점이 각각을 제대로 분류하는지 본다.
"""

import unittest
from typing import Dict, List

from ai.citation import raw_text_covers
from ai.judgment.cases import (
    ASK_NOTICE,
    BASE_PROFILE,
    CASES,
    MET,
    UNKNOWN,
    UNMET,
)
from ai.judgment.evaluate import evaluate


def _find_case(exceptions_text: str, profile: Dict[str, object]):
    for case in CASES:
        if case.exceptions_text == exceptions_text and case.profile == profile:
            return case
    raise AssertionError("평가 케이스를 찾지 못했다")


def oracle_judge(exceptions_text, profile, raw_text) -> List[Dict[str, object]]:
    """항상 기대대로 답하는 판정기. 채점기가 통과를 통과로 세는지 확인용."""
    case = _find_case(exceptions_text, profile)
    return [
        {
            "summary": case.pattern,
            "result": case.expected_result,
            "excerpt": case.exceptions_text,
            "needed_field": case.expected_needed_field,
        }
    ]


def always_unmet_judge(exceptions_text, profile, raw_text) -> List[Dict[str, object]]:
    """무엇이든 미충족이라고 하는 판정기. 가장 위험한 실패 유형."""
    return [
        {
            "summary": "제외 대상",
            "result": UNMET,
            "excerpt": exceptions_text,
            "needed_field": None,
        }
    ]


def hallucinating_judge(exceptions_text, profile, raw_text) -> List[Dict[str, object]]:
    """원문에 없는 근거를 대는 판정기. 인용 검증이 막아야 한다."""
    return [
        {
            "summary": "소득 기준 초과",
            "result": UNMET,
            "excerpt": "가구 소득이 기준 중위소득 150%를 초과하는 자는 제외합니다",
            "needed_field": None,
        }
    ]


def crashing_judge(exceptions_text, profile, raw_text):
    raise ValueError("JSON 파싱 실패")


def empty_judge(exceptions_text, profile, raw_text):
    """예외 조건을 하나도 못 뽑은 경우."""
    return []


class TestCaseData(unittest.TestCase):
    """케이스 데이터 자체가 성립하는지."""

    def test_케이스는_여덟개이고_id가_J1부터_J8까지다(self):
        self.assertEqual([c.id for c in CASES], [f"J{i}" for i in range(1, 9)])

    def test_모든_케이스의_예외_문장이_원문_안에_있다(self):
        """이 불변식이 깨지면 그 케이스는 무조건 not_found 로 떨어진다."""
        for case in CASES:
            with self.subTest(case=case.id):
                self.assertTrue(
                    raw_text_covers(case.exceptions_text, case.raw_text),
                    f"{case.id}: exceptions_text 가 raw_text 안에 없다",
                )

    def test_발췌_최소_길이를_넘는다(self):
        for case in CASES:
            with self.subTest(case=case.id):
                self.assertGreaterEqual(len(case.exceptions_text), 10)

    def test_프로필_키가_허용된_항목이다(self):
        """설계서 1-2 표에 없는 항목을 만들어 쓰면 다른 역할과 어긋난다."""
        allowed = set(BASE_PROFILE) | {
            "주거 형태",
            "서울 거주 기간",
            "남은 학기",
            "구직 기간",
            "고용보험 가입 이력",
            "다른 지원 수혜 중",
            "가구원 수",
            "직전 학기 성적",
        }
        for case in CASES:
            with self.subTest(case=case.id):
                self.assertTrue(
                    set(case.profile) <= allowed,
                    f"{case.id}: 허용되지 않은 항목 {set(case.profile) - allowed}",
                )

    def test_기대_결과가_세_값_중_하나다(self):
        for case in CASES:
            with self.subTest(case=case.id):
                self.assertIn(case.expected_result, {MET, UNMET, UNKNOWN})

    def test_미확인이_아니면_필요_항목을_기대하지_않는다(self):
        for case in CASES:
            if case.expected_result != UNKNOWN:
                with self.subTest(case=case.id):
                    self.assertIsNone(case.expected_needed_field)

    def test_미확인이면_필요_항목을_기대한다(self):
        for case in CASES:
            if case.expected_result == UNKNOWN:
                with self.subTest(case=case.id):
                    self.assertIsNotNone(case.expected_needed_field)


class TestScoring(unittest.TestCase):
    """채점기가 각 실패 유형을 제대로 분류하는지."""

    def test_완벽한_판정기는_전부_통과하고_완료_기준을_만족한다(self):
        report = evaluate(oracle_judge)
        self.assertEqual(report.passed, report.total, report.detail())
        self.assertEqual(report.false_unmet, 0)
        self.assertEqual(report.removed_excerpt_count, 0)
        self.assertTrue(report.meets_completion_criteria())

    def test_전부_미충족이라고_하면_미충족_오판이_잡힌다(self):
        report = evaluate(always_unmet_judge)
        # J1 만 기대가 미충족이므로 나머지 7건이 오판이다.
        self.assertEqual(report.false_unmet, 7, report.detail())
        self.assertFalse(
            report.meets_completion_criteria(),
            "미충족 오판이 있으면 통과 수와 무관하게 완료 기준 미달이어야 한다",
        )

    def test_환각_발췌는_인용_검증에서_제거되고_미확인으로_내려간다(self):
        report = evaluate(hallucinating_judge)
        self.assertEqual(report.removed_excerpt_count, report.total)
        self.assertEqual(
            report.false_unmet,
            0,
            "환각으로 낸 미충족은 인용 검증이 미확인으로 내려야 한다",
        )
        for outcome in report.outcomes:
            with self.subTest(case=outcome.case.id):
                self.assertEqual(outcome.actual_result, UNKNOWN)

    def test_판정기가_터져도_채점이_계속된다(self):
        report = evaluate(crashing_judge)
        self.assertEqual(report.errors, report.total)
        self.assertEqual(report.false_unmet, 0, "실패는 미확인으로 떨어져야 안전하다")

    def test_조건을_못_뽑으면_미확인으로_본다(self):
        report = evaluate(empty_judge)
        for outcome in report.outcomes:
            with self.subTest(case=outcome.case.id):
                self.assertEqual(outcome.actual_result, UNKNOWN)

    def test_조건이_여러개면_가장_심각한_것을_대표로_삼는다(self):
        """실제 카드에 나타날 상태와 일치해야 한다 (설계서 1-3)."""

        def multi_judge(exceptions_text, profile, raw_text):
            return [
                {
                    "summary": "충족 조건",
                    "result": MET,
                    "excerpt": exceptions_text,
                    "needed_field": None,
                },
                {
                    "summary": "제외 대상",
                    "result": UNMET,
                    "excerpt": exceptions_text,
                    "needed_field": None,
                },
            ]

        report = evaluate(multi_judge)
        for outcome in report.outcomes:
            with self.subTest(case=outcome.case.id):
                self.assertEqual(outcome.actual_result, UNMET)

    def test_결과만_맞고_필요_항목이_틀리면_구분해_센다(self):
        def wrong_field_judge(exceptions_text, profile, raw_text):
            case = _find_case(exceptions_text, profile)
            return [
                {
                    "summary": case.pattern,
                    "result": case.expected_result,
                    "excerpt": case.exceptions_text,
                    "needed_field": "가구원 수" if case.expected_needed_field else None,
                }
            ]

        report = evaluate(wrong_field_judge)
        unknown_cases = [c for c in CASES if c.expected_result == UNKNOWN]
        # "가구원 수" 를 기대하는 케이스는 없으므로 미확인 케이스 전부가 여기 해당한다.
        self.assertEqual(report.result_only_passed, len(unknown_cases), report.detail())
        self.assertEqual(report.false_unmet, 0)

    def test_요약에_핵심_수치가_들어간다(self):
        summary = evaluate(oracle_judge).summary()
        self.assertIn("미충족 오판", summary)
        self.assertIn("완료 기준", summary)

    def test_실제_문장_미교체를_요약에서_알린다(self):
        """지표를 발표에 쓸 때 참고용임을 놓치지 않도록."""
        report = evaluate(oracle_judge)
        if report.placeholder_cases:
            self.assertIn("실제 공고 문장이 아님", report.summary())


class TestSafeDirection(unittest.TestCase):
    """안전한 방향의 오차와 위험한 방향의 오차를 구분하는지."""

    def test_미충족을_미확인으로_본_것은_안전한_오차로_센다(self):
        def cautious_judge(exceptions_text, profile, raw_text):
            case = _find_case(exceptions_text, profile)
            result = UNKNOWN if case.expected_result == UNMET else case.expected_result
            return [
                {
                    "summary": case.pattern,
                    "result": result,
                    "excerpt": case.exceptions_text,
                    "needed_field": case.expected_needed_field
                    or (ASK_NOTICE if result == UNKNOWN else None),
                }
            ]

        report = evaluate(cautious_judge)
        self.assertEqual(report.safe_misses, 1, report.detail())
        self.assertEqual(report.false_unmet, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
