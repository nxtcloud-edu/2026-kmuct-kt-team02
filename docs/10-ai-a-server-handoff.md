# 10. AI A ↔ 서버 인계 (dict → pydantic)

> 이 문서는 AI A(`ai/conversation/`)가 내는 dict 와 서버(`server/sse.py`, `server/schemas.py`, `server/chat_service.py`)의 pydantic 모델을 **누가 어떻게 잇는지** 한 장에 못 박는다.
> 12:00 통합 회의에서 이 문서만 보고 결정할 수 있어야 한다. 결정 대상은 4장 표의 다섯 건이고, 6장이 확인 방법이다.
> 모든 줄 번호는 `dev` 브랜치 현재 상태(커밋 609765a + 커밋되지 않은 AI A 작업분)를 직접 읽어 확인한 값이다. 추측으로 적은 숫자는 없다. `ai/conversation/` 은 지금도 고쳐지고 있어 AI A 쪽 줄 번호는 흔들릴 수 있다. 이름(함수·상수·필드)은 흔들리지 않으니 어긋나면 이름으로 찾는다.

## 1. 결론 먼저

**변환 계층이 저장소에 없다.** `server/` 아래 파이썬 파일은 23개(구현 15개 + `server/tests/` 8개)이고, 그중 `ai.conversation` 을 import 하는 파일은 **0건**이다. `server/chat_service.py` 의 `ChatService` 는 55~60행의 Protocol 선언뿐이고, 같은 파일이 정의하는 것은 입력·출력 모델(22~44행)과 `RecoverableChatError`(47~52행)다. `server/main.py` 25·44행은 `chat_service` 기본값을 `None` 으로 둔다. `server/ai_gateway.py` 는 이름이 비슷해 오해를 사기 쉬운데, 이 파일이 하는 일은 **마스킹과 프로바이더 호출**뿐이다 — 84~112행 `_invoke` 가 `mask_pii`·`mask_structure` 를 거쳐 `AIProviderRequest` 를 만들고 `provider.invoke` 를 부른다. AI A 의 dict 를 이벤트 모델로 옮기는 코드는 `server/` 안에 한 줄도 없다.

**서버 모델은 전부 `extra="forbid"` 다.** `server/schemas.py` 24~27행의 `ContractModel` 이 `extra="forbid"` 와 `str_strip_whitespace=True` 를 정하고, 모든 이벤트 payload 모델이 이것을 상속한다. 키 이름이 하나만 달라도 통과하지 못한다.

그 실패는 **스트림 중간에서 난다.** `server/api/chat.py` 66행이 첫 `status` 프레임을 `yield` 하는 순간 200 과 `text/event-stream` 헤더가 이미 클라이언트로 나간다. 프레임 검증은 그 뒤다 — 30~44행 `_frame_builder` → `server/sse.py` 162행 `encode_sse` → 134행 `validate_sse_event`. 그래서 키가 어긋나면 HTTP 오류가 아니라 **이유 없이 끊긴 스트림**이 된다. `done` 이 오지 않으므로 프론트는 입력창을 다시 열지 못한다(`server/README.md` 3장 13단계, 85행).

**변환은 AI A 가 맡는다. 자리는 `ai/conversation/handoff.py` 다.** 이 문서를 쓰는 동안 그 파일이 들어왔다(커밋 전 상태). 공개 함수는 `as_plain`(pydantic 모델을 dict 로 벗김), `profile_update_event`, `related_event`, `related_chip_ids`, `followup_event`, `answer_failure_payload`, `delta_lines` 이고, 아래 2장의 여섯 쌍과 3장이 그 목록과 1:1로 대응한다. 서버 쪽에 두지 않기로 한 이유는 두 가지다. 첫째, 변환에 필요한 지식이 전부 AI A 쪽에 있다 — 어떤 키가 무엇을 뜻하고 무엇을 버려도 되는지는 `interpret.py`·`related.py`·`answer.py` 의 판단이다. 둘째, `server/` 는 백엔드B 소유이고(`CONTRIBUTING.md` 6장) 변환 코드는 통합 중에 가장 자주 고쳐질 파일이다. 남의 폴더에 두면 매번 PR 이 교차한다. **다른 선택을 버린 이유**: `server/` 아래 새 모듈로 두면 pydantic 모델을 바로 만들 수 있어 한 단계가 줄지만, AI A 가 키를 하나 바꿀 때마다 백엔드B 가 따라 고쳐야 한다. 12:00~14:30 사이에 그 왕복을 감당할 수 없다. `handoff.py` 는 dict 를 dict 로 옮기고, pydantic 검증은 서버가 받는 자리에서 한 번만 한다.

아래 2장은 쌍을 세 부류로 나눠 적는다.

| 부류 | 쌍 | 지금 상태 |
| --- | --- | --- |
| 이름이 맞는 쌍 | 2-2 후속 질문, 2-4 각주, 2-5 정책 | 키 이름은 맞다. 넘기는 **모양**(모델 객체 vs dict)만 정하면 된다 |
| 이름이 안 맞는 쌍 | 2-1 프로필 변경, 2-3 관련 질문 칩 | 키 이름과 타입이 다르다. `handoff.py` 가 반드시 필요하다 |
| 실을 자리가 없는 쌍 | 2-3 칩 `id`, 2-6 `region`, 3장 답변 실패 | 서버 모델에 자리가 없다. 서버를 고칠지 버릴지 결정해야 한다 |

가장 위험한 것은 2-4·2-5 다. **이름이 맞기 때문에** 모델 객체를 그대로 넘기고 싶어지는데, 그러면 예외도 로그도 없이 답변이 통째로 사라진다.

## 2. 쌍별 대조표

### 2-1. `interpret.MergeResult.to_profile_update()` → `ProfileUpdateEventData`

AI A: `ai/conversation/interpret.py` 956~968행 `MergeResult.to_profile_update`. 변경 항목 하나의 모양은 913~920행 `AppliedChange.to_dict`.
서버: `server/sse.py` 44~47행. `server/chat_service.py` 37행이 이 모델을 `profile_update` 로 들고 있고, `server/api/chat.py` 77~78행이 `None` 이 아닐 때만 프레임을 만든다.

