import { all } from '../db/index.ts';
import type { QueryPlan, RetrievedItem } from '../types.ts';
import { ftsEscape } from '../util/text.ts';
import { DAY } from '../util/time.ts';
import { COVERAGE_FLOOR, queryCoverage } from './coverage.ts';
import { cosine, embeddings, fromBlob } from './embedding.ts';

/**
 * Hybrid retrieval.
 *
 * Four signals, fused with reciprocal rank fusion rather than a hand-tuned
 * weighted sum — RRF is robust when the signals disagree, which they do
 * constantly on noisy transcripts:
 *
 *   lexical   FTS5 BM25 over the formatted text and the raw ASR
 *   semantic  cosine over local embeddings (paraphrase, misspelling)
 *   temporal  distance from the window the planner extracted
 *   salience  how much the memory has been reinforced / how notable the episode
 *
 * Every score that contributed is returned on the item and lands in the trace,
 * so a retrieval result can be argued with rather than trusted.
 */

const RRF_K = 60;

export interface SearchOptions {
  limit?: number;
  /** Hard-filter by the planner's time window instead of merely preferring it. */
  strictTime?: boolean;
  now?: number;
}

interface Scored {
  id: string;
  lexical: number | null;
  semantic: number;
  temporal: number;
  prior: number;
}

function rrf(ranks: (number | null)[]): number {
  let total = 0;
  for (const rank of ranks) {
    if (rank === null) continue;
    total += 1 / (RRF_K + rank);
  }
  return total;
}

function rankMap(items: Scored[], key: keyof Scored): Map<string, number> {
  const ordered = items
    .filter((i) => i[key] !== null)
    .sort((a, b) => (b[key] as number) - (a[key] as number));
  const map = new Map<string, number>();
  ordered.forEach((item, index) => map.set(item.id, index + 1));
  return map;
}

function temporalScore(occurredAt: number, plan: QueryPlan, now: number): number {
  const { from, to, centre } = plan.time_range;
  if (from !== null && to !== null) {
    if (occurredAt >= from && occurredAt <= to) {
      // Inside the window, closeness to the named moment still matters: "around
      // 5PM" should rank 17:04 above 16:44, not treat the whole window as flat.
      if (!centre) return 1;
      const half = Math.max(1, (to - from) / 2);
      return 1 - 0.4 * Math.min(1, Math.abs(occurredAt - centre) / half);
    }
    const distance = occurredAt < from ? from - occurredAt : occurredAt - to;
    // Soft edges: "around 5PM" should still surface 4:20PM, just lower.
    return Math.max(0, 1 - distance / (2 * DAY));
  }
  const age = Math.max(0, now - occurredAt);
  return Math.exp(-age / (45 * DAY));
}

// --------------------------------------------------------------------------
// Dictations
// --------------------------------------------------------------------------

