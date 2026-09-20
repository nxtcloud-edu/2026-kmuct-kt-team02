import {
  forwardRef,
  useId,
  type InputHTMLAttributes,
  type ReactNode,
  type SelectHTMLAttributes,
} from "react";
import { AlertCircle, Check, ChevronDown } from "lucide-react";
import { cn } from "@/lib/cn";

interface ShellProps {
  id: string;
  label: string;
  hint?: string;
  error?: string;
  required?: boolean;
  children: ReactNode;
}

function FieldShell({ id, label, hint, error, required, children }: ShellProps) {
  return (
    <div className="space-y-1.5">
      <label
        htmlFor={id}
        className="flex items-center gap-1 text-[0.875rem] font-semibold text-ink-700"
      >
        {label}
        {required && (
          <span aria-hidden="true" className="text-brand-500">
            *
          </span>
        )}
      </label>
      {children}
      {error ? (
        <p
          id={`${id}-error`}
          role="alert"
          className="flex items-center gap-1.5 text-[0.875rem] font-medium text-urgent"
        >
          <AlertCircle aria-hidden="true" className="h-3.5 w-3.5 shrink-0" />
          {error}
        </p>
      ) : (
        hint && (
          <p id={`${id}-hint`} className="text-[0.875rem] text-ink-500">
            {hint}
          </p>
        )
      )}
    </div>
  );
}

interface TextFieldProps extends InputHTMLAttributes<HTMLInputElement> {
  label: string;
  hint?: string;
  error?: string;
  /** 오른쪽 부착 요소 (단위 표기, 비밀번호 토글 등) */
  trailing?: ReactNode;
}

export const TextField = forwardRef<HTMLInputElement, TextFieldProps>(
  function TextField(
    { label, hint, error, trailing, className, id, required, ...rest },
    ref,
  ) {
    const autoId = useId();
    const fieldId = id ?? autoId;

    return (
      <FieldShell id={fieldId} label={label} hint={hint} error={error} required={required}>
        <div className="relative">
          <input
            ref={ref}
            id={fieldId}
            required={required}
            aria-invalid={error ? true : undefined}
            aria-describedby={
              error ? `${fieldId}-error` : hint ? `${fieldId}-hint` : undefined
            }
            className={cn(
              "control h-12",
              error && "control-error",
              trailing && "pr-16",
              className,
            )}
            {...rest}
          />
          {trailing && (
            <div className="absolute right-3 top-1/2 -translate-y-1/2">{trailing}</div>
          )}
        </div>
      </FieldShell>
    );
  },
);

interface SelectOption {
  value: string;
  label: string;
}

interface SelectFieldProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label: string;
  hint?: string;
  error?: string;
  options: readonly SelectOption[];
  placeholder?: string;
}

export const SelectField = forwardRef<HTMLSelectElement, SelectFieldProps>(
  function SelectField(
    { label, hint, error, options, placeholder = "선택해 주세요", className, id, required, ...rest },
    ref,
  ) {
    const autoId = useId();
    const fieldId = id ?? autoId;

    return (
      <FieldShell id={fieldId} label={label} hint={hint} error={error} required={required}>
        <div className="relative">
          <select
            ref={ref}
            id={fieldId}
            required={required}
            aria-invalid={error ? true : undefined}
            aria-describedby={
              error ? `${fieldId}-error` : hint ? `${fieldId}-hint` : undefined
            }
            className={cn(
              "control h-12 cursor-pointer appearance-none pr-10",
              error && "control-error",
              className,
            )}
            {...rest}
          >
            <option value="">{placeholder}</option>
            {options.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
          <ChevronDown
            aria-hidden="true"
            className="pointer-events-none absolute right-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-500"
          />
        </div>
      </FieldShell>
    );
  },
);

interface CheckboxProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "type"> {
  label: string;
  description?: string;
}

export function Checkbox({ label, description, className, id, ...rest }: CheckboxProps) {
  const autoId = useId();
  const fieldId = id ?? autoId;

  return (
    <div className={cn("flex items-start gap-3", className)}>
      <input
        type="checkbox"
        id={fieldId}
        className="mt-0.5 h-[1.15rem] w-[1.15rem] shrink-0 cursor-pointer rounded border-line-strong accent-brand-500 focus-ring"
        {...rest}
      />
      <label htmlFor={fieldId} className="cursor-pointer select-none">
        <span className="block text-[0.9375rem] font-medium text-ink-800">{label}</span>
        {description && (
          <span className="mt-0.5 block text-[0.875rem] text-ink-500">{description}</span>
        )}
      </label>
    </div>
  );
}

/** 선택형 칩. 관심 분야, 소득 구간, 현재 상태에 쓴다 */
export function Chip({
  label,
  selected,
  onToggle,
  disabled,
  className,
}: {
  label: string;
  selected: boolean;
  onToggle: () => void;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      disabled={disabled}
      aria-pressed={selected}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-4 py-2 text-[0.875rem] font-semibold transition-all duration-200 focus-ring",
        "disabled:cursor-not-allowed disabled:opacity-55",
        selected
          ? "border-brand-500 bg-brand-50 text-brand-700"
          : "border-line bg-white text-ink-600 hover:border-line-strong hover:text-brand-700",
        className,
      )}
    >
      {selected && <Check aria-hidden="true" className="h-3.5 w-3.5" />}
      {label}
    </button>
  );
}

/** 단일 선택 버튼 그룹 */
export function OptionGroup<TValue extends string>({
  legend,
  options,
  value,
  onChange,
  columns = 3,
  hint,
  required,
}: {
  legend: string;
  options: ReadonlyArray<{ value: TValue; label: string }>;
  value: TValue | "";
  onChange: (next: TValue) => void;
  columns?: 2 | 3;
  hint?: string;
  required?: boolean;
}) {
  return (
    <fieldset>
      <legend className="flex items-center gap-1 text-[0.875rem] font-semibold text-ink-700">
        {legend}
        {required && (
          <span aria-hidden="true" className="text-brand-500">
            *
          </span>
        )}
      </legend>
      <div
        className={cn(
          "mt-2 grid gap-2",
          columns === 2 ? "grid-cols-2" : "grid-cols-2 sm:grid-cols-3",
        )}
      >
        {options.map((option) => {
          const selected = value === option.value;
          return (
            <button
              key={option.value}
              type="button"
              onClick={() => onChange(option.value)}
              aria-pressed={selected}
              className={cn(
                "rounded-xl border px-3 py-2.5 text-[0.9375rem] font-semibold transition-all duration-200 focus-ring",
                selected
                  ? "border-brand-500 bg-brand-50 text-brand-700"
                  : "border-line bg-white text-ink-700 hover:border-line-strong hover:text-brand-700",
              )}
            >
              {option.label}
            </button>
          );
        })}
      </div>
      {hint && <p className="mt-2 text-[0.875rem] text-ink-500">{hint}</p>}
    </fieldset>
  );
}
