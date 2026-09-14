import { all, one, run } from '../db/index.ts';
import { providerLabel } from '../llm/index.ts';
import type { TraceStage, Usage } from '../types.ts';
import { id } from '../util/ids.ts';

/**
 * A trace is the answer to "why did memory affect this result, or why did it
 * not". It records the plan, every candidate that was considered with its
 * scores, what was included, what was excluded and for what reason, which tools
 * ran, and what each stage cost in time and tokens.
 *
 * It is written for every ingestion and every Hey Kivi turn, not only when
 * something goes wrong — a trace you have to reproduce is not evidence.
 */
export class Tracer {
  readonly id: string;
  private readonly startedAt = Date.now();
  private readonly stages: TraceStage[] = [];
  private readonly payload: Record<string, unknown> = {};
  private usage: Usage = { tokens_in: 0, tokens_out: 0, cost_usd: 0, calls: 0 };
  private stageStart = Date.now();

  constructor(
    readonly kind: 'ingest' | 'query',
    readonly userId: string,
    readonly subjectId: string | null = null,
  ) {
    this.id = id('trc');
  }

  /** Closes the current stage and opens the next. */
  stage(name: string, detail?: Record<string, unknown>): void {
    const now = Date.now();
    this.stages.push({ name, ms: now - this.stageStart, detail });
    this.stageStart = now;
  }

  async timed<T>(name: string, fn: () => Promise<T> | T, detail?: () => Record<string, unknown>): Promise<T> {
    const begin = Date.now();
    const result = await fn();
    const now = Date.now();
    this.stages.push({ name, ms: now - begin, detail: detail?.() });
    this.stageStart = now;
    return result;
  }

  set(key: string, value: unknown): void {
    this.payload[key] = value;
  }

  push(key: string, value: unknown): void {
    const list = (this.payload[key] as unknown[]) ?? [];
    list.push(value);
    this.payload[key] = list;
  }

  addUsage(usage: Usage): void {
    this.usage = {
      tokens_in: this.usage.tokens_in + usage.tokens_in,
      tokens_out: this.usage.tokens_out + usage.tokens_out,
      cost_usd: this.usage.cost_usd + usage.cost_usd,
      calls: this.usage.calls + usage.calls,
    };
  }

  get totals(): Usage {
    return this.usage;
  }

  get elapsedMs(): number {
    return Date.now() - this.startedAt;
  }

  save(subjectId = this.subjectId): string {
    const endedAt = Date.now();
    const [provider, model] = providerLabel().split(':');
    run(
      `INSERT INTO traces
         (id, user_id, kind, subject_id, provider, model, started_at, ended_at, duration_ms,
          stages, payload, tokens_in, tokens_out, cost_usd, llm_calls)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [
        this.id,
        this.userId,
        this.kind,
        subjectId,
        provider ?? 'local',
        model ?? '',
        this.startedAt,
        endedAt,
        endedAt - this.startedAt,
        JSON.stringify(this.stages),
        JSON.stringify(this.payload),
        this.usage.tokens_in,
        this.usage.tokens_out,
        this.usage.cost_usd,
        this.usage.calls,
      ],
    );
    return this.id;
  }

  toJSON() {
    return {
      id: this.id,
      kind: this.kind,
      duration_ms: this.elapsedMs,
      stages: this.stages,
      payload: this.payload,
      usage: this.usage,
    };
  }
}

export interface StoredTrace {
  id: string;
  kind: string;
  subject_id: string | null;
  provider: string;
  model: string;
  started_at: number;
  duration_ms: number;
  stages: TraceStage[];
  payload: Record<string, unknown>;
  tokens_in: number;
  tokens_out: number;
  cost_usd: number;
  llm_calls: number;
}

function hydrate(row: Record<string, unknown>): StoredTrace {
  return {
    ...(row as unknown as StoredTrace),
    stages: JSON.parse(String(row.stages ?? '[]')) as TraceStage[],
    payload: JSON.parse(String(row.payload ?? '{}')) as Record<string, unknown>,
  };
}

export function getTrace(traceId: string): StoredTrace | undefined {
  const row = one('SELECT * FROM traces WHERE id = ?', [traceId]);
  return row ? hydrate(row) : undefined;
}

export function traceForSubject(subjectId: string): StoredTrace | undefined {
  const row = one('SELECT * FROM traces WHERE subject_id = ? ORDER BY started_at DESC LIMIT 1', [subjectId]);
  return row ? hydrate(row) : undefined;
}

export function recentTraces(userId: string, kind: 'ingest' | 'query', limit = 50): StoredTrace[] {
  return all('SELECT * FROM traces WHERE user_id = ? AND kind = ? ORDER BY started_at DESC LIMIT ?', [
    userId,
    kind,
    limit,
  ]).map(hydrate);
}

export function latencySummary(userId: string, kind: 'ingest' | 'query') {
  const rows = all<{ duration_ms: number }>(
    'SELECT duration_ms FROM traces WHERE user_id = ? AND kind = ? ORDER BY duration_ms',
    [userId, kind],
  );
  if (rows.length === 0) return { count: 0, p50: 0, p95: 0, max: 0, mean: 0 };
  const values = rows.map((r) => r.duration_ms);
  const at = (q: number) => values[Math.min(values.length - 1, Math.floor(q * values.length))]!;
  return {
    count: values.length,
    p50: at(0.5),
    p95: at(0.95),
    max: values[values.length - 1]!,
    mean: Math.round(values.reduce((a, b) => a + b, 0) / values.length),
  };
}

export function usageSummary(userId: string) {
  const row = one<{ tokens_in: number; tokens_out: number; cost_usd: number; llm_calls: number }>(
    'SELECT SUM(tokens_in) AS tokens_in, SUM(tokens_out) AS tokens_out, SUM(cost_usd) AS cost_usd, SUM(llm_calls) AS llm_calls FROM traces WHERE user_id = ?',
    [userId],
  );
  return {
    tokens_in: Number(row?.tokens_in ?? 0),
    tokens_out: Number(row?.tokens_out ?? 0),
    cost_usd: Number(row?.cost_usd ?? 0),
    llm_calls: Number(row?.llm_calls ?? 0),
  };
}
