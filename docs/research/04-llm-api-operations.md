# LLM API 운용

> 조사 목적: Claude 또는 GPT 중 무엇을 받아도 통하는 공통 방침을 정한다. 호출 지점은 메시지 해석·예외 조건 판정·답변 작성 세 곳이다.
> 조사일 2026-09-20. **기능 지원 여부는 시점에 따라 바뀐다. 본선 당일 공식 문서로 다시 확인한다.**

## 한 줄 결론

- **어느 쪽을 받아도 스키마 강제는 쓸 수 있다.** 두 제공사 모두 제약 디코딩 기반 기능을 제공한다. 프롬프트로 "JSON만 달라"고 부탁하는 방식으로 돌아가지 않는다.
- **호출부를 한 군데로 모은다.** 모델 이름, 스키마 강제 방식, 스트리밍 처리, 재시도를 한 파일에서만 다룬다. 당일 모델이 바뀌어도 그 파일만 고친다.
- **재시도는 1회.** 20초 상한 안에서 두 번 이상 재시도할 시간이 없다. 실패는 미확인으로 흡수한다.
- **속도 제한과 용량 부족을 구분한다.** 전자는 우리가 요청을 줄이면 해결되고, 후자는 기다릴 수밖에 없다. 후자에 걸리면 데모 모드로 넘긴다.
- **긴 원문은 구분자로 감싸고 지시문은 앞과 끝에 둔다.** 그리고 캐시가 되는지 확인한다. 우리는 같은 정책 원문을 반복 전송한다.

## 제공사 비교

