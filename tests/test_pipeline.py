"""한 턴 배선 테스트 (ai/conversation/pipeline.py, docs/03-api-contract.md 9장).

``pipeline`` 은 **서버가 부를 유일한 입구**다. 여기가 조용히 어긋나면 카드는 떠 있는데
후속 질문이 안 뜨거나, 프롬프트에 뼈대가 빠진 채로 모델이 불린다. 둘 다 예외 없이
일어나므로 테스트로만 보인다. 그래서 이 모듈은 정상 경로보다 **판단 근거**를 본다.

여기서 지키려는 계약
  - ``unknown_items_from`` 은 규칙 엔진 dict 를 ``followup.UnknownItem`` 으로 바꾸고,
    **바꿀 수 없는 것은 조용히 버리지 않고 ``dropped`` 에 남긴다** (표기 불일치가 보여야 한다).
  - 없는 ``policy_rank`` 는 0 이 아니라 큰 수로 채운다. 모르는 값이 1위 정책을 앞지르면
    안 된다.
  - ``interpret_turn`` 은 planned 변경을 프로필에 넣지 않고, 범위 밖·잡담에서 고정 응답으로
    턴을 끝내면서도 ``profile_update`` 는 숨기지 않는다.
  - ``build_answer_prompt`` 는 ``Skeleton.outline()`` 을 프롬프트에 싣고, 금지 표현과 길이
    상한을 ``answer`` 모듈에서 끌어온다(다시 적지 않는다). 문안은 ``PROMPT_TODO`` 자리로 남는다.
  - ``finish_turn`` 은 P0 위반만 버리고(금지 표현·각주 문제), 길이와 고정 문구 문제는
    경고만 남기고 내보낸다.
  - 네 함수 모두 **어떤 입력에도 예외를 던지지 않는다.** 여기서 터지면 규칙 기반 카드까지
    500 에 묻힌다.

모델도 네트워크도 쓰지 않는다. 시간을 재는 코드가 없으므로 시계를 주입할 자리도 없다
(예산 추적은 ``llm.BudgetTracker`` 몫이고 ``tests/test_llm.py`` 가 본다).
"""

import json
import re
import unittest
from unittest import mock

from ai.conversation import answer, fields, followup, interpret, llm, pipeline, prompts, questions

# 서버가 부여한 각주 목록 (docs/03-api-contract.md 5장)을 줄여서 쓴다.
FOOTNOTES = (
    {"footnote_id": 1, "policy_id": "SEOUL-001", "excerpt": "월 20만원을 지원합니다"},
    {"footnote_id": 2, "policy_id": "SEOUL-002", "excerpt": "휴학생은 제외합니다"},
)

SUMMARY = "신청 가능성이 높은 제도 2개, 확인이 필요한 제도 1개를 찾았어요."
DETAIL = "청년월세지원은 월 20만원을 지원해요[1]."
CLOSING = answer.CLOSING_LINE + "."

#: 검증을 통과하는 모델 답변. 요약 + 각주가 붙은 설명 + 고정 문구.
GOOD_ANSWER = f"{SUMMARY} {DETAIL} {CLOSING}"

#: 폼에서 받은 기본 프로필 (docs/01-glossary-profile.md 2장).
PROFILE = {
    "age": 24,
    "region": "seoul",
    "district": "관악구",
    "status": "enrolled",
    "categories": ["housing"],
}


def policy(**overrides):
    """정책 판정 결과 하나 (docs/03-api-contract.md 4장). 필요한 키만 덮어쓴다."""
    base = {
        "policy_id": "SEOUL-001",
        "title": "청년월세지원",
        "status": answer.CHECK,
        "conditions": [
            {
                "result": "unknown",
                "needed_field": fields.INCOME_BRACKET,
                "footnote_id": 1,
            }
        ],
    }
    base.update(overrides)
    return base


def unknown(**overrides):
    """규칙 엔진이 낸 미확인 항목 dict 하나 (rules/README.md 8장)."""
    base = {
        "field": fields.INCOME_BRACKET,
        "policy_id": "SEOUL-001",
        "policy_rank": 0,
        "policy_title": "청년월세지원",
    }
    base.update(overrides)
    return base


def change(field, value, timing=fields.CURRENT):
    """해석 결과의 프로필 변경 하나 (docs/05-interfaces.md 3장)."""
    return {"field": field, "value": value, "timing": timing}


def model_output(**overrides):
    """모델 해석 출력 dict. 의도만 있는 최소 형태에서 시작한다."""
    base = {"intent": fields.FIND_POLICY}
    base.update(overrides)
    return base


def finish(**overrides):
    """``finish_turn`` 한 번. 반복되는 네 인자를 줄인다."""
    kwargs = {
        "answer_text": GOOD_ANSWER,
        "footnotes": FOOTNOTES,
        "policies": [policy()],
        "unknown_items": [unknown()],
    }
    kwargs.update(overrides)
    return pipeline.finish_turn(**kwargs)


def block(prompt, open_tag, close_tag):
    """프롬프트에서 태그로 감싼 블록 하나만 떼어 낸다.

    "프롬프트에 값이 있다"가 아니라 "**그 블록에** 값이 있다"를 봐야 한다. 규칙이 출력
    블록에 들어가 있으면 문안 담당자가 찾지 못한다.
    """
    start = prompt.user.find(open_tag)
    end = prompt.user.find(close_tag, start + 1)
    if start == -1 or end == -1:
        return ""
    return prompt.user[start : end + len(close_tag)]


def rules_block(prompt):
    """규칙 블록 본문."""
    return block(prompt, llm.RULES_OPEN, llm.RULES_CLOSE)


def output_block(prompt):
    """출력 규칙 블록 본문. 뼈대와 각주 번호가 여기 들어간다."""
    return block(prompt, llm.OUTPUT_OPEN, llm.OUTPUT_CLOSE)


class TestUnknownItemsConversion(unittest.TestCase):
    """규칙 엔진 dict → ``followup.UnknownItem`` 변환 (경계 어댑터의 본업)."""

    def test_dict_목록을_UnknownItem_목록으로_바꾼다(self):
        """이 변환이 없으면 11단계가 dataclass 를 요구하다 TypeError 로 끝난다."""
        result = pipeline.unknown_items_from([unknown()])
        self.assertEqual(len(result), 1, f"항목이 통과하지 않았다: {result.dropped}")
        item = result.items[0]
        self.assertIsInstance(item, followup.UnknownItem)
        self.assertEqual(
            (item.field, item.policy_id, item.policy_rank, item.policy_title),
            (fields.INCOME_BRACKET, "SEOUL-001", 0, "청년월세지원"),
        )

    def test_키_이름_변형을_받는다(self):
        """규칙 엔진과 AI B 가 같은 개념을 다른 이름으로 낸다. 어느 쪽이든 읽어야 한다."""
        shapes = (
            ("계약 이름", unknown()),
            (
                "needed_field/rank/title",
                {
                    "needed_field": fields.INCOME_BRACKET,
                    "policy_id": "SEOUL-001",
                    "rank": 0,
                    "title": "청년월세지원",
                },
            ),
            (
                "name/display_rank/policyTitle",
                {
                    "name": fields.INCOME_BRACKET,
                    "id": "SEOUL-001",
                    "display_rank": 0,
                    "policyTitle": "청년월세지원",
                },
            ),
        )
        for label, raw in shapes:
            with self.subTest(shape=label):
                result = pipeline.unknown_items_from([raw])
                self.assertEqual(len(result), 1, f"{label}: {result.dropped}")
                item = result.items[0]
                self.assertEqual(item.field, fields.INCOME_BRACKET, label)
                self.assertEqual(item.policy_id, "SEOUL-001", label)
                self.assertEqual(item.policy_rank, 0, label)
                self.assertEqual(item.policy_title, "청년월세지원", label)

    def test_이미_변환된_항목은_그대로_통과시킨다(self):
        """규칙 결과와 AI 결과가 섞이는 중간 상태에서 둘이 한 목록에 들어온다."""
        already = followup.UnknownItem(
            field=fields.HOUSING_TYPE, policy_id="SEOUL-002", policy_rank=1
        )
        result = pipeline.unknown_items_from([unknown(), already])
        self.assertEqual(len(result), 2, f"섞인 목록에서 탈락이 났다: {result.dropped}")
        self.assertIn(already, result.items)

    def test_policy_id가_없어도_버리지_않고_기록만_남긴다(self):
        """정책 번호를 몰라도 물을 항목은 정해진다. 버리면 질문이 사라진다."""
        result = pipeline.unknown_items_from([unknown(policy_id=None)])
        self.assertEqual(len(result), 1, f"버려졌다: {result.dropped}")
        self.assertEqual(result.items[0].policy_id, "")
        self.assertIn(
            f"{pipeline.NOTE_NO_POLICY_ID}:{fields.INCOME_BRACKET}",
            result.notes,
            f"관용 처리 기록이 없다: {result.notes}",
        )

    def test_policy_id가_빈_항목_여러_개는_한_정책으로_센다(self):
        """모르는 값으로 영향 정책 수를 부풀리지 않는다는 판단 (어댑터 독스트링)."""
        result = pipeline.unknown_items_from(
            [unknown(policy_id=""), unknown(policy_id=None)]
        )
        candidates = followup.collect_candidates(result.items)
        self.assertEqual(len(candidates), 1, f"후보가 갈라졌다: {candidates}")
        self.assertEqual(
            candidates[0].policy_count,
            1,
            "policy_id 가 빈 항목이 서로 다른 정책으로 세어졌다",
        )


