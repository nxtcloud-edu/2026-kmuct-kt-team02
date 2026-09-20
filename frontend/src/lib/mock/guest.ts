/**
 * 로그인하지 않은 상태의 대화 응답.
 *
 * 프로필이 없어 자격을 판정할 수 없다. 그래서 여기서는 판정하지 않고
 * 공고에 적힌 내용(혜택, 마감, 조건 문구, 출처)만 안내한다.
 * 판정·후속 질문·조건부 문장은 로그인 뒤 서버가 담당한다.
 */

import { formatKoreanDate } from "../deadline";
import { COPY } from "../labels";
import type { Category, Footnote, PolicyInfo } from "../contract";
import { conditionName } from "../contract";
import { MOCK_POLICIES, type MockPolicy } from "./policies";
import { toPolicyInfo } from "./display";

/** 마감이 지나지 않은 정책만 후보로 둔다 */
function isOpen(policy: MockPolicy, now: Date): boolean {
  if (!policy.apply_end) return true;
  return policy.apply_end >= toISO(now);
}

function toISO(date: Date): string {
  const kst = new Date(date.getTime() + 9 * 60 * 60 * 1000);
  return kst.toISOString().slice(0, 10);
}

/** 키워드 일치 수 */
function score(policy: MockPolicy, query: string): number {
  const text = query.toLowerCase();
  if (!text) return 0;
  let hits = 0;
  for (const keyword of policy.keywords) {
    if (text.includes(keyword)) hits += 2;
  }
  if (text.includes(policy.title.replace(/\s/g, ""))) hits += 4;
  for (const word of policy.title.split(" ")) {
    if (word.length > 1 && text.includes(word)) hits += 1;
  }
  return hits;
}

/**
 * 질문과 관련된 정책을 찾는다.
 * 키워드가 하나도 맞지 않으면 마감이 가까운 순으로 기본 목록을 보여 준다.
 */
export function searchGuestPolicies(
  query: string,
  limit = 5,
  now: Date = new Date(),
): PolicyInfo[] {
  const open = MOCK_POLICIES.filter((policy) => isOpen(policy, now));

  const scored = open
    .map((policy) => ({ policy, hits: score(policy, query) }))
    .filter((item) => item.hits > 0)
    .sort((a, b) => b.hits - a.hits);

  const picked =
    scored.length > 0
      ? scored.slice(0, limit).map((item) => item.policy)
      : [...open]
          .sort((a, b) =>
            (a.apply_end ?? "9999-12-31").localeCompare(b.apply_end ?? "9999-12-31"),
          )
          .slice(0, limit);

  let footnoteId = 1;
  const items: PolicyInfo[] = [];
  for (const policy of picked) {
    const { info, nextFootnoteId } = toPolicyInfo(policy, footnoteId, now);
    footnoteId = nextFootnoteId;
    items.push(info);
  }
  return items;
}

/** 각주 목록. 발췌가 있는 조건만 */
export function buildGuestFootnotes(policies: PolicyInfo[]): Footnote[] {
  const notes: Footnote[] = [];
  for (const policy of policies) {
    for (const condition of policy.conditions) {
      if (!condition.excerpt) continue;
      notes.push({
        footnote_id: condition.footnote_id,
        policy_id: policy.policy_id,
        excerpt: condition.excerpt,
        agency: policy.agency,
        checked_at: policy.checked_at,
        source_url: condition.source_url,
      });
    }
  }
  return notes;
}

/**
 * 안내 문장.
 * 판정 표현을 쓰지 않는다. 공고 기준만 전하고, 판정은 로그인 뒤에 가능하다고 알린다.
 */
export function buildGuestAnswer(
  policies: PolicyInfo[],
  matched: boolean,
): string[] {
  if (policies.length === 0) {
    return [
      "지금 찾은 제도가 없어요.",
      "'월세', '취업 준비', '생활비', '교통비'처럼 상황을 한 단어로 말해 주시면 다시 찾아볼게요.",
      COPY.finalCheck,
    ];
  }

  const sentences: string[] = [
    matched
      ? `말씀하신 내용과 관련된 제도 ${policies.length}건을 공고 기준으로 정리했어요.`
      : `지금 접수 중이고 마감이 가까운 제도 ${policies.length}건을 먼저 보여드릴게요.`,
  ];

  for (const policy of policies) {
    const first = policy.conditions[0];
    const mark = first ? `[${first.footnote_id}]` : "";
    const names = policy.conditions.map(conditionName).join(", ");
    const deadline = policy.deadline.apply_end
      ? `마감은 ${formatKoreanDate(policy.deadline.apply_end)}(${policy.deadline.badge})`
      : "상시 접수";
    const conditionText = names ? ` 공고가 두는 조건은 ${names}예요.` : "";
    sentences.push(
      `${policy.title}은 ${policy.benefit}이고 ${deadline}이에요.${conditionText}${mark}`,
    );
  }

  sentences.push(
    "지금은 공고에 적힌 내용만 알려드릴 수 있어요. 로그인해서 나이와 현재 상태를 알려주시면 제도마다 내가 조건을 채우는지 따져서 알려드릴 수 있어요.",
  );
  sentences.push(COPY.finalCheck);

  return sentences;
}

/**
 * 관련 질문. 서버 `related` 이벤트와 같은 모양(문자열 배열, 최대 3개)으로 돌려준다
 * (server/sse.py RelatedEventData).
 */
export function buildGuestChips(policies: PolicyInfo[]): string[] {
  const candidates: Array<{ category: Category; question: string }> = [
    { category: "housing", question: "월세 지원 있어?" },
    { category: "job", question: "취업 준비에 도움 되는 제도 알려줘" },
    { category: "living", question: "생활비 지원 받을 수 있는 거 있어?" },
    { category: "culture", question: "교통비 아낄 수 있는 방법 있을까?" },
  ];
  // 이미 보여 준 분야와 겹치지 않는 질문을 앞에 둔다
  const shown = new Set<Category>(policies.flatMap((policy) => policy.categories));
  return [...candidates]
    .sort((a, b) => Number(shown.has(a.category)) - Number(shown.has(b.category)))
    .slice(0, 3)
    .map((item) => item.question);
}
