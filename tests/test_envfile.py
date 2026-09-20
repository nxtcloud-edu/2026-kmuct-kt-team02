""".env 읽기 테스트 (ai/envfile.py).

여기서 지키려는 계약
  - **환경변수가 파일보다 이긴다.** 파일이 환경변수를 덮어쓰면 배포 환경 설정이 저장소에
    남은 파일 때문에 조용히 바뀐다. 그 실패는 "왜 다른 키로 호출되지"로 나타난다
  - 값이 비어 있으면 채우지 않은 자리로 본다 (``.env.example`` 을 복사해 쓰는 경우)
  - 어떤 파일 내용에도 예외를 던지지 않는다. 파일 문제가 서버를 못 뜨게 하면 안 된다
  - **값을 절대 돌려주지 않는 진단 경로가 있다** (``loaded_names``). 값을 찍으면 그 자리가
    곧 키 유출 지점이 된다

파일을 임시 디렉터리에 만들어 검사한다. 저장소 루트의 실제 ``.env`` 를 읽지 않는다 —
개발자 로컬 파일 때문에 통과하거나 실패하는 테스트가 되면 안 된다.
"""

import os
import tempfile
import unittest
from pathlib import Path

from ai import envfile, gateway


class TestParse(unittest.TestCase):
    """``.env`` 한 줄씩 읽는 규칙."""

    def test_흔한_형태를_모두_읽는다(self):
        cases = (
            ("기본", "API_KEY=sk-1", {"API_KEY": "sk-1"}),
            ("등호 앞뒤 공백", "API_KEY = sk-1", {"API_KEY": "sk-1"}),
            ("겹따옴표", 'API_KEY="sk-1"', {"API_KEY": "sk-1"}),
            ("홑따옴표", "API_KEY='sk-1'", {"API_KEY": "sk-1"}),
            ("export 붙음", "export API_KEY=sk-1", {"API_KEY": "sk-1"}),
            ("빈 값", "API_KEY=", {"API_KEY": ""}),
        )
        for label, text, expected in cases:
            with self.subTest(case=label):
                self.assertEqual(envfile.parse(text), expected, label)

    def test_주석과_빈_줄은_건너뛴다(self):
        text = "# 설명\n\n  # 들여쓴 주석\nAPI_KEY=sk-1\n"
        self.assertEqual(envfile.parse(text), {"API_KEY": "sk-1"})

    def test_값_안의_샵을_주석으로_보지_않는다(self):
        """키에 ``#`` 이 들어갈 수 있다. 값을 잘라내면 인증 오류의 원인을 못 찾는다."""
        self.assertEqual(envfile.parse("API_KEY=sk-a#b"), {"API_KEY": "sk-a#b"})

    def test_이상한_줄에도_예외가_없다(self):
        for label, text in (
            ("등호 없음", "API_KEY"),
            ("이름 없음", "=sk-1"),
            ("빈 문자열", ""),
            ("None 같은 입력", None),
        ):
            with self.subTest(case=label):
                self.assertIsInstance(envfile.parse(text), dict, label)


class TestEnvironmentWins(unittest.TestCase):
    """환경변수가 파일보다 이긴다."""

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / ".env"
        self.path.write_text("API_KEY=from-file\nLLM_MODEL=model-from-file\n", encoding="utf-8")
        envfile.reset_cache()
        self.addCleanup(self.dir.cleanup)
        self.addCleanup(envfile.reset_cache)

    def test_환경변수가_있으면_그것을_쓴다(self):
        merged = envfile.merged({"API_KEY": "from-env"}, path=self.path)
        self.assertEqual(merged["API_KEY"], "from-env")

    def test_환경변수에_없는_것만_파일에서_채운다(self):
        merged = envfile.merged({"API_KEY": "from-env"}, path=self.path)
        self.assertEqual(merged["LLM_MODEL"], "model-from-file")

    def test_환경변수가_빈_문자열이면_파일을_쓴다(self):
        """``.env.example`` 을 복사해 쓰면 빈 값이 남는다."""
        merged = envfile.merged({"API_KEY": "   "}, path=self.path)
        self.assertEqual(merged["API_KEY"], "from-file")

    def test_apply_to_environ_이_기존_값을_덮지_않는다(self):
        os.environ["API_KEY"] = "already-set"
        self.addCleanup(os.environ.pop, "API_KEY", None)
        self.addCleanup(os.environ.pop, "LLM_MODEL", None)

        filled = envfile.apply_to_environ(self.path)
        self.assertEqual(os.environ["API_KEY"], "already-set", "환경변수를 덮어썼다")
        self.assertNotIn("API_KEY", filled)
        self.assertIn("LLM_MODEL", filled)


class TestNoSecretLeak(unittest.TestCase):
    """값을 돌려주지 않는 진단 경로가 있어야 한다."""

    def test_loaded_names_는_이름만_준다(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("API_KEY=sk-super-secret\nEMPTY=\n", encoding="utf-8")
            envfile.reset_cache()
            names = envfile.loaded_names(path)
            self.assertIn("API_KEY", names)
            self.assertNotIn("EMPTY", names, "빈 값을 채워진 것으로 셌다")
            for name in names:
                self.assertNotIn("sk-super-secret", name)
        envfile.reset_cache()


class TestMissingFile(unittest.TestCase):
    """파일이 없거나 깨져도 서버는 떠야 한다."""

    def test_파일이_없으면_빈_dict(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(envfile.read(Path(tmp) / "없는파일"), {})
            self.assertIsNone(envfile.find_file(Path(tmp)))
        envfile.reset_cache()

    def test_디렉터리를_넘겨도_예외가_없다(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(envfile.read(Path(tmp)), {})
        envfile.reset_cache()

    def test_BOM_이_붙어도_읽는다(self):
        """Windows 편집기가 붙인다."""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("API_KEY=sk-1\n", encoding="utf-8-sig")
            envfile.reset_cache()
            self.assertEqual(envfile.read(path).get("API_KEY"), "sk-1")
        envfile.reset_cache()


class TestGatewayUsesFile(unittest.TestCase):
    """``gateway`` 가 ``.env`` 를 본다. 이것이 이 기능의 목적이다."""

    def test_env_를_명시하면_파일을_보지_않는다(self):
        """테스트가 개발자 로컬 ``.env`` 에 영향받으면 안 된다."""
        self.assertIsNone(gateway.load_config({}))
        self.assertEqual(
            set(gateway.missing_settings({})),
            {gateway.ENV_API_KEY[0], gateway.ENV_MODEL[0]},
        )

    def test_파일에_값이_있으면_설정이_생긴다(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("API_KEY=sk-1\nLLM_MODEL=bedrock-haiku\n", encoding="utf-8")
            envfile.reset_cache()
            merged = envfile.merged({}, path=path)
            config = gateway.load_config(merged)
            self.assertIsNotNone(config)
            self.assertEqual(config.model, "bedrock-haiku")
        envfile.reset_cache()


if __name__ == "__main__":
    unittest.main()
