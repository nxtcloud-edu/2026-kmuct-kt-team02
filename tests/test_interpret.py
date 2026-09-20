"""메시지 해석·프로필 반영 테스트 (ai/conversation/README.md 3장).

모델 출력은 신뢰하지 않는다는 전제를 검증한다. 그래서 정상 경로보다 **틀린 입력**이 많다.

여기서 가장 중요한 세 가지
  1. ``from_model_output`` 은 어떤 입력에도 예외를 던지지 않는다. 해석 하나 때문에 전체
     응답을 실패시키면 규칙 기반 카드까지 사라진다 (docs/03-api-contract.md 9장).
  2. ``planned`` 변경은 프로필을 바꾸지 않는다. 휴학 예정자에게 휴학생 전용 제도를 지금
     신청 가능한 것처럼 보여주면 틀린 안내다 (README 3장).
  3. ``merge`` 는 입력 프로필을 변경하지 않는다. 원본이 바뀌면 전후 비교가 불가능해져
     "휴학으로 바꿨어요" 같은 안내와 프로필 바 갱신을 만들 수 없다.

모델을 부르지 않는 모듈이라 전부 로직만으로 확인한다.
"""

import copy
import unittest

from ai.conversation import fields
from ai.conversation.interpret import (
    AGE_MAX,
    AGE_MIN,
    CATEGORY_ALL,
    DEFAULT_INTENT,
    DROP_ALREADY_ALL,
    DROP_NOT_A_MAPPING,
    DROP_NOT_ALLOWED,
    DROP_NUMERIC_INCOME,
    DROP_REGION_FIXED,
    DROP_SAME_AS_CURRENT,
    DROP_SUPERSEDED,
    DROP_UNKNOWN_FIELD,
    DROP_WOULD_EMPTY,
    INCOME_BRACKET_NOTICE,
    NOTICE_TEMPLATES,
    OUT_OF_SCOPE_REPLY,
    SMALLTALK_REPLY,
    apply_model_output,
    behavior_of,
    change_notice,
    fixed_reply_for,
    from_model_output,
    label_of,
    merge,
    parse_model_json,
)

# 폼에서 받은 기본 프로필 (docs/01-glossary-profile.md 2장)
BASE_PROFILE = {
    "age": 24,
    "region": "seoul",
    "district": "관악구",
    "status": "enrolled",
    "categories": ["housing", "job"],
    "income_bracket": "50_100",
}


def profile():
    """매번 새 프로필. 테스트끼리 같은 dict 를 공유하면 불변 검증이 무의미해진다."""
    return copy.deepcopy(BASE_PROFILE)


def change(field, value, timing=fields.CURRENT):
    """모델이 내놓는 프로필 변경 한 건의 모양 (docs/05-interfaces.md 3장)."""
    return {"field": field, "value": value, "timing": timing}


def reasons(result):
    """버린 이유 코드만. 지표 집계와 같은 값을 본다."""
    return [item.reason for item in result.dropped]


def applied_fields(result):
    return [item.field for item in result.applied]


class TestToleratesAnyInput(unittest.TestCase):
    """예외를 던지지 않는다. 이 반이 깨지면 나머지 검증의 전제가 무너진다."""

    CASES = (
        ("None", None),
        ("문자열", "다음 학기에 휴학해요"),
        ("빈 리스트", []),
        ("숫자", 42),
        ("불리언", True),
        ("빈 dict", {}),
        ("변경 목록에 None 과 숫자", {"profile_changes": [None, 3, "age"]}),
        ("값이 리스트", {"profile_changes": {"status": ["on_leave", "enrolled"]}}),
        ("값이 중첩 dict", {"profile_changes": {"status": {"value": {"deep": 1}}}}),
        ("의도가 숫자", {"intent": 7}),
        ("관심 분야가 숫자", {"category_changes": 5}),
        ("정책 목록이 dict", {"mentioned_policies": {"a": 1}}),
        ("정책 목록이 집합", {"mentioned_policies": {"SEOUL-001", "SEOUL-002"}}),
        ("모르는 최상위 키", {"이상한키": [1, 2]}),
        ("추가 답변이 문자열 하나", {"extra_answers": "housing_type"}),
    )

    def test_어떤_입력에도_예외를_던지지_않는다(self):
        for label, data in self.CASES:
            with self.subTest(case=label):
                interpretation = from_model_output(data)
                self.assertIn(
                    interpretation.intent,
                    fields.INTENTS,
                    f"{label}: 의도가 허용 값 밖이다 ({interpretation.intent})",
                )

    def test_예외를_던지지_않은_뒤_반영도_안전하다(self):
        """해석이 통과해도 ``merge`` 에서 터지면 같은 사고가 난다."""
        for label, data in self.CASES:
            with self.subTest(case=label):
                interpretation, result = apply_model_output(profile(), data)
                self.assertIsInstance(result.profile, dict, f"{label}")

    def test_dict가_아니면_기록을_남긴다(self):
        interpretation = from_model_output("휴학했어요")
        self.assertEqual(reasons(interpretation), [DROP_NOT_A_MAPPING])
        self.assertIn("intent_defaulted", interpretation.notes)


