# 09. 값 표기 결정 (영문 vs 한국어)

> 이 문서는 12:00 통합 전에 결정해야 할 한 가지를 다룬다.
> 사실 조사는 끝났다. 결정은 팀이 한다. 권고는 4장에 있다. 결정은 7장에 적는다.

## 1. 한 줄 요약과 왜 급한가

같은 개념에 두 가지 표기가 돌아다닌다. `ai/conversation` 은 영문 snake_case, `ai/judgment` 와 `ai/citation` 은 한국어다.

**예외가 나지 않는다.** 그래서 통합 직전까지 안 보인다. 지금 `python -m pytest -q` 는 291건 전부 통과한다(직접 실행해 확인). 각 모듈은 자기 표기 안에서 완결돼 있어 단독으로는 아무 문제가 없다.

실패 경로:

| 순서 | 무슨 일이 | 화면에는 |
| --- | --- | --- |
| 1 | AI A 가 `{"status": "on_leave"}` 로 프로필 변경을 만든다 | 프로필 바에 "휴학" 표시. 정상으로 보인다 |
| 2 | AI B 판정기는 `profile["현재 상태"]` 를 읽는다. 키가 없어 값이 비어 보인다 | — |
| 3 | 프로필 정보가 없으니 예외 조건을 판정할 수 없다. 전부 `unknown` | — |
| 4 | `unknown` 이 1개 이상이면 판정 상태는 `check` (`docs/01-glossary-profile.md` 4장) | 카드 전부 "확인이 필요해요" |
| 5 | 후속 질문이 계속 나온다. 답해도 같은 자리로 돌아온다 | 동작은 하는데 결론이 없다 |

반대 방향도 있다. AI B 가 `needed_field` 에 `다른 지원 수혜 중` 을 담아 보내면 AI A 는 그것을 **매핑하지 않고 버린다**(`ai/conversation/pipeline.py` 325~329행에 그렇게 쓰여 있다). 이 경로는 이미 동작 중이다. `ai/conversation/__main__.py` 75행이 일부러 그 입력을 넣어 탈락 기록을 보여준다.

AI A 는 이 충돌을 코드에 세 번 적어 놨다: `ai/conversation/fields.py` 11~14행, `ai/conversation/cases.py` 23~38행, `ai/conversation/llm.py` 1152~1155행. 전부 "12:00 통합 때 정한다"로 끝난다. 그 12:00 이 지금이다.

## 2. 현황 표

### 2-1. 항목별 표기

| # | 개념 | 문서가 정한 값 | 코드가 쓰는 값 | 충돌 |
| --- | --- | --- | --- | --- |
| 1 | 조건 결과 | `met`, `unmet`, `unknown` — `CONTRIBUTING.md` 7-2, `docs/01-glossary-profile.md` 1장, `docs/03-api-contract.md` 4-1, `docs/04-data-schema.md` 5장, `rules/README.md` 4장 | `"충족"`, `"미충족"`, `"미확인"` — `ai/judgment/cases.py` 32~34행, `ai/citation/verify.py` 25~27행 | **있음** |
| 2 | 판정 상태 | `likely`, `check`, `unlikely` — `CONTRIBUTING.md` 7-2, `docs/01-glossary-profile.md` 1·4장 | 영문 그대로 — `ai/conversation/answer.py` 77~79행, `ai/conversation/related.py` 41~42행. `ai/judgment`·`ai/citation` 에는 이 값이 없다(상태는 코드가 계산) | 없음 |
| 3 | 프로필 항목 이름 | `age`, `region`, `district`, `status`, `categories`, `income_bracket` — `docs/01-glossary-profile.md` 2장 | 영문: `ai/conversation/fields.py` 38~59행 / 한국어: `ai/judgment/cases.py` 86~91행 (`"나이"`, `"거주지"`, `"자치구"`, `"현재 상태"`, `"관심 분야"`, `"가구 소득"`) | **있음** |
| 4 | 프로필 값 | `enrolled`, `on_leave`, `unknown` … — `docs/01-glossary-profile.md` 2~3장 | 영문: `ai/conversation/interpret.py` 50~82행 / 한국어: `ai/judgment/cases.py` 89~91·144·153·172·191행 (`"재학"`, `"휴학"`, `"잘 모르겠어요"`, `"없음"`, `"모름"`, `"서울"`, `"취업·훈련"`) | **있음** |
| 5 | `needed_field` 값 | 추가 항목 이름(영문) 또는 `"공고 확인 필요"` — `docs/03-api-contract.md` 4-1, `docs/05-interfaces.md` 4장 | 영문: `ai/conversation/fields.py` 47~59행 / 한국어: `ai/judgment/cases.py` 164·193행, `tests/test_judgment_eval.py` 106·108행 | **있음** |
| 6 | 조건 요약 키 | `name` — `docs/03-api-contract.md` 4-1 | `summary` — `ai/citation/verify.py` 106·136·143행, `ai/judgment/evaluate.py` 31행 | **있음** |
| 7 | `"공고 확인 필요"` | 한국어 그대로 — `docs/03-api-contract.md` 4-1, `docs/05-interfaces.md` 4장 | 양쪽 다 한국어 — `ai/judgment/cases.py` 37행, `ai/citation/verify.py` 147행, `ai/conversation/fields.py` 62행 | 없음 |

