"""정책 데이터 점검 테스트 (`ai/judgment/data_check.py`).

인용 검증이 전건 실패하는 원인은 대부분 판정이 아니라 데이터다.
그 원인을 데이터 단계에서 잡는지 본다.
"""

import contextlib
import io
import json
import tempfile
import unicodedata
import unittest
from pathlib import Path

from ai.judgment.data_check import (
    ISSUE_CHECKED_AT_MISSING,
    ISSUE_CONDITION_SOURCE_NOT_IN_RAW,
    ISSUE_EXCEPTIONS_NOT_IN_RAW,
    ISSUE_RAW_TEXT_MISSING,
    ISSUE_SOURCE_URL_MISSING,
    check_policies,
    load_policies,
    main,
)

RAW = (
    "○ 지원 대상\n"
    "  - 서울시에 주민등록이 되어 있는 만 19세 이상 34세 이하 청년\n"
    "○ 제외 대상\n"
    "  - 휴학생은 지원 대상에서 제외합니다\n"
)
EXCEPTIONS = "휴학생은 지원 대상에서 제외합니다"


def policy(**overrides):
    item = {
        "id": "SEOUL-001",
        "source_url": "https://example.go.kr/notice/1",
        "checked_at": "2026-09-20",
        "raw_text": RAW,
        "exceptions_text": EXCEPTIONS,
        "condition_sources": {},
    }
    item.update(overrides)
    return item


class TestHealthyData(unittest.TestCase):
    def test_정상_정책은_문제없다(self):
        report = check_policies([policy()])
        self.assertTrue(report.ok, report.summary())
        self.assertEqual(report.checked, 1)
        self.assertEqual(report.judgeable, 1)

    def test_플랫폼_차이가_있어도_통과한다(self):
        """macOS NFD 원문, Windows CRLF 원문."""
        for raw in (unicodedata.normalize("NFD", RAW), RAW.replace("\n", "\r\n")):
            with self.subTest(form="NFD" if "\r" not in raw else "CRLF"):
                self.assertTrue(check_policies([policy(raw_text=raw)]).ok)

    def test_condition_sources도_검증한다(self):
        sources = {"status": "휴학생은 지원 대상에서 제외합니다"}
        self.assertTrue(check_policies([policy(condition_sources=sources)]).ok)


class TestBrokenData(unittest.TestCase):
    def test_원문이_없으면_잡는다(self):
        report = check_policies([policy(raw_text="")])
        self.assertIn(ISSUE_RAW_TEXT_MISSING, report.by_code())
        self.assertEqual(report.judgeable, 0, "원문이 없으면 판정 대상으로 세지 않는다")

    def test_예외_문장을_다듬어_옮기면_잡는다(self):
        """이 정책의 모든 발췌가 not_found 로 떨어진다."""
        report = check_policies([policy(exceptions_text="휴학생 지원 불가")])
        self.assertIn(ISSUE_EXCEPTIONS_NOT_IN_RAW, report.by_code())
        self.assertFalse(report.ok)

    def test_근거_문장이_원문에_없으면_잡는다(self):
        sources = {"income": "기준 중위소득 150% 이하"}
        report = check_policies([policy(condition_sources=sources)])
        self.assertIn(ISSUE_CONDITION_SOURCE_NOT_IN_RAW, report.by_code())

    def test_출처와_확인일_누락을_잡는다(self):
        report = check_policies([policy(source_url="", checked_at="")])
        codes = report.by_code()
        self.assertIn(ISSUE_SOURCE_URL_MISSING, codes)
        self.assertIn(ISSUE_CHECKED_AT_MISSING, codes)

    def test_여러_정책의_문제를_모아_센다(self):
        report = check_policies(
            [policy(), policy(id="A", raw_text=""), policy(id="B", exceptions_text="다른 말")]
        )
        self.assertEqual(report.checked, 3)
        self.assertEqual(len(report.issues), 2)
        self.assertEqual({i.policy_id for i in report.issues}, {"A", "B"})


class TestJudgeableCount(unittest.TestCase):
    def test_예외_문장이_없으면_판정_대상이_아니다(self):
        report = check_policies([policy(exceptions_text="")])
        self.assertTrue(report.ok, "예외 조건이 없는 정책은 정상이다")
        self.assertEqual(report.judgeable, 0)

    def test_판정_대상이_0건이면_요약이_경고한다(self):
        """지금 data/policies/staging.json 상태다. 원문 각주가 동작할 수 없다."""
        report = check_policies([policy(exceptions_text="")])
        self.assertIn("예외 조건 판정과 원문 각주가 아직 동작할 수 없다", report.summary())

    def test_판정_대상이_있으면_그_경고는_없다(self):
        self.assertNotIn("동작할 수 없다", check_policies([policy()]).summary())

    def test_빈_목록(self):
        report = check_policies([])
        self.assertTrue(report.ok)
        self.assertEqual(report.checked, 0)


class TestLoadAndMain(unittest.TestCase):
    """`main()` 은 표를 출력한다. 테스트 결과가 가려지지 않게 삼킨다."""

    def write(self, payload):
        path = Path(tempfile.mkdtemp()) / "policies.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def run_main(self, argv):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main(argv)
        return code, buffer.getvalue()

    def test_목록_형식을_읽는다(self):
        self.assertEqual(len(load_policies(self.write([policy()]))), 1)

    def test_policies_키_형식을_읽는다(self):
        self.assertEqual(len(load_policies(self.write({"policies": [policy()]}))), 1)

    def test_정상_데이터면_종료_코드가_0이다(self):
        code, _ = self.run_main([self.write([policy()])])
        self.assertEqual(code, 0)

    def test_문제가_있으면_종료_코드가_1이고_표를_낸다(self):
        code, output = self.run_main([self.write([policy(raw_text="")])])
        self.assertEqual(code, 1)
        self.assertIn(ISSUE_RAW_TEXT_MISSING, output)

    def test_읽을_수_없으면_종료_코드가_1이다(self):
        code, output = self.run_main(["/tmp/does-not-exist-policies.json"])
        self.assertEqual(code, 1)
        self.assertIn("읽지 못했다", output)

    def test_모양이_다르면_종료_코드가_1이다(self):
        code, _ = self.run_main([self.write({"unexpected": 1})])
        self.assertEqual(code, 1)


class TestRealStagingData(unittest.TestCase):
    """저장소의 실제 정책 파일. 아직 골격이라 문제가 잡히는 것이 정상이다."""

    def test_실제_파일을_읽고_점검할_수_있다(self):
        path = Path("data/policies/staging.json")
        if not path.exists():
            self.skipTest("정책 파일이 아직 없다")
        report = check_policies(load_policies(str(path)))
        self.assertGreater(report.checked, 0)
        self.assertIsInstance(report.summary(), str)


if __name__ == "__main__":
    unittest.main()
