export interface Card {
  card_id: string;
  deck_id: string;
  front: string;
  back: string;
  topic: string;
  ease_factor?: number;
  interval_days?: number;
  repetitions?: number;
  due_date?: string;
}

export interface LearnerProfile {
  user_id?: string;
  weak_topics?: Record<string, number>;
  preferences?: Record<string, string>;
  stats?: {
    total_reviews?: number;
    accuracy?: number;
    streak_days?: number;
  };
  notes?: string;
}

export interface CreateDeckResponse {
  deck_id: string;
  card_count: number;
  source_s3_key: string | null;
  /** Present when the material produced no usable cards. */
  warning?: string;
}

export interface SessionStartResponse {
  cards: Card[];
  profile: LearnerProfile;
  /** Present when nothing is due — a friendly state, not an error. */
  message?: string;
}

export interface GradeResponse {
  is_correct: boolean;
  explanation: string;
  quality: number;
  interval_days: number | null;
  due_date: string | null;
}

export interface TranscribeResponse {
  text: string;
  /** Present when transcription produced nothing; the user should type instead. */
  message?: string;
}

/** What `GET /health` reports. Only the demo fields are used by the UI. */
export interface HealthResponse {
  status: string;
  /** True when study-mcp exposes the clock tools, so the demo control is usable. */
  demo_mode?: boolean;
  /** The date study-mcp is currently pretending it is. Null outside demo mode. */
  simulated_date?: string | null;
}

/** Returned by the demo clock endpoints. */
export interface ClockState {
  demo_mode: boolean;
  offset_days: number;
  simulated_date: string;
  real_date: string;
  /** Present when the server refused — e.g. demo mode off, or days out of range. */
  error?: string;
}

/** The structured error shape every tutor-agent endpoint returns on failure. */
export interface ApiErrorBody {
  error: string;
  code: string;
  request_id: string;
}
