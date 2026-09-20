"""Claude API 호출. **모델 호출은 이 파일에서만 한다.**

모델 이름, 호출 방식, 오류 분류를 한 군데로 모은다. 당일 모델이 바뀌어도 이 파일만
고친다 (`docs/research/04-llm-api-operations.md` "모델 교체에 대비하는 방법",
`CONTRIBUTING.md` 7-1-1 "모델 호출").

이 모듈이 하는 일은 셋뿐이다.

1. 설정을 환경 변수에서 읽는다 (모델 이름을 코드에 박지 않는다)
2. 시스템 지시문과 사용자 메시지를 그대로 보내고 **응답 본문 문자열만** 돌려준다
3. 실패를 표로 정해진 종류(`kind`)로 분류한다

하지 않는 일: 응답 파싱(`schema.py`), 재시도·백오프(`judge.py`), 프롬프트 조립
(`prompt.py`), 인용 검증(`citation.py`).

## 환경 변수

| 변수 | 기본값 | 뜻 |
| --- | --- | --- |
| `CLAUDE_API_KEY` | 없음 (필수) | API 키. 비어 있으면 `LLMError(kind="config")` |
| `ANTHROPIC_API_KEY` | 없음 | 대체 이름. `CLAUDE_API_KEY` 가 비었을 때만 본다 |
| `CLAUDE_MODEL` | 없음 (필수) | 캠프에서 발급받은 정확한 모델 이름. 코드에 기본값을 두지 않는다 |
| `CLAUDE_MAX_TOKENS` | `1024` | 판정 응답은 조건 몇 개뿐이라 짧다 |
| `CLAUDE_TEMPERATURE` | `0.0` | 판정이므로 낮게 (연구 문서 7장) |

키는 각자 로컬 환경 변수에만 둔다. 저장소·채팅으로 공유하지 않는다
(`CONTRIBUTING.md` 4장).

## 오류 분류

| kind | 언제 | `retryable` | 이유 |
| --- | --- | --- | --- |
| `config` | 키·설정이 없거나 `anthropic` 미설치 | False | 다시 불러도 같다 |
| `timeout` | 제한 시간 초과 | False | 재시도하면 또 그만큼 걸려 20초 상한을 넘긴다 |
| `rate_limit` | 429 | False | 대기 시간이 우리 예산보다 길다. 요청 수를 줄이는 게 빠르다 |
| `overloaded` | 529 | False | 재시도로 해결되지 않는다. 데모 모드로 넘길 신호 |
| `server` | 5xx (529 제외) | False | 같은 입력을 다시 보내도 바뀔 근거가 약하고 시간 예산을 압박한다 |
| `schema` | 응답 본문이 비었거나 형식이 아예 아님 | True | 1회 |
| `auth` | 401 / 403 | False | 키 문제다 |
| `unknown` | 그 외 | False | 원인을 모르는 채 다시 부르지 않는다 |

**이 모듈은 재시도하지 않는다.** 분류만 하고 판단은 부르는 쪽(`judge.py`)에 맡긴다.
그래야 정책당 8초·전체 20초 예산을 부르는 쪽이 통제할 수 있다
(`ai/judgment/README.md` 9장).

## 기록과 금지

- 사용자 메시지 원문과 프로필은 **어디에도 남기지 않는다.** 이 모듈은 로깅을 하지
  않고, 예외 메시지에 프롬프트 내용이나 원래 예외의 문자열을 담지 않는다
  (`docs/03-api-contract.md` 11장)
- API 키는 예외 메시지에도, `repr()` 에도 나오지 않는다
- 토큰 수만 `last_usage` 에 남긴다. 지표용이다 (`docs/03-api-contract.md` 13장)
"""

from __future__ import annotations

import os
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Protocol

# ---------------------------------------------------------------------------
# 환경 변수 이름과 기본값
# ---------------------------------------------------------------------------

ENV_API_KEY = "CLAUDE_API_KEY"
ENV_API_KEY_FALLBACK = "ANTHROPIC_API_KEY"
ENV_MODEL = "CLAUDE_MODEL"
ENV_MAX_TOKENS = "CLAUDE_MAX_TOKENS"
ENV_TEMPERATURE = "CLAUDE_TEMPERATURE"

# 모델 이름은 환경 변수에서만 읽는다. 코드에 기본값을 두면 캠프에서 받은
# 정확한 이름과 달라도 호출 시점까지 발견하지 못한다.
# 호환성을 위해 상수 이름은 남기되 값은 None 이다. 실제 값으로 사용하지 않는다.
DEFAULT_MODEL: Optional[str] = None

