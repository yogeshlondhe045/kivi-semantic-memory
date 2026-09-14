import { all, count, one, run } from '../db/index.ts';
import { embeddings, toBlob } from '../retrieval/embedding.ts';
import type { Memory, MemoryCandidate, MemoryType, Rejection } from '../types.ts';
import { id } from '../util/ids.ts';
import { dedupeKey, normalise } from '../util/text.ts';
import { parseDueDate } from '../util/time.ts';
import { CONFIRMATION_THRESHOLD } from './policy.ts';

/** How many questions Kivi is willing to have outstanding at once. */
export const MAX_OPEN_REVIEWS = 4;

export type WriteAction = 'created' | 'reinforced' | 'superseded' | 'held_for_confirmation' | 'unchanged';

export interface WriteOutcome {
  action: WriteAction;
  memory: Memory;
  previous?: Memory;
  /** Plain-language reason, surfaced in the trace and in the app. */
  because: string;
}

/**
 * The identity of a memory. Two candidates sharing a key are talking about the
 * same thing, so the second one either reinforces the first or replaces it.
 * Commitments are keyed on the whole statement because two promises about the
 * same document are two promises, not one changing its mind.
 */
export function keyFor(candidate: MemoryCandidate): string {
  if (candidate.type === 'commitment') return dedupeKey([candidate.statement]);
  const attribute = (candidate.detail?.attribute as string | undefined) ?? '';
  return dedupeKey([candidate.type, candidate.subject, attribute]);
}

export function findByKey(userId: string, type: MemoryType, key: string): Memory | undefined {
  return one<Memory>(
    `SELECT * FROM memories
      WHERE user_id = ? AND type = ? AND dedupe_key = ? AND status IN ('active','pending_confirmation')
      LIMIT 1`,
    [userId, type, key],
  );
}

export function isTombstoned(userId: string, candidate: MemoryCandidate): boolean {
  const key = keyFor(candidate);
  return (
    count('SELECT COUNT(*) AS n FROM tombstones WHERE user_id = ? AND type = ? AND dedupe_key = ?', [
      userId,
      candidate.type,
      key,
    ]) > 0
  );
}

export interface WriteContext {
  userId: string;
  dictationId: string;
  capturedAt: number;
  needsConfirmation: boolean;
}

export function writeCandidate(candidate: MemoryCandidate, ctx: WriteContext): WriteOutcome {
  const key = keyFor(candidate);
  const existing = findByKey(ctx.userId, candidate.type, key);
  const now = Date.now();

  if (!existing) {
    const memory = insertMemory(candidate, key, ctx, now);
    addEvidence(memory.id, ctx.dictationId, 'created', candidate.quote, candidate.confidence, now);
    bumpEvidence(memory.id);
    return {
      action: ctx.needsConfirmation ? 'held_for_confirmation' : 'created',
      memory,
      because: ctx.needsConfirmation
        ? `New, but only ${Math.round(candidate.confidence * 100)}% sure — held for you to confirm rather than used silently.`
        : 'Nothing like this was known, so it was recorded with the sentence that justified it.',
    };
  }

  // An entity is an identity, not an assertion that can be contradicted. Seeing
  // "Meridian" again — however the recogniser spelled it this time — is always
  // more evidence for the same name, so entities merge and never supersede.
  const sameClaim =
    candidate.type === 'entity' || normalise(existing.statement) === normalise(candidate.statement);

  if (sameClaim) {
    if (candidate.type === 'entity') mergeEntityVariants(existing, candidate);
    // Repetition is the strongest signal a memory is real. Confidence rises
    // towards 1 but never reaches it, and never from a single source.
    const nextConfidence = Math.min(0.97, existing.confidence + (1 - existing.confidence) * 0.35);
    run(
      `UPDATE memories
          SET confidence = ?, last_seen_at = ?, updated_at = ?,
              status = CASE WHEN status = 'pending_confirmation' AND ? >= ? THEN 'active' ELSE status END
        WHERE id = ?`,
      [nextConfidence, ctx.capturedAt, now, nextConfidence, CONFIRMATION_THRESHOLD, existing.id],
    );
    addEvidence(existing.id, ctx.dictationId, 'reinforced', candidate.quote, candidate.confidence, now);
    bumpEvidence(existing.id);
    const memory = one<Memory>('SELECT * FROM memories WHERE id = ?', [existing.id])!;
    return {
      action: 'reinforced',
      memory,
      because: `Said again, so confidence rose from ${existing.confidence.toFixed(2)} to ${nextConfidence.toFixed(2)} across ${memory.evidence_count} mentions.`,
    };
  }

  // Same subject, different claim. The newer statement wins, but the old one is
  // kept and linked — a superseded memory is history, not a mistake to hide.
  if (ctx.capturedAt < existing.last_seen_at) {
    return {
      action: 'unchanged',
      memory: existing,
      because: 'An older dictation said something different. The more recent statement stands.',
    };
  }

  run(`UPDATE memories SET status = 'superseded', updated_at = ? WHERE id = ?`, [now, existing.id]);
  const memory = insertMemory(candidate, key, ctx, now);
  run('UPDATE memories SET superseded_by = ? WHERE id = ?', [memory.id, existing.id]);
  addEvidence(existing.id, ctx.dictationId, 'contradicted', candidate.quote, candidate.confidence, now);
  addEvidence(memory.id, ctx.dictationId, 'superseded', candidate.quote, candidate.confidence, now);
  bumpEvidence(memory.id);

  return {
    action: 'superseded',
    memory,
    previous: existing,
    because: `This contradicts "${existing.statement}". The newer statement is now current; the old one is kept, marked superseded.`,
  };
}

