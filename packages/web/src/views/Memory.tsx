import { useEffect, useState } from 'react';
import { MemoryPanel } from '../components/Panels';
import { api, type LexiconRow, type MemoryRow, type MemoryStats, type ReviewItem } from '../lib/api';
import { pct, relative, when } from '../lib/format';

const TYPES = [
  { id: '', label: 'Everything' },
  { id: 'fact', label: 'Facts' },
  { id: 'preference', label: 'Preferences' },
  { id: 'commitment', label: 'Commitments' },
  { id: 'entity', label: 'Names' },
] as const;

const BLURB: Record<string, string> = {
  fact: 'Things about your working world that you stated outright.',
  preference: 'How you have said you want writing and work done.',
  commitment: 'Things you said out loud that you would do.',
  entity: 'Names you use, and the spellings the recogniser gets wrong. These are the only memories that reach ordinary dictation.',
};

export function Memory({ onChanged }: { onChanged: () => void }) {
  const [items, setItems] = useState<MemoryRow[]>([]);
  const [stats, setStats] = useState<MemoryStats | null>(null);
  const [reviews, setReviews] = useState<ReviewItem[]>([]);
  const [lexicon, setLexicon] = useState<LexiconRow[]>([]);
  const [type, setType] = useState('');
  const [status, setStatus] = useState('active');
  const [q, setQ] = useState('');
  const [open, setOpen] = useState<string | null>(null);

  const load = () => {
    api.memories({ type: type || undefined, status, q }).then((data) => {
      setItems(data.items);
      setStats(data.stats);
    });
    api.review().then((r) => setReviews(r.items));
    api.lexicon().then((r) => setLexicon(r.items));
  };

  useEffect(load, [type, status, q]);

  return (
    <div className="page wide">
      <div className="page-head">
        <h1>What Kivi knows</h1>
        <p className="lede">
          Everything here came from something you said, and every line can be traced back to the recording it
          came from. Open any of them to see the sentence. Remove any of them for good.
        </p>
      </div>

      {reviews.length > 0 && (
        <div className="review-card" style={{ marginBottom: 22 }}>
          <div className="meta" style={{ marginBottom: 3 }}>
            {reviews.length} thing{reviews.length === 1 ? '' : 's'} Kivi isn’t sure about
          </div>
          <div className="question">{reviews[0]!.question}</div>
          <div className="row" style={{ marginTop: 11 }}>
            {reviews[0]!.options.map((option) => (
              <button
                key={option.id}
                className={option.effect === 'activate' ? 'btn primary' : 'btn'}
                onClick={async () => {
                  await api.resolveReview(reviews[0]!.id, option.id);
                  load();
                  onChanged();
                }}
              >
                {option.label}
              </button>
            ))}
          </div>
          <p className="meta" style={{ marginBottom: 0, marginTop: 10 }}>
            Kivi asks one at a time. Until you answer, it does not act on this.
          </p>
        </div>
      )}

      <div className="filters">
        <input className="search" placeholder="Search memories…" value={q} onChange={(e) => setQ(e.target.value)} />
        {TYPES.map((option) => (
          <button
            key={option.id || 'all'}
            className="btn"
            style={type === option.id ? { borderColor: 'var(--sage-dim)', color: 'var(--sage)' } : undefined}
            onClick={() => setType(option.id)}
          >
            {option.label}
            {stats ? ` ${countFor(stats, option.id, status)}` : ''}
          </button>
        ))}
        <div className="spacer" />
        <button className="btn" onClick={() => setStatus(status === 'active' ? 'superseded' : 'active')}>
          {status === 'active' ? 'Show what changed' : 'Back to current'}
        </button>
      </div>

      {type && BLURB[type] && (
        <p className="lede" style={{ fontSize: 13.5, marginBottom: 14 }}>
          {BLURB[type]}
        </p>
      )}

      {status === 'superseded' && (
        <p className="lede" style={{ fontSize: 13.5, marginBottom: 14 }}>
          These were true and are not any more. Kivi keeps them so you can see what changed and when, rather
          than quietly rewriting history.
        </p>
      )}

      {items.length === 0 ? (
        <div className="empty">Nothing here yet.</div>
      ) : (
        items.map((memory) => (
          <div className="card interactive" key={memory.id} onClick={() => setOpen(memory.id)}>
            <div className="row between">
              <div className="row" style={{ gap: 8 }}>
                <span className={`chip ${memory.type}`}>{memory.type}</span>
                {memory.status === 'pending_confirmation' && <span className="chip preference">unconfirmed</span>}
                {memory.origin === 'user_edit' && <span className="chip ok">you confirmed this</span>}
              </div>
              <span className="meta">{relative(memory.last_seen_at)}</span>
            </div>
            <div style={{ marginTop: 8, fontSize: 15.5 }} className={status === 'superseded' ? 'strike' : ''}>
              {memory.statement}
            </div>
            <div className="meta" style={{ marginTop: 5 }}>
              {memory.evidence} mention{memory.evidence === 1 ? '' : 's'} · confidence {pct(memory.confidence)} ·
              first heard {when(memory.first_seen_at)}
              {memory.detail?.due_at ? ` · due ${when(memory.detail.due_at as number)}` : ''}
            </div>
          </div>
        ))
      )}

      {type === 'entity' || type === '' ? (
        <>
          <h2>Spellings Kivi applies while you dictate</h2>
          <p className="lede" style={{ fontSize: 13.5 }}>
            This is the only thing semantic memory changes about ordinary dictation. It corrects names you have
            already used — it never alters what you said.
          </p>
          {lexicon.length === 0 ? (
            <div className="empty">No names learned yet.</div>
          ) : (
            <table>
              <thead>
                <tr>
                  <th>writes</th>
                  <th>when it hears</th>
                  <th style={{ width: 70 }}>times</th>
                </tr>
              </thead>
              <tbody>
                {lexicon.map((entry) => (
                  <tr key={entry.id}>
                    <td>
                      <strong>{entry.canonical}</strong>
                    </td>
                    <td className="mono" style={{ color: 'var(--muted)' }}>
                      {entry.variants.length ? entry.variants.join(', ') : '—'}
                    </td>
                    <td className="mono">{entry.uses}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      ) : null}

      {open && (
        <MemoryPanel
          id={open}
          onClose={() => setOpen(null)}
          onForget={() => {
            load();
            onChanged();
          }}
        />
      )}
    </div>
  );
}

function countFor(stats: MemoryStats, type: string, status: string): number {
  return stats.byType
    .filter((row) => row.status === status && (!type || row.type === type))
    .reduce((sum, row) => sum + row.n, 0);
}
