"""모델 호출 지점 테스트 (docs/research/04-llm-api-operations.md 3장, docs/03-api-contract.md 9장).

네트워크를 타지 않는다. 어댑터는 ``StubAdapter`` 로, 시간은 ``fixed_clock`` 으로 주입한다.
**실제로 초를 기다리는 테스트는 하나도 없다.** 20초 예산을 확인하려고 20초를 쓰면
그 테스트는 아무도 돌리지 않게 된다.

여기서 지키려는 계약
  - 재시도는 형식 오류 1회뿐이다. 시간 초과·속도 제한·용량 부족은 재시도하지 않는다
    (research 3장 표).
  - ``Gateway`` 는 ``ModelCallError`` 를 밖으로 내보내지 않는다. 호출하는 쪽이 try 를
    빼먹어도 500 이 나지 않아야 한다.
  - 예산이 없으면 아예 부르지 않는다. 불러 놓고 취소하는 것보다 확실하다.
  - 스트리밍이 실패해도 받은 조각은 버리지 않는다 (research 2장).
"""

import unittest

from ai.conversation.llm import (
    CALL_SITE_POLICIES,
    MIN_CALL_ROOM_S,
    PROFILE_OPEN,
    REASON_BUDGET_EXHAUSTED,
    REASON_NO_ADAPTER,
    RESERVED_NON_MODEL_S,
    RULES_OPEN,
    SOURCE_OPEN,
    TOTAL_BUDGET_S,
    BudgetTracker,
    CallSite,
    Gateway,
    LlmAdapter,
    ModelCapacityError,
    ModelFormatError,
    ModelRateLimitError,
    ModelServerError,
    ModelTimeoutError,
    OutputKind,
    PromptSections,
    StubAdapter,
    api_key_present,
    assemble_prompt,
    fixed_clock,
    policy_for,
    resolve_model,
    run_parallel,
)

SCHEMA = {"type": "object", "properties": {"intent": {"type": "string"}}}
MODEL_DATA = {"intent": "find_policy"}
# 해석 실패 시 "변경 없음", 판정 실패 시 "전체 미확인" 같은 값이 온다. 모양은 부르는 쪽 몫이다.
FALLBACK = {"intent": "find_policy", "profile_changes": []}

SCHEMA_SITES = (CallSite.INTERPRET, CallSite.JUDGE)


class ExplodingAdapter:
    """``ModelCallError`` 로 옮기지 못한 예외를 던지는 어댑터.

    어댑터 구현자가 제공사 예외 매핑을 빠뜨린 상황이다. 그래도 흐름은 멈추면 안 된다.
    """

    def __init__(self, error: BaseException) -> None:
        self.error = error
        self.calls = 0

    def complete_structured(self, request):
        self.calls += 1
        raise self.error

    def stream_text(self, request):
        self.calls += 1
        raise self.error


def gateway(adapter, *, times=(0.0,), env=None):
    """시계를 주입한 게이트웨이.

    ``times`` 의 첫 값이 턴 시작 시각이고 그 뒤 값이 "지금"이다. 값이 떨어지면 마지막
    값을 계속 쓴다. 환경 변수는 빈 매핑을 기본으로 둬서 실행 환경에 좌우되지 않게 한다.
    """
    return Gateway(adapter, clock=fixed_clock(times), env={} if env is None else env)


def call(gw, site=CallSite.INTERPRET, **overrides):
    """스키마 강제 호출 한 번. 반복되는 인자를 줄인다."""
    kwargs = {
        "system": "너는 청년 정책 안내를 돕는다",
        "user": "휴학했어요",
        "schema": SCHEMA,
        "fallback": FALLBACK,
    }
    kwargs.update(overrides)
    return gw.call_structured(site, **kwargs)


def stream(gw, **overrides):
    """답변 스트리밍 한 번."""
    kwargs = {"system": "답변 규칙", "user": "요약해 주세요"}
    kwargs.update(overrides)
    return gw.stream_answer(**kwargs)