### 2-2. 조사 중 추가로 발견한 것

| 발견 | 어디 | 왜 문제인가 |
| --- | --- | --- |
| `docs/04-data-schema.md` 1장의 예시 열이 한국어다. `statuses`는 "재학, 휴학, 졸업예정, 구직", `regions`는 "서울 / 전국 / 자치구명", `extra_conditions`는 "주거 형태: 월세" | `docs/04-data-schema.md` 1·2장 | 같은 문서 2장은 "프로필 신분 5개 값 중 해당하는 것 전부"라고만 쓴다. 데이터 담당이 예시 열을 보고 한국어를 넣으면 규칙 엔진 입력부터 한국어가 된다. `ai/judgment/cases.py` 87·90행의 `"서울"`, `"취업·훈련"` 이 정확히 이 예시 표기다 |
| 필드 이름도 두 가지다. `docs/04-data-schema.md` 는 `id`, `category`(단수), `docs/03-api-contract.md` 4장은 `policy_id`, `categories`(복수) | `docs/04-data-schema.md` 1장, `docs/03-api-contract.md` 4장 | 값 표기와는 별개 문제지만 같은 경계에서 깨진다. 이 문서의 결정 범위 밖이므로 백엔드B에게 따로 올린다 |
| `ai/conversation/answer.py` 90~97행이 한국어 **화면 문구**를 상태 값으로도 받아들인다(`"신청 가능성이 높아요"` → `likely`) | `ai/conversation/answer.py` 90~97행 | 관용 처리다. 표기가 어긋났을 때 요약 문장이 "찾지 못했어요"로 뒤집히는 것을 막기 위한 방어이고, 주석에 그 이유가 적혀 있다. 표기를 정해도 이 방어는 남겨 두는 편이 낫다 |
| `ai/judgment/evaluate.py` 143행이 완료 기준 출력에 `"충족"`/`"미충족"` 을 쓴다 | `ai/judgment/evaluate.py` 143행 | 값이 아니라 지표 슬라이드용 문구다. 조건 결과 상수를 영문으로 바꿔도 이 줄은 한국어로 남는 게 맞다. 바꿀 때 혼동하지 않도록 적어 둔다 |
| `server/`, `rules/`, `frontend/` 에 `.py` 파일이 하나도 없다. README 만 있다 | 폴더 전수 확인 | "규칙 엔진과 서버도 고쳐야 한다"는 걱정은 아직 실체가 없다. 지금 표기를 정하는 비용이 가장 싼 시점이다 |

### 2-3. 지금 표기로 통과하는 테스트

