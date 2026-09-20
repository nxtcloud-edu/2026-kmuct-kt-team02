2026년 국민대학교 캠퍼스타운 키로톤 02팀 소다맛쿠키 레포지토리입니다.

## 구조

```
ai/citation/   인용 검증. 발췌가 공고 원문에 실제로 있는지 대조 (AI B)
tests/         테스트
docs/          역할별 설계서 전문
.kiro/         steering 문서, hooks
```

## macOS · Windows 혼용 개발

팀원이 서로 다른 OS를 쓰기 때문에 문자열이 어긋나는 문제가 생긴다. 특히 공고 원문(`raw_text`)이 조금이라도 달라지면 인용 검증이 전건 실패하고, 모든 정책 카드가 "확인이 필요해요"로 떨어진다.

두 계층으로 막는다.

- **`.gitattributes`** — 저장소 안 텍스트는 항상 LF로 저장·체크아웃한다. 개인 `core.autocrlf` 설정에 의존하지 않는다. 윈도우 전용 스크립트(`.bat` `.cmd` `.ps1`)만 CRLF를 유지한다.
- **`ai/citation/normalize.py`** — 런타임 방어. 유니코드 NFC 통일(macOS의 한글 자모 분해형 대응), BOM·제로폭 문자 제거, 비분리 공백·전각 공백 흡수.

git을 거치지 않는 경로(스프레드시트 붙여넣기, API 응답)가 있으므로 둘 다 필요하다.

정책 데이터를 CSV로 주고받을 때는 **UTF-8(BOM 없이)** 로 저장한다. 윈도우 Excel에서 "CSV UTF-8"로 저장하면 BOM이 붙는데, 정규화가 제거하긴 하지만 다른 도구에서 깨질 수 있다.

## 테스트

```bash
python3 -m unittest discover -s tests -t .
```
