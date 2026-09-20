import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  applyFollowupAnswer,
  mockHiddenUnlikely,
  recalculate,
  streamChat,
  type StreamController,
} from "@/lib/api";
import { FOLLOWUP_FIELD_LABEL } from "@/lib/labels";
import { ERROR_MESSAGE } from "@/lib/contract";
import type {
  Footnote,
  FollowupField,
  FollowupQuestion,
  PolicyEvaluation,
  Profile,
  ProfileField,
  RelatedChip,
  SessionCreateResponse,
  Stage,
} from "@/lib/contract";

/** 대화 한 턴 */
export interface Turn {
  id: string;
  role: "user" | "agent";
  /** 사용자 메시지 또는 답변 본문 */
  text: string;
  /** 스트리밍이 끝났는지 */
  complete: boolean;
  stage?: Stage;
  footnotes: Footnote[];
  followup: FollowupQuestion | null;
  related: RelatedChip[];
  /** 프로필 변경 안내 한 줄 */
  profileNotice?: string;
  error?: string;
}

let turnSeq = 0;
function nextId(prefix: string): string {
  turnSeq += 1;
  return `${prefix}-${Date.now().toString(36)}-${turnSeq}`;
}

export function useChatStream(session: SessionCreateResponse) {
  const [profile, setProfile] = useState<Profile>(session.profile);
  const [policies, setPolicies] = useState<PolicyEvaluation[]>(session.policies);
  const [hiddenCount, setHiddenCount] = useState(session.hidden_unlikely_count);
  const [hiddenPolicies, setHiddenPolicies] = useState<PolicyEvaluation[]>([]);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [streaming, setStreaming] = useState(false);
  const [askedFields, setAskedFields] = useState<FollowupField[]>([]);
  const [changedFields, setChangedFields] = useState<ProfileField[]>([]);
  /** 상태가 방금 바뀐 정책 */
  const [updatedIds, setUpdatedIds] = useState<string[]>([]);
  /** 스크린리더에 알릴 짧은 메시지 */
  const [politeMessage, setPoliteMessage] = useState("");
  const [assertiveMessage, setAssertiveMessage] = useState("");

  const controllerRef = useRef<StreamController | null>(null);
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

  // 화면을 떠날 때 진행 중인 스트림을 정리한다
  useEffect(() => () => controllerRef.current?.abort(), []);

  // 강조 표시는 2초 뒤에 지운다 (frontend/README.md 3-5, 3-9)
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
  const applyPolicies = useCallback((incoming: PolicyEvaluation[]) => {
    const before = new Map(
      policiesRef.current.map((item) => [item.policy_id, item.status]),
    );
    const changed = incoming
      .filter((item) => before.has(item.policy_id) && before.get(item.policy_id) !== item.status)
      .map((item) => item.policy_id);

    setPolicies(incoming);
    if (changed.length > 0) {
      setUpdatedIds(changed);
      // 카드마다 알리지 않고 한 번만 알린다
      setPoliteMessage("조건이 갱신됐어요");
    }
  }, []);

  const send = useCallback(
    (message: string) => {
      const text = message.trim();
      if (!text || streaming) return;

      lastMessageRef.current = text;
      setStreaming(true);
      setAssertiveMessage("");
      setPoliteMessage("정책 찾는 중");

      setTurns((prev) => [
        ...prev,
        { id: nextId("user"), role: "user", text, complete: true, footnotes: [], followup: null, related: [] },
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

      void streamChat(
        {
          session_id: session.session_id,
          message: text,
          client_message_id: nextId("msg"),
          profile: profileRef.current,
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

          onProfileUpdate: ({ changes, notice, profile: nextProfile }) => {
            setProfile(nextProfile);
            profileRef.current = nextProfile;
            setChangedFields(changes.map((change) => change.field));
            patchLastAgent((turn) => ({ ...turn, profileNotice: notice }));
          },

          onPolicies: ({ policies: incoming, hidden_unlikely_count }) => {
            applyPolicies(incoming);
            setHiddenCount(hidden_unlikely_count);
            setHiddenPolicies(mockHiddenUnlikely(profileRef.current, text));
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

          onRelated: ({ chips }) =>
            patchLastAgent((turn) => ({ ...turn, related: chips })),

          // 부분 실패. 카드는 그대로 두고 오류 문구만 붙인다
          onStreamError: ({ message: reason }) => {
            patchLastAgent((turn) => ({ ...turn, error: reason }));
            setAssertiveMessage(reason);
          },

          onDone: () => {
            patchLastAgent((turn) => ({ ...turn, complete: true, stage: undefined }));
            setStreaming(false);
            setPoliteMessage(
              `답변 완료. 제도 ${policiesRef.current.length}개를 찾았어요`,
            );
          },

          onFailure: (failure) => {
            // 카드는 그대로 두고 오류 문구만 붙인다
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
    [session.session_id, streaming, patchLastAgent, applyPolicies],
  );

  const retry = useCallback(() => {
    const message = lastMessageRef.current;
    if (!message) return;
    setTurns((prev) => prev.slice(0, -2));
    window.setTimeout(() => send(message), 0);
  }, [send]);

  /**
   * 후속 질문에 답한다.
   * 목업 모드에서는 프로필에 값을 바로 넣고 결과를 다시 계산한다.
   * 서버 모드에서는 답변을 메시지로 보내 서버가 프로필에 반영한다.
   */
  const answerFollowup = useCallback(
    (field: FollowupField, value: unknown, label: string) => {
      const nextProfile = applyFollowupAnswer(profileRef.current, field, value);
      setProfile(nextProfile);
      profileRef.current = nextProfile;
      // planned_basis는 프로필 항목이 아니라 강조 대상에서 뺀다
      if (field !== "planned_basis") setChangedFields([field]);
      setAskedFields((prev) => (prev.includes(field) ? prev : [...prev, field]));

      const result = recalculate(nextProfile, lastMessageRef.current);
      applyPolicies(result.policies);
      setHiddenCount(result.hidden_unlikely_count);
      setHiddenPolicies(mockHiddenUnlikely(nextProfile, lastMessageRef.current));

      patchLastAgent((turn) => ({ ...turn, followup: null }));
      send(`${FOLLOWUP_FIELD_LABEL[field]}는 ${label}이에요`);
    },
    [send, patchLastAgent, applyPolicies],
  );

  const skipFollowup = useCallback(
    (field: FollowupField) => {
      // 건너뛴 항목은 미확인으로 남기고 다시 묻지 않는다
      setAskedFields((prev) => (prev.includes(field) ? prev : [...prev, field]));
      patchLastAgent((turn) => ({ ...turn, followup: null }));
      setPoliteMessage("건너뛰었어요. 해당 조건은 확인이 필요해요로 남습니다");
    },
    [patchLastAgent],
  );

  /** 프로필을 직접 수정했을 때 (요약 바 수정) */
  const updateProfile = useCallback(
    (nextProfile: Profile, changed: ProfileField[]) => {
      setProfile(nextProfile);
      profileRef.current = nextProfile;
      setChangedFields(changed);

      const result = recalculate(nextProfile, lastMessageRef.current);
      applyPolicies(result.policies);
      setHiddenCount(result.hidden_unlikely_count);
      setHiddenPolicies(mockHiddenUnlikely(nextProfile, lastMessageRef.current));
    },
    [applyPolicies],
  );

  const lastAgentIndex = useMemo(
    () => turns.reduce((acc, turn, index) => (turn.role === "agent" ? index : acc), -1),
    [turns],
  );

  return {
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
    updateProfile,
  };
}
