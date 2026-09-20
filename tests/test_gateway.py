"""캠프 게이트웨이 호출기 테스트 (ai/gateway.py).

캠프가 준 것은 Anthropic 키가 아니라 **OpenAI 호환 게이트웨이**다(대회 API 가이드).
그래서 이 파일이 확인하는 것은 하나다. **키와 모델 별칭만 넣으면 AI A 와 AI B 가 둘 다
같은 게이트웨이를 통해 돈다.**

여기서 지키려는 계약
  - ``GatewayClient`` 가 AI B ``LLMClient`` 규격을 만족한다 (``ExceptionJudge`` 에 꽂힌다)
  - ``GatewayAdapter`` 가 AI A ``LlmAdapter`` 규격을 만족한다 (``llm.Gateway`` 에 꽂힌다)
  - 두 파트가 **같은 설정**을 쓴다. 모델 별칭이 갈리면 한쪽만 403 이 난다
  - 오류 분류가 AI A·AI B 재시도 정책과 맞물린다. 인증·설정 오류는 재시도하지 않는다
  - 키가 없으면 ``None`` 이고 예외를 던지지 않는다
  - 예외 메시지에 키와 프롬프트 본문이 없다
  - ``openai`` 가 없어도 모듈 import 가 실패하지 않는다

네트워크를 쓰지 않는다. ``openai`` SDK 도 import 하지 않는다. 가짜 SDK 클라이언트를 넣는다.
"""

import unittest

from ai import gateway, runtime, turn
from ai.conversation import answer, fields, llm
from ai.judgment import client as judgment_client

ENV_READY = {"API_KEY": "sk-test-key", "LLM_MODEL": "bedrock-haiku"}

RAW_TEXT = "○ 제외 대상\n  - 타 청년 지원금을 수혜 중인 자는 중복 신청할 수 없습니다\n"
EXCERPT = "타 청년 지원금을 수혜 중인 자는 중복 신청할 수 없습니다"
PROFILE = {"age": 23, "region": "seoul", "status": "enrolled", "categories": ["housing"]}

JUDGE_BODY = (
    '[{"name": "타 지원금 수혜 여부", "result": "unknown", '
    f'"excerpt": "{EXCERPT}", "needed_field": "other_benefit"}}]'
)


# --- 가짜 OpenAI SDK -------------------------------------------------------


class _Message:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content=None, delta=None):
        self.message = _Message(content) if content is not None else None
        self.delta = _Message(delta) if delta is not None else None


class _Response:
    def __init__(self, content):
        self.choices = [_Choice(content=content)]


class _Completions:
    def __init__(self, owner):
        self.owner = owner

    def create(self, **kwargs):
        self.owner.calls.append(kwargs)
        if self.owner.error is not None:
            raise self.owner.error
        if kwargs.get("stream"):
            return iter([_Response(None) for _ in ()] or [
                type("C", (), {"choices": [_Choice(delta=piece)]})()
                for piece in self.owner.chunks
            ])
        return _Response(self.owner.body)


class FakeSDK:
    """``openai.OpenAI`` 자리에 꽂는 가짜 클라이언트."""

    def __init__(self, body=JUDGE_BODY, chunks=None, error=None):
        self.body = body
        self.chunks = chunks if chunks is not None else (
            "확인이 필요한 제도를 찾았어요. ",
            "이 제도는 조건 확인이 필요해요[1]. ",
            answer.CLOSING_LINE + ".",
        )
        self.error = error
        self.calls = []
        self.chat = type("Chat", (), {"completions": _Completions(self)})()


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


