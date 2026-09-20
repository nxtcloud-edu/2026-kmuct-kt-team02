"""모델 호출부 테스트 (`ai/judgment/client.py`).

**실제 API 를 부르지 않는다.** 키가 없고, 부를 필요도 없다. 확인할 것은 설정 읽기와
실패 분류이고 둘 다 가짜 sdk 객체로 재현된다.

사전 점검에서 찾은 "이러면 데모가 깨진다" 시나리오를 그대로 케이스로 옮겼다.

| 시나리오 | 왜 |
| --- | --- |
| 키가 없음 | 개발 환경에 키가 없는 상태에서도 import·테스트가 되어야 한다 |
| `anthropic` 미설치 | 지연 import 가 실제로 지연되는지 |
| 429 · 529 · 5xx · 401 | 종류를 잘못 분류하면 `judge.py` 가 재시도 판단을 틀린다 |
| 시간 초과 | 재시도하면 20초 상한이 깨진다. `retryable` 이 False 여야 한다 |
| 응답이 비었음 | 형식 오류로 잡아야 1회 재시도 대상이 된다 |
| 키 노출 | 예외 메시지·문자열 표현에 키가 섞이면 로그로 흘러간다 |
"""

import sys
import time
import unittest

from ai.judgment.client import (
    DEFAULT_MAX_TOKENS,
    DEFAULT_MODEL,
    DEFAULT_TEMPERATURE,
    ENV_API_KEY,
    ENV_API_KEY_FALLBACK,
    ENV_MAX_TOKENS,
    ENV_MODEL,
    ENV_TEMPERATURE,
    KINDS,
    RETRYABLE_KINDS,
    ClaudeClient,
    ClaudeConfig,
    LLMError,
    load_config,
)

# 테스트 안에서만 쓰는 가짜 키. 이 문자열이 예외나 repr 에 나오면 실패다.
SECRET = "sk-ant-test-THIS-MUST-NOT-LEAK"

CONFIG = ClaudeConfig(
    api_key=SECRET,
    model="test-model-x",
    max_tokens=256,
    temperature=0.0,
)


# ---------------------------------------------------------------------------
# 가짜 SDK
#
# 실제 SDK 에서 우리가 쓰는 모양은 `sdk.messages.create(...)` 하나뿐이다.
# ---------------------------------------------------------------------------


class FakeBlock:
    def __init__(self, text):
        self.text = text


class FakeUsage:
    def __init__(self, input_tokens=12, output_tokens=34):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class FakeResponse:
    def __init__(self, texts=("본문",), usage=None):
        self.content = [FakeBlock(t) for t in texts]
        if usage is not None:
            self.usage = usage


class FakeMessages:
    def __init__(self, response=None, error=None, accept_timeout=True, delay=0.0):
        self.response = response
        self.error = error
        self.accept_timeout = accept_timeout
        self.delay = delay
        self.calls = []  # create() 에 실제로 넘어간 인자
        self.attempts = 0  # TypeError 로 되돌린 호출까지 포함

    def create(self, **kwargs):
        self.attempts += 1
        if not self.accept_timeout and "timeout" in kwargs:
            # timeout 인자를 모르는 SDK 버전을 흉내낸다. 네트워크는 타지 않는다.
            raise TypeError("create() got an unexpected keyword argument 'timeout'")
        self.calls.append(kwargs)
        if self.delay:
            time.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.response


class FakeSDK:
    def __init__(self, **kwargs):
        self.messages = FakeMessages(**kwargs)


def client_with(**kwargs):
    """가짜 sdk 를 물린 클라이언트와 그 sdk."""
    sdk = FakeSDK(**kwargs)
    return ClaudeClient(CONFIG, sdk=sdk), sdk


def http_error(status, message="boom"):
    """상태 코드를 들고 있는 예외. 실제 SDK 예외의 최소 형태."""
    exc = RuntimeError(message)
    exc.status_code = status
    return exc


# 상태 코드 없이 클래스 이름만으로 판단해야 하는 경우
class APITimeoutError(Exception):
    pass


class RateLimitError(Exception):
    pass


class OverloadedError(Exception):
    pass


class AuthenticationError(Exception):
    pass


class InternalServerError(Exception):
    pass


