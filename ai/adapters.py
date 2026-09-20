"""AI A 의 모델 호출 경계 — AI B 의 ``ai/judgment/client.py`` 를 ``llm.LlmAdapter`` 로 감싼다.

서버는 턴이 시작될 때 ``adapter_from_env()`` 로 어댑터를 한 번 만들고(키가 없으면 ``None``),
그것을 ``llm.Gateway(adapter=...)`` 에 넣어 해석(3단계)·판정(7단계)·답변(10단계)에서 공유한다.
``Gateway`` 와 그 안의 ``BudgetTracker`` 는 **턴마다 새로 만든다.** 재사용하면 두 번째 턴이
시작부터 "예산 없음"이 되어 모델을 조용히 건너뛴다(``ai/conversation/llm.py`` 와
``ai/conversation/pipeline.py`` 가 그 이유를 적어 두었다). 어댑터 자체는 상태가 없어 재사용해도 된다.

이 파일이 하는 일과 하지 않는 일
--------------------------------
하는 일은 둘뿐이다. **한 번 호출하고, 실패를 ``llm`` 예외 타입으로 번역해 올린다.**

**재시도·타임아웃 강제·예산 관리를 여기서 하지 않는다.** ``llm.Gateway`` 와
``llm.CallSitePolicy`` 가 이미 한다(형식 오류 1회 재시도, 남은 예산으로 타임아웃을 깎기,
실패를 폴백으로 흡수). 어댑터가 한 번 더 재시도하면 전체 20초 예산이 **오류 없이** 두 배로
샌다. 그 어긋남은 화면에서 "AI 가 느리다"로만 보이고 원인을 찾기 어렵다. 그래서 이 파일에는
``for attempt in ...`` 도, ``time.sleep`` 도 없다.

SDK 호출·오류 분류·키 로딩을 다시 만들지 않는 이유도 같다. ``ai/judgment/client.py`` 에
이미 있고(지연 import, 상태 코드 분류, ``timeout`` 강제, ``from None`` 으로 프롬프트 유출 차단),
두 곳에 생기면 한쪽만 고쳐진다. 이 파일은 그 클라이언트의 ``complete()`` 를 부르는 얇은 층이다.

기록 금지
---------
API 키와 프롬프트 본문은 예외 메시지에 넣지 않는다. 원래 예외의 문자열도 쓰지 않고
종류 이름(``kind``)과 상태 코드만 남긴다. 이유는 ``server/ai_gateway.py`` 가 SDK 예외를
``from None`` 으로 끊는 것과 같다. 제공사 예외 메시지에는 요청 본문이 실려 나올 수 있다.
"""

from __future__ import annotations

import json
import os
from dataclasses import replace
from typing import Any, Dict, Iterator, List, Mapping, Optional, Tuple, Type

from ai.conversation import answer as conversation_answer
from ai.conversation import llm
from ai.judgment import client as judgment_client

