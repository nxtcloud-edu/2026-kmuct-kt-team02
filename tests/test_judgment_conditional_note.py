"""조건부 문장 테스트 (`ai/judgment/conditional_note.py`).

고정 형식을 벗어나지 않는지, 값을 채울 수 없을 때 지어내지 않는지를 본다.
README 14장 완료 기준의 "조건부 문장이 고정 형식을 벗어나지 않음"에 해당한다.
"""

import unittest

from ai.judgment.conditional_note import (
    build_conditional_note,
    exception_phrase,
    extra_field_phrase,
    income_phrase,
)
from ai.judgment.values import (
    BY_AI,
    BY_RULE,
    EXTRA_CONDITION_CLAUSES,
    MET,
    UNKNOWN,
    UNMET,
)

TAIL = " 신청 가능해요"


def cond(result, *, name="", judged_by=BY_AI, needed_field=None, **extra):
    item = {
        "name": name,
        "result": result,
        "judged_by": judged_by,
        "needed_field": needed_field,
    }
    item.update(extra)
    return item


class TestIncomePhrase(unittest.TestCase):
    def test_상한을_넣어_문장을_만든다(self):
        self.assertEqual(
            income_phrase(150), "가구 소득이 기준 중위소득 150% 이하라면 신청 가능해요"
        )

    def test_문자열로_와도_처리한다(self):
        self.assertEqual(income_phrase("120"), income_phrase(120))

    def test_상한이_없으면_만들지_않는다(self):
        """소득 조건이 없는 정책. 지어낸 숫자를 넣으면 안 된다."""
        self.assertIsNone(income_phrase(None))
        self.assertIsNone(income_phrase(""))
        self.assertIsNone(income_phrase("해당없음"))


class TestExtraFieldPhrase(unittest.TestCase):
    """문구 표에서 고른 조건절이 읽히는 문장이 되어야 한다.

    "(항목)이 (값)이라면" 을 기계적으로 만들면 "다른 지원 수혜가 없음이라면" 처럼
    말이 되지 않는다. 그래서 조합마다 조건절을 표에 적어 두고 거기서 고른다.
    """

    def test_문구_표에서_고른_문장(self):
        self.assertEqual(
            extra_field_phrase("housing_type", "monthly_rent"),
            "월세로 살고 있다면 신청 가능해요",
        )
        self.assertEqual(
            extra_field_phrase("last_gpa", "above"),
            "직전 학기 성적이 공고 기준 이상이라면 신청 가능해요",
        )

    def test_있음_없음_값이_어색하지_않다(self):
        """기계적 조립의 가장 나쁜 사례였다."""
        self.assertEqual(
            extra_field_phrase("other_benefit", "no"),
            "다른 청년 지원금을 받고 있지 않다면 신청 가능해요",
        )
        self.assertEqual(
            extra_field_phrase("employment_insurance", "no"),
            "고용보험에 가입한 적이 없다면 신청 가능해요",
        )

    def test_값_라벨을_그대로_붙이지_않는다(self):
        """"없음이라면", "1학기라면" 같은 형태가 남아 있지 않은지 본다."""
        for field, value in (
            ("other_benefit", "no"),
            ("other_benefit", "yes"),
            ("employment_insurance", "no"),
            ("remaining_semesters", "one"),
            ("household_size", "1"),
        ):
            with self.subTest(field=field, value=value):
                phrase = extra_field_phrase(field, value)
                self.assertNotIn("없음이라면", phrase)
                self.assertNotIn("있음이라면", phrase)

    def test_표에_없는_조합은_조사를_맞춘_기본_형태로_만든다(self):
        """문구가 빠져도 문장은 나와야 한다. 비면 카드에 상태 문구만 남는다."""
        # 표에 없는 값을 넣어 폴백 경로를 지나가게 한다.
        phrase = extra_field_phrase("housing_type", "goshiwon")
        self.assertIsNotNone(phrase)
        self.assertTrue(phrase.endswith(TAIL))
        self.assertTrue(phrase.startswith("주거 형태가 "), phrase)

    def test_폴백에서_종성에_따라_조사가_갈린다(self):
        # 값이 종성 없음 -> "라면"
        self.assertIn("가나다라면", extra_field_phrase("housing_type", "가나다"))
        # 값이 종성 있음 -> "이라면"
        self.assertIn("가나닭이라면", extra_field_phrase("housing_type", "가나닭"))

    def test_표에_있는_조합은_모두_문장이_된다(self):
        for field, values in EXTRA_CONDITION_CLAUSES.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    phrase = extra_field_phrase(field, value)
                    self.assertIsNotNone(phrase)
                    self.assertTrue(phrase.endswith(TAIL))

    def test_모름을_필요한_값으로_요구하면_만들지_않는다(self):
        """정책이 "성적 모름"을 요구할 일은 없다. 데이터 오류다.

        말이 되지 않는 문장을 내보내는 것보다 다음 조건으로 넘기는 쪽이 낫다.
        """
        self.assertIsNone(extra_field_phrase("last_gpa", "unknown"))
        self.assertIsNone(extra_field_phrase("employment_insurance", "unknown"))

    def test_추가_항목이_아니면_만들지_않는다(self):
        self.assertIsNone(extra_field_phrase("income_bracket", "under_50"))
        self.assertIsNone(extra_field_phrase("made_up_field", "x"))

    def test_필요한_값을_모르면_만들지_않는다(self):
        self.assertIsNone(extra_field_phrase("housing_type", None))
        self.assertIsNone(extra_field_phrase("housing_type", ""))