class blocked_anthropic:
    """`import anthropic` 을 실패하게 만드는 구간.

    `sys.modules` 에 None 이 있으면 import 가 ImportError 를 낸다. 설치 여부와
    무관하게 같은 결과가 나오므로 개발 환경이 달라도 테스트가 흔들리지 않는다.
    """

    def __enter__(self):
        self._had = "anthropic" in sys.modules
        self._prev = sys.modules.get("anthropic")
        sys.modules["anthropic"] = None
        return self

    def __exit__(self, *exc_info):
        if self._had:
            sys.modules["anthropic"] = self._prev
        else:
            sys.modules.pop("anthropic", None)
        return False


# ---------------------------------------------------------------------------


class TestLoadConfig(unittest.TestCase):
    """환경 변수에서 읽는다. 모델 이름을 코드에 박지 않는다."""

    def test_환경_변수를_그대로_읽는다(self):
        config = load_config(
            {
                ENV_API_KEY: SECRET,
                ENV_MODEL: "claude-from-env",
                ENV_MAX_TOKENS: "512",
                ENV_TEMPERATURE: "0.2",
            }
        )
        self.assertEqual(config.api_key, SECRET)
        self.assertEqual(config.model, "claude-from-env")
        self.assertEqual(config.max_tokens, 512)
        self.assertAlmostEqual(config.temperature, 0.2)

    def test_키만_있으면_나머지는_기본값(self):
        config = load_config({ENV_API_KEY: SECRET})
        self.assertEqual(config.model, DEFAULT_MODEL)
        self.assertEqual(config.max_tokens, DEFAULT_MAX_TOKENS)
        self.assertAlmostEqual(config.temperature, DEFAULT_TEMPERATURE)

    def test_기본_온도는_낮다(self):
        """판정은 같은 입력에 같은 결과가 나오는 쪽이 낫다 (연구 문서 7장)."""
        self.assertLessEqual(DEFAULT_TEMPERATURE, 0.2)

    def test_키가_없으면_config_오류(self):
        with self.assertRaises(LLMError) as ctx:
            load_config({})
        self.assertEqual(ctx.exception.kind, "config")
        self.assertFalse(ctx.exception.retryable)

    def test_키가_공백뿐이면_config_오류(self):
        with self.assertRaises(LLMError) as ctx:
            load_config({ENV_API_KEY: "   "})
        self.assertEqual(ctx.exception.kind, "config")

    def test_대체_이름도_본다(self):
        config = load_config({ENV_API_KEY_FALLBACK: SECRET})
        self.assertEqual(config.api_key, SECRET)

    def test_기본_이름이_대체_이름보다_우선이다(self):
        config = load_config(
            {ENV_API_KEY: "primary", ENV_API_KEY_FALLBACK: "fallback"}
        )
        self.assertEqual(config.api_key, "primary")

    def test_숫자가_아닌_값은_조용히_넘기지_않는다(self):
        for name, value in (
            (ENV_MAX_TOKENS, "많이"),
            (ENV_TEMPERATURE, "낮게"),
        ):
            with self.subTest(name=name):
                with self.assertRaises(LLMError) as ctx:
                    load_config({ENV_API_KEY: SECRET, name: value})
                self.assertEqual(ctx.exception.kind, "config")

    def test_범위를_벗어난_값은_config_오류(self):
        for env in (
            {ENV_MAX_TOKENS: "0"},
            {ENV_MAX_TOKENS: "-1"},
            {ENV_TEMPERATURE: "1.5"},
            {ENV_TEMPERATURE: "-0.1"},
        ):
            with self.subTest(env=env):
                payload = {ENV_API_KEY: SECRET}
                payload.update(env)
                with self.assertRaises(LLMError) as ctx:
                    load_config(payload)
                self.assertEqual(ctx.exception.kind, "config")

    def test_env_를_주지_않으면_os_environ_을_본다(self):
        """실제 환경에 키가 없어도 동작이 정해져 있어야 한다."""
        import os

        try:
            load_config()
        except LLMError as exc:
            self.assertEqual(exc.kind, "config")
            self.assertFalse(
                os.environ.get(ENV_API_KEY) or os.environ.get(ENV_API_KEY_FALLBACK),
                "키가 있는데 config 오류가 났다",
            )


