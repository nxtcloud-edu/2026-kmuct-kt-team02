"""관련 질문 칩 테스트 (ai/conversation/README.md 7장).

칩은 제안처럼 보이지만 **안내로 읽힌다.** 소득이 문제가 아닌 사람에게 "소득 기준 확인하는
방법" 칩을 띄우면 사용자는 소득이 문제라고 이해하고 엉뚱한 서류를 찾는다. 그래서
"어떤 상황에서 어떤 칩이 나가는가"와 "안 나가야 할 때 안 나가는가"를 같은 무게로 본다.

칩은 P1 이므로 **어떤 입력에도 예외를 던지지 않아야** 한다. 칩 하나 때문에 답변 전체가
실패하면 안 된다(docs/03-api-contract.md 9장).
"""

import inspect
import unittest

from ai.conversation import fields
from ai.conversation.related import (
    CHIP_COMPARE_TOP2,
    CHIP_DEADLINE_ORDER,
    CHIP_DEADLINE_WHEN,
    CHIP_DOCUMENTS,
    CHIP_INCOME_HOWTO,
    CHIP_PRIORITY,
    CHIP_SIMILAR,
    CHIP_UNLIKELY_REASON,
    CHIPS,
    FIXED_CHIP_IDS,
    MAX_CHIPS,
    build,
    build_fixed,
    candidate_ids,
    fixed,
    income_is_the_blocker,
    select,
    to_event,
)


def policy(status="likely", conditions=None, deadline=None):
    """정책 판정 결과 하나 (docs/03-api-contract.md 4장에서 칩 판단에 쓰는 부분만)."""
    return {
        "policy_id": "SEOUL-001",
        "status": status,
        "conditions": [] if conditions is None else conditions,
        "deadline": {} if deadline is None else deadline,
    }


def income_unknown(status="check"):
    """확인이 필요한 이유가 소득인 정책."""
    return policy(
        status=status,
        conditions=[
            {
                "name": "가구 소득 기준",
                "result": "unknown",
                "needed_field": fields.INCOME_BRACKET,
            }
        ],
    )


def needs_field(field, status="check"):
    """확인이 필요한 이유가 소득이 아닌 정책."""
    return policy(
        status=status,
        conditions=[{"name": "조건", "result": "unknown", "needed_field": field}],
    )


def imminent(d_day=3):
    """마감 임박 정책. ``is_imminent`` 는 코드가 계산해 넘긴 값이다."""
    return policy(deadline={"apply_end": "2026-01-10", "d_day": d_day, "is_imminent": True})


def chip_ids(policies, shown=(), allow_compare=True):
    return [chip.id for chip in select(policies, shown=shown, allow_compare=allow_compare)]


class TestSituationToChip(unittest.TestCase):
    """README 7장 표의 네 상황이 각각 맞는 칩을 만드는지."""

    def test_소득_때문에_확인이_필요하면_소득_칩(self):
        self.assertEqual(chip_ids([income_unknown()]), [CHIP_INCOME_HOWTO])

    def test_어려울_수_있는_정책이_있으면_이유_칩(self):
        self.assertEqual(chip_ids([policy(status="unlikely")]), [CHIP_UNLIKELY_REASON])

    def test_카드가_두_개_이상이면_비교_칩(self):
        self.assertEqual(chip_ids([policy(), policy()]), [CHIP_COMPARE_TOP2])

    def test_카드가_하나면_비교_칩이_없다(self):
        """비교할 상대가 없는데 비교 칩을 띄우면 누른 사용자가 답을 못 받는다."""
        self.assertEqual(chip_ids([policy()]), [])

    def test_마감_임박이_있으면_준비_순서_칩(self):
        self.assertEqual(chip_ids([imminent()]), [CHIP_DEADLINE_ORDER])

    def test_상황에_맞는_칩이_없으면_빈_목록(self):
        """빈 목록이 정상 동작이다. 채우려고 아무 칩이나 띄우지 않는다."""
        self.assertEqual(select([policy()]), [])
        self.assertEqual(select([]), [])

    def test_문구는_표에서만_나온다(self):
        """모델이 만든 문구가 섞이면 우리가 답할 수 없는 것을 묻게 된다."""
        for chip in select([income_unknown(), policy(status="unlikely"), imminent()]):
            with self.subTest(chip=chip.id):
                self.assertIs(chip, CHIPS[chip.id], f"{chip.id}: 표의 객체가 아니다")