class TestDefaultPolicyRank(unittest.TestCase):
    """``policy_rank`` 기본값 (어댑터 주석의 판단: 0 이 아니라 큰 수).

    모르는 순위가 1위 정책을 앞지르면 후속 질문이 엉뚱한 항목을 묻는다. 그 방향의 실수가
    "뒤로 밀리는" 실수보다 비싸다.
    """

    def test_rank가_없으면_기본값으로_채운다(self):
        """``UnknownItem`` 에 기본값이 없어 키 하나가 빠지면 TypeError 가 난다."""
        result = pipeline.unknown_items_from([{"field": fields.HOUSING_TYPE, "policy_id": "P1"}])
        self.assertEqual(len(result), 1, f"버려졌다: {result.dropped}")
        self.assertEqual(result.items[0].policy_rank, pipeline.DEFAULT_POLICY_RANK)
        self.assertIn(
            f"{pipeline.NOTE_RANK_DEFAULTED}:{fields.HOUSING_TYPE}",
            result.notes,
            f"기본값으로 채운 기록이 없다: {result.notes}",
        )

    def test_rank가_정수로_읽히지_않으면_기본값이고_이유가_다르게_기록된다(self):
        """값이 온 경우와 아예 없는 경우를 지표에서 구분해야 원인을 찾을 수 있다."""
        result = pipeline.unknown_items_from(
            [{"field": fields.HOUSING_TYPE, "policy_id": "P1", "policy_rank": "곧"}]
        )
        self.assertEqual(result.items[0].policy_rank, pipeline.DEFAULT_POLICY_RANK)
        self.assertIn(f"{pipeline.NOTE_RANK_NOT_INT}:{fields.HOUSING_TYPE}", result.notes)

    def test_기본_rank는_0이_아니라_큰_수다(self):
        """0 이면 순위를 모르는 항목이 화면 맨 위 정책보다 먼저 선택된다."""
        self.assertGreater(
            pipeline.DEFAULT_POLICY_RANK,
            0,
            "기본 rank 가 0 이면 모르는 값이 우선순위를 얻는다",
        )
        self.assertGreaterEqual(
            pipeline.DEFAULT_POLICY_RANK,
            100,
            f"동점 처리에서 뒤로 밀릴 만큼 커야 한다 (지금 {pipeline.DEFAULT_POLICY_RANK})",
        )

    def test_rank를_모르는_항목은_1위_정책보다_뒤로_밀린다(self):
        """상수 값만 보지 않고 ``followup.choose`` 와 함께 실제 선택을 확인한다."""
        result = pipeline.unknown_items_from(
            [
                {"field": fields.HOUSING_TYPE, "policy_id": "P9"},  # rank 없음
                unknown(policy_id="P1", policy_rank=0),
            ]
        )
        chosen = followup.choose(result.items)
        self.assertIsNotNone(chosen, "물을 것이 없다고 판단했다")
        self.assertEqual(
            chosen.field,
            fields.INCOME_BRACKET,
            f"1위 정책의 항목이 아니라 {chosen.field} 를 먼저 물었다",
        )


class TestUnknownItemsDropped(unittest.TestCase):
    """탈락 기록. **이 어댑터가 존재하는 이유다.**

    ``followup.collect_candidates`` 도 같은 항목을 걸러내지만 조용하다. 표기가 어긋났을 때
    "왜 후속 질문이 안 뜨지"로 시간을 태우는 경로가 여기라서, 탈락을 반환값에 싣는지 본다.
    """

    def test_한국어_항목_이름은_탈락하고_기록에_남는다(self):
        """``ai/judgment`` 표기(한국어)와 이 패키지 표기(영문)가 어긋난 상태를 드러내야 한다."""
        name = "다른 지원 수혜 중"
        result = pipeline.unknown_items_from([unknown(field=name)])
        self.assertEqual(len(result), 0, "표에 없는 이름이 통과했다")
        self.assertEqual([entry.reason for entry in result.dropped], [pipeline.DROP_NOT_IN_FIELD_TABLE])
        self.assertEqual([entry.field for entry in result.dropped], [name])
        self.assertIn(
            name,
            result.dropped_fields(),
            f"로그 한 줄에 이름이 안 보인다: {result.dropped_fields()}",
        )

    def test_한국어_이름을_영문으로_매핑하지_않는다(self):
        """매핑을 여기서 만들면 표기 결정이 코드에 숨는다. 12:00 에 팀이 정할 일이다."""
        result = pipeline.unknown_items_from([unknown(field="다른 지원 수혜 중")])
        self.assertEqual(
            [item.field for item in result.items],
            [],
            "한국어 이름을 영문 항목으로 이어 붙였다",
        )

    def test_표에_없는_이름은_이유_코드로_구분된다(self):
        cases = (
            ("표에 없는 영문 이름", "favorite_color", pipeline.DROP_NOT_IN_FIELD_TABLE),
            ("폼에서 받은 기본 항목", fields.AGE, pipeline.DROP_NOT_IN_FIELD_TABLE),
            ("공고 확인 필요", fields.ASK_NOTICE, pipeline.DROP_NOT_IN_FIELD_TABLE),
        )
        for label, name, reason in cases:
            with self.subTest(case=label):
                result = pipeline.unknown_items_from([unknown(field=name)])
                self.assertEqual(len(result), 0, f"{label} 이 통과했다")
                self.assertEqual([entry.reason for entry in result.dropped], [reason], label)

    def test_공고_확인_필요는_물을_수_없어_탈락한다(self):
        """``ASK_NOTICE`` 는 항목이 아니라 "공고를 직접 보라"는 표시다 (fields.py)."""
        result = pipeline.unknown_items_from([unknown(field=fields.ASK_NOTICE)])
        self.assertEqual(len(result), 0)
        self.assertIn(fields.ASK_NOTICE, result.dropped_fields())

    def test_질문_틀이_없는_항목은_탈락한다(self):
        """즉석에서 질문 문장을 만들지 않는다 (README 5장). 지금은 표가 다 채워져 있어
        틀 하나를 비운 상태를 만들어 확인한다."""
        self.assertEqual(
            questions.missing_templates(), [], "전제 확인: 지금은 질문 틀이 다 있다"
        )
        trimmed = {
            name: template
            for name, template in questions.TEMPLATES.items()
            if name != fields.HOUSEHOLD_SIZE
        }
        with mock.patch.object(questions, "TEMPLATES", trimmed):
            result = pipeline.unknown_items_from(
                [unknown(field=fields.HOUSEHOLD_SIZE, policy_rank=0)]
            )
        self.assertEqual(len(result), 0, "질문 문구가 없는데 통과했다")
        self.assertEqual(
            [entry.reason for entry in result.dropped],
            [pipeline.DROP_NO_QUESTION_TEMPLATE],
            f"이유 코드가 다르다: {result.dropped}",
        )

    def test_항목_이름을_읽을_수_없으면_탈락한다(self):
        result = pipeline.unknown_items_from([{"policy_id": "P1", "policy_rank": 0}])
        self.assertEqual([entry.reason for entry in result.dropped], [pipeline.DROP_NO_FIELD])
        self.assertEqual(result.dropped_fields(), (), "이름이 없으면 로그에 빈 값을 넣지 않는다")

    def test_dict가_아닌_원소는_타입_이름과_자리를_남긴다(self):
        """규칙 엔진 출력과 맞춰 보려면 몇 번째가 이상했는지 알아야 한다."""
        result = pipeline.unknown_items_from([unknown(), "income_bracket", None])
        reasons = [(entry.index, entry.reason, entry.raw) for entry in result.dropped]
        self.assertEqual(
            reasons,
            [(1, pipeline.DROP_NOT_A_MAPPING, "str"), (2, pipeline.DROP_NOT_A_MAPPING, "NoneType")],
            f"자리와 타입이 기록되지 않았다: {result.dropped}",
        )

    def test_summary가_지표용_숫자를_담는다(self):
        """지표는 "후속 질문이 왜 안 떴나"를 사후에 세는 유일한 값이다 (13장)."""
        result = pipeline.unknown_items_from(
            [unknown(), unknown(field="다른 지원 수혜 중"), "이상한 값"]
        )
        summary = result.summary()
        self.assertEqual(summary["kept"], 1, summary)
        self.assertEqual(summary["dropped"], 2, summary)
        self.assertEqual(
            summary["dropped_reasons"],
            {pipeline.DROP_NOT_IN_FIELD_TABLE: 1, pipeline.DROP_NOT_A_MAPPING: 1},
            summary,
        )
        self.assertEqual(summary["dropped_fields"], ["다른 지원 수혜 중"], summary)
        self.assertIn("notes", summary)

    def test_dropped_fields는_중복과_빈_이름을_뺀다(self):
        result = pipeline.unknown_items_from(
            [unknown(field="다른 지원 수혜 중"), unknown(field="다른 지원 수혜 중"), 3]
        )
        self.assertEqual(result.dropped_fields(), ("다른 지원 수혜 중",))