| 파일 | 한국어 값이 있는 줄 | 비고 |
| --- | --- | --- |
| `tests/test_citation.py` | 170·172·173·181·182·185·186·188행 | `"미충족"`, `"미확인"`, `"공고 확인 필요"` 를 단정으로 비교한다 |
| `tests/test_judgment_eval.py` | 106·108행 | `"다른 지원 수혜 중"`, `"직전 학기 성적"` |

전체 실행 결과: 291건 통과, 서브테스트 493건 통과 (0.35초). 직접 돌려 확인했다.

## 3. 선택지와 비용

### 안 A — 전부 영문 snake_case (문서 기준)

| 항목 | 내용 |
| --- | --- |
| 고칠 파일 | `ai/judgment/cases.py`(17줄), `ai/judgment/evaluate.py`(2줄), `ai/citation/verify.py`(4줄), `tests/test_judgment_eval.py`(2줄), `tests/test_citation.py`(5줄) |
| 분량 | **5개 파일 / 약 30줄.** 상수 정의 6줄만 바꾸면 나머지는 상수를 참조하는 곳이라 자동으로 따라온다. 리터럴로 박힌 곳이 위 30줄이다 |
| 깨질 테스트 | `tests/test_citation.py` 8곳, `tests/test_judgment_eval.py` 2곳. 같은 커밋에서 함께 고친다 |
| 함께 처리 | `summary` → `name` (`docs/03-api-contract.md` 4-1). `ai/citation/verify.py` 3곳, `ai/judgment/evaluate.py` 1곳, 테스트 11곳 |
| 위험 | 낮다. 조건 결과는 상수 3개, 프로필 키는 한 곳(`BASE_PROFILE`)에 모여 있다. 판정 프롬프트 문안을 이미 한국어 값으로 써 뒀다면 그 문안도 함께 고쳐야 한다 |
| 이 안을 고르면 | **AI B가 12:30까지** `ai/judgment`·`ai/citation`·그 테스트를 고친다. **AI A는 손댈 것이 없다.** **백엔드B가 13:00 통합에서** 두 모듈 사이에 프로필 dict 가 실제로 오가는지 확인한다 |

### 안 B — 전부 한국어

| 항목 | 내용 |
| --- | --- |
| 고칠 파일 | `ai/conversation/interpret.py`(51줄), `questions.py`(27줄), `cases.py`(12줄), `answer.py`(6줄), `related.py`(3줄), `fields.py`(2줄 + 상수 정의 약 30줄), `__main__.py`(3줄), `tests/test_interpret.py`(54줄), `tests/test_related.py`(21줄), `tests/test_questions.py`(13줄), `tests/test_llm.py`(3줄) |
| 분량 | **11개 파일 / 약 195줄** (허용 값 문자열이 나오는 줄 기준). 안 A의 6배가 넘는다 |
| 문서도 고친다 | `CONTRIBUTING.md` 7-2, `docs/01-glossary-profile.md` 2~3장, `docs/03-api-contract.md` 4장, `docs/04-data-schema.md` 1·2·5장, `docs/05-interfaces.md` 2~4장, `rules/README.md` 4·8장. 문서가 기준이라는 규칙이 있으므로 코드보다 문서를 먼저 고쳐야 한다 |
| 깨질 테스트 | `tests/test_interpret.py`, `tests/test_related.py`, `tests/test_questions.py`, `tests/test_llm.py` — 4개 파일 91줄 |
| 위험 | 높다. 프론트가 `docs/03-api-contract.md` 4장을 보고 화면을 만들고 있다. 상태 값이 한국어가 되면 프론트의 비교문과 CSS 클래스까지 영향을 받는다. 한글 키는 NFC/NFD 문제도 함께 끌고 온다(`ai/citation/normalize.py` 11~20행이 그 문제를 설명한다) |
| 이 안을 고르면 | **백엔드B가 12:20까지** 문서 6곳을 고치고 프론트에 공지한다. **AI A가 13:00까지** 7개 파일과 테스트 4개를 고친다. **프론트가** 상태 값 비교 코드를 확인한다. AI B는 손댈 것이 없다 |