# 판정 응답은 조건 몇 개짜리 구조화 출력이다. 길 필요가 없다.
DEFAULT_MAX_TOKENS = 1024

# 판정은 같은 입력에 같은 결과가 나오는 쪽이 낫다 (연구 문서 7장).
# 다만 낮은 온도로도 완전한 재현성은 보장되지 않는다.
DEFAULT_TEMPERATURE = 0.0

# 설치 안내. 버전 고정은 백엔드B가 의존성 목록에서 한다.
#
# 주의 (백엔드B에 알려야 하는 사항): 현재 `anthropic` 1.x 는 Python 3.10 이상을
# 요구한다. 이 개발 환경은 3.9.6 이다. 실행 환경을 3.10 이상으로 맞추거나 3.8+ 를
# 지원하는 0.x 계열로 고정해야 한다. 어느 쪽이든 이 파일은 그대로 쓸 수 있다.
# 호출 모양(`client.messages.create(...)`)은 두 계열이 같다.
INSTALL_HINT = (
    "anthropic 패키지가 필요하다. 로컬에서는 'pip install anthropic' 으로 설치하고, "
    "고정할 버전은 백엔드B가 의존성 목록에 넣는다"
)

# ---------------------------------------------------------------------------
# 오류 종류
# ---------------------------------------------------------------------------

KIND_CONFIG = "config"
KIND_TIMEOUT = "timeout"
KIND_RATE_LIMIT = "rate_limit"
KIND_OVERLOADED = "overloaded"
KIND_SERVER = "server"
KIND_SCHEMA = "schema"
KIND_AUTH = "auth"
KIND_UNKNOWN = "unknown"

KINDS = (
    KIND_CONFIG,
    KIND_TIMEOUT,
    KIND_RATE_LIMIT,
    KIND_OVERLOADED,
    KIND_SERVER,
    KIND_SCHEMA,
    KIND_AUTH,
    KIND_UNKNOWN,
)

# 문서 합의대로 형식 오류만 1회 재시도한다.
# 서버 오류는 같은 입력을 다시 보내도 결과가 바뀔 근거가 약하고,
# 정책당 8초·전체 20초 예산을 압박하므로 재시도하지 않는다.
RETRYABLE_KINDS = frozenset({KIND_SCHEMA})

# 429 는 속도 제한, 529 는 용량 부족. 둘을 구분해야 대응이 갈린다
# (연구 문서 4장: 전자는 요청을 줄이면 되고 후자는 데모 모드로 넘긴다).
STATUS_RATE_LIMIT = 429
STATUS_OVERLOADED = 529
STATUS_AUTH = (401, 403)


class LLMError(Exception):
    """모델 호출 실패. `kind` 로 분류하고 `retryable` 은 표에서 정해진다.

    `retryable` 을 호출한 쪽이 넘기지 못하게 한 것은 의도다. 재시도 여부가
    파일마다 갈리면 시간 예산 규칙이 깨진다.
    """

    def __init__(
        self,
        kind: str,
        message: str = "",
        *,
        status: Optional[int] = None,
        cause_type: Optional[str] = None,
    ) -> None:
        self.kind = kind if kind in KINDS else KIND_UNKNOWN
        self.status = status
        self.retryable = self.kind in RETRYABLE_KINDS
        # 원래 예외의 문자열은 담지 않는다. 요청 본문이 섞여 들어올 수 있다.
        # 종류 이름만 남긴다.
        self.cause_type = cause_type
        text = message or self.kind
        super().__init__(f"[{self.kind}] {text}")


# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------


@dataclass(frozen=True, repr=False)
class ClaudeConfig:
    """호출에 필요한 값. 키가 들어 있으므로 `repr` 을 직접 정의한다."""

    api_key: str
    model: str
    max_tokens: int
    temperature: float

    def __repr__(self) -> str:
        # 키를 절대 출력하지 않는다. 길이도 알리지 않는다.
        return (
            "ClaudeConfig(api_key='***', "
            f"model={self.model!r}, max_tokens={self.max_tokens!r}, "
            f"temperature={self.temperature!r})"
        )

    __str__ = __repr__


