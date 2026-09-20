"""답변 검증·정리 테스트 (ai/conversation/README.md 6장, 11장 완료 기준).

이 모듈은 P0 "각주 없는 판정 문장 0건"을 지키는 마지막 관문이다. 그래서 정상 경로보다
**새는 경로**를 촘촘히 본다.

  - 금지 표현 7종과 어미 변형을 잡는가 (완료 기준 "금지 표현 0건")
  - 각주가 필요한 문장에 각주가 없거나, 있는데 목록에 없는 번호인가
    (완료 기준 "각주 없는 판정 문장 0건")
  - 600자 상한 (완료 기준 "답변 600자 이내")
  - 고정 문구 (README 6장 5번)

특히 ``unknown_footnote`` 는 인용 검증 실패로 조건이 unknown 으로 내려가 각주 목록에서
번호가 빠졌을 때 실제로 일어나는 실패다(docs/03-api-contract.md 4-3). 그래서 별도 반에서 본다.

가장 중요한 계약은 ``sanitize`` 결과를 다시 ``validate`` 하면 통과한다는 것이다.
그게 깨지면 정리 단계가 있어도 검증 실패 답변이 그대로 나간다.
"""

import unittest

from ai.conversation.answer import (
    BANNED,
    BROADEN_LINE,
    CHECK,
    CLOSING_LINE,
    DATA_SCOPE_LINE,
    LIKELY,
    MAX_ANSWER_LEN,
    MISSING_FOOTNOTE,
    NO_CLOSING_LINE,
    NO_RESULT_LINE,
    SLOT_FOOTNOTE,
    TOO_LONG,
    UNKNOWN_FOOTNOTE,
    UNLIKELY,
    build_skeleton,
    find_banned,
    footnote_numbers,
    has_footnote,
    known_footnote_ids,
    needs_footnote,
    sanitize,
    split_sentences,
    validate,
)

# 서버가 부여한 각주 목록. 실제 응답 모양(docs/03-api-contract.md 5장)을 줄여서 쓴다.
FOOTNOTES = (
    {"footnote_id": 1, "policy_id": "SEOUL-001", "excerpt": "월 20만원을 지원합니다"},
    {"footnote_id": 2, "policy_id": "SEOUL-002", "excerpt": "휴학생은 제외합니다"},
)

SUMMARY = "신청 가능성이 높은 제도 2개, 확인이 필요한 제도 1개를 찾았어요."
CLOSING = CLOSING_LINE + "."


def codes(text, footnotes=FOOTNOTES):
    """``validate`` 가 낸 문제 코드 목록. 반복되는 두 줄을 줄인다."""
    return validate(text, footnotes).codes()


def details_for(text, code, footnotes=FOOTNOTES):
    """특정 코드로 걸린 문제들의 detail 목록."""
    return [p.detail for p in validate(text, footnotes).problems if p.code == code]


def policy(**overrides):
    """정책 판정 결과 하나 (docs/03-api-contract.md 4장). 필요한 키만 덮어쓴다."""
    base = {"policy_id": "SEOUL-001", "title": "청년월세지원", "status": LIKELY}
    base.update(overrides)
    return base


class TestBannedPhrases(unittest.TestCase):
    """금지 표현 7종 (README 6장). 하나라도 새면 완료 기준이 깨진다."""

    # (기대 label, 문장). 어미 변형을 같은 label 에 여러 줄 둔다.
    SAMPLES = (
        ("대상입니다", "이 제도는 신청 대상입니다[1]."),
        ("대상입니다", "이 제도는 신청 대상이에요[1]."),
        ("대상입니다", "이 제도는 신청 대상이십니다[1]."),
        ("받을 수 있어요", "월세 지원을 받을 수 있어요[1]."),
        ("받을 수 있어요", "월세 지원을 받을 수 있습니다[1]."),
        ("받을 수 있어요", "월세 지원을 받으실 수 있어요[1]."),
        ("보장", "지원금 지급을 보장해요[1]."),
        ("확실히", "확실히 조건을 충족해요[1]."),
        ("무조건", "무조건 신청 조건을 충족해요[1]."),
        ("100%", "지원 확률은 100% 예요[1]."),
        ("걱정 마세요", "서류는 걱정 마세요[1]."),
    )

    def test_금지_표현_일곱종을_각각_잡는다(self):
        """어미 변형까지 포함해 label 단위로 걸리는지 본다."""
        for label, sentence in self.SAMPLES:
            with self.subTest(label=label, sentence=sentence):
                labels = [banned.label for banned in find_banned(sentence)]
                self.assertIn(
                    label, labels, f"{sentence!r} 에서 {label} 을 잡지 못했다 (걸린 것: {labels})"
                )

    def test_일곱종_전부가_표본에_들어_있다(self):
        """표본이 일부만 덮고 있으면 이 반이 통과해도 완료 기준을 못 지킨다."""
        covered = {label for label, _ in self.SAMPLES}
        self.assertEqual(
            len(covered), 7, f"금지 표현 7종을 모두 덮어야 한다 (덮은 것: {sorted(covered)})"
        )

    def test_validate_가_금지_표현을_문제로_보고한다(self):
        """``find_banned`` 만 잡고 ``validate`` 가 놓치면 답변이 그대로 나간다."""
        for label, sentence in self.SAMPLES:
            with self.subTest(label=label):
                text = f"{sentence} {CLOSING}"
                self.assertIn(BANNED, codes(text), f"{sentence!r} 가 통과했다")
                self.assertIn(label, details_for(text, BANNED))

    def test_금지_표현_건수를_센다(self):
        """지표가 "금지 표현 0건"이라 건수가 필요하다."""
        text = f"확실히 무조건 조건을 충족해요[1]. {CLOSING}"
        check = validate(text, FOOTNOTES)
        self.assertEqual(check.banned_hits, 2, f"두 건이어야 한다: {check.problems}")


