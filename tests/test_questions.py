"""후속 질문 고정 문구 테스트 (ai/conversation/README.md 5장).

이 표는 **화면에 그대로 나가는 문구**이고, 선택지 값은 **프로필에 그대로 들어가는 값**이다.
그래서 두 방향으로 어긋날 수 있고 둘 다 조용히 깨진다.

1. 항목에 틀이 없으면 그 항목은 영원히 안 물어진다. 규칙 엔진이 미확인으로 계속 내도
   ``followup`` 이 틀 없는 항목을 버리기 때문에 화면에는 아무 일도 일어나지 않는다.
2. 선택지 값이 ``docs/01-glossary-profile.md`` 의 허용 값과 다르면, 사용자가 버튼을
   눌렀는데 규칙 엔진이 그 값을 모른다. 그래서 값은 문서 표와 하나씩 대조한다.
"""

import unittest

from ai.conversation import fields
from ai.conversation.questions import (
    MAX_REASON_POLICIES,
    PLANNED_BASIS_QUESTION_FALLBACK,
    PLANNED_BASIS_QUESTIONS,
    PLANNED_BASIS_REASON_FALLBACK,
    PLANNED_BASIS_REASONS,
    PLANNED_CHANGE_QUESTION,
    TEMPLATES,
    build,
    get,
    missing_templates,
    planned_question,
    planned_reason,
)

# docs/01-glossary-profile.md 2장(가구 소득) 순서 그대로.
# 순서까지 고정하는 이유는 버튼 순서가 화면 순서이기 때문이다.
INCOME_VALUES = ("under_50", "50_100", "100_150", "over_150", "unknown")

# docs/01-glossary-profile.md 2장 자치구 목록 (25개).
DISTRICTS = (
    "강남구", "강동구", "강북구", "강서구", "관악구", "광진구", "구로구", "금천구",
    "노원구", "도봉구", "동대문구", "동작구", "마포구", "서대문구", "서초구", "성동구",
    "성북구", "송파구", "양천구", "영등포구", "용산구", "은평구", "종로구", "중구",
    "중랑구",
)

# docs/01-glossary-profile.md 3장 추가 항목 허용 값. 항목마다 문서를 열어 옮겼다.
ALLOWED_VALUES = {
    fields.INCOME_BRACKET: set(INCOME_VALUES),
    fields.DISTRICT: set(DISTRICTS),
    fields.HOUSING_TYPE: {"parents", "monthly_rent", "jeonse", "dormitory", "other"},
    fields.RESIDENCE_PERIOD: {"under_6m", "6m_1y", "over_1y"},
    fields.REMAINING_SEMESTERS: {"one", "two_plus"},
    fields.JOB_SEEKING_PERIOD: {"under_6m", "over_6m"},
    fields.EMPLOYMENT_INSURANCE: {"yes", "no", "unknown"},
    fields.OTHER_BENEFIT: {"yes", "no"},
    fields.HOUSEHOLD_SIZE: {"1", "2", "3", "4_plus"},
    fields.LAST_GPA: {"above", "below", "unknown"},
}

# README 6장 금지 표현. 질문 문구도 화면 문구이므로 같은 기준을 적용한다.
BANNED_PHRASES = ("대상입니다", "확실히", "무조건", "100%", "보장")

# docs/03-api-contract.md 6장 후속 질문 본문 키.
FOLLOWUP_KEYS = {
    "field",
    "question",
    "reason",
    "options",
    "allow_free_text",
    "allow_skip",
}


def values_of(field):
    """항목 선택지의 값 목록. 순서 유지."""
    return [option.value for option in TEMPLATES[field].options]


