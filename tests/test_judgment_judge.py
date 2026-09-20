"""판정 오케스트레이션 테스트 (`ai/judgment/judge.py`).

**실제 API 를 부르지 않는다.** 가짜 클라이언트로 재시도·시간 예산·실패 흡수를 본다.

여기서 막는 것
| 시나리오 | 왜 |
| --- | --- |
| 판정 실패에 빈 목록을 돌려줌 | 조건 0개면 판정 상태가 likely 로 가서 실패가 유리하게 작용한다 |
| 시간 초과에 재시도 | 전체 20초 상한이 깨진다 |
| 속도 제한·용량 부족에 재시도 | 풀리지 않는다. 시간만 쓴다 |
| 예외가 새어 나감 | 규칙 엔진 카드까지 사라진다 |
| 동시 요청이 상한을 넘김 | 분당 요청 수 한도에 걸린다 |
| 검증 안 된 발췌가 화면으로 | 원문에 없는 근거가 나간다 |
"""

import threading
import time
import unittest

from ai.judgment.citation import raw_text_covers
from ai.judgment.judge import (
    DEFAULT_BATCH_BUDGET,
    DEFAULT_CALL_TIMEOUT,
    DEFAULT_MAX_WORKERS,
    DEFAULT_POLICY_BUDGET,
    REASON_NO_CONDITIONS,
    ExceptionJudge,
)
from ai.judgment.client import LLMError
from ai.judgment.scoring import evaluate
from ai.judgment.values import ASK_NOTICE, MET, UNKNOWN, UNMET

RAW = (
    "○ 지원 대상\n"
    "  - 서울시에 주민등록이 되어 있는 만 19세 이상 34세 이하 청년\n"
    "○ 제외 대상\n"
    "  - 휴학생은 지원 대상에서 제외합니다\n"
    "  - 타 청년 지원금을 수혜 중인 자는 중복 신청할 수 없습니다\n"
)
EXCEPTIONS = "휴학생은 지원 대상에서 제외합니다"
PROFILE = {"age": 23, "region": "seoul", "status": "on_leave"}

GOOD_BODY = (
    '{"conditions": [{"name": "휴학생 제외", "result": "unmet", '
    '"excerpt": "휴학생은 지원 대상에서 제외합니다", "needed_field": ""}]}'
)

POLICY = {
    "id": "SEOUL-001",
    "exceptions_text": EXCEPTIONS,
    "raw_text": RAW,
}


class FakeClient:
    """정해진 순서대로 응답하거나 실패하는 클라이언트."""

    def __init__(self, script):
        #: script 는 응답 문자열 또는 던질 예외의 목록
        self.script = list(script)
        self.calls = []
        self.timeouts = []
        self.lock = threading.Lock()
        self.concurrent = 0
        self.max_concurrent = 0
        self.delay = 0.0

    def complete(self, *, system, user, timeout, schema=None):
        with self.lock:
            self.calls.append({"system": system, "user": user, "schema": schema})
            self.timeouts.append(timeout)
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
            return item
        finally:
            with self.lock:
                self.concurrent -= 1


def judge_with(script, **kwargs):
    """가짜 클라이언트를 물린 판정기.

    배치 예산 기본값은 정책당 예산과 같아 단일 정책 테스트에 영향이 없다.
    """
    client = FakeClient(script)
    return ExceptionJudge(client, **kwargs), client


class TestDefaults(unittest.TestCase):
    def test_시간_예산이_문서와_같다(self):
        """정책당 8초, 호출 6초. 남는 2초가 재시도 여유다."""
        self.assertEqual(DEFAULT_POLICY_BUDGET, 8.0)
        self.assertEqual(DEFAULT_CALL_TIMEOUT, 6.0)
        self.assertLess(DEFAULT_CALL_TIMEOUT, DEFAULT_POLICY_BUDGET)

    def test_동시_실행_상한이_있다(self):
        """후보 수만큼 무조건 늘리면 분당 요청 수 한도에 걸린다."""
        self.assertEqual(DEFAULT_MAX_WORKERS, 3)