class TestBannedFalsePositive(unittest.TestCase):
    """정상 문장을 잡으면 설명이 사라진다. 오탐은 미탐과 다른 방향의 손해다."""

    NORMAL = (
        "휴학 중이라 이 제도는 대상이 아니에요[1].",
        "소득 구간을 알려주시면 대상인지 확인이 필요해요[1].",
        "서류를 내면 받을 수 있는지 확인이 필요해요[1].",
        "가구 소득이 기준 중위소득 100% 이하여야 해요[1].",
        "국민기초생활보장 수급자는 별도 기준을 봐요[1].",
    )

    def test_정상_문장을_금지_표현으로_보지_않는다(self):
        for sentence in self.NORMAL:
            with self.subTest(sentence=sentence):
                hits = [banned.label for banned in find_banned(sentence)]
                self.assertEqual(hits, [], f"{sentence!r} 를 오탐했다: {hits}")

    def test_정상_문장만_있으면_validate_가_통과한다(self):
        """각주와 고정 문구를 갖춘 정상 답변이 통과해야 오탐 여부를 확인할 수 있다."""
        text = " ".join(self.NORMAL) + " " + CLOSING
        check = validate(text, FOOTNOTES)
        self.assertTrue(check.ok, f"정상 답변이 막혔다: {check.problems}")


class TestUnknownFootnote(unittest.TestCase):
    """본문이 쓴 번호가 각주 목록에 없는 경우.

    인용 검증이 실패하면 그 조건은 unknown 이 되고 발췌가 비워진다
    (docs/03-api-contract.md 4-3). 각주 목록에서 번호가 빠지는데 모델이 쓴 본문은
    이미 그 번호를 쓴 상태다. 화면에 연결되지 않는 각주가 남는 실제 실패다.
    """

    def test_목록에_없는_번호를_잡는다(self):
        text = f"소득 조건은 기준 중위소득 100% 이하예요[3]. {CLOSING}"
        check = validate(text, FOOTNOTES)
        self.assertIn(UNKNOWN_FOOTNOTE, check.codes(), f"[3] 을 놓쳤다: {check.problems}")
        self.assertEqual(check.unknown_footnotes, (3,))
        self.assertIn("[3]", details_for(text, UNKNOWN_FOOTNOTE))

    def test_없는_번호는_근거로_세지_않는다(self):
        """번호가 붙어 있어도 연결되지 않으면 각주 없는 판정 문장과 같다."""
        text = f"소득 조건은 기준 중위소득 100% 이하예요[3]. {CLOSING}"
        check = validate(text, FOOTNOTES)
        self.assertIn(MISSING_FOOTNOTE, check.codes(), "없는 번호를 근거로 셌다")
        self.assertEqual(check.cited_sentences, 0)

    def test_있는_번호는_문제로_보지_않는다(self):
        text = f"소득 조건은 기준 중위소득 100% 이하예요[1]. {CLOSING}"
        check = validate(text, FOOTNOTES)
        self.assertNotIn(UNKNOWN_FOOTNOTE, check.codes(), f"오탐: {check.problems}")
        self.assertEqual(check.unknown_footnotes, ())


