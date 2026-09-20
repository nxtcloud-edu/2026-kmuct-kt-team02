# 03. API와 스트리밍 계약

> 출처: PRD v2 11장, 역할별 설계서 4장. 소유: 백엔드B. **10:30 확정 후 변경 시 전원 공지.**
> 이 문서는 계약이다. 구현은 이 명세를 따르고, 바꿔야 하면 백엔드B에게 알린다.
> 필드 이름은 모두 snake_case, 날짜는 `YYYY-MM-DD`, 시간 계산은 한국 시간 기준.
> 프론트는 이 문서만 보고 목업 데이터를 만들어 화면을 먼저 완성한다.

## 1. 엔드포인트 요약

| 방식 | 경로 | 설명 | 시간 목표 |
| --- | --- | --- | --- |
| GET | `/health` | 서버와 외부 의존성 연결 상태 점검 | 즉시 |
| POST | `/session` | 온보딩 폼 제출. 세션 번호와 규칙 기반 1차 결과 | 1초 이내 |
| POST | `/chat` | 메시지 또는 후속 질문 답변. 스트리밍 응답 | 첫 이벤트 즉시, 전체 20초 상한 |
| GET | `/policies/{id}` | 정책 상세 + 현재 세션 기준 판정 결과 | 1초 이내 |
| PATCH | `/session/{id}/profile` | 프로필 직접 수정, 규칙 결과 재계산 | 1초 이내 |

인증 없음. 세션 번호로만 식별한다. 개인 식별 정보를 받지 않으므로 로그인 개념이 없다.

### 1-1. GET /health

운영 점검 응답은 아래 상태만 반환한다. 자격 증명, 환경 변수 원문, LLM 공급자·모델명은 반환하지 않는다.

| 필드 | 내용 |
| --- | --- |
| `status` | 서버가 요청을 처리하면 `ok` |
| `verified_policy_count` | 연결된 정책 공급자의 검수 완료 정책 수. 미연결이면 `null` |
| `policy_dependency` | `connected` 또는 `unconnected` |
| `llm_adapter_configured` | LLM adapter가 설정됐는지 여부만 표시하는 boolean |

정책 공급자가 아직 연결되지 않았어도 `/health` 자체는 200을 반환하고 `policy_dependency=unconnected`로 명시한다.

## 2. POST /session

**받는 것** — 서울 거주를 전제로 한 온보딩 입력. `region`은 요청에서 받지 않고 서버 내부 Profile에 `seoul`로 고정한다.

| 필드 | 필수 | 허용 값 |
| --- | --- | --- |
| `age` | 필수 | 15~39 정수 |
| `district` | 필수 | 서울 25개 자치구명. 비움 또는 `null` 불가 |
| `status` | 필수 | `enrolled`, `on_leave`, `final_semester`, `job_seeking`, `employed` |
| `categories` | 필수 | `scholarship`, `living`, `job`, `culture`, `housing`, `all` 중 하나 이상 |
| `income_bracket` | 선택 | `under_50`, `50_100`, `100_150`, `over_150`, `unknown` (기본값 `unknown`) |

클라이언트가 `region`을 보내면 계약에 없는 필드로 입력 오류를 반환한다. 응답의 정리된 `profile`에는 항상 `region: "seoul"`이 들어간다.

**돌려주는 것**

| 필드 | 내용 |
| --- | --- |
| `session_id` | 세션 번호 |
| `profile` | 정리된 프로필 (기본 항목 + 추가 항목 묶음) |
| `policies` | 규칙 기반 정책 판정 결과 목록. 4장 참고. `likely`·`check` 상위 5개 |
| `hidden_unlikely_count` | 접힌 영역에 들어갈 `unlikely` 개수 |
| `followup` | 첫 후속 질문. 없으면 비움. 6장 참고 |

**검증**: 필수 항목 누락 또는 허용 값 밖 → 입력 오류

## 3. POST /chat

**받는 것**

