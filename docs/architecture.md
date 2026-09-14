# Architecture

Status: Phase 2. The schema in this document is implemented and migrated; the
pipelines are specified here and implemented in Phases 3–5.

> **Scope note.** `PRODUCT_POSITION.md` and `PRODUCT_VISION.md` are not yet in
> the repository, and the assignment forbids AI authorship of them. Everything
> below is therefore derived from the *assignment's* constraints and the shape
> of its data, never from an invented product position. Decisions that genuinely
> require the Part One documents are parked in
> [`open-product-decisions.md`](./open-product-decisions.md) with a provisional
> default, so implementation proceeds without pre-empting them.

---

## 1. What the system is

A person dictates all day. Kivi writes what they say. Across hundreds of those
dictations there is durable understanding — preferences, facts, episodes — that
could make *Hey Kivi* genuinely useful when the person later asks for help.

The whole system exists to do three things well:

1. decide what, out of hundreds of dictations, is worth believing;
2. retrieve the right belief when a request arrives, and know when it has none;
3. be able to show its work, for both the user and an engineer.

Everything else is support.

## 2. Components

Four processes. No queue, no cache, no worker pool — ingestion is a batched
synchronous job with a progress table, which is sufficient at 500 records and
removes an entire class of "did the worker die?" failure from the review path.

```
┌──────────────────────────────────────────────────┐
│  web/   Next.js 15 · TypeScript · Tailwind       │   the normal-user surface
└───────────────────────┬──────────────────────────┘
                        │ REST / JSON
┌───────────────────────┴──────────────────────────┐
│  api/   FastAPI · Python 3.11 · SQLAlchemy 2.0   │
│                                                  │
│   ingestion ─ memory ─ retrieval ─ Hey Kivi loop │
│                        │                         │
│   providers ─ chat: offline│anthropic│openai│…   │
│               embed: offline│openai│sarvam       │
└───────────────────────┬──────────────────────────┘
                        │
┌───────────────────────┴──────────────────────────┐
│  Postgres 16 + pgvector 0.6                      │
│  HNSW (cosine) · tsvector/GIN · JSONB            │
└──────────────────────────────────────────────────┘
```

**One database, not three.** Vectors, full-text, relational state and the audit
trail all live in Postgres. A dedicated vector store would add an operational
component and a consistency problem (memory rows and their vectors drifting
apart) in exchange for performance that 500 records do not need. See
[ADR-0007](./adr/0007-single-postgres.md).

**The provider layer is one chokepoint.** Every model call returns a
`ModelUsage` alongside its payload, so token counts, latency and cost estimates
are captured structurally at the call site instead of being reconstructed later.
Chat and embeddings are configured *independently* because vendors do not sell
them as a pair — the Claude API has no embeddings endpoint at all
([ADR-0003](./adr/0003-provider-layer.md)).

---

## 3. The four anchor decisions

These are the load-bearing ones. Each falls out of the assignment's data shape,
so none of them pre-empts the product position.

### 3.1 Dictation is mostly composition, not assertion

A dictation corpus is overwhelmingly the user writing *to somebody else*. When
someone dictates *"Hi Sarah, the deadline is Tuesday"* into Slack, the deadline
is a claim addressed to Sarah — it is not evidence that the user believes,
prefers, or should be reminded of anything.

Conflating *what the user said* with *what the user believes* is the single
largest failure mode available to this system, and a naive
transcript-to-embedding pipeline commits it on almost every record. Admission
therefore separates the two explicitly, and the rejection is recorded under
`dictation_content_not_assertion`.

### 3.2 The model proposes; deterministic policy decides

Extraction is a model call. **Admission, confidence, status, conflict
resolution and abstention are computed in code.** A model may suggest that
something is worth remembering; it does not get to set its own confidence or
promote its own belief to active.

This is what makes behaviour reproducible, sweepable and testable, and it is the
concrete form of the assignment's warning against forcing an answer because a
model produced one. See [ADR-0002](./adr/0002-policy-decides.md).

### 3.3 Raw-vs-formatted divergence is a confidence signal

The assignment supplies *both* the raw ASR and the LLM-formatted text. A system
that reads only the formatted text throws away the only available check on it.

Extraction reads the formatted text (it is cleaner), but an entity or claim that
appears in only one of the two is weaker evidence than one present in both. That
ratio is computed once at ingestion as `transcript.asr_agreement` and feeds the
confidence function — so ASR noise degrades confidence instead of silently
becoming a false memory.

