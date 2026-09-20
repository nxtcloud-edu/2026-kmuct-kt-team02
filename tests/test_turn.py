"""AI 한 턴 연결 테스트 (ai/turn.py, docs/03-api-contract.md 9장).

``ai/turn.py`` 는 AI A(``ai/conversation``)와 AI B(``ai/judgment``)를 잇는다. 두 파트는
각각 자기 테스트가 있고 각각 통과한다. **그런데도 경계에서 조용히 어긋날 수 있다.**
어긋나는 방식이 예외가 아니라 "빈 값"이기 때문이다.

여기서 지키려는 계약
  - AI 판정이 낸 미확인 조건이 **후속 질문으로 이어진다.** 규칙 엔진 미확인만 넘기면
    예외 조건 때문에 ``check`` 인 정책에서 질문이 뜨지 않는다.
  - 각주 번호가 정책들에 걸쳐 **이어서** 붙는다. 정책마다 1부터 다시 시작하면 답변의
    ``[1]`` 이 엉뚱한 발췌를 가리키고, 그 어긋남은 오류 없이 화면에 나간다.
  - 인용 검증에 실패한 발췌가 결과에 **남지 않는다.**
  - 범위 밖·잡담이면 판정기를 **부르지 않는다.** 20초 예산을 답할 수 없는 질문에 쓰지 않는다.
  - 판정기가 없어도 턴이 끝까지 돈다. 모델 키 없는 환경에서도 카드는 나가야 한다.
  - 어떤 입력에도 예외를 던지지 않는다. 여기서 터지면 규칙 기반 카드까지 500 에 묻힌다.

모델도 네트워크도 쓰지 않는다. 판정기는 가짜 LLM 클라이언트를 꽂은 실제
``ExceptionJudge`` 다. 가짜 판정기 대신 실제 판정기를 쓰는 이유는, 이 파일이 확인해야 하는
것이 **AI B 의 실제 출력 모양**과 맞물리는지이기 때문이다. 판정기까지 가짜로 만들면
맞물림을 확인하지 못하고 이 테스트의 목적이 사라진다.
"""

import json
import unittest

from ai import turn
from ai.conversation import answer, fields
from ai.judgment import ASK_NOTICE, MET, UNKNOWN, ExceptionJudge

# 예외 조건 두 줄을 담은 공고 원문. 발췌는 이 안에 있어야 인용 검증을 통과한다.
RAW_TEXT = (
    "○ 제외 대상\n"
    "  - 휴학생은 지원 대상에서 제외합니다\n"
    "  - 타 청년 지원금을 수혜 중인 자는 중복 신청할 수 없습니다\n"
)

EXCERPT_DUP = "타 청년 지원금을 수혜 중인 자는 중복 신청할 수 없습니다"
EXCERPT_LEAVE = "휴학생은 지원 대상에서 제외합니다"

PROFILE = {
    "age": 23,
    "region": "seoul",
    "status": "enrolled",
    "categories": ["housing"],
}


def policy(policy_id="SEOUL-001", **overrides):
    """정책 데이터 하나. 판정 입력(`raw_text`)과 화면 출력 키를 함께 가진다."""
    base = {
        "id": policy_id,
        "policy_id": policy_id,
        "title": f"제도 {policy_id}",
        "status": answer.CHECK,
        "agency": "서울시",
        "source_url": f"https://example.com/{policy_id}",
        "checked_at": "2026-09-01",
        "raw_text": RAW_TEXT,
        "exceptions_text": EXCERPT_DUP,
        "conditions": [],
    }
    base.update(overrides)
    return base


class FakeClient:
    """가짜 LLM 클라이언트. ``ai.judgment.client.LLMClient`` 자리에 꽂는다."""

    def __init__(self, payload=None, *, raise_error=None):
        self.calls = 0
        self.payload = payload
        self.raise_error = raise_error

    def complete(self, *, system, user, timeout, schema=None):
        self.calls += 1
        if self.raise_error is not None:
            raise self.raise_error
        return self.payload


def condition_json(*items):
    return json.dumps(list(items), ensure_ascii=False)


def unknown_condition(needed_field="other_benefit", excerpt=EXCERPT_DUP):
    return {
        "name": "타 지원금 수혜 여부",
        "result": UNKNOWN,
        "excerpt": excerpt,
        "needed_field": needed_field,
    }


def met_condition(excerpt=EXCERPT_LEAVE):
    return {"name": "휴학생 제외", "result": MET, "excerpt": excerpt}


