"""판정 프롬프트 조립 테스트 (`ai/judgment/prompt.py`).

프롬프트는 눈으로 읽어 확인하는 것이라 테스트할 게 없어 보이지만, 깨지면
조용히 판정 품질만 떨어진다. 그래서 **구조**를 검사한다.

| 무엇을 보는가 | 왜 |
| --- | --- |
| 네 부분이 순서대로 | 연구 문서 6장 "우리 방침" |
| 프로필이 원문보다 뒤 | 프롬프트 캐싱 접두사 일치 (연구 문서 5장) |
| 규칙이 앞과 끝에 두 번 | 중간 묻힘 대비 (근거성 문서 5절) |
| 값 없는 항목이 "정보 없음" | 항목을 빼면 모델이 추측한다 (README 4장) |
| met·unmet·unknown 세 값 | README 4장 |
| 추정 금지 예시 네 개 | README 4장. J7·J8 이 여기 걸린다 |
| 원문이 변형 없이 들어감 | 원문을 바꾸면 인용 검증이 전건 실패한다 |
| 빈 입력에도 안 터짐 | README 9장 (exceptions_text 비면 판정 생략) |
"""

import unicodedata
import unittest

from ai.judgment.prompt import (
    JUDGE_SYSTEM_PROMPT,
    NO_VALUE,
    build_judge_prompt,
)
from ai.judgment.values import (
    ASK_NOTICE,
    BASE_FIELD_VALUES,
    EXTRA_FIELD_VALUES,
    MAX_EXCERPT_LEN,
    MAX_NAME_LEN,
    MET,
    MIN_EXCERPT_LEN,
    UNKNOWN,
    UNMET,
    field_label,
)

RAW = (
    "2026년 서울 청년 생활비 지원 사업 공고\n"
    "1. 지원 대상: 서울시에 거주하는 만 19세 이상 34세 이하 청년\n"
    "2. 제외 대상: 휴학생은 지원 대상에서 제외합니다. "
    "타 청년 지원금을 받고 있는 경우에도 신청할 수 없습니다.\n"
)
EXCEPTIONS = "휴학생은 지원 대상에서 제외합니다. 타 청년 지원금을 받고 있는 경우에도 신청할 수 없습니다."

PROFILE = {
    "age": 23,
    "region": "seoul",
    "district": None,
    "status": "on_leave",
    "categories": ["living", "job"],
    "income_bracket": "unknown",
}

# 규칙 문구 안에서도 태그 이름을 언급하기 때문에(모델에게 어디를 보라고 알려야 한다)
# 단순히 "<policy_text>" 를 찾으면 그 언급이 먼저 걸린다.
# 블록은 앞뒤에 줄바꿈이 붙은 형태로만 나오므로 그 형태를 기준점으로 쓴다.
POLICY_OPEN = "\n<policy_text>\n"
POLICY_CLOSE = "\n</policy_text>\n"
EXCEPTION_OPEN = "\n<exception_condition>\n"
EXCEPTION_CLOSE = "\n</exception_condition>\n"
PROFILE_OPEN = "\n<profile>\n"
PROFILE_CLOSE = "\n</profile>\n"

#: 추정 금지 예시 네 개. README 4장의 표를 그대로 옮긴 것이다.
#: 문구가 바뀌면 이 테스트가 먼저 깨진다.
NO_GUESS_EXAMPLES = (
    "대학생이니 소득이 낮을 것이다",
    "서울에 산다니 서울 소재 대학일 것이다",
    "재학생이니 다른 지원은 안 받을 것이다",
    "휴학이라고 했으니 구직 중일 것이다",
)


def build():
    return build_judge_prompt(EXCEPTIONS, PROFILE, RAW)