| AI A 가 내는 키 | 서버 모델 필드 | 상태 | 조치 |
| --- | --- | --- | --- |
| `changes` (list of dict, 965행) | `changed_fields` (`dict[ProfileField, JsonValue]`, sse.py 45행) | 이름 다름 + 타입 다름 | `handoff.profile_update_event` 가 항목 이름을 키로, `after` 를 값으로 접는다 |
| `notice` (str, 966행) | `message` (str, 46행) | 이름 다름 | 그대로 옮긴다. 항목별 안내를 공백으로 이어 붙인 값이다(949~954행 `notices`) |
| `profile` (dict, 967행) | `profile` (`Profile`, 47행) | 일치 | `Profile.model_validate` 를 통과해야 한다. `region` 키가 섞이면 거부된다(2-6) |
| `changes[].before` (916행) | 없음 | 표현 불가 | 버린다 |
| `changes[].label` (918행) | 없음 | 표현 불가 | 버린다 |
| `changes[].notice` (항목별, 919행) | 없음 | 표현 불가 | 버린다. 합친 문구는 `message` 에 남는다 |

이름 불일치 2건에 더해, **`changes` 를 dict 로 접는 순간 `before`·`label`·항목별 `notice` 세 가지가 사라진다.** 이 세 값은 `docs/03-api-contract.md` 5-3 이 "화면이 무엇이 무엇으로 바뀌었는지 보여주기 때문"이라고 적어 둔 값이고, AI A 는 913~920행에서 그것을 채운다. 서버 모델에는 자리가 없다.

**버리는 쪽으로 권고한다.** `ProfileUpdateEventData` 를 `changes` 구조로 바꾸는 것이 계약(5-3)과 구현을 되붙이는 옳은 방향이긴 하다. 그런데 프론트가 이미 확정된 모델을 보고 프로필 바를 만들고 있고, 잃는 것은 "휴학 → 재학" 형태의 전후 표시 하나다. `message` 에 "휴학으로 바꿨어요"가 그대로 들어가므로 사용자가 무엇이 바뀌었는지는 안다. **다른 선택을 버린 이유**: 모델을 `changes` 로 바꾸면 프론트·서버·AI A 세 곳이 동시에 움직여야 하고, 얻는 것은 `before` 표시뿐이다. 12:00 에 남은 시간에 비해 비싸다.

`changed_fields` 의 키 타입이 `ProfileField` 라는 점을 주의한다. AI A 는 `region` 변경을 허용 값으로 인정하므로(`interpret.py` 58행 `REGION_VALUES`, 106~108행 `BASE_CHANGE_FIELDS`) 그 키가 실리면 enum 에 없어 스트림 중간에 터진다. 2-6 과 5장이 이것을 다룬다.

### 2-2. `followup.next_question()` / `questions.build()` → `FollowupQuestion`

AI A: `ai/conversation/questions.py` 382~410행(`build`), `ai/conversation/followup.py` 150~167행(`next_question` 이 `build` 결과를 그대로 돌려준다).
서버: `server/schemas.py` 339~345행. 선택지는 334~336행 `FollowupOption`. `server/sse.py` 105~107행이 이 모델을 `followup` payload 로 쓴다.

| AI A 가 내는 키 | 서버 모델 필드 | 상태 | 조치 |
| --- | --- | --- | --- |
| `field` (questions.py 401행) | `field: AskableProfileField` (schemas.py 340행) | 일치 | `questions.TEMPLATES`(142~243행)의 키 10개가 `AskableProfileField` 198~207행의 10개와 같다 |
| `question` (402행) | `question` (341행) | 일치 | 없음 |
| `reason` (403행) | `reason` (342행) | 일치 | 없음 |
| `options` (404~407행, `value`·`label` 쌍) | `options: list[FollowupOption]` (343행) | 일치 | `FollowupOption.value` 가 `JsonValue`(335행)라 문자열 값이 그대로 들어간다 |
| `allow_free_text` (408행) | `allow_free_text` (344행) | 일치 | `district` 만 `True` 다(`questions.py` 123~140행의 결정) |
| `allow_skip` (409행, 항상 `True`) | `allow_skip: Literal[True]` (345행) | 일치 | 없음 |

**키 6개가 정확히 일치한다.** 이 쌍은 `FollowupQuestion.model_validate(questions.build(...))` 한 줄로 끝난다. 2장에서 유일하게 이름 협상이 필요 없는 쌍이다.

예외가 하나 있다. `questions.build_planned_question()`(413~442행)은 `field` 에 `PLANNED_BASIS_FIELD`(263행, 값은 `planned_basis`)를 담는다. 이 값은 `AskableProfileField` 에 없다. 그래서 **planned 확인 질문("다음 학기에 휴학할 거예요" → "휴학 기준으로 볼까요?")은 지금 `followup` 이벤트로 나갈 수 없다.**

이것은 AI A 의 실수가 아니다. `questions.py` 252~262행이 프로필 항목 표 밖의 이름을 **일부러** 골랐다. 전에는 이 질문의 `field` 가 `status` 였고, 후속 질문 답변은 해석을 건너뛰고 값을 그대로 프로필에 넣으므로(`docs/03-api-contract.md` 3장) `status="planned"` 라는 허용 값 밖 값이 프로필에 박혔다. 이름을 바꾼 것이 그 구멍을 막았고, 그 결과가 enum 불일치로 드러난 것이다. 결정은 4장 3번에 있다.

### 2-3. `related.build()` / `to_event()` → `RelatedEventData`

AI A: `ai/conversation/related.py` 295~301행(`build`) → 286~292행(`to_event`). 칩 하나는 70~72행 `RelatedChip.to_event`.
서버: `server/sse.py` 72~73행. `server/chat_service.py` 43행에서 `None` 을 허용하고, `server/api/chat.py` 90~91행이 `None` 이 아닐 때만 프레임을 만든다.

| AI A 가 내는 키 | 서버 모델 필드 | 상태 | 조치 |
| --- | --- | --- | --- |
| `chips` (related.py 292행, list of dict) | `questions` (`list[str]`, sse.py 73행) | 이름 다름 + 타입 다름 | `handoff.related_event` 가 `chips` 의 `text` 만 뽑아 문자열 목록으로 펼친다 |
| `chips[].text` (72행) | `questions[i]` (str) | 이름 다름 | 위와 같다 |
| `chips[].id` (72행) | 없음 | 표현 불가 | 4장 2번의 결정 대상. `handoff.related_chip_ids` 가 세션에 넣을 번호를 따로 돌려준다 |
| 최대 3개 (`MAX_CHIPS = 3`, 36행) | `Field(max_length=3)` (73행) | 일치 | 없음 |
| 빈 칩 목록 (`chips` 가 빈 목록) | `None` | 이름 다름 | **빈 목록은 `None` 으로 매핑한다** |

두 가지가 걸린다.

