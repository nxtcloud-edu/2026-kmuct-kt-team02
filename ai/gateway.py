"""캠프 게이트웨이 호출기 (OpenAI 호환).

**이 파일이 실제 모델을 부르는 유일한 곳이다.**

캠프가 준 것은 Anthropic API 키가 아니라 **OpenAI 호환 게이트웨이**다
(AI 모델 API 사용 가이드, 고려대 경진대회 · AI Innovators Challenge).

| 항목 | 값 | 환경변수 |
| --- | --- | --- |
| 게이트웨이 주소 | ``https://52.79.201.46/v1`` (도메인 없는 EIP) | ``LLM_BASE_URL`` |
| 키 | ``sk-`` 로 시작하는 값 | ``API_KEY`` |
| 모델 | 실제 모델 ID 가 아니라 **별칭** (예: ``bedrock-haiku``) | ``LLM_MODEL`` |

그래서 ``ai/adapters.py`` 의 ``ClaudeAdapter`` 와 ``ai/judgment/client.py`` 의
``ClaudeClient`` 는 이 환경에서 쓸 수 없다. 둘은 ``anthropic`` SDK 로 Anthropic 엔드포인트를
직접 부른다. 두 파일을 고치지 않고 이 파일을 새로 둔 이유는 두 가지다. Anthropic 직접 호출
경로가 나중에 필요할 수 있고(키를 따로 받으면 그대로 쓴다), AI B 소유 파일을 대회 중에
바꾸면 그쪽 테스트와 충돌한다.

이 파일 하나가 **두 가지 규격을 동시에 만족한다.**

    GatewayClient.complete(system=, user=, timeout=, schema=) -> str
        ``ai/judgment/client.py`` 의 ``LLMClient`` Protocol. ``ExceptionJudge`` 가 쓴다
    GatewayAdapter.complete_structured / stream_text
        ``ai/conversation/llm.py`` 의 ``LlmAdapter`` Protocol. ``Gateway`` 가 쓴다

한 클래스로 합치지 않은 이유: 두 Protocol 의 실패 규격이 다르다. AI B 는
``judgment.client.LLMError`` 를, AI A 는 ``conversation.llm.ModelCallError`` 계열을
기대한다. 한 객체가 두 예외 체계를 섞어 던지면 어느 쪽 재시도 정책이 도는지 알 수 없다.
그래서 호출부는 ``GatewayClient`` 하나로 공유하고, 예외 번역만 각각 입힌다.

TLS 확인
--------
주소가 도메인 없는 IP 라서 인증서 검증이 실패할 것으로 의심했는데, 실제로 확인해 보니
기본 검증으로도 응답이 온다(키 없이 부르면 401). **검증을 끄지 않는다.** 끄면 중간자
공격에 열리고, 프롬프트에는 사용자 프로필이 들어간다.

민감정보
--------
마스킹은 서버가 이미 한다(``server/pii.py``, ``server/ai_gateway.py``). 여기서 다시 하지
않는다. 두 번 마스킹하면 원문이 두 번 망가진다. 대신 **예외 메시지에 요청 본문과 키를
넣지 않는다.** SDK 예외를 그대로 체이닝하면 프롬프트가 로그로 새어 나간다.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Iterator, Mapping, Optional, Tuple

from ai import envfile
from ai.conversation import llm
from ai.judgment import client as judgment_client

# ---------------------------------------------------------------------------
# 1. 설정
# ---------------------------------------------------------------------------

#: 키 환경변수. 가이드가 ``API_KEY`` 를 쓰므로 그것을 정본으로 둔다. 나머지는 팀 안에서
#: 이미 쓰이던 이름이라 함께 읽는다. 한 곳에만 키를 넣어도 돌아야 한다 —
#: 이름이 갈려서 조용히 스텁으로 도는 것이 통합에서 가장 찾기 어려운 실패다.
ENV_API_KEY: Tuple[str, ...] = (
    "API_KEY",
    "LLM_API_KEY",
    "CLAUDE_API_KEY",
    "ANTHROPIC_API_KEY",
    "KMUCT_LLM_API_KEY",
)

#: 모델 별칭 환경변수.
ENV_MODEL: Tuple[str, ...] = ("LLM_MODEL", "CLAUDE_MODEL", "KMUCT_LLM_MODEL")

#: 게이트웨이 주소 환경변수.
ENV_BASE_URL: Tuple[str, ...] = ("LLM_BASE_URL", "OPENAI_BASE_URL")

#: 가이드가 준 주소. 환경변수가 없으면 이 값을 쓴다.
#:
#: 모델 별칭과 달리 기본값을 두는 이유: 주소는 대회가 정한 하나뿐이고 **틀리면 즉시
#: 연결 오류로 드러난다.** 모델 별칭은 틀려도 403 이 나고 그 실패가 "AI 고장"과 구분되지
#: 않아서 기본값을 두지 않는다.
DEFAULT_BASE_URL = "https://52.79.201.46/v1"

#: 가이드가 권한 시작 별칭. **기본값으로 쓰지 않는다.**
#: 승인받지 않은 별칭을 부르면 403 이고, 그 오류 메시지에 허용 목록이 나온다.
SUGGESTED_MODEL = "bedrock-haiku"

INSTALL_HINT = "게이트웨이를 부르려면 openai 패키지가 필요하다: pip install openai"

#: 응답 토큰 상한. 답변은 600자 이내이므로(``answer.MAX_ANSWER_LEN``) 넉넉하다.
DEFAULT_MAX_TOKENS = 2048


def _read(source: Mapping[str, str], names: Tuple[str, ...]) -> str:
    for name in names:
        value = source.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


@dataclass(frozen=True, repr=False)
class GatewayConfig:
    """게이트웨이 설정. ``repr`` 에 키를 넣지 않는다."""

    api_key: str
    model: str
    base_url: str = DEFAULT_BASE_URL
    max_tokens: int = DEFAULT_MAX_TOKENS

    def __repr__(self) -> str:
        return (
            f"GatewayConfig(model={self.model!r}, base_url={self.base_url!r}, "
            f"api_key=<{len(self.api_key)}자>)"
        )


def _source(env: Optional[Mapping[str, str]]) -> Mapping[str, str]:
    """설정을 읽을 곳. 환경변수와 저장소 루트 ``.env`` 를 합친다.

    ``env`` 를 명시하면 **그것만** 쓴다. 테스트가 파일에 영향을 받으면, 개발자 로컬의
    ``.env`` 때문에 통과하거나 실패하는 테스트가 생긴다.

    환경변수가 파일보다 이긴다(``ai/envfile.py`` 독스트링). ``os.environ`` 을 바꾸지 않고
    읽기만 하므로 이 호출이 프로세스 전역 상태를 건드리지 않는다.
    """
    if env is not None:
        return env
    try:
        return envfile.merged()
    except Exception:  # noqa: BLE001 - 파일 문제가 서버를 못 뜨게 하지 않는다
        import os

        return os.environ


def missing_settings(env: Optional[Mapping[str, str]] = None) -> Tuple[str, ...]:
    """무엇이 없어서 모델을 부를 수 없는지.

    키를 넣었는데 모델이 안 불리는 상황에서 **가장 먼저 볼 값**이다. 조용한 건너뛰기를
    드러내는 것이 이 함수의 전부다.
    """
    source = _source(env)
    gaps = []
    if not _read(source, ENV_API_KEY):
        gaps.append(ENV_API_KEY[0])
    if not _read(source, ENV_MODEL):
        gaps.append(ENV_MODEL[0])
    return tuple(gaps)


def load_config(env: Optional[Mapping[str, str]] = None) -> Optional[GatewayConfig]:
    """환경에서 설정을 읽는다. 부족하면 ``None``. **예외를 던지지 않는다.**

    ``None`` 을 돌려주는 이유: 키 없는 환경에서도 서버는 떠야 하고, AI 가 전부 실패해도
    규칙 기반 카드는 화면에 남아야 한다(``docs/03-api-contract.md`` 9장).

    ``env`` 를 주지 않으면 환경변수와 저장소 루트 ``.env`` 를 함께 본다. 환경변수가 이긴다.
    """
    source = _source(env)
    api_key = _read(source, ENV_API_KEY)
    model = _read(source, ENV_MODEL)
    if not api_key or not model:
        return None

    raw_tokens = _read(source, ("LLM_MAX_TOKENS", "CLAUDE_MAX_TOKENS"))
    try:
        max_tokens = int(raw_tokens) if raw_tokens else DEFAULT_MAX_TOKENS
    except ValueError:
        max_tokens = DEFAULT_MAX_TOKENS

    return GatewayConfig(
        api_key=api_key,
        model=model,
        base_url=_read(source, ENV_BASE_URL) or DEFAULT_BASE_URL,
        max_tokens=max_tokens,
    )


# ---------------------------------------------------------------------------
# 2. 오류 분류
# ---------------------------------------------------------------------------

# SDK 예외 이름 → AI B ``LLMError.kind``.
#
# **이름으로 분류하는 이유**: ``openai`` 를 모듈 수준에서 import 하지 않기 때문이다
# (설치되지 않은 환경에서 ``ai`` 패키지가 죽으면 규칙 기반 카드까지 사라진다).
# 클래스를 참조할 수 없으니 타입 이름과 상태 코드로 분류한다. SDK 가 예외 이름을 바꾸면
# 이 표가 낡는데, 그때는 ``KIND_UNKNOWN`` 으로 떨어지고 재시도하지 않는다 — 안전한 방향이다.
_ERROR_NAME_KINDS: Tuple[Tuple[str, str], ...] = (
    ("APITimeoutError", judgment_client.KIND_TIMEOUT),
    ("Timeout", judgment_client.KIND_TIMEOUT),
    ("RateLimitError", judgment_client.KIND_RATE_LIMIT),
    ("AuthenticationError", judgment_client.KIND_AUTH),
    ("PermissionDeniedError", judgment_client.KIND_AUTH),
    ("NotFoundError", judgment_client.KIND_CONFIG),
    ("BadRequestError", judgment_client.KIND_CONFIG),
    ("InternalServerError", judgment_client.KIND_SERVER),
    ("APIConnectionError", judgment_client.KIND_SERVER),
)

# 상태 코드 → kind. 이름보다 정확하므로 먼저 본다.
_STATUS_KINDS: Dict[int, str] = {
    401: judgment_client.KIND_AUTH,
    403: judgment_client.KIND_AUTH,
    404: judgment_client.KIND_CONFIG,
    408: judgment_client.KIND_TIMEOUT,
    429: judgment_client.KIND_RATE_LIMIT,
    500: judgment_client.KIND_SERVER,
    502: judgment_client.KIND_SERVER,
    503: judgment_client.KIND_OVERLOADED,
    504: judgment_client.KIND_TIMEOUT,
    529: judgment_client.KIND_OVERLOADED,
}

#: ``kind`` → AI A 예외 타입. AI A 와 AI B 의 재시도 정책이 같은 분류를 보게 한다.
#: 인증·설정 오류는 재시도해도 절대 안 된다(키나 별칭이 틀린 것이다). 그래서
#: ``ModelCallError`` 로 보낸다 — ``CallSitePolicy.should_retry`` 가 재시도하지 않는 쪽이다.
_LLM_EXCEPTIONS = {
    judgment_client.KIND_TIMEOUT: llm.ModelTimeoutError,
    judgment_client.KIND_RATE_LIMIT: llm.ModelRateLimitError,
    judgment_client.KIND_OVERLOADED: llm.ModelCapacityError,
    judgment_client.KIND_SERVER: llm.ModelServerError,
    judgment_client.KIND_SCHEMA: llm.ModelFormatError,
    judgment_client.KIND_AUTH: llm.ModelCallError,
    judgment_client.KIND_CONFIG: llm.ModelCallError,
    judgment_client.KIND_UNKNOWN: llm.ModelCallError,
}


def _status_of(exc: BaseException) -> Optional[int]:
    for name in ("status_code", "status", "http_status"):
        value = getattr(exc, name, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def classify(exc: BaseException) -> str:
    """SDK 예외를 AI B ``kind`` 로 분류한다."""
    status = _status_of(exc)
    if status is not None and status in _STATUS_KINDS:
        return _STATUS_KINDS[status]
    if status is not None and 500 <= status < 600:
        return judgment_client.KIND_SERVER

    names = {type(exc).__name__} | {base.__name__ for base in type(exc).__mro__}
    for needle, kind in _ERROR_NAME_KINDS:
        if any(needle in name for name in names):
            return kind
    if isinstance(exc, TimeoutError):
        return judgment_client.KIND_TIMEOUT
    return judgment_client.KIND_UNKNOWN


def as_llm_error(exc: BaseException) -> judgment_client.LLMError:
    """AI B 규격 예외로. **원래 메시지를 담지 않는다** (요청 본문이 섞인다)."""
    kind = classify(exc)
    return judgment_client.LLMError(
        kind, status=_status_of(exc), cause_type=type(exc).__name__
    )


def as_model_error(exc: BaseException) -> llm.ModelCallError:
    """AI A 규격 예외로. 메시지는 종류 이름만."""
    kind = classify(exc)
    factory = _LLM_EXCEPTIONS.get(kind, llm.ModelCallError)
    return factory(f"gateway:{kind}")


# ---------------------------------------------------------------------------
# 3. 호출기 (AI B ``LLMClient`` 규격)
# ---------------------------------------------------------------------------


class GatewayClient:
    """게이트웨이 호출기. ``ai/judgment/client.py`` 의 ``LLMClient`` 로 쓸 수 있다.

    ``ExceptionJudge(GatewayClient())`` 로 바로 꽂힌다.

    **재시도하지 않는다.** 재시도는 부르는 쪽 정책이다(``ExceptionJudge`` 와
    ``llm.CallSitePolicy`` 가 각각 한다). 여기서 또 하면 20초 예산이 조용히 두 배로 샌다.
    """

    def __init__(
        self,
        config: Optional[GatewayConfig] = None,
        *,
        env: Optional[Mapping[str, str]] = None,
        sdk_client: Optional[Any] = None,
    ) -> None:
        self._config = config if config is not None else load_config(env)
        self._sdk_client = sdk_client
        self.calls = 0

    def __repr__(self) -> str:
        return f"GatewayClient(config={self._config!r})"

    @property
    def config(self) -> Optional[GatewayConfig]:
        return self._config

    @property
    def ready(self) -> bool:
        return self._config is not None or self._sdk_client is not None

    def _client(self) -> Any:
        """OpenAI SDK 클라이언트. **지연 import 한다.**

        모듈 import 시점에 끌어오면 ``openai`` 가 없는 환경에서 ``ai`` 패키지 전체가 죽고,
        규칙 기반 카드까지 화면에서 사라진다.
        """
        if self._sdk_client is not None:
            return self._sdk_client
        if self._config is None:
            raise judgment_client.LLMError(judgment_client.KIND_CONFIG, "설정이 없다")
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - 설치 여부에 따라 갈린다
            raise judgment_client.LLMError(
                judgment_client.KIND_CONFIG, INSTALL_HINT
            ) from None
        self._sdk_client = OpenAI(
            base_url=self._config.base_url, api_key=self._config.api_key
        )
        return self._sdk_client

    def complete(
        self,
        *,
        system: str,
        user: str,
        timeout: float,
        schema: Optional[dict] = None,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
    ) -> str:
        """한 번 부르고 본문 문자열을 돌려준다. 실패는 ``LLMError``.

        ``schema`` 는 받아 두지만 게이트웨이에 넘기지 않는다. OpenAI 호환 게이트웨이가
        구조화 출력을 지원하는지 대회 가이드에 없고, 지원하지 않는 필드를 보내면 400 이
        난다. **구조화 출력 없이도 안전하다** — ``ai/judgment/schema.py`` 의
        ``parse_conditions`` 와 ``ai/conversation/interpret.py`` 의 ``parse_model_json``
        이 앞뒤 설명이 붙은 JSON 을 떼어 내도록 이미 만들어져 있다. 스키마를 쓰면 파싱
        실패가 줄지만, 지금은 **부르지 못하는 것보다 파싱으로 막는 쪽**이 낫다.
        """
        self.calls += 1
        try:
            client = self._client()
            response = client.chat.completions.create(
                model=model or (self._config.model if self._config else ""),
                messages=[
                    {"role": "system", "content": system or ""},
                    {"role": "user", "content": user or ""},
                ],
                temperature=(
                    llm.TEMPERATURE_STRUCTURED if temperature is None else float(temperature)
                ),
                max_tokens=self._config.max_tokens if self._config else DEFAULT_MAX_TOKENS,
                timeout=max(0.1, float(timeout)),
            )
        except judgment_client.LLMError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise as_llm_error(exc) from None

        text = _first_text(response)
        if not text:
            raise judgment_client.LLMError(judgment_client.KIND_SCHEMA, "빈 응답")
        return text

    def stream(
        self,
        *,
        system: str,
        user: str,
        timeout: float,
        temperature: Optional[float] = None,
        model: Optional[str] = None,
    ) -> Iterator[str]:
        """조각을 흘린다. 실패는 ``LLMError``.

        **공백만 있는 조각은 버리지 않는다.** 문장 중간 공백을 없애면 단어가 붙는다.
        서버 ``AnswerDeltaEventData.delta`` 가 ``min_length=1`` 이라 공백 조각을 막아야
        하는데, 그 걸러내기는 ``ai/conversation/handoff.py`` 의 ``delta_lines`` 가 완성된
        본문을 문장 단위로 나눌 때 한다. 여기서 조각 경계를 건드리면 본문이 망가진다.
        """
        try:
            client = self._client()
            stream = client.chat.completions.create(
                model=model or (self._config.model if self._config else ""),
                messages=[
                    {"role": "system", "content": system or ""},
                    {"role": "user", "content": user or ""},
                ],
                temperature=llm.TEMPERATURE_ANSWER if temperature is None else temperature,
                max_tokens=self._config.max_tokens if self._config else DEFAULT_MAX_TOKENS,
                timeout=max(0.1, float(timeout)),
                stream=True,
            )
            for chunk in stream:
                piece = _first_delta(chunk)
                if piece:
                    yield piece
        except judgment_client.LLMError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise as_llm_error(exc) from None


def _first_text(response: Any) -> str:
    """``chat.completions`` 응답에서 본문을 꺼낸다. 모양이 달라도 견딘다."""
    choices = getattr(response, "choices", None)
    if not choices:
        return ""
    message = getattr(choices[0], "message", None)
    content = getattr(message, "content", None) if message is not None else None
    if isinstance(content, str):
        return content.strip()
    # 일부 게이트웨이는 content 를 블록 목록으로 준다.
    if isinstance(content, (list, tuple)):
        parts = []
        for block in content:
            text = getattr(block, "text", None)
            if text is None and isinstance(block, Mapping):
                text = block.get("text")
            if isinstance(text, str):
                parts.append(text)
        return "".join(parts).strip()
    return ""


def _first_delta(chunk: Any) -> str:
    choices = getattr(chunk, "choices", None)
    if not choices:
        return ""
    delta = getattr(choices[0], "delta", None)
    content = getattr(delta, "content", None) if delta is not None else None
    return content if isinstance(content, str) else ""


# ---------------------------------------------------------------------------
# 4. 어댑터 (AI A ``LlmAdapter`` 규격)
# ---------------------------------------------------------------------------

_JSON_START = re.compile(r"[\[{]")


class GatewayAdapter:
    """``ai/conversation/llm.py`` 의 ``LlmAdapter`` 구현.

    같은 ``GatewayClient`` 를 AI B 와 공유한다. 설정과 SDK 클라이언트가 한 곳에서 오므로
    모델 별칭·주소·키가 두 파트에서 갈릴 수 없다.
    """

    def __init__(self, client: Optional[GatewayClient] = None, **kwargs: Any) -> None:
        self.client = client if client is not None else GatewayClient(**kwargs)

    def __repr__(self) -> str:
        return f"GatewayAdapter(client={self.client!r})"

    def complete_structured(self, request: llm.StructuredRequest) -> llm.StructuredResponse:
        system = getattr(request, "system", "") or ""
        user = getattr(request, "user", "") or ""
        timeout = getattr(request, "timeout_s", None) or 8.0
        try:
            body = self.client.complete(
                system=str(system),
                user=str(user),
                timeout=float(timeout),
                # ``StructuredRequest.model`` 이 채워져 있으면 그것을 쓴다. 호출 지점마다
                # 다른 모델을 쓸 수 있게 열어 둔 자리이고(``llm.resolve_model``), 무시하면
                # 그 설정이 조용히 사라진다.
                model=getattr(request, "model", None),
                temperature=getattr(request, "temperature", None),
            )
        except judgment_client.LLMError as exc:
            raise _from_llm_error(exc) from None
        except Exception as exc:  # noqa: BLE001
            raise as_model_error(exc) from None

        data = _loose_json(body)
        if data is None:
            # 형식 오류는 재시도 대상이다 (CallSitePolicy). 한 번 더 부르면 붙는 경우가 많다.
            raise llm.ModelFormatError("gateway:schema")
        return llm.StructuredResponse(data=data, cached=None)

    def stream_text(self, request: llm.TextRequest) -> Iterator[str]:
        system = getattr(request, "system", "") or ""
        user = getattr(request, "user", "") or ""
        timeout = getattr(request, "timeout_s", None) or 3.0
        try:
            for piece in self.client.stream(
                system=str(system),
                user=str(user),
                timeout=float(timeout),
                temperature=getattr(request, "temperature", None),
                model=getattr(request, "model", None),
            ):
                yield piece
        except judgment_client.LLMError as exc:
            raise _from_llm_error(exc) from None
        except Exception as exc:  # noqa: BLE001
            raise as_model_error(exc) from None


def _from_llm_error(exc: judgment_client.LLMError) -> llm.ModelCallError:
    factory = _LLM_EXCEPTIONS.get(getattr(exc, "kind", ""), llm.ModelCallError)
    return factory(f"gateway:{getattr(exc, 'kind', 'unknown')}")


def _loose_json(body: str) -> Optional[Any]:
    """앞뒤 설명이 붙은 JSON 을 떼어 낸다.

    모델은 "다음과 같습니다:" 같은 머리말을 붙인다. ``interpret.parse_model_json`` 이 같은
    일을 하지만 그 함수는 ``Interpretation`` 을 돌려주므로 여기서 쓸 수 없다.
    """
    text = (body or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        pass

    fenced = re.findall(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    for block in fenced:
        try:
            return json.loads(block.strip())
        except ValueError:
            continue

    match = _JSON_START.search(text)
    if match is None:
        return None
    for end in range(len(text), match.start(), -1):
        try:
            return json.loads(text[match.start() : end])
        except ValueError:
            continue
    return None


def adapter_from_env(env: Optional[Mapping[str, str]] = None) -> Optional[GatewayAdapter]:
    """환경이 준비됐으면 어댑터, 아니면 ``None``. **예외를 던지지 않는다.**"""
    config = load_config(env)
    if config is None:
        return None
    return GatewayAdapter(GatewayClient(config))


__all__ = [
    "DEFAULT_BASE_URL",
    "SUGGESTED_MODEL",
    "ENV_API_KEY",
    "ENV_MODEL",
    "ENV_BASE_URL",
    "GatewayConfig",
    "GatewayClient",
    "GatewayAdapter",
    "classify",
    "as_llm_error",
    "as_model_error",
    "load_config",
    "missing_settings",
    "adapter_from_env",
]