class TestMissingFootnote(unittest.TestCase):
    """판정·금액·날짜가 든 문장에 각주가 없으면 내보내지 않는다 (README 6장 근거 규칙)."""

    CASES = (
        ("판정", "신청 가능성이 높은 조건을 충족해요."),
        ("금액", "청년월세지원은 월 20만원을 지원해요."),
        ("기간", "접수는 3월 31일에 마감돼요."),
        ("요건", "가구 소득이 기준 중위소득 이하여야 해요."),
    )

    def test_근거가_필요한_문장에_각주가_없으면_잡는다(self):
        for signal, sentence in self.CASES:
            with self.subTest(signal=signal, sentence=sentence):
                text = f"{sentence} {CLOSING}"
                check = validate(text, FOOTNOTES)
                self.assertIn(
                    MISSING_FOOTNOTE,
                    check.codes(),
                    f"{sentence!r} 가 각주 없이 통과했다",
                )
                self.assertTrue(
                    any(signal in detail for detail in details_for(text, MISSING_FOOTNOTE)),
                    f"{signal} 신호가 기록되지 않았다: {details_for(text, MISSING_FOOTNOTE)}",
                )

    def test_각주가_붙으면_통과한다(self):
        for signal, sentence in self.CASES:
            with self.subTest(signal=signal):
                cited = sentence.replace(".", "[1].")
                check = validate(f"{cited} {CLOSING}", FOOTNOTES)
                self.assertTrue(check.ok, f"{cited!r} 가 막혔다: {check.problems}")
                self.assertEqual(check.cited_sentences, 1)

    def test_요약과_고정_문구는_각주를_요구하지_않는다(self):
        """코드가 센 값과 안내 문구는 대응할 발췌가 존재하지 않는다."""
        for sentence in (SUMMARY, CLOSING, NO_RESULT_LINE + ".", BROADEN_LINE + "."):
            with self.subTest(sentence=sentence):
                self.assertFalse(
                    needs_footnote(sentence), f"{sentence!r} 에 각주를 요구하면 오탐이다"
                )

    def test_요약과_고정_문구만_있는_답변은_통과한다(self):
        check = validate(f"{SUMMARY} {CLOSING}", FOOTNOTES)
        self.assertTrue(check.ok, f"코드가 만든 문장이 코드 검사에 걸렸다: {check.problems}")

    def test_결과_없음_세_줄도_통과한다(self):
        text = " ".join(
            (NO_RESULT_LINE + ".", BROADEN_LINE + ".", DATA_SCOPE_LINE + ".", CLOSING)
        )
        check = validate(text, FOOTNOTES)
        self.assertTrue(check.ok, f"결과 없음 답변이 막혔다: {check.problems}")


class TestLengthAndClosing(unittest.TestCase):
    """600자 상한과 고정 문구 (README 6장, 11장)."""

    def test_600자_초과를_잡는다(self):
        text = "괜찮은 안내 문장이에요. " * 60 + CLOSING
        self.assertGreater(len(text), MAX_ANSWER_LEN, "전제 확인: 표본이 상한을 넘는다")
        check = validate(text, FOOTNOTES)
        self.assertIn(TOO_LONG, check.codes(), f"길이를 놓쳤다: {check.length}자")
        self.assertEqual(check.length, len(text))

    def test_상한_이내는_잡지_않는다(self):
        check = validate(f"{SUMMARY} {CLOSING}", FOOTNOTES)
        self.assertNotIn(TOO_LONG, check.codes())

    def test_상한을_인자로_낮출_수_있다(self):
        """정책이 바뀌어도 상한 값을 밖에서 줄 수 있어야 한다."""
        check = validate(f"{SUMMARY} {CLOSING}", FOOTNOTES, max_len=10)
        self.assertIn(TOO_LONG, check.codes())

    def test_고정_문구가_없으면_잡는다(self):
        check = validate(SUMMARY, FOOTNOTES)
        self.assertIn(NO_CLOSING_LINE, check.codes(), "고정 문구 없이 통과했다")

    def test_고정_문구는_문장_부호가_달라도_인정한다(self):
        check = validate(f"{SUMMARY} {CLOSING_LINE}!", FOOTNOTES)
        self.assertNotIn(NO_CLOSING_LINE, check.codes())


class TestKnownFootnoteIds(unittest.TestCase):
    """각주 목록의 모양이 통합 전까지 하나로 정해지지 않아 넓게 받는다."""

    CASES = (
        ("항목 dict 목록", [{"footnote_id": 1}, {"id": 2}], {1, 2}),
        ("단일 dict", {"footnote_id": 3}, {3}),
        ("번호 집합", {1, 2}, {1, 2}),
        ("번호 키 dict", {1: {"excerpt": "가"}, 2: {"excerpt": "나"}}, {1, 2}),
        ("문자열 번호 목록", [1, "2"], {1, 2}),
        ("None", None, set()),
        ("빈 목록", [], set()),
        ("숫자 하나", 4, {4}),
    )

    def test_여러_입력_모양을_받는다(self):
        for label, raw, expected in self.CASES:
            with self.subTest(shape=label):
                self.assertEqual(
                    known_footnote_ids(raw), frozenset(expected), f"{label} 을 잘못 읽었다"
                )

    def test_읽을_수_없는_값은_조용히_버린다(self):
        """각주 하나의 모양이 이상해서 응답 전체를 실패시키면 손해가 더 크다."""
        self.assertEqual(known_footnote_ids([{"footnote_id": "가"}, {"id": None}, 5]), frozenset({5}))

    def test_번호_집합만_넘겨도_validate_가_동작한다(self):
        check = validate(f"소득 조건을 충족해요[1]. {CLOSING}", {1})
        self.assertTrue(check.ok, f"번호 집합을 못 받았다: {check.problems}")