class TestTemplateCoverage(unittest.TestCase):
    """표에 빠진 항목이 없는지. 빠지면 그 항목은 영원히 안 물어진다."""

    def test_모든_추가_항목에_질문_틀이_있다(self):
        """``followup`` 은 틀 없는 항목을 조용히 버린다. 그래서 여기서 잡아야 한다."""
        self.assertEqual(
            missing_templates(),
            [],
            "질문 틀이 없는 항목이 있다. 이 항목은 후속 질문으로 나가지 않는다",
        )

    def test_표에_없는_항목은_틀이_없다(self):
        """반대 방향. 물을 수 없는 항목에 틀을 만들어 두면 폼 값을 또 묻게 된다."""
        for field in (fields.AGE, fields.REGION, fields.CATEGORIES, fields.ASK_NOTICE):
            with self.subTest(field=field):
                self.assertIsNone(get(field), f"{field} 에 질문 틀이 있다")

    def test_틀의_field가_키와_같다(self):
        """키와 내용이 어긋나면 답변이 다른 항목에 저장된다."""
        for field, template in TEMPLATES.items():
            with self.subTest(field=field):
                self.assertEqual(template.field, field)

    def test_모든_틀에_선택지가_하나_이상_있다(self):
        """선택지가 없으면 버튼 없는 질문이 나가고, 사용자는 직접 입력만 남는다."""
        for field, template in TEMPLATES.items():
            with self.subTest(field=field):
                self.assertGreaterEqual(
                    len(template.options), 1, f"{field}: 선택지가 비어 있다"
                )


class TestOptionValues(unittest.TestCase):
    """선택지 값은 프로필에 그대로 들어간다. 문서 허용 값과 어긋나면 규칙 엔진이 모른다."""

    def test_선택지_값이_문서_허용_값_안에_있다(self):
        for field, template in TEMPLATES.items():
            allowed = ALLOWED_VALUES[field]
            with self.subTest(field=field):
                actual = {option.value for option in template.options}
                self.assertTrue(
                    actual <= allowed,
                    f"{field}: 문서에 없는 값 {sorted(actual - allowed)}",
                )

    def test_소득_구간은_문서의_다섯_값_그대로다(self):
        """소득은 판정이 가장 많이 갈리는 항목이다. 값 하나만 달라도 조건이 안 걸린다."""
        self.assertEqual(
            tuple(values_of(fields.INCOME_BRACKET)),
            INCOME_VALUES,
            "소득 구간 선택지가 문서 2장의 5개 값과 다르다",
        )

    def test_자치구_선택지는_25개_목록_안에_있다(self):
        district_values = values_of(fields.DISTRICT)
        for value in district_values:
            with self.subTest(district=value):
                self.assertIn(value, DISTRICTS, f"{value} 는 서울 자치구 목록에 없다")

    def test_자치구는_일부만_버튼이라_직접_입력을_허용한다(self):
        """25개를 다 깔지 않기로 했으므로 나머지를 받을 길이 있어야 한다."""
        template = TEMPLATES[fields.DISTRICT]
        self.assertLess(len(template.options), len(DISTRICTS))
        self.assertTrue(
            template.allow_free_text,
            "버튼에 없는 구에 사는 사용자가 답할 방법이 없다",
        )

    def test_가구원_수는_문서의_네_값_그대로다(self):
        self.assertEqual(tuple(values_of(fields.HOUSEHOLD_SIZE)), ("1", "2", "3", "4_plus"))

    def test_선택지_값이_중복되지_않는다(self):
        for field, template in TEMPLATES.items():
            with self.subTest(field=field):
                values = values_of(field)
                self.assertEqual(len(values), len(set(values)), f"{field}: 값 중복")

    def test_선택지_문구가_비어_있지_않다(self):
        for field, template in TEMPLATES.items():
            for option in template.options:
                with self.subTest(field=field, value=option.value):
                    self.assertTrue(option.label.strip(), "화면에 빈 버튼이 나간다")
                    self.assertTrue(option.value.strip())


class TestWording(unittest.TestCase):
    """문구 자체. 화면에 그대로 나가므로 비어 있거나 단정하면 안 된다."""

    def test_질문과_이유가_비어_있지_않다(self):
        for field, template in TEMPLATES.items():
            with self.subTest(field=field):
                self.assertTrue(template.question.strip(), f"{field}: 질문이 비었다")
                self.assertTrue(template.reason.strip(), f"{field}: 이유가 비었다")

    def test_질문과_이유에_금지_표현이_없다(self):
        """README 6장. 후속 질문 단계에서 단정하면 판정 전에 결론을 말하는 셈이다."""
        for field, template in TEMPLATES.items():
            for phrase in BANNED_PHRASES:
                with self.subTest(field=field, phrase=phrase):
                    self.assertNotIn(phrase, template.question, f"{field}: 질문")
                    self.assertNotIn(phrase, template.reason, f"{field}: 이유")

    def test_질문이_물음표로_끝난다(self):
        """질문 카드에 서술문이 들어가면 답을 요구하는 것으로 읽히지 않는다."""
        for field, template in TEMPLATES.items():
            with self.subTest(field=field):
                self.assertTrue(
                    template.question.rstrip().endswith("?"),
                    f"{field}: 질문 문구가 물음표로 끝나지 않는다",
                )


