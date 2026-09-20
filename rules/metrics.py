"""데이터 조건 점검과 채점 (data/README.md 6장, data/eval/README.md 2장).

두 가지를 한다.

1. 데이터 전체 조건 점검 — 개별 정책이 다 맞아도 이 조건이 깨지면 데모가 살지 않는다
2. 대표 프로필 채점 — 추천 정확도와 판정 일치율

**정답셋은 이 저장소에 없다.** 백엔드A 로컬이나 비공개 시트에만 둔다. 채점할 때 경로를
인자로 넘긴다 (data/eval/README.md).

채점 중에 정답셋이 틀렸다고 판단되면 고칠 수 있지만, **실제 결과에 맞춰 정답을 고치면
채점이 아니라 사후 합리화다.** 무엇을 왜 고쳤는지 남긴다.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from . import constants as c
from . import deadline as dl
from . import engine, loader

# data/README.md 6장의 데이터 전체 조건
MIN_VERIFIED = 20
MIN_OPEN_OR_ALWAYS = 15
MIN_INCOME_FREE_RATIO = 0.30
MIN_EXTRA_CONDITION = 5
MIN_DEMO_UNLIKELY = 2

# data/README.md 5장 분야별 목표 건수
CATEGORY_TARGETS = {
    "scholarship": 5,
    "living": 5,
    "job": 6,
    "culture": 5,
    "housing": 5,
}

# 정확도 목표 (docs/06-demo-and-metrics.md 5장)
TARGET_ACCURACY = 0.80


def _income_free(policy: dict) -> bool:
    """규칙 엔진이 소득을 묻지 않는 정책인가.

    `income_max_pct` 가 비었더라도 `exceptions_text` 에 소득 문장이 있으면 AI B 가
    판정한다. 그건 "소득 조건이 없는 정책"이 아니므로 구분해 센다.
    """
    if policy.get("income_max_pct") is not None:
        return False
    text = policy.get("exceptions_text") or ""
    return "소득" not in text and "연소득" not in text


def check_data_conditions(
    policies: list[dict],
    profiles: list[dict],
    today: date,
) -> dict:
    """데이터 전체 조건을 집계한다. 미달 항목도 그대로 돌려준다."""
    total = len(policies)
    verified = loader.count_verified(policies)

    open_or_always = [
        policy["id"]
        for policy in policies
        if not dl.is_closed(policy, today) and not dl.is_upcoming(policy, today)
    ]
    income_free = [policy["id"] for policy in policies if _income_free(policy)]
    with_extra = [policy["id"] for policy in policies if policy.get("extra_conditions")]

    category_counts = {name: 0 for name in CATEGORY_TARGETS}
    for policy in policies:
        for name in policy.get("categories") or []:
            if name in category_counts:
                category_counts[name] += 1

    demo = next((entry for entry in profiles if entry.get("데모_프로필")), None)
    demo_unlikely = 0
    if demo is not None:
        result = engine.evaluate_policies(demo["profile"], policies, today)
        demo_unlikely = result["hidden_unlikely_count"]

    ratio = len(income_free) / total if total else 0.0

    checks = [
        {
            "항목": "검수 통과 정책 수",
            "값": verified,
            "목표": f"{MIN_VERIFIED} 이상",
            "충족": verified >= MIN_VERIFIED,
        },
        {
            "항목": "접수 중·상시 접수",
            "값": len(open_or_always),
            "목표": f"{MIN_OPEN_OR_ALWAYS} 이상",
            "충족": len(open_or_always) >= MIN_OPEN_OR_ALWAYS,
        },
        {
            "항목": "소득 조건 없는 정책 비율",
            "값": f"{len(income_free)}/{total} ({ratio:.0%})",
            "목표": f"{MIN_INCOME_FREE_RATIO:.0%} 이상",
            "충족": ratio >= MIN_INCOME_FREE_RATIO,
        },
        {
            "항목": "추가 항목 조건이 있는 정책",
            "값": len(with_extra),
            "목표": f"{MIN_EXTRA_CONDITION} 이상",
            "충족": len(with_extra) >= MIN_EXTRA_CONDITION,
        },
        {
            "항목": "데모 프로필에서 unlikely",
            "값": demo_unlikely,
            "목표": f"{MIN_DEMO_UNLIKELY} 이상",
            "충족": demo_unlikely >= MIN_DEMO_UNLIKELY,
        },
    ]
    for name, target in CATEGORY_TARGETS.items():
        count = category_counts[name]
        checks.append(
            {
                "항목": f"분야 {name}",
                "값": count,
                "목표": f"{target} 이상",
                "충족": count >= target,
            }
        )

    return {
        "checks": checks,
        "미달": [item["항목"] for item in checks if not item["충족"]],
        "상세": {
            "접수_중_상시": open_or_always,
            "소득_조건_없음": income_free,
            "추가_항목_있음": with_extra,
        },
    }


def score_recommendations(
    policies: list[dict],
    profiles: list[dict],
    answers: dict,
    today: date,
) -> dict:
    """정답셋과 대조해 추천 정확도와 판정 일치율을 계산한다.

    추천 정확도 = 프로필별 상위 3개 안에 들어온 정답 정책 수 합계 ÷ (프로필 수 × 3)
    판정 일치율 = 기대 상태와 실제 상태가 같은 정책 수 ÷ 기대 상태를 적은 정책 수
    """
    expected = answers.get("profiles") or {}
    rows = []
    hit_total = slot_total = 0
    status_match = status_total = 0

    for entry in profiles:
        profile_id = entry["id"]
        answer = expected.get(profile_id)
        if not answer:
            continue

        result = engine.evaluate_policies(entry["profile"], policies, today)
        actual_top = [card["policy_id"] for card in result["policies"][:3]]
        actual_status = {
            card["policy_id"]: card["status"]
            for card in result["policies"] + result["hidden_unlikely"]
        }

        wanted_top = answer.get("expected_top3") or []
        hits = [pid for pid in wanted_top if pid in actual_top]
        hit_total += len(hits)
        slot_total += 3

        wanted_status = answer.get("expected_status") or {}
        matched = [
            pid for pid, status in wanted_status.items() if actual_status.get(pid) == status
        ]
        status_match += len(matched)
        status_total += len(wanted_status)

        rows.append(
            {
                "프로필": profile_id,
                "기대_상위3": wanted_top,
                "실제_상위3": actual_top,
                "적중": len(hits),
                "놓친_정책": [pid for pid in wanted_top if pid not in actual_top],
                "상태_일치": f"{len(matched)}/{len(wanted_status)}" if wanted_status else "-",
                "상태_불일치": {
                    pid: {"기대": status, "실제": actual_status.get(pid)}
                    for pid, status in wanted_status.items()
                    if actual_status.get(pid) != status
                },
            }
        )

    accuracy = hit_total / slot_total if slot_total else 0.0
    agreement = status_match / status_total if status_total else None

    return {
        "rows": rows,
        "추천_정확도": accuracy,
        "적중": hit_total,
        "전체_칸": slot_total,
        "판정_일치율": agreement,
        "목표_충족": accuracy >= TARGET_ACCURACY,
    }


def _load_profiles(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))["profiles"]


def _print_conditions(report: dict) -> None:
    print("데이터 전체 조건")
    for item in report["checks"]:
        mark = "OK  " if item["충족"] else "미달"
        print(f"  {mark} {item['항목']}: {item['값']} (목표 {item['목표']})")
    if report["미달"]:
        print(f"\n미달 {len(report['미달'])}건: {', '.join(report['미달'])}")
    else:
        print("\n전 항목 충족")


def _print_score(report: dict) -> None:
    print("대표 프로필 채점")
    for row in report["rows"]:
        print(f"  {row['프로필']}: 적중 {row['적중']}/3 | 상태 {row['상태_일치']}")
        if row["놓친_정책"]:
            print(f"    놓침: {row['놓친_정책']} | 실제 상위3: {row['실제_상위3']}")
        if row["상태_불일치"]:
            print(f"    상태 불일치: {row['상태_불일치']}")
    print(
        f"\n추천 정확도 {report['적중']}/{report['전체_칸']} "
        f"({report['추천_정확도']:.1%}) 목표 {TARGET_ACCURACY:.0%} "
        f"{'통과' if report['목표_충족'] else '미달'}"
    )
    if report["판정_일치율"] is not None:
        print(f"판정 일치율 {report['판정_일치율']:.1%}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="데이터 조건 점검과 대표 프로필 채점")
    parser.add_argument("--policies", default="data/policies/policies.json")
    parser.add_argument("--profiles", default="data/profiles/profiles.json")
    parser.add_argument(
        "--answers",
        help="정답셋 경로. 저장소에 없다. 로컬이나 비공개 시트에서 가져온다",
    )
    parser.add_argument("--today", help="기준일 YYYY-MM-DD. 없으면 한국 시간 오늘")
    args = parser.parse_args(argv)

    today = dl.parse_date(args.today) or dl.today_kst()
    policies, issues = loader.load_policies(args.policies)
    if issues:
        print(f"검수 필요 {len(issues)}건")
        for issue in issues[:10]:
            print(f"  - {issue}")
        print()

    profiles = _load_profiles(Path(args.profiles))

    print(f"기준일 {today} · 정책 {len(policies)}건 · 프로필 {len(profiles)}개\n")
    conditions = check_data_conditions(policies, profiles, today)
    _print_conditions(conditions)

    if not args.answers:
        print("\n정답셋 경로(--answers)가 없어 채점을 건너뛴다.")
        return 0

    answers = json.loads(Path(args.answers).read_text(encoding="utf-8"))
    print()
    score = score_recommendations(policies, profiles, answers, today)
    _print_score(score)
    return 0 if score["목표_충족"] else 1


if __name__ == "__main__":
    sys.exit(main())