class TestSplitSentences(unittest.TestCase):
    """문장 분리. 여기서 깨지면 뒤따르는 문장이 각주 없는 문장으로 오인된다."""

    def test_소수점에서_깨지지_않는다(self):
        self.assertEqual(split_sentences("지원 비율은 3.5% 예요."), ("지원 비율은 3.5% 예요.",))

    def test_마침표로_끊은_날짜에서_깨지지_않는다(self):
        text = "접수는 2026. 3. 1. 까지예요."
        self.assertEqual(split_sentences(text), (text,))

    def test_종결_부호를_문장에_붙여_돌려준다(self):
        """``sanitize`` 가 남은 문장을 다시 이어 붙일 때 부호가 사라지면 안 된다."""
        self.assertEqual(
            split_sentences("첫 문장이에요. 둘째 문장인가요?"),
            ("첫 문장이에요.", "둘째 문장인가요?"),
        )

    def test_줄바꿈도_문장_경계로_본다(self):
        self.assertEqual(split_sentences("첫 줄\n둘째 줄"), ("첫 줄", "둘째 줄"))

    def test_빈_입력은_빈_결과다(self):
        self.assertEqual(split_sentences(""), ())
        self.assertEqual(split_sentences(None), ())


class TestFootnoteNumbers(unittest.TestCase):
    """각주 번호 추출. 대괄호 안이 숫자인 것만 각주다."""

    def test_붙여_쓴_번호를_둘로_읽는다(self):
        self.assertEqual(footnote_numbers("두 제도가 겹쳐요[1][2]."), (1, 2))

    def test_숫자가_아닌_대괄호는_각주가_아니다(self):
        self.assertEqual(footnote_numbers(f"마감이 가까워요{SLOT_FOOTNOTE}."), ())
        self.assertFalse(has_footnote("자리 표시만 있어요[각주]."))

    def test_중복은_한_번만_나온_순서대로(self):
        self.assertEqual(footnote_numbers("[2] 그리고 [1] 다시 [2]"), (2, 1))

    def test_자리_표시만_있는_판정_문장은_각주가_없는_것으로_본다(self):
        """모델이 번호를 채우지 않으면 그 문장은 빠져야 한다. 안전한 실패다."""
        text = f"3월 31일에 마감돼요{SLOT_FOOTNOTE}. {CLOSING}"
        self.assertIn(MISSING_FOOTNOTE, codes(text))


class TestSanitizeContract(unittest.TestCase):
    """핵심 계약: ``sanitize`` 결과는 다시 ``validate`` 해서 통과해야 한다.

    이게 깨지면 정리 단계를 거쳐도 검증 실패 답변이 사용자에게 나간다.
    """

    CASES = (
        (
            "단정과 없는 각주가 섞인 답변",
            f"{SUMMARY} 청년월세지원은 월 20만원을 지원해요[1]. "
            "확실히 대상입니다[2]. 소득 조건은 기준 중위소득 100% 이하예요[7].",
        ),
        (
            "수식어만 든 답변",
            f"{SUMMARY} 확실히 소득 조건을 충족해요[1]. {CLOSING}",
        ),
        (
            "각주 없는 판정 문장이 든 답변",
            f"{SUMMARY} 신청 조건을 충족해요. {CLOSING}",
        ),
        (
            "고정 문구가 없는 답변",
            f"{SUMMARY} 휴학생은 제외돼요[2].",
        ),
        (
            "600자를 넘는 답변",
            SUMMARY
            + " "
            + "청년월세지원은 월 20만원을 지원하고 서류는 주민등록초본이에요[1]. " * 20
            + CLOSING,
        ),
        ("빈 답변", ""),
    )

    def test_정리한_결과는_검증을_통과한다(self):
        for label, text in self.CASES:
            with self.subTest(case=label):
                result = sanitize(text, FOOTNOTES)
                check = validate(result.text, FOOTNOTES)
                self.assertTrue(
                    check.ok,
                    f"{label}: 정리 후에도 문제가 남았다 -> {check.problems}",
                )

    def test_정리_결과는_항상_고정_문구를_갖는다(self):
        for label, text in self.CASES:
            with self.subTest(case=label):
                self.assertIn(CLOSING_LINE, sanitize(text, FOOTNOTES).text, f"{label}")

    def test_정리_결과는_600자_이내다(self):
        for label, text in self.CASES:
            with self.subTest(case=label):
                result = sanitize(text, FOOTNOTES)
                self.assertLessEqual(result.final_length, MAX_ANSWER_LEN, f"{label}")