# ---------------------------------------------------------------------------
# 1. 오류 번역표 (분기를 흩뿌리지 않는다)
# ---------------------------------------------------------------------------
#
# AI B 의 `LLMError.kind` 를 AI A 의 `llm` 예외 계층으로 옮긴다. 이 표가 틀리면
# `CallSitePolicy.should_retry` 가 재시도하면 안 되는 것을 재시도해 20초를 태운다.
# `llm.CALL_SITE_POLICIES` 의 `retry_on` 은 해석·판정에서 `ModelFormatError` 하나뿐이고
# 답변에서는 비어 있다. 따라서 **`ModelFormatError` 로 옮기는 kind 만 재시도 대상**이 된다.
#
# | LLMError.kind | llm 예외 | 재시도(해석·판정) | 근거 |
# | --- | --- | --- | --- |
# | `schema` | `ModelFormatError` | O (1회) | 같은 입력에 형식만 틀리는 경우가 있다. `LLMError.retryable` 도 True |
# | `timeout` | `ModelTimeoutError` | X | 다시 부르면 같은 시간이 또 든다 |
# | `rate_limit` | `ModelRateLimitError` | X | 요청 수를 줄여야 풀린다. 기다리는 것이 예산보다 길다 |
# | `overloaded` | `ModelCapacityError` | X | 언제 풀리는지 알 수 없다. 데모 모드로 넘길 신호 |
# | `server` | `ModelServerError` | X | 같은 입력이 바뀔 근거가 약하다 |
# | `auth` | `ModelServerError` | X | **키가 틀린 것이다. 재시도는 무조건 낭비** |
# | `config` | `ModelServerError` | X | 설정·SDK 누락. 다시 불러도 같다 |
# | `unknown` | `ModelServerError` | X | 원인을 모르는 채 예산을 쓰지 않는다 |
#
# `auth`·`config` 를 `ModelServerError` 로 보낸 이유: `llm` 에는 설정 오류용 타입이 없고,
# 남은 후보 중 `ModelFormatError`(재시도됨)는 절대 안 되며, `ModelRateLimitError`(요청을
# 줄이라는 뜻)와 `ModelCapacityError`(데모 모드로 넘기라는 신호)는 대응 지시가 틀리다.
# `ModelServerError` 는 "그 밖의 오류"로 정의돼 있고 어느 `retry_on` 에도 없어 안전하다.
# 대가는 지표에서 `server_error` 로 뭉쳐 보이는 것이다. 키 설정 문제를 지표로 구분하려면
# `llm` 에 `ModelConfigError` 를 더해야 하는데 그건 AI A 의 `llm.py` 를 고치는 일이라
# 여기서 하지 않는다. 대신 `adapter_from_env` 가 키·모델 누락을 **부르기 전에** 걸러낸다.
#
# 모르는 종류(`unknown`)는 **재시도하지 않는 쪽**을 골랐다. 두 실수의 값이 다르다.
# 재시도하지 않아 놓친 성공은 그 조건이 "미확인"으로 표시되고 후속 질문으로 이어진다.
# 반대로 잘못 재시도하면 20초 상한을 넘겨 화면이 멈추고, 그 턴 전체가 사라진다.
# `llm.py` 도 매핑되지 않은 어댑터 예외를 `server_error` 로 흡수하니 방향이 같다.

ERROR_TRANSLATION: Mapping[str, Type[llm.ModelCallError]] = {
    judgment_client.KIND_SCHEMA: llm.ModelFormatError,
    judgment_client.KIND_TIMEOUT: llm.ModelTimeoutError,
    judgment_client.KIND_RATE_LIMIT: llm.ModelRateLimitError,
    judgment_client.KIND_OVERLOADED: llm.ModelCapacityError,
    judgment_client.KIND_SERVER: llm.ModelServerError,
    judgment_client.KIND_AUTH: llm.ModelServerError,
    judgment_client.KIND_CONFIG: llm.ModelServerError,
    judgment_client.KIND_UNKNOWN: llm.ModelServerError,
}
"""``LLMError.kind`` → ``llm`` 예외 타입. 표는 여기 한 곳에만 둔다."""

#: 표에 없는 종류가 왔을 때 쓰는 타입. 어느 ``retry_on`` 에도 없어 재시도되지 않는다.
DEFAULT_TRANSLATION: Type[llm.ModelCallError] = llm.ModelServerError


def translate_error(error: judgment_client.LLMError) -> llm.ModelCallError:
    """``LLMError`` 를 ``llm`` 예외로 바꾼다.

    메시지는 **우리가 새로 만든다.** 원래 예외의 문자열을 옮기지 않는 이유는, 그 안에
    프롬프트 조각이나 설정 값이 섞여 서버 로그로 흘러갈 수 있기 때문이다. 남기는 것은
    종류 이름과 HTTP 상태 코드뿐이고, 둘 다 사용자 입력이 아니다.
    """
    kind = getattr(error, "kind", judgment_client.KIND_UNKNOWN)
    target = ERROR_TRANSLATION.get(kind, DEFAULT_TRANSLATION)
    return target(_safe_message(kind, status=getattr(error, "status", None)))


