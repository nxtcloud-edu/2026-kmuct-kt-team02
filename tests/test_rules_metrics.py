"""데이터 조건 점검과 채점 (rules/metrics.py)."""

from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from rules import constants as c
from rules import metrics

TODAY = date(2026, 9, 20)
SENTENCE = "만 19세 이상 만 39세 이하 서울에 거주하는 청년이 신청할 수 있다."


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
        "statuses": [],
        "income_max_pct": None,
        "extra_conditions": [],
        "exceptions_text": "",
        "benefit": "",
        "documents": [],
        "steps": [],
        "raw_text": SENTENCE,
        "condition_sources": {"age": SENTENCE},
    }
    base.update(overrides)
    return base


def profile_entry(profile_id="P1", demo=False, **overrides) -> dict:
    base = {
        "age": 23,
        "region": "seoul",
        "district": None,
        "status": "enrolled",
        "categories": ["job", "living"],
        "income_bracket": "unknown",
    }
    base.update(overrides)
    return {"id": profile_id, "데모_프로필": demo, "profile": base}


def find(report: dict, name: str) -> dict:
    return next(item for item in report["checks"] if item["항목"] == name)


class IncomeFreeTest(unittest.TestCase):
    def test_소득_상한이_있으면_소득_조건_있음(self):
        self.assertFalse(metrics._income_free(policy(income_max_pct=150)))

    def test_상한이_없고_예외_문장도_없으면_소득_무관(self):
        self.assertTrue(metrics._income_free(policy()))

    def test_상한이_없어도_예외_문장에_소득이_있으면_무관이_아니다(self):
        """중위소득 %가 아닌 소득 기준은 AI B 가 판정한다. 소득 무관으로 세면 안 된다."""
        target = policy(exceptions_text="연소득 5천만원 이하인 무주택 세대주")
        self.assertFalse(metrics._income_free(target))


class CheckDataConditionsTest(unittest.TestCase):
    def test_검수_통과_수를_센다(self):
        policies = [policy("SEOUL-001"), policy("SEOUL-002", data_status=c.CLOSED)]
        report = metrics.check_data_conditions(policies, [profile_entry()], TODAY)
        self.assertEqual(find(report, "검수 통과 정책 수")["값"], 1)

    def test_접수_중과_상시만_센다(self):
        policies = [
            policy("SEOUL-001"),
            policy("SEOUL-002", apply_end="2026-09-30"),
            policy("SEOUL-003", apply_end="2026-09-19"),
            policy("SEOUL-004", apply_start="2026-10-01", apply_end="2026-10-31"),
        ]
        report = metrics.check_data_conditions(policies, [profile_entry()], TODAY)
        self.assertEqual(find(report, "접수 중·상시 접수")["값"], 2)
        self.assertEqual(
            report["상세"]["접수_중_상시"], ["SEOUL-001", "SEOUL-002"]
        )

    def test_소득_무관_비율을_계산한다(self):
        policies = [policy("SEOUL-001"), policy("SEOUL-002", income_max_pct=150)]
        report = metrics.check_data_conditions(policies, [profile_entry()], TODAY)
        self.assertIn("50%", find(report, "소득 조건 없는 정책 비율")["값"])
        self.assertTrue(find(report, "소득 조건 없는 정책 비율")["충족"])

    def test_추가_항목_조건을_센다(self):
        policies = [policy("SEOUL-001", extra_conditions=["주거 형태: 월세"])]
        report = metrics.check_data_conditions(policies, [profile_entry()], TODAY)
        self.assertEqual(find(report, "추가 항목 조건이 있는 정책")["값"], 1)

    def test_데모_프로필의_unlikely_를_센다(self):
        policies = [
            policy("SEOUL-001"),
            policy("SEOUL-002", age_min=30, age_max=39),
        ]
        profiles = [profile_entry("P1", demo=True), profile_entry("P2")]
        report = metrics.check_data_conditions(policies, profiles, TODAY)
        self.assertEqual(find(report, "데모 프로필에서 unlikely")["값"], 1)

    def test_데모_프로필이_없으면_0으로_둔다(self):
        report = metrics.check_data_conditions([policy()], [profile_entry()], TODAY)
        self.assertEqual(find(report, "데모 프로필에서 unlikely")["값"], 0)

    def test_분야별_건수를_센다(self):
        policies = [
            policy("SEOUL-001", categories=["job", "living"]),
            policy("SEOUL-002", categories=["living"]),
        ]
        report = metrics.check_data_conditions(policies, [profile_entry()], TODAY)
        self.assertEqual(find(report, "분야 living")["값"], 2)
        self.assertEqual(find(report, "분야 job")["값"], 1)

    def test_미달_목록을_돌려준다(self):
        report = metrics.check_data_conditions([policy()], [profile_entry()], TODAY)
        self.assertIn("검수 통과 정책 수", report["미달"])

    def test_정책이_없어도_깨지지_않는다(self):
        report = metrics.check_data_conditions([], [profile_entry()], TODAY)
        self.assertIn("0/0", find(report, "소득 조건 없는 정책 비율")["값"])