class TestCallSites(unittest.TestCase):
    """세 호출 지점이 각각 동작하는지 (research "호출 지점별 방침")."""

    def test_스텁_어댑터가_인터페이스를_만족한다(self):
        self.assertIsInstance(StubAdapter(), LlmAdapter)

    def test_해석과_판정은_스키마_강제_호출로_동작한다(self):
        for site in SCHEMA_SITES:
            with self.subTest(site=site.value):
                adapter = StubAdapter(structured_data=MODEL_DATA)
                outcome = call(gateway(adapter), site)
                self.assertTrue(outcome.ok, f"{site.value}: {outcome.failure}")
                self.assertEqual(outcome.data, MODEL_DATA)
                self.assertEqual(outcome.attempts, 1)
                self.assertTrue(outcome.called)
                self.assertEqual(len(adapter.structured_calls), 1)

    def test_답변은_스트리밍_호출로_동작한다(self):
        adapter = StubAdapter(text_chunks=("첫 문장이에요. ", "둘째 문장이에요."))
        outcome = stream(gateway(adapter))
        self.assertTrue(outcome.ok, outcome.failure)
        self.assertEqual(outcome.text, "첫 문장이에요. 둘째 문장이에요.")
        self.assertEqual(len(adapter.text_calls), 1)
        self.assertIsNotNone(outcome.first_chunk_s, "첫 조각 시각을 기록해야 한다")

    def test_호출_지점별_타임아웃을_어댑터에_넘긴다(self):
        """정책 표의 숫자가 실제 요청에 실리는지 확인한다."""
        for site in SCHEMA_SITES:
            with self.subTest(site=site.value):
                adapter = StubAdapter(structured_data=MODEL_DATA)
                call(gateway(adapter), site)
                self.assertEqual(
                    adapter.structured_calls[0].timeout_s, policy_for(site).timeout_s
                )

    def test_지점별_온도를_어댑터에_넘긴다(self):
        adapter = StubAdapter(structured_data=MODEL_DATA)
        call(gateway(adapter), CallSite.JUDGE)
        self.assertEqual(
            adapter.structured_calls[0].temperature, policy_for(CallSite.JUDGE).temperature
        )

    def test_답변_지점을_스키마_호출로_쓰면_막는다(self):
        """출력 형태가 다르다. 조용히 동작하면 잘못된 배선이 숨는다."""
        with self.assertRaises(ValueError):
            call(gateway(StubAdapter()), CallSite.ANSWER)

    def test_세_지점_모두_정책_표에_있다(self):
        self.assertEqual(set(CALL_SITE_POLICIES), set(CallSite))
        self.assertIs(policy_for(CallSite.ANSWER).output, OutputKind.TEXT_STREAM)


class TestRetryRules(unittest.TestCase):
    """재시도 규칙 (research 3장 표). 형식 오류만 1회."""

    def test_형식_오류는_한_번_재시도한다(self):
        """같은 입력에 형식만 틀린 경우가 있어 한 번은 가치가 있다."""
        adapter = StubAdapter(
            structured_data=MODEL_DATA, structured_errors=(ModelFormatError(), None)
        )
        gw = gateway(adapter)
        outcome = call(gw)
        self.assertTrue(outcome.ok, f"재시도가 성공을 살리지 못했다: {outcome.failure}")
        self.assertEqual(outcome.attempts, 2, "시도 횟수가 2여야 한다")
        self.assertEqual(len(adapter.structured_calls), 2)
        self.assertEqual(gw.metrics["retries"], 1)

    def test_형식_오류가_두_번이면_폴백_값을_돌려준다(self):
        adapter = StubAdapter(
            structured_errors=(ModelFormatError(), ModelFormatError())
        )
        outcome = call(gateway(adapter))
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.data, FALLBACK, "폴백 값이 그대로 나와야 한다")
        self.assertEqual(outcome.failure, "format_error")
        self.assertEqual(outcome.attempts, 2, "재시도는 1회뿐이다")
        self.assertEqual(len(adapter.structured_calls), 2)

    def test_시간_초과는_재시도하지_않는다(self):
        """재시도하면 같은 시간이 또 든다. 20초 상한을 넘긴다."""
        adapter = StubAdapter(structured_errors=(ModelTimeoutError(),))
        outcome = call(gateway(adapter))
        self.assertEqual(outcome.attempts, 1, "시간 초과를 재시도했다")
        self.assertEqual(outcome.failure, "timeout")
        self.assertEqual(len(adapter.structured_calls), 1)

    def test_속도_제한과_용량_부족은_사유가_다르고_재시도하지_않는다(self):
        """대응이 다르다. 속도 제한은 요청을 줄이면 풀리고 용량 부족은 데모 모드로 넘긴다."""
        cases = ((ModelRateLimitError(), "rate_limit"), (ModelCapacityError(), "capacity"))
        seen = set()
        for error, expected in cases:
            with self.subTest(error=type(error).__name__):
                adapter = StubAdapter(structured_errors=(error,))
                outcome = call(gateway(adapter))
                self.assertEqual(outcome.failure, expected)
                self.assertEqual(outcome.attempts, 1, f"{expected} 을 재시도했다")
                seen.add(outcome.failure)
        self.assertEqual(len(seen), 2, "두 실패가 같은 사유로 뭉개졌다")

    def test_답변_작성은_재시도하지_않는다(self):
        adapter = StubAdapter(text_chunks=("가",), text_error=ModelFormatError())
        outcome = stream(gateway(adapter))
        self.assertEqual(outcome.attempts, 1)
        self.assertEqual(len(adapter.text_calls), 1)


