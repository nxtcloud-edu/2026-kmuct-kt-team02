"""Backend B orchestration across rules, AI A, AI B, and SSE semantics."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from pydantic import JsonValue, ValidationError

from ai.conversation import pipeline as conversation_pipeline
from server.chat_service import ChatPipelineRequest, ChatServiceEvent
from server.config import TURN_BUDGET_CEILING_S
from server.errors import AppError, ErrorCode, default_message
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
    unknown_items_from_policies,
    validate_ai_json,
)
from server.policy_repository import PolicySourceCatalog
from server.rule_engine import RuleEngine, RuleEngineResult
from server.schemas import ContractModel, FollowupQuestion, Policy, PolicyEvaluation
from server.session_store import SessionStore
from server.sse import (
    AnswerDeltaEventData,
    Footnote,
    FootnotesEventData,
    PoliciesEventData,
    ProfileUpdateEventData,
    RelatedEventData,
    SSEEventName,
    StatusEventData,
)

_LOGGER = logging.getLogger(__name__)

#: 답변을 만들지 못했을 때 대신 보내는 문구.
#:
#: `server/errors.py` 의 사용자 문구를 그대로 쓴다. 같은 실패를 HTTP 오류와 스트림에서
#: 다른 말로 설명하면 프론트가 두 문구를 따로 관리해야 한다. 마침표는 여기서 붙인다 —
#: 오류 응답은 문구만 담고, 스트림은 답변 본문 자리라 문장으로 끝나야 한다.
_FALLBACK_ANSWER = f"{default_message(ErrorCode.ANSWER_FAILED)}."

#: 예산이 다 떨어진 단계에 그래도 남겨 주는 시간. 0 을 넘기면 `asyncio.timeout` 이
#: 즉시 만료돼 폴백으로 가는데, 그 판단을 암묵적으로 두지 않고 호출부에서 명시한다.
_STARVED_S = 0.0


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
    #: 이 판정이 끝나야 하는 시점. **`time.monotonic()` 기준**이다.
    #:
    #: 없으면 구현체가 자기 예산만 본다. 그러면 한 턴의 남은 시간과 무관하게 매번 같은
    #: 시간을 쓰고, 앞 단계가 느렸던 턴에서 전체 상한을 넘긴다. 서버가 시점을 넘겨야
    #: 판정기가 **서버 타임아웃보다 먼저** 스스로 멈추고 "미확인" 자리표시자를 돌려줄 수
    #: 있다. 서버가 먼저 끊으면 그 부분 결과까지 버려진다.
    deadline: float | None = None


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
            # 답변만 포기하고 **후속 질문과 칩은 살린다.** `finish_turn` 이 이미 계산해 둔
            # 값이다. 금지 표현 하나로 답변이 버려진 턴에서 후속 질문 버튼까지 사라지면
            # 사용자는 대화를 이어갈 수단을 잃고, 그 원인은 화면에 보이지 않는다.
            # `ai/turn.py` 의 실패표도 "답변 실패 → 카드·각주·후속 질문·칩은 남는다"다.
            return (
                [AnswerDeltaEventData(delta=_FALLBACK_ANSWER)],
                footnotes,
                finalized.followup if finalized is not None else None,
                finalized.related if finalized is not None else None,
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