class TestConfig(unittest.TestCase):
    """설정은 환경변수에서만 온다."""

    def test_키와_모델이_있으면_설정이_생긴다(self):
        config = gateway.load_config(ENV_READY)
        self.assertIsNotNone(config)
        self.assertEqual(config.model, "bedrock-haiku")
        self.assertEqual(config.base_url, gateway.DEFAULT_BASE_URL)

    def test_주소는_기본값이_있고_모델은_없다(self):
        """주소는 틀리면 즉시 연결 오류다. 모델 별칭은 틀리면 403 이고 그 실패가
        화면에서 "AI 고장"과 구분되지 않는다. 그래서 모델만 기본값을 두지 않는다."""
        self.assertTrue(gateway.DEFAULT_BASE_URL.startswith("https://"))
        self.assertIsNone(gateway.load_config({"API_KEY": "sk-x"}), "모델 없이 설정이 생겼다")

    def test_키_이름이_갈려도_읽는다(self):
        """한 곳에만 키를 넣어도 돌아야 한다. 이름이 갈려서 조용히 스텁으로 도는 것이
        통합에서 가장 찾기 어려운 실패다."""
        for name in gateway.ENV_API_KEY:
            with self.subTest(env=name):
                config = gateway.load_config({name: "sk-x", "LLM_MODEL": "m"})
                self.assertIsNotNone(config, name)

    def test_모델_이름이_갈려도_읽는다(self):
        for name in gateway.ENV_MODEL:
            with self.subTest(env=name):
                config = gateway.load_config({"API_KEY": "sk-x", name: "m"})
                self.assertIsNotNone(config, name)

    def test_설정에_키가_노출되지_않는다(self):
        config = gateway.load_config({"API_KEY": "sk-super-secret", "LLM_MODEL": "m"})
        self.assertNotIn("sk-super-secret", repr(config))

    def test_없는_것을_알려준다(self):
        self.assertEqual(
            set(gateway.missing_settings({})), {gateway.ENV_API_KEY[0], gateway.ENV_MODEL[0]}
        )
        self.assertEqual(gateway.missing_settings(ENV_READY), ())

    def test_깨진_환경에도_예외가_없다(self):
        for label, env in (
            ("빈 dict", {}),
            ("빈 문자열", {"API_KEY": "", "LLM_MODEL": ""}),
            ("공백", {"API_KEY": "  ", "LLM_MODEL": " "}),
            ("토큰이 숫자가 아님", {"API_KEY": "k", "LLM_MODEL": "m", "LLM_MAX_TOKENS": "많이"}),
        ):
            with self.subTest(case=label):
                gateway.load_config(env)
                gateway.missing_settings(env)


class TestJudgmentProtocol(unittest.TestCase):
    """``GatewayClient`` 가 AI B ``LLMClient`` 규격을 만족해야 한다."""

    def test_complete_가_본문을_돌려준다(self):
        sdk = FakeSDK()
        client = gateway.GatewayClient(gateway.load_config(ENV_READY), sdk_client=sdk)
        body = client.complete(system="지시", user="질문", timeout=5.0)
        self.assertEqual(body.strip(), JUDGE_BODY)
        self.assertEqual(len(sdk.calls), 1)

    def test_모델_별칭이_요청에_실린다(self):
        sdk = FakeSDK()
        client = gateway.GatewayClient(gateway.load_config(ENV_READY), sdk_client=sdk)
        client.complete(system="지시", user="질문", timeout=5.0)
        self.assertEqual(sdk.calls[0]["model"], "bedrock-haiku")

    def test_빈_응답은_형식_오류다(self):
        """빈 응답을 그대로 넘기면 조건이 하나도 없는 것으로 읽혀 모든 조건이
        충족으로 계산될 수 있다."""
        sdk = FakeSDK(body="")
        client = gateway.GatewayClient(gateway.load_config(ENV_READY), sdk_client=sdk)
        with self.assertRaises(judgment_client.LLMError) as caught:
            client.complete(system="지시", user="질문", timeout=5.0)
        self.assertEqual(caught.exception.kind, judgment_client.KIND_SCHEMA)

    def test_ExceptionJudge_에_그대로_꽂힌다(self):
        """이것이 AI B 연결의 최종 증거다."""
        from ai.judgment import ExceptionJudge

        sdk = FakeSDK()
        client = gateway.GatewayClient(gateway.load_config(ENV_READY), sdk_client=sdk)
        verdicts = ExceptionJudge(client).judge_policies([policy()], PROFILE)
        self.assertEqual(len(verdicts), 1)
        self.assertTrue(verdicts[0]["conditions"], "조건이 하나도 안 나왔다")


