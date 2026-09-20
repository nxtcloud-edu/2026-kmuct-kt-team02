"""AI A dict ↔ 서버 모델 변환 테스트 (ai/conversation/handoff.py).

근거 문서: ``docs/10-ai-a-server-handoff.md`` (2장 쌍별 대조표, 3장 답변 실패,
4장 서버에 요청하는 변경, 6장 통합 체크리스트), ``docs/03-api-contract.md`` 5·10·11장.

여기서 지키려는 계약
  - 서버 이벤트 payload 모델은 전부 ``extra="forbid"`` 이고 검증은 **200 이 나간 뒤**에
    일어난다. 키 이름이 하나만 달라도 4xx 가 아니라 **이유 없이 끊긴 스트림**이다. 그래서
    이 모듈은 "값이 맞는지"보다 **키 이름과 없어도 되는 키가 없는지**를 본다.
  - ``profile_update_event`` 는 세 키(``changed_fields``/``message``/``profile``)만 담고
    ``region`` 을 뺀다. ``ProfileField`` enum 에 ``region`` 이 없다.
  - ``related_event`` 는 칩이 없을 때 ``None`` 을 돌려준다. 빈 목록을 넘기면 서버가 매 턴
    빈 ``related`` 이벤트를 낸다(``is not None`` 으로만 판단한다). 칩 ``id`` 는 이벤트 본문이
    아니라 ``related_chip_ids`` 로 따로 나온다.
  - ``followup_event`` 는 ``planned_basis`` 질문만 걸러내고 나머지는 그대로 통과시킨다.
    키 6개가 이미 정확히 일치하는 쌍이다.
  - **``as_plain`` 이 이 모듈의 존재 이유다.** pydantic v2 모델 객체를 dict 로 바꾸지 않으면
    ``answer.known_footnote_ids`` 가 빈 집합을 돌려주고 답변이 통째로, 예외도 로그도 없이
    사라진다. 그 대비를 테스트로 고정한다.
  - ``delta_lines`` 는 공백 조각을 버린다. 공백 조각 하나가 스트림 중간 ValidationError 다.
  - 모든 공개 함수가 **어떤 입력에도 예외를 던지지 않는다.** 여기서 터지면 이미 화면에 있는
    규칙 기반 카드까지 500 에 묻힌다.

**fastapi 도 pydantic 도 import 하지 않는다.** 지금 이 환경에 fastapi 가 없고, 있더라도
AI 파트 테스트가 서버 설치를 요구하면 모듈 경계가 사라진다(``docs/10`` 5장). 그래서 서버
모델 자리에는 ``model_dump`` 를 가진 **가짜 객체**를 쓴다.
"""

import unittest

import ai.conversation as package
from ai.conversation import answer, fields, handoff, interpret, questions, related


class FakeModel:
    """pydantic v2 ``BaseModel`` 을 닮은 가짜 객체.

    닮아야 하는 점은 두 가지다.
      - ``model_dump(mode="json")`` 으로 dict 를 낸다
      - ``__iter__`` 를 가진다. **이것이 조용한 실패의 원인이다.** ``BaseModel`` 은
        ``(키, 값)`` 쌍을 내는 ``__iter__`` 가 있어서 ``answer.known_footnote_ids`` 의
        Iterable 갈래로 잘못 들어간다. ``Mapping`` 은 아니므로 키를 읽지 못하고 빈 집합이
        된다. 실제 모델을 쓰지 않고도 이 경로를 그대로 재현할 수 있다.
    """

    def __init__(self, data):
        self._data = dict(data)
        self.dump_modes = []

    def model_dump(self, mode="python"):
        self.dump_modes.append(mode)
        return dict(self._data)

    def __iter__(self):
        return iter(self._data.items())


class FakeModelWithoutMode:
    """``mode`` 를 모르는 모델(pydantic v1 계열). ``model_dump()`` 로 물러나야 한다."""

    def __init__(self, data):
        self._data = dict(data)

    def model_dump(self):
        return dict(self._data)


