# 10. AI A → 서버 handoff

## 목적

AI A의 내부 결과를 서버 공개 DTO로 검증해 전달한다. 변환은 `ai/conversation/handoff.py`에 둘 수 있지만, 서버가 최종 Pydantic 검증을 수행한다. 공개 wire 계약은 항상 `docs/03-api-contract.md`가 기준이다.

## 확정 매핑

| AI A 결과 | 서버 SSE payload |
| --- | --- |
| `profile_update.changes` | `profile_update.changes` |
| `profile_update.notice` | `profile_update.notice` |
| `profile_update.profile` | `profile_update.profile` |
| `related.chips[].id`, `text` | `related.chips[].id`, `text` |
| 일반 후속 질문 | `followup` |
| 미래 계획 확인 질문 | `followup.field = planned_basis` |

`profile_update`에서 `before`, `after`, `label`, 항목별 `notice`를 버리지 않는다. 관련 질문은 텍스트만 보내지 않으며 `id`를 세션의 중복 제거 키로 보존한다.

## 예정 변경

AI A가 `timing=planned` 변경을 읽으면 서버는 현재 프로필을 바꾸지 않고 보류 변경으로 세션에 저장한다. 서버는 일반 후속 질문보다 `planned_basis` 질문을 먼저 한 번 보낸다.

- `planned`: 보류 변경을 평가용 프로필에만 overlay한다.
- `current` 또는 skip: 보류 변경을 버리고 현재 기준을 유지한다.

## 실패 처리

AI 출력이 서버 DTO 검증에 실패하거나 스트림 도중 복구 가능한 실패가 나면 서버는 안전한 `error { code, message }`를 보낸 뒤 `done`으로 종료한다. AI A 변환기는 임의의 키·값 보정이나 지역(`region`) 변경을 만들지 않는다.