class TestPlannedIsHeld(unittest.TestCase):
    """미래 계획은 프로필을 바꾸지 않는다 (README 3장)."""

    def setUp(self):
        self.before = profile()
        self.interpretation, self.result = apply_model_output(
            self.before,
            {
                "intent": "find_policy",
                "profile_changes": [change("status", "on_leave", fields.PLANNED)],
            },
        )

    def test_상태가_그대로다(self):
        self.assertEqual(
            self.result.profile["status"], "enrolled", "planned 변경이 프로필에 반영됐다"
        )

    def test_보류_목록에_담긴다(self):
        self.assertEqual([c.field for c in self.result.held], ["status"])
        self.assertEqual(self.result.held[0].value, "on_leave")
        self.assertTrue(self.result.needs_planned_confirmation)

    def test_프로필_갱신_이벤트를_보내지_않는다(self):
        """이벤트는 프로필이 바뀔 때만 보낸다 (docs/03-api-contract.md 5장)."""
        self.assertIsNone(self.result.to_profile_update())
        self.assertFalse(self.result.changed)
        self.assertEqual(self.result.applied, ())

    def test_해석_단계에서도_planned로_분류된다(self):
        self.assertEqual(len(self.interpretation.planned_changes), 1)
        self.assertEqual(self.interpretation.current_changes, ())
        self.assertTrue(self.interpretation.needs_planned_confirmation)

    def test_시점을_모르면_current로_본다(self):
        """지금 말한 사실을 계획으로 오해하면 "바꿨는데 안 바뀐다"가 된다."""
        _, result = apply_model_output(
            profile(), {"profile_changes": [change("status", "on_leave", "언젠가")]}
        )
        self.assertEqual(result.profile["status"], "on_leave")