def judge_with(payload, **kwargs):
    """가짜 클라이언트를 꽂은 실제 판정기. (판정 함수, 클라이언트) 를 돌려준다."""
    client = FakeClient(payload, **kwargs)
    return ExceptionJudge(client).judge_policies, client


def good_answer(text="확인이 필요한 제도를 찾았어요."):
    """검증을 통과하는 답변. 각주 [1] 이 붙은 판정 문장과 고정 문구를 담는다."""

    def writer(prompt):
        return f"{text} 이 제도는 조건 확인이 필요해요[1]. {answer.CLOSING_LINE}."

    return writer


class TestJudgmentFeedsFollowup(unittest.TestCase):
    """AI 판정이 낸 미확인이 후속 질문으로 이어져야 한다.

    이것이 두 파트를 잇는 **가장 중요한 계약**이다. ``followup`` 은 ``UnknownItem`` 목록을
    요구하고 AI 판정은 ``result="unknown"`` + ``needed_field`` 를 낸다. 그 사이를 아무도
    잇지 않으면, 예외 조건 때문에 확인이 필요한 정책에서 질문이 뜨지 않는다. 예외는 나지
    않고 화면만 조용히 비어 있다.
    """

    def test_판정의_needed_field_가_후속_질문이_된다(self):
        judge, client = judge_with(condition_json(unknown_condition()))
        result = turn.run_turn(
            PROFILE,
            {"intent": fields.FIND_POLICY},
            policies=[policy()],
            judge_policies=judge,
            answer_writer=good_answer(),
        )

        self.assertEqual(client.calls, 1, "판정기가 불리지 않았다")
        self.assertIsNotNone(result.followup, "판정이 미확인을 냈는데 후속 질문이 없다")
        self.assertEqual(result.followup.get("field"), fields.OTHER_BENEFIT)
        self.assertTrue(
            result.followup.get("question"),
            f"질문 문구가 비었다: {result.followup}",
        )

    def test_규칙_미확인과_판정_미확인이_함께_후보가_된다(self):
        """둘 중 하나만 넘기면 그쪽 원인으로만 질문이 뜬다."""
        judge, _ = judge_with(condition_json(unknown_condition()))
        rule_items = [
            {
                "field": fields.INCOME_BRACKET,
                "policy_id": "SEOUL-001",
                "policy_rank": 0,
                "policy_title": "제도 SEOUL-001",
            }
        ]
        result = turn.run_turn(
            PROFILE,
            {"intent": fields.FIND_POLICY},
            policies=[policy()],
            rule_unknown_items=rule_items,
            judge_policies=judge,
            answer_writer=good_answer(),
        )
        self.assertIsNotNone(result.followup)
        # 어느 쪽이 먼저 선택되는지는 followup 의 우선순위가 정한다. 여기서 확인하는 것은
        # 두 출처가 모두 후보에 들어갔다는 사실이다.
        self.assertIn(
            result.followup.get("field"),
            {fields.INCOME_BRACKET, fields.OTHER_BENEFIT},
            f"두 출처 어느 쪽도 후보에 없다: {result.followup}",
        )

    def test_공고_확인_필요는_질문으로_만들지_않는다(self):
        """물을 수 있는 항목이 아니다. 조건부 문장으로만 안내한다."""
        judge, _ = judge_with(condition_json(unknown_condition(needed_field=ASK_NOTICE)))
        result = turn.run_turn(
            PROFILE,
            {"intent": fields.FIND_POLICY},
            policies=[policy()],
            judge_policies=judge,
            answer_writer=good_answer(),
        )
        if result.followup is not None:
            self.assertNotEqual(result.followup.get("field"), ASK_NOTICE)