class TestSanitizeBehavior(unittest.TestCase):
    """무엇을 어떻게 고치는지."""

    def test_없는_각주를_참조하는_문장은_문장째_빠진다(self):
        """번호만 지우면 근거 없는 판정 문장이 남는다. 그건 P0 위반이다."""
        text = f"{SUMMARY} 소득 조건은 기준 중위소득 100% 이하예요[7]. {CLOSING}"
        result = sanitize(text, FOOTNOTES)
        self.assertNotIn("[7]", result.text)
        self.assertNotIn("기준 중위소득", result.text, "번호만 지우고 문장을 남겼다")
        self.assertIn(
            UNKNOWN_FOOTNOTE,
            [removal.code for removal in result.removals],
            "무엇을 why 뺐는지 기록이 없다",
        )

    def test_단정_문장은_빼고_수식어는_지워_살린다(self):
        text = f"{SUMMARY} 확실히 소득 조건을 충족해요[1]. 지원 대상입니다[2]. {CLOSING}"
        result = sanitize(text, FOOTNOTES)
        self.assertIn("소득 조건을 충족해요[1]", result.text, "수식어만 지우고 살려야 한다")
        self.assertNotIn("확실히", result.text)
        self.assertNotIn("대상입니다", result.text, "단정 문장은 빠져야 한다")

    def test_각주_없는_판정_문장은_빠진다(self):
        text = f"{SUMMARY} 신청 조건을 충족해요. {CLOSING}"
        result = sanitize(text, FOOTNOTES)
        self.assertNotIn("신청 조건을 충족해요", result.text)
        self.assertIn(MISSING_FOOTNOTE, [removal.code for removal in result.removals])

    def test_고정_문구가_없으면_붙인다(self):
        result = sanitize(f"{SUMMARY} 휴학생은 제외돼요[2].", FOOTNOTES)
        self.assertTrue(result.closing_added)
        self.assertIn(CLOSING_LINE, result.text)

    def test_600자로_줄일_때_요약과_고정_문구를_남긴다(self):
        """요약은 결론이고 고정 문구는 P0 안내다. 둘 중 하나가 빠지면 답변이 성립하지 않는다."""
        text = (
            SUMMARY
            + " "
            + "청년월세지원은 월 20만원을 지원하고 서류는 주민등록초본이에요[1]. " * 20
            + CLOSING
        )
        result = sanitize(text, FOOTNOTES)
        self.assertTrue(result.truncated, "길이 때문에 뺀 기록이 없다")
        self.assertLessEqual(result.final_length, MAX_ANSWER_LEN, result.text)
        self.assertIn(SUMMARY, result.text, "요약이 사라졌다")
        self.assertIn(CLOSING_LINE, result.text, "고정 문구가 사라졌다")
        self.assertIn(TOO_LONG, [removal.code for removal in result.removals])

    def test_길이를_글자_중간에서_자르지_않는다(self):
        """문장이 잘리면 각주 번호나 조건이 반쯤 남아 오히려 틀린 안내가 된다."""
        text = (
            SUMMARY
            + " "
            + "청년월세지원은 월 20만원을 지원하고 서류는 주민등록초본이에요[1]. " * 20
            + CLOSING
        )
        result = sanitize(text, FOOTNOTES)
        for sentence in split_sentences(result.text):
            with self.subTest(sentence=sentence):
                self.assertTrue(
                    sentence.endswith(".") or sentence.endswith("요"),
                    f"문장이 중간에서 잘렸다: {sentence!r}",
                )

    def test_길이_정보를_함께_돌려준다(self):
        text = f"{SUMMARY} 확실히 소득 조건을 충족해요[1]. {CLOSING}"
        result = sanitize(text, FOOTNOTES)
        self.assertEqual(result.original_length, len(text))
        self.assertEqual(result.final_length, len(result.text))