export function searchDictations(
  userId: string,
  plan: QueryPlan,
  options: SearchOptions = {},
): RetrievedItem[] {
  const limit = options.limit ?? 8;
  const now = options.now ?? Date.now();
  const match = ftsEscape(plan.keywords.join(' ') || plan.raw);

  const filters: string[] = ['d.user_id = ?'];
  const params: unknown[] = [userId];

  if (plan.apps.length) {
    filters.push(`d.app IN (${plan.apps.map(() => '?').join(',')})`);
    params.push(...plan.apps);
  }
  if (options.strictTime && plan.time_range.from !== null && plan.time_range.to !== null) {
    filters.push('d.captured_at BETWEEN ? AND ?');
    params.push(plan.time_range.from, plan.time_range.to);
  }

  const lexical = new Map<string, number>();
  if (match) {
    const rows = all<{ id: string; score: number }>(
      `SELECT d.id AS id, -bm25(dictations_fts, 4.0, 1.0) AS score
         FROM dictations_fts
         JOIN dictations d ON d.rowid = dictations_fts.rowid
        WHERE dictations_fts MATCH ? AND ${filters.join(' AND ')}
        ORDER BY score DESC
        LIMIT 120`,
      [match, ...params],
    );
    for (const row of rows) lexical.set(row.id, row.score);
  }

  const pool = all<{
    id: string;
    captured_at: number;
    app: string;
    formatted: string;
    raw_asr: string;
    surface: string;
    embedding: Uint8Array | null;
  }>(
    `SELECT d.id, d.captured_at, d.app, d.formatted, d.raw_asr, d.surface, e.embedding
       FROM dictations d
       LEFT JOIN episodes e ON e.dictation_id = d.id
      WHERE ${filters.join(' AND ')}
      ORDER BY d.captured_at DESC
      LIMIT 600`,
    params,
  );

  const queryVector = embeddings.embed(plan.raw);
  const scored: Scored[] = pool.map((row) => {
    const vector = fromBlob(row.embedding);
    return {
      id: row.id,
      lexical: lexical.get(row.id) ?? null,
      semantic: vector ? cosine(queryVector, vector) : 0,
      temporal: temporalScore(row.captured_at, plan, now),
      prior: row.surface === 'dictation' ? 0.5 : 0.2,
    };
  });

  // When someone names an hour they are pointing at one recording, not
  // describing a topic. Counting the temporal rank twice lets "around 5PM
  // yesterday in Slack" beat a keyword coincidence elsewhere in the day.
  const timeIsExplicit = plan.time_range.from !== null;

  return fuse(scored, pool, limit, (row) => ({
    kind: 'dictation' as const,
    id: row.id,
    text: row.formatted,
    meta: { captured_at: row.captured_at, app: row.app, raw_asr: row.raw_asr },
  }), timeIsExplicit ? 2 : 1);
}

// --------------------------------------------------------------------------
// Memories
// --------------------------------------------------------------------------

export function searchMemories(userId: string, plan: QueryPlan, options: SearchOptions = {}): RetrievedItem[] {
  const limit = options.limit ?? 8;
  const now = options.now ?? Date.now();
  const match = ftsEscape(plan.keywords.join(' ') || plan.raw);

  // A question about spelling really is only about entities. Every other hint
  // is a guess, and a guess that filters rather than ranks is how "what do we
  // use for deploys?" ends up unanswerable because the answer happened to be
  // stored as a preference rather than a fact.
  const hardFilter = plan.memory_types.length === 1 && plan.memory_types[0] === 'entity';
  const preferred = new Set(plan.memory_types);

  const filters = [`m.user_id = ?`, `m.status = 'active'`];
  const params: unknown[] = [userId];
  if (hardFilter) {
    filters.push(`m.type = ?`);
    params.push('entity');
  }

  const lexical = new Map<string, number>();
  if (match) {
    const rows = all<{ id: string; score: number }>(
      `SELECT m.id AS id, -bm25(memories_fts, 2.0, 3.0) AS score
         FROM memories_fts
         JOIN memories m ON m.rowid = memories_fts.rowid
        WHERE memories_fts MATCH ? AND ${filters.join(' AND ')}
        ORDER BY score DESC
        LIMIT 120`,
      [match, ...params],
    );
    for (const row of rows) lexical.set(row.id, row.score);
  }

  const pool = all<{
    id: string;
    type: string;
    subject: string;
    statement: string;
    detail: string;
    confidence: number;
    evidence_count: number;
    last_seen_at: number;
    first_seen_at: number;
    embedding: Uint8Array | null;
  }>(
    `SELECT m.id, m.type, m.subject, m.statement, m.detail, m.confidence, m.evidence_count,
            m.last_seen_at, m.first_seen_at, m.embedding
       FROM memories m
      WHERE ${filters.join(' AND ')}
      ORDER BY m.last_seen_at DESC
      LIMIT 800`,
    params,
  );

  const queryVector = embeddings.embed(plan.raw);
  const scored: Scored[] = pool.map((row) => ({
    id: row.id,
    lexical: lexical.get(row.id) ?? null,
    semantic: fromBlob(row.embedding) ? cosine(queryVector, fromBlob(row.embedding)!) : 0,
    // A memory is not an event: recency matters less than how often it held true.
    temporal: 0.4 + 0.6 * temporalScore(row.last_seen_at, plan, now),
    prior:
      (row.confidence * 0.7 + Math.min(1, row.evidence_count / 4) * 0.3) *
      (preferred.size === 0 || preferred.has(row.type as never) ? 1 : 0.75),
  }));

  return fuse(scored, pool, limit, (row) => ({
    kind: 'memory' as const,
    id: row.id,
    text: row.statement,
    meta: {
      type: row.type,
      subject: row.subject,
      confidence: row.confidence,
      evidence_count: row.evidence_count,
      detail: safeJson(row.detail),
      last_seen_at: row.last_seen_at,
    },
  }));
}

