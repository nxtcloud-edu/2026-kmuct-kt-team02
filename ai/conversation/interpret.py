"""메시지 해석 결과 검증과 반영 (ai/conversation/README.md 3장).

모델이 문장에서 뽑아낸 결과(dict)를 받아 **로직으로만** 검증하고 프로필에 반영한다.
이 모듈은 LLM 을 호출하지 않는다. 호출하는 쪽이 모델 출력을 그대로 넘기고,
여기서는 "믿을 수 있는 것만 남기는" 일을 한다. 그래서 모델 없이 테스트할 수 있다.

경계가 이렇게 그어진 이유는 ``ai/conversation/README.md`` 2장 그대로다.
문장 이해는 모델이 하고, 허용 값 검증·프로필 반영·화면 문구는 코드가 한다.

담는 것
-------
1. 허용 값 표 (``docs/01-glossary-profile.md`` 2~3장)
2. 해석 결과 구조 (``docs/05-interfaces.md`` 3장)
3. 해석 기준 (README 3장: planned 보류, 대화 값 우선, 숫자 소득 거부)
4. ``merge`` — 프로필 반영과 ``profile_update`` 이벤트 본문 (``docs/03-api-contract.md`` 5장)
5. 의도별 동작 표 (README 3장)

중복에 대하여
------------
아래 허용 값 표는 규칙 엔진과 서버도 아는 값이다. 같은 규칙이 두 곳에 있는 상태다.
지금은 공용 상수 모듈이 없어 여기 둔다. **12:00 통합 때 공용 모듈로 합칠 후보다.**
합칠 때 기준 문서는 항상 ``docs/01-glossary-profile.md`` 2~3장이다.

실패 방침
--------
모델 출력은 신뢰하지 않는다. 모르는 키, 잘못된 타입, 허용 값 밖, ``None``, 빈 문자열,
리스트 대신 문자열이 오는 경우를 모두 견딘다. **예외를 던져 전체 응답을 실패시키지 않는다.**
이상한 항목은 버리고 무엇을 왜 버렸는지 ``dropped`` 에 남긴다. 이 기록은 지표용이며
사용자 화면에 쓰지 않는다 (``ai/citation/verify.py`` 의 제거 기록과 같은 취급).
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union

from . import fields

# ---------------------------------------------------------------------------
# 1. 허용 값 표 (docs/01-glossary-profile.md 2~3장)
#    12:00 통합 때 공용 모듈로 합칠 후보.
# ---------------------------------------------------------------------------

# 나이는 값 목록이 아니라 범위로 검증한다 (2장: 만 나이 정수 15~39).
AGE_MIN = 15
AGE_MAX = 39

REGION_VALUES = frozenset({"seoul", "outside_seoul"})

STATUS_VALUES = frozenset(
    {"enrolled", "on_leave", "final_semester", "job_seeking", "employed"}
)

CATEGORY_ALL = "all"
CATEGORY_ORDER = ("scholarship", "living", "job", "culture", "housing")
CATEGORY_VALUES = frozenset(CATEGORY_ORDER) | {CATEGORY_ALL}

INCOME_BRACKET_VALUES = frozenset(
    {"under_50", "50_100", "100_150", "over_150", "unknown"}
)

# 서울 25개 자치구 (docs/01-glossary-profile.md 2장)
DISTRICTS = frozenset(
    {
        "강남구", "강동구", "강북구", "강서구", "관악구", "광진구", "구로구", "금천구",
        "노원구", "도봉구", "동대문구", "동작구", "마포구", "서대문구", "서초구", "성동구",
        "성북구", "송파구", "양천구", "영등포구", "용산구", "은평구", "종로구", "중구", "중랑구",
    }
)

# 추가 항목 8종 (docs/01-glossary-profile.md 3장)
EXTRA_FIELD_VALUES: Dict[str, frozenset] = {
    fields.HOUSING_TYPE: frozenset(
        {"parents", "monthly_rent", "jeonse", "dormitory", "other"}
    ),
    fields.RESIDENCE_PERIOD: frozenset({"under_6m", "6m_1y", "over_1y"}),
    fields.REMAINING_SEMESTERS: frozenset({"one", "two_plus"}),
    fields.JOB_SEEKING_PERIOD: frozenset({"under_6m", "over_6m"}),
    fields.EMPLOYMENT_INSURANCE: frozenset({"yes", "no", "unknown"}),
    fields.OTHER_BENEFIT: frozenset({"yes", "no"}),
    fields.HOUSEHOLD_SIZE: frozenset({"1", "2", "3", "4_plus"}),
    fields.LAST_GPA: frozenset({"above", "below", "unknown"}),
}

# 대화로 바꿀 수 있는 기본 항목.
# ``fields.is_askable`` 은 "물을 수 있는 항목"만 참이라 기본 항목을 걸러내지 못한다
# (나이·거주지·상태는 폼에서 받았으므로 묻지 않지만, 대화로 정정할 수는 있다).
# 그래서 기본 항목은 여기 별도 허용 목록을 둔다 (README 3장 "표에 없는 항목은 만들지 않는다").
BASE_CHANGE_FIELDS = frozenset(
    {fields.AGE, fields.REGION, fields.DISTRICT, fields.STATUS, fields.INCOME_BRACKET}
)

# 값 목록으로 검증하는 항목만 모은 표. 나이(범위)와 자치구(25개 목록)는 따로 다룬다.
ALLOWED_VALUES: Dict[str, frozenset] = {
    fields.REGION: REGION_VALUES,
    fields.STATUS: STATUS_VALUES,
    fields.INCOME_BRACKET: INCOME_BRACKET_VALUES,
    **EXTRA_FIELD_VALUES,
}

# 화면 표시 순서. applied 목록 정렬에 쓴다.
# 모델이 준 순서를 그대로 쓰면 같은 입력에 다른 순서가 나와 화면이 흔들린다.
PROFILE_FIELD_ORDER = (
    fields.AGE,
    fields.REGION,
    fields.DISTRICT,
    fields.STATUS,
    fields.CATEGORIES,
    fields.INCOME_BRACKET,
) + fields.FIELD_ORDER[2:]  # income_bracket, district 는 위에서 이미 셈

_FIELD_POSITION = {name: index for index, name in enumerate(PROFILE_FIELD_ORDER)}


# ---------------------------------------------------------------------------
# 2. 고정 문구 (README 3장). 모델이 만들지 않는다.
# ---------------------------------------------------------------------------

# 숫자 소득을 말했을 때 붙이는 안내 (README 3장 해석 기준).
INCOME_BRACKET_NOTICE = "가구 소득 구간을 알려주시면 더 정확해요"

# 범위 밖 (README 3장 의도별 동작 표 문구 그대로 + 할 수 있는 것 안내)
OUT_OF_SCOPE_LINE = "행정 절차나 법률 상담은 아직 도와드리기 어려워요"
OUT_OF_SCOPE_HELP = "청년 지원 제도를 찾고 신청 조건을 확인하는 건 도와드릴 수 있어요"
OUT_OF_SCOPE_REPLY = f"{OUT_OF_SCOPE_LINE}. {OUT_OF_SCOPE_HELP}."

# 잡담 (한 줄 응대 후 정책 찾기 유도)
SMALLTALK_LINE = "네, 도움이 됐다면 다행이에요"
SMALLTALK_HELP = "필요한 지원 분야를 말씀해 주시면 맞는 제도를 찾아드릴게요"
SMALLTALK_REPLY = f"{SMALLTALK_LINE}. {SMALLTALK_HELP}."

# 값 → 화면 문구 (docs/01-glossary-profile.md 2~3장의 괄호 표기 그대로)
VALUE_LABELS: Dict[str, Dict[str, str]] = {
    fields.REGION: {"seoul": "서울", "outside_seoul": "서울 밖"},
    fields.STATUS: {
        "enrolled": "재학",
        "on_leave": "휴학",
        "final_semester": "졸업예정",
        "job_seeking": "졸업 후 구직",
        "employed": "재직",
    },
    fields.INCOME_BRACKET: {
        "under_50": "기준 중위소득 50% 이하",
        "50_100": "50~100%",
        "100_150": "100~150%",
        "over_150": "150% 초과",
        "unknown": "잘 모르겠어요",
    },
    fields.CATEGORIES: {
        "scholarship": "장학·교육",
        "living": "생활비",
        "job": "취업·훈련",
        "culture": "교통·통신·문화",
        "housing": "주거",
        "all": "전체",
    },
    fields.HOUSING_TYPE: {
        "parents": "부모님 집",
        "monthly_rent": "월세",
        "jeonse": "전세",
        "dormitory": "기숙사",
        "other": "기타",
    },
    fields.RESIDENCE_PERIOD: {
        "under_6m": "6개월 미만",
        "6m_1y": "6개월~1년",
        "over_1y": "1년 이상",
    },
    fields.REMAINING_SEMESTERS: {"one": "1학기", "two_plus": "2학기 이상"},
    fields.JOB_SEEKING_PERIOD: {"under_6m": "6개월 미만", "over_6m": "6개월 이상"},
    fields.EMPLOYMENT_INSURANCE: {"yes": "있음", "no": "없음", "unknown": "모르겠어요"},
    fields.OTHER_BENEFIT: {"yes": "있음", "no": "없음"},
    fields.HOUSEHOLD_SIZE: {"1": "1인", "2": "2인", "3": "3인", "4_plus": "4인 이상"},
    # "모르겠어요"는 questions.TEMPLATES 의 버튼 문구와 같아야 한다. 사용자가 누른
    # 버튼 문구와 변경 안내 문구가 다르면 같은 값이 화면에서 두 이름으로 보인다.
    fields.LAST_GPA: {"above": "기준 이상", "below": "기준 미만", "unknown": "모르겠어요"},
}

# 변경 안내 문구 틀. ``{label}`` 은 값의 화면 문구, ``{ro}`` 는 으로/로, ``{eul}`` 은 을/를.
# 조사를 코드가 고르는 이유는 "휴학으로"와 "재직으로"처럼 값마다 달라서다.
# "(으)로" 같은 표기는 화면 문구로 쓰기에 거칠다.
NOTICE_TEMPLATES: Dict[str, str] = {
    fields.AGE: "나이를 {label}{ro} 바꿨어요",
    fields.REGION: "거주지를 {label}{ro} 바꿨어요",
    fields.DISTRICT: "사는 곳을 {label}{ro} 바꿨어요",
    fields.STATUS: "{label}{ro} 바꿨어요",
    fields.INCOME_BRACKET: "가구 소득을 {label}{ro} 바꿨어요",
    fields.HOUSING_TYPE: "주거 형태를 {label}{ro} 바꿨어요",
    fields.RESIDENCE_PERIOD: "서울 거주 기간을 {label}{ro} 바꿨어요",
    fields.REMAINING_SEMESTERS: "남은 학기를 {label}{ro} 바꿨어요",
    fields.JOB_SEEKING_PERIOD: "구직 기간을 {label}{ro} 바꿨어요",
    fields.EMPLOYMENT_INSURANCE: "고용보험 가입 이력을 {label}{ro} 바꿨어요",
    fields.OTHER_BENEFIT: "다른 지원 수혜 여부를 {label}{ro} 바꿨어요",
    fields.HOUSEHOLD_SIZE: "가구원 수를 {label}{ro} 바꿨어요",
    fields.LAST_GPA: "직전 학기 성적을 {label}{ro} 바꿨어요",
}

CATEGORY_ADDED_NOTICE = "관심 분야에 {label}{eul} 추가했어요"
CATEGORY_REMOVED_NOTICE = "관심 분야에서 {label}{eul} 뺐어요"


# ---------------------------------------------------------------------------
# 3. 의도별 동작 표 (README 3장)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IntentBehavior:
    """의도 하나의 동작.

    full_flow   전체 흐름(후보 선정 → 조건 판정 → 답변)을 도는가
    recalculate 규칙 재계산이 필요한가
    fixed_reply 고정 응답 문구. 있으면 모델을 부르지 않고 이 문장만 내보낸다

    ``ask_followup`` 은 ``fields.NO_FOLLOWUP_INTENTS`` 에서 끌어온다.
    같은 규칙을 두 번 적지 않기 위해서다.
    """

    intent: str
    full_flow: bool
    recalculate: bool
    fixed_reply: Optional[str] = None

    @property
    def ask_followup(self) -> bool:
        """후속 질문을 하는가 (README 4장)."""
        return self.intent not in fields.NO_FOLLOWUP_INTENTS


# 범위 밖과 잡담은 규칙 재계산과 판정을 건너뛰고 바로 종료한다 (README 3장).
BEHAVIORS: Dict[str, IntentBehavior] = {
    fields.FIND_POLICY: IntentBehavior(fields.FIND_POLICY, True, True),
    # 그 정책 판정 위주로 답변하므로 전체 흐름은 돌지 않지만 재계산은 필요하다.
    fields.POLICY_QUESTION: IntentBehavior(fields.POLICY_QUESTION, False, True),
    fields.COMPARE: IntentBehavior(fields.COMPARE, False, True),
    # 결과 정리만. 프로필이 바뀌었을 수 있으므로 재계산은 한다. 질문만 생략한다.
    fields.RESULT_ONLY: IntentBehavior(fields.RESULT_ONLY, True, True),
    fields.OUT_OF_SCOPE: IntentBehavior(
        fields.OUT_OF_SCOPE, False, False, OUT_OF_SCOPE_REPLY
    ),
    fields.SMALLTALK: IntentBehavior(fields.SMALLTALK, False, False, SMALLTALK_REPLY),
}

# 의도가 없거나 알 수 없을 때의 기본값.
# find_policy 로 둔다. out_of_scope 로 두면 보여줄 수 있는 결과를 안 보여주고,
# smalltalk 로 두면 규칙 재계산을 건너뛰어 카드가 낡은 상태로 남는다.
# 전체 흐름은 느릴 뿐 틀리지 않는다. 틀린 안내보다 느린 안내가 낫다.
DEFAULT_INTENT = fields.FIND_POLICY


def behavior_of(intent: Optional[str]) -> IntentBehavior:
    """의도의 동작. 모르는 의도는 기본 의도의 동작을 돌려준다."""
    return BEHAVIORS.get(intent or "", BEHAVIORS[DEFAULT_INTENT])


def fixed_reply_for(intent: Optional[str]) -> Optional[str]:
    """고정 응답 문구. 없으면 None (모델이 답변을 쓴다)."""
    return behavior_of(intent).fixed_reply


# ---------------------------------------------------------------------------
# 조사 처리 (화면 문구용)
# ---------------------------------------------------------------------------

# 숫자로 끝나는 문구는 읽는 소리로 종성을 판단한다. "1인" 처럼 뒤에 글자가 붙으면
# 그 글자로 판단하므로 실제로 쓰이는 경우는 드물지만, 표에 없는 값이 라벨로 쓰일 때를 위한 대비다.
_DIGIT_HAS_JONGSEONG = {
    "0": True, "1": True, "3": True, "6": True, "7": True, "8": True,
    "2": False, "4": False, "5": False, "9": False,
}
_DIGIT_IS_RIEUL = {"1": True, "7": True, "8": True}


def _tail(text: str) -> str:
    """조사 판단에 쓸 마지막 글자. 괄호·기호는 건너뛴다."""
    for char in reversed(text.strip()):
        if char.isalnum():
            return char
    return ""


def _jongseong(text: str) -> Tuple[bool, bool]:
    """(종성이 있는가, 그 종성이 ㄹ인가)."""
    char = _tail(text)
    if not char:
        return False, False
    if "가" <= char <= "힣":
        code = (ord(char) - 0xAC00) % 28
        return code != 0, code == 8
    if char in _DIGIT_HAS_JONGSEONG:
        return _DIGIT_HAS_JONGSEONG[char], _DIGIT_IS_RIEUL.get(char, False)
    return False, False  # 영문·기호는 종성 없음으로 본다


def _ro(text: str) -> str:
    """으로 / 로."""
    has_jong, is_rieul = _jongseong(text)
    return "로" if (not has_jong or is_rieul) else "으로"


def _eul(text: str) -> str:
    """을 / 를."""
    has_jong, _ = _jongseong(text)
    return "을" if has_jong else "를"


def label_of(field_name: str, value: Union[str, int, None]) -> str:
    """값의 화면 문구. 표에 없으면 값 자체를 쓴다 (자치구처럼 값이 곧 문구인 항목)."""
    if value is None:
        return ""
    if field_name == fields.AGE:
        return f"만 {value}세"
    return VALUE_LABELS.get(field_name, {}).get(str(value), str(value))


def change_notice(field_name: str, value: Union[str, int, None]) -> str:
    """변경 안내 문구. 표에 틀이 없으면 빈 문자열.

    빈 문자열이면 화면에 안내 줄을 붙이지 않는다. 즉석에서 문장을 만들지 않는다
    (CONTRIBUTING.md 8장: 화면 문구는 표에서 관리).
    """
    template = NOTICE_TEMPLATES.get(field_name)
    if template is None:
        return ""
    label = label_of(field_name, value)
    if not label:
        return ""
    return template.format(label=label, ro=_ro(label), eul=_eul(label))


def _category_notice(added: Sequence[str], removed: Sequence[str]) -> str:
    """관심 분야 변경 안내. 추가와 제외를 한 줄로 묶는다."""
    parts: List[str] = []
    if added:
        label = ", ".join(label_of(fields.CATEGORIES, value) for value in added)
        parts.append(CATEGORY_ADDED_NOTICE.format(label=label, eul=_eul(label)))
    if removed:
        label = ", ".join(label_of(fields.CATEGORIES, value) for value in removed)
        parts.append(CATEGORY_REMOVED_NOTICE.format(label=label, eul=_eul(label)))
    return " ".join(parts)


# ---------------------------------------------------------------------------
# 4. 해석 결과 구조 (docs/05-interfaces.md 3장)
# ---------------------------------------------------------------------------

# 버린 이유 코드. 지표 집계에 쓰므로 문자열을 바꾸지 않는다.
DROP_NOT_A_MAPPING = "not_a_mapping"
DROP_UNKNOWN_FIELD = "unknown_field"
DROP_EMPTY_VALUE = "empty_value"
DROP_NOT_SCALAR = "not_scalar"
DROP_NOT_ALLOWED = "not_allowed_value"
DROP_AGE_RANGE = "age_out_of_range"
DROP_AGE_TYPE = "age_not_int"
DROP_NUMERIC_INCOME = "numeric_income"
DROP_CATEGORY_FIELD = "category_field_not_allowed"
DROP_SUPERSEDED = "superseded"
DROP_SAME_AS_CURRENT = "same_as_current"
DROP_ALREADY_ALL = "already_covered_by_all"
DROP_WOULD_EMPTY = "would_empty_categories"
DROP_EMPTY_POLICY_ID = "empty_policy_id"

# 숫자 소득으로 볼 항목 이름 조각. 모델이 표에 없는 이름으로 소득을 보낼 때를 잡는다.
_INCOME_HINTS = ("income", "salary", "wage", "earn", "소득", "월급", "수입", "연봉")


@dataclass(frozen=True)
class ProfileChange:
    """프로필 변경 하나 (docs/05-interfaces.md 3장).

    field  프로필 항목 이름
    value  검증을 통과한 값. 나이만 int, 나머지는 str
    timing ``fields.CURRENT`` 또는 ``fields.PLANNED``
    """

    field: str
    value: Union[str, int]
    timing: str = fields.CURRENT

    @property
    def is_planned(self) -> bool:
        return self.timing == fields.PLANNED


@dataclass(frozen=True)
class CategoryChanges:
    """관심 분야 변경. 추가와 제외를 함께 담는다."""

    add: Tuple[str, ...] = ()
    remove: Tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return bool(self.add or self.remove)


@dataclass(frozen=True)
class DroppedItem:
    """버린 항목 기록. 지표용이며 화면에 쓰지 않는다.

    where  어디서 왔는가 (모델 출력의 키 또는 ``merge``)
    field  항목 이름. 모르면 빈 문자열
    value  원래 값을 문자열로
    reason 이유 코드
    """

    where: str
    field: str
    value: str
    reason: str


@dataclass(frozen=True)
class Interpretation:
    """메시지 해석 결과 (docs/05-interfaces.md 3장).

    profile_changes 는 기본 항목, extra_answers 는 추가 항목 답변이다.
    모델이 둘을 섞어 보내도 **항목 이름으로 다시 나눈다.** 모델이 어느 키에 넣었는지보다
    항목 이름이 기준이기 때문이다.

    notes 는 버리지 않고 관용 처리한 기록이다 (예: 의도를 기본값으로 채움).
    dropped 와 마찬가지로 지표용이다.
    """

    intent: str = DEFAULT_INTENT
    profile_changes: Tuple[ProfileChange, ...] = ()
    extra_answers: Tuple[ProfileChange, ...] = ()
    category_changes: CategoryChanges = CategoryChanges()
    mentioned_policies: Tuple[str, ...] = ()
    needs_income_bracket_notice: bool = False
    dropped: Tuple[DroppedItem, ...] = ()
    notes: Tuple[str, ...] = ()

    @property
    def behavior(self) -> IntentBehavior:
        return behavior_of(self.intent)

    def all_changes(self) -> Tuple[ProfileChange, ...]:
        """기본 항목과 추가 항목 변경을 합친 것."""
        return self.profile_changes + self.extra_answers

    @property
    def current_changes(self) -> Tuple[ProfileChange, ...]:
        """지금 반영할 변경."""
        return tuple(c for c in self.all_changes() if not c.is_planned)

    @property
    def planned_changes(self) -> Tuple[ProfileChange, ...]:
        """미래 계획. 프로필을 바꾸지 않고 확인 질문 대상이 된다 (README 3장)."""
        return tuple(c for c in self.all_changes() if c.is_planned)

    @property
    def needs_planned_confirmation(self) -> bool:
        """확인 질문("바뀐 뒤 기준으로 볼까요?")이 필요한가."""
        return bool(self.planned_changes)

    def notices(self) -> Tuple[str, ...]:
        """해석 단계에서 붙일 고정 안내 문구."""
        return (INCOME_BRACKET_NOTICE,) if self.needs_income_bracket_notice else ()


# ---------------------------------------------------------------------------
# 모델 출력 읽기 도우미. 여기서부터는 전부 "틀린 입력을 견디는" 코드다.
# ---------------------------------------------------------------------------

_TOP_KEYS = {
    "intent": ("intent",),
    "profile_changes": ("profile_changes", "profile_change", "profileChanges"),
    "extra_answers": ("extra_answers", "extra_answer", "extraAnswers"),
    "category_changes": ("category_changes", "categories", "categoryChanges"),
    "mentioned_policies": (
        "mentioned_policies",
        "policy_ids",
        "policies",
        "mentionedPolicies",
    ),
}
_KNOWN_TOP_KEYS = {key for aliases in _TOP_KEYS.values() for key in aliases}

_TIMING_KEYS = ("timing", "when", "tense", "time", "시점")
_FIELD_KEYS = ("field", "name", "key", "항목")
_VALUE_KEYS = ("value", "val", "값")
_ADD_KEYS = ("add", "added", "include", "추가")
_REMOVE_KEYS = ("remove", "removed", "exclude", "제외")


def _pick(data: Mapping[str, Any], keys: Sequence[str]) -> Any:
    """여러 이름 중 먼저 있는 값. 없으면 None."""
    for key in keys:
        if key in data:
            return data[key]
    return None


def _as_items(raw: Any) -> List[Any]:
    """리스트로 만든다.

    모델은 리스트 자리에 문자열 하나를 보내기도 하고 None 을 보내기도 한다.
    문자열 하나는 원소 하나로 본다. 문자를 하나씩 쪼개면 안 된다.
    """
    if raw is None:
        return []
    if isinstance(raw, (str, int, float)):
        return [raw]
    if isinstance(raw, Mapping):
        return [raw]
    if isinstance(raw, Iterable):
        return list(raw)
    return []


def _as_text(raw: Any) -> Optional[str]:
    """스칼라만 문자열로. 리스트·dict 는 None (값 자리에 올 수 없는 타입)."""
    if raw is None or isinstance(raw, (Mapping, list, tuple, set)):
        return None
    if isinstance(raw, bool):
        return "yes" if raw else "no"  # 고용보험·다른 지원은 yes/no 라 참/거짓이 오기도 한다
    if isinstance(raw, float) and raw.is_integer():
        return str(int(raw))
    return str(raw).strip()


def _normalize_field_name(raw: Any) -> str:
    """항목 이름 정리. 공백과 대소문자만 맞춘다. 이름을 바꿔주지는 않는다."""
    text = _as_text(raw) or ""
    return text.strip().lower().replace(" ", "_").replace("-", "_")


def _normalize_timing(raw: Any) -> str:
    """시점 정리.

    모르는 값은 ``current`` 로 본다. 사용자가 지금 말한 사실을 계획으로 오해하면
    프로필이 갱신되지 않아 "바꿨는데 안 바뀐다"가 된다. 반대 방향 실수가 더 안전하다.
    """
    text = (_as_text(raw) or "").strip().lower()
    if text in {fields.PLANNED, "future", "plan", "예정", "계획"}:
        return fields.PLANNED
    return fields.CURRENT


def _looks_like_income_number(text: str) -> bool:
    """숫자 소득으로 보이는가.

    허용 값("50_100")도 숫자를 담고 있으므로 **허용 값 확인을 먼저 통과한 뒤에만** 부른다.
    """
    return any(char.isdigit() for char in text)


def _is_income_field(field_name: str) -> bool:
    return any(hint in field_name for hint in _INCOME_HINTS)


def _clean_change(
    field_name: str, raw_value: Any
) -> Tuple[Optional[Union[str, int]], Optional[str]]:
    """항목 하나를 검증한다. 반환은 (값, 버린 이유). 값이 None 이면 버린다.

    README 3장 "확실히 말한 것만": 허용 값에 없거나 빈 값이면 버린다.
    """
    if field_name == fields.CATEGORIES:
        # 관심 분야는 추가·제외 구분이 필요하므로 category_changes 로만 받는다.
        return None, DROP_CATEGORY_FIELD

    text = _as_text(raw_value)
    if text is None:
        return None, DROP_NOT_SCALAR
    if not text:
        return None, DROP_EMPTY_VALUE

    if field_name == fields.AGE:
        try:
            age = int(float(text))
        except (TypeError, ValueError):
            return None, DROP_AGE_TYPE
        if not (AGE_MIN <= age <= AGE_MAX):
            return None, DROP_AGE_RANGE
        return age, None

    if field_name == fields.DISTRICT:
        if text in DISTRICTS:
            return text, None
        if f"{text}구" in DISTRICTS:  # "관악" 처럼 구가 빠진 경우만 보정한다
            return f"{text}구", None
        return None, DROP_NOT_ALLOWED

    allowed = ALLOWED_VALUES.get(field_name)
    if allowed is None:
        return None, DROP_UNKNOWN_FIELD
    if text in allowed:
        return text, None

    # 숫자 소득을 구간으로 바꾸지 않는다 (README 3장).
    # 개인 소득과 가구 소득 기준이 다르므로 "월 150" 을 "100_150" 으로 옮길 수 없다.
    if field_name == fields.INCOME_BRACKET and _looks_like_income_number(text):
        return None, DROP_NUMERIC_INCOME

    return None, DROP_NOT_ALLOWED


def _read_change_entries(raw: Any) -> List[Tuple[str, Any, str]]:
    """변경 목록을 (항목 이름, 값, 시점) 목록으로 펼친다.

    견디는 형태
        [{"field": "status", "value": "on_leave", "timing": "planned"}, ...]
        {"status": "on_leave", "housing_type": "monthly_rent"}
        {"status": {"value": "on_leave", "timing": "planned"}}
        "status"  (값이 없어 결국 버려진다)
    """
    entries: List[Tuple[str, Any, str]] = []

    if isinstance(raw, Mapping):
        # {"field": ..., "value": ...} 하나만 온 경우와 {항목: 값} 묶음을 구분한다.
        if _pick(raw, _FIELD_KEYS) is not None:
            candidates: List[Any] = [raw]
        else:
            for key, value in raw.items():
                name = _normalize_field_name(key)
                if isinstance(value, Mapping):
                    entries.append(
                        (
                            name,
                            _pick(value, _VALUE_KEYS),
                            _normalize_timing(_pick(value, _TIMING_KEYS)),
                        )
                    )
                else:
                    entries.append((name, value, fields.CURRENT))
            return entries
    else:
        candidates = _as_items(raw)

    for item in candidates:
        if isinstance(item, Mapping):
            name = _normalize_field_name(_pick(item, _FIELD_KEYS))
            value = _pick(item, _VALUE_KEYS)
            timing = _normalize_timing(_pick(item, _TIMING_KEYS))
            entries.append((name, value, timing))
        else:
            # 값 없이 항목 이름만 온 경우. 값이 없으므로 버려진다.
            entries.append((_normalize_field_name(item), None, fields.CURRENT))
    return entries


def _read_categories(raw: Any) -> Tuple[List[str], List[str], List[DroppedItem]]:
    """관심 분야 변경을 읽는다. 반환은 (추가, 제외, 버린 기록)."""
    added: List[str] = []
    removed: List[str] = []
    dropped: List[DroppedItem] = []

    if isinstance(raw, Mapping):
        add_raw = _pick(raw, _ADD_KEYS)
        remove_raw = _pick(raw, _REMOVE_KEYS)
        if add_raw is None and remove_raw is None:
            # {"housing": "add"} 같은 형태도 있다.
            for key, value in raw.items():
                name = _normalize_field_name(key)
                action = (_as_text(value) or "").lower()
                target = removed if action in {"remove", "exclude", "제외"} else added
                if name in CATEGORY_VALUES:
                    target.append(name)
                else:
                    dropped.append(
                        DroppedItem("category_changes", name, action, DROP_NOT_ALLOWED)
                    )
            return added, removed, dropped
    else:
        add_raw, remove_raw = raw, None  # 리스트만 오면 추가로 본다

    for bucket, items in (("add", _as_items(add_raw)), ("remove", _as_items(remove_raw))):
        for item in items:
            text = (_as_text(item) or "").strip().lower()
            if not text:
                dropped.append(
                    DroppedItem("category_changes", bucket, "", DROP_EMPTY_VALUE)
                )
                continue
            if text not in CATEGORY_VALUES:
                dropped.append(
                    DroppedItem("category_changes", bucket, text, DROP_NOT_ALLOWED)
                )
                continue
            (added if bucket == "add" else removed).append(text)

    return added, removed, dropped


def _dedupe(
    changes: Sequence[ProfileChange], dropped: List[DroppedItem]
) -> Tuple[ProfileChange, ...]:
    """같은 항목·같은 시점에 변경이 여러 개면 **마지막 것**을 쓴다.

    모델은 문장에 나온 순서대로 결과를 내놓는다. 한 문장에서 같은 항목을 두 번 말하면
    (예: "휴학이요, 아니 재학이요") 뒤에 말한 것이 사용자의 최종 의사다.
    앞의 것은 ``superseded`` 로 기록한다.
    """
    kept: Dict[Tuple[str, str], ProfileChange] = {}
    for change in changes:
        key = (change.field, change.timing)
        previous = kept.get(key)
        if previous is not None:
            dropped.append(
                DroppedItem(
                    "profile_changes",
                    previous.field,
                    str(previous.value),
                    DROP_SUPERSEDED,
                )
            )
        kept[key] = change
    # 화면 순서를 고정한다.
    return tuple(
        sorted(
            kept.values(),
            key=lambda c: (_FIELD_POSITION.get(c.field, len(PROFILE_FIELD_ORDER)), c.timing),
        )
    )


def from_model_output(data: Any) -> Interpretation:
    """모델 출력(dict)을 해석 결과로 바꾼다.

    **예외를 던지지 않는다.** dict 가 아니어도, 키가 이상해도, 값 타입이 틀려도
    통과한 것만 담은 결과를 돌려준다. 해석 하나 때문에 전체 응답을 실패시키면
    규칙 기반 카드까지 사라진다 (docs/03-api-contract.md 9장: AI 가 실패해도 카드는 남는다).
    """
    dropped: List[DroppedItem] = []
    notes: List[str] = []

    if not isinstance(data, Mapping):
        return Interpretation(
            intent=DEFAULT_INTENT,
            dropped=(DroppedItem("root", "", type(data).__name__, DROP_NOT_A_MAPPING),),
            notes=("intent_defaulted",),
        )

    # 의도
    raw_intent = (_as_text(_pick(data, _TOP_KEYS["intent"])) or "").strip().lower()
    if raw_intent in fields.INTENTS:
        intent = raw_intent
    else:
        intent = DEFAULT_INTENT
        notes.append("intent_defaulted")
        if raw_intent:
            dropped.append(DroppedItem("intent", "intent", raw_intent, DROP_NOT_ALLOWED))

    # 모르는 최상위 키는 기록만 남긴다. 버려도 나머지 해석에는 영향이 없다.
    for key in data:
        if key not in _KNOWN_TOP_KEYS:
            notes.append(f"unknown_key:{key}")

    # 프로필 변경 + 추가 항목 답변.
    # 모델이 어느 키에 넣었든 항목 이름으로 다시 나눈다.
    entries = _read_change_entries(_pick(data, _TOP_KEYS["profile_changes"]))
    entries += _read_change_entries(_pick(data, _TOP_KEYS["extra_answers"]))

    base: List[ProfileChange] = []
    extra: List[ProfileChange] = []
    needs_income_notice = False

    for name, raw_value, timing in entries:
        if not name:
            dropped.append(
                DroppedItem("profile_changes", "", _as_text(raw_value) or "", DROP_UNKNOWN_FIELD)
            )
            continue

        known = name in BASE_CHANGE_FIELDS or fields.is_askable(name) or name == fields.CATEGORIES
        if not known:
            # 표에 없는 항목은 만들지 않는다 (README 3장).
            # 다만 숫자 소득을 이상한 이름으로 보낸 경우는 안내로 연결한다.
            if _is_income_field(name):
                needs_income_notice = True
                dropped.append(
                    DroppedItem(
                        "profile_changes", name, _as_text(raw_value) or "", DROP_NUMERIC_INCOME
                    )
                )
            else:
                dropped.append(
                    DroppedItem(
                        "profile_changes", name, _as_text(raw_value) or "", DROP_UNKNOWN_FIELD
                    )
                )
            continue

        value, reason = _clean_change(name, raw_value)
        if value is None:
            if reason == DROP_NUMERIC_INCOME:
                needs_income_notice = True
            dropped.append(
                DroppedItem("profile_changes", name, _as_text(raw_value) or "", reason or "")
            )
            continue

        change = ProfileChange(name, value, timing)
        (base if name in BASE_CHANGE_FIELDS else extra).append(change)

    # 관심 분야
    added, removed, category_dropped = _read_categories(
        _pick(data, _TOP_KEYS["category_changes"])
    )
    dropped.extend(category_dropped)

    # 언급된 정책
    policies: List[str] = []
    for item in _as_items(_pick(data, _TOP_KEYS["mentioned_policies"])):
        text = _as_text(item)
        if not text:
            dropped.append(
                DroppedItem("mentioned_policies", "", "", DROP_EMPTY_POLICY_ID)
            )
            continue
        if text not in policies:
            policies.append(text)

    return Interpretation(
        intent=intent,
        profile_changes=_dedupe(base, dropped),
        extra_answers=_dedupe(extra, dropped),
        category_changes=CategoryChanges(
            add=tuple(dict.fromkeys(added)), remove=tuple(dict.fromkeys(removed))
        ),
        mentioned_policies=tuple(policies),
        needs_income_bracket_notice=needs_income_notice,
        dropped=tuple(dropped),
        notes=tuple(notes),
    )


def parse_model_json(text: str) -> Interpretation:
    """모델이 준 JSON 문자열을 해석 결과로. 깨진 JSON 도 예외 없이 처리한다.

    앞뒤에 설명이 붙어 오는 경우가 실제로 있어 첫 ``{`` 부터 마지막 ``}`` 까지만 떼어 본다.
    """
    # 문자열이 아닌 값이 오면 그대로 해석 단계로 넘긴다.
    # 전에는 `not text` 를 통과한 숫자가 `.strip()` 에서 터졌다. 이 파일은 "예외를 던져
    # 전체 응답을 실패시키지 않는다"를 선언한 모듈이라 그 자체가 계약 위반이었다.
    if not isinstance(text, str):
        return from_model_output(text if isinstance(text, Mapping) else None)
    if not text.strip():
        return from_model_output(None)
    try:
        return from_model_output(json.loads(text))
    except (ValueError, TypeError):
        pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return from_model_output(json.loads(text[start : end + 1]))
        except (ValueError, TypeError):
            pass
    return Interpretation(
        dropped=(DroppedItem("root", "", "json_decode_error", DROP_NOT_A_MAPPING),),
        notes=("intent_defaulted",),
    )


# ---------------------------------------------------------------------------
# 5. 프로필 반영
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AppliedChange:
    """실제로 바뀐 항목 하나. ``profile_update`` 이벤트에 그대로 실린다.

    before/after 를 함께 담는 이유는 화면이 "무엇이 무엇으로 바뀌었는지" 보여주기 때문이다.
    notice 는 표에서 온 고정 문구다. 모델이 만들지 않는다.
    """

    field: str
    before: Any
    after: Any
    notice: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "field": self.field,
            "before": self.before,
            "after": self.after,
            "label": label_of(self.field, self.after) if self.field != fields.CATEGORIES else "",
            "notice": self.notice,
        }


@dataclass(frozen=True)
class MergeResult:
    """반영 결과.

    profile  새 프로필 (입력은 그대로 둔다)
    applied  실제로 바뀐 항목
    held     반영하지 않고 보류한 planned 변경. 확인 질문 대상
    dropped  버린 항목 기록 (해석 단계 + 반영 단계)
    notes    관용 처리 기록
    """

    profile: Dict[str, Any]
    applied: Tuple[AppliedChange, ...] = ()
    held: Tuple[ProfileChange, ...] = ()
    dropped: Tuple[DroppedItem, ...] = ()
    needs_income_bracket_notice: bool = False
    notes: Tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return bool(self.applied)

    @property
    def needs_planned_confirmation(self) -> bool:
        return bool(self.held)

    def notices(self) -> Tuple[str, ...]:
        """화면에 붙일 안내 문구. 변경 안내 + 소득 구간 안내."""
        lines = [change.notice for change in self.applied if change.notice]
        if self.needs_income_bracket_notice:
            lines.append(INCOME_BRACKET_NOTICE)
        return tuple(lines)

    def to_profile_update(self) -> Optional[Dict[str, Any]]:
        """``profile_update`` 이벤트 본문 (docs/03-api-contract.md 5장).

        담는 것은 "바뀐 항목과 값, 안내 문구, 갱신된 프로필"이다.
        바뀐 것이 없으면 None. 이벤트는 프로필이 바뀔 때만 보낸다.
        """
        if not self.applied:
            return None
        return {
            "changes": [change.to_dict() for change in self.applied],
            "notice": " ".join(self.notices()),
            "profile": copy.deepcopy(self.profile),
        }


def _merge_categories(
    current: Any, changes: CategoryChanges, dropped: List[DroppedItem]
) -> Tuple[List[str], List[str], List[str]]:
    """관심 분야를 합친다. 반환은 (새 목록, 실제로 추가된 것, 실제로 제외된 것).

    ``all`` 처리는 docs/01-glossary-profile.md 2장을 따른다.
      - ``all`` 을 추가하면 나머지는 해제한다.
      - 이미 ``all`` 인데 특정 분야를 추가하면 아무 일도 없다. 전체가 이미 후보다.
      - 이미 ``all`` 인데 특정 분야를 제외하면 5개로 펼친 뒤 뺀다. 그래야 제외가 뜻을 갖는다.
      - 관심 분야는 필수라 비울 수 없다. 전부 빠지면 원래대로 둔다.

    돌려주는 목록은 항상 표 순서(``CATEGORY_ORDER``)로 정렬한다. 프로필에 들어 있던
    허용 값 밖의 분야는 조용히 걸러진다. 규칙 엔진이 모르는 분야가 남아 있으면
    후보 선정에서 어긋나기 때문이다.
    """
    existing = [
        value
        for value in _as_items(current)
        if isinstance(value, str) and value in CATEGORY_VALUES
    ]
    existing = list(dict.fromkeys(existing))

    if not changes:
        return existing, [], []

    if CATEGORY_ALL in changes.add:
        after = [CATEGORY_ALL]
    else:
        working = list(existing)
        # 뺄 수 있는 요청만 골라낸다. `all` 제거 요청은 관심 분야를 비우라는 뜻이라
        # 받아들이지 않으므로, 그것만 들어온 경우에는 펼치지도 않는다.
        #
        # 전에는 먼저 5개로 펼친 뒤 `all` 제거만 거부해서, 사용자가 "전체 빼 주세요"라고
        # 했는데 "장학·교육, 생활비, 취업·훈련, 교통·통신·문화, 주거를 추가했어요"라는
        # 안내가 나갔다. 요청을 거부하면서 프로필은 바꾸는 상태였다.
        removable = [value for value in changes.remove if value != CATEGORY_ALL]
        if CATEGORY_ALL in working and removable:
            working = list(CATEGORY_ORDER)  # all 을 펼쳐 놓고 뺀다
        for value in changes.add:
            if CATEGORY_ALL in working:
                dropped.append(
                    DroppedItem("merge", fields.CATEGORIES, value, DROP_ALREADY_ALL)
                )
                continue
            if value not in working:
                working.append(value)
        for value in changes.remove:
            if value == CATEGORY_ALL:
                # "전체를 빼 달라"는 말은 관심 분야를 비우라는 뜻이 되어 필수 항목을 깬다.
                dropped.append(
                    DroppedItem("merge", fields.CATEGORIES, value, DROP_WOULD_EMPTY)
                )
                continue
            if value in working:
                working.remove(value)
        after = working

    if not after:
        for value in changes.remove:
            dropped.append(
                DroppedItem("merge", fields.CATEGORIES, value, DROP_WOULD_EMPTY)
            )
        return existing, [], []

    order = {value: index for index, value in enumerate((CATEGORY_ALL,) + CATEGORY_ORDER)}
    after = sorted(dict.fromkeys(after), key=lambda value: order.get(value, len(order)))

    added = [value for value in after if value not in existing]
    removed = [value for value in existing if value not in after]
    if CATEGORY_ALL in after:
        # all 로 합쳐지며 나머지가 사라진 것은 "뺐다"가 아니다.
        # "전체를 추가했어요. 취업·훈련을 뺐어요" 로 나가면 사용자가 잃은 것으로 읽는다.
        removed = []
    return after, added, removed


def merge(profile: Optional[Mapping[str, Any]], interpretation: Interpretation) -> MergeResult:
    """해석 결과를 프로필에 반영한다.

    입력 프로필은 **바꾸지 않는다.** 원본을 고치면 전후 비교가 불가능해져
    "휴학으로 바꿨어요" 같은 안내와 프로필 바 갱신을 만들 수 없다.

    적용 규칙 (README 3장)
      - 시점이 ``planned`` 인 변경은 반영하지 않고 ``held`` 로 넘긴다.
        지금 신청할 수 있는 제도를 찾는 것이 목적이므로, 휴학 예정자에게
        휴학생 전용 제도를 지금 신청 가능한 것처럼 보여주면 틀린 안내가 된다.
      - 시점이 ``current`` 면 폼 값을 덮어쓴다 (docs/05-interfaces.md 6장: 대화 값이 이긴다).
      - 값이 지금과 같으면 바뀐 항목에 넣지 않는다. 안 바뀐 것을 바뀌었다고 하면 안 된다.
    """
    new_profile: Dict[str, Any] = copy.deepcopy(dict(profile or {}))
    dropped: List[DroppedItem] = list(interpretation.dropped)
    applied: List[AppliedChange] = []

    for change in interpretation.current_changes:
        before = new_profile.get(change.field)
        if before == change.value:
            dropped.append(
                DroppedItem("merge", change.field, str(change.value), DROP_SAME_AS_CURRENT)
            )
            continue
        new_profile[change.field] = change.value
        applied.append(
            AppliedChange(
                field=change.field,
                before=before,
                after=change.value,
                notice=change_notice(change.field, change.value),
            )
        )

    after, added, removed = _merge_categories(
        new_profile.get(fields.CATEGORIES), interpretation.category_changes, dropped
    )
    if added or removed:
        before_categories = [
            value
            for value in _as_items(new_profile.get(fields.CATEGORIES))
            if isinstance(value, str)
        ]
        new_profile[fields.CATEGORIES] = after
        applied.append(
            AppliedChange(
                field=fields.CATEGORIES,
                before=before_categories,
                after=list(after),
                notice=_category_notice(added, removed),
            )
        )
    elif fields.CATEGORIES in new_profile:
        new_profile[fields.CATEGORIES] = after

    applied.sort(key=lambda c: _FIELD_POSITION.get(c.field, len(PROFILE_FIELD_ORDER)))

    return MergeResult(
        profile=new_profile,
        applied=tuple(applied),
        held=interpretation.planned_changes,
        dropped=tuple(dropped),
        needs_income_bracket_notice=interpretation.needs_income_bracket_notice,
        notes=interpretation.notes,
    )


def apply_model_output(
    profile: Optional[Mapping[str, Any]], data: Any
) -> Tuple[Interpretation, MergeResult]:
    """모델 출력을 받아 해석과 반영을 한 번에. 호출하는 쪽 편의용."""
    interpretation = from_model_output(data)
    return interpretation, merge(profile, interpretation)