def _safe_message(kind: str, *, status: Optional[int] = None) -> str:
    """예외 메시지. 키도 프롬프트도 담기지 않는 형태로만 만든다."""
    text = f"모델 호출 실패 (kind={kind})"
    if isinstance(status, int) and not isinstance(status, bool):
        text = f"{text} status={status}"
    return text


# ---------------------------------------------------------------------------
# 2. 설정 (환경 변수 이름이 갈라져 있다)
# ---------------------------------------------------------------------------
#
# AI B(`ai/judgment/client.py`)와 AI A(`ai/conversation/llm.py`)가 서로 다른 이름을 본다.
# 한 곳에만 키를 넣으면 다른 쪽이 조용히 스텁으로 돈다. 그래서 **둘 다 읽는 것을 정본**으로
# 삼고, AI B 의 이름을 먼저 본다(실제 호출 코드가 그 이름을 쓴다). 새 이름은 만들지 않는다.
#
# | 무엇 | 보는 순서 | 비면 |
# | --- | --- | --- |
# | API 키 | `CLAUDE_API_KEY` → `ANTHROPIC_API_KEY` → `KMUCT_LLM_API_KEY` | 어댑터 없음(`None`) |
# | 모델 이름 | `CLAUDE_MODEL` → `KMUCT_LLM_MODEL` | 어댑터 없음(`None`) |
# | 최대 토큰 | `CLAUDE_MAX_TOKENS` | `client.py` 기본값 |
# | 온도 | `CLAUDE_TEMPERATURE` | `llm.TEMPERATURE_STRUCTURED` / 답변은 `llm.TEMPERATURE_ANSWER` |
#
# 모델 이름에 기본값을 두지 않는다. `ClaudeConfig.DEFAULT_MODEL` 도 `None` 이다. 이름을
# 지어내면 런타임 404 가 나고, 그 실패는 데모 중에야 보인다. 캠프에서 받은 정확한 이름을
# 환경 변수로 넣는 것이 의도된 설계다.
#
# 한계 (보고용): `llm.ENV_MODEL_BY_SITE` 의 지점별 모델 이름
# (`KMUCT_LLM_MODEL_INTERPRET` 등)은 여기서 반영되지 않는다. 모델 이름은 `ClaudeConfig` 에
# 들어 있는 **클라이언트 단위** 설정이고 `LLMClient.complete` 에는 모델 인자가 없다.
# 지점별로 다른 모델을 쓰려면 클라이언트를 지점마다 만들어야 하는데, 그러면 이 파일이
# 호출 지점 표를 다시 아는 셈이 되어 경계가 흐려진다.

#: API 키를 보는 순서
API_KEY_ENV_ORDER: Tuple[str, ...] = (
    judgment_client.ENV_API_KEY,
    judgment_client.ENV_API_KEY_FALLBACK,
    llm.ENV_API_KEY,
)

#: 모델 이름을 보는 순서
MODEL_ENV_ORDER: Tuple[str, ...] = (
    judgment_client.ENV_MODEL,
    llm.ENV_MODEL,
)


def _read(source: Mapping[str, str], name: str) -> str:
    """환경 변수 하나. 없거나 공백뿐이면 빈 문자열. 값은 돌려주되 로그에 남기지 않는다."""
    try:
        value = source.get(name)
    except Exception:  # Mapping 이 아닌 것을 받은 경우
        return ""
    if value is None:
        return ""
    return str(value).strip()


def _first(source: Mapping[str, str], names: Tuple[str, ...]) -> str:
    for name in names:
        value = _read(source, name)
        if value:
            return value
    return ""


