"""판정 오케스트레이션 테스트 (`ai/judgment/judge.py`).

**실제 API 를 부르지 않는다.** `ai/conversation/llm.py` 의 `Gateway` 에 가짜 어댑터를
물려 재시도·예산·실패 흡수를 본다.

재시도와 타임아웃 규칙 자체는 `Gateway` 가 가지고 있고 AI A 쪽 테스트가 검증한다.
여기서는 **우리가 그 규칙을 제대로 쓰고 있는지**와 실패를 자리표시로 바꾸는지를 본다.

여기서 막는 것
| 시나리오 | 왜 |
| --- | --- |
| 판정 실패에 빈 목록을 돌려줌 | 조건 0개면 판정 상태가 likely 로 가서 실패가 유리하게 작용한다 |
| 예외가 새어 나감 | 규칙 엔진이 이미 띄운 카드까지 사라진다 |
| 예산을 따로 계산 | 세 호출 지점이 합쳐서 20초를 넘긴다 |
| 검증 안 된 발췌가 화면으로 | 원문에 없는 근거가 나간다 |
| 조건을 못 뽑았는데 성공으로 처리 | 같은 이유로 likely 가 된다 |
"""

import json
import threading
import time
import unittest

from ai.conversation.llm import (
    BudgetTracker,
    CallSite,
    Gateway,
    ModelCapacityError,
    ModelFormatError,
    ModelRateLimitError,
    ModelTimeoutError,
    StructuredResponse,
    policy_for,
)
from ai.judgment.citation import raw_text_covers
from ai.judgment.judge import (
    JUDGE_FALLBACK,
    REASON_NO_CONDITIONS,
    ExceptionJudge,
    JudgeStats,
)
from ai.judgment.scoring import evaluate
from ai.judgment.values import ASK_NOTICE, UNKNOWN, UNMET

RAW = (
    "○ 지원 대상\n"
    "  - 서울시에 주민등록이 되어 있는 만 19세 이상 34세 이하 청년\n"
    "○ 제외 대상\n"
    "  - 휴학생은 지원 대상에서 제외합니다\n"
    "  - 타 청년 지원금을 수혜 중인 자는 중복 신청할 수 없습니다\n"
)
EXCEPTIONS = "휴학생은 지원 대상에서 제외합니다"
PROFILE = {"age": 23, "region": "seoul", "status": "on_leave"}

GOOD_DATA = {
    "conditions": [
        {
            "name": "휴학생 제외",
            "result": UNMET,
            "excerpt": EXCEPTIONS,
            "needed_field": "",
        }
    ]
}

POLICY = {"id": "SEOUL-001", "exceptions_text": EXCEPTIONS, "raw_text": RAW}


class FakeAdapter:
    """대본대로 응답하거나 실패하는 어댑터.

    `LlmAdapter` 프로토콜에서 우리가 쓰는 것은 `complete_structured` 하나다.
    """

    def __init__(self, script, delay=0.0):
        self.script = list(script)
        self.requests = []
        self.delay = delay
        self.lock = threading.Lock()
        self.concurrent = 0
        self.max_concurrent = 0

    def complete_structured(self, request):
        with self.lock:
            self.requests.append(request)
            self.concurrent += 1
            self.max_concurrent = max(self.max_concurrent, self.concurrent)
        try:
            if self.delay:
                time.sleep(self.delay)
            if not self.script:
                raise AssertionError("대본보다 많이 불렀다")
            item = self.script.pop(0)
            if isinstance(item, BaseException):
                raise item
            if isinstance(item, StructuredResponse):
                return item
            return StructuredResponse(data=item)
        finally:
            with self.lock:
                self.concurrent -= 1

    def stream_text(self, request):  # 판정은 스트리밍을 쓰지 않는다
        raise AssertionError("판정은 stream_text 를 쓰지 않는다")


def judge_with(script, *, delay=0.0, budget=None, max_workers=None):
    adapter = FakeAdapter(script, delay=delay)
    gateway = Gateway(adapter=adapter, budget=budget)
    return ExceptionJudge(gateway, max_workers=max_workers), adapter, gateway