def footnote_models():
    """서버 ``FootnotesEventData`` 를 닮은 가짜 객체 (docs/03-api-contract.md 5-1)."""
    return FakeModel(
        {
            "footnotes": [
                FakeModel(
                    {
                        "footnote_id": 1,
                        "policy_id": "SEOUL-001",
                        "excerpt": "월 20만원을 지원합니다",
                    }
                ),
                FakeModel(
                    {
                        "footnote_id": 2,
                        "policy_id": "SEOUL-002",
                        "excerpt": "휴학생은 제외합니다",
                    }
                ),
            ]
        }
    )


def policy_models():
    """서버 ``PolicyEvaluation`` 목록을 닮은 가짜 객체 (docs/03-api-contract.md 4장)."""
    return [
        FakeModel(
            {
                "policy_id": "SEOUL-001",
                "title": "청년월세지원",
                "status": "check",
                "conditions": [
                    FakeModel({"result": "unknown", "needed_field": fields.INCOME_BRACKET})
                ],
                "deadline": FakeModel({"is_imminent": True, "badge": "D-3", "d_day": 3}),
            }
        )
    ]


def merged(*changes):
    """``interpret.merge`` 를 한 번 돌려 실제 ``MergeResult`` 를 얻는다.

    dict 를 손으로 적지 않는 이유는, 키 이름이 ``interpret`` 쪽에서 바뀌면 이 테스트가
    같이 깨져야 하기 때문이다. 손으로 적으면 변환이 낡은 모양만 계속 통과시킨다.
    """
    profile = {
        "age": 24,
        "region": "seoul",
        "district": "관악구",
        "status": "enrolled",
        "categories": ["housing"],
    }
    interpretation = interpret.Interpretation(
        profile_changes=tuple(
            interpret.ProfileChange(field=field, value=value) for field, value in changes
        )
    )
    return interpret.merge(profile, interpretation)


def chips_event(*pairs):
    """``related.to_event`` 와 같은 모양을 만든다. 상황 판단 없이 칩을 지정할 때 쓴다."""
    return {"chips": [{"id": chip_id, "text": text} for chip_id, text in pairs]}


class TestPackageWiring(unittest.TestCase):
    """패키지가 이 모듈을 실제로 불러오는가. 안 불러오면 서버가 ``None`` 을 부른다."""

    def test_handoff가_패키지에서_보인다(self):
        self.assertIn(
            "handoff",
            package.loaded_modules(),
            f"불러오지 못했다: {package.MISSING_MODULES}",
        )
        self.assertIsNotNone(package.handoff, "이름이 None 이다")