class TestBuildSkeleton(unittest.TestCase):
    """코드가 확정할 수 있는 부분만 만드는지 (README 6장 1~5)."""

    def test_likely와_check_개수를_요약한다(self):
        skeleton = build_skeleton(
            [
                policy(policy_id="A", status=LIKELY),
                policy(policy_id="B", status=LIKELY),
                policy(policy_id="C", status=CHECK),
                policy(policy_id="D", status=UNLIKELY),
            ]
        )
        self.assertEqual((skeleton.likely_count, skeleton.check_count), (2, 1))
        self.assertIn("높은 제도 2개", skeleton.summary)
        self.assertIn("확인이 필요한 제도 1개", skeleton.summary)

    def test_화면_문구가_status_자리에_와도_읽는다(self):
        """프론트가 status 와 status_label 을 섞어 넘기는 실수에 대비한다.

        받는 값은 계약에 있는 것(docs/03-api-contract.md 4장)과 화면 문구
        (docs/01-glossary-profile.md 4장)뿐이다. 문서에 없는 값을 창작해 받지 않는다.
        """
        skeleton = build_skeleton(
            [policy(status="신청 가능성이 높아요"), policy(policy_id="B", status="확인이 필요해요")]
        )
        self.assertEqual((skeleton.likely_count, skeleton.check_count), (1, 1))

    def test_0개인_쪽은_문장에서_뺀다(self):
        """"확인이 필요한 제도 0개"가 첫 문장에 남으면 사용자가 먼저 보는 값이 0이 된다."""
        skeleton = build_skeleton([policy(status=LIKELY)])
        self.assertIn("높은 제도 1개", skeleton.summary)
        self.assertNotIn("확인이 필요한", skeleton.summary)

        skeleton = build_skeleton([policy(status=CHECK)])
        self.assertIn("확인이 필요한 제도 1개", skeleton.summary)
        self.assertNotIn("가능성이 높은", skeleton.summary)

    def test_둘_다_0이면_결과_없음_문구를_쓴다(self):
        skeleton = build_skeleton([policy(status=UNLIKELY)])
        self.assertEqual(skeleton.summary, NO_RESULT_LINE + ".")
        self.assertEqual(
            skeleton.extra_lines, (BROADEN_LINE + ".", DATA_SCOPE_LINE + ".")
        )
        self.assertEqual(skeleton.policy_slots, (), "결과가 없으면 설명할 자리도 없다")

    def test_정책이_아예_없어도_결과_없음이다(self):
        self.assertEqual(build_skeleton([]).summary, NO_RESULT_LINE + ".")

    def test_마감_임박_정책만_마감_문장에_담는다(self):
        skeleton = build_skeleton(
            [
                policy(title="청년월세지원", deadline={"is_imminent": True, "badge": "마감 임박 D-3"}),
                policy(policy_id="B", title="여유있는제도", deadline={"is_imminent": False, "badge": "D-40"}),
            ]
        )
        self.assertIsNotNone(skeleton.deadline_line)
        self.assertIn("청년월세지원 마감 임박 D-3", skeleton.deadline_line)
        self.assertNotIn("여유있는제도", skeleton.deadline_line)

    def test_마감_임박이_없으면_마감_문장이_없다(self):
        skeleton = build_skeleton([policy(deadline={"is_imminent": False, "badge": "D-40"})])
        self.assertIsNone(skeleton.deadline_line)

    def test_마감_문장에_각주_자리를_둔다(self):
        """마감일은 공고에서 온 날짜라 각주가 필요한 문장이다."""
        skeleton = build_skeleton([policy(deadline={"is_imminent": True, "badge": "오늘 마감"})])
        self.assertIn(SLOT_FOOTNOTE, skeleton.deadline_line)

    def test_날짜를_계산하지_않고_badge를_그대로_인용한다(self):
        """날짜 계산은 코드가 한 번만 한다. 두 곳에서 세면 배지와 문장이 어긋난다."""
        skeleton = build_skeleton(
            [
                policy(
                    deadline={
                        "is_imminent": True,
                        "badge": "오늘 마감",
                        "d_day": 99,
                        "apply_end": "2030-01-01",
                    }
                )
            ]
        )
        self.assertIn("오늘 마감", skeleton.deadline_line)
        self.assertNotIn("99", skeleton.deadline_line, "badge 대신 d_day 를 셌다")
        self.assertNotIn("2030", skeleton.deadline_line, "마감일을 직접 해석했다")

    def test_badge가_없으면_d_day로만_만든다(self):
        skeleton = build_skeleton([policy(deadline={"is_imminent": True, "d_day": 5})])
        self.assertIn("D-5", skeleton.deadline_line)

    def test_badge와_d_day가_모두_없으면_제목만_남는다(self):
        skeleton = build_skeleton([policy(title="청년월세지원", deadline={"is_imminent": True})])
        self.assertIn("청년월세지원", skeleton.deadline_line)