class TestUnknownItemsTolerance(unittest.TestCase):
    """깨진 입력. 어댑터가 터지면 답변도 카드도 같이 사라진다."""

    TOLERATED = (
        ("None", None),
        ("문자열", "income_bracket"),
        ("숫자", 7),
        ("None 하나 든 목록", [None]),
        ("dict 하나", unknown()),
        ("빈 목록", []),
        ("빈 dict", [{}]),
        ("값이 모두 None", [{"field": None, "policy_id": None, "policy_rank": None}]),
        ("중첩 목록", [[unknown()]]),
        ("불리언", True),
    )

    def test_깨진_입력에도_예외를_던지지_않는다(self):
        for label, raw in self.TOLERATED:
            with self.subTest(case=label):
                result = pipeline.unknown_items_from(raw)
                self.assertIsInstance(result, pipeline.UnknownItems, label)
                self.assertIsInstance(result.summary(), dict, label)

    def test_dict_하나만_와도_항목으로_읽는다(self):
        """목록 자리에 dict 하나가 오는 실수가 실제로 있다. 버리면 질문이 사라진다."""
        result = pipeline.unknown_items_from(unknown())
        self.assertEqual(len(result), 1, f"dict 하나를 못 읽었다: {result.dropped}")

    def test_빈_입력은_통과도_탈락도_없다(self):
        for raw in (None, [], ()):
            with self.subTest(raw=raw):
                result = pipeline.unknown_items_from(raw)
                self.assertEqual((len(result), len(result.dropped)), (0, 0))


class TestUnknownItemsHandoff(unittest.TestCase):
    """``followup`` 에 그대로 넘길 수 있는가. 여기가 이 어댑터의 목적지다."""

    def test_len과_iter로_UnknownItem_목록처럼_쓰인다(self):
        result = pipeline.unknown_items_from([unknown(), unknown(field=fields.HOUSING_TYPE)])
        self.assertEqual(len(result), 2)
        self.assertEqual(list(result), list(result.items))

    def test_next_question에_변환_결과를_바로_넘길_수_있다(self):
        """``followup.next_question(unknown_items_from(raw))`` 가 이 어댑터의 사용법이다."""
        result = pipeline.unknown_items_from([unknown()])
        question = followup.next_question(result, None, fields.FIND_POLICY)
        self.assertIsNotNone(question, "변환 결과를 11단계가 못 읽었다")
        self.assertEqual(question["field"], fields.INCOME_BRACKET)
        self.assertTrue(question["allow_skip"], "건너뛰기는 항상 허용이다 (6장)")

    def test_finish_turn은_변환_결과와_dict_목록을_모두_받는다(self):
        """서버가 어느 쪽을 넘겨도 후속 질문이 같아야 한다."""
        converted = pipeline.unknown_items_from([unknown()])
        for label, raw in (("dict 목록", [unknown()]), ("변환 결과", converted)):
            with self.subTest(shape=label):
                result = finish(unknown_items=raw)
                self.assertIsNotNone(result.followup, f"{label}: 질문이 없다")
                self.assertEqual(result.followup["field"], fields.INCOME_BRACKET, label)

    def test_finish_turn이_탈락_기록을_결과에_실어_보낸다(self):
        """후속 질문이 없는 이유가 표기 불일치인지 바로 보여야 한다."""
        result = finish(unknown_items=[unknown(field="다른 지원 수혜 중")])
        self.assertIsNone(result.followup, "물을 수 없는 항목으로 질문을 만들었다")
        self.assertEqual(result.unknown_items.dropped_fields(), ("다른 지원 수혜 중",))
        self.assertEqual(
            result.metrics["unknown_items"]["dropped_reasons"],
            {pipeline.DROP_NOT_IN_FIELD_TABLE: 1},
            result.metrics["unknown_items"],
        )


class TestInterpretTurnInput(unittest.TestCase):
    """3단계 입력 모양. 서버가 "문자열인지 dict인지"를 판단하지 않게 여기서 나눈다."""

    def test_dict와_JSON_문자열을_모두_받는다(self):
        data = model_output(profile_changes=[change(fields.STATUS, "on_leave")])
        shapes = (
            ("dict", data),
            ("JSON 문자열", json.dumps(data, ensure_ascii=False)),
        )
        for label, raw in shapes:
            with self.subTest(shape=label):
                result = pipeline.interpret_turn(PROFILE, raw)
                self.assertEqual(result.profile["status"], "on_leave", label)

    def test_앞뒤에_설명이_붙은_JSON도_읽는다(self):
        """모델이 "네, 해석했어요 {...}" 로 답하는 경우가 실제로 있다."""
        data = json.dumps(
            model_output(profile_changes=[change(fields.STATUS, "on_leave")]),
            ensure_ascii=False,
        )
        result = pipeline.interpret_turn(PROFILE, f"해석 결과는 다음과 같아요. {data} 이상입니다.")
        self.assertEqual(result.profile["status"], "on_leave", "설명이 붙은 JSON 을 못 읽었다")

    def test_해석_결과와_반영_결과를_그대로_담는다(self):
        """서버가 applied·held·dropped 를 직접 읽어야 해서 새 dict 로 감싸지 않는다."""
        result = pipeline.interpret_turn(PROFILE, model_output())
        self.assertIsInstance(result.interpretation, interpret.Interpretation)
        self.assertIsInstance(result.merged, interpret.MergeResult)
        self.assertEqual(result.intent, fields.FIND_POLICY)


class TestInterpretTurnProfile(unittest.TestCase):
    """프로필 반영 (README 3장). planned 를 지금 값으로 넣으면 틀린 안내가 된다."""

    def test_planned_변경은_프로필을_바꾸지_않고_보류된다(self):
        """휴학 예정자에게 휴학생 전용 제도를 지금 신청 가능한 것처럼 보여줄 수 없다."""
        result = pipeline.interpret_turn(
            PROFILE, model_output(profile_changes=[change(fields.STATUS, "on_leave", fields.PLANNED)])
        )
        self.assertEqual(result.profile["status"], "enrolled", "planned 변경이 반영됐다")
        self.assertEqual(
            [held.field for held in result.merged.held],
            [fields.STATUS],
            f"보류 목록이 비었다: {result.merged.held}",
        )
        self.assertTrue(result.needs_planned_confirmation, "확인 질문 대상이 아니라고 봤다")

    def test_planned만_있으면_profile_update가_없다(self):
        """이벤트는 프로필이 바뀔 때만 보낸다 (5장). 안 바뀐 것을 바뀌었다고 하면 안 된다."""
        result = pipeline.interpret_turn(
            PROFILE, model_output(profile_changes=[change(fields.STATUS, "on_leave", fields.PLANNED)])
        )
        self.assertIsNone(result.profile_update, f"바뀐 게 없는데 이벤트를 만들었다: {result.profile_update}")

    def test_current_변경은_폼_값을_덮어쓰고_이벤트를_만든다(self):
        """대화 값이 폼 값을 이긴다 (docs/05-interfaces.md 6장)."""
        result = pipeline.interpret_turn(
            PROFILE, model_output(profile_changes=[change(fields.STATUS, "on_leave")])
        )
        self.assertEqual(result.profile["status"], "on_leave")
        self.assertIsNotNone(result.profile_update, "프로필이 바뀌었는데 이벤트가 없다")
        self.assertEqual(
            [entry["field"] for entry in result.profile_update["changes"]], [fields.STATUS]
        )
        self.assertEqual(result.profile_update["profile"]["status"], "on_leave")
        self.assertTrue(result.profile_update["notice"], "화면에 붙일 안내 문구가 비었다")

    def test_입력_프로필을_바꾸지_않는다(self):
        """원본을 고치면 전후 비교가 불가능해져 "휴학으로 바꿨어요"를 만들 수 없다."""
        original = {"status": "enrolled", "categories": ["housing"]}
        snapshot = json.dumps(original, ensure_ascii=False, sort_keys=True)
        pipeline.interpret_turn(
            original, model_output(profile_changes=[change(fields.STATUS, "on_leave")])
        )
        self.assertEqual(
            json.dumps(original, ensure_ascii=False, sort_keys=True),
            snapshot,
            "입력 프로필이 바뀌었다",
        )

    def test_허용_값_밖은_반영하지_않고_기록만_남긴다(self):
        """검증은 ``interpret`` 이 한다. 이 파일이 허용 값 표를 다시 갖지 않는지 본다."""
        result = pipeline.interpret_turn(
            PROFILE, model_output(profile_changes=[change(fields.STATUS, "그만두는_중")])
        )
        self.assertEqual(result.profile["status"], "enrolled")
        self.assertIsNone(result.profile_update)
        self.assertTrue(result.merged.dropped, "무엇을 버렸는지 기록이 없다")


