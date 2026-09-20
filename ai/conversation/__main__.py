"""AI A 파트 준비 상태 보기.

    python -m ai.conversation

``ai/judgment/__main__.py`` 와 같은 역할이다. 무엇이 채워졌고 무엇이 비었는지를
한 화면에서 본다. 12:00 통합 전 자기 파트 점검용이고, 서버가 부르는 코드가 아니다.

파일을 쓰거나 네트워크를 타지 않는다. 종료 코드는 **빠진 것이 있으면 1** 이다.
그래야 나중에 점검을 자동화할 때 통과 여부를 알 수 있다.
"""

from __future__ import annotations

import sys
import unicodedata
from typing import Any, Dict, List, Sequence

from . import answer, cases, fields, followup, pipeline, questions, related


def _width(text: str) -> int:
    """터미널에서 차지하는 칸 수. 한글은 2칸이다.

    단순 문자 수로 정렬하면 한글이 섞인 표가 어긋난다. 윈도우 터미널도 같다.
    """
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _width(text))


def _table(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> List[str]:
    """칸 폭을 맞춘 표. 한글 폭을 고려한다."""
    columns = [list(headers)] + [list(row) for row in rows]
    widths = [
        max(_width(str(row[index])) for row in columns) for index in range(len(headers))
    ]
    lines = ["  ".join(_pad(str(h), w) for h, w in zip(headers, widths)).rstrip()]
    lines.append("  ".join("-" * w for w in widths))
    for row in rows:
        lines.append("  ".join(_pad(str(cell), w) for cell, w in zip(row, widths)).rstrip())
    return lines


def _section(title: str) -> List[str]:
    return ["", title, "=" * _width(title)]


def _question_rows() -> List[List[str]]:
    rows: List[List[str]] = []
    for field in fields.FIELD_ORDER:
        template = questions.get(field)
        if template is None:
            rows.append([field, "없음", "-", "-"])
            continue
        rows.append(
            [
                field,
                template.question,
                str(len(template.options)),
                "허용" if template.allow_free_text else "닫음",
            ]
        )
    return rows


def _followup_demo() -> List[str]:
    """후속 질문 선택 예시. 가짜 미확인 항목으로 어떤 항목이 골라지는지 보여준다."""
    raw: List[Dict[str, Any]] = [
        {"field": fields.LAST_GPA, "policy_id": "P1", "policy_rank": 0, "policy_title": "국가장학금"},
        {"field": fields.INCOME_BRACKET, "policy_id": "P2", "policy_rank": 1, "policy_title": "청년월세지원"},
        {"field": fields.INCOME_BRACKET, "policy_id": "P3", "policy_rank": 2, "policy_title": "청년수당"},
        # 항목 이름 표기가 어긋난 경우. 탈락 기록에 남는 것을 보여주기 위해 일부러 넣는다.
        {"needed_field": "다른 지원 수혜 중", "policy_id": "P4"},
    ]
    converted = pipeline.unknown_items_from(raw)
    lines = [
        f"미확인 항목 {len(raw)}건 투입 → 통과 {len(converted)}건, 탈락 {len(converted.dropped)}건",
    ]
    for entry in converted.dropped:
        lines.append(f"  탈락: {entry.field or '(이름 없음)'} ({entry.reason})")

    chosen = followup.choose(converted.items)
    lines.append(f"고른 항목: {chosen.field if chosen else '(없음)'}")
    payload = followup.next_question(converted.items)
    if payload:
        lines.append(f"  질문: {payload['question']}")
        lines.append(f"  이유: {payload['reason']}")

    skipped = followup.AskedState(skipped={fields.INCOME_BRACKET})
    again = followup.choose(converted.items, skipped)
    lines.append(
        f"소득을 건너뛴 뒤 고른 항목: {again.field if again else '(없음, 더 묻지 않는다)'}"
    )
    lines.append(
        "planned 확인 질문 예: "
        + str(questions.build_planned_question(fields.STATUS, "휴학")["question"])
    )
    return lines


def _related_demo() -> List[str]:
    policies = [
        {
            "policy_id": "P1",
            "status": "check",
            "conditions": [{"result": "unknown", "needed_field": fields.INCOME_BRACKET}],
            "deadline": {"is_imminent": True, "badge": "마감 임박 D-3"},
        },
        {"policy_id": "P2", "status": "unlikely", "conditions": [], "deadline": {}},
    ]
    chips = related.build(policies)["chips"]
    lines = [f"상황 판단 칩 {len(chips)}개"]
    lines.extend(f"  {chip['text']}" for chip in chips)
    fixed = related.build_fixed()["chips"]
    lines.append(f"대체 모드 고정 칩 {len(fixed)}개 (빼는 순서 2번)")
    lines.extend(f"  {chip['text']}" for chip in fixed)
    return lines


def _answer_demo() -> List[str]:
    """답변 검증 예시. 금지 표현과 없는 각주가 실제로 걸리는지 보여준다."""
    footnotes = [{"footnote_id": 1, "excerpt": "월 20만원을 지원합니다"}]
    draft = (
        "신청 가능성이 높은 제도 1개를 찾았어요. "
        "청년월세지원은 월 20만원을 지원해요[1]. "
        "확실히 지원 대상입니다[2]."
    )
    check = answer.validate(draft, footnotes)
    result = answer.sanitize(draft, footnotes)
    rechecked = answer.validate(result.text, footnotes)
    return [
        f"검증 전 문제 {len(check.problems)}건: {', '.join(check.codes()) or '없음'}",
        f"정리 후 문제 {len(rechecked.problems)}건 (계약: 0건)",
        f"제거 {len(result.removals)}건, {result.original_length}자 → {result.final_length}자",
        f"결과: {result.text}",
    ]


def main() -> int:
    lines: List[str] = ["AI A (대화) 준비 상태"]

    missing = questions.missing_templates()
    lines += _section("1. 후속 질문 문구 표 (README 5장)")
    lines.append(
        f"항목 {len(fields.FIELD_ORDER)}개 중 틀 있음 {len(fields.FIELD_ORDER) - len(missing)}개"
        + (f", 빠짐 {missing}" if missing else "")
    )
    lines += _table(("항목", "질문", "선택지", "직접 입력"), _question_rows())

    lines += _section("2. 후속 질문 선택 (README 4장)")
    lines += _followup_demo()

    lines += _section("3. 관련 질문 칩 (README 7장, P1)")
    lines += _related_demo()

    lines += _section("4. 답변 검증과 정리 (README 6장)")
    lines += _answer_demo()

    lines += _section("5. 평가 케이스 (README 8~9장)")
    lines.append(
        f"해석 테스트 {len(cases.INTERPRETATION_CASES)}개 (완료 기준 9개 이상 통과), "
        f"대화 평가 {len(cases.CONVERSATION_CASES)}개 (전부 통과)"
    )
    pending = cases.policy_data_dependent_cases()
    lines.append(
        "검수 데이터가 붙어야 판정할 수 있는 케이스: "
        + (", ".join(case.id for case in pending) if pending else "없음")
    )

    # 아직 안 된 것. 12:00 통합 전에 채워야 하는 것들이다.
    todo = [
        ("모델 어댑터", "llm.LlmAdapter 구현이 StubAdapter 뿐이다. 10:30 모델 확정 후"),
        ("프롬프트 문안", "pipeline.ANSWER_RULE_SLOTS 의 자리를 담당자가 채운다"),
        ("평가 채점기", "cases.py 는 데이터만 있다. 채점 실행 경로가 없다"),
        ("값 표기 통일", "ai/judgment 는 한국어 값, 이 패키지는 영문 snake_case"),
    ]
    lines += _section("6. 아직 안 된 것")
    lines += _table(("무엇", "내용"), [[name, note] for name, note in todo])

    print("\n".join(lines))
    # 빠진 질문 틀이 있으면 실패로 본다. 그 항목은 영원히 안 물어진다.
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
