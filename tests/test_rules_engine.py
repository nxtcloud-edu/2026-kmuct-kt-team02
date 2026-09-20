"""후보 선정·상태·정렬·미확인 항목과 로더 (notes/judgment-tables.md 4·6~9장)."""

from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

import rules
from rules import candidates, constants as c, loader, sorting

TODAY = date(2026, 9, 20)
RAW = "만 19세 이상 만 39세 이하 서울 거주 청년이 신청할 수 있다. 소득 제한은 없다."


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
        "raw_text": RAW,
        "condition_sources": {},
    }
    base.update(overrides)
    return base


class ExclusionTest(unittest.TestCase):
    def test_후보가_되는_정상_정책(self):
        self.assertIsNone(candidates.exclusion_reason(profile(), policy(), TODAY))

    def test_제외_사유들(self):
        cases = {
            c.EXCLUDED_CLOSED: policy(apply_end="2026-09-19"),
            c.EXCLUDED_CRAWLED: policy(source_kind=c.SOURCE_CRAWLED),
            c.EXCLUDED_NO_SOURCE: policy(source_url=None),
            c.EXCLUDED_NO_RAW_TEXT: policy(raw_text=""),
            c.EXCLUDED_CATEGORY: policy(categories=["housing"]),
        }
        for expected, target in cases.items():
            with self.subTest(expected=expected):
                self.assertEqual(
                    candidates.exclusion_reason(profile(), target, TODAY), expected
                )

    def test_확인일이_없으면_제외한다(self):
        self.assertEqual(
            candidates.exclusion_reason(profile(), policy(checked_at=None), TODAY),
            c.EXCLUDED_NO_SOURCE,
        )

    def test_all_을_고르면_모든_분야가_후보다(self):
        self.assertIsNone(
            candidates.exclusion_reason(
                profile(categories=["all"]), policy(categories=["housing"]), TODAY
            )
        )


class StatusTest(unittest.TestCase):
    def test_상태_결정_규칙(self):
        cases = [
            ([{"result": c.MET}], c.LIKELY),
            ([{"result": c.MET}, {"result": c.UNKNOWN}], c.CHECK),
            ([{"result": c.UNKNOWN}, {"result": c.UNMET}], c.UNLIKELY),
            ([], c.LIKELY),
        ]
        for conditions, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(candidates.decide_status(conditions), expected)

    def test_화면_문구를_코드가_붙인다(self):
        result = rules.evaluate_policies(profile(), [policy()], TODAY)
        self.assertEqual(result["policies"][0]["status_label"], "신청 가능성이 높아요")


class SortingTest(unittest.TestCase):
    def test_상태가_먼저다(self):
        result = rules.evaluate_policies(
            profile(),
            [
                policy("SEOUL-002", income_max_pct=150),
                policy("SEOUL-001"),
            ],
            TODAY,
        )
        statuses = [item["status"] for item in result["policies"]]
        self.assertEqual(statuses, [c.LIKELY, c.CHECK])

    def test_같은_상태에서_임박이_먼저다(self):
        result = rules.evaluate_policies(
            profile(),
            [policy("SEOUL-002"), policy("SEOUL-001", apply_end="2026-09-22")],
            TODAY,
        )
        self.assertEqual(
            [item["policy_id"] for item in result["policies"]], ["SEOUL-001", "SEOUL-002"]
        )

    def test_상시는_마감일_있는_것보다_뒤다(self):
        result = rules.evaluate_policies(
            profile(),
            [policy("SEOUL-001"), policy("SEOUL-002", apply_end="2026-10-31")],
            TODAY,
        )
        self.assertEqual(
            [item["policy_id"] for item in result["policies"]], ["SEOUL-002", "SEOUL-001"]
        )

    def test_접수_예정은_상시보다_뒤다(self):
        result = rules.evaluate_policies(
            profile(),
            [
                policy("SEOUL-002", apply_start="2026-10-01", apply_end="2026-10-31"),
                policy("SEOUL-001"),
            ],
            TODAY,
        )
        self.assertEqual(
            [item["policy_id"] for item in result["policies"]], ["SEOUL-001", "SEOUL-002"]
        )

    def test_관심_분야_일치_수가_많은_것이_먼저다(self):
        result = rules.evaluate_policies(
            profile(),
            [policy("SEOUL-002"), policy("SEOUL-001", categories=["job", "living"])],
            TODAY,
        )
        self.assertEqual(result["policies"][0]["policy_id"], "SEOUL-001")

    def test_같은_입력이면_같은_순서다(self):
        policies = [policy(f"SEOUL-00{index}") for index in range(1, 6)]
        first = rules.evaluate_policies(profile(), policies, TODAY)
        second = rules.evaluate_policies(profile(), list(reversed(policies)), TODAY)
        self.assertEqual(
            [item["policy_id"] for item in first["policies"]],
            [item["policy_id"] for item in second["policies"]],
        )

    def test_기본_표시는_다섯개_접힌_영역은_세개다(self):
        후보 = [policy(f"SEOUL-10{index}") for index in range(7)]
        미충족 = [
            policy(f"SEOUL-20{index}", statuses=["job_seeking"]) for index in range(5)
        ]
        result = rules.evaluate_policies(profile(), 후보 + 미충족, TODAY)
        self.assertEqual(len(result["policies"]), c.BASIC_DISPLAY_LIMIT)
        self.assertEqual(result["hidden_unlikely_count"], c.HIDDEN_UNLIKELY_LIMIT)

    def test_limit_을_줄일_수_있다(self):
        후보 = [policy(f"SEOUL-10{index}") for index in range(7)]
        result = rules.evaluate_policies(profile(), 후보, TODAY, limit=3)
        self.assertEqual(len(result["policies"]), 3)


