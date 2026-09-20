"""Shared Pydantic contracts for the API and module boundaries.

This module validates data shapes only. Policy eligibility, sorting, D-day
calculation, and condition-merging logic remain owned by backend A and the
integration layer described in the repository documentation.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    JsonValue,
    field_validator,
    model_validator,
)


class ContractModel(BaseModel):
    """Strict base for all public contracts."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Region(StrEnum):
    SEOUL = "seoul"


class District(StrEnum):
    GANGNAM = "강남구"
    GANGDONG = "강동구"
    GANGBUK = "강북구"
    GANGSEO = "강서구"
    GWANAK = "관악구"
    GWANGJIN = "광진구"
    GURO = "구로구"
    GEUMCHEON = "금천구"
    NOWON = "노원구"
    DOBONG = "도봉구"
    DONGDAEMUN = "동대문구"
    DONGJAK = "동작구"
    MAPO = "마포구"
    SEODAEMUN = "서대문구"
    SEOCHO = "서초구"
    SEONGDONG = "성동구"
    SEONGBUK = "성북구"
    SONGPA = "송파구"
    YANGCHEON = "양천구"
    YEONGDEUNGPO = "영등포구"
    YONGSAN = "용산구"
    EUNPYEONG = "은평구"
    JONGNO = "종로구"
    JUNG = "중구"
    JUNGNANG = "중랑구"


class UserStatus(StrEnum):
    ENROLLED = "enrolled"
    ON_LEAVE = "on_leave"
    FINAL_SEMESTER = "final_semester"
    JOB_SEEKING = "job_seeking"
    EMPLOYED = "employed"


class Category(StrEnum):
    SCHOLARSHIP = "scholarship"
    LIVING = "living"
    JOB = "job"
    CULTURE = "culture"
    HOUSING = "housing"
    ALL = "all"


class IncomeBracket(StrEnum):
    UNDER_50 = "under_50"
    FROM_50_TO_100 = "50_100"
    FROM_100_TO_150 = "100_150"
    OVER_150 = "over_150"
    UNKNOWN = "unknown"


class HousingType(StrEnum):
    PARENTS = "parents"
    MONTHLY_RENT = "monthly_rent"
    JEONSE = "jeonse"
    DORMITORY = "dormitory"
    OTHER = "other"


class ResidencePeriod(StrEnum):
    UNDER_6M = "under_6m"
    FROM_6M_TO_1Y = "6m_1y"
    OVER_1Y = "over_1y"


class RemainingSemesters(StrEnum):
    ONE = "one"
    TWO_PLUS = "two_plus"


class JobSeekingPeriod(StrEnum):
    UNDER_6M = "under_6m"
    OVER_6M = "over_6m"