class TestDeadlineBadgeFromDDay(unittest.TestCase):
    """``badge`` 가 비었을 때 ``d_day`` 로 만드는 배지 문구.

    이 테스트가 지키려는 계약
      1. 배지 문구 표(docs/03-api-contract.md 4-2: 오늘 마감 / 마감 임박 D-n / D-n /
         상시 접수 / 접수 예정)에 없는 문구를 이 모듈이 만들지 않는다. 서버
         ``Deadline.d_day`` 는 ``int | None`` 이라 0 과 음수를 막지 않으므로, 값을 그대로
         끼우면 마감이 지난 정책에 ``"D--3"`` 이 나갔다.
      2. 문구를 만들 수 없으면 배지만 비운다. 마감 임박 여부는 ``is_imminent`` 가 정하고
         (``related.py`` 도 같은 규칙) ``d_day`` 로 그 판단을 뒤집지 않는다. 그래서
         제목은 문장에 남는다.
    """

    # (설명, d_day, 문장에 있어야 하는 것, 문장에 없어야 하는 것)
    CASES = (
        ("양수는 표의 D-n 형태로 만든다", 5, "D-5", None),
        ("1 은 경계값이라 만든다", 1, "D-1", None),
        # 표는 마감일이 오늘이면 "오늘 마감" 이라 하고 "D-0" 은 없다. 어느 문구를 쓸지
        # 고르는 일은 날짜를 아는 서버의 몫이라 여기서는 비운다.
        ("0 은 표에 문구가 없어 비운다", 0, None, "D-0"),
        ("음수는 사람이 읽을 수 없는 문구가 된다", -3, None, "D--3"),
        ("큰 음수도 같다", -120, None, "D--120"),
        ("None 이면 만들 것이 없다", None, None, "D-None"),
        ("문자열은 정수로 읽지 않는다", "곧", None, "D-곧"),
        ("음수 문자열도 읽지 않는다", "-3", None, "D--3"),
    )

    def test_표에_없는_배지_문구를_만들지_않는다(self):
        for label, d_day, expected, forbidden in self.CASES:
            with self.subTest(case=label, d_day=d_day):
                line = build_skeleton(
                    [
                        policy(
                            title="청년월세지원",
                            deadline={"is_imminent": True, "d_day": d_day},
                        )
                    ]
                ).deadline_line
                self.assertIsNotNone(line, f"{label}: 마감 문장이 사라졌다")
                self.assertIn(
                    "청년월세지원", line, f"{label}: 제목까지 사라졌다 ({line!r})"
                )
                if expected is not None:
                    self.assertIn(expected, line, f"{label}: {line!r}")
                if forbidden is not None:
                    self.assertNotIn(forbidden, line, f"{label}: {line!r}")

    def test_음수_d_day에_D_두개가_나오지_않는다(self):
        """``"D--3"`` 을 직접 겨냥한다. 마감이 지난 정책의 배지 문구다."""
        line = build_skeleton(
            [policy(title="청년월세지원", deadline={"is_imminent": True, "d_day": -3})]
        ).deadline_line
        self.assertNotIn("D--", line, f"음수를 그대로 끼웠다: {line!r}")
        self.assertNotIn("D-", line, f"음수로 배지를 만들었다: {line!r}")
        self.assertNotIn("3", line, f"남은 일수를 문장에 담았다: {line!r}")

    def test_배지가_이미_있으면_d_day를_보지_않는다(self):
        """서버가 준 문구가 있으면 그것을 인용한다. 음수 d_day 가 섞여도 문구가 이긴다."""
        for label, d_day in (("음수", -3), ("0", 0), ("None", None), ("문자열", "곧")):
            with self.subTest(case=label, d_day=d_day):
                line = build_skeleton(
                    [
                        policy(
                            title="청년월세지원",
                            deadline={
                                "is_imminent": True,
                                "badge": "오늘 마감",
                                "d_day": d_day,
                            },
                        )
                    ]
                ).deadline_line
                self.assertIn("오늘 마감", line, f"{label}: {line!r}")
                self.assertNotIn("D-", line, f"{label}: d_day 로 배지를 다시 만들었다 ({line!r})")

    def test_제목도_없고_배지도_만들_수_없으면_문장에서_빠진다(self):
        """빈 자리를 문장에 남기면 "마감이 가까운 제도가 있어요: ." 가 나간다."""
        line = build_skeleton(
            [policy(title="", deadline={"is_imminent": True, "d_day": -3})]
        ).deadline_line
        self.assertIsNone(line, f"빈 마감 문장이 만들어졌다: {line!r}")

    def test_배지를_만들_수_없는_정책과_만들_수_있는_정책이_섞여도_된다(self):
        """한 정책의 d_day 가 음수라고 다른 정책의 배지가 사라지면 안 된다."""
        line = build_skeleton(
            [
                policy(
                    policy_id="A",
                    title="지난제도",
                    deadline={"is_imminent": True, "d_day": -3},
                ),
                policy(
                    policy_id="B",
                    title="청년월세지원",
                    deadline={"is_imminent": True, "d_day": 3},
                ),
            ]
        ).deadline_line
        self.assertIn("지난제도", line)
        self.assertIn("청년월세지원 D-3", line, f"{line!r}")
        self.assertNotIn("D--3", line, f"{line!r}")

    def test_제목이_없는_정책은_설명_자리를_만들지_않는다(self):
        """빈 제목으로 자리를 열면 모델이 정책명을 지어낸다."""
        skeleton = build_skeleton(
            [policy(policy_id="A", title=""), policy(policy_id="B", title="청년월세지원")]
        )
        self.assertEqual([slot.title for slot in skeleton.policy_slots], ["청년월세지원"])

    def test_설명_자리는_최대_다섯개다(self):
        skeleton = build_skeleton(
            [policy(policy_id=f"P{i}", title=f"제도{i}") for i in range(7)]
        )
        self.assertEqual(len(skeleton.policy_slots), 5)

    def test_likely를_check보다_앞에_둔다(self):
        skeleton = build_skeleton(
            [
                policy(policy_id="A", title="확인필요제도", status=CHECK),
                policy(policy_id="B", title="가능제도", status=LIKELY),
            ]
        )
        self.assertEqual(
            [slot.title for slot in skeleton.policy_slots], ["가능제도", "확인필요제도"]
        )

    def test_outline은_요약으로_시작하고_고정_문구로_끝난다(self):
        skeleton = build_skeleton([policy(status=CHECK)])
        outline = skeleton.outline()
        self.assertEqual(outline[0], skeleton.summary)
        self.assertEqual(outline[-1], CLOSING_LINE)


