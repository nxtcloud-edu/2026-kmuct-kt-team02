"""AI B 내부 조건 → API 조건 경계 테스트."""

import unittest

from ai.judgment.citation import placeholder_unknown, verify_conditions
from ai.judgment.integration import (
    ISSUE_INVALID_SHAPE,
    ISSUE_INVALID_VALUE,
    ISSUE_NOT_VERIFIED,
    ISSUE_SOURCE_URL_MISSING,
    ISSUE_UNGROUNDED,
    public_conditions,
)
from ai.judgment.values import BY_AI, MET, UNKNOWN, UNMET

URL = "https://example.go.kr/notice/1"
RAW = "○ 제외 대상\n  - 휴학생은 지원 대상에서 제외합니다\n"
EXCERPT = "휴학생은 지원 대상에서 제외합니다"


def verified(**overrides):
    item = {
        "name": "휴학생 제외",
        "result": UNMET,
        "judged_by": BY_AI,
        "excerpt": EXCERPT,
        "needed_field": None,
        "excerpt_verified": True,
    }
    item.update(overrides)
    return item


class TestPublicConditions(unittest.TestCase):
    def test_검증된_조건만_API_필드로_만든다(self):
        batch = public_conditions([verified()], source_url=URL)
        self.assertEqual(batch.hidden_count, 0)
        self.assertEqual(batch.issues, [])
        self.assertEqual(
            batch.conditions[0],
            {
                "name": "휴학생 제외",
                "result": UNMET,
                "judged_by": BY_AI,
                "excerpt": EXCERPT,
                "source_url": URL,
                "footnote_id": 1,
                "needed_field": None,
            },
        )

    def test_내부용_키가_응답에_나가지_않는다(self):
        condition = verified(
            excerpt_truncated=True,
            placeholder_reason="x",
            made_up="internal",
        )
        public = public_conditions([condition], source_url=URL).conditions[0]
        self.assertEqual(
            set(public),
            {
                "name",
                "result",
                "judged_by",
                "excerpt",
                "source_url",
                "footnote_id",
                "needed_field",
            },
        )

    def test_unknown만_needed_field를_유지한다(self):
        unknown = verified(result=UNKNOWN, needed_field="other_benefit")
        met = verified(result=MET, needed_field="other_benefit")
        result = public_conditions([unknown, met], source_url=URL).conditions
        self.assertEqual(result[0]["needed_field"], "other_benefit")
        self.assertIsNone(result[1]["needed_field"])

    def test_각주가_순서대로_붙는다(self):
        conditions = [verified(name=f"조건 {i}") for i in range(3)]
        batch = public_conditions(conditions, source_url=URL, start_footnote_id=4)
        self.assertEqual([c["footnote_id"] for c in batch.conditions], [4, 5, 6])
        self.assertEqual(batch.next_footnote_id, 7)

    def test_API_화면_순서로_정렬한_뒤_각주를_붙인다(self):
        """`docs/03-api-contract.md` 4-3: unmet → unknown → met."""
        conditions = [
            verified(name="충족 조건", result=MET),
            verified(name="확인 필요", result=UNKNOWN, needed_field="other_benefit"),
            verified(name="제외 대상", result=UNMET),
        ]
        batch = public_conditions(conditions, source_url=URL)
        self.assertEqual(
            [c["result"] for c in batch.conditions], [UNMET, UNKNOWN, MET]
        )
        self.assertEqual([c["footnote_id"] for c in batch.conditions], [1, 2, 3])
        self.assertEqual(
            [c["name"] for c in batch.conditions],
            ["제외 대상", "확인 필요", "충족 조건"],
        )

    def test_여러_정책이_각주_번호를_이어쓴다(self):
        first = public_conditions([verified()], source_url=URL)
        second = public_conditions(
            [verified(name="중복 수혜 제한")],
            source_url="https://example.go.kr/notice/2",
            start_footnote_id=first.next_footnote_id,
        )
        self.assertEqual(first.conditions[0]["footnote_id"], 1)
        self.assertEqual(second.conditions[0]["footnote_id"], 2)


class TestHiddenInternalConditions(unittest.TestCase):
    """상태 계산에는 남지만 화면에는 나가면 안 되는 조건."""

    def test_판정_실패_자리표시는_숨긴다(self):
        batch = public_conditions([placeholder_unknown("timeout")], source_url=URL)
        self.assertEqual(batch.conditions, [])
        self.assertEqual(batch.hidden_count, 1)
        self.assertIn(ISSUE_NOT_VERIFIED, batch.issues)

    def test_인용_검증_실패한_조건은_숨긴다(self):
        bad = {
            "name": "지어낸 조건",
            "result": UNMET,
            "judged_by": BY_AI,
            "excerpt": "원문에 없는 조건입니다 정말로",
            "needed_field": None,
        }
        checked, removed = verify_conditions([bad], RAW)
        self.assertEqual(len(removed), 1)
        self.assertEqual(checked[0]["result"], UNKNOWN)
        batch = public_conditions(checked, source_url=URL)
        self.assertEqual(batch.conditions, [])
        self.assertEqual(batch.hidden_count, 1)

    def test_검증_전_조건도_숨긴다(self):
        item = verified()
        item.pop("excerpt_verified")
        batch = public_conditions([item], source_url=URL)
        self.assertEqual(batch.conditions, [])
        self.assertIn(ISSUE_NOT_VERIFIED, batch.issues)

    def test_검증_실패_조건은_내부에는_남는다(self):
        bad = verified(excerpt="원문에 없는 발췌입니다 정말로")
        checked, _ = verify_conditions([bad], RAW)
        self.assertEqual(len(checked), 1, "정책 상태 계산에는 unknown 조건이 남아야 한다")
        self.assertEqual(checked[0]["result"], UNKNOWN)
        self.assertEqual(public_conditions(checked, source_url=URL).conditions, [])