| 필드 | 필수 | 내용 |
| --- | --- | --- |
| `session_id` | 필수 | `/session`에서 발급받은 세션 UUID |
| `message` | 필수 | 비어 있지 않은 사용자 메시지 |
| `client_message_id` | 필수 | 프론트가 메시지별로 부여한 식별자 |

서버는 세션 존재·만료를 스트림을 열기 전에 확인한다. 세션이 없거나 만료됐으면 SSE가 아닌 공통 오류 JSON과 HTTP 404를 반환한다.
메시지는 PII 마스킹을 거친 값만 내부 Chat pipeline에 전달하며 원문은 세션이나 로그에 저장하지 않는다.

후속 질문 답변이 들어오면 메시지 해석(AI A) 단계를 건너뛰고 값을 바로 프로필에 넣는다.
이 한 줄에 제약 두 개가 따라온다. 해석을 건너뛴다는 것은 값 검증도 건너뛴다는 뜻이다.

- **후속 질문 선택지의 값은 반드시 그 항목의 허용 값이어야 한다.** 허용 값 밖 글자가 오면 규칙 엔진이 모르는 값이 그대로 프로필에 박힌다. 답을 받은 것처럼 보이지만 판정은 그대로 미확인이라 **건너뛰기보다 나쁘다.** 그래서 직접 입력(`allow_free_text`)은 타이핑한 글자가 곧 허용 값인 `district` 에서만 연다 (`ai/conversation/README.md` 5장).
- **프로필 항목이 아닌 질문의 답은 프로필에 넣지 않는다.** 기준 시점을 묻는 확인 질문의 `field` 는 `planned_basis` 이고 프로필 항목이 아니다 (`ai/conversation/questions.py` 의 `PLANNED_BASIS_FIELD`). 이 답은 세션이 따로 기억해 다음 해석의 기준 시점으로 쓴다 (11장). 바뀔 항목 이름(`status` 등)으로 물으면 `status="planned"` 라는 허용 값 밖 값이 프로필에 들어간다.

**돌려주는 것**: 스트리밍 이벤트. 5장.

**검증**: 세 필드 누락 또는 빈 문자열 → 입력 오류. 세션 없음·만료 → `session_expired` 404.

## 4. 정책 판정 결과 항목

`/session`, `/chat`의 `policies` 이벤트, `/policies/{id}`가 모두 같은 모양을 쓴다.

| 필드 | 내용 | 채우는 쪽 |
| --- | --- | --- |
| `policy_id` | 정책 번호 (예: `SEOUL-003`) | 백엔드A |
| `title` | 정책명. 데이터의 값 그대로 | 백엔드A |
| `agency` | 담당 기관 | 백엔드A |
| `categories` | 분야 목록 | 백엔드A |
| `status` | `likely`, `check`, `unlikely` | 백엔드B (규칙 + AI 결과 합산) |
| `status_label` | 화면 문구. 코드가 붙인다 | 백엔드B |
| `benefit` | 혜택 요약 | 백엔드A |
| `conditions` | 조건 목록. 아래 4-1 | 백엔드A + AI B |
| `conditional_note` | 조건부 문장. `check`일 때만, 나머지는 비움 | AI B (고정 형식) |
| `deadline` | 마감 정보. 아래 4-2 | 백엔드A |
| `documents` | 필요 서류 목록 | 백엔드A |
| `steps` | 신청 단계 목록 | 백엔드A |
| `source_url` | 공고 원문 주소 | 백엔드A |
| `apply_url` | 신청 페이지 주소 | 백엔드A |
| `checked_at` | 최종 확인일 | 백엔드A |
| `data_status` | `verified`, `recheck`, `closed`, `upcoming` | 백엔드A |

### 4-1. 조건 항목