class TestInterpretTurnEarlyExit(unittest.TestCase):
    """4단계 조기 종료 (9장, README 3장 의도별 동작).

    이 경로가 없으면 범위 밖 질문에도 전체 흐름이 돌아 20초를 답할 수 없는 질문에 쓴다.
    """

    FIXED_REPLY_INTENTS = (
        (fields.OUT_OF_SCOPE, interpret.OUT_OF_SCOPE_REPLY),
        (fields.SMALLTALK, interpret.SMALLTALK_REPLY),
    )

    def test_범위_밖과_잡담은_고정_응답으로_끝낸다(self):
        for intent, expected in self.FIXED_REPLY_INTENTS:
            with self.subTest(intent=intent):
                result = pipeline.interpret_turn(PROFILE, model_output(intent=intent))
                self.assertEqual(result.fixed_reply, expected, f"{intent}: 고정 응답이 다르다")
                self.assertTrue(result.stop_here, f"{intent}: 턴을 끝내지 않았다")

    def test_범위_밖과_잡담은_규칙_재계산을_요구하지_않는다(self):
        """판정을 하지 않는 경로다. 재계산을 돌리면 예산만 쓴다."""
        for intent, _ in self.FIXED_REPLY_INTENTS:
            with self.subTest(intent=intent):
                result = pipeline.interpret_turn(PROFILE, model_output(intent=intent))
                self.assertFalse(result.needs_recalculation, f"{intent}: 재계산을 요구했다")

    def test_고정_응답_경로에서도_profile_update는_그대로_나온다(self):
        """"범위 밖"은 답변을 줄이라는 뜻이고 프로필을 숨기라는 뜻이 아니다.

        숨기면 화면의 프로필 바와 세션 값이 어긋난다.
        """
        result = pipeline.interpret_turn(
            PROFILE,
            model_output(
                intent=fields.OUT_OF_SCOPE, profile_changes=[change(fields.STATUS, "on_leave")]
            ),
        )
        self.assertTrue(result.stop_here, "전제 확인: 조기 종료 경로다")
        self.assertIsNotNone(result.profile_update, "프로필 변경 이벤트를 숨겼다")
        self.assertEqual(result.profile["status"], "on_leave")

    def test_정책_찾기는_고정_응답이_없고_재계산이_필요하다(self):
        result = pipeline.interpret_turn(PROFILE, model_output(intent=fields.FIND_POLICY))
        self.assertIsNone(result.fixed_reply, "모델이 답변을 쓸 경로인데 고정 응답이 생겼다")
        self.assertFalse(result.stop_here)
        self.assertTrue(result.needs_recalculation)

    def test_결과_정리와_비교도_재계산은_한다(self):
        """프로필이 바뀌었을 수 있으므로 카드를 낡은 상태로 두지 않는다 (README 3장 표)."""
        for intent in (fields.RESULT_ONLY, fields.COMPARE, fields.POLICY_QUESTION):
            with self.subTest(intent=intent):
                result = pipeline.interpret_turn(PROFILE, model_output(intent=intent))
                self.assertTrue(result.needs_recalculation, f"{intent}: 재계산을 건너뛰었다")
                self.assertFalse(result.stop_here, f"{intent}: 여기서 끝내면 카드 설명이 없다")


class TestInterpretTurnTolerance(unittest.TestCase):
    """깨진 모델 출력. 해석 실패가 규칙 기반 카드를 지우지 않아야 한다."""

    TOLERATED = (
        ("None", None),
        ("숫자", 7),
        ("실수", 3.5),
        ("불리언", True),
        ("설명만 있는 문자열", "무슨 말인지 모르겠어요"),
        ("빈 문자열", ""),
        ("빈 dict", {}),
        ("목록", [1, 2, 3]),
        ("깨진 JSON", '{"intent": "find_policy", '),
        ("아무 객체", object()),
    )

    def test_깨진_입력에도_예외를_던지지_않는다(self):
        for label, raw in self.TOLERATED:
            with self.subTest(case=label):
                result = pipeline.interpret_turn(PROFILE, raw)
                self.assertIsInstance(result, pipeline.InterpretedTurn, label)
                self.assertEqual(result.intent, interpret.DEFAULT_INTENT, label)

    def test_해석에_실패하면_변경_없이_진행한다(self):
        """프로필이 그대로여도 후보 선정은 돌아야 카드가 나온다."""
        for label, raw in self.TOLERATED:
            with self.subTest(case=label):
                result = pipeline.interpret_turn(PROFILE, raw)
                self.assertEqual(result.profile, PROFILE, f"{label}: 프로필이 바뀌었다")
                self.assertIsNone(result.profile_update, f"{label}: 없는 변경을 알렸다")
                self.assertTrue(result.needs_recalculation, f"{label}: 재계산을 건너뛰었다")
                self.assertFalse(result.stop_here, f"{label}: 턴을 조기 종료했다")

    def test_프로필_자체가_이상해도_예외가_없다(self):
        """세션 값이 깨진 상태에서도 서버가 500 을 내지 않아야 한다."""
        for label, profile in (("문자열", "이상한 프로필"), ("숫자", 5), ("목록", [1, 2])):
            with self.subTest(case=label):
                result = pipeline.interpret_turn(profile, model_output())
                self.assertIsInstance(result.profile, dict, label)

    def test_프로필이_없어도_동작한다(self):
        result = pipeline.interpret_turn(None, model_output(profile_changes=[change(fields.STATUS, "on_leave")]))
        self.assertEqual(result.profile["status"], "on_leave")