def missing_settings(env: Optional[Mapping[str, str]] = None) -> Tuple[str, ...]:
    """모델을 부르기 위해 아직 없는 설정의 **이름만** 돌려준다.

    ``adapter_from_env`` 가 ``None`` 을 돌려줬을 때 "왜"가 보여야 한다. 그렇지 않으면
    키를 넣었다고 생각하는 사람과 스텁으로 도는 서버가 만난다. 값은 절대 돌려주지 않고
    변수 이름만 낸다(``llm.api_key_present`` 와 같은 태도).
    """
    source = os.environ if env is None else env
    missing: List[str] = []
    if not _first(source, API_KEY_ENV_ORDER):
        missing.append(judgment_client.ENV_API_KEY)
    if not _first(source, MODEL_ENV_ORDER):
        missing.append(judgment_client.ENV_MODEL)
    return tuple(missing)


# ---------------------------------------------------------------------------
# 3. 어댑터
# ---------------------------------------------------------------------------


class ClaudeAdapter:
    """``llm.LlmAdapter`` 구현체. 실제 호출은 ``ai.judgment.client`` 가 한다.

    생성자가 ``LLMClient`` 를 받는 것은 테스트 주입 구멍이기 이전에 **AI A 와 AI B 가 같은
    모델 클라이언트를 공유하는 통로**다. AI B 의 ``ExceptionJudge`` 는 ``LLMClient`` 를 받아
    쓰고, 같은 객체를 이 어댑터에 넣으면 해석·판정·답변이 한 클라이언트를 쓴다. 그래야
    모델 이름·키·토큰 한도가 한 곳에서 정해지고, 지표(``last_usage``)도 한 군데 모인다.
    두 개를 따로 만들면 한쪽 모델만 바뀌어도 아무 오류 없이 서로 다른 모델로 돌아간다.

    ``answer_client`` 는 답변용으로만 쓰는 선택 인자다. 온도가 호출 인자가 아니라
    ``ClaudeConfig`` 항목이라 답변 온도(``llm.TEMPERATURE_ANSWER``)를 쓰려면 설정이 다른
    클라이언트가 하나 더 필요하다(아래 ``stream_text`` 참고). 주지 않으면 ``client`` 를 쓴다.

    상태가 없으므로 턴 사이에 재사용해도 된다. 턴마다 새로 만들어야 하는 것은
    ``llm.Gateway`` 쪽이다.
    """

    def __init__(
        self,
        client: Optional[judgment_client.LLMClient] = None,
        *,
        answer_client: Optional[judgment_client.LLMClient] = None,
    ) -> None:
        # 기본값을 여기서 만들어도 환경 변수를 읽지 않는다. `ClaudeClient` 는 설정을
        # 실제 호출 시점에 읽는다. 키 없는 환경에서도 객체 생성은 성공해야 한다.
        self._client: judgment_client.LLMClient = (
            client if client is not None else judgment_client.ClaudeClient()
        )
        self._answer_client: judgment_client.LLMClient = (
            answer_client if answer_client is not None else self._client
        )

    def __repr__(self) -> str:
        # 키도 모델 이름도 여기서 찍지 않는다. 클라이언트의 repr 은 그 클래스가 책임진다.
        return f"ClaudeAdapter(client={type(self._client).__name__})"

    @property
    def client(self) -> judgment_client.LLMClient:
        """구조화 호출(해석·판정)에 쓰는 클라이언트. AI B 와 공유하는 그 객체다."""
        return self._client

    @property
    def answer_client(self) -> judgment_client.LLMClient:
        """답변 호출에 쓰는 클라이언트. 따로 주지 않았으면 ``client`` 와 같은 객체다."""
        return self._answer_client

    # -- 구조화 출력 (해석 3단계, 판정 7단계) ------------------------------

    def complete_structured(self, request: llm.StructuredRequest) -> llm.StructuredResponse:
        """스키마에 맞는 값을 한 번에 돌려준다.

        ``StructuredRequest`` 의 항목을 이렇게 넘긴다.

        | 요청 항목 | 어디로 | 비고 |
        | --- | --- | --- |
        | ``system`` | ``complete(system=...)`` | 손대지 않는다 |
        | ``user`` | ``complete(user=...)`` | ``assemble_prompt`` 결과 그대로 |
        | ``timeout_s`` | ``complete(timeout=...)`` | 게이트웨이가 남은 예산으로 이미 깎아 준 값 |
        | ``schema`` | ``complete(schema=...)`` | 그대로 전달. 아래 주의 |
        | ``temperature`` | 전달 못 함 | 온도는 ``ClaudeConfig`` 항목이다 (아래) |
        | ``model`` | 전달 못 함 | 모델 이름도 ``ClaudeConfig`` 항목이다 |
        | ``prefer_cache`` | 전달 못 함 | ``client.py`` 가 캐시 표시를 아직 붙이지 않는다 |

        ``schema`` 를 넘기지만 ``ClaudeClient.supports_schema_enforcement`` 는 ``False`` 다.
        즉 **형식이 보장되지 않는다.** 그래서 응답 문자열을 여기서 JSON 으로 읽고, 실패하면
        ``ModelFormatError`` 를 던진다. 그 하나만 게이트웨이의 재시도 대상이라 "설명 문장이
        앞에 붙어 온 한 번"이 두 번째 시도에서 살아난다. 파싱을 건너뛰고 문자열을 그대로
        올리면 ``StructuredResponse.data`` 가 ``Mapping`` 이라는 약속이 깨진다.

        온도는 ``llm.TEMPERATURE_STRUCTURED`` 를 쓴다. 숫자를 여기 다시 적지 않는다.
        다만 ``LLMClient.complete`` 에 온도 인자가 없어 **호출마다 바꿀 수 없다.** 온도는
        ``ClaudeConfig.temperature``(``CLAUDE_TEMPERATURE``)로 정해지고, 그 값을
        ``adapter_from_env`` 가 ``llm.TEMPERATURE_STRUCTURED`` 에서 가져와 넣는다. 설정을
        호출 인자로 끌어올리는 것은 ``client.py`` 를 고치는 일이라 여기서 하지 않는다.
        구조화 호출과 ``client.py`` 의 기본값이 둘 다 0.0 이라 지금은 어긋나지 않지만,
        한쪽이 바뀌면 조용히 갈라진다. 그래서 두 값을 테스트에서 맞춰 본다.
        """
        body = self._complete(
            self._client,
            system=request.system,
            user=request.user,
            timeout_s=request.timeout_s,
            schema=request.schema,
        )
        return llm.StructuredResponse(data=_parse_structured(body, request.schema), cached=None)
        # cached 를 None 으로 둔 이유:
        #   1. `client.py` 는 프롬프트 캐시 표시를 아직 붙이지 않는다(거기 TODO 로 남아 있다).
        #      그러니 캐시 토큰 수는 항상 0 이고, 그걸 읽어 False 를 채우면 "미적용"과
        #      "시도하지 않음"을 구분할 수 없게 만든다. 후자가 사실이다.
        #   2. 토큰 수는 `ClaudeClient.last_usage` 에 있지만 그것은 **마지막 호출** 값이다.
        #      판정은 후보별로 `run_parallel` 로 동시에 부르며 클라이언트를 공유하므로,
        #      그 값을 이 호출의 결과라고 적으면 다른 호출의 숫자를 섞어 기록한다.
        #      틀린 지표는 없는 지표보다 나쁘다. `llm.py` 도 모르면 None 이라고 적어 두었다.

    # -- 답변 스트리밍 (10단계) -------------------------------------------

    def stream_text(self, request: llm.TextRequest) -> Iterator[str]:
        """답변 조각을 순서대로 내놓는다.

        **(나) 한 번에 받아 문장 단위로 흘리는 길을 골랐다.** (가) Anthropic 스트리밍 API 를
        직접 쓰는 길을 버린 이유:

          - ``ai/judgment/client.py`` 의 ``complete`` 는 스트리밍이 아니다. 스트리밍은
            ``messages.create(..., stream=True)`` 또는 ``messages.stream()`` 컨텍스트로
            **호출 모양이 다르고**(Anthropic 스트리밍 문서: 서버 전송 이벤트로 델타를 받는다,
            https://docs.anthropic.com/en/api/streaming), 이벤트 종류별 처리가 따로 필요하다.
            그걸 여기서 하면 SDK 호출 지점이 ``client.py`` 와 이 파일 두 곳이 된다. 지연
            import, 상태 코드 분류, ``timeout`` 강제, 프롬프트 유출 차단을 두 번 구현하게 되고
            모델을 바꿀 때 한쪽만 고쳐진다. 그 비용이 이 결정의 핵심이다.
          - 체감 대기: (가)는 첫 글자가 수백 ms 에 나오고 (나)는 답변이 다 만들어진 뒤 나온다.
            답변은 몇 문장짜리라 보통 2~4초이고, 답변 호출은 20초 예산의 10단계에서 시작한다.
            즉 (나)의 손해는 "먼저 한 문장을 보며 기다리는" 경험이지 상한 초과가 아니다.
            게이트웨이가 첫 조각 타임아웃(3초)을 재고 있으므로 늦으면 실패로 흡수된다.
          - 쓰는 쪽에서 보면 모양이 같다. 서버는 조각마다 ``answer_delta`` 를 보내면 되고
            (``docs/03-api-contract.md`` 5장) 이벤트 순서와 개수도 문장 단위로 유지된다.

        (가)로 가려면 ``client.py`` 에 스트리밍 메서드를 **AI B 가** 더하고 이 어댑터가 그걸
        부르는 것이 맞다. 그때 이 메서드만 바꾸면 되고 부르는 쪽은 그대로다.

        공백 조각을 흘리지 않으면서 단어가 붙지 않게 하는 방법
        ----------------------------------------------------
        서버의 ``AnswerDeltaEventData.delta`` 는 ``min_length=1`` 이고 ``ContractModel`` 이
        공백을 잘라내므로(``server/sse.py``) 공백만 있는 조각 하나가 스트림 중간에
        ValidationError 를 낸다. 반대로 문장에서 공백을 다 없애면 "첫 문장." + "둘째 문장."
        이 붙어 읽힌다. 그래서 **조각을 공백으로 만들지 않고, 문장 뒤에 한 칸을 붙여** 보낸다
        (마지막 문장은 그대로). 조각은 항상 글자로 시작하니 ``min_length`` 를 만족하고,
        이어 붙이면 문장 사이에 칸이 남는다. 서버가 조각별로 공백을 잘라내는 것은
        ``server/orchestrator_adapters.py`` 가 이미 하는 일과 같아서 새 동작이 아니다.

        문장 분리는 ``ai/conversation/answer.split_sentences`` 를 쓴다. 정규식을 새로 쓰지
        않는다. 그 함수는 "3.5%" 와 "2026. 3. 1." 에서 자르지 않게 이미 손질돼 있다.

        온도는 ``llm.TEMPERATURE_ANSWER`` 다. ``complete_structured`` 와 같은 이유로 호출
        인자로 못 넘기고 클라이언트 설정으로 들어간다. ``adapter_from_env`` 가 답변용
        클라이언트를 그 온도로 따로 만들어 주며, 하나만 주면 구조화 온도로 돈다(보수적인 쪽).
        """
        body = self._complete(
            self._answer_client,
            system=request.system,
            user=request.user,
            timeout_s=request.timeout_s,
            schema=None,
        )
        return iter(answer_deltas(body))

    # -- 내부 -------------------------------------------------------------

    def _complete(
        self,
        client: judgment_client.LLMClient,
        *,
        system: str,
        user: str,
        timeout_s: float,
        schema: Optional[Mapping[str, Any]],
    ) -> str:
        """한 번 부른다. 재시도는 ``llm.Gateway`` 의 일이다.

        **어떤 입력에도 ``llm.ModelCallError`` 계층 밖의 예외를 올리지 않는다.**
        ``llm.Gateway`` 가 넓은 ``except`` 로 흡수해 주지만, 그 자리에 걸리면 사유 코드가
        전부 ``server_error`` 로 뭉개져 무엇이 실패했는지 지표에서 사라진다.
        """
        try:
            body = client.complete(
                system=system,
                user=user,
                timeout=timeout_s,
                schema=dict(schema) if schema is not None else None,
            )
        except judgment_client.LLMError as error:
            raise translate_error(error) from None
        except llm.ModelCallError:
            # 이미 우리 계층이면 그대로 올린다 (가짜 클라이언트가 그렇게 던질 수 있다).
            raise
        except Exception as exc:  # noqa: BLE001 - 제공사·주입 객체의 모든 실패를 번역한다
            # 원래 예외를 연결하지 않는다(`from None`). 메시지에 프롬프트가 섞여 있을 수 있다.
            raise llm.ModelServerError(
                _safe_message(judgment_client.KIND_UNKNOWN)
            ) from None

        if not isinstance(body, str) or not body.strip():
            # `client.complete` 는 빈 본문을 schema 오류로 만들지만, 주입된 클라이언트가
            # 다른 것을 돌려줄 수 있다. 형식 문제이므로 재시도 대상으로 둔다.
            raise llm.ModelFormatError(_safe_message(judgment_client.KIND_SCHEMA))
        return body