class TestUsesSharedGateway(unittest.TestCase):
    """모델 호출을 우리 쪽에 따로 두지 않는다."""

    def test_판정_호출_지점_정책을_그대로_쓴다(self):
        """예산·재시도 숫자를 우리가 다시 정하지 않는다."""
        spec = policy_for(CallSite.JUDGE)
        self.assertEqual(spec.timeout_s, 8.0)
        self.assertEqual(spec.timeout_scope, "정책당")
        self.assertEqual(spec.max_retries, 1)
        self.assertTrue(spec.should_retry(ModelFormatError()))
        self.assertFalse(spec.should_retry(ModelTimeoutError()))
        self.assertFalse(spec.should_retry(ModelRateLimitError()))
        self.assertFalse(spec.should_retry(ModelCapacityError()))

    def test_judgment_폴더에_모델_호출부가_없다(self):
        """`client.py` 를 없애고 공용 계층으로 옮겼다."""
        import pathlib

        folder = pathlib.Path("ai/judgment")
        self.assertFalse((folder / "client.py").exists())
        for path in folder.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            with self.subTest(file=path.name):
                self.assertNotIn("import anthropic", text)
                self.assertNotIn("CLAUDE_API_KEY", text)

    def test_판정_지점으로_부른다(self):
        judge, adapter, gateway = judge_with([GOOD_DATA])
        judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
        self.assertEqual(gateway.metrics["calls"], 1)

    def test_스키마와_시스템_지시문을_넘긴다(self):
        judge, adapter, _ = judge_with([GOOD_DATA])
        judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
        request = adapter.requests[0]
        self.assertIn("conditions", request.schema.get("properties", {}))
        self.assertIn("예외 조건", request.system)
        self.assertIn(RAW, request.user, "원문이 그대로 들어가야 발췌를 대조할 수 있다")

    def test_판정_온도는_낮다(self):
        judge, adapter, _ = judge_with([GOOD_DATA])
        judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
        self.assertEqual(adapter.requests[0].temperature, 0.0)


class TestNormalPath(unittest.TestCase):
    def test_정상_응답을_조건으로_돌려준다(self):
        judge, _, _ = judge_with([GOOD_DATA])
        conditions = judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
        self.assertEqual(len(conditions), 1)
        self.assertEqual(conditions[0]["result"], UNMET)
        self.assertEqual(conditions[0]["judged_by"], "ai")
        self.assertEqual(judge.stats.judged, 1)
        self.assertEqual(judge.stats.placeholders, 0)

    def test_어댑터가_문자열로_줘도_받는다(self):
        """스키마 강제를 못 켠 어댑터가 본문을 그대로 주는 경우."""
        judge, _, _ = judge_with([json.dumps(GOOD_DATA, ensure_ascii=False)])
        conditions = judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
        self.assertEqual(conditions[0]["result"], UNMET)


class TestSkip(unittest.TestCase):
    """`exceptions_text` 가 비면 판정을 생략한다 (README 9장)."""

    def test_빈_예외_문장이면_부르지_않는다(self):
        for value in ("", "   ", None):
            with self.subTest(value=value):
                judge, adapter, gateway = judge_with([])
                self.assertEqual(judge.judge_policy(value, PROFILE, RAW), [])
                self.assertEqual(adapter.requests, [])
                self.assertEqual(gateway.metrics["calls"], 0)
                self.assertEqual(judge.stats.skipped, 1)

    def test_생략은_실패가_아니다(self):
        judge, _, _ = judge_with([])
        judge.judge_policy("", PROFILE, RAW)
        self.assertEqual(judge.stats.placeholders, 0)


