# 09. 값·이름 결정 기록

## 확정값

공개 API, SSE, 프론트 계약, 서버 DTO가 공유하는 식별자와 enum 값은 영문 `snake_case`로 고정한다.

| 개념 | 확정값 |
| --- | --- |
| 조건 요약 키 | `name` |
| 조건 결과 | `met`, `unmet`, `unknown` |
| 조건 판정 주체 | `rule`, `ai` |
| 정책 상태 | `likely`, `check`, `unlikely` |
| 프로필·후속 질문 항목 | 영문 `snake_case` |
| 예정 기준 | `planned`, `current` |
| 조건부 확인 불가 문구 | `공고 확인 필요` |

화면에 보여 주는 레이블과 안내 문구만 한국어다. 값이나 키를 한국어 문구로 대체하지 않는다.

## 결정 근거

- `server/schemas.py`가 strict Pydantic DTO와 영문 enum을 사용한다. 다른 표기를 넘기면 조용한 보정 대신 검증 실패가 난다.
- 프론트의 상태 비교·CSS·SSE dispatch는 안정적인 기계값을 필요로 한다. 화면 문구 변경이 wire 값 변경으로 번지면 안 된다.
- `name`은 서버 조건 모델과 AI B 인용 검증 모듈의 공개 형태가 사용하는 유일한 키다. `summary`는 호환 alias로도 받지 않는다.
- `planned_basis`는 프로필 필드가 아니라 세션 기준값이다. `status="planned"`처럼 프로필 enum 밖 값을 넣지 않는다.

## 적용 규칙

- AI A/B와 rules adapter는 서버로 넘기기 전에 이 표의 값만 만든다.
- 서버는 값 변환을 추측으로 수행하지 않는다. strict 검증에 실패한 AI 출력은 안전한 fallback과 `error → done`으로 처리한다.
- DTO·SSE·프론트 타입·문서가 동시에 바뀌어야 하는 값 변경은 이 문서를 먼저 수정하고 계약 테스트를 갱신한다.
