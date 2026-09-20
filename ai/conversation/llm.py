"""모델 호출 지점 단일화 (docs/research/04-llm-api-operations.md "모델 교체에 대비하는 방법").

우리가 모델을 부르는 곳은 세 곳뿐이다. 메시지 해석, 예외 조건 판정, 답변 작성.
그 셋의 시간 예산·출력 형태·실패 처리·재시도·프롬프트 조립 순서를 이 파일에만 둔다.
흩어 두면 당일 모델 교체가 "교체"가 아니라 "재작성"이 된다.

Claude 를 붙일 때 무엇만 고치면 되는가
--------------------------------------
1. `LlmAdapter` 프로토콜을 구현하는 어댑터 클래스 하나를 새로 만든다
   (`complete_structured` 와 `stream_text` 두 메서드).
2. 그 어댑터 안에서 제공사 예외를 이 파일의 `ModelFormatError` / `ModelTimeoutError` /
   `ModelRateLimitError` / `ModelCapacityError` / `ModelServerError` 로 옮겨 던진다. 속도 제한과
   용량 부족을 반드시 나눠 매핑한다 (전자는 요청을 줄이면 풀리고 후자는 재시도가 무의미하다).
3. 모델 이름은 환경 변수(`ENV_MODEL`, `ENV_MODEL_BY_SITE`)로 넣는다. 코드에 박지 않는다.
4. `Gateway(adapter=...)` 의 어댑터만 `StubAdapter` 에서 바꿔 끼운다. 호출하는 쪽(해석·판정·답변)
   코드는 건드리지 않는다.
5. 예산·재시도·폴백 값을 바꿀 일이 있으면 `CALL_SITE_POLICIES` 표 한 곳만 고친다.

이 파일이 하지 않는 것
----------------------
실제 네트워크 호출, 제공사 SDK 사용, 프롬프트 문안 완성. 표준 라이브러리만 쓴다.
외부 패키지를 미리 박으면 10:30 에 어느 모델을 받아도 교체 비용이 생긴다.
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from typing import (
    Any,
    Callable,
    Dict,
    FrozenSet,
    Generic,
    Iterable,
    Iterator,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
    Type,
    TypeVar,
    runtime_checkable,
)

T = TypeVar("T")

# ---------------------------------------------------------------------------
# 설정 (모델 이름을 코드에 박지 않는다)
# ---------------------------------------------------------------------------

# 값이 아니라 **이름만** 상수로 둔다. 키 값은 읽어도 로그·반환값에 남기지 않는다.
ENV_MODEL = "KMUCT_LLM_MODEL"
ENV_API_KEY = "KMUCT_LLM_API_KEY"
ENV_MAX_CONCURRENCY = "KMUCT_LLM_MAX_CONCURRENCY"


class CallSite(str, Enum):
    """모델을 부르는 세 지점 (research 4장 "호출 지점별 방침")."""

    INTERPRET = "interpret"  # 메시지 해석 (AI A)
    JUDGE = "judge"  # 예외 조건 판정 (AI B)
    ANSWER = "answer"  # 답변 작성 (AI A)


# 호출 지점마다 다른 모델을 쓸 수도 있다(판정은 큰 모델, 답변은 빠른 모델 등).
# 지점별 변수가 비어 있으면 ENV_MODEL 로 떨어진다.
ENV_MODEL_BY_SITE: Mapping[CallSite, str] = {
    CallSite.INTERPRET: "KMUCT_LLM_MODEL_INTERPRET",
    CallSite.JUDGE: "KMUCT_LLM_MODEL_JUDGE",
    CallSite.ANSWER: "KMUCT_LLM_MODEL_ANSWER",
}

# 전체 상한 (docs/03-api-contract.md 9장 13단계)
TOTAL_BUDGET_S = 20.0

# 규칙 재계산·인용 검증·후속 질문 선택 등 모델이 아닌 단계에 남겨 두는 여유.
# 모델 호출로 20초를 꽉 채우면 done 이벤트가 상한을 넘는다.
RESERVED_NON_MODEL_S = 1.5

# 이보다 적게 남았으면 부르지 않는다. 불러도 첫 조각조차 못 받는다.
MIN_CALL_ROOM_S = 0.3

# 동시 실행 상한 (research 4장). 후보 수만큼 무조건 늘리면 분당 요청·토큰 한도에 걸린다.
DEFAULT_MAX_CONCURRENCY = 3

# 판정 온도는 낮게. 완전한 재현성은 보장되지 않는다 (research 7장).
TEMPERATURE_STRUCTURED = 0.0
# 답변은 판정을 바꾸지 않지만 금지 표현이 섞이는 것을 막으려면 높게 두지 않는다 (research 7장).
TEMPERATURE_ANSWER = 0.3


# ---------------------------------------------------------------------------
# 실패 종류 (research 3장 "시간 제한과 재시도")
# ---------------------------------------------------------------------------


class ModelCallError(Exception):
    """모델 호출 실패의 공통 조상.

    어댑터는 제공사 예외를 **반드시 이 계층으로 옮겨** 던진다. 그래야 실패 규칙이
    제공사와 무관해지고, 모델을 바꿀 때 재시도·폴백 코드를 다시 쓰지 않는다.
    """

    #: 지표·이벤트에 쓰는 짧은 사유 코드
    reason = "model_error"


class ModelFormatError(ModelCallError):
    """출력 형식이 깨졌다. 스키마 위반, 파싱 실패.

    **재시도 가치가 있는 유일한 실패다.** 같은 입력에 형식만 틀리는 경우가 있다.
    """

    reason = "format_error"


class ModelTimeoutError(ModelCallError):
    """시간 초과. 재시도하지 않는다.

    재시도하면 같은 시간이 또 든다. 20초 상한을 넘기는 대신 미확인으로 흡수한다.
    """

    reason = "timeout"


class ModelRateLimitError(ModelCallError):
    """속도 제한(분당 요청/토큰 초과).

    **우리가 요청을 줄이면 풀린다.** 대기 시간이 우리 예산보다 길므로 기다리지 않고
    동시 실행 수와 후보 수를 줄이는 쪽으로 대응한다 (research 4장).
    """

    reason = "rate_limit"


class ModelCapacityError(ModelCallError):
    """용량 부족·과부하.

    계정 사용량과 무관한 환경 문제이고 언제 풀리는지 알 수 없다.
    **재시도로 해결되지 않는다.** 데모 중 이게 나면 데모 모드로 넘긴다
    (docs/03-api-contract.md 14장).

    `ModelRateLimitError` 와 나눠 둔 이유가 이것이다. 하나로 묶으면 "요청을 줄인다"와
    "포기하고 데모 모드"라는 서로 다른 대응을 구분할 수 없다.
    """

    reason = "capacity"


class ModelServerError(ModelCallError):
    """그 밖의 오류. 제공사 5xx, 연결 끊김, 예상 못 한 응답 등."""

    reason = "server_error"


#: 모델을 부르지 않고 폴백한 경우의 사유 코드 (예산 초과)
REASON_BUDGET_EXHAUSTED = "budget_exhausted"
#: 어댑터가 없다(설정 누락)
REASON_NO_ADAPTER = "no_adapter"


# ---------------------------------------------------------------------------
# 호출 지점 정의 (숫자를 한 표에 모은다)
# ---------------------------------------------------------------------------


class OutputKind(str, Enum):
    """출력 형태 (research 1장·2장)."""

    SCHEMA = "schema"  # 스키마 강제, 한 번에 받는다
    TEXT_STREAM = "text_stream"  # 자연어 스트리밍


@dataclass(frozen=True)
class CallSitePolicy:
    """한 호출 지점의 예산과 실패 처리.

    근거: `docs/research/04-llm-api-operations.md` 3장 표 + "호출 지점별 방침" 표,
    `docs/03-api-contract.md` 9장 단계별 예산.

    숫자를 코드 여기저기 흩지 않는 이유는 단순하다. 20초 상한을 나눠 쓰는 값들이라
    한 곳을 고치면 다른 곳도 같이 봐야 한다. 표로 두면 그 검토가 한 화면에서 끝난다.

    timeout_s          이 호출에 허용하는 초
    timeout_scope      그 초가 무엇에 대한 것인지 (전체 / 첫 조각 / 정책당)
    output             출력 형태
    temperature        온도
    retry_on           재시도할 실패 종류. 여기 없는 실패는 그대로 폴백한다
    max_retries        재시도 횟수 (재시도 대상일 때만)
    fallback           실패 시 무엇으로 대체하는가 (사람이 읽는 설명)
    doc_ref            근거 문서
    """

    site: CallSite
    label: str
    timeout_s: float
    timeout_scope: str
    output: OutputKind
    temperature: float
    retry_on: FrozenSet[Type[ModelCallError]]
    max_retries: int
    fallback: str
    doc_ref: str

    def should_retry(self, error: ModelCallError) -> bool:
        """이 실패를 재시도할지."""
        if self.max_retries <= 0:
            return False
        return any(isinstance(error, kind) for kind in self.retry_on)


# 재시도는 형식 오류에만. 공식 문서는 지수 백오프를 권고하고 서버 오류도 재시도 대상으로
# 보지만(research 3장), 우리는 **알면서 의도적으로 1회로 제한한다.**
#
#   - 전체 상한이 20초다. 판정은 정책당 8초를 쓴다. 백오프 대기(보통 1초 이상)를 넣고
#     두 번째 시도를 하면 그 정책 하나가 예산을 다 먹는다.
#   - 사용자를 20초 넘게 기다리게 하는 것보다 그 조건을 "미확인"으로 표시하는 편이 낫다.
#     미확인은 화면에서 후속 질문으로 이어지지만, 멈춘 화면은 아무것도 못 한다.
#   - 서버 오류도 research 3장에서는 1회 재시도 대상이지만, 형식 오류와 달리 같은 입력을
#     다시 보내도 결과가 바뀔 근거가 약하다. 표에서 `retry_on` 한 줄만 고치면 되도록 열어 둔다.
#
# 이 기록을 남기는 목적: 나중에 "왜 백오프를 안 넣었냐"는 질문에 답하기 위해서다.
RETRY_ON_FORMAT_ONLY: FrozenSet[Type[ModelCallError]] = frozenset({ModelFormatError})


CALL_SITE_POLICIES: Mapping[CallSite, CallSitePolicy] = {
    CallSite.INTERPRET: CallSitePolicy(
        site=CallSite.INTERPRET,
        label="메시지 해석",
        timeout_s=3.0,
        timeout_scope="전체",
        output=OutputKind.SCHEMA,
        temperature=TEMPERATURE_STRUCTURED,
        retry_on=RETRY_ON_FORMAT_ONLY,
        max_retries=1,
        fallback="프로필 변경 없이 진행한다 (해석 결과를 빈 값으로 둔다)",
        doc_ref="research 04 3장·호출 지점별 방침, api-contract 9장 3단계",
    ),
    CallSite.JUDGE: CallSitePolicy(
        site=CallSite.JUDGE,
        label="예외 조건 판정",
        timeout_s=8.0,
        timeout_scope="정책당",
        output=OutputKind.SCHEMA,
        temperature=TEMPERATURE_STRUCTURED,
        retry_on=RETRY_ON_FORMAT_ONLY,
        max_retries=1,
        fallback="그 정책의 예외 조건 전체를 미확인으로 둔다",
        doc_ref="research 04 3장·호출 지점별 방침, api-contract 9장 7단계",
    ),
    CallSite.ANSWER: CallSitePolicy(
        site=CallSite.ANSWER,
        label="답변 작성",
        # 3초는 **첫 조각** 기준이다. 전체는 남은 예산 안에서 흐른다 (research 2장).
        timeout_s=3.0,
        timeout_scope="첫 조각",
        output=OutputKind.TEXT_STREAM,
        temperature=TEMPERATURE_ANSWER,
        retry_on=frozenset(),
        max_retries=0,
        fallback="답변 실패 이벤트를 보내고 카드는 유지한다. 받은 조각은 버리지 않는다",
        doc_ref="research 04 2장·3장, api-contract 9장 10단계",
    ),
}
"""세 호출 지점의 예산·실패 처리 표.