**`id` 가 전송 경로에서 사라지면 `related.select(shown=...)` 의 중복 제거가 영구히 죽는다.** `related.py` 246~265행 `select` 는 `shown` 에 담긴 칩 번호를 빼고 고른다. 그 번호는 프론트가 눌린 칩을 되돌려 보내고 서버 세션이 모아 둔 값이어야 한다(`docs/03-api-contract.md` 5-2, 11장). 와이어에 `id` 가 없으면 프론트가 되돌려 보낼 것이 없고, 세션의 `shown` 은 영원히 빈 집합이다. 같은 상황에서 같은 칩이 매 턴 다시 뜨고, 누른 칩을 또 권한다 — `related.py` 253~257행이 막으려고 쓴 동작이 그대로 깨진다.

**빈 칩 목록을 `None` 으로 접지 않으면 매 턴 빈 `related` 이벤트가 나간다.** `related.build` 는 상황에 맞는 칩이 없을 때 빈 목록을 돌려주고 그것이 정상 동작이다(246~257행). 그런데 빈 목록으로 만든 `RelatedEventData` 는 검증을 통과하고, `server/api/chat.py` 90행은 `None` 검사만 하므로 프레임이 그대로 나간다. 프론트는 칩 영역을 매번 그렸다 비우게 된다.

### 2-4. 각주 목록 → `FootnotesEventData` (그리고 `answer.known_footnote_ids` 가 받는 모양)

서버: `server/sse.py` 59~66행(`Footnote`), 68~69행(`FootnotesEventData`). `server/chat_service.py` 40~42행이 기본값으로 빈 목록을 둔다.
AI A: 각주를 **만들지 않고 읽는다.** `ai/conversation/answer.py` 187~226행 `known_footnote_ids` 가 입구이고, 그 결과는 485행(`validate`)과 613행(`sanitize`)에서 쓰인다.

| AI A 가 읽는 키 | 서버 모델 필드 | 상태 | 조치 |
| --- | --- | --- | --- |
| `footnotes` 래퍼 (answer.py 211~214행) | `FootnotesEventData.footnotes` (sse.py 69행) | 일치 | `known_footnote_ids` 가 `footnotes`·`items`·`data` 래퍼를 벗긴다 |
| `footnote_id` 또는 `id` (answer.py 216·235행) | `Footnote.footnote_id` (60행) | 일치 | `docs/03-api-contract.md` 5-1 이 `footnote_id` 로 확정했다 |
| (읽지 않음) | `policy_id`, `excerpt`, `agency`, `checked_at`, `source_url` (61~65행) | 일치 | AI A 는 번호만 쓴다. 나머지는 프론트가 쓴다 |
| **모양** | pydantic 모델 객체 | 타입 다름 | `model_dump(mode="json")` 한 dict 로 넘긴다 (`handoff.as_plain` 이 그 일을 한다) |

마지막 줄이 이 쌍의 전부다. **`FootnotesEventData` 객체나 `Footnote` 객체 목록을 그대로 넘기면 `known_footnote_ids` 가 `frozenset()` 을 돌려준다.** 187~226행은 `Mapping`(206행), `str`/`bytes`/`int`(220행), `Iterable`(223행) 세 갈래로만 분기하는데 pydantic 모델은 `Mapping` 이 아니다. `FootnotesEventData` 객체는 세 갈래를 모두 빠져나가 226행의 `frozenset()` 으로 떨어진다. `Footnote` 객체를 담은 **리스트**를 넘기면 `Iterable` 갈래로 들어가지만, 229~239행 `_collect_ids` 가 원소마다 `Mapping` 인지 보고 아니면 원소 자체를 `_as_int`(242행)에 넘기므로 역시 번호가 하나도 모이지 않는다.

그 결과는 이렇다. 쓸 수 있는 번호가 없으므로 각주가 붙은 문장이 전부 `unknown_footnote` 로 잡혀(466행 `validate`) 정리 단계에서 삭제되고(590행 `sanitize`), 남는 것은 항상 붙는 고정 문구 한 줄이다. `pipeline.finish_turn`(787행)은 그것을 `EMPTY_AFTER_SANITIZE`(735행)로 잡아 `answer_failed=True` 로 바꾼다(836~840행). **즉 각주가 붙은 문장이 전부 지워지고 답변이 통째로 버려진다. 예외도 로그도 없다.** `docs/03-api-contract.md` 183행이 같은 실패를 "오류는 나지 않는다"로 경고한다.

`model_dump(mode="json")` 을 지정하는 이유는 `checked_at` 이 `date` 이고 `source_url` 이 `HttpUrl` 이기 때문이다. `mode="python"` 으로도 번호는 읽히지만, 같은 dict 를 프롬프트나 지표 기록에 재사용할 때 직렬화 가능한 값만 들고 있는 편이 안전하다.

### 2-5. 정책 판정 결과 → `PoliciesEventData` (`answer.build_skeleton` 이 읽는 키)

서버: `server/sse.py` 50~52행. 항목은 `server/schemas.py` 311~330행 `PolicyEvaluation`, 조건은 293~300행 `ConditionEvaluation`, 마감은 303~308행 `Deadline`.
AI A: `ai/conversation/answer.py` 798행 `build_skeleton`, 890행 `_deadline_line`, `ai/conversation/related.py` 151~164행 `_as_policies`·179~202행 `income_is_the_blocker`·205~213행 `_has_imminent_deadline`.

| AI A 가 읽는 키 | 서버 모델 필드 | 상태 | 조치 |
| --- | --- | --- | --- |
| `status` (answer.py 816행) | `PolicyEvaluation.status` (`EvaluationStatus`) | 일치 | `likely`/`check`/`unlikely` 값이 같다 |
| `title` (answer.py 833행) | `PolicyEvaluation.title` | 일치 | 없음 |
| `policy_id` (answer.py 838행) | `PolicyEvaluation.policy_id` | 일치 | 없음 |
| `conditions[].result` (related.py 197행) | `ConditionEvaluation.result` (schemas.py 295행) | 일치 | `unknown` 값이 같다 |
| `conditions[].needed_field` (related.py 199행) | `ConditionEvaluation.needed_field` (300행) | 일치 | 타입이 `AskableProfileField` 와 `공고 확인 필요` 의 합집합이라 `income_bracket` 비교가 성립한다 |
| `deadline.is_imminent` (related.py 212행, answer.py 925행) | `Deadline.is_imminent` (308행) | 일치 | 없음 |
| `deadline.badge` (answer.py 928행) | `Deadline.badge` (307행) | 일치 | 없음 |
| `deadline.d_day` (answer.py 930행) | `Deadline.d_day` (306행) | 일치 | 없음 |
| (읽지 않음) | `conditions[].name` (294행) | 일치 | 조건 요약 키를 서버가 `name` 으로 이미 확정했다(`docs/09-value-naming-decision.md` 2-1 의 6번) |
| **모양** | pydantic 모델 객체 | 타입 다름 | `model_dump(mode="json")` 한 dict 로 넘긴다 (`handoff.as_plain`) |

