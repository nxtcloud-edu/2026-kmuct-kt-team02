"""Backend B orchestration across rules, AI A, AI B, and SSE semantics."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pydantic import JsonValue

from server.chat_service import ChatPipelineRequest, ChatServiceEvent
from server.orchestrator_adapters import (
    ExceptionJudgmentOutput,
    Intent,
    MessageInterpretationOutput,
    apply_message_interpretation,
    build_answer_prompt,
    finalize_answer,
    footnotes_from_policies,
    merge_exception_judgment,
    next_footnote_id,
    unknown_exception_judgment,
    validate_ai_json,
)
from server.rule_engine import RuleEngine, RuleEngineResult
from server.schemas import ContractModel, Policy, PolicyEvaluation
from server.session_store import SessionStore
from server.sse import (
    AnswerDeltaEventData,
    FootnotesEventData,
    PoliciesEventData,
    ProfileUpdateEventData,
    SSEEventName,
    StatusEventData,
)

_FALLBACK_ANSWER = "설명을 불러오지 못했어요. 카드에서 조건을 확인해 주세요."


class LLMTask(StrEnum):
    INTERPRET_MESSAGE = "interpret_message"
    COMPOSE_ANSWER = "compose_answer"


class StructuredLLMRequest(ContractModel):
    task: LLMTask
    input: dict[str, JsonValue]
    output_schema: dict[str, JsonValue]


class TextLLMRequest(ContractModel):
    task: LLMTask
    system: str = ""
    user: str


class LLMClient(Protocol):
    """Provider-neutral interface; model IDs belong only in implementations."""

    async def complete_json(self, request: StructuredLLMRequest) -> object:
        """Return untrusted structured model output."""
        ...

    def stream_text(self, request: TextLLMRequest) -> AsyncIterator[str]:
        """Yield untrusted answer text chunks."""
        ...


class ExceptionJudgeRequest(ContractModel):
    policy_id: str
    exceptions_text: str
    raw_text: str
    profile: dict[str, JsonValue]
    output_schema: dict[str, JsonValue]


class ExceptionJudge(Protocol):
    """AI B boundary; no production implementation exists in the repository yet."""

    async def judge(self, request: ExceptionJudgeRequest) -> object:
        """Return untrusted exception-condition JSON."""
        ...


class PolicySourceCatalog(Protocol):
    """Backend A source-data lookup needed for exception citation checks."""

    def get(self, policy_id: str) -> Policy | None:
        ...


@dataclass(frozen=True, slots=True)
class OrchestratorTimeouts:
    interpretation_s: float = 3.0
    rule_engine_s: float = 1.0
    judgment_s: float = 8.0
    answer_s: float = 15.0

    def __post_init__(self) -> None:
        if any(
            value <= 0
            for value in (
                self.interpretation_s,
                self.rule_engine_s,
                self.judgment_s,
                self.answer_s,
            )
        ):
            raise ValueError("all orchestrator timeouts must be positive")


class BackendBOrchestrator:
    """Concrete ChatService using real deterministic modules and injected ports."""

    def __init__(
        self,
        *,
        llm_client: LLMClient,
        rule_engine: RuleEngine,
        policy_sources: PolicySourceCatalog,
        exception_judge: ExceptionJudge | None,
        session_store: SessionStore,
        timeouts: OrchestratorTimeouts | None = None,
    ) -> None:
        self._llm_client = llm_client
        self._rule_engine = rule_engine
        self._policy_sources = policy_sources
        self._exception_judge = exception_judge
        self._session_store = session_store
        self._timeouts = timeouts or OrchestratorTimeouts()

    async def _interpret(self, request: ChatPipelineRequest):
        llm_request = StructuredLLMRequest(
            task=LLMTask.INTERPRET_MESSAGE,
            input={
                "message": request.masked_message,
                "profile": request.profile.model_dump(mode="json"),
            },
            output_schema=MessageInterpretationOutput.model_json_schema(),
        )
        try:
            async with asyncio.timeout(self._timeouts.interpretation_s):
                raw_output = await self._llm_client.complete_json(llm_request)
            output = validate_ai_json(MessageInterpretationOutput, raw_output)
        except Exception:
            output = MessageInterpretationOutput()
        return apply_message_interpretation(request.profile, output)

    async def _run_rules(self, profile) -> RuleEngineResult:
        async with asyncio.timeout(self._timeouts.rule_engine_s):
            return await asyncio.to_thread(self._rule_engine.evaluate, profile, limit=5)

    async def _judge_one(
        self,
        policy: PolicyEvaluation,
        source: Policy,
        profile,
    ) -> ExceptionJudgmentOutput:
        if not source.exceptions_text.strip():
            return ExceptionJudgmentOutput()
        if self._exception_judge is None:
            return unknown_exception_judgment()

        request = ExceptionJudgeRequest(
            policy_id=policy.policy_id,
            exceptions_text=source.exceptions_text,
            raw_text=source.raw_text,
            profile=profile.model_dump(mode="json"),
            output_schema=ExceptionJudgmentOutput.model_json_schema(),
        )
        try:
            async with asyncio.timeout(self._timeouts.judgment_s):
                raw_output = await self._exception_judge.judge(request)
            return validate_ai_json(ExceptionJudgmentOutput, raw_output)
        except Exception:
            return unknown_exception_judgment()

    async def _apply_exception_judgments(
        self,
        policies: list[PolicyEvaluation],
        profile,
    ) -> tuple[list[PolicyEvaluation], bool]:
        candidates: list[tuple[int, PolicyEvaluation, Policy]] = []
        for index, policy in enumerate(policies):
            source = self._policy_sources.get(policy.policy_id)
            if source is not None and source.exceptions_text.strip():
                candidates.append((index, policy, source))
        if not candidates:
            return policies, False

        outputs = await asyncio.gather(
            *(
                self._judge_one(policy, source, profile)
                for _index, policy, source in candidates
            )
        )
        updated = list(policies)
        footnote_id = next_footnote_id(policies)
        changed = False
        for (index, policy, source), output in zip(candidates, outputs, strict=True):
            merged = merge_exception_judgment(
                policy,
                source,
                output,
                first_footnote_id=footnote_id,
            )
            updated[index] = merged.policy
            changed = changed or merged.changed
            footnote_id += len(merged.policy.conditions) - len(policy.conditions)
        return updated, changed

    async def _generate_answer(
        self,
        *,
        profile,
        policies: list[PolicyEvaluation],
        intent: Intent,
        fixed_reply: str | None,
    ):
        footnotes = footnotes_from_policies(policies)
        if fixed_reply:
            return (
                [AnswerDeltaEventData(delta=fixed_reply)],
                footnotes,
                None,
                None,
            )

        prompt = build_answer_prompt(profile, policies, footnotes)
        chunks: list[str] = []
        try:
            request = TextLLMRequest(
                task=LLMTask.COMPOSE_ANSWER,
                system=prompt.system,
                user=prompt.user,
            )
            async with asyncio.timeout(self._timeouts.answer_s):
                async for chunk in self._llm_client.stream_text(request):
                    if not isinstance(chunk, str):
                        raise TypeError("answer chunks must be strings")
                    chunks.append(chunk)
            finalized = finalize_answer(
                "".join(chunks),
                policies=policies,
                footnotes=footnotes,
                intent=intent,
            )
        except Exception:
            finalized = None

        if finalized is None or finalized.answer_failed or not finalized.answer_deltas:
            return (
                [AnswerDeltaEventData(delta=_FALLBACK_ANSWER)],
                footnotes,
                None,
                None,
            )
        return (
            finalized.answer_deltas,
            footnotes,
            finalized.followup,
            finalized.related,
        )

    async def stream(
        self,
        request: ChatPipelineRequest,
    ) -> AsyncIterator[ChatServiceEvent]:
        yield ChatServiceEvent(
            SSEEventName.STATUS,
            StatusEventData(stage="searching"),
        )

        turn = await self._interpret(request)
        if turn.profile != request.profile:
            self._session_store.update_profile(request.session_id, turn.profile)

        profile_update = turn.profile_update
        if request.pii_notice:
            if profile_update is None:
                profile_update = ProfileUpdateEventData(
                    changed_fields={},
                    message=request.pii_notice,
                    profile=turn.profile,
                )
            else:
                profile_update = ProfileUpdateEventData(
                    changed_fields=profile_update.changed_fields,
                    message=f"{request.pii_notice} {profile_update.message}",
                    profile=profile_update.profile,
                )
        if profile_update is not None:
            yield ChatServiceEvent(SSEEventName.PROFILE_UPDATE, profile_update)

        rule_result = await self._run_rules(turn.profile)
        initial_payload = PoliciesEventData(
            policies=rule_result.policies,
            hidden_unlikely_count=rule_result.hidden_unlikely_count,
        )
        yield ChatServiceEvent(SSEEventName.POLICIES, initial_payload)
        yield ChatServiceEvent(
            SSEEventName.STATUS,
            StatusEventData(stage="checking"),
        )

        final_policies, changed = await self._apply_exception_judgments(
            list(rule_result.policies),
            turn.profile,
        )
        if changed:
            yield ChatServiceEvent(
                SSEEventName.POLICIES,
                PoliciesEventData(
                    policies=final_policies,
                    hidden_unlikely_count=rule_result.hidden_unlikely_count,
                ),
            )

        yield ChatServiceEvent(
            SSEEventName.STATUS,
            StatusEventData(stage="summarizing"),
        )
        answer_deltas, footnotes, followup, related = await self._generate_answer(
            profile=turn.profile,
            policies=final_policies,
            intent=turn.intent,
            fixed_reply=turn.fixed_reply,
        )
        for delta in answer_deltas:
            yield ChatServiceEvent(SSEEventName.ANSWER_DELTA, delta)
        yield ChatServiceEvent(
            SSEEventName.FOOTNOTES,
            FootnotesEventData(footnotes=footnotes),
        )
        if followup is not None:
            yield ChatServiceEvent(SSEEventName.FOLLOWUP, followup)
        if related is not None:
            yield ChatServiceEvent(SSEEventName.RELATED, related)