// --------------------------------------------------------------------------
// Episodes
// --------------------------------------------------------------------------

export function searchEpisodes(userId: string, plan: QueryPlan, options: SearchOptions = {}): RetrievedItem[] {
  const limit = options.limit ?? 8;
  const now = options.now ?? Date.now();
  const match = ftsEscape(plan.keywords.join(' ') || plan.raw);

  // Episodes for dictations the policy withheld carry a placeholder summary.
  // They must never surface as an answer — the person's own recording stays
  // searchable in Dictations, but Kivi does not reason from it.
  const filters = [
    'e.user_id = ?',
    `e.topics NOT LIKE '%withheld%'`,
    // Recordings behind a forgotten memory are not grounds for an answer.
    'e.dictation_id NOT IN (SELECT dictation_id FROM suppressed_evidence WHERE user_id = e.user_id)',
  ];
  const params: unknown[] = [userId];
  if (plan.apps.length) {
    filters.push(`e.app IN (${plan.apps.map(() => '?').join(',')})`);
    params.push(...plan.apps);
  }

  const lexical = new Map<string, number>();
  if (match) {
    const rows = all<{ id: string; score: number }>(
      `SELECT e.id AS id, -bm25(episodes_fts, 3.0, 1.0) AS score
         FROM episodes_fts
         JOIN episodes e ON e.rowid = episodes_fts.rowid
        WHERE episodes_fts MATCH ? AND ${filters.join(' AND ')}
        ORDER BY score DESC
        LIMIT 120`,
      [match, ...params],
    );
    for (const row of rows) lexical.set(row.id, row.score);
  }

  const pool = all<{
    id: string;
    dictation_id: string;
    occurred_at: number;
    app: string;
    summary: string;
    topics: string;
    entities: string;
    salience: number;
    embedding: Uint8Array | null;
  }>(
    `SELECT e.id, e.dictation_id, e.occurred_at, e.app, e.summary, e.topics, e.entities, e.salience, e.embedding
       FROM episodes e
      WHERE ${filters.join(' AND ')}
      ORDER BY e.occurred_at DESC
      LIMIT 600`,
    params,
  );

  const queryVector = embeddings.embed(plan.raw);
  const scored: Scored[] = pool.map((row) => ({
    id: row.id,
    lexical: lexical.get(row.id) ?? null,
    semantic: fromBlob(row.embedding) ? cosine(queryVector, fromBlob(row.embedding)!) : 0,
    temporal: temporalScore(row.occurred_at, plan, now),
    prior: row.salience,
  }));

  return fuse(scored, pool, limit, (row) => ({
    kind: 'episode' as const,
    id: row.id,
    text: row.summary,
    meta: {
      dictation_id: row.dictation_id,
      occurred_at: row.occurred_at,
      app: row.app,
      topics: safeJson(row.topics),
      entities: safeJson(row.entities),
    },
  }));
}

// --------------------------------------------------------------------------

