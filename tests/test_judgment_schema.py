"""판정 출력 스키마·파싱 테스트 (`ai/judgment/schema.py`).

사전 점검에서 "이러면 깨진다"로 적은 시나리오를 그대로 케이스로 옮겼다.
정상 경로만 확인하지 않는다. 여기서 막는 것은 **쓸 수 있는 판정을 형식 때문에
잃는 것**과 **알아보지 못한 값을 조용히 통과시키는 것** 두 가지다.
"""

import unicodedata
import unittest

from ai.judgment.schema import (
    CONDITION_SCHEMA,
    DROP_INVALID_RESULT,
    DROP_MISSING_EXCERPT,
    DROP_NOT_OBJECT,
    ERROR_ALL_DROPPED,
    ERROR_EMPTY_INPUT,
    JUDGE_OUTPUT_SCHEMA,
    ParseOutcome,
    normalize_result,
    parse_conditions,
)
from ai.judgment.values import (
    ALLOWED_NEEDED_FIELDS,
    ASK_NOTICE,
    BY_AI,
    MAX_NAME_LEN,
    MET,
    UNKNOWN,
    UNMET,
)

# 실제 공고 문장 형태를 가정한 발췌
EXCERPT_LEAVE = "휴학생은 지원 대상에서 제외합니다"
EXCERPT_OTHER = "타 청년 지원금을 수혜 중인 자는 중복 신청할 수 없습니다"

GOOD_JSON = """
[
  {"name": "휴학생 제외", "result": "unmet", "excerpt": "휴학생은 지원 대상에서 제외합니다", "needed_field": ""},
  {"name": "타 지원금 중복 불가", "result": "unknown", "excerpt": "타 청년 지원금을 수혜 중인 자는 중복 신청할 수 없습니다", "needed_field": "other_benefit"}
]
"""


class TestSchemaShape(unittest.TestCase):
    """스키마 강제용 정의 (`docs/research/04-llm-api-operations.md` 1장)."""

    def test_조건_속성은_네_개다(self):
        self.assertEqual(
            set(CONDITION_SCHEMA["properties"]),
            {"name", "result", "excerpt", "needed_field"},
        )

    def test_모든_속성이_필수다(self):
        """엄격 모드는 모든 속성을 필수 목록에 넣으라고 요구한다."""
        self.assertEqual(
            set(CONDITION_SCHEMA["required"]),
            set(CONDITION_SCHEMA["properties"]),
        )

    def test_needed_field_가_필수이면서_빈_값을_허용한다(self):
        """선택 항목으로 빼는 대신 빈 문자열을 허용하는 방식이어야 한다."""
        self.assertIn("needed_field", CONDITION_SCHEMA["required"])
        self.assertIn("", CONDITION_SCHEMA["properties"]["needed_field"]["enum"])

    def test_needed_field_허용_값은_values_목록과_같다(self):
        enum = set(CONDITION_SCHEMA["properties"]["needed_field"]["enum"])
        self.assertEqual(enum, set(ALLOWED_NEEDED_FIELDS) | {""})
        self.assertIn(ASK_NOTICE, enum)

    def test_result_는_세_값만_허용한다(self):
        self.assertEqual(
            CONDITION_SCHEMA["properties"]["result"]["enum"],
            [MET, UNMET, UNKNOWN],
        )

    def test_추가_속성을_막는다(self):
        self.assertIs(CONDITION_SCHEMA["additionalProperties"], False)
        self.assertIs(JUDGE_OUTPUT_SCHEMA["additionalProperties"], False)

    def test_최상위는_조건_목록을_담은_객체다(self):
        """최상위가 배열이면 못 받는 제공사가 있다. 객체로 감싼다."""
        self.assertEqual(JUDGE_OUTPUT_SCHEMA["type"], "object")
        self.assertEqual(JUDGE_OUTPUT_SCHEMA["required"], ["conditions"])
        conditions = JUDGE_OUTPUT_SCHEMA["properties"]["conditions"]
        self.assertEqual(conditions["type"], "array")
        self.assertIs(conditions["items"], CONDITION_SCHEMA)

    def test_중첩이_얕다(self):
        """조건 항목의 속성에 다시 객체나 배열이 들어가지 않는다."""
        for name, prop in CONDITION_SCHEMA["properties"].items():
            with self.subTest(prop=name):
                self.assertEqual(prop["type"], "string")

    def test_지원되지_않을_수_있는_길이_키워드를_쓰지_않는다(self):
        for name, prop in CONDITION_SCHEMA["properties"].items():
            with self.subTest(prop=name):
                self.assertNotIn("minLength", prop)
                self.assertNotIn("maxLength", prop)