class TestGatewayAbsorbsFailures(unittest.TestCase):
    """핵심 계약: 게이트웨이는 예외를 밖으로 던지지 않는다.

    호출하는 쪽이 try 를 빼먹는 순간 500 이 나는 구조를 만들지 않는다. AI 가 전부
    실패해도 규칙 기반 카드로 굴러가야 한다 (docs/03-api-contract.md 9장).
    """

    ERRORS = (
        ModelFormatError("스키마 위반"),
        ModelTimeoutError("8초 초과"),
        ModelRateLimitError("429"),
        ModelCapacityError("과부하"),
        ModelServerError("5xx"),
    )

    def test_어떤_모델_실패에도_결과_객체를_돌려준다(self):
        for error in self.ERRORS:
            with self.subTest(error=type(error).__name__):
                adapter = StubAdapter(structured_errors=(error, error))
                outcome = call(gateway(adapter))
                self.assertFalse(outcome.ok)
                self.assertEqual(outcome.data, FALLBACK)
                self.assertEqual(outcome.failure, error.reason)

    def test_스트리밍_실패도_결과_객체로_돌려준다(self):
        for error in self.ERRORS:
            with self.subTest(error=type(error).__name__):
                adapter = StubAdapter(text_chunks=("가",), text_error=error)
                outcome = stream(gateway(adapter))
                self.assertFalse(outcome.ok)
                self.assertEqual(outcome.failure, error.reason)

    def test_모델_계층이_아닌_예외도_흡수한다(self):
        """어댑터의 예외 매핑이 빠진 경우다. 그래도 흐름은 멈추지 않는다."""
        for error in (RuntimeError("SDK 내부 오류"), KeyError("choices"), ValueError("파싱")):
            with self.subTest(error=type(error).__name__):
                adapter = ExplodingAdapter(error)
                outcome = call(gateway(adapter))
                self.assertFalse(outcome.ok)
                self.assertEqual(outcome.failure, ModelServerError.reason)
                self.assertEqual(outcome.data, FALLBACK)
                self.assertEqual(adapter.calls, 1)

    def test_스트리밍에서도_계층_밖_예외를_흡수한다(self):
        outcome = stream(gateway(ExplodingAdapter(RuntimeError("연결 끊김"))))
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failure, ModelServerError.reason)

    def test_어댑터가_없어도_폴백으로_끝낸다(self):
        """설정이 누락된 상태다. 예외 대신 사유 코드를 남긴다."""
        gw = gateway(None)
        outcome = call(gw)
        self.assertEqual(outcome.failure, REASON_NO_ADAPTER)
        self.assertFalse(outcome.called)
        self.assertEqual(stream(gw).failure, REASON_NO_ADAPTER)

    def test_실패_사유를_지표로_센다(self):
        gw = gateway(StubAdapter(structured_errors=(ModelTimeoutError(),)))
        call(gw)
        snapshot = gw.metrics_snapshot()
        self.assertEqual(snapshot["failures"], {"timeout": 1})
        self.assertIn("budget", snapshot)