class TestFailureIsNotEmpty(unittest.TestCase):
    """실패는 빈 목록이 아니라 unknown 자리표시다."""

    def test_시간_초과면_자리표시를_돌려준다(self):
        judge, _, _ = judge_with([ModelTimeoutError()])
        conditions = judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
        self.assertEqual(len(conditions), 1, "빈 목록이면 likely 로 갈 수 있다")
        self.assertEqual(conditions[0]["result"], UNKNOWN)
        self.assertTrue(conditions[0]["placeholder"])
        self.assertEqual(conditions[0]["needed_field"], ASK_NOTICE)
        self.assertEqual(conditions[0]["placeholder_reason"], ModelTimeoutError.reason)

    def test_실패_종류가_자리표시에_남는다(self):
        cases = (
            (ModelTimeoutError(), ModelTimeoutError.reason),
            (ModelRateLimitError(), ModelRateLimitError.reason),
            (ModelCapacityError(), ModelCapacityError.reason),
        )
        for error, reason in cases:
            with self.subTest(reason=reason):
                judge, _, _ = judge_with([error])
                conditions = judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
                self.assertEqual(conditions[0]["placeholder_reason"], reason)

    def test_어댑터가_없으면_자리표시다(self):
        """설정이 빠진 상태. 카드는 규칙 엔진 결과로 그대로 서 있다."""
        judge = ExceptionJudge()  # 기본 Gateway, 어댑터 없음
        conditions = judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
        self.assertEqual(conditions[0]["result"], UNKNOWN)
        self.assertEqual(conditions[0]["placeholder_reason"], "no_adapter")

    def test_조건을_못_뽑으면_자리표시다(self):
        """모델이 빈 목록을 냈다. 예외 문장이 있는데 조건 0개면 likely 로 갈 수 있다."""
        judge, _, _ = judge_with([JUDGE_FALLBACK])
        conditions = judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
        self.assertEqual(len(conditions), 1)
        self.assertEqual(conditions[0]["result"], UNKNOWN)
        self.assertEqual(conditions[0]["placeholder_reason"], REASON_NO_CONDITIONS)

    def test_항목이_전부_깨지면_자리표시다(self):
        data = {"conditions": [{"name": "x", "result": "아마도", "excerpt": EXCEPTIONS}]}
        judge, _, _ = judge_with([data])
        conditions = judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
        self.assertTrue(conditions[0]["placeholder"])

    def test_자리표시는_인용_검증을_건너뛴다(self):
        """근거가 없는 게 정상이므로 제거 건수에 세면 지표가 흐려진다."""
        judge, _, _ = judge_with([ModelTimeoutError()])
        result = judge.judge_and_verify(POLICY, PROFILE)
        self.assertEqual(result["removed"], [])
        self.assertEqual(result["conditions"][0]["result"], UNKNOWN)


class TestRetryIsGatewayJob(unittest.TestCase):
    """재시도 규칙은 Gateway 가 가진다. 우리가 다시 구현하지 않는다."""

    def test_형식_오류는_한_번_다시_시도된다(self):
        judge, adapter, gateway = judge_with([ModelFormatError(), GOOD_DATA])
        conditions = judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
        self.assertEqual(conditions[0]["result"], UNMET)
        self.assertEqual(len(adapter.requests), 2)
        self.assertEqual(gateway.metrics["retries"], 1)

    def test_시간_초과는_재시도되지_않는다(self):
        judge, adapter, _ = judge_with([ModelTimeoutError(), GOOD_DATA])
        conditions = judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
        self.assertTrue(conditions[0]["placeholder"])
        self.assertEqual(len(adapter.requests), 1, "재시도하면 또 그만큼 걸린다")

    def test_속도_제한과_용량_부족도_재시도되지_않는다(self):
        for error in (ModelRateLimitError(), ModelCapacityError()):
            with self.subTest(error=type(error).__name__):
                judge, adapter, _ = judge_with([error, GOOD_DATA])
                judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
                self.assertEqual(len(adapter.requests), 1)


class TestSharedBudget(unittest.TestCase):
    """세 호출 지점이 예산을 나눠 쓴다. 우리가 따로 계산하지 않는다."""

    def test_예산이_없으면_부르지_않는다(self):
        """해석과 답변이 이미 예산을 다 썼다면 판정은 건너뛴다.

        시계에 상수를 더하면 경과 시간이 0이다. 실제로 흐르는 시계를 써야 한다.
        """
        ticks = iter([0.0] + [100.0] * 50)
        spent = BudgetTracker(total_s=20.0, clock=lambda: next(ticks, 100.0))
        judge, adapter, _ = judge_with([GOOD_DATA], budget=spent)
        self.assertEqual(spent.remaining_s(), 0.0, "전제 확인: 예산이 바닥난 상태")
        conditions = judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
        self.assertEqual(adapter.requests, [], "예산이 없으면 호출 자체를 하지 않는다")
        self.assertTrue(conditions[0]["placeholder"])
        self.assertEqual(conditions[0]["placeholder_reason"], "budget_exhausted")

    def test_남은_예산보다_긴_타임아웃을_주지_않는다(self):
        """정책상 8초여도 남은 예산이 짧으면 그만큼만 쓴다."""
        budget = BudgetTracker(total_s=5.0, reserved_s=1.5)
        judge, adapter, _ = judge_with([GOOD_DATA], budget=budget)
        judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
        self.assertLessEqual(adapter.requests[0].timeout_s, 3.5)

    def test_예산을_공유한다(self):
        """같은 Gateway 를 쓰면 판정이 쓴 시간이 예산에 반영된다."""
        budget = BudgetTracker(total_s=20.0)
        judge, _, gateway = judge_with([GOOD_DATA], budget=budget)
        self.assertIs(gateway.budget, budget)
        before = budget.remaining_s()
        judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
        self.assertLessEqual(budget.remaining_s(), before)


