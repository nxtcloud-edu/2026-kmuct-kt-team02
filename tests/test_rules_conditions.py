"""규칙 조건 판정 (notes/judgment-tables.md 1·3장)."""

from __future__ import annotations

import unittest

from rules import conditions as cond
from rules import constants as c

RAW = "만 19세 이상 만 34세 이하 서울 거주 미취업 청년이 신청할 수 있다. 가구 소득은 기준 중위소득 150% 이하여야 한다."


def profile(**overrides) -> dict:
    base = {
        "age": 23,
        "region": "seoul",
        "district": None,
        "status": "enrolled",
        "categories": ["job", "living"],
        "income_bracket": "unknown",
    }
    base.update(overrides)
    return base


def policy(**overrides) -> dict:
    base = {
        "id": "SEOUL-001",
        "source_url": "https://example.seoul.go.kr/notice/1",
        "age_min": 19,
        "age_max": 34,
        "regions": ["서울"],
        "statuses": ["enrolled"],
        "income_max_pct": None,
        "extra_conditions": [],
        "raw_text": RAW,
        "condition_sources": {},
    }
    base.update(overrides)
    return base


def result_of(conditions: list[dict], name: str) -> str | None:
    for condition in conditions:
        if condition["name"] == name:
            return condition["result"]
    return None


class AgeConditionTest(unittest.TestCase):
    def test_경계값_네_가지(self):
        cases = {18: c.UNMET, 19: c.MET, 34: c.MET, 35: c.UNMET}
        for age, expected in cases.items():
            with self.subTest(age=age):
                condition, _ = cond.evaluate_age(profile(age=age), policy())
                self.assertEqual(condition["result"], expected)

    def test_나이_제한이_없으면_조건을_만들지_않는다(self):
        self.assertIsNone(
            cond.evaluate_age(profile(), policy(age_min=None, age_max=None))
        )

    def test_한쪽만_있으면_그쪽만_본다(self):
        condition, _ = cond.evaluate_age(profile(age=70), policy(age_min=15, age_max=None))
        self.assertEqual(condition["result"], c.MET)

    def test_unknown_분기는_없다(self):
        for age in (19, 34):
            with self.subTest(age=age):
                condition, _ = cond.evaluate_age(profile(age=age), policy())
                self.assertNotEqual(condition["result"], c.UNKNOWN)


class RegionConditionTest(unittest.TestCase):
    def test_서울과_전국은_충족(self):
        for regions in (["서울"], ["전국"], ["서울", "전국"]):
            with self.subTest(regions=regions):
                condition, _ = cond.evaluate_region(profile(), policy(regions=regions))
                self.assertEqual(condition["result"], c.MET)

    def test_자치구_한정_일치는_충족(self):
        condition, _ = cond.evaluate_region(
            profile(district="마포구"), policy(regions=["마포구"])
        )
        self.assertEqual(condition["result"], c.MET)

    def test_자치구_한정_불일치는_미충족(self):
        condition, _ = cond.evaluate_region(
            profile(district="성북구"), policy(regions=["마포구"])
        )
        self.assertEqual(condition["result"], c.UNMET)

    def test_자치구_미선택이면_미확인이고_자치구를_묻는다(self):
        condition, _ = cond.evaluate_region(
            profile(district=None), policy(regions=["마포구"])
        )
        self.assertEqual(condition["result"], c.UNKNOWN)
        self.assertEqual(condition["needed_field"], "district")


class StatusConditionTest(unittest.TestCase):
    def test_포함되면_충족_미포함이면_미충족(self):
        condition, _ = cond.evaluate_status(profile(status="enrolled"), policy())
        self.assertEqual(condition["result"], c.MET)
        condition, _ = cond.evaluate_status(profile(status="job_seeking"), policy())
        self.assertEqual(condition["result"], c.UNMET)

    def test_statuses_가_비면_조건을_만들지_않는다(self):
        self.assertIsNone(cond.evaluate_status(profile(), policy(statuses=[])))


