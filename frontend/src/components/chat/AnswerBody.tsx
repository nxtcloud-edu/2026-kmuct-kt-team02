import { ExternalLink, Quote } from "lucide-react";
import { Popover } from "@/components/ui/Popover";
import { formatDotDate } from "@/lib/deadline";
import { COPY } from "@/lib/labels";
import type { Footnote } from "@/lib/contract";

/** 본문 안의 [1] 표기 */
const FOOTNOTE_PATTERN = /\[(\d{1,2})\]/g;

/**
 * 답변 본문 (frontend/README.md 3-4).
 *
 * 접근성: 이 영역을 자동 낭독 대상으로 두지 않는다. 진행 단계와 완료만 LiveRegion에서 알린다.
 * 각주 번호는 답변이 끝나고 각주 데이터가 온 뒤에만 누를 수 있게 한다.
 */
export function AnswerBody({
  text,
  footnotes,
  streaming,
}: {
  text: string;
  footnotes: Footnote[];
  streaming: boolean;
}) {
  const byId = new Map(footnotes.map((note) => [note.footnote_id, note]));
  const paragraphs = text.split("\n\n").filter((part) => part.trim().length > 0);

  return (
    <div className="space-y-2.5">
      {paragraphs.map((paragraph, paragraphIndex) => {
        const parts: Array<string | Footnote | number> = [];
        let cursor = 0;

        for (const match of paragraph.matchAll(FOOTNOTE_PATTERN)) {
          const start = match.index ?? 0;
          if (start > cursor) parts.push(paragraph.slice(cursor, start));
          const id = Number(match[1]);
          const note = byId.get(id);
          // 각주 데이터가 아직 없으면 비활성 번호로 둔다
          parts.push(note ?? id);
          cursor = start + match[0].length;
        }
        if (cursor < paragraph.length) parts.push(paragraph.slice(cursor));

        const isLast = paragraphIndex === paragraphs.length - 1;

        return (
          <p
            key={paragraphIndex}
            className="text-[0.9375rem] leading-relaxed text-ink-800"
          >
            {parts.map((part, partIndex) => {
              if (typeof part === "string") {
                return <span key={partIndex}>{part}</span>;
              }
              if (typeof part === "number") {
                return <InactiveMark key={partIndex} id={part} />;
              }
              return <FootnoteMark key={partIndex} note={part} />;
            })}
            {streaming && isLast && (
              <span
                aria-hidden="true"
                className="ml-0.5 inline-block h-4 w-[2px] animate-caret bg-brand-500 align-middle"
              />
            )}
          </p>
        );
      })}
    </div>
  );
}

/** 각주 데이터가 오기 전에는 눌러도 반응하지 않게 두지 않고, 비활성으로 표시한다 */
function InactiveMark({ id }: { id: number }) {
  return (
    <span
      aria-hidden="true"
      className="ml-0.5 inline-flex h-[1.1rem] min-w-[1.1rem] items-center justify-center rounded border border-line bg-canvas-200 px-1 align-super text-[0.75rem] font-bold leading-none text-ink-400"
    >
      {id}
    </span>
  );
}

function FootnoteMark({ note }: { note: Footnote }) {
  return (
    <Popover
      triggerLabel={COPY.footnoteButton(note.footnote_id)}
      triggerClassName="align-super"
      trigger={
        <span className="ml-0.5 inline-flex h-[1.1rem] min-w-[1.1rem] items-center justify-center rounded-full border border-brand-200 bg-brand-50 px-1 text-[0.75rem] font-bold leading-none text-brand-700 transition-colors hover:border-brand-400 hover:bg-brand-100">
          {note.footnote_id}
        </span>
      }
    >
      <span className="flex items-center gap-1.5 text-[0.875rem] font-bold text-ink-900">
        <Quote aria-hidden="true" className="h-3.5 w-3.5 text-brand-500" />
        공고 원문 발췌
      </span>
      <span className="mt-2 block rounded-xl border-l-[3px] border-brand-300 bg-canvas-50 px-3 py-2.5 text-[0.9375rem] leading-relaxed text-ink-700">
        {note.excerpt}
      </span>
      <span className="mt-2.5 block text-[0.875rem] text-ink-500">
        {note.agency} · 확인일 {formatDotDate(note.checked_at)}
      </span>
      <a
        href={note.source_url}
        target="_blank"
        rel="noreferrer noopener"
        className="mt-2 inline-flex items-center gap-1 rounded text-[0.875rem] font-semibold text-brand-600 underline decoration-brand-200 decoration-2 underline-offset-2 transition-colors hover:text-brand-700 focus-ring"
      >
        공고 원문 보기
        <ExternalLink aria-hidden="true" className="h-3.5 w-3.5" />
      </a>
    </Popover>
  );
}