class TestNormalPath(unittest.TestCase):
    def test_정상_응답을_조건으로_돌려준다(self):
        judge, client = judge_with([GOOD_BODY])
        conditions = judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
        self.assertEqual(len(conditions), 1)
        self.assertEqual(conditions[0]["result"], UNMET)
        self.assertEqual(conditions[0]["judged_by"], "ai")
        self.assertEqual(judge.stats.successes, 1)
        self.assertEqual(judge.stats.calls, 1)
        self.assertEqual(judge.stats.retries, 0)

    def test_시스템_지시문과_스키마를_넘긴다(self):
        judge, client = judge_with([GOOD_BODY])
        judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
        sent = client.calls[0]
        self.assertIn("예외 조건", sent["system"])
        self.assertIsNotNone(sent["schema"])
        self.assertIn(RAW, sent["user"], "원문이 그대로 들어가야 발췌를 대조할 수 있다")

    def test_첫_호출_제한은_호출_예산이다(self):
        judge, client = judge_with([GOOD_BODY])
        judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
        self.assertEqual(client.timeouts[0], DEFAULT_CALL_TIMEOUT)


class TestSkip(unittest.TestCase):
    """`exceptions_text` 가 비면 판정을 생략한다 (README 9장)."""

    def test_빈_예외_문장이면_부르지_않는다(self):
        for value in ("", "   ", None):
            with self.subTest(value=value):
                judge, client = judge_with([])
                self.assertEqual(judge.judge_policy(value, PROFILE, RAW), [])
                self.assertEqual(client.calls, [])
                self.assertEqual(judge.stats.skipped, 1)
                self.assertEqual(judge.stats.calls, 0)

    def test_생략은_실패가_아니다(self):
        """예외 조건이 없는 정책이므로 조건 0개가 맞다."""
        judge, _ = judge_with([])
        judge.judge_policy("", PROFILE, RAW)
        self.assertEqual(judge.stats.failures, 0)


class TestFailureIsNotEmpty(unittest.TestCase):
    """실패는 빈 목록이 아니라 unknown 자리표시다."""

    def test_실패하면_자리표시_조건을_돌려준다(self):
        judge, _ = judge_with([LLMError("timeout")])
        conditions = judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
        self.assertEqual(len(conditions), 1, "빈 목록이면 likely 로 갈 수 있다")
        self.assertEqual(conditions[0]["result"], UNKNOWN)
        self.assertTrue(conditions[0]["placeholder"])
        self.assertEqual(conditions[0]["needed_field"], ASK_NOTICE)

    def test_자리표시에_실패_사유가_남는다(self):
        judge, _ = judge_with([LLMError("rate_limit")])
        conditions = judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
        self.assertEqual(conditions[0]["placeholder_reason"], "rate_limit")

    def test_빈_조건_목록은_성공으로_보지_않는다(self):
        """예외 문장이 있는데 조건 0개면 모든 조건 met 으로 계산되어 likely 로 갈 수 있다."""
        for body in ('{"conditions": []}', "[]"):
            with self.subTest(body=body):
                judge, _ = judge_with([body])
                conditions = judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
                self.assertEqual(len(conditions), 1)
                self.assertEqual(conditions[0]["result"], UNKNOWN)
                self.assertTrue(conditions[0]["placeholder"])
                self.assertEqual(conditions[0]["placeholder_reason"], REASON_NO_CONDITIONS)
                self.assertEqual(judge.stats.successes, 0)

    def test_빈_예외_문장_생략과_빈_조건_판정_누락을_구분한다(self):
        judge, _ = judge_with(['{"conditions": []}'])
        self.assertEqual(judge.judge_policy("", PROFILE, RAW), [])
        self.assertEqual(judge.judge_policy(EXCEPTIONS, PROFILE, RAW)[0]["result"], UNKNOWN)
        self.assertEqual(judge.stats.skipped, 1)
        self.assertEqual(judge.stats.failures, 1)

    def test_자리표시는_인용_검증을_건너뛴다(self):
        """근거가 없는 게 정상이므로 제거 건수에 세면 지표가 흐려진다."""
        judge, _ = judge_with([LLMError("timeout")])
        result = judge.judge_and_verify(POLICY, PROFILE)
        self.assertEqual(result["removed"], [])
        self.assertEqual(result["conditions"][0]["result"], UNKNOWN)


