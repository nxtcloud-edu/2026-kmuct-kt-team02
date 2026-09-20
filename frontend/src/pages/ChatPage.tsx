import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { ChevronDown, Search, Send } from "lucide-react";
import { Button } from "@/components/ui/Button";
import { Card, EmptyState } from "@/components/ui/Surface";
import { LiveRegion } from "@/components/ui/LiveRegion";
import { Logo } from "@/components/brand/Logo";
import { PolicyCard, PolicyCardSkeleton } from "@/components/policy/PolicyCard";
import { PolicyDetailPanel } from "@/components/policy/PolicyDetailPanel";
import { AnswerBody } from "@/components/chat/AnswerBody";
import { FollowupCard } from "@/components/chat/FollowupCard";
import { StatusSteps } from "@/components/chat/StatusSteps";
import { ProfileSummaryBar } from "@/components/chat/ProfileSummaryBar";
import { useChatStream } from "@/hooks/useChatStream";
import { useApp } from "@/providers/AppProvider";
import { looksSensitive } from "@/lib/mask";
import { COPY, EXAMPLE_CHIPS } from "@/lib/labels";
import type { PolicyEvaluation } from "@/lib/contract";

export function ChatPage() {
  const { session, ready } = useApp();
  const navigate = useNavigate();

  // 온보딩을 거치지 않았으면 홈으로 보낸다
  useEffect(() => {
    if (ready && !session) navigate("/", { replace: true });
  }, [ready, session, navigate]);

  if (!session) return null;
  return <ChatView key={session.session_id} />;
}

