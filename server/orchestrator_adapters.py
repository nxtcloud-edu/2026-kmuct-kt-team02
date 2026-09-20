"""Adapters between strict server DTOs and other owners' public modules."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, Field, JsonValue, ValidationError

from ai.citation import verify_conditions
from ai.conversation import answer as conversation_answer
from ai.conversation import pipeline as conversation_pipeline
from server.schemas import (
    AskableProfileField,
    Category,
    ConditionEvaluation,
    ConditionResult,
    ContractModel,
    EvaluationStatus,
    FollowupQuestion,
    JudgedBy,
    Policy,
    PolicyEvaluation,
    Profile,
    ProfileField,
)
from server.sse import (
    AnswerDeltaEventData,
    Footnote,
    ProfileUpdateEventData,
    RelatedEventData,
)


class Intent(StrEnum):
    FIND_POLICY = "find_policy"
    POLICY_QUESTION = "policy_question"
    COMPARE = "compare"
    RESULT_ONLY = "result_only"
    OUT_OF_SCOPE = "out_of_scope"
    SMALLTALK = "smalltalk"


class ChangeTiming(StrEnum):
    CURRENT = "current"
    PLANNED = "planned"


class AIProfileChange(ContractModel):
    field: ProfileField
    value: JsonValue
    timing: ChangeTiming = ChangeTiming.CURRENT


class AICategoryChanges(ContractModel):
    add: list[Category] = Field(default_factory=list)
    remove: list[Category] = Field(default_factory=list)


class MessageInterpretationOutput(ContractModel):
    """Only AI A fields that Backend B is allowed to accept."""

    intent: Intent = Intent.FIND_POLICY
    profile_changes: list[AIProfileChange] = Field(default_factory=list)
    extra_answers: list[AIProfileChange] = Field(default_factory=list)
    category_changes: AICategoryChanges = Field(default_factory=AICategoryChanges)
    mentioned_policies: list[str] = Field(default_factory=list)


class AIExceptionCondition(ContractModel):
    summary: str = Field(min_length=1, max_length=20)
    result: Literal["충족", "미충족", "미확인"]
    excerpt: str | None = Field(default=None, min_length=10, max_length=150)
    needed_field: str | None = Field(default=None, max_length=64)


class ExceptionJudgmentOutput(ContractModel):
    """AI B output; dates, URLs, policy status, and unknown keys are rejected."""

    conditions: list[AIExceptionCondition] = Field(default_factory=list, max_length=20)

    #: 예외 조건을 판정하지 못했는가. **AI 가 채우는 값이 아니라 서버가 붙이는 표시다.**
    #:
    #: 빈 조건 목록만으로는 "예외 조건이 없다"와 "판정을 못 했다"를 구분할 수 없다. 앞은
    #: `likely` 로 가도 맞고 뒤는 안 된다. `merge_exception_judgment` 가 이 값을 보고
    #: 상태를 `check` 로 막는다. `validate_ai_json` 이 모델 출력을 검증할 때는 이 키가
    #: 오지 않으므로 기본값 False 가 쓰인다.
    judgment_failed: bool = False


ModelT = TypeVar("ModelT", bound=BaseModel)


def validate_ai_json(model_type: type[ModelT], raw_output: object) -> ModelT:
    """Validate dict output or a JSON string with one strict Pydantic model."""

    if isinstance(raw_output, str):
        return model_type.model_validate_json(raw_output)
    return model_type.model_validate(raw_output)


class ConversationTurn(ContractModel):
    profile: Profile
    profile_update: ProfileUpdateEventData | None = None
    intent: Intent
    fixed_reply: str | None = None


def apply_message_interpretation(
    profile: Profile,
    output: MessageInterpretationOutput,
) -> ConversationTurn:
    """Reuse AI A's merge semantics and translate its event shape."""

    interpreted = conversation_pipeline.interpret_turn(
        profile.model_dump(mode="json"),
        output.model_dump(mode="json", exclude_none=True),
    )
    try:
        updated_profile = Profile.model_validate(interpreted.profile)
    except ValidationError:
        updated_profile = profile.model_copy(deep=True)
        return ConversationTurn(
            profile=updated_profile,
            intent=Intent(interpreted.intent),
            fixed_reply=interpreted.fixed_reply,
        )

    changed_fields: dict[ProfileField, JsonValue] = {}
    raw_update = interpreted.profile_update or {}
    raw_changes = raw_update.get("changes", [])
    updated_values = updated_profile.model_dump(mode="json")
    if isinstance(raw_changes, list):
        for change in raw_changes:
            if not isinstance(change, dict):
                continue
            try:
                field = ProfileField(str(change.get("field") or ""))
            except ValueError:
                continue
            changed_fields[field] = updated_values[field.value]

    profile_update = None
    if changed_fields:
        notice = str(raw_update.get("notice") or "").strip()
        profile_update = ProfileUpdateEventData(
            changed_fields=changed_fields,
            message=notice or "프로필을 갱신했어요.",
            profile=updated_profile,
        )

    return ConversationTurn(
        profile=updated_profile,
        profile_update=profile_update,
        intent=Intent(interpreted.intent),
        fixed_reply=interpreted.fixed_reply,
    )