### 안 C — 경계에서 변환

| 항목 | 내용 |
| --- | --- |
| 변환기 위치 | 후보는 둘이다. (가) `ai/conversation/pipeline.py` — 이미 "AI 와 서버 사이 경계"를 자임하는 파일이고 `unknown_items_from` 이 같은 자리에 있다. (나) `server/` 아래 새 모듈 — 다만 `server/` 에는 아직 `.py` 파일이 없다 |
| 분량 | 새 파일 1개. 항목 이름 14개 + 조건 결과 3개 + 프로필 값 약 30개의 양방향 표. **추정 60~100줄** (추정이다. 다른 두 안의 분량은 실측이고 이것만 추정이다). 기존 코드는 거의 그대로 |
| 깨질 테스트 | 없다. 대신 변환기 테스트를 새로 써야 한다 |
| 위험 | 가장 높다. 이유는 분량이 아니라 **규칙이 두 곳에 생긴다**는 것이다. 허용 값을 하나 추가하면 문서·양쪽 코드·변환표 네 군데를 고쳐야 하고, 한 군데를 빼먹으면 그 값만 조용히 사라진다. `ai/conversation/pipeline.py` 325~329행이 바로 이 이유로 매핑을 거부하고 탈락 기록만 남긴다. 또 `CONTRIBUTING.md` 7-1이 "변환이 필요한 곳은 프론트 경계 한 곳뿐"이라고 정한 것을 깬다 |
| 이 안을 고르면 | **AI A가 12:40까지** 변환기와 그 테스트를 만든다. **AI B가** 자기 출력이 변환기를 통과하는지 확인한다. **백엔드B가** 변환기를 거치지 않는 경로가 없는지 통합에서 확인한다 |

## 4. 권고

**안 A(전부 영문 snake_case)를 권고한다.**

| 근거 | 출처 |
| --- | --- |
| `CONTRIBUTING.md` 7-2 가 고정 값을 이미 영문으로 확정했다. 판정 상태·조건 결과·의도·데이터 상태가 전부 영문이다. 그리고 8장이 "문서와 코드가 다르면 문서가 기준"이라고 정했다 | `CONTRIBUTING.md` 7-2, 8장 |
| `docs/03-api-contract.md` 4장 계약이 영문이고, **프론트가 그 계약을 보고 화면을 만들고 있다.** 값을 한국어로 바꾸면 이미 합의된 계약 하나를 되돌리는 일이 된다 | `docs/03-api-contract.md` 4·4-1장 |
| 화면에 보이는 한국어 문구는 값과 분리한다는 규칙이 이미 있다 | `CONTRIBUTING.md` 7-2 마지막 줄 |
| 값이 한국어면 그 분리가 무의미해진다. `on_leave` 와 "휴학"은 하나를 고쳐도 다른 하나가 안 바뀌지만, 값이 "휴학"이면 문구를 다듬는 순간 값이 바뀐다 | 6장 참고 |
| 파이썬은 snake_case 가 관습이다. `CONTRIBUTING.md` 7-1 이 "서버·규칙 엔진·AI 모듈 사이에는 변환이 없다"를 그 근거로 들었다. 한국어 키를 쓰면 그 이점이 사라진다 | `CONTRIBUTING.md` 7-1 |
| 안 B가 고쳐야 할 문서가 6곳이고 코드가 195줄이다. 안 A는 문서 0곳, 코드 30줄이다 | 3장 실측 |
| `server/`·`rules/`·`frontend/` 에 아직 파이썬 구현이 없다. 지금이 표기를 맞추는 가장 싼 시점이고, 13:00 이후에는 안 A의 분량도 늘어난다 | 폴더 전수 확인 |

### 반대 근거 (정직하게)