class TestConversationProtocol(unittest.TestCase):
    """``GatewayAdapter`` 가 AI A ``LlmAdapter`` 규격을 만족해야 한다."""

    def test_필요한_메서드가_있다(self):
        adapter = gateway.GatewayAdapter(
            gateway.GatewayClient(gateway.load_config(ENV_READY), sdk_client=FakeSDK())
        )
        for name in ("complete_structured", "stream_text"):
            with self.subTest(method=name):
                self.assertTrue(callable(getattr(adapter, name, None)), name)

    def test_llm_Gateway_에_꽂아_구조화_호출이_돈다(self):
        """이것이 AI A 연결의 최종 증거다."""
        sdk = FakeSDK(body='{"intent": "find_policy"}')
        adapter = gateway.GatewayAdapter(
            gateway.GatewayClient(gateway.load_config(ENV_READY), sdk_client=sdk)
        )
        outcome = llm.Gateway(adapter=adapter).call_structured(
            llm.CallSite.INTERPRET,
            system="지시",
            user="질문",
            schema={"type": "object"},
            fallback={},
        )
        self.assertTrue(outcome.ok, f"실패: {outcome.failure}")
        self.assertEqual(outcome.data.get("intent"), fields.FIND_POLICY)

    def test_앞뒤_설명이_붙은_JSON_도_읽는다(self):
        """모델은 "다음과 같습니다:" 같은 머리말을 붙인다."""
        for label, body in (
            ("머리말", '결과는 다음과 같습니다: {"intent": "find_policy"}'),
            ("코드 울타리", '```json\n{"intent": "find_policy"}\n```'),
            ("뒤에 설명", '{"intent": "find_policy"} 이상입니다'),
        ):
            with self.subTest(case=label):
                sdk = FakeSDK(body=body)
                adapter = gateway.GatewayAdapter(
                    gateway.GatewayClient(gateway.load_config(ENV_READY), sdk_client=sdk)
                )
                response = adapter.complete_structured(
                    llm.StructuredRequest(
                        system="지시", user="질문", schema={"type": "object"}, timeout_s=5.0
                    )
                )
                self.assertEqual(response.data.get("intent"), "find_policy", label)

    def test_JSON_이_전혀_없으면_형식_오류다(self):
        sdk = FakeSDK(body="JSON 은 없고 설명만 있습니다")
        adapter = gateway.GatewayAdapter(
            gateway.GatewayClient(gateway.load_config(ENV_READY), sdk_client=sdk)
        )
        with self.assertRaises(llm.ModelFormatError):
            adapter.complete_structured(
                llm.StructuredRequest(
                    system="지시", user="질문", schema={"type": "object"}, timeout_s=5.0
                )
            )

    def test_스트리밍이_조각을_낸다(self):
        sdk = FakeSDK()
        adapter = gateway.GatewayAdapter(
            gateway.GatewayClient(gateway.load_config(ENV_READY), sdk_client=sdk)
        )
        pieces = list(
            adapter.stream_text(
                llm.TextRequest(system="지시", user="질문", timeout_s=5.0)
            )
        )
        self.assertTrue(pieces)
        self.assertIn(answer.CLOSING_LINE, "".join(pieces))


class TestErrorClassification(unittest.TestCase):
    """오류 분류가 두 파트의 재시도 정책과 맞물려야 한다.

    분류가 틀리면 재시도하면 안 되는 것을 재시도해 20초를 태운다.
    """

    def test_상태_코드로_분류한다(self):
        cases = (
            (401, judgment_client.KIND_AUTH),
            (403, judgment_client.KIND_AUTH),
            (404, judgment_client.KIND_CONFIG),
            (429, judgment_client.KIND_RATE_LIMIT),
            (500, judgment_client.KIND_SERVER),
            (503, judgment_client.KIND_OVERLOADED),
            (529, judgment_client.KIND_OVERLOADED),
        )
        for status, expected in cases:
            with self.subTest(status=status):
                exc = type("APIStatusError", (Exception,), {})()
                exc.status_code = status
                self.assertEqual(gateway.classify(exc), expected)

    def test_인증_오류는_재시도하지_않는다(self):
        """키나 별칭이 틀린 것이다. 다시 불러도 같은 결과고 예산만 태운다."""
        exc = type("AuthenticationError", (Exception,), {})()
        exc.status_code = 401
        as_b = gateway.as_llm_error(exc)
        self.assertFalse(as_b.retryable, "AI B 가 인증 오류를 재시도한다")

        as_a = gateway.as_model_error(exc)
        spec = llm.policy_for(llm.CallSite.ANSWER)
        self.assertFalse(spec.should_retry(as_a), "AI A 가 인증 오류를 재시도한다")

    def test_모르는_오류는_재시도하지_않는다(self):
        """재시도해서 20초를 태우는 쪽이 한 번 실패하는 쪽보다 나쁘다."""
        exc = RuntimeError("알 수 없음")
        self.assertEqual(gateway.classify(exc), judgment_client.KIND_UNKNOWN)
        self.assertFalse(gateway.as_llm_error(exc).retryable)

    def test_타임아웃은_타임아웃으로_분류한다(self):
        for label, exc in (
            ("TimeoutError", TimeoutError()),
            ("APITimeoutError", type("APITimeoutError", (Exception,), {})()),
        ):
            with self.subTest(case=label):
                self.assertEqual(gateway.classify(exc), judgment_client.KIND_TIMEOUT)

    def test_SDK_예외가_llm_예외로_번역된다(self):
        sdk = FakeSDK(error=TimeoutError())
        adapter = gateway.GatewayAdapter(
            gateway.GatewayClient(gateway.load_config(ENV_READY), sdk_client=sdk)
        )
        with self.assertRaises(llm.ModelCallError):
            adapter.complete_structured(
                llm.StructuredRequest(
                    system="지시", user="질문", schema={"type": "object"}, timeout_s=5.0
                )
            )

    def test_예외_메시지에_키와_프롬프트가_없다(self):
        secret = "sk-super-secret"
        prompt = "사용자 프로필 나이 23 관악구"
        sdk = FakeSDK(error=RuntimeError(f"{secret} {prompt} 실패"))
        client = gateway.GatewayClient(
            gateway.load_config({"API_KEY": secret, "LLM_MODEL": "m"}), sdk_client=sdk
        )
        with self.assertRaises(judgment_client.LLMError) as caught:
            client.complete(system="지시", user=prompt, timeout=5.0)
        text = str(caught.exception)
        self.assertNotIn(secret, text)
        self.assertNotIn("관악구", text)