class TestBudget(unittest.TestCase):
    """시간 예산 (docs/03-api-contract.md 9장 13단계). 시계를 주입해 확인한다."""

    def test_예산이_없으면_모델을_부르지_않는다(self):
        """불러 놓고 취소하는 것보다 안 부르는 것이 확실하다."""
        adapter = StubAdapter(structured_data=MODEL_DATA)
        gw = gateway(adapter, times=(0.0, 25.0))  # 턴 시작 후 25초 경과
        outcome = call(gw)
        self.assertEqual(adapter.structured_calls, [], "예산이 없는데 모델을 불렀다")
        self.assertFalse(outcome.called)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.failure, REASON_BUDGET_EXHAUSTED)
        self.assertEqual(outcome.data, FALLBACK)
        self.assertEqual(outcome.attempts, 0)

    def test_예산이_없으면_답변도_부르지_않는다(self):
        adapter = StubAdapter(text_chunks=("가",))
        outcome = stream(gateway(adapter, times=(0.0, 25.0)))
        self.assertEqual(adapter.text_calls, [], "예산이 없는데 답변을 불렀다")
        self.assertEqual(outcome.failure, REASON_BUDGET_EXHAUSTED)
        self.assertEqual(outcome.text, "")

    def test_건너뛴_호출을_지점별로_센다(self):
        gw = gateway(StubAdapter(), times=(0.0, 25.0))
        call(gw, CallSite.JUDGE)
        self.assertEqual(gw.budget.skipped_calls, 1)
        self.assertEqual(gw.budget.skipped_by_site, {"judge": 1})

    def test_남은_예산이_타임아웃보다_짧으면_줄여서_부른다(self):
        """정책상 8초여도 남은 예산이 짧으면 그만큼만 준다."""
        adapter = StubAdapter(structured_data=MODEL_DATA)
        outcome = call(gateway(adapter, times=(0.0, 17.0)), CallSite.JUDGE)
        self.assertTrue(outcome.ok, outcome.failure)
        self.assertAlmostEqual(
            adapter.structured_calls[0].timeout_s,
            TOTAL_BUDGET_S - RESERVED_NON_MODEL_S - 17.0,
            msg="타임아웃을 남은 예산으로 줄이지 않았다",
        )

    def test_clamp가_남은_예산_안으로_줄인다(self):
        budget = BudgetTracker(clock=fixed_clock((0.0, 14.0)))
        self.assertAlmostEqual(budget.remaining_s(), TOTAL_BUDGET_S - RESERVED_NON_MODEL_S - 14.0)
        self.assertAlmostEqual(budget.clamp(8.0), 4.5)
        self.assertAlmostEqual(budget.clamp(2.0), 2.0, msg="예산이 남으면 줄이지 않는다")

    def test_남은_예산은_음수가_되지_않는다(self):
        budget = BudgetTracker(clock=fixed_clock((0.0, 30.0)))
        self.assertEqual(budget.remaining_s(), 0.0)
        self.assertEqual(budget.clamp(8.0), 0.0)
        self.assertFalse(budget.has_room(MIN_CALL_ROOM_S))

    def test_예산이_넉넉하면_여유가_있다고_본다(self):
        budget = BudgetTracker(clock=fixed_clock((0.0,)))
        self.assertTrue(budget.has_room())
        self.assertEqual(budget.elapsed_s(), 0.0)


