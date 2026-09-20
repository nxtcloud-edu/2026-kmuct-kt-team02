"""체크리스트 추출 (`ai/judgment/README.md` 8장).

공고 원문에서 필요 서류와 신청 단계를 뽑는다.

| 항목 | 규칙 |
| --- | --- |
| 서류 | 공고에 있는 것만. 줄마다 하나. 발급처가 공고에 있으면 괄호로 |
| 신청 단계 | 공고에 있는 순서대로. **원문에 없는 단계를 추가하지 않는다** |
| 마지막 항목 | 공식 신청 페이지 이동은 프론트가 붙인다. 여기서 넣지 않는다 |

"온라인 신청" 같은 당연해 보이는 단계도 공고에 없으면 넣지 않는다.
절차를 지어내면 사용자가 그 순서대로 준비하다 막힌다.

어디서 가져오는가
-----------------
1차 출처는 정책 데이터의 `documents` 와 `steps` 다. 백엔드A가 공고에서 옮겨 검수한
값이므로 그대로 쓴다 (`docs/04-data-schema.md` 2장).
비어 있을 때만 `raw_text` 에서 찾는다.

`raw_text` 에서 찾는 것은 머리글과 항목 기호에 의존하는 단순한 방법이다.
공고 형식이 제각각이라 놓치는 경우가 있다. 놓치면 빈 목록이 되고,
**없는 항목을 만들어 내지는 않는다.** 그게 더 안전한 실패다.

근거 확인
---------
목록의 각 항목이 `raw_text` 안에 있는지 함께 확인해 ``ungrounded`` 로 돌려준다.
다만 **항목을 버리지는 않는다.** 백엔드A가 발급처를 괄호로 덧붙이는 등 형식을
다듬는 경우가 있어서, 원문에 글자 그대로 없다고 지어낸 것이라 단정할 수 없다.
근거 없는 조건 점검표(README 11장)에서 눈으로 확인할 거리로 남긴다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from ai.judgment.normalize import canonical, normalize

# 줄 앞에 붙는 항목 기호. 내용이 아니므로 떼어낸다.
_BULLET = re.compile(r"^\s*(?:[-–—·•○◦□▢▪※*]+|\(?\d{1,2}[.)]|\d{1,2}\s*단계)\s*")

# 구역 머리글을 찾을 때 쓰는 낱말.
_DOCUMENT_HEADERS = ("제출 서류", "제출서류", "구비 서류", "구비서류", "필요 서류", "필요서류", "서류")
_STEP_HEADERS = ("신청 방법", "신청방법", "신청 절차", "신청절차", "추진 절차", "추진절차", "절차")

# 머리글로 볼 줄. 공고는 ○ ◇ ■ 같은 기호나 "가." 로 구역을 나눈다.
#
# 숫자 머리글("1. 지원 자격")은 일부러 넣지 않는다. 신청 단계도 같은 형태
# ("1. 누리집 접속")라서 구분할 수 없고, 머리글로 오인하면 수집이 중간에 끊긴다.
# 숫자로 구역을 나눈 공고의 머리글을 놓칠 수 있지만, 단계를 통째로 잃는 쪽이 더 나쁘다.
_HEADER_MARK = re.compile("^[ \t]*[○◇■▶◆◈□▣●※]|^[ \t]*[가-힣]\\.[ \t]")


@dataclass
class Checklist:
    """체크리스트.

    documents   필요 서류
    steps       신청 단계 (공고 순서 그대로)
    ungrounded  `raw_text` 에서 찾지 못한 항목. 버리지 않고 점검거리로 남긴다
    source      값을 어디서 가져왔는지 (`field` 또는 `raw_text`)
    """

    documents: List[str] = field(default_factory=list)
    steps: List[str] = field(default_factory=list)
    ungrounded: List[str] = field(default_factory=list)
    source: Dict[str, str] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not self.documents and not self.steps


def _clean_line(line: str) -> str:
    return _BULLET.sub("", canonical(line)).strip()


def split_lines(value: object) -> List[str]:
    """줄마다 하나인 값을 목록으로. 항목 기호를 떼고 중복을 지운다."""
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        raw_lines: Sequence[str] = [str(item) for item in value]
    else:
        raw_lines = str(value).splitlines()

    items: List[str] = []
    seen = set()
    for line in raw_lines:
        text = _clean_line(line)
        if not text:
            continue
        key = normalize(text)
        if key in seen:
            continue
        seen.add(key)
        items.append(text)
    return items


def _is_header(line: str) -> bool:
    return bool(_HEADER_MARK.match(line))


def _section_items(raw_text: str, headers: Sequence[str]) -> List[str]:
    """머리글 다음에 이어지는 항목 줄을 모은다.

    머리글을 만나면 수집을 시작하고, 다음 머리글을 만나면 멈춘다.
    """
    lines = canonical(raw_text).splitlines()
    collected: List[str] = []
    collecting = False

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        if _is_header(line):
            body = _clean_line(line)
            matched = any(header in body for header in headers)
            if collecting and not matched:
                break
            collecting = matched
            # 머리글 줄에 "제출 서류: 주민등록초본" 처럼 값이 붙어 있는 경우
            if matched and ":" in body:
                tail = body.split(":", 1)[1].strip()
                if tail:
                    collected.extend(part.strip() for part in tail.split(",") if part.strip())
            continue

        if collecting:
            text = _clean_line(line)
            if text:
                collected.append(text)

    return split_lines(collected)


def extract_checklist(policy: Dict[str, Any]) -> Checklist:
    """정책 데이터에서 체크리스트를 만든다.

    ``policy`` 는 `docs/04-data-schema.md` 1장의 정책 데이터다.
    `documents`, `steps`, `raw_text` 를 본다.
    """
    raw_text = str(policy.get("raw_text") or "")

    documents = split_lines(policy.get("documents"))
    doc_source = "field"
    if not documents and raw_text:
        documents = _section_items(raw_text, _DOCUMENT_HEADERS)
        doc_source = "raw_text"

    steps = split_lines(policy.get("steps"))
    step_source = "field"
    if not steps and raw_text:
        steps = _section_items(raw_text, _STEP_HEADERS)
        step_source = "raw_text"

    ungrounded: List[str] = []
    if raw_text:
        haystack = normalize(raw_text)
        for item in documents + steps:
            if normalize(item) not in haystack:
                ungrounded.append(item)

    return Checklist(
        documents=documents,
        steps=steps,
        ungrounded=ungrounded,
        source={"documents": doc_source, "steps": step_source},
    )
