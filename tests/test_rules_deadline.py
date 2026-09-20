"""마감 배지와 데이터 상태 경계값 (notes/judgment-tables.md 5장).

기준일은 2026-09-20 으로 고정한다. `today` 를 인자로 받으므로 실행 날짜와 무관하게
같은 결과가 나와야 한다.
"""

from __future__ import annotations

import unittest
from datetime import date

from rules import constants as c
from rules import deadline as dl

TODAY = date(2026, 9, 20)


def policy(**overrides) -> dict:
    base = {
        "id": "SEOUL-001",
        "apply_start": "2026-09-01",
        "apply_end": None,
        "checked_at": "2026-09-19",
        "data_status": c.VERIFIED,
    }
    base.update(overrides)
    return base


class ParseDateTest(unittest.TestCase):
    def test_형식이_맞으면_날짜를_돌려준다(self):
        self.assertEqual(dl.parse_date("2026-09-20"), TODAY)

    def test_날짜_객체는_그대로_통과한다(self):
        self.assertEqual(dl.parse_date(TODAY), TODAY)

    def test_빈_값과_잘못된_형식은_None(self):
        for value in (None, "", "2026-13-40", "어제", "20260920"):
            with self.subTest(value=value):
                self.assertIsNone(dl.parse_date(value))


class BadgeBoundaryTest(unittest.TestCase):
    """판정표 5장 경계표를 그대로 검증한다."""

    def test_마감_지남(self):
        result = dl.compute_deadline(policy(apply_end="2026-09-19"), TODAY)
        self.assertEqual(result["d_day"], -1)
        self.assertTrue(dl.is_closed(policy(apply_end="2026-09-19"), TODAY))

    def test_오늘_마감(self):
        result = dl.compute_deadline(policy(apply_end="2026-09-20"), TODAY)
        self.assertEqual(result["badge"], "오늘 마감")
        self.assertEqual(result["d_day"], 0)
        self.assertTrue(result["is_imminent"])

    def test_마감_임박_하한과_상한(self):
        cases = {"2026-09-21": 1, "2026-09-27": 7}
        for apply_end, expected in cases.items():
            with self.subTest(apply_end=apply_end):
                result = dl.compute_deadline(policy(apply_end=apply_end), TODAY)
                self.assertEqual(result["badge"], f"마감 임박 D-{expected}")
                self.assertTrue(result["is_imminent"])

    def test_임박_경계_다음날은_임박이_아니다(self):
        result = dl.compute_deadline(policy(apply_end="2026-09-28"), TODAY)
        self.assertEqual(result["badge"], "D-8")
        self.assertFalse(result["is_imminent"])

    def test_마감일이_없으면_상시_접수(self):
        result = dl.compute_deadline(policy(apply_end=None), TODAY)
        self.assertEqual(result["badge"], "상시 접수")
        self.assertIsNone(result["d_day"])
        self.assertFalse(result["is_imminent"])

    def test_접수_시작이_미래면_접수_예정(self):
        result = dl.compute_deadline(
            policy(apply_start="2026-10-01", apply_end="2026-10-31"), TODAY
        )
        self.assertEqual(result["badge"], "접수 예정 (10.1 시작)")
        self.assertIsNone(result["d_day"])
        self.assertFalse(result["is_imminent"])


class DataStatusTest(unittest.TestCase):
    def test_확인일_14일은_재확인이_아니다(self):
        self.assertFalse(dl.needs_recheck(policy(checked_at="2026-09-06"), TODAY))

    def test_확인일_15일_초과는_재확인(self):
        self.assertTrue(dl.needs_recheck(policy(checked_at="2026-09-05"), TODAY))

    def test_판정_순서는_마감_접수예정_재확인_순이다(self):
        마감 = policy(apply_end="2026-09-19", checked_at="2026-01-01")
        self.assertEqual(dl.resolve_data_status(마감, TODAY), c.CLOSED)

        예정 = policy(apply_start="2026-10-01", apply_end="2026-10-31", checked_at="2026-01-01")
        self.assertEqual(dl.resolve_data_status(예정, TODAY), c.UPCOMING)

        재확인 = policy(apply_end="2026-09-27", checked_at="2026-09-05")
        self.assertEqual(dl.resolve_data_status(재확인, TODAY), c.RECHECK)

        정상 = policy(apply_end="2026-09-27", checked_at="2026-09-19")
        self.assertEqual(dl.resolve_data_status(정상, TODAY), c.VERIFIED)

    def test_재확인은_배지를_덮지_않는다(self):
        겹침 = policy(apply_end="2026-09-27", checked_at="2026-09-05")
        self.assertEqual(dl.compute_deadline(겹침, TODAY)["badge"], "마감 임박 D-7")
        self.assertEqual(dl.resolve_data_status(겹침, TODAY), c.RECHECK)

    def test_저장된_closed_는_날짜와_무관하게_마감이다(self):
        self.assertTrue(dl.is_closed(policy(apply_end=None, data_status=c.CLOSED), TODAY))


if __name__ == "__main__":
    unittest.main()
