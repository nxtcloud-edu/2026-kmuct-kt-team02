"""해석 프롬프트 문안 테스트 (``ai/conversation/prompts.py``).

모델을 부르지 않는다. 문안은 문자열이므로 조립 결과만 본다.

여기서 지키려는 것은 하나다. **문안이 ``interpret`` 의 표와 같은 말을 하는가.**
어긋나면 모델이 표 밖의 값을 내고 ``interpret._clean_change`` 가 예외 없이 버린다.
그 실패는 로그에도 화면에도 "값이 틀렸다"로 나오지 않고 "말했는데 프로필이 안 바뀐다"
하나로만 보인다. 그래서 값 목록과 매핑 예시를 여기서 고정한다.
"""

import unittest

from ai.conversation import fields, interpret, prompts


class InterpretSystemTest(unittest.TestCase):
    def test_system_text_is_a_constant_string(self):
        self.assertIsInstance(prompts.INTERPRET_SYSTEM, str)
        self.assertTrue(prompts.INTERPRET_SYSTEM.strip())

    def test_system_text_asks_for_json_only(self):
        self.assertIn("JSON", prompts.INTERPRET_SYSTEM)


class AllowedValueTableTest(unittest.TestCase):
    """값 목록은 ``interpret`` 에서 끌어온다. 문안에 다시 적지 않는다."""

    def setUp(self):
        self.text = prompts.interpret_rules_text()

    def test_extra_field_values_are_all_present(self):
        for field_name, allowed in interpret.EXTRA_FIELD_VALUES.items():
            with self.subTest(field=field_name):
                self.assertIn(field_name, self.text)
                for value in allowed:
                    self.assertIn(value, self.text)

    def test_timing_values_are_explained(self):
        self.assertIn(fields.CURRENT, self.text)
        self.assertIn(fields.PLANNED, self.text)

    def test_region_is_declared_unchangeable(self):
        self.assertIn(fields.REGION, self.text)

    def test_numeric_income_is_refused(self):
        self.assertIn(fields.INCOME_BRACKET, self.text)
        self.assertIn("월 150만원", self.text)


class ColloquialMappingTest(unittest.TestCase):
    """구어 → 허용값 매핑 예시.

    "자취해요" 에 모델이 ``housing_type="self"`` 를 냈다. 허용값이 아니라 조용히 버려졌다.
    예시가 문안에 있는지, 그 예시의 값이 정본 표 안인지 둘 다 본다.
    """

    def setUp(self):
        self.text = prompts.colloquial_examples_text()
        self.allowed = interpret.EXTRA_FIELD_VALUES[fields.HOUSING_TYPE]

    def test_housing_colloquial_phrases_are_mapped(self):
        for phrase, value in (
            ("자취", "monthly_rent"),
            ("원룸", "monthly_rent"),
            ("부모님 집", "parents"),
            ("기숙사", "dormitory"),
            ("전세", "jeonse"),
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, self.text)
                self.assertIn(value, self.text)

    def test_every_example_value_is_in_the_canonical_table(self):
        """예시 값이 표 밖이면 예시 자체가 고장을 만든다."""
        for field_name, examples in prompts._COLLOQUIAL_EXAMPLES.items():
            allowed = interpret.EXTRA_FIELD_VALUES[field_name]
            for value in examples:
                with self.subTest(field=field_name, value=value):
                    self.assertIn(value, allowed)

    def test_values_outside_the_table_are_dropped_from_the_text(self):
        """정본에서 값이 사라지면 예시도 사라진다. 문안만 낡지 않게."""
        original = interpret.EXTRA_FIELD_VALUES[fields.HOUSING_TYPE]
        interpret.EXTRA_FIELD_VALUES[fields.HOUSING_TYPE] = frozenset(
            original - {"jeonse"}
        )
        try:
            text = prompts.colloquial_examples_text()
        finally:
            interpret.EXTRA_FIELD_VALUES[fields.HOUSING_TYPE] = original
        self.assertNotIn("전세", text)
        self.assertIn("자취", text)

    def test_unmapped_colloquial_must_be_left_empty(self):
        """틀린 값이 들어가는 것이 안 들어가는 것보다 나쁘다."""
        self.assertIn("비운다", self.text)
        self.assertIn("고시원", self.text)

    def test_mapping_is_part_of_the_rules_text(self):
        self.assertIn("자취", prompts.interpret_rules_text())


class BuildInterpretPromptTest(unittest.TestCase):
    def test_prompt_carries_the_message_and_the_rules(self):
        prompt = prompts.build_interpret_prompt("자취해요", {"status": "enrolled"})

        self.assertIn("자취해요", prompt)
        self.assertIn("status: enrolled", prompt)
        self.assertIn("monthly_rent", prompt)

    def test_prompt_without_a_profile_says_so(self):
        prompt = prompts.build_interpret_prompt("자취해요")

        self.assertIn("(없음)", prompt)

    def test_profile_lists_are_flattened(self):
        prompt = prompts.build_interpret_prompt("", {"categories": ["job", "housing"]})

        self.assertIn("categories: job, housing", prompt)


if __name__ == "__main__":
    unittest.main()