class TestStreamingPartialResponse(unittest.TestCase):
    """부분 응답은 버리지 않는다 (research 2장).

    이미 화면에 나간 문장을 되돌릴 수 없고, 되돌릴 이유도 없다.
    """

    def test_실패해도_받은_조각이_남는다(self):
        deltas = []
        adapter = StubAdapter(
            text_chunks=("첫 문장이에요. ", "둘째 문장이에요. ", "셋째 문장이에요."),
            text_error=ModelServerError("연결 끊김"),
            text_error_after=2,
        )
        outcome = stream(gateway(adapter), on_delta=deltas.append)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.text, "첫 문장이에요. 둘째 문장이에요. ")
        self.assertEqual(deltas, ["첫 문장이에요. ", "둘째 문장이에요. "])
        self.assertEqual(outcome.failure, ModelServerError.reason)

    def test_첫_조각_전에_끊기면_빈_본문으로_실패한다(self):
        adapter = StubAdapter(text_chunks=("가",), text_error=ModelTimeoutError(), text_error_after=0)
        outcome = stream(gateway(adapter))
        self.assertEqual(outcome.text, "")
        self.assertEqual(outcome.failure, "timeout")

    def test_조각이_하나도_오지_않으면_실패로_본다(self):
        """형식상 성공이어도 답변으로 쓸 수 없다."""
        outcome = stream(gateway(StubAdapter(text_chunks=())))
        self.assertFalse(outcome.ok, "빈 답변을 성공으로 처리했다")
        self.assertEqual(outcome.text, "")
        self.assertEqual(outcome.failure, ModelServerError.reason)
        self.assertTrue(outcome.called, "부르기는 했으므로 called 는 참이다")
        self.assertEqual(outcome.attempts, 1)

    def test_조각마다_콜백을_부른다(self):
        deltas = []
        stream(gateway(StubAdapter(text_chunks=("가", "나"))), on_delta=deltas.append)
        self.assertEqual(deltas, ["가", "나"])


class TestRunParallel(unittest.TestCase):
    """병렬 호출 (research 4장). 한 건이 실패해도 나머지를 살린다."""

    def test_한_건이_실패해도_나머지가_산다(self):
        def boom():
            raise RuntimeError("판정 실패")

        def rate_limited():
            raise ModelRateLimitError("429")

        results = run_parallel(
            {
                "SEOUL-001": lambda: "met",
                "SEOUL-002": boom,
                "SEOUL-003": rate_limited,
                "SEOUL-004": lambda: "unknown",
            },
            max_workers=2,
        )
        self.assertEqual(set(results), {"SEOUL-001", "SEOUL-002", "SEOUL-003", "SEOUL-004"})
        self.assertTrue(results["SEOUL-001"].ok)
        self.assertEqual(results["SEOUL-001"].value, "met")
        self.assertTrue(results["SEOUL-004"].ok)
        self.assertFalse(results["SEOUL-002"].ok)
        self.assertEqual(results["SEOUL-002"].failure, ModelServerError.reason)
        self.assertEqual(results["SEOUL-003"].failure, "rate_limit")
        self.assertIsNone(results["SEOUL-002"].value)

    def test_게이트웨이를_쓰면_실패도_결과로_담긴다(self):
        """각 작업이 게이트웨이를 쓰면 예외가 아예 올라오지 않는다."""
        failing = gateway(StubAdapter(structured_errors=(ModelCapacityError(),)))
        working = gateway(StubAdapter(structured_data=MODEL_DATA))
        results = run_parallel(
            {"bad": lambda: call(failing, CallSite.JUDGE), "good": lambda: call(working, CallSite.JUDGE)},
            max_workers=2,
        )
        self.assertTrue(results["bad"].ok, "게이트웨이가 예외를 던졌다")
        self.assertEqual(results["bad"].value.failure, "capacity")
        self.assertTrue(results["good"].value.ok)

    def test_작업이_없으면_빈_결과다(self):
        self.assertEqual(run_parallel({}), {})


