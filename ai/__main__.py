"""모델을 직접 두드려 보는 도구.

    python -m ai                 상태 확인 (키·모델·연결)
    python -m ai models          내 키로 부를 수 있는 모델 별칭 목록
    python -m ai ping            모델에 한 문장 보내고 응답·지연 확인
    python -m ai judge           예외 조건 판정만 (AI B)
    python -m ai answer          답변 작성만 (AI A)
    python -m ai chat            한 턴 전체 (판정 → 인용 검증 → 답변 → 후속 질문 → 칩)
    python -m ai chat "메시지"    사용자 메시지를 넣어 한 턴

**키 값을 출력하지 않는다.** 어떤 경로에서도 길이와 앞 네 글자까지만 보여 준다. 화면 공유나
캡처로 키가 새는 것이 이 도구의 유일한 위험이라, 그 자리를 아예 두지 않는다.

여기 있는 정책 데이터는 **시험용**이다. 실제 공고가 아니고 ``source_url`` 도 예시다.
공고 원문(``raw_text``)에 예외 조건 문장(``exceptions_text``)이 그대로 들어 있어야 인용
검증을 통과한다(``ai/judgment/citation.py`` 의 ``raw_text_covers``). 시험용 데이터에서
그 불변식을 깨면 "판정은 됐는데 각주가 하나도 안 나온다"가 되고, 원인을 데이터가 아니라
코드에서 찾게 된다.
"""

from __future__ import annotations

import sys
import time
from typing import Any, Dict, List, Optional

from ai import envfile, gateway, runtime, turn
from ai.conversation import answer, fields

# ---------------------------------------------------------------------------
# 시험용 정책 (실제 공고가 아니다)
# ---------------------------------------------------------------------------

_RAW_TEXT = (
    "○ 지원 대상\n"
    "  - 서울특별시에 거주하는 만 19세 이상 34세 이하 청년\n"
    "  - 미취업 상태이거나 주 30시간 미만 근로 중인 자\n"
    "○ 제외 대상\n"
    "  - 타 청년 지원금을 수혜 중인 자는 중복 신청할 수 없습니다\n"
    "  - 최근 3년 이내 본 사업에 참여한 이력이 있는 자\n"
    "○ 지원 내용\n"
    "  - 월 50만원을 최대 6개월 지원합니다\n"
)

SAMPLE_POLICIES: List[Dict[str, Any]] = [
    {
        "id": "SEOUL-001",
        "policy_id": "SEOUL-001",
        "title": "청년 월세 지원",
        "agency": "서울특별시",
        "categories": ["housing"],
        "status": answer.CHECK,
        "benefit": "월 20만원 최대 10개월",
        "source_url": "https://example.seoul.go.kr/policy/001",
        "checked_at": "2026-09-01",
        "raw_text": _RAW_TEXT,
        "exceptions_text": "타 청년 지원금을 수혜 중인 자는 중복 신청할 수 없습니다",
        "conditions": [],
        "deadline": {"badge": "마감 임박 D-5", "d_day": 5, "is_imminent": True},
    },
    {
        "id": "SEOUL-002",
        "policy_id": "SEOUL-002",
        "title": "청년 취업 장려금",
        "agency": "서울특별시",
        "categories": ["job"],
        "status": answer.CHECK,
        "benefit": "월 50만원 최대 6개월",
        "source_url": "https://example.seoul.go.kr/policy/002",
        "checked_at": "2026-09-01",
        "raw_text": _RAW_TEXT,
        "exceptions_text": "최근 3년 이내 본 사업에 참여한 이력이 있는 자",
        "conditions": [],
        "deadline": {"badge": "상시 접수", "d_day": None, "is_imminent": False},
    },
]

SAMPLE_PROFILE: Dict[str, Any] = {
    "age": 23,
    "region": "seoul",
    "district": "관악구",
    "status": "enrolled",
    "categories": ["housing", "job"],
    "income_bracket": "unknown",
}


# ---------------------------------------------------------------------------
# 출력 도우미
# ---------------------------------------------------------------------------


def _hide(value: Optional[str]) -> str:
    """키를 가린다. 길이와 앞 네 글자까지만."""
    if not value:
        return "(없음)"
    text = str(value)
    return f"{text[:4]}... ({len(text)}자)"