**이름은 전부 맞는다. 문제는 또 모양이다.** `build_skeleton` 811행은 `isinstance(policy, Mapping)` 인 원소만 남긴다. `PolicyEvaluation` 객체는 `Mapping` 이 아니므로 목록이 통째로 비고, `_summary_line`(873행)이 결과 없음 문구를 돌려준다. **카드가 화면에 5장 떠 있는데 답변은 "지금 조건으로는 맞는 제도를 찾지 못했어요"로 나간다.** `_normalize_status`(857행)의 주석이 "화면과 답변이 정반대를 말하는 것이 가장 나쁜 실패"라고 적어 둔 상태가 정확히 이 경로로 만들어진다.

같은 이유로 두 가지가 더 조용히 사라진다.

| 사라지는 것 | 왜 | 근거 |
| --- | --- | --- |
| 마감 강조 문장 | `_deadline_line` 922행이 `Mapping` 이 아닌 원소를 건너뛴다. `deadline` 자체도 `Mapping` 이어야 한다(925행) | `answer.py` 890행 이하 |
| 마감 칩 (`deadline_order`) | `related._as_policies` 158·164행이 `Mapping` 이 아닌 원소를 걸러내므로 `_has_imminent_deadline` 이 항상 거짓이다 | `related.py` 151~164행, 205~213행 |

마감 칩은 `related.py` 119~124행 `CHIP_PRIORITY` 가 **1순위**로 정해 둔 칩이다("마감은 지나면 답이 사라진다"). 그것이 예외 없이 사라진다.

### 2-6. `Profile` ↔ `ai/conversation/fields.py` 항목 이름과 `interpret.py` 허용 값

서버: `server/schemas.py` 147~156행 `ProfileInput`, 159~178행 `Profile`, 181~194행 `ProfileField`.
AI A: `ai/conversation/fields.py` 44~59행(항목 이름), `ai/conversation/interpret.py` 55~108행(허용 값).

| AI A 가 내는 키 | 서버 모델 필드 | 상태 | 조치 |
| --- | --- | --- | --- |
| `age` (fields.py 44행), 15~39 (interpret.py 47~48행) | `Profile.age` → `Age` (schemas.py 135행, `ge=15, le=39`) | 일치 | 없음 |
| `region` (fields.py 45행), `seoul`·`outside_seoul` (interpret.py 58행) | `Profile.region` → `Literal[Region.SEOUL]` (166행). `ProfileField` 에 **없음**(181~194행) | 표현 불가 | AI A 가 버린다(5장) |
| `district` (fields.py 46행), 자치구 25개 (interpret.py 73~81행) | `District` (schemas.py 34~59행, 25개) | 일치 | 없음 |
| `status` (47행), 5개 (interpret.py 60~62행) | `UserStatus` (62~67행, 5개: `enrolled`·`on_leave`·`final_semester`·`job_seeking`·`employed`) | 일치 | 없음 |
| `categories` (48행), 5개 + `all` (interpret.py 64~66행) | `Category` (70~76행, 6개) | 일치 | 서버는 중복과 `all` 조합을 거부한다(139~145행). `interpret._merge_categories` 가 이미 같은 규칙을 지킨다 |
| `income_bracket` (49행), 5개 (interpret.py 68~70행) | `IncomeBracket` (79~84행, 5개) | 일치 | 없음 |
| `housing_type` (52행), 5개 (interpret.py 83~85행) | `HousingType` (87~92행, 5개) | 일치 | 없음 |
| `residence_period` (53행), 3개 (interpret.py 86행) | `ResidencePeriod` (95~98행, 3개) | 일치 | 없음 |
| `remaining_semesters` (54행), 2개 (interpret.py 87행) | `RemainingSemesters` (101~103행, 2개) | 일치 | 없음 |
| `job_seeking_period` (55행), 2개 (interpret.py 88행) | `JobSeekingPeriod` (106~108행, 2개) | 일치 | 없음 |
| `employment_insurance` (56행), 3개 (interpret.py 89행) | `YesNoUnknown` (111~114행, 3개) | 일치 | 없음 |
| `other_benefit` (57행), 2개 (interpret.py 90행) | `YesNo` (117~119행, 2개) | 일치 | 없음 |
| `household_size` (58행), 4개 (interpret.py 91행) | `HouseholdSize` (122~126행, 4개) | 일치 | 값이 문자열 `1`~`4_plus` 인 것도 같다 |
| `last_gpa` (59행), 3개 (interpret.py 92행) | `LastGpa` (129~132행, 3개) | 일치 | 없음 |

**`region` 을 빼면 항목 이름 13개와 값 목록이 전부 일치한다.** `fields.py` 는 기본 항목 6개(44~49행)와 추가 항목 8개(52~59행), 합쳐 이름 14개를 두는데 그중 하나가 `region` 이고, `ProfileField`(181~194행)는 정확히 나머지 13개를 담는다. 값도 항목마다 개수와 문자열이 같다 — 위 표의 "일치" 13줄이 그것을 항목별로 확인한 결과다. `docs/09-value-naming-decision.md` 4장이 권고한 "전부 영문 snake_case"를 서버가 그대로 따랐고 `ai/conversation` 은 처음부터 그 표기였다. 이름·값 쪽에서 통합에 손댈 것은 없다.

`region` 만 세 방향으로 어긋난다. `interpret.py` 58행은 `outside_seoul` 을 허용 값으로 인정하고, `Profile.region` 은 `seoul` 하나만 받으며(166행), `ProfileField` 에는 이름조차 없다. 서버 쪽 근거는 명확하다 — `docs/01-glossary-profile.md` 27행이 `region` 을 "입력 안 함 / `seoul` 고정"으로 정했고, `server/tests/test_schemas.py` 32~34행이 `ProfileInput` 이 `region` 을 거부하는 것을, 59~76행이 요청에 섞인 `region` 을 422 로 떨어뜨리는 것을 테스트한다. 서울 거주가 제품 전제다. 그래서 AI A 가 버리는 쪽으로 정했다(5장).

