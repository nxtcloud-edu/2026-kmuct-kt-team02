"""인용 검증 테스트.

사전 점검에서 찾은 "이러면 깨진다" 시나리오를 그대로 케이스로 옮겼다.
정상 경로만 확인하지 않고, macOS · Windows 왕복에서 실제로 생기는 형태를 넣는다.
"""

import unicodedata
import unittest

from ai.citation import (
    canonical,
    normalize,
    raw_text_covers,
    verify_conditions,
    verify_excerpt,
)

# 실제 공고 문장 형태를 가정한 원문
RAW = (
    "○ 지원 대상\n"
    "  - 서울시에 주민등록이 되어 있는 만 19세 이상 34세 이하 청년\n"
    "  - 신청일 기준 국내 대학에 재학 중인 자\n"
    "○ 제외 대상\n"
    "  - 휴학생은 지원 대상에서 제외합니다\n"
    "  - 타 청년 지원금을 수혜 중인 자는 중복 신청할 수 없습니다\n"
)

EXCERPT = "휴학생은 지원 대상에서 제외합니다"


class TestCrossPlatform(unittest.TestCase):
    """macOS 와 Windows 를 번갈아 쓸 때 생기는 차이."""

    def test_nfd_원문과_nfc_발췌가_대조된다(self):
        """macOS 경로에서 자모 분해형으로 들어온 원문.

        정규화가 없으면 길이가 달라 포함 검사가 무조건 실패한다.
        """
        raw_nfd = unicodedata.normalize("NFD", RAW)
        self.assertNotEqual(raw_nfd, RAW, "전제 확인: NFD 와 NFC 는 다른 문자열이다")
        self.assertNotIn(EXCERPT, raw_nfd, "전제 확인: 정규화 없이는 실패한다")

        result = verify_excerpt(EXCERPT, raw_nfd)
        self.assertTrue(result.ok, f"실패 사유: {result.reason}")

    def test_nfd_발췌와_nfc_원문도_대조된다(self):
        """반대 방향. 발췌가 NFD 로 들어온 경우."""
        excerpt_nfd = unicodedata.normalize("NFD", EXCERPT)
        result = verify_excerpt(excerpt_nfd, RAW)
        self.assertTrue(result.ok, f"실패 사유: {result.reason}")

    def test_crlf_원문이_lf_발췌와_대조된다(self):
        """Windows 에서 커밋하거나 붙여넣어 CRLF 가 된 원문."""
        raw_crlf = RAW.replace("\n", "\r\n")
        result = verify_excerpt(EXCERPT, raw_crlf)
        self.assertTrue(result.ok, f"실패 사유: {result.reason}")

    def test_줄바꿈을_넘어가는_발췌도_대조된다(self):
        """AI 가 두 줄에 걸친 구간을 한 줄로 붙여 내보내는 경우."""
        excerpt = "휴학생은 지원 대상에서 제외합니다 - 타 청년 지원금을 수혜 중인 자는"
        for raw in (RAW, RAW.replace("\n", "\r\n")):
            with self.subTest(raw="CRLF" if "\r" in raw else "LF"):
                self.assertTrue(verify_excerpt(excerpt, raw).ok)

    def test_bom이_붙은_원문(self):
        """Windows Excel 이 UTF-8 로 저장하면 선두에 BOM 이 붙는다."""
        result = verify_excerpt(EXCERPT, "\ufeff" + RAW)
        self.assertTrue(result.ok, f"실패 사유: {result.reason}")

    def test_비분리공백과_전각공백(self):
        """HTML 공고 복붙에서 흔한 형태."""
        raw = RAW.replace("휴학생은 지원", "휴학생은\u00a0지원").replace(
            "대상에서 제외", "대상에서\u3000제외"
        )
        self.assertNotIn(EXCERPT, raw, "전제 확인: 정규화 없이는 실패한다")
        self.assertTrue(verify_excerpt(EXCERPT, raw).ok)

    def test_제로폭_문자(self):
        raw = RAW.replace("휴학생", "휴\u200b학생")
        self.assertNotIn(EXCERPT, raw, "전제 확인: 정규화 없이는 실패한다")
        self.assertTrue(verify_excerpt(EXCERPT, raw).ok)

    def test_세_요인이_한꺼번에_섞인_경우(self):
        raw = "\ufeff" + unicodedata.normalize(
            "NFD", RAW.replace("\n", "\r\n").replace("휴학생은 지원", "휴학생은\u00a0지원")
        )
        self.assertTrue(verify_excerpt(EXCERPT, raw).ok)


class TestExcerptRecovery(unittest.TestCase):
    """화면에 나가는 발췌는 항상 원문 쪽이어야 한다 (원문 우선 원칙)."""

    def test_통과하면_원문에서_되찾은_구간을_돌려준다(self):
        excerpt = "휴학생은   지원\n대상에서 제외합니다"  # 공백이 원문과 다름
        result = verify_excerpt(excerpt, RAW)
        self.assertTrue(result.ok)
        self.assertEqual(result.excerpt, EXCERPT)
        self.assertIn(result.excerpt, canonical(RAW), "되찾은 구간은 원문에 그대로 있어야 한다")

    def test_nfd_원문이어도_되찾은_구간은_표준형이다(self):
        raw_nfd = unicodedata.normalize("NFD", RAW)
        result = verify_excerpt(EXCERPT, raw_nfd)
        self.assertTrue(result.ok)
        self.assertEqual(
            result.excerpt,
            unicodedata.normalize("NFC", result.excerpt),
            "화면에 나가는 값은 NFC 로 통일되어야 한다",
        )


