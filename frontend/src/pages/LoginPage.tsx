import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Check, Eye, EyeOff } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Checkbox, TextField } from "@/components/ui/Field";
import { Card } from "@/components/ui/Surface";
import { Logo } from "@/components/brand/Logo";
import { useApp } from "@/providers/AppProvider";

const BRAND_POINTS = [
  "공식 공고 기반 판정 확인",
  "개인정보는 맞춤 안내에만 사용",
];

/**
 * 프론트 데모용 로그인 화면.
 *
 * 아이디와 비밀번호가 비어 있지 않으면 로그인 처리한다.
 * 인증 서버를 호출하지 않으며 비밀번호는 컴포넌트 밖으로 전달하거나 저장하지 않는다.
 */
export function LoginPage() {
  const navigate = useNavigate();
  const { signIn, showToast } = useApp();

  const [userId, setUserId] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [remember, setRemember] = useState(true);
  const [errors, setErrors] = useState<{ userId?: string; password?: string }>({});
  const [submitting, setSubmitting] = useState(false);

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();

    const next: { userId?: string; password?: string } = {};
    if (!userId.trim()) next.userId = "아이디를 입력해 주세요.";
    if (!password.trim()) next.password = "비밀번호를 입력해 주세요.";

    setErrors(next);
    if (Object.keys(next).length > 0) return;

    setSubmitting(true);
    // 데모용 지연 뒤 로컬 상태만 바꾼다. 비밀번호는 어디에도 전달하지 않는다.
    window.setTimeout(() => {
      signIn(userId.trim(), remember);
      setSubmitting(false);
      showToast("로그인했어요.");
      navigate("/mypage");
    }, 500);
  };

  return (
    <div className="mx-auto max-w-[1100px] px-4 py-10 sm:px-6 lg:px-8 lg:py-16">
      <Card className="overflow-hidden p-0">
        <div className="grid lg:grid-cols-[0.9fr_1.1fr]">
          {/* 왼쪽 브랜드 메시지 */}
          <div className="relative overflow-hidden bg-hero-gradient p-8 text-white sm:p-10">
            <div
              aria-hidden="true"
              className="absolute -right-10 -top-10 h-40 w-40 rounded-full bg-white/15 blur-2xl"
            />
            <Logo size={36} inverted />
            <h1 className="mt-8 text-3xl font-extrabold leading-tight tracking-tight sm:text-[2.25rem]">
              필요한 혜택을
              <br />
              내게 쏘다.
            </h1>
            <p className="mt-4 max-w-sm text-[0.9375rem] leading-relaxed text-white/85">
              로그인하면 저장한 조건으로 바로 찾아보고, 더 정확한 판정을 받을 수 있어요.
            </p>

            <ul className="mt-8 space-y-2.5">
              {BRAND_POINTS.map((point) => (
                <li key={point} className="flex items-start gap-2.5">
                  <span
                    aria-hidden="true"
                    className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-white/20"
                  >
                    <Check className="h-3 w-3" />
                  </span>
                  <span className="text-[0.9375rem] leading-relaxed text-white/90">
                    {point}
                  </span>
                </li>
              ))}
            </ul>
          </div>

          {/* 오른쪽 폼 */}
          <div className="p-8 sm:p-10">
            <h2 className="text-2xl font-bold text-ink-900">다시 만나서 반가워요</h2>
            <p className="mt-1.5 text-[0.9375rem] text-ink-600">
              쏘다 계정으로 로그인하면 맞춤 상담을 이어갈 수 있어요.
            </p>

            <form onSubmit={handleSubmit} noValidate className="mt-7 space-y-4">
              <TextField
                label="아이디"
                type="text"
                autoComplete="username"
                placeholder="아이디 입력"
                required
                value={userId}
                error={errors.userId}
                onChange={(event) => {
                  setUserId(event.target.value);
                  if (errors.userId) {
                    setErrors((previous) => ({ ...previous, userId: undefined }));
                  }
                }}
              />

              <TextField
                label="비밀번호"
                type={showPassword ? "text" : "password"}
                autoComplete="current-password"
                placeholder="비밀번호 입력"
                required
                value={password}
                error={errors.password}
                onChange={(event) => {
                  setPassword(event.target.value);
                  if (errors.password) {
                    setErrors((previous) => ({ ...previous, password: undefined }));
                  }
                }}
                trailing={
                  <button
                    type="button"
                    onClick={() => setShowPassword((value) => !value)}
                    aria-pressed={showPassword}
                    aria-label={showPassword ? "비밀번호 숨기기" : "비밀번호 표시"}
                    className="flex h-8 w-8 items-center justify-center rounded-lg text-ink-500 transition-colors hover:bg-canvas-200 hover:text-brand-700 focus-ring"
                  >
                    {showPassword ? (
                      <EyeOff aria-hidden="true" className="h-4 w-4" />
                    ) : (
                      <Eye aria-hidden="true" className="h-4 w-4" />
                    )}
                  </button>
                }
              />

              <div className="flex flex-wrap items-center justify-between gap-2">
                <Checkbox
                  label="로그인 상태 유지"
                  checked={remember}
                  onChange={(event) => setRemember(event.target.checked)}
                />
                <button
                  type="button"
                  onClick={() =>
                    showToast("데모 로그인에서는 비밀번호 찾기를 지원하지 않아요.", "error")
                  }
                  className="rounded text-[0.875rem] font-semibold text-brand-600 underline decoration-brand-200 decoration-2 underline-offset-2 transition-colors hover:text-brand-700 focus-ring"
                >
                  비밀번호 찾기
                </button>
              </div>

              <Button variant="solid" type="submit" size="lg" block loading={submitting}>
                로그인
              </Button>
            </form>

            <p className="mt-6 text-center text-[0.9375rem] text-ink-600">
              계정이 없어도 괜찮아요.{" "}
              <Link
                to="/"
                className="rounded font-bold text-brand-600 underline decoration-brand-200 decoration-2 underline-offset-2 transition-colors hover:text-brand-700 focus-ring"
              >
                로그인 없이 찾아보기
              </Link>
            </p>

            <p className="mt-4 rounded-xl border border-line-soft bg-canvas-50 px-4 py-3 text-[0.875rem] leading-relaxed text-ink-500">
              로그인 상태 유지 시 아이디만 이 브라우저에 저장해요. 비밀번호는 저장하거나
              전송하지 않아요.
            </p>
          </div>
        </div>
      </Card>
    </div>
  );
}
