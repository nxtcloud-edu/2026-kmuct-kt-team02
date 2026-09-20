import { useMemo, useState } from "react";
import { Search, SlidersHorizontal } from "lucide-react";
import { Card, EmptyState, SectionHeading } from "@/components/ui/Surface";
import { PolicyCard } from "@/components/policy/PolicyCard";
import { PolicyDetailPanel } from "@/components/policy/PolicyDetailPanel";
import { Chip } from "@/components/ui/Field";
import { allPolicyInfo } from "@/lib/mock/display";
import { CATEGORY_LABEL, COPY } from "@/lib/labels";
import { CATEGORY_OPTIONS } from "@/lib/profileOptions";
import type { Category, PolicyInfo } from "@/lib/contract";

type SortKey = "deadline" | "title";

/**
 * 정책 모아보기.
 *
 * 프로필과 무관한 목록이라 판정하지 않는다. 마감 배지와 출처, 확인일만 보여 주고
 * 내가 대상인지는 대화에서 확인하도록 안내한다.
 */
export function PoliciesPage() {
  const [query, setQuery] = useState("");
  const [filters, setFilters] = useState<Category[]>([]);
  const [sortKey, setSortKey] = useState<SortKey>("deadline");
  const [selected, setSelected] = useState<PolicyInfo | null>(null);

  const items = useMemo(() => allPolicyInfo(), []);

  const filtered = useMemo(() => {
    const keyword = query.trim().toLowerCase();

    const matched = items.filter((policy) => {
      const byCategory =
        filters.length === 0 ||
        filters.includes("all") ||
        policy.categories.some((item) => filters.includes(item));

      const byKeyword =
        keyword.length === 0 ||
        policy.title.toLowerCase().includes(keyword) ||
        policy.agency.toLowerCase().includes(keyword) ||
        policy.benefit.toLowerCase().includes(keyword);

      return byCategory && byKeyword;
    });

    return [...matched].sort((a, b) => {
      if (sortKey === "title") return a.title.localeCompare(b.title);
      const byImminent =
        Number(b.deadline.is_imminent) - Number(a.deadline.is_imminent);
      if (byImminent !== 0) return byImminent;
      const aEnd = a.deadline.apply_end ?? "9999-12-31";
      const bEnd = b.deadline.apply_end ?? "9999-12-31";
      if (aEnd !== bEnd) return aEnd.localeCompare(bEnd);
      return a.policy_id.localeCompare(b.policy_id);
    });
  }, [items, filters, query, sortKey]);

  const toggleFilter = (value: Category) => {
    setFilters((prev) => {
      if (value === "all") return prev.includes("all") ? [] : ["all"];
      const withoutAll = prev.filter((item) => item !== "all");
      return withoutAll.includes(value)
        ? withoutAll.filter((item) => item !== value)
        : [...withoutAll, value];
    });
  };

  return (
    <div className="mx-auto max-w-[1400px] px-4 py-10 sm:px-6 lg:px-8">
      <SectionHeading
        eyebrow="정책 모아보기"
        title={`검수된 제도 ${items.length}건`}
        description="쏘다가 다루는 서울시·중앙정부 제도 목록이에요. 내가 대상인지 확인하려면 AI 상담에서 조건 판정을 받아 보세요."
      />

      <Card className="mt-6 p-4 sm:p-5">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-end">
          <div className="flex-1">
            <label
              htmlFor="policy-search"
              className="text-[0.875rem] font-semibold text-ink-700"
            >
              검색
            </label>
            <div className="relative mt-1.5">
              <Search
                aria-hidden="true"
                className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-400"
              />
              <input
                id="policy-search"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="제도명, 기관, 혜택으로 찾기"
                className="control h-11 pl-10"
              />
            </div>
          </div>

          <div>
            <label
              htmlFor="policy-sort"
              className="flex items-center gap-1.5 text-[0.875rem] font-semibold text-ink-700"
            >
              <SlidersHorizontal aria-hidden="true" className="h-3.5 w-3.5" />
              정렬
            </label>
            <select
              id="policy-sort"
              value={sortKey}
              onChange={(event) => setSortKey(event.target.value as SortKey)}
              className="control mt-1.5 h-11 w-full cursor-pointer lg:w-44"
            >
              <option value="deadline">마감 임박순</option>
              <option value="title">제도명순</option>
            </select>
          </div>
        </div>

        <fieldset className="mt-4">
          <legend className="text-[0.875rem] font-semibold text-ink-700">분야</legend>
          <div className="mt-2 flex flex-wrap gap-2">
            {CATEGORY_OPTIONS.map((option) => (
              <Chip
                key={option.value}
                label={option.label}
                selected={filters.includes(option.value)}
                onToggle={() => toggleFilter(option.value)}
              />
            ))}
          </div>
        </fieldset>
      </Card>

      <p className="mt-5 text-[0.9375rem] font-semibold text-ink-700">
        {filtered.length}건
        {filters.length > 0 && !filters.includes("all") && (
          <span className="ml-1.5 font-medium text-ink-500">
            · {filters.map((item) => CATEGORY_LABEL[item]).join(", ")}
          </span>
        )}
      </p>

      {filtered.length === 0 ? (
        <Card className="mt-3">
          <EmptyState
            icon={Search}
            title="조건에 맞는 제도가 없어요"
            description="검색어를 줄이거나 분야 필터를 해제해 보세요."
          />
        </Card>
      ) : (
        <div className="mt-3 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
          {filtered.map((policy) => (
            <PolicyCard key={policy.policy_id} policy={policy} onOpen={setSelected} />
          ))}
        </div>
      )}

      <p className="mt-6 text-[0.875rem] text-ink-500">{COPY.finalCheck}</p>

      <PolicyDetailPanel policy={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