예외 조건 판정은 AI B 담당이지만 **호출 지점이므로 여기 정의해 둔다.**
담당자별로 호출부를 따로 두면 재시도 규칙과 타임아웃이 갈라지고, 20초를 어떻게
나눠 쓰는지 아무도 한눈에 못 본다. 나중에 이 모듈을 공용 위치(예: ``ai/common``)로
옮길 때도 파일 하나만 움직이면 된다.
"""


def policy_for(site: CallSite) -> CallSitePolicy:
    """호출 지점의 정책을 꺼낸다."""
    return CALL_SITE_POLICIES[site]


def resolve_model(site: CallSite, env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """설정에서 모델 이름을 읽는다 (research "모델 교체에 대비하는 방법").

    지점별 변수 → 공통 변수 순서로 본다. 둘 다 없으면 None 이고, 그 처리는 어댑터가 한다
    (스텁 어댑터는 모델 이름이 없어도 동작한다).
    """
    source = os.environ if env is None else env
    per_site = ENV_MODEL_BY_SITE.get(site)
    if per_site:
        name = source.get(per_site)
        if name:
            return name
    return source.get(ENV_MODEL) or None


def api_key_present(env: Optional[Mapping[str, str]] = None) -> bool:
    """키가 설정돼 있는지만 알려준다.

    **키 값은 돌려주지도, 로그에 남기지도 않는다.** 이 함수가 존재하는 이유가 그것이다.
    설정 확인이 필요할 때 아무도 `os.environ[ENV_API_KEY]` 를 직접 찍지 않게 한다.
    """
    source = os.environ if env is None else env
    return bool((source.get(ENV_API_KEY) or "").strip())


def max_concurrency(env: Optional[Mapping[str, str]] = None) -> int:
    """동시 실행 상한을 설정에서 읽는다 (research 4장). 잘못된 값은 기본값으로 떨어진다."""
    source = os.environ if env is None else env
    raw = (source.get(ENV_MAX_CONCURRENCY) or "").strip()
    if not raw:
        return DEFAULT_MAX_CONCURRENCY
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_CONCURRENCY
    return value if value >= 1 else DEFAULT_MAX_CONCURRENCY


# ---------------------------------------------------------------------------
# 어댑터 경계
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StructuredRequest:
    """스키마 강제 호출 요청 (research 1장).

    system      시스템 지시문. **동적으로 조립하지 않는다** (캐시 접두사가 깨진다, research 5장)
    user        사용자 입력 블록. `assemble_prompt` 가 만든 문자열을 그대로 넣는다
    schema      출력 스키마. 제공사 문법이 아니라 JSON Schema 형태의 평범한 매핑으로 받는다
    temperature 온도
    timeout_s   이 호출에 허용하는 초. 어댑터가 반드시 지켜야 한다
    model       모델 이름 (설정에서 온 값). None 이면 어댑터 기본값
    prefer_cache 캐싱을 쓸 수 있으면 써 달라는 **힌트**. 어댑터가 무시해도 동작은 같다

    엄격 모드가 모든 속성을 필수로 요구하는 제공사가 있으므로, 스키마를 만드는 쪽은
    "없을 수도 있는 값"을 항목 누락이 아니라 **빈 값 허용**으로 표현한다 (research 1장).
    """

    system: str
    user: str
    schema: Mapping[str, Any]
    temperature: float = TEMPERATURE_STRUCTURED
    timeout_s: float = 8.0
    model: Optional[str] = None
    prefer_cache: bool = False


@dataclass(frozen=True)
class TextRequest:
    """자연어 스트리밍 호출 요청 (research 2장).

    timeout_s 는 **첫 조각** 기준이다. 전체 길이는 호출하는 쪽의 남은 예산이 결정한다.
    """

    system: str
    user: str
    temperature: float = TEMPERATURE_ANSWER
    timeout_s: float = 3.0
    model: Optional[str] = None
    prefer_cache: bool = False


@dataclass(frozen=True)
class StructuredResponse:
    """스키마 강제 호출 응답.

    data   스키마에 맞는 값. 파싱까지 어댑터가 끝낸다
    cached 캐시가 걸렸는지. 알 수 없으면 None

    캐싱은 조건을 못 맞추면 **오류 없이 조용히 미적용**된다 (research 5장).
    그래서 걸렸는지 여부를 응답에 실어 두고, 지표로 확인한다.
    """

    data: Mapping[str, Any]
    cached: Optional[bool] = None


@runtime_checkable
class LlmAdapter(Protocol):
    """모델 호출 인터페이스.

    구조화 출력 하나, 텍스트 스트리밍 하나면 우리 세 호출 지점을 모두 덮는다.
    **제공사 고유 개념을 여기 노출하지 않는다.** 도구 정의, 캐시 표시, 메시지 역할 이름,
    응답 객체 구조는 전부 어댑터 안에서 끝낸다. 부르는 쪽은 어느 제공사인지 몰라야 한다.
    """

    def complete_structured(self, request: StructuredRequest) -> StructuredResponse:
        """스키마에 맞는 결과를 한 번에 돌려준다. 실패는 `ModelCallError` 계층으로 던진다."""
        ...

    def stream_text(self, request: TextRequest) -> Iterator[str]:
        """자연어 조각을 순서대로 내놓는다. 실패는 `ModelCallError` 계층으로 던진다."""
        ...


# ---------------------------------------------------------------------------
# 예산 관리
# ---------------------------------------------------------------------------

#: 시계는 주입할 수 있어야 한다. 그래야 예산 초과 상황을 테스트로 만들 수 있다.
Clock = Callable[[], float]


class BudgetTracker:
    """턴 전체의 남은 시간을 추적한다 (docs/03-api-contract.md 9장).

    전체 20초를 넘기면 그때까지의 결과로 끝내야 한다. 그러려면 호출 **전에** 남은 예산을
    확인하고, 이미 없으면 부르지 않고 폴백해야 한다. 불러 놓고 취소하는 것보다 안 부르는
    것이 확실하다.

    벽시계가 아니라 `time.monotonic` 을 쓴다. 시스템 시간이 조정되면 벽시계 차이는
    음수가 되거나 튄다. 경과 시간 측정에 쓸 값이 아니다.

    턴마다 새로 만든다. 세션이나 프로세스 단위로 재사용하면 예산이 뒤섞인다.
    """

    def __init__(
        self,
        total_s: float = TOTAL_BUDGET_S,
        *,
        reserved_s: float = RESERVED_NON_MODEL_S,
        clock: Optional[Clock] = None,
    ) -> None:
        self.total_s = total_s
        self.reserved_s = reserved_s
        self._clock: Clock = clock or time.monotonic
        self._started_at = self._clock()
        #: 부르지 않고 폴백한 건수 (지표: AI 실패·시간 초과 건수, api-contract 13장)
        self.skipped_calls = 0
        self.skipped_by_site: Dict[str, int] = {}

    def elapsed_s(self) -> float:
        """시작부터 지난 초."""
        return max(0.0, self._clock() - self._started_at)

    def remaining_s(self) -> float:
        """모델 호출에 쓸 수 있는 남은 초. 예약분을 뺀 값이며 0 미만으로 내려가지 않는다."""
        return max(0.0, self.total_s - self.reserved_s - self.elapsed_s())

    def has_room(self, need_s: float = MIN_CALL_ROOM_S) -> bool:
        """이 호출을 시작할 여유가 있는지."""
        return self.remaining_s() >= max(need_s, 0.0)

    def clamp(self, timeout_s: float) -> float:
        """호출 타임아웃을 남은 예산 안으로 줄인다.

        정책상 8초여도 남은 예산이 4초면 4초만 준다. 그래야 마지막 호출이 상한을 넘기지 않는다.
        """
        return max(0.0, min(timeout_s, self.remaining_s()))

    def note_skipped(self, site: CallSite) -> None:
        """부르지 않고 폴백한 것을 기록한다."""
        self.skipped_calls += 1
        key = site.value
        self.skipped_by_site[key] = self.skipped_by_site.get(key, 0) + 1

    def snapshot(self) -> Dict[str, Any]:
        """지표용 요약. 사용자 화면에 쓰지 않는다."""
        return {
            "total_s": round(self.total_s, 3),
            "reserved_s": round(self.reserved_s, 3),
            "elapsed_s": round(self.elapsed_s(), 3),
            "remaining_s": round(self.remaining_s(), 3),
            "skipped_calls": self.skipped_calls,
            "skipped_by_site": dict(self.skipped_by_site),
        }


# ---------------------------------------------------------------------------
# 호출 결과 (폴백을 흡수한 형태)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StructuredOutcome:
    """스키마 강제 호출 결과.

    ok        성공 여부
    data      성공이면 모델 결과, 실패면 **호출한 쪽이 준 폴백 값**
    failure   실패 사유 코드 (`ModelCallError.reason` 또는 `REASON_*`). 성공이면 None
    attempts  실제 시도 횟수 (재시도 포함). 부르지 않았으면 0
    called    모델을 실제로 불렀는지 (예산 초과로 건너뛰면 False)
    elapsed_s 이 호출에 쓴 초
    cached    캐시 적용 여부. 알 수 없으면 None
    """

    ok: bool
    data: Mapping[str, Any]
    failure: Optional[str]
    attempts: int
    called: bool
    elapsed_s: float
    cached: Optional[bool] = None


@dataclass(frozen=True)
class TextOutcome:
    """스트리밍 호출 결과.

    실패해도 `text` 에는 **그때까지 받은 조각이 남는다.** 이미 화면에 나간 문장을
    되돌릴 수 없고, 되돌릴 이유도 없다 (research 2장 "부분 응답은 버리지 않는다").
    """

    ok: bool
    text: str
    failure: Optional[str]
    attempts: int
    called: bool
    elapsed_s: float
    first_chunk_s: Optional[float] = None


# ---------------------------------------------------------------------------
# 게이트웨이 — 실패를 여기서 흡수한다
# ---------------------------------------------------------------------------


class Gateway:
    """세 호출 지점을 감싸는 단일 창구.

    **이 클래스는 `ModelCallError` 를 밖으로 내보내지 않는다.** 실패를 폴백 값으로 바꿔
    돌려준다. 이유:

      - 실패 규칙이 한 곳에 모인다. 호출한 쪽이 예외를 잡게 하면 "시간 초과는 미확인,
        형식 오류는 1회 재시도" 같은 규칙이 해석·판정·답변 세 모듈에 각각 복사되고,
        한 군데만 고쳐져 어긋난다 (research "실패를 부르는 쪽이 아니라 호출부에서 흡수한다").
      - 우리 제품은 AI 가 전부 실패해도 규칙 기반 카드로 굴러가야 한다
        (api-contract 9장 실패 처리). 그 성질은 폴백이 기본값일 때만 유지된다.
      - 예외를 쓰면 호출한 쪽이 try 를 빼먹는 순간 500 이 난다. 데모에서 가장 비싼 실수다.

    턴마다 새로 만든다. `BudgetTracker` 를 공유하기 때문이다.
    """

    def __init__(
        self,
        adapter: Optional[LlmAdapter],
        *,
        budget: Optional[BudgetTracker] = None,
        env: Optional[Mapping[str, str]] = None,
        clock: Optional[Clock] = None,
    ) -> None:
        self.adapter = adapter
        self._clock: Clock = clock or time.monotonic
        self.budget = budget or BudgetTracker(clock=self._clock)
        self._env = env
        #: 지표 (api-contract 13장). 사용자 화면에 쓰지 않는다.
        self.metrics: Dict[str, Any] = {
            "calls": 0,
            "failures": {},
            "retries": 0,
            "skipped": 0,
            "cache_hits": 0,
            "cache_unknown": 0,
        }

    # -- 내부 -------------------------------------------------------------

    def _model(self, site: CallSite) -> Optional[str]:
        return resolve_model(site, self._env)

    def _note_failure(self, reason: str) -> None:
        failures = self.metrics["failures"]
        failures[reason] = failures.get(reason, 0) + 1

    def _note_cache(self, cached: Optional[bool]) -> None:
        if cached is None:
            self.metrics["cache_unknown"] += 1
        elif cached:
            self.metrics["cache_hits"] += 1

    # -- 구조화 출력 ------------------------------------------------------

    def call_structured(
        self,
        site: CallSite,
        *,
        system: str,
        user: str,
        schema: Mapping[str, Any],
        fallback: Mapping[str, Any],
        prefer_cache: bool = False,
    ) -> StructuredOutcome:
        """스키마 강제 호출 (research 1장·3장).

        메시지 해석과 예외 조건 판정이 이 함수를 쓴다. `fallback` 은 실패 시 그대로
        돌려줄 값이다. 해석이면 "변경 없음", 판정이면 "전체 미확인"을 넣는다.
        폴백 값을 이 모듈이 정하지 않는 이유는, 그 모양(해석 결과 / 조건 목록)이
        호출 지점마다 다르고 그건 각 담당 모듈의 스키마이기 때문이다.
        """
        spec = policy_for(site)
        if spec.output is not OutputKind.SCHEMA:
            raise ValueError(f"{site.value} 는 스키마 강제 호출 지점이 아니다")

        started = self._clock()

        if self.adapter is None:
            self.metrics["skipped"] += 1
            self.budget.note_skipped(site)
            self._note_failure(REASON_NO_ADAPTER)
            return StructuredOutcome(
                ok=False,
                data=fallback,
                failure=REASON_NO_ADAPTER,
                attempts=0,
                called=False,
                elapsed_s=0.0,
            )

        # 예산을 먼저 본다. 이미 넘었으면 부르지 않는다.
        if not self.budget.has_room():
            self.metrics["skipped"] += 1
            self.budget.note_skipped(site)
            self._note_failure(REASON_BUDGET_EXHAUSTED)
            return StructuredOutcome(
                ok=False,
                data=fallback,
                failure=REASON_BUDGET_EXHAUSTED,
                attempts=0,
                called=False,
                elapsed_s=0.0,
            )

        attempts = 0
        last_reason = ModelServerError.reason
        while True:
            timeout_s = self.budget.clamp(spec.timeout_s)
            if timeout_s < MIN_CALL_ROOM_S:
                # 재시도하려 했는데 예산이 없는 경우도 여기로 온다.
                self.metrics["skipped"] += 1
                self.budget.note_skipped(site)
                self._note_failure(REASON_BUDGET_EXHAUSTED)
                return StructuredOutcome(
                    ok=False,
                    data=fallback,
                    failure=REASON_BUDGET_EXHAUSTED,
                    attempts=attempts,
                    called=attempts > 0,
                    elapsed_s=self._clock() - started,
                )

            request = StructuredRequest(
                system=system,
                user=user,
                schema=schema,
                temperature=spec.temperature,
                timeout_s=timeout_s,
                model=self._model(site),
                prefer_cache=prefer_cache,
            )

            attempts += 1
            self.metrics["calls"] += 1
            try:
                response = self.adapter.complete_structured(request)
            except ModelCallError as error:
                last_reason = error.reason
                if spec.should_retry(error) and attempts <= spec.max_retries:
                    self.metrics["retries"] += 1
                    continue
                self._note_failure(last_reason)
                return StructuredOutcome(
                    ok=False,
                    data=fallback,
                    failure=last_reason,
                    attempts=attempts,
                    called=True,
                    elapsed_s=self._clock() - started,
                )
            except Exception:  # noqa: BLE001 - 어댑터가 옮기지 못한 예외까지 흡수한다
                # 여기까지 오면 어댑터의 예외 매핑이 빠진 것이다. 그래도 흐름은 멈추지 않는다.
                self._note_failure(ModelServerError.reason)
                return StructuredOutcome(
                    ok=False,
                    data=fallback,
                    failure=ModelServerError.reason,
                    attempts=attempts,
                    called=True,
                    elapsed_s=self._clock() - started,
                )

            self._note_cache(response.cached)
            return StructuredOutcome(
                ok=True,
                data=response.data,
                failure=None,
                attempts=attempts,
                called=True,
                elapsed_s=self._clock() - started,
                cached=response.cached,
            )

    # -- 텍스트 스트리밍 --------------------------------------------------

    def stream_answer(
        self,
        *,
        system: str,
        user: str,
        on_delta: Optional[Callable[[str], None]] = None,
        prefer_cache: bool = False,
    ) -> TextOutcome:
        """답변 작성 호출 (research 2장, api-contract 9장 10단계).

        재시도 없음. 실패하면 받은 조각을 그대로 남긴 채 실패 사유를 돌려주고,
        호출한 쪽은 `answer_failed` 이벤트를 보내고 카드를 유지한다.

        `on_delta` 는 조각마다 불린다. 서버가 그 자리에서 `answer_delta` 이벤트로 흘리면 된다.
        모델 응답 형식을 프론트에 그대로 노출하지 않기 위해 조각은 평범한 문자열만 넘긴다.
        """
        site = CallSite.ANSWER
        spec = policy_for(site)
        started = self._clock()
        chunks: list[str] = []
        first_chunk_s: Optional[float] = None

        if self.adapter is None:
            self.metrics["skipped"] += 1
            self.budget.note_skipped(site)
            self._note_failure(REASON_NO_ADAPTER)
            return TextOutcome(
                ok=False,
                text="",
                failure=REASON_NO_ADAPTER,
                attempts=0,
                called=False,
                elapsed_s=0.0,
            )

        if not self.budget.has_room():
            self.metrics["skipped"] += 1
            self.budget.note_skipped(site)
            self._note_failure(REASON_BUDGET_EXHAUSTED)
            return TextOutcome(
                ok=False,
                text="",
                failure=REASON_BUDGET_EXHAUSTED,
                attempts=0,
                called=False,
                elapsed_s=0.0,
            )

        request = TextRequest(
            system=system,
            user=user,
            temperature=spec.temperature,
            # 첫 조각 기준 타임아웃. 남은 예산이 더 짧으면 그쪽을 따른다.
            timeout_s=self.budget.clamp(spec.timeout_s),
            model=self._model(site),
            prefer_cache=prefer_cache,
        )

        self.metrics["calls"] += 1
        try:
            for chunk in self.adapter.stream_text(request):
                if first_chunk_s is None:
                    first_chunk_s = self._clock() - started
                if chunk:
                    chunks.append(chunk)
                    if on_delta is not None:
                        on_delta(chunk)
                # 전체 상한을 넘기면 받은 만큼으로 끝낸다 (api-contract 9장 13단계).
                if not self.budget.has_room():
                    self._note_failure(REASON_BUDGET_EXHAUSTED)
                    return TextOutcome(
                        ok=False,
                        text="".join(chunks),
                        failure=REASON_BUDGET_EXHAUSTED,
                        attempts=1,
                        called=True,
                        elapsed_s=self._clock() - started,
                        first_chunk_s=first_chunk_s,
                    )
        except ModelCallError as error:
            self._note_failure(error.reason)
            return TextOutcome(
                ok=False,
                text="".join(chunks),
                failure=error.reason,
                attempts=1,
                called=True,
                elapsed_s=self._clock() - started,
                first_chunk_s=first_chunk_s,
            )
        except Exception:  # noqa: BLE001 - 어댑터가 옮기지 못한 예외까지 흡수한다
            self._note_failure(ModelServerError.reason)
            return TextOutcome(
                ok=False,
                text="".join(chunks),
                failure=ModelServerError.reason,
                attempts=1,
                called=True,
                elapsed_s=self._clock() - started,
                first_chunk_s=first_chunk_s,
            )

        text = "".join(chunks)
        if not text:
            # 조각이 하나도 오지 않았다. 형식상 성공이어도 답변으로 쓸 수 없다.
            self._note_failure(ModelServerError.reason)
            return TextOutcome(
                ok=False,
                text="",
                failure=ModelServerError.reason,
                attempts=1,
                called=True,
                elapsed_s=self._clock() - started,
                first_chunk_s=first_chunk_s,
            )

        return TextOutcome(
            ok=True,
            text=text,
            failure=None,
            attempts=1,
            called=True,
            elapsed_s=self._clock() - started,
            first_chunk_s=first_chunk_s,
        )

    def metrics_snapshot(self) -> Dict[str, Any]:
        """지표 요약. 예산 정보까지 함께 낸다."""
        snapshot = dict(self.metrics)
        snapshot["failures"] = dict(self.metrics["failures"])
        snapshot["budget"] = self.budget.snapshot()
        return snapshot


# ---------------------------------------------------------------------------
# 병렬 호출 (research 4장)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParallelItem(Generic[T]):
    """병렬 실행 항목 하나의 결과.

    ok      성공 여부
    value   성공이면 결과, 실패면 None
    failure 실패 사유 코드
    """

    key: str
    ok: bool
    value: Optional[T]
    failure: Optional[str] = None


def run_parallel(
    tasks: Mapping[str, Callable[[], T]],
    *,
    max_workers: Optional[int] = None,
    timeout_s: Optional[float] = None,
) -> Dict[str, ParallelItem[T]]:
    """동기 함수들을 동시에 돌린다. 예외 조건 판정을 후보별로 보낼 때 쓴다.

    근거: research 4장 "병렬 호출과 한도", api-contract 9장 7단계.

      - 동시 실행 수에 상한을 둔다. 후보 수만큼 무조건 늘리면 분당 요청·토큰 한도에 걸린다.
      - **한 정책이 실패해도 나머지는 살린다.** 각 작업의 예외를 그 항목에만 담는다.
      - 각 작업이 `Gateway.call_structured` 를 쓰면 예외가 아예 올라오지 않는다.
        여기서 예외를 잡는 건 그 밖의 실수까지 흡수하기 위한 이중 안전장치다.

    비동기 프레임워크를 확정하지 않는다. 서버 프레임워크가 미정이라 여기서 asyncio 를
    전제하면 나중에 다시 쓰게 된다. **동기 함수를 받아 동시에 돌리는 형태**로 두었으니,
    서버가 비동기면 그 경계에서 `asyncio.to_thread` 나 executor 로 이 함수를 감싸면 된다.
    반대로 서버가 동기면 그대로 부른다.

    `timeout_s` 를 넘긴 작업은 결과를 기다리지 않고 미완료로 표시한다. 스레드는 남아서
    끝나지만 우리는 그 결과를 쓰지 않는다. 표준 라이브러리 스레드는 밖에서 끊을 수 없다.
    그래서 각 작업의 **어댑터 타임아웃이 1차 방어선**이고 이건 2차 방어선이다.
    """
    if not tasks:
        return {}

    limit = max_workers if max_workers is not None else max_concurrency()
    limit = max(1, min(limit, len(tasks)))

    results: Dict[str, ParallelItem[T]] = {}
    with ThreadPoolExecutor(max_workers=limit) as pool:
        futures = {pool.submit(fn): key for key, fn in tasks.items()}
        try:
            for future in as_completed(futures, timeout=timeout_s):
                key = futures[future]
                try:
                    results[key] = ParallelItem(key=key, ok=True, value=future.result())
                except ModelCallError as error:
                    results[key] = ParallelItem(key=key, ok=False, value=None, failure=error.reason)
                except Exception:  # noqa: BLE001 - 한 건의 실패가 나머지를 끌어내리지 않게
                    results[key] = ParallelItem(
                        key=key, ok=False, value=None, failure=ModelServerError.reason
                    )
        except TimeoutError:
            # as_completed 의 전체 대기 시간 초과. 못 받은 것은 아래에서 채운다.
            pass

        for future, key in futures.items():
            if key not in results:
                future.cancel()
                results[key] = ParallelItem(
                    key=key, ok=False, value=None, failure=ModelTimeoutError.reason
                )

    return results


# ---------------------------------------------------------------------------
# 프롬프트 조립 (research 6장)
# ---------------------------------------------------------------------------

#: 원문을 감싸는 구분자. 읽는 사람이 지침·정책·데이터를 구분할 수 없으면 모델도 못 한다.
SOURCE_OPEN = "<policy_text>"
SOURCE_CLOSE = "</policy_text>"
PROFILE_OPEN = "<profile>"
PROFILE_CLOSE = "</profile>"
RULES_OPEN = "<rules>"
RULES_CLOSE = "</rules>"
OUTPUT_OPEN = "<output_rules>"
OUTPUT_CLOSE = "</output_rules>"


@dataclass(frozen=True)
class PromptSections:
    """프롬프트를 이루는 네 부분.

    rules        판정·해석 규칙 (지시문)
    source_text  정책 공고 원문 (구분자로 감싼다)
    profile      사용자 프로필. **매번 바뀌는 값**
    output_rules 출력 형태와 금지 사항

    문안 자체는 각 담당 모듈이 채운다. 이 모듈은 **순서와 감싸는 방식만** 정한다.
    """

    rules: str
    source_text: str
    profile: str
    output_rules: str


@dataclass(frozen=True)
class AssembledPrompt:
    """조립 결과.

    system 시스템 지시문. 캐시 접두사가 깨지지 않도록 동적 조립을 피한다 (research 5장)
    user   순서가 고정된 사용자 블록
    order  실제로 쓰인 블록 순서. 지표·디버깅용
    """

    system: str
    user: str
    order: Tuple[str, ...]


def assemble_prompt(
    sections: PromptSections,
    *,
    system: str = "",
    cache_friendly: bool = False,
) -> AssembledPrompt:
    """지시문·원문·프로필·출력 형식의 순서를 고정한다 (research 5장·6장).

    부르는 쪽이 매번 순서를 정하면 두 가지가 깨진다. 캐시 접두사가 한 토큰만 달라도
    미적용되고(그것도 오류 없이 조용히), 관련 정보가 중간에 놓일 때의 위치 효과를
    관리할 수 없다. 그래서 순서를 함수로 박는다.

    두 경우를 모두 다룬다.

    `cache_friendly=False` (캐싱 미사용)
        규칙 → 원문 → 프로필 → 출력 규칙 → **규칙 재확인**.
        지켜야 할 규칙을 앞과 끝에 두 번 둔다. 원문이 가운데 놓이는 배치라
        위치 효과를 규칙 반복으로 상쇄한다.

    `cache_friendly=True` (캐싱 사용)
        원문 → 규칙 → 프로필 → 출력 규칙.
        원문이 앞쪽 고정 위치로 가야 접두사 캐시가 걸린다. 매번 바뀌는 프로필은
        원문보다 **뒤**에 둔다. 앞에 두면 접두사가 매번 달라져 캐시가 무의미해진다.

    빈 블록은 건너뛴다. 빈 태그만 남으면 잡음이고, 구조가 없을 때 태그는 도움이 되지 않는다.
    """
    blocks: list[Tuple[str, str]] = []

    rules = sections.rules.strip()
    source_text = sections.source_text.strip()
    profile = sections.profile.strip()
    output_rules = sections.output_rules.strip()

    def rules_block(name: str) -> Optional[Tuple[str, str]]:
        if not rules:
            return None
        return (name, f"{RULES_OPEN}\n{rules}\n{RULES_CLOSE}")

    def source_block() -> Optional[Tuple[str, str]]:
        if not source_text:
            return None
        return ("source", f"{SOURCE_OPEN}\n{source_text}\n{SOURCE_CLOSE}")

    def profile_block() -> Optional[Tuple[str, str]]:
        if not profile:
            return None
        return ("profile", f"{PROFILE_OPEN}\n{profile}\n{PROFILE_CLOSE}")

    def output_block() -> Optional[Tuple[str, str]]:
        if not output_rules:
            return None
        return ("output_rules", f"{OUTPUT_OPEN}\n{output_rules}\n{OUTPUT_CLOSE}")

    if cache_friendly:
        candidates = [source_block(), rules_block("rules"), profile_block(), output_block()]
    else:
        candidates = [
            rules_block("rules"),
            source_block(),
            profile_block(),
            output_block(),
            rules_block("rules_tail"),
        ]

    for candidate in candidates:
        if candidate is not None:
            blocks.append(candidate)

    return AssembledPrompt(
        system=system,
        user="\n\n".join(body for _, body in blocks),
        order=tuple(name for name, _ in blocks),
    )


# ---------------------------------------------------------------------------
# 스텁 어댑터
# ---------------------------------------------------------------------------


@dataclass
class StubAdapter:
    """정해진 응답을 돌려주는 어댑터. 모델 없이 전체 흐름을 돌린다.

    쓰는 곳
      - 데모 모드 (api-contract 14장): 모델이 막혀도 화면이 끝까지 간다
      - 회귀 검사 (research 8장): 해석 테스트 10개, 대화 평가 C1~C8 을 모델 없이 돌린다
      - 실패 처리 확인: 형식 오류·시간 초과·속도 제한·용량 부족을 지정해 발생시킨다

    structured_data   `complete_structured` 가 돌려줄 값
    text_chunks       `stream_text` 가 내놓을 조각
    structured_errors 구조화 호출에서 순서대로 던질 예외. None 이면 그 차례는 성공.
                      길이를 넘어가면 그 뒤는 모두 성공한다.
                      예: `[ModelFormatError(), None]` → 첫 시도 형식 오류, 재시도 성공
    text_error        스트리밍 중 던질 예외. `text_error_after` 조각을 낸 뒤 던진다
    cached            응답에 실을 캐시 적용 여부
    delay_s           호출마다 흘려보낼 시간. 실제 sleep 이 아니라 시계를 앞당기는 값이며,
                      `sleep` 을 주입해 테스트에서 쓴다

    시간 초과를 흉내낼 때 실제로 기다리지 않는다. `ModelTimeoutError` 를 바로 던진다.
    테스트가 8초를 기다릴 이유가 없다.
    """

    structured_data: Mapping[str, Any] = field(default_factory=dict)
    text_chunks: Sequence[str] = ()
    structured_errors: Sequence[Optional[ModelCallError]] = ()
    text_error: Optional[ModelCallError] = None
    text_error_after: int = 0
    cached: Optional[bool] = None
    delay_s: float = 0.0
    sleep: Optional[Callable[[float], None]] = None

    #: 호출 기록 (검증용)
    structured_calls: list[StructuredRequest] = field(default_factory=list)
    text_calls: list[TextRequest] = field(default_factory=list)

    def _tick(self) -> None:
        if self.delay_s > 0 and self.sleep is not None:
            self.sleep(self.delay_s)

    def complete_structured(self, request: StructuredRequest) -> StructuredResponse:
        """지정된 값 또는 지정된 실패를 돌려준다."""
        index = len(self.structured_calls)
        self.structured_calls.append(request)
        self._tick()

        if index < len(self.structured_errors):
            error = self.structured_errors[index]
            if error is not None:
                raise error

        if request.timeout_s <= 0:
            raise ModelTimeoutError("남은 예산이 없다")

        return StructuredResponse(data=dict(self.structured_data), cached=self.cached)

    def stream_text(self, request: TextRequest) -> Iterator[str]:
        """조각을 순서대로 내놓는다. `text_error` 가 있으면 지정 위치에서 끊는다."""
        self.text_calls.append(request)
        self._tick()

        def generate() -> Iterator[str]:
            for position, chunk in enumerate(self.text_chunks):
                if self.text_error is not None and position == self.text_error_after:
                    raise self.text_error
                yield chunk
            if self.text_error is not None and self.text_error_after >= len(self.text_chunks):
                raise self.text_error

        return generate()


def fixed_clock(values: Iterable[float]) -> Clock:
    """정해진 값을 순서대로 내놓는 시계. 예산 초과 상황을 만들 때 쓴다.

    값이 떨어지면 마지막 값을 계속 돌려준다. 시계를 주입할 수 있게 만든 이유가 이것이다.
    20초를 실제로 기다리는 테스트는 쓸 수 없다.
    """
    queue = list(values)
    if not queue:
        raise ValueError("시계 값이 최소 하나 필요하다")
    state = {"index": 0}

    def clock() -> float:
        index = state["index"]
        if index < len(queue) - 1:
            state["index"] = index + 1
        return queue[index]

    return clock


# ---------------------------------------------------------------------------
# 알려진 한계와 미정 사항
# ---------------------------------------------------------------------------
#
# 미정 (10:30 이후 확정)
#   - 어느 모델을 받는지. 그래서 어댑터 구현이 비어 있고 외부 패키지를 import 하지 않는다.
#   - 서버 프레임워크(동기/비동기). 그래서 `run_parallel` 이 동기 함수를 받는 형태다.
#   - 실제 분당 요청·토큰 한도. `DEFAULT_MAX_CONCURRENCY` 는 research 4장의 "후보 3건" 권고에
#     맞춘 추정이고, 한도를 확인하면 환경 변수로 조정한다.
#   - 개발 단계에서 팀원이 키를 나눠 쓰는지. 같은 키를 5명이 쓰면 개발 중에도 429 가 난다.
#
# 한계
#   - `run_parallel` 의 `timeout_s` 는 결과를 기다리지 않게만 한다. 표준 라이브러리 스레드를
#     밖에서 끊을 수 없으므로 실제 취소는 어댑터의 타임아웃이 담당한다. 어댑터가 타임아웃을
#     지키지 않으면 스레드가 남는다. 어댑터 구현 시 반드시 `timeout_s` 를 전달해야 한다.
#   - 스트리밍 첫 조각 타임아웃도 마찬가지다. 게이트웨이는 첫 조각 도착 시각을 재서 기록만
#     하고, 3초를 강제하는 것은 어댑터다.
#   - 캐시 적용 여부는 어댑터가 응답 토큰 정보에서 읽어 채워야 한다. 못 채우면 None 이고,
#     그러면 "조용한 미적용"을 알아챌 수 없다 (research 5장).
#   - 스키마 강제는 형식만 보장한다. 내용이 맞다는 보장이 아니다. 발췌 대조는
#     `ai/citation/verify.py` 가 따로 한다.
#   - 재시도는 형식 오류 1회뿐이다. 속도 제한에 걸리면 이 모듈은 즉시 폴백하고, 요청을 줄이는
#     판단(후보 수 축소)은 호출하는 쪽이 지표를 보고 한다.
#   - `ai/judgment` 와 `ai/citation` 은 한국어 값("충족", "미확인")을 쓰고 이 모듈의 사유 코드는
#     영문 snake_case 다. 폴백 값의 표기는 호출하는 쪽이 정하므로 충돌하지 않지만,
#     12:00 통합 때 한쪽으로 맞출지 확인한다.
