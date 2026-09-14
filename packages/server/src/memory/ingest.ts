import { all, one, run, transaction } from '../db/index.ts';
import { llm } from '../llm/index.ts';
import { embeddings, toBlob } from '../retrieval/embedding.ts';
import { Tracer } from '../trace/index.ts';
import type { Dictation, MemoryCandidate } from '../types.ts';
import { contentHash, id } from '../util/ids.ts';
import { wordCount } from '../util/text.ts';
import { evaluateCandidate, screenDictation } from './policy.ts';
import {
  isTombstoned,
  openReviewItem,
  recordRejection,
  upsertLexicon,
  writeCandidate,
  type WriteAction,
} from './store.ts';

export interface IncomingDictation {
  external_id?: string | null;
  captured_at: number;
  app: string;
  surface?: string;
  device?: string | null;
  duration_ms?: number | null;
  language?: string;
  asr_confidence?: number | null;
  raw_asr: string;
  formatted: string;
  style?: string | null;
}

export interface IngestOutcome {
  dictation_id: string;
  status: 'processed' | 'duplicate';
  trace_id: string | null;
  created: number;
  reinforced: number;
  superseded: number;
  held: number;
  rejected: number;
  duration_ms: number;
}

/**
 * One dictation in, one decision trail out.
 *
 * The order matters: the dictation is stored first and always. Kivi never
 * refuses to keep the person's own words — the policy decides what it is
 * willing to *believe*, not what the person is allowed to say. A dictation in a
 * screened category stays fully searchable; it simply produces no memories.
 */
