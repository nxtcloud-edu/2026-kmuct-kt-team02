"""AI B 실전 호출 준비 상태 점검.

API 키를 받기 전에도 코드·평가 데이터·실행 환경 중 무엇이 막고 있는지 알 수 있게 한다.
**키 값과 모델 이름 값은 출력하지 않는다.** 설정 유무만 본다.

사용법
------

    python3 -m ai.judgment.readiness

현재 예상 결과는 API 키·모델·SDK·실제 J1~J8 원문이 준비되지 않았다는 안내다.
키를 받은 뒤 같은 명령을 다시 실행하면 남은 항목만 보인다.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from dataclasses import dataclass, field
from typing import List, Mapping, Optional, Sequence, Tuple

from ai.judgment.cases import placeholder_cases
from ai.judgment.client import (
    ENV_API_KEY,
    ENV_API_KEY_FALLBACK,
    ENV_MODEL,
)

#: 서버의 `pyproject.toml`이 요구하는 버전. 통합 실행은 이 이상이어야 한다.
REQUIRED_PYTHON = (3, 11)


@dataclass(frozen=True)
class ReadinessItem:
    name: str
    ready: bool
    detail: str
    category: str

    @property
    def mark(self) -> str:
        return "준비" if self.ready else "대기"


@dataclass
class ReadinessReport:
    items: List[ReadinessItem] = field(default_factory=list)

    @property
    def ready_for_live_call(self) -> bool:
        """실제 Claude 호출과 J1~J8 평가까지 가능한지."""
        return all(item.ready for item in self.items)

    @property
    def blockers(self) -> List[ReadinessItem]:
        return [item for item in self.items if not item.ready]

    def to_table(self) -> str:
        lines = [
            "| 구분 | 항목 | 상태 | 설명 |",
            "| --- | --- | --- | --- |",
        ]
        for item in self.items:
            lines.append(
                f"| {item.category} | {item.name} | {item.mark} | {item.detail} |"
            )
        return "\n".join(lines)

    def summary(self) -> str:
        if self.ready_for_live_call:
            return "AI B 실전 호출 준비 완료"
        names = ", ".join(item.name for item in self.blockers)
        return f"AI B 실전 호출 대기 {len(self.blockers)}개: {names}"


def check_readiness(
    env: Optional[Mapping[str, str]] = None,
    *,
    python_version: Optional[Sequence[int]] = None,
    anthropic_available: Optional[bool] = None,
    placeholder_count: Optional[int] = None,
) -> ReadinessReport:
    """준비 상태를 검사한다. 테스트가 모든 외부 상태를 주입할 수 있다."""
    source = os.environ if env is None else env
    version = tuple(python_version or sys.version_info[:3])
    sdk_ready = (
        importlib.util.find_spec("anthropic") is not None
        if anthropic_available is None
        else anthropic_available
    )
    placeholders = (
        len(placeholder_cases()) if placeholder_count is None else placeholder_count
    )

    api_key_ready = bool(
        str(source.get(ENV_API_KEY) or source.get(ENV_API_KEY_FALLBACK) or "").strip()
    )
    model_ready = bool(str(source.get(ENV_MODEL) or "").strip())
    python_ready = tuple(version[:2]) >= REQUIRED_PYTHON

    items = [
        ReadinessItem(
            name="Python 3.11+",
            ready=python_ready,
            detail=(
                f"현재 {version[0]}.{version[1]}.{version[2] if len(version) > 2 else 0}; "
                "서버 pyproject.toml 기준"
            ),
            category="실행 환경",
        ),
        ReadinessItem(
            name="Anthropic SDK",
            ready=sdk_ready,
            detail="설치됨" if sdk_ready else "의존성 버전 확정·설치 필요",
            category="실행 환경",
        ),
        ReadinessItem(
            name="Claude API 키",
            ready=api_key_ready,
            detail="환경 변수 설정됨" if api_key_ready else f"{ENV_API_KEY} 설정 필요",
            category="API",
        ),
        ReadinessItem(
            name="Claude 모델 이름",
            ready=model_ready,
            detail="환경 변수 설정됨" if model_ready else f"{ENV_MODEL} 설정 필요",
            category="API",
        ),
        ReadinessItem(
            name="J1~J8 실제 공고 문장",
            ready=placeholders == 0,
            detail=(
                "실제 공고 문장으로 교체됨"
                if placeholders == 0
                else f"대체물 {placeholders}건 남음; 이 상태의 점수는 발표 금지"
            ),
            category="평가 데이터",
        ),
    ]
    return ReadinessReport(items)


def main() -> int:
    report = check_readiness()
    print(report.to_table())
    print()
    print(report.summary())
    print("\n키 값과 모델 이름 값은 보안상 출력하지 않는다.")
    return 0 if report.ready_for_live_call else 1


if __name__ == "__main__":
    raise SystemExit(main())
