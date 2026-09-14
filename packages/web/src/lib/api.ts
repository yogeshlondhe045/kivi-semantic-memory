const BASE = '';

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { 'content-type': 'application/json', ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    const body = await response.text();
    throw new Error(`${response.status} ${path}: ${body.slice(0, 200)}`);
  }
  return (await response.json()) as T;
}

export const api = {
  health: () => request<Health>('/api/health'),
  ask: (question: string, conversationId?: string) =>
    request<AskResult>('/api/hey-kivi', {
      method: 'POST',
      body: JSON.stringify({ question, conversation_id: conversationId }),
    }),
  dictations: (params: Record<string, string | number | undefined> = {}) =>
    request<{ total: number; items: DictationRow[] }>(`/api/dictations?${query(params)}`),
  dictation: (id: string) => request<DictationDetail>(`/api/dictations/${id}`),
  speak: (body: { raw_asr: string; app: string }) =>
    request<SpeakResult>('/api/dictations', { method: 'POST', body: JSON.stringify(body) }),
  memories: (params: Record<string, string | undefined> = {}) =>
    request<{ items: MemoryRow[]; stats: MemoryStats }>(`/api/memories?${query(params)}`),
  memory: (id: string) => request<MemoryDetail>(`/api/memories/${id}`),
  forget: (id: string) => request<{ ok: boolean }>(`/api/memories/${id}`, { method: 'DELETE' }),
  ignored: () => request<{ items: IgnoredRow[]; by_reason: { reason_code: string; n: number }[] }>('/api/ignored'),
  review: () => request<{ items: ReviewItem[] }>('/api/review'),
  resolveReview: (id: string, choice: string) =>
    request<{ ok: boolean }>(`/api/review/${id}`, { method: 'POST', body: JSON.stringify({ choice }) }),
  lexicon: () => request<{ items: LexiconRow[] }>('/api/lexicon'),
  trace: (id: string) => request<Trace>(`/api/traces/${id}`),
  traces: (kind: 'query' | 'ingest' = 'query') => request<Trace[]>(`/api/traces?kind=${kind}`),
  stats: () => request<Stats>('/api/stats'),
};

function query(params: Record<string, string | number | undefined>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== '') search.set(key, String(value));
  }
  return search.toString();
}

// ---------------------------------------------------------------- types --

export interface Health {
  ok: boolean;
  provider: string;
  user: { id: string; display_name: string; timezone: string } | null;
  counts: { dictations: number; memories: number; open_reviews: number; ignored: number };
}

export interface Citation {
  kind: 'memory' | 'dictation';
  id: string;
  label: string;
  quote?: string;
  captured_at?: number;
  app?: string;
}

export interface AskResult {
  conversation_id: string;
  turn_id: string;
  text: string;
  outcome: 'answered' | 'abstained' | 'acted' | 'clarified';
  citations: Citation[];
  trace_id: string;
  duration_ms: number;
  provider: string;
  tools_used: string[];
}

export interface DictationRow {
  id: string;
  captured_at: number;
  app: string;
  device: string | null;
  language: string;
  asr_confidence: number | null;
  raw_asr: string;
  formatted: string;
  word_count: number;
  summary: string | null;
  salience: number | null;
  memories_touched: number;
  ignored: number;
}

export interface DictationDetail {
  dictation: DictationRow & { style: string | null; duration_ms: number | null };
  episode: { summary: string; topics: string; entities: string; salience: number } | null;
  memories: { id: string; type: string; statement: string; confidence: number; status: string; kind: string; quote: string }[];
  ignored: IgnoredRow[];
  trace: Trace | null;
}

export interface SpeakResult {
  dictation_id: string;
  status: string;
  created: number;
  reinforced: number;
  superseded: number;
  held: number;
  rejected: number;
  duration_ms: number;
  trace: Trace | null;
  memories: { id: string; type: string; statement: string; confidence: number; status: string; kind: string }[];
  ignored: IgnoredRow[];
}

export interface MemoryRow {
  id: string;
  type: 'entity' | 'fact' | 'preference' | 'commitment';
  subject: string;
  statement: string;
  detail: Record<string, unknown>;
  confidence: number;
  status: string;
  origin: string;
  evidence: number;
  first_seen_at: number;
  last_seen_at: number;
  expires_at: number | null;
}

export interface MemoryDetail {
  memory: MemoryRow;
  evidence: {
    id: string;
    kind: string;
    quote: string;
    confidence: number;
    dictation_id: string;
    captured_at: number;
    app: string;
    formatted: string;
    raw_asr: string;
  }[];
  superseded: { id: string; statement: string; updated_at: number }[];
}

export interface MemoryStats {
  byType: { type: string; status: string; n: number }[];
  rejections: { reason_code: string; n: number }[];
  dictations: number;
  episodes: number;
  lexicon: number;
  openReviews: number;
}

export interface IgnoredRow {
  id: string;
  candidate_type: string;
  candidate: string;
  reason_code: string;
  reason: string;
  created_at: number;
  formatted?: string;
  captured_at?: number;
  app?: string;
}

export interface ReviewItem {
  id: string;
  memory_id: string | null;
  kind: string;
  question: string;
  options: { id: string; label: string; effect: string }[];
  context: Record<string, unknown>;
  type: string | null;
  statement: string | null;
  confidence: number | null;
}

export interface LexiconRow {
  id: string;
  canonical: string;
  variants: string[];
  uses: number;
  enabled: number;
}

export interface Trace {
  id: string;
  kind: string;
  subject_id: string | null;
  provider: string;
  model: string;
  started_at: number;
  duration_ms: number;
  stages: { name: string; ms: number; detail?: Record<string, unknown> }[];
  payload: Record<string, unknown>;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  llm_calls: number;
}

export interface Stats {
  provider: string;
  memory: MemoryStats;
  latency: Record<'ingest' | 'query', { count: number; p50: number; p95: number; max: number; mean: number }>;
  usage: { tokens_in: number; tokens_out: number; cost_usd: number; llm_calls: number };
  database: {
    bytes: number;
    per_dictation_bytes: number;
    samples: { taken_at: number; dictation_count: number; memory_count: number; episode_count: number; rejection_count: number; bytes: number }[];
  };
  active_memories: number;
  oldest: string | null;
}
