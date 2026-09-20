"""백엔드B 프로토콜 어댑터 (rules/repository.py).

`RuleEngine`, `PolicyRepository` 프로토콜을 실제 Pydantic 계약으로 검증한다.
"""

from __future__ import annotations

import json
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from rules import constants as c

# 이 파일만 `server` 계약 모델을 쓴다. 나머지 rules 테스트는 표준 라이브러리로 돌아간다.
# 의존성이 없는 환경(맨 python3 + unittest)에서는 건너뛴다. 정식 러너는 pytest 다
# (docs/00-overview.md 확정 결정).
try:
    from server.rule_engine import RuleEngineResult, RuleEngineUnavailableError
    from server.schemas import Policy, Profile

    from rules import repository

    DEPENDENCIES_READY = True
except ModuleNotFoundError as error:  # pragma: no cover - 환경에 따라 갈린다
    DEPENDENCIES_READY = False
    MISSING = error.name

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
        "statuses": ["enrolled"],
        "income_max_pct": None,
        "extra_conditions": [],
        "exceptions_text": "",
        "benefit": "월 20만원",
        "documents": [],
        "steps": [],
        "raw_text": SENTENCE,
        "condition_sources": {"age": SENTENCE},
    }
    base.update(overrides)
    return base


def profile(**overrides) -> Profile:
    """테스트용 프로필.

    `district` 를 채우는 이유는 취향이 아니다. `ProfileInput.district` 가 현재 **필수**라
    자치구 없는 Profile 을 만들 수 없다. 문서(`docs/01-glossary-profile.md` 2장,
    `docs/03-api-contract.md` 2장)는 선택 입력으로 정했다. DistrictContractTest 참고.
    """
    base = {
        "age": 23,
        "district": "성북구",
        "status": "enrolled",
        "categories": ["job", "living"],
        "income_bracket": "unknown",
    }
    base.update(overrides)
    return Profile.model_validate(base)


@unittest.skipUnless(DEPENDENCIES_READY, "fastapi·pydantic 미설치 환경에서는 건너뛴다")
class AdapterTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)

    def write(self, rows) -> Path:
        path = Path(self.temp.name) / "policies.json"
        path.write_text(json.dumps({"policies": rows}, ensure_ascii=False), encoding="utf-8")
        return path

    def build(self, rows):
        return repository.build(self.write(rows), clock=lambda: TODAY)


class PolicyStoreTest(AdapterTestCase):
    def test_정책_상세를_Policy_로_돌려준다(self):
        store, _ = self.build([policy()])
        found = store.get("SEOUL-001")
        self.assertIsInstance(found, Policy)
        self.assertEqual(found.title, "정책 SEOUL-001")

    def test_없는_id_는_None(self):
        store, _ = self.build([policy()])
        self.assertIsNone(store.get("SEOUL-999"))

    def test_계약을_못_맞추는_행은_None(self):
        store, _ = self.build([policy(source_url=None)])
        self.assertIsNone(store.get("SEOUL-001"))

    def test_검수_완료_수를_센다(self):
        store, _ = self.build(
            [policy("SEOUL-001"), policy("SEOUL-002", data_status=c.RECHECK)]
        )
        self.assertEqual(store.count_verified(), 1)

    def test_원문이_없는_staging_행은_계약을_통과하지_못한다(self):
        root = Path(__file__).resolve().parents[1]
        store = repository.PolicyStore(root / "data" / "policies" / "staging.json")
        self.assertEqual(len(store), 27)
        # 아직 공고 원문을 채우지 않은 정책은 source_url 이 비어 Policy 로 만들 수 없다.
        self.assertIsNone(store.get("GOV-001"))

    def test_승격본은_검수_완료로_집계된다(self):
        root = Path(__file__).resolve().parents[1]
        store = repository.PolicyStore(root / "data" / "policies" / "policies.json")
        self.assertGreaterEqual(len(store), 4)
        # 마감된 청년수당은 data_status 가 closed 라 검수 완료 집계에서 빠진다.
        self.assertEqual(store.count_verified(), len(store) - 1)
        self.assertIsNotNone(store.get("GOV-006"))