class TestAssemblePrompt(unittest.TestCase):
    """블록 순서를 함수로 박는다 (research 5장·6장)."""

    SECTIONS = PromptSections(
        rules="판정 규칙이다",
        source_text="○ 지원 대상 - 만 19세 이상 34세 이하",
        profile="만 24세, 서울 관악구, 재학",
        output_rules="JSON 으로만 답한다",
    )

    def test_캐싱_미사용이면_규칙이_앞과_끝에_두_번_들어간다(self):
        """원문이 가운데 놓이는 배치라 위치 효과를 규칙 반복으로 상쇄한다."""
        prompt = assemble_prompt(self.SECTIONS)
        self.assertEqual(
            prompt.order, ("rules", "source", "profile", "output_rules", "rules_tail")
        )
        self.assertEqual(prompt.user.count(RULES_OPEN), 2)

    def test_캐싱_사용이면_원문이_맨_앞이고_프로필이_뒤에_온다(self):
        """매번 바뀌는 프로필이 앞에 있으면 접두사가 달라져 캐시가 무의미해진다."""
        prompt = assemble_prompt(self.SECTIONS, cache_friendly=True)
        self.assertEqual(prompt.order, ("source", "rules", "profile", "output_rules"))
        self.assertTrue(prompt.user.startswith(SOURCE_OPEN), prompt.user[:40])
        self.assertLess(
            prompt.user.index(SOURCE_OPEN),
            prompt.user.index(PROFILE_OPEN),
            "프로필이 원문보다 앞에 있다",
        )
        self.assertEqual(prompt.user.count(RULES_OPEN), 1, "캐싱 경로는 규칙을 한 번만 둔다")

    def test_빈_블록은_건너뛴다(self):
        """빈 태그만 남으면 잡음이다."""
        sections = PromptSections(
            rules="판정 규칙이다", source_text="", profile="   ", output_rules="JSON 으로만 답한다"
        )
        prompt = assemble_prompt(sections)
        self.assertEqual(prompt.order, ("rules", "output_rules", "rules_tail"))
        self.assertNotIn(SOURCE_OPEN, prompt.user)
        self.assertNotIn(PROFILE_OPEN, prompt.user)

    def test_모든_블록이_비면_본문도_빈다(self):
        prompt = assemble_prompt(PromptSections("", "", "", ""))
        self.assertEqual(prompt.user, "")
        self.assertEqual(prompt.order, ())

    def test_시스템_지시문은_조립하지_않고_그대로_둔다(self):
        """동적으로 조립하면 캐시 접두사가 깨진다."""
        prompt = assemble_prompt(self.SECTIONS, system="너는 안내자다")
        self.assertEqual(prompt.system, "너는 안내자다")
        self.assertNotIn("너는 안내자다", prompt.user)


class TestSettings(unittest.TestCase):
    """설정 읽기. 모델 이름을 코드에 박지 않는다."""

    def test_api_key_present는_키_값을_돌려주지_않는다(self):
        env = {"KMUCT_LLM_API_KEY": "sk-secret-0000"}
        result = api_key_present(env)
        self.assertIs(result, True)
        self.assertNotIn("sk-secret-0000", repr(result), "키 값이 새어 나왔다")

    def test_키가_없거나_공백이면_거짓이다(self):
        self.assertFalse(api_key_present({}))
        self.assertFalse(api_key_present({"KMUCT_LLM_API_KEY": "   "}))

    def test_지점별_모델_변수가_공통_변수를_이긴다(self):
        env = {"KMUCT_LLM_MODEL": "공통모델", "KMUCT_LLM_MODEL_JUDGE": "판정모델"}
        self.assertEqual(resolve_model(CallSite.JUDGE, env), "판정모델")
        self.assertEqual(
            resolve_model(CallSite.INTERPRET, env), "공통모델", "공통 변수로 떨어져야 한다"
        )

    def test_모델_변수가_없으면_None이다(self):
        """처리는 어댑터가 한다. 스텁은 모델 이름 없이도 동작한다."""
        for site in CallSite:
            with self.subTest(site=site.value):
                self.assertIsNone(resolve_model(site, {}))

    def test_해석한_모델_이름을_요청에_싣는다(self):
        adapter = StubAdapter(structured_data=MODEL_DATA)
        gw = gateway(adapter, env={"KMUCT_LLM_MODEL_INTERPRET": "해석모델"})
        call(gw, CallSite.INTERPRET)
        self.assertEqual(adapter.structured_calls[0].model, "해석모델")


if __name__ == "__main__":
    unittest.main()