# ---------------------------------------------------------------------------
# 4. 응답 다루기
# ---------------------------------------------------------------------------

#: 문장 사이에 넣는 칸. 조각을 공백만으로 만들지 않기 위해 **문장 뒤에 붙인다.**
SENTENCE_GAP = " "


def answer_deltas(text: str) -> Tuple[str, ...]:
    """답변 본문을 스트리밍 조각으로 나눈다. 공백만 있는 조각은 내지 않는다."""
    pieces = tuple(
        stripped for piece in conversation_answer.split_sentences(text or "") if (stripped := piece.strip())
    )
    if not pieces:
        # 종결 부호가 없는 한 줄 등으로 분리가 빈 결과를 낼 수 있다. 버리면 답변이 사라진다.
        whole = (text or "").strip()
        return (whole,) if whole else ()
    last = len(pieces) - 1
    return tuple(piece if index == last else piece + SENTENCE_GAP for index, piece in enumerate(pieces))


def _parse_structured(body: str, schema: Optional[Mapping[str, Any]]) -> Mapping[str, Any]:
    """응답 문자열에서 JSON 을 읽는다. 실패는 모두 ``ModelFormatError``."""
    value = _first_json_value(body)

    if isinstance(value, Mapping):
        return dict(value)

    if isinstance(value, list):
        # 모델이 최상위를 배열로 내는 경우가 있다(판정 조건 목록이 그렇다). 키 이름을
        # 지어내지 않고 **요청 스키마가 요구하는 유일한 항목 이름**으로만 감싼다.
        key = _sole_required_property(schema)
        if key:
            return {key: list(value)}

    raise llm.ModelFormatError(_safe_message(judgment_client.KIND_SCHEMA))