class TestComplete(unittest.TestCase):
    """정상 경로. 응답 본문 문자열만 돌려준다."""

    def test_본문_문자열을_돌려준다(self):
        client, sdk = client_with(response=FakeResponse(texts=('{"conditions": []}',)))
        body = client.complete(system="지시문", user="사용자", timeout=8.0)
        self.assertEqual(body, '{"conditions": []}')

    def test_여러_블록은_순서대로_이어_붙인다(self):
        client, _ = client_with(response=FakeResponse(texts=("앞", "뒤")))
        self.assertEqual(client.complete(system="s", user="u", timeout=8.0), "앞뒤")

    def test_딕셔너리_모양_응답도_받는다(self):
        """SDK 버전에 따라 딕셔너리로 오는 경로가 있다."""
        response = {"content": [{"type": "text", "text": "본문"}]}
        client, _ = client_with(response=response)
        self.assertEqual(client.complete(system="s", user="u", timeout=8.0), "본문")

    def test_지시문과_메시지를_손대지_않고_보낸다(self):
        system = "  판정 규칙\n원문에 없는 조건을 만들지 않는다  "
        user = "예외 문장:\n- 휴학생 제외"
        client, sdk = client_with(response=FakeResponse())
        client.complete(system=system, user=user, timeout=8.0)

        sent = sdk.messages.calls[0]
        self.assertEqual(sent["system"], system)
        self.assertEqual(sent["messages"], [{"role": "user", "content": user}])

    def test_설정값을_그대로_넘긴다(self):
        client, sdk = client_with(response=FakeResponse())
        client.complete(system="s", user="u", timeout=7.5)

        sent = sdk.messages.calls[0]
        self.assertEqual(sent["model"], CONFIG.model)
        self.assertEqual(sent["max_tokens"], CONFIG.max_tokens)
        self.assertEqual(sent["temperature"], CONFIG.temperature)
        self.assertEqual(sent["timeout"], 7.5)

    def test_timeout_인자를_모르는_sdk_도_처리한다(self):
        client, sdk = client_with(response=FakeResponse(), accept_timeout=False)
        self.assertEqual(client.complete(system="s", user="u", timeout=8.0), "본문")
        self.assertEqual(sdk.messages.attempts, 2, "인자 없이 한 번 더 부른다")
        self.assertNotIn("timeout", sdk.messages.calls[0])

    def test_timeout_과_무관한_TypeError_는_되돌리지_않는다(self):
        """인자 이름이 안 맞는 경우와 SDK 내부 오류를 구분해야 한다."""
        client, sdk = client_with(error=TypeError("unhashable type: 'dict'"))
        with self.assertRaises(LLMError) as ctx:
            client.complete(system="s", user="u", timeout=8.0)
        self.assertEqual(ctx.exception.kind, "unknown")
        self.assertEqual(sdk.messages.attempts, 1)

    def test_한_번만_부른다(self):
        """재시도는 judge.py 담당이다. 이 모듈은 재시도하지 않는다."""
        client, sdk = client_with(error=http_error(500))
        with self.assertRaises(LLMError):
            client.complete(system="s", user="u", timeout=8.0)
        self.assertEqual(sdk.messages.attempts, 1)

    def test_스키마를_넘겨도_호출은_된다(self):
        """스키마 강제 방식은 당일 확인 후 붙인다. 지금은 적용하지 않는다."""
        client, _ = client_with(response=FakeResponse())
        body = client.complete(
            system="s", user="u", timeout=8.0, schema={"type": "object"}
        )
        self.assertEqual(body, "본문")
        self.assertFalse(
            ClaudeClient.supports_schema_enforcement,
            "적용하지 않는데 지원한다고 표시하면 부르는 쪽이 검증을 건너뛴다",
        )


class TestUsage(unittest.TestCase):
    """지표용 기록. 이것 때문에 호출을 실패시키지 않는다."""

    def test_토큰_수를_남긴다(self):
        client, _ = client_with(response=FakeResponse(usage=FakeUsage(11, 22)))
        client.complete(system="s", user="u", timeout=8.0)
        self.assertEqual(client.last_usage["input_tokens"], 11)
        self.assertEqual(client.last_usage["output_tokens"], 22)
        self.assertIsNotNone(client.last_elapsed)

    def test_토큰_수가_없어도_호출은_성공한다(self):
        client, _ = client_with(response=FakeResponse())  # usage 없음
        self.assertEqual(client.complete(system="s", user="u", timeout=8.0), "본문")
        self.assertIsNone(client.last_usage)

    def test_usage_모양이_깨져도_호출은_성공한다(self):
        response = FakeResponse()
        response.usage = "이상한 값"
        client, _ = client_with(response=response)
        self.assertEqual(client.complete(system="s", user="u", timeout=8.0), "본문")
        self.assertIsNone(client.last_usage)