| 필드 | 내용 |
| --- | --- |
| `name` 또는 `summary` | 조건 요약. 20자 이내 명사형 (예: 타 청년 지원금 중복 불가). **이름이 갈려 있다. 아래 참고** |
| `result` | `met`, `unmet`, `unknown` |
| `judged_by` | `rule` 또는 `ai` |
| `excerpt` | 공고 원문 발췌 10~150자. 인용 검증 통과한 것만 |
| `source_url` | 발췌의 출처 |
| `footnote_id` | 각주 번호. 한 응답 안에서 1부터 순서대로 |
| `needed_field` | `unknown`일 때만. 프로필 항목 이름(기본 항목과 추가 항목 모두) 또는 "공고 확인 필요" |

**조건 요약 키 이름은 12:00 통합 때 확정한다.** 이 문서는 `name` 으로 정했는데 구현 두 곳이 `summary` 를 쓴다. `ai/citation/verify.py` 의 `verify_conditions` 는 검증 실패 시 `item["summary"]` 를 비우고 제거 기록에도 `summary` 를 담는다. `ai/judgment/evaluate.py` 는 판정기가 돌려줄 형식을 `summary`, `result`, `excerpt`, `needed_field` 로 적어 둔다 (설계서 6-3). 확정하지 않은 채 두면 **서버는 `name` 을 읽고 AI B 는 `summary` 를 채우는 상태가 되어, 조건 요약이 빈 칸으로 화면에 나가고 오류는 나지 않는다.** 표는 그대로 그려지므로 통합 중에 알아채기 어렵다. 어느 이름으로 모을지는 백엔드B·AI B 가 함께 정한다. 여기서 한쪽으로 확정하지 않는 이유는, 이 문서만 고치면 코드 두 파일이 조용히 어긋난 채 남기 때문이다.

`needed_field` 는 후속 확인이 가능한 프로필 항목을 담는다. `income_bracket`은 선택 입력이라 `unknown`으로 남을 수 있다. `district`는 새 세션에서 필수지만 이전·불완전 세션을 방어적으로 처리할 수 있도록 값 표에는 유지한다. 서버는 현재 Profile이 완전하면 이미 확정된 항목을 다시 묻지 않는다.

### 4-2. 마감 항목

| 필드 | 내용 |
| --- | --- |
| `apply_start` | 접수 시작일 |
| `apply_end` | 마감일. 상시 접수면 비움 |
| `d_day` | 남은 일수. 코드가 계산 |
| `badge` | 화면 배지 문구 (오늘 마감 / 마감 임박 D-n / D-n / 상시 접수 / 접수 예정 (M.D 시작)) |
| `is_imminent` | 마감 7일 이내 여부 |

### 4-3. 규칙

- `conditions` 순서는 화면 표시 순서와 같다: `unmet` → `unknown` → `met`
- 소득 상한이 비어 있는 정책은 소득 조건을 `conditions`에 넣지 않는다.
- `conditional_note`는 `status`가 `check`일 때만 채운다.
- 인용 검증에 실패한 조건은 `result`를 `unknown`으로 바꾸고 `excerpt`를 비운다.
- `source_url` 또는 `checked_at`이 없는 정책은 응답에 넣지 않는다 (FR12).
- `footnote_id`는 AI A가 답변 문장에 붙이는 각주 번호와 같아야 한다.

## 5. 스트리밍 이벤트

응답은 `text/event-stream`이며 각 SSE의 `data`는 공통 envelope 세 필드를 쓴다: `request_id`, `seq`, `payload`.

- `request_id`는 서버가 요청마다 만든 UUID이며 한 스트림 안에서 동일하다.
- `seq`는 1부터 시작해 이벤트마다 1씩 증가한다.
- `payload`만 이벤트별 모델을 따른다.