_RESULT_MAP: dict[str, ConditionResult] = {
    "충족": ConditionResult.MET,
    "미충족": ConditionResult.UNMET,
    "미확인": ConditionResult.UNKNOWN,
}
_STATUS_LABELS: dict[EvaluationStatus, str] = {
    EvaluationStatus.LIKELY: "신청 가능성이 높아요",
    EvaluationStatus.CHECK: "확인이 필요해요",
    EvaluationStatus.UNLIKELY: "어려울 수 있어요",
}
_NEEDED_FIELD_MAP: dict[str, AskableProfileField] = {
    "자치구": AskableProfileField.DISTRICT,
    "가구 소득": AskableProfileField.INCOME_BRACKET,
    "주거 형태": AskableProfileField.HOUSING_TYPE,
    "서울 거주 기간": AskableProfileField.RESIDENCE_PERIOD,
    "남은 학기": AskableProfileField.REMAINING_SEMESTERS,
    "구직 기간": AskableProfileField.JOB_SEEKING_PERIOD,
    "고용보험 가입 이력": AskableProfileField.EMPLOYMENT_INSURANCE,
    "다른 지원 수혜 중": AskableProfileField.OTHER_BENEFIT,
    "가구원 수": AskableProfileField.HOUSEHOLD_SIZE,
    "직전 학기 성적": AskableProfileField.LAST_GPA,
}
_ASK_NOTICE = "공고 확인 필요"


class JudgmentMergeResult(ContractModel):
    policy: PolicyEvaluation
    verified_footnotes: list[Footnote] = Field(default_factory=list)
    citation_failures: int = 0
    changed: bool = False


def unknown_exception_judgment() -> ExceptionJudgmentOutput:
    """예외 조건 판정을 못 했을 때 돌려줄 결과. **조건을 만들지 않는다.**

    이전에는 `공고 확인 필요` 조건 한 건을 만들어 붙였다. 그게 카드 조건 목록에 그대로
    나타나서, 규칙 기반 조건 6건이 제대로 붙은 정책에도 정체 모를 "공고 확인 필요" 줄이
    하나 더 생겼다.

    빈 목록으로 바꾼 근거. **판정을 못 한 것과 "공고를 봐야 한다"는 다르다.** 모델이
    안 불렸을 뿐인데 사용자에게 공고를 확인하라고 말하면 근거 없는 안내다. 게이트웨이가
    분당 요청 수 제한에 걸린 동안에는 모든 정책에 그 줄이 붙어서, 데이터가 부실한 정책과
    구분되지 않았다.

    잃는 것은 "예외 조건을 판정하지 못했다"는 표시다. 그건 사용자에게 알릴 값이 아니라
    지표에 남길 값이고, 호출부가 로그로 남긴다. 화면에는 규칙 기반 조건이 그대로 남고,
    모든 조건이 충족이면 그 판정은 예외 조건을 못 본 상태에서 나온 것이므로 상태 계산이
    낙관적으로 기울 수 있다 — 그 위험은 `data_status` 배지와 고정 문구("최종 신청 전 공식
    공고에서 다시 확인하세요")가 이미 덮는다.
    """
    return ExceptionJudgmentOutput(conditions=[], judgment_failed=True)


def _needed_field(value: object) -> AskableProfileField | Literal["공고 확인 필요"] | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text == _ASK_NOTICE:
        return _ASK_NOTICE
    if text in _NEEDED_FIELD_MAP:
        return _NEEDED_FIELD_MAP[text]
    try:
        return AskableProfileField(text)
    except ValueError:
        return _ASK_NOTICE


def _status_from_conditions(
    conditions: list[ConditionEvaluation],
    fallback: EvaluationStatus,
) -> EvaluationStatus:
    if not conditions:
        return fallback
    if any(item.result == ConditionResult.UNMET for item in conditions):
        return EvaluationStatus.UNLIKELY
    if any(item.result == ConditionResult.UNKNOWN for item in conditions):
        return EvaluationStatus.CHECK
    return EvaluationStatus.LIKELY