| 반대 근거 | 사실 확인 |
| --- | --- |
| AI B 코드는 이미 한국어로 돌아가고 테스트가 통과한다 | 사실이다. 291건 전부 통과한다. 고치는 쪽이 AI B다 |
| 고치는 쪽이 일이 더 많다 | 이 건에서는 아니다. 안 A는 5개 파일 30줄, 안 B는 11개 파일 195줄 + 문서 6곳이다. **고치는 쪽이 더 적은 경우다** |
| 한국어 값이 LLM 판정에 유리할 수 있다 | 확인하지 못했다. `ai/judgment/evaluate.py` 는 판정기를 인자로 받고 아직 실제 LLM 이 붙지 않았다. 프롬프트가 한국어 값을 요구하도록 쓰였다면 그 문안도 함께 고쳐야 하고, 그만큼 안 A의 분량이 늘어난다. **AI B가 이 한 가지를 확인한 뒤 결정하는 것이 맞다** |
| 한국어 값이 디버깅할 때 읽기 쉽다 | 사실이다. 다만 화면 문구 표(`ai/conversation/interpret.py` 137~180행 `VALUE_LABELS`)가 이미 값→문구 변환을 갖고 있어, 로그에 문구를 함께 찍는 것으로 대신할 수 있다 |

## 5. 안 A 를 고르면 할 일 체크리스트

| 담당 | 파일 | 무엇을 | 테스트 동반 |
| --- | --- | --- | --- |
| AI B | `ai/judgment/cases.py` 32~34행 | `MET`/`UNMET`/`UNKNOWN` 을 `met`/`unmet`/`unknown` 으로 | — |
| AI B | `ai/judgment/cases.py` 86~91행 | `BASE_PROFILE` 키를 `age`·`region`·`district`·`status`·`categories`·`income_bracket` 으로, 값을 `seoul`·`enrolled`·`job`·`unknown` 으로 | — |
| AI B | `ai/judgment/cases.py` 144·153·172·191행 | 케이스별 프로필 덮어쓰기 키와 값을 영문으로 (`status`/`on_leave`, `status`/`enrolled`, `other_benefit`/`no`, `last_gpa`/`unknown`) | — |
| AI B | `ai/judgment/cases.py` 164·193행 | `expected_needed_field` 를 `other_benefit`, `last_gpa` 로 | — |
| AI B | `ai/judgment/cases.py` 37행 | `ASK_NOTICE` 는 **그대로 둔다.** 문서도 `"공고 확인 필요"` 다 | — |
| AI B | `ai/citation/verify.py` 25~27행 | 조건 결과 상수 3개를 영문으로 | ✅ `tests/test_citation.py` |
| AI B | `ai/citation/verify.py` 147행 | 기본 `needed_field` 는 그대로 `"공고 확인 필요"` | ✅ |
| AI B | `ai/citation/verify.py` 106·136·143행 | 조건 요약 키를 `summary` → `name` (`docs/03-api-contract.md` 4-1) | ✅ |
| AI B | `ai/judgment/evaluate.py` 31행 | 독스트링의 조건 항목 형식을 `name` 으로 | — |
| AI B | `ai/judgment/evaluate.py` 143·191행 | 143행은 **문구이므로 한국어로 둔다.** 191행 `actual_needed_field` 는 `ASK_NOTICE` 상수를 쓰도록 | ✅ `tests/test_judgment_eval.py` |
| AI B | `tests/test_citation.py` 170~188행 | 단정값 8곳을 영문으로, `summary` 키 4곳을 `name` 으로 | 이 파일 자체 |
| AI B | `tests/test_judgment_eval.py` 106·108행 | `"다른 지원 수혜 중"`, `"직전 학기 성적"` 을 `other_benefit`, `last_gpa` 로 | 이 파일 자체 |
| AI B | `tests/test_judgment_eval.py` 37·49·61·183·189·206·240행 | `summary` 키 7곳을 `name` 으로 | 이 파일 자체 |
| AI B | 판정 프롬프트 문안 | 한국어 값을 요구하는 문장이 있으면 영문으로. 없으면 없다고 보고 | — |
| AI A | `ai/conversation/` | **바꿀 것 없음.** 주석 3곳(`fields.py` 11~14행, `cases.py` 23~38행, `llm.py` 1152~1155행)의 "12:00 에 정한다"를 결정 결과로 갱신 | — |
| 백엔드B | `docs/04-data-schema.md` 1장 | `statuses`·`regions`·`extra_conditions` 예시 열을 영문 값으로 바꾸거나, 예시임을 명시 | — |
| 백엔드B | 통합 | AI A → AI B 프로필 dict, AI B → 서버 조건 목록이 실제로 맞물리는지 13:00 통합에서 한 번 확인 | — |
| 전원 | — | `python -m pytest -q` 가 291건 전부 통과하는 상태로 PR | — |

