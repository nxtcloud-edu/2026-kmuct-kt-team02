"""턴 실행 묶음 테스트 (ai/runtime.py).

``runtime.for_turn`` 은 "키만 넣으면 모델이 돈다"를 보장하는 자리다. 여기가 어긋나면
키를 제대로 넣었는데도 모델이 안 불리고, 화면에는 "AI 가 실패했다" 모양만 보인다.
예외가 아니라 조용한 건너뛰기라서 테스트로만 드러난다.

여기서 지키려는 계약
  - 키와 모델 이름이 있으면 판정기와 답변 작성기가 **둘 다** 만들어진다
  - 없으면 ``None`` 이 되고 **예외를 던지지 않는다.** 키 없는 환경에서도 서버는 떠야 한다
  - 무엇이 없어서 건너뛰는지 ``missing`` 에 남는다 (키를 넣었는데 안 돌 때 먼저 볼 값)
  - ``for_turn`` 을 두 번 부르면 **다른 게이트웨이**가 나온다. 재사용하면 두 번째 턴이
    시작부터 예산 없음이 되어 모델을 건너뛴다
  - 판정 예산이 전체 예산보다 작다 (두 모듈이 합쳐 20초를 넘지 않게)
  - 실제로 ``turn.run_turn`` 에 꽂아 한 턴이 돈다

모델도 네트워크도 SDK 도 쓰지 않는다. 가짜 클라이언트와 가짜 어댑터를 주입한다.
"""

import unittest

from ai import adapters, runtime, turn
from ai.conversation import answer, fields, llm

ENV_READY = {"CLAUDE_API_KEY": "test-key", "CLAUDE_MODEL": "claude-test"}

RAW_TEXT = (
    "○ 제외 대상\n"
    "  - 타 청년 지원금을 수혜 중인 자는 중복 신청할 수 없습니다\n"
)
EXCERPT = "타 청년 지원금을 수혜 중인 자는 중복 신청할 수 없습니다"

PROFILE = {"age": 23, "region": "seoul", "status": "enrolled", "categories": ["housing"]}


class FakeJudgeClient:
    """``ai.judgment.client.LLMClient`` 자리에 꽂는 가짜 클라이언트."""

    def __init__(self, body=None):
        self.calls = 0
        self.body = body or (
            '[{"name": "타 지원금 수혜 여부", "result": "unknown", '
            f'"excerpt": "{EXCERPT}", "needed_field": "other_benefit"}}]'
        )

    def complete(self, *, system, user, timeout, schema=None):
        self.calls += 1
        return self.body


class FakeAdapter:
    """``llm.LlmAdapter`` 자리에 꽂는 가짜 어댑터."""

    def __init__(self, chunks=None):
        self.structured_calls = 0
        self.stream_calls = 0
        self.chunks = chunks if chunks is not None else (
            "확인이 필요한 제도를 찾았어요. ",
            "이 제도는 조건 확인이 필요해요[1]. ",
            answer.CLOSING_LINE + ".",
        )

    def complete_structured(self, request):
        self.structured_calls += 1
        return llm.StructuredResponse(data={}, cached=None)

    def stream_text(self, request):
        self.stream_calls += 1
        for chunk in self.chunks:
            yield chunk


def policy(policy_id="SEOUL-001"):
    return {
        "id": policy_id,
        "policy_id": policy_id,
        "title": f"제도 {policy_id}",
        "status": answer.CHECK,
        "agency": "서울시",
        "source_url": f"https://example.com/{policy_id}",
        "checked_at": "2026-09-01",
        "raw_text": RAW_TEXT,
        "exceptions_text": EXCERPT,
        "conditions": [],
    }