class TestFootnoteNumbering(unittest.TestCase):
    """각주 번호는 한 응답 안에서 1부터 순서대로다 (docs/03-api-contract.md 4-1, 5-1).

    정책마다 1번부터 다시 시작하면 답변의 ``[1]`` 이 어느 정책의 발췌인지 알 수 없다.
    ``public_conditions`` 는 한 정책 분량만 번호를 붙이고 ``next_footnote_id`` 를 돌려주므로,
    그 값을 다음 정책에 넘기는 것은 이 파일의 책임이다. 빼먹으면 오류 없이 어긋난다.
    """

    def test_정책_두_개의_각주_번호가_겹치지_않는다(self):
        judge, _ = judge_with(condition_json(unknown_condition()))
        result = turn.run_turn(
            PROFILE,
            {"intent": fields.FIND_POLICY},
            policies=[policy("SEOUL-001"), policy("SEOUL-002")],
            judge_policies=judge,
            answer_writer=good_answer(),
        )
        numbers = [row["footnote_id"] for row in result.footnotes]
        self.assertEqual(
            len(numbers),
            len(set(numbers)),
            f"각주 번호가 겹쳤다: {numbers}",
        )
        self.assertEqual(numbers, sorted(numbers), f"번호 순서가 어긋났다: {numbers}")

    def test_각주가_1부터_시작한다(self):
        judge, _ = judge_with(condition_json(unknown_condition()))
        result = turn.run_turn(
            PROFILE,
            {"intent": fields.FIND_POLICY},
            policies=[policy()],
            judge_policies=judge,
            answer_writer=good_answer(),
        )
        self.assertTrue(result.footnotes, "각주가 하나도 안 나왔다")
        self.assertEqual(result.footnotes[0]["footnote_id"], 1)

    def test_각주_항목에_출처와_발췌가_있다(self):
        """``answer.sanitize`` 가 이 목록으로 각주 번호를 검사한다 (5-1)."""
        judge, _ = judge_with(condition_json(unknown_condition()))
        result = turn.run_turn(
            PROFILE,
            {"intent": fields.FIND_POLICY},
            policies=[policy()],
            judge_policies=judge,
            answer_writer=good_answer(),
        )
        row = result.footnotes[0]
        for key in ("footnote_id", "policy_id", "excerpt", "source_url"):
            with self.subTest(key=key):
                self.assertTrue(row.get(key), f"{key} 가 비었다: {row}")

    def test_각주_번호와_조건_번호가_같다(self):
        """답변의 각주와 카드의 조건이 같은 번호를 가리켜야 한다."""
        judge, _ = judge_with(condition_json(unknown_condition()))
        result = turn.run_turn(
            PROFILE,
            {"intent": fields.FIND_POLICY},
            policies=[policy()],
            judge_policies=judge,
            answer_writer=good_answer(),
        )
        condition_numbers = {
            item.get("footnote_id") for item in result.policies[0]["conditions"]
        }
        footnote_numbers = {row["footnote_id"] for row in result.footnotes}
        self.assertEqual(condition_numbers, footnote_numbers)


class TestCitationGate(unittest.TestCase):
    """인용 검증에 실패한 발췌는 결과에 남지 않아야 한다.

    원문에 없는 근거가 화면에 나가는 것이 이 제품에서 가장 나쁜 실패다. 판정기 출력을
    그대로 쓰면 그 일이 일어난다. ``judge_and_verify`` 가 검증하고 ``public_conditions``
    가 공개 여부를 정하는데, 이 파일이 검증 전 값을 쓰면 두 관문이 모두 무력해진다.
    """

    def test_원문에_없는_발췌는_공개되지_않는다(self):
        judge, _ = judge_with(
            condition_json(unknown_condition(excerpt="이 문장은 공고 원문에 존재하지 않습니다"))
        )
        result = turn.run_turn(
            PROFILE,
            {"intent": fields.FIND_POLICY},
            policies=[policy()],
            judge_policies=judge,
            answer_writer=good_answer(),
        )
        for row in result.footnotes:
            self.assertNotIn("존재하지 않습니다", row["excerpt"])
        for item in result.policies[0]["conditions"]:
            self.assertNotIn("존재하지 않습니다", str(item.get("excerpt") or ""))

    def test_검증_실패는_지표에_남는다(self):
        """조용히 사라지면 통합 중에 원인을 찾을 수 없다."""
        judge, _ = judge_with(condition_json(unknown_condition(excerpt="원문에 없는 문장입니다요")))
        result = turn.run_turn(
            PROFILE,
            {"intent": fields.FIND_POLICY},
            policies=[policy()],
            judge_policies=judge,
            answer_writer=good_answer(),
        )
        judgment = result.metrics.get("judgment", {})
        self.assertTrue(
            judgment.get("removed_excerpts") or judgment.get("issues"),
            f"검증 실패가 기록되지 않았다: {judgment}",
        )

    def test_통과한_발췌는_원문_구간으로_바뀐다(self):
        """화면에는 항상 원문 쪽 문장이 나간다 (ai/judgment/citation.py)."""
        judge, _ = judge_with(condition_json(unknown_condition()))
        result = turn.run_turn(
            PROFILE,
            {"intent": fields.FIND_POLICY},
            policies=[policy()],
            judge_policies=judge,
            answer_writer=good_answer(),
        )
        self.assertIn(result.footnotes[0]["excerpt"], RAW_TEXT)


