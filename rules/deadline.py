"""마감 배지와 데이터 상태 판정 (docs/01-glossary-profile.md 5장).

**D-day 는 코드만 계산한다.** 모든 계산은 한국 시간 기준이고 `today` 를 인자로 받는다.
함수 안에서 현재 시각을 읽지 않는다. 그러지 않으면 경계값 테스트와 정답셋 채점이
실행 날짜에 따라 흔들린다.

배지 문구와 경계는 `frontend/src/lib/deadline.ts` 와 같은 값을 쓴다. 목업 화면과
서버 응답이 같은 문구를 보여야 데모에서 카드가 흔들리지 않는다.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

from . import constants as c

KST = timezone(timedelta(hours=9))

# 날짜는 `YYYY-MM-DD` 문자열만 받는다 (CONTRIBUTING.md 7-3).
# date.fromisoformat 은 3.11부터 `20260920` 같은 압축 형식도 받아들이는데,
# `frontend/src/lib/deadline.ts` 는 이 형식을 거부한다. 양쪽이 같은 값을 같게 읽어야
# 한쪽에서만 날짜가 살아나는 일이 없다.
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def today_kst() -> date:
    """한국 시간 기준 오늘. 어댑터 경계에서만 쓰고 판정 함수에는 인자로 넘긴다."""
    return datetime.now(KST).date()


def parse_date(value: str | date | None) -> date | None:
    """`YYYY-MM-DD` 문자열을 날짜로. 형식이 아니면 None."""
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not _ISO_DATE.match(text):
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def days_until(target: str | date | None, today: date) -> int | None:
    """마감일까지 남은 일수. 오늘이 마감이면 0, 지났으면 음수."""
    parsed = parse_date(target)
    if parsed is None:
        return None
    return (parsed - today).days


def days_since(target: str | date | None, today: date) -> int | None:
    """확인일로부터 지난 일수."""
    parsed = parse_date(target)
    if parsed is None:
        return None
    return (today - parsed).days


def is_closed(policy: dict, today: date) -> bool:
    """마감 여부. 저장된 상태가 closed 면 날짜와 무관하게 마감으로 본다."""
    if policy.get("data_status") == c.CLOSED:
        return True
    remaining = days_until(policy.get("apply_end"), today)
    return remaining is not None and remaining < 0


def is_upcoming(policy: dict, today: date) -> bool:
    """접수 시작일이 오늘 이후인가."""
    start = days_until(policy.get("apply_start"), today)
    return start is not None and start > 0


def needs_recheck(policy: dict, today: date) -> bool:
    """확인일로부터 14일을 넘겼는가. 저장된 상태가 recheck 면 그대로 존중한다."""
    if policy.get("data_status") == c.RECHECK:
        return True
    elapsed = days_since(policy.get("checked_at"), today)
    return elapsed is not None and elapsed > c.RECHECK_DAYS


def resolve_data_status(policy: dict, today: date) -> str:
    """날짜에서 실제 데이터 상태를 계산한다. 판정 순서를 고정한다.

    closed → upcoming → recheck → verified 순서로 본다. recheck 는 배지를 덮지 않고
    `data_status` 로만 전달해, 프론트가 기존 배지에 "재확인 필요"를 덧붙이게 한다
    (`frontend/src/lib/deadline.ts` 의 needsRecheck 와 같은 방식).
    """
    if is_closed(policy, today):
        return c.CLOSED
    if is_upcoming(policy, today):
        return c.UPCOMING
    if needs_recheck(policy, today):
        return c.RECHECK
    return c.VERIFIED


def compute_deadline(policy: dict, today: date) -> dict:
    """Deadline 계약(server/schemas.py)에 맞는 dict 를 만든다."""
    apply_start = policy.get("apply_start")
    apply_end = policy.get("apply_end")

    if is_upcoming(policy, today):
        start = parse_date(apply_start)
        label = f"{start.month}.{start.day} 시작" if start else "시작 예정"
        return {
            "apply_start": apply_start,
            "apply_end": apply_end,
            "d_day": None,
            "badge": f"접수 예정 ({label})",
            "is_imminent": False,
        }

    d_day = days_until(apply_end, today)

    if d_day is None:
        badge, imminent = "상시 접수", False
    elif d_day == 0:
        badge, imminent = "오늘 마감", True
    elif 0 < d_day <= c.IMMINENT_DAYS:
        badge, imminent = f"마감 임박 D-{d_day}", True
    elif d_day > c.IMMINENT_DAYS:
        badge, imminent = f"D-{d_day}", False
    else:
        # 마감이 지난 정책은 후보에서 빠지므로 여기까지 오지 않는 것이 정상이다.
        badge, imminent = "접수 마감", False

    return {
        "apply_start": apply_start,
        "apply_end": apply_end,
        "d_day": d_day,
        "badge": badge,
        "is_imminent": imminent,
    }
