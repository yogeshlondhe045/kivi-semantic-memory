import { useEffect, useState } from 'react';
import { MemoryPanel, Panel, TracePanel } from '../components/Panels';
import { api, type DictationDetail, type DictationRow, type SpeakResult } from '../lib/api';
import { pct, relative, when } from '../lib/format';

const APPS = ['', 'slack', 'gmail', 'notion', 'linear', 'whatsapp', 'docs', 'vscode', 'notes'];

export function Dictations({ onChanged }: { onChanged: () => void }) {
  const [rows, setRows] = useState<DictationRow[]>([]);
  const [total, setTotal] = useState(0);
  const [app, setApp] = useState('');
  const [q, setQ] = useState('');
  const [limit, setLimit] = useState(40);
  const [open, setOpen] = useState<string | null>(null);
  const [speaking, setSpeaking] = useState(false);

  const load = () => {
    api.dictations({ app, q, limit }).then((data) => {
      setRows(data.items);
      setTotal(data.total);
    });
  };

  useEffect(load, [app, q, limit]);

  return (
    <div className="page wide">
      <div className="page-head">
        <h1>Dictations</h1>
        <p className="lede">
          Everything Kivi has heard. The left column is what the recogniser produced; the right is what Kivi
          wrote. Memory is built from the gap between them.
        </p>
      </div>

      <ReplayComposer
        open={speaking}
        onToggle={() => setSpeaking((v) => !v)}
        onDone={() => {
          load();
          onChanged();
        }}
      />

      <div className="filters">
        <input className="search" placeholder="Search your history…" value={q} onChange={(e) => setQ(e.target.value)} />
        {APPS.map((name) => (
          <button
            key={name || 'all'}
            className="btn"
            style={app === name ? { borderColor: 'var(--sage-dim)', color: 'var(--sage)' } : undefined}
            onClick={() => setApp(name)}
          >
            {name || 'all apps'}
          </button>
        ))}
      </div>

      <p className="meta" style={{ marginBottom: 12 }}>
        {total} dictation{total === 1 ? '' : 's'}
        {app ? ` in ${app}` : ''}
        {q ? ` matching “${q}”` : ''}
      </p>

      {rows.map((row) => (
        <div className="card interactive" key={row.id} onClick={() => setOpen(row.id)}>
          <div className="row between" style={{ marginBottom: 8 }}>
            <span className="meta">
              {when(row.captured_at)} · {row.app}
              {row.language !== 'en' ? ` · ${row.language}` : ''}
            </span>
            <div className="row" style={{ gap: 6 }}>
              {row.memories_touched > 0 && <span className="chip ok">{row.memories_touched} learned</span>}
              {row.ignored > 0 && <span className="chip withheld">{row.ignored} ignored</span>}
              <span className="meta">{relative(row.captured_at)}</span>
            </div>
          </div>
          <div>{row.formatted}</div>
          <div className="meta mono" style={{ marginTop: 7, opacity: 0.72 }}>
            {row.raw_asr.slice(0, 150)}
            {row.raw_asr.length > 150 ? '…' : ''}
          </div>
        </div>
      ))}

      {rows.length < total && (
        <button className="btn" style={{ marginTop: 14 }} onClick={() => setLimit((n) => n + 40)}>
          Show more
        </button>
      )}

      {open && <DictationPanel id={open} onClose={() => setOpen(null)} onChanged={onChanged} />}
    </div>
  );
}

/**
 * The replay client.
 *
 * Speech recognition is out of scope for this assignment, so dictations are
 * replayed as text. Everything downstream is the real pipeline: the same
 * extraction, the same policy gate, the same writes — which is why the result
 * card below shows what was learned *and* what was refused, immediately.
 */
