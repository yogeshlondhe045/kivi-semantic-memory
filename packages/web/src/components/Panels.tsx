import { useEffect, useState } from 'react';
import { api, type MemoryDetail, type Trace } from '../lib/api';
import { bytes, pct, usd, when } from '../lib/format';

export function Panel({ children, onClose }: { children: React.ReactNode; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <>
      <div className="scrim" onClick={onClose} />
      <aside className="panel">{children}</aside>
    </>
  );
}

/**
 * The provenance panel. Opening a memory shows every dictation that produced or
 * reinforced it, in order, with the sentence quoted. This is the whole promise
 * of the product in one screen: nothing Kivi believes is unattributable.
 */
export function MemoryPanel({ id, onClose, onForget }: { id: string; onClose: () => void; onForget?: () => void }) {
  const [data, setData] = useState<MemoryDetail | null>(null);
  const [confirming, setConfirming] = useState(false);

  useEffect(() => {
    let live = true;
    api.memory(id).then((d) => live && setData(d));
    return () => {
      live = false;
    };
  }, [id]);

  if (!data) {
    return (
      <Panel onClose={onClose}>
        <p className="meta">Loading…</p>
      </Panel>
    );
  }

  const { memory, evidence, superseded } = data;
  const due = memory.detail?.due_at as number | undefined;

  return (
    <Panel onClose={onClose}>
      <div className="panel-head">
        <div>
          <span className={`chip ${memory.type}`}>{memory.type}</span>
          <h3 style={{ marginTop: 8 }}>{memory.statement}</h3>
        </div>
        <button className="btn quiet" onClick={onClose}>
          Close
        </button>
      </div>

      <div className="row wrap" style={{ gap: 14, marginBottom: 18 }}>
        <span className="meta">Confidence {pct(memory.confidence)}</span>
        <span className="meta">
          {evidence.length} mention{evidence.length === 1 ? '' : 's'}
        </span>
        <span className="meta">First heard {when(memory.first_seen_at)}</span>
        {due ? <span className="meta">Due {when(due)}</span> : null}
      </div>

      <p className="lede" style={{ fontSize: 13.5 }}>
        Kivi believes this because you said it. Every line below is a recording it came from — nothing
        else contributed.
      </p>

      <h2>Where it came from</h2>
      {evidence.map((item) => (
        <div className="card" key={item.id}>
          <div className="row between" style={{ marginBottom: 7 }}>
            <span className="meta">
              {when(item.captured_at)} · {item.app}
            </span>
            <span className={`chip ${item.kind === 'contradicted' ? 'withheld' : ''}`}>{item.kind}</span>
          </div>
          <div className="quote-block">“{item.quote}”</div>
        </div>
      ))}

      {superseded.length > 0 && (
        <>
          <h2>What this replaced</h2>
          {superseded.map((item) => (
            <div className="card" key={item.id}>
              <div className="strike">{item.statement}</div>
              <div className="meta" style={{ marginTop: 4 }}>
                Replaced {when(item.updated_at)}. Kept, not deleted — you can see what changed.
              </div>
            </div>
          ))}
        </>
      )}

      {onForget && (
        <>
          <div className="divider" />
          {confirming ? (
            <div className="card">
              <p style={{ marginTop: 0 }}>
                Forgetting this is permanent. Kivi will not relearn it, even if you say something similar
                again.
              </p>
              <div className="row">
                <button
                  className="btn danger"
                  onClick={async () => {
                    await api.forget(memory.id);
                    onForget();
                    onClose();
                  }}
                >
                  Forget it permanently
                </button>
                <button className="btn quiet" onClick={() => setConfirming(false)}>
                  Keep it
                </button>
              </div>
            </div>
          ) : (
            <button className="btn danger" onClick={() => setConfirming(true)}>
              Forget this
            </button>
          )}
        </>
      )}
    </Panel>
  );
}