class TestRetry(unittest.TestCase):
    """재시도는 형식 오류와 서버 오류만, 1회."""

    def test_형식이_깨지면_한_번_다시_시도한다(self):
        judge, client = judge_with(["이건 JSON 이 아닙니다", GOOD_BODY])
        conditions = judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
        self.assertEqual(len(conditions), 1)
        self.assertEqual(conditions[0]["result"], UNMET)
        self.assertEqual(judge.stats.calls, 2)
        self.assertEqual(judge.stats.retries, 1)

    def test_서버_오류는_재시도하지_않는다(self):
        """문서 합의는 "형식 오류만 1회"다. 서버 오류까지 넓히면 20초 상한을 압박한다."""
        judge, client = judge_with([LLMError("server"), GOOD_BODY])
        conditions = judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
        self.assertTrue(conditions[0]["placeholder"])
        self.assertEqual(judge.stats.calls, 1)
        self.assertEqual(judge.stats.retries, 0)

    def test_두_번_깨지면_포기한다(self):
        """두 번 이상 재시도하면 20초 상한을 넘긴다."""
        judge, client = judge_with(["깨짐", "또 깨짐"])
        conditions = judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
        self.assertTrue(conditions[0]["placeholder"])
        self.assertEqual(judge.stats.calls, 2)
        self.assertEqual(len(client.script), 0)

    def test_시간_초과는_재시도하지_않는다(self):
        judge, client = judge_with([LLMError("timeout"), GOOD_BODY])
        conditions = judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
        self.assertTrue(conditions[0]["placeholder"])
        self.assertEqual(judge.stats.calls, 1, "재시도하면 또 그만큼 걸린다")
        self.assertEqual(judge.stats.retries, 0)

    def test_속도_제한은_재시도하지_않는다(self):
        judge, client = judge_with([LLMError("rate_limit"), GOOD_BODY])
        judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
        self.assertEqual(judge.stats.calls, 1, "대기 시간이 우리 예산보다 길다")

    def test_용량_부족은_재시도하지_않고_데모_모드_신호를_남긴다(self):
        judge, client = judge_with([LLMError("overloaded"), GOOD_BODY])
        judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
        self.assertEqual(judge.stats.calls, 1)
        self.assertEqual(judge.stats.overloaded, 1)
        self.assertIn("데모 모드", judge.stats.summary())

    def test_인증_오류는_재시도하지_않는다(self):
        judge, client = judge_with([LLMError("auth"), GOOD_BODY])
        judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
        self.assertEqual(judge.stats.calls, 1)

    def test_남은_예산이_없으면_재시도하지_않는다(self):
        """재시도할 수 있는 실패라도 시간이 없으면 넘어간다."""
        judge, client = judge_with(
            [LLMError("server"), GOOD_BODY], policy_budget=6.0, call_timeout=6.0
        )
        judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
        self.assertEqual(judge.stats.calls, 1)

    def test_재시도_제한은_남은_예산을_넘지_않는다(self):
        judge, client = judge_with(
            ["깨짐", GOOD_BODY], policy_budget=8.0, call_timeout=6.0
        )
        judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
        self.assertEqual(client.timeouts, [6.0, 2.0])