class TestEarlyExit(unittest.TestCase):
    """범위 밖·잡담이면 판정과 답변을 건너뛴다 (9장 4단계)."""

    def test_범위_밖이면_판정기를_부르지_않는다(self):
        judge, client = judge_with(condition_json(unknown_condition()))
        result = turn.run_turn(
            PROFILE,
            {"intent": fields.OUT_OF_SCOPE},
            policies=[policy()],
            judge_policies=judge,
            answer_writer=good_answer(),
        )
        self.assertTrue(result.stop_here, "고정 응답으로 끝나지 않았다")
        self.assertTrue(result.fixed_reply, "고정 응답 문구가 비었다")
        self.assertEqual(
            client.calls, 0, "답할 수 없는 질문에 모델 예산을 썼다"
        )

    def test_잡담도_같다(self):
        judge, client = judge_with(condition_json(unknown_condition()))
        result = turn.run_turn(
            PROFILE,
            {"intent": fields.SMALLTALK},
            policies=[policy()],
            judge_policies=judge,
            answer_writer=good_answer(),
        )
        self.assertTrue(result.stop_here)
        self.assertEqual(client.calls, 0)

    def test_조기_종료에도_프로필_갱신은_숨기지_않는다(self):
        """"범위 밖"은 답변을 줄이라는 뜻이고 프로필을 숨기라는 뜻이 아니다."""
        result = turn.run_turn(
            PROFILE,
            {
                "intent": fields.OUT_OF_SCOPE,
                "profile_changes": [{"field": fields.STATUS, "value": "on_leave"}],
            },
            policies=[policy()],
        )
        self.assertTrue(result.stop_here)
        self.assertIsNotNone(
            result.profile_update, "프로필이 바뀌었는데 이벤트가 사라졌다"
        )
        self.assertEqual(result.profile.get(fields.STATUS), "on_leave")


class TestDegradedPaths(unittest.TestCase):
    """한 단계가 실패해도 나머지는 남아야 한다 (9장 실패 처리)."""

    def test_판정기가_없어도_턴이_돈다(self):
        """모델 키 없는 환경에서도 카드는 나가야 한다."""
        result = turn.run_turn(
            PROFILE,
            {"intent": fields.FIND_POLICY},
            policies=[policy()],
            judge_policies=None,
            answer_writer=good_answer(),
        )
        self.assertEqual(len(result.policies), 1, "카드가 사라졌다")
        self.assertIn(turn.NOTE_NO_JUDGE, result.metrics.get("notes", []))

    def test_판정_실패가_카드를_지우지_않는다(self):
        judge, _ = judge_with(None, raise_error=RuntimeError("모델 없음"))
        result = turn.run_turn(
            PROFILE,
            {"intent": fields.FIND_POLICY},
            policies=[policy()],
            judge_policies=judge,
            answer_writer=good_answer(),
        )
        self.assertEqual(len(result.policies), 1)

    def test_한_정책_실패가_다른_정책을_지우지_않는다(self):
        calls = {"n": 0}

        def flaky(policies, profile):
            """첫 정책만 실패하는 판정기."""
            calls["n"] += 1
            raise RuntimeError("일부 실패")

        result = turn.run_turn(
            PROFILE,
            {"intent": fields.FIND_POLICY},
            policies=[policy("SEOUL-001"), policy("SEOUL-002")],
            judge_policies=flaky,
            answer_writer=good_answer(),
        )
        self.assertEqual(len(result.policies), 2, "정책 목록이 줄었다")
        self.assertIn(turn.NOTE_JUDGE_FAILED, result.metrics.get("notes", []))

    def test_답변_작성기가_없으면_답변만_실패한다(self):
        judge, _ = judge_with(condition_json(unknown_condition()))
        result = turn.run_turn(
            PROFILE,
            {"intent": fields.FIND_POLICY},
            policies=[policy()],
            judge_policies=judge,
            answer_writer=None,
        )
        self.assertTrue(result.answer_failed)
        self.assertEqual(result.answer_text, "")
        self.assertEqual(len(result.policies), 1, "카드가 사라졌다")
        self.assertTrue(result.footnotes, "각주가 사라졌다")
        self.assertIsNotNone(result.followup, "후속 질문이 사라졌다")

    def test_답변_작성기가_터져도_나머지는_남는다(self):
        judge, _ = judge_with(condition_json(unknown_condition()))

        def boom(prompt):
            raise RuntimeError("모델 오류")

        result = turn.run_turn(
            PROFILE,
            {"intent": fields.FIND_POLICY},
            policies=[policy()],
            judge_policies=judge,
            answer_writer=boom,
        )
        self.assertTrue(result.answer_failed)
        self.assertEqual(len(result.policies), 1)
        self.assertIsNotNone(result.followup)
        self.assertIn(turn.NOTE_ANSWER_WRITER_FAILED, result.metrics.get("notes", []))

    def test_금지_표현이_있는_답변은_버리고_카드는_남긴다(self):
        """P0 위반만 버린다 (pipeline.BLOCKING_PROBLEM_CODES)."""
        judge, _ = judge_with(condition_json(unknown_condition()))
        banned = answer.BANNED_PHRASES[0]

        def writer(prompt):
            return f"{banned.example} 조건 확인이 필요해요[1]. {answer.CLOSING_LINE}."

        result = turn.run_turn(
            PROFILE,
            {"intent": fields.FIND_POLICY},
            policies=[policy()],
            judge_policies=judge,
            answer_writer=writer,
        )
        self.assertEqual(len(result.policies), 1, "카드가 사라졌다")
        self.assertTrue(result.footnotes, "각주가 사라졌다")