def merge_exception_judgment(
    policy: PolicyEvaluation,
    source: Policy,
    output: ExceptionJudgmentOutput,
    *,
    first_footnote_id: int,
) -> JudgmentMergeResult:
    """Verify AI B excerpts, normalize values, and compute status in code."""

    checked, removed = verify_conditions(
        [item.model_dump(mode="json") for item in output.conditions],
        source.raw_text,
    )
    merged_conditions = list(policy.conditions)
    verified_footnotes: list[Footnote] = []
    seen_names = {item.name for item in merged_conditions}
    next_footnote_id = first_footnote_id

    for item in checked:
        verified = bool(item.get("excerpt_verified")) and bool(item.get("excerpt"))
        name = str(item.get("summary") or _ASK_NOTICE).strip()[:20] or _ASK_NOTICE
        if name in seen_names:
            continue
        seen_names.add(name)
        result = _RESULT_MAP.get(str(item.get("result")), ConditionResult.UNKNOWN)
        needed_field = _needed_field(item.get("needed_field"))
        if result == ConditionResult.UNKNOWN and needed_field is None:
            needed_field = _ASK_NOTICE
        excerpt = str(item["excerpt"]) if verified else None

        condition = ConditionEvaluation(
            name=name,
            result=result,
            judged_by=JudgedBy.AI,
            excerpt=excerpt,
            source_url=source.source_url,
            footnote_id=next_footnote_id,
            needed_field=needed_field,
        )
        merged_conditions.append(condition)
        if excerpt is not None:
            verified_footnotes.append(
                Footnote(
                    footnote_id=next_footnote_id,
                    policy_id=policy.policy_id,
                    excerpt=excerpt,
                    agency=policy.agency,
                    checked_at=policy.checked_at,
                    source_url=source.source_url,
                )
            )
        next_footnote_id += 1

    status = _status_from_conditions(merged_conditions, policy.status)
    if output.judgment_failed and status is EvaluationStatus.LIKELY:
        # 예외 조건을 **판정하지 못했는데** 규칙 조건만으로 `likely` 가 되는 경우를 막는다.
        #
        # 이 정책에는 예외 조건 문장이 있고(호출부가 그때만 판정을 부른다) 그 문장을 아직
        # 못 봤다. 그 상태에서 "신청 가능성이 높아요" 라고 말하면 사용자에게 될 것처럼
        # 안내하는 것이고, 예외 조건이 실제로 걸리는 경우 그게 가장 나쁜 오류다
        # (.kiro/steering/03-dev-method.md: 잘못된 단정은 안 된다고 잘못 말하는 것과 같은 급).
        #
        # 조건 목록에는 아무것도 추가하지 않는다. `공고 확인 필요` 줄을 넣었더니 규칙 조건이
        # 제대로 붙은 정책에도 정체 모를 줄이 하나 더 생겼다. 상태만 한 칸 낮춰서
        # "확인이 필요해요" 로 두면, 화면에는 조건 표와 고정 문구가 그대로 남는다.
        status = EvaluationStatus.CHECK
    updated = PolicyEvaluation.model_validate(
        {
            **policy.model_dump(mode="json"),
            "conditions": [item.model_dump(mode="json") for item in merged_conditions],
            "status": status.value,
            "status_label": _STATUS_LABELS[status],
        }
    )
    return JudgmentMergeResult(
        policy=updated,
        verified_footnotes=verified_footnotes,
        citation_failures=len(removed),
        changed=updated != policy,
    )


def next_footnote_id(policies: list[PolicyEvaluation]) -> int:
    ids = [
        condition.footnote_id
        for policy in policies
        for condition in policy.conditions
    ]
    return max(ids, default=0) + 1


def footnotes_from_policies(policies: list[PolicyEvaluation]) -> list[Footnote]:
    """Build footnotes only from conditions that still have verified excerpts."""

    footnotes: list[Footnote] = []
    seen: set[int] = set()
    for policy in policies:
        for condition in policy.conditions:
            if condition.excerpt is None or condition.footnote_id in seen:
                continue
            seen.add(condition.footnote_id)
            footnotes.append(
                Footnote(
                    footnote_id=condition.footnote_id,
                    policy_id=policy.policy_id,
                    excerpt=condition.excerpt,
                    agency=policy.agency,
                    checked_at=policy.checked_at,
                    source_url=condition.source_url,
                )
            )
    return footnotes


