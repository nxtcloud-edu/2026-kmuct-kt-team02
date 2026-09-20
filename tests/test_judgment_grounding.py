"""근거 없는 조건 점검 테스트 (`ai/judgment/grounding.py`).

사전 점검에서 찾은 "이러면 놓친다 / 이러면 잡음이 된다" 시나리오를 그대로 케이스로 옮겼다.
정상 경로만 확인하지 않는다.

이 도구가 잡아야 하는 것은 인용 검증이 통과시킨 뒤에도 남는 위험이다.
발췌는 원문에 있는데 조건 요약이 원문에 없는 숫자를 말하는 경우다.
"""

import unicodedata
import unittest

from ai.judgment.grounding import (
    NAME_WITHOUT_EXCERPT,
    UNGROUNDED_NUMBER,
    GroundingReport,
    check_condition,
    check_conditions,
    main,
    numbers_in,
)
from ai.judgment.values import BY_AI, MET, UNKNOWN

# 실제 공고 문장 형태를 가정한 원문
RAW = (
    "○ 지원 대상\n"
    "  - 서울시에 주민등록이 되어 있는 만 19세 이상 34세 이하 청년\n"
    "  - 가구 소득이 기준 중위소득 150% 이하인 가구의 청년\n"
    "  - 월 최대 200,000원까지 지원\n"
    "○ 제외 대상\n"
    "  - 휴학생은 지원 대상에서 제외합니다\n"
    "  - 최근 2년 내 동일 사업 참여자는 제외합니다\n"
)


def condition(name, **overrides):
    """검증을 통과한 조건 하나. 테스트마다 필요한 값만 덮어쓴다."""
    item = {
        "name": name,
        "result": MET,
        "judged_by": BY_AI,
        "excerpt": "휴학생은 지원 대상에서 제외합니다",
        "needed_field": "",
        "excerpt_verified": True,
    }
    item.update(overrides)
    return item


class TestNumberGrounding(unittest.TestCase):
    """조건 요약의 숫자가 원문에 있는지."""

    def test_원문에_있는_숫자는_문제없음(self):
        report = check_condition(condition("소득 150% 이하"), RAW)
        self.assertTrue(report.ok, report.summary())
        self.assertEqual(report.checked, 1)

    def test_원문에_없는_숫자를_잡는다(self):
        """원문은 150% 인데 요약이 180% 라고 말하는 경우. 지어낸 비율이다."""
        report = check_condition(condition("소득 180% 이하"), RAW)
        self.assertEqual(report.count, 1)
        issue = report.issues[0]
        self.assertEqual(issue.kind, UNGROUNDED_NUMBER)
        self.assertEqual(issue.token, "180")
        self.assertEqual(issue.condition_name, "소득 180% 이하")

    def test_숫자_여러_개면_없는_것만_잡는다(self):
        report = check_condition(condition("만 19세~40세"), RAW)
        self.assertEqual([i.token for i in report.issues], ["40"])

    def test_부분_문자열로_통과시키지_않는다(self):
        """원문에 150 이 있어도 요약의 15 를 근거 있다고 보면 안 된다."""
        report = check_condition(condition("소득 15% 이하"), RAW)
        self.assertEqual([i.token for i in report.issues], ["15"])

    def test_숫자가_없는_요약은_문제없음(self):
        report = check_condition(condition("휴학생 제외"), RAW)
        self.assertTrue(report.ok)


class TestNumberFormats(unittest.TestCase):
    """표기가 달라도 같은 숫자로 본다."""

    def test_쉼표_섞인_원문과_쉼표_없는_요약(self):
        """원문 200,000원 · 요약 200000원."""
        report = check_condition(condition("월 200000원 지원"), RAW)
        self.assertTrue(report.ok, report.summary())

    def test_쉼표_섞인_요약과_쉼표_없는_원문(self):
        raw = RAW.replace("200,000", "200000")
        report = check_condition(condition("월 200,000원 지원"), raw)
        self.assertTrue(report.ok, report.summary())

    def test_소수점은_하나의_숫자로_본다(self):
        raw = "지원 비율은 1.5배입니다"
        self.assertTrue(check_condition(condition("지원 1.5배"), raw).ok)
        self.assertEqual(check_condition(condition("지원 2.5배"), raw).count, 1)

    def test_쉼표_묶음이_세_자리가_아니면_따로_읽는다(self):
        """1,2 를 12 로 읽으면 없는 숫자를 만들어 대조가 조용히 틀어진다."""
        self.assertEqual(numbers_in("1,2 항"), set())
        self.assertEqual(numbers_in("12,345 원"), {"12345"})

    def test_한_자리_수는_건너뛴다(self):
        """"2년차", "1개" 처럼 요약을 다듬다 생기는 숫자. 잡음이면 점검표를 안 본다."""
        report = check_condition(condition("최근 5년 참여자 제외"), RAW)
        self.assertTrue(report.ok, report.summary())
        self.assertEqual(numbers_in("1개 2년 3단계"), set())

    def test_두_자리부터_잡는다(self):
        self.assertEqual(numbers_in("10개"), {"10"})


