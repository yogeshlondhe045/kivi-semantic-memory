import type { FastifyInstance } from 'fastify';
import { ask } from '../agent/index.ts';
import { config } from '../config.ts';
import { all, count, databaseBytes, one, run } from '../db/index.ts';
import { providerLabel } from '../llm/index.ts';
import { ingestDictation } from '../memory/ingest.ts';
import { activeMemories, forgetMemory, lexiconFor, memoryStats } from '../memory/store.ts';
import { clearCoverageCache } from '../retrieval/coverage.ts';
import { getTrace, latencySummary, recentTraces, traceForSubject, usageSummary } from '../trace/index.ts';
import { formatTimestamp } from '../util/time.ts';

const USER = config.defaultUserId;

export async function registerRoutes(app: FastifyInstance): Promise<void> {
  // ------------------------------------------------------------------ health
  app.get('/api/health', async () => ({
    ok: true,
    provider: providerLabel(),
    user: one('SELECT id, display_name, timezone FROM users WHERE id = ?', [USER]) ?? null,
    counts: {
      dictations: count('SELECT COUNT(*) AS n FROM dictations WHERE user_id = ?', [USER]),
      memories: count(`SELECT COUNT(*) AS n FROM memories WHERE user_id = ? AND status = 'active'`, [USER]),
      open_reviews: count(`SELECT COUNT(*) AS n FROM review_items WHERE user_id = ? AND status = 'open'`, [USER]),
      ignored: count('SELECT COUNT(*) AS n FROM rejections WHERE user_id = ?', [USER]),
    },
  }));

  // --------------------------------------------------------------- hey kivi
  app.post<{ Body: { question?: string; conversation_id?: string } }>('/api/hey-kivi', async (request, reply) => {
    const question = (request.body?.question ?? '').trim();
    if (!question) return reply.code(400).send({ error: 'question is required' });
    const result = await ask(USER, question, { conversationId: request.body?.conversation_id });
    return result;
  });

  app.get<{ Params: { id: string } }>('/api/conversations/:id', async (request) => ({
    conversation: one('SELECT * FROM conversations WHERE id = ? AND user_id = ?', [request.params.id, USER]) ?? null,
    turns: all(
      'SELECT id, role, content, outcome, citations, trace_id, created_at FROM turns WHERE conversation_id = ? ORDER BY created_at',
      [request.params.id],
    ).map((turn) => ({ ...turn, citations: JSON.parse(String(turn.citations ?? '[]')) })),
  }));

  app.get('/api/conversations', async () =>
    all('SELECT * FROM conversations WHERE user_id = ? ORDER BY updated_at DESC LIMIT 30', [USER]),
  );

  // ------------------------------------------------------------- dictations
  app.get<{ Querystring: { limit?: string; offset?: string; app?: string; q?: string } }>(
    '/api/dictations',
    async (request) => {
      const limit = Math.min(200, Number(request.query.limit ?? 40));
      const offset = Number(request.query.offset ?? 0);
      const filters = ['d.user_id = ?'];
      const params: unknown[] = [USER];

      if (request.query.app) {
        filters.push('d.app = ?');
        params.push(request.query.app);
      }
      if (request.query.q) {
        filters.push('(d.formatted LIKE ? OR d.raw_asr LIKE ?)');
        params.push(`%${request.query.q}%`, `%${request.query.q}%`);
      }

      return {
        total: count(`SELECT COUNT(*) AS n FROM dictations d WHERE ${filters.join(' AND ')}`, params),
        items: all(
          `SELECT d.id, d.captured_at, d.app, d.device, d.language, d.asr_confidence, d.raw_asr,
                  d.formatted, d.word_count, e.summary, e.salience,
                  (SELECT COUNT(*) FROM memory_evidence me WHERE me.dictation_id = d.id) AS memories_touched,
                  (SELECT COUNT(*) FROM rejections r WHERE r.dictation_id = d.id) AS ignored
             FROM dictations d
             LEFT JOIN episodes e ON e.dictation_id = d.id
            WHERE ${filters.join(' AND ')}
            ORDER BY d.captured_at DESC
            LIMIT ? OFFSET ?`,
          [...params, limit, offset],
        ),
      };
    },
  );

  app.get<{ Params: { id: string } }>('/api/dictations/:id', async (request, reply) => {
    const dictation = one('SELECT * FROM dictations WHERE id = ? AND user_id = ?', [request.params.id, USER]);
    if (!dictation) return reply.code(404).send({ error: 'not found' });

    return {
      dictation,
      episode: one('SELECT * FROM episodes WHERE dictation_id = ?', [request.params.id]) ?? null,
      memories: all(
        `SELECT m.id, m.type, m.statement, m.confidence, m.status, me.kind, me.quote
           FROM memory_evidence me
           JOIN memories m ON m.id = me.memory_id
          WHERE me.dictation_id = ?`,
        [request.params.id],
      ),
      ignored: all('SELECT * FROM rejections WHERE dictation_id = ?', [request.params.id]),
      trace: traceForSubject(request.params.id) ?? null,
    };
  });

  /** The replay client: speak a new dictation and watch memory form. */
  app.post<{
    Body: { raw_asr?: string; formatted?: string; app?: string; captured_at?: number; language?: string };
  }>('/api/dictations', async (request, reply) => {
    const raw = (request.body?.raw_asr ?? '').trim();
    const formatted = (request.body?.formatted ?? raw).trim();
    if (!formatted) return reply.code(400).send({ error: 'raw_asr or formatted is required' });

    const outcome = await ingestDictation(USER, {
      captured_at: request.body?.captured_at ?? Date.now(),
      app: (request.body?.app ?? 'notes').toLowerCase(),
      language: request.body?.language ?? 'en',
      raw_asr: raw || formatted,
      formatted,
      device: 'replay-client',
      asr_confidence: 0.95,
    });
    clearCoverageCache();

    return {
      ...outcome,
      trace: outcome.trace_id ? getTrace(outcome.trace_id) : null,
      memories: all(
        `SELECT m.id, m.type, m.statement, m.confidence, m.status, me.kind
           FROM memory_evidence me JOIN memories m ON m.id = me.memory_id
          WHERE me.dictation_id = ?`,
        [outcome.dictation_id],
      ),
      ignored: all('SELECT * FROM rejections WHERE dictation_id = ?', [outcome.dictation_id]),
    };
  });

  // ---------------------------------------------------------------- memories
  app.get<{ Querystring: { type?: string; status?: string; q?: string } }>('/api/memories', async (request) => {
    const filters = ['m.user_id = ?'];
    const params: unknown[] = [USER];

    if (request.query.type) {
      filters.push('m.type = ?');
      params.push(request.query.type);
    }
    filters.push('m.status = ?');
    params.push(request.query.status ?? 'active');

    if (request.query.q) {
      filters.push('(m.statement LIKE ? OR m.subject LIKE ?)');
      params.push(`%${request.query.q}%`, `%${request.query.q}%`);
    }

    return {
      items: all(
        `SELECT m.*, (SELECT COUNT(*) FROM memory_evidence me WHERE me.memory_id = m.id) AS evidence
           FROM memories m
          WHERE ${filters.join(' AND ')}
          ORDER BY m.type, m.confidence DESC, m.last_seen_at DESC`,
        params,
      ).map((row) => ({ ...row, detail: JSON.parse(String(row.detail ?? '{}')) })),
      stats: memoryStats(USER),
    };
  });

  app.get<{ Params: { id: string } }>('/api/memories/:id', async (request, reply) => {
    const memory = one('SELECT * FROM memories WHERE id = ? AND user_id = ?', [request.params.id, USER]);
    if (!memory) return reply.code(404).send({ error: 'not found' });

    return {
      memory: { ...memory, detail: JSON.parse(String(memory.detail ?? '{}')) },
      evidence: all(
        `SELECT me.id, me.kind, me.quote, me.created_at, me.confidence,
                d.id AS dictation_id, d.captured_at, d.app, d.formatted, d.raw_asr
           FROM memory_evidence me
           JOIN dictations d ON d.id = me.dictation_id
          WHERE me.memory_id = ?
          ORDER BY d.captured_at`,
        [request.params.id],
      ),
      superseded: all('SELECT id, statement, updated_at FROM memories WHERE superseded_by = ?', [request.params.id]),
    };
  });

  app.delete<{ Params: { id: string } }>('/api/memories/:id', async (request, reply) => {
    const removed = forgetMemory(USER, request.params.id);
    if (!removed) return reply.code(404).send({ error: 'not found' });
    clearCoverageCache();
    return { ok: true, tombstoned: true };
  });

  // -------------------------------------------------------------- ignored
  app.get('/api/ignored', async () => ({
    items: all(
      `SELECT r.*, d.formatted, d.captured_at, d.app
         FROM rejections r
         LEFT JOIN dictations d ON d.id = r.dictation_id
        WHERE r.user_id = ?
        ORDER BY r.created_at DESC
        LIMIT 200`,
      [USER],
    ),
    by_reason: all(
      'SELECT reason_code, COUNT(*) AS n FROM rejections WHERE user_id = ? GROUP BY reason_code ORDER BY n DESC',
      [USER],
    ),
  }));

  // --------------------------------------------------------------- review
  app.get('/api/review', async () => ({
    items: all(
      `SELECT r.*, m.type, m.statement, m.confidence
         FROM review_items r
         LEFT JOIN memories m ON m.id = r.memory_id
        WHERE r.user_id = ? AND r.status = 'open'
        ORDER BY r.created_at`,
      [USER],
    ).map((row) => ({
      ...row,
      options: JSON.parse(String(row.options ?? '[]')),
      context: JSON.parse(String(row.context ?? '{}')),
    })),
  }));

  app.post<{ Params: { id: string }; Body: { choice?: string } }>('/api/review/:id', async (request, reply) => {
    const item = one<{ id: string; memory_id: string | null; options: string; context: string }>(
      `SELECT * FROM review_items WHERE id = ? AND user_id = ? AND status = 'open'`,
      [request.params.id, USER],
    );
    if (!item) return reply.code(404).send({ error: 'not found' });

    const options = JSON.parse(item.options) as { id: string; effect: string }[];
    const chosen = options.find((o) => o.id === request.body?.choice);
    if (!chosen) return reply.code(400).send({ error: 'unknown choice' });

    if (item.memory_id) {
      if (chosen.effect === 'activate') {
        run(`UPDATE memories SET status = 'active', origin = 'user_edit', confidence = MAX(confidence, 0.9), updated_at = ? WHERE id = ?`, [
          Date.now(),
          item.memory_id,
        ]);
      } else if (chosen.effect === 'forget') {
        forgetMemory(USER, item.memory_id, 'user_rejected_confirmation');
      } else if (chosen.effect === 'restore_previous') {
        const context = JSON.parse(item.context) as { previous_id?: string };
        if (context.previous_id) {
          run(`UPDATE memories SET status = 'active', superseded_by = NULL, updated_at = ? WHERE id = ?`, [
            Date.now(),
            context.previous_id,
          ]);
          forgetMemory(USER, item.memory_id, 'user_kept_previous');
        }
      }
    }

    run(`UPDATE review_items SET status = 'resolved', resolved_at = ?, resolution = ? WHERE id = ?`, [
      Date.now(),
      chosen.id,
      item.id,
    ]);
    clearCoverageCache();
    return { ok: true, effect: chosen.effect };
  });

  // --------------------------------------------------------------- lexicon
  app.get('/api/lexicon', async () => ({ items: lexiconFor(USER) }));

  // ---------------------------------------------------------------- traces
  app.get<{ Params: { id: string } }>('/api/traces/:id', async (request, reply) => {
    const trace = getTrace(request.params.id);
    if (!trace) return reply.code(404).send({ error: 'not found' });
    return trace;
  });

  app.get<{ Querystring: { kind?: 'ingest' | 'query' } }>('/api/traces', async (request) =>
    recentTraces(USER, request.query.kind ?? 'query', 40),
  );

  // ----------------------------------------------------------------- stats
  app.get('/api/stats', async () => {
    const growth = all<{ taken_at: number; dictation_count: number; memory_count: number; bytes: number }>(
      'SELECT taken_at, dictation_count, memory_count, episode_count, rejection_count, bytes FROM db_growth_samples WHERE user_id = ? ORDER BY taken_at',
      [USER],
    );
    const dictations = count('SELECT COUNT(*) AS n FROM dictations WHERE user_id = ?', [USER]);
    const bytes = databaseBytes();

    return {
      provider: providerLabel(),
      memory: memoryStats(USER),
      latency: { ingest: latencySummary(USER, 'ingest'), query: latencySummary(USER, 'query') },
      usage: usageSummary(USER),
      database: {
        bytes,
        per_dictation_bytes: dictations ? Math.round(bytes / dictations) : 0,
        samples: growth,
      },
      active_memories: activeMemories(USER).length,
      oldest: (() => {
        const row = one<{ captured_at: number }>(
          'SELECT captured_at FROM dictations WHERE user_id = ? ORDER BY captured_at LIMIT 1',
          [USER],
        );
        return row ? formatTimestamp(row.captured_at) : null;
      })(),
    };
  });
}