class TestNeverRaises(unittest.TestCase):
    """예외가 새어 나가면 규칙 엔진 카드까지 사라진다."""

    def test_클라이언트가_LLMError가_아닌_것을_던져도_흡수한다(self):
        judge, _ = judge_with([ValueError("예상 못 한 오류")])
        conditions = judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
        self.assertTrue(conditions[0]["placeholder"])
        self.assertEqual(judge.stats.failures, 1)

    def test_클라이언트가_이상한_것을_돌려줘도_흡수한다(self):
        judge, _ = judge_with([None])
        conditions = judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
        self.assertTrue(conditions[0]["placeholder"])

    def test_원문이_없어도_터지지_않는다(self):
        judge, _ = judge_with([GOOD_BODY])
        conditions = judge.judge_policy(EXCEPTIONS, PROFILE, None)
        self.assertIsInstance(conditions, list)

    def test_프로필이_없어도_터지지_않는다(self):
        judge, _ = judge_with([GOOD_BODY])
        conditions = judge.judge_policy(EXCEPTIONS, None, RAW)
        self.assertEqual(conditions[0]["result"], UNMET)


class TestVerifyIntegration(unittest.TestCase):
    """운영 경로는 인용 검증까지 끝낸다."""

    def test_판정과_검증을_한_번에_한다(self):
        judge, _ = judge_with([GOOD_BODY])
        result = judge.judge_and_verify(POLICY, PROFILE)
        self.assertEqual(result["policy_id"], "SEOUL-001")
        self.assertEqual(result["conditions"][0]["result"], UNMET)
        self.assertTrue(result["conditions"][0]["excerpt_verified"])
        self.assertEqual(result["removed"], [])

    def test_환각_발췌는_검증에서_걸러진다(self):
        """원문에 없는 근거가 화면으로 나가지 않는다."""
        body = (
            '{"conditions": [{"name": "소득 초과", "result": "unmet", '
            '"excerpt": "가구 소득이 기준 중위소득 150%를 초과하면 제외합니다", '
            '"needed_field": ""}]}'
        )
        judge, _ = judge_with([body])
        result = judge.judge_and_verify(POLICY, PROFILE)
        self.assertEqual(result["conditions"][0]["result"], UNKNOWN)
        self.assertIsNone(result["conditions"][0]["excerpt"])
        self.assertEqual(len(result["removed"]), 1)
        self.assertEqual(result["removed"][0]["reason"], "not_found")

    def test_통과한_발췌는_원문_구간으로_바뀐다(self):
        body = (
            '{"conditions": [{"name": "휴학생 제외", "result": "unmet", '
            '"excerpt": "휴학생은   지원\\n대상에서 제외합니다", "needed_field": ""}]}'
        )
        judge, _ = judge_with([body])
        result = judge.judge_and_verify(POLICY, PROFILE)
        excerpt = result["conditions"][0]["excerpt"]
        self.assertEqual(excerpt, EXCEPTIONS)
        self.assertTrue(raw_text_covers(excerpt, RAW))


