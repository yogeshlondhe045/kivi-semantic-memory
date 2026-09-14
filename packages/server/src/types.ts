export type MemoryType = 'entity' | 'fact' | 'preference' | 'commitment';
export type MemoryStatus = 'active' | 'pending_confirmation' | 'superseded' | 'retired';

export interface Dictation {
  id: string;
  user_id: string;
  batch_id: string | null;
  external_id: string | null;
  captured_at: number;
  app: string;
  surface: string;
  device: string | null;
  duration_ms: number | null;
  language: string;
  asr_confidence: number | null;
  raw_asr: string;
  formatted: string;
  style: string | null;
  word_count: number;
  content_hash: string;
  ingested_at: number;
  processed_at: number | null;
}

export interface Memory {
  id: string;
  user_id: string;
  type: MemoryType;
  dedupe_key: string;
  subject: string;
  statement: string;
  detail: string;
  confidence: number;
  status: MemoryStatus;
  origin: 'observed' | 'stated' | 'user_edit';
  superseded_by: string | null;
  evidence_count: number;
  first_seen_at: number;
  last_seen_at: number;
  updated_at: number;
  expires_at: number | null;
}

/** What an extractor proposes. Nothing reaches `memories` without passing policy. */
export interface MemoryCandidate {
  type: MemoryType;
  subject: string;
  statement: string;
  detail?: Record<string, unknown>;
  confidence: number;
  quote: string;
  /** `stated` = the user said it about themselves; `observed` = inferred from usage. */
  origin?: 'observed' | 'stated';
}

export interface EpisodeCandidate {
  summary: string;
  topics: string[];
  entities: string[];
  salience: number;
}

export interface ExtractionResult {
  episode: EpisodeCandidate;
  candidates: MemoryCandidate[];
  /** Extractor-level notes, before the policy gate sees the candidates. */
  notes?: string[];
}

export type RejectionReason =
  | 'sensitive_category'
  | 'low_confidence'
  | 'transient'
  | 'trivial'
  | 'third_party'
  | 'tombstoned'
  | 'duplicate'
  | 'malformed';

export interface Rejection {
  candidate_type: string;
  candidate: string;
  reason_code: RejectionReason;
  reason: string;
}

export interface QueryPlan {
  intent: 'recall' | 'find_dictation' | 'rewrite' | 'commitments' | 'memory_admin' | 'smalltalk';
  keywords: string[];
  entities: string[];
  apps: string[];
  time_range: { from: number | null; to: number | null; label: string | null; centre?: number | null };
  memory_types: MemoryType[];
  wants_sources: boolean;
  raw: string;
}

export interface RetrievedItem {
  kind: 'memory' | 'episode' | 'dictation';
  id: string;
  text: string;
  score: number;
  signals: Record<string, number>;
  meta: Record<string, unknown>;
}

export interface Citation {
  kind: 'memory' | 'dictation';
  id: string;
  label: string;
  quote?: string;
  captured_at?: number;
  app?: string;
}

export interface TraceStage {
  name: string;
  ms: number;
  detail?: Record<string, unknown>;
}

export interface Usage {
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  calls: number;
}