class TestNeverRaises(unittest.TestCase):
    """예외가 새어 나가면 규칙 엔진 카드까지 사라진다."""

    def test_어댑터가_엉뚱한_예외를_던져도_흡수한다(self):
        judge, _, _ = judge_with([ValueError("예상 못 한 오류")])
        conditions = judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
        self.assertTrue(conditions[0]["placeholder"])

    def test_어댑터가_이상한_모양을_돌려줘도_흡수한다(self):
        for data in ({"message": "ok"}, None, 3, "판정할 수 없습니다"):
            with self.subTest(data=data):
                judge, _, _ = judge_with([StructuredResponse(data=data)])
                conditions = judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
                self.assertEqual(len(conditions), 1)
                self.assertTrue(conditions[0]["placeholder"])

    def test_원문이_없어도_터지지_않는다(self):
        judge, _, _ = judge_with([GOOD_DATA])
        self.assertIsInstance(judge.judge_policy(EXCEPTIONS, PROFILE, None), list)

    def test_프로필이_없어도_터지지_않는다(self):
        judge, _, _ = judge_with([GOOD_DATA])
        self.assertEqual(judge.judge_policy(EXCEPTIONS, None, RAW)[0]["result"], UNMET)


class TestVerifyIntegration(unittest.TestCase):
    """운영 경로는 인용 검증까지 끝낸다."""

    def test_판정과_검증을_한_번에_한다(self):
        judge, _, _ = judge_with([GOOD_DATA])
        result = judge.judge_and_verify(POLICY, PROFILE)
        self.assertEqual(result["policy_id"], "SEOUL-001")
        self.assertEqual(result["conditions"][0]["result"], UNMET)
        self.assertTrue(result["conditions"][0]["excerpt_verified"])
        self.assertEqual(result["removed"], [])

    def test_환각_발췌는_검증에서_걸러진다(self):
        data = {
            "conditions": [
                {
                    "name": "소득 초과",
                    "result": UNMET,
                    "excerpt": "가구 소득이 기준 중위소득 150%를 초과하면 제외합니다",
                    "needed_field": "",
                }
            ]
        }
        judge, _, _ = judge_with([data])
        result = judge.judge_and_verify(POLICY, PROFILE)
        self.assertEqual(result["conditions"][0]["result"], UNKNOWN)
        self.assertIsNone(result["conditions"][0]["excerpt"])
        self.assertEqual(result["removed"][0]["reason"], "not_found")

    def test_통과한_발췌는_원문_구간으로_바뀐다(self):
        data = {
            "conditions": [
                {
                    "name": "휴학생 제외",
                    "result": UNMET,
                    "excerpt": "휴학생은   지원\n대상에서 제외합니다",
                    "needed_field": "",
                }
            ]
        }
        judge, _, _ = judge_with([data])
        result = judge.judge_and_verify(POLICY, PROFILE)
        excerpt = result["conditions"][0]["excerpt"]
        self.assertEqual(excerpt, EXCEPTIONS)
        self.assertTrue(raw_text_covers(excerpt, RAW))


class TestConcurrency(unittest.TestCase):
    def test_여러_정책을_순서대로_돌려준다(self):
        policies = [dict(POLICY, id=f"SEOUL-00{i}") for i in range(1, 6)]
        judge, _, _ = judge_with([GOOD_DATA] * 5)
        results = judge.judge_policies(policies, PROFILE)
        self.assertEqual(
            [r["policy_id"] for r in results],
            ["SEOUL-001", "SEOUL-002", "SEOUL-003", "SEOUL-004", "SEOUL-005"],
        )

    def test_동시_실행이_상한을_넘지_않는다(self):
        policies = [dict(POLICY, id=f"P{i}") for i in range(6)]
        judge, adapter, _ = judge_with([GOOD_DATA] * 6, delay=0.02, max_workers=3)
        judge.judge_policies(policies, PROFILE)
        self.assertLessEqual(adapter.max_concurrent, 3)

    def test_하나가_실패해도_나머지는_돌아온다(self):
        policies = [dict(POLICY, id="A"), dict(POLICY, id="B")]
        judge, _, _ = judge_with([ModelTimeoutError(), GOOD_DATA], max_workers=1)
        results = judge.judge_policies(policies, PROFILE)
        self.assertEqual(len(results), 2)
        reasons = {r["policy_id"]: r["conditions"][0].get("placeholder") for r in results}
        self.assertEqual(set(reasons), {"A", "B"})
        self.assertEqual(sum(1 for v in reasons.values() if v), 1)

    def test_같은_정책_번호가_두_번_와도_결과를_잃지_않는다(self):
        policies = [dict(POLICY), dict(POLICY)]
        judge, _, _ = judge_with([GOOD_DATA] * 2)
        results = judge.judge_policies(policies, PROFILE)
        self.assertEqual(len(results), 2)

    def test_빈_목록이면_부르지_않는다(self):
        judge, adapter, _ = judge_with([])
        self.assertEqual(judge.judge_policies([], PROFILE), [])
        self.assertEqual(adapter.requests, [])