class TestMaxAndPriority(unittest.TestCase):
    """최대 3개. 네 상황이 다 맞을 때 무엇을 버리는지가 규칙이다."""

    def setUp(self):
        # 네 상황을 한꺼번에 만든다: check(소득) + unlikely + 2개 이상 + 마감 임박
        self.all_four = [income_unknown(), policy(status="unlikely"), imminent()]

    def test_네_상황이_다_맞아도_세_개만_나간다(self):
        chips = select(self.all_four)
        self.assertEqual(len(chips), MAX_CHIPS, f"칩이 {len(chips)}개 나갔다")

    def test_우선순위대로_남고_비교_칩이_빠진다(self):
        """비교는 P1 이고 빼는 순서 1번이다. 먼저 사라질 기능을 가리키는 칩을 남기지 않는다."""
        self.assertEqual(
            chip_ids(self.all_four),
            [CHIP_DEADLINE_ORDER, CHIP_INCOME_HOWTO, CHIP_UNLIKELY_REASON],
        )

    def test_후보_순서가_고정_우선순위를_따른다(self):
        ids = candidate_ids(self.all_four)
        self.assertEqual(ids, [i for i in CHIP_PRIORITY if i in set(ids)])
        self.assertEqual(len(ids), 4, "네 상황이 모두 걸려야 하는 입력이다")

    def test_이벤트로_만들_때도_세_개를_넘지_않는다(self):
        payload = to_event([CHIPS[chip_id] for chip_id in CHIP_PRIORITY])
        self.assertEqual(len(payload["chips"]), MAX_CHIPS)


class TestIncomeChipIsNarrow(unittest.TestCase):
    """check 가 있다는 것만으로 소득 칩을 띄우지 않는다."""

    def test_확인_이유가_소득이_아니면_소득_칩이_없다(self):
        for field in (
            fields.EMPLOYMENT_INSURANCE,
            fields.RESIDENCE_PERIOD,
            fields.LAST_GPA,
            fields.ASK_NOTICE,
        ):
            with self.subTest(needed_field=field):
                self.assertNotIn(
                    CHIP_INCOME_HOWTO,
                    chip_ids([needs_field(field)]),
                    f"{field} 때문에 확인이 필요한데 소득 칩이 나갔다",
                )

    def test_필요_항목이_없으면_소득_칩이_없다(self):
        """원인을 모르면 띄우지 않는다. 틀린 방향을 가리키는 손해가 더 크다."""
        no_field = policy(status="check", conditions=[{"name": "조건", "result": "unknown"}])
        self.assertFalse(income_is_the_blocker([no_field]))
        self.assertEqual(chip_ids([no_field]), [])

    def test_필요_항목이_비어_있어도_소득_칩이_없다(self):
        blank = policy(
            status="check",
            conditions=[{"name": "조건", "result": "unknown", "needed_field": ""}],
        )
        self.assertFalse(income_is_the_blocker([blank]))

    def test_소득_조건이_이미_판정됐으면_소득_칩이_없다(self):
        """미확인이 아니면 확인할 것이 남아 있지 않다."""
        for result in ("met", "unmet"):
            with self.subTest(result=result):
                settled = policy(
                    status="check",
                    conditions=[
                        {
                            "name": "가구 소득 기준",
                            "result": result,
                            "needed_field": fields.INCOME_BRACKET,
                        }
                    ],
                )
                self.assertFalse(income_is_the_blocker([settled]))

    def test_판정_상태가_check가_아니면_소득_칩이_없다(self):
        """likely·unlikely 는 이미 확정된 상태다. 확인을 권할 이유가 없다."""
        for status in ("likely", "unlikely"):
            with self.subTest(status=status):
                self.assertFalse(income_is_the_blocker([income_unknown(status=status)]))

    def test_여러_정책_중_하나라도_소득이면_칩이_나간다(self):
        policies = [needs_field(fields.LAST_GPA), income_unknown()]
        self.assertIn(CHIP_INCOME_HOWTO, chip_ids(policies))