class TestCrossPlatform(unittest.TestCase):
    """macOS 와 Windows 를 번갈아 쓸 때 생기는 차이 (normalize 재사용 확인)."""

    def test_자모_분해형_원문에서도_동작한다(self):
        raw_nfd = unicodedata.normalize("NFD", RAW)
        self.assertNotEqual(raw_nfd, RAW, "전제 확인: NFD 와 NFC 는 다른 문자열이다")

        self.assertTrue(check_condition(condition("소득 150% 이하"), raw_nfd).ok)
        self.assertEqual(check_condition(condition("소득 180% 이하"), raw_nfd).count, 1)

    def test_숫자_사이_제로폭_공백을_흡수한다(self):
        """웹 공고 복사에 섞이는 제로폭 공백. 1 과 50 으로 갈리면 150 과 어긋난다."""
        raw = RAW.replace("150%", "1\u200b50%")
        self.assertTrue(check_condition(condition("소득 150% 이하"), raw).ok)

    def test_bom이_붙은_원문(self):
        self.assertTrue(check_condition(condition("소득 150% 이하"), "\ufeff" + RAW).ok)


class TestNameWithoutExcerpt(unittest.TestCase):
    """근거 없이 조건 요약만 화면에 나가는 경우."""

    def test_발췌가_비면_잡는다(self):
        report = check_condition(condition("휴학생 제외", excerpt=""), RAW)
        self.assertEqual(report.count, 1)
        self.assertEqual(report.issues[0].kind, NAME_WITHOUT_EXCERPT)

    def test_발췌가_None이면_잡는다(self):
        report = check_condition(condition("휴학생 제외", excerpt=None), RAW)
        self.assertEqual([i.kind for i in report.issues], [NAME_WITHOUT_EXCERPT])

    def test_발췌가_공백뿐이면_잡는다(self):
        report = check_condition(condition("휴학생 제외", excerpt="   \n"), RAW)
        self.assertEqual([i.kind for i in report.issues], [NAME_WITHOUT_EXCERPT])

    def test_검증_실패인데_요약이_남아_있으면_잡는다(self):
        """인용 검증은 실패 시 요약을 비워야 한다. 안 비웠으면 근거 없는 문구가 나간다."""
        item = condition("휴학생 제외", result=UNKNOWN, excerpt_verified=False)
        report = check_condition(item, RAW)
        self.assertEqual(report.count, 1)
        self.assertEqual(report.issues[0].kind, NAME_WITHOUT_EXCERPT)
        self.assertIn(UNKNOWN, report.issues[0].detail)

    def test_검증_실패이고_요약이_비었으면_문제없음(self):
        """인용 검증이 제대로 비운 상태. 화면에 나가는 문구가 없다."""
        item = condition("", result=UNKNOWN, excerpt=None, excerpt_verified=False)
        report = check_condition(item, RAW)
        self.assertTrue(report.ok)
        self.assertEqual(report.checked, 1, "검사는 했으므로 분모에 센다")

    def test_검증_전_조건은_판단하지_않는다(self):
        """`excerpt_verified` 키가 없는 조건. 검증 전 상태를 문제로 세면 건수가 틀어진다."""
        item = condition("휴학생 제외")
        del item["excerpt_verified"]
        self.assertTrue(check_condition(item, RAW).ok)

    def test_placeholder_조건은_건너뛴다(self):
        """판정 실패 자리표시는 name 이 빈 채로 오는 것이 정상이다."""
        item = {
            "name": "",
            "result": UNKNOWN,
            "judged_by": BY_AI,
            "excerpt": None,
            "needed_field": "공고 확인 필요",
            "placeholder": True,
            "placeholder_reason": "timeout",
            "excerpt_verified": False,
        }
        report = check_condition(item, RAW)
        self.assertTrue(report.ok)
        self.assertEqual(report.checked, 0, "건너뛴 조건은 분모에 세지 않는다")

    def test_placeholder인데_요약이_남아_있어도_건너뛴다(self):
        """자리표시 처리 쪽 문제는 이 도구가 아니라 citation 테스트가 본다."""
        item = condition("휴학생 제외", placeholder=True, excerpt=None)
        self.assertTrue(check_condition(item, RAW).ok)


