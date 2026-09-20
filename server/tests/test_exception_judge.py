"""`server/exception_judge.py` 계약 고정.

가장 중요한 것은 **어댑터 출력이 `ExceptionJudgmentOutput` 검증을 통과하는지**다.
통과하지 못하면 `orchestrator._judge_one` 의 `except` 가 전건을 미확인으로 떨궈
카드 전체가 "확인이 필요해요"가 된다. 오류 화면이 아니라 결론 없는 화면이 되기 때문에
눈으로는 정상처럼 보인다. 그래서 테스트로 잡아야 한다.
"""

import asyncio

from server.exception_judge import ASK_NOTICE, AiBExceptionJudgeAdapter
from server.orchestrator import ExceptionJudgeRequest
from server.orchestrator_adapters import ExceptionJudgmentOutput, validate_ai_json

VERIFIED_EXCERPT = "채무조정, 개인회생자 중 성실 상환자 및 완제자에 한한다"


class FakeAiBJudge:
    """AI B 의 `judge_and_verify` 만 흉내낸다."""

    def __init__(self, conditions: list[dict], removed: list[dict] | None = None) -> None:
        self._conditions = conditions
        self._removed = removed or []
        self.calls: list[tuple[dict, object]] = []

    def judge_and_verify(self, policy, profile=None, *, deadline=None):
        self.calls.append((policy, profile))
        return {
            "policy_id": policy.get("id"),
            "conditions": self._conditions,
            "removed": self._removed,
        }


def request() -> ExceptionJudgeRequest:
    return ExceptionJudgeRequest(
        policy_id="SEOUL-005",
        exceptions_text="채무조정, 개인회생자 중 성실 상환자 및 완제자",
        raw_text=f"신청자격 ... {VERIFIED_EXCERPT} ... 문의",
        profile={"age": 27, "status": "job_seeking", "region": "seoul"},
        output_schema={"type": "object"},
    )


def judge(conditions: list[dict], removed: list[dict] | None = None) -> dict:
    adapter = AiBExceptionJudgeAdapter(FakeAiBJudge(conditions, removed))
    return asyncio.run(adapter.judge(request()))


def ai_b_condition(**overrides) -> dict:
    """AI B 가 실제로 내보내는 모양. 영문 결과와 여분 키를 모두 포함한다."""
    base = {
        "name": "채무조정 성실상환",
        "summary": "채무조정 성실상환",
        "result": "met",
        "judged_by": "ai",
        "excerpt": VERIFIED_EXCERPT,
        "excerpt_verified": True,
        "needed_field": None,
    }
    base.update(overrides)
    return base


# --- 계약 통과 ------------------------------------------------------------


def test_output_passes_the_strict_server_contract() -> None:
    """이 테스트가 깨지면 모든 카드가 조용히 '확인이 필요해요'로 바뀐다."""
    output = judge([ai_b_condition()])

    validated = validate_ai_json(ExceptionJudgmentOutput, output)
    assert len(validated.conditions) == 1
    condition = validated.conditions[0]
    assert condition.summary == "채무조정 성실상환"
    assert condition.result == "충족"
    assert condition.excerpt == VERIFIED_EXCERPT


def test_extra_ai_b_keys_are_dropped() -> None:
    """`ContractModel` 은 extra='forbid' 다. judged_by 하나만 남아도 전건 실패한다."""
    output = judge([ai_b_condition()])

    assert set(output["conditions"][0]) == {
        "summary",
        "result",
        "excerpt",
        "needed_field",
    }


# --- 결과 값 변환 ---------------------------------------------------------


def test_english_results_become_korean() -> None:
    output = judge(
        [
            ai_b_condition(name="가", summary="가", result="met"),
            ai_b_condition(name="나", summary="나", result="unmet"),
            ai_b_condition(name="다", summary="다", result="unknown"),
        ]
    )

    assert [item["result"] for item in output["conditions"]] == [
        "충족",
        "미충족",
        "미확인",
    ]
    validate_ai_json(ExceptionJudgmentOutput, output)


def test_unrecognized_result_becomes_unknown_rather_than_failing() -> None:
    """모르는 값을 충족으로 읽으면 안 되는 사람에게 된다고 말한다. 미충족 오판보다 나쁘다."""
    output = judge([ai_b_condition(result="아마도")])

    assert output["conditions"][0]["result"] == "미확인"