class TestBuildAnswerPromptWiring(unittest.TestCase):
    """10단계 준비. 여기서 확인할 것은 문안이 아니라 **연결**이다."""

    def setUp(self):
        self.policies = [
            policy(policy_id="A", title="청년월세지원", status=answer.LIKELY,
                   conditions=[{"footnote_id": 2}, {"footnote_id": 5}]),
            policy(policy_id="B", title="희망두배청년통장", status=answer.CHECK,
                   conditions=[{"footnote_id": "번호 미정"}]),
        ]
        self.skeleton = answer.build_skeleton(self.policies)

    def prompt(self, **overrides):
        kwargs = {
            "profile": PROFILE,
            "policies": self.policies,
            "source_text": "월세 지원은 월 20만원입니다",
        }
        kwargs.update(overrides)
        return pipeline.build_answer_prompt(self.skeleton, **kwargs)

    def test_뼈대_줄_목록이_출력_규칙에_들어간다(self):
        """``Skeleton.outline()`` 은 "프롬프트에 그대로 넣는다"고 적힌 값이다. 그 연결이 핵심이다."""
        prompt = self.prompt()
        rules = output_block(prompt)
        self.assertTrue(self.skeleton.outline(), "전제 확인: 뼈대에 줄이 있다")
        for line in self.skeleton.outline():
            with self.subTest(line=line):
                self.assertIn(line, rules, f"뼈대 줄이 프롬프트에 없다: {line!r}")

    def test_요약과_고정_문구가_뼈대_순서대로_실린다(self):
        """요약과 마감 문장은 코드가 확정한 값이다. 모델이 다시 쓰면 개수가 어긋난다."""
        rules = output_block(self.prompt())
        self.assertLess(
            rules.index(self.skeleton.summary),
            rules.index(self.skeleton.closing_line),
            "요약이 고정 문구보다 뒤에 있다",
        )

    def test_정책_조건에서_각주_번호를_모은다(self):
        """번호는 서버가 부여한 값을 읽기만 한다 (4-1). 여기서 만들지 않는다."""
        rules = output_block(self.prompt())
        self.assertIn("[2]", rules, "조건의 footnote_id 를 읽지 않았다")
        self.assertIn("[5]", rules)
        self.assertNotIn("번호 미정", rules, "정수로 읽을 수 없는 값을 실었다")

    def test_각주_목록이_있으면_그것을_우선_쓴다(self):
        """각주 목록은 인용 검증을 통과한 결과다. 조건에서 모은 값보다 정확하다."""
        rules = output_block(self.prompt(footnotes=[{"footnote_id": 7}]))
        self.assertIn("[7]", rules)
        self.assertNotIn("[2]", rules, "각주 목록이 있는데 조건에서 다시 모았다")

    def test_쓸_수_있는_번호가_없으면_없다고_적는다(self):
        """"번호 없음"을 적지 않으면 모델이 번호를 지어낸다."""
        prompt = pipeline.build_answer_prompt(self.skeleton, policies=[], footnotes=None)
        self.assertIn("각주 번호 없음", output_block(prompt))

    def test_금지_표현_목록이_규칙_블록에_들어간다(self):
        rules = rules_block(self.prompt())
        for label in pipeline.BANNED_PHRASE_LABELS:
            with self.subTest(label=label):
                self.assertIn(label, rules, f"금지 표현 {label} 이 규칙 블록에 없다")

    def test_금지_표현_목록을_answer_모듈에서_끌어온다(self):
        """목록을 다시 적으면 한쪽만 고쳐진다. 값이 아니라 출처를 확인한다."""
        self.assertEqual(
            pipeline.BANNED_PHRASE_LABELS,
            tuple(phrase.label for phrase in answer.BANNED_PHRASES),
            "pipeline 이 금지 표현 목록을 따로 들고 있다",
        )

    def test_600자_상한이_규칙_블록에_들어가고_answer_값을_쓴다(self):
        rules = rules_block(self.prompt())
        self.assertIn(
            str(answer.MAX_ANSWER_LEN),
            rules,
            f"길이 상한({answer.MAX_ANSWER_LEN})이 규칙 블록에 없다",
        )
        self.assertEqual(
            [slot.id for slot in pipeline.ANSWER_RULE_SLOTS].count("length"),
            1,
            "길이 규칙 자리가 하나가 아니다",
        )

    def test_규칙_블록에_네_가지_자리와_문안이_모두_있다(self):
        """네 항목이 자리와 **문안**을 함께 가져야 한다.

        근거 문서 경로(``slot.doc_ref``)는 프롬프트에 넣지 않는다. 모델에게 저장소 경로는
        무의미한 토큰이고, 최악의 경우 "README 를 확인하겠습니다" 같은 메타 응답을 부른다.
        ``PROMPT_TODO`` 자리표시자가 실려 나갔을 때 실제로 그 일이 났다 — 모델이 빈 문안을
        채워 주는 답변을 냈다. 근거를 사람이 찾을 수 있어야 한다는 요구는 아래
        ``test_근거_문서는_코드에_남아_있다`` 가 지킨다. 코드를 읽으면 된다.
        """
        rules = rules_block(self.prompt())
        for slot in pipeline.ANSWER_RULE_SLOTS:
            with self.subTest(slot=slot.id):
                self.assertIn(f"[{slot.id}]", rules, f"{slot.id} 자리가 없다")
                text = prompts.rule_text(slot.id)
                self.assertTrue(text, f"{slot.id} 문안이 비었다")
                self.assertIn(
                    text.splitlines()[0], rules, f"{slot.id} 문안이 프롬프트에 없다"
                )

    def test_근거_문서는_코드에_남아_있다(self):
        """프롬프트에서 뺐어도 사람이 근거를 찾을 수 있어야 한다."""
        for slot in pipeline.ANSWER_RULE_SLOTS:
            with self.subTest(slot=slot.id):
                self.assertTrue(slot.doc_ref, f"{slot.id} 근거 문서가 비었다")

    def test_저장소_경로가_프롬프트에_새지_않는다(self):
        """모델에게 파일 경로를 주면 그것을 읽으려 하거나 언급한다."""
        prompt = self.prompt()
        for needle in (".md", "docs/", "ai/conversation/"):
            with self.subTest(needle=needle):
                self.assertNotIn(needle, prompt.user, f"프롬프트에 {needle} 가 있다")

    def test_자리표시자가_프롬프트에_남지_않는다(self):
        """문안을 여기서 지어내면 검토 없이 데모에 들어간다. 지금은 비어 있어야 정상이다."""
        prompt = self.prompt()
        self.assertNotIn(
            pipeline.PROMPT_TODO,
            rules_block(prompt),
            "규칙 블록에 자리표시자가 남아 있다. 모델이 그것을 채우려 든다",
        )
        self.assertNotIn(
            pipeline.PROMPT_TODO,
            output_block(prompt),
            "출력 블록에 자리표시자가 남아 있다",
        )
        self.assertNotIn(
            pipeline.PROMPT_TODO, prompt.user, "프롬프트 어디에도 자리표시자가 없어야 한다"
        )
        # 뼈대가 내는 ``{{정책별 설명}}`` 은 다르다. 그것은 **모델이 채울 자리**이고
        # ``Skeleton.outline()`` 이 의도적으로 넣는다(``answer.py``). ``PROMPT_TODO`` 는
        # 사람이 채워야 할 자리라 모델에게 보여선 안 된다. 둘을 구분해 검사한다.
        self.assertIn(
            "{{", prompt.user, "전제 확인: 뼈대의 모델 채울 자리는 남아 있어야 한다"
        )

    def test_프로필_값을_그대로_싣고_순서를_고정한다(self):
        """같은 프로필에 같은 프롬프트가 나와야 캐시가 걸린다."""
        first = self.prompt()
        shuffled = dict(reversed(list(PROFILE.items())))
        second = self.prompt(profile=shuffled)
        self.assertEqual(first.user, second.user, "키 순서에 따라 프롬프트가 달라졌다")
        self.assertIn("status: enrolled", block(first, llm.PROFILE_OPEN, llm.PROFILE_CLOSE))

    def test_캐싱을_쓰지_않으면_규칙이_앞과_끝에_두_번_온다(self):
        prompt = self.prompt(cache_friendly=False)
        self.assertEqual(
            prompt.order, ("rules", "source", "profile", "output_rules", "rules_tail"), prompt.order
        )

    def test_캐싱_경로에서는_원문이_맨_앞이고_프로필이_뒤로_간다(self):
        """매번 바뀌는 프로필이 원문 앞에 오면 접두사가 달라져 캐시가 조용히 미적용된다."""
        prompt = self.prompt(cache_friendly=True)
        self.assertEqual(prompt.order, ("source", "rules", "profile", "output_rules"), prompt.order)
        self.assertEqual(prompt.user.index(llm.SOURCE_OPEN), 0, "원문이 맨 앞이 아니다")
        self.assertLess(
            prompt.user.index(llm.SOURCE_OPEN),
            prompt.user.index(llm.PROFILE_OPEN),
            "프로필이 원문보다 앞에 있다",
        )

    def test_원문을_비우면_그_블록을_건너뛴다(self):
        prompt = self.prompt(source_text="")
        self.assertNotIn("source", prompt.order, prompt.order)

    def test_시스템_지시문은_받은_값을_그대로_쓴다(self):
        """동적으로 조립하면 캐시 접두사가 깨진다 (research 5장)."""
        prompt = self.prompt(system="너는 청년 정책 안내를 돕는다")
        self.assertEqual(prompt.system, "너는 청년 정책 안내를 돕는다")


