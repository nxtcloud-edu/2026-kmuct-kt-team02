import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  hiddenUnlikelyPolicies,
  streamChat,
  submitFollowupAnswer,
  type StreamController,
} from "@/lib/api";
import {
  buildGuestAnswer,
  buildGuestChips,
  buildGuestFootnotes,
  searchGuestPolicies,
} from "@/lib/mock/guest";
import { FOLLOWUP_FIELD_LABEL } from "@/lib/labels";
import { ERROR_MESSAGE, changedFieldList } from "@/lib/contract";
import type {
  Footnote,
  FollowupField,
  FollowupQuestion,
  PolicyEvaluation,
  PolicyInfo,
  Profile,
  ProfileField,
  SessionCreateResponse,
  Stage,
} from "@/lib/contract";

/** 대화 한 턴 */
export interface Turn {
  id: string;
  role: "user" | "agent";
  text: string;
  complete: boolean;
  stage?: Stage;
  footnotes: Footnote[];
  followup: FollowupQuestion | null;
  /** 관련 질문. 서버가 문자열 배열로 보낸다 (server/sse.py RelatedEventData) */
  related: string[];
  /** 프로필 변경 안내 한 줄 */
  profileNotice?: string;
  error?: string;
}

let turnSeq = 0;
function nextId(prefix: string): string {
  turnSeq += 1;
  return `${prefix}-${Date.now().toString(36)}-${turnSeq}`;
}

function wait(ms: number, timers: number[]): Promise<void> {
  return new Promise((resolve) => {
    timers.push(window.setTimeout(resolve, ms));
  });
}

/**
 * 대화 상태.
 *
 * session이 있으면 프로필 기준으로 판정까지 받는 맞춤 모드다.
 * session이 null이면 로그인 전이라 프로필이 없으므로 공고 기준 안내만 한다.
 */