class ScoreRecommendationsTest(unittest.TestCase):
    def setUp(self):
        self.policies = [policy(f"SEOUL-00{index}") for index in range(1, 5)]
        self.profiles = [profile_entry("P1", demo=True)]

    def test_전부_맞으면_100퍼센트(self):
        top3 = [
            card["policy_id"]
            for card in metrics.engine.evaluate_policies(
                self.profiles[0]["profile"], self.policies, TODAY
            )["policies"][:3]
        ]
        answers = {"profiles": {"P1": {"expected_top3": top3}}}
        report = metrics.score_recommendations(self.policies, self.profiles, answers, TODAY)
        self.assertEqual(report["추천_정확도"], 1.0)
        self.assertTrue(report["목표_충족"])

    def test_하나도_못_맞추면_0퍼센트이고_목표_미달이다(self):
        answers = {"profiles": {"P1": {"expected_top3": ["GOV-999", "GOV-998", "GOV-997"]}}}
        report = metrics.score_recommendations(self.policies, self.profiles, answers, TODAY)
        self.assertEqual(report["추천_정확도"], 0.0)
        self.assertFalse(report["목표_충족"])
        self.assertEqual(len(report["rows"][0]["놓친_정책"]), 3)

    def test_분모는_프로필당_세_칸이다(self):
        answers = {"profiles": {"P1": {"expected_top3": ["SEOUL-001"]}}}
        report = metrics.score_recommendations(self.policies, self.profiles, answers, TODAY)
        self.assertEqual(report["전체_칸"], 3)
        self.assertEqual(report["적중"], 1)

    def test_판정_일치율을_계산하고_불일치를_보여준다(self):
        answers = {
            "profiles": {
                "P1": {
                    "expected_top3": [],
                    "expected_status": {"SEOUL-001": c.LIKELY, "SEOUL-002": c.UNLIKELY},
                }
            }
        }
        report = metrics.score_recommendations(self.policies, self.profiles, answers, TODAY)
        self.assertEqual(report["판정_일치율"], 0.5)
        self.assertIn("SEOUL-002", report["rows"][0]["상태_불일치"])

    def test_정답이_없는_프로필은_건너뛴다(self):
        profiles = [profile_entry("P1"), profile_entry("P2")]
        answers = {"profiles": {"P1": {"expected_top3": ["SEOUL-001"]}}}
        report = metrics.score_recommendations(self.policies, profiles, answers, TODAY)
        self.assertEqual([row["프로필"] for row in report["rows"]], ["P1"])

    def test_정답셋이_비면_정확도는_0이다(self):
        report = metrics.score_recommendations(self.policies, self.profiles, {}, TODAY)
        self.assertEqual(report["추천_정확도"], 0.0)
        self.assertIsNone(report["판정_일치율"])


class CommandLineTest(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        (self.dir / "policies.json").write_text(
            json.dumps({"policies": [policy()]}, ensure_ascii=False), encoding="utf-8"
        )
        (self.dir / "profiles.json").write_text(
            json.dumps({"profiles": [profile_entry(demo=True)]}, ensure_ascii=False),
            encoding="utf-8",
        )

    def args(self, *extra: str) -> list[str]:
        return [
            "--policies", str(self.dir / "policies.json"),
            "--profiles", str(self.dir / "profiles.json"),
            "--today", "2026-09-20",
            *extra,
        ]

    def test_정답셋_없이_조건_점검만_하면_성공으로_끝난다(self):
        self.assertEqual(metrics.main(self.args()), 0)

    def test_목표_미달이면_종료_코드가_1이다(self):
        answers = self.dir / "answers.json"
        answers.write_text(
            json.dumps({"profiles": {"P1": {"expected_top3": ["GOV-999"]}}}),
            encoding="utf-8",
        )
        self.assertEqual(metrics.main(self.args("--answers", str(answers))), 1)

    def test_목표_충족이면_종료_코드가_0이다(self):
        answers = self.dir / "answers.json"
        answers.write_text(
            json.dumps(
                {"profiles": {"P1": {"expected_top3": ["SEOUL-001", "SEOUL-001", "SEOUL-001"]}}}
            ),
            encoding="utf-8",
        )
        self.assertEqual(metrics.main(self.args("--answers", str(answers))), 0)


class RepositoryDataTest(unittest.TestCase):
    def test_저장소_데이터로_조건_점검이_돌아간다(self):
        root = Path(__file__).resolve().parents[1]
        policies, issues = metrics.loader.load_policies(
            root / "data" / "policies" / "policies.json"
        )
        self.assertEqual(issues, [])
        profiles = json.loads(
            (root / "data" / "profiles" / "profiles.json").read_text(encoding="utf-8")
        )["profiles"]
        report = metrics.check_data_conditions(policies, profiles, TODAY)
        self.assertEqual(len(report["checks"]), 10)
        # 아직 원문을 채운 정책이 적어 미달 항목이 남아 있는 것이 정상이다.
        self.assertIn("검수 통과 정책 수", report["미달"])


if __name__ == "__main__":
    unittest.main()
