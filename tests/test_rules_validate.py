"""프로필 값 검증과 저장 정책 조회 (rules/validate.py, rules/saved.py)."""

from __future__ import annotations

import unittest
from datetime import date

import rules
from rules import constants as c
from rules import validate

TODAY = date(2026, 9, 20)
SENTENCE = "만 19세 이상 만 39세 이하 서울에 거주하는 청년이 신청할 수 있다."


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


def policy(policy_id="SEOUL-001", **overrides) -> dict:
    base = {
        "id": policy_id,
        "title": f"정책 {policy_id}",
        "agency": "서울특별시",
        "categories": ["job"],
        "source_url": f"https://example.seoul.go.kr/{policy_id}",
        "apply_url": None,
        "checked_at": "2026-09-19",
        "fetched_at": None,
        "source_kind": c.SOURCE_MANUAL,
        "data_status": c.VERIFIED,
        "apply_start": "2026-09-01",
        "apply_end": None,
        "age_min": 19,
        "age_max": 39,
        "regions": ["서울"],
        "statuses": ["enrolled"],
        "income_max_pct": None,
        "extra_conditions": [],
        "exceptions_text": "",
        "benefit": "",
        "documents": [],
        "steps": [],
        "raw_text": SENTENCE,
        "condition_sources": {},
    }
    base.update(overrides)
    return base


class NormalizeProfileTest(unittest.TestCase):
    def test_정상_프로필은_그대로_통과한다(self):
        normalized, issues = validate.normalize_profile(profile())
        self.assertEqual(issues, [])
        self.assertEqual(normalized["age"], 23)
        self.assertEqual(normalized["categories"], ["job", "living"])

    def test_표에_없는_필드는_버린다(self):
        normalized, issues = validate.normalize_profile({**profile(), "혈액형": "A"})
        self.assertNotIn("혈액형", normalized)
        self.assertTrue(any("표에 없는 필드" in issue for issue in issues))

    def test_허용_값_밖의_값은_미확인으로_되돌린다(self):
        normalized, issues = validate.normalize_profile(profile(housing_type="월세"))
        self.assertIsNone(normalized["housing_type"])
        self.assertTrue(any("housing_type" in issue for issue in issues))

    def test_옛_스키마_신분값도_미확인으로_되돌린다(self):
        normalized, issues = validate.normalize_profile(profile(status="student"))
        self.assertIsNone(normalized["status"])
        self.assertTrue(any("status" in issue for issue in issues))

    def test_region_은_서울로_고정한다(self):
        normalized, issues = validate.normalize_profile(profile(region="outside_seoul"))
        self.assertEqual(normalized["region"], "seoul")
        self.assertTrue(any("region" in issue for issue in issues))

    def test_region_이_없어도_서울로_채운다(self):
        raw = profile()
        raw.pop("region")
        normalized, _ = validate.normalize_profile(raw)
        self.assertEqual(normalized["region"], "seoul")

    def test_나이_경계와_범위_밖(self):
        for age in (15, 39):
            with self.subTest(age=age):
                normalized, issues = validate.normalize_profile(profile(age=age))
                self.assertEqual(normalized["age"], age)
                self.assertEqual(issues, [])
        for age in (14, 40, "23", True, None):
            with self.subTest(age=age):
                normalized, issues = validate.normalize_profile(profile(age=age))
                self.assertNotIn("age", normalized)
                self.assertTrue(issues)

    def test_all_은_단독으로_만든다(self):
        normalized, _ = validate.normalize_profile(profile(categories=["all", "job"]))
        self.assertEqual(normalized["categories"], ["all"])

    def test_중복_분야는_한_번만_남긴다(self):
        normalized, _ = validate.normalize_profile(profile(categories=["job", "job"]))
        self.assertEqual(normalized["categories"], ["job"])

    def test_허용_값_밖의_분야는_걸러내고_기록한다(self):
        normalized, issues = validate.normalize_profile(profile(categories=["job", "창업"]))
        self.assertEqual(normalized["categories"], ["job"])
        self.assertTrue(issues)

    def test_자치구는_비워도_통과한다(self):
        """문서가 선택 입력으로 정했다 (docs/01-glossary-profile.md 2장)."""
        normalized, issues = validate.normalize_profile(profile(district=None))
        self.assertIsNone(normalized["district"])
        self.assertEqual(issues, [])