class RuleEngineAdapterTest(AdapterTestCase):
    def test_계약_모델로_결과를_돌려준다(self):
        _, adapter = self.build([policy()])
        result = adapter.evaluate(profile(), limit=5)
        self.assertIsInstance(result, RuleEngineResult)
        self.assertEqual(len(result.policies), 1)
        card = result.policies[0]
        self.assertEqual(card.policy_id, "SEOUL-001")
        self.assertEqual(card.status_label, "신청 가능성이 높아요")
        self.assertEqual(card.deadline.badge, "상시 접수")
        self.assertGreaterEqual(card.conditions[0].footnote_id, 1)

    def test_clock_을_바꿔_끼우면_마감_배지가_바뀐다(self):
        path = self.write([policy(apply_end="2026-09-22")])
        store = repository.PolicyStore(path)

        가까운날 = repository.RuleEngineAdapter(store, clock=lambda: TODAY)
        self.assertEqual(
            가까운날.evaluate(profile()).policies[0].deadline.badge, "마감 임박 D-2"
        )

        먼날 = repository.RuleEngineAdapter(store, clock=lambda: date(2026, 9, 1))
        self.assertEqual(먼날.evaluate(profile()).policies[0].deadline.badge, "D-21")

    def test_마감된_정책은_결과에_들어가지_않는다(self):
        _, adapter = self.build([policy(apply_end="2026-09-19")])
        result = adapter.evaluate(profile())
        self.assertEqual(result.policies, [])

    def test_unlikely_는_개수로만_전달되고_목록은_따로_꺼낸다(self):
        _, adapter = self.build(
            [policy("SEOUL-001"), policy("SEOUL-002", statuses=["job_seeking"])]
        )
        result = adapter.evaluate(profile())
        self.assertEqual(result.hidden_unlikely_count, 1)
        hidden = adapter.latest_hidden_unlikely()
        self.assertEqual([item.policy_id for item in hidden], ["SEOUL-002"])
        self.assertEqual(hidden[0].status.value, c.UNLIKELY)

    def test_미확인_항목을_꺼낼_수_있다(self):
        _, adapter = self.build([policy(income_max_pct=150)])
        adapter.evaluate(profile())
        items = adapter.latest_unknown_items()
        self.assertEqual([item["field"] for item in items], ["income_bracket"])

    def test_정책_데이터가_없으면_사용_불가를_올린다(self):
        _, adapter = self.build([])
        with self.assertRaises(RuleEngineUnavailableError):
            adapter.evaluate(profile())

    def test_기본_표시는_다섯개를_넘지_않는다(self):
        _, adapter = self.build([policy(f"SEOUL-10{index}") for index in range(7)])
        result = adapter.evaluate(profile())
        self.assertEqual(len(result.policies), 5)

    def test_정책_상세_판정을_돌려준다(self):
        """`/policies/{id}` 가 부르는 경로. server/api/policies.py 53행."""
        store, adapter = self.build([policy()])
        found = store.get("SEOUL-001")
        evaluation = adapter.evaluate_policy(profile(), found)
        self.assertEqual(evaluation.policy_id, "SEOUL-001")
        self.assertEqual(evaluation.status_label, "신청 가능성이 높아요")
        self.assertGreaterEqual(evaluation.conditions[0].footnote_id, 1)

    def test_상세는_후보_제외_규칙을_적용하지_않는다(self):
        """마감됐거나 관심 분야가 달라도 상세는 열려야 한다."""
        마감 = policy("SEOUL-001", apply_end="2026-09-19")
        분야_불일치 = policy("SEOUL-002", categories=["housing"])
        store, adapter = self.build([마감, 분야_불일치])

        closed = adapter.evaluate_policy(profile(), store.get("SEOUL-001"))
        self.assertEqual(closed.data_status.value, c.CLOSED)
        self.assertEqual(closed.deadline.badge, "접수 마감")

        mismatch = adapter.evaluate_policy(profile(), store.get("SEOUL-002"))
        self.assertEqual(mismatch.policy_id, "SEOUL-002")

    def test_없는_정책_상세는_사용_불가를_올린다(self):
        store, adapter = self.build([policy("SEOUL-001")])
        found = store.get("SEOUL-001")
        found_copy = found.model_copy(update={"id": "SEOUL-999"})
        with self.assertRaises(RuleEngineUnavailableError):
            adapter.evaluate_policy(profile(), found_copy)

    def test_자치구가_있는_대표_프로필은_계약을_통과한다(self):
        root = Path(__file__).resolve().parents[1]
        entries = json.loads(
            (root / "data" / "profiles" / "profiles.json").read_text(encoding="utf-8")
        )["profiles"]
        _, adapter = self.build([policy(f"SEOUL-10{index}") for index in range(3)])
        checked = 0
        for entry in entries:
            if entry["profile"]["district"] is None:
                continue
            with self.subTest(profile=entry["id"]):
                result = adapter.evaluate(Profile.model_validate(entry["profile"]))
                self.assertIsInstance(result, RuleEngineResult)
                checked += 1
        self.assertEqual(checked, 1, "자치구를 채운 대표 프로필은 P2 하나다")


@unittest.skipUnless(DEPENDENCIES_READY, "fastapi·pydantic 미설치 환경에서는 건너뛴다")
class DistrictContractTest(unittest.TestCase):
    """자치구는 선택 입력이다.

    `docs/01-glossary-profile.md` 2장, `docs/03-api-contract.md` 2장, FR01 인수 기준
    "필수 3개" 가 모두 선택으로 정해 두었다. 한동안 `ProfileInput.district` 가 필수여서
    대표 프로필 네 개와 자치구 후속 질문이 막혀 있었고, 그 상태를 되돌린 뒤의 계약을
    여기서 고정한다.
    """

    def test_자치구_없는_프로필을_받아들인다(self):
        profile = Profile.model_validate(
            {
                "age": 23,
                "district": None,
                "status": "enrolled",
                "categories": ["job"],
                "income_bracket": "unknown",
            }
        )
        self.assertIsNone(profile.district)

    def test_대표_프로필_다섯_개가_모두_계약을_통과한다(self):
        root = Path(__file__).resolve().parents[1]
        entries = json.loads(
            (root / "data" / "profiles" / "profiles.json").read_text(encoding="utf-8")
        )["profiles"]
        for entry in entries:
            with self.subTest(profile=entry["id"]):
                profile = Profile.model_validate(entry["profile"])
                self.assertEqual(profile.region.value, "seoul")

    def test_규칙_엔진은_자치구가_없으면_미확인으로_두고_묻는다(self):
        from rules import conditions as cond

        condition, _ = cond.evaluate_region(
            {"district": None}, {"regions": ["마포구"], "source_url": None, "condition_sources": {}}
        )
        self.assertEqual(condition["result"], c.UNKNOWN)
        self.assertEqual(condition["needed_field"], "district")


if __name__ == "__main__":
    unittest.main()
