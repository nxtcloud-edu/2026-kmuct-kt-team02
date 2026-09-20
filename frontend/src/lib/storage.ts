/**
 * 브라우저 저장소 헬퍼.
 *
 * 저장하는 것: 온보딩 프로필 값(나이·상태·관심 분야·소득 구간·자치구), 로그인 아이디.
 * 저장하지 않는 것: 비밀번호, 이름, 연락처, 주민등록번호, 계좌번호, 사용자 메시지 원문.
 * (금지 사항: docs/05-interfaces.md 7장)
 */

export const STORAGE_KEYS = {
  profileInput: "sodda.profile_input",
  auth: "sodda.auth",
} as const;

export function readJSON<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return fallback;
    const parsed = JSON.parse(raw) as T;
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return { ...fallback, ...parsed };
    }
    return parsed;
  } catch {
    return fallback;
  }
}

export function writeJSON(key: string, value: unknown): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // 저장 공간 제한 등은 조용히 무시한다
  }
}

export function removeKey(key: string): void {
  try {
    window.localStorage.removeItem(key);
  } catch {
    // 무시
  }
}
