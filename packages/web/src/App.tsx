import { useCallback, useEffect, useState } from 'react';
import { api, type Health } from './lib/api';
import { Dictations } from './views/Dictations';
import { HeyKivi } from './views/HeyKivi';
import { Ignored } from './views/Ignored';
import { Memory } from './views/Memory';
import { System } from './views/System';

type View = 'kivi' | 'dictations' | 'memory' | 'ignored' | 'system';

const NAV: { id: View; label: string }[] = [
  { id: 'kivi', label: 'Hey Kivi' },
  { id: 'dictations', label: 'Dictations' },
  { id: 'memory', label: 'Memory' },
  { id: 'ignored', label: 'Never kept' },
  { id: 'system', label: 'System' },
];

export function App() {
  const [view, setView] = useState<View>('kivi');
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    api
      .health()
      .then((h) => {
        setHealth(h);
        setError(null);
      })
      .catch((err: Error) => setError(err.message));
  }, []);

  useEffect(refresh, [refresh]);

  const counts: Partial<Record<View, { n: number; alert?: boolean }>> = health
    ? {
        dictations: { n: health.counts.dictations },
        memory: { n: health.counts.memories },
        ignored: { n: health.counts.ignored },
      }
    : {};

  return (
    <div className="shell">
      <nav className="rail">
        <div className="brand">
          <div className="brand-mark">K</div>
          <div>
            <div className="brand-name">Kivi</div>
            <div className="brand-sub">memory</div>
          </div>
        </div>

        {NAV.map((item) => (
          <button
            key={item.id}
            className="nav-item"
            aria-current={view === item.id}
            onClick={() => setView(item.id)}
          >
            <span>{item.label}</span>
            {counts[item.id] ? <span className="count">{counts[item.id]!.n}</span> : null}
          </button>
        ))}

        {health && health.counts.open_reviews > 0 && (
          <button className="nav-item" onClick={() => setView('memory')} style={{ marginTop: 10 }}>
            <span style={{ color: 'var(--amber)' }}>Needs you</span>
            <span className="count alert">{health.counts.open_reviews}</span>
          </button>
        )}

        <div className="rail-foot">
          {health?.user && <div style={{ color: 'var(--muted)' }}>{health.user.display_name}</div>}
          <div className="provider-chip" style={{ marginTop: 6 }}>
            <span className={`dot ${health?.provider.startsWith('anthropic') ? '' : 'offline'}`} />
            {health?.provider ?? 'connecting…'}
          </div>
        </div>
      </nav>

      <main className="main">
        {error ? (
          <div className="page">
            <h1>The backend isn’t answering</h1>
            <p className="lede">{error}</p>
            <p className="lede">
              Start it with <code className="mono">npm start</code>, or check RUN.md for the full sequence.
            </p>
          </div>
        ) : view === 'kivi' ? (
          <HeyKivi onChanged={refresh} />
        ) : view === 'dictations' ? (
          <Dictations onChanged={refresh} />
        ) : view === 'memory' ? (
          <Memory onChanged={refresh} />
        ) : view === 'ignored' ? (
          <Ignored />
        ) : (
          <System />
        )}
      </main>
    </div>
  );
}