class TestBuildAnswerPromptTolerance(unittest.TestCase):
    """프롬프트 조립 실패가 카드를 지우지 않아야 한다 (9장 10단계)."""

    TOLERATED = (
        ("뼈대 None", None),
        ("뼈대가 문자열", "요약 한 줄"),
        ("뼈대가 숫자", 5),
        ("outline 이 없는 객체", object()),
    )

    def test_뼈대가_이상해도_예외를_던지지_않는다(self):
        for label, skeleton in self.TOLERATED:
            with self.subTest(case=label):
                prompt = pipeline.build_answer_prompt(skeleton)
                self.assertIsInstance(prompt, llm.AssembledPrompt, label)
                self.assertIn("rules", prompt.order, f"{label}: 규칙 블록마저 사라졌다")

    def test_뼈대가_없으면_없다고_적는다(self):
        """빈 자리를 그냥 두면 모델이 순서를 지어낸다."""
        prompt = pipeline.build_answer_prompt(None)
        self.assertIn("뼈대 없음", output_block(prompt))

    def test_정책_목록이_이상한_타입이어도_동작한다(self):
        skeleton = answer.build_skeleton([policy()])
        for label, policies in (
            ("문자열", "정책 목록"),
            ("None", None),
            ("숫자", 3),
            ("None 이 섞인 목록", [None, policy()]),
            ("dict 하나", policy()),
        ):
            with self.subTest(case=label):
                prompt = pipeline.build_answer_prompt(skeleton, policies=policies)
                self.assertIn("rules", prompt.order, label)

    def test_outline이_터지는_뼈대여도_프롬프트는_나온다(self):
        class BrokenSkeleton:
            """``outline()`` 이 예외를 던지는 뼈대. 중간 상태에서 나올 수 있다."""

            def outline(self):
                raise RuntimeError("뼈대가 깨졌다")

        prompt = pipeline.build_answer_prompt(BrokenSkeleton())
        self.assertIn("뼈대 없음", output_block(prompt))


class TestFinishTurnAnswer(unittest.TestCase):
    """10단계 정리·검증. 정상 답변은 그대로 나가야 한다."""

    def test_정상_답변이_그대로_나간다(self):
        result = finish()
        self.assertFalse(result.answer_failed, f"정상 답변이 막혔다: {result.blocking_codes}")
        self.assertIn(DETAIL, result.answer_text)
        self.assertIn(answer.CLOSING_LINE, result.answer_text)
        self.assertTrue(result.check.ok, f"검증에 걸렸다: {result.check.problems}")

    def test_후속_질문과_칩이_답변과_함께_온다(self):
        """11·12단계를 같은 함수에서 끝내는 것이 이 함수의 계약이다."""
        result = finish(policies=[policy(), policy(policy_id="B", status=answer.UNLIKELY)])
        self.assertIsNotNone(result.followup, "물을 것이 있는데 질문이 없다")
        self.assertEqual(result.followup["field"], fields.INCOME_BRACKET)
        self.assertTrue(result.related["chips"], f"칩이 비었다: {result.related}")

    def test_정리_기록을_함께_돌려준다(self):
        """무엇을 왜 뺐는지 지표로 남아야 한다 (13장)."""
        result = finish(answer_text=f"{SUMMARY} 확실히 조건을 충족해요[1]. {CLOSING}")
        codes = [removal.code for removal in result.removals]
        self.assertTrue(codes, "정리했는데 기록이 없다")
        self.assertFalse(result.answer_failed, "수식어만 지우고 살릴 수 있는 답변을 버렸다")
        self.assertNotIn("확실히", result.answer_text)


class TestFinishTurnDrops(unittest.TestCase):
    """P0 위반만 버린다는 판단 (``BLOCKING_PROBLEM_CODES`` 주석).

    카드는 이미 화면에 있다. 근거 없는 단정을 내보내는 손해가 설명을 못 내보내는 손해보다 크다.
    """

    def test_버리는_문제는_금지_표현과_각주_문제뿐이다(self):
        self.assertEqual(
            pipeline.BLOCKING_PROBLEM_CODES,
            frozenset({answer.BANNED, answer.MISSING_FOOTNOTE, answer.UNKNOWN_FOOTNOTE}),
            "버리는 문제 목록이 바뀌었다",
        )

    def test_금지_표현이_정리_후에도_남으면_답변을_버린다(self):
        """``sanitize`` 계약이 깨진 상황이다. 정리 단계를 믿고 넘기면 단정이 그대로 나간다.

        고정 문구는 ``sanitize`` 가 금지 표현 검사 **뒤에** 붙이므로, 그 문구에 걸리는
        표현을 넣으면 정리를 통과해 검증까지 살아 남는다. 그 구멍을 ``finish_turn`` 이
        막는지 본다.
        """
        surviving = answer.BannedPhrase(
            "테스트금지", re.compile(re.escape(answer.CLOSING_LINE))
        )
        with mock.patch.object(answer, "BANNED_PHRASES", (surviving,)):
            result = finish()
        self.assertTrue(result.answer_failed, "금지 표현이 남았는데 내보냈다")
        self.assertEqual(result.answer_text, "", "실패인데 본문이 남았다")
        self.assertIn(
            answer.BANNED, result.blocking_codes, f"이유가 기록되지 않았다: {result.blocking_codes}"
        )

    def test_정리_후_고정_문구만_남으면_실패로_본다(self):
        """설명을 요청했는데 주의 문구 한 줄만 받는 것은 실패를 성공처럼 보여 주는 것이다."""
        cases = (
            ("고정 문구만 있는 답변", CLOSING),
            ("전부 금지 표현", f"확실히 지원 대상입니다[1]. {CLOSING}"),
            ("빈 답변", ""),
            ("공백만", "   "),
        )
        for label, text in cases:
            with self.subTest(case=label):
                result = finish(answer_text=text)
                self.assertTrue(result.answer_failed, f"{label}: 주의 문구만 내보냈다")
                self.assertEqual(result.answer_text, "", label)
                self.assertIn(
                    pipeline.EMPTY_AFTER_SANITIZE,
                    result.blocking_codes,
                    f"{label}: 이유가 {result.blocking_codes} 로 기록됐다",
                )

    def test_실패해도_칩과_지표는_남는다(self):
        """답변을 버리는 것과 카드·칩을 지우는 것은 다른 일이다."""
        result = finish(answer_text="", policies=[policy(), policy(policy_id="B")])
        self.assertTrue(result.answer_failed)
        self.assertIn("chips", result.related)
        self.assertIn("answer", result.metrics)

    def test_각주_목록을_안_넘기면_판정_문장이_사라진다(self):
        """인용 검증 결과를 빼먹으면 근거가 붙은 문장이 전부 없는 번호 참조가 된다."""
        result = finish(footnotes=None)
        self.assertNotIn(DETAIL, result.answer_text, "없는 번호를 참조하는 문장이 남았다")
        self.assertIn(
            answer.UNKNOWN_FOOTNOTE,
            [removal.code for removal in result.removals],
            f"왜 빠졌는지 기록이 없다: {result.removals}",
        )

    def test_각주_목록을_안_넘기고_설명만_있으면_실패가_된다(self):
        """요약 없이 설명만 있는 답변은 통째로 빠져 고정 문구만 남는다."""
        result = finish(answer_text=f"{DETAIL} {CLOSING}", footnotes=None)
        self.assertTrue(result.answer_failed, "설명이 전부 빠졌는데 성공으로 봤다")
        self.assertIn(pipeline.EMPTY_AFTER_SANITIZE, result.blocking_codes)


class TestFinishTurnKeepsImperfect(unittest.TestCase):
    """길이 초과와 고정 문구 누락은 버리지 않는다는 판단.

    읽기 불편할 뿐 틀린 안내가 아니다. 불편함 때문에 설명을 없애지 않는다.
    """

    def test_길이와_고정_문구_문제는_버리는_목록에_없다(self):
        for code in (answer.TOO_LONG, answer.NO_CLOSING_LINE):
            with self.subTest(code=code):
                self.assertNotIn(
                    code, pipeline.BLOCKING_PROBLEM_CODES, f"{code} 때문에 답변을 버린다"
                )

    def test_긴_답변은_줄여서_내보낸다(self):
        long_text = SUMMARY + " " + (DETAIL + " ") * 40 + CLOSING
        self.assertGreater(len(long_text), answer.MAX_ANSWER_LEN, "전제 확인: 상한을 넘는 표본")
        result = finish(answer_text=long_text)
        self.assertFalse(result.answer_failed, f"길이 때문에 버렸다: {result.blocking_codes}")
        self.assertLessEqual(len(result.answer_text), answer.MAX_ANSWER_LEN)
        self.assertTrue(result.metrics["answer"]["truncated"], "줄인 기록이 없다")
        self.assertIn(SUMMARY, result.answer_text, "요약이 사라졌다")

    def test_고정_문구가_없으면_붙여서_내보낸다(self):
        result = finish(answer_text=DETAIL)
        self.assertFalse(result.answer_failed, f"고정 문구 때문에 버렸다: {result.blocking_codes}")
        self.assertIn(answer.CLOSING_LINE, result.answer_text)
        self.assertTrue(result.metrics["answer"]["closing_added"], "붙인 기록이 없다")


