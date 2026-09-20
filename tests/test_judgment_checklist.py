"""체크리스트 추출 테스트 (`ai/judgment/checklist.py`).

핵심은 **없는 것을 만들어 내지 않는지**다.
절차를 지어내면 사용자가 그 순서대로 준비하다 막힌다 (README 8장).
"""

import unittest

from ai.judgment.checklist import _HEADER_MARK, extract_checklist, split_lines

RAW = (
    "○ 지원 자격\n"
    "  - 서울시에 주민등록이 되어 있는 만 19세 이상 34세 이하 청년\n"
    "○ 제출 서류\n"
    "  - 주민등록초본 (정부24 발급)\n"
    "  - 건강보험료 납부확인서\n"
    "  - 재학증명서\n"
    "○ 신청 방법\n"
    "  1. 누리집 접속 후 회원가입\n"
    "  2. 신청서 작성 및 서류 첨부\n"
    "  3. 심사 결과 문자 통보\n"
)


class TestHeaderDetection(unittest.TestCase):
    """머리글과 항목을 구분한다."""

    def test_기호로_시작하면_머리글(self):
        for line in ("○ 제출 서류", "■ 신청 방법", "◆ 유의 사항", "가. 지원 자격"):
            with self.subTest(line=line):
                self.assertTrue(_HEADER_MARK.match(line))

    def test_숫자로_시작하면_머리글이_아니다(self):
        """공고의 "1. 지원 자격"(머리글)과 "1. 누리집 접속"(단계)은 형태가 같다.

        구분할 수 없으므로 항목으로 본다. 머리글로 오인하면 수집이 중간에 끊겨
        신청 단계를 통째로 잃는다.
        """
        for line in ("  1. 누리집 접속 후 회원가입", "2. 신청서 작성", "10. 결과 통보"):
            with self.subTest(line=line):
                self.assertFalse(_HEADER_MARK.match(line))

    def test_항목_기호로_시작하면_머리글이_아니다(self):
        for line in ("  - 주민등록초본", "  · 재학증명서", "  • 통장 사본"):
            with self.subTest(line=line):
                self.assertFalse(_HEADER_MARK.match(line))


class TestSplitLines(unittest.TestCase):
    def test_항목_기호를_떼어낸다(self):
        self.assertEqual(
            split_lines("- 주민등록초본\n· 재학증명서\n1. 통장 사본"),
            ["주민등록초본", "재학증명서", "통장 사본"],
        )

    def test_빈_줄을_버린다(self):
        self.assertEqual(split_lines("주민등록초본\n\n  \n재학증명서"), ["주민등록초본", "재학증명서"])

    def test_중복을_지운다(self):
        self.assertEqual(split_lines("주민등록초본\n주민등록초본"), ["주민등록초본"])

    def test_공백만_다른_중복도_지운다(self):
        self.assertEqual(split_lines("주민등록  초본\n주민등록 초본"), ["주민등록  초본"])

    def test_목록도_받는다(self):
        self.assertEqual(split_lines(["- 서류A", "서류B"]), ["서류A", "서류B"])

    def test_빈_입력(self):
        self.assertEqual(split_lines(None), [])
        self.assertEqual(split_lines(""), [])


class TestExtractFromFields(unittest.TestCase):
    """1차 출처는 백엔드A 가 검수한 documents / steps 필드."""

    def test_필드가_있으면_그대로_쓴다(self):
        result = extract_checklist(
            {
                "documents": "주민등록초본 (정부24 발급)\n건강보험료 납부확인서",
                "steps": "누리집 접속 후 회원가입\n신청서 작성 및 서류 첨부",
                "raw_text": RAW,
            }
        )
        self.assertEqual(
            result.documents, ["주민등록초본 (정부24 발급)", "건강보험료 납부확인서"]
        )
        self.assertEqual(result.steps, ["누리집 접속 후 회원가입", "신청서 작성 및 서류 첨부"])
        self.assertEqual(result.source, {"documents": "field", "steps": "field"})
        self.assertEqual(result.ungrounded, [])