## 3. 답변 실패를 계약이 표현하지 못한다

AI A 는 답변 실패를 **이미 판정하고 있다.** `ai/conversation/pipeline.py` 772행의 `FinishedTurn.answer_failed` 가 그 값이고, 836~839행이 금지 표현·누락 각주·없는 각주 번호(722~724행 `BLOCKING_PROBLEM_CODES`)와 정리 후 빈 본문(735행)을 보고 그것을 켠다. 실패면 `answer_text` 는 빈 문자열이다(840행). 873~882행의 바깥 `except` 도 같은 값을 켠 채 카드만 남긴다.

그 값을 와이어에 실을 방법이 없다.

| 막는 것 | 위치 | 무슨 일이 되는가 |
| --- | --- | --- |
| `answer_deltas` 의 `Field(min_length=1)` | `server/chat_service.py` 39행 | 델타가 0개인 `ChatPipelineResult` 를 만들 수 없다. 실패를 "답변 없음"으로 표현하는 경로가 닫혀 있다 |
| `delta` 의 `Field(min_length=1)` | `server/sse.py` 56행 | 빈 문자열 델타 하나로 우회할 수도 없다 |
| `str_strip_whitespace=True` | `server/schemas.py` 27행 | 공백만 있는 조각은 먼저 다듬어진 뒤 `min_length=1` 에 걸린다 |
| `SSEEventName` 에 `error` 없음 | `server/sse.py` 23~31행 (8개: `status`, `profile_update`, `policies`, `answer_delta`, `footnotes`, `followup`, `related`, `done`) | 실패를 알리는 이벤트 이름 자체가 없다 |

`server/errors.py` 23행의 `ErrorCode.ANSWER_FAILED` 와 30행의 문구("설명을 불러오지 못했어요. 카드에서 조건을 확인해 주세요")는 **HTTP JSON 엔벨로프 전용**이다. 66~68행 `_response` 가 `JSONResponse` 를 만들고, 71·78·105행의 핸들러 세 개가 그것을 쓴다. `server/` 전체에서 `ANSWER_FAILED` 가 등장하는 곳은 `errors.py` 23행과 30행뿐이고 — 직접 `Select-String` 으로 `server/*.py`, `server/api/*.py`, `server/tests/*.py` 를 훑어 확인했다 — **스트림에서 쓰이는 곳은 0건이다.** 스트림은 이미 200 을 보낸 뒤이므로 JSON 엔벨로프로 되돌아갈 수도 없다.

`RecoverableChatError`(`server/chat_service.py` 47~52행)도 탈출구가 아니다. 생성자가 완전한 `ChatPipelineResult` 를 요구하고, `server/api/chat.py` 74~76행이 `exc.fallback` 을 정상 결과처럼 이어 쓴다. 그 fallback 역시 `answer_deltas` 를 1개 이상 담아야 한다. 이름이 "복구 가능"인데 실제로는 "성공한 척하기"만 된다.

그리고 **계약과 구현이 갈라져 있다.** `docs/03-api-contract.md` 5장 이벤트 표 157행에 `error` 이벤트가 **있다** — "부분 실패 / 오류 코드와 사용자 문구 / 카드 유지 + 오류 문구 + 다시 시도 버튼". 프론트는 이 표를 보고 화면을 만들고 있다. 구현에는 그 이벤트가 없다.

### 3-1. 최소 변경 두 가지

| 항목 | (가) `SSEEventName` 에 `error` 추가 + `answer_deltas` `min_length` 제거 | (나) `ChatPipelineResult` 에 `answer_failed: bool` 추가 + 실패 시 고정 문구를 델타로 |
| --- | --- | --- |
| 누가 | 백엔드B | 백엔드B |
| 무엇을 몇 줄 | `server/sse.py`: enum 1줄(31행 뒤), payload 모델 3줄(73행 뒤), 이벤트 래퍼 3줄(113행 뒤), `SSEEvent` union 1줄(120~128행) → 약 8줄. `server/chat_service.py` 39행에서 `Field(min_length=1)` 제거 1줄. `server/api/chat.py` 83~84행에 빈 델타 분기 3~4줄. **합계 약 13줄, 3개 파일** | `server/chat_service.py` 에 필드 1줄. `server/api/chat.py` 83행 앞에 실패 시 `errors.py` 30행 문구를 델타로 넣는 3줄. **합계 약 4줄, 2개 파일** |
| 프론트가 실패를 구분할 수 있는가 | **할 수 있다.** 이벤트 이름과 오류 코드로 구분한다. `docs/03-api-contract.md` 157행에 이미 적힌 반응("카드 유지 + 오류 문구 + 다시 시도 버튼")을 그대로 구현할 수 있다 | **할 수 없다.** `answer_failed` 는 payload 모델에 자리가 없어 와이어에 나가지 않는다. 프론트는 `answer_delta` 본문 문자열을 오류 문구와 비교해야 실패를 안다 |
| 계약과의 관계 | 계약을 구현에 맞춘다. 갈라진 것을 되붙인다 | 계약의 `error` 이벤트는 계속 미구현으로 남는다 |
| 남는 위험 | `answer_deltas` 가 빈 목록을 허용하게 되므로 "델타 0개 + `error` 없음"이라는 조용한 조합이 새로 생긴다. `server/api/chat.py` 에서 그 조합을 막아야 한다 | 문구가 값이 된다. `docs/09-value-naming-decision.md` 6장이 "값이 '휴학'이면 문구를 다듬는 순간 값이 바뀐다"고 적은 것과 같은 실수다. 오류 문구를 다듬으면 프론트의 실패 판정이 조용히 깨진다 |

**(가)를 권고한다.** 줄 수는 (나)가 3배 적지만, (나)는 프론트가 실패를 구분할 수 없다는 한 가지 때문에 목적을 달성하지 못한다. 실패를 성공처럼 보여 주는 것은 `ai/conversation/pipeline.py` 726~735행이 이미 거부한 방향이다 — 정리 후 고정 문구만 남은 답변을 내보내지 않기로 한 이유가 "사용자는 설명을 요청했는데 주의 문구만 받는다. 실패를 성공처럼 보여 주는 것"이었다. 같은 판단을 서버 경계에서 되풀이할 이유가 없다.

(나)의 변형 — `DoneEventData`(`server/sse.py` 76~77행)에 `answer_failed` 를 싣는 방법 — 도 검토했다. 프론트가 구분할 수는 있게 되지만 스트림 끝에서야 알게 되므로 답변 자리에 오류 문구와 다시 시도 버튼을 붙일 시점을 놓친다. 그리고 `done` 은 "정상 또는 복구 가능한 fallback 종료"를 뜻하는 이벤트라(`docs/03-api-contract.md` 158행) 실패 정보를 여기에 얹으면 이벤트의 뜻이 둘로 갈린다.