class TestExceptionPhrase(unittest.TestCase):
    def test_조건_요약으로_문장을_만든다(self):
        self.assertEqual(
            exception_phrase("타 청년 지원금 중복 불가"),
            "타 청년 지원금 중복 불가에 해당하지 않는다면 신청 가능해요",
        )

    def test_요약이_없으면_만들지_않는다(self):
        """인용 검증에 실패한 조건은 요약이 비어 있다."""
        self.assertIsNone(exception_phrase(""))
        self.assertIsNone(exception_phrase("   "))
        self.assertIsNone(exception_phrase(None))


class TestBuildConditionalNote(unittest.TestCase):
    """check 상태일 때만 채운다."""

    def test_unmet이_있으면_만들지_않는다(self):
        """unlikely 상태다. 조건부 문장은 check 일 때만."""
        conditions = [
            cond(UNMET, name="휴학생 제외"),
            cond(UNKNOWN, name="소득", judged_by=BY_RULE),
        ]
        self.assertIsNone(build_conditional_note(conditions, income_max_pct=150))

    def test_전부_met이면_만들지_않는다(self):
        """likely 상태다."""
        self.assertIsNone(build_conditional_note([cond(MET, name="나이", judged_by=BY_RULE)]))

    def test_조건이_없으면_만들지_않는다(self):
        self.assertIsNone(build_conditional_note([]))

    def test_소득_하나만_미확인(self):
        conditions = [cond(UNKNOWN, name="소득", judged_by=BY_RULE)]
        self.assertEqual(
            build_conditional_note(conditions, income_max_pct=150),
            "가구 소득이 기준 중위소득 150% 이하라면 신청 가능해요",
        )

    def test_field_표시가_있으면_그것으로_소득을_알아낸다(self):
        """백엔드A 가 field 를 넣어주면 조건 요약 문구에 의존하지 않는다."""
        conditions = [cond(UNKNOWN, name="가구 소득 상한", judged_by=BY_RULE, field="income_bracket")]
        self.assertEqual(
            build_conditional_note(conditions, income_max_pct=100),
            "가구 소득이 기준 중위소득 100% 이하라면 신청 가능해요",
        )

    def test_추가_항목_미확인(self):
        conditions = [cond(UNKNOWN, name="주거 형태", judged_by=BY_RULE, needed_field="housing_type")]
        self.assertEqual(
            build_conditional_note(
                conditions, extra_conditions={"housing_type": "monthly_rent"}
            ),
            "월세로 살고 있다면 신청 가능해요",
        )

    def test_예외_조건_미확인(self):
        conditions = [cond(UNKNOWN, name="타 지원금 중복 불가", judged_by=BY_AI)]
        self.assertEqual(
            build_conditional_note(conditions),
            "타 지원금 중복 불가에 해당하지 않는다면 신청 가능해요",
        )

    def test_미확인_두개면_남은_개수를_덧붙인다(self):
        conditions = [
            cond(UNKNOWN, name="소득", judged_by=BY_RULE),
            cond(UNKNOWN, name="타 지원금 중복 불가", judged_by=BY_AI),
        ]
        self.assertEqual(
            build_conditional_note(conditions, income_max_pct=120),
            "가구 소득이 기준 중위소득 120% 이하라면 신청 가능해요 (확인할 조건 1개 더)",
        )

    def test_미확인_셋이면_두_개_더(self):
        conditions = [
            cond(UNKNOWN, name="소득", judged_by=BY_RULE),
            cond(UNKNOWN, name="타 지원금 중복 불가", judged_by=BY_AI),
            cond(UNKNOWN, name="성적 기준", judged_by=BY_RULE, needed_field="last_gpa"),
        ]
        note = build_conditional_note(conditions, income_max_pct=120)
        self.assertTrue(note.endswith("(확인할 조건 2개 더)"), note)

    def test_첫_조건을_못_만들면_다음_조건으로_넘어간다(self):
        """인용 검증에 실패해 요약이 빈 조건이 맨 위일 수 있다.

        그 조건 때문에 문장을 포기하면 카드가 상태 문구만 남는다.
        """
        conditions = [
            cond(UNKNOWN, name="", judged_by=BY_AI, needed_field="공고 확인 필요"),
            cond(UNKNOWN, name="주거", judged_by=BY_RULE, needed_field="housing_type"),
        ]
        self.assertEqual(
            build_conditional_note(
                conditions, extra_conditions={"housing_type": "monthly_rent"}
            ),
            "월세로 살고 있다면 신청 가능해요 (확인할 조건 1개 더)",
        )

    def test_전부_못_만들면_비운다(self):
        """억지로 문장을 만들어 근거 없는 내용을 내보내지 않는다."""
        conditions = [cond(UNKNOWN, name="", judged_by=BY_AI, needed_field="공고 확인 필요")]
        self.assertIsNone(build_conditional_note(conditions))

    def test_소득_조건인데_상한을_모르면_다른_조건을_쓴다(self):
        conditions = [
            cond(UNKNOWN, name="소득", judged_by=BY_RULE),
            cond(UNKNOWN, name="타 지원금 중복 불가", judged_by=BY_AI),
        ]
        note = build_conditional_note(conditions, income_max_pct=None)
        self.assertEqual(
            note,
            "타 지원금 중복 불가에 해당하지 않는다면 신청 가능해요 (확인할 조건 1개 더)",
        )

    def test_항상_고정_어미로_끝난다(self):
        """AI 가 자유롭게 쓰지 않는다는 것을 형태로 확인한다."""
        conditions = [cond(UNKNOWN, name="소득", judged_by=BY_RULE)]
        note = build_conditional_note(conditions, income_max_pct=150)
        self.assertTrue(note.endswith(TAIL))


if __name__ == "__main__":
    unittest.main(verbosity=2)