class TestRenderReason(unittest.TestCase):
    """이유 문구 채우기. 빈 자리가 남으면 미완성 화면처럼 보인다."""

    def test_정책명이_있으면_이름이_들어간다(self):
        template = TEMPLATES[fields.INCOME_BRACKET]
        reason = template.render_reason(["청년월세지원"], 1)
        self.assertIn("청년월세지원", reason)
        self.assertIn("1", reason)

    def test_정책명이_없으면_개수로만_말한다(self):
        """규칙 엔진이 제목을 안 넘기는 경우가 있다. 그때도 문장이 성립해야 한다."""
        reason = TEMPLATES[fields.INCOME_BRACKET].render_reason([], 3)
        self.assertIn("3", reason)
        self.assertTrue(reason.strip())

    def test_어느_경우에도_빈_자리가_남지_않는다(self):
        """○○ 나 {policies} 가 화면에 나가면 그 자리에서 미완성처럼 보인다."""
        for field, template in TEMPLATES.items():
            for label, titles, count in (
                ("정책명 있음", ["청년월세지원", "청년수당"], 2),
                ("정책명 없음", [], 2),
                ("하나", ["청년수당"], 1),
                ("개수 0", [], 0),
            ):
                with self.subTest(field=field, case=label):
                    reason = template.render_reason(titles, count)
                    self.assertTrue(reason.strip(), f"{field}: 이유가 비었다")
                    self.assertNotIn("○○", reason, f"{field}: 미완성 표시가 남았다")
                    self.assertNotIn("{", reason, f"{field}: 치환되지 않은 자리")
                    self.assertNotIn("}", reason)

    def test_정책명이_많으면_등으로_줄인다(self):
        """질문 한 줄이 길어지면 읽히지 않는다. 그래서 상한을 둔다."""
        titles = ["가", "나", "다", "라"]
        self.assertGreater(len(titles), MAX_REASON_POLICIES, "전제 확인")

        reason = TEMPLATES[fields.INCOME_BRACKET].render_reason(titles, len(titles))
        self.assertIn("등", reason, "상한을 넘겼는데 줄이지 않았다")
        for shown in titles[:MAX_REASON_POLICIES]:
            self.assertIn(shown, reason)
        for hidden in titles[MAX_REASON_POLICIES:]:
            self.assertNotIn(hidden, reason, f"{hidden} 이 문구에 그대로 남았다")

    def test_상한_이하면_등을_붙이지_않는다(self):
        reason = TEMPLATES[fields.INCOME_BRACKET].render_reason(["가", "나"], 2)
        self.assertNotIn("등", reason, "다 보여줬는데 더 있는 것처럼 읽힌다")

    def test_보여준_개수보다_전체가_많으면_등을_붙인다(self):
        """제목은 두 개뿐인데 영향 정책이 다섯 개인 경우."""
        reason = TEMPLATES[fields.INCOME_BRACKET].render_reason(["가", "나"], 5)
        self.assertIn("등", reason)
        self.assertIn("5", reason)