class TestProfileUpdateEvent(unittest.TestCase):
    """``MergeResult`` → ``ProfileUpdateEventData`` (docs/10 2-1).

    이름이 안 맞는 쌍이다. ``changes``→``changed_fields``, ``notice``→``message`` 두 건이
    어긋나 있고, 접는 순간 세 값이 사라진다.
    """

    def test_세_키로_접는다(self):
        """서버 모델은 ``extra="forbid"`` 다. 키가 하나 남거나 이름이 다르면 스트림이 끊긴다."""
        payload = handoff.profile_update_event(merged((fields.STATUS, "on_leave")))
        self.assertIsNotNone(payload, "변경이 있는데 이벤트를 안 만들었다")
        self.assertEqual(
            sorted(payload),
            ["changed_fields", "message", "profile"],
            f"키가 서버 모델과 다르다: {sorted(payload)}",
        )

    def test_changes를_항목_이름과_after_값의_dict로_접는다(self):
        payload = handoff.profile_update_event(merged((fields.STATUS, "on_leave")))
        self.assertEqual(
            payload["changed_fields"],
            {fields.STATUS: "on_leave"},
            f"바뀐 값이 after 가 아니다: {payload['changed_fields']}",
        )

    def test_notice가_message로_간다(self):
        """문구는 표에서 온 고정 값이다. 변환이 문구를 다시 쓰지 않는다."""
        result = merged((fields.STATUS, "on_leave"))
        payload = handoff.profile_update_event(result)
        self.assertEqual(
            payload["message"],
            result.to_profile_update()["notice"],
            f"문구가 달라졌다: {payload['message']}",
        )

    def test_profile은_이름_그대로다(self):
        result = merged((fields.STATUS, "on_leave"))
        payload = handoff.profile_update_event(result)
        self.assertEqual(payload["profile"], result.profile)

    def test_region은_changed_fields에서_빠지고_기록에_남는다(self):
        """``ProfileField`` enum 에 ``region`` 이 없다. 넣으면 스트림 중간 ValidationError 다."""
        payload = handoff.profile_update_event(
            merged((fields.REGION, "outside_seoul"), (fields.STATUS, "on_leave"))
        )
        self.assertNotIn(
            fields.REGION,
            payload["changed_fields"],
            f"region 이 실렸다: {payload['changed_fields']}",
        )
        self.assertIn(fields.STATUS, payload["changed_fields"], "다른 항목까지 사라졌다")
        self.assertEqual(
            [(entry.field, entry.reason) for entry in payload.notes.dropped],
            [(fields.REGION, handoff.DROP_REGION_NOT_IN_ENUM)],
            f"버린 사실이 기록에 없다: {payload.notes.summary()}",
        )

    def test_region만_바뀐_턴은_None이다(self):
        """서버 프로필 관점에서 바뀐 것이 없다. 빈 changed_fields 이벤트를 보내지 않는다."""
        self.assertIsNone(
            handoff.profile_update_event(merged((fields.REGION, "outside_seoul")))
        )

    def test_바뀐_것이_없으면_None이다(self):
        """서버는 ``None`` 일 때 이벤트를 보내지 않는다 (``is not None`` 으로만 판단한다)."""
        for label, value in (
            ("변경 없는 MergeResult", merged()),
            ("to_profile_update 결과 None", None),
            ("빈 changes", {"changes": [], "notice": "", "profile": {}}),
        ):
            with self.subTest(case=label):
                self.assertIsNone(handoff.profile_update_event(value), label)

    def test_before_label_항목별_notice는_버려지고_건수가_남는다(self):
        """서버 모델에 자리가 없다. 버리는 것 자체가 결정이라 건수를 세어 둔다 (docs/10 2-1)."""
        payload = handoff.profile_update_event(
            merged((fields.STATUS, "on_leave"), (fields.HOUSING_TYPE, "jeonse"))
        )
        counts = payload.notes.summary()["note_reasons"]
        for code in (
            handoff.NOTE_BEFORE_NO_SERVER_FIELD,
            handoff.NOTE_LABEL_NO_SERVER_FIELD,
            handoff.NOTE_ITEM_NOTICE_NO_SERVER_FIELD,
        ):
            with self.subTest(code=code):
                self.assertEqual(counts.get(code), 2, f"{code} 건수가 다르다: {counts}")

    def test_이미_to_profile_update한_dict도_받는다(self):
        """서버가 어느 쪽을 넘길지 정하지 않아도 되게 둘 다 받는다."""
        result = merged((fields.STATUS, "on_leave"))
        from_object = handoff.profile_update_event(result)
        from_dict = handoff.profile_update_event(result.to_profile_update())
        self.assertEqual(dict(from_object), dict(from_dict))

    def test_읽을_수_없는_change는_버리고_기록한다(self):
        payload = handoff.profile_update_event(
            {
                "changes": ["문자열", {"after": "값만 있다"}, {"field": fields.STATUS, "after": "on_leave"}],
                "notice": "",
                "profile": {},
            }
        )
        self.assertEqual(payload["changed_fields"], {fields.STATUS: "on_leave"})
        self.assertEqual(
            payload.notes.summary()["dropped_reasons"],
            {handoff.DROP_CHANGE_NOT_A_MAPPING: 1, handoff.DROP_CHANGE_NO_FIELD: 1},
            f"이유 코드가 다르다: {payload.notes.summary()}",
        )

    def test_기록은_payload_키가_아니라_속성이다(self):
        """기록을 키로 넣으면 ``extra="forbid"`` 에 걸려 이 파일이 막으려는 실패를 만든다."""
        payload = handoff.profile_update_event(merged((fields.REGION, "outside_seoul"), (fields.STATUS, "on_leave")))
        self.assertNotIn("notes", payload, f"기록이 payload 키로 들어갔다: {sorted(payload)}")
        self.assertIsInstance(payload.notes, handoff.HandoffNotes)