class TestDeadlineUsesGivenFlag(unittest.TestCase):
    """날짜를 다시 계산하지 않는다 (README 2장, docs/01-glossary-profile.md 5장).

    AI 쪽이 D-day 를 계산하기 시작하면 화면 배지와 답변의 남은 일수가 어긋난다.
    """

    def test_is_imminent가_거짓이면_d_day가_작아도_마감_칩이_없다(self):
        for d_day in (0, 1, 3):
            with self.subTest(d_day=d_day):
                given = policy(deadline={"d_day": d_day, "is_imminent": False})
                self.assertEqual(
                    chip_ids([given]),
                    [],
                    f"d_day={d_day} 를 보고 마감 임박을 다시 판단했다",
                )

    def test_is_imminent가_없으면_마감_칩이_없다(self):
        given = policy(deadline={"apply_end": "2026-01-02", "d_day": 1})
        self.assertEqual(chip_ids([given]), [])

    def test_is_imminent가_참이면_d_day가_없어도_마감_칩이_나간다(self):
        given = policy(deadline={"is_imminent": True})
        self.assertEqual(chip_ids([given]), [CHIP_DEADLINE_ORDER])


class TestShown(unittest.TestCase):
    """이미 보여준 칩과 누른 칩은 다시 띄우지 않는다 (README 7장)."""

    def setUp(self):
        self.policies = [income_unknown(), policy(status="unlikely"), imminent()]

    def test_이미_보여준_칩은_빠진다(self):
        ids = chip_ids(self.policies, shown=[CHIP_DEADLINE_ORDER])
        self.assertNotIn(CHIP_DEADLINE_ORDER, ids)
        self.assertEqual(ids[0], CHIP_INCOME_HOWTO, "다음 우선순위가 올라와야 한다")

    def test_빠진_자리에_다음_후보가_들어온다(self):
        """3개 상한 때문에 밀려 있던 비교 칩이 올라온다."""
        ids = chip_ids(self.policies, shown=[CHIP_INCOME_HOWTO])
        self.assertEqual(
            ids, [CHIP_DEADLINE_ORDER, CHIP_UNLIKELY_REASON, CHIP_COMPARE_TOP2]
        )

    def test_전부_보여줬으면_빈_목록(self):
        self.assertEqual(chip_ids(self.policies, shown=CHIP_PRIORITY), [])

    def test_상관없는_번호는_무시된다(self):
        self.assertEqual(
            chip_ids(self.policies, shown=["없는_칩"]),
            chip_ids(self.policies),
        )


class TestAllowCompare(unittest.TestCase):
    """비교 기능(P1)이 빠졌을 때 비교 칩을 끌 수 있어야 한다."""

    def test_끄면_비교_칩이_나가지_않는다(self):
        self.assertEqual(chip_ids([policy(), policy()], allow_compare=False), [])

    def test_끄더라도_다른_칩은_그대로다(self):
        ids = chip_ids([income_unknown(), income_unknown()], allow_compare=False)
        self.assertEqual(ids, [CHIP_INCOME_HOWTO])