class NoResultTest(unittest.TestCase):
    def test_likely_와_check_가_없으면_결과_없음이다(self):
        result = rules.evaluate_policies(
            profile(), [policy(statuses=["job_seeking"])], TODAY
        )
        self.assertTrue(result["no_result"])
        self.assertEqual(result["policies"], [])
        self.assertEqual(result["hidden_unlikely_count"], 1)

    def test_후보가_없어도_빈_결과를_돌려준다(self):
        result = rules.evaluate_policies(profile(), [], TODAY)
        self.assertTrue(result["no_result"])
        self.assertEqual(result["excluded"], [])


class FootnoteTest(unittest.TestCase):
    """footnote_id 는 1 이상의 정수 필수 필드다 (server/schemas.py)."""

    def test_모든_조건에_1부터_번호를_매긴다(self):
        result = rules.evaluate_policies(profile(), [policy()], TODAY)
        ids = [item["footnote_id"] for item in result["policies"][0]["conditions"]]
        self.assertEqual(ids, list(range(1, len(ids) + 1)))

    def test_번호는_정책을_넘어가며_이어진다(self):
        result = rules.evaluate_policies(
            profile(), [policy("SEOUL-001"), policy("SEOUL-002")], TODAY
        )
        ids = [
            condition["footnote_id"]
            for evaluation in result["policies"]
            for condition in evaluation["conditions"]
        ]
        self.assertEqual(ids, list(range(1, len(ids) + 1)))

    def test_접힌_영역_번호는_기본_표시_다음부터다(self):
        result = rules.evaluate_policies(
            profile(),
            [policy("SEOUL-001"), policy("SEOUL-002", statuses=["job_seeking"])],
            TODAY,
        )
        basic_ids = [
            condition["footnote_id"]
            for evaluation in result["policies"]
            for condition in evaluation["conditions"]
        ]
        hidden_ids = [
            condition["footnote_id"]
            for evaluation in result["hidden_unlikely"]
            for condition in evaluation["conditions"]
        ]
        self.assertTrue(min(hidden_ids) > max(basic_ids))

    def test_발췌가_있으면_그_조건에만_원문이_붙는다(self):
        sentence = "만 19세 이상 만 39세 이하 서울 거주 청년이 신청할 수 있다."
        result = rules.evaluate_policies(
            profile(), [policy(condition_sources={"age": sentence})], TODAY
        )
        conditions = result["policies"][0]["conditions"]
        with_excerpt = [item for item in conditions if item["excerpt"]]
        self.assertEqual(len(with_excerpt), 1)
        self.assertEqual(with_excerpt[0]["name"], "나이")


class UnknownItemTest(unittest.TestCase):
    def test_미확인_항목을_표시_순서와_함께_내보낸다(self):
        result = rules.evaluate_policies(
            profile(), [policy(income_max_pct=150)], TODAY
        )
        items = result["unknown_items"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["field"], "income_bracket")
        self.assertEqual(items[0]["policy_id"], "SEOUL-001")
        self.assertEqual(items[0]["policy_rank"], 0)
        self.assertEqual(items[0]["policy_title"], "정책 SEOUL-001")

    def test_물을_수_없는_항목은_내보내지_않는다(self):
        result = rules.evaluate_policies(
            profile(), [policy(extra_conditions=["혈액형: A형"])], TODAY
        )
        self.assertEqual(result["unknown_items"], [])

    def test_policy_id_가_비면_내보내지_않는다(self):
        from rules import unknowns

        items = unknowns.unknown_items(
            [{"policy_id": "", "title": "x", "conditions": [
                {"result": c.UNKNOWN, "needed_field": "district"}
            ]}]
        )
        self.assertEqual(items, [])