class TestExtractFromRawText(unittest.TestCase):
    """필드가 비어 있을 때만 원문에서 찾는다."""

    def test_서류_구역을_찾는다(self):
        result = extract_checklist({"raw_text": RAW})
        self.assertEqual(
            result.documents,
            ["주민등록초본 (정부24 발급)", "건강보험료 납부확인서", "재학증명서"],
        )
        self.assertEqual(result.source["documents"], "raw_text")

    def test_신청_단계_구역을_찾는다(self):
        """숫자 머리글 오인 버그가 재발하면 여기서 잡힌다."""
        result = extract_checklist({"raw_text": RAW})
        self.assertEqual(
            result.steps,
            ["누리집 접속 후 회원가입", "신청서 작성 및 서류 첨부", "심사 결과 문자 통보"],
        )
        self.assertEqual(result.source["steps"], "raw_text")

    def test_다음_머리글에서_멈춘다(self):
        """지원 자격 항목이 서류 목록에 섞이지 않아야 한다."""
        result = extract_checklist({"raw_text": RAW})
        for item in result.documents:
            self.assertNotIn("주민등록이 되어 있는", item)

    def test_머리글_줄에_값이_붙어_있는_경우(self):
        raw = "○ 제출 서류: 주민등록초본, 재학증명서\n○ 문의\n  - 02-000-0000\n"
        result = extract_checklist({"raw_text": raw})
        self.assertEqual(result.documents, ["주민등록초본", "재학증명서"])

    def test_구역이_없으면_빈_목록이다(self):
        """없는 항목을 만들어 내지 않는다. 빈 목록이 더 안전한 실패다."""
        raw = "○ 지원 자격\n  - 만 19세 이상\n"
        result = extract_checklist({"raw_text": raw})
        self.assertEqual(result.documents, [])
        self.assertEqual(result.steps, [])

    def test_원문도_필드도_없으면_비어_있다(self):
        result = extract_checklist({})
        self.assertTrue(result.is_empty)
        self.assertEqual(result.ungrounded, [])


class TestGrounding(unittest.TestCase):
    """원문에서 찾지 못한 항목을 표시한다."""

    def test_원문에_없는_단계를_표시한다(self):
        """"온라인 신청" 처럼 당연해 보이지만 공고에 없는 단계."""
        result = extract_checklist(
            {"steps": "누리집 접속 후 회원가입\n온라인 신청", "raw_text": RAW}
        )
        self.assertEqual(result.ungrounded, ["온라인 신청"])

    def test_표시하되_버리지_않는다(self):
        """백엔드A 가 발급처를 덧붙이는 등 형식을 다듬는 경우가 있다.

        원문에 글자 그대로 없다고 지어낸 것이라 단정할 수 없으므로
        점검거리로만 남긴다 (README 11장).
        """
        result = extract_checklist(
            {"documents": "주민등록초본 (온라인 발급 가능)", "raw_text": RAW}
        )
        self.assertIn("주민등록초본 (온라인 발급 가능)", result.documents)
        self.assertIn("주민등록초본 (온라인 발급 가능)", result.ungrounded)

    def test_원문_기반_항목은_표시되지_않는다(self):
        result = extract_checklist({"raw_text": RAW})
        self.assertEqual(result.ungrounded, [])

    def test_원문이_없으면_판단하지_않는다(self):
        result = extract_checklist({"documents": "주민등록초본"})
        self.assertEqual(result.ungrounded, [])


class TestNoFabrication(unittest.TestCase):
    """마지막 항목(공식 신청 페이지 이동)은 프론트가 붙인다."""

    def test_신청_페이지_이동을_넣지_않는다(self):
        result = extract_checklist({"raw_text": RAW, "apply_url": "https://example.gov.kr"})
        joined = " ".join(result.steps)
        self.assertNotIn("example.gov.kr", joined)
        self.assertNotIn("신청 페이지로 이동", joined)


if __name__ == "__main__":
    unittest.main(verbosity=2)