function insertMemory(
  candidate: MemoryCandidate,
  key: string,
  ctx: WriteContext,
  now: number,
): Memory {
  const memoryId = id('mem');
  const detail = { ...(candidate.detail ?? {}) };
  let expiresAt: number | null = null;

  if (candidate.type === 'commitment') {
    const due =
      (detail.due_at as number | null | undefined) ??
      (typeof detail.due_at_text === 'string' ? parseDueDate(detail.due_at_text, ctx.capturedAt) : null) ??
      parseDueDate(candidate.quote, ctx.capturedAt);
    detail.due_at = due ?? null;
    // A promise stops being live a week after its deadline, or a month after it
    // was made when no deadline was spoken.
    expiresAt = due ? due + 7 * 86_400_000 : ctx.capturedAt + 30 * 86_400_000;
  }

  run(
    `INSERT INTO memories
       (id, user_id, type, dedupe_key, subject, statement, detail, confidence, status, origin,
        evidence_count, first_seen_at, last_seen_at, updated_at, expires_at, embedding)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)`,
    [
      memoryId,
      ctx.userId,
      candidate.type,
      key,
      candidate.subject,
      candidate.statement,
      JSON.stringify(detail),
      candidate.confidence,
      ctx.needsConfirmation ? 'pending_confirmation' : 'active',
      candidate.origin ?? 'observed',
      ctx.capturedAt,
      ctx.capturedAt,
      now,
      expiresAt,
      toBlob(embeddings.embed(`${candidate.subject} ${candidate.statement}`)),
    ],
  );

  return one<Memory>('SELECT * FROM memories WHERE id = ?', [memoryId])!;
}

export function addEvidence(
  memoryId: string,
  dictationId: string,
  kind: 'created' | 'reinforced' | 'contradicted' | 'superseded' | 'user_edit',
  quote: string,
  confidence: number,
  now = Date.now(),
): void {
  run(
    `INSERT INTO memory_evidence (id, memory_id, dictation_id, kind, quote, confidence, created_at)
     VALUES (?, ?, ?, ?, ?, ?, ?)`,
    [id('ev'), memoryId, dictationId, kind, quote.slice(0, 600), confidence, now],
  );
}

/** Folds a newly observed ASR spelling into an entity Kivi already knows. */
function mergeEntityVariants(existing: Memory, candidate: MemoryCandidate): void {
  const incoming = (candidate.detail?.variants as string[] | undefined) ?? [];
  if (incoming.length === 0) return;
  const detail = JSON.parse(existing.detail || '{}') as { variants?: string[]; canonical?: string };
  const merged = [...new Set([...(detail.variants ?? []), ...incoming])].filter(
    (v) => v && v.toLowerCase() !== (detail.canonical ?? existing.subject).toLowerCase(),
  );
  if (merged.length === (detail.variants ?? []).length) return;
  run('UPDATE memories SET detail = ? WHERE id = ?', [
    JSON.stringify({ ...detail, variants: merged }),
    existing.id,
  ]);
}

function bumpEvidence(memoryId: string): void {
  run(
    `UPDATE memories
        SET evidence_count = (SELECT COUNT(*) FROM memory_evidence WHERE memory_id = ?)
      WHERE id = ?`,
    [memoryId, memoryId],
  );
}

