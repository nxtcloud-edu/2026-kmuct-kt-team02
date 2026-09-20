"""판정 평가 하네스 테스트 (`ai/judgment/cases.py`, `scoring.py`).

판정기가 아니라 **채점기**를 검증한다.
채점이 틀리면 지표 전체가 틀리고, 발표에서 잘못된 수치를 말하게 된다.

스텁 판정기로 "완벽한 경우", "위험하게 틀린 경우", "환각한 경우",
"터진 경우"를 만들어 채점이 각각을 제대로 분류하는지 본다.
"""

import unittest
from typing import Dict, List

from ai.judgment.cases import BASE_PROFILE, CASES
from ai.judgment.citation import raw_text_covers
from ai.judgment.scoring import evaluate
from ai.judgment.values import (
    ALL_PROFILE_FIELDS,
    ASK_NOTICE,
    BASE_FIELD_VALUES,
    BY_AI,
    EXTRA_FIELD_VALUES,
    MET,
    MIN_EXCERPT_LEN,
    UNKNOWN,
    UNMET,
)


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
            "name": case.pattern,
            "result": case.expected_result,
            "judged_by": BY_AI,
            "excerpt": case.exceptions_text,
            "needed_field": case.expected_needed_field,
        }
    ]


def always_unmet_judge(exceptions_text, profile, raw_text) -> List[Dict[str, object]]:
    """무엇이든 `unmet` 이라고 하는 판정기. 가장 위험한 실패 유형."""
    return [
        {
            "name": "제외 대상",
            "result": UNMET,
            "judged_by": BY_AI,
            "excerpt": exceptions_text,
            "needed_field": None,
        }
    ]