class BlockingIssueTest(unittest.TestCase):
    def test_판정_세_축이_있으면_막지_않는다(self):
        self.assertEqual(validate.blocking_issues(profile()), [])

    def test_세_축이_비면_막는다(self):
        for field in ("age", "status", "categories"):
            with self.subTest(field=field):
                broken = profile()
                broken.pop(field)
                self.assertTrue(validate.blocking_issues(broken))

    def test_자치구가_없는_것은_막지_않는다(self):
        self.assertEqual(validate.blocking_issues(profile(district=None)), [])


class SavedPolicyTest(unittest.TestCase):
    def test_마감된_정책도_목록에_남고_맨_뒤로_간다(self):
        policies = [
            policy("SEOUL-001", apply_end="2026-09-19"),
            policy("SEOUL-002"),
        ]
        result = rules.evaluate_by_ids(
            profile(), policies, TODAY, ["SEOUL-001", "SEOUL-002"]
        )
        ids = [item["policy_id"] for item in result["policies"]]
        self.assertEqual(ids, ["SEOUL-002", "SEOUL-001"])
        마감 = result["policies"][-1]
        self.assertEqual(마감["data_status"], c.CLOSED)
        self.assertEqual(마감["deadline"]["badge"], "접수 마감")

    def test_관심_분야가_달라도_저장_목록에는_남는다(self):
        policies = [policy("SEOUL-001", categories=["housing"])]
        result = rules.evaluate_by_ids(profile(), policies, TODAY, ["SEOUL-001"])
        self.assertEqual(len(result["policies"]), 1)

    def test_출처가_없으면_저장_목록에서도_뺀다(self):
        policies = [policy("SEOUL-001", source_url=None)]
        result = rules.evaluate_by_ids(profile(), policies, TODAY, ["SEOUL-001"])
        self.assertEqual(result["policies"], [])
        self.assertEqual(result["excluded"][0]["reason"], c.EXCLUDED_NO_SOURCE)

    def test_검수_전_크롤링_데이터는_저장_목록에서도_뺀다(self):
        policies = [policy("SEOUL-001", source_kind=c.SOURCE_CRAWLED)]
        result = rules.evaluate_by_ids(profile(), policies, TODAY, ["SEOUL-001"])
        self.assertEqual(result["excluded"][0]["reason"], c.EXCLUDED_CRAWLED)

    def test_사라진_번호는_제거하고_기록한다(self):
        result = rules.evaluate_by_ids(
            profile(), [policy("SEOUL-001")], TODAY, ["SEOUL-001", "SEOUL-999"]
        )
        self.assertEqual(result["missing_ids"], ["SEOUL-999"])
        self.assertEqual(len(result["policies"]), 1)

    def test_같은_번호를_두_번_저장해도_한_번만_나온다(self):
        result = rules.evaluate_by_ids(
            profile(), [policy("SEOUL-001")], TODAY, ["SEOUL-001", "SEOUL-001"]
        )
        self.assertEqual(len(result["policies"]), 1)

    def test_각주_번호를_1부터_매긴다(self):
        policies = [policy("SEOUL-001", condition_sources={"age": SENTENCE})]
        result = rules.evaluate_by_ids(profile(), policies, TODAY, ["SEOUL-001"])
        ids = [item["footnote_id"] for item in result["policies"][0]["conditions"]]
        self.assertEqual(ids, list(range(1, len(ids) + 1)))

    def test_빈_목록이면_빈_결과다(self):
        result = rules.evaluate_by_ids(profile(), [policy()], TODAY, [])
        self.assertEqual(result["policies"], [])
        self.assertEqual(result["missing_ids"], [])


if __name__ == "__main__":
    unittest.main()
