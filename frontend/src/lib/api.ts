/**
 * 서버 통신 레이어.
 *
 * - API 주소는 빌드에 박지 않고 `public/config.json`을 실행 중에 읽는다
 *   (frontend/README.md 9장: 주소가 바뀌어도 그 파일만 다시 올린다)
 * - `/chat`은 POST 스트리밍이라 브라우저 기본 EventSource를 쓸 수 없고
 *   @microsoft/fetch-event-source 를 쓴다
 * - 주소가 비어 있으면 같은 이벤트 순서를 만드는 목업 스트림으로 동작한다
 *
 * 이벤트 순서: status → (profile_update) → policies → status → answer_delta → footnotes
 *              → (followup) → (related) → done
 * 모든 이벤트 data는 {request_id, seq, payload} 봉투에 담겨 온다 (server/sse.py).
 */

import { fetchEventSource } from "@microsoft/fetch-event-source";
import type {
  AnswerDeltaPayload,
  DonePayload,
  ErrorEventPayload,
  ErrorResponse,
  FollowupField,
  FollowupQuestion,
  FootnotesPayload,
  PoliciesPayload,
  Profile,
  ProfileInput,
  ProfileUpdatePayload,
  RelatedPayload,
  SSEEnvelope,
  SessionCreateResponse,
  StatusPayload,
} from "./contract";
import { ERROR_MESSAGE } from "./contract";
import { mockEvaluate } from "./mock/evaluate";
import { buildAnswer, buildFootnotes, buildRelated, pickFollowup } from "./mock/answer";

/* ------------------------------------------------------------------ *
 * 런타임 설정
 * ------------------------------------------------------------------ */

interface RuntimeConfig {
  api_base_url: string;
}

let configPromise: Promise<RuntimeConfig> | null = null;

/** config.json을 한 번만 읽는다. 실패하면 목업 모드로 떨어진다. */
export function loadConfig(): Promise<RuntimeConfig> {
  if (configPromise) return configPromise;

  configPromise = (async () => {
    try {
      // base가 './'이므로 문서 기준 상대 경로로 읽는다.
      const response = await fetch("config.json", { cache: "no-store" });
      if (!response.ok) return { api_base_url: "" };
      const parsed = (await response.json()) as Partial<RuntimeConfig>;
      return { api_base_url: (parsed.api_base_url ?? "").replace(/\/$/, "") };
    } catch {
      return { api_base_url: "" };
    }
  })();

  return configPromise;
}

export async function isMockMode(): Promise<boolean> {
  const config = await loadConfig();
  return config.api_base_url.length === 0;
}

/** 서버 오류 응답을 사용자 문구로 바꾼다 */
async function toErrorMessage(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as ErrorResponse;
    if (body?.error?.message) return body.error.message;
    if (body?.error?.code) return ERROR_MESSAGE[body.error.code];
  } catch {
    // 본문이 JSON이 아닌 경우는 아래 기본 문구를 쓴다
  }
  if (response.status === 404) return ERROR_MESSAGE.session_expired;
  if (response.status === 422) return ERROR_MESSAGE.invalid_input;
  return ERROR_MESSAGE.server_error;
}

/* ------------------------------------------------------------------ *
 * POST /session
 * ------------------------------------------------------------------ */

/** 목업 세션 결과를 만든다 */
function mockSession(input: ProfileInput): SessionCreateResponse {
  const profile: Profile = { ...input, region: "seoul" };
  const result = mockEvaluate(profile);
  return {
    session_id: `mock-${Date.now().toString(36)}`,
    profile,
    policies: result.policies,
    hidden_unlikely_count: result.hidden_unlikely_count,
    followup: null,
  };
}

export async function createSession(
  input: ProfileInput,
): Promise<SessionCreateResponse> {
  const { api_base_url } = await loadConfig();
  if (!api_base_url) return mockSession(input);

  const response = await fetch(`${api_base_url}/session`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });

  if (!response.ok) throw new Error(await toErrorMessage(response));
  return (await response.json()) as SessionCreateResponse;
}

/* ------------------------------------------------------------------ *
 * POST /chat (스트리밍)
 * ------------------------------------------------------------------ */

export interface StreamHandlers {
  onStatus: (payload: StatusPayload) => void;
  onProfileUpdate: (payload: ProfileUpdatePayload) => void;
  onPolicies: (payload: PoliciesPayload) => void;
  onAnswerDelta: (payload: AnswerDeltaPayload) => void;
  onFootnotes: (payload: FootnotesPayload) => void;
  onFollowup: (payload: FollowupQuestion) => void;
  onRelated: (payload: RelatedPayload) => void;
  onDone: (payload: DonePayload) => void;
  /** 부분 실패. 카드는 유지하고 오류 문구만 보여 준다 */
  onStreamError: (payload: ErrorEventPayload) => void;
  /** 스트림이 끊기거나 열리지 못한 경우 */
  onFailure: (message: string) => void;
}

export interface ChatStreamOptions {
  session_id: string;
  message: string;
  client_message_id: string;
  /** 목업 모드에서 판정에 쓸 프로필 */
  profile: Profile;
  /** 목업 모드에서 이미 물어본 항목 */
  askedFields: FollowupField[];
}

export interface StreamController {
  abort: () => void;
}