| 이벤트 | 언제 | `payload` 내용 | 프론트 반응 |
| --- | --- | --- | --- |
| `status` | 단계가 바뀔 때 | 진행 단계 (`searching`, `checking`, `summarizing`) | 진행 단계 표시 갱신 |
| `profile_update` | 프로필이 바뀔 때 | 바뀐 항목과 값, 안내 문구, 갱신된 프로필. 아래 5-3 | 프로필 바 갱신 + 대화에 안내 한 줄 |
| `policies` | 규칙 결과 직후, AI 판정 후 한 번 더 | 정책 판정 결과 목록, 접힌 `unlikely` 개수 | AI 설명보다 먼저 카드 갱신. 기존 카드는 자리 유지하고 상태만 바꿈, 새 카드는 뒤에 추가 |
| `answer_delta` | 답변 생성 중 반복 | 답변 조각 (문장 단위) | 답변 본문에 이어 붙임 |
| `footnotes` | 답변 완료 시 | 각주 목록. 아래 5-1 | 각주 번호를 발췌와 연결 |
| `followup` | 물을 것이 있을 때 | 6장 | 후속 질문 카드 표시 |
| `related` | P1 | 관련 질문 칩 최대 3개 (번호 + 문구). 아래 5-2 | 칩 표시 |
| `error` | 부분 실패 | 오류 코드와 사용자 문구 | 카드 유지 + 오류 문구 + 다시 시도 버튼 |
| `done` | 정상 또는 복구 가능한 fallback 종료 | 총 소요 시간 | 진행 단계 숨김, 입력창 활성 |

기본 순서는 `status → profile_update(선택) → policies → status → answer_delta(반복) → footnotes → followup(선택) → related(선택) → done`이다.
`policies`는 첫 `answer_delta`보다 반드시 먼저 보내고, 정상 또는 복구 가능한 fallback은 `done`으로 끝낸다.

응답에 `Cache-Control: no-cache`를 넣고, Nginx 계열 프록시 환경의 버퍼링 방지를 위해 `X-Accel-Buffering: no`를 함께 보낸다.
클라이언트 연결이 끊기면 현재 pipeline task를 취소하고 남은 이벤트를 만들지 않는다.

### 5-1. 각주 항목 (`footnotes`)

이벤트 본문은 `footnotes` 키로 감싼 목록이다. 항목 하나는 아래 여섯 필드다.

| 필드 | 내용 |
| --- | --- |
| `footnote_id` | 각주 번호. 4-1의 `footnote_id` 와 같은 값 |
| `policy_id` | 각주가 가리키는 정책 번호 |
| `excerpt` | 공고 원문 발췌. 인용 검증 통과한 것만 |
| `agency` | 담당 기관 |
| `checked_at` | 최종 확인일 |
| `source_url` | 발췌의 출처 |

**번호 키는 `footnote_id` 로 확정한다.** 4-1이 이미 그 이름을 쓰므로 5장을 맞추는 쪽이 고칠 곳이 적고, 두 장이 같은 값을 다른 이름으로 부르면 각주 번호가 어긋났을 때 어느 쪽이 틀렸는지 가릴 수 없다. `ai/conversation/answer.py` 의 `known_footnote_ids` 는 지금 `footnote_id` 와 `id` 를 모두 읽는다(그 함수 독스트링은 5장을 `id` 로 읽어 두었다). 이름을 하나로 고정해 두지 않으면 두 이름이 계속 살아남는다.

**감싼 채로 넘겨도 된다.** 서버가 AI A 에 각주를 넘길 때 `{"footnotes": [...]}` 이벤트 본문을 그대로 넘겨도 되고, 목록만 넘겨도 되고, 번호 집합만 넘겨도 된다. `known_footnote_ids` 가 `footnotes`·`items`·`data` 키를 벗기고 번호 집합과 번호를 키로 쓴 묶음까지 받는다. 이벤트 본문을 재사용하는 것이 서버에서 가장 자연스러운 호출이라 그 경로를 계약에 적어 둔다. 적어 두지 않으면 서버가 목록만 넘기려고 본문을 다시 풀어 쓰는 코드를 만든다.

