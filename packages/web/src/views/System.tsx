import { useEffect, useState } from 'react';
import { TracePanel } from '../components/Panels';
import { api, type Stats, type Trace } from '../lib/api';
import { bytes, relative, usd } from '../lib/format';

/**
 * The engineer's view. Reachable from the normal interface because the numbers
 * here — how long retrieval takes, how fast the database grows, what a memory
 * costs to build — are the ones that decide whether this product can exist at
 * the size of a real user's history.
 */
export function System() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [traces, setTraces] = useState<Trace[]>([]);
  const [open, setOpen] = useState<string | null>(null);

  useEffect(() => {
    api.stats().then(setStats);
    api.traces('query').then(setTraces).catch(() => setTraces([]));
  }, []);

  if (!stats) return <div className="page">Loading…</div>;

  const growth = stats.database.samples;
  const first = growth[0];
  const last = growth[growth.length - 1];

  return (
    <div className="page wide">
      <div className="page-head">
        <h1>System</h1>
        <p className="lede">
          What the memory costs to run. Latency, growth and model usage are measured from real traces, not
          estimated.
        </p>
      </div>

      <h2>Provider</h2>
      <div className="card">
        <div className="row between">
          <span className="mono">{stats.provider}</span>
          <span className={`chip ${stats.provider.startsWith('anthropic') ? 'ok' : 'preference'}`}>
            {stats.provider.startsWith('anthropic') ? 'model-backed' : 'offline, deterministic'}
          </span>
        </div>
        <p className="meta" style={{ marginBottom: 0, marginTop: 8 }}>
          {stats.provider.startsWith('anthropic')
            ? 'Extraction, planning and answers run on the model. Costs below are real.'
            : 'No credentials configured, so the deterministic provider is running. The pipeline, storage, retrieval, policy and traces are identical; only the extractor and the answer writer differ. Set ANTHROPIC_API_KEY and LLM_PROVIDER=anthropic to switch.'}
        </p>
      </div>

      <h2>Latency</h2>
      <div className="stat-grid">
        <Stat value={ms(stats.latency.query, 'p50')} label="Hey Kivi, median" />
        <Stat value={ms(stats.latency.query, 'p95')} label="Hey Kivi, p95" />
        <Stat value={ms(stats.latency.ingest, 'p50')} label="ingest per dictation, median" />
        <Stat value={ms(stats.latency.ingest, 'p95')} label="ingest per dictation, p95" />
      </div>
      <p className="meta" style={{ marginTop: 8 }}>
        {stats.latency.query.count === 0
          ? `No conversations yet — ask Kivi something and these fill in. ${stats.latency.ingest.count} ingestions measured.`
          : `${stats.latency.query.count} conversations and ${stats.latency.ingest.count} ingestions measured.`}
      </p>

      <h2>Database growth</h2>
      <div className="stat-grid">
        <Stat value={bytes(stats.database.bytes)} label="total on disk" />
        <Stat value={bytes(stats.database.per_dictation_bytes)} label="per dictation" />
        <Stat value={stats.memory.dictations} label="dictations" />
        <Stat value={stats.active_memories} label="active memories" />
      </div>
      {first && last && growth.length > 1 && (
        <p className="meta" style={{ marginTop: 8 }}>
          Grew from {bytes(first.bytes)} at {first.dictation_count} dictations to {bytes(last.bytes)} at{' '}
          {last.dictation_count}.
        </p>
      )}
      <p className="meta">
        Memories grow far more slowly than dictations — {stats.active_memories} active memories from{' '}
        {stats.memory.dictations} recordings. That ratio is the point: most of what a person says is not worth
        remembering, and a system that keeps everything is a search index wearing a memory’s clothes.
      </p>

      <h2>Model usage</h2>
      <div className="stat-grid">
        <Stat value={stats.usage.llm_calls} label="model calls" />
        <Stat value={stats.usage.tokens_in.toLocaleString()} label="input tokens" />
        <Stat value={stats.usage.tokens_out.toLocaleString()} label="output tokens" />
        <Stat value={usd(stats.usage.cost_usd)} label="total cost" />
      </div>

      <h2>What Kivi has kept, and refused</h2>
      <table>
        <thead>
          <tr>
            <th>kind</th>
            <th>status</th>
            <th style={{ width: 80 }}>count</th>
          </tr>
        </thead>
        <tbody>
          {stats.memory.byType.map((row) => (
            <tr key={row.type + row.status}>
              <td>
                <span className={`chip ${row.type}`}>{row.type}</span>
              </td>
              <td className="meta">{row.status.replace(/_/g, ' ')}</td>
              <td className="mono">{row.n}</td>
            </tr>
          ))}
          {stats.memory.rejections.map((row) => (
            <tr key={row.reason_code}>
              <td>
                <span className="chip withheld">refused</span>
              </td>
              <td className="meta">{row.reason_code.replace(/_/g, ' ')}</td>
              <td className="mono">{row.n}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h2>Recent decisions</h2>
      {traces.length === 0 ? (
        <div className="empty">No conversations yet.</div>
      ) : (
        <table>
          <thead>
            <tr>
              <th>question</th>
              <th style={{ width: 90 }}>outcome</th>
              <th style={{ width: 70 }}>ms</th>
              <th style={{ width: 70 }} />
            </tr>
          </thead>
          <tbody>
            {traces.slice(0, 18).map((trace) => (
              <tr key={trace.id}>
                <td>
                  {String((trace.payload as { question?: string }).question ?? '—')}
                  <div className="meta">{relative(trace.started_at)}</div>
                </td>
                <td>
                  <span className={`chip ${(trace.payload as { outcome?: string }).outcome === 'abstained' ? 'withheld' : 'ok'}`}>
                    {String((trace.payload as { outcome?: string }).outcome ?? '—')}
                  </span>
                </td>
                <td className="mono">{trace.duration_ms}</td>
                <td>
                  <button className="btn quiet" onClick={() => setOpen(trace.id)}>
                    trace
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {open && <TracePanel id={open} onClose={() => setOpen(null)} />}
    </div>
  );
}

/** Nothing measured is "—", not "0 ms". A zero here would read as a lie. */
function ms(summary: { count: number; p50: number; p95: number }, key: 'p50' | 'p95'): string {
  return summary.count === 0 ? '—' : `${summary[key]} ms`;
}

function Stat({ value, label }: { value: string | number; label: string }) {
  return (
    <div className="stat">
      <div className="value">{value}</div>
      <div className="label">{label}</div>
    </div>
  );
}