| 기능 | Claude | GPT | 우리에게 중요한 점 |
| --- | --- | --- | --- |
| 스키마 강제 | 구조화된 출력 기능을 제공하고, 스키마나 도구 정의 중 한 방식으로 쓸 수 있다 ([공식 문서](https://platform.claude.com/docs/en/build-with-claude/structured-outputs), [발표](https://websitemain.claude.com/blog/structured-outputs-on-the-claude-developer-platform)) | 엄격 모드로 스키마 준수를 보장한다. 응답 형식 지정과 함수 호출 양쪽에서 쓸 수 있다 ([발표](https://openai.com/index/introducing-structured-outputs-in-the-api/), [가이드](https://developers.openai.com/api/docs/guides/structured-outputs)) | 예외 조건 판정에 쓴다. 어느 쪽이든 결과를 코드가 바로 받을 수 있다 |
| 엄격 모드 제약 | — | 모든 속성을 필수 목록에 넣어야 하고, 추가 속성을 막아야 하며, 일부 키워드는 지원하지 않는다 ([정리](https://docs.merge.dev/merge-gateway/capabilities/structured-outputs)) | 선택 항목을 그냥 빼는 설계를 못 쓴다. "필요한 추가 항목"처럼 없을 수도 있는 값은 빈 값을 허용하는 형태로 만든다 |
| 프롬프트 캐싱 | 표시한 접두사까지 캐시한다. 최소 토큰 수를 넘겨야 적용되고, 접두사가 한 토큰이라도 다르면 미적용 ([게이트웨이 설명](https://orqai.mintlify.app/docs/ai-gateway/features/prompt-caching), [최소 길이 정리](https://docs.inkeep.com/guides/observability/prompt-caching)) | 캐시 읽기가 있다 | 정책 원문을 반복 전송하므로 효과가 크다. **원문을 프롬프트 앞쪽 고정 위치에 두어야** 캐시가 걸린다 |
| 캐시 미적용 시 동작 | 조건을 못 맞추면 오류 없이 일반 요청으로 처리된다 ([설명](https://help.apiyi.com/en/claude-prompt-caching-not-hit-minimum-token-troubleshooting-en.html)) | — | **조용히 실패한다.** 캐시가 걸렸는지 응답의 토큰 정보로 확인해야 한다 |
| 속도 제한 오류 | 분당 요청 수와 분당 토큰 수 초과 시 429. 재시도 대기 시간을 알려 주는 헤더가 있다 ([정리](https://www.rapidevelopers.com/ai-build-errors/error-429-too-many-requests)) | 유사 | 후보별 동시 판정이 여기 걸릴 수 있다 |
| 용량 부족 오류 | 529로 구분된다. 계정 사용량과 무관한 환경 문제이고 언제 풀리는지 알려 주는 헤더가 없다 ([설명](https://medium.com/@reshtei/anthropic-529-overloaded-error-what-it-means-and-how-to-handle-it-in-production-98f476b2e69e), [현상 정리](https://www.ssdnodes.com/learn/claude-code-model-overloaded-error)) | 유사한 과부하 응답 | **재시도로 해결되지 않는다.** 데모 중 이게 나면 데모 모드로 넘긴다 |
| 서버 오류 재시도 권고 | 지수 백오프로 재시도하라고 명시 ([오류 문서](https://docs.anthropic.com/en/api/errors?)) | 유사 | 우리는 시간 예산 때문에 1회로 제한한다 |

> 위 표의 최소 토큰 수, 오류 코드, 지원 범위는 변할 수 있다. 10:30에 캠프에서 어느 모델을 받는지 확정한 뒤 해당 공식 문서를 열어 이 표를 갱신한다.

## 1. 스키마 강제

프롬프트로 형식을 부탁하는 것과 스키마를 강제하는 것은 다르다. 제약 디코딩 방식은 스키마를 위반하는 토큰이 애초에 나올 수 없게 만든다([방식 설명](https://docs.aihubmix.com/en/api/Structured-Output)). Claude 문서도 스키마 준수가 필요하면 프롬프트 기법 대신 구조화된 출력을 쓰라고 안내한다([출력 일관성 문서](https://docs.anthropic.com/en/docs/test-and-evaluate/strengthen-guardrails/keep-claude-in-character)).

다만 스키마를 보장받아도 **내용이 맞다는 보장은 아니다.** 목록이나 복합 객체가 들어가면 난도가 올라간다는 평가도 있다(`docs/research/02-grounding-and-citation.md` 3절).

**우리 호출 지점에 주는 함의**

| 호출 지점 | 스키마 강제 | 이유 |
| --- | --- | --- |
| 메시지 해석 | 쓴다 | 의도와 프로필 변경을 코드가 바로 받는다. 항목이 적고 얕다 |
| 예외 조건 판정 | 쓴다 | 조건 목록을 받아 검증기로 넘긴다. 조건당 항목 네 개, 중첩 얕게 유지 |
| 답변 작성 | 쓰지 않는다 | 자연어를 스트리밍한다. 형식 강제가 문장 품질을 떨어뜨린다 |

엄격 모드가 모든 속성을 필수로 요구하는 제약이 있으므로, "미확인일 때만 채우는 필요한 추가 항목"은 **항목을 빼는 방식이 아니라 빈 값을 허용하는 방식**으로 설계한다.

## 2. 스트리밍

답변 작성만 스트리밍한다. 나머지 두 호출은 결과를 한 번에 받는다.

| 항목 | 방침 |
| --- | --- |
| 첫 조각 도착 | 우리 목표는 3초. 시스템 지시문과 정책 원문을 다 넣고도 첫 문장이 3초 안에 와야 한다. 캐싱이 걸리면 유리하다 |
| 스트리밍 중 오류 | 이미 보낸 조각은 화면에 남는다. 중간에 끊기면 답변 실패 이벤트를 보내고 카드를 유지한다 |
| 부분 응답 | 버리지 않는다. 문장 단위로 이미 붙였으므로 그대로 둔다 |
| 클라이언트에 전달 | 우리 서버가 받아서 다시 우리 프론트로 흘린다. 모델 응답 형식을 프론트에 그대로 노출하지 않는다 |

프론트가 S3에 올라가므로 **스트리밍이 교차 출처에서 실제로 뚫리는지 12:00 통합 때 확인한다.** 일반 요청은 되는데 스트리밍만 막히는 경우가 흔하다.

## 3. 시간 제한과 재시도

| 상황 | 재시도 | 이유 |
| --- | --- | --- |
| 출력 형식이 깨짐 | 1회 | 같은 입력에 형식만 틀린 경우가 있어 한 번은 가치가 있다 |
| 시간 초과 (정책당 8초) | 하지 않음 | 재시도하면 또 8초가 든다. 20초 상한을 넘긴다 |
| 속도 제한 오류 | 하지 않음. 요청 수를 줄인다 | 대기 시간이 우리 예산보다 길다. 후보를 3건으로 줄이는 것이 빠르다 |
| 용량 부족 오류 | 하지 않음 | 재시도로 해결되지 않는다. 데모 모드로 넘긴다 |
| 서버 오류 | 1회 | 공식 권고는 지수 백오프지만 우리 예산에서는 한 번이 한계다 |

공식 문서는 지수 백오프를 권고하고 대기 시간 헤더를 참고하라고 안내한다([오류 문서](https://docs.anthropic.com/en/api/errors?), [속도 제한 대응 정리](https://www.aifreeapi.com/en/posts/fix-claude-api-429-rate-limit-error)). 우리는 이 권고를 알면서도 **의도적으로 1회로 제한한다.** 사용자를 20초 넘게 기다리게 하는 것보다 미확인으로 표시하는 것이 낫다는 판단이다. 이 결정을 문서에 남겨 두는 이유는, 나중에 왜 백오프를 안 넣었냐는 질문에 답하기 위해서다.

## 4. 병렬 호출과 한도

우리는 예외 조건 판정을 후보별로 동시에 보낸다. 후보가 5건이면 동시 요청 5개다.

| 위험 | 대응 |
| --- | --- |
| 분당 요청 수 초과 | 동시 요청 수를 후보 수만큼 무조건 늘리지 않고 상한을 둔다 |
| 분당 토큰 수 초과 | 정책 원문이 길어 토큰 한도에 먼저 걸릴 가능성이 있다. 후보를 3건으로 줄인다 |
| 조율되지 않은 요청 폭주가 429의 주된 원인이라는 지적 | 팀원 5명이 같은 키를 쓰면 개발 중에도 한도에 걸린다. **개발 단계에서 키를 나눠 쓰는지 10:30에 확인한다** |
| 캐시된 토큰이 한도 계산에서 유리하게 처리될 수 있다는 정리 | 캐싱을 적용하면 처리량 측면에서도 이득이 있다 ([정리](https://www.aifreeapi.com/en/posts/claude-api-429-error-fix)) |

관련 근거: [속도 제한의 주된 원인](https://markaicode.com/errors/anthropic-api-rate-limit-exceeded-fix/), [429 대응 방법](https://www.rapidevelopers.com/ai-build-errors/error-429-too-many-requests)

## 5. 프롬프트 캐싱

우리는 같은 정책 원문을 여러 번 보낸다. 사용자가 후속 질문에 답할 때마다 같은 후보를 다시 판정하기 때문이다. 캐싱이 가장 잘 맞는 형태다.

| 조건 | 내용 | 우리가 할 것 |
| --- | --- | --- |
| 접두사 일치 | 앞부분이 한 토큰이라도 다르면 적용되지 않는다 ([설명](https://trigger.dev/docs/ai-chat/prompt-caching), [실무 정리](https://dev.to/thegdsks/prompt-caching-with-the-claude-api-a-practical-guide-14ce)) | 프로필처럼 매번 바뀌는 값을 **원문보다 뒤에** 둔다 |
| 최소 길이 | 표시한 내용이 최소 토큰 수를 넘어야 적용된다 ([최소 길이 정리](https://docs.inkeep.com/guides/observability/prompt-caching)) | 정책 원문은 충분히 길다. 짧은 정책은 캐시가 안 걸릴 수 있음을 감안 |
| 순서 | 시스템 지시문, 도구 정의, 첫 메시지 순서가 같아야 한다 | 지시문을 동적으로 조립하지 않는다 |
| 조용한 미적용 | 조건을 못 맞추면 오류 없이 일반 처리된다 | 응답의 토큰 정보로 캐시 적용 여부를 확인한다 |
| 만료 | 비활성 기간이 지나면 만료된다 | 데모 직전 한 번 예열 호출을 해 둔다 |

**주의**: 캐싱은 "있으면 좋은 것"이다. 10:30에 확인해서 안 되면 그냥 안 쓴다. 이것 때문에 시간을 쓰지 않는다.

## 6. 긴 컨텍스트 배치

| 권고 | 내용 | 자료 |
| --- | --- | --- |
| 여러 문서를 넣을 때는 각 문서를 태그로 감싸고 본문과 출처를 하위 태그로 구분한다 | 문서 구조화 | [장문 컨텍스트 팁](https://docs.anthropic.com/es/docs/build-with-claude/prompt-engineering/long-context-tips) |
| 지시문·맥락·예시·입력이 섞이는 프롬프트에서는 태그로 구분하는 것이 도움이 된다. 구조가 없을 때는 오히려 잡음이다 | 조건부 권고 | [태그 사용 기준](https://www.claudexml.com/), [태그 사용 문서](https://docs.anthropic.com/pt/docs/build-with-claude/prompt-engineering/use-xml-tags) |
| 읽는 사람이 지침과 정책과 데이터를 구분할 수 없으면 모델도 못 한다 | 판단 기준 | [프롬프트 작성 원칙 정리](https://movez.substack.com/i/198832782/) |
| 관련 정보가 중간에 있으면 성능이 떨어진다 | 위치 효과 | `docs/research/02-grounding-and-citation.md` 5절 |

**우리 방침**: 예외 조건 판정 프롬프트는 네 부분으로 나눈다. 판정 규칙(지시문) → 정책 원문(구분자로 감쌈) → 프로필 → 출력 형태와 금지 사항. 원문을 가운데에 두고 **지켜야 할 규칙을 앞과 끝에 두 번 둔다.** 캐싱을 쓰면 원문이 앞쪽 고정 위치로 가야 하므로, 그때는 순서를 원문 → 규칙 → 프로필로 바꾸고 금지 사항을 끝에 둔다.

## 7. 온도와 결정성

판정은 같은 입력에 같은 결과가 나오는 것이 바람직하다. 낮은 온도를 쓴다. 다만 **완전한 재현성은 보장되지 않는다.** 그래서 정답셋 채점을 두 번(13:00~14:30, 14:30 직전) 하는 현재 계획이 맞다. 한 번 통과했으니 됐다고 넘기지 않는다.

답변 작성은 판정을 바꾸지 않으므로 온도에 덜 민감하다. 다만 금지 표현이 섞이는 것을 막으려면 여기도 높게 두지 않는다.

## 8. 프롬프트를 바꿀 때 회귀를 잡는 법

평가 파이프라인을 세울 시간은 없다. 가장 가벼운 방법은 **고정된 소수의 케이스를 매번 같은 순서로 돌려 보는 것**이다. 우리는 이미 그 케이스를 갖고 있다.

| 케이스 | 무엇을 잡는가 | 담당 |
| --- | --- | --- |
| 해석 테스트 10개 | 메시지 해석 회귀 | AI A |
| 대화 평가 C1~C8 | 후속 질문·답변·프로필 갱신 회귀 | AI A |
| 판정 평가 J1~J8 | 예외 조건 판정 회귀, 특히 미충족 오판 | AI B |
| 대표 프로필 P1~P5 | 추천 정확도와 판정 일치율 | 백엔드A |

프롬프트를 고친 뒤 이 중 자기 담당 케이스를 다시 돌린다. 통과하지 못하면 고친 것을 되돌린다. 이게 우리가 감당할 수 있는 유일한 회귀 검사다.

## 호출 지점별 방침

| 호출 지점 | 시간 예산 | 출력 형태 | 실패 시 | 재시도 |
| --- | --- | --- | --- | --- |
| 메시지 해석 | 3초 | 스키마 강제 | 프로필 변경 없이 진행 | 1회 (형식 오류만) |
| 예외 조건 판정 | 정책당 8초 | 스키마 강제 | 그 정책의 예외 조건 전체를 미확인 | 1회 (형식 오류만). 시간 초과는 재시도 없음 |
| 답변 작성 | 첫 문장 3초, 전체는 20초 상한 안 | 자연어 스트리밍 | 답변 실패 이벤트, 카드 유지 | 없음 |

공통: 모델에게 날짜를 계산시키지 않는다. 판정 상태는 코드가 확정한다. 모델이 낸 발췌는 로직으로 원문과 대조한다.

## 모델 교체에 대비하는 방법

당일 어느 모델을 받을지 10:30에 정해진다. 그 전에 구조를 정해 두면 교체 비용이 거의 없다.

| 원칙 | 왜 |
| --- | --- |
| 모델 호출을 한 군데에서만 한다 | 모델 이름, 스키마 강제 방식, 스트리밍 처리, 재시도, 캐싱 표시가 전부 제공사마다 다르다. 흩어 두면 교체가 아니라 재작성이 된다 |
| 모델 이름을 코드에 박지 않는다 | 설정에서 읽는다 |
| 세 호출 지점은 "무엇을 받아 무엇을 돌려주는가"로만 정의한다 | 내부에서 어느 제공사를 쓰는지 다른 모듈이 몰라야 한다 |
| 실패를 부르는 쪽이 아니라 호출부에서 흡수한다 | 시간 초과와 형식 오류를 각 모듈이 따로 처리하면 규칙이 갈린다 |
| 두 제공사에 공통인 기능만 쓴다 | 한쪽에만 있는 기능에 의존하면 교체 시 설계가 바뀐다 |

## 출처

- Claude: [구조화된 출력](https://platform.claude.com/docs/en/build-with-claude/structured-outputs), [구조화된 출력 발표](https://websitemain.claude.com/blog/structured-outputs-on-the-claude-developer-platform), [출력 일관성](https://docs.anthropic.com/en/docs/test-and-evaluate/strengthen-guardrails/keep-claude-in-character), [오류와 재시도](https://docs.anthropic.com/en/api/errors?), [장문 컨텍스트 팁](https://docs.anthropic.com/es/docs/build-with-claude/prompt-engineering/long-context-tips), [태그로 프롬프트 구조화](https://docs.anthropic.com/pt/docs/build-with-claude/prompt-engineering/use-xml-tags)
- GPT: [구조화된 출력 발표](https://openai.com/index/introducing-structured-outputs-in-the-api/), [구조화된 출력 가이드](https://developers.openai.com/api/docs/guides/structured-outputs), [소개 예제](https://cookbook.openai.com/examples/structured_outputs_intro)
- 제약과 실무: [엄격 모드 제약 정리](https://docs.merge.dev/merge-gateway/capabilities/structured-outputs), [제약 디코딩 동작 설명](https://docs.aihubmix.com/en/api/Structured-Output)
- 캐싱: [접두사 캐시 동작](https://trigger.dev/docs/ai-chat/prompt-caching), [최소 길이](https://docs.inkeep.com/guides/observability/prompt-caching), [표시 방식](https://orqai.mintlify.app/docs/ai-gateway/features/prompt-caching), [조용한 미적용](https://help.apiyi.com/en/claude-prompt-caching-not-hit-minimum-token-troubleshooting-en.html), [실무 주의점](https://dev.to/thegdsks/prompt-caching-with-the-claude-api-a-practical-guide-14ce)
- 한도와 과부하: [429 대응](https://www.rapidevelopers.com/ai-build-errors/error-429-too-many-requests), [속도 제한 원인](https://markaicode.com/errors/anthropic-api-rate-limit-exceeded-fix/), [캐싱과 처리량](https://www.aifreeapi.com/en/posts/claude-api-429-error-fix), [용량 부족 오류의 성격](https://medium.com/@reshtei/anthropic-529-overloaded-error-what-it-means-and-how-to-handle-it-in-production-98f476b2e69e), [과부하 현상 정리](https://www.ssdnodes.com/learn/claude-code-model-overloaded-error)
- 프롬프트 구조: [태그 사용 기준](https://www.claudexml.com/), [작성 원칙 정리](https://movez.substack.com/i/198832782/)

> 위 출처의 내용은 라이선스 제약에 따라 요약·재구성했다. 기능 지원 범위와 수치는 변동되므로 당일 공식 문서에서 확인한다.
