import { useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { Info, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Chip, OptionGroup, SelectField, TextField } from "@/components/ui/Field";
import { Card } from "@/components/ui/Surface";
import { COPY } from "@/lib/labels";
import {
  AGE_MAX,
  AGE_MIN,
  CATEGORY_OPTIONS,
  DISTRICT_OPTIONS,
  INCOME_OPTIONS,
  STATUS_OPTIONS,
} from "@/lib/profileOptions";
import { useApp } from "@/providers/AppProvider";
import type {
  Category,
  District,
  IncomeBracket,
  ProfileInput,
  UserStatus,
} from "@/lib/contract";

/** 서버 필수 항목(age·status·categories) + 참고용 선택 항목. 자치구는 선택이라 필수에 넣지 않는다 */
const COMPLETION_FIELDS = [
  { key: "age", label: "나이" },
  { key: "status", label: "현재 상태" },
  { key: "categories", label: "관심 분야" },
  { key: "district", label: "자치구" },
  { key: "income_bracket", label: "가구 소득" },
] as const;

/**
 * 마이페이지.
 *
 * 프로필 항목 표(docs/01-glossary-profile.md 2장)에 있는 값만 다룬다.
 * 이름·생년월일·휴대폰은 금지 사항이라 넣지 않는다. 나이는 만 나이 숫자로 받는다.
 */
export function MyPage() {
  const navigate = useNavigate();
  const { profileInput, saveProfileInput, startSessionFromProfile, auth, showToast } =
    useApp();

  const [age, setAge] = useState("");
  const [district, setDistrict] = useState<District | "">("");
  const [status, setStatus] = useState<UserStatus | "">("");
  const [categories, setCategories] = useState<Category[]>([]);
  const [income, setIncome] = useState<IncomeBracket>("unknown");
  const [ageError, setAgeError] = useState<string>();
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (!profileInput) return;
    setAge(String(profileInput.age));
    setDistrict((profileInput.district ?? "") as District | "");
    setStatus(profileInput.status);
    setCategories(profileInput.categories);
    setIncome(profileInput.income_bracket);
    setDirty(false);
  }, [profileInput]);

  const ageNumber = Number(age);
  const ageValid =
    age.trim().length > 0 &&
    Number.isInteger(ageNumber) &&
    ageNumber >= AGE_MIN &&
    ageNumber <= AGE_MAX;

  const completion = useMemo(() => {
    const values: Record<string, unknown> = {
      age: ageValid ? ageNumber : "",
      district,
      status,
      categories,
      income_bracket: income === "unknown" ? "" : income,
    };
    const filled = COMPLETION_FIELDS.filter(({ key }) => {
      const value = values[key];
      if (Array.isArray(value)) return value.length > 0;
      return value !== "" && value !== null && value !== undefined;
    });
    return {
      percent: Math.round((filled.length / COMPLETION_FIELDS.length) * 100),
      filled: filled.length,
      missing: COMPLETION_FIELDS.filter(
        ({ key }) => !filled.some((item) => item.key === key),
      ),
    };
  }, [ageValid, ageNumber, status, categories, income, district]);

  const toggleCategory = (value: Category) => {
    setDirty(true);
    setCategories((prev) => {
      if (value === "all") return prev.includes("all") ? [] : ["all"];
      const withoutAll = prev.filter((item) => item !== "all");
      return withoutAll.includes(value)
        ? withoutAll.filter((item) => item !== value)
        : [...withoutAll, value];
    });
  };

  /** 서버가 필수로 요구하는 세 항목(나이·현재 상태·관심 분야)만 있으면 저장할 수 있다. 자치구는 선택 */
  const profileReady = ageValid && status !== "" && categories.length > 0;
  const canSave = profileReady && dirty && !saving;

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
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

    setSaving(true);
    saveProfileInput(input);
    setDirty(false);
    // 저장한 조건으로 판정을 다시 받도록 세션을 갱신한다
    void startSessionFromProfile(input).then(() => {
      setSaving(false);
      showToast("내 정보를 저장했어요. 이제 맞춤 판정을 받을 수 있어요.");
    });
  };

  /** 저장한 조건으로 맞춤 판정을 시작하고 대화 화면으로 이동한다 */
  const startWithProfile = async () => {
    // 여기서 만드는 값은 서버가 요구하는 필수 항목을 모두 채운 상태여야 한다. 자치구는 선택
    if (!ageValid || status === "" || categories.length === 0) return;
    const input: ProfileInput = {
      age: ageNumber,
      district: district === "" ? null : district,
      status,
      categories,
      income_bracket: income,
    };
    saveProfileInput(input);
    setDirty(false);
    const ok = await startSessionFromProfile(input);
    if (ok) navigate("/");
  };

  return (
    <div className="mx-auto max-w-[1200px] px-4 py-10 sm:px-6 lg:px-8">
      <header className="mb-6">
        <p className="text-[0.875rem] font-bold uppercase tracking-[0.14em] text-brand-500">
          마이페이지
        </p>
        <h1 className="mt-1.5 text-2xl font-extrabold tracking-tight text-ink-900 sm:text-3xl">
          내 조건 관리
        </h1>
        <p className="mt-2 max-w-3xl text-[0.9375rem] leading-relaxed text-ink-600">
          저장한 조건으로 상담을 시작하면 같은 정보를 다시 말하지 않아도 돼요.
          {auth ? ` 현재 ${auth.userId} 아이디로 로그인되어 있어요.` : ""}
        </p>
      </header>

      <div className="grid gap-5 lg:grid-cols-[18rem_1fr]">
        {/* 왼쪽 사이드 */}
        <aside className="space-y-5 lg:sticky lg:top-24 lg:self-start">
          <Card className="p-6">
            <div className="flex items-center gap-3">
              <span
                aria-hidden="true"
                className="flex h-12 w-12 items-center justify-center rounded-2xl bg-brand-gradient text-lg font-bold text-white"
              >
                {(auth?.userId ?? "쏘")[0].toUpperCase()}
              </span>
              <div className="min-w-0">
                <p className="truncate text-[0.9375rem] font-bold text-ink-900">
                  {auth?.userId ?? "로그인하지 않음"}
                </p>
                <p className="text-[0.875rem] text-ink-500">
                  프로필 {completion.percent}% 완성
                </p>
              </div>
            </div>

            <div
              role="progressbar"
              aria-valuenow={completion.percent}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-label="프로필 완성도"
              className="mt-4 h-2.5 w-full overflow-hidden rounded-full bg-canvas-200"
            >
              <div
                className="h-full rounded-full bg-brand-gradient transition-all duration-500"
                style={{ width: `${completion.percent}%` }}
              />
            </div>
            <p className="mt-2 text-[0.875rem] text-ink-500">
              {completion.filled} / {COMPLETION_FIELDS.length} 항목
            </p>

            {completion.missing.length > 0 && (
              <ul className="mt-3 flex flex-wrap gap-1.5">
                {completion.missing.map((item) => (
                  <li
                    key={item.key}
                    className="rounded-lg bg-canvas-200 px-2 py-1 text-[0.875rem] font-medium text-ink-600"
                  >
                    {item.label}
                  </li>
                ))}
              </ul>
            )}

            <Button
              block
              className="mt-5"
              disabled={!profileReady}
              onClick={() => void startWithProfile()}
            >
              이 조건으로 상담 시작
            </Button>
            {!profileReady && (
              <p className="mt-2 text-[0.875rem] text-ink-500">
                나이, 현재 상태, 관심 분야를 채우면 맞춤 판정을 받을 수 있어요.
              </p>
            )}

            {!auth && (
              <Link to="/login" className="mt-2 block rounded-xl focus-ring">
                <Button block variant="secondary">
                  로그인
                </Button>
              </Link>
            )}
          </Card>

          <Card className="p-6">
            <h2 className="flex items-center gap-2 text-[1.0625rem] font-bold text-ink-900">
              <ShieldCheck aria-hidden="true" className="h-4 w-4 text-brand-600" />
              개인정보 안내
            </h2>
            <ul className="mt-3 space-y-2 text-[0.9375rem] leading-relaxed text-ink-600">
              <li>맞춤 안내와 자격 판단에만 사용해요.</li>
              <li>이름, 연락처, 주민등록번호, 계좌번호는 받지 않아요.</li>
              <li>{COPY.privacyNotice}</li>
            </ul>
          </Card>
        </aside>

        {/* 오른쪽 폼 */}
        <form onSubmit={handleSubmit} noValidate className="space-y-5">
          <Card className="p-6 sm:p-7">
            <h2 className="text-lg font-bold text-ink-900">기본 조건</h2>
            <p className="mt-1 text-[0.9375rem] text-ink-600">
              나이와 현재 상태가 대부분 제도의 판정 기준이에요.
            </p>

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
                  setDirty(true);
                }}
              />

              <SelectField
                label="자치구"
                hint="선택이에요. 고르면 구에서 하는 지원도 함께 찾아봐요"
                options={DISTRICT_OPTIONS}
                placeholder="자치구를 선택해 주세요 (선택)"
                value={district}
                onChange={(event) => {
                  setDistrict(event.target.value as District | "");
                  setDirty(true);
                }}
              />
            </div>

            <div className="mt-5">
              <OptionGroup
                legend="현재 상태"
                required
                options={STATUS_OPTIONS}
                value={status}
                onChange={(next) => {
                  setStatus(next);
                  setDirty(true);
                }}
              />
            </div>

            <div className="mt-5">
              <OptionGroup
                legend="가구 소득"
                options={INCOME_OPTIONS}
                value={income}
                onChange={(next) => {
                  setIncome(next);
                  setDirty(true);
                }}
                hint={COPY.incomeHelp}
              />
            </div>
          </Card>

          <Card className="p-6 sm:p-7">
            <h2 className="text-lg font-bold text-ink-900">관심 분야</h2>
            <p className="mt-1 text-[0.9375rem] text-ink-600">
              고른 분야의 제도를 먼저 찾아 드려요.
            </p>

            <fieldset className="mt-4">
              <legend className="sr-only">관심 분야 선택</legend>
              <div className="flex flex-wrap gap-2">
                {CATEGORY_OPTIONS.map((option) => (
                  <Chip
                    key={option.value}
                    label={option.label}
                    selected={categories.includes(option.value)}
                    onToggle={() => toggleCategory(option.value)}
                  />
                ))}
              </div>
            </fieldset>

            <p className="mt-3 flex items-start gap-2 text-[0.875rem] leading-relaxed text-ink-500">
              <Info aria-hidden="true" className="mt-0.5 h-3.5 w-3.5 shrink-0" />
              주거 형태, 가구원 수처럼 제도마다 다른 조건은 상담 중에 필요할 때만 여쭤봐요.
            </p>
          </Card>

          <div className="flex flex-wrap items-center gap-3">
            <Button type="submit" size="lg" disabled={!canSave} loading={saving}>
              변경사항 저장
            </Button>
            <Button
              type="button"
              variant="secondary"
              size="lg"
              disabled={!dirty}
              onClick={() => {
                if (!profileInput) return;
                setAge(String(profileInput.age));
                setDistrict((profileInput.district ?? "") as District | "");
                setStatus(profileInput.status);
                setCategories(profileInput.categories);
                setIncome(profileInput.income_bracket);
                setDirty(false);
              }}
            >
              취소
            </Button>
            {dirty && (
              <span className="text-[0.875rem] font-semibold text-check">
                저장하지 않은 변경이 있어요.
              </span>
            )}
          </div>
        </form>
      </div>
    </div>
  );
}
