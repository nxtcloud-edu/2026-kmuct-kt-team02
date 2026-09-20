"""후속 질문 선택 테스트 (ai/conversation/README.md 4장).

문구가 아니라 **선택**을 검증한다. 선택이 틀리면 같은 상황에서 다른 질문이 나오고,
그러면 데모 5번 장면(후속 질문에 답하면 카드가 바뀜)을 리허설할 수 없다.

특히 C2(모르겠어요라고 답한 질문을 또 받지 않음)와 C8(전부 건너뛰면 질문이 멈춤)을
가장 두껍게 덮는다. README 9장이 "이 두 개가 깨지면 후속 질문이 심문이 된다"고 적어 둔
지점이라, 나머지 우선순위 규칙보다 먼저 깨지지 않는지 본다.
"""

import unittest
from unittest import mock

from ai.conversation import fields, questions
from ai.conversation.followup import (
    AskedState,
    UnknownItem,
    choose,
    collect_candidates,
    next_question,
)

# docs/03-api-contract.md 6장의 후속 질문 본문 키. 이 집합이 계약이다.
FOLLOWUP_KEYS = {
    "field",
    "question",
    "reason",
    "options",
    "allow_free_text",
    "allow_skip",
}


def item(field, policy_id, rank=0, title=""):
    """미확인 항목 하나. 규칙 엔진이 넘기는 모양을 짧게 쓰기 위한 헬퍼."""
    return UnknownItem(
        field=field, policy_id=policy_id, policy_rank=rank, policy_title=title
    )


def chosen_field(items, state=None, intent=fields.FIND_POLICY):
    """고른 항목 이름. 고른 것이 없으면 None."""
    candidate = choose(items, state, intent)
    return None if candidate is None else candidate.field


def counts_by_field(items, state=None):
    """후보별 영향 정책 수. {항목: 개수}"""
    return {c.field: c.policy_count for c in collect_candidates(items, state)}


class TestAskedStateImmutability(unittest.TestCase):
    """세션이 들고 다니는 상태가 제자리에서 바뀌면 안 된다.

    한 턴의 상태를 다음 턴에 넘기면서 이전 턴 값을 같이 더럽히면,
    "이미 물었나" 판단이 턴 사이에 어긋난다.
    """

    def test_with_asked는_원본을_바꾸지_않는다(self):
        base = AskedState()
        after = base.with_asked(fields.INCOME_BRACKET)

        self.assertEqual(base.asked, set(), "원본의 asked 가 변경됐다")
        self.assertEqual(after.asked, {fields.INCOME_BRACKET})
        self.assertIsNot(base, after, "새 상태 객체를 돌려줘야 한다")

    def test_with_skipped는_원본을_바꾸지_않는다(self):
        base = AskedState(asked={fields.DISTRICT})
        after = base.with_skipped(fields.INCOME_BRACKET)

        self.assertEqual(base.skipped, set(), "원본의 skipped 가 변경됐다")
        self.assertEqual(after.skipped, {fields.INCOME_BRACKET})
        self.assertEqual(after.asked, {fields.DISTRICT}, "기존 asked 는 유지돼야 한다")

    def test_쌓아_올려도_이전_상태가_남아_있다(self):
        first = AskedState()
        second = first.with_asked(fields.INCOME_BRACKET)
        third = second.with_skipped(fields.DISTRICT)

        self.assertFalse(first.is_done(fields.INCOME_BRACKET))
        self.assertFalse(second.is_done(fields.DISTRICT))
        self.assertTrue(third.is_done(fields.INCOME_BRACKET))
        self.assertTrue(third.is_done(fields.DISTRICT))

    def test_물어본_것과_건너뛴_것을_모두_완료로_본다(self):
        state = AskedState(asked={fields.DISTRICT}, skipped={fields.LAST_GPA})
        self.assertTrue(state.is_done(fields.DISTRICT))
        self.assertTrue(state.is_done(fields.LAST_GPA))
        self.assertFalse(state.is_done(fields.HOUSING_TYPE))