### 3-2. 공백만 있는 조각 하나가 스트림을 끊는다

델타에 공백만 있는 조각이 하나라도 들어가면 **스트림 중간에 `ValidationError` 가 난다.** `server/schemas.py` 27행의 `str_strip_whitespace=True` 가 먼저 공백을 벗기고, `server/sse.py` 56행의 `min_length=1` 이 그 빈 문자열을 거부한다. 터지는 자리는 `server/api/chat.py` 83~84행 루프 안이고, 그때는 앞선 `status`·`policies` 프레임이 이미 나간 뒤다. 클라이언트는 절반 그려진 답변과 끊긴 연결만 본다.

AI A 가 내놓는 본문은 `answer.sanitize`(590행)와 `answer.validate`(466행)를 통과한 한 덩어리 문자열이므로, 이 위험은 AI A 쪽이 아니라 **문장을 쪼개는 코드**에 있다. 문장 단위로 자르면(`docs/03-api-contract.md` 5장 `answer_delta` 행) 종결 부호 뒤 공백이 조각으로 남기 쉽다. `handoff.delta_lines` 가 다듬은 뒤 빈 조각을 버리는 것으로 끝난다. 한 줄이다.

### 3-3. `error` 뒤에 `done` 을 보내는지가 계약에 없다

`docs/03-api-contract.md` 5장은 `error` 이벤트를 157행에 적어 두고, 158행의 `done` 은 "정상 또는 복구 가능한 fallback 종료"라고만 적는다. 160~161행의 순서 설명에도 `error` 가 없다 — 기본 순서 문장은 `status` 부터 `done` 까지만 열거하고, 161행은 "정상 또는 복구 가능한 fallback은 `done`으로 끝낸다"로 끝난다. **`error` 가 그 "복구 가능한 fallback" 에 들어가는지 아닌지가 적혀 있지 않다.**

`server/README.md` 85행도 같은 범위까지만 말한다 — "정상 또는 복구 가능한 fallback 경로는 종료 이벤트를 반드시 보낸다. 종료를 못 보내면 프론트는 입력창을 다시 열지 못하고, 사용자 입장에서는 화면이 멈춘 것과 같다." 그 문장이 `error` 를 덮는다고 읽을 근거가 없다.

결과는 이렇다. **`error` 를 받은 프론트는 입력창을 열 근거가 없다.** 입력창을 다시 여는 조건은 `done` 이고(157~158행의 프론트 반응 열), `error` 행의 반응은 "카드 유지 + 오류 문구 + 다시 시도 버튼"까지다. 다시 시도 버튼을 눌러도 입력창이 잠겨 있으면 사용자가 할 수 있는 일이 없다.

4장 1번을 구현할 때 **`error` 뒤에 `done` 을 보내는 것으로 함께 확정하기를 권고한다.** 근거는 `server/README.md` 3장 13단계의 "어떤 경우에도 반드시 보낸다"다. 답변 실패는 카드가 남는 부분 실패이므로 턴 자체는 끝난 것이고, 끝난 턴은 `done` 으로 닫는 것이 이벤트 뜻과 맞는다. **다른 선택을 버린 이유**: `error` 를 종료 이벤트로도 취급하면 프론트가 두 종류의 종료를 다루게 되고, 종료 처리 코드가 두 곳으로 갈린다. 지금 한 곳뿐인 것을 둘로 만드는 변경은 12:00 에 할 일이 아니다.

## 4. 서버에 요청하는 변경 (백엔드B 앞)

| 요청 | 파일 | 예상 분량 | 안 하면 무엇이 깨지는가 | 대안 |
| --- | --- | --- | --- | --- |
| 1. `error` 이벤트 추가 + `answer_deltas` `min_length` 제거, 그리고 `error` 뒤 `done` 확정 (3장) | `server/sse.py` 23~31·72~128행, `server/chat_service.py` 39행, `server/api/chat.py` 83~84행 | 3개 파일 약 13줄 | 답변 실패를 와이어에 실을 수 없다. 실패한 턴은 고정 문구를 정상 답변처럼 내보내거나(실패를 숨김) 스트림이 끊긴다. `docs/03-api-contract.md` 157행의 `error` 가 계속 미구현으로 남고, 3-3 의 입력창 잠김도 그대로다 | (나) `ChatPipelineResult.answer_failed` + 고정 문구 델타. 4줄로 끝나지만 프론트가 실패를 구분할 수 없다(3-1) |
| 2. `related` 칩 `id` 보존 | `server/sse.py` 72~73행 (`questions: list[str]` 을 `id`·`text` 쌍 목록으로, 또는 `question_ids` 를 병행) | 1개 파일 약 6줄 + 프론트 1곳 | 세션의 `shown` 이 영원히 빈 집합이라 `related.select` 의 중복 제거가 작동하지 않는다. 같은 칩이 매 턴 다시 뜨고, 누른 칩을 또 권한다. `docs/03-api-contract.md` 5-2·11장이 전제하는 경로가 시작되지 않는다 | 서버가 문구에서 번호를 되찾는 역매핑 표를 들기. 칩 문구가 두 곳에 생겨 `docs/09-value-naming-decision.md` 안 C 와 같은 위험을 만든다. 문구를 다듬으면 매핑이 조용히 깨진다 |
| 3. `AskableProfileField` 에 `planned_basis` 허용 여부 결정 | `server/schemas.py` 197~207행, 또는 340행 `field` 타입만 넓히기 | 1~3줄 | planned 확인 질문을 `followup` 으로 보낼 수 없다. "다음 학기에 휴학할 거예요"에 아무 질문도 뜨지 않고, `interpret.MergeResult`(924행)의 `held`(929행)에 쌓인 변경이 영원히 확인되지 않는다. `docs/03-api-contract.md` 11장의 기준 시점 기록도 시작되지 않는다 | enum 을 건드리지 않고 340행 `field` 타입만 넓히는 쪽. `AskableProfileField` 가 "물을 수 있는 **프로필 항목**"이라는 뜻을 유지하므로 이 대안을 권고한다. planned 전용 이벤트를 새로 만드는 것은 프론트가 후속 질문 카드를 한 모양으로만 그린다는 전제(`questions.py` 410~418행)를 깬다 |
| 4. `/chat` 요청에 후속 질문 답변·건너뛰기 필드 | `server/schemas.py` 356~359행 `ChatRequest`, `server/api/chat.py` 114~152행 | 약 10줄 | 현재 `ChatRequest` 는 `session_id`/`message`/`client_message_id` 뿐이라 버튼 답변을 `message` 문장으로 밀어넣는 수밖에 없다. 그러면 해석 단계를 타므로 **`server/README.md` 70행이 약속한 해석 건너뛰기 3초 절약이 사라진다.** `docs/02-requirements-ears.md` FR05(37~44행)는 건너뛰기를 요구하는데, `message` 가 `min_length=1`(schemas.py 357행)이라 건너뛰기를 빈 메시지로 표현할 수도 없다 | 라벨 대신 값(`parents`)을 `message` 에 넣기. 허용 값은 통과하지만 해석을 여전히 타고, 사용자 메시지 기록에 코드 값이 섞인다. 건너뛰기는 표현할 방법이 여전히 없다 |
| 5. 각주·정책을 `model_dump(mode="json")` dict 로 넘기기 (`handoff.as_plain`) | `ai/conversation/handoff.py` 를 부르는 서버 쪽 호출 2곳 | 호출 2줄 | 2-4·2-5 그대로다. 답변이 통째로 버려지거나 "찾지 못했어요"로 뒤집히고, 마감 강조 문장과 마감 칩이 조용히 사라진다. **예외도 로그도 없다** | 없다. AI A 쪽을 pydantic 모델을 받게 고치는 방법도 있지만, `ai/conversation` 이 pydantic 을 알게 되면 모듈 경계가 사라진다. `interpret.py` 가 선언한 "로직으로만 검증한다"가 깨진다 |