class TestConcurrency(unittest.TestCase):
    def test_여러_정책을_순서대로_돌려준다(self):
        policies = [dict(POLICY, id=f"SEOUL-00{i}") for i in range(1, 6)]
        judge, _ = judge_with([GOOD_BODY] * 5)
        results = judge.judge_policies(policies, PROFILE)
        self.assertEqual(
            [r["policy_id"] for r in results],
            ["SEOUL-001", "SEOUL-002", "SEOUL-003", "SEOUL-004", "SEOUL-005"],
        )

    def test_동시_실행이_상한을_넘지_않는다(self):
        policies = [dict(POLICY, id=f"P{i}") for i in range(6)]
        judge, client = judge_with([GOOD_BODY] * 6, max_workers=3)
        client.delay = 0.02
        judge.judge_policies(policies, PROFILE)
        self.assertLessEqual(client.max_concurrent, 3)

    def test_하나가_실패해도_나머지는_돌아온다(self):
        policies = [dict(POLICY, id="A"), dict(POLICY, id="B")]
        judge, _ = judge_with([LLMError("timeout"), GOOD_BODY], max_workers=1)
        results = judge.judge_policies(policies, PROFILE)
        self.assertEqual(len(results), 2)
        self.assertTrue(results[0]["conditions"][0]["placeholder"])
        self.assertEqual(results[1]["conditions"][0]["result"], UNMET)

    def test_빈_목록이면_부르지_않는다(self):
        judge, client = judge_with([])
        self.assertEqual(judge.judge_policies([], PROFILE), [])
        self.assertEqual(client.calls, [])

    def test_배치_예산이_전체_20초를_압박하지_않는다(self):
        """후보 5개를 worker 3개로 돌리면 두 wave 가 된다. 배치 상한이 없으면 16초가 된다."""
        self.assertEqual(DEFAULT_BATCH_BUDGET, 8.0)
        self.assertLessEqual(DEFAULT_BATCH_BUDGET, DEFAULT_POLICY_BUDGET)

    def test_배치_예산이_끝나면_남은_후보는_부르지_않는다(self):
        policies = [dict(POLICY, id=f"P{i}") for i in range(4)]
        judge, client = judge_with([GOOD_BODY] * 4, max_workers=1, batch_budget=0.0)
        results = judge.judge_policies(policies, PROFILE)
        self.assertEqual(len(results), 4)
        self.assertEqual(client.calls, [], "예산이 없으면 호출 자체를 하지 않는다")
        for result in results:
            self.assertTrue(result["conditions"][0]["placeholder"])

    def test_정책별_타임아웃이_남은_배치_예산을_넘지_않는다(self):
        judge, client = judge_with([GOOD_BODY], batch_budget=1.5)
        judge.judge_policies([dict(POLICY)], PROFILE)
        self.assertLessEqual(client.timeouts[0], 1.5)


class TestScoringHandoff(unittest.TestCase):
    """평가 하네스에 그대로 넣을 수 있어야 한다."""

    def test_judge_policy를_evaluate에_넣을_수_있다(self):
        """J1~J8 회귀 검사를 프롬프트 고칠 때마다 돌린다 (연구 문서 8장)."""

        class OracleClient:
            """케이스 원문에서 예외 문장을 그대로 인용하는 가짜 모델."""

            def complete(self, *, system, user, timeout, schema=None):
                start = user.index("<exception_condition>\n") + len(
                    "<exception_condition>\n"
                )
                end = user.index("\n</exception_condition>")
                excerpt = user[start:end]
                return (
                    '{"conditions": [{"name": "조건", "result": "unknown", '
                    '"excerpt": %s, "needed_field": "%s"}]}'
                    % (_json_string(excerpt), ASK_NOTICE)
                )

        judge = ExceptionJudge(OracleClient())
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


def _json_string(text):
    import json

    return json.dumps(text, ensure_ascii=False)


class TestStats(unittest.TestCase):
    def test_요약에_핵심_수치가_들어간다(self):
        judge, _ = judge_with([GOOD_BODY, LLMError("timeout")])
        judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
        judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
        summary = judge.stats.summary()
        self.assertIn("판정 호출", summary)
        self.assertIn("실패 종류", summary)

    def test_파싱에서_버린_항목을_센다(self):
        body = (
            '{"conditions": ['
            '{"name": "깨짐", "result": "아마도", "excerpt": "휴학생은 지원 대상에서 제외합니다"},'
            '{"name": "휴학생 제외", "result": "unmet", "excerpt": "휴학생은 지원 대상에서 제외합니다"}'
            "]}"
        )
        judge, _ = judge_with([body])
        judge.judge_policy(EXCEPTIONS, PROFILE, RAW)
        self.assertEqual(judge.stats.parse_dropped, 1)


if __name__ == "__main__":
    unittest.main()