class TestFixedFallback(unittest.TestCase):
    """대체 모드 (docs/00-overview.md 빼는 순서 2번)."""

    def test_고정_칩은_세_개다(self):
        self.assertEqual([chip.id for chip in fixed()], list(FIXED_CHIP_IDS))
        self.assertEqual(len(FIXED_CHIP_IDS), MAX_CHIPS)

    def test_정책_목록을_받지_않는다(self):
        """줄이는 것은 문구가 아니라 판단이다. 정책을 받으면 그 요점이 사라진다."""
        parameters = inspect.signature(fixed).parameters
        self.assertEqual(list(parameters), ["shown"])

    def test_상황_판단이_필요한_문구를_쓰지_않는다(self):
        """unlikely 가 없을 때 "조건이 안 맞는 이유" 를 띄우면 없는 사실을 말하는 셈이다."""
        self.assertEqual(
            set(FIXED_CHIP_IDS),
            {CHIP_DOCUMENTS, CHIP_DEADLINE_WHEN, CHIP_SIMILAR},
        )
        self.assertEqual(
            set(FIXED_CHIP_IDS) & set(CHIP_PRIORITY),
            set(),
            "상황을 보는 칩이 대체 모드에 섞였다",
        )

    def test_이미_보여준_것은_적게_내민다(self):
        chips = fixed(shown=[CHIP_DOCUMENTS])
        self.assertEqual([chip.id for chip in chips], [CHIP_DEADLINE_WHEN, CHIP_SIMILAR])

    def test_이벤트_본문도_같은_형태다(self):
        self.assertEqual(
            build_fixed(),
            {"chips": [CHIPS[chip_id].to_event() for chip_id in FIXED_CHIP_IDS]},
        )


class TestEventPayload(unittest.TestCase):
    """``related`` 이벤트에 실을 형태 (docs/03-api-contract.md 5장)."""

    def test_id와_문구_쌍의_목록이다(self):
        payload = build([income_unknown(), imminent()])
        self.assertEqual(set(payload), {"chips"})
        self.assertIsInstance(payload["chips"], list)
        for chip in payload["chips"]:
            with self.subTest(chip=chip):
                self.assertEqual(set(chip), {"id", "text"})
                self.assertTrue(chip["text"].strip(), "화면에 빈 칩이 나간다")
                self.assertEqual(chip["text"], CHIPS[chip["id"]].text)

    def test_고른_칩과_순서가_같다(self):
        policies = [income_unknown(), policy(status="unlikely"), imminent()]
        self.assertEqual(
            [chip["id"] for chip in build(policies)["chips"]],
            chip_ids(policies),
        )

    def test_칩이_없으면_빈_목록을_담는다(self):
        """키를 빼지 않는다. 프론트가 키 없음과 빈 목록을 다르게 다루면 곤란하다."""
        self.assertEqual(build([]), {"chips": []})

    def test_최대_개수를_넘기지_않는다(self):
        policies = [income_unknown(), policy(status="unlikely"), imminent()]
        self.assertLessEqual(len(build(policies)["chips"]), MAX_CHIPS)


class TestBrokenInput(unittest.TestCase):
    """깨진 입력에 예외를 던지지 않는다. 칩 하나 때문에 답변이 실패하면 안 된다."""

    def test_빈_목록(self):
        self.assertEqual(select([]), [])
        self.assertEqual(candidate_ids([]), [])

    def test_conditions가_문자열이면_조건이_없는_것으로_본다(self):
        given = {"status": "check", "conditions": "가구 소득 기준", "deadline": {}}
        self.assertEqual(select([given]), [])

    def test_conditions에_이상한_항목이_섞여_있어도_나머지를_읽는다(self):
        given = {
            "status": "check",
            "conditions": [
                "문자열",
                None,
                {"result": "unknown", "needed_field": fields.INCOME_BRACKET},
            ],
        }
        self.assertEqual(chip_ids([given]), [CHIP_INCOME_HOWTO])

    def test_deadline이_None이거나_문자열(self):
        for deadline in (None, "2026-01-10", []):
            with self.subTest(deadline=deadline):
                given = {"status": "likely", "conditions": [], "deadline": deadline}
                self.assertEqual(select([given]), [])

    def test_키가_전부_빠진_정책(self):
        self.assertEqual(select([{}]), [])
        self.assertEqual(select([{}, {}]), [CHIPS[CHIP_COMPARE_TOP2]])

    def test_상태_값이_알_수_없는_값(self):
        self.assertEqual(select([{"status": "모름"}]), [])

    def test_튜플로_넘겨도_동작한다(self):
        self.assertEqual(chip_ids((imminent(),)), [CHIP_DEADLINE_ORDER])


if __name__ == "__main__":
    unittest.main(verbosity=2)