class TestInvalidInputs(unittest.TestCase):
    def test_source_url이_없으면_모두_숨긴다(self):
        for url in (None, "", "   "):
            with self.subTest(url=url):
                batch = public_conditions([verified()], source_url=url)
                self.assertEqual(batch.conditions, [])
                self.assertEqual(batch.hidden_count, 1)
                self.assertIn(ISSUE_SOURCE_URL_MISSING, batch.issues)

    def test_이름이_비면_숨긴다(self):
        batch = public_conditions([verified(name="")], source_url=URL)
        self.assertEqual(batch.conditions, [])
        self.assertIn(ISSUE_INVALID_SHAPE, batch.issues)

    def test_결과_값이_틀리면_숨긴다(self):
        batch = public_conditions([verified(result="maybe")], source_url=URL)
        self.assertEqual(batch.conditions, [])
        self.assertIn(ISSUE_INVALID_SHAPE, batch.issues)

    def test_서버_계약_밖_판정_주체는_숨긴다(self):
        """서버 `JudgedBy` 는 rule 과 ai 뿐이다."""
        batch = public_conditions([verified(judged_by="invented")], source_url=URL)
        self.assertEqual(batch.conditions, [])
        self.assertIn(ISSUE_INVALID_VALUE, batch.issues)

    def test_허용_목록_밖_needed_field는_숨긴다(self):
        condition = verified(result=UNKNOWN, needed_field="not_in_contract")
        batch = public_conditions([condition], source_url=URL)
        self.assertEqual(batch.conditions, [])
        self.assertIn(ISSUE_INVALID_VALUE, batch.issues)

    def test_http가_아닌_출처는_모두_숨긴다(self):
        """서버 `source_url` 은 HttpUrl 이다."""
        for url in ("not-a-url", "ftp://example.go.kr/a", "example.go.kr"):
            with self.subTest(url=url):
                batch = public_conditions([verified()], source_url=url)
                self.assertEqual(batch.conditions, [])
                self.assertIn(ISSUE_SOURCE_URL_MISSING, batch.issues)

    def test_발췌_길이가_서버_계약을_벗어나면_숨긴다(self):
        """서버 `excerpt` 는 10~150자다."""
        for excerpt in ("짧다", "가" * 151):
            with self.subTest(length=len(excerpt)):
                batch = public_conditions([verified(excerpt=excerpt)], source_url=URL)
                self.assertEqual(batch.conditions, [])
                self.assertIn(ISSUE_INVALID_SHAPE, batch.issues)

    def test_이름이_상한을_넘으면_숨긴다(self):
        batch = public_conditions([verified(name="가" * 21)], source_url=URL)
        self.assertEqual(batch.conditions, [])
        self.assertIn(ISSUE_INVALID_SHAPE, batch.issues)


class TestUngroundedSummary(unittest.TestCase):
    """발췌는 원문에 있는데 요약이 원문에 없는 숫자를 말하는 경우."""

    def test_원문에_없는_숫자를_말하면_숨긴다(self):
        raw = "○ 지원 대상\n  - 가구 소득이 기준 중위소득 150% 이하\n" + RAW
        condition = verified(name="소득 180% 이하", excerpt=EXCERPT)
        batch = public_conditions([condition], source_url=URL, raw_text=raw)
        self.assertEqual(batch.conditions, [], "인용 검증은 통과하지만 요약이 지어낸 숫자다")
        self.assertIn(ISSUE_UNGROUNDED, batch.issues)

    def test_원문에_있는_숫자는_공개한다(self):
        raw = "○ 지원 대상\n  - 가구 소득이 기준 중위소득 150% 이하\n" + RAW
        condition = verified(name="소득 150% 이하", excerpt=EXCERPT)
        batch = public_conditions([condition], source_url=URL, raw_text=raw)
        self.assertEqual(len(batch.conditions), 1)

    def test_raw_text를_주지_않으면_검사하지_않는다(self):
        condition = verified(name="소득 180% 이하", excerpt=EXCERPT)
        batch = public_conditions([condition], source_url=URL)
        self.assertEqual(len(batch.conditions), 1)

    def test_빈_목록(self):
        batch = public_conditions([], source_url=URL)
        self.assertEqual(batch.conditions, [])
        self.assertEqual(batch.next_footnote_id, 1)
        self.assertEqual(batch.hidden_count, 0)

    def test_각주_시작값은_최소_1이다(self):
        batch = public_conditions([verified()], source_url=URL, start_footnote_id=0)
        self.assertEqual(batch.conditions[0]["footnote_id"], 1)


if __name__ == "__main__":
    unittest.main()
