"""턴마다 새로 만드는 실행 묶음 (서버가 부를 두 번째 입구).

**서버는 턴이 시작될 때 ``for_turn()`` 을 부르고, 그 결과를 ``turn.run_turn`` 에 넘긴다.**

    rt = runtime.for_turn()
    result = turn.run_turn(
        profile, model_output,
        policies=candidates,
        judge_policies=rt.judge_policies,
        answer_writer=rt.answer_writer,
    )

이 파일이 있는 이유는 하나다. **모델을 부르는 데 필요한 조립이 세 곳에 흩어져 있다.**
``ai/adapters.py`` 가 어댑터를 만들고, ``ai/conversation/llm.py`` 가 예산과 게이트웨이를
들고, ``ai/judgment`` 가 판정기를 들고 있다. 서버가 이 셋을 직접 조립하면 조립 순서와
"턴마다 새로 만들어야 한다"는 규칙이 서버 코드에 숨는다. 그 규칙을 어기면 오류 없이
두 번째 턴부터 모델이 아예 불리지 않는다.

왜 턴마다 새로 만드는가
-----------------------
``llm.BudgetTracker`` 는 **생성 시점부터** 20초를 센다(``llm.TOTAL_BUDGET_S``).
``Gateway`` 는 그 예산과 지표를 들고 있다. 세션이나 프로세스 단위로 재사용하면 두 번째
턴이 시작부터 "예산 없음"이 되어 모델을 건너뛴다. 그 실패는 예외 없이 일어나고 화면에는
"AI 가 실패했다" 모양으로만 보인다. ``ai/conversation/pipeline.py`` 독스트링이 같은
경고를 적어 뒀고, 이 파일은 그 규칙을 코드로 만든다. ``for_turn`` 을 부를 때마다 새
``Gateway`` 와 새 ``ExceptionJudge`` 가 생긴다.

예산이 두 갈래인 문제
---------------------
**이 파일이 해결하지 못하는 것을 먼저 적는다.** AI B ``ExceptionJudge`` 는 자체 예산을
센다(정책당 ``DEFAULT_POLICY_BUDGET``, 배치 ``DEFAULT_BATCH_BUDGET``). AI A
``BudgetTracker`` 는 턴 전체 20초를 센다. 둘은 서로를 모른다. 그래서 판정이 배치 예산을
다 쓰고 답변이 20초 예산의 남은 만큼을 또 쓰면 합계가 20초를 넘을 수 있다.

``for_turn`` 은 **판정 예산을 전체 예산에서 떼어 주는 것**으로 이 틈을 좁힌다. 판정기에
넘길 배치 예산을 ``JUDGMENT_SHARE`` 로 제한하고, 답변은 그 뒤 남은 예산을 ``Gateway`` 가
센다. 완전한 해결은 두 모듈이 같은 시계를 공유해야 하는데, 그것은 AI B 쪽 생성자 변경이
필요해서 12:00 통합 안건으로 남긴다. 지금 값으로도 합계가 전체 예산을 넘지 않는 것은
``JUDGMENT_SHARE`` 가 전체보다 작기 때문이다.

키가 없을 때
------------
``for_turn`` 은 **예외를 던지지 않는다.** 키나 모델 이름이 없으면 ``judge_policies`` 와
``answer_writer`` 가 ``None`` 이 되고, ``turn.run_turn`` 은 그 경우를 이미 견딘다(예외
조건은 전부 미확인, 답변은 실패, 카드는 남는다). 무엇이 없어서 건너뛰는지는
``Runtime.missing`` 에 남는다. 키를 넣었는데 모델이 안 불리는 상황에서 **가장 먼저 볼
값**이 그것이다.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from ai import adapters
from ai.conversation import llm

# AI B 는 선택적으로 불러온다. ai/turn.py 와 같은 이유다. 판정 모듈 하나가 못 불러와졌다고
# 이 파일이 죽으면 규칙 기반 카드까지 화면에서 사라진다.
_JUDGE_IMPORT_ERROR: Optional[str] = None

try:  # pragma: no cover - import 경로는 테스트에서 직접 확인한다
    from ai.judgment import ExceptionJudge
    from ai.judgment.client import ClaudeClient
except Exception as _exc:  # noqa: BLE001
    _JUDGE_IMPORT_ERROR = f"ai.judgment: {type(_exc).__name__}: {_exc}"
    ExceptionJudge = None  # type: ignore[assignment]
    ClaudeClient = None  # type: ignore[assignment]


#: 전체 예산 중 예외 조건 판정에 줄 몫.
#:
#: 0.4 를 고른 근거. 한 턴은 해석 → 판정 → 답변 순서이고, 사용자가 기다리는 체감은
#: 답변 스트리밍이 시작되는 시점에 끊긴다. 판정이 예산을 다 쓰면 답변이 한 글자도 나오지
#: 못한 채 턴이 끝나고, 화면에는 카드만 남는다. 반대로 판정 몫을 너무 줄이면 조건이 전부
#: 미확인으로 떨어져 카드가 "확인이 필요해요" 로만 채워진다. 둘 중 앞쪽이 더 나쁘다.
#: 답변은 대체 문구가 없고 판정은 미확인이라는 정직한 대체 상태가 있기 때문이다.
#: 그래서 판정에 절반보다 적게 준다.
JUDGMENT_SHARE = 0.4

#: 판정 몫의 하한. 이보다 적으면 판정을 아예 건너뛴다.
#: 반쯤 판정하다 타임아웃으로 전부 미확인이 되면 토큰만 쓰고 결과는 건너뛴 것과 같다.
MIN_JUDGMENT_BUDGET_S = 1.0

# 무엇이 없어서 건너뛰는지 나타내는 코드. 지표 집계에 쓰므로 문자열을 바꾸지 않는다.
MISSING_JUDGE_MODULE = "judgment_module"
MISSING_ADAPTER = "llm_adapter"
MISSING_ANSWER_PROMPT = "answer_prompt_text"


@dataclass
class Runtime:
    """한 턴 분량의 실행 묶음. **턴이 끝나면 버린다.**

    gateway        모델 호출 게이트웨이. 예산과 지표를 들고 있다
    judge_policies ``turn.run_turn`` 에 넘길 판정 함수. 키가 없으면 None
    answer_writer  ``turn.run_turn`` 에 넘길 답변 작성 함수. 키가 없으면 None
    missing        무엇이 없어서 건너뛰는지. 비어 있어야 정상이다
    """

    gateway: llm.Gateway
    judge_policies: Optional[Any] = None
    answer_writer: Optional[Any] = None
    missing: Tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        """모델을 실제로 부를 수 있는 상태인가."""
        return self.judge_policies is not None and self.answer_writer is not None

    def metrics_snapshot(self) -> Dict[str, Any]:
        """턴이 끝나면 지표로 기록한다 (docs/03-api-contract.md 13장).

        사용자 화면에 쓰지 않는다.
        """
        snapshot: Dict[str, Any] = {"missing": list(self.missing), "ready": self.ready}
        try:
            snapshot["llm"] = self.gateway.metrics_snapshot()
        except Exception:  # noqa: BLE001 - 지표 수집이 턴을 실패시키지 않게
            snapshot["llm"] = {}
        return snapshot


def _answer_writer(gateway: llm.Gateway, on_delta: Optional[Any] = None):
    """``turn.run_turn`` 에 넘길 답변 작성 함수를 만든다.

    ``turn.run_turn`` 은 ``pipeline.build_answer_prompt`` 가 만든 ``AssembledPrompt`` 를
    넘기고, ``gateway.stream_answer`` 는 ``system`` 과 ``user`` 를 따로 받는다. 그 사이를
    여기서 잇는다. 프롬프트 객체를 그대로 넘기면 ``TypeError`` 가 나고, 그 예외는
    ``turn`` 의 넓은 ``except`` 에 잡혀 **답변만 조용히 실패한다.** 통합 중에 원인을
    찾기 어려운 경로라 이 자리에 명시적으로 둔다.

    ``on_delta`` 를 주면 조각마다 불린다. 서버가 그 자리에서 ``answer_delta`` 이벤트로
    흘리면 된다(``llm.Gateway.stream_answer`` 독스트링).

    **스트리밍과 P0 검증의 관계.** 이 함수는 완성된 본문을 돌려준다. ``finish_turn`` 이
    완성된 본문을 받아야 금지 표현·각주 검증을 할 수 있기 때문이다(README 11장 완료 기준).
    서버가 ``on_delta`` 로 조각을 먼저 흘리기로 하면, 화면에 나간 문장을 나중에 지울 수
    없다는 것을 받아들이는 선택이 된다. 그 결정은 서버 몫이라 여기서 막지 않고 통로만 둔다.
    ``docs/03-api-contract.md`` 9장 10단계가 정리·검증을 답변 발행 앞에 두고 있으니,
    기본값은 조각을 흘리지 않는 쪽이다.
    """

    def write(prompt: Any) -> str:
        system = getattr(prompt, "system", "") or ""
        user = getattr(prompt, "user", "") or ""
        if not user:
            # 프롬프트 조립이 실패했다. 모델을 부르지 않는다. 빈 프롬프트로 부르면
            # 토큰을 쓰고 아무 지시 없는 답변을 받는다.
            return ""
        outcome = gateway.stream_answer(system=str(system), user=str(user), on_delta=on_delta)
        # 실패해도 예외를 던지지 않는다. ``TextOutcome.text`` 에는 그때까지 받은 조각이
        # 남아 있고(llm.py 주석), 빈 문자열이면 turn 이 answer_failed 로 처리하고 카드는 남는다.
        text = getattr(outcome, "text", "")
        return text if isinstance(text, str) else ""

    return write


def for_turn(
    env: Optional[Mapping[str, str]] = None,
    *,
    adapter: Optional[llm.LlmAdapter] = None,
    judge_client: Optional[Any] = None,
    on_delta: Optional[Any] = None,
) -> Runtime:
    """한 턴 분량의 실행 묶음을 만든다. **턴마다 부른다.**

    근거: ``ai/conversation/llm.py`` (예산·게이트웨이), ``ai/adapters.py`` (어댑터),
    ``ai/judgment/judge.py`` (판정기), ``docs/03-api-contract.md`` 9장.

    ``adapter`` 와 ``judge_client`` 를 직접 넘길 수 있다. 테스트용 구멍이 아니라, 서버가
    한 프로세스에서 어댑터를 재사용하고 예산만 새로 세고 싶을 때 쓰는 통로다. 어댑터는
    상태가 없어 재사용해도 안전하고, 재사용하면 안 되는 것은 예산과 지표 쪽이다.

    **예외를 던지지 않는다.** 키가 없으면 ``judge_policies`` 와 ``answer_writer`` 가
    ``None`` 이 되고 ``turn.run_turn`` 이 그 경우를 견딘다.
    """
    missing: List[str] = []

    if adapter is None:
        try:
            adapter = adapters.adapter_from_env(env)
        except Exception:  # noqa: BLE001 - 어댑터 조립 실패가 턴을 막지 않게
            adapter = None
    if adapter is None:
        missing.append(MISSING_ADAPTER)
        try:
            missing.extend(adapters.missing_settings(env))
        except Exception:  # noqa: BLE001
            pass

    gateway = llm.Gateway(adapter=adapter)

    judge_policies = None
    if ExceptionJudge is None:
        missing.append(MISSING_JUDGE_MODULE)
        if _JUDGE_IMPORT_ERROR:
            missing.append(_JUDGE_IMPORT_ERROR)
    elif adapter is not None or judge_client is not None:
        try:
            client = judge_client
            if client is None and ClaudeClient is not None:
                # 어댑터가 감싼 것과 같은 클라이언트를 쓴다. 어댑터가 AI B client 를
                # 감싸고 있으므로 그 안의 것을 꺼내 쓰면 설정이 한 곳에서 온다.
                client = getattr(adapter, "client", None)
                if client is None:
                    client = ClaudeClient()
            judge = ExceptionJudge(client, batch_budget=_judgment_budget(gateway))
            judge_policies = judge.judge_policies
        except Exception:  # noqa: BLE001 - 판정기 조립 실패가 카드를 지우지 않게
            judge_policies = None
            missing.append(MISSING_JUDGE_MODULE)

    answer_writer = _answer_writer(gateway, on_delta) if adapter is not None else None

    return Runtime(
        gateway=gateway,
        judge_policies=judge_policies,
        answer_writer=answer_writer,
        missing=tuple(dict.fromkeys(missing)),
    )


def _judgment_budget(gateway: llm.Gateway) -> float:
    """판정에 줄 배치 예산.

    전체 예산에서 ``JUDGMENT_SHARE`` 만큼 떼어 준다. 남은 예산을 기준으로 계산하는 이유는
    해석이 이미 시간을 썼을 수 있기 때문이다. 하한보다 적으면 0 을 돌려주고, 그러면 판정기는
    타임아웃으로 전부 미확인을 낸다(``ExceptionJudge`` 가 그 경우를 견딘다).
    """
    try:
        remaining = float(gateway.budget.remaining_s())
    except Exception:  # noqa: BLE001 - 예산을 못 읽으면 보수적으로 기본값을 쓴다
        remaining = llm.TOTAL_BUDGET_S
    share = max(0.0, remaining * JUDGMENT_SHARE)
    return share if share >= MIN_JUDGMENT_BUDGET_S else 0.0


__all__ = [
    "JUDGMENT_SHARE",
    "MIN_JUDGMENT_BUDGET_S",
    "MISSING_JUDGE_MODULE",
    "MISSING_ADAPTER",
    "MISSING_ANSWER_PROMPT",
    "Runtime",
    "for_turn",
]