**각주 목록을 넘기지 않으면 답변이 사라진다.** 목록이 비어 있으면 쓸 수 있는 번호가 하나도 없는 것이 되고, 판정·조건·금액·날짜가 든 문장이 전부 "각주 없는 판정 문장"으로 취급돼 정리 단계에서 삭제된다 (`ai/conversation/README.md` 6장 근거 규칙). 고정 문구는 항상 붙으므로 결과는 "최종 신청 전 공식 공고에서 다시 확인하세요." **한 줄로 줄어들고, 오류는 나지 않는다.** `ai/conversation/pipeline.py` 의 `finish_turn` 을 거치면 이 상태를 `empty_after_sanitize` 로 잡아 `answer_failed`(10장)로 바꾸지만, `answer.sanitize` 만 직접 부르면 그 한 줄이 정상 답변으로 나간다.

### 5-2. 관련 질문 칩 항목 (`related`)

이벤트 본문은 `chips` 키로 감싼 목록이다 (`ai/conversation/related.py` 의 `to_event`). 칩 하나는 두 필드다.

| 필드 | 내용 |
| --- | --- |
| `id` | 칩 번호 (예: `income_check_howto`). 화면에 쓰지 않는다 |
| `text` | 화면에 그대로 나가는 칩 문구. 고정 문구 표에서 온 값이다 |

**프론트가 화면에 쓰는 것은 `text` 뿐이다.** `id` 는 사용자가 칩을 눌렀을 때 되돌려 보낼 값이다. 서버는 그 번호를 세션의 "이미 보여준 칩"에 넣고(11장), 다음 턴에 제외 목록으로 넘긴다. 이렇게 해야 "이미 보여준 칩과 누른 칩을 다시 띄우지 않는다"(`ai/conversation/README.md` 7장)를 지킬 수 있다. 문구가 아니라 번호로 거르는 이유는, 칩 문구를 다듬었을 때 이미 보여준 칩이 새 칩으로 되살아나는 것을 막기 위해서다.

### 5-3. 프로필 변경 항목 (`profile_update`)

이벤트 본문은 세 키다 (`ai/conversation/interpret.py` 의 `MergeResult.to_profile_update`).

| 필드 | 내용 |
| --- | --- |
| `changes` | 실제로 바뀐 항목 목록. 아래 표 |
| `notice` | 대화에 붙일 안내 한 줄. 변경 안내와 소득 구간 안내를 공백으로 이어 붙인 값 |
| `profile` | 갱신된 프로필 전체 |

변경 항목 하나:

| 필드 | 내용 |
| --- | --- |
| `field` | 바뀐 프로필 항목 이름 |
| `before` | 바뀌기 전 값. 없던 항목이면 비움 |
| `after` | 바뀐 뒤 값 |
| `label` | `after` 의 화면 문구. `categories` 는 비움 |
| `notice` | 이 변경에 붙는 안내 문구. 고정 문구 표에서 온 값이고 AI가 만들지 않는다 |

**바뀐 것이 없으면 이벤트를 보내지 않는다.** 이벤트가 오면 프로필 바가 갱신되고 대화에 안내 한 줄이 붙으므로, 변경 없는 이벤트는 사용자에게 바뀌지 않은 것이 바뀐 것처럼 보이게 한다. `before` 와 `after` 를 함께 담는 이유는 화면이 "무엇이 무엇으로 바뀌었는지" 보여주기 때문이다.

## 6. 후속 질문에 담기는 것

| 필드 | 내용 |
| --- | --- |
| `field` | 묻는 항목 이름 (`docs/01-glossary-profile.md` 3장) |
| `question` | 질문 문구. 고정 문구 표에서 가져옴 |
| `reason` | 이유 한 줄. 영향받는 정책명과 개수를 채워 넣음 |
| `options` | 선택지 목록 (값 + 화면 문구) |
| `allow_free_text` | 직접 입력 허용 여부 |
| `allow_skip` | 건너뛰기 허용 여부 (항상 허용) |

질문 문구와 이유 문구는 `ai/conversation/README.md`의 고정 문구 표에서 가져온다. AI가 새로 만들지 않는다.

## 7. GET /policies/{id}