def load_config(env: Optional[Mapping[str, str]] = None) -> ClaudeConfig:
    """환경 변수에서 설정을 읽는다.

    `env` 를 주면 그걸 쓰고, 없으면 `os.environ`. 테스트가 가짜 환경을 넣는다.

    값이 깨져 있으면 기본값으로 조용히 넘어가지 않고 `config` 오류를 낸다.
    잘못된 온도로 판정을 돌리는 것보다 시작할 때 멈추는 쪽이 낫다.
    """
    source: Mapping[str, str] = os.environ if env is None else env

    api_key = _read(source, ENV_API_KEY) or _read(source, ENV_API_KEY_FALLBACK)
    if not api_key:
        raise LLMError(
            KIND_CONFIG,
            f"{ENV_API_KEY} 가 비어 있다. 로컬 환경 변수에 키를 넣는다 "
            f"(대체 이름 {ENV_API_KEY_FALLBACK} 도 본다)",
        )

    model = _read(source, ENV_MODEL)
    if not model:
        raise LLMError(
            KIND_CONFIG,
            f"{ENV_MODEL} 이 비어 있다. 캠프에서 받은 정확한 모델 이름을 넣는다",
        )
    max_tokens = _read_int(source, ENV_MAX_TOKENS, DEFAULT_MAX_TOKENS)
    temperature = _read_float(source, ENV_TEMPERATURE, DEFAULT_TEMPERATURE)

    if max_tokens <= 0:
        raise LLMError(KIND_CONFIG, f"{ENV_MAX_TOKENS} 는 1 이상이어야 한다")
    if not 0.0 <= temperature <= 1.0:
        raise LLMError(KIND_CONFIG, f"{ENV_TEMPERATURE} 는 0.0~1.0 범위여야 한다")

    return ClaudeConfig(
        api_key=api_key,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
    )


def _read(source: Mapping[str, str], name: str) -> str:
    """환경 변수 하나. 없거나 공백뿐이면 빈 문자열."""
    try:
        value = source.get(name)
    except Exception:  # Mapping 이 아닌 것을 넘긴 경우
        return ""
    if value is None:
        return ""
    return str(value).strip()


def _read_int(source: Mapping[str, str], name: str, default: int) -> int:
    text = _read(source, name)
    if not text:
        return default
    try:
        return int(text)
    except ValueError:
        raise LLMError(KIND_CONFIG, f"{name} 가 정수가 아니다") from None


def _read_float(source: Mapping[str, str], name: str, default: float) -> float:
    text = _read(source, name)
    if not text:
        return default
    try:
        return float(text)
    except ValueError:
        raise LLMError(KIND_CONFIG, f"{name} 가 숫자가 아니다") from None


# ---------------------------------------------------------------------------
# 호출 인터페이스
# ---------------------------------------------------------------------------


class LLMClient(Protocol):
    """부르는 쪽이 보는 것은 이 형태뿐이다.

    어느 제공사를 쓰는지 다른 모듈이 몰라야 한다
    (연구 문서 "모델 교체에 대비하는 방법").
    """

    def complete(
        self,
        *,
        system: str,
        user: str,
        timeout: float,
        schema: Optional[dict] = None,
    ) -> str:
        ...