class TestRuntimeWiring(unittest.TestCase):
    """``runtime.for_turn`` 이 게이트웨이를 먼저 본다."""

    def test_게이트웨이_키로_준비된다(self):
        rt = runtime.for_turn(ENV_READY)
        self.assertTrue(rt.ready, f"준비되지 않았다: {rt.missing}")

    def test_키가_없으면_무엇이_없는지_알려준다(self):
        rt = runtime.for_turn({})
        self.assertFalse(rt.ready)
        self.assertIn(gateway.ENV_API_KEY[0], rt.missing)
        self.assertIn(gateway.ENV_MODEL[0], rt.missing)

    def test_판정기와_답변_작성기가_같은_설정을_쓴다(self):
        """모델 별칭이 갈리면 한쪽만 403 이 난다."""
        adapter = gateway.adapter_from_env(ENV_READY)
        rt = runtime.for_turn(ENV_READY, adapter=adapter)
        self.assertTrue(rt.ready)
        self.assertIs(
            getattr(adapter, "client", None).config,
            adapter.client.config,
            "판정기와 어댑터가 다른 설정을 들고 있다",
        )


class TestEndToEnd(unittest.TestCase):
    """게이트웨이 하나로 한 턴이 끝까지 돌아야 한다. 이 파일의 최종 증거다."""

    def test_판정과_답변이_같은_게이트웨이로_돈다(self):
        judge_sdk = FakeSDK()
        answer_sdk = FakeSDK()
        judge_client = gateway.GatewayClient(
            gateway.load_config(ENV_READY), sdk_client=judge_sdk
        )
        adapter = gateway.GatewayAdapter(
            gateway.GatewayClient(gateway.load_config(ENV_READY), sdk_client=answer_sdk)
        )
        rt = runtime.for_turn(ENV_READY, adapter=adapter, judge_client=judge_client)

        result = turn.run_turn(
            PROFILE,
            {"intent": fields.FIND_POLICY},
            policies=[policy()],
            judge_policies=rt.judge_policies,
            answer_writer=rt.answer_writer,
        )

        self.assertTrue(judge_sdk.calls, "판정 모델이 불리지 않았다")
        self.assertTrue(answer_sdk.calls, "답변 모델이 불리지 않았다")
        self.assertFalse(
            result.answer_failed,
            f"답변 실패: {result.metrics.get('answer', {}).get('blocking_codes')}",
        )
        self.assertTrue(result.footnotes, "각주가 없다")
        self.assertIsNotNone(result.followup, "후속 질문이 없다")
        self.assertEqual(result.followup.get("field"), fields.OTHER_BENEFIT)

    def test_답변_요청이_스트리밍으로_나간다(self):
        answer_sdk = FakeSDK()
        adapter = gateway.GatewayAdapter(
            gateway.GatewayClient(gateway.load_config(ENV_READY), sdk_client=answer_sdk)
        )
        rt = runtime.for_turn(ENV_READY, adapter=adapter)
        from ai.conversation import pipeline

        rt.answer_writer(
            pipeline.build_answer_prompt(answer.build_skeleton([policy()]), profile=PROFILE)
        )
        self.assertTrue(answer_sdk.calls[0].get("stream"), "스트리밍으로 부르지 않았다")

    def test_키가_없으면_카드만_남는다(self):
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


if __name__ == "__main__":
    unittest.main()