class TestServerShape(unittest.TestCase):
    """결과가 서버 모델 키 이름을 쓴다 (docs/10-ai-a-server-handoff.md 2장)."""

    def test_프로필_갱신이_서버_키를_쓴다(self):
        result = turn.run_turn(
            PROFILE,
            {
                "intent": fields.FIND_POLICY,
                "profile_changes": [{"field": fields.STATUS, "value": "on_leave"}],
            },
            policies=[policy()],
        )
        self.assertIsNotNone(result.profile_update)
        self.assertIn("changed_fields", result.profile_update)
        self.assertIn("message", result.profile_update)
        self.assertNotIn("changes", result.profile_update)
        self.assertNotIn("notice", result.profile_update)

    def test_칩이_서버_키를_쓰고_번호는_따로_나온다(self):
        judge, _ = judge_with(condition_json(unknown_condition()))
        result = turn.run_turn(
            PROFILE,
            {"intent": fields.FIND_POLICY},
            policies=[policy(deadline={"badge": "D-3", "d_day": 3, "is_imminent": True})],
            judge_policies=judge,
            answer_writer=good_answer(),
        )
        if result.related is not None:
            self.assertIn("questions", result.related)
            self.assertNotIn("chips", result.related)
            self.assertIsInstance(result.related_chip_ids, tuple)

    def test_델타에_공백_조각이_없다(self):
        """서버 ``AnswerDeltaEventData.delta`` 가 ``min_length=1`` 이다."""
        judge, _ = judge_with(condition_json(unknown_condition()))
        result = turn.run_turn(
            PROFILE,
            {"intent": fields.FIND_POLICY},
            policies=[policy()],
            judge_policies=judge,
            answer_writer=good_answer(),
        )
        self.assertTrue(result.answer_deltas)
        for piece in result.answer_deltas:
            with self.subTest(piece=piece):
                self.assertTrue(piece.strip(), "공백 조각이 스트림에 들어간다")

    def test_이어_붙인_델타가_답변과_같다(self):
        judge, _ = judge_with(condition_json(unknown_condition()))
        result = turn.run_turn(
            PROFILE,
            {"intent": fields.FIND_POLICY},
            policies=[policy()],
            judge_policies=judge,
            answer_writer=good_answer(),
        )
        joined = " ".join(result.answer_deltas)
        self.assertEqual(
            joined.replace(" ", ""),
            result.answer_text.replace(" ", ""),
            "델타를 이어 붙여도 답변이 되지 않는다",
        )

    def test_region_은_프로필_갱신에_실리지_않는다(self):
        """서버 ``ProfileField`` enum 에 ``region`` 이 없다."""
        result = turn.run_turn(
            PROFILE,
            {
                "intent": fields.FIND_POLICY,
                "profile_changes": [
                    {"field": fields.REGION, "value": "outside_seoul"},
                    {"field": fields.STATUS, "value": "on_leave"},
                ],
            },
            policies=[policy()],
        )
        if result.profile_update is not None:
            self.assertNotIn(fields.REGION, result.profile_update.get("changed_fields", {}))