class TestErrorKinds(unittest.TestCase):
    """실패 분류. 여기가 틀리면 judge.py 의 재시도 판단이 틀린다."""

    def assert_kind(self, error, expected_kind, *, expected_status=None):
        client, _ = client_with(error=error)
        with self.assertRaises(LLMError) as ctx:
            client.complete(system="s", user="u", timeout=8.0)
        self.assertEqual(ctx.exception.kind, expected_kind)
        if expected_status is not None:
            self.assertEqual(ctx.exception.status, expected_status)
        return ctx.exception

    def test_429_는_rate_limit(self):
        exc = self.assert_kind(http_error(429), "rate_limit", expected_status=429)
        self.assertFalse(exc.retryable, "대기 시간이 우리 예산보다 길다")

    def test_529_는_overloaded(self):
        exc = self.assert_kind(http_error(529), "overloaded", expected_status=529)
        self.assertFalse(exc.retryable, "재시도로 해결되지 않는다")

    def test_5xx_는_server(self):
        for status in (500, 502, 503, 504):
            with self.subTest(status=status):
                exc = self.assert_kind(http_error(status), "server")
                self.assertTrue(exc.retryable, "서버 오류만 1회 재시도 대상")

    def test_529_는_server_로_섞이지_않는다(self):
        """5xx 범위지만 성격이 다르다. 데모 모드로 넘길 신호다."""
        exc = self.assert_kind(http_error(529), "overloaded")
        self.assertFalse(exc.retryable)

    def test_401_403_은_auth(self):
        for status in (401, 403):
            with self.subTest(status=status):
                exc = self.assert_kind(http_error(status), "auth")
                self.assertFalse(exc.retryable)

    def test_그_외_4xx_는_unknown(self):
        for status in (400, 404, 422):
            with self.subTest(status=status):
                exc = self.assert_kind(http_error(status), "unknown")
                self.assertFalse(exc.retryable)

    def test_상태_코드가_없으면_클래스_이름으로_분류한다(self):
        """SDK 예외 타입을 import 하지 않고 분류해야 한다."""
        cases = (
            (APITimeoutError("t"), "timeout"),
            (RateLimitError("r"), "rate_limit"),
            (OverloadedError("o"), "overloaded"),
            (AuthenticationError("a"), "auth"),
            (InternalServerError("i"), "server"),
        )
        for error, kind in cases:
            with self.subTest(kind=kind):
                self.assert_kind(error, kind)

    def test_response_안에_있는_상태_코드도_읽는다(self):
        class Response:
            status_code = 429

        exc = RuntimeError("boom")
        exc.response = Response()
        self.assert_kind(exc, "rate_limit", expected_status=429)

    def test_정체를_모르는_예외는_unknown(self):
        exc = self.assert_kind(ValueError("뭔가 잘못됨"), "unknown")
        self.assertFalse(exc.retryable, "원인을 모르는 채 다시 부르지 않는다")


class TestTimeout(unittest.TestCase):
    """시간 초과는 재시도하지 않는다. 20초 상한이 이유다."""

    def test_시간_초과_예외는_timeout_이고_재시도_안_함(self):
        client, _ = client_with(error=TimeoutError("timed out"))
        with self.assertRaises(LLMError) as ctx:
            client.complete(system="s", user="u", timeout=8.0)
        self.assertEqual(ctx.exception.kind, "timeout")
        self.assertFalse(ctx.exception.retryable)

    def test_sdk_가_timeout_을_무시하면_경과_시간으로_잡는다(self):
        """제한을 넘겨 도착한 응답은 쓰지 않는다. 쓰면 전체 상한이 깨진다."""
        client, _ = client_with(response=FakeResponse(), delay=0.05)
        with self.assertRaises(LLMError) as ctx:
            client.complete(system="s", user="u", timeout=0.001)
        self.assertEqual(ctx.exception.kind, "timeout")
        self.assertFalse(ctx.exception.retryable)

    def test_남은_시간이_없으면_부르지도_않는다(self):
        for timeout in (0, -1.0):
            with self.subTest(timeout=timeout):
                client, sdk = client_with(response=FakeResponse())
                with self.assertRaises(LLMError) as ctx:
                    client.complete(system="s", user="u", timeout=timeout)
                self.assertEqual(ctx.exception.kind, "timeout")
                self.assertEqual(sdk.messages.attempts, 0)

    def test_다른_실패라도_제한을_넘겼으면_timeout(self):
        client, _ = client_with(error=http_error(500), delay=0.05)
        with self.assertRaises(LLMError) as ctx:
            client.complete(system="s", user="u", timeout=0.001)
        self.assertEqual(ctx.exception.kind, "timeout")