class TestReadyWithKey(unittest.TestCase):
    """키와 모델 이름이 있으면 모델을 부를 수 있어야 한다."""

    def test_키가_있으면_판정기와_답변_작성기가_둘_다_생긴다(self):
        rt = runtime.for_turn(ENV_READY)
        self.assertTrue(rt.ready, f"준비되지 않았다: {rt.missing}")
        self.assertIsNotNone(rt.judge_policies, "판정기가 없다")
        self.assertIsNotNone(rt.answer_writer, "답변 작성기가 없다")
        self.assertEqual(rt.missing, (), f"빠진 것이 있다: {rt.missing}")

    def test_대체_환경변수_이름도_인식한다(self):
        """키 이름이 갈라져 있어 한쪽만 넣으면 조용히 스텁으로 돈다."""
        for label, env in (
            ("CLAUDE_API_KEY", {"CLAUDE_API_KEY": "k", "CLAUDE_MODEL": "m"}),
            ("ANTHROPIC_API_KEY", {"ANTHROPIC_API_KEY": "k", "CLAUDE_MODEL": "m"}),
        ):
            with self.subTest(case=label):
                rt = runtime.for_turn(env)
                self.assertTrue(rt.ready, f"{label}: {rt.missing}")


class TestMissingKey(unittest.TestCase):
    """키가 없어도 서버는 떠야 한다 (예외 금지)."""

    def test_키가_없으면_None_이지만_예외는_없다(self):
        rt = runtime.for_turn({})
        self.assertFalse(rt.ready)
        self.assertIsNone(rt.judge_policies)
        self.assertIsNone(rt.answer_writer)
        self.assertIsInstance(rt.gateway, llm.Gateway, "게이트웨이는 있어야 한다")

    def test_무엇이_없는지_missing_에_남는다(self):
        """키를 넣었는데 안 돌 때 가장 먼저 볼 값이다.

        이름은 캠프 게이트웨이 기준이다(``ai/gateway.py``). 대회가 준 것이 Anthropic 키가
        아니라 OpenAI 호환 게이트웨이라서, 안내하는 이름이 그쪽이어야 한다. 엉뚱한 이름을
        알려 주면 키를 넣고도 안 도는 이유를 찾지 못한다.
        """
        from ai import gateway

        rt = runtime.for_turn({})
        self.assertIn(runtime.MISSING_ADAPTER, rt.missing)
        self.assertIn(gateway.ENV_API_KEY[0], rt.missing)
        self.assertIn(gateway.ENV_MODEL[0], rt.missing)

    def test_모델_이름만_없어도_건너뛴다(self):
        """지어낸 모델 별칭은 403 이다. 이름이 없으면 부르지 않는 것이 맞다."""
        from ai import gateway

        rt = runtime.for_turn({"API_KEY": "sk-k"})
        self.assertFalse(rt.ready)
        self.assertIn(gateway.ENV_MODEL[0], rt.missing)

    def test_깨진_환경에도_예외를_던지지_않는다(self):
        for label, env in (
            ("None", None),
            ("빈 dict", {}),
            ("값이 빈 문자열", {"CLAUDE_API_KEY": "", "CLAUDE_MODEL": ""}),
            ("공백", {"CLAUDE_API_KEY": "   ", "CLAUDE_MODEL": "  "}),
        ):
            with self.subTest(case=label):
                rt = runtime.for_turn(env)
                self.assertIsInstance(rt, runtime.Runtime, label)


class TestPerTurnFreshness(unittest.TestCase):
    """턴마다 새로 만들어야 한다.

    ``BudgetTracker`` 는 생성 시점부터 20초를 센다. 재사용하면 두 번째 턴이 시작부터
    예산 없음이 되어 모델을 건너뛴다. 그 실패는 예외 없이 일어난다.
    """

    def test_두_번_부르면_게이트웨이가_다르다(self):
        first = runtime.for_turn(ENV_READY)
        second = runtime.for_turn(ENV_READY)
        self.assertIsNot(
            first.gateway, second.gateway, "게이트웨이를 재사용하면 예산이 이어진다"
        )

    def test_두_번_부르면_판정기도_다르다(self):
        first = runtime.for_turn(ENV_READY)
        second = runtime.for_turn(ENV_READY)
        self.assertIsNot(first.judge_policies, second.judge_policies)

    def test_새_게이트웨이는_예산이_남아_있다(self):
        rt = runtime.for_turn(ENV_READY)
        self.assertTrue(
            rt.gateway.budget.has_room(), "새로 만든 예산에 여유가 없다"
        )