`session_id`를 필수 query parameter로 받는다. 서버는 세션의 현재 Profile과 백엔드 A 정책 저장소의 원본 정책을 `RuleEngine.evaluate_policy(profile, policy)`에 넘겨 단건 판정을 받는다. 추천 상위 5개 목록을 검색해 상세를 대신하지 않는다.

응답은 4장의 `PolicyEvaluation`과 같은 형태이며 조건별 판정, 혜택, 마감, 서류, 신청 단계, 담당 기관, `source_url`, `apply_url`, `checked_at`을 포함한다.
`source_url` 또는 `checked_at`이 없는 정책은 상세에 노출하지 않고 `policy_not_found` 404로 처리한다.
세션이 없거나 만료됐으면 `session_expired` 404, 정책이 없으면 `policy_not_found` 404다.

## 8. PATCH /session/{id}/profile

바꿀 필드만 보낸다. 생략한 필드는 유지하고, 빈 요청은 입력 오류다. `region`은 서버 고정 필드라 보낼 수 없다.

| 구분 | 필드 | 명시적 `null` 처리 |
| --- | --- | --- |
| 삭제 불가 | `age`, `district`, `status`, `categories` | 입력 오류 422 |
| 소득 초기화 | `income_bracket` | `unknown`으로 정규화 |
| 삭제 가능 | `housing_type`, `residence_period`, `remaining_semesters`, `job_seeking_period`, `employment_insurance`, `other_benefit`, `household_size`, `last_gpa` | 값을 `null`로 삭제 |

전체 Profile을 Pydantic으로 다시 검증하고 백엔드 A `RuleEngine.evaluate(profile, limit=5)`로 추천을 재계산한 뒤에만 세션 값을 갱신한다.
응답은 `/session`과 같은 `session_id`, 전체 `profile`, `policies`, `hidden_unlikely_count`, `followup` 형태다.
세션이 없거나 만료됐으면 `session_expired` 404, 수정값이 잘못됐으면 `invalid_input` 422다.

## 9. /chat 처리 순서와 시간 예산

| 단계 | 하는 일 | 담당 | 보내는 이벤트 | 예산 |
| --- | --- | --- | --- | --- |
| 1 | 민감정보 마스킹 | 서버 (12장) | (마스킹 시) 안내 표시 | 즉시 |
| 2 | 진행 단계 "정책 찾는 중" | 서버 | `status` | 즉시 |
| 3 | AI A 메시지 해석 | AI A | `profile_update` (변경 시) | 3초 |
| 4 | 범위 밖·잡담이면 짧은 답변 후 종료 | AI A 가 판단, 서버가 종료 | `answer_delta`, `done` | — |
| 5 | 규칙 재계산, 후보 선정 | 규칙 엔진 | `policies` (규칙 기반) | 1초 |
| 6 | 진행 단계 "조건 확인 중" | 서버 | `status` | 즉시 |
| 7 | AI B 예외 조건 판정 (후보별 동시 실행) | AI B | — | 정책당 8초 |
| 8 | 인용 검증, 상태 확정 | AI B | `policies` (최종) | 즉시 |
| 9 | 진행 단계 "정리 중" | 서버 | `status` | 즉시 |
| 10 | AI A 답변 작성 | 프롬프트와 정리는 AI A, 모델 호출은 서버 | `answer_delta` 반복, `footnotes` | 첫 문장 3초 |
| 11 | 후속 질문 선택 | AI A | `followup` | 즉시 |
| 12 | 관련 질문 (P1) | AI A | `related` | 즉시 |
| 13 | 종료 | 서버 | `done` | 전체 20초 상한 |

담당 열을 넣은 이유는, 같은 단계를 두 사람이 자기 일로 읽거나 아무도 자기 일로 읽지 않는 것을 막기 위해서다. 10단계가 특히 그렇다. 모델 호출은 `answer_delta` 를 흘리면서 해야 하므로 서버가 하고, 그 앞의 프롬프트 조립과 뒤의 정리·검증은 AI A 가 한다.

