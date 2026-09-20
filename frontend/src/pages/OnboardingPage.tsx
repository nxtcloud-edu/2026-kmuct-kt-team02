import { useEffect, useMemo, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import { Info, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Chip, OptionGroup, SelectField, TextField } from "@/components/ui/Field";
import { Card } from "@/components/ui/Surface";
import { Logo } from "@/components/brand/Logo";
import { COPY, EXAMPLE_CHIPS } from "@/lib/labels";
import {
  AGE_MAX,
  AGE_MIN,
  CATEGORY_OPTIONS,
  DISTRICT_OPTIONS,
  INCOME_OPTIONS,
  STATUS_OPTIONS,
} from "@/lib/profileOptions";
import { createSession } from "@/lib/api";
import { useApp } from "@/providers/AppProvider";
import type {
  Category,
  District,
  IncomeBracket,
  ProfileInput,
  UserStatus,
} from "@/lib/contract";

/**
 * 온보딩 폼 + 첫 질문 (frontend/README.md 3-2, 3-3).
 *
 * 필수는 나이, 현재 상태, 관심 분야 세 개다.
 * 거주지는 서버가 서울로 고정하므로 폼에서 묻지 않는다
 * (server/schemas.py: region is deliberately not client-provided).
 */
export function OnboardingPage() {
  const navigate = useNavigate();
  const { profileInput, saveProfileInput, setSession, showToast } = useApp();

  const [age, setAge] = useState("");
  const [district, setDistrict] = useState<District | "">("");
  const [status, setStatus] = useState<UserStatus | "">("");
  const [categories, setCategories] = useState<Category[]>([]);
  const [income, setIncome] = useState<IncomeBracket>("unknown");
  const [question, setQuestion] = useState("");
  const [ageError, setAgeError] = useState<string>();
  const [submitting, setSubmitting] = useState(false);

  // 저장된 값이 있으면 채워 둔다
  useEffect(() => {
    if (!profileInput) return;
    setAge(String(profileInput.age));
    setDistrict((profileInput.district ?? "") as District | "");
    setStatus(profileInput.status);
    setCategories(profileInput.categories);
    setIncome(profileInput.income_bracket);
  }, [profileInput]);

  const ageNumber = Number(age);
  const ageValid =
    age.trim().length > 0 &&
    Number.isInteger(ageNumber) &&
    ageNumber >= AGE_MIN &&
    ageNumber <= AGE_MAX;

  const canSubmit = useMemo(
    () => ageValid && status !== "" && categories.length > 0 && !submitting,
    [ageValid, status, categories.length, submitting],
  );

  /** 전체를 고르면 나머지를 해제한다 */
  const toggleCategory = (value: Category) => {
    setCategories((prev) => {
      if (value === "all") return prev.includes("all") ? [] : ["all"];
      const withoutAll = prev.filter((item) => item !== "all");
      return withoutAll.includes(value)
        ? withoutAll.filter((item) => item !== value)
        : [...withoutAll, value];
    });
  };

  const start = async (firstMessage: string) => {
    if (!ageValid) {
      setAgeError(`만 ${AGE_MIN}세부터 ${AGE_MAX}세까지 입력해 주세요`);
      return;
    }
    if (status === "" || categories.length === 0) return;

    const input: ProfileInput = {
      age: ageNumber,
      district: district === "" ? null : district,
      status,
      categories,
      income_bracket: income,
    };

    setSubmitting(true);
    try {
      saveProfileInput(input);
      const session = await createSession(input);
      setSession(session);
      navigate("/chat", { state: { firstMessage } });
    } catch (error) {
      showToast(
        error instanceof Error ? error.message : COPY.answerFailed,
        "error",
      );
    } finally {
      setSubmitting(false);
    }
  };

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    void start(question.trim());
  };

  return (
    <div className="mx-auto max-w-[1000px] px-4 py-10 sm:px-6 lg:px-8">
      <header className="text-center">
        <Logo size={40} className="justify-center" />
        <h1 className="mt-5 text-2xl font-extrabold leading-tight tracking-tight text-ink-900 sm:text-[2rem]">
          {COPY.onboardingTitle}
        </h1>
        <p className="mx-auto mt-3 max-w-xl text-[0.9375rem] leading-relaxed text-ink-600">
          {COPY.onboardingDescription}
        </p>
      </header>

      <form onSubmit={handleSubmit} noValidate className="mt-8 space-y-5">
        <Card className="p-6 sm:p-7">
          <h2 className="text-lg font-bold text-ink-900">기본 정보</h2>

          <div className="mt-5 grid gap-5 sm:grid-cols-2">
            <TextField
              label="나이"
              type="number"
              inputMode="numeric"
              min={AGE_MIN}
              max={AGE_MAX}
              required
              placeholder="23"
              value={age}
              error={ageError}
              trailing={
                <span className="text-[0.875rem] font-medium text-ink-500">
                  세(만 나이)
                </span>
              }
              onChange={(event) => {
                setAge(event.target.value);
                setAgeError(undefined);
              }}
            />

            <SelectField
              label="자치구"
              hint="선택하면 구에서 하는 지원도 찾아볼 수 있어요"
              options={DISTRICT_OPTIONS}
              placeholder="자치구 선택 안 함"
              value={district}
              onChange={(event) => setDistrict(event.target.value as District | "")}
            />
          </div>

          <div className="mt-5">
            <OptionGroup
              legend="현재 상태"
              required
              options={STATUS_OPTIONS}
              value={status}
              onChange={setStatus}
            />
          </div>

          <fieldset className="mt-5">
            <legend className="flex items-center gap-1 text-[0.875rem] font-semibold text-ink-700">
              관심 분야
              <span aria-hidden="true" className="text-brand-500">
                *
              </span>
            </legend>
            <div className="mt-2 flex flex-wrap gap-2">
              {CATEGORY_OPTIONS.map((option) => (
                <Chip
                  key={option.value}
                  label={option.label}
                  selected={categories.includes(option.value)}
                  onToggle={() => toggleCategory(option.value)}
                />
              ))}
            </div>
            <p className="mt-2 text-[0.875rem] text-ink-500">
              여러 개 고를 수 있어요. 전체를 고르면 나머지는 해제돼요.
            </p>
          </fieldset>

          <div className="mt-5">
            <OptionGroup
              legend="가구 소득"
              options={INCOME_OPTIONS}
              value={income}
              onChange={setIncome}
              hint={COPY.incomeHelp}
            />
          </div>

          <p className="mt-5 flex items-start gap-2 rounded-xl border border-line-soft bg-canvas-50 px-3 py-2.5 text-[0.875rem] leading-relaxed text-ink-600">
            <Info aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-brand-500" />
            {COPY.privacyNotice}
          </p>
        </Card>

        <Card className="p-6 sm:p-7">
          <h2 className="text-lg font-bold text-ink-900">첫 질문</h2>
          <p className="mt-1 text-[0.9375rem] text-ink-600">
            질문 없이 바로 찾아볼 수도 있어요.
          </p>

          <label htmlFor="first-question" className="sr-only">
            첫 질문
          </label>
          <textarea
            id="first-question"
            rows={2}
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            placeholder={COPY.chatPlaceholder}
            className="control mt-4 resize-none py-3"
          />

          <ul className="mt-3 flex flex-wrap gap-2">
            {EXAMPLE_CHIPS.map((chip) => (
              <li key={chip}>
                {/* 칩을 누르면 그 문장을 바로 보낸다 */}
                <button
                  type="button"
                  disabled={!canSubmit}
                  onClick={() => void start(chip)}
                  className="rounded-full border border-line bg-white px-3.5 py-2 text-left text-[0.875rem] font-medium text-ink-700 transition-colors hover:border-brand-300 hover:text-brand-700 disabled:cursor-not-allowed disabled:opacity-55 focus-ring"
                >
                  {chip}
                </button>
              </li>
            ))}
          </ul>

          <div className="mt-6 flex flex-wrap gap-3">
            <Button
              type="submit"
              size="lg"
              disabled={!canSubmit}
              loading={submitting}
              leftIcon={<Sparkles aria-hidden="true" className="h-4 w-4" />}
            >
              {COPY.onboardingSubmit}
            </Button>
            <Button
              type="button"
              variant="secondary"
              size="lg"
              disabled={!canSubmit}
              onClick={() => void start("")}
            >
              {COPY.browseWithoutQuestion}
            </Button>
          </div>

          {!canSubmit && !submitting && (
            <p className="mt-3 text-[0.875rem] text-ink-500">
              나이, 현재 상태, 관심 분야를 채우면 시작할 수 있어요.
            </p>
          )}
        </Card>
      </form>
    </div>
  );
}