def _sole_required_property(schema: Optional[Mapping[str, Any]]) -> Optional[str]:
    """스키마가 최상위 필수 항목 하나만 요구하면 그 이름. 아니면 None."""
    if not isinstance(schema, Mapping):
        return None
    required = schema.get("required")
    if isinstance(required, (list, tuple)) and len(required) == 1:
        name = required[0]
        if isinstance(name, str) and name:
            return name
    return None


def _first_json_value(body: str) -> Any:
    """문자열에서 첫 JSON 값을 읽는다. 코드펜스와 앞뒤 설명 문장을 견딘다.

    스키마 강제가 걸리지 않은 경로에서는 ```` ```json ```` 로 감싸거나 설명 문장을 덧붙인
    출력이 그대로 온다(``ai/judgment/schema.py`` 가 같은 사정을 적어 두었다). 전체를 버리면
    쓸 수 있는 응답까지 잃는다.
    """
    text = (body or "").strip()
    if text.startswith("```"):
        newline = text.find("\n")
        text = text[newline + 1 :] if newline != -1 else ""
        fence = text.rfind("```")
        if fence != -1:
            text = text[:fence]
        text = text.strip()

    try:
        return json.loads(text)
    except ValueError:
        pass

    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char not in "{[":
            continue
        try:
            value, _ = decoder.raw_decode(text[index:])
        except ValueError:
            continue
        return value

    raise llm.ModelFormatError(_safe_message(judgment_client.KIND_SCHEMA))