class TestFinishTurnFollowup(unittest.TestCase):
    """11단계. 같은 질문을 두 번 하면 대화형이라는 인상이 한순간에 깨진다."""

    def test_결과_정리_의도에서는_묻지_않는다(self):
        """``result_only``·``out_of_scope``·``smalltalk`` 은 질문을 생략한다 (README 4장)."""
        for intent in sorted(fields.NO_FOLLOWUP_INTENTS):
            with self.subTest(intent=intent):
                result = finish(intent=intent)
                self.assertIsNone(result.followup, f"{intent}: 묻지 않아야 하는데 물었다")
                self.assertFalse(result.metrics["followup"]["asked"], intent)

    def test_물을_것이_없으면_None이다(self):
        """None 은 정상 동작이다. 억지로 질문을 만들면 심문이 된다 (FR05)."""
        result = finish(unknown_items=[])
        self.assertIsNone(result.followup)
        self.assertEqual(result.metrics["followup"]["asked_field"], "")

    def test_물어본_항목과_건너뛴_항목을_다시_묻지_않는다(self):
        """``asked_state`` 전달이 실제로 먹히는지. 안 먹히면 화면에서 바로 보이는 실패다."""
        shapes = (
            ("AskedState(asked)", followup.AskedState(asked={fields.INCOME_BRACKET})),
            ("AskedState(skipped)", followup.AskedState(skipped={fields.INCOME_BRACKET})),
            ("dict asked", {"asked": [fields.INCOME_BRACKET]}),
            ("dict asked_fields", {"asked_fields": [fields.INCOME_BRACKET]}),
            ("dict skipped", {"skipped": [fields.INCOME_BRACKET]}),
            ("dict skipped_fields", {"skipped_fields": [fields.INCOME_BRACKET]}),
            ("두 집합 쌍", ({fields.INCOME_BRACKET}, set())),
            ("두 목록 쌍", ([], [fields.INCOME_BRACKET])),
            ("이름 목록 하나", [fields.INCOME_BRACKET]),
            ("이름 집합 하나", {fields.INCOME_BRACKET}),
        )
        for label, state in shapes:
            with self.subTest(shape=label):
                result = finish(asked_state=state)
                self.assertIsNone(result.followup, f"{label}: 같은 항목을 다시 물었다")

    def test_이름_목록_하나는_물어본_것으로_해석한다(self):
        """건너뛴 것으로 오해하면 사용자가 답한 항목을 다시 묻는다. 반대 실수가 덜 나쁘다.

        ``AskedState`` 는 물어본 것과 건너뛴 것을 똑같이 "다시 묻지 않는다"로 처리하므로,
        여기서 볼 수 있는 것은 **재질문하지 않는다**는 안전한 방향이다.
        """
        result = finish(asked_state=[fields.INCOME_BRACKET])
        self.assertIsNone(result.followup, "이름 목록 하나를 무시했다")

    def test_다른_항목만_처리했으면_계속_묻는다(self):
        """이름 목록을 받았다고 모든 질문을 막아 버리면 후속 질문이 통째로 사라진다."""
        result = finish(asked_state=[fields.DISTRICT])
        self.assertIsNotNone(result.followup, "관계없는 항목 때문에 질문이 막혔다")
        self.assertEqual(result.followup["field"], fields.INCOME_BRACKET)

    def test_asked_state가_이상해도_예외가_없다(self):
        for label, state in (
            ("None", None),
            ("문자열", fields.INCOME_BRACKET),
            ("숫자", 5),
            ("빈 dict", {}),
            ("세 원소 목록", ["a", "b", "c"]),
            ("아무 객체", object()),
        ):
            with self.subTest(case=label):
                result = finish(asked_state=state)
                self.assertIsInstance(result, pipeline.FinishedTurn, label)

    def test_문자열_하나는_그_항목을_처리한_것으로_본다(self):
        """세션이 이름 하나만 넘기는 모양도 받는다 (11장이 모양을 정하지 않았다)."""
        result = finish(asked_state=fields.INCOME_BRACKET)
        self.assertIsNone(result.followup, "이름 하나를 무시했다")


class TestFinishTurnChips(unittest.TestCase):
    """12단계 (P1). 칩이 없는 것은 정상이고, 키가 없는 것은 계약 위반이다."""

    def test_칩이_없으면_빈_목록을_담는다(self):
        """프론트가 ``chips`` 키를 읽는다 (5장). 키를 빼면 화면에서 터진다."""
        result = finish(policies=[])
        self.assertEqual(result.related, {"chips": []}, result.related)

    def test_이미_보여준_칩은_다시_내보내지_않는다(self):
        policies = [policy(), policy(policy_id="B", title="희망두배청년통장")]
        before = finish(policies=policies).related["chips"]
        self.assertTrue(before, "전제 확인: 칩이 나오는 상황이다")
        shown = [chip["id"] for chip in before]
        after = finish(policies=policies, shown_chips=shown).related["chips"]
        self.assertEqual(after, [], f"이미 본 칩을 다시 내보냈다: {after}")

    def test_비교_기능을_끄면_비교_칩이_빠진다(self):
        """비교 없이 비교 칩을 띄우면 누른 사용자가 답을 못 받는다."""
        policies = [policy(), policy(policy_id="B", title="희망두배청년통장")]
        allowed = [chip["id"] for chip in finish(policies=policies).related["chips"]]
        blocked = [
            chip["id"]
            for chip in finish(policies=policies, allow_compare=False).related["chips"]
        ]
        self.assertIn("compare_top2", allowed, "전제 확인: 비교 칩이 나오는 상황이다")
        self.assertNotIn("compare_top2", blocked)

    def test_칩_번호를_지표에_남긴다(self):
        policies = [policy(), policy(policy_id="B", title="희망두배청년통장")]
        result = finish(policies=policies)
        self.assertEqual(
            result.metrics["related"]["chip_ids"],
            [chip["id"] for chip in result.related["chips"]],
            result.metrics["related"],
        )


class TestFinishTurnMetrics(unittest.TestCase):
    """완료 기준 네 줄(README 11장)을 숫자로 확인할 수 있는가 (13장)."""

    def test_완료_기준_네_줄을_확인할_값이_있다(self):
        result = finish()
        answer_metrics = result.metrics["answer"]
        self.assertEqual(answer_metrics["banned_hits"], 0, "금지 표현 0건을 셀 수 없다")
        self.assertEqual(
            answer_metrics["evidence_sentences"],
            answer_metrics["cited_sentences"],
            "각주 없는 판정 문장 0건을 셀 수 없다",
        )
        self.assertLessEqual(
            answer_metrics["final_length"], answer.MAX_ANSWER_LEN, "길이를 셀 수 없다"
        )
        self.assertIn("closing_added", answer_metrics, "고정 문구 여부를 셀 수 없다")
        self.assertIn(answer.CLOSING_LINE, result.answer_text)

    def test_원본_길이와_정리_후_길이를_함께_남긴다(self):
        result = finish()
        self.assertEqual(result.metrics["answer"]["original_length"], len(GOOD_ANSWER))
        self.assertEqual(result.metrics["answer"]["final_length"], len(result.answer_text))

    def test_의도를_허용_값으로_맞춰_기록한다(self):
        """모르는 의도는 기본 의도로 떨어진다. 기준은 ``interpret.DEFAULT_INTENT`` 다."""
        for label, intent in (("모르는 문자열", "그냥질문"), ("숫자", 7), ("None", None)):
            with self.subTest(case=label):
                result = finish(intent=intent)
                self.assertEqual(result.metrics["intent"], interpret.DEFAULT_INTENT, label)

    def test_버리게_만든_코드를_읽을_수_있다(self):
        result = finish(answer_text=CLOSING)
        self.assertEqual(result.blocking_codes, (pipeline.EMPTY_AFTER_SANITIZE,))
        self.assertEqual(
            result.metrics["answer"]["blocking_codes"], list(result.blocking_codes)
        )

    def test_미확인_항목_요약이_지표에_들어간다(self):
        result = finish(unknown_items=[unknown(), "이상한 값"])
        self.assertEqual(result.metrics["unknown_items"]["kept"], 1)
        self.assertEqual(result.metrics["unknown_items"]["dropped"], 1)