class TestRegionIsFixed(unittest.TestCase):
    """거주지는 대화로 바꿀 수 없다 (docs/01-glossary-profile.md 2장, README 3장).

    이 테스트가 지키려는 계약
      1. `region` 변경은 값이 무엇이든 프로필에 반영되지 않는다. 서버 ``Profile.region`` 은
         ``Literal[Region.SEOUL]`` 하나이고 ``ProfileField`` enum 에 `region` 이 없다.
         반영하면 프로필이 서버 검증에서 막히고 changed_fields 키로도 쓸 수 없다.
      2. 버린 이유는 ``DROP_REGION_FIXED`` 다. ``DROP_UNKNOWN_FIELD`` 로 섞이면
         "모델이 표에 없는 이름을 보냈다"와 "제도상 바꿀 수 없는 항목이다"를 지표에서
         구분할 수 없다.
      3. 한 항목 때문에 턴 전체가 죽지 않는다. 같은 턴의 다른 변경은 정상 반영된다.
      4. 시점이 `planned` 여도 보류되지 않는다. "거주지가 바뀐 뒤 기준"은 존재할 수 없다.
    """

    # 허용 값 표에 있던 두 값 모두. "seoul" 은 프로필 값과 같아 얼핏 무해해 보이지만,
    # 통과시키면 프로필에 region 키가 없을 때 "거주지를 서울로 바꿨어요" 가 나간다.
    VALUES = ("outside_seoul", "seoul")

    def test_거주지_변경은_프로필에_반영되지_않는다(self):
        for value in self.VALUES:
            with self.subTest(value=value):
                before = profile()
                _, result = apply_model_output(
                    before, {"profile_changes": [change("region", value)]}
                )
                self.assertEqual(
                    result.profile["region"],
                    "seoul",
                    f"region={value} 가 프로필에 반영됐다: {result.profile['region']!r}",
                )
                self.assertEqual(
                    applied_fields(result),
                    [],
                    f"region={value} 가 바뀐 항목에 들어갔다: {applied_fields(result)}",
                )
                self.assertIsNone(
                    result.to_profile_update(),
                    "거주지만 말한 턴에 프로필 갱신 이벤트를 보냈다",
                )

    def test_버린_이유가_region_is_fixed다(self):
        """이유 코드를 unknown_field 로 섞으면 지표에서 원인을 알 수 없다."""
        for value in self.VALUES:
            with self.subTest(value=value):
                _, result = apply_model_output(
                    profile(), {"profile_changes": [change("region", value)]}
                )
                self.assertIn(
                    DROP_REGION_FIXED,
                    reasons(result),
                    f"region={value} 의 이유 코드가 {reasons(result)} 였다",
                )
                self.assertNotIn(
                    DROP_UNKNOWN_FIELD,
                    reasons(result),
                    "제도상 바꿀 수 없는 항목이 모르는 항목으로 기록됐다",
                )

    def test_같은_값으로_바꾸려_해도_region_is_fixed다(self):
        """프로필과 같은 값이어도 ``same_as_current`` 가 아니다.

        ``same_as_current`` 는 "바꿀 수 있지만 값이 같다"는 뜻이라 다음 턴에 다른 값이면
        반영된다는 말이 된다. 거주지는 어떤 값이든 반영되지 않으므로 이유가 다르다.
        """
        _, result = apply_model_output(
            profile(), {"profile_changes": [change("region", "seoul")]}
        )
        self.assertEqual(
            reasons(result),
            [DROP_REGION_FIXED],
            f"이유 코드가 {reasons(result)} 였다",
        )

    def test_거주지가_섞여_있어도_같은_턴의_다른_변경은_반영된다(self):
        """한 항목이 걸려 턴 전체가 죽으면 사용자는 말한 것이 통째로 무시됐다고 느낀다."""
        _, result = apply_model_output(
            profile(),
            {
                "profile_changes": [
                    change("region", "outside_seoul"),
                    change("status", "on_leave"),
                    change("district", "마포구"),
                ],
                "extra_answers": [change("housing_type", "monthly_rent")],
            },
        )
        self.assertEqual(result.profile["status"], "on_leave")
        self.assertEqual(result.profile["district"], "마포구")
        self.assertEqual(result.profile["housing_type"], "monthly_rent")
        self.assertEqual(result.profile["region"], "seoul")
        self.assertEqual(
            applied_fields(result),
            ["district", "status", "housing_type"],
            f"바뀐 항목이 {applied_fields(result)} 였다",
        )
        self.assertIn(DROP_REGION_FIXED, reasons(result))

    def test_planned_거주지_변경도_보류되지_않는다(self):
        """보류하면 "거주지가 바뀐 뒤 기준으로 볼까요" 질문이 나간다. 그 시점은 없다."""
        interpretation, result = apply_model_output(
            profile(),
            {"profile_changes": [change("region", "outside_seoul", fields.PLANNED)]},
        )
        self.assertEqual(
            interpretation.planned_changes,
            (),
            f"planned 목록에 {[c.field for c in interpretation.planned_changes]} 가 남았다",
        )
        self.assertEqual(
            result.held,
            (),
            f"보류 목록에 {[c.field for c in result.held]} 가 들어갔다",
        )
        self.assertFalse(result.needs_planned_confirmation)
        self.assertIn(DROP_REGION_FIXED, reasons(result))

    def test_거주지_변경_안내_문구는_남아_있지_않다(self):
        """반영되지 않는 항목의 안내 문구를 표에 남기면 다음 사람이 바꿀 수 있다고 읽는다."""
        self.assertNotIn(
            fields.REGION,
            NOTICE_TEMPLATES,
            "도달할 수 없는 거주지 변경 안내 문구가 표에 남아 있다",
        )
        self.assertEqual(change_notice(fields.REGION, "outside_seoul"), "")

    def test_거주지_값은_읽기와_표시에_그대로_쓴다(self):
        """지워진 것은 변경 경로뿐이다. 서버가 프로필에 담아 보내는 값은 계속 읽는다."""
        _, result = apply_model_output(profile(), {"profile_changes": []})
        self.assertEqual(
            result.profile["region"], "seoul", "입력 프로필의 거주지가 사라졌다"
        )
        for value, expected in (("seoul", "서울"), ("outside_seoul", "서울 밖")):
            with self.subTest(value=value):
                self.assertEqual(label_of(fields.REGION, value), expected)