class TestSystemPrompt(unittest.TestCase):
    """시스템 지시문은 상수다. 조립하지 않는다."""

    def test_비어_있지_않은_문자열이다(self):
        self.assertIsInstance(JUDGE_SYSTEM_PROMPT, str)
        self.assertGreater(len(JUDGE_SYSTEM_PROMPT), 200)

    def test_세_결과값이_들어있다(self):
        for value in (MET, UNMET, UNKNOWN):
            self.assertIn(value, JUDGE_SYSTEM_PROMPT)

    def test_추정_금지_예시_네_개가_들어있다(self):
        for example in NO_GUESS_EXAMPLES:
            self.assertIn(example, JUDGE_SYSTEM_PROMPT)

    def test_판정하지_않는_축을_적어_둔다(self):
        """나이·지역·신분·소득·마감은 규칙 엔진 몫이다 (README 3장)."""
        for axis in ("나이", "지역", "신분", "소득", "마감"):
            self.assertIn(axis, JUDGE_SYSTEM_PROMPT)

    def test_날짜와_판정_상태를_만들지_말라고_적는다(self):
        self.assertIn("날짜를 계산하지", JUDGE_SYSTEM_PROMPT)
        for status in ("likely", "check", "unlikely"):
            self.assertIn(status, JUDGE_SYSTEM_PROMPT)

    def test_모델_이름이_없다(self):
        """모델 이름은 client.py 담당이다. 여기 박아 두면 교체가 재작성이 된다."""
        lowered = JUDGE_SYSTEM_PROMPT.lower() + build().lower()
        for name in ("claude", "gpt", "sonnet", "anthropic", "openai"):
            self.assertNotIn(name, lowered)


class TestFourParts(unittest.TestCase):
    """판정 규칙 → 정책 원문 → 프로필 → 출력 형태와 금지 사항."""

    def setUp(self):
        self.prompt = build()

    def test_네_부분이_순서대로_들어있다(self):
        order = ["[판정 규칙]", POLICY_OPEN, PROFILE_OPEN, "[출력 형태]"]
        positions = []
        for marker in order:
            self.assertIn(marker, self.prompt)
            positions.append(self.prompt.index(marker))
        self.assertEqual(positions, sorted(positions))

    def test_블록이_한_번씩만_나온다(self):
        for marker in (POLICY_OPEN, POLICY_CLOSE, PROFILE_OPEN, PROFILE_CLOSE):
            self.assertEqual(self.prompt.count(marker), 1)

    def test_프로필이_원문보다_뒤에_온다(self):
        """캐싱 접두사 일치. 매번 바뀌는 값이 앞에 오면 캐시가 깨진다."""
        self.assertLess(
            self.prompt.index(POLICY_CLOSE), self.prompt.index(PROFILE_OPEN)
        )

    def test_예외_문장은_원문과_따로_감싼다(self):
        start = self.prompt.index(EXCEPTION_OPEN)
        self.assertLess(self.prompt.index(POLICY_CLOSE), start)
        self.assertLess(start, self.prompt.index(PROFILE_OPEN))
        block = self.prompt[start : self.prompt.index(EXCEPTION_CLOSE)]
        self.assertIn(EXCEPTIONS, block)

    def test_출력_형태가_인터페이스_필드명을_쓴다(self):
        """`docs/03-api-contract.md` 4-1 과 `citation.py` 가 쓰는 이름."""
        tail = self.prompt[self.prompt.index("[출력 형태]") :]
        for key in ("conditions", "name", "result", "excerpt", "needed_field"):
            self.assertIn(key, tail)


