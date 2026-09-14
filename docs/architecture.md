# Architecture

One Node process, one SQLite file, one React app. The shape is deliberately dull
so that the interesting decisions are all in the memory layer rather than in the
plumbing.

```
speech  ──▶  dictation  ──▶  extraction  ──▶  POLICY GATE  ──▶  memory + provenance
(replayed)      │                                  │                    │
                │                                  ▼                    │
                │                             rejections                │
                │                          (reason recorded)            │
                ▼                                                       ▼
            episode  ─────────────────────────────────────────▶  hybrid retrieval
                                                                        │
   Hey Kivi  ──▶  plan  ──▶  retrieve  ──▶  ground?  ──┬── yes ──▶  tools ──▶ answer + citations
                                                       └── no  ──▶  "I don't have that"
                             every step writes a trace ───────────────────────┘
```

## Processes and files

| Path | What lives there |
| --- | --- |
| `packages/server/src/db` | Schema, migrations, the thin SQLite wrapper. |
| `packages/server/src/memory` | Extraction pipeline, the policy gate, the memory store. |
| `packages/server/src/retrieval` | Embeddings, hybrid search, term coverage, the planner. |
| `packages/server/src/agent` | Hey Kivi loop, the five tools, memory-driven rewriting. |
| `packages/server/src/llm` | Provider interface, Anthropic provider, offline provider. |
| `packages/server/src/trace` | The decision trace and the metrics derived from it. |
| `packages/web/src` | The interface. |
| `corpus/` | Deterministic generator and the committed 500-record corpus. |
| `eval/` | Graded cases, the runner, generated results. |

## Storage

SQLite through Node 22's built-in `node:sqlite`. The choice is about the review
path more than the technology: no native compilation, no prebuilt binary
download, no service to start. A single user's lifetime of dictation is tens of
megabytes, which is comfortably inside what an embedded database handles well,
and the whole store is one file a reviewer can open with `sqlite3`.

Five migrations, applied in order and recorded with a checksum. See
[`memory-model.md`](memory-model.md) for the tables and why each exists.

## Retrieval

Four signals, fused with reciprocal rank fusion:

| Signal | Source | Why it is there |
| --- | --- | --- |
| lexical | SQLite FTS5, BM25 over formatted text and raw ASR | Exact words, including the misheard spelling |
| semantic | local hashed n-gram embedding, cosine | Paraphrase and near-miss spellings |
| temporal | distance from the window the planner extracted | "around 5PM yesterday" is a constraint, not decoration |
| prior | memory confidence × evidence count, or episode salience | A belief said five times outranks one said once |

RRF rather than a weighted sum because the signals disagree constantly on noisy
transcripts, and rank fusion degrades gracefully when one of them is nonsense.
Every component score travels with the result and lands in the trace.

The embedding is local and deterministic — hashed word unigrams, bigrams and
character 4-grams, L2-normalised, 384 dimensions. It is weaker than a trained
encoder at pure semantics and stronger at the noisy-spelling matching this
corpus is full of. `EmbeddingProvider` is the seam: a hosted model drops in
without touching retrieval.

### Grounding, which is the part that matters

Fused rank says "this was the best of what I had". It never says "this answers
the question". A grounding test built on the fused score alone will return the
five most popular memories, with total confidence, for a question about an
office lease in Zurich.

So three conditions must all hold before Hey Kivi is allowed to speak:

1. something ranked at all;
2. the top result has lexical or semantic support, not just recency;
3. **some single retrieved item actually contains the rare words of the
   question**, weighted by inverse document frequency over this user's own
   corpus.

The third is the one that does the work. A name the person has never said
carries maximum weight, so a question containing one cannot be answered by
anything — which is exactly right. Coverage is computed per candidate and
maximised, not summed across the result set: an answer needs one source that
contains the question, not a pile of sources that between them mention each word
once.

Implementation: `retrieval/coverage.ts`, `assessGrounding` in
`retrieval/search.ts`.

## The Hey Kivi loop

At most four steps. Memory reaches the model through exactly two channels:

- a short standing brief of active, high-confidence **preferences** — the things
  that shape *how* Kivi should write for this person;
- **tool results** — the things that answer *what* was asked.

Nothing else from the database is ever pasted into the prompt. A person's whole
history is not context; it is a searchable store, and that distinction is what
keeps every answer citable.

Five tools, chosen because the use cases need them and for no other reason:

| Tool | Use case |
| --- | --- |
| `recall` | Answer a question from history, or abstain |
| `search_dictations` | Find a specific recording by time, app and topic |
| `rewrite_dictation` | Reuse past text, applying remembered preferences |
| `list_commitments` | What the person said out loud they would do |
| `update_memory` | Correct or forget, from inside the conversation |

A question that falls in a screened category (health, money, politics, faith,
relationships, credentials) is refused *before* retrieval runs. Kivi never
formed a memory from that material, so searching could only surface the person's
own raw words back at them dressed as an answer.

## Model providers

`LlmProvider` has two methods: `extract` (one dictation in, an episode and
memory candidates out) and `complete` (one step of the agent loop, with tool
calling).

**`anthropic`** is the first-class implementation: strict tool use for
extraction, prompt caching on the system prompt, a cheap model for the
500-record ingestion pass and the main model for conversation, real token and
cost accounting. It drops any candidate whose quote does not actually appear in
the dictation — a citation on an invented claim is worse than no citation.

**`local`** is a deterministic, network-free implementation of the same
interface: pattern rules for extraction, a small policy for tool selection, and
answers assembled from tool payloads. It exists so the declared review path
needs no credentials. Everything around it — storage, the policy gate,
retrieval, provenance, abstention, traces — is identical under both providers,
which is the point.

The active provider is shown in the app, stamped into every trace, and recorded
in every evaluation result.

## Traces

Every ingestion and every Hey Kivi turn writes a row to `traces`: the plan, each
candidate considered with all four component scores, what was included, what was
excluded and why, the tools called with arguments and timings, per-stage
latency, tokens and cost.

This is not logging. It is the artefact that makes "memory affected this result"
— or "it did not, and here is why" — checkable without re-running anything. It
is reachable from the normal interface rather than a developer console, because
"how do you know that?" is a user's question before it is an engineer's; the
engineer's version is the same object, one toggle further down.