class TestAlreadyDoneIsExcluded(unittest.TestCase):
    """C2 · C8. 같은 질문을 두 번 하지 않는다.

    규칙 엔진은 세션 상태를 모르고 미확인 목록을 계속 낸다. 걸러내는 책임이 여기 있다.
    """

    def test_건너뛴_항목은_다시_묻지_않는다(self):
        """C2. 가장 중요한 동작. 건너뛴 것을 또 물으면 대화형이라는 인상이 깨진다."""
        items = [item(fields.INCOME_BRACKET, "P1", 0, "청년월세지원")]
        state = AskedState(skipped={fields.INCOME_BRACKET})

        self.assertEqual(counts_by_field(items, state), {})
        self.assertIsNone(
            choose(items, state),
            "건너뛴 항목이 후보에 남아 다시 선택됐다",
        )
        self.assertIsNone(next_question(items, state))

    def test_이미_물어본_항목은_다시_묻지_않는다(self):
        items = [item(fields.INCOME_BRACKET, "P1")]
        state = AskedState(asked={fields.INCOME_BRACKET})
        self.assertIsNone(choose(items, state), "이미 물은 항목이 다시 선택됐다")

    def test_건너뛴_항목_대신_다음_항목으로_넘어간다(self):
        """C2 의 뒷부분. 멈추는 게 아니라 다음 항목을 물어야 한다."""
        items = [
            item(fields.INCOME_BRACKET, "P1", 0),
            item(fields.HOUSING_TYPE, "P2", 1),
        ]
        state = AskedState(skipped={fields.INCOME_BRACKET})
        self.assertEqual(chosen_field(items, state), fields.HOUSING_TYPE)

    def test_전부_건너뛰면_질문이_멈춘다(self):
        """C8. 모든 항목을 건너뛴 뒤에는 더 묻지 않는다."""
        items = [
            item(field, f"P{index}", index)
            for index, field in enumerate(fields.FIELD_ORDER)
        ]
        state = AskedState(skipped=set(fields.FIELD_ORDER))

        self.assertEqual(collect_candidates(items, state), [])
        self.assertIsNone(choose(items, state), "전부 건너뛴 뒤에도 질문이 나왔다")
        self.assertIsNone(next_question(items, state))

    def test_같은_항목이_여러_정책에서_와도_한_번_걸러진다(self):
        """건너뛴 항목이 정책 수만큼 여러 번 들어오는 것이 실제 입력 모양이다."""
        items = [
            item(fields.LAST_GPA, "P1", 0),
            item(fields.LAST_GPA, "P2", 1),
            item(fields.LAST_GPA, "P3", 2),
        ]
        state = AskedState(skipped={fields.LAST_GPA})
        self.assertIsNone(choose(items, state))


class TestPriority(unittest.TestCase):
    """README 4장 3~5번 우선순위. 순서가 흔들리면 데모가 재현되지 않는다."""

    def test_영향_정책_수가_많은_항목이_먼저다(self):
        """3번. 한 번의 질문으로 더 많은 판정을 확정하는 쪽을 고른다."""
        items = [
            # 표 순서도 앞이고 상위 정책에 걸린 항목이지만 정책 수가 적다
            item(fields.INCOME_BRACKET, "P1", 0),
            item(fields.LAST_GPA, "P2", 3),
            item(fields.LAST_GPA, "P3", 4),
        ]
        self.assertEqual(
            chosen_field(items),
            fields.LAST_GPA,
            "정책 수가 많은 항목보다 표 순서·정책 순위가 앞선 항목이 선택됐다",
        )

    def test_정책_수가_같으면_더_위에_있는_정책의_항목이다(self):
        """4번. 사용자가 화면에서 먼저 보는 카드의 판정을 먼저 확정한다."""
        items = [
            item(fields.LAST_GPA, "P1", 0),  # 표 순서는 맨 뒤지만 1등 카드
            item(fields.INCOME_BRACKET, "P2", 3),  # 표 순서는 맨 앞
        ]
        self.assertEqual(
            chosen_field(items),
            fields.LAST_GPA,
            "정책 순위(policy_rank)보다 표 순서가 앞서 적용됐다",
        )

    def test_정책_수와_순위가_같으면_표_순서를_따른다(self):
        """5번. 마지막 동점 처리. 여기까지 와야 순서가 완전히 결정된다."""
        items = [
            item(fields.HOUSEHOLD_SIZE, "P1", 2),
            item(fields.DISTRICT, "P2", 2),
        ]
        self.assertEqual(
            chosen_field(items),
            fields.DISTRICT,
            "동점일 때 fields.FIELD_ORDER 순서를 따르지 않았다",
        )

    def test_후보_정렬이_우선순위_전체를_따른다(self):
        """세 기준이 한꺼번에 걸린 입력으로 정렬 결과 전체를 확인한다."""
        items = [
            item(fields.HOUSEHOLD_SIZE, "P1", 0),  # 1개, rank 0
            item(fields.DISTRICT, "P2", 0),  # 1개, rank 0, 표 순서 앞
            item(fields.LAST_GPA, "P3", 5),  # 2개 → 맨 앞
            item(fields.LAST_GPA, "P4", 6),
            item(fields.HOUSING_TYPE, "P5", 4),  # 1개, rank 4 → 맨 뒤
        ]
        self.assertEqual(
            [candidate.field for candidate in collect_candidates(items)],
            [
                fields.LAST_GPA,
                fields.DISTRICT,
                fields.HOUSEHOLD_SIZE,
                fields.HOUSING_TYPE,
            ],
        )

    def test_같은_정책이_두_조건으로_와도_한_번만_센다(self):
        """7번. policy_id 중복 계산은 조용히 우선순위를 뒤집는다.

        한 정책의 조건 두 개가 같은 항목을 필요로 하는 일이 실제로 있다
        (소득 상한 조건과 가구 소득 조건). 이걸 2로 세면 진짜 2개 정책이 밀린다.
        """
        items = [
            item(fields.LAST_GPA, "P1", 0),
            item(fields.LAST_GPA, "P1", 1),  # 같은 정책
            item(fields.HOUSEHOLD_SIZE, "P2", 2),
            item(fields.HOUSEHOLD_SIZE, "P3", 3),  # 서로 다른 두 정책
        ]
        self.assertEqual(
            counts_by_field(items),
            {fields.LAST_GPA: 1, fields.HOUSEHOLD_SIZE: 2},
            "같은 policy_id 가 중복 계산됐다",
        )
        self.assertEqual(chosen_field(items), fields.HOUSEHOLD_SIZE)

    def test_중복_정책은_best_rank에도_영향을_주지_않는다(self):
        candidates = collect_candidates(
            [item(fields.LAST_GPA, "P1", 4), item(fields.LAST_GPA, "P1", 1)]
        )
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].best_rank, 1, "가장 위 정책의 순위를 써야 한다")


