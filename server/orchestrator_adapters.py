"""Adapters between strict server DTOs and other owners' public modules."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal, TypeVar

from pydantic import BaseModel, Field, JsonValue, ValidationError

from ai.judgment.citation import verify_conditions
from ai.conversation import answer as conversation_answer
from ai.conversation import interpret as conversation_interpret
from ai.conversation import pipeline as conversation_pipeline
from ai.conversation import questions as conversation_questions
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
    ProfileChange,
    ProfileUpdateEventData,
    RelatedChip,
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
    name: str = Field(min_length=1, max_length=20)
    result: ConditionResult
    excerpt: str | None = Field(default=None, min_length=10, max_length=150)
    needed_field: str | None = Field(default=None, max_length=64)


class ExceptionJudgmentOutput(ContractModel):
    """AI B output; dates, URLs, policy status, and unknown keys are rejected."""

    conditions: list[AIExceptionCondition] = Field(default_factory=list, max_length=20)


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
    planned_changes: dict[ProfileField, JsonValue] = Field(default_factory=dict)


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

    changes: list[ProfileChange] = []
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
            changes.append(
                ProfileChange(
                    field=field,
                    before=change.get("before"),
                    after=updated_values[field.value],
                    label=change.get("label"),
                    notice=change.get("notice"),
                )
            )

    planned_changes: dict[ProfileField, JsonValue] = {}
    for change in getattr(interpreted.merged, "held", ()):
        try:
            field = ProfileField(str(change.field))
        except (AttributeError, ValueError):
            continue
        planned_changes[field] = change.value

    profile_update = None
    if changes:
        notice = str(raw_update.get("notice") or "").strip()
        profile_update = ProfileUpdateEventData(
            changes=changes,
            notice=notice or "프로필을 갱신했어요.",
            profile=updated_profile,
        )

    return ConversationTurn(
        profile=updated_profile,
        profile_update=profile_update,
        intent=Intent(interpreted.intent),
        fixed_reply=interpreted.fixed_reply,
        planned_changes=planned_changes,
    )


def planned_followup(turn: ConversationTurn) -> FollowupQuestion | None:
    """Build one planned-basis question before ordinary unknown-field questions."""

    if not turn.planned_changes:
        return None
    field, value = next(iter(turn.planned_changes.items()))
    payload = conversation_questions.build_planned_question(
        field.value,
        conversation_interpret.label_of(field.value, value),
    )
    try:
        return FollowupQuestion.model_validate(payload)
    except ValidationError:
        return None


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
    return ExceptionJudgmentOutput(
        conditions=[
            AIExceptionCondition(
                name=_ASK_NOTICE,
                result=ConditionResult.UNKNOWN,
                excerpt=None,
                needed_field=_ASK_NOTICE,
            )
        ]
    )


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
        name = str(item.get("name") or _ASK_NOTICE).strip()[:20] or _ASK_NOTICE
        if name in seen_names:
            continue
        seen_names.add(name)
        try:
            result = ConditionResult(str(item.get("result")))
        except ValueError:
            result = ConditionResult.UNKNOWN
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
    chips = [
        RelatedChip(
            id=str(chip.get("id") or "").strip(),
            text=str(chip.get("text") or "").strip(),
        )
        for chip in raw_chips
        if isinstance(chip, dict)
        and str(chip.get("id") or "").strip()
        and str(chip.get("text") or "").strip()
    ][:3]
    if chips:
        related = RelatedEventData(chips=chips)

    return FinalizedAnswer(
        answer_deltas=deltas,
        followup=followup,
        related=related,
        answer_failed=False,
    )