class TestNormalPath(unittest.TestCase):
    def test_정상_json_배열(self):
        outcome = parse_conditions(GOOD_JSON)
        self.assertTrue(outcome.ok, outcome.error)
        self.assertEqual(outcome.dropped, [])
        self.assertEqual(len(outcome.conditions), 2)

    def test_항목_형식이_api_계약과_같다(self):
        """`docs/03-api-contract.md` 4-1."""
        first = parse_conditions(GOOD_JSON).conditions[0]
        self.assertEqual(first["name"], "휴학생 제외")
        self.assertEqual(first["result"], UNMET)
        self.assertEqual(first["judged_by"], BY_AI)
        self.assertEqual(first["excerpt"], EXCERPT_LEAVE)
        self.assertIsNone(first["needed_field"])

    def test_판정_주체는_항상_ai다(self):
        for condition in parse_conditions(GOOD_JSON).conditions:
            self.assertEqual(condition["judged_by"], BY_AI)

    def test_원문_발췌를_고치지_않는다(self):
        """공백·문장 부호를 손대면 인용 검증이 원문을 되찾을 수 없다."""
        text = '[{"name": "x", "result": "met", "excerpt": "  ○ 제외 대상 - 휴학생  ", "needed_field": ""}]'
        self.assertEqual(
            parse_conditions(text).conditions[0]["excerpt"],
            "○ 제외 대상 - 휴학생",
        )


class TestBrokenShapes(unittest.TestCase):
    """스키마를 강제해도 어긋나는 출력들."""

    def test_코드펜스로_감싼_경우(self):
        text = f"```json\n{GOOD_JSON}\n```"
        outcome = parse_conditions(text)
        self.assertTrue(outcome.ok, outcome.error)
        self.assertEqual(len(outcome.conditions), 2)

    def test_언어_표시_없는_코드펜스(self):
        outcome = parse_conditions(f"```\n{GOOD_JSON}\n```")
        self.assertEqual(len(outcome.conditions), 2)

    def test_펜스가_여러_개면_긴_것을_쓴다(self):
        short = '```json\n[{"name":"예","result":"met","excerpt":"' + EXCERPT_LEAVE + '","needed_field":""}]\n```'
        text = f"형식 예시입니다:\n{short}\n실제 판정:\n```json\n{GOOD_JSON}\n```"
        self.assertEqual(len(parse_conditions(text).conditions), 2)

    def test_닫는_펜스가_없는_경우(self):
        outcome = parse_conditions(f"```json\n{GOOD_JSON}")
        self.assertTrue(outcome.ok, outcome.error)
        self.assertEqual(len(outcome.conditions), 2)

    def test_앞뒤에_설명_문장이_붙은_경우(self):
        text = f"다음과 같이 판정했습니다: {GOOD_JSON} 이상입니다."
        outcome = parse_conditions(text)
        self.assertTrue(outcome.ok, outcome.error)
        self.assertEqual(len(outcome.conditions), 2)

    def test_설명_문장에_괄호가_섞인_경우(self):
        """설명 안의 "[1]" 같은 덩어리에 끌려가지 않는다."""
        text = f'각주 [1] 과 [2] 를 붙였습니다. 결과는 {GOOD_JSON} 입니다 (끝).'
        self.assertEqual(len(parse_conditions(text).conditions), 2)

    def test_conditions_로_감싼_경우(self):
        text = '{"conditions": ' + GOOD_JSON + "}"
        self.assertEqual(len(parse_conditions(text).conditions), 2)

    def test_다른_감싸는_키들(self):
        for key in ("조건", "results", "items", "data"):
            with self.subTest(key=key):
                text = '{"%s": %s}' % (key, GOOD_JSON)
                outcome = parse_conditions(text)
                self.assertTrue(outcome.ok, outcome.error)
                self.assertEqual(len(outcome.conditions), 2)

    def test_배열로_감싸지_않고_조건_하나만_보낸_경우(self):
        text = '{"name": "휴학생 제외", "result": "unmet", "excerpt": "%s", "needed_field": ""}' % EXCERPT_LEAVE
        outcome = parse_conditions(text)
        self.assertTrue(outcome.ok, outcome.error)
        self.assertEqual(len(outcome.conditions), 1)
        self.assertEqual(outcome.conditions[0]["result"], UNMET)

    def test_목록_끝에_쉼표가_남은_경우(self):
        text = (
            '[{"name": "휴학생 제외", "result": "unmet", '
            '"excerpt": "%s", "needed_field": "",},]' % EXCERPT_LEAVE
        )
        outcome = parse_conditions(text)
        self.assertTrue(outcome.ok, outcome.error)
        self.assertEqual(len(outcome.conditions), 1)

    def test_문자열_안에_괄호가_있는_json(self):
        """발췌에 괄호가 들어가면 깊이 세기가 엉뚱한 데서 끝날 수 있다."""
        excerpt = "제외 대상: (1) 휴학생 [재학 증명 불가], (2) 타 지원금 수혜자 {중복}"
        text = (
            '앞말 [{"name": "제외 대상", "result": "unknown", '
            '"excerpt": "%s", "needed_field": "other_benefit"}] 뒷말' % excerpt
        )
        outcome = parse_conditions(text)
        self.assertTrue(outcome.ok, outcome.error)
        self.assertEqual(outcome.conditions[0]["excerpt"], excerpt)

    def test_문자열_안에_이스케이프된_따옴표가_있는_json(self):
        text = (
            '[{"name": "기타 부적격", "result": "unknown", '
            '"excerpt": "\\"기타 부적격자\\"는 제외합니다", "needed_field": ""}]'
        )
        outcome = parse_conditions(text)
        self.assertTrue(outcome.ok, outcome.error)
        self.assertEqual(
            outcome.conditions[0]["excerpt"], '"기타 부적격자"는 제외합니다'
        )
        self.assertEqual(outcome.conditions[0]["needed_field"], ASK_NOTICE)