# ---------------------------------------------------------------------------
# 5. 팩토리
# ---------------------------------------------------------------------------


def adapter_from_env(env: Optional[Mapping[str, str]] = None) -> Optional[llm.LlmAdapter]:
    """설정에서 어댑터를 만든다. 만들 수 없으면 ``None``. **예외를 던지지 않는다.**

    키나 모델 이름이 없으면 ``None`` 을 돌려준다. 그러면 ``llm.Gateway`` 가 모델을 부르지
    않고 ``REASON_NO_ADAPTER`` 로 폴백하며, 규칙 기반 카드는 화면에 그대로 나간다.
    예외를 던지면 키 없는 개발 환경에서 서버가 아예 뜨지 않는다. 그건 데모 전날 가장 비싼
    실패다. 무엇이 없어서 ``None`` 인지는 ``missing_settings`` 로 본다.

    환경 변수는 AI B 이름을 먼저 본다(``API_KEY_ENV_ORDER``, ``MODEL_ENV_ORDER``).
    실제 검증(정수·범위)은 ``client.load_config`` 에 맡긴다. 그쪽 규칙을 여기서 다시 쓰면
    한쪽만 고쳐진다.
    """
    source = os.environ if env is None else env
    try:
        if missing_settings(source):
            return None

        # AI B 의 로더를 그대로 쓰기 위해, 어느 이름으로 들어왔든 그쪽 이름으로 옮겨 담는다.
        merged: Dict[str, str] = {
            judgment_client.ENV_API_KEY: _first(source, API_KEY_ENV_ORDER),
            judgment_client.ENV_MODEL: _first(source, MODEL_ENV_ORDER),
        }
        explicit_temperature = _read(source, judgment_client.ENV_TEMPERATURE)
        max_tokens = _read(source, judgment_client.ENV_MAX_TOKENS)
        if max_tokens:
            merged[judgment_client.ENV_MAX_TOKENS] = max_tokens
        if explicit_temperature:
            merged[judgment_client.ENV_TEMPERATURE] = explicit_temperature

        config = judgment_client.load_config(merged)
        if not explicit_temperature:
            # 온도 숫자는 `llm` 에서 가져온다. 여기 적지 않는다. 운영자가
            # `CLAUDE_TEMPERATURE` 를 직접 넣었으면 그 뜻을 존중해 양쪽에 그대로 쓴다.
            structured_config = replace(config, temperature=llm.TEMPERATURE_STRUCTURED)
            answer_config = replace(config, temperature=llm.TEMPERATURE_ANSWER)
        else:
            structured_config = config
            answer_config = config

        return ClaudeAdapter(
            judgment_client.ClaudeClient(structured_config),
            # 답변만 온도가 다르다. 설정이 클라이언트 단위라 클라이언트를 하나 더 둔다.
            # 같은 설정이면 나눌 이유가 없다.
            answer_client=(
                judgment_client.ClaudeClient(answer_config)
                if answer_config is not structured_config
                else None
            ),
        )
    except judgment_client.LLMError:
        # 설정이 깨진 경우다(정수 아님, 범위 밖 등). 서버를 세우지 않는다.
        return None
    except Exception:  # noqa: BLE001 - 팩토리는 어떤 경우에도 예외를 올리지 않는다
        return None


__all__ = [
    "API_KEY_ENV_ORDER",
    "ClaudeAdapter",
    "DEFAULT_TRANSLATION",
    "ERROR_TRANSLATION",
    "MODEL_ENV_ORDER",
    "SENTENCE_GAP",
    "adapter_from_env",
    "answer_deltas",
    "missing_settings",
    "translate_error",
]
