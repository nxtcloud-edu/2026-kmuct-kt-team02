"""AI B 실전 호출 준비 상태 점검 테스트."""

import unittest

from ai.judgment.client import ENV_API_KEY, ENV_MODEL
from ai.judgment.readiness import check_readiness


class TestReadiness(unittest.TestCase):
    def test_아무것도_준비되지_않으면_모두_대기(self):
        report = check_readiness(
            {}, python_version=(3, 9, 6), anthropic_available=False, placeholder_count=8
        )
        self.assertFalse(report.ready_for_live_call)
        self.assertEqual(len(report.blockers), 5)
        self.assertIn("대기 5개", report.summary())

    def test_전부_준비되면_실전_호출_가능(self):
        report = check_readiness(
            {ENV_API_KEY: "secret", ENV_MODEL: "model"},
            python_version=(3, 11, 0),
            anthropic_available=True,
            placeholder_count=0,
        )
        self.assertTrue(report.ready_for_live_call)
        self.assertEqual(report.blockers, [])
        self.assertEqual(report.summary(), "AI B 실전 호출 준비 완료")

    def test_키와_모델_값을_출력하지_않는다(self):
        key = "sk-must-not-leak"
        model = "secret-model-name"
        report = check_readiness(
            {ENV_API_KEY: key, ENV_MODEL: model},
            python_version=(3, 11, 0),
            anthropic_available=True,
            placeholder_count=0,
        )
        rendered = report.to_table() + report.summary()
        self.assertNotIn(key, rendered)
        self.assertNotIn(model, rendered)
        self.assertIn("환경 변수 설정됨", rendered)

    def test_파이썬_경계값(self):
        ready = check_readiness(
            {}, python_version=(3, 11, 0), anthropic_available=False, placeholder_count=8
        )
        old = check_readiness(
            {}, python_version=(3, 10, 99), anthropic_available=False, placeholder_count=8
        )
        self.assertTrue(next(i for i in ready.items if i.name == "Python 3.11+").ready)
        self.assertFalse(next(i for i in old.items if i.name == "Python 3.11+").ready)

    def test_키만_있어도_모델이_없으면_대기(self):
        report = check_readiness(
            {ENV_API_KEY: "secret"},
            python_version=(3, 11, 0),
            anthropic_available=True,
            placeholder_count=0,
        )
        blockers = [item.name for item in report.blockers]
        self.assertNotIn("Claude API 키", blockers)
        self.assertIn("Claude 모델 이름", blockers)

    def test_모델만_있어도_키가_없으면_대기(self):
        report = check_readiness(
            {ENV_MODEL: "model"},
            python_version=(3, 11, 0),
            anthropic_available=True,
            placeholder_count=0,
        )
        blockers = [item.name for item in report.blockers]
        self.assertIn("Claude API 키", blockers)
        self.assertNotIn("Claude 모델 이름", blockers)

    def test_실제_문장_대체물이_남으면_평가_준비_안됨(self):
        report = check_readiness(
            {ENV_API_KEY: "secret", ENV_MODEL: "model"},
            python_version=(3, 11, 0),
            anthropic_available=True,
            placeholder_count=2,
        )
        item = next(i for i in report.items if i.name == "J1~J8 실제 공고 문장")
        self.assertFalse(item.ready)
        self.assertIn("2건", item.detail)
        self.assertIn("발표 금지", item.detail)

    def test_표에_다섯_항목이_있다(self):
        report = check_readiness(
            {}, python_version=(3, 9, 6), anthropic_available=False, placeholder_count=8
        )
        table = report.to_table()
        self.assertEqual(len(report.items), 5)
        for name in (
            "Python 3.11+",
            "Anthropic SDK",
            "Claude API 키",
            "Claude 모델 이름",
            "J1~J8 실제 공고 문장",
        ):
            self.assertIn(name, table)


if __name__ == "__main__":
    unittest.main()