class TestAliases(unittest.TestCase):
    def test_결과_값_별칭(self):
        cases = {
            "met": MET,
            "충족": MET,
            "satisfied": MET,
            "YES": MET,
            "unmet": UNMET,
            "not_met": UNMET,
            "not met": UNMET,
            "미충족": UNMET,
            "제외": UNMET,
            "unknown": UNKNOWN,
            "unclear": UNKNOWN,
            "미확인": UNKNOWN,
            "확인 필요": UNKNOWN,
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(normalize_result(raw), expected)

    def test_not_met_이_met_으로_읽히지_않는다(self):
        """부분 일치로 맞추면 미충족이 충족이 된다. 가장 위험한 오판이다."""
        for raw in ("not_met", "not-met", "NOT MET", "unmet", "미충족"):
            with self.subTest(raw=raw):
                self.assertEqual(normalize_result(raw), UNMET)

    def test_알아볼_수_없는_결과는_none(self):
        for raw in ("아마도", "probably", "", None, 3, "met 인 것 같음"):
            with self.subTest(raw=raw):
                self.assertIsNone(normalize_result(raw))

    def test_결과_값_별칭이_파싱에_반영된다(self):
        text = (
            '[{"조건 요약": "휴학생 제외", "결과": "충족", '
            '"발췌": "%s", "필요한 추가 항목": ""}]' % EXCERPT_LEAVE
        )
        outcome = parse_conditions(text)
        self.assertTrue(outcome.ok, outcome.error)
        self.assertEqual(outcome.conditions[0]["result"], MET)
        self.assertEqual(outcome.conditions[0]["name"], "휴학생 제외")
        self.assertEqual(outcome.conditions[0]["excerpt"], EXCERPT_LEAVE)

    def test_키_이름_별칭(self):
        text = (
            '[{"summary": "타 지원금 중복", "verdict": "unclear", '
            '"quote": "%s", "needed": "다른 지원 수혜"}]' % EXCERPT_OTHER
        )
        outcome = parse_conditions(text)
        self.assertTrue(outcome.ok, outcome.error)
        condition = outcome.conditions[0]
        self.assertEqual(condition["name"], "타 지원금 중복")
        self.assertEqual(condition["result"], UNKNOWN)
        self.assertEqual(condition["excerpt"], EXCERPT_OTHER)
        self.assertEqual(condition["needed_field"], "other_benefit")

    def test_영문_키_별칭들(self):
        for key in ("excerpt", "quote", "evidence", "citation", "text", "근거"):
            with self.subTest(key=key):
                text = '[{"name": "x", "result": "met", "%s": "%s"}]' % (
                    key,
                    EXCERPT_LEAVE,
                )
                outcome = parse_conditions(text)
                self.assertTrue(outcome.ok, outcome.error)
                self.assertEqual(outcome.conditions[0]["excerpt"], EXCERPT_LEAVE)

    def test_자모_분해형_키도_읽는다(self):
        """macOS 경로에서 한글 키가 분해형으로 올 수 있다."""
        text = (
            '[{"조건 요약": "휴학생 제외", "결과": "미충족", '
            '"발췌": "%s", "필요한 추가 항목": ""}]' % EXCERPT_LEAVE
        )
        outcome = parse_conditions(unicodedata.normalize("NFD", text))
        self.assertTrue(outcome.ok, outcome.error)
        self.assertEqual(outcome.conditions[0]["result"], UNMET)


class TestNeededField(unittest.TestCase):
    def test_허용_목록_밖_값은_공고_확인_필요로_바뀐다(self):
        """목록 밖 항목을 그냥 두면 후속 질문이 그 항목을 물을 수 없다."""
        text = (
            '[{"name": "동일 사업 참여", "result": "unknown", '
            '"excerpt": "%s", "needed_field": "최근 3년 내 참여 이력"}]' % EXCERPT_OTHER
        )
        self.assertEqual(
            parse_conditions(text).conditions[0]["needed_field"], ASK_NOTICE
        )

    def test_허용_목록_안_값은_그대로_둔다(self):
        text = (
            '[{"name": "성적 기준", "result": "unknown", '
            '"excerpt": "%s", "needed_field": "last_gpa"}]' % EXCERPT_OTHER
        )
        self.assertEqual(
            parse_conditions(text).conditions[0]["needed_field"], "last_gpa"
        )

    def test_화면_문구로_보낸_경우_항목_이름으로_되돌린다(self):
        text = (
            '[{"name": "성적 기준", "result": "unknown", '
            '"excerpt": "%s", "needed_field": "직전 학기 성적"}]' % EXCERPT_OTHER
        )
        self.assertEqual(
            parse_conditions(text).conditions[0]["needed_field"], "last_gpa"
        )

    def test_공고_확인_필요를_띄어쓰기_다르게_보낸_경우(self):
        text = (
            '[{"name": "기타 부적격", "result": "unknown", '
            '"excerpt": "%s", "needed_field": "공고확인필요"}]' % EXCERPT_OTHER
        )
        self.assertEqual(
            parse_conditions(text).conditions[0]["needed_field"], ASK_NOTICE
        )

    def test_미확인인데_비어_있으면_공고_확인_필요(self):
        text = (
            '[{"name": "기타 부적격", "result": "unknown", '
            '"excerpt": "%s", "needed_field": ""}]' % EXCERPT_OTHER
        )
        self.assertEqual(
            parse_conditions(text).conditions[0]["needed_field"], ASK_NOTICE
        )

    def test_미확인인데_항목이_아예_없으면_공고_확인_필요(self):
        text = '[{"name": "기타 부적격", "result": "unknown", "excerpt": "%s"}]' % EXCERPT_OTHER
        self.assertEqual(
            parse_conditions(text).conditions[0]["needed_field"], ASK_NOTICE
        )

    def test_미확인이_아니면_항목은_none이다(self):
        """충족·미충족에 필요한 추가 항목이 남으면 묻지 않아도 될 것을 묻는다."""
        for result in ("met", "unmet"):
            with self.subTest(result=result):
                text = (
                    '[{"name": "휴학생 제외", "result": "%s", '
                    '"excerpt": "%s", "needed_field": "last_gpa"}]'
                    % (result, EXCERPT_LEAVE)
                )
                self.assertIsNone(
                    parse_conditions(text).conditions[0]["needed_field"]
                )


class TestItemRescue(unittest.TestCase):
    """항목 단위 구제. 하나가 깨졌다고 전체를 버리지 않는다."""

    def test_한_항목만_깨지면_나머지는_살아남는다(self):
        text = (
            "["
            '{"name": "깨진 항목", "result": "아마도", "excerpt": "%s"},' % EXCERPT_LEAVE
            + '{"name": "멀쩡한 항목", "result": "unmet", "excerpt": "%s"}' % EXCERPT_OTHER
            + "]"
        )
        outcome = parse_conditions(text)
        self.assertTrue(outcome.ok, outcome.error)
        self.assertEqual(len(outcome.conditions), 1)
        self.assertEqual(outcome.conditions[0]["name"], "멀쩡한 항목")
        self.assertEqual(
            outcome.dropped, [{"index": 0, "reason": DROP_INVALID_RESULT}]
        )

    def test_발췌_없는_항목은_버린다(self):
        """근거 없는 판정은 인용 검증도 통과할 수 없다."""
        for excerpt in ('""', '"   "', "null", "[]"):
            with self.subTest(excerpt=excerpt):
                text = (
                    '[{"name": "휴학생 제외", "result": "unmet", '
                    '"excerpt": %s, "needed_field": ""}]' % excerpt
                )
                outcome = parse_conditions(text)
                self.assertFalse(outcome.ok)
                self.assertEqual(
                    outcome.dropped, [{"index": 0, "reason": DROP_MISSING_EXCERPT}]
                )

    def test_발췌_항목이_아예_없는_경우도_버린다(self):
        text = '[{"name": "휴학생 제외", "result": "unmet"}]'
        outcome = parse_conditions(text)
        self.assertEqual(
            outcome.dropped, [{"index": 0, "reason": DROP_MISSING_EXCERPT}]
        )

    def test_결과를_알아볼_수_없으면_버린다(self):
        text = '[{"name": "x", "result": "아마도", "excerpt": "%s"}]' % EXCERPT_LEAVE
        outcome = parse_conditions(text)
        self.assertEqual(
            outcome.dropped, [{"index": 0, "reason": DROP_INVALID_RESULT}]
        )

    def test_객체가_아닌_항목은_버린다(self):
        text = '["휴학생 제외", 3, null, {"name":"x","result":"met","excerpt":"%s"}]' % EXCERPT_LEAVE
        outcome = parse_conditions(text)
        self.assertTrue(outcome.ok, outcome.error)
        self.assertEqual(len(outcome.conditions), 1)
        self.assertEqual(
            [d["reason"] for d in outcome.dropped],
            [DROP_NOT_OBJECT] * 3,
        )
        self.assertEqual([d["index"] for d in outcome.dropped], [0, 1, 2])

    def test_전부_깨지면_error가_채워진다(self):
        text = '[{"result": "아마도"}, "문자열", 7]'
        outcome = parse_conditions(text)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.error, ERROR_ALL_DROPPED)
        self.assertEqual(outcome.conditions, [])
        self.assertEqual(len(outcome.dropped), 3)

    def test_발췌_목록으로_보내면_첫_번째만_쓴다(self):
        """이어 붙이면 원문에 없는 문장이 만들어진다."""
        text = '[{"name": "x", "result": "met", "excerpt": ["%s", "%s"]}]' % (
            EXCERPT_LEAVE,
            EXCERPT_OTHER,
        )
        self.assertEqual(
            parse_conditions(text).conditions[0]["excerpt"], EXCERPT_LEAVE
        )