function ReplayComposer({ open, onToggle, onDone }: { open: boolean; onToggle: () => void; onDone: () => void }) {
  const [text, setText] = useState('');
  const [app, setApp] = useState('slack');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<SpeakResult | null>(null);
  const [trace, setTrace] = useState<string | null>(null);

  if (!open) {
    return (
      <button className="btn primary" style={{ marginBottom: 18 }} onClick={onToggle}>
        Dictate something new
      </button>
    );
  }

  return (
    <div className="card" style={{ marginBottom: 18, borderColor: 'var(--ink-500)' }}>
      <div className="row between" style={{ marginBottom: 10 }}>
        <strong>Speak a new dictation</strong>
        <button className="btn quiet" onClick={onToggle}>
          Close
        </button>
      </div>
      <p className="meta" style={{ marginTop: 0 }}>
        Type it the way it would arrive from the recogniser — lowercase, no punctuation, misheard names and
        all. Kivi will process it exactly as it processes a real one.
      </p>
      <textarea
        className="search"
        style={{ width: '100%', minHeight: 84, resize: 'vertical', fontFamily: 'var(--mono)', fontSize: 13 }}
        placeholder="i prefer slack messages under four lines anything longer and people skim it"
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <div className="row" style={{ marginTop: 10 }}>
        <select className="search" style={{ flex: 'none', width: 130 }} value={app} onChange={(e) => setApp(e.target.value)}>
          {APPS.filter(Boolean).map((name) => (
            <option key={name}>{name}</option>
          ))}
        </select>
        <button
          className="btn primary"
          disabled={busy || !text.trim()}
          onClick={async () => {
            setBusy(true);
            try {
              const outcome = await api.speak({ raw_asr: text.trim(), app });
              setResult(outcome);
              setText('');
              onDone();
            } finally {
              setBusy(false);
            }
          }}
        >
          {busy ? 'Processing…' : 'Send to Kivi'}
        </button>
      </div>

      {result && (
        <div style={{ marginTop: 16 }}>
          <div className="row wrap" style={{ gap: 6 }}>
            <span className="chip ok">{result.created} learned</span>
            {result.reinforced > 0 && <span className="chip">{result.reinforced} reinforced</span>}
            {result.superseded > 0 && <span className="chip preference">{result.superseded} replaced</span>}
            {result.held > 0 && <span className="chip preference">{result.held} to confirm</span>}
            {result.rejected > 0 && <span className="chip withheld">{result.rejected} ignored</span>}
            <span className="meta">{result.duration_ms} ms</span>
          </div>
          {result.memories.map((memory) => (
            <div className="card" key={memory.id} style={{ marginTop: 8 }}>
              <span className={`chip ${memory.type}`}>{memory.type}</span>{' '}
              <span>{memory.statement}</span>
              <div className="meta" style={{ marginTop: 3 }}>
                {memory.kind} · confidence {pct(memory.confidence)} · {memory.status.replace(/_/g, ' ')}
              </div>
            </div>
          ))}
          {result.ignored.map((item) => (
            <div className="card" key={item.id} style={{ marginTop: 8 }}>
              <span className="chip withheld">{item.reason_code.replace(/_/g, ' ')}</span>
              <div className="meta" style={{ marginTop: 5 }}>{item.reason}</div>
            </div>
          ))}
          {result.memories.length === 0 && result.ignored.length === 0 && (
            <p className="meta">Nothing durable in that one. It is kept as a recording and stays searchable.</p>
          )}
          {result.trace && (
            <button className="btn quiet" style={{ marginTop: 8 }} onClick={() => setTrace(result.trace!.id)}>
              See what it did
            </button>
          )}
        </div>
      )}
      {trace && <TracePanel id={trace} onClose={() => setTrace(null)} />}
    </div>
  );
}

function DictationPanel({ id, onClose, onChanged }: { id: string; onClose: () => void; onChanged: () => void }) {
  const [data, setData] = useState<DictationDetail | null>(null);
  const [memory, setMemory] = useState<string | null>(null);
  const [trace, setTrace] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    api.dictation(id).then((d) => live && setData(d));
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

  const { dictation, episode, memories, ignored } = data;

  return (
    <>
      <Panel onClose={onClose}>
        <div className="panel-head">
          <div>
            <h3>{when(dictation.captured_at)}</h3>
            <span className="meta">
              {dictation.app} · {dictation.device ?? 'unknown device'} · {dictation.word_count} words
              {dictation.asr_confidence ? ` · ASR ${pct(dictation.asr_confidence)}` : ''}
            </span>
          </div>
          <button className="btn quiet" onClick={onClose}>
            Close
          </button>
        </div>

        <h2>What Kivi wrote</h2>
        <div className="card">{dictation.formatted}</div>

        <h2>What the recogniser heard</h2>
        <div className="card mono" style={{ color: 'var(--cream-dim)' }}>
          {dictation.raw_asr}
        </div>

        {episode && (
          <>
            <h2>How Kivi summarised it</h2>
            <div className="card">
              {episode.summary}
              <div className="meta" style={{ marginTop: 6 }}>
                salience {episode.salience.toFixed(2)}
              </div>
            </div>
          </>
        )}

        <h2>What it learned here</h2>
        {memories.length === 0 ? (
          <p className="meta">Nothing durable. The recording is kept and stays searchable.</p>
        ) : (
          memories.map((item) => (
            <div className="card interactive" key={item.id + item.kind} onClick={() => setMemory(item.id)}>
              <div className="row between">
                <span className={`chip ${item.type}`}>{item.type}</span>
                <span className="chip">{item.kind}</span>
              </div>
              <div style={{ marginTop: 7 }}>{item.statement}</div>
              <div className="quote-block" style={{ marginTop: 7 }}>
                “{item.quote}”
              </div>
            </div>
          ))
        )}

        {ignored.length > 0 && (
          <>
            <h2>What it refused to keep</h2>
            {ignored.map((item) => (
              <div className="card" key={item.id}>
                <span className="chip withheld">{item.reason_code.replace(/_/g, ' ')}</span>
                <div className="meta" style={{ marginTop: 6 }}>
                  {item.reason}
                </div>
              </div>
            ))}
          </>
        )}

        {data.trace && (
          <>
            <div className="divider" />
            <button className="btn quiet" onClick={() => setTrace(data.trace!.id)}>
              See how it decided
            </button>
          </>
        )}
      </Panel>
      {memory && <MemoryPanel id={memory} onClose={() => setMemory(null)} onForget={onChanged} />}
      {trace && <TracePanel id={trace} onClose={() => setTrace(null)} />}
    </>
  );
}
