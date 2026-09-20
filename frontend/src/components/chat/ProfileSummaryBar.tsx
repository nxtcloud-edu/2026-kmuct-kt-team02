import { cn } from "@/lib/cn";
import { PROFILE_FIELD_LABEL, formatProfileValue } from "@/lib/labels";
import { readProfileField, type Profile, type ProfileField } from "@/lib/contract";

/**
 * 프로필 요약 바 (frontend/README.md 3-9).
 * "만 23세 · 재학 · 취업·훈련 · 소득 모름" 형태. 대화로 바뀌면 그 항목을 잠깐 강조한다.
 * 거주지는 서버가 서울로 고정하므로 표시 항목에 넣지 않는다.
 */

const SUMMARY_FIELDS: ProfileField[] = [
  "age",
  "status",
  "categories",
  "income_bracket",
  "district",
  "housing_type",
  "residence_period",
  "remaining_semesters",
  "job_seeking_period",
  "employment_insurance",
  "other_benefit",
  "household_size",
  "last_gpa",
];

export function ProfileSummaryBar({
  profile,
  changedFields,
}: {
  profile: Profile;
  /** 방금 바뀐 항목. 2초 동안 강조한다 */
  changedFields: ProfileField[];
}) {
  const entries = SUMMARY_FIELDS.filter((field) => {
    const value = readProfileField(profile, field);
    if (Array.isArray(value)) return value.length > 0;
    return value !== null && value !== undefined && value !== "";
  });

  return (
    <div className="flex flex-wrap items-center gap-2 rounded-2xl border border-line bg-canvas-50 px-4 py-3">
      <h2 className="sr-only">내 프로필 요약</h2>

      <ul className="flex flex-1 flex-wrap items-center gap-1.5">
        {entries.map((field) => {
          const value = readProfileField(profile, field);
          const changed = changedFields.includes(field);
          return (
            <li
              key={field}
              className={cn(
                "inline-flex items-center gap-1 rounded-lg border px-2.5 py-1 text-[0.875rem] transition-colors",
                changed
                  ? "border-brand-400 bg-brand-50 text-brand-700"
                  : "border-line bg-white text-ink-800",
              )}
            >
              <span className="font-medium text-ink-500">
                {PROFILE_FIELD_LABEL[field]}
              </span>
              <span className="font-semibold">{formatProfileValue(field, value)}</span>
            </li>
          );
        })}
      </ul>

    </div>
  );
}