# --- 요약 이름 ------------------------------------------------------------


def test_summary_falls_back_to_name() -> None:
    condition = ai_b_condition()
    del condition["summary"]

    output = judge([condition])

    assert output["conditions"][0]["summary"] == "채무조정 성실상환"


def test_empty_summary_becomes_the_ask_notice() -> None:
    """자리표시자 조건은 이름이 빈 문자열이다. min_length=1 에 걸린다."""
    output = judge([ai_b_condition(name="", summary="", result="unknown")])

    assert output["conditions"][0]["summary"] == ASK_NOTICE
    validate_ai_json(ExceptionJudgmentOutput, output)


def test_long_summary_is_trimmed_to_the_contract_limit() -> None:
    output = judge([ai_b_condition(name="가" * 40, summary="가" * 40)])

    assert output["conditions"][0]["summary"] == "가" * 20
    validate_ai_json(ExceptionJudgmentOutput, output)


# --- 발췌 -----------------------------------------------------------------


def test_unverified_excerpt_is_dropped() -> None:
    """대조에 실패한 문장을 근거로 보여주면 공고에 없는 말을 인용하는 셈이다."""
    output = judge([ai_b_condition(excerpt_verified=False)])

    assert output["conditions"][0]["excerpt"] is None


def test_excerpt_longer_than_the_contract_limit_is_dropped() -> None:
    """AI B 는 정규화 길이로 재지만 돌려주는 값은 원문 구간이라 더 길 수 있다."""
    output = judge([ai_b_condition(excerpt="가" * 200)])

    assert output["conditions"][0]["excerpt"] is None
    validate_ai_json(ExceptionJudgmentOutput, output)


def test_excerpt_shorter_than_the_contract_minimum_is_dropped() -> None:
    output = judge([ai_b_condition(excerpt="짧다")])

    assert output["conditions"][0]["excerpt"] is None
    validate_ai_json(ExceptionJudgmentOutput, output)


def test_condition_survives_when_its_excerpt_is_dropped() -> None:
    """발췌가 빠져도 조건은 남는다. 각주만 사라진다."""
    output = judge([ai_b_condition(excerpt="짧다")])

    assert len(output["conditions"]) == 1
    assert output["conditions"][0]["result"] == "충족"


# --- 물어볼 항목 ----------------------------------------------------------


def test_unknown_without_a_needed_field_gets_the_ask_notice() -> None:
    """미확인인데 물을 항목이 없으면 후속 질문을 만들 수 없어 대화가 멈춘다."""
    output = judge([ai_b_condition(result="unknown", needed_field=None)])

    assert output["conditions"][0]["needed_field"] == ASK_NOTICE


def test_english_needed_field_passes_through() -> None:
    """서버가 AskableProfileField 로 받는다. AI B 와 영문 키가 일치한다."""
    output = judge(
        [ai_b_condition(result="unknown", needed_field="employment_insurance")]
    )

    assert output["conditions"][0]["needed_field"] == "employment_insurance"
    validate_ai_json(ExceptionJudgmentOutput, output)


# --- 호출 모양 ------------------------------------------------------------


def test_policy_is_reassembled_from_the_request_fields() -> None:
    fake = FakeAiBJudge([ai_b_condition()])
    adapter = AiBExceptionJudgeAdapter(fake)

    asyncio.run(adapter.judge(request()))

    policy, profile = fake.calls[0]
    assert policy["id"] == "SEOUL-005"
    assert policy["exceptions_text"].startswith("채무조정")
    assert VERIFIED_EXCERPT in policy["raw_text"]
    # 프로필은 영문 키 그대로 넘긴다. AI B 가 같은 키를 읽는다.
    assert profile["status"] == "job_seeking"


def test_non_dict_conditions_are_ignored() -> None:
    output = judge([ai_b_condition(), "이상한 값", None])

    assert len(output["conditions"]) == 1


def test_empty_judgment_is_valid() -> None:
    output = judge([])

    validated = validate_ai_json(ExceptionJudgmentOutput, output)
    assert validated.conditions == []
