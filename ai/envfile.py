""".env 파일 읽기.

**왜 있는가.** 설정은 원래 환경변수로만 읽는다(``ai/gateway.py`` 의 ``load_config``).
그런데 터미널을 새로 열면 매번 다시 설정해야 하고, 데모 중에 그걸 빼먹으면 모델이 조용히
건너뛰어진다. 그래서 저장소 루트의 ``.env`` 를 읽어 **환경변수에 없는 값만** 채운다.

**환경변수가 항상 이긴다.** 파일이 환경변수를 덮어쓰면, 배포 환경에서 설정한 값이 저장소에
남은 파일 때문에 조용히 바뀐다. 그 실패는 "왜 다른 키로 호출되지"로 나타나고 원인을 찾기
어렵다. 파일은 **비어 있는 자리만** 채운다.

**``python-dotenv`` 를 쓰지 않는 이유.** 형식이 단순해서 직접 읽는 편이 짧고, 의존성을
하나 줄이면 고정할 버전도 하나 줄어든다. 대신 그 패키지가 지원하는 것 중 일부는 여기
없다(변수 확장 ``${VAR}``, 여러 줄 값). 필요해지면 그때 바꾼다. 지금 넣어야 하는 값은
키와 모델 별칭 두 개다.

**키를 로그에 찍지 않는다.** 이 모듈은 어떤 경로에서도 값을 출력하지 않는다. 무엇을 읽었는지
알아야 할 때는 ``loaded_names()`` 로 **이름만** 본다.

``.env`` 는 ``.gitignore`` 에 있다(``.env``, ``.env.*``, 예시만 ``!.env.example``).
그래도 커밋 전에 한 번 확인하는 습관이 안전하다. GitHub 에 올라간 키는 몇 분 안에
수집된다(대회 API 가이드 4장).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple

#: 찾을 파일 이름. 앞에 있는 것이 이긴다.
FILE_NAMES: Tuple[str, ...] = (".env", ".env.local")

#: 값에서 벗길 감싸는 문자.
_QUOTES = ("\"", "'")

# 한 번 읽으면 기억한다. 같은 프로세스에서 턴마다 파일을 다시 읽을 이유가 없다.
_cache: Optional[Dict[str, str]] = None
_cache_source: Optional[Path] = None


def _repo_root() -> Path:
    """저장소 루트. 이 파일이 ``<root>/ai/envfile.py`` 라는 사실에서 구한다."""
    return Path(__file__).resolve().parent.parent


def find_file(start: Optional[Path] = None) -> Optional[Path]:
    """읽을 파일을 찾는다. 없으면 ``None``.

    저장소 루트에서 시작해 위로 올라가지 않는다. 위로 올라가면 다른 프로젝트의 ``.env`` 를
    잘못 읽을 수 있고, 그 값으로 모델을 부르면 남의 키로 호출하는 셈이 된다.
    """
    base = start if start is not None else _repo_root()
    for name in FILE_NAMES:
        candidate = base / name
        if candidate.is_file():
            return candidate
    return None


def parse(text: str) -> Dict[str, str]:
    """``.env`` 내용을 dict 로. **예외를 던지지 않는다.**

    견디는 형태
        KEY=value
        KEY = value          (앞뒤 공백 무시)
        KEY="value"          (감싼 따옴표 벗김)
        export KEY=value     (셸에서 복사해 붙인 경우)
        # 주석              (줄 전체 무시)
        KEY=                 (빈 값. 채우지 않은 자리로 본다)

    ``#`` 을 값 안에서 주석으로 보지 않는다. 키에 ``sk-...#...`` 같은 문자가 들어갈 수
    있고, 값의 일부를 잘라내면 인증 오류의 원인을 찾기 어렵다. 주석은 **줄 맨 앞**에만
    있는 것으로 본다.
    """
    values: Dict[str, str] = {}
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        if not name:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in _QUOTES:
            value = value[1:-1]
        values[name] = value
    return values


def read(path: Optional[Path] = None, *, refresh: bool = False) -> Dict[str, str]:
    """파일을 읽어 dict 로. 없으면 빈 dict. **예외를 던지지 않는다.**"""
    global _cache, _cache_source

    target = path if path is not None else find_file()
    if target is None:
        return {}
    if not refresh and _cache is not None and _cache_source == target:
        return dict(_cache)

    # ``utf-8-sig`` 로 읽는다. BOM 이 있으면 벗기고 없으면 ``utf-8`` 과 같게 동작한다.
    # 그냥 ``utf-8`` 로 읽으면 BOM 이 첫 글자로 남아 **첫 줄의 키 이름이 ``\ufeffAPI_KEY``
    # 가 된다.** 그러면 그 값을 못 찾고, 오류 없이 "키를 안 넣은 것"처럼 보인다. Windows
    # 편집기(메모장 등)가 BOM 을 붙이므로 실제로 일어나는 실패다.
    try:
        text = target.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = target.read_text(encoding="cp949")
        except Exception:  # noqa: BLE001 - 파일 문제가 서버를 못 뜨게 하지 않는다
            return {}
    except Exception:  # noqa: BLE001
        return {}

    _cache = parse(text)
    _cache_source = target
    return dict(_cache)


def apply_to_environ(
    path: Optional[Path] = None, *, refresh: bool = False
) -> Tuple[str, ...]:
    """``.env`` 값을 ``os.environ`` 의 **빈 자리에만** 채운다.

    돌려주는 것은 **채운 이름 목록**이다. 값은 절대 돌려주지 않는다.

    이미 값이 있는 이름은 건드리지 않는다(모듈 독스트링 "환경변수가 항상 이긴다").
    빈 문자열도 "채우지 않은 자리"로 보고 덮어쓴다 — ``.env.example`` 을 복사해 쓰면
    빈 값이 남기 때문이다.
    """
    filled: List[str] = []
    for name, value in read(path, refresh=refresh).items():
        if not value:
            continue
        current = os.environ.get(name)
        if current is None or not current.strip():
            os.environ[name] = value
            filled.append(name)
    return tuple(filled)


def merged(
    env: Optional[Mapping[str, str]] = None,
    *,
    path: Optional[Path] = None,
    refresh: bool = False,
) -> Dict[str, str]:
    """환경변수와 파일을 합친 매핑. **환경변수가 이긴다.**

    ``os.environ`` 을 건드리지 않고 읽기만 하는 경로다. 설정을 읽는 함수가 이것을 쓰면
    프로세스 전역 상태를 바꾸지 않아도 되고, 테스트도 서로 간섭하지 않는다.
    """
    result = read(path, refresh=refresh)
    source = env if env is not None else os.environ
    for name, value in source.items():
        if value is not None and str(value).strip():
            result[name] = str(value)
    return result


def loaded_names(
    path: Optional[Path] = None, *, refresh: bool = False
) -> Tuple[str, ...]:
    """파일에 값이 채워져 있는 이름 목록. **값은 돌려주지 않는다.**

    "키를 넣었는데 왜 안 되지"를 확인할 때 쓴다. 값을 찍으면 그 자리가 곧 키 유출 지점이 된다.
    """
    return tuple(name for name, value in read(path, refresh=refresh).items() if value)


def source_path(path: Optional[Path] = None) -> Optional[str]:
    """실제로 읽은 파일 경로. 어느 파일이 쓰였는지 확인할 때."""
    target = path if path is not None else find_file()
    return str(target) if target is not None else None


def reset_cache() -> None:
    """읽어 둔 내용을 버린다. 테스트와 파일을 고친 뒤에 쓴다."""
    global _cache, _cache_source
    _cache = None
    _cache_source = None


__all__ = [
    "FILE_NAMES",
    "find_file",
    "parse",
    "read",
    "apply_to_environ",
    "merged",
    "loaded_names",
    "source_path",
    "reset_cache",
]