def hallucinating_judge(exceptions_text, profile, raw_text) -> List[Dict[str, object]]:
    """원문에 없는 근거를 대는 판정기. 인용 검증이 막아야 한다."""
    return [
        {
            "name": "소득 기준 초과",
            "result": UNMET,
            "judged_by": BY_AI,
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
                self.assertGreaterEqual(len(case.exceptions_text), MIN_EXCERPT_LEN)

    def test_프로필_키가_허용된_항목이다(self):
        """문서 표에 없는 항목을 만들어 쓰면 다른 역할과 어긋난다."""
        for case in CASES:
            with self.subTest(case=case.id):
                unknown_keys = set(case.profile) - ALL_PROFILE_FIELDS
                self.assertEqual(
                    unknown_keys, set(), f"{case.id}: 허용되지 않은 항목 {unknown_keys}"
                )

    def test_프로필_값이_허용된_값이다(self):
        """`docs/01-glossary-profile.md` 2~3장 허용 값만 쓴다."""
        allowed = dict(BASE_FIELD_VALUES)
        allowed.update(EXTRA_FIELD_VALUES)
        for case in CASES:
            for key, value in case.profile.items():
                choices = allowed.get(key, ())
                if not choices or value is None:
                    continue  # age, district 처럼 목록이 없는 항목
                values = value if isinstance(value, list) else [value]
                for item in values:
                    with self.subTest(case=case.id, field=key, value=item):
                        self.assertIn(item, choices)

    def test_기대_결과가_세_값_중_하나다(self):
        for case in CASES:
            with self.subTest(case=case.id):
                self.assertIn(case.expected_result, {MET, UNMET, UNKNOWN})

    def test_unknown이_아니면_필요_항목을_기대하지_않는다(self):
        for case in CASES:
            if case.expected_result != UNKNOWN:
                with self.subTest(case=case.id):
                    self.assertIsNone(case.expected_needed_field)

    def test_unknown이면_필요_항목을_기대한다(self):
        for case in CASES:
            if case.expected_result == UNKNOWN:
                with self.subTest(case=case.id):
                    self.assertIsNotNone(case.expected_needed_field)

    def test_기대하는_필요_항목이_허용_목록_안에_있다(self):
        allowed = set(EXTRA_FIELD_VALUES) | {ASK_NOTICE}
        for case in CASES:
            if case.expected_needed_field:
                with self.subTest(case=case.id):
                    self.assertIn(case.expected_needed_field, allowed)


class TestScoring(unittest.TestCase):
    """채점기가 각 실패 유형을 제대로 분류하는지."""

    def test_완벽한_판정기는_전부_통과하고_완료_기준을_만족한다(self):
        report = evaluate(oracle_judge)
        self.assertEqual(report.passed, report.total, report.detail())
        self.assertEqual(report.false_unmet, 0)
        self.assertEqual(report.removed_excerpt_count, 0)
        self.assertTrue(report.meets_completion_criteria())

    def test_전부_unmet이라고_하면_오판이_잡힌다(self):
        report = evaluate(always_unmet_judge)
        # J1 만 기대가 unmet 이므로 나머지 7건이 오판이다.
        self.assertEqual(report.false_unmet, 7, report.detail())
        self.assertFalse(
            report.meets_completion_criteria(),
            "unmet 오판이 있으면 통과 수와 무관하게 완료 기준 미달이어야 한다",
        )

    def test_환각_발췌는_인용_검증에서_제거되고_unknown으로_내려간다(self):
        report = evaluate(hallucinating_judge)
        self.assertEqual(report.removed_excerpt_count, report.total)
        self.assertEqual(
            report.false_unmet,
            0,
            "환각으로 낸 unmet 은 인용 검증이 unknown 으로 내려야 한다",
        )
        for outcome in report.outcomes:
            with self.subTest(case=outcome.case.id):
                self.assertEqual(outcome.actual_result, UNKNOWN)

    def test_판정기가_터져도_채점이_계속된다(self):
        report = evaluate(crashing_judge)
        self.assertEqual(report.errors, report.total)
        self.assertEqual(report.false_unmet, 0, "실패는 unknown 으로 떨어져야 안전하다")

    def test_조건을_못_뽑으면_unknown으로_본다(self):
        report = evaluate(empty_judge)
        for outcome in report.outcomes:
            with self.subTest(case=outcome.case.id):
                self.assertEqual(outcome.actual_result, UNKNOWN)

    def test_조건이_여러개면_가장_심각한_것을_대표로_삼는다(self):
        """실제 카드에 나타날 상태와 일치해야 한다."""

        def multi_judge(exceptions_text, profile, raw_text):
            return [
                {
                    "name": "충족 조건",
                    "result": MET,
                    "judged_by": BY_AI,
                    "excerpt": exceptions_text,
                    "needed_field": None,
                },
                {
                    "name": "제외 대상",
                    "result": UNMET,
                    "judged_by": BY_AI,
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
                    "name": case.pattern,
                    "result": case.expected_result,
                    "judged_by": BY_AI,
                    "excerpt": case.exceptions_text,
                    "needed_field": "household_size" if case.expected_needed_field else None,
                }
            ]

        report = evaluate(wrong_field_judge)
        unknown_cases = [c for c in CASES if c.expected_result == UNKNOWN]
        self.assertEqual(report.result_only_passed, len(unknown_cases), report.detail())
        self.assertEqual(report.false_unmet, 0)

    def test_요약에_핵심_수치가_들어간다(self):
        summary = evaluate(oracle_judge).summary()
        self.assertIn("unmet 오판", summary)
        self.assertIn("완료 기준", summary)

    def test_실제_문장_미교체를_요약에서_알린다(self):
        """지표를 발표에 쓸 때 참고용임을 놓치지 않도록."""
        report = evaluate(oracle_judge)
        if report.placeholder_cases:
            self.assertIn("실제 공고 문장이 아님", report.summary())


class TestSafeDirection(unittest.TestCase):
    """안전한 방향의 오차와 위험한 방향의 오차를 구분하는지."""

    def test_unmet을_unknown으로_본_것은_안전한_오차로_센다(self):
        def cautious_judge(exceptions_text, profile, raw_text):
            case = _find_case(exceptions_text, profile)
            result = UNKNOWN if case.expected_result == UNMET else case.expected_result
            return [
                {
                    "name": case.pattern,
                    "result": result,
                    "judged_by": BY_AI,
                    "excerpt": case.exceptions_text,
                    "needed_field": case.expected_needed_field
                    or (ASK_NOTICE if result == UNKNOWN else None),
                }
            ]

        report = evaluate(cautious_judge)
        self.assertEqual(report.safe_misses, 1, report.detail())
        self.assertEqual(report.false_unmet, 0)


class TestProfileFieldsSanity(unittest.TestCase):
    """`values.py` 표가 문서와 어긋나지 않는지."""

    def test_기본_프로필이_허용_값을_쓴다(self):
        for key, value in BASE_PROFILE.items():
            choices = BASE_FIELD_VALUES.get(key, ())
            if not choices or value is None:
                continue
            values = value if isinstance(value, list) else [value]
            for item in values:
                with self.subTest(field=key, value=item):
                    self.assertIn(item, choices)

    def test_추가_항목은_여덟개다(self):
        self.assertEqual(len(EXTRA_FIELD_VALUES), 8)


if __name__ == "__main__":
    unittest.main(verbosity=2)