def _rule(title: str) -> None:
    print()
    print(f"── {title} " + "─" * max(0, 56 - len(title)))


def _fail(message: str) -> int:
    print(f"\n실패: {message}")
    return 1


# ---------------------------------------------------------------------------
# 명령
# ---------------------------------------------------------------------------


def cmd_status() -> int:
    """키·모델·연결 상태. 무엇이 없어서 안 도는지 알려준다."""
    _rule("설정")
    path = envfile.source_path()
    print(f"읽은 .env       {path or '(없음)'}")
    print(f".env 에 채워짐  {', '.join(envfile.loaded_names()) or '(없음)'}")

    config = gateway.load_config()
    if config is None:
        gaps = gateway.missing_settings()
        print(f"설정            없음. 채워야 할 것: {', '.join(gaps)}")
    else:
        print(f"게이트웨이      {config.base_url}")
        print(f"모델 별칭       {config.model}")
        print(f"키              {_hide(config.api_key)}")

    _rule("모듈")
    from ai import MISSING_MODULES, loaded_modules

    print(f"불러온 모듈     {', '.join(loaded_modules())}")
    if MISSING_MODULES:
        for line in MISSING_MODULES:
            print(f"  실패          {line}")
    if turn.MISSING:
        for line in turn.MISSING:
            print(f"  turn 경고     {line}")

    _rule("실행 준비")
    rt = runtime.for_turn()
    print(f"ready           {rt.ready}")
    if rt.missing:
        print(f"없는 것         {', '.join(rt.missing)}")
        print()
        print("→ .env 에 API_KEY 와 LLM_MODEL 을 채우면 된다.")
        print("  모델 별칭 목록:  python -m ai models")
        return 1

    print()
    print("→ 준비됐다. python -m ai ping 으로 실제 호출을 확인한다.")
    return 0


def cmd_models() -> int:
    """내 키로 부를 수 있는 모델 별칭 목록."""
    config = gateway.load_config()
    if config is None:
        return _fail(f"설정이 없다. 채워야 할 것: {', '.join(gateway.missing_settings())}")

    url = f"{config.base_url.rstrip('/')}/models"
    _rule("모델 별칭")
    print(f"조회            {url}")
    try:
        import json
        import urllib.request

        request = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {config.api_key}"}
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            body = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - 진단 도구다
        # 403 응답 본문에 허용 목록이 들어 있는 경우가 있다 (대회 가이드 5장).
        detail = getattr(exc, "read", None)
        if callable(detail):
            try:
                print(detail().decode("utf-8", "replace")[:500])
            except Exception:  # noqa: BLE001
                pass
        return _fail(f"{type(exc).__name__}: {str(exc)[:200]}")

    names = [
        str(item.get("id"))
        for item in (body.get("data") or [])
        if isinstance(item, dict) and item.get("id")
    ]
    if not names:
        print("(목록이 비어 있다)")
        return 1
    for name in names:
        mark = "  ← 지금 설정" if name == config.model else ""
        print(f"  {name}{mark}")
    if config.model not in names:
        print()
        print(f"주의: 설정된 별칭 {config.model!r} 이 목록에 없다. 호출하면 403 이 난다.")
        return 1
    return 0