**서버가 AI A 쪽에서 부를 것은 `ai/conversation/pipeline.py` 의 네 함수뿐이다.** `interpret_turn`(3~4단계), `build_answer_prompt`(10단계 프롬프트 조립), `finish_turn`(10~12단계 정리·검증·후속 질문·칩), `unknown_items_from`(5·7단계가 내놓는 미확인 항목 목록을 후속 질문 입력으로 변환). 나머지는 이 파일이 안에서 부른다.

**실패 처리**

- 7단계에서 시간을 넘긴 정책은 예외 조건을 전부 `unknown`으로 두고 진행한다.
- 10단계가 실패했지만 복구 가능한 경우 고정 fallback 설명을 `answer_delta`로 보내고 `done`으로 종료한다.
- 전체 20초를 넘기면 그때까지의 결과로 `done`을 보낸다.
- **AI가 전부 실패해도 5단계의 규칙 기반 카드는 이미 화면에 있다.**

## 10. 오류

| 경우 | 코드 | 사용자에게 보일 문구 |
| --- | --- | --- |
| 입력 오류 | `invalid_input` | 입력한 정보를 다시 확인해 주세요 |
| 세션 만료 | `session_expired` | 시간이 지나 처음부터 다시 시작할게요 |
| 정책 없음·노출 제외 | `policy_not_found` | 정책을 찾을 수 없어요 |
| 서버 오류 | `server_error` | 잠시 문제가 생겼어요. 다시 시도해 주세요 |
| 답변 실패 | `answer_failed` | 설명을 불러오지 못했어요. 카드에서 조건을 확인해 주세요 |

문구는 프론트와 공유한다. 서버는 코드를, 프론트는 문구를 담당한다.

## 11. 세션

- 서버 메모리에만 보관. **데이터베이스를 쓰지 않는다.**
- 보관 내용
  - 프로필
  - 물어본 항목과 건너뛴 항목
  - 최근 대화 6턴
  - 현재 표시 중인 정책 목록
  - **이미 보여준 관련 질문 칩 번호** (5-2의 `id`) — 누른 칩과 보여준 칩을 구분하지 않고 한 묶음으로 둔다. 다음 턴에 이 묶음을 제외 목록으로 넘겨야 같은 칩이 다시 뜨지 않는다
  - **기준 시점 선택** (`planned_basis`, 값은 "바뀐 뒤 기준" 또는 "지금 기준") — 프로필 항목이 아니라 프로필에 넣을 수 없고(3장), 다음 턴 해석이 어느 시점 기준으로 판정할지 알아야 한다. 건너뛴 경우도 이 이름으로 기록해 같은 확인 질문을 다시 묻지 않는다
- 30분 동안 요청이 없으면 폐기
- 사용자 메시지 원문과 프로필은 기록에 남기지 않는다

## 12. 민감정보 마스킹 (FR15)

| 대상 | 판단 기준 | 처리 |
| --- | --- | --- |
| 주민등록번호 | 숫자 6자리 + 하이픈 또는 공백 + 숫자 7자리 | 별표로 가리고 안내 표시 |
| 계좌번호 | 하이픈 포함 숫자 10~16자리 연속 | 가리고 안내 표시 |
| 전화번호 | 010으로 시작하는 11자리 | 가림 |

가린 값은 AI 요청에도, 기록에도 남기지 않는다.

## 13. 기록 (지표용)

| 항목 | 용도 |
| --- | --- |
| 단계별 소요 시간 | 성능 지표 |
| 인용 검증에서 제거된 발췌 수 | 근거성 지표 |
| AI 실패·시간 초과 건수 | 안정성 점검 |

## 14. 데모 모드

데모 경로(`docs/06-demo-and-metrics.md`)의 입력별 최종 결과를 저장해 두고, 같은 입력이 오면
저장된 결과를 같은 속도감으로 스트리밍한다. 켜고 끄는 방법은 팀만 안다.
14:30~15:00 데모 경로를 5회 실행해 응답 시간을 기록한다.