class TestSchemaKind(unittest.TestCase):
    """본문이 비거나 모양이 아예 아니면 형식 오류다 (1회 재시도 대상)."""

    def assert_schema(self, response):
        client, _ = client_with(response=response)
        with self.assertRaises(LLMError) as ctx:
            client.complete(system="s", user="u", timeout=8.0)
        self.assertEqual(ctx.exception.kind, "schema")
        self.assertTrue(ctx.exception.retryable)

    def test_빈_본문은_schema_오류(self):
        self.assert_schema(FakeResponse(texts=("",)))

    def test_공백뿐인_본문도_schema_오류(self):
        self.assert_schema(FakeResponse(texts=("   \n  ",)))

    def test_블록이_없어도_schema_오류(self):
        self.assert_schema(FakeResponse(texts=()))

    def test_content_가_없는_응답도_schema_오류(self):
        self.assert_schema(object())

    def test_None_응답도_schema_오류(self):
        self.assert_schema(None)


class TestNoSecretLeak(unittest.TestCase):
    """키는 어디에도 나오지 않는다."""

    def test_설정의_문자열_표현에_키가_없다(self):
        for text in (repr(CONFIG), str(CONFIG), "{}".format(CONFIG)):
            self.assertNotIn(SECRET, text)
        self.assertIn("***", repr(CONFIG))

    def test_클라이언트의_문자열_표현에_키가_없다(self):
        client, _ = client_with(response=FakeResponse())
        self.assertNotIn(SECRET, repr(client))

    def test_예외_메시지에_키가_섞이지_않는다(self):
        """SDK 예외 문자열에 키가 들어 있어도 우리 예외로는 넘어오지 않아야 한다."""
        client, _ = client_with(error=http_error(401, f"bad key {SECRET}"))
        with self.assertRaises(LLMError) as ctx:
            client.complete(system="s", user="u", timeout=8.0)
        self.assertNotIn(SECRET, str(ctx.exception))
        self.assertNotIn(SECRET, repr(ctx.exception))

    def test_예외에_프롬프트_내용이_담기지_않는다(self):
        """사용자 메시지 원문은 기록에 남기지 않는다 (docs/03-api-contract.md 11장)."""
        user = "저는 휴학 중이고 월세로 살아요"
        client, _ = client_with(error=http_error(500, f"request body: {user}"))
        with self.assertRaises(LLMError) as ctx:
            client.complete(system="s", user=user, timeout=8.0)
        self.assertNotIn(user, str(ctx.exception))


class TestLazyImport(unittest.TestCase):
    """`anthropic` 없이도 import 되고 테스트가 돈다."""

    def test_패키지가_없으면_호출_시점에_config_오류(self):
        client = ClaudeClient(CONFIG)  # sdk 주입 없음
        with blocked_anthropic():
            with self.assertRaises(LLMError) as ctx:
                client.complete(system="s", user="u", timeout=8.0)
        self.assertEqual(ctx.exception.kind, "config")
        self.assertFalse(ctx.exception.retryable)
        self.assertIn("anthropic", str(ctx.exception), "설치 방법을 담는다")

    def test_객체를_만드는_것만으로는_설정을_읽지_않는다(self):
        """키가 없는 환경에서도 생성은 된다."""
        client = ClaudeClient()
        self.assertIsInstance(repr(client), str)


class TestRetryableTable(unittest.TestCase):
    """`retryable` 은 표대로 고정이다."""

    EXPECTED = {
        "config": False,
        "timeout": False,
        "rate_limit": False,
        "overloaded": False,
        "server": True,
        "schema": True,
        "auth": False,
        "unknown": False,
    }

    def test_모든_종류가_표와_같다(self):
        self.assertEqual(set(KINDS), set(self.EXPECTED))
        for kind, retryable in self.EXPECTED.items():
            with self.subTest(kind=kind):
                self.assertEqual(LLMError(kind=kind).retryable, retryable)

    def test_재시도_가능한_종류는_둘뿐이다(self):
        self.assertEqual(RETRYABLE_KINDS, frozenset({"server", "schema"}))

    def test_모르는_종류는_unknown_으로_떨어진다(self):
        error = LLMError(kind="something_else")
        self.assertEqual(error.kind, "unknown")
        self.assertFalse(error.retryable)


if __name__ == "__main__":
    unittest.main()