1·3·4 는 **결정**이 필요하고, 2 는 결정과 구현이 함께 필요하며, 5 는 결정 없이 구현만 하면 된다. 5 가 가장 싸고 가장 위험하다.

## 5. AI A 가 지키는 것

서버에 요청하지 않고 AI A 쪽에서 이미 정한 것들이다. 4장의 결정이 어느 쪽으로 나도 이것은 바뀌지 않는다.

| 정한 것 | 근거 | 확인 방법 |
| --- | --- | --- |
| **`region` 변경은 프로필에 반영하지 않고 버린다.** `handoff.profile_update_event` 가 `changed_fields` 를 만들 때 `region` 키를 빼고 탈락 기록으로 남긴다 | `Profile.region` 은 `Literal[Region.SEOUL]`(`server/schemas.py` 166행)이라 다른 값은 422 로 거부되고(`server/tests/test_schemas.py` 59~76행), `ProfileField`(181~194행)에 `region` 이 없어 `changed_fields` 키로도 쓸 수 없다. `docs/01-glossary-profile.md` 2장 27행이 "입력 안 함, `seoul` 고정"으로 정했다 | `region` 변경이 담긴 메시지를 보냈을 때 `profile_update` 프레임에 `region` 이 없고 스트림이 `done` 까지 간다 |
| **정책·각주는 dict 로만 받는다.** pydantic 모델 객체를 받지 않고, 받았을 때 동작을 보정하지도 않는다 | 2-4·2-5. 모델을 받아 주려면 `ai/conversation` 이 pydantic 을 알아야 하고, 그러면 `interpret.py` 가 선언한 "LLM 도 프레임워크도 없이 로직으로만 검증한다"가 깨진다. 모듈 경계가 사라지는 비용이 호출 한 줄보다 크다 | `known_footnote_ids` 결과가 빈 집합이 아닌지. `build_skeleton` 요약이 "찾지 못했어요"가 아닌지 |
| **모르는 값은 매핑하지 않고 탈락 기록으로 남긴다.** 이름을 바꿔 주거나 비슷한 값으로 옮기지 않는다 | `interpret.py` 실패 방침, `pipeline.unknown_items_from` 의 `not_in_field_table`. 허용 값 밖 값을 프로필에 넣으면 규칙 엔진이 모르는 값이 박혀 판정이 그대로 미확인으로 남는다 — `questions.py` 123~140행이 "건너뛰기보다 나쁘다"로 적은 상태다. `docs/09-value-naming-decision.md` 안 C 를 버린 이유와 같다 | `finish_turn` 결과의 `unknown_items` 탈락 기록과 `Interpretation.dropped` 를 본다. 후속 질문이 안 뜰 때 원인이 표기 불일치인지 여기서 바로 보인다 |
| **질문·칩 문구는 표에서만 가져오고 AI 가 지어내지 않는다.** | `questions.TEMPLATES`(142~243행)와 `related.CHIPS`(76~106행)가 유일한 출처다. `CONTRIBUTING.md` 8장이 화면 문구를 표에서 관리하라고 정했고, 모델이 만든 칩은 우리가 답할 수 없는 것을 물을 수 있다(`related.py` 1~12행) | `followup` 의 `question`·`options[].label` 과 `related` 의 문구가 표에 있는 문자열과 글자까지 같은지 |

## 6. 통합 체크리스트

순서대로 한다. 1~3 은 결정 없이 지금 할 수 있고, 4 부터는 4장의 결정이 필요하다.