class TestGuardrail(unittest.TestCase):
    """근거 없는 내용은 반드시 막아야 한다."""

    def test_환각_발췌는_실패한다(self):
        result = verify_excerpt("소득이 기준 중위소득 150% 이하인 자에 한합니다", RAW)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "not_found")
        self.assertIsNone(result.excerpt)

    def test_한_글자_바꾼_발췌도_실패한다(self):
        """정규화가 문장 내용까지 뭉개지 않는지 확인한다."""
        self.assertFalse(verify_excerpt("휴학생은 지원 대상에서 제외됩니다", RAW).ok)

    def test_너무_짧은_발췌는_실패한다(self):
        result = verify_excerpt("제외합니다", RAW)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "too_short")

    def test_빈_발췌와_빈_원문(self):
        self.assertEqual(verify_excerpt("", RAW).reason, "empty_excerpt")
        self.assertEqual(verify_excerpt("   \n  ", RAW).reason, "empty_excerpt")
        self.assertEqual(verify_excerpt(EXCERPT, "").reason, "empty_raw")

    def test_상한_초과_발췌는_잘라서_검증한다(self):
        """길이 때문에 맞는 판정을 버리지 않는다.

        긴 예외 문장을 AI 가 통째로 인용하는 경우가 실제로 생긴다.
        """
        long_raw = RAW + (
            "  - 최근 3년 이내 본 사업에 참여한 이력이 있는 자, 타 지방자치단체의 "
            "유사 사업에 참여 중인 자, 그 밖에 사업 목적에 부합하지 않는다고 "
            "심의위원회가 판단하는 자는 지원 대상에서 제외합니다\n"
        )
        long_excerpt = normalize(long_raw)  # 원문 전체
        self.assertGreater(len(long_excerpt), 150, "전제 확인: 상한을 넘는 발췌여야 한다")

        result = verify_excerpt(long_excerpt, long_raw)
        self.assertTrue(result.ok, f"실패 사유: {result.reason}")
        self.assertTrue(result.truncated)
        self.assertIn(result.excerpt, canonical(long_raw))
        self.assertLessEqual(len(normalize(result.excerpt)), 150)

    def test_상한_초과이면서_원문에_없으면_실패한다(self):
        """자르기가 환각을 통과시키는 구멍이 되지 않아야 한다.

        앞 150자가 원문에 없으면 그대로 실패해야 한다.
        """
        fake = "가구 소득이 기준 중위소득 150% 이하인 무주택 세대주로서 " * 6
        self.assertGreater(len(normalize(fake)), 150)
        result = verify_excerpt(fake, RAW)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "not_found")


class TestVerifyConditions(unittest.TestCase):
    """설계서 6-4 실패 처리."""

    def test_실패한_조건은_미확인으로_내려가고_화면에서_빠진다(self):
        conditions = [
            {"summary": "휴학생 제외", "result": "미충족", "excerpt": EXCERPT},
            {
                "summary": "소득 기준 150% 이하",
                "result": "미충족",
                "excerpt": "가구 소득이 기준 중위소득 150% 이하여야 합니다",
            },
        ]
        checked, removed = verify_conditions(conditions, RAW)

        self.assertEqual(len(checked), 2, "조건 개수는 유지된다 (판정 상태 계산에 필요)")

        self.assertEqual(checked[0]["result"], "미충족")
        self.assertEqual(checked[0]["summary"], "휴학생 제외")
        self.assertTrue(checked[0]["excerpt_verified"])

        self.assertEqual(checked[1]["result"], "미확인", "환각 조건은 미충족으로 남겨선 안 된다")
        self.assertEqual(checked[1]["summary"], "")
        self.assertIsNone(checked[1]["excerpt"])
        self.assertEqual(checked[1]["needed_field"], "공고 확인 필요")

        self.assertEqual(len(removed), 1)
        self.assertEqual(removed[0]["reason"], "not_found")

    def test_조건이_없으면_빈_결과(self):
        checked, removed = verify_conditions([], RAW)
        self.assertEqual(checked, [])
        self.assertEqual(removed, [])


class TestDataInvariant(unittest.TestCase):
    """exceptions_text 가 raw_text 안에 있어야 한다 (데이터 검수)."""

    def test_원문에_포함된_예외_문장은_통과(self):
        self.assertTrue(raw_text_covers(EXCERPT, RAW))

    def test_플랫폼_차이가_있어도_통과(self):
        self.assertTrue(raw_text_covers(EXCERPT, unicodedata.normalize("NFD", RAW)))
        self.assertTrue(raw_text_covers(EXCERPT, RAW.replace("\n", "\r\n")))

    def test_다듬어_옮긴_예외_문장은_걸러진다(self):
        """백엔드A 가 요약하거나 고쳐 쓴 경우. 이 정책은 전건 실패하므로 미리 잡아야 한다."""
        self.assertFalse(raw_text_covers("휴학생 지원 불가", RAW))

    def test_예외_조건이_없으면_문제없음(self):
        self.assertTrue(raw_text_covers("", RAW))


if __name__ == "__main__":
    unittest.main(verbosity=2)
