import { useEffect, useState } from 'react';
import { api, type IgnoredRow } from '../lib/api';
import { when } from '../lib/format';

const REASON_COPY: Record<string, { title: string; blurb: string }> = {
  sensitive_category: {
    title: 'Off limits',
    blurb:
      'Health, politics, religion, relationships, money, other people’s private circumstances, credentials. Kivi will never build a memory from these, whatever you say, and no prompt can talk it into doing so — the rule lives in code, not in an instruction to a model.',
  },
  transient: {
    title: 'True for an hour',
    blurb: 'Moods, traffic, a flaky connection. Accurate now, misleading in a week, so nothing is kept.',
  },
  trivial: {
    title: 'Nothing in it',
    blurb: '“Okay”, “testing one two”, acknowledgements. Recording these would only make the rest noisier.',
  },
  low_confidence: {
    title: 'Not sure enough',
    blurb: 'The extraction was too weak to act on. Below the floor Kivi drops it rather than guessing.',
  },
  third_party: {
    title: 'Not yours to keep',
    blurb: 'Private details about someone else.',
  },
  tombstoned: {
    title: 'You deleted this',
    blurb: 'You removed this memory before. Hearing it again does not bring it back.',
  },
  duplicate: { title: 'Already known', blurb: 'Kivi already had this.' },
  malformed: { title: 'Not a claim', blurb: 'Nothing coherent to record.' },
};

/**
 * "What it deliberately ignored" is a product surface, not a debug page.
 *
 * A memory system asks for an unusual amount of trust. The fastest way to earn
 * it is to be concrete about the things you refuse to keep — and to show that
 * the refusals actually happened, on this person's own recordings, with the
 * reason attached.
 */
export function Ignored() {
  const [items, setItems] = useState<IgnoredRow[]>([]);
  const [byReason, setByReason] = useState<{ reason_code: string; n: number }[]>([]);
  const [filter, setFilter] = useState('');

  useEffect(() => {
    api.ignored().then((data) => {
      setItems(data.items);
      setByReason(data.by_reason);
    });
  }, []);

  const shown = filter ? items.filter((item) => item.reason_code === filter) : items;

  return (
    <div className="page wide">
      <div className="page-head">
        <h1>What Kivi won’t keep</h1>
        <p className="lede">
          A memory system is defined as much by what it refuses to learn as by what it stores. Every refusal
          below is recorded with the reason, on your own recordings. Nothing here reached memory.
        </p>
      </div>

      <div className="stat-grid" style={{ marginBottom: 18 }}>
        {byReason.map((row) => (
          <button
            key={row.reason_code}
            className="stat"
            style={{
              textAlign: 'left',
              cursor: 'pointer',
              borderColor: filter === row.reason_code ? 'var(--sage-dim)' : undefined,
            }}
            onClick={() => setFilter(filter === row.reason_code ? '' : row.reason_code)}
          >
            <div className="value">{row.n}</div>
            <div className="label">{REASON_COPY[row.reason_code]?.title ?? row.reason_code}</div>
          </button>
        ))}
      </div>

      {byReason.length === 0 && (
        <div className="empty">
          Nothing has been refused yet. Seed the corpus or dictate something Kivi should not keep.
        </div>
      )}

      {filter && REASON_COPY[filter] && (
        <p className="lede" style={{ fontSize: 13.5, marginBottom: 16 }}>
          {REASON_COPY[filter]!.blurb}
        </p>
      )}

      {shown.slice(0, 60).map((item) => (
        <div className="card" key={item.id}>
          <div className="row between" style={{ marginBottom: 7 }}>
            <span className="chip withheld">{item.reason_code.replace(/_/g, ' ')}</span>
            <span className="meta">
              {item.captured_at ? when(item.captured_at) : ''}
              {item.app ? ` · ${item.app}` : ''}
            </span>
          </div>
          {item.formatted && <div className="quote-block">“{item.formatted}”</div>}
          <div className="meta" style={{ marginTop: 8 }}>
            {item.reason}
          </div>
        </div>
      ))}

      {shown.length > 60 && <p className="meta">…and {shown.length - 60} more.</p>}
    </div>
  );
}