def cmd_ping(message: str = "한 문장으로 자기소개해 주세요") -> int:
    """모델에 한 문장 보내고 응답과 지연을 본다."""
    config = gateway.load_config()
    if config is None:
        return _fail(f"설정이 없다. 채워야 할 것: {', '.join(gateway.missing_settings())}")

    _rule("한 번 호출 (스트리밍 아님)")
    client = gateway.GatewayClient()
    started = time.monotonic()
    try:
        body = client.complete(
            system="너는 간결하게 답한다.", user=message, timeout=25.0
        )
    except Exception as exc:  # noqa: BLE001 - 진단 도구다
        return _fail(f"{type(exc).__name__}: {str(exc)[:200]}")
    print(f"걸린 시간       {time.monotonic() - started:.2f}초")
    print(f"응답            {body[:300]}")

    _rule("스트리밍 (첫 조각 지연이 예산을 정한다)")
    started = time.monotonic()
    first: Optional[float] = None
    count = 0
    try:
        for piece in client.stream(
            system="너는 간결하게 답한다.", user=message, timeout=25.0
        ):
            count += 1
            if first is None:
                first = time.monotonic() - started
    except Exception as exc:  # noqa: BLE001
        return _fail(f"{type(exc).__name__}: {str(exc)[:200]}")

    total = time.monotonic() - started
    print(f"첫 조각         {first:.2f}초" if first is not None else "첫 조각         (없음)")
    print(f"전체            {total:.2f}초 / 조각 {count}개")

    # 실측이 정책값을 넘으면 그 단계는 항상 실패한다. 그 사실을 여기서 드러낸다.
    from ai.conversation import llm

    budget = llm.policy_for(llm.CallSite.ANSWER).timeout_s
    if first is not None and first > budget:
        print()
        print(
            f"주의: 첫 조각 {first:.2f}초가 답변 예산 {budget}초를 넘는다. "
            "이 상태로는 답변이 항상 비어서 나간다."
        )
        print("  → ai/conversation/llm.py 의 CALL_SITE_POLICIES 를 올려야 한다.")
        return 1
    return 0


def cmd_judge() -> int:
    """예외 조건 판정만 (AI B)."""
    rt = runtime.for_turn()
    if rt.judge_policies is None:
        return _fail(f"판정기를 만들 수 없다: {', '.join(rt.missing)}")

    _rule("예외 조건 판정")
    started = time.monotonic()
    verdicts = rt.judge_policies(SAMPLE_POLICIES, SAMPLE_PROFILE)
    print(f"걸린 시간       {time.monotonic() - started:.2f}초")
    for verdict in verdicts:
        print()
        print(f"  정책          {verdict.get('policy_id')}")
        for item in verdict.get("conditions") or ():
            print(
                f"    조건        {item.get('name')!r} / {item.get('result')} "
                f"/ 필요항목 {item.get('needed_field')}"
            )
            excerpt = item.get("excerpt")
            print(f"    발췌        {str(excerpt)[:60] if excerpt else '(없음)'}")
        removed = verdict.get("removed") or ()
        if removed:
            print(f"    인용 제거   {[r.get('reason') for r in removed]}")
    return 0


def cmd_answer() -> int:
    """답변 작성만 (AI A). 프롬프트가 무엇을 지시했는지도 보여 준다."""
    rt = runtime.for_turn()
    if rt.answer_writer is None:
        return _fail(f"답변 작성기를 만들 수 없다: {', '.join(rt.missing)}")

    from ai.conversation import pipeline

    footnotes = [
        {
            "footnote_id": 1,
            "policy_id": "SEOUL-001",
            "excerpt": "월 50만원을 최대 6개월 지원합니다",
            "source_url": "https://example.seoul.go.kr/policy/001",
        }
    ]
    policies = [dict(SAMPLE_POLICIES[0])]
    policies[0]["conditions"] = [
        {
            "name": "타 지원금 수혜 여부",
            "result": "unknown",
            "footnote_id": 1,
            "excerpt": "월 50만원을 최대 6개월 지원합니다",
            "needed_field": fields.OTHER_BENEFIT,
        }
    ]

    prompt = pipeline.build_answer_prompt(
        answer.build_skeleton(policies),
        profile=SAMPLE_PROFILE,
        policies=policies,
        footnotes=footnotes,
    )

    _rule("프롬프트")
    todo = prompt.user.count(pipeline.PROMPT_TODO)
    print(f"user 길이       {len(prompt.user)}자")
    print(f"빈 문안 자리    {todo}개" + ("  ← 채워야 한다" if todo else ""))

    _rule("모델 답변 (정리 전)")
    started = time.monotonic()
    raw = rt.answer_writer(prompt)
    print(f"걸린 시간       {time.monotonic() - started:.2f}초")
    print(raw[:600] if raw else "(비었음)")

    _rule("정리·검증 후")
    finished = pipeline.finish_turn(raw, footnotes=footnotes, policies=policies)
    print(f"실패            {finished.answer_failed}")
    if finished.blocking_codes:
        print(f"버린 이유       {', '.join(finished.blocking_codes)}")
    if finished.removals:
        for removal in finished.removals:
            print(f"  제거          [{removal.code}] {removal.text[:50]}")
    print()
    print(finished.answer_text or "(비었음)")
    return 1 if finished.answer_failed else 0