class TestRelatedEvent(unittest.TestCase):
    """``related.build()`` → ``RelatedEventData`` (docs/10 2-3, 4장 2번)."""

    def test_칩_문구만_questions로_간다(self):
        payload = handoff.related_event(chips_event(("a", "첫 칩"), ("b", "둘째 칩")))
        self.assertEqual(sorted(payload), ["questions"], f"키가 다르다: {sorted(payload)}")
        self.assertEqual(payload["questions"], ["첫 칩", "둘째 칩"])

    def test_칩이_없으면_None이다(self):
        """빈 목록을 넘기면 서버가 매 턴 빈 ``related`` 이벤트를 낸다. 프론트는 칩 영역을
        그렸다 비운다."""
        for label, value in (
            ("빈 chips", {"chips": []}),
            ("related.build 결과", related.build([])),
            ("빈 목록", []),
            ("None", None),
        ):
            with self.subTest(case=label):
                self.assertIsNone(handoff.related_event(value), label)

    def test_id는_related_chip_ids로_따로_나온다(self):
        """이벤트 본문에는 ``id`` 자리가 없는데 ``related.select(shown=...)`` 는 번호로
        중복을 거른다. 번호가 사라지면 그 기능이 영구히 죽는다 (docs/10 2-3)."""
        body = chips_event(("deadline_order", "마감 순서"), ("compare_top2", "비교"))
        payload = handoff.related_event(body)
        self.assertEqual(
            handoff.related_chip_ids(body),
            ("deadline_order", "compare_top2"),
            "세션에 저장할 번호가 나오지 않았다",
        )
        self.assertNotIn("chips", payload)
        self.assertNotIn("id", str(payload["questions"]), "번호가 문구에 섞였다")

    def test_id가_와이어에서_사라진다는_사실이_기록된다(self):
        """이 기록이 비어 있지 않은 동안에는 서버가 아직 id 를 보존하지 않는다는 뜻이다."""
        payload = handoff.related_event(chips_event(("deadline_order", "마감 순서")))
        self.assertIn(
            f"{handoff.NOTE_CHIP_ID_NOT_ON_WIRE}:deadline_order",
            payload.notes.notes,
            f"기록이 없다: {payload.notes.summary()}",
        )

    def test_상한은_related_모듈에서_끌어온다(self):
        """숫자를 변환 계층에 다시 적지 않는다. 서버 상한과 같은 값이어야 한다."""
        self.assertEqual(handoff.MAX_RELATED_QUESTIONS, related.MAX_CHIPS)

    def test_상한을_넘는_칩은_버리고_기록한다(self):
        payload = handoff.related_event(
            chips_event(*[(f"c{index}", f"칩 {index}") for index in range(5)])
        )
        self.assertEqual(len(payload["questions"]), related.MAX_CHIPS)
        self.assertEqual(
            payload.notes.summary()["dropped_reasons"],
            {handoff.DROP_CHIP_OVER_LIMIT: 5 - related.MAX_CHIPS},
            payload.notes.summary(),
        )
        self.assertEqual(
            len(handoff.related_chip_ids(chips_event(*[(f"c{i}", f"칩 {i}") for i in range(5)]))),
            related.MAX_CHIPS,
            "세션에 저장할 번호가 이벤트에 실린 칩보다 많다",
        )

    def test_RelatedChip_객체를_그대로_넘겨도_읽는다(self):
        """``to_event()`` 를 빼먹는 호출이 실제로 나온다. 칩은 P1 이고 버릴 이유가 없다."""
        payload = handoff.related_event(related.fixed())
        self.assertIsNotNone(payload, "칩 객체를 못 읽었다")
        self.assertEqual(
            payload["questions"],
            [related.CHIPS[chip_id].text for chip_id in related.FIXED_CHIP_IDS],
        )

    def test_문구가_빈_칩은_버린다(self):
        """``delta`` 와 같은 이유다. 빈 문구는 서버 모델을 통과해도 화면에 빈 칩을 만든다."""
        payload = handoff.related_event(chips_event(("a", ""), ("b", "살아남는 칩")))
        self.assertEqual(payload["questions"], ["살아남는 칩"])
        self.assertEqual(
            [entry.reason for entry in payload.notes.dropped],
            [handoff.DROP_CHIP_NO_TEXT],
        )

    def test_번호가_빈_칩은_shown에_들어가지_않는다(self):
        """빈 문자열을 ``shown`` 에 넣으면 세션이 "번호 없는 칩을 보여줬다"고 기억한다."""
        ids = handoff.related_chip_ids(chips_event(("", "번호 없는 칩"), ("b", "정상 칩")))
        self.assertEqual(ids, ("b",), f"빈 번호가 섞였다: {ids}")


