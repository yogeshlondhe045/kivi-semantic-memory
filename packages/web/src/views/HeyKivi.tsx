import { useEffect, useRef, useState } from 'react';
import { MemoryPanel, TracePanel } from '../components/Panels';
import { api, type AskResult, type Citation, type ReviewItem } from '../lib/api';
import { when } from '../lib/format';

interface Turn {
  role: 'user' | 'kivi';
  text: string;
  result?: AskResult;
}

const OPENERS = [
  'Find the dictation I did around 5PM yesterday in Slack and polish it for the meeting I’m walking into',
  'Who is the PM on Meridian?',
  'What did I promise Priya?',
  'How do I like Slack messages written?',
  'What did I decide about the Zurich office lease?',
];

export function HeyKivi({ onChanged }: { onChanged: () => void }) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState('');
  const [busy, setBusy] = useState(false);
  const [conversationId, setConversationId] = useState<string | undefined>();
  const [review, setReview] = useState<ReviewItem | null>(null);
  const [openMemory, setOpenMemory] = useState<string | null>(null);
  const [openTrace, setOpenTrace] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  const loadReview = () => api.review().then((r) => setReview(r.items[0] ?? null));
  useEffect(() => {
    loadReview();
  }, []);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [turns, busy]);

  async function send(question: string) {
    const text = question.trim();
    if (!text || busy) return;
    setInput('');
    setTurns((prev) => [...prev, { role: 'user', text }]);
    setBusy(true);
    try {
      const result = await api.ask(text, conversationId);
      setConversationId(result.conversation_id);
      setTurns((prev) => [...prev, { role: 'kivi', text: result.text, result }]);
      onChanged();
      loadReview();
    } catch (err) {
      setTurns((prev) => [...prev, { role: 'kivi', text: `Something broke: ${(err as Error).message}` }]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="page kivi">
      {turns.length === 0 ? (
        <div className="page-head">
          <h1>Hey Kivi</h1>
          <p className="lede">
            Ask about anything you’ve dictated. Kivi answers from your own recordings and shows you which
            ones — and says so plainly when your history doesn’t contain the answer.
          </p>
        </div>
      ) : null}

      {review && (
        <div className="review-card" style={{ marginBottom: 22 }}>
          <div className="meta" style={{ marginBottom: 3 }}>
            One thing Kivi isn’t sure about
          </div>
          <div className="question">{review.question}</div>
          <div className="row" style={{ marginTop: 11 }}>
            {review.options.map((option) => (
              <button
                key={option.id}
                className={option.effect === 'activate' ? 'btn primary' : 'btn'}
                onClick={async () => {
                  await api.resolveReview(review.id, option.id);
                  await loadReview();
                  onChanged();
                }}
              >
                {option.label}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="thread">
        {turns.map((turn, i) =>
          turn.role === 'user' ? (
            <div className="turn-user" key={i}>
              {turn.text}
            </div>
          ) : (
            <div className={`turn-kivi ${turn.result?.outcome === 'abstained' ? 'abstained' : ''}`} key={i}>
              <div className="answer">{turn.text}</div>
              {turn.result && (
                <>
                  {turn.result.citations.length > 0 && (
                    <div className="sources">
                      {turn.result.citations.map((citation) => (
                        <SourceRow
                          key={`${citation.kind}:${citation.id}`}
                          citation={citation}
                          onOpen={() => citation.kind === 'memory' && setOpenMemory(citation.id)}
                        />
                      ))}
                    </div>
                  )}
                  <div className="row" style={{ marginTop: 10, gap: 8 }}>
                    <button className="btn quiet" onClick={() => setOpenTrace(turn.result!.trace_id)}>
                      Why this answer
                    </button>
                    <span className="meta">
                      {turn.result.duration_ms} ms · {turn.result.tools_used.join(', ') || 'no tools'} ·{' '}
                      {turn.result.outcome}
                    </span>
                  </div>
                </>
              )}
            </div>
          ),
        )}
        {busy && (
          <div className="thinking">
            <i />
            <i />
            <i />
          </div>
        )}
        <div ref={endRef} />
      </div>

      <div className="composer">
        <div className="composer-inner">
          <textarea
            value={input}
            rows={1}
            placeholder="Hey Kivi…"
            onChange={(e) => {
              setInput(e.target.value);
              e.target.style.height = 'auto';
              e.target.style.height = `${Math.min(160, e.target.scrollHeight)}px`;
            }}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                send(input);
              }
            }}
          />
          <button className="mic" disabled={busy || !input.trim()} onClick={() => send(input)} title="Ask">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
              <path d="M12 19V5M5 12l7-7 7 7" />
            </svg>
          </button>
        </div>
        {turns.length === 0 && (
          <div className="suggestions">
            {OPENERS.map((opener) => (
              <button className="suggestion" key={opener} onClick={() => send(opener)}>
                {opener}
              </button>
            ))}
          </div>
        )}
      </div>

      {openMemory && <MemoryPanel id={openMemory} onClose={() => setOpenMemory(null)} onForget={onChanged} />}
      {openTrace && <TracePanel id={openTrace} onClose={() => setOpenTrace(null)} />}
    </div>
  );
}

function SourceRow({ citation, onOpen }: { citation: Citation; onOpen: () => void }) {
  return (
    <button className="source" onClick={onOpen}>
      <span className={`chip ${citation.kind === 'memory' ? 'ok' : ''}`}>
        {citation.kind === 'memory' ? 'memory' : 'dictation'}
      </span>
      <span className="quote">{citation.quote ? `“${citation.quote}”` : citation.label}</span>
      {citation.captured_at && (
        <span className="meta" style={{ whiteSpace: 'nowrap' }}>
          {when(citation.captured_at)}
          {citation.app ? ` · ${citation.app}` : ''}
        </span>
      )}
    </button>
  );
}