def cmd_chat(message: str = "") -> int:
    """한 턴 전체. 서버가 하는 일을 그대로 한다."""
    rt = runtime.for_turn()
    if not rt.ready:
        print(f"주의: 모델을 부를 수 없다 ({', '.join(rt.missing)}).")
        print("      판정은 건너뛰고 답변은 실패한다. 카드는 그대로 나간다.")

    model_output: Dict[str, Any] = {"intent": fields.FIND_POLICY}
    if message:
        print(f"입력            {message!r}")
        if rt.interpret is None:
            print("주의: 모델이 없어 해석을 건너뛴다. 의도만 find_policy 로 넣는다.")
        else:
            _rule("해석 (3단계)")
            started = time.monotonic()
            model_output = rt.interpret(message, SAMPLE_PROFILE)
            print(f"걸린 시간       {time.monotonic() - started:.2f}초")
            print(f"모델 출력       {model_output}")

    _rule("한 턴")
    started = time.monotonic()
    result = turn.run_turn(
        SAMPLE_PROFILE,
        model_output,
        policies=SAMPLE_POLICIES,
        judge_policies=rt.judge_policies,
        answer_writer=rt.answer_writer,
    )
    elapsed = time.monotonic() - started
    print(f"걸린 시간       {elapsed:.2f}초 (상한 20초)")

    _rule("정책 카드")
    for policy in result.policies:
        print(f"  {policy.get('policy_id')} {policy.get('title')} [{policy.get('status')}]")
        for item in policy.get("conditions") or ():
            print(
                f"    [{item.get('footnote_id')}] {item.get('name')} / {item.get('result')}"
            )

    _rule("각주")
    for row in result.footnotes:
        print(f"  [{row['footnote_id']}] {row['excerpt'][:60]}")
    if not result.footnotes:
        print("  (없음)")

    _rule("답변")
    if result.answer_failed:
        codes = result.metrics.get("answer", {}).get("blocking_codes")
        print(f"실패. 이유: {codes or '(모델 호출 안 됨)'}")
        print("→ 화면에는 카드가 남고 실패 문구가 나간다.")
    else:
        print(result.answer_text)
        print(f"\n(델타 {len(result.answer_deltas)}개로 흘러간다)")

    _rule("프로필 갱신")
    if result.profile_update:
        print(f"  바뀐 항목     {result.profile_update.get('changed_fields')}")
        print(f"  안내          {result.profile_update.get('message') or '(없음)'}")
    else:
        print("  (바뀐 것 없음)")
    if result.needs_planned_confirmation:
        print("  → 바뀐 뒤 기준으로 볼지 확인 질문이 필요하다 (planned 변경 보류 중)")
    if result.stop_here:
        print()
        print(f"※ 범위 밖·잡담으로 턴을 끝냈다: {result.fixed_reply}")

    _rule("후속 질문")
    if result.followup:
        print(f"  항목          {result.followup.get('field')}")
        print(f"  질문          {result.followup.get('question')}")
        print(f"  이유          {result.followup.get('reason')}")
        options = result.followup.get("options") or ()
        print(f"  선택지        {[o.get('label') for o in options]}")
    else:
        print("  (물을 것 없음)")

    _rule("관련 질문 칩")
    print(f"  {result.related.get('questions') if result.related else '(없음)'}")

    _rule("지표 (화면에 쓰지 않는다)")
    judgment = result.metrics.get("judgment", {})
    print(f"  판정          {judgment}")
    print(f"  빠진 것       {rt.missing or '(없음)'}")
    return 1 if result.answer_failed else 0


USAGE = __doc__ or ""


def main(argv: Optional[List[str]] = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    command = args[0] if args else "status"
    rest = " ".join(args[1:])

    if command in {"-h", "--help", "help"}:
        print(USAGE)
        return 0

    handlers = {
        "status": cmd_status,
        "models": cmd_models,
        "judge": cmd_judge,
        "answer": cmd_answer,
    }
    if command in handlers:
        return handlers[command]()
    if command == "ping":
        return cmd_ping(rest or "한 문장으로 자기소개해 주세요")
    if command == "chat":
        return cmd_chat(rest)

    print(f"모르는 명령: {command!r}")
    print(USAGE)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