| # | 무엇을 | 담당 | 무엇을 보면 됐는지 아는가 |
| --- | --- | --- | --- |
| 1 | `ai/conversation/handoff.py` 를 커밋하고 여섯 쌍의 변환이 전부 그 안에 있는지 확인한다 | AI A | 2장의 여섯 쌍과 3장이 `as_plain`·`profile_update_event`·`related_event`·`related_chip_ids`·`followup_event`·`answer_failure_payload`·`delta_lines` 에 대응된다. 변환 코드가 `server/` 나 `pipeline.py` 에 흩어져 있지 않다 |
| 2 | 각주와 정책을 `model_dump(mode="json")` dict 로 넘기는 호출을 붙인다 (4장 5번) | 백엔드B + AI A | `finish_turn` 결과 `metrics` 의 `blocking_codes` 가 비어 있고, 답변 요약 문장이 카드 수와 같은 말을 한다. 카드 5장에 "찾지 못했어요"가 나오면 이 항목이 안 된 것이다 |
| 3 | `to_profile_update()` 세 키를 `changed_fields`/`message`/`profile` 로 접고, `region` 키를 뺀다. `before`·`label`·항목별 `notice` 를 버리는 결정을 기록한다 (2-1, 5장) | AI A | 프로필을 바꾸는 메시지를 보냈을 때 `profile_update` 프레임이 세 키만 담고 스트림이 `done` 까지 간다 |
| 4 | 델타를 만들 때 다듬은 뒤 빈 조각을 버린다 (3-2) | AI A | 종결 부호 뒤 공백이 남는 본문으로 한 번 호출해 `ValidationError` 없이 끝까지 스트리밍된다 |
| 5 | `related` 칩 `id` 를 보존할지 결정하고 반영한다. 빈 칩 목록은 `None` 으로 접는다 (4장 2번, 2-3) | 백엔드B + 프론트 | 같은 상황을 두 턴 연속 만들었을 때 같은 칩이 다시 뜨지 않는다. 그리고 칩이 없는 턴에 `related` 프레임이 아예 나가지 않는다 |
| 6 | 답변 실패 표현을 결정하고 반영한다. `error` 뒤 `done` 여부도 함께 적는다 (4장 1번, 3장) | 백엔드B + 프론트 | 금지 표현이 든 답변을 일부러 만들어 `answer_failed` 를 유발했을 때, 화면에 오류 문구와 다시 시도 버튼이 뜨고 입력창이 다시 열린다. 카드는 남아 있다 |
| 7 | `planned_basis` 를 `followup` 으로 보낼지 결정하고 반영한다 (4장 3번, 2-2) | 백엔드B + AI A | "다음 학기에 휴학할 거예요"를 보냈을 때 `followup` 프레임이 나온다. 안 보내기로 정했다면 `MergeResult.held` 를 무엇으로 처리하는지 문서에 적혀 있다 |
| 8 | `/chat` 요청에 후속 질문 답변·건너뛰기 필드를 넣을지 결정한다 (4장 4번) | 백엔드B + 프론트 | 버튼을 눌렀을 때 3단계(메시지 해석)가 실행되지 않는다. 단계별 소요 시간 기록(`docs/03-api-contract.md` 13장)에서 해석 단계가 빠졌는지로 확인한다 |
| 9 | 7장의 문서 세 곳을 담당자가 고쳤는지 확인한다 | 백엔드B | `frontend/README.md` 에 거주지 토글이 없고, `data/profiles/README.md` P5 가 서울 안 프로필이며, `.kiro/steering/02-roles-common.md` 필수 항목이 3개다 |
| 10 | 데모 경로를 처음부터 끝까지 한 번 통과시킨다 | 전원 | 프레임 순서가 `status → profile_update → policies → status → answer_delta… → footnotes → followup → related → done` 이고 마지막이 `done` 이다(`docs/03-api-contract.md` 160행). 중간에 끊긴 스트림이 한 번도 없다 |

2 번이 앞에 오는 이유는, 이것만 결정 없이 지금 할 수 있고 안 했을 때 **아무 오류 없이** 답변이 사라지기 때문이다. 나머지 실패는 스트림이 끊기거나 이벤트가 안 오는 형태로 눈에 보이지만, 2 번은 정상 동작처럼 보인다. 12:00 통합에서 이 한 가지만 놓치면 오후 내내 "답변이 왜 안 나오지"를 찾게 된다.

## 7. 문서 밖에서 발견한 어긋남 (다른 담당자 앞)

`region` 이 "입력 안 함, `seoul` 고정"으로 정해진 것(`docs/01-glossary-profile.md` 27행)을 따라가지 않은 문서가 세 곳 있다. **이 문서는 그 파일을 고치지 않았다.** 소유자가 고칠 목록으로만 남긴다(`CONTRIBUTING.md` 6장).

| 문서 | 줄 | 지금 적힌 것 | 무엇과 어긋나는가 | 누가 고친다 |
| --- | --- | --- | --- | --- |
| `frontend/README.md` | 46행 | 거주지 항목이 "서울 / 서울 외 토글. 서울 선택 시 자치구 드롭다운(선택)" | `ProfileInput`(`server/schemas.py` 147~156행)에 `region` 필드가 없고, 보내면 422 다 | 프론트 |
| `frontend/README.md` | 51·53행 | 시작 버튼이 "필수 4개"를 요구하고, 53행이 그 4개를 "나이, 거주지, 현재 상태, 관심 분야"로 못 박음 | 폼이 받는 필수 항목은 `age`·`status`·`categories` 세 개다(`ProfileInput` 148·150·151행. `district` 와 `income_bracket` 은 선택) | 프론트 |
| `data/profiles/README.md` | 17행 | P5 가 "만 21세, 서울 외, 재학, 관심 분야 전체" | 서울 밖 프로필을 서버가 만들 수 없다. `Profile.region` 은 `seoul` 하나다(166행) | 백엔드A |
| `data/profiles/README.md` | 21행 | 값 적는 방법 설명에 "거주지는 `seoul` 또는 `outside_seoul`로" | `Region`(`server/schemas.py` 30~31행)에 `outside_seoul` 이 없다 | 백엔드A |
| `.kiro/steering/02-roles-common.md` | 40행 | 거주지가 "필수"이고 허용 값에 "서울 외"가 있음 | 위와 같다. 41~42행의 현재 상태·관심 분야는 맞다 | 백엔드B |
| `.kiro/steering/02-roles-common.md` | 109·143행 | 109행 규칙 설명이 "서울 외 거주인데 서울 정책"을 미충족 예시로 들고, 143행 P5 가 "서울 외" | 서울 밖 사용자가 존재하지 않으므로 그 미충족 경로 자체가 생기지 않는다 | 백엔드B |

두 가지를 덧붙인다.

**`frontend/README.md` 대로 폼을 만들면 온보딩 제출이 전건 실패한다.** `server/tests/test_schemas.py` 59~76행 `test_region_request_field_returns_422` 가 `region` 이 섞인 `POST /session` 요청을 이미 422 로 거부하고, 32~34행은 `ProfileInput` 자체가 `region` 키를 받지 않는 것을 검증한다. `ContractModel` 이 `extra="forbid"`(`server/schemas.py` 27행)이므로 토글 값을 보내는 순간 전건이 `invalid_input` 이다. 일부 요청만 실패하는 것이 아니다.

**`.kiro/steering/02-roles-common.md` 는 Kiro 에 자동 적용되는 파일이다.** 파일 머리의 프런트매터가 `inclusion: always` 다. 그래서 이 문서가 틀린 항목 표를 들고 있는 동안에는 **생성되는 코드에 거주지 토글과 "서울 외" 값이 계속 번진다.** 다른 세 문서는 사람이 읽고 틀릴 수 있는 정도지만, 이 파일은 읽지 않아도 섞여 들어간다. 세 곳 중 먼저 고칠 것을 하나만 고르면 이 파일이다.
