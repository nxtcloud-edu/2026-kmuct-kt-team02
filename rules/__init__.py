"""규칙 엔진 (담당: 백엔드A).

외부 의존성 없이 표준 라이브러리만 쓴다. 입출력은 snake_case dict 다.
백엔드B 가 부르는 것은 아래 세 함수뿐이다.

    load_policies(path)                      정책 파일 로드와 검증
    count_verified(policies)                 /health 의 verified_policy_count
    evaluate_policies(profile, policies, today, limit)   판정·정렬·미확인 항목

`today` 를 인자로 받는 이유는 같은 입력에 같은 출력을 보장하기 위해서다.
D-day 계산이 실행 시각에 좌우되면 마감 경계값 테스트와 정답셋 채점이 흔들린다.
"""

from .engine import evaluate_policies, evaluate_policy
from .loader import count_verified, load_policies
from .saved import evaluate_by_ids
from .validate import blocking_issues, normalize_profile

__all__ = [
    "blocking_issues",
    "count_verified",
    "evaluate_by_ids",
    "evaluate_policies",
    "evaluate_policy",
    "load_policies",
    "normalize_profile",
]