class TestCurrentOverwritesForm(unittest.TestCase):
    """폼 값과 다르게 말하면 대화 값을 따른다 (docs/05-interfaces.md 6장)."""

    def test_폼_값을_덮어쓴다(self):
        _, result = apply_model_output(
            profile(), {"profile_changes": [change("status", "on_leave")]}
        )
        self.assertEqual(result.profile["status"], "on_leave")
        self.assertEqual(applied_fields(result), ["status"])

    def test_바뀐_항목에_전후_값을_담는다(self):
        _, result = apply_model_output(
            profile(), {"profile_changes": [change("district", "마포구")]}
        )
        event = result.to_profile_update()
        self.assertIsNotNone(event, "프로필이 바뀌면 이벤트를 보낸다")
        self.assertEqual(event["changes"][0]["before"], "관악구")
        self.assertEqual(event["changes"][0]["after"], "마포구")
        self.assertEqual(event["profile"]["district"], "마포구")

    def test_같은_값을_다시_넣으면_바뀐_항목에_넣지_않는다(self):
        """안 바뀐 것을 바뀌었다고 안내하면 사용자가 혼란스럽다."""
        _, result = apply_model_output(
            profile(), {"profile_changes": [change("status", "enrolled")]}
        )
        self.assertEqual(result.applied, ())
        self.assertIn(DROP_SAME_AS_CURRENT, reasons(result))
        self.assertIsNone(result.to_profile_update())

    def test_한_문장에서_같은_항목을_두_번_말하면_뒤가_이긴다(self):
        _, result = apply_model_output(
            profile(),
            {
                "profile_changes": [
                    change("status", "on_leave"),
                    change("status", "job_seeking"),
                ]
            },
        )
        self.assertEqual(result.profile["status"], "job_seeking")
        self.assertIn(DROP_SUPERSEDED, reasons(result))

    def test_추가_항목_답변도_반영한다(self):
        """묻지 않았어도 말했으면 반영한다 (README 3장)."""
        _, result = apply_model_output(
            profile(), {"extra_answers": [change("housing_type", "monthly_rent")]}
        )
        self.assertEqual(result.profile["housing_type"], "monthly_rent")

    def test_모델이_키를_섞어_보내도_항목_이름으로_나눈다(self):
        interpretation = from_model_output(
            {"profile_changes": [change("housing_type", "jeonse")]}
        )
        self.assertEqual([c.field for c in interpretation.extra_answers], ["housing_type"])
        self.assertEqual(interpretation.profile_changes, ())


class TestNumericIncomeRejected(unittest.TestCase):
    """숫자 소득을 구간으로 바꾸지 않는다 (README 3장).

    개인 소득과 가구 소득 기준이 다르므로 "월 200" 을 "100_150" 으로 옮길 수 없다.
    """

    CASES = (
        ("구간 자리에 숫자", {"profile_changes": [change("income_bracket", "200만원")]}),
        ("구간 자리에 정수", {"profile_changes": [change("income_bracket", 200)]}),
        ("표에 없는 소득 항목", {"profile_changes": [change("monthly_income", "200")]}),
    )

    def test_숫자_소득을_거부하고_안내로_연결한다(self):
        for label, data in self.CASES:
            with self.subTest(case=label):
                interpretation, result = apply_model_output(profile(), data)
                self.assertTrue(
                    interpretation.needs_income_bracket_notice,
                    f"{label}: 소득 구간 안내가 켜지지 않았다",
                )
                self.assertIn(INCOME_BRACKET_NOTICE, result.notices())
                self.assertEqual(
                    result.profile["income_bracket"],
                    "50_100",
                    f"{label}: 숫자를 구간으로 옮겼다",
                )
                self.assertIn(DROP_NUMERIC_INCOME, reasons(result))

    def test_허용된_구간_값은_그대로_받는다(self):
        interpretation, result = apply_model_output(
            profile(), {"profile_changes": [change("income_bracket", "100_150")]}
        )
        self.assertEqual(result.profile["income_bracket"], "100_150")
        self.assertFalse(interpretation.needs_income_bracket_notice)