class TestFollowupEvent(unittest.TestCase):
    """``followup.next_question()`` → ``FollowupQuestion`` (docs/10 2-2, 4장 3번)."""

    def test_키를_그대로_통과시킨다(self):
        """이 쌍은 키 6개가 이미 정확히 일치한다. 변환하는 척하지 않는다."""
        question = questions.build(fields.INCOME_BRACKET)
        payload = handoff.followup_event(question)
        self.assertEqual(dict(payload), dict(question), "통과시켜야 할 값이 바뀌었다")
        self.assertEqual(
            sorted(payload),
            ["allow_free_text", "allow_skip", "field", "options", "question", "reason"],
            f"서버 모델 키 6개와 다르다: {sorted(payload)}",
        )

    def test_planned_basis_질문은_걸러낸다(self):
        """``AskableProfileField`` enum 에 ``planned_basis`` 가 없다. 보내면 스트림이 끊긴다."""
        question = questions.build_planned_question(fields.STATUS, "휴학")
        self.assertEqual(
            question["field"],
            questions.PLANNED_BASIS_FIELD,
            "전제 확인: planned 확인 질문의 field 는 planned_basis 다",
        )
        self.assertIsNone(handoff.followup_event(question), "그대로 내보냈다")

    def test_걸러낸_사실이_이름으로_기록된다(self):
        """어느 경로가 막혔는지 지표에서 보여야 한다. 12:00 결정 안건(4장 3번)이다."""
        handled = handoff.followup_event(questions.build_planned_question(fields.STATUS, "휴학"))
        self.assertIsNone(handled)
        # ``None`` 경로는 기록을 실어 보낼 자리가 없으므로(모듈 독스트링), 코드 값이 이름을
        # 담고 있는지만 고정한다. 이름이 바뀌면 지표 집계가 조용히 갈린다.
        self.assertIn(questions.PLANNED_BASIS_FIELD, handoff.DROP_PLANNED_BASIS)

    def test_field를_다른_항목으로_바꾸지_않는다(self):
        """임시로 ``status`` 같은 이름을 붙여 보내면 그 답이 프로필에 박힌다 (questions.py)."""
        question = questions.build_planned_question(fields.STATUS, "휴학")
        self.assertIsNone(handoff.followup_event(question))
        self.assertEqual(
            question["field"],
            questions.PLANNED_BASIS_FIELD,
            "입력 dict 를 고쳤다. 변환은 남의 값을 바꾸지 않는다",
        )

    def test_물을_것이_없으면_None이다(self):
        """``next_question`` 이 ``None`` 을 내는 것은 정상 동작이다 (FR05)."""
        for label, value in (("None", None), ("문자열", "income_bracket"), ("목록", [])):
            with self.subTest(case=label):
                self.assertIsNone(handoff.followup_event(value), label)