class TestBuild(unittest.TestCase):
    """docs/03-api-contract.md 6장 본문 만들기."""

    def test_알_수_없는_항목은_None(self):
        """표에 없는 항목을 즉석에서 만들어 묻지 않는다."""
        for field in ("nickname", "", fields.ASK_NOTICE, fields.AGE):
            with self.subTest(field=field):
                self.assertIsNone(build(field), f"{field} 에 대해 질문이 만들어졌다")

    def test_계약에_적힌_키만_들어_있다(self):
        payload = build(fields.HOUSING_TYPE, ["청년월세지원"], 1)
        self.assertEqual(set(payload), FOLLOWUP_KEYS)

    def test_모든_항목이_본문을_만들_수_있다(self):
        for field in fields.FIELD_ORDER:
            with self.subTest(field=field):
                payload = build(field, ["청년수당"], 1)
                self.assertIsNotNone(payload, f"{field}: 본문이 만들어지지 않았다")
                self.assertEqual(payload["field"], field)
                self.assertTrue(payload["question"].strip())
                self.assertTrue(payload["reason"].strip())
                self.assertGreaterEqual(len(payload["options"]), 1)
                self.assertIs(payload["allow_skip"], True, "건너뛰기는 항상 허용한다")
                self.assertIsInstance(payload["allow_free_text"], bool)

    def test_선택지가_값과_문구_쌍으로_직렬화된다(self):
        payload = build(fields.LAST_GPA)
        self.assertEqual(
            payload["options"],
            [
                {"value": "above", "label": "기준 이상"},
                {"value": "below", "label": "기준 미만"},
                {"value": "unknown", "label": "모르겠어요"},
            ],
        )

    def test_개수를_주지_않으면_정책명_수로_센다(self):
        payload = build(fields.INCOME_BRACKET, ["가", "나", "다"])
        self.assertIn("3", payload["reason"])


class TestPlannedChangeQuestion(unittest.TestCase):
    """미래 계획 확인 질문 (README 3장, C4).

    프로필을 바꾸지 않고 어느 기준으로 볼지 묻는 질문이다. 선택지 값이 시점 값과
    같아야 서버가 해석할 수 있다.
    """

    def test_선택지가_시점_값_두_개다(self):
        self.assertEqual(
            {option.value for option in PLANNED_CHANGE_QUESTION.options},
            {fields.PLANNED, fields.CURRENT},
        )

    def test_문구가_비어_있지_않고_금지_표현이_없다(self):
        self.assertTrue(PLANNED_CHANGE_QUESTION.question.strip())
        self.assertTrue(PLANNED_CHANGE_QUESTION.reason.strip())
        for phrase in BANNED_PHRASES:
            with self.subTest(phrase=phrase):
                self.assertNotIn(phrase, PLANNED_CHANGE_QUESTION.question)


class TestPlannedBasisRegionRemoved(unittest.TestCase):
    """거주지에는 planned 확인 질문을 만들지 않는다.

    이 테스트가 지키려는 계약
      거주지는 `seoul` 고정이라(``docs/01-glossary-profile.md`` 2장)
      ``interpret._clean_change`` 가 `region` 변경을 시점과 무관하게 버린다. 보류되는
      일이 없으므로 "거주지가 바뀐 뒤 기준" 이라는 시점도 없다. 표에 행을 남겨 두면
      도달할 수 없는 문구가 화면 문구 목록에 섞여, 다음 사람이 거주지를 대화로 바꿀 수
      있다고 읽는다.
    """

    def test_두_표에_거주지_행이_없다(self):
        for label, table in (
            ("질문 틀", PLANNED_BASIS_QUESTIONS),
            ("이유 문구", PLANNED_BASIS_REASONS),
        ):
            with self.subTest(table=label):
                self.assertNotIn(
                    fields.REGION,
                    table,
                    f"{label} 표에 거주지 행이 남아 있다: {table.get(fields.REGION)!r}",
                )

    def test_거주지를_넘기면_항목_이름_없는_기본_문구로_떨어진다(self):
        """표에 없는 항목의 처리와 같아야 한다. 즉석 작문으로 새 문구를 만들지 않는다."""
        question = planned_question(fields.REGION, "서울 밖")
        self.assertEqual(
            question,
            PLANNED_BASIS_QUESTION_FALLBACK,
            f"거주지 전용 질문이 만들어졌다: {question!r}",
        )
        reason = planned_reason(fields.REGION)
        self.assertEqual(
            reason,
            PLANNED_BASIS_REASON_FALLBACK,
            f"거주지 전용 이유 문구가 만들어졌다: {reason!r}",
        )

    def test_어느_표에도_거주지를_말하는_문구가_없다(self):
        """항목 이름을 다른 키로 옮겨 적는 실수까지 잡는다."""
        for table_label, table in (
            ("질문 틀", PLANNED_BASIS_QUESTIONS),
            ("이유 문구", PLANNED_BASIS_REASONS),
        ):
            for field, text in table.items():
                with self.subTest(table=table_label, field=field):
                    self.assertNotIn(
                        "거주지",
                        text,
                        f"{table_label}[{field}] 가 거주지를 말한다: {text!r}",
                    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