export function recordRejection(
  userId: string,
  dictationId: string | null,
  rejection: Rejection,
): void {
  run(
    `INSERT INTO rejections (id, user_id, dictation_id, candidate_type, candidate, reason_code, reason, created_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
    [
      id('rej'),
      userId,
      dictationId,
      rejection.candidate_type,
      rejection.candidate.slice(0, 400),
      rejection.reason_code,
      rejection.reason,
      Date.now(),
    ],
  );
}

// --------------------------------------------------------------------------
// Lexicon — the one channel from semantic memory into ordinary dictation
// --------------------------------------------------------------------------

export function upsertLexicon(userId: string, canonical: string, variants: string[], memoryId: string | null): void {
  const existing = one<{ id: string; variants: string }>(
    'SELECT id, variants FROM lexicon WHERE user_id = ? AND canonical = ?',
    [userId, canonical],
  );
  const now = Date.now();

  if (!existing) {
    run(
      `INSERT INTO lexicon (id, user_id, canonical, variants, memory_id, uses, enabled, updated_at)
       VALUES (?, ?, ?, ?, ?, 1, 1, ?)`,
      [id('lex'), userId, canonical, JSON.stringify(unique(variants)), memoryId, now],
    );
    return;
  }

  const merged = unique([...(JSON.parse(existing.variants) as string[]), ...variants]);
  run('UPDATE lexicon SET variants = ?, uses = uses + 1, updated_at = ? WHERE id = ?', [
    JSON.stringify(merged),
    now,
    existing.id,
  ]);
}

export interface LexiconEntry {
  id: string;
  canonical: string;
  variants: string[];
  uses: number;
  enabled: number;
}

export function lexiconFor(userId: string): LexiconEntry[] {
  return all<{ id: string; canonical: string; variants: string; uses: number; enabled: number }>(
    'SELECT id, canonical, variants, uses, enabled FROM lexicon WHERE user_id = ? ORDER BY uses DESC, canonical',
    [userId],
  ).map((row) => ({ ...row, variants: JSON.parse(row.variants) as string[] }));
}

function unique(items: string[]): string[] {
  return [...new Set(items.map((v) => v.trim()).filter(Boolean))];
}

// --------------------------------------------------------------------------
// Review queue and forgetting
// --------------------------------------------------------------------------

export function openReviewItem(
  userId: string,
  memoryId: string,
  kind: string,
  question: string,
  options: { id: string; label: string; effect: string }[],
  context: Record<string, unknown> = {},
): void {
  const duplicate = count(
    `SELECT COUNT(*) AS n FROM review_items WHERE user_id = ? AND memory_id = ? AND status = 'open'`,
    [userId, memoryId],
  );
  if (duplicate > 0) return;

  // The person is a user, not an administrator of a memory system. Past a
  // handful of open questions, Kivi stops asking and simply leaves the
  // remaining low-confidence memories unused.
  const open = count(`SELECT COUNT(*) AS n FROM review_items WHERE user_id = ? AND status = 'open'`, [userId]);
  if (open >= MAX_OPEN_REVIEWS) return;

  run(
    `INSERT INTO review_items (id, user_id, memory_id, kind, question, options, context, status, created_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?)`,
    [id('rev'), userId, memoryId, kind, question, JSON.stringify(options), JSON.stringify(context), Date.now()],
  );
}

/**
 * Forgetting is permanent and leaves a tombstone, so re-ingesting the same
 * dictation does not quietly bring the memory back. Evidence rows go with it.
 */
export function forgetMemory(userId: string, memoryId: string, reason = 'user_forget'): boolean {
  const memory = one<Memory>('SELECT * FROM memories WHERE id = ? AND user_id = ?', [memoryId, userId]);
  if (!memory) return false;

  // The recordings that produced this belief are suppressed as grounds for an
  // answer. They stay in Dictations and stay findable — the person's words are
  // theirs — but Kivi stops reasoning from them.
  const sources = all<{ dictation_id: string }>(
    'SELECT DISTINCT dictation_id FROM memory_evidence WHERE memory_id = ?',
    [memoryId],
  ).map((row) => row.dictation_id);

  const tombstoneId = id('tomb');
  run(
    `INSERT OR REPLACE INTO tombstones
       (id, user_id, type, dedupe_key, statement, reason, created_at, suppressed_dictations)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?)`,
    [tombstoneId, userId, memory.type, memory.dedupe_key, memory.statement, reason, Date.now(), JSON.stringify(sources)],
  );
  for (const dictationId of sources) {
    run(
      `INSERT OR IGNORE INTO suppressed_evidence (user_id, dictation_id, tombstone_id, created_at)
       VALUES (?, ?, ?, ?)`,
      [userId, dictationId, tombstoneId, Date.now()],
    );
  }
  run('DELETE FROM lexicon WHERE memory_id = ?', [memoryId]);
  run(`UPDATE review_items SET status = 'dismissed' WHERE memory_id = ?`, [memoryId]);
  run('DELETE FROM memories WHERE id = ?', [memoryId]);
  return true;
}

export function activeMemories(userId: string, types?: MemoryType[]): Memory[] {
  const typeClause = types?.length ? ` AND type IN (${types.map(() => '?').join(',')})` : '';
  return all<Memory>(
    `SELECT * FROM memories WHERE user_id = ? AND status = 'active'${typeClause} ORDER BY last_seen_at DESC`,
    [userId, ...(types ?? [])],
  );
}

export function memoryStats(userId: string) {
  const byType = all<{ type: string; status: string; n: number }>(
    'SELECT type, status, COUNT(*) AS n FROM memories WHERE user_id = ? GROUP BY type, status',
    [userId],
  );
  const rejections = all<{ reason_code: string; n: number }>(
    'SELECT reason_code, COUNT(*) AS n FROM rejections WHERE user_id = ? GROUP BY reason_code ORDER BY n DESC',
    [userId],
  );
  return {
    byType,
    rejections,
    dictations: count('SELECT COUNT(*) AS n FROM dictations WHERE user_id = ?', [userId]),
    episodes: count('SELECT COUNT(*) AS n FROM episodes WHERE user_id = ?', [userId]),
    lexicon: count('SELECT COUNT(*) AS n FROM lexicon WHERE user_id = ?', [userId]),
    openReviews: count(`SELECT COUNT(*) AS n FROM review_items WHERE user_id = ? AND status = 'open'`, [userId]),
  };
}