class TestEdgeCases(unittest.TestCase):
    """경계값."""

    def test_빈_조건_목록(self):
        report = check_conditions([], RAW)
        self.assertTrue(report.ok)
        self.assertEqual(report.checked, 0)
        self.assertIn("0건", report.summary())

    def test_None_조건_목록(self):
        self.assertTrue(check_conditions(None, RAW).ok)

    def test_원문이_비었을_때_숫자는_근거가_없다(self):
        """대조할 근거가 아예 없다. 데이터 문제이기도 해서 눈에 보이는 편이 낫다."""
        for raw in ("", None, "   "):
            with self.subTest(raw=repr(raw)):
                report = check_condition(condition("소득 150% 이하"), raw)
                self.assertEqual([i.kind for i in report.issues], [UNGROUNDED_NUMBER])

    def test_원문이_비었고_요약에_숫자가_없으면_문제없음(self):
        self.assertTrue(check_condition(condition("휴학생 제외"), "").ok)

    def test_요약이_비면_아무것도_잡지_않는다(self):
        self.assertTrue(check_condition(condition(""), RAW).ok)
        self.assertTrue(check_condition(condition(None), RAW).ok)

    def test_같은_숫자가_여러_번_나오면_한_건이다(self):
        report = check_condition(condition("180일 180% 기준"), RAW)
        self.assertEqual(report.count, 1)


class TestReport(unittest.TestCase):
    """집계와 지표 문구."""

    def test_조건_목록을_합쳐_센다(self):
        conditions = [
            condition("소득 150% 이하"),
            condition("소득 180% 이하"),
            condition("휴학생 제외", excerpt=None),
        ]
        report = check_conditions(conditions, RAW)
        self.assertEqual(report.checked, 3)
        self.assertEqual(report.count, 2)
        self.assertFalse(report.ok)
        self.assertEqual(
            report.by_kind(),
            {UNGROUNDED_NUMBER: 1, NAME_WITHOUT_EXCERPT: 1},
        )

    def test_summary에_건수가_들어간다(self):
        report = check_conditions([condition("소득 180% 이하")], RAW)
        summary = report.summary()
        self.assertIn("1건", summary)
        self.assertIn("1개", summary)
        self.assertIn(UNGROUNDED_NUMBER, summary)

    def test_통과했을_때_summary(self):
        report = check_conditions([condition("휴학생 제외")], RAW)
        summary = report.summary()
        self.assertIn("0건", summary)
        self.assertNotIn(UNGROUNDED_NUMBER, summary)

    def test_빈_보고서_기본값(self):
        report = GroundingReport()
        self.assertTrue(report.ok)
        self.assertEqual(report.count, 0)
        self.assertEqual(report.by_kind(), {})

    def test_보고서는_서로_섞이지_않는다(self):
        """dataclass 기본값을 공유하면 앞 보고서 건수가 뒤에 섞인다."""
        first = GroundingReport()
        first.issues.append(check_condition(condition("소득 180% 이하"), RAW).issues[0])
        self.assertEqual(GroundingReport().count, 0)


class TestEntryPoint(unittest.TestCase):
    """`python3 -m ai.judgment.grounding`."""

    def test_main은_안내만_출력하고_끝난다(self):
        import contextlib
        import io

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = main()

        self.assertEqual(code, 0)
        output = buffer.getvalue()
        self.assertIn("grounding-checklist.md", output, "사람이 볼 양식을 안내해야 한다")
        self.assertIn(UNGROUNDED_NUMBER, output)


if __name__ == "__main__":
    unittest.main()
