import type {
  ApiErrorBody,
  CreateDeckResponse,
  GradeResponse,
  SessionStartResponse,
  TranscribeResponse,
} from "./types";

// The tutor-agent is called directly from the browser. Rather than baking a
// per-environment URL into the bundle at build time — NEXT_PUBLIC_* vars are
// inlined at build, which would mean one image per environment and a rebuild
// whenever a node's public IP changed — the URL is derived at runtime:
//
//   - same hostname as the page (dev and prod share the worker node's IP), and
//   - agent NodePort = frontend NodePort + 500  (dev 30300->30800, prod 31300->31800).
//
// So one image works in dev, prod, and on any IP. Falls back to localhost for
// `next dev` and for SSR, where window does not exist.
export function resolveAgentUrl(): string {
  const override = process.env.NEXT_PUBLIC_AGENT_URL;
  if (override) return override;

  if (typeof window !== "undefined") {
    const { protocol, hostname, port } = window.location;
    const frontendPort = Number(port);
    if (Number.isFinite(frontendPort) && frontendPort > 0) {
      return `${protocol}//${hostname}:${frontendPort + 500}`;
    }
    // No explicit port (behind a proxy on 80/443): assume the same origin.
    return `${protocol}//${hostname}`;
  }
  return "http://localhost:8000";
}

/**
 * POST JSON and return the parsed body.
 *
 * The agent returns `{error, code, request_id}` on failure, never a traceback,
 * so surface that message directly — it is already written for the learner.
 */
async function post<T>(path: string, body: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${resolveAgentUrl()}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch {
    // A network-level failure, not an HTTP error: the agent is unreachable.
    throw new Error("Couldn't reach the tutor. Check your connection and try again.");
  }

  if (!response.ok) {
    const detail = await response
      .json()
      .then((parsed: ApiErrorBody) => parsed.error)
      .catch(() => null);
    throw new Error(detail || `Request failed (${response.status})`);
  }

  return (await response.json()) as T;
}

export function createDeckFromText(
  userId: string,
  title: string,
  text: string,
): Promise<CreateDeckResponse> {
  return post<CreateDeckResponse>("/decks", { user_id: userId, title, text });
}

export function createDeckFromFile(
  userId: string,
  title: string,
  fileB64: string,
  contentType: string,
): Promise<CreateDeckResponse> {
  return post<CreateDeckResponse>("/decks", {
    user_id: userId,
    title,
    file_b64: fileB64,
    content_type: contentType,
  });
}

export function startSession(userId: string): Promise<SessionStartResponse> {
  return post<SessionStartResponse>("/session/start", { user_id: userId });
}

export function submitAnswer(
  userId: string,
  card: { card_id: string; deck_id: string; front: string; back: string },
  studentAnswer: string,
): Promise<GradeResponse> {
  return post<GradeResponse>("/session/answer", {
    user_id: userId,
    deck_id: card.deck_id,
    card_id: card.card_id,
    card_front: card.front,
    card_back: card.back,
    student_answer: studentAnswer,
  });
}

export function transcribe(audioB64: string): Promise<TranscribeResponse> {
  return post<TranscribeResponse>("/transcribe", { audio_b64: audioB64 });
}