class TestAsPlain(unittest.TestCase):
    """모델 객체 → dict (docs/10 2-4·2-5, 4장 5번)."""

    def test_모델_객체를_dict로_바꾼다(self):
        plain = handoff.as_plain(FakeModel({"footnote_id": 1, "excerpt": "월 20만원"}))
        self.assertEqual(plain, {"footnote_id": 1, "excerpt": "월 20만원"})

    def test_json_모드로_부른다(self):
        """``checked_at`` 은 ``date``, ``source_url`` 은 ``HttpUrl`` 이다. 직렬화는 모델에 맡긴다."""
        model = FakeModel({"footnote_id": 1})
        handoff.as_plain(model)
        self.assertEqual(model.dump_modes, ["json"], f"호출 모드가 다르다: {model.dump_modes}")

    def test_mode를_모르는_모델도_받는다(self):
        plain = handoff.as_plain(FakeModelWithoutMode({"footnote_id": 3}))
        self.assertEqual(plain, {"footnote_id": 3})

    def test_중첩과_목록을_재귀로_처리한다(self):
        plain = handoff.as_plain(
            FakeModel(
                {
                    "footnotes": [FakeModel({"footnote_id": 1})],
                    "deadline": FakeModel({"is_imminent": True}),
                }
            )
        )
        self.assertEqual(
            plain,
            {"footnotes": [{"footnote_id": 1}], "deadline": {"is_imminent": True}},
        )

    def test_이미_dict인_값은_그대로다(self):
        data = {"footnotes": [{"footnote_id": 1}]}
        self.assertEqual(handoff.as_plain(data), data)

    def test_튜플과_집합은_목록이_된다(self):
        self.assertEqual(handoff.as_plain((1, 2)), [1, 2])
        self.assertEqual(sorted(handoff.as_plain({1, 2})), [1, 2])

    def test_모델_밖의_값은_손대지_않는다(self):
        """``date``/``UUID`` 직렬화 규칙을 여기서 또 만들지 않는다 (모델이 한다)."""
        class Weird:
            pass

        weird = Weird()
        for label, value in (
            ("문자열", "seoul"),
            ("정수", 7),
            ("불리언", True),
            ("None", None),
            ("모르는 객체", weird),
        ):
            with self.subTest(case=label):
                self.assertIs(handoff.as_plain(value), value, label)

    def test_자기_자신을_담은_값에도_터지지_않는다(self):
        loop = []
        loop.append(loop)
        self.assertIsInstance(handoff.as_plain(loop), list)


class TestFootnoteRegression(unittest.TestCase):
    """**이 모듈이 존재하는 이유.** (docs/10 2-4, 6장 체크리스트 2번)

    각주를 모델 객체로 넘기면 ``known_footnote_ids`` 가 빈 집합을 돌려주고, 각주가 붙은
    문장이 전부 ``unknown_footnote`` 로 잡혀 정리 단계에서 삭제되고, 남은 고정 문구 한 줄을
    ``finish_turn`` 이 ``EMPTY_AFTER_SANITIZE`` 로 잡아 답변을 통째로 버린다.
    **예외도 로그도 없다.** 그래서 테스트로만 보인다.
    """

    def test_as_plain을_거치면_각주_번호가_나온다(self):
        ids = answer.known_footnote_ids(handoff.as_plain(footnote_models()))
        self.assertEqual(set(ids), {1, 2}, f"번호를 읽지 못했다: {ids}")

    def test_거치지_않으면_빈_집합이다(self):
        """가짜 객체가 ``__iter__`` 를 가진 것이 요점이다. pydantic v2 모델이 그렇다."""
        ids = answer.known_footnote_ids(footnote_models())
        self.assertEqual(
            ids,
            frozenset(),
            f"전제가 깨졌다. 이 대비가 사라지면 이 모듈의 존재 이유가 사라진다: {ids}",
        )

    def test_모델_객체_목록도_빈_집합이다(self):
        """``FootnotesEventData`` 대신 ``Footnote`` 목록을 넘기는 경로도 같이 죽는다."""
        models = footnote_models().model_dump(mode="json")["footnotes"]
        self.assertEqual(answer.known_footnote_ids(models), frozenset())
        self.assertEqual(set(answer.known_footnote_ids(handoff.as_plain(models))), {1, 2})

    def test_정책을_모델로_넘기면_마감_칩이_조용히_사라진다(self):
        """``related._as_policies`` 도 ``Mapping`` 검사를 한다. 1순위 칩이 예외 없이 사라진다."""
        models = policy_models()
        self.assertEqual(
            related.build(models),
            {"chips": []},
            "전제가 깨졌다: 모델 객체가 그냥 통과했다",
        )
        chips = related.build(handoff.as_plain(models))
        self.assertIn(
            related.CHIP_DEADLINE_ORDER,
            [chip["id"] for chip in chips["chips"]],
            f"as_plain 을 거쳐도 마감 칩이 없다: {chips}",
        )