class TestScoringHandoff(unittest.TestCase):
    """평가 하네스에 그대로 넣을 수 있어야 한다."""

    def test_judge_policy를_evaluate에_넣을_수_있다(self):
        """J1~J8 회귀 검사를 프롬프트 고칠 때마다 돌린다."""

        class OracleAdapter:
            """케이스 원문에서 예외 문장을 그대로 인용하는 가짜 모델."""

            def complete_structured(self, request):
                start = request.user.index("<exception_condition>\n") + len(
                    "<exception_condition>\n"
                )
                end = request.user.index("\n</exception_condition>")
                excerpt = request.user[start:end]
                return StructuredResponse(
                    data={
                        "conditions": [
                            {
                                "name": "조건",
                                "result": UNKNOWN,
                                "excerpt": excerpt,
                                "needed_field": ASK_NOTICE,
                            }
                        ]
                    }
                )

            def stream_text(self, request):
                raise AssertionError("쓰지 않는다")

        judge = ExceptionJudge(Gateway(adapter=OracleAdapter()))
        report = evaluate(judge.judge_policy)
        self.assertEqual(report.total, 8)
        self.assertEqual(
            report.false_unmet, 0, "모두 unknown 이므로 unmet 오판이 없어야 한다"
        )
        self.assertEqual(
            report.removed_excerpt_count,
            0,
            "원문에서 인용했으므로 인용 검증을 통과해야 한다",
        )


class TestStats(unittest.TestCase):
    """Gateway 가 세는 것을 다시 세지 않는다."""

    def test_자체로_세는_것은_생략_판정_자리표시다(self):
        judge, _, _ = judge_with([GOOD_DATA, ModelTimeoutError()])
        judge.judge_policy("", PROFILE, RAW)
        judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
        judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
        self.assertEqual(judge.stats.skipped, 1)
        self.assertEqual(judge.stats.judged, 1)
        self.assertEqual(judge.stats.placeholders, 1)

    def test_용량_부족을_gateway에서_읽는다(self):
        judge, _, _ = judge_with([ModelCapacityError()])
        judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
        stats = judge.stats_snapshot()
        self.assertEqual(stats.overloaded, 1)
        self.assertIn("데모 모드", stats.summary())

    def test_gateway_지표를_함께_낸다(self):
        judge, _, _ = judge_with([GOOD_DATA])
        judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
        stats = judge.stats_snapshot()
        self.assertIsNotNone(stats.gateway_metrics)
        self.assertEqual(stats.gateway_metrics["calls"], 1)
        self.assertIn("모델 호출", stats.summary())

    def test_파싱에서_버린_항목을_센다(self):
        data = {
            "conditions": [
                {"name": "깨짐", "result": "아마도", "excerpt": EXCEPTIONS},
                {"name": "휴학생 제외", "result": UNMET, "excerpt": EXCEPTIONS},
            ]
        }
        judge, _, _ = judge_with([data])
        judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
        self.assertEqual(judge.stats.parse_dropped, 1)

    def test_지표_모으기에_그대로_넘길_수_있다(self):
        """`metrics.collect(judge_stats=...)` 가 overloaded 를 읽는다."""
        from ai.judgment.metrics import collect

        judge, _, _ = judge_with([ModelCapacityError()])
        judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
        report = collect(judge_stats=judge.stats_snapshot())
        self.assertTrue(any("데모 모드" in n for n in report.notes), report.notes)

    def test_빈_기록의_기본값(self):
        stats = JudgeStats()
        self.assertEqual(stats.overloaded, 0)
        self.assertIsInstance(stats.summary(), str)


if __name__ == "__main__":
    unittest.main()