class TestAllowedValues(unittest.TestCase):
    """허용 값 밖은 버린다 (docs/01-glossary-profile.md 2~3장이 유일한 기준)."""

    def test_나이_범위_밖을_버린다(self):
        for age in (14, 40, "열아홉", None):
            with self.subTest(age=age):
                _, result = apply_model_output(
                    profile(), {"profile_changes": [change("age", age)]}
                )
                self.assertEqual(result.profile["age"], 24, f"{age} 가 반영됐다")
                self.assertEqual(result.applied, ())

    def test_범위_경계는_받는다(self):
        for age in (AGE_MIN, AGE_MAX):
            with self.subTest(age=age):
                _, result = apply_model_output(
                    profile(), {"profile_changes": [change("age", age)]}
                )
                self.assertEqual(result.profile["age"], age)

    def test_모르는_신분을_버린다(self):
        _, result = apply_model_output(
            profile(), {"profile_changes": [change("status", "휴학중")]}
        )
        self.assertEqual(result.profile["status"], "enrolled")
        self.assertIn(DROP_NOT_ALLOWED, reasons(result))

    def test_모르는_분야를_버린다(self):
        _, result = apply_model_output(
            profile(), {"category_changes": {"add": ["startup"]}}
        )
        self.assertEqual(result.profile["categories"], ["housing", "job"])
        self.assertIn(DROP_NOT_ALLOWED, reasons(result))

    def test_표에_없는_항목을_만들지_않는다(self):
        _, result = apply_model_output(
            profile(), {"profile_changes": [change("favorite_color", "blue")]}
        )
        self.assertNotIn("favorite_color", result.profile, "표에 없는 항목이 생겼다")
        self.assertIn(DROP_UNKNOWN_FIELD, reasons(result))

    def test_자치구는_구가_빠져도_보정한다(self):
        _, result = apply_model_output(
            profile(), {"profile_changes": [change("district", "마포")]}
        )
        self.assertEqual(result.profile["district"], "마포구")

    def test_자치구가_아닌_지역명은_버린다(self):
        _, result = apply_model_output(
            profile(), {"profile_changes": [change("district", "성남시")]}
        )
        self.assertEqual(result.profile["district"], "관악구")


class TestProfileIsNotMutated(unittest.TestCase):
    """입력 프로필 불변. 깨지면 전후 비교가 불가능해진다."""

    def test_merge가_입력_프로필을_바꾸지_않는다(self):
        original = profile()
        snapshot = copy.deepcopy(original)
        interpretation = from_model_output(
            {
                "profile_changes": [change("status", "on_leave")],
                "category_changes": {"add": ["scholarship"]},
            }
        )
        merge(original, interpretation)
        self.assertEqual(original, snapshot, "입력 프로필이 바뀌었다")

    def test_관심_분야_목록도_공유하지_않는다(self):
        """리스트를 얕게 복사하면 반영 결과를 고칠 때 원본까지 바뀐다."""
        original = profile()
        _, result = apply_model_output(original, {"category_changes": {"add": ["all"]}})
        result.profile["categories"].append("job")
        self.assertEqual(original["categories"], ["housing", "job"])

    def test_이벤트_본문도_프로필을_복사해_담는다(self):
        _, result = apply_model_output(
            profile(), {"profile_changes": [change("status", "on_leave")]}
        )
        event = result.to_profile_update()
        event["profile"]["status"] = "엉뚱한 값"
        self.assertEqual(result.profile["status"], "on_leave")

    def test_프로필이_None이어도_동작한다(self):
        _, result = apply_model_output(
            None, {"profile_changes": [change("status", "on_leave")]}
        )
        self.assertEqual(result.profile["status"], "on_leave")