class TestBuildSkeletonTolerance(unittest.TestCase):
    """규칙 결과와 AI 결과가 합쳐지는 중간 상태에서도 불린다.

    키 하나가 비었다고 예외를 던지면 답변 단계가 통째로 실패한다
    (docs/03-api-contract.md 9장 10단계).
    """

    TOLERATED = (
        ("None 입력", None),
        ("빈 목록", []),
        ("빈 dict 하나", [{}]),
        ("값이 모두 None", [{"policy_id": None, "title": None, "status": None, "deadline": None}]),
        ("deadline 이 문자열", [{"title": "제도", "status": LIKELY, "deadline": "2026-03-01"}]),
        ("deadline 이 목록", [{"title": "제도", "status": LIKELY, "deadline": ["2026-03-01"]}]),
        ("d_day 가 문자열", [{"title": "제도", "status": LIKELY, "deadline": {"is_imminent": True, "d_day": "곧"}}]),
        ("status 가 숫자", [{"title": "제도", "status": 3}]),
        ("모르는 키만 있음", [{"이상한키": 1}]),
    )

    def test_깨진_입력에도_예외를_던지지_않는다(self):
        for label, policies in self.TOLERATED:
            with self.subTest(case=label):
                skeleton = build_skeleton(policies)
                self.assertTrue(skeleton.summary, f"{label}: 요약이 비었다")
                self.assertEqual(skeleton.closing_line, CLOSING_LINE)

    def test_모르는_상태_값은_확인_필요로_센다(self):
        """모르는 값을 likely 로 올리면 근거 없이 "가능성 높음"이 나간다. 그건 막아야 한다.

        그렇다고 unlikely 로 떨어뜨리면 더 나쁘다. 상태 값 표기가 어긋나는 순간
        카드 5장이 화면에 떠 있는데 요약이 "찾지 못했어요"로 나간다. 화면과 답변이
        정반대를 말하는 것이 가장 나쁜 실패다. 그래서 check(확인이 필요해요)로 센다.
        """
        skeleton = build_skeleton([policy(status="아마도")])
        self.assertEqual((skeleton.likely_count, skeleton.check_count), (0, 1))
        self.assertIn("확인이 필요한 제도 1개", skeleton.summary)
        self.assertNotIn("가능성이 높은", skeleton.summary, "근거 없이 likely 로 올렸다")

    def test_카드가_있으면_결과_없음이라고_말하지_않는다(self):
        """상태 값이 전부 어긋나도 카드가 있다는 사실은 바뀌지 않는다."""
        skeleton = build_skeleton([policy(status=None), policy(policy_id="B", status="")])
        self.assertNotEqual(
            skeleton.summary,
            NO_RESULT_LINE + ".",
            "카드가 있는데 결과 없음이라고 말했다",
        )

    def test_unlikely만_있으면_결과_없음이다(self):
        """명시적으로 unlikely 인 것은 세지 않는다. 접힌 영역에 들어가는 카드다."""
        skeleton = build_skeleton([policy(status=UNLIKELY)])
        self.assertEqual((skeleton.likely_count, skeleton.check_count), (0, 0))
        self.assertEqual(skeleton.summary, NO_RESULT_LINE + ".")


if __name__ == "__main__":
    unittest.main()