class IncomeConditionTest(unittest.TestCase):
    """판정표 3장 검산표 6행."""

    def test_검산표(self):
        cases = [
            (150, "100_150", c.MET),
            (120, "100_150", c.UNKNOWN),
            (150, "over_150", c.UNMET),
            (100, "under_50", c.MET),
            (120, "unknown", c.UNKNOWN),
        ]
        for limit, bracket, expected in cases:
            with self.subTest(limit=limit, bracket=bracket):
                condition, _ = cond.evaluate_income(
                    profile(income_bracket=bracket), policy(income_max_pct=limit)
                )
                self.assertEqual(condition["result"], expected)

    def test_소득_조건이_없으면_조건을_만들지_않는다(self):
        self.assertIsNone(
            cond.evaluate_income(profile(income_bracket="unknown"), policy(income_max_pct=None))
        )

    def test_미확인일_때_소득을_묻는다(self):
        condition, _ = cond.evaluate_income(
            profile(income_bracket="unknown"), policy(income_max_pct=150)
        )
        self.assertEqual(condition["needed_field"], "income_bracket")


class ExtraConditionTest(unittest.TestCase):
    def test_답이_같으면_충족_다르면_미충족(self):
        target = policy(extra_conditions=["주거 형태: 월세"])
        conditions, _ = cond.evaluate_extra_conditions(
            {**profile(), "housing_type": "monthly_rent"}, target
        )
        self.assertEqual(conditions[0]["result"], c.MET)

        conditions, _ = cond.evaluate_extra_conditions(
            {**profile(), "housing_type": "jeonse"}, target
        )
        self.assertEqual(conditions[0]["result"], c.UNMET)

    def test_답이_없으면_미확인이고_해당_항목을_묻는다(self):
        conditions, _ = cond.evaluate_extra_conditions(
            profile(), policy(extra_conditions=["주거 형태: 월세"])
        )
        self.assertEqual(conditions[0]["result"], c.UNKNOWN)
        self.assertEqual(conditions[0]["needed_field"], "housing_type")

    def test_해석할_수_없는_값은_미충족으로_단정하지_않는다(self):
        conditions, issues = cond.evaluate_extra_conditions(
            {**profile(), "housing_type": "monthly_rent"},
            policy(extra_conditions=["주거 형태: 전세 또는 월세"]),
        )
        self.assertEqual(conditions[0]["result"], c.UNKNOWN)
        self.assertTrue(issues)

    def test_표에_없는_항목은_조건을_만들지_않고_기록한다(self):
        conditions, issues = cond.evaluate_extra_conditions(
            profile(), policy(extra_conditions=["혈액형: A형"])
        )
        self.assertEqual(conditions, [])
        self.assertTrue(issues)

    def test_형식이_아니면_기록만_남긴다(self):
        conditions, issues = cond.evaluate_extra_conditions(
            profile(), policy(extra_conditions=["주거 형태"])
        )
        self.assertEqual(conditions, [])
        self.assertTrue(issues)


class ExcerptTest(unittest.TestCase):
    def test_근거_문장이_없으면_발췌는_비고_기록이_남는다(self):
        condition, issue = cond.evaluate_age(profile(), policy())
        self.assertIsNone(condition["excerpt"])
        self.assertIn("근거 문장 없음", issue)

    def test_열자_미만_발췌는_쓰지_않는다(self):
        target = policy(raw_text="만 19세 이상", condition_sources={"age": "만 19세 이상"})
        condition, issue = cond.evaluate_age(profile(), target)
        self.assertIsNone(condition["excerpt"])
        self.assertIn("10자 미만", issue)

    def test_길이가_맞으면_발췌를_연결한다(self):
        sentence = "만 19세 이상 만 34세 이하 서울 거주 미취업 청년이 신청할 수 있다."
        target = policy(condition_sources={"age": sentence})
        condition, issue = cond.evaluate_age(profile(), target)
        self.assertEqual(condition["excerpt"], sentence)
        self.assertIsNone(issue)

    def test_150자_초과_발췌는_쓰지_않는다(self):
        long_sentence = "가" * 151
        target = policy(raw_text=long_sentence, condition_sources={"age": long_sentence})
        condition, issue = cond.evaluate_age(profile(), target)
        self.assertIsNone(condition["excerpt"])
        self.assertIn("150자 초과", issue)


class ConditionOrderTest(unittest.TestCase):
    def test_미충족_미확인_충족_순으로_정렬한다(self):
        conditions, _ = cond.evaluate_conditions(
            profile(status="job_seeking", income_bracket="unknown"),
            policy(income_max_pct=150),
        )
        results = [condition["result"] for condition in conditions]
        self.assertEqual(results, sorted(results, key={c.UNMET: 0, c.UNKNOWN: 1, c.MET: 2}.get))
        self.assertEqual(results[0], c.UNMET)


if __name__ == "__main__":
    unittest.main()