class LoaderTest(unittest.TestCase):
    def _write(self, payload) -> Path:
        directory = Path(self.temp.name)
        path = directory / "policies.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def test_정상_정책만_통과하고_오류는_기록된다(self):
        path = self._write(
            {"policies": [policy("SEOUL-001"), policy("SEOUL-002", id="", title="")]}
        )
        policies, issues = loader.load_policies(path)
        self.assertEqual([item["id"] for item in policies], ["SEOUL-001"])
        self.assertTrue(issues)

    def test_검증_항목들(self):
        cases = {
            "id 형식": policy(id="SEOUL-1"),
            "categories 비어 있음": policy(categories=[]),
            "statuses 허용 값": policy(statuses=["student"]),
            "data_status 허용 값": policy(data_status="검수완료"),
            "source_kind 허용 값": policy(source_kind="사람"),
            "날짜 형식": policy(apply_end="2026/09/30"),
            "apply_start 가 늦음": policy(apply_start="2026-10-01", apply_end="2026-09-30"),
            "age_min 이 큼": policy(age_min=40, age_max=20),
            "all 조합": policy(categories=["all", "job"]),
        }
        for label, target in cases.items():
            with self.subTest(label=label):
                policies, issues = loader.load_policies(self._write({"policies": [target]}))
                self.assertEqual(policies, [])
                self.assertTrue(issues)

    def test_condition_sources_문장이_raw_text_에_없으면_거른다(self):
        target = policy(condition_sources={"age": "원문에 없는 문장이다"})
        policies, issues = loader.load_policies(self._write({"policies": [target]}))
        self.assertEqual(policies, [])
        self.assertTrue(any("raw_text 안에 없음" in issue for issue in issues))

    def test_id_중복은_뒤엣것을_거른다(self):
        path = self._write({"policies": [policy("SEOUL-001"), policy("SEOUL-001")]})
        policies, issues = loader.load_policies(path)
        self.assertEqual(len(policies), 1)
        self.assertTrue(any("중복" in issue for issue in issues))

    def test_파일이_없으면_예외를_올리지_않는다(self):
        policies, issues = loader.load_policies(Path(self.temp.name) / "없는파일.json")
        self.assertEqual(policies, [])
        self.assertTrue(issues)

    def test_json_이_깨져도_예외를_올리지_않는다(self):
        path = Path(self.temp.name) / "broken.json"
        path.write_text("{", encoding="utf-8")
        policies, issues = loader.load_policies(path)
        self.assertEqual(policies, [])
        self.assertTrue(issues)

    def test_검수_완료_수를_센다(self):
        rows = [
            policy("SEOUL-001"),
            policy("SEOUL-002", data_status=c.RECHECK),
            policy("SEOUL-003", source_kind=c.SOURCE_CRAWLED),
        ]
        self.assertEqual(loader.count_verified(rows), 1)


class RepositoryDataTest(unittest.TestCase):
    """저장소에 실제로 들어 있는 데이터로 돌려본다."""

    def test_미검수_staging_은_전건_후보에서_빠진다(self):
        path = Path(__file__).resolve().parents[1] / "data" / "policies" / "staging.json"
        policies, issues = loader.load_policies(path)
        self.assertEqual(len(policies), 27)
        self.assertEqual(issues, [])

        result = rules.evaluate_policies(profile(), policies, TODAY)
        self.assertTrue(result["no_result"])
        reasons = {item["reason"] for item in result["excluded"]}
        self.assertEqual(reasons, {c.EXCLUDED_NO_SOURCE})

    def test_대표_프로필_다섯개를_모두_돌린다(self):
        root = Path(__file__).resolve().parents[1]
        policies, _ = loader.load_policies(root / "data" / "policies" / "staging.json")
        entries = json.loads(
            (root / "data" / "profiles" / "profiles.json").read_text(encoding="utf-8")
        )["profiles"]
        for entry in entries:
            with self.subTest(profile=entry["id"]):
                result = rules.evaluate_policies(entry["profile"], policies, TODAY)
                self.assertIsInstance(result["policies"], list)
                self.assertEqual(len(result["excluded"]), 27)


if __name__ == "__main__":
    unittest.main()