class TestNameTrimming(unittest.TestCase):
    def test_조건_요약이_상한을_넘으면_자르고_표시한다(self):
        name = "가" * (MAX_NAME_LEN + 10)
        text = '[{"name": "%s", "result": "met", "excerpt": "%s"}]' % (
            name,
            EXCERPT_LEAVE,
        )
        condition = parse_conditions(text).conditions[0]
        self.assertEqual(len(condition["name"]), MAX_NAME_LEN)
        self.assertTrue(condition["name_trimmed"])

    def test_상한_이내면_표시하지_않는다(self):
        text = '[{"name": "휴학생 제외", "result": "met", "excerpt": "%s"}]' % EXCERPT_LEAVE
        condition = parse_conditions(text).conditions[0]
        self.assertNotIn("name_trimmed", condition)

    def test_자모_분해형_요약이_길이_때문에_잘리지_않는다(self):
        """NFC 로 맞춰 세지 않으면 20자 이내 요약이 두 배로 세어져 잘린다."""
        name = "타 청년 지원금 중복 불가"
        text = '[{"name": "%s", "result": "met", "excerpt": "%s"}]' % (
            unicodedata.normalize("NFD", name),
            EXCERPT_LEAVE,
        )
        condition = parse_conditions(text).conditions[0]
        self.assertEqual(condition["name"], name)
        self.assertNotIn("name_trimmed", condition)

    def test_요약이_없어도_판정은_버리지_않는다(self):
        """요약이 비면 화면에 이름이 안 나갈 뿐이다. 조건을 지우면 미충족이 사라진다."""
        text = '[{"result": "unmet", "excerpt": "%s"}]' % EXCERPT_LEAVE
        outcome = parse_conditions(text)
        self.assertTrue(outcome.ok, outcome.error)
        self.assertEqual(outcome.conditions[0]["name"], "")
        self.assertEqual(outcome.conditions[0]["result"], UNMET)