class ClaudeClient:
    """Claude API 호출기.

    - `anthropic` 은 **호출 시점에 지연 import** 한다. 패키지가 없어도 이 모듈을
      import 하고 테스트를 돌릴 수 있어야 한다
    - `sdk` 로 가짜 객체를 넣을 수 있다. 테스트가 이걸 쓴다. 필요한 모양은
      `sdk.messages.create(...)` 하나다
    - 여러 정책을 동시에 판정할 때 한 인스턴스를 스레드들이 공유해도 호출 자체는
      안전하다. 다만 `last_usage` 는 마지막 응답 하나만 남는다 (지표용이라 감수)
    """

    def __init__(
        self,
        config: Optional[ClaudeConfig] = None,
        *,
        sdk: Optional[object] = None,
    ) -> None:
        # 설정은 실제로 필요할 때 읽는다. 키가 없는 환경에서도 객체는 만들 수 있다.
        self._config = config
        self._sdk = sdk
        self._lock = threading.Lock()

        # 지표용. 못 꺼내면 None 으로 둔다 (docs/03-api-contract.md 13장).
        self.last_usage: Optional[Dict[str, int]] = None
        self.last_elapsed: Optional[float] = None

    # 스키마 강제는 제공사·SDK 버전마다 방식이 달라 당일 확인 후에 붙인다.
    # 지금은 `complete(schema=...)` 를 받아도 **적용하지 않는다.** 적용한 척하면
    # 부르는 쪽이 검증을 건너뛴다. 응답 형식 확인은 `schema.py` 가 한다.
    supports_schema_enforcement = False

    def __repr__(self) -> str:
        model = self._config.model if self._config is not None else "(미확정)"
        return f"ClaudeClient(model={model!r})"

    @property
    def config(self) -> ClaudeConfig:
        """설정. 처음 쓸 때 환경 변수에서 읽는다."""
        if self._config is None:
            self._config = load_config()
        return self._config

    def complete(
        self,
        *,
        system: str,
        user: str,
        timeout: float,
        schema: Optional[dict] = None,
    ) -> str:
        """한 번 호출하고 **응답 본문 문자열만** 돌려준다. 파싱하지 않는다.

        시스템 지시문과 사용자 메시지는 손대지 않고 그대로 보낸다.
        실패하면 `LLMError` 를 던진다. 재시도는 하지 않는다.
        """
        if timeout is None or timeout <= 0:
            # 남은 예산이 없으면 부르지 않는다. 불러도 어차피 버릴 응답이다.
            raise LLMError(KIND_TIMEOUT, "남은 시간이 없어 호출하지 않았다")

        sdk = self._ensure_sdk()
        started = time.monotonic()
        try:
            response = self._create(sdk, system=system, user=user, timeout=timeout)
        except LLMError:
            raise
        except Exception as exc:
            elapsed = time.monotonic() - started
            # `from None` 은 의도다. 원래 예외의 문자열·트레이스백에 프롬프트 내용이
            # 섞여 서버 로그로 흘러가는 경로를 막는다. 종류 이름만 남긴다.
            raise _classify(exc, elapsed=elapsed, timeout=timeout) from None

        elapsed = time.monotonic() - started
        self._record(response, elapsed)

        # SDK 가 timeout 을 무시하는 경우를 대비해 경과 시간도 본다.
        # 예산을 넘겨 도착한 응답은 쓰지 않는다. 쓰면 전체 20초 상한이 깨진다.
        if elapsed > timeout:
            raise LLMError(
                KIND_TIMEOUT,
                f"제한 {timeout:.1f}초를 넘겨 도착했다 ({elapsed:.1f}초)",
            )

        body = _extract_text(response).strip()
        if not body:
            raise LLMError(KIND_SCHEMA, "응답 본문이 비어 있다")
        return body

    # -- 내부 ---------------------------------------------------------------

    def _ensure_sdk(self) -> Any:
        """SDK 객체. 주입된 것이 있으면 그걸 쓴다."""
        if self._sdk is not None:
            return self._sdk

        config = self.config  # 키가 없으면 여기서 config 오류

        try:
            import anthropic  # 지연 import: 모듈 import 시점에 없어도 된다
        except ImportError:
            raise LLMError(KIND_CONFIG, INSTALL_HINT) from None

        try:
            self._sdk = anthropic.Anthropic(api_key=config.api_key)
        except Exception as exc:
            raise LLMError(
                KIND_CONFIG,
                "SDK 초기화에 실패했다",
                cause_type=type(exc).__name__,
            ) from None
        return self._sdk

    def _create(self, sdk: Any, *, system: str, user: str, timeout: float) -> Any:
        config = self.config
        kwargs: Dict[str, Any] = {
            "model": config.model,
            "max_tokens": config.max_tokens,
            "temperature": config.temperature,
            "system": system,
            "messages": [{"role": "user", "content": user}],
        }
        # 프롬프트 캐싱을 붙일 자리. 정책 원문을 반복 전송하므로 효과가 있을 수 있지만
        # 연구 문서 5장이 "있으면 좋은 것, 안 되면 그냥 안 쓴다"로 정했다. 지금은 쓰지
        # 않는다. 붙일 때는 system 을 블록 목록으로 바꿔 앞쪽 고정 부분에 캐시 표시를
        # 하고, 적용 여부를 `last_usage` 의 캐시 토큰 수로 확인한다.

        try:
            return sdk.messages.create(timeout=timeout, **kwargs)
        except TypeError as exc:
            # timeout 없는 재호출은 금지한다. SDK 가 timeout 인자를 지원하지 않으면
            # 네트워크 호출이 정책당 8초·전체 20초 상한을 무시한다. 지원 버전을
            # 고정하거나 SDK 어댑터를 고쳐야 하는 설정 오류다.
            if "timeout" in str(exc):
                raise LLMError(
                    KIND_CONFIG,
                    "설치된 Anthropic SDK가 timeout 인자를 지원하지 않는다. 지원 버전을 고정한다",
                    cause_type=type(exc).__name__,
                ) from None
            raise

    def _record(self, response: Any, elapsed: float) -> None:
        """토큰 수와 소요 시간을 남긴다. 이것 때문에 호출을 실패시키지 않는다."""
        try:
            usage = _extract_usage(response)
        except Exception:
            usage = None
        with self._lock:
            self.last_usage = usage
            self.last_elapsed = elapsed


# ---------------------------------------------------------------------------
# 응답에서 값 꺼내기
#
# SDK 객체, 딕셔너리, 가짜 객체를 모두 받아야 한다. 모양이 다르면 예외를 내지 않고
# 빈 값으로 둔다. 본문이 비면 `complete` 가 schema 오류로 만든다.
# ---------------------------------------------------------------------------


