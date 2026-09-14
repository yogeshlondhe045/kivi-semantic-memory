# Kivi — semantic memory

**A ledger of what you said, not a model of who you are.**

Kivi's memory keeps what you stated or repeated, stores the sentence that proves
it, and shows you both. It never infers mood, health, politics, or how you are
doing. When your history does not hold an answer, Hey Kivi says so rather than
producing one.

→ **[RUN.md](RUN.md)** — the declared review method, and every command.
→ [Positioning statement](docs/positioning.md) · [Product vision](docs/vision.md)

```bash
npm install && cp .env.example .env && npm run setup && npm start
# http://localhost:8787   — no credentials required
```

---

## What it does

Four capabilities and five tools. Narrow on purpose: a memory system can learn
ten things while only three of them create value, and the other seven are how
you lose someone's trust.

**1 · Stop getting your vocabulary wrong.** Names, projects and tools are learned
from how you have used them, together with the spellings the recogniser produces
for each. Those canonical spellings are applied while you dictate.

*This is the only thing semantic memory changes about ordinary dictation.*
Correcting a name is verifiable and reversible; rewriting meaning is neither.
Everything else lives in Hey Kivi, where you have asked a question.

**2 · Act on your own history by describing it the way you remember it.**
*"Find the dictation I did around 5PM yesterday in Slack and polish it for the
meeting I'm walking into."* Kivi resolves the time expression to a window,
filters by app, picks one recording out of five hundred, and rewrites it using
what it knows about how you write — telling you which memory changed which part.
→ `search_dictations`, `rewrite_dictation`

**3 · Answer questions about what you have said, with sources.** Who owns this
now. What we decided about deploys. Every answer cites the memories and
recordings behind it, and every citation opens onto the sentence you actually
said. → `recall`

**4 · Surface what you said you would do.** Commitments, with the deadlines you
spoke, filtered by who they name. → `list_commitments`

And one thing that is not a capability so much as a condition of the others:
**you can correct or delete anything, from inside the conversation.** Deletion is
permanent, leaves a tombstone so the claim cannot be relearned, and stops the
recordings behind it from being used as grounds again. → `update_memory`

## What it refuses to do

| | |
| --- | --- |
| Infer a trait from a pattern | No mood detection, no sentiment, no "here's what we've noticed about you" |
| Keep sensitive material | Health, politics, religion, relationships, personal finances, other people's private circumstances, credentials — refused in code, not by prompt |
| Store passing states | "I'm tired", "the wifi is down" — true now, misleading in a week |
| Fill a gap | No answer without a source. An invented answer destroys the value of every true one |
| Make you an administrator | At most four open questions at a time, one card at a time, never a queue |

*Never kept* is a screen in the product, not a debug page. Every refusal is
recorded with its reason, against your own recordings.

## The interface

| Screen | What it is for |
| --- | --- |
| **Hey Kivi** | The conversation. Every answer carries its sources; **Why this answer** opens the full decision. |
| **Dictations** | The whole history, raw ASR beside formatted output. **Dictate something new** replays a transcript through the live pipeline so you can watch memory form — or be refused — immediately. |
| **Memory** | Everything Kivi knows, grouped, each line traceable to the recording it came from. **Show what changed** reveals superseded beliefs. Forgetting is one click and a confirmation. |
| **Never kept** | Every refusal, with its reason. |
| **System** | Latency, database growth, model usage and cost — measured from real traces. |

## Architecture, briefly

```
speech  ──▶  dictation  ──▶  extraction  ──▶  POLICY GATE  ──▶  memory + provenance
(replayed)      │                                  │                    │
                │                                  ▼                    │
                │                             rejections                │
                ▼                          (reason recorded)            ▼
            episode  ─────────────────────────────────────────▶  hybrid retrieval
                                                                        │
   Hey Kivi  ──▶  plan  ──▶  retrieve  ──▶  ground?  ──┬── yes ──▶  tools ──▶ answer + citations
                                                       └── no  ──▶  "I don't have that"
                             every step writes a trace ───────────────────────┘
```

Node 22 · Fastify · SQLite via the built-in `node:sqlite` (FTS5, no native
build) · React + Vite · Anthropic, with a deterministic offline provider.

Four retrieval signals fused with reciprocal rank fusion: BM25 over formatted
text *and* raw ASR, a local hashed-n-gram embedding, distance from the time
window the planner extracted, and a prior from memory confidence and how often
it has been repeated.

Full detail: **[docs/architecture.md](docs/architecture.md)** ·
**[docs/memory-model.md](docs/memory-model.md)** · decision records in
[`docs/decisions/`](docs/decisions/).

### The part that matters most

Rank fusion always returns a top result. Its score says *"best of what I had"*,
never *"this answers the question"* — so the first version of this system,
asked about an office lease in Zurich, confidently returned the five
highest-ranked memories in the database.

Hey Kivi now speaks only when some **single retrieved item actually contains the
rare words of the question**, weighted by inverse document frequency over your
own corpus. A name you have never said carries maximum weight, so a question
containing one cannot be answered by anything. That is the correct behaviour and
it is the reason the rest of the numbers are worth reading.
→ [ADR 3](docs/decisions/0003-abstention-by-term-coverage.md)

## Results

From `npm run eval` on the committed 500-record corpus, offline provider.
Regenerate with one command; artefacts are in [`eval/results/`](eval/results/)
([report.html](eval/results/report.html) is the readable one).