### 3.4 Log why memory was *not* used

The assignment asks how an engineer inspects why memory did **or did not**
affect a result. Most systems log only what was retrieved and used, which can
explain a good answer but never a miss.

Every candidate that retrieval considered is persisted in `retrieval_hit` with
`included_in_context` and, when false, an `exclusion_reason` code. A miss is
then a query, not an investigation. See
[ADR-0006](./adr/0006-log-exclusions.md).

---

## 4. Flows

### 4.1 Ingestion

```
record → normalize → salience gate → extract candidates → ADMISSION POLICY
       → persist memory + version + evidence (+ links) → embed → retrievable
```

| Stage | What it does | Why it is separate |
|---|---|---|
| Normalize | Resolve relative time against `occurred_at`, detect language, extract entity aliases, compute `asr_agreement`, hash content | Deterministic and cheap; must not depend on a model being available |
| **Salience gate** | Rule + heuristic triage that drops obviously memory-free records **before any LLM call** | Makes "deliberately ignores" measurable, and is the main lever on ingestion cost |
| Extract | One structured model call per surviving record, returning typed candidates with spans | Typed schema + one repair attempt; a second failure is recorded, never defaulted |
| **Admit** | Deterministic policy: create / reinforce / refine / supersede / reject / defer | §3.2 — the decision point of the whole system |
| Persist | `memory` + `memory_version` + `memory_evidence` (+ `memory_link`) | Provenance written in the same transaction as the belief |
| Embed | `memory_embedding`, and `transcript_embedding` for episodic recall | Separate table; failure degrades to lexical retrieval rather than losing the memory |

Idempotency: `(user_id, external_id)` is unique and `content_hash` is indexed.
Re-importing a corpus must not inflate evidence counts, because evidence count
drives confidence — a duplicate import would otherwise manufacture
corroboration out of nothing.

### 4.2 Memory creation, update, conflict, removal

| Event | Outcome | Recorded as |
|---|---|---|
| First explicit durable statement | `proposed` → `active` if explicitness is high, else stays `proposed` | version `created` |
| Same claim, new transcript | `evidence_count` and `independent_source_count` rise; confidence recomputed | version `reinforced` |
| Same claim, sharper wording | statement/attributes updated | version `refined` |
| Later statement contradicts | new memory `active`, old → `superseded`; edge `supersedes` | version `superseded` on the old |
| Explicit user correction | as above, reason `explicit_correction`, confidence penalty on the old | version `superseded` |
| `valid_to` passes | → `expired` by sweep | version `expired` |
| User removes | → `retracted`, dropped from retrieval, audit kept | version `user_deleted` |
| User demands erasure | → `purged`: statement nulled, embeddings deleted, audit row kept | version `user_deleted` |

`proposed` is the honest resting place for a single weak mention: the system has
noticed something but has not earned the right to act on it. Promotion to
`active` requires **either** an explicit first-person durable assertion **or**
corroboration from ≥2 independent transcripts.

Confidence is a deterministic function of independent source count, explicitness,
`asr_agreement`, volatility-aware recency decay, and a contradiction penalty —
never a number the model reported about itself.

### 4.3 Retrieval

Deliberately **not** `question → vector search → LLM`.

```
1. understand    → RetrievalPlan {intent, entities, time window, surface, kinds}
2. generate      → memory-vector ∪ memory-lexical ∪ structured
                   ∪ transcript-vector ∪ transcript-lexical      (parallel)
3. fuse          → reciprocal rank fusion
4. filter        → status · visibility · validity window · confidence floor
                   (every drop writes an exclusion_reason)
5. reconcile     → contradictions surfaced, not silently resolved
6. hydrate       → evidence spans for survivors
7. SUFFICIENCY   → deterministic: does the surviving set cover what was asked?
8. answer        → generation constrained to cited ids → citation check
```

Two arms rather than one because vectors and exact tokens fail differently: a
vector search will cheerfully return "the Acme report" for a question about
Acme*tech*, while a lexical arm on `subject_key` will not. Episodic requests
("the dictation I did around 5 PM yesterday in Slack") are mostly a structured
metadata filter, and would be poorly served by either.

### 4.4 Abstention

Step 7 is the anti-hallucination mechanism, and it is **deterministic and runs
before generation**. The system does not ask a model to be humble; it checks
whether the retrieved set actually covers the entities and slots the request
needs, and if it does not, it abstains with a reason code — `no_evidence`,
`weak_evidence`, `stale_only`, `conflicting_evidence`, `out_of_scope`,
`needs_clarification`.

