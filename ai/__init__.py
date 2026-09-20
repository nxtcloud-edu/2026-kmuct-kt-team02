"""AI 파트 (대화 · 판정 · 인용 검증).

| 모듈 | 하는 일 | 담당 |
| --- | --- | --- |
| ``turn`` | **한 턴 전체.** 서버가 부를 입구 | 공용 |
| ``runtime`` | 턴마다 만드는 실행 묶음 (게이트웨이 · 판정기 · 답변 작성기) | 공용 |
| ``gateway`` | 캠프 게이트웨이 호출 (OpenAI 호환) | 공용 |
| ``envfile`` | ``.env`` 읽기 | 공용 |
| ``adapters`` | Anthropic 직접 호출 경로 (키를 따로 받는 경우) | 공용 |
| ``conversation`` | 해석 · 후속 질문 · 답변 · 칩 | AI A |
| ``judgment`` | 예외 조건 판정 · 인용 검증 · 평가 | AI B |

서버가 한 턴에 쓰는 것은 두 개다.

    rt = ai.runtime.for_turn()
    result = ai.turn.run_turn(profile, model_output, policies=candidates,
                              judge_policies=rt.judge_policies,
                              answer_writer=rt.answer_writer)

직접 두드려 보려면 ``python -m ai`` 를 쓴다.

import 실패를 삼키는 이유
-------------------------
한 모듈의 문법 오류나 아직 없는 import 때문에 ``import ai`` 가 실패하면, 그 순간 서버가
뜨지 않고 규칙 기반 카드조차 화면에 나가지 못한다. AI 가 전부 실패해도 규칙 기반 카드는
살아 있어야 한다는 것이 계약이다(``docs/03-api-contract.md`` 9장). 패키지 import 는 그
계약보다 앞에 있으니 여기서 막는다.

조용히 넘기지는 않는다. 실패한 모듈은 ``MISSING_MODULES`` 에 이유와 함께 남고 해당 이름은
``None`` 이 된다. ``ai/conversation/__init__.py`` 와 같은 방식이다.
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Dict, List, Optional

_MODULE_NAMES = (
    "envfile",
    "gateway",
    "adapters",
    "turn",
    "runtime",
)

#: 불러오지 못한 모듈과 그 이유. 비어 있어야 정상이다.
MISSING_MODULES: List[str] = []

_loaded: Dict[str, ModuleType] = {}

for _name in _MODULE_NAMES:
    try:
        _loaded[_name] = importlib.import_module(f".{_name}", __name__)
    except Exception as _exc:  # noqa: BLE001 - 어떤 이유로든 패키지를 죽이지 않는다
        MISSING_MODULES.append(f"{_name}: {type(_exc).__name__}: {_exc}")

envfile: Optional[ModuleType] = _loaded.get("envfile")
gateway: Optional[ModuleType] = _loaded.get("gateway")
adapters: Optional[ModuleType] = _loaded.get("adapters")
turn: Optional[ModuleType] = _loaded.get("turn")
runtime: Optional[ModuleType] = _loaded.get("runtime")


def loaded_modules() -> List[str]:
    """실제로 불러온 모듈 이름. 선언 순서대로."""
    return [name for name in _MODULE_NAMES if name in _loaded]


__all__ = [
    "envfile",
    "gateway",
    "adapters",
    "turn",
    "runtime",
    "MISSING_MODULES",
    "loaded_modules",
]