export async function streamChat(
  options: ChatStreamOptions,
  handlers: StreamHandlers,
): Promise<StreamController> {
  const { api_base_url } = await loadConfig();
  if (!api_base_url) return startMockStream(options, handlers);

  const controller = new AbortController();

  void fetchEventSource(`${api_base_url}/chat`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: JSON.stringify({
      session_id: options.session_id,
      message: options.message,
      client_message_id: options.client_message_id,
    }),
    signal: controller.signal,
    // 탭이 숨겨져도 스트림을 유지한다
    openWhenHidden: true,
    onopen: async (response) => {
      if (response.ok) return;
      throw new Error(await toErrorMessage(response));
    },
    onmessage: (event) => {
      if (!event.data) return;
      let envelope: SSEEnvelope<unknown>;
      try {
        envelope = JSON.parse(event.data) as SSEEnvelope<unknown>;
      } catch {
        return;
      }
      dispatch(event.event, envelope.payload, handlers);
    },
    onerror: (error) => {
      const message =
        error instanceof Error && error.message ? error.message : ERROR_MESSAGE.server_error;
      handlers.onFailure(message);
      // 값을 throw 하면 fetch-event-source가 자동 재시도를 멈춘다
      throw error;
    },
  });

  return { abort: () => controller.abort() };
}

function dispatch(
  name: string | undefined,
  payload: unknown,
  handlers: StreamHandlers,
): void {
  switch (name) {
    case "status":
      handlers.onStatus(payload as StatusPayload);
      return;
    case "profile_update":
      handlers.onProfileUpdate(payload as ProfileUpdatePayload);
      return;
    case "policies":
      handlers.onPolicies(payload as PoliciesPayload);
      return;
    case "answer_delta":
      handlers.onAnswerDelta(payload as AnswerDeltaPayload);
      return;
    case "footnotes":
      handlers.onFootnotes(payload as FootnotesPayload);
      return;
    case "followup":
      handlers.onFollowup(payload as FollowupQuestion);
      return;
    case "related":
      handlers.onRelated(payload as RelatedPayload);
      return;
    case "error":
      handlers.onStreamError(payload as ErrorEventPayload);
      return;
    case "done":
      handlers.onDone(payload as DonePayload);
      return;
    default:
      return;
  }
}

/* ------------------------------------------------------------------ *
 * 목업 스트림
 * ------------------------------------------------------------------ */

function startMockStream(
  options: ChatStreamOptions,
  handlers: StreamHandlers,
): StreamController {
  let cancelled = false;
  const timers: number[] = [];

  const wait = (ms: number) =>
    new Promise<void>((resolve) => {
      timers.push(window.setTimeout(resolve, ms));
    });

  const run = async () => {
    const startedAt = performance.now();
    try {
      handlers.onStatus({ stage: "searching" });
      await wait(320);
      if (cancelled) return;

      const result = mockEvaluate(options.profile, options.message);

      // 규칙 기반 카드를 답변보다 먼저 내려 준다 (P0: 즉시 1차 결과)
      handlers.onPolicies({
        policies: result.policies,
        hidden_unlikely_count: result.hidden_unlikely_count,
      });

      handlers.onStatus({ stage: "checking" });
      await wait(280);
      if (cancelled) return;

      handlers.onStatus({ stage: "summarizing" });
      await wait(200);
      if (cancelled) return;

      // 문장 단위로 붙인다. 글자 단위로 쪼개지 않는다
      for (const sentence of buildAnswer(result.policies)) {
        if (cancelled) return;
        handlers.onAnswerDelta({ delta: `${sentence}\n\n` });
        await wait(Math.min(260, 90 + sentence.length * 4));
      }
      if (cancelled) return;

      handlers.onFootnotes({ footnotes: buildFootnotes(result.policies) });

      const followup = pickFollowup(result.policies, options.profile, options.askedFields);
      if (followup) {
        await wait(140);
        if (cancelled) return;
        handlers.onFollowup(followup);
      }

      const related = buildRelated(result.policies);
      if (related.length > 0) handlers.onRelated({ chips: related });

      handlers.onDone({
        total_duration_ms: Math.max(0, Math.round(performance.now() - startedAt)),
      });
    } catch {
      if (!cancelled) handlers.onFailure(ERROR_MESSAGE.answer_failed);
    }
  };

  void run();

  return {
    abort: () => {
      cancelled = true;
      for (const timer of timers) window.clearTimeout(timer);
    },
  };
}

/**
 * 목업 모드에서 후속 질문 답변을 프로필에 반영한다.
 * planned_basis는 프로필 항목이 아니므로 넣지 않는다 (docs/03-api-contract.md 3장).
 */
export function applyFollowupAnswer(
  profile: Profile,
  field: FollowupField,
  value: unknown,
): Profile {
  if (field === "planned_basis") return profile;
  return { ...profile, [field]: value } as Profile;
}

/** 목업 모드에서 프로필 변경 후 결과를 다시 계산한다 */
export function recalculate(profile: Profile, query = ""): PoliciesPayload {
  const result = mockEvaluate(profile, query);
  return {
    policies: result.policies,
    hidden_unlikely_count: result.hidden_unlikely_count,
  };
}

/** 접힌 영역에 보여줄 unlikely 목록 (목업 전용) */
export function mockHiddenUnlikely(profile: Profile, query = "") {
  return mockEvaluate(profile, query).hidden_unlikely;
}