class TestUnaskable(unittest.TestCase):
    """물을 수 없는 항목은 후보에서 빠진다 (docs/01-glossary-profile.md 3장)."""

    def test_공고_확인_필요는_묻지_않는다(self):
        """ASK_NOTICE 는 항목이 아니라 "공고를 직접 보라"는 표시다.

        이걸 물으면 선택지가 없는 질문이 나가고 사용자는 답할 수 없다.
        """
        items = [item(fields.ASK_NOTICE, "P1", 0, "청년수당")]
        self.assertEqual(counts_by_field(items), {})
        self.assertIsNone(choose(items), "ASK_NOTICE 가 질문 항목으로 선택됐다")

    def test_공고_확인_필요가_섞여_있어도_나머지는_고른다(self):
        items = [
            item(fields.ASK_NOTICE, "P1", 0),
            item(fields.ASK_NOTICE, "P2", 1),
            item(fields.HOUSING_TYPE, "P3", 5),
        ]
        self.assertEqual(chosen_field(items), fields.HOUSING_TYPE)

    def test_폼에서_받은_필수_항목은_묻지_않는다(self):
        """나이·거주지·현재 상태·관심 분야는 필수라 미확인이 될 수 없다."""
        for field in (fields.AGE, fields.REGION, fields.STATUS, fields.CATEGORIES):
            with self.subTest(field=field):
                self.assertIsNone(
                    choose([item(field, "P1")]),
                    f"{field} 는 대화로 묻는 항목이 아니다",
                )

    def test_질문_틀이_없는_항목은_빠진다(self):
        """틀이 없으면 문구를 즉석에서 만들지 않고 그 항목을 포기한다."""
        without_gpa = {
            name: template
            for name, template in questions.TEMPLATES.items()
            if name != fields.LAST_GPA
        }
        with mock.patch.dict(questions.TEMPLATES, without_gpa, clear=True):
            self.assertIsNone(
                choose([item(fields.LAST_GPA, "P1", 0)]),
                "질문 틀이 없는데도 선택됐다",
            )
            # 틀이 있는 항목은 그대로 선택된다 (표만 비교한다는 확인)
            self.assertEqual(
                chosen_field(
                    [item(fields.LAST_GPA, "P1", 0), item(fields.DISTRICT, "P2", 9)]
                ),
                fields.DISTRICT,
            )


class TestIntent(unittest.TestCase):
    """의도별 동작 (README 3장 표, 4장 마지막 줄)."""

    def test_후속_질문을_하지_않는_의도에서는_묻지_않는다(self):
        """C5. "결과만 보여줘"에 질문을 붙이면 요청을 무시한 셈이 된다."""
        items = [item(fields.INCOME_BRACKET, "P1", 0, "청년월세지원")]
        for intent in (fields.RESULT_ONLY, fields.OUT_OF_SCOPE, fields.SMALLTALK):
            with self.subTest(intent=intent):
                self.assertIsNone(
                    choose(items, None, intent),
                    f"{intent} 의도인데 질문이 선택됐다",
                )
                self.assertIsNone(next_question(items, None, intent))

    def test_묻는_의도에서는_평소처럼_고른다(self):
        items = [item(fields.INCOME_BRACKET, "P1", 0)]
        for intent in (fields.FIND_POLICY, fields.POLICY_QUESTION, fields.COMPARE):
            with self.subTest(intent=intent):
                self.assertEqual(chosen_field(items, None, intent), fields.INCOME_BRACKET)

    def test_묻지_않는_의도_목록이_설계와_같다(self):
        self.assertEqual(
            set(fields.NO_FOLLOWUP_INTENTS),
            {fields.RESULT_ONLY, fields.OUT_OF_SCOPE, fields.SMALLTALK},
        )