def _field(obj: Any, name: str) -> Any:
    """속성 또는 매핑 키. 없으면 None."""
    value = getattr(obj, name, None)
    if value is None and isinstance(obj, Mapping):
        value = obj.get(name)
    return value


def _extract_text(response: Any) -> str:
    """응답 본문. 텍스트 블록을 순서대로 이어 붙인다."""
    content = _field(response, "content")
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    try:
        blocks = list(content)
    except TypeError:
        return ""

    parts = []
    for block in blocks:
        if isinstance(block, str):
            parts.append(block)
            continue
        text = _field(block, "text")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


_USAGE_FIELDS = (
    "input_tokens",
    "output_tokens",
    # 캐싱을 쓰게 되면 적용 여부를 이 값으로 확인한다 (연구 문서 5장 "조용한 미적용").
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)


def _extract_usage(response: Any) -> Optional[Dict[str, int]]:
    """토큰 수. 못 꺼내면 None."""
    usage = _field(response, "usage")
    if usage is None:
        return None
    out: Dict[str, int] = {}
    for name in _USAGE_FIELDS:
        value = _field(usage, name)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            out[name] = value
    return out or None


# ---------------------------------------------------------------------------
# 오류 분류
#
# `anthropic` 없이도 분류해야 하므로 예외 타입을 import 하지 않는다.
# 상태 코드와 예외 클래스 이름으로 판단한다.
# ---------------------------------------------------------------------------

_TIMEOUT_TYPES = (TimeoutError, socket.timeout)


def _status_of(exc: BaseException) -> Optional[int]:
    """예외에서 HTTP 상태 코드를 꺼낸다. 없으면 None."""
    candidates = [
        getattr(exc, "status_code", None),
        getattr(exc, "status", None),
        getattr(exc, "http_status", None),
    ]
    response = getattr(exc, "response", None)
    if response is not None:
        candidates.append(getattr(response, "status_code", None))
        candidates.append(getattr(response, "status", None))

    for value in candidates:
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            try:
                return int(value.strip())
            except ValueError:
                continue
    return None


def _looks_like_timeout(exc: BaseException) -> bool:
    if isinstance(exc, _TIMEOUT_TYPES):
        return True
    name = type(exc).__name__
    # anthropic 은 APITimeoutError, 다른 계층은 ReadTimeout / ConnectTimeout 를 쓴다.
    return "Timeout" in name or "TimedOut" in name


def _classify(exc: BaseException, *, elapsed: float, timeout: float) -> LLMError:
    """SDK 예외를 표의 종류로 바꾼다."""
    name = type(exc).__name__
    status = _status_of(exc)

    # 시간 초과가 먼저다. 제한을 넘겼으면 다른 이유로 실패했어도 예산은 이미 없다.
    if _looks_like_timeout(exc) or elapsed > timeout:
        return LLMError(
            KIND_TIMEOUT,
            f"제한 {timeout:.1f}초 안에 끝나지 않았다 ({elapsed:.1f}초)",
            status=status,
            cause_type=name,
        )

    if status == STATUS_RATE_LIMIT or "RateLimit" in name:
        return LLMError(
            KIND_RATE_LIMIT,
            "속도 제한에 걸렸다. 재시도하지 않고 동시 요청 수를 줄인다",
            status=status,
            cause_type=name,
        )

    if status == STATUS_OVERLOADED or "Overload" in name:
        return LLMError(
            KIND_OVERLOADED,
            "모델 용량 부족. 재시도로 풀리지 않는다. 데모 모드로 넘길 신호다",
            status=status,
            cause_type=name,
        )

    if status in STATUS_AUTH or "Authentication" in name or "PermissionDenied" in name:
        return LLMError(
            KIND_AUTH,
            "인증·권한 문제다. 키 설정을 확인한다",
            status=status,
            cause_type=name,
        )

    if status is not None and 500 <= status <= 599:
        return LLMError(
            KIND_SERVER,
            "서버 오류다. 부르는 쪽이 1회까지 다시 시도할 수 있다",
            status=status,
            cause_type=name,
        )

    if "InternalServer" in name:
        return LLMError(
            KIND_SERVER,
            "서버 오류다. 부르는 쪽이 1회까지 다시 시도할 수 있다",
            status=status,
            cause_type=name,
        )

    return LLMError(
        KIND_UNKNOWN,
        "분류하지 못한 실패다. 다시 부르지 않는다",
        status=status,
        cause_type=name,
    )