function ChatView() {
  const { session } = useApp();
  const navigate = useNavigate();
  const location = useLocation();
  const chat = useChatStream(session!);

  const [input, setInput] = useState("");
  const [selected, setSelected] = useState<PolicyEvaluation | null>(null);
  const [showHidden, setShowHidden] = useState(false);
  const startedRef = useRef(false);
  const timelineRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  // 온보딩에서 넘어온 첫 질문을 한 번만 보낸다
  useEffect(() => {
    if (startedRef.current) return;
    startedRef.current = true;
    const state = location.state as { firstMessage?: string } | null;
    const first = state?.firstMessage?.trim();
    if (first) chat.send(first);
  }, [location.state, chat]);

  // 새 메시지가 붙으면 타임라인을 아래로 옮긴다
  useEffect(() => {
    const node = timelineRef.current;
    if (!node) return;
    node.scrollTo({ top: node.scrollHeight, behavior: "smooth" });
  }, [chat.turns]);

  const sensitive = looksSensitive(input);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!input.trim() || chat.streaming) return;
    chat.send(input);
    setInput("");
  };

  const onKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      if (!input.trim() || chat.streaming) return;
      chat.send(input);
      setInput("");
    }
  };

  const showSkeleton = chat.streaming && chat.policies.length === 0;

  return (
    <div className="mx-auto max-w-[1400px] px-4 py-6 sm:px-6 lg:px-8">
      {/* 라이브 리전은 갱신 전에 페이지에 있어야 한다 */}
      <LiveRegion
        politeMessage={chat.politeMessage}
        assertiveMessage={chat.assertiveMessage}
      />

      {/* 상단 고정 프로필 요약 바 */}
      <div className="sticky top-[4.25rem] z-30 mb-4">
        <ProfileSummaryBar
          profile={chat.profile}
          changedFields={chat.changedFields}
          onEdit={() => navigate("/mypage")}
        />
      </div>

      {/* 결과 카드 왼쪽, 대화 오른쪽 */}
      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
        {/* 정책 카드 묶음 */}
        <section aria-label="찾은 제도" className="order-2 lg:order-1">
          <div className="mb-3 flex items-baseline justify-between gap-3">
            <h2 className="text-[1.0625rem] font-bold text-ink-900">
              찾은 제도{" "}
              <span className="text-gradient">{chat.policies.length}</span>개
            </h2>
            <Link
              to="/policies"
              className="rounded text-[0.875rem] font-semibold text-brand-600 underline decoration-brand-200 decoration-2 underline-offset-2 transition-colors hover:text-brand-700 focus-ring"
            >
              전체 정책 보기
            </Link>
          </div>

          {showSkeleton ? (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-1 xl:grid-cols-2">
              <PolicyCardSkeleton />
              <PolicyCardSkeleton />
              <PolicyCardSkeleton />
            </div>
          ) : chat.policies.length === 0 ? (
            <Card>
              <EmptyState
                icon={Search}
                title={COPY.emptyTitle}
                description={
                  <ul className="space-y-1 text-left">
                    <li>· {COPY.emptyHintAll}</li>
                    <li>· {COPY.emptyHintDistrict}</li>
                  </ul>
                }
                action={
                  <a
                    href={COPY.emptyPortalUrl}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="rounded-xl focus-ring"
                  >
                    <Button variant="secondary">{COPY.emptyPortalLabel}</Button>
                  </a>
                }
              />
            </Card>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-1 xl:grid-cols-2">
              {chat.policies.map((policy) => (
                <PolicyCard
                  key={policy.policy_id}
                  policy={policy}
                  onOpen={setSelected}
                  updated={chat.updatedIds.includes(policy.policy_id)}
                />
              ))}
            </div>
          )}

          {/* 접힌 영역: 조건이 맞지 않을 수 있는 제도 */}
          {chat.hiddenCount > 0 && (
            <div className="mt-3">
              <button
                type="button"
                onClick={() => setShowHidden((value) => !value)}
                aria-expanded={showHidden}
                className="flex w-full items-center justify-between gap-2 rounded-xl border border-line bg-white px-4 py-3 text-[0.9375rem] font-semibold text-ink-700 transition-colors hover:border-line-strong focus-ring"
              >
                {COPY.hiddenUnlikely(chat.hiddenCount)}
                <ChevronDown
                  aria-hidden="true"
                  className={`h-4 w-4 transition-transform ${showHidden ? "rotate-180" : ""}`}
                />
              </button>

              {showHidden && (
                <div className="mt-3 grid animate-fade-in gap-3 sm:grid-cols-2 lg:grid-cols-1 xl:grid-cols-2">
                  {chat.hiddenPolicies.map((policy) => (
                    <PolicyCard
                      key={policy.policy_id}
                      policy={policy}
                      onOpen={setSelected}
                    />
                  ))}
                </div>
              )}
            </div>
          )}
        </section>

        {/* AI 대화 */}
        <section
          aria-label="AI 혜택 상담"
          className="order-1 flex min-h-[34rem] flex-col overflow-hidden rounded-3xl border border-line bg-white shadow-panel lg:order-2 lg:h-[calc(100vh-13rem)] lg:sticky lg:top-[9.5rem]"
        >
          <div className="flex items-center gap-3 border-b border-line-soft bg-canvas-50 px-5 py-4">
            <Logo variant="mark" size={30} />
            <div>
              <h2 className="text-[1.0625rem] font-bold text-ink-900">쏘다 AI 상담</h2>
              <p className="text-[0.875rem] text-ink-500">
                공식 공고를 근거로 답하고, 모든 판정에 원문 각주를 붙여요
              </p>
            </div>
          </div>

          <div
            ref={timelineRef}
            className="scrollbar-slim flex-1 space-y-4 overflow-y-auto px-5 py-5"
          >
            {chat.turns.length === 0 && (
              <div className="rounded-2xl border border-line-soft bg-canvas-50 p-4">
                <p className="text-[0.9375rem] leading-relaxed text-ink-700">
                  왼쪽에 규칙으로 먼저 찾은 제도를 띄웠어요. 궁금한 걸 말해 주시면 조건을
                  확인해서 근거와 함께 설명해 드릴게요.
                </p>
                <ul className="mt-3 flex flex-wrap gap-2">
                  {EXAMPLE_CHIPS.map((chip) => (
                    <li key={chip}>
                      <button
                        type="button"
                        onClick={() => chat.send(chip)}
                        className="rounded-full border border-line bg-white px-3 py-1.5 text-left text-[0.875rem] font-medium text-ink-700 transition-colors hover:border-brand-300 hover:text-brand-700 focus-ring"
                      >
                        {chip}
                      </button>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {chat.turns.map((turn, index) => {
              if (turn.role === "user") {
                return (
                  <div key={turn.id} className="flex animate-fade-up justify-end">
                    <p className="max-w-[85%] rounded-2xl rounded-br-md bg-brand-gradient px-4 py-2.5 text-[0.9375rem] leading-relaxed text-white">
                      {turn.text}
                    </p>
                  </div>
                );
              }

              const interactive = !chat.streaming && index === chat.lastAgentIndex;

              return (
                <div key={turn.id} className="flex animate-fade-up gap-2.5">
                  <span className="mt-0.5 hidden shrink-0 sm:block">
                    <Logo variant="mark" size={28} />
                  </span>

                  <div className="min-w-0 flex-1 space-y-2">
                    {turn.stage && <StatusSteps stage={turn.stage} />}

                    {turn.profileNotice && (
                      <p className="rounded-lg border border-brand-200 bg-brand-50 px-3 py-2 text-[0.875rem] font-semibold text-brand-700">
                        {turn.profileNotice}
                      </p>
                    )}

                    {turn.text.length > 0 && (
                      <div className="rounded-2xl rounded-tl-md border border-line bg-white px-4 py-3">
                        <AnswerBody
                          text={turn.text}
                          footnotes={turn.footnotes}
                          streaming={!turn.complete}
                        />
                      </div>
                    )}

                    {turn.error && (
                      <div className="rounded-2xl border border-urgent-border bg-urgent-bg px-4 py-3">
                        <p className="text-[0.9375rem] leading-relaxed text-ink-800">
                          {turn.error}
                        </p>
                        <Button
                          size="sm"
                          variant="secondary"
                          className="mt-3"
                          onClick={chat.retry}
                        >
                          {COPY.retry}
                        </Button>
                      </div>
                    )}

                    {turn.followup && (
                      <FollowupCard
                        followup={turn.followup}
                        disabled={!interactive}
                        onAnswer={(value, label) =>
                          chat.answerFollowup(turn.followup!.field, value, label)
                        }
                        onSkip={() => chat.skipFollowup(turn.followup!.field)}
                      />
                    )}

                    {turn.complete && turn.related.length > 0 && (
                      <ul className="flex flex-wrap gap-2">
                        {turn.related.map((chip) => (
                          <li key={chip.id}>
                            <button
                              type="button"
                              disabled={chat.streaming}
                              onClick={() => chat.send(chip.text)}
                              className="rounded-full border border-line bg-white px-3 py-1.5 text-[0.875rem] font-medium text-ink-600 transition-colors hover:border-brand-300 hover:text-brand-700 disabled:opacity-55 focus-ring"
                            >
                              {chip.text}
                            </button>
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          {/* 입력창. 답변 생성 중에는 비활성 */}
          <form onSubmit={submit} className="border-t border-line-soft bg-canvas-50 px-5 py-4">
            <label htmlFor="chat-input" className="sr-only">
              상담 내용 입력
            </label>
            <div className="flex items-end gap-2 rounded-2xl border border-line bg-white p-2 transition-colors focus-within:border-brand-300">
              <textarea
                id="chat-input"
                ref={inputRef}
                rows={1}
                value={input}
                disabled={chat.streaming}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={onKeyDown}
                placeholder={COPY.chatPlaceholder}
                className="max-h-28 min-h-[2.5rem] flex-1 resize-none bg-transparent px-3 py-2 text-[0.9375rem] leading-relaxed text-ink-900 outline-none placeholder:text-ink-400 disabled:cursor-not-allowed"
              />
              <Button
                type="submit"
                disabled={!input.trim() || chat.streaming}
                loading={chat.streaming}
                aria-label="메시지 전송"
                leftIcon={<Send aria-hidden="true" className="h-4 w-4" />}
              >
                전송
              </Button>
            </div>

            <p className="mt-2 text-[0.875rem] text-ink-500">
              {sensitive ? (
                <span className="font-semibold text-urgent">{COPY.piiNotice}</span>
              ) : (
                "Enter로 전송, Shift + Enter로 줄바꿈해요."
              )}
            </p>
          </form>
        </section>
      </div>

      <PolicyDetailPanel policy={selected} onClose={() => setSelected(null)} />
    </div>
  );
}