A second gate runs *after* generation: any claim in the answer without a
citation causes the answer to be downgraded to `uncited_generation` rather than
returned. Both gates write to `hey_kivi_request.abstention_reason`.

Conflicting evidence produces one short clarifying question, never a coin flip.

### 4.5 Hey Kivi tool loop

A small agentic loop, not an agent platform: plan → select tool → validate
arguments → execute against real state → record → respond. Every call writes a
`tool_call` row with arguments, result, status and latency, so a reviewer can
confirm the tool *ran* rather than being narrated by the model. Arguments that
fail validation are recorded as `rejected` and never executed.

*Which* tools exist is product-gated (decision D3) — implementing a tool set
before the use cases are fixed would be building features in search of a
justification.

### 4.6 Provenance

```
answer
  └→ cited_memory_ids           hey_kivi_request
      └→ memory                 status, confidence, validity
          └→ memory_version     what changed, when, and the reason code
              └→ memory_evidence  relation, span offsets, quote
                  └→ transcript   formatted_text + raw_asr + occurred_at
```

Every hop is a row and is traversable in SQL. `directly observed` vs `derived`
is a property of the memory (`memory_link.derived_from`), so the interface can
say which it is instead of implying observation for an inference. This chain is
exercised by `api/tests/test_schema.py::test_provenance_chain_resolves_to_raw_speech`.

### 4.7 Failure handling

Every failure produces a row and stays visible. Nothing is swallowed.

| Failure | Behaviour | Visible as |
|---|---|---|
| No API key / provider down | Offline provider serves the request; UI shows a degraded banner | `ModelUsage.degraded`, `hey_kivi_request.degraded` |
| Extractor returns invalid JSON | Validate → one repair attempt → record and stop | `repaired` flag; `extraction_failed` |
| Embedding fails | Memory persists and is served lexically; retried later | missing `memory_embedding` row |
| Retrieval finds nothing | Abstain before generation | `abstention_reason` |
| Answer cites nothing | Downgrade to abstention | `uncited_generation` |
| Tool errors | Plain-language message to the user | `tool_call.status = failed` |
| Partial import | Job ends `partial` with counts — never "succeeded" | `ingestion_job.status` |

### 4.8 Data lifecycle

Transcripts are retained; they are the evidence base and deleting them would
orphan every belief. Memories are versioned forever and are never hard-deleted
by the system. User deletion is a tombstone plus exclusion from retrieval;
`purge` additionally nulls the statement and drops embeddings, so "forget"
honestly means forget while the audit trail still records that something was
removed and when. Time-bounded memories expire by sweep against `valid_to`.

### 4.9 Evaluation

The runner drives the same code paths the product uses — there is no evaluation
back door. A failed case joins through `eval_result.request_id` to the request,
its retrieval events, the candidates that were dropped and why, the tool calls
and the evidence, so a failure is diagnosable from stored state without
re-running anything. `db_size_sample` records per-table size at named stages,
which is how database growth gets measured rather than asserted.

---

## 5. Tradeoffs taken

| Decision | Cost accepted | Why anyway |
|---|---|---|
| Synchronous batched ingestion | Won't scale past ~10⁴ records | 500 records; removes a whole failure class from the review path |
| One Postgres | No specialised ANN performance | Consistency and one-command setup matter more here |
| Deterministic admission policy | Less adaptive than an LLM judge | Reproducible, sweepable, testable — the assignment's core ask |
| Persisting dropped retrieval candidates | ~40 extra rows per request | Only way to explain why memory did *not* affect a result |
| Offline provider by default | Noticeably worse quality with no key | A reviewer can never be blocked; and it is labelled, not hidden |
| Fixed 1024-d embedding space | Providers must project to it | A fixed-width pgvector column can be HNSW-indexed; see ADR-0004 |

## 6. Known gaps at the end of Phase 2

1. **The Compose path is unverified in the build environment.** Docker Hub's
   blob CDN is blocked here, so no image can be pulled. The schema was instead
   verified against a natively installed Postgres 16 + pgvector 0.6. The Compose
   file is syntactically valid (`docker compose config`) but has not been run.
   This is the top reproducibility risk and must be closed before submission.
2. The `api` and `web` services are absent from Compose until Phases 3 and 5.
3. Eight product decisions remain open (`open-product-decisions.md`).