export function useChatStream(session: SessionCreateResponse | null) {
  const guest = session === null;

  const [profile, setProfile] = useState<Profile | null>(session?.profile ?? null);
  const [policies, setPolicies] = useState<PolicyInfo[]>(session?.policies ?? []);
  const [hiddenCount, setHiddenCount] = useState(session?.hidden_unlikely_count ?? 0);
  const [hiddenPolicies, setHiddenPolicies] = useState<PolicyEvaluation[]>([]);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [askedFields, setAskedFields] = useState<FollowupField[]>([]);
  const [changedFields, setChangedFields] = useState<ProfileField[]>([]);
  const [updatedIds, setUpdatedIds] = useState<string[]>([]);
  const [politeMessage, setPoliteMessage] = useState("");
  const [assertiveMessage, setAssertiveMessage] = useState("");

  const controllerRef = useRef<StreamController | null>(null);
  const guestTimersRef = useRef<number[]>([]);
  const guestCancelRef = useRef(false);
  const profileRef = useRef(profile);
  const askedRef = useRef(askedFields);
  const policiesRef = useRef(policies);
  const lastMessageRef = useRef("");

  useEffect(() => {
    profileRef.current = profile;
  }, [profile]);
  useEffect(() => {
    askedRef.current = askedFields;
  }, [askedFields]);
  useEffect(() => {
    policiesRef.current = policies;
  }, [policies]);

  // 화면을 떠날 때 진행 중인 작업을 정리한다
  useEffect(
    () => () => {
      controllerRef.current?.abort();
      guestCancelRef.current = true;
      for (const timer of guestTimersRef.current) window.clearTimeout(timer);
    },
    [],
  );

  // 강조 표시는 2초 뒤에 지운다
  useEffect(() => {
    if (changedFields.length === 0 && updatedIds.length === 0) return;
    const timer = window.setTimeout(() => {
      setChangedFields([]);
      setUpdatedIds([]);
    }, 2000);
    return () => window.clearTimeout(timer);
  }, [changedFields, updatedIds]);

  const patchLastAgent = useCallback((patch: (turn: Turn) => Turn) => {
    setTurns((prev) => {
      const index = prev.reduce((acc, turn, i) => (turn.role === "agent" ? i : acc), -1);
      if (index === -1) return prev;
      const next = [...prev];
      next[index] = patch(next[index]);
      return next;
    });
  }, []);

  /** 카드 상태가 바뀐 정책을 찾아 강조 대상으로 표시한다 */
  const applyPolicies = useCallback((incoming: PolicyInfo[]) => {
    const before = new Map(
      policiesRef.current.map((item) => [
        item.policy_id,
        (item as PolicyEvaluation).status,
      ]),
    );
    const changed = incoming
      .filter((item) => {
        const status = (item as PolicyEvaluation).status;
        return (
          status !== undefined &&
          before.has(item.policy_id) &&
          before.get(item.policy_id) !== status
        );
      })
      .map((item) => item.policy_id);

    setPolicies(incoming);
    if (changed.length > 0) {
      setUpdatedIds(changed);
      setPoliteMessage("조건이 갱신됐어요");
    }
  }, []);

  const appendTurns = useCallback((text: string) => {
    setTurns((prev) => [
      ...prev,
      {
        id: nextId("user"),
        role: "user",
        text,
        complete: true,
        footnotes: [],
        followup: null,
        related: [],
      },
      {
        id: nextId("agent"),
        role: "agent",
        text: "",
        complete: false,
        stage: "searching",
        footnotes: [],
        followup: null,
        related: [],
      },
    ]);
  }, []);

  /** 로그인 전: 공고 기준 안내만 문장 단위로 흘려 보낸다 */
  const sendAsGuest = useCallback(
    async (text: string) => {
      guestCancelRef.current = false;
      const timers = guestTimersRef.current;

      patchLastAgent((turn) => ({ ...turn, stage: "searching" }));
      setPoliteMessage("정책 찾는 중");
      await wait(320, timers);
      if (guestCancelRef.current) return;

      const found = searchGuestPolicies(text);
      const matched = found.length > 0 && text.trim().length > 0;
      applyPolicies(found);
      setHiddenCount(0);
      setHiddenPolicies([]);

      patchLastAgent((turn) => ({ ...turn, stage: "summarizing" }));
      setPoliteMessage("정리 중");
      await wait(220, timers);
      if (guestCancelRef.current) return;

      for (const sentence of buildGuestAnswer(found, matched)) {
        if (guestCancelRef.current) return;
        patchLastAgent((turn) => ({ ...turn, text: `${turn.text}${sentence}\n\n` }));
        await wait(Math.min(240, 90 + sentence.length * 4), timers);
      }
      if (guestCancelRef.current) return;

      patchLastAgent((turn) => ({
        ...turn,
        footnotes: buildGuestFootnotes(found),
        related: buildGuestChips(found),
        complete: true,
        stage: undefined,
      }));
      setStreaming(false);
      setPoliteMessage(`안내 완료. 제도 ${found.length}건을 보여드렸어요`);
    },
    [patchLastAgent, applyPolicies],
  );

  const send = useCallback(
    (message: string) => {
      const text = message.trim();
      if (!text || streaming) return;

      lastMessageRef.current = text;
      setStreaming(true);
      setAssertiveMessage("");
      appendTurns(text);

      if (guest || !session || !profileRef.current) {
        void sendAsGuest(text);
        return;
      }

      const currentProfile = profileRef.current;
      setPoliteMessage("정책 찾는 중");

      void streamChat(
        {
          session_id: session.session_id,
          message: text,
          client_message_id: nextId("msg"),
          profile: currentProfile,
          askedFields: askedRef.current,
        },
        {
          onStatus: ({ stage }) => {
            patchLastAgent((turn) => ({ ...turn, stage }));
            setPoliteMessage(
              stage === "searching"
                ? "정책 찾는 중"
                : stage === "checking"
                  ? "조건 확인 중"
                  : "정리 중",
            );
          },

          onProfileUpdate: ({ changed_fields, message, profile: nextProfile }) => {
            setProfile(nextProfile);
            profileRef.current = nextProfile;
            setChangedFields(changedFieldList(changed_fields));
            patchLastAgent((turn) => ({ ...turn, profileNotice: message }));
          },

          onPolicies: ({ policies: incoming, hidden_unlikely_count }) => {
            applyPolicies(incoming);
            setHiddenCount(hidden_unlikely_count);
            // 서버는 개수만 준다. 목록은 목업 모드에서만 채워진다
            void hiddenUnlikelyPolicies(profileRef.current!, text).then(setHiddenPolicies);
          },

          onAnswerDelta: ({ delta }) =>
            patchLastAgent((turn) => ({ ...turn, text: turn.text + delta })),

          onFootnotes: ({ footnotes }) =>
            patchLastAgent((turn) => ({ ...turn, footnotes })),

          onFollowup: (followup) => {
            patchLastAgent((turn) => ({ ...turn, followup }));
            setAskedFields((prev) =>
              prev.includes(followup.field) ? prev : [...prev, followup.field],
            );
          },

          onRelated: ({ questions }) =>
            patchLastAgent((turn) => ({ ...turn, related: questions })),

          // 부분 실패. 카드는 그대로 두고 오류 문구만 붙인다
          onStreamError: ({ message: reason }) => {
            patchLastAgent((turn) => ({ ...turn, error: reason }));
            setAssertiveMessage(reason);
          },

          onDone: () => {
            patchLastAgent((turn) => ({ ...turn, complete: true, stage: undefined }));
            setStreaming(false);
            setPoliteMessage(`답변 완료. 제도 ${policiesRef.current.length}개를 찾았어요`);
          },

          onFailure: (failure) => {
            patchLastAgent((turn) => ({
              ...turn,
              complete: true,
              stage: undefined,
              error: failure || ERROR_MESSAGE.answer_failed,
            }));
            setStreaming(false);
            setAssertiveMessage(failure || ERROR_MESSAGE.answer_failed);
          },
        },
      ).then((controller) => {
        controllerRef.current = controller;
      });
    },
    [guest, session, streaming, appendTurns, patchLastAgent, applyPolicies, sendAsGuest],
  );

  const retry = useCallback(() => {
    const message = lastMessageRef.current;
    if (!message) return;
    setTurns((prev) => prev.slice(0, -2));
    window.setTimeout(() => send(message), 0);
  }, [send]);

  const answerFollowup = useCallback(
    (field: FollowupField, value: unknown, label: string) => {
      const current = profileRef.current;
      if (!current) return;

      setAskedFields((prev) => (prev.includes(field) ? prev : [...prev, field]));
      patchLastAgent((turn) => ({ ...turn, followup: null }));

      // 판정을 다시 받는다. 실제 모드는 서버가, 목업 모드는 브라우저가 계산한다
      void submitFollowupAnswer({
        session_id: session?.session_id ?? "",
        profile: current,
        field,
        value,
        query: lastMessageRef.current,
      })
        .then((result) => {
          setProfile(result.profile);
          profileRef.current = result.profile;
          setChangedFields([field]);
          applyPolicies(result.policies);
          setHiddenCount(result.hidden_unlikely_count);
          setHiddenPolicies(result.hidden_unlikely);
        })
        .catch((error: unknown) => {
          // 반영에 실패하면 화면 값을 바꾸지 않는다. 판정과 프로필이 어긋나는 게 더 나쁘다
          setAssertiveMessage(
            error instanceof Error ? error.message : ERROR_MESSAGE.server_error,
          );
        })
        .finally(() => {
          send(`${FOLLOWUP_FIELD_LABEL[field]}는 ${label}이에요`);
        });
    },
    [session, send, patchLastAgent, applyPolicies],
  );

  const skipFollowup = useCallback(
    (field: FollowupField) => {
      setAskedFields((prev) => (prev.includes(field) ? prev : [...prev, field]));
      patchLastAgent((turn) => ({ ...turn, followup: null }));
      setPoliteMessage("건너뛰었어요. 해당 조건은 확인이 필요해요로 남습니다");
    },
    [patchLastAgent],
  );

  const lastAgentIndex = useMemo(
    () => turns.reduce((acc, turn, index) => (turn.role === "agent" ? index : acc), -1),
    [turns],
  );

  return {
    guest,
    profile,
    policies,
    hiddenCount,
    hiddenPolicies,
    turns,
    streaming,
    changedFields,
    updatedIds,
    politeMessage,
    assertiveMessage,
    lastAgentIndex,
    send,
    retry,
    answerFollowup,
    skipFollowup,
  };
}
