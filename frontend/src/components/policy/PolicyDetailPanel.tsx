import { useState } from "react";
import { ExternalLink, Quote } from "lucide-react";
import { SidePanel } from "@/components/ui/SidePanel";
import { Button } from "@/components/ui/Button";
import { Popover } from "@/components/ui/Popover";
import {
  CONDITION_LABEL,
  ConditionIcon,
  DeadlineBadge,
  RecheckBadge,
  SourceLine,
  StatusBadge,
  conditionLabelClass,
} from "@/components/ui/Surface";
import { formatDotDate, formatKoreanDate } from "@/lib/deadline";
import { sortConditions } from "@/lib/sort";
import { CATEGORY_LABEL, COPY } from "@/lib/labels";
import { cn } from "@/lib/cn";
import { conditionName, isEvaluated, type PolicyInfo } from "@/lib/contract";

/**
 * 정책 상세 패널 (frontend/README.md 3-7).
 * 순서: 머리 → 조건 판정표 → 조건부 문장 → 혜택 → 신청 기간 → 체크리스트 → 출처 → 고정 안내
 */
export function PolicyDetailPanel({
  policy,
  onClose,
}: {
  policy: PolicyInfo | null;
  onClose: () => void;
}) {
  // 체크 상태는 세션 동안만 유지한다 (frontend/README.md 3-8)
  const [checked, setChecked] = useState<string[]>([]);

  if (!policy) return null;
  const evaluated = isEvaluated(policy);

  const toggle = (key: string) =>
    setChecked((prev) =>
      prev.includes(key) ? prev.filter((item) => item !== key) : [...prev, key],
    );

  const conditions = sortConditions(policy.conditions);

  return (
    <SidePanel
      open
      onClose={onClose}
      title={policy.title}
      footer={
        policy.apply_url ? (
          <a
            href={policy.apply_url}
            target="_blank"
            rel="noreferrer noopener"
            className="block rounded-xl focus-ring"
          >
            <Button
              block
              rightIcon={<ExternalLink aria-hidden="true" className="h-4 w-4" />}
            >
              공식 신청 페이지로 이동
            </Button>
          </a>
        ) : undefined
      }
    >
      {/* 1. 머리 */}
      <div className="flex flex-wrap items-center gap-1.5">
        {evaluated && <StatusBadge status={policy.status} />}
        <DeadlineBadge deadline={policy.deadline} />
        <RecheckBadge checkedAt={policy.checked_at} dataStatus={policy.data_status} />
      </div>

      {!evaluated && (
        <p className="mt-3 rounded-xl border border-line bg-canvas-50 px-3 py-2.5 text-[0.875rem] leading-relaxed text-ink-600">
          아래는 공고에 적힌 조건이에요. 로그인해서 나이와 현재 상태를 알려주시면 조건마다
          충족 여부를 따져서 보여드릴 수 있어요.
        </p>
      )}
      <p className="mt-3 text-[0.875rem] font-medium text-ink-500">
        {policy.agency} · {policy.categories.map((item) => CATEGORY_LABEL[item]).join(", ")}
      </p>

      {/* 2. 조건 판정표 */}
      <section className="mt-5">
        <h3 className="text-[0.9375rem] font-bold text-ink-900">조건 판정</h3>
        <ul className="mt-2 space-y-2">
          {conditions.map((condition) => (
            <li
              key={`${conditionName(condition)}-${condition.footnote_id}`}
              className="flex items-start gap-2 rounded-xl border border-line-soft bg-white px-3 py-2.5"
            >
              {/* 판정이 없으면 결과 아이콘을 보여 주지 않는다 */}
              {evaluated && (
                <span className="mt-0.5">
                  <ConditionIcon result={condition.result} />
                </span>
              )}
              <span className="flex-1 text-[0.9375rem] leading-relaxed text-ink-700">
                {evaluated && (
                  <span
                    className={cn("font-bold", conditionLabelClass(condition.result))}
                  >
                    [{CONDITION_LABEL[condition.result]}]{" "}
                  </span>
                )}
                {conditionName(condition)}
                {evaluated && condition.needed_field === "공고 확인 필요" && (
                  <span className="ml-1 text-[0.875rem] text-ink-500">
                    (공고 확인 필요)
                  </span>
                )}
              </span>

              {condition.excerpt && (
                <Popover
                  triggerLabel={COPY.footnoteButton(condition.footnote_id)}
                  trigger={
                    <span className="flex h-6 w-6 items-center justify-center rounded-full border border-brand-200 bg-brand-50 text-[0.8125rem] font-bold text-brand-700 transition-colors hover:border-brand-400">
                      {condition.footnote_id}
                    </span>
                  }
                >
                  <span className="flex items-center gap-1.5 text-[0.875rem] font-bold text-ink-900">
                    <Quote aria-hidden="true" className="h-3.5 w-3.5 text-brand-500" />
                    공고 원문 발췌
                  </span>
                  <span className="mt-2 block rounded-xl border-l-[3px] border-brand-300 bg-canvas-50 px-3 py-2.5 text-[0.9375rem] leading-relaxed text-ink-700">
                    {condition.excerpt}
                  </span>
                  <span className="mt-2.5 block text-[0.875rem] text-ink-500">
                    {policy.agency} · 확인일 {formatDotDate(policy.checked_at)}
                  </span>
                  <a
                    href={condition.source_url}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="mt-2 inline-flex items-center gap-1 rounded text-[0.875rem] font-semibold text-brand-600 underline decoration-brand-200 decoration-2 underline-offset-2 transition-colors hover:text-brand-700 focus-ring"
                  >
                    공고 원문 보기
                    <ExternalLink aria-hidden="true" className="h-3.5 w-3.5" />
                  </a>
                </Popover>
              )}
            </li>
          ))}
        </ul>
      </section>

      {/* 3. 조건부 문장 */}
      {evaluated && policy.conditional_note && (
        <p className="mt-4 rounded-xl border border-check-border bg-check-bg px-3 py-2.5 text-[0.9375rem] font-medium leading-relaxed text-check">
          {policy.conditional_note}
        </p>
      )}

      {/* 4. 혜택 */}
      <section className="mt-5">
        <h3 className="text-[0.9375rem] font-bold text-ink-900">혜택</h3>
        <p className="mt-2 rounded-xl border border-line-soft bg-canvas-50 px-3 py-2.5 text-[0.9375rem] leading-relaxed text-ink-800">
          {policy.benefit}
        </p>
      </section>

      {/* 5. 신청 기간 */}
      <section className="mt-5">
        <h3 className="text-[0.9375rem] font-bold text-ink-900">신청 기간</h3>
        <p className="mt-2 text-[0.9375rem] text-ink-700">
          {formatKoreanDate(policy.deadline.apply_start)} ~{" "}
          {policy.deadline.apply_end
            ? formatKoreanDate(policy.deadline.apply_end)
            : "상시 접수"}
          <span className="ml-2 font-semibold text-ink-900">
            {policy.deadline.badge}
          </span>
        </p>
      </section>

      {/* 6. 체크리스트 — 서류 먼저, 신청 단계 다음 */}
      <section className="mt-5">
        <h3 className="text-[0.9375rem] font-bold text-ink-900">준비 체크리스트</h3>

        <h4 className="mt-3 text-[0.875rem] font-semibold text-ink-600">서류</h4>
        <ul className="mt-1.5 space-y-1">
          {policy.documents.map((document) => {
            const key = `doc:${document}`;
            const done = checked.includes(key);
            return (
              <li key={key}>
                <label className="flex cursor-pointer items-start gap-2.5 rounded-lg px-2 py-1.5 transition-colors hover:bg-canvas-100">
                  <input
                    type="checkbox"
                    checked={done}
                    onChange={() => toggle(key)}
                    className="mt-0.5 h-[1.15rem] w-[1.15rem] shrink-0 cursor-pointer rounded border-line-strong accent-brand-500 focus-ring"
                  />
                  <span
                    className={cn(
                      "text-[0.9375rem] leading-relaxed",
                      done ? "text-ink-400 line-through" : "text-ink-700",
                    )}
                  >
                    {document}
                  </span>
                </label>
              </li>
            );
          })}
        </ul>

        <h4 className="mt-3 text-[0.875rem] font-semibold text-ink-600">신청 단계</h4>
        <ul className="mt-1.5 space-y-1">
          {policy.steps.map((step, index) => {
            const key = `step:${step}`;
            const done = checked.includes(key);
            return (
              <li key={key}>
                <label className="flex cursor-pointer items-start gap-2.5 rounded-lg px-2 py-1.5 transition-colors hover:bg-canvas-100">
                  <input
                    type="checkbox"
                    checked={done}
                    onChange={() => toggle(key)}
                    className="mt-0.5 h-[1.15rem] w-[1.15rem] shrink-0 cursor-pointer rounded border-line-strong accent-brand-500 focus-ring"
                  />
                  <span
                    className={cn(
                      "text-[0.9375rem] leading-relaxed",
                      done ? "text-ink-400 line-through" : "text-ink-700",
                    )}
                  >
                    <span className="mr-1.5 font-bold text-brand-600">{index + 1}</span>
                    {step}
                  </span>
                </label>
              </li>
            );
          })}
        </ul>
      </section>

      {/* 7. 출처 */}
      <SourceLine
        className="mt-5 border-t border-line-soft pt-4"
        agency={policy.agency}
        sourceUrl={policy.source_url}
        checkedAt={policy.checked_at}
      />

      {/* 8. 고정 안내 */}
      <p className="mt-3 text-[0.875rem] font-medium text-ink-600">{COPY.finalCheck}</p>
    </SidePanel>
  );
}