function fuse<T extends { id: string }>(
  scored: Scored[],
  pool: T[],
  limit: number,
  project: (row: T) => Omit<RetrievedItem, 'score' | 'signals'>,
  temporalWeight = 1,
): RetrievedItem[] {
  const lexicalRanks = rankMap(scored, 'lexical');
  const semanticRanks = rankMap(scored, 'semantic');
  const temporalRanks = rankMap(scored, 'temporal');
  const priorRanks = rankMap(scored, 'prior');
  const byId = new Map(pool.map((row) => [row.id, row]));

  return scored
    .map((s) => {
      const temporalRank = temporalRanks.get(s.id) ?? null;
      const score = rrf([
        lexicalRanks.get(s.id) ?? null,
        semanticRanks.get(s.id) ?? null,
        ...Array.from({ length: temporalWeight }, () => temporalRank),
        priorRanks.get(s.id) ?? null,
      ]);
      const row = byId.get(s.id)!;
      return {
        ...project(row),
        score,
        signals: {
          lexical: s.lexical ?? 0,
          semantic: Number(s.semantic.toFixed(4)),
          temporal: Number(s.temporal.toFixed(4)),
          prior: Number(s.prior.toFixed(4)),
          fused: Number(score.toFixed(6)),
        },
      } as RetrievedItem;
    })
    .sort((a, b) => b.score - a.score)
    .slice(0, limit);
}

function safeJson(input: string): unknown {
  try {
    return JSON.parse(input);
  } catch {
    return input;
  }
}

/**
 * The abstention test.
 *
 * Retrieval always returns its best guess; that is not the same as having an
 * answer. Three conditions must all hold before Hey Kivi is allowed to speak:
 *
 *   1. something ranked at all;
 *   2. the top result has real lexical or semantic support, not just recency;
 *   3. the retrieved items actually contain the rare words of the question.
 *
 * The third is the one that matters. Without it, a question about a topic the
 * person has never dictated returns the five most generally-popular memories
 * with total confidence. With it, the answer is "I don't have that", which is
 * the only answer that keeps the rest of the system worth trusting.
 */
export const GROUNDING_FLOOR = 0.0225;

export interface GroundingVerdict {
  grounded: boolean;
  reason: string;
  top_score: number;
  coverage: number;
  missing_terms: string[];
  unknown_terms: string[];
}

export function assessGrounding(
  userId: string,
  plan: QueryPlan,
  items: RetrievedItem[],
): GroundingVerdict {
  const base = { top_score: items[0]?.score ?? 0, coverage: 0, missing_terms: [] as string[], unknown_terms: [] as string[] };

  if (items.length === 0) {
    return { ...base, grounded: false, reason: 'Nothing matched the query at all.' };
  }

  const top = items[0]!;
  if (top.score < GROUNDING_FLOOR) {
    return { ...base, grounded: false, reason: `Best fused score ${top.score.toFixed(4)} is below the floor ${GROUNDING_FLOOR}.` };
  }
  if (top.signals.lexical <= 0 && top.signals.semantic < 0.08) {
    return {
      ...base,
      grounded: false,
      reason: 'The best result was carried by recency alone, with no word or meaning overlap.',
    };
  }

  const coverage = queryCoverage(userId, plan, items);
  const verdict = {
    ...base,
    coverage: Number(coverage.score.toFixed(3)),
    missing_terms: coverage.missing,
    unknown_terms: coverage.unknown,
  };

  if (coverage.unknown.length > 0 && coverage.score < 0.75) {
    return {
      ...verdict,
      grounded: false,
      reason: `The history has never contained ${coverage.unknown.map((t) => `"${t}"`).join(', ')}, so nothing here can be about it.`,
    };
  }
  if (coverage.score < COVERAGE_FLOOR) {
    return {
      ...verdict,
      grounded: false,
      reason: `Retrieved items cover only ${(coverage.score * 100).toFixed(0)}% of the question's weighted terms (floor ${COVERAGE_FLOOR * 100}%). Missing: ${coverage.missing.join(', ')}.`,
    };
  }

  return { ...verdict, grounded: true, reason: `Top result is supported and covers ${(coverage.score * 100).toFixed(0)}% of the question's terms.` };
}
