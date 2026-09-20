"""정책 데이터 로드와 필드 검증 (rules/README.md 9장).

데이터 한 건이 잘못돼 있으면 **그 한 건만 빠지고 나머지 결과는 정상으로 나가야 한다.**
실패 건은 검수 필요 목록으로 모아 돌려준다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import constants as c
from . import deadline as dl

ID_PATTERN = re.compile(r"^(SEOUL|GOV)-\d{3}$")

REQUIRED_TEXT_FIELDS = ("id", "title", "agency")
DATE_FIELDS = ("checked_at", "fetched_at", "apply_start", "apply_end")

VALID_CATEGORIES = frozenset(
    {"scholarship", "living", "job", "culture", "housing", "all"}
)
VALID_STATUSES = frozenset(
    {"enrolled", "on_leave", "final_semester", "job_seeking", "employed"}
)
VALID_DATA_STATUS = frozenset({c.VERIFIED, c.RECHECK, c.CLOSED, c.UPCOMING})
VALID_SOURCE_KIND = frozenset({c.SOURCE_MANUAL, c.SOURCE_CRAWLED})


def _validate(policy: dict) -> list[str]:
    """정책 한 건의 문제를 모두 모은다. 빈 목록이면 통과."""
    problems: list[str] = []
    policy_id = policy.get("id") or "<id 없음>"

    for field in REQUIRED_TEXT_FIELDS:
        if not (policy.get(field) or "").strip():
            problems.append(f"{field} 비어 있음")

    if policy.get("id") and not ID_PATTERN.match(policy["id"]):
        problems.append("id 형식이 SEOUL-### 또는 GOV-### 가 아님")

    categories = policy.get("categories") or []
    if not categories:
        problems.append("categories 비어 있음")
    elif not set(categories) <= VALID_CATEGORIES:
        problems.append(f"categories 허용 값 아님: {sorted(set(categories) - VALID_CATEGORIES)}")
    elif len(categories) != len(set(categories)):
        problems.append("categories 중복")
    elif c.CATEGORY_ALL in categories and len(categories) != 1:
        problems.append("all 은 다른 분야와 함께 쓸 수 없음")

    statuses = policy.get("statuses") or []
    if not set(statuses) <= VALID_STATUSES:
        problems.append(f"statuses 허용 값 아님: {sorted(set(statuses) - VALID_STATUSES)}")

    if policy.get("data_status") not in VALID_DATA_STATUS:
        problems.append(f"data_status 허용 값 아님: {policy.get('data_status')!r}")

    if policy.get("source_kind") not in VALID_SOURCE_KIND:
        problems.append(f"source_kind 허용 값 아님: {policy.get('source_kind')!r}")

    for field in DATE_FIELDS:
        value = policy.get(field)
        if value and dl.parse_date(value) is None:
            problems.append(f"{field} 날짜 형식 아님: {value!r}")

    start, end = dl.parse_date(policy.get("apply_start")), dl.parse_date(policy.get("apply_end"))
    if start and end and start > end:
        problems.append("apply_start 가 apply_end 보다 늦음")

    low, high = policy.get("age_min"), policy.get("age_max")
    if low is not None and high is not None and low > high:
        problems.append("age_min 이 age_max 보다 큼")

    sources = policy.get("condition_sources") or {}
    raw_text = policy.get("raw_text") or ""
    for key, sentence in sources.items():
        if sentence and sentence.strip() and sentence.strip() not in raw_text:
            # 인용 검증이 문자열 포함 여부로 동작하므로 여기서 걸러야 한다.
            problems.append(f"condition_sources[{key}] 문장이 raw_text 안에 없음")

    return [f"{policy_id}: {problem}" for problem in problems]


def load_policies(path: str | Path) -> tuple[list[dict], list[str]]:
    """정책 파일을 읽어 통과한 정책과 검수 필요 목록을 돌려준다.

    파일 자체를 읽지 못하면 빈 목록과 사유를 돌려준다. 예외를 올리지 않는 이유는
    정책 데이터가 없어도 서버가 기동해 `/health` 로 상태를 알려야 하기 때문이다
    (docs/03-api-contract.md 1-1).
    """
    file_path = Path(path)
    try:
        payload = json.loads(file_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [], [f"{file_path}: 파일이 없음"]
    except json.JSONDecodeError as error:
        return [], [f"{file_path}: JSON 파싱 실패 ({error.msg})"]

    rows = payload.get("policies") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        return [], [f"{file_path}: policies 배열을 찾을 수 없음"]

    policies: list[dict] = []
    issues: list[str] = []
    seen: set[str] = set()

    for row in rows:
        if not isinstance(row, dict):
            issues.append(f"{file_path}: 정책 항목이 객체가 아님")
            continue

        problems = _validate(row)
        policy_id = row.get("id")
        if policy_id in seen:
            problems.append(f"{policy_id}: id 중복")
        if problems:
            issues.extend(problems)
            continue

        seen.add(policy_id)
        policies.append(row)

    return policies, issues


def count_verified(policies: list[dict]) -> int:
    """검수 완료 정책 수. `/health` 의 verified_policy_count 에 쓴다.

    "검수 완료"는 사람이 수집·검수했고(`source_kind == manual`) 상태가 `verified` 인
    것으로 센다. 문서에 정의가 없어 백엔드A 가 정한 기준이다 (notes/open-items.md 2장).
    """
    return sum(
        1
        for policy in policies
        if policy.get("source_kind") == c.SOURCE_MANUAL
        and policy.get("data_status") == c.VERIFIED
    )