class TestAnswerFailurePayload(unittest.TestCase):
    """답변 실패 표현 (docs/10 3장, docs/03-api-contract.md 10장)."""

    def test_코드와_델타_문구를_담는다(self):
        payload = handoff.answer_failure_payload()
        self.assertEqual(sorted(payload), ["code", "delta"], payload)
        self.assertEqual(payload["code"], handoff.ANSWER_FAILED_CODE)
        self.assertEqual(payload["delta"], handoff.ANSWER_FAILED_MESSAGE)

    def test_문구는_델타로_쓸_수_있는_모양이다(self):
        """``AnswerDeltaEventData.delta`` 는 ``min_length=1`` + 공백 제거다."""
        delta = handoff.answer_failure_payload()["delta"]
        self.assertTrue(delta.strip(), "공백만 있는 델타는 스트림 중간에 터진다")
        self.assertEqual(delta, delta.strip(), "앞뒤 공백이 있으면 서버가 다듬는다")

    def test_문구가_문서의_answer_failed_문구다(self):
        """문구는 지어낸 값이 아니다 (10장 오류 표, server/errors.py 와 같은 문장)."""
        self.assertIn("설명을 불러오지 못했어요", handoff.ANSWER_FAILED_MESSAGE)
        self.assertIn("카드에서 조건을 확인해 주세요", handoff.ANSWER_FAILED_MESSAGE)
        self.assertEqual(handoff.ANSWER_FAILED_CODE, "answer_failed")


class TestDeltaLines(unittest.TestCase):
    """답변 본문 → ``answer_delta`` 조각 (docs/10 3-2, 6장 체크리스트 7번)."""

    def test_공백_조각을_버린다(self):
        """공백 조각 하나가 스트림 중간 ValidationError 를 낸다."""
        pieces = handoff.delta_lines("첫 문장이에요.   둘째 문장이에요.  \n\n  ")
        self.assertEqual(pieces, ("첫 문장이에요.", "둘째 문장이에요."))
        for piece in pieces:
            with self.subTest(piece=piece):
                self.assertTrue(piece.strip(), "공백만 있는 조각이 남았다")
                self.assertEqual(piece, piece.strip(), "앞뒤 공백이 남았다")

    def test_본문이_비면_빈_튜플이다(self):
        for label, value in (("빈 문자열", ""), ("공백만", "   \n "), ("None", None)):
            with self.subTest(case=label):
                self.assertEqual(handoff.delta_lines(value), (), label)

    def test_문장_분리는_answer_모듈에_맡긴다(self):
        """정규식을 다시 쓰면 "3.5%" 와 "2026. 3. 1." 에서 갈라진다."""
        text = "월 20만원이고 금리는 3.5%예요. 마감은 2026. 3. 1. 까지예요."
        self.assertEqual(
            handoff.delta_lines(text),
            tuple(piece.strip() for piece in answer.split_sentences(text)),
            "분리 결과가 answer.split_sentences 와 다르다",
        )

    def test_종결_부호가_없어도_본문을_잃지_않는다(self):
        self.assertEqual(handoff.delta_lines("부호 없는 한 줄"), ("부호 없는 한 줄",))

    def test_문자열이_아니면_빈_튜플이다(self):
        """모델 어댑터가 dict 를 그대로 넘기는 실수에서 답변 자리에 dict 가 온다."""
        for value in ({"text": "답변"}, 7, [1, 2], True):
            with self.subTest(value=value):
                self.assertEqual(handoff.delta_lines(value), ())


