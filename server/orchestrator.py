"""Backend B orchestration across rules, AI A, AI B, and SSE semantics."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pydantic import JsonValue

from ai.conversation import answer as conversation_answer
from server.chat_service import ChatPipelineRequest, ChatServiceEvent
from server.metrics import record_chat_metric
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
    planned_followup,
)
from server.policy_repository import PolicySourceCatalog
from server.rule_engine import RuleEngine, RuleEngineResult
from server.schemas import ContractModel, Policy, PolicyEvaluation
from server.session_store import PendingProfileChange, SessionStore, TurnSummary
from server.sse import (
    AnswerDeltaEventData,
    FootnotesEventData,
    PoliciesEventData,
    ProfileUpdateEventData,
    ErrorEventData,
    SSEEventName,
    StatusEventData,
)

_FALLBACK_ANSWER = "설명을 불러오지 못했어요. 카드에서 조건을 확인해 주세요."
_SENTENCE_END = re.compile(r"[.!?。！？]+(?:\s+|$)")


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
        if request.turn_type != "message":
            return apply_message_interpretation(
                request.profile,
                MessageInterpretationOutput(intent=Intent.FIND_POLICY),
            )
        llm_request = StructuredLLMRequest(
            task=LLMTask.INTERPRET_MESSAGE,
            input={
                "message": request.masked_message or "",
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
    ) -> tuple[list[PolicyEvaluation], bool, int]:
        candidates: list[tuple[int, PolicyEvaluation, Policy]] = []
        for index, policy in enumerate(policies):
            source = self._policy_sources.get(policy.policy_id)
            if source is not None and source.exceptions_text.strip():
                candidates.append((index, policy, source))
        if not candidates:
            return policies, False, 0

        outputs = await asyncio.gather(
            *(
                self._judge_one(policy, source, profile)
                for _index, policy, source in candidates
            )
        )
        updated = list(policies)
        footnote_id = next_footnote_id(policies)
        changed = False
        citation_failures = 0
        for (index, policy, source), output in zip(candidates, outputs, strict=True):
            merged = merge_exception_judgment(
                policy,
                source,
                output,
                first_footnote_id=footnote_id,
            )
            updated[index] = merged.policy
            changed = changed or merged.changed
            citation_failures += merged.citation_failures
            footnote_id += len(merged.policy.conditions) - len(policy.conditions)
        return updated, changed, citation_failures

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

    @staticmethod
    def _complete_sentences(buffer: str) -> tuple[list[str], str]:
        """Keep an incomplete model fragment private until a sentence is complete."""

        completed: list[str] = []
        cursor = 0
        for match in _SENTENCE_END.finditer(buffer):
            sentence = buffer[cursor:match.end()].strip()
            if sentence:
                completed.append(sentence)
            cursor = match.end()
        return completed, buffer[cursor:]

    async def _stream_safe_answer(
        self,
        *,
        profile,
        policies: list[PolicyEvaluation],
        intent: Intent,
        fixed_reply: str | None,
    ) -> AsyncIterator[ChatServiceEvent]:
        """Emit only sentence-complete, citation-sanitized answer deltas."""

        footnotes = footnotes_from_policies(policies)
        if fixed_reply:
            yield ChatServiceEvent(SSEEventName.ANSWER_DELTA, AnswerDeltaEventData(delta=fixed_reply))
            yield ChatServiceEvent(SSEEventName.FOOTNOTES, FootnotesEventData(footnotes=footnotes))
            return

        raw_parts: list[str] = []
        pending = ""
        emitted = False
        failed = False
        followup = None
        related = None
        try:
            prompt = build_answer_prompt(profile, policies, footnotes)
            llm_request = TextLLMRequest(
                task=LLMTask.COMPOSE_ANSWER,
                system=prompt.system,
                user=prompt.user,
            )
            async with asyncio.timeout(self._timeouts.answer_s):
                async for chunk in self._llm_client.stream_text(llm_request):
                    if not isinstance(chunk, str):
                        raise TypeError("answer chunks must be strings")
                    raw_parts.append(chunk)
                    complete, pending = self._complete_sentences(pending + chunk)
                    for sentence in complete:
                        cleaned = conversation_answer.sanitize(
                            sentence,
                            [item.model_dump(mode="json") for item in footnotes],
                        )
                        checked = conversation_answer.validate(
                            cleaned.text,
                            [item.model_dump(mode="json") for item in footnotes],
                        )
                        if cleaned.text.strip() and checked.ok:
                            emitted = True
                            yield ChatServiceEvent(
                                SSEEventName.ANSWER_DELTA,
                                AnswerDeltaEventData(delta=cleaned.text.strip()),
                            )

            if pending.strip():
                cleaned = conversation_answer.sanitize(
                    pending,
                    [item.model_dump(mode="json") for item in footnotes],
                )
                checked = conversation_answer.validate(
                    cleaned.text,
                    [item.model_dump(mode="json") for item in footnotes],
                )
                if cleaned.text.strip() and checked.ok:
                    emitted = True
                    yield ChatServiceEvent(
                        SSEEventName.ANSWER_DELTA,
                        AnswerDeltaEventData(delta=cleaned.text.strip()),
                    )

            finalized = finalize_answer(
                "".join(raw_parts),
                policies=policies,
                footnotes=footnotes,
                intent=intent,
            )
            followup = finalized.followup
            related = finalized.related
            # A sentence can be locally incomplete even though the completed
            # answer is valid as a whole (for example, a citation follows the
            # sentence boundary). In that case fall back to the final safe
            # splitter rather than exposing raw chunks or failing a good turn.
            if not emitted and not finalized.answer_failed:
                for delta in finalized.answer_deltas:
                    emitted = True
                    yield ChatServiceEvent(SSEEventName.ANSWER_DELTA, delta)
            failed = finalized.answer_failed or not emitted
        except Exception:
            failed = True

        if failed:
            yield ChatServiceEvent(
                SSEEventName.ANSWER_DELTA,
                AnswerDeltaEventData(delta=_FALLBACK_ANSWER),
            )
            yield ChatServiceEvent(
                SSEEventName.ERROR,
                ErrorEventData(
                    code="answer_failed",
                    message=_FALLBACK_ANSWER,
                ),
            )
            # The transport appends the sole `done` event after this terminal
            # error.  Do not leak optional follow-ups or chips from an answer
            # that could not pass the safety gate.
            return
        yield ChatServiceEvent(SSEEventName.FOOTNOTES, FootnotesEventData(footnotes=footnotes))
        if followup is not None:
            yield ChatServiceEvent(SSEEventName.FOLLOWUP, followup)
        if related is not None:
            yield ChatServiceEvent(SSEEventName.RELATED, related)

    async def stream(
        self,
        request: ChatPipelineRequest,
    ) -> AsyncIterator[ChatServiceEvent]:
        turn_started_at = time.perf_counter()
        yield ChatServiceEvent(
            SSEEventName.STATUS,
            StatusEventData(stage="searching"),
        )

        interpretation_started_at = time.perf_counter()
        turn = await self._interpret(request)
        interpretation_ms = int((time.perf_counter() - interpretation_started_at) * 1000)
        if turn.profile != request.profile:
            current_fields = (
                {change.field for change in turn.profile_update.changes}
                if turn.profile_update is not None
                else set()
            )
            self._session_store.update_profile(
                request.session_id,
                turn.profile,
                clear_pending_fields=current_fields,
            )
        if turn.planned_changes:
            self._session_store.set_pending_planned_changes(
                request.session_id,
                tuple(
                    PendingProfileChange(field, value)
                    for field, value in turn.planned_changes.items()
                ),
            )
        # Direct follow-up turns were already summarized atomically with their
        # profile update by SessionStore.  Only interpreted message turns need
        # a new summary here.
        if request.turn_type == "message":
            self._session_store.record_turn_summary(
                request.session_id,
                TurnSummary(kind=request.turn_type, intent=turn.intent.value),
            )

        profile_update = turn.profile_update
        if request.pii_notice and profile_update is not None:
            profile_update = ProfileUpdateEventData(
                changes=profile_update.changes,
                notice=f"{request.pii_notice} {profile_update.notice}",
                profile=profile_update.profile,
            )
        if profile_update is not None:
            yield ChatServiceEvent(SSEEventName.PROFILE_UPDATE, profile_update)

        rules_started_at = time.perf_counter()
        rule_result = await self._run_rules(turn.profile)
        rules_ms = int((time.perf_counter() - rules_started_at) * 1000)
        initial_payload = PoliciesEventData(
            policies=rule_result.policies,
            hidden_unlikely_count=rule_result.hidden_unlikely_count,
        )
        yield ChatServiceEvent(SSEEventName.POLICIES, initial_payload)
        yield ChatServiceEvent(
            SSEEventName.STATUS,
            StatusEventData(stage="checking"),
        )

        judgment_started_at = time.perf_counter()
        final_policies, changed, citation_failures = await self._apply_exception_judgments(
            list(rule_result.policies),
            turn.profile,
        )
        judgment_ms = int((time.perf_counter() - judgment_started_at) * 1000)
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
        answer_started_at = time.perf_counter()
        async for event in self._stream_safe_answer(
            profile=turn.profile,
            policies=final_policies,
            intent=turn.intent,
            fixed_reply=turn.fixed_reply,
        ):
            # A planned-basis question is the only follow-up for its turn.
            if event.event == SSEEventName.FOLLOWUP and planned_followup(turn) is not None:
                continue
            yield event
        planned_question = planned_followup(turn)
        if planned_question is not None:
            yield ChatServiceEvent(SSEEventName.FOLLOWUP, planned_question)
        record_chat_metric(
            {
                "duration_ms": int((time.perf_counter() - turn_started_at) * 1000),
                "outcome": "orchestrated",
                "citation_failures": citation_failures,
                "interpretation_ms": interpretation_ms,
                "rules_ms": rules_ms,
                "judgment_ms": judgment_ms,
                "answer_ms": int((time.perf_counter() - answer_started_at) * 1000),
            }
        )