export async function ingestDictation(
  userId: string,
  incoming: IncomingDictation,
  options: { batchId?: string | null } = {},
): Promise<IngestOutcome> {
  const tracer = new Tracer('ingest', userId);
  const hash = contentHash([incoming.raw_asr, incoming.formatted, incoming.captured_at]);

  const existing = one<{ id: string }>('SELECT id FROM dictations WHERE user_id = ? AND content_hash = ?', [
    userId,
    hash,
  ]);
  if (existing) {
    return {
      dictation_id: existing.id,
      status: 'duplicate',
      trace_id: null,
      created: 0,
      reinforced: 0,
      superseded: 0,
      held: 0,
      rejected: 0,
      duration_ms: tracer.elapsedMs,
    };
  }

  const dictationId = id('dict');
  const now = Date.now();

  run(
    `INSERT INTO dictations
       (id, user_id, batch_id, external_id, captured_at, app, surface, device, duration_ms, language,
        asr_confidence, raw_asr, formatted, style, word_count, content_hash, ingested_at, processed_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)`,
    [
      dictationId,
      userId,
      options.batchId ?? null,
      incoming.external_id ?? null,
      incoming.captured_at,
      incoming.app,
      incoming.surface ?? 'dictation',
      incoming.device ?? null,
      incoming.duration_ms ?? null,
      incoming.language ?? 'en',
      incoming.asr_confidence ?? null,
      incoming.raw_asr,
      incoming.formatted,
      incoming.style ?? null,
      wordCount(incoming.formatted),
      hash,
      now,
    ],
  );
  tracer.stage('store_dictation', { dictation_id: dictationId, words: wordCount(incoming.formatted) });

  const screened = screenDictation(`${incoming.formatted} ${incoming.raw_asr}`);
  if (screened) {
    recordRejection(userId, dictationId, {
      candidate_type: 'dictation',
      candidate: incoming.formatted.slice(0, 200),
      reason_code: 'sensitive_category',
      reason: `Whole dictation withheld from memory: it touches ${screened.label}. The transcript is kept and stays searchable; Kivi forms no beliefs from it.`,
    });
    insertEpisode(userId, dictationId, incoming, {
      summary: `Withheld from memory — this dictation touches ${screened.label}.`,
      topics: ['withheld'],
      entities: [],
      salience: 0.1,
    });
    run('UPDATE dictations SET processed_at = ? WHERE id = ?', [Date.now(), dictationId]);
    tracer.set('screened', screened);
    tracer.stage('policy_screen', { outcome: 'withheld', category: screened.code });
    const traceId = tracer.save(dictationId);
    return {
      dictation_id: dictationId,
      status: 'processed',
      trace_id: traceId,
      created: 0,
      reinforced: 0,
      superseded: 0,
      held: 0,
      rejected: 1,
      duration_ms: tracer.elapsedMs,
    };
  }

  const knownSubjects = all<{ subject: string }>(
    `SELECT DISTINCT subject FROM memories WHERE user_id = ? AND status = 'active' ORDER BY last_seen_at DESC LIMIT 60`,
    [userId],
  ).map((r) => r.subject);

  const { result, usage } = await tracer.timed(
    'extract',
    () =>
      llm().extract({
        dictation: {
          id: dictationId,
          raw_asr: incoming.raw_asr,
          formatted: incoming.formatted,
          app: incoming.app,
          captured_at: incoming.captured_at,
          language: incoming.language ?? 'en',
        },
        knownSubjects,
      }),
    () => ({ known_subjects: knownSubjects.length }),
  );
  tracer.addUsage(usage);
  tracer.set('candidates_proposed', result.candidates.length);
  if (result.notes?.length) tracer.set('extractor_notes', result.notes);

  const counts: Record<WriteAction | 'rejected', number> = {
    created: 0,
    reinforced: 0,
    superseded: 0,
    held_for_confirmation: 0,
    unchanged: 0,
    rejected: 0,
  };

  transaction(() => {
    for (const candidate of result.candidates) {
      const decision = evaluateCandidate(candidate, {
        tombstoned: (c) => isTombstoned(userId, c),
      });

      if (!decision.allowed) {
        counts.rejected += 1;
        recordRejection(userId, dictationId, decision.rejection!);
        tracer.push('rejected', {
          type: candidate.type,
          statement: candidate.statement,
          reason_code: decision.rejection!.reason_code,
          reason: decision.rejection!.reason,
        });
        continue;
      }

      const outcome = writeCandidate(candidate, {
        userId,
        dictationId,
        capturedAt: incoming.captured_at,
        // Entities are spelling knowledge: low stakes, visible in Memory, and
        // undone by one click. Holding them back until confirmed would make
        // Kivi useless at the one job it does during ordinary dictation.
        needsConfirmation: candidate.type === 'entity' ? false : Boolean(decision.needsConfirmation),
      });
      counts[outcome.action] += 1;

      tracer.push('written', {
        memory_id: outcome.memory.id,
        type: outcome.memory.type,
        statement: outcome.memory.statement,
        action: outcome.action,
        because: outcome.because,
        confidence: outcome.memory.confidence,
      });

      // A spelling Kivi is only half sure of is not worth interrupting anyone
      // for; it stays unconfirmed and unused until it is heard again. Being
      // asked twenty questions is its own kind of untrustworthy.
      if (outcome.action === 'held_for_confirmation' && candidate.type !== 'entity') {
        openReviewItem(
          userId,
          outcome.memory.id,
          'confirm_new',
          confirmationQuestion(candidate),
          [
            { id: 'yes', label: "Yes, that's right", effect: 'activate' },
            { id: 'no', label: 'No, drop it', effect: 'forget' },
          ],
          { quote: candidate.quote, dictation_id: dictationId },
        );
      }

      if (outcome.action === 'superseded' && outcome.previous) {
        openReviewItem(
          userId,
          outcome.memory.id,
          'resolve_conflict',
          `You said "${outcome.memory.statement}". Kivi had "${outcome.previous.statement}". Should the newer one stand?`,
          [
            { id: 'new', label: 'Use the newer one', effect: 'activate' },
            { id: 'old', label: 'Keep the older one', effect: 'restore_previous' },
          ],
          { previous_id: outcome.previous.id, dictation_id: dictationId },
        );
      }

      maybeUpdateLexicon(userId, candidate, outcome.memory.id);
    }

    insertEpisode(userId, dictationId, incoming, result.episode);
    run('UPDATE dictations SET processed_at = ? WHERE id = ?', [Date.now(), dictationId]);
  });

  tracer.stage('write_memories', counts);
  const traceId = tracer.save(dictationId);

  return {
    dictation_id: dictationId,
    status: 'processed',
    trace_id: traceId,
    created: counts.created,
    reinforced: counts.reinforced,
    superseded: counts.superseded,
    held: counts.held_for_confirmation,
    rejected: counts.rejected,
    duration_ms: tracer.elapsedMs,
  };
}