class TestCategoryAll(unittest.TestCase):
    """``all`` 처리 (docs/01-glossary-profile.md 2장)."""

    def test_all을_추가하면_나머지를_해제한다(self):
        _, result = apply_model_output(profile(), {"category_changes": {"add": ["all"]}})
        self.assertEqual(result.profile["categories"], [CATEGORY_ALL])

    def test_all로_합쳐진_것을_뺐다고_안내하지_않는다(self):
        """"전체를 추가했어요. 취업·훈련을 뺐어요" 로 나가면 사용자가 잃은 것으로 읽는다."""
        _, result = apply_model_output(profile(), {"category_changes": {"add": ["all"]}})
        notice = result.to_profile_update()["notice"]
        self.assertIn("추가", notice)
        self.assertNotIn("뺐어요", notice, f"잃은 것처럼 읽히는 안내: {notice}")

    def test_이미_all이면_분야_추가는_아무_일도_하지_않는다(self):
        current = dict(profile(), categories=[CATEGORY_ALL])
        _, result = apply_model_output(current, {"category_changes": {"add": ["housing"]}})
        self.assertEqual(result.profile["categories"], [CATEGORY_ALL])
        self.assertEqual(result.applied, ())
        self.assertIn(DROP_ALREADY_ALL, reasons(result))

    def test_이미_all인데_분야를_제외하면_다섯개로_펼친_뒤_뺀다(self):
        """그래야 제외가 뜻을 갖는다."""
        current = dict(profile(), categories=[CATEGORY_ALL])
        _, result = apply_model_output(current, {"category_changes": {"remove": ["job"]}})
        self.assertEqual(
            result.profile["categories"], ["scholarship", "living", "culture", "housing"]
        )
        self.assertNotIn(CATEGORY_ALL, result.profile["categories"])

    def test_전부_제외되어_비면_원래대로_둔다(self):
        """관심 분야는 필수라 비울 수 없다."""
        current = dict(profile(), categories=["housing"])
        _, result = apply_model_output(
            current, {"category_changes": {"remove": ["housing"]}}
        )
        self.assertEqual(result.profile["categories"], ["housing"])
        self.assertIn(DROP_WOULD_EMPTY, reasons(result))

    def test_all을_빼달라는_말은_받아들이지_않는다(self):
        """"전체를 빼 달라"는 말은 필수 항목을 비우라는 뜻이라 거부한다.

        알려진 문제: 요청은 거부되지만 ``all`` 이 5개로 펼쳐진 채 남아 프로필 갱신
        이벤트가 한 번 나간다. 여기서는 "비지 않는다"까지만 확인한다.
        """
        current = dict(profile(), categories=[CATEGORY_ALL])
        _, result = apply_model_output(current, {"category_changes": {"remove": ["all"]}})
        self.assertIn(DROP_WOULD_EMPTY, reasons(result))
        self.assertTrue(result.profile["categories"], "관심 분야가 비었다")

    def test_목록만_오면_추가로_본다(self):
        _, result = apply_model_output(
            profile(), {"category_changes": ["scholarship"]}
        )
        self.assertIn("scholarship", result.profile["categories"])


class TestParseModelJson(unittest.TestCase):
    """모델은 JSON 만 내놓지 않는다."""

    def test_깨진_JSON을_견딘다(self):
        interpretation = parse_model_json('{"intent": "smalltalk",')
        self.assertEqual(interpretation.intent, DEFAULT_INTENT)
        self.assertIn(DROP_NOT_A_MAPPING, [item.reason for item in interpretation.dropped])

    def test_빈_문자열을_견딘다(self):
        self.assertEqual(parse_model_json("   ").intent, DEFAULT_INTENT)

    def test_앞뒤에_설명이_붙은_JSON을_읽는다(self):
        text = '요청을 해석했습니다.\n{"intent": "smalltalk"}\n이상입니다.'
        self.assertEqual(parse_model_json(text).intent, "smalltalk")

    def test_정상_JSON을_읽는다(self):
        text = '{"intent": "result_only", "profile_changes": [{"field": "status", "value": "on_leave"}]}'
        interpretation = parse_model_json(text)
        self.assertEqual(interpretation.intent, "result_only")
        self.assertEqual([c.field for c in interpretation.profile_changes], ["status"])


