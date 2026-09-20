import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";
import { Link } from "react-router-dom";
import { ChevronDown, Search, Send, Sparkles, UserCheck } from "lucide-react";
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
import type { PolicyInfo } from "@/lib/contract";

/**
 * 홈 = AI 대화 화면.
 *
 * 로그인 전에는 프로필이 없어 판정할 수 없으므로 공고 기준 안내만 하고,
 * 로그인하면 맞춤 판정을 받을 수 있다고 알린다.
 * 로그인 후에는 마이페이지에 저장한 조건으로 세션을 만들어 판정까지 받는다.
 */
export function ChatPage() {
  const { session, ready } = useApp();
  // 세션이 생기거나 사라지면 대화를 새로 시작한다
  return <ChatView key={session?.session_id ?? "guest"} ready={ready} />;
}

function ChatView({ ready }: { ready: boolean }) {
  const { session, auth, profileInput, startSessionFromProfile } = useApp();
  const chat = useChatStream(session);

  const [input, setInput] = useState("");
  const [selected, setSelected] = useState<PolicyInfo | null>(null);
  const [showHidden, setShowHidden] = useState(false);
  const timelineRef = useRef<HTMLDivElement>(null);

  // 로그인했고 저장한 조건이 있으면 맞춤 모드로 올린다
  useEffect(() => {
    if (!ready || session || !auth || !profileInput) return;
    void startSessionFromProfile();
  }, [ready, session, auth, profileInput, startSessionFromProfile]);

  useEffect(() => {
    const node = timelineRef.current;
    if (!node) return;
    node.scrollTo({ top: node.scrollHeight, behavior: "smooth" });
  }, [chat.turns]);

  const sensitive = looksSensitive(input);
  const showSkeleton = chat.streaming && chat.policies.length === 0;

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

  return (
    <div className="mx-auto max-w-[1400px] px-4 py-6 sm:px-6 lg:px-8">
      <LiveRegion
        politeMessage={chat.politeMessage}
        assertiveMessage={chat.assertiveMessage}
      />

      {/* 맞춤 모드에서만 프로필 요약 바를 고정한다 */}
      {!chat.guest && chat.profile && (
        <div className="sticky top-[4.25rem] z-30 mb-4">
          <ProfileSummaryBar
            profile={chat.profile}
            changedFields={chat.changedFields}
            onEdit={() => undefined}
          />
        </div>
      )}

      {/* 로그인 전 안내 */}
      {chat.guest && <GuestNotice loggedIn={Boolean(auth)} />}

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.1fr)]">
        {/* 정책 카드 */}
        <section aria-label="찾은 제도" className="order-2 lg:order-1">
          <div className="mb-3 flex items-baseline justify-between gap-3">
            <h2 className="text-[1.0625rem] font-bold text-ink-900">
              {chat.guest ? "관련 제도" : "찾은 제도"}{" "}
              <span className="text-gradient">{chat.policies.length}</span>건
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
                title="아직 보여드릴 제도가 없어요"
                description="오른쪽 대화창에 지금 상황이나 필요한 걸 말해 주시면 관련 제도를 찾아 정리해 드릴게요."
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

          {/* 접힌 영역은 판정이 있을 때만 의미가 있다 */}
          {!chat.guest && chat.hiddenCount > 0 && (
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
          className="order-1 flex min-h-[34rem] flex-col overflow-hidden rounded-3xl border border-line bg-white shadow-panel lg:order-2 lg:sticky lg:top-[5.5rem] lg:h-[calc(100vh-9rem)]"
        >
          <div className="flex items-center justify-between gap-3 border-b border-line-soft bg-canvas-50 px-5 py-4">
            <div className="flex items-center gap-3">
              <Logo variant="mark" size={30} />
              <div>
                <h1 className="text-[1.0625rem] font-bold text-ink-900">
                  쏘다 AI 혜택 상담
                </h1>
                <p className="text-[0.875rem] text-ink-500">
                  {chat.guest
                    ? "공고에 적힌 내용을 근거와 함께 알려드려요"
                    : "내 조건으로 판정해서 근거와 함께 알려드려요"}
                </p>
              </div>
            </div>
            <span
              className={
                chat.guest
                  ? "hidden shrink-0 rounded-full border border-line bg-white px-2.5 py-1 text-[0.875rem] font-semibold text-ink-600 sm:inline-flex"
                  : "hidden shrink-0 items-center gap-1.5 rounded-full border border-likely-border bg-likely-bg px-2.5 py-1 text-[0.875rem] font-bold text-likely sm:inline-flex"
              }
            >
              {chat.guest ? (
                "공고 기준"
              ) : (
                <>
                  <UserCheck aria-hidden="true" className="h-3.5 w-3.5" />
                  맞춤 판정
                </>
              )}
            </span>
          </div>

          <div
            ref={timelineRef}
            className="scrollbar-slim flex-1 space-y-4 overflow-y-auto px-5 py-5"
          >
            {chat.turns.length === 0 && (
              <div className="rounded-2xl border border-line-soft bg-canvas-50 p-4">
                <p className="text-[0.9375rem] leading-relaxed text-ink-700">
                  안녕하세요, 쏘다예요. 정책 이름을 몰라도 괜찮아요. 지금 상황이나 필요한
                  걸 편하게 말해 주세요.
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

                    {/* 로그인 전 답변 끝에 맞춤 판정 안내를 붙인다 */}
                    {chat.guest && turn.complete && !turn.error && (
                      <LoginUpsell loggedIn={Boolean(auth)} compact />
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

          <form onSubmit={submit} className="border-t border-line-soft bg-canvas-50 px-5 py-4">
            <label htmlFor="chat-input" className="sr-only">
              상담 내용 입력
            </label>
            <div className="flex items-end gap-2 rounded-2xl border border-line bg-white p-2 transition-colors focus-within:border-brand-300">
              <textarea
                id="chat-input"
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

/** 화면 상단 안내 */
function GuestNotice({ loggedIn }: { loggedIn: boolean }) {
  return (
    <div className="mb-4 flex flex-col gap-3 rounded-2xl border border-brand-200 bg-brand-50/70 px-4 py-3.5 sm:flex-row sm:items-center sm:justify-between">
      <p className="flex items-start gap-2.5 text-[0.9375rem] leading-relaxed text-ink-800">
        <Sparkles aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-brand-600" />
        {loggedIn ? (
          <span>
            <span className="font-bold">조건을 채우면 맞춤 판정을 받을 수 있어요.</span>{" "}
            마이페이지에서 나이와 현재 상태를 알려주시면 제도마다 내가 조건을 채우는지
            따져서 보여드려요.
          </span>
        ) : (
          <span>
            <span className="font-bold">
              지금은 공고에 적힌 내용만 알려드릴 수 있어요.
            </span>{" "}
            로그인하면 나이와 현재 상태를 기준으로 내가 신청할 수 있는 제도인지 따져서
            알려드려요.
          </span>
        )}
      </p>
      <Link to={loggedIn ? "/mypage" : "/login"} className="shrink-0 rounded-xl focus-ring">
        <Button size="sm">{loggedIn ? "내 조건 입력하기" : "로그인하고 맞춤 안내 받기"}</Button>
      </Link>
    </div>
  );
}

/** 답변 아래에 붙는 맞춤 판정 안내 */
function LoginUpsell({
  loggedIn,
  compact = false,
}: {
  loggedIn: boolean;
  compact?: boolean;
}) {
  return (
    <div
      className={`rounded-2xl border border-dashed border-brand-300 bg-brand-50/50 px-4 ${
        compact ? "py-3" : "py-4"
      }`}
    >
      <p className="flex items-start gap-2 text-[0.9375rem] leading-relaxed text-ink-700">
        <UserCheck aria-hidden="true" className="mt-0.5 h-4 w-4 shrink-0 text-brand-600" />
        {loggedIn
          ? "마이페이지에 나이와 현재 상태를 저장하면 위 제도가 내 조건에 맞는지 조건별로 따져서 보여드릴 수 있어요."
          : "로그인하면 위 제도가 내 조건에 맞는지 조건별로 따져서 보여드릴 수 있어요. 나이와 현재 상태만 알려주시면 돼요."}
      </p>
      <Link
        to={loggedIn ? "/mypage" : "/login"}
        className="mt-2.5 inline-block rounded-xl focus-ring"
      >
        <Button size="sm" variant="secondary">
          {loggedIn ? "내 조건 입력하기" : "로그인하기"}
        </Button>
      </Link>
    </div>
  );
}