class TestTotalFailure(unittest.TestCase):
    def test_빈_문자열(self):
        outcome = parse_conditions("")
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.error, ERROR_EMPTY_INPUT)
        self.assertEqual(outcome.conditions, [])

    def test_공백만_있는_문자열(self):
        self.assertEqual(parse_conditions("  \n\t ").error, ERROR_EMPTY_INPUT)

    def test_문자열이_아닌_입력(self):
        for value in (None, 3, [], {}):
            with self.subTest(value=value):
                outcome = parse_conditions(value)
                self.assertFalse(outcome.ok)
                self.assertEqual(outcome.error, ERROR_EMPTY_INPUT)

    def test_json이_아닌_문장(self):
        outcome = parse_conditions("판정할 수 없습니다. 원문에 해당 조건이 없습니다.")
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.conditions, [])

    def test_괄호는_있지만_json이_아닌_경우(self):
        outcome = parse_conditions("조건 목록: [휴학생 제외, 타 지원금 중복]")
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.conditions, [])

    def test_괄호가_닫히지_않고_끊긴_출력(self):
        outcome = parse_conditions('[{"name": "휴학생 제외", "result": "unm')
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.conditions, [])

    def test_조건_목록이_아닌_json(self):
        outcome = parse_conditions('{"message": "판정을 완료했습니다"}')
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.conditions, [])

    def test_빈_목록은_실패가_아니다(self):
        """판정할 문장이 없다는 정상 응답이다. 그 다음 판단은 호출부가 한다."""
        for text in ("[]", '{"conditions": []}'):
            with self.subTest(text=text):
                outcome = parse_conditions(text)
                self.assertTrue(outcome.ok, outcome.error)
                self.assertEqual(outcome.conditions, [])
                self.assertEqual(outcome.dropped, [])