function confirmationQuestion(candidate: MemoryCandidate): string {
  switch (candidate.type) {
    case 'preference':
      return `Should Kivi remember that you ${candidate.statement.replace(/^Prefers /, 'prefer ').replace(/^Does not want /, "don't want ").toLowerCase()}?`;
    case 'entity':
      return `Is "${candidate.subject}" spelled that way?`;
    case 'commitment':
      return `Kivi heard a commitment: ${candidate.statement}. Keep track of it?`;
    default:
      return `Should Kivi remember this? ${candidate.statement}`;
  }
}

function maybeUpdateLexicon(userId: string, candidate: MemoryCandidate, memoryId: string): void {
  if (candidate.type !== 'entity') return;
  const canonical = (candidate.detail?.canonical as string | undefined) ?? candidate.subject;
  const variants = (candidate.detail?.variants as string[] | undefined) ?? [];
  if (!canonical || canonical.length < 3) return;
  upsertLexicon(userId, canonical, variants, memoryId);
}

function insertEpisode(
  userId: string,
  dictationId: string,
  incoming: IncomingDictation,
  episode: { summary: string; topics: string[]; entities: string[]; salience: number },
): void {
  run(
    `INSERT OR REPLACE INTO episodes
       (id, user_id, dictation_id, occurred_at, app, summary, topics, entities, salience, embedding)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    [
      id('ep'),
      userId,
      dictationId,
      incoming.captured_at,
      incoming.app,
      episode.summary,
      JSON.stringify(episode.topics),
      JSON.stringify(episode.entities),
      episode.salience,
      toBlob(embeddings.embed(`${episode.summary} ${episode.topics.join(' ')} ${episode.entities.join(' ')}`)),
    ],
  );
}

export interface BatchResult {
  batch_id: string;
  processed: number;
  duplicates: number;
  created: number;
  reinforced: number;
  superseded: number;
  held: number;
  rejected: number;
  duration_ms: number;
  bytes_before: number;
  bytes_after: number;
}

export async function ingestBatch(
  userId: string,
  records: IncomingDictation[],
  options: { label: string; sourcePath?: string; onProgress?: (done: number, total: number) => void } = {
    label: 'batch',
  },
): Promise<BatchResult> {
  const { databaseBytes } = await import('../db/index.ts');
  const batchId = id('batch');
  const startedAt = Date.now();
  const bytesBefore = databaseBytes();

  run(
    `INSERT INTO ingest_batches (id, user_id, label, source_path, record_count, skipped_count, started_at, provider)
     VALUES (?, ?, ?, ?, 0, 0, ?, ?)`,
    [batchId, userId, options.label, options.sourcePath ?? null, startedAt, llm().name],
  );

  const totals = { processed: 0, duplicates: 0, created: 0, reinforced: 0, superseded: 0, held: 0, rejected: 0 };
  const ordered = [...records].sort((a, b) => a.captured_at - b.captured_at);

  for (let i = 0; i < ordered.length; i++) {
    const outcome = await ingestDictation(userId, ordered[i]!, { batchId });
    if (outcome.status === 'duplicate') {
      totals.duplicates += 1;
    } else {
      totals.processed += 1;
      totals.created += outcome.created;
      totals.reinforced += outcome.reinforced;
      totals.superseded += outcome.superseded;
      totals.held += outcome.held;
      totals.rejected += outcome.rejected;
    }
    options.onProgress?.(i + 1, ordered.length);
  }

  const bytesAfter = databaseBytes();
  run(
    'UPDATE ingest_batches SET record_count = ?, skipped_count = ?, finished_at = ?, bytes_before = ?, bytes_after = ? WHERE id = ?',
    [totals.processed, totals.duplicates, Date.now(), bytesBefore, bytesAfter, batchId],
  );

  run(
    `INSERT INTO db_growth_samples (id, user_id, taken_at, dictation_count, memory_count, episode_count, rejection_count, bytes)
     SELECT ?, ?, ?,
       (SELECT COUNT(*) FROM dictations WHERE user_id = ?),
       (SELECT COUNT(*) FROM memories WHERE user_id = ?),
       (SELECT COUNT(*) FROM episodes WHERE user_id = ?),
       (SELECT COUNT(*) FROM rejections WHERE user_id = ?),
       ?`,
    [id('grow'), userId, Date.now(), userId, userId, userId, userId, bytesAfter],
  );

  return {
    batch_id: batchId,
    ...totals,
    duration_ms: Date.now() - startedAt,
    bytes_before: bytesBefore,
    bytes_after: bytesAfter,
  };
}

export type { Dictation };