## 6. 화면 문구는 건드리지 않는다

**값만 바꾼다. 사용자에게 보이는 한국어는 그대로다.**

| 이것은 값이다 (바꾼다) | 이것은 문구다 (그대로) | 문구가 사는 곳 |
| --- | --- | --- |
| `on_leave` | 휴학 | `docs/01-glossary-profile.md` 2장 괄호, `ai/conversation/interpret.py` 139~143행 |
| `unknown` (소득) | 잘 모르겠어요 | `docs/01-glossary-profile.md` 2장 소득 구간 표의 "뜻" 열 |
| `check` | 확인이 필요해요 | `docs/01-glossary-profile.md` 4장 화면 문구 열 |
| `unmet` | 미충족 | `docs/01-glossary-profile.md` 1장 |
| `other_benefit` | 다른 지원 수혜 중 | `docs/01-glossary-profile.md` 3장 항목 열 |

혼동하면 화면이 영어로 바뀐다. `docs/01-glossary-profile.md` 4장의 상태 문구("신청 가능성이 높아요", "확인이 필요해요", "어려울 수 있어요")와 2~3장 괄호 안의 한국어는 **값이 아니라 문구다.** 이 문서의 결정은 그 표를 전혀 건드리지 않는다.

`"공고 확인 필요"` 만 예외다. 이것은 문구처럼 보이지만 `needed_field` 에 들어가는 **값**이고, 문서가 한국어로 정했다(`docs/03-api-contract.md` 4-1, `docs/05-interfaces.md` 4장). 그대로 둔다.

## 7. 결정 기록

| 항목 | 결정 | 정한 사람 | 시각 |
| --- | --- | --- | --- |
| 조건 결과 | | | |
| 프로필 항목 이름 | | | |
| 프로필 값 | | | |
| `needed_field` 값 | | | |
| 조건 요약 키 (`name` vs `summary`) | | | |
| `docs/04-data-schema.md` 예시 열 표기 | | | |
| 반영 완료 확인 | | | |

## 8. 확인하지 못한 것

| 항목 | 이유 |
| --- | --- |
| `ai/conversation/README.md`, `ai/judgment/README.md` 전문 | 다른 담당이 동시에 수정 중이라 읽지 않았다. `ai/conversation/README.md` 189~192행이 `check`·`unlikely` 를 영문으로 쓰는 것만 확인했다 |
| 규칙 엔진·서버·프론트의 실제 표기 | `rules/`, `server/`, `frontend/` 에 구현 파일이 없다. README 만 있다 |
| 실제 정책 데이터의 값 표기 | `data/policies/` 폴더가 아직 없다. `data/` 아래에는 README 3개뿐이다 |
| 판정 프롬프트 문안의 표기 | `ai/judgment/evaluate.py` 는 판정기를 인자로 받고, 실제 LLM 판정기가 아직 없다. 문안은 저장소에 없다 |
| 안 C 변환기의 실제 분량 | 파일이 없으므로 추정치다. 안 A·안 B 분량은 실측이다 |