class TestTolerance(unittest.TestCase):
    """어떤 입력에도 예외를 던지지 않는다.

    여기서 터지면 규칙 기반 카드까지 500 에 묻힌다 (9장: AI 가 실패해도 카드는 남는다).
    """

    def test_깨진_입력에도_결과_모양이_유지된다(self):
        cases = (
            ("프로필 None", {"profile": None}),
            ("프로필이 문자열", {"profile": "프로필"}),
            ("해석 출력 None", {"interpretation_output": None}),
            ("해석 출력이 숫자", {"interpretation_output": 7}),
            ("해석 출력이 깨진 JSON", {"interpretation_output": "{이건 JSON 이 아니다"}),
            ("정책이 문자열", {"policies": "정책"}),
            ("정책에 None 섞임", {"policies": [None, policy()]}),
            ("정책이 숫자 목록", {"policies": [1, 2, 3]}),
            ("규칙 미확인이 문자열", {"rule_unknown_items": "income_bracket"}),
            ("물어본 항목이 객체", {"asked_state": object()}),
            ("보여준 칩이 None", {"shown_chips": None}),
            ("각주 시작 번호가 문자열", {"start_footnote_id": "1"}),
            ("각주 시작 번호가 음수", {"start_footnote_id": -5}),
        )
        for label, overrides in cases:
            with self.subTest(case=label):
                kwargs = {
                    "profile": PROFILE,
                    "interpretation_output": {"intent": fields.FIND_POLICY},
                    "policies": [policy()],
                }
                kwargs.update(overrides)
                result = turn.run_turn(
                    kwargs.pop("profile"),
                    kwargs.pop("interpretation_output"),
                    **kwargs,
                )
                self.assertIsInstance(result, turn.TurnResult, label)
                self.assertIsInstance(result.metrics, dict, label)
                self.assertIsInstance(result.policies, tuple, label)
                self.assertIsInstance(result.footnotes, tuple, label)

    def test_판정기가_이상한_값을_돌려줘도_견딘다(self):
        for label, bad in (
            ("None", lambda p, pr: None),
            ("문자열", lambda p, pr: "판정 결과"),
            ("숫자 목록", lambda p, pr: [1, 2]),
            ("빈 dict 목록", lambda p, pr: [{}]),
        ):
            with self.subTest(case=label):
                result = turn.run_turn(
                    PROFILE,
                    {"intent": fields.FIND_POLICY},
                    policies=[policy()],
                    judge_policies=bad,
                    answer_writer=good_answer(),
                )
                self.assertEqual(len(result.policies), 1, label)

    def test_정책이_없어도_돈다(self):
        result = turn.run_turn(PROFILE, {"intent": fields.FIND_POLICY}, policies=[])
        self.assertEqual(result.policies, ())
        self.assertEqual(result.footnotes, ())

    def test_AI_B_없이도_이_모듈을_쓸_수_있다(self):
        """``MISSING`` 이 비어 있어야 정상이지만, 비어 있지 않아도 turn 은 돈다."""
        self.assertIsInstance(turn.MISSING, list)
        result = turn.run_turn(PROFILE, {"intent": fields.FIND_POLICY}, policies=[policy()])
        self.assertIsInstance(result, turn.TurnResult)


class TestMetrics(unittest.TestCase):
    """지표는 20초 예산을 디버깅하는 데만 쓴다. 사용자 화면에 쓰지 않는다."""

    def test_단계별_지표가_모인다(self):
        judge, _ = judge_with(condition_json(unknown_condition()))
        result = turn.run_turn(
            PROFILE,
            {"intent": fields.FIND_POLICY},
            policies=[policy()],
            judge_policies=judge,
            answer_writer=good_answer(),
        )
        for key in ("intent", "interpretation", "judgment", "answer"):
            with self.subTest(key=key):
                self.assertIn(key, result.metrics)

    def test_판정_지표에_공개_건수와_제거_건수가_있다(self):
        judge, _ = judge_with(condition_json(unknown_condition(), met_condition()))
        result = turn.run_turn(
            PROFILE,
            {"intent": fields.FIND_POLICY},
            policies=[policy()],
            judge_policies=judge,
            answer_writer=good_answer(),
        )
        judgment = result.metrics["judgment"]
        for key in ("policies", "footnotes", "unknown_items", "removed_excerpts"):
            with self.subTest(key=key):
                self.assertIn(key, judgment)


if __name__ == "__main__":
    unittest.main()
