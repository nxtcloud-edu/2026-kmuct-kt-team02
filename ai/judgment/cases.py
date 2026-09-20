"""예외 조건 판정 평가 케이스 J1~J8 (설계서 6-7).

목적
----
예외 조건 판정이 설계서 6-2 기준대로 동작하는지 확인한다.
특히 **미충족 오판**을 잡는 것이 핵심이다. 사용자에게 "안 된다"고 잘못 말하는 것이
가장 나쁜 실패이고, 완료 기준도 미충족 오판 0건이다.

채워야 할 것
------------
각 케이스의 ``raw_text`` 와 ``exceptions_text`` 는 **우리 팀이 검수한 정책의 공고
원문 문장으로 바꿔야 한다.** 지금 들어 있는 문장은 실제 공고의 표현 방식을 따라
쓴 대체물이고, ``source_url`` 이 None 인 케이스가 아직 실제 문장이 아니라는 표시다.

실제 문장으로 바꿔야 하는 이유는 두 가지다.

1. 공고 문장은 예상보다 지저분하다. 괄호 주석, 표를 풀어 쓴 줄, "등", "그 밖에"
   같은 열린 표현이 섞인다. 깔끔한 문장으로만 평가하면 본선에서 다르게 동작한다.
2. 평가에 쓴 ``raw_text`` 가 실제 정책 데이터와 다르면 인용 검증 결과도 달라진다.

바꾸는 방법은 ``exceptions_text`` 를 공고에서 복사해 붙이고, 그 문장을 포함한
자격·제외 대상 단락을 ``raw_text`` 에 그대로 넣고, ``source_url`` 을 채우는 것이다.
문장을 다듬거나 요약하면 안 된다. 인용 검증이 깨진다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

# 조건 결과 값 (설계서 1-1)
MET = "충족"
UNMET = "미충족"
UNKNOWN = "미확인"

# 필요한 추가 항목으로 지정할 수 없는 경우에 쓰는 값 (설계서 6-2)
ASK_NOTICE = "공고 확인 필요"


@dataclass(frozen=True)
class ExceptionCase:
    """예외 조건 판정 평가 케이스 하나.

    id                    J1~J8
    pattern               예외 문장 유형. 설계서 6-7 표의 분류
    raw_text              공고 원문. exceptions_text 를 반드시 포함해야 한다
    exceptions_text       판정 대상이 되는 예외 조건 문장
    profile               사용자 프로필 (설계서 1-2 허용 값)
    expected_result       기대 조건 결과
    expected_needed_field 기대하는 필요한 추가 항목. 미확인일 때만 의미가 있다
    source_url            실제 공고 주소. None 이면 아직 실제 문장이 아니라는 뜻
    policy_id             연결된 정책 번호. 실제 데이터 연결 후 채운다
    note                  이 케이스가 무엇을 확인하는지
    """

    id: str
    pattern: str
    raw_text: str
    exceptions_text: str
    profile: Dict[str, object]
    expected_result: str
    expected_needed_field: Optional[str] = None
    source_url: Optional[str] = None
    policy_id: Optional[str] = None
    note: str = ""

    @property
    def is_placeholder(self) -> bool:
        """실제 공고 문장으로 채워지지 않은 케이스."""
        return self.source_url is None


def _raw(header: str, *lines: str) -> str:
    """공고 단락 형태로 원문을 구성한다.

    실제 공고는 항목 기호와 들여쓰기가 섞여 있다. 그 형태를 유지해서
    정규화와 인용 검증이 실제 조건에서 동작하는지 함께 확인한다.
    """
    body = "\n".join(f"  - {line}" for line in lines)
    return f"○ {header}\n{body}\n"


# 프로필 기본값. 케이스마다 필요한 항목만 덮어쓴다.
# 설계서 1-2 허용 값을 따른다.
BASE_PROFILE: Dict[str, object] = {
    "나이": 23,
    "거주지": "서울",
    "자치구": None,
    "현재 상태": "재학",
    "관심 분야": ["취업·훈련"],
    "가구 소득": "잘 모르겠어요",
}


def profile(**overrides: object) -> Dict[str, object]:
    merged = dict(BASE_PROFILE)
    merged.update(overrides)
    return merged


# --------------------------------------------------------------------------
# J1~J8
# --------------------------------------------------------------------------

_LEAVE_RAW = _raw(
    "제외 대상",
    "휴학생은 지원 대상에서 제외합니다",
    "재학 중인 대학생 중 직전 학기를 이수하지 않은 자",
)

_DUP_RAW = _raw(
    "제외 대상",
    "타 청년 지원금을 수혜 중인 자는 중복 신청할 수 없습니다",
    "국가 및 지방자치단체의 유사 사업 참여자",
)

_REPEAT_RAW = _raw(
    "제외 대상",
    "최근 3년 이내 본 사업에 참여한 이력이 있는 자는 신청할 수 없습니다",
)

_GRADE_RAW = _raw(
    "지원 자격",
    "직전 학기 성적이 100점 만점 기준 80점 이상인 자",
)

_VAGUE_RAW = _raw(
    "제외 대상",
    "그 밖에 사업 목적에 부합하지 않는다고 심의위원회가 판단하는 부적격자",
)

_CAMPUS_RAW = _raw(
    "지원 자격",
    "서울특별시에 소재한 대학에 재학 중인 자",
)


CASES: List[ExceptionCase] = [
    ExceptionCase(
        id="J1",
        pattern="휴학생 제외",
        raw_text=_LEAVE_RAW,
        exceptions_text="휴학생은 지원 대상에서 제외합니다",
        profile=profile(**{"현재 상태": "휴학"}),
        expected_result=UNMET,
        note="프로필에 명시된 값이 제외 대상에 분명히 해당한다. 미충족으로 단정해도 되는 유일한 유형.",
    ),
    ExceptionCase(
        id="J2",
        pattern="휴학생 제외",
        raw_text=_LEAVE_RAW,
        exceptions_text="휴학생은 지원 대상에서 제외합니다",
        profile=profile(**{"현재 상태": "재학"}),
        expected_result=MET,
        note="제외 대상에 해당하지 않음이 프로필로 분명하다.",
    ),
    ExceptionCase(
        id="J3",
        pattern="타 지원금 수혜자 제외",
        raw_text=_DUP_RAW,
        exceptions_text="타 청년 지원금을 수혜 중인 자는 중복 신청할 수 없습니다",
        profile=profile(),
        expected_result=UNKNOWN,
        expected_needed_field="다른 지원 수혜 중",
        note="프로필에 정보가 없다. 대학생이라 지원금을 안 받을 것이라고 추정하면 안 된다.",
    ),
    ExceptionCase(
        id="J4",
        pattern="타 지원금 수혜자 제외",
        raw_text=_DUP_RAW,
        exceptions_text="타 청년 지원금을 수혜 중인 자는 중복 신청할 수 없습니다",
        profile=profile(**{"다른 지원 수혜 중": "없음"}),
        expected_result=MET,
        note="추가 항목 답변이 있으면 충족으로 확정된다.",
    ),
    ExceptionCase(
        id="J5",
        pattern="최근 n년 내 동일 사업 참여자 제외",
        raw_text=_REPEAT_RAW,
        exceptions_text="최근 3년 이내 본 사업에 참여한 이력이 있는 자는 신청할 수 없습니다",
        profile=profile(),
        expected_result=UNKNOWN,
        expected_needed_field=ASK_NOTICE,
        note="과거 참여 이력은 1-2 추가 항목에 없다. 새 항목을 만들지 말고 공고 확인 필요로 둔다.",
    ),
    ExceptionCase(
        id="J6",
        pattern="학점 기준 이상",
        raw_text=_GRADE_RAW,
        exceptions_text="직전 학기 성적이 100점 만점 기준 80점 이상인 자",
        profile=profile(**{"직전 학기 성적": "모름"}),
        expected_result=UNKNOWN,
        expected_needed_field="직전 학기 성적",
        note="사용자가 모른다고 답한 항목. 건너뛴 것과 같게 미확인으로 남긴다.",
    ),
    ExceptionCase(
        id="J7",
        pattern="모호 문장",
        raw_text=_VAGUE_RAW,
        exceptions_text="그 밖에 사업 목적에 부합하지 않는다고 심의위원회가 판단하는 부적격자",
        profile=profile(),
        expected_result=UNKNOWN,
        expected_needed_field=ASK_NOTICE,
        note="어떤 프로필이어도 판정할 수 없다. 심의 재량 문장을 미충족으로 만들면 안 된다.",
    ),
    ExceptionCase(
        id="J8",
        pattern="서울 소재 대학 재학생",
        raw_text=_CAMPUS_RAW,
        exceptions_text="서울특별시에 소재한 대학에 재학 중인 자",
        profile=profile(거주지="서울"),
        expected_result=UNKNOWN,
        expected_needed_field=ASK_NOTICE,
        note="거주지와 학교 소재지는 다른 정보다. 서울 거주를 서울 소재 대학으로 넘겨짚으면 안 된다.",
    ),
]


def placeholder_cases() -> List[ExceptionCase]:
    """아직 실제 공고 문장으로 바뀌지 않은 케이스."""
    return [case for case in CASES if case.is_placeholder]
