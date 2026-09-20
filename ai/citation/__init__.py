"""`ai.judgment` 로 옮긴 인용 검증의 호환 재수출 (AI B 담당).

## 왜 이 파일이 있는가

인용 검증은 `docs/07-schedule-roles.md` 의 폴더 표에 따라 `ai/judgment/` 로 옮겼다.
`ai/citation/` 은 그 표에 없는 폴더였다.

그런데 `server/orchestrator_adapters.py` 가 `from ai.citation import verify_conditions`
를 쓴다. 폴더만 옮기면 **서버가 import 단계에서 죽는다.** 모듈 하나가 없어서 API가
아예 뜨지 않는 것이라, 데모 경로 전체가 멈춘다.

그 import 줄은 백엔드B 파일이라 내가 고치지 않는다. 대신 이 파일이 옛 경로를 살려
두고 구현은 `ai/judgment/` 한 곳만 남긴다. 로직 사본은 만들지 않는다. 사본을 두면
두 경로가 서로 다르게 동작하기 시작하고, 어느 쪽이 화면에 나갔는지 알 수 없게 된다.

## 없어진 것

`MET` / `UNMET` / `UNKNOWN` 은 `ai.judgment.values` 에서 가져온다. 값이 한국어
(`"충족"`)에서 영문(`"met"`)으로 바뀌었다. `CONTRIBUTING.md` 7-2 가 영문으로 정해
두었고 `docs/09-value-naming-decision.md` 가 그 방향으로 결정한 것을 따른 결과다.
**이 경로로 import 하는 쪽은 값이 영문이라는 것을 알아야 한다.**

## 정리 시점

백엔드B가 import 를 `from ai.judgment import verify_conditions` 로 바꾸면 이 폴더를
지운다. 그 전까지는 남겨 둔다.
"""

from ai.judgment.citation import (
    MAX_EXCERPT_LEN,
    MIN_EXCERPT_LEN,
    VerifyResult,
    contains,
    raw_text_covers,
    verify_conditions,
    verify_excerpt,
)
from ai.judgment.normalize import canonical, normalize, normalize_with_map
from ai.judgment.values import MET, UNKNOWN, UNMET

__all__ = [
    "canonical",
    "normalize",
    "normalize_with_map",
    "verify_excerpt",
    "verify_conditions",
    "contains",
    "raw_text_covers",
    "VerifyResult",
    "MIN_EXCERPT_LEN",
    "MAX_EXCERPT_LEN",
    "MET",
    "UNMET",
    "UNKNOWN",
]