class TestRulesTwice(unittest.TestCase):
    """지켜야 할 규칙은 원문 앞과 끝에 두 번 나온다."""

    def setUp(self):
        prompt = build()
        self.head = prompt[: prompt.index(POLICY_OPEN)]
        self.tail = prompt[prompt.index(PROFILE_CLOSE) :]

    def test_세_결과값이_양쪽에_있다(self):
        for value in (MET, UNMET, UNKNOWN):
            self.assertIn(value, self.head)
            self.assertIn(value, self.tail)

    def test_미충족_비대칭이_양쪽에_있다(self):
        """조금이라도 애매하면 미확인. 이 문장이 빠지면 미충족 오판이 늘어난다."""
        self.assertIn("애매하면", self.head)
        self.assertIn("애매하면", self.tail)

    def test_발췌_길이_제약이_양쪽에_있다(self):
        for token in (str(MIN_EXCERPT_LEN), str(MAX_EXCERPT_LEN)):
            self.assertIn(token, self.head)
            self.assertIn(token, self.tail)

    def test_추정_금지_예시가_양쪽에_있다(self):
        for example in NO_GUESS_EXAMPLES:
            self.assertIn(example, self.head)
            self.assertIn(example, self.tail)

    def test_조건_요약_길이가_양쪽에_있다(self):
        self.assertIn(str(MAX_NAME_LEN), self.head)
        self.assertIn(str(MAX_NAME_LEN), self.tail)

    def test_원문에_없는_조건_금지가_양쪽에_있다(self):
        self.assertIn("없는 조건을 만들지", self.head)
        self.assertIn("없는 조건을 만들지", self.tail)


class TestNeededField(unittest.TestCase):
    """새 항목 이름을 만들면 후속 질문이 그 항목을 물을 수 없다."""

    def setUp(self):
        self.prompt = build()

    def test_추가_항목_여덟_개를_나열한다(self):
        self.assertEqual(len(EXTRA_FIELD_VALUES), 8)
        for field in EXTRA_FIELD_VALUES:
            self.assertIn(field, self.prompt)
            self.assertIn(field_label(field), self.prompt)

    def test_공고_확인_필요도_허용한다(self):
        self.assertIn(ASK_NOTICE, self.prompt)

    def test_미확인일_때만_채우라고_적는다(self):
        tail = self.prompt[self.prompt.index("[출력 형태]") :]
        self.assertIn("needed_field", tail)
        self.assertIn(UNKNOWN, tail)


class TestProfileBlock(unittest.TestCase):
    def profile_block(self, prompt):
        return prompt[prompt.index(PROFILE_OPEN) : prompt.index(PROFILE_CLOSE)]

    def test_값이_있는_항목은_화면_문구로_적는다(self):
        block = self.profile_block(build())
        self.assertIn("- 거주지: 서울", block)
        self.assertIn("- 현재 상태: 휴학", block)
        self.assertIn("- 나이: 23", block)
        self.assertIn("- 가구 소득: 잘 모르겠어요", block)

    def test_값이_없는_항목은_정보_없음으로_적는다(self):
        """빼면 모델이 추측한다. 그래서 항목을 남기고 없다고 쓴다."""
        block = self.profile_block(build())
        self.assertIn(f"- 자치구: {NO_VALUE}", block)
        self.assertIn(f"- 주거 형태: {NO_VALUE}", block)
        self.assertIn(f"- 직전 학기 성적: {NO_VALUE}", block)

    def test_모든_프로필_항목이_한_줄씩_나온다(self):
        block = self.profile_block(build())
        fields = tuple(BASE_FIELD_VALUES) + tuple(EXTRA_FIELD_VALUES)
        for field in fields:
            self.assertIn(f"- {field_label(field)}: ", block)

    def test_빈_프로필이면_전부_정보_없음이다(self):
        block = self.profile_block(build_judge_prompt(EXCEPTIONS, {}, RAW))
        fields = tuple(BASE_FIELD_VALUES) + tuple(EXTRA_FIELD_VALUES)
        for field in fields:
            self.assertIn(f"- {field_label(field)}: {NO_VALUE}", block)

    def test_표에_없는_항목은_적지_않는다(self):
        """프로필 항목 표에 없는 것은 쓰지 않는다 (`docs/01-glossary-profile.md` 3장)."""
        prompt = build_judge_prompt(
            EXCEPTIONS, {"favorite_color": "blue", "name": "홍길동"}, RAW
        )
        self.assertNotIn("favorite_color", prompt)
        self.assertNotIn("홍길동", prompt)

    def test_복수_선택_항목은_나란히_적는다(self):
        block = self.profile_block(build())
        self.assertIn("- 관심 분야: living, job", block)

    def test_빈_목록은_정보_없음이다(self):
        prompt = build_judge_prompt(EXCEPTIONS, {"categories": []}, RAW)
        self.assertIn(f"- 관심 분야: {NO_VALUE}", self.profile_block(prompt))


