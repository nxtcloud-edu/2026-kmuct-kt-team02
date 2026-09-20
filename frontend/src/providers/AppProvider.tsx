import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { ProfileInput, SessionCreateResponse } from "@/lib/contract";
import { STORAGE_KEYS, readJSON, removeKey, writeJSON } from "@/lib/storage";
import { isMockMode } from "@/lib/api";

/** 로그인 계정. 이름과 연락처는 담지 않는다 */
interface Auth {
  email: string;
}

interface Toast {
  id: number;
  message: string;
  tone: "success" | "error";
}

interface AppContextValue {
  /** 온보딩에서 입력한 프로필. 없으면 아직 온보딩을 안 한 상태 */
  profileInput: ProfileInput | null;
  /** 세션 생성 결과. 대화 화면이 이걸로 시작한다 */
  session: SessionCreateResponse | null;
  auth: Auth | null;
  ready: boolean;
  mockMode: boolean;
  saveProfileInput: (input: ProfileInput) => void;
  setSession: (session: SessionCreateResponse | null) => void;
  clearSession: () => void;
  signIn: (email: string, remember: boolean) => void;
  signOut: () => void;
  toast: Toast | null;
  showToast: (message: string, tone?: Toast["tone"]) => void;
  dismissToast: () => void;
}

const AppContext = createContext<AppContextValue | null>(null);

export function AppProvider({ children }: { children: ReactNode }) {
  const [profileInput, setProfileInput] = useState<ProfileInput | null>(null);
  const [session, setSessionState] = useState<SessionCreateResponse | null>(null);
  const [auth, setAuth] = useState<Auth | null>(null);
  const [ready, setReady] = useState(false);
  const [mockMode, setMockMode] = useState(true);
  const [toast, setToast] = useState<Toast | null>(null);

  useEffect(() => {
    const storedProfile = readJSON<ProfileInput | null>(STORAGE_KEYS.profileInput, null);
    if (storedProfile && typeof storedProfile.age === "number") {
      setProfileInput(storedProfile);
    }

    const storedAuth = readJSON<Partial<Auth>>(STORAGE_KEYS.auth, {});
    if (storedAuth.email) setAuth({ email: storedAuth.email });

    void isMockMode().then(setMockMode);
    setReady(true);
  }, []);

  const showToast = useCallback((message: string, tone: Toast["tone"] = "success") => {
    setToast({ id: Date.now(), message, tone });
  }, []);

  const dismissToast = useCallback(() => setToast(null), []);

  useEffect(() => {
    if (!toast) return;
    const timer = window.setTimeout(() => setToast(null), 3600);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const saveProfileInput = useCallback((input: ProfileInput) => {
    setProfileInput(input);
    writeJSON(STORAGE_KEYS.profileInput, input);
  }, []);

  const setSession = useCallback((next: SessionCreateResponse | null) => {
    setSessionState(next);
  }, []);

  const clearSession = useCallback(() => setSessionState(null), []);

  const signIn = useCallback((email: string, remember: boolean) => {
    const next: Auth = { email };
    setAuth(next);
    if (remember) writeJSON(STORAGE_KEYS.auth, next);
    else removeKey(STORAGE_KEYS.auth);
  }, []);

  const signOut = useCallback(() => {
    setAuth(null);
    removeKey(STORAGE_KEYS.auth);
    showToast("로그아웃했어요.");
  }, [showToast]);

  const value = useMemo<AppContextValue>(
    () => ({
      profileInput,
      session,
      auth,
      ready,
      mockMode,
      saveProfileInput,
      setSession,
      clearSession,
      signIn,
      signOut,
      toast,
      showToast,
      dismissToast,
    }),
    [
      profileInput,
      session,
      auth,
      ready,
      mockMode,
      saveProfileInput,
      setSession,
      clearSession,
      signIn,
      signOut,
      toast,
      showToast,
      dismissToast,
    ],
  );

  return <AppContext.Provider value={value}>{children}</AppContext.Provider>;
}

export function useApp(): AppContextValue {
  const ctx = useContext(AppContext);
  if (!ctx) throw new Error("useApp은 AppProvider 안에서만 쓸 수 있어요.");
  return ctx;
}