class TestNothingToAsk(unittest.TestCase):
    """None 이 정상 동작이다 (FR05). 억지로 질문을 만들면 심문이 된다."""

    def test_미확인_항목이_없으면_None(self):
        self.assertEqual(collect_candidates([]), [])
        self.assertIsNone(choose([]))
        self.assertIsNone(next_question([]))

    def test_항목이_하나면_그것을_고른다(self):
        """경계. 후보가 하나일 때 정렬·잘라내기에서 사라지지 않아야 한다."""
        items = [item(fields.OTHER_BENEFIT, "P1", 2)]
        self.assertEqual(chosen_field(items), fields.OTHER_BENEFIT)

    def test_물을_수_없는_항목만_있으면_None(self):
        items = [item(fields.ASK_NOTICE, "P1"), item(fields.AGE, "P2")]
        self.assertIsNone(choose(items))


class TestNextQuestionPayload(unittest.TestCase):
    """docs/03-api-contract.md 6장 형식. 프론트가 이 키만 보고 카드를 그린다."""

    def setUp(self):
        self.items = [
            item(fields.INCOME_BRACKET, "P1", 0, "청년월세지원"),
            item(fields.INCOME_BRACKET, "P2", 1, "청년수당"),
        ]
        self.payload = next_question(self.items)

    def test_계약에_적힌_키만_들어_있다(self):
        self.assertIsNotNone(self.payload)
        self.assertEqual(
            set(self.payload),
            FOLLOWUP_KEYS,
            "후속 질문 본문의 키가 계약과 다르다",
        )

    def test_고른_항목과_본문의_항목이_같다(self):
        self.assertEqual(self.payload["field"], choose(self.items).field)

    def test_건너뛰기는_항상_허용된다(self):
        """건너뛸 수 없는 질문은 심문이 된다. 어떤 항목이든 True 여야 한다."""
        for field in fields.FIELD_ORDER:
            with self.subTest(field=field):
                payload = next_question([item(field, "P1")])
                self.assertIsNotNone(payload, f"{field}: 질문 본문이 만들어지지 않았다")
                self.assertIs(
                    payload["allow_skip"],
                    True,
                    f"{field}: allow_skip 이 True 가 아니다",
                )

    def test_질문과_이유가_비어_있지_않다(self):
        self.assertTrue(self.payload["question"].strip())
        self.assertTrue(self.payload["reason"].strip())

    def test_선택지가_값과_문구_쌍으로_하나_이상_있다(self):
        options = self.payload["options"]
        self.assertIsInstance(options, list)
        self.assertGreaterEqual(len(options), 1, "선택지가 없으면 버튼이 나가지 않는다")
        for option in options:
            with self.subTest(option=option):
                self.assertEqual(set(option), {"value", "label"})

    def test_이유_문구에_영향받는_정책명과_개수가_채워진다(self):
        self.assertIn("청년월세지원", self.payload["reason"])
        self.assertIn("2", self.payload["reason"])
        self.assertNotIn("○○", self.payload["reason"], "미완성 표시가 남았다")
        self.assertNotIn("{", self.payload["reason"], "치환되지 않은 자리가 남았다")

    def test_정책명이_없으면_개수로만_말한다(self):
        payload = next_question(
            [item(fields.INCOME_BRACKET, "P1", 0), item(fields.INCOME_BRACKET, "P2", 1)]
        )
        self.assertIn("2", payload["reason"])
        self.assertNotIn("○○", payload["reason"])
        self.assertNotIn("{", payload["reason"])

    def test_정책명은_화면_순서대로_들어가고_중복되지_않는다(self):
        items = [
            item(fields.INCOME_BRACKET, "P1", 3, "청년수당"),
            item(fields.INCOME_BRACKET, "P2", 0, "청년월세지원"),
            item(fields.INCOME_BRACKET, "P3", 5, "청년월세지원"),
        ]
        titles = list(choose(items).policy_titles)
        self.assertEqual(titles, ["청년월세지원", "청년수당"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
