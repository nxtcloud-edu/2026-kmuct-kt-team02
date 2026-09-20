/**
 * 민감정보 입력 감지 (FR15).
 *
 * 실제 마스킹은 서버가 AI 전송 전에 수행한다 (server/pii.py).
 * 프론트는 입력창 아래 안내를 띄우기 위해 같은 형식을 감지만 한다.
 * 감지한 값을 어디에도 저장하거나 기록하지 않는다.
 */

const PATTERNS = [
  /** 주민등록번호: 숫자 6자리 + 하이픈 또는 공백 + 숫자 7자리 */
  /\b\d{6}[-\s]\d{7}\b/,
  /** 계좌번호: 하이픈 포함 숫자 10~16자리 연속 */
  /\b\d[\d-]{8,18}\d\b/,
  /** 전화번호: 010으로 시작하는 11자리 */
  /\b010[-\s]?\d{4}[-\s]?\d{4}\b/,
];

/** 민감정보 형식이 보이면 true. 어떤 값인지는 돌려주지 않는다. */
export function looksSensitive(text: string): boolean {
  return PATTERNS.some((pattern) => pattern.test(text));
}