def unknown_items_from_policies(policies: list[PolicyEvaluation]) -> list[dict[str, object]]:
    items: list[dict[str, object]] = []
    for rank, policy in enumerate(policies):
        for condition in policy.conditions:
            if condition.result != ConditionResult.UNKNOWN or condition.needed_field is None:
                continue
            needed = (
                condition.needed_field.value
                if isinstance(condition.needed_field, AskableProfileField)
                else condition.needed_field
            )
            items.append(
                {
                    "field": needed,
                    "policy_id": policy.policy_id,
                    "policy_rank": rank,
                    "policy_title": policy.title,
                }
            )
    return items


class FinalizedAnswer(ContractModel):
    answer_deltas: list[AnswerDeltaEventData]
    followup: FollowupQuestion | None = None
    related: RelatedEventData | None = None
    answer_failed: bool = False


def build_answer_prompt(
    profile: Profile,
    policies: list[PolicyEvaluation],
    footnotes: list[Footnote],
):
    policy_dicts = [item.model_dump(mode="json") for item in policies]
    footnote_dicts = [item.model_dump(mode="json") for item in footnotes]
    skeleton = conversation_answer.build_skeleton(policy_dicts)
    source_text = "\n".join(item.excerpt for item in footnotes)
    return conversation_pipeline.build_answer_prompt(
        skeleton,
        profile=profile.model_dump(mode="json"),
        policies=policy_dicts,
        footnotes=footnote_dicts,
        source_text=source_text,
    )


def finalize_answer(
    answer_text: str,
    *,
    policies: list[PolicyEvaluation],
    footnotes: list[Footnote],
    intent: Intent,
) -> FinalizedAnswer:
    policy_dicts = [item.model_dump(mode="json") for item in policies]
    footnote_dicts = [item.model_dump(mode="json") for item in footnotes]
    finished = conversation_pipeline.finish_turn(
        answer_text,
        footnotes=footnote_dicts,
        policies=policy_dicts,
        unknown_items=unknown_items_from_policies(policies),
        intent=intent.value,
    )
    if finished.answer_failed or not finished.answer_text.strip():
        return FinalizedAnswer(answer_deltas=[], answer_failed=True)

    sentences = conversation_answer.split_sentences(finished.answer_text)
    deltas = [
        AnswerDeltaEventData(delta=sentence.strip())
        for sentence in sentences
        if sentence.strip()
    ]

    followup = None
    if finished.followup is not None:
        try:
            followup = FollowupQuestion.model_validate(finished.followup)
        except ValidationError:
            followup = None

    related = None
    raw_chips = finished.related.get("chips", []) if isinstance(finished.related, dict) else []
    questions = [
        str(chip.get("text") or "").strip()
        for chip in raw_chips
        if isinstance(chip, dict) and str(chip.get("text") or "").strip()
    ][:3]
    if questions:
        related = RelatedEventData(questions=questions)

    return FinalizedAnswer(
        answer_deltas=deltas,
        followup=followup,
        related=related,
        answer_failed=False,
    )


def rule_based_fallback(
    *,
    policies: list[PolicyEvaluation],
    footnotes: list[Footnote],
    intent: Intent,
) -> FinalizedAnswer | None:
    """모델 없이 판정 결과만으로 답변을 만든다. 만들 수 없으면 `None`.

    모델 호출이 실패했을 때 쓴다. 게이트웨이가 분당 요청 수 제한(429)에 걸리면 모든
    질문이 고정 사과 문구로 답하게 되는데, 답변에 필요한 재료는 이미 판정 결과에 다
    들어 있다. `ai/conversation/answer.py` 의 `rule_based_answer` 가 그것을 문장으로
    옮긴다.

    조립한 문장도 `finalize_answer` 를 그대로 통과시킨다. 검증을 건너뛰면 이 경로만
    금지 표현·각주 규칙 밖에 놓이고, 그 예외는 나중에 아무도 기억하지 못한다.
    통과하지 못하면 `None` 을 돌려주고 호출부가 고정 문구로 돌아간다.

    `None` 을 돌려주는 경우: 정책이 없거나, 각주를 붙일 수 있는 조건이 없어서 판정
    문장이 전부 지워진 경우다. 그때는 설명할 내용 자체가 없다.
    """
    try:
        text = conversation_answer.rule_based_answer(
            [item.model_dump(mode="json") for item in policies]
        )
    except Exception:  # noqa: BLE001 - 폴백이 턴을 실패시키지 않게
        return None
    if not text.strip():
        return None

    finalized = finalize_answer(
        text, policies=policies, footnotes=footnotes, intent=intent
    )
    if finalized.answer_failed or not finalized.answer_deltas:
        return None
    return finalized