class TestFinishTurnTolerance(unittest.TestCase):
    """정리 실패가 카드를 지우지 않아야 한다 (9장 10단계)."""

    def test_깨진_입력에도_예외를_던지지_않는다(self):
        cases = (
            ("답변이 None", {"answer_text": None}),
            ("답변이 숫자", {"answer_text": 5}),
            ("답변이 목록", {"answer_text": [GOOD_ANSWER]}),
            ("각주가 문자열", {"footnotes": "각주 없음"}),
            ("정책 목록이 문자열", {"policies": "정책 목록"}),
            ("정책 목록이 None", {"policies": None}),
            ("정책 목록에 None", {"policies": [None, policy()]}),
            ("미확인 항목이 문자열", {"unknown_items": "income_bracket"}),
            ("미확인 항목이 숫자", {"unknown_items": 3}),
            ("의도가 숫자", {"intent": 7}),
            ("보여준 칩이 None", {"shown_chips": None}),
        )
        for label, overrides in cases:
            with self.subTest(case=label):
                result = finish(**overrides)
                self.assertIsInstance(result, pipeline.FinishedTurn, label)
                self.assertIsInstance(result.related, dict, label)
                self.assertIsInstance(result.metrics, dict, label)

    def test_답변이_없으면_실패로_두고_카드는_남긴다(self):
        """서버는 ``answer_failed`` 오류를 보내고 카드는 유지한다 (10장)."""
        for label, text in (("None", None), ("빈 문자열", ""), ("숫자", 5)):
            with self.subTest(case=label):
                result = finish(answer_text=text)
                self.assertTrue(result.answer_failed, label)
                self.assertEqual(result.answer_text, "", label)
                self.assertTrue(
                    result.blocking_codes or result.metrics.get("error"),
                    f"{label}: 실패인데 이유가 없다",
                )

    def test_검증_통과와_답변_성공은_다른_값이다(self):
        """``check.ok`` 만 보고 내보내면 주의 문구 한 줄이 답변으로 나간다.

        ``validate`` 는 정리된 본문만 보므로 고정 문구 한 줄은 "문제 없음"이다. 실패 판단은
        ``answer_failed`` 에만 있다. 서버가 어느 값을 읽어야 하는지 여기서 못 박는다.
        """
        result = finish(answer_text="")
        self.assertTrue(result.check.ok, "전제 확인: 고정 문구만 남으면 검증은 통과한다")
        self.assertTrue(result.answer_failed, "검증을 통과했다고 성공으로 봤다")
        self.assertEqual(result.answer_text, "")

    def test_모든_인자가_이상해도_결과_모양은_유지된다(self):
        result = pipeline.finish_turn(
            None, "각주", 5, object(), asked_state=object(), intent=object(), shown_chips=7
        )
        self.assertTrue(result.answer_failed)
        self.assertEqual(result.answer_text, "")
        self.assertIn("chips", result.related, f"칩 키가 사라졌다: {result.related}")
        self.assertIsInstance(result.unknown_items, pipeline.UnknownItems)


class TestFinishTurnAnswerTypeDoesNotSinkTheTurn(unittest.TestCase):
    """답변 한 자리의 타입 실수가 11~12단계를 끌어내리지 않아야 한다.

    모델 어댑터가 dict 를 그대로 넘기거나 파싱 결과에서 문자열을 잘못 꺼내면
    ``answer_text`` 에 문자열이 아닌 값이 온다. 답변이 실패하는 것은 받아들일 수 있다
    (카드는 남고 ``answer_failed`` 문구가 있다). 받아들일 수 없는 것은 그 실패가 후속
    질문·칩·지표까지 같이 삼키는 것이다. 그 경로는 예외 없이 조용하다.
    """

    NON_TEXT = (
        ("숫자", 5),
        ("목록", [GOOD_ANSWER]),
        ("dict", {"text": GOOD_ANSWER}),
        ("객체", object()),
        ("참", True),
    )

    def test_답변이_문자열이_아니어도_후속_질문은_나온다(self):
        for label, bad in self.NON_TEXT:
            with self.subTest(case=label):
                result = finish(answer_text=bad)
                self.assertTrue(result.answer_failed, label)
                self.assertIsNotNone(
                    result.followup, f"{label}: 물을 항목이 있는데 후속 질문이 사라졌다"
                )
                self.assertEqual(
                    result.followup.get("field"), fields.INCOME_BRACKET, label
                )

    def test_답변이_문자열이_아니어도_칩과_지표는_남는다(self):
        for label, bad in self.NON_TEXT:
            with self.subTest(case=label):
                result = finish(answer_text=bad)
                self.assertIn("chips", result.related, label)
                self.assertNotIn(
                    "error", result.metrics, f"{label}: 바깥 except 로 떨어졌다"
                )
                self.assertEqual(result.metrics.get("unknown_items", {}).get("kept"), 1, label)

    def test_dict_답변이_화면_문구로_새지_않는다(self):
        """``str()`` 로 억지로 바꾸면 ``{'text': ...}`` 가 답변처럼 나간다."""
        result = finish(answer_text={"text": GOOD_ANSWER})
        self.assertEqual(result.answer_text, "")
        self.assertNotIn("{", result.answer_text)


class TestEmptyPolicyIdDoesNotInflateCount(unittest.TestCase):
    """빈 ``policy_id`` 가 영향 정책 수를 부풀리지 않아야 한다.

    그 숫자는 질문 문구에 "N개 제도"로 그대로 나가고(``questions.build``), 후보 정렬
    1순위이기도 하다(``followup.Candidate.sort_key``). 부풀린 값은 사용자에게 틀린 개수를
    보여 주면서 엉뚱한 항목을 먼저 묻게 만든다.
    """

    def test_빈_id_하나가_섞여도_아는_정책만_센다(self):
        items = pipeline.unknown_items_from(
            [unknown(policy_id="SEOUL-001"), unknown(policy_id="")]
        )
        candidates = followup.collect_candidates(items.items)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(
            candidates[0].policy_count,
            1,
            "빈 policy_id 가 별개 정책으로 세어졌다",
        )

    def test_전부_빈_id_면_최소_한_개로_본다(self):
        items = pipeline.unknown_items_from([unknown(policy_id=""), unknown(policy_id="")])
        candidates = followup.collect_candidates(items.items)
        self.assertEqual(candidates[0].policy_count, 1, "0개로 세면 질문 문구가 깨진다")

    def test_빈_id_는_기록으로_남는다(self):
        items = pipeline.unknown_items_from([unknown(policy_id="")])
        self.assertTrue(
            any(note.startswith(pipeline.NOTE_NO_POLICY_ID) for note in items.notes),
            f"빈 policy_id 가 조용히 넘어갔다: {items.notes}",
        )

    def test_부풀린_개수가_질문_순서를_바꾸지_않는다(self):
        """빈 id 항목이 많은 쪽이 실제 영향이 큰 항목을 앞지르면 안 된다."""
        items = pipeline.unknown_items_from(
            [
                unknown(field=fields.HOUSING_TYPE, policy_id="", policy_rank=5),
                unknown(field=fields.HOUSING_TYPE, policy_id="", policy_rank=6),
                unknown(field=fields.INCOME_BRACKET, policy_id="SEOUL-001", policy_rank=0),
                unknown(field=fields.INCOME_BRACKET, policy_id="SEOUL-002", policy_rank=1),
            ]
        )
        candidates = followup.collect_candidates(items.items)
        self.assertEqual(
            candidates[0].field,
            fields.INCOME_BRACKET,
            f"빈 id 가 우선순위를 얻었다: {[(c.field, c.policy_count) for c in candidates]}",
        )


class TestModuleDocstringMatchesCode(unittest.TestCase):
    """"아직 없는 연결" 목록이 사실과 어긋나면 안 된다.

    이 목록은 다른 담당자가 읽고 작업을 나누는 근거다. 이미 된 일이 "아직 없다"로 남아
    있으면 같은 일을 두 번 하거나, 반대로 안 된 일을 됐다고 믿는다. 문서가 코드보다
    기준이라는 규칙은 문서가 코드와 맞을 때만 뜻이 있다.
    """

    def test_패키지_자동_로딩은_이미_되어_있다(self):
        from ai import conversation

        self.assertIn("pipeline", conversation.loaded_modules())
        self.assertIsNotNone(conversation.pipeline)
        self.assertNotIn(
            "_MODULE_NAMES",
            pipeline.__doc__ or "",
            "이미 등록된 모듈을 독스트링이 아직 없다고 말한다",
        )

    def test_아직_없는_것은_프롬프트_문안과_어댑터다(self):
        text = pipeline.__doc__ or ""
        self.assertIn("프롬프트 문안", text)
        self.assertIn("모델 어댑터", text)
        self.assertTrue(
            any(slot.id and pipeline.PROMPT_TODO for slot in pipeline.ANSWER_RULE_SLOTS),
            "문안 자리가 사라졌는데 독스트링은 그대로다",
        )


if __name__ == "__main__":
    unittest.main()