class YesNoUnknown(StrEnum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


class YesNo(StrEnum):
    YES = "yes"
    NO = "no"


class HouseholdSize(StrEnum):
    ONE = "1"
    TWO = "2"
    THREE = "3"
    FOUR_PLUS = "4_plus"


class LastGpa(StrEnum):
    ABOVE = "above"
    BELOW = "below"
    UNKNOWN = "unknown"


Age = Annotated[int, Field(strict=True, ge=15, le=39)]
CategorySelection = Annotated[list[Category], Field(min_length=1)]


def _check_categories(categories: list[Category]) -> list[Category]:
    if len(categories) != len(set(categories)):
        raise ValueError("categories must not contain duplicates")
    if Category.ALL in categories and len(categories) != 1:
        raise ValueError("all cannot be combined with another category")
    return categories


class ProfileInput(ContractModel):
    """POST /session input; region is deliberately not client-provided."""

    age: Age
    district: District
    status: UserStatus
    categories: CategorySelection
    income_bracket: IncomeBracket = IncomeBracket.UNKNOWN

    _validate_categories = field_validator("categories")(_check_categories)


class Profile(ProfileInput):
    """Normalized in-memory profile returned by the server.

    Seoul is a product invariant, not a form value. Additional fields are
    optional and may be populated by conversation modules later.
    """

    region: Literal[Region.SEOUL] = Region.SEOUL
    housing_type: HousingType | None = None
    residence_period: ResidencePeriod | None = None
    remaining_semesters: RemainingSemesters | None = None
    job_seeking_period: JobSeekingPeriod | None = None
    employment_insurance: YesNoUnknown | None = None
    other_benefit: YesNo | None = None
    household_size: HouseholdSize | None = None
    last_gpa: LastGpa | None = None

    @classmethod
    def from_input(cls, profile_input: ProfileInput) -> "Profile":
        return cls.model_validate(profile_input.model_dump())


class ProfileField(StrEnum):
    AGE = "age"
    DISTRICT = "district"
    STATUS = "status"
    CATEGORIES = "categories"
    INCOME_BRACKET = "income_bracket"
    HOUSING_TYPE = "housing_type"
    RESIDENCE_PERIOD = "residence_period"
    REMAINING_SEMESTERS = "remaining_semesters"
    JOB_SEEKING_PERIOD = "job_seeking_period"
    EMPLOYMENT_INSURANCE = "employment_insurance"
    OTHER_BENEFIT = "other_benefit"
    HOUSEHOLD_SIZE = "household_size"
    LAST_GPA = "last_gpa"


class AskableProfileField(StrEnum):
    DISTRICT = "district"
    INCOME_BRACKET = "income_bracket"
    HOUSING_TYPE = "housing_type"
    RESIDENCE_PERIOD = "residence_period"
    REMAINING_SEMESTERS = "remaining_semesters"
    JOB_SEEKING_PERIOD = "job_seeking_period"
    EMPLOYMENT_INSURANCE = "employment_insurance"
    OTHER_BENEFIT = "other_benefit"
    HOUSEHOLD_SIZE = "household_size"
    LAST_GPA = "last_gpa"


class PlannedBasis(StrEnum):
    """Which point in time a pending future change should be evaluated against."""

    PLANNED = "planned"
    CURRENT = "current"


FollowupField = AskableProfileField | Literal["planned_basis"]


class ProfilePatch(ContractModel):
    age: Age | None = None
    district: District | None = None
    status: UserStatus | None = None
    categories: CategorySelection | None = None
    income_bracket: IncomeBracket | None = None
    housing_type: HousingType | None = None
    residence_period: ResidencePeriod | None = None
    remaining_semesters: RemainingSemesters | None = None
    job_seeking_period: JobSeekingPeriod | None = None
    employment_insurance: YesNoUnknown | None = None
    other_benefit: YesNo | None = None
    household_size: HouseholdSize | None = None
    last_gpa: LastGpa | None = None

    @field_validator("categories")
    @classmethod
    def validate_optional_categories(
        cls, categories: list[Category] | None
    ) -> list[Category] | None:
        return None if categories is None else _check_categories(categories)

    @model_validator(mode="after")
    def validate_explicit_values(self) -> "ProfilePatch":
        if not self.model_fields_set:
            raise ValueError("at least one profile field is required")
        for field_name in ("age", "district", "status", "categories"):
            if field_name in self.model_fields_set and getattr(self, field_name) is None:
                raise ValueError(f"{field_name} cannot be null")
        return self


class PolicySourceKind(StrEnum):
    MANUAL = "manual"
    CRAWLED = "crawled"


class PolicyDataStatus(StrEnum):
    VERIFIED = "verified"
    RECHECK = "recheck"
    CLOSED = "closed"
    UPCOMING = "upcoming"


class Policy(ContractModel):
    """Backend A policy-data boundary, without eligibility behavior."""

    id: Annotated[str, Field(min_length=1)]
    title: Annotated[str, Field(min_length=1)]
    agency: Annotated[str, Field(min_length=1)]
    categories: CategorySelection
    source_url: HttpUrl
    apply_url: HttpUrl | None = None
    checked_at: date | None = None
    fetched_at: date | None = None
    source_kind: PolicySourceKind = PolicySourceKind.MANUAL
    data_status: PolicyDataStatus = PolicyDataStatus.VERIFIED
    apply_start: date | None = None
    apply_end: date | None = None
    age_min: int | None = None
    age_max: int | None = None
    regions: list[str] = Field(default_factory=list)
    statuses: list[UserStatus] = Field(default_factory=list)
    income_max_pct: float | None = None
    extra_conditions: list[str] = Field(default_factory=list)
    exceptions_text: str = ""
    benefit: str = ""
    documents: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    raw_text: str = ""
    condition_sources: dict[str, str] = Field(default_factory=dict)

    _validate_categories = field_validator("categories")(_check_categories)


class EvaluationStatus(StrEnum):
    LIKELY = "likely"
    CHECK = "check"
    UNLIKELY = "unlikely"


class ConditionResult(StrEnum):
    MET = "met"
    UNMET = "unmet"
    UNKNOWN = "unknown"


class JudgedBy(StrEnum):
    RULE = "rule"
    AI = "ai"


class ConditionEvaluation(ContractModel):
    name: Annotated[str, Field(min_length=1, max_length=20)]
    result: ConditionResult
    judged_by: JudgedBy
    excerpt: Annotated[str, Field(min_length=10, max_length=150)] | None = None
    source_url: HttpUrl
    footnote_id: Annotated[int, Field(strict=True, ge=1)]
    needed_field: AskableProfileField | Literal["공고 확인 필요"] | None = None


class Deadline(ContractModel):
    apply_start: date | None = None
    apply_end: date | None = None
    d_day: int | None = None
    badge: str
    is_imminent: bool


class PolicyEvaluation(ContractModel):
    """Shared API DTO; no policy-evaluation rules are implemented here."""

    policy_id: Annotated[str, Field(min_length=1)]
    title: Annotated[str, Field(min_length=1)]
    agency: Annotated[str, Field(min_length=1)]
    categories: CategorySelection
    status: EvaluationStatus
    status_label: Annotated[str, Field(min_length=1)]
    benefit: str
    conditions: list[ConditionEvaluation]
    conditional_note: str | None = None
    deadline: Deadline
    documents: list[str]
    steps: list[str]
    source_url: HttpUrl
    apply_url: HttpUrl | None = None
    checked_at: date
    data_status: PolicyDataStatus

    _validate_categories = field_validator("categories")(_check_categories)


class FollowupOption(ContractModel):
    value: JsonValue
    label: Annotated[str, Field(min_length=1)]


class FollowupQuestion(ContractModel):
    field: FollowupField
    question: Annotated[str, Field(min_length=1)]
    reason: Annotated[str, Field(min_length=1)]
    options: list[FollowupOption]
    allow_free_text: bool
    allow_skip: Literal[True] = True


class SessionCreateResponse(ContractModel):
    session_id: Annotated[str, Field(min_length=1)]
    profile: Profile
    policies: list[PolicyEvaluation]
    hidden_unlikely_count: Annotated[int, Field(strict=True, ge=0)]
    followup: FollowupQuestion | None = None


class MessageTurn(ContractModel):
    type: Literal["message"]
    message: Annotated[str, Field(min_length=1)]


class FollowupAnswerTurn(ContractModel):
    type: Literal["followup_answer"]
    field: FollowupField
    value: JsonValue


class FollowupSkipTurn(ContractModel):
    type: Literal["followup_skip"]
    field: FollowupField


ChatTurn = Annotated[
    MessageTurn | FollowupAnswerTurn | FollowupSkipTurn,
    Field(discriminator="type"),
]


class ChatRequest(ContractModel):
    session_id: Annotated[str, Field(min_length=1)]
    client_message_id: Annotated[str, Field(min_length=1, max_length=128)]
    turn: ChatTurn


class PolicyDependencyStatus(StrEnum):
    CONNECTED = "connected"
    UNCONNECTED = "unconnected"


class HealthResponse(ContractModel):
    status: Literal["ok"] = "ok"
    verified_policy_count: Annotated[int, Field(strict=True, ge=0)] | None
    policy_dependency: PolicyDependencyStatus
    llm_adapter_configured: bool