class TestBudgetSplit(unittest.TestCase):
    """판정 예산이 전체 예산보다 작아야 한다.

    AI B 는 자체 예산을, AI A 는 전체 20초를 센다. 둘이 서로를 몰라서 합계가 20초를 넘을
    수 있다. ``for_turn`` 은 판정 몫을 떼어 주는 것으로 그 틈을 좁힌다.
    """

    def test_판정_몫이_전체보다_작다(self):
        gateway = llm.Gateway(adapter=FakeAdapter())
        share = runtime._judgment_budget(gateway)
        self.assertLess(share, llm.TOTAL_BUDGET_S, "판정이 전체 예산을 다 쓸 수 있다")
        self.assertGreater(share, 0.0)

    def test_몫이_절반보다_작다(self):
        """답변은 대체 문구가 없고 판정은 미확인이라는 정직한 대체 상태가 있다."""
        self.assertLess(runtime.JUDGMENT_SHARE, 0.5)

    def test_예산이_거의_없으면_판정을_건너뛴다(self):
        """반쯤 판정하다 타임아웃되면 토큰만 쓰고 결과는 건너뛴 것과 같다."""

        class Drained:
            def remaining_s(self):
                return 0.1

        gateway = llm.Gateway(adapter=FakeAdapter())
        gateway.budget = Drained()  # type: ignore[assignment]
        self.assertEqual(runtime._judgment_budget(gateway), 0.0)


class TestEndToEnd(unittest.TestCase):
    """실제로 ``turn.run_turn`` 에 꽂아 한 턴이 돌아야 한다.

    이것이 이 파일의 최종 증거다. 조립이 맞아도 ``turn`` 이 기대하는 모양과 어긋나면
    아무 일도 일어나지 않는다.
    """

    def _runtime(self):
        adapter = FakeAdapter()
        rt = runtime.for_turn(
            ENV_READY, adapter=adapter, judge_client=FakeJudgeClient()
        )
        return rt, adapter

    def test_한_턴이_끝까지_돈다(self):
        rt, adapter = self._runtime()
        result = turn.run_turn(
            PROFILE,
            {"intent": fields.FIND_POLICY},
            policies=[policy()],
            judge_policies=rt.judge_policies,
            answer_writer=rt.answer_writer,
        )
        self.assertFalse(
            result.answer_failed,
            f"답변이 실패했다: {result.metrics.get('answer', {}).get('blocking_codes')}",
        )
        self.assertTrue(result.answer_text, "답변 본문이 비었다")
        self.assertEqual(adapter.stream_calls, 1, "답변 모델이 불리지 않았다")

    def test_판정_결과가_후속_질문까지_이어진다(self):
        rt, _ = self._runtime()
        result = turn.run_turn(
            PROFILE,
            {"intent": fields.FIND_POLICY},
            policies=[policy()],
            judge_policies=rt.judge_policies,
            answer_writer=rt.answer_writer,
        )
        self.assertIsNotNone(result.followup, "판정 미확인이 질문으로 이어지지 않았다")
        self.assertEqual(result.followup.get("field"), fields.OTHER_BENEFIT)

    def test_각주가_붙는다(self):
        rt, _ = self._runtime()
        result = turn.run_turn(
            PROFILE,
            {"intent": fields.FIND_POLICY},
            policies=[policy()],
            judge_policies=rt.judge_policies,
            answer_writer=rt.answer_writer,
        )
        self.assertTrue(result.footnotes, "각주가 없다")
        self.assertEqual(result.footnotes[0]["footnote_id"], 1)

    def test_키가_없으면_카드만_남는다(self):
        """모델 키 없는 환경에서도 카드는 나가야 한다."""
        rt = runtime.for_turn({})
        result = turn.run_turn(
            PROFILE,
            {"intent": fields.FIND_POLICY},
            policies=[policy()],
            judge_policies=rt.judge_policies,
            answer_writer=rt.answer_writer,
        )
        self.assertTrue(result.answer_failed)
        self.assertEqual(len(result.policies), 1, "카드가 사라졌다")

    def test_지표에_빠진_것과_모델_기록이_남는다(self):
        rt, _ = self._runtime()
        turn.run_turn(
            PROFILE,
            {"intent": fields.FIND_POLICY},
            policies=[policy()],
            judge_policies=rt.judge_policies,
            answer_writer=rt.answer_writer,
        )
        snapshot = rt.metrics_snapshot()
        self.assertIn("missing", snapshot)
        self.assertIn("ready", snapshot)
        self.assertIn("llm", snapshot)


