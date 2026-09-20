import { useState, type FormEvent } from "react";
import { HelpCircle } from "lucide-react";
import { Button } from "@/components/ui/Button";
import type { FollowupQuestion } from "@/lib/contract";

/**
 * 후속 질문 카드 (frontend/README.md 3-4).
 * 질문 한 줄, 이유 한 줄, 선택지 버튼, 직접 입력, 건너뛰기.
 * 버튼은 done 이후에만 누를 수 있다.
 */
export function FollowupCard({
  followup,
  onAnswer,
  onSkip,
  disabled,
}: {
  followup: FollowupQuestion;
  onAnswer: (value: unknown, label: string) => void;
  onSkip: () => void;
  disabled: boolean;
}) {
  const [custom, setCustom] = useState("");

  const submitCustom = (event: FormEvent) => {
    event.preventDefault();
    const value = custom.trim();
    if (!value) return;
    setCustom("");
    onAnswer(value, value);
  };

  return (
    <div className="mt-3 rounded-2xl border border-brand-200 bg-brand-50/60 p-4">
      <p className="text-[0.9375rem] font-bold text-ink-900">{followup.question}</p>
      <p className="mt-1.5 flex items-start gap-1.5 text-[0.875rem] leading-relaxed text-ink-600">
        <HelpCircle aria-hidden="true" className="mt-0.5 h-3.5 w-3.5 shrink-0" />
        {followup.reason}
      </p>

      <div className="mt-3 flex flex-wrap gap-2">
        {followup.options.map((option) => (
          <button
            key={String(option.value)}
            type="button"
            disabled={disabled}
            onClick={() => onAnswer(option.value, option.label)}
            className="rounded-lg border border-line bg-white px-3 py-2 text-[0.875rem] font-semibold text-ink-700 transition-colors hover:border-brand-300 hover:text-brand-700 disabled:cursor-not-allowed disabled:opacity-55 focus-ring"
          >
            {option.label}
          </button>
        ))}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        {followup.allow_free_text && (
          <form onSubmit={submitCustom} className="flex flex-1 items-center gap-2">
            <label htmlFor="followup-custom" className="sr-only">
              직접 입력
            </label>
            <input
              id="followup-custom"
              value={custom}
              onChange={(event) => setCustom(event.target.value)}
              disabled={disabled}
              placeholder="직접 입력"
              className="control h-10 flex-1"
            />
            <Button
              type="submit"
              size="sm"
              variant="secondary"
              disabled={disabled || custom.trim().length === 0}
            >
              입력
            </Button>
          </form>
        )}

        <button
          type="button"
          disabled={disabled}
          onClick={onSkip}
          className="rounded-lg px-2.5 py-2 text-[0.875rem] font-semibold text-ink-500 underline decoration-ink-400/40 underline-offset-2 transition-colors hover:text-ink-800 disabled:cursor-not-allowed disabled:opacity-55 focus-ring"
        >
          건너뛰기
        </button>
      </div>
    </div>
  );
}