class TestHandoffNotes(unittest.TestCase):
    """기록 요약 (지표용, docs/03-api-contract.md 13장). 화면에 쓰지 않는다."""

    def test_summary가_지표용_숫자를_담는다(self):
        payload = handoff.profile_update_event(
            merged((fields.REGION, "outside_seoul"), (fields.STATUS, "on_leave"))
        )
        summary = payload.notes.summary()
        self.assertEqual(summary["dropped"], 1, summary)
        self.assertEqual(
            summary["dropped_reasons"], {handoff.DROP_REGION_NOT_IN_ENUM: 1}, summary
        )
        self.assertEqual(summary["dropped_fields"], [fields.REGION], summary)
        self.assertIn("note_reasons", summary)

    def test_빈_기록도_같은_모양이다(self):
        summary = handoff.HandoffNotes().summary()
        self.assertEqual(
            summary,
            {
                "dropped": 0,
                "dropped_reasons": {},
                "dropped_fields": [],
                "notes": [],
                "note_reasons": {},
            },
        )

    def test_dropped_fields는_중복과_빈_이름을_뺀다(self):
        notes = handoff.HandoffNotes(
            dropped=(
                handoff.DroppedValue(fields.REGION, handoff.DROP_REGION_NOT_IN_ENUM),
                handoff.DroppedValue(fields.REGION, handoff.DROP_REGION_NOT_IN_ENUM),
                handoff.DroppedValue("", handoff.DROP_CHANGE_NO_FIELD),
            )
        )
        self.assertEqual(notes.dropped_fields(), (fields.REGION,))


class TestTolerance(unittest.TestCase):
    """깨진 입력. **여기서 터지면 규칙 기반 카드까지 500 에 묻힌다** (docs/03 9장)."""

    BAD_INPUTS = (
        ("None", None),
        ("숫자", 7),
        ("문자열", "profile_update"),
        ("불리언", True),
        ("빈 dict", {}),
        ("빈 목록", []),
        ("모델 객체", FakeModel({"changes": "이상한 값"})),
        ("중첩 쓰레기", {"changes": [[{"field": None}]], "notice": [], "profile": 3}),
        ("chips 가 문자열", {"chips": "마감 순서"}),
        ("깊은 목록", [[[[{"id": 1}]]]]),
    )

    def test_모든_공개_함수가_예외를_던지지_않는다(self):
        converters = (
            ("profile_update_event", handoff.profile_update_event),
            ("related_event", handoff.related_event),
            ("followup_event", handoff.followup_event),
            ("as_plain", handoff.as_plain),
            ("delta_lines", handoff.delta_lines),
            ("related_chip_ids", handoff.related_chip_ids),
        )
        for name, function in converters:
            for label, value in self.BAD_INPUTS:
                with self.subTest(function=name, case=label):
                    function(value)  # 예외가 나면 여기서 실패한다

    def test_돌려주는_모양이_계약과_같다(self):
        for label, value in self.BAD_INPUTS:
            with self.subTest(case=label):
                for name, function in (
                    ("profile_update_event", handoff.profile_update_event),
                    ("related_event", handoff.related_event),
                    ("followup_event", handoff.followup_event),
                ):
                    result = function(value)
                    self.assertTrue(
                        result is None or isinstance(result, dict),
                        f"{name}({label}) 가 {type(result).__name__} 을 돌려줬다",
                    )
                self.assertIsInstance(handoff.delta_lines(value), tuple, label)
                self.assertIsInstance(handoff.related_chip_ids(value), tuple, label)

    def test_객체를_넘겨도_서버_모델에_넣을_수_있는_dict다(self):
        """payload 가 dict 가 아니면 ``Model(**payload)`` 자체가 TypeError 다."""
        payload = handoff.profile_update_event(merged((fields.STATUS, "on_leave")))
        self.assertIsInstance(payload, dict)
        self.assertTrue(all(isinstance(key, str) for key in payload))


if __name__ == "__main__":
    unittest.main()
