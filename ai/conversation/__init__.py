"""대화 (AI A 담당).

담당 범위
---------
메시지 해석, 후속 질문 선택과 문장, 답변 작성, 각주 매핑, 관련 질문 칩, 대화 평가
(``ai/conversation/README.md`` 2장).

AI 가 하는 것과 코드가 하는 것
------------------------------
| AI 가 하는 것 | 코드가 하는 것 |
| --- | --- |
| 자연어에서 관심 분야·생활 사건·프로필 변경 추출 | 어떤 정보를 물을지 **선택** |
| 답변 설명 작성 | 판정 상태 확정 |
| (P1) 질문 문장을 맥락에 맞게 다듬기 | D-day 와 날짜 계산 |
| — | 각주 번호 부여 |
| — | 이미 물었거나 건너뛴 항목 기록 |
| — | 질문·칩 문구 표 관리 |

경계가 이렇게 그어진 이유는 하나다. **모델이 잘하는 일(문장 이해와 생성)과 못 믿을 일
(셈, 날짜, 일관된 판정)을 섞지 않는다.** 그래서 이 패키지에서 모델을 부르는 지점은
메시지 해석과 답변 작성 두 곳뿐이고, 나머지 모듈은 모델 없이 결정적으로 동작한다.

모듈
----
| 모듈 | 하는 일 |
| --- | --- |
| ``fields`` | 프로필 항목 이름, 의도 값, 질문 순서 |
| ``questions`` | 후속 질문 고정 문구 표 |
| ``followup`` | 무엇을 물을지 고르기 |
| ``interpret`` | 모델 해석 결과 검증과 프로필 반영 |
| ``answer`` | 답변 검증·정리와 뼈대 만들기 |
| ``llm`` | 모델 호출 한 곳 (예산·재시도·폴백) |
| ``related`` | 관련 질문 칩 고르기 (P1) |
| ``cases`` | 해석 테스트 10개와 대화 평가 C1~C8 데이터 |
| ``pipeline`` | **서버가 부를 유일한 입구.** 한 턴의 순서 |

## 서버는 ``pipeline`` 만 부르면 된다

이 패키지의 공개 함수는 30개가 넘지만 서버가 한 턴에 쓰는 것은 ``pipeline`` 의 네 개다
(``interpret_turn``, ``unknown_items_from``, ``build_answer_prompt``, ``finish_turn``).
나머지는 ``pipeline`` 이 문서 순서대로 부른다. 자세한 단계 표는 그 모듈 독스트링에 있다.

import 실패를 삼키는 이유
-------------------------
같은 패키지에 여러 사람이 동시에 파일을 넣는 중이다. 한 모듈의 문법 오류나 아직 없는
import 때문에 ``import ai.conversation`` 이 실패하면, 그 순간 서버가 뜨지 않고 규칙 기반
카드조차 화면에 나가지 못한다. AI 가 전부 실패해도 규칙 기반 카드는 살아 있어야 한다는
것이 계약이다(``docs/03-api-contract.md`` 9장). 패키지 import 는 그 계약보다 앞에 있으니
여기서 막는다.

대신 조용히 넘기지 않는다. 실패한 모듈은 ``MISSING_MODULES`` 에 이유와 함께 남고,
해당 이름은 ``None`` 이 된다. 부르는 쪽에서 ``None`` 검사를 잊으면 그때 터지는데,
그것은 이 패키지 밖의 한 경로만 죽는 실패다.
"""

from __future__ import annotations

import importlib
from types import ModuleType
from typing import Dict, List, Optional

# 이 패키지에서 공개하는 모듈. 왼쪽부터 순서대로 불러온다.
# 새 모듈(interpret, answer, llm)이 생기면 여기에 이름을 추가한다.
_MODULE_NAMES = (
    "fields",
    "questions",
    "followup",
    "interpret",
    "answer",
    "llm",
    "related",
    "cases",
    "pipeline",
)

# 불러오지 못한 모듈과 그 이유. 비어 있어야 정상이다.
MISSING_MODULES: List[str] = []

_loaded: Dict[str, ModuleType] = {}

for _name in _MODULE_NAMES:
    try:
        _loaded[_name] = importlib.import_module(f".{_name}", __name__)
    except Exception as _exc:  # noqa: BLE001 - 어떤 이유로든 패키지를 죽이지 않는다
        MISSING_MODULES.append(f"{_name}: {type(_exc).__name__}: {_exc}")

fields: Optional[ModuleType] = _loaded.get("fields")
questions: Optional[ModuleType] = _loaded.get("questions")
followup: Optional[ModuleType] = _loaded.get("followup")
interpret: Optional[ModuleType] = _loaded.get("interpret")
answer: Optional[ModuleType] = _loaded.get("answer")
llm: Optional[ModuleType] = _loaded.get("llm")
related: Optional[ModuleType] = _loaded.get("related")
cases: Optional[ModuleType] = _loaded.get("cases")
pipeline: Optional[ModuleType] = _loaded.get("pipeline")


def loaded_modules() -> List[str]:
    """실제로 불러온 모듈 이름. 선언 순서대로."""
    return [name for name in _MODULE_NAMES if name in _loaded]


__all__ = [
    "fields",
    "questions",
    "followup",
    "interpret",
    "answer",
    "llm",
    "related",
    "cases",
    "pipeline",
    "MISSING_MODULES",
    "loaded_modules",
]