class TestParseOutcome(unittest.TestCase):
    def test_기본값(self):
        outcome = ParseOutcome()
        self.assertTrue(outcome.ok)
        self.assertEqual(outcome.conditions, [])
        self.assertEqual(outcome.dropped, [])

    def test_ok는_error_유무다(self):
        self.assertFalse(ParseOutcome(error="x").ok)


class TestCitationHandoff(unittest.TestCase):
    """파싱 결과를 인용 검증기에 그대로 넘길 수 있는지."""

    def test_verify_conditions에_바로_넣을_수_있다(self):
        from ai.judgment.citation import verify_conditions

        raw = (
            "○ 제외 대상\n"
            "  - 휴학생은 지원 대상에서 제외합니다\n"
            "  - 타 청년 지원금을 수혜 중인 자는 중복 신청할 수 없습니다\n"
        )
        outcome = parse_conditions(GOOD_JSON)
        checked, removed = verify_conditions(outcome.conditions, raw)
        self.assertEqual(removed, [], "파싱 결과가 인용 검증을 그대로 통과해야 한다")
        self.assertEqual(len(checked), 2)
        self.assertTrue(all(c["excerpt_verified"] for c in checked))

    def test_발췌_길이_검증은_이_모듈이_하지_않는다(self):
        """10자 미만 발췌도 파서는 통과시킨다. 길이 판단은 citation.py 몫이다."""
        text = '[{"name": "x", "result": "met", "excerpt": "휴학생"}]'
        outcome = parse_conditions(text)
        self.assertTrue(outcome.ok, outcome.error)
        self.assertEqual(outcome.conditions[0]["excerpt"], "휴학생")


if __name__ == "__main__":
    unittest.main()