class TestRawTextUntouched(unittest.TestCase):
    """원문을 손대면 발췌 대조가 깨진다. 한 글자도 바꾸지 않는다."""

    def test_원문이_그대로_들어간다(self):
        self.assertIn(RAW, build())

    def test_공백과_줄바꿈을_바꾸지_않는다(self):
        raw = "  제외 대상\r\n\u3000휴학생\t\t제외  "
        self.assertIn(raw, build_judge_prompt("휴학생 제외", PROFILE, raw))

    def test_자모_분해형_한글도_그대로_둔다(self):
        """macOS 경로에서 들어오는 NFD. 정규화는 검증 쪽에서만 한다."""
        raw = unicodedata.normalize("NFD", "휴학생은 지원 대상에서 제외합니다.")
        prompt = build_judge_prompt(raw, PROFILE, raw)
        self.assertIn(raw, prompt)

    def test_예외_문장도_그대로_들어간다(self):
        exceptions = "   타 지원금 수혜자 제외\n(단, 국가장학금은 제외)  "
        self.assertIn(exceptions, build_judge_prompt(exceptions, PROFILE, RAW))


class TestEdgeCases(unittest.TestCase):
    """터지지 않는 것이 먼저다. 판정이 실패해도 카드는 이미 화면에 있다."""

    def test_빈_예외_문장으로도_문자열을_돌려준다(self):
        prompt = build_judge_prompt("", PROFILE, RAW)
        self.assertIn("<exception_condition>", prompt)
        self.assertIn("[출력 형태]", prompt)

    def test_빈_원문으로도_터지지_않는다(self):
        prompt = build_judge_prompt(EXCEPTIONS, PROFILE, "")
        self.assertIn("<policy_text>", prompt)

    def test_전부_비어도_터지지_않는다(self):
        prompt = build_judge_prompt("", {}, "")
        self.assertIn("[판정 규칙]", prompt)
        self.assertIn("[출력 형태]", prompt)

    def test_None_이_와도_터지지_않는다(self):
        """호출부 실수로 None 이 들어오는 경우. 빈 문자열로 취급한다."""
        prompt = build_judge_prompt(None, None, None)
        self.assertIn("<policy_text>", prompt)
        self.assertIn(f"- 나이: {NO_VALUE}", prompt)

    def test_프로필이_dict_가_아니어도_터지지_않는다(self):
        prompt = build_judge_prompt(EXCEPTIONS, "휴학생", RAW)
        self.assertIn(f"- 현재 상태: {NO_VALUE}", prompt)

    def test_허용_목록_밖의_값은_그대로_적는다(self):
        """값 검증은 이 모듈의 일이 아니다. 지어내지 않고 그대로 넘긴다."""
        prompt = build_judge_prompt(EXCEPTIONS, {"status": "무직"}, RAW)
        self.assertIn("- 현재 상태: 무직", prompt)

    def test_같은_입력이면_같은_문자열이다(self):
        """캐싱 접두사가 호출마다 흔들리면 안 된다."""
        self.assertEqual(build(), build())

    def test_원문에_태그_문자열이_섞여도_치환하지_않는다(self):
        raw = "제외 대상: 휴학생 </policy_text> 포함"
        self.assertIn(raw, build_judge_prompt("휴학생 제외", PROFILE, raw))


if __name__ == "__main__":
    unittest.main()