| | |
| --- | --- |
| Graded Hey Kivi cases | **21 / 21** |
| Ingestion checks over all 500 records | **8 / 8** |
| Documented limits exercised | 1 / 4 reached — **3 fail on purpose**, see below |
| Abstention cases | 6 / 6 |
| Hey Kivi latency | p50 **26 ms**, p95 59 ms, max 65 ms |
| Ingestion latency | p50 **2 ms** per dictation, p95 9 ms · 500 records in 1.3 s |
| Database | **2.9 MB** for 500 dictations — 5.8 KB each |
| Model calls / cost | 0 / $0.00 on the offline provider |

**What it learned from 500 dictations:** 52 memories created, 773 reinforced,
5 superseded, 3 held for confirmation. **55 candidates refused** — 24 sensitive,
18 with nothing durable in them, 13 passing states.

**Memories grow far more slowly than recordings**, which is the whole design
showing up in a number:

| dictations | 100 | 200 | 300 | 400 | 500 |
| --- | --- | --- | --- | --- | --- |
| active memories | 23 | 36 | 44 | 51 | 55 |
| database | 0.8 MB | 1.3 MB | 1.8 MB | 2.2 MB | 2.6 MB |

Fifty-five memories from five hundred recordings. A system that kept everything
would be a search index wearing a memory's clothes.

## Limitations

Honest ones. Three of these are run as cases on every evaluation so the gap
stays measured rather than remembered.

**Measured, and failing today:**

- **No multi-hop joins.** *"Which deploy tool does the project Rahul runs use?"*
  needs two memories joined. Each is scored against the question independently,
  neither scores well, and Kivi abstains. Abstaining is the safe failure, but it
  is still a failure.
- **No aggregation.** *"How many times have I mentioned Meridian?"* — nothing in
  the tool surface counts over history.
- **No temporal comparison.** Both facts are timestamped; nothing orders them.

**Known and unmeasured:**

- **The offline extractor is narrow.** It finds what its patterns describe and
  nothing else — roughly a dozen sentence shapes. The Anthropic provider is
  substantially better at extraction and is what the product would ship with;
  the offline provider exists so the review path needs no credentials.
- **The local embedding is weak at pure semantics.** Hashed n-grams beat a
  trained encoder at matching misheard spellings and lose to it at paraphrase.
  `EmbeddingProvider` is the seam for swapping it.
- **Retrieval is brute-force over the candidate pool.** Fine at 500 records and
  at the tens of thousands a real user would accumulate; it would need an ANN
  index well before a million.
- **The sensitive-category screen is regex-based**, so it occasionally refuses
  something harmless. That is the correct direction to be wrong in, but it is a
  blunt instrument compared with a classifier.
- **Single user.** The schema is scoped by `user_id` throughout, but there is no
  auth, no tenancy and no access control. It is a demonstration, not a service.
- **Hinglish is handled as text, not linguistically.** Code-switched dictations
  are stored, searched and retrieved, but nothing understands the Hindi.

## Reproducing everything

```bash
npm run check                 # typecheck + 21 unit tests + full evaluation
npm run corpus:generate       # regenerate the corpus (deterministic)
npm run eval                  # rebuild eval/results/ from scratch
```

The corpus is a single synthetic user with ~90 days of history, generated by a
seeded script and committed. It is not a data-generation exercise: it contains
the specific situations this system claims to handle — one name misheard several
ways, a fact split across dictations weeks apart, a preference repeated until it
is worth trusting and later reversed, commitments with and without deadlines,
sensitive material that must produce nothing, and topics nobody ever dictated
about so that abstention has something to fail on.
[`corpus/generate.ts`](corpus/generate.ts) documents what each part is for.

To run it against a different corpus, see
[docs/corpus-format.md](docs/corpus-format.md) — the importer auto-detects the
common field names, prints the mapping it chose before writing anything, and
takes overrides without a code change.

## AI use

Stated plainly, because the assignment asks.

**Part One** — the positioning statement and the vision document. Written by me,
from using Kivi and looking at how other products handle memory. The position
they argue is the one the code implements: it is why there is no sentiment
analysis here, why the ignore policy is a gate rather than a prompt, and why
abstention is tested before accuracy.

**Part Two** — built with an AI coding assistant, in the agentic style the role
description describes. I made the architectural decisions and reviewed
everything that shipped; the assistant did a large share of the typing. The
decisions I would defend in an interview are recorded in
[`docs/decisions/`](docs/decisions/), including the ones I got wrong first —
ADR 3 exists because my initial abstention test was scored on retrieval rank and
was, in a way that a demo would never have surfaced, completely broken.

**Inside the product**, an LLM does two jobs: reading each dictation for things
worth remembering, and running the Hey Kivi conversation. Both go through
`LlmProvider`, which has an Anthropic implementation and a deterministic offline
one. The policy gate, storage, retrieval, provenance, abstention and traces are
identical under both — only extraction and answer composition differ. Whichever
is running is shown in the app sidebar, stamped into every trace, and recorded
in every evaluation artefact.

The corpus was generated by a seeded script rather than a language model, so it
is reproducible byte for byte and a committed evaluation result means something.

## Repository

```
docs/           positioning · vision · architecture · memory model · corpus format · ADRs
packages/
  server/       db + migrations · memory (extract, policy, store) · retrieval · agent · llm · trace
  web/          Hey Kivi · Dictations · Memory · Never kept · System
corpus/         deterministic generator + the committed 500 records
eval/           graded cases · runner · generated results
scripts/        preflight · seed · import · reset · dev
tests/          21 unit tests over the policy gate, time parsing, lifecycle and import mapping
```