class TestIntentBehavior(unittest.TestCase):
    """의도별 동작 (README 3장)."""

    def test_의도가_불명이면_기본_의도로_떨어지고_기록이_남는다(self):
        """out_of_scope 로 떨어뜨리면 보여줄 수 있는 결과를 안 보여준다."""
        for raw in ("찾아줘", "", None, 3):
            with self.subTest(intent=raw):
                interpretation = from_model_output({"intent": raw})
                self.assertEqual(interpretation.intent, DEFAULT_INTENT)
                self.assertIn("intent_defaulted", interpretation.notes)

    def test_허용된_의도는_그대로_쓴다(self):
        for intent in fields.INTENTS:
            with self.subTest(intent=intent):
                self.assertEqual(from_model_output({"intent": intent}).intent, intent)

    def test_범위_밖과_잡담은_고정_응답_문구를_쓴다(self):
        self.assertEqual(fixed_reply_for(fields.OUT_OF_SCOPE), OUT_OF_SCOPE_REPLY)
        self.assertEqual(fixed_reply_for(fields.SMALLTALK), SMALLTALK_REPLY)
        self.assertIn("도와드리기 어려워요", OUT_OF_SCOPE_REPLY)

    def test_범위_밖과_잡담은_재계산을_건너뛴다(self):
        """시간 예산을 쓰지 않는다 (README 3장)."""
        for intent in (fields.OUT_OF_SCOPE, fields.SMALLTALK):
            with self.subTest(intent=intent):
                behavior = behavior_of(intent)
                self.assertFalse(behavior.recalculate, f"{intent} 가 재계산을 한다")
                self.assertFalse(behavior.full_flow)
                self.assertFalse(behavior.ask_followup)

    def test_정책_찾기는_전체_흐름을_돌고_고정_응답이_없다(self):
        behavior = behavior_of(fields.FIND_POLICY)
        self.assertTrue(behavior.full_flow)
        self.assertTrue(behavior.recalculate)
        self.assertIsNone(behavior.fixed_reply)

    def test_결과_정리는_질문만_생략하고_재계산은_한다(self):
        """프로필이 바뀌었을 수 있어 재계산은 필요하다. 카드가 낡은 상태로 남으면 안 된다."""
        behavior = behavior_of(fields.RESULT_ONLY)
        self.assertTrue(behavior.recalculate)
        self.assertFalse(behavior.ask_followup)

    def test_모르는_의도의_동작은_기본_의도를_따른다(self):
        self.assertEqual(behavior_of("엉뚱한의도").intent, DEFAULT_INTENT)


class TestChangeNotice(unittest.TestCase):
    """변경 안내 문구. 조사를 코드가 고른다 (값마다 달라서다)."""

    CASES = (
        ("status", "on_leave", "휴학으로 바꿨어요"),
        ("status", "employed", "재직으로 바꿨어요"),
        ("status", "enrolled", "재학으로 바꿨어요"),
        ("housing_type", "monthly_rent", "주거 형태를 월세로 바꿨어요"),
        ("housing_type", "jeonse", "주거 형태를 전세로 바꿨어요"),
        ("district", "마포구", "사는 곳을 마포구로 바꿨어요"),
        # `region` 행은 뺐다. 거주지는 `seoul` 고정이라 변경이 반영되지 않으므로
        # 이 문구는 나갈 수 없다 (``TestRegionIsFixed`` 가 그 계약을 지킨다).
        ("age", 24, "나이를 만 24세로 바꿨어요"),
    )

    def test_조사를_맞게_붙인다(self):
        for field, value, expected in self.CASES:
            with self.subTest(field=field, value=value):
                self.assertEqual(change_notice(field, value), expected)

    def test_틀이_없는_항목은_안내를_만들지_않는다(self):
        """즉석에서 문장을 만들지 않는다 (CONTRIBUTING.md 8장)."""
        self.assertEqual(change_notice("categories", "housing"), "")
        self.assertEqual(change_notice("favorite_color", "blue"), "")

    def test_반영_결과의_안내_문구에_그대로_실린다(self):
        _, result = apply_model_output(
            profile(), {"profile_changes": [change("status", "on_leave")]}
        )
        self.assertIn("휴학으로 바꿨어요", result.notices())


if __name__ == "__main__":
    unittest.main()