const STAGE_COLOURS = ['#90c46a', '#e0b155', '#7aa2f7', '#c58fe7', '#e08a7a'];

/**
 * The trace panel — "why did memory affect this result, or why did it not".
 *
 * Deliberately reachable from the normal interface rather than hidden behind a
 * developer console: the question "how do you know that?" belongs to the person
 * using the product, and the engineer's version is the same data, one toggle
 * further down.
 */
export function TracePanel({ id, onClose }: { id: string; onClose: () => void }) {
  const [trace, setTrace] = useState<Trace | null>(null);
  const [raw, setRaw] = useState(false);

  useEffect(() => {
    let live = true;
    api.trace(id).then((t) => live && setTrace(t));
    return () => {
      live = false;
    };
  }, [id]);

  if (!trace) {
    return (
      <Panel onClose={onClose}>
        <p className="meta">Loading…</p>
      </Panel>
    );
  }

  const payload = trace.payload as {
    plans?: { intent: string; keywords: string[]; entities: string[]; apps: string[]; window: string | null }[];
    tool_calls?: { name: string; ms: number; grounded: boolean; returned: number; considered?: Considered[]; plan?: Record<string, unknown> }[];
    notes?: string[];
    memory_influence?: string;
    outcome?: string;
    rejected?: { type: string; statement: string; reason_code: string; reason: string }[];
    written?: { type: string; statement: string; action: string; because: string }[];
  };

  const total = Math.max(1, trace.stages.reduce((sum, s) => sum + s.ms, 0));
  const considered = payload.tool_calls?.flatMap((c) => c.considered ?? []) ?? [];

  return (
    <Panel onClose={onClose}>
      <div className="panel-head">
        <div>
          <h3>Why this result</h3>
          <span className="meta mono">
            {trace.id} · {trace.provider}
            {trace.model ? `:${trace.model}` : ''}
          </span>
        </div>
        <button className="btn quiet" onClick={onClose}>
          Close
        </button>
      </div>

      {payload.memory_influence && <p className="lede">{payload.memory_influence}</p>}

      <h2>Time spent · {trace.duration_ms} ms</h2>
      <div className="stage-bar">
        {trace.stages.map((stage, i) => (
          <span
            key={stage.name + i}
            title={`${stage.name} ${stage.ms}ms`}
            style={{ width: `${(stage.ms / total) * 100}%`, background: STAGE_COLOURS[i % STAGE_COLOURS.length] }}
          />
        ))}
      </div>
      <div className="row wrap" style={{ gap: 12, marginTop: 6 }}>
        {trace.stages.map((stage, i) => (
          <span className="meta" key={stage.name + i}>
            <span className="dot" style={{ background: STAGE_COLOURS[i % STAGE_COLOURS.length], display: 'inline-block', marginRight: 5 }} />
            {stage.name} {stage.ms}ms
          </span>
        ))}
      </div>

      {payload.plans && payload.plans.length > 0 && (
        <>
          <h2>What Kivi searched for</h2>
          {payload.plans.map((plan, i) => (
            <div className="card" key={i}>
              <div className="row wrap" style={{ gap: 6 }}>
                <span className="chip">{plan.intent}</span>
                {plan.window && <span className="chip">{plan.window}</span>}
                {plan.apps.map((app) => (
                  <span className="chip" key={app}>
                    {app}
                  </span>
                ))}
              </div>
              <div className="meta" style={{ marginTop: 8 }}>
                terms: {plan.keywords.join(', ') || '—'}
                {plan.entities.length ? ` · names: ${plan.entities.join(', ')}` : ''}
              </div>
            </div>
          ))}
        </>
      )}

      {payload.tool_calls && payload.tool_calls.length > 0 && (
        <>
          <h2>Tools called</h2>
          {payload.tool_calls.map((call, i) => (
            <div className="card" key={i}>
              <div className="row between">
                <span className="mono">{call.name}</span>
                <span className={`chip ${call.grounded ? 'ok' : 'withheld'}`}>
                  {call.grounded ? `${call.returned} result${call.returned === 1 ? '' : 's'}` : 'nothing grounded'}
                </span>
              </div>
              <div className="meta" style={{ marginTop: 5 }}>
                {call.ms} ms
              </div>
            </div>
          ))}
        </>
      )}

      {considered.length > 0 && (
        <>
          <h2>Everything it looked at</h2>
          <table>
            <thead>
              <tr>
                <th>candidate</th>
                <th style={{ width: 62 }}>lexical</th>
                <th style={{ width: 62 }}>meaning</th>
                <th style={{ width: 52 }}>time</th>
                <th style={{ width: 62 }}>fused</th>
              </tr>
            </thead>
            <tbody>
              {considered.slice(0, 12).map((item, i) => (
                <tr key={item.id + i}>
                  <td>
                    <span className="chip" style={{ marginRight: 6 }}>
                      {item.kind}
                    </span>
                    {item.text}
                  </td>
                  <td className="mono">{item.signals.lexical.toFixed(2)}</td>
                  <td className="mono">{item.signals.semantic.toFixed(3)}</td>
                  <td className="mono">{item.signals.temporal.toFixed(2)}</td>
                  <td className="mono">{item.signals.fused.toFixed(4)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </>
      )}

      {payload.written && payload.written.length > 0 && (
        <>
          <h2>What it learned</h2>
          {payload.written.map((item, i) => (
            <div className="card" key={i}>
              <div className="row between">
                <span className={`chip ${item.type}`}>{item.type}</span>
                <span className="chip">{item.action.replace(/_/g, ' ')}</span>
              </div>
              <div style={{ marginTop: 7 }}>{item.statement}</div>
              <div className="meta" style={{ marginTop: 4 }}>
                {item.because}
              </div>
            </div>
          ))}
        </>
      )}

      {payload.rejected && payload.rejected.length > 0 && (
        <>
          <h2>What it refused to keep</h2>
          {payload.rejected.map((item, i) => (
            <div className="card" key={i}>
              <div className="row between">
                <span className="chip withheld">{item.reason_code.replace(/_/g, ' ')}</span>
              </div>
              <div style={{ marginTop: 7 }}>{item.statement}</div>
              <div className="meta" style={{ marginTop: 4 }}>
                {item.reason}
              </div>
            </div>
          ))}
        </>
      )}

      {payload.notes && payload.notes.length > 0 && (
        <>
          <h2>Notes</h2>
          {payload.notes.map((note, i) => (
            <p className="meta" key={i} style={{ margin: '4px 0' }}>
              {note}
            </p>
          ))}
        </>
      )}

      <h2>Cost</h2>
      <div className="stat-grid">
        <div className="stat">
          <div className="value">{trace.llm_calls}</div>
          <div className="label">model calls</div>
        </div>
        <div className="stat">
          <div className="value">{trace.tokens_in + trace.tokens_out}</div>
          <div className="label">tokens</div>
        </div>
        <div className="stat">
          <div className="value">{usd(trace.cost_usd)}</div>
          <div className="label">cost</div>
        </div>
      </div>

      <div className="divider" />
      <button className="btn quiet" onClick={() => setRaw((v) => !v)}>
        {raw ? 'Hide' : 'Show'} raw trace
      </button>
      {raw && <pre className="json">{JSON.stringify(trace, null, 2)}</pre>}
    </Panel>
  );
}

interface Considered {
  kind: string;
  id: string;
  text: string;
  score: number;
  signals: { lexical: number; semantic: number; temporal: number; prior: number; fused: number };
}

export function Stat({ value, label }: { value: string | number; label: string }) {
  return (
    <div className="stat">
      <div className="value">{value}</div>
      <div className="label">{label}</div>
    </div>
  );
}

export { bytes };