class TestPromptWiring(unittest.TestCase):
    """프롬프트 객체와 ``stream_answer`` 시그니처를 잇는 자리.

    ``turn`` 은 ``AssembledPrompt`` 를 넘기고 ``stream_answer`` 는 ``system``/``user`` 를
    따로 받는다. 그대로 넘기면 ``TypeError`` 가 나고 ``turn`` 의 넓은 ``except`` 에 잡혀
    **답변만 조용히 실패한다.** 통합 중에 원인을 찾기 어려운 경로라 테스트로 고정한다.
    """

    def test_조립된_프롬프트를_그대로_넘길_수_있다(self):
        from ai.conversation import pipeline

        adapter = FakeAdapter()
        rt = runtime.for_turn(ENV_READY, adapter=adapter)
        skeleton = answer.build_skeleton([policy()])
        prompt = pipeline.build_answer_prompt(skeleton, profile=PROFILE, policies=[policy()])

        text = rt.answer_writer(prompt)
        self.assertTrue(text, "프롬프트를 넘겼는데 본문이 비었다")
        self.assertEqual(adapter.stream_calls, 1, "모델이 불리지 않았다")

    def test_빈_프롬프트로는_모델을_부르지_않는다(self):
        """토큰을 쓰고 아무 지시 없는 답변을 받는 경로를 막는다."""

        class Empty:
            system = ""
            user = ""

        adapter = FakeAdapter()
        rt = runtime.for_turn(ENV_READY, adapter=adapter)
        self.assertEqual(rt.answer_writer(Empty()), "")
        self.assertEqual(adapter.stream_calls, 0, "빈 프롬프트로 모델을 불렀다")

    def test_on_delta_로_조각을_흘릴_수_있다(self):
        """서버가 그 자리에서 ``answer_delta`` 이벤트를 보낸다."""
        seen = []
        adapter = FakeAdapter()
        rt = runtime.for_turn(ENV_READY, adapter=adapter, on_delta=seen.append)

        class Prompt:
            system = "지시문"
            user = "사용자 프롬프트"

        text = rt.answer_writer(Prompt())
        self.assertTrue(seen, "조각이 흘러나오지 않았다")
        self.assertEqual("".join(seen), text, "흘린 조각과 본문이 다르다")

    def test_프롬프트가_이상해도_예외를_던지지_않는다(self):
        adapter = FakeAdapter()
        rt = runtime.for_turn(ENV_READY, adapter=adapter)
        for label, bad in (("None", None), ("숫자", 5), ("문자열", "프롬프트"), ("객체", object())):
            with self.subTest(case=label):
                self.assertIsInstance(rt.answer_writer(bad), str, label)


class TestAdapterReuse(unittest.TestCase):
    """어댑터는 재사용해도 안전하다. 재사용하면 안 되는 것은 예산과 지표다."""

    def test_같은_어댑터로_두_턴을_돌릴_수_있다(self):
        adapter = FakeAdapter()
        first = runtime.for_turn(ENV_READY, adapter=adapter, judge_client=FakeJudgeClient())
        second = runtime.for_turn(ENV_READY, adapter=adapter, judge_client=FakeJudgeClient())
        self.assertTrue(first.ready)
        self.assertTrue(second.ready)
        self.assertIsNot(first.gateway, second.gateway)


if __name__ == "__main__":
    unittest.main()
