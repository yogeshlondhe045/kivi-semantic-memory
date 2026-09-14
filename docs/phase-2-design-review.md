# Phase 2 design review

The phase gate asks: *"Does the architecture naturally follow from that product
position?"* It cannot be answered as written, because the position documents are
absent. The answerable version is: **does this architecture follow from the
assignment's constraints, and does it avoid foreclosing product decisions that
have not been made?** That is what is reviewed below, along with what is weak.

---

## 1. Verification actually performed

Not "files were generated" — these commands were run.

| Check | Result |
|---|---|
| `alembic upgrade head` on an empty Postgres 16.13 + pgvector 0.6.0 | 20 tables, 19 enums, 32 FKs, 3 check constraints |
| `alembic downgrade base` | 0 tables **and 0 leftover enum types** |
| `upgrade head` again after downgrade | 20 tables — clean cycle |
| `pytest tests/` | 10 passed |
| Provenance traversal answer → memory → version → evidence → transcript → `raw_asr` | resolves; independent sources distinguishable by surface |
| Constraint enforcement (duplicate import, dual-target hit, confidence > 1, self-contradiction) | all four rejected by the database |
| HNSW index usable for ANN | `Index Scan using ix_memory_embedding_hnsw`, 0.73 ms top-5 over 2,000 vectors |
| Generated tsvector + GIN | `plainto_tsquery` match without application-side population |
| `docker compose config` | valid |
| `ruff check` | clean |

The downgrade/upgrade cycle **found a real bug**: `op.drop_table` leaves native
ENUM types behind, so the second `upgrade head` would have failed with "type
already exists". That is precisely the reset path a reviewer exercises. Testing
reset in Phase 2 rather than Phase 9 is why it was caught cheaply.

## 2. The top risk: the primary review method is unverified

Docker Hub's blob CDN is blocked in this build environment — every `docker pull`
fails with 403 at the CloudFront layer. **No container has been built or run.**
The schema was verified against a natively installed Postgres 16 + pgvector 0.6
instead.

So the declared primary review method rests on an unexecuted file. `docker
compose config` validates syntax and nothing more. This must be closed in an
environment with registry access before submission; until then the honest
statement is "Compose is written, not proven".

Partial mitigation already in place: the native path (`make native-setup`,
`make native-db`, `make migrate`) is fully verified and will be documented in
RUN.md as a first-class alternative rather than a footnote.

## 3. Weaknesses I am not hiding

**`decision_event` is duplicated state.** It denormalizes what the typed tables
already hold, and nothing structurally prevents it from drifting out of sync. It
is the one table I would cut under pressure. It survives because both the user's
"why?" panel and the evaluation report need a chronology, and unioning five
tables per render is worse. Phase 3 must write it through exactly one helper —
if writes appear in several places, the drift becomes real.

**`attributes` JSONB can become a junk drawer.** It is schema-less by design, so
extraction can record slots it finds without a migration per claim shape. The
failure mode is obvious: inconsistent keys that nothing can query. Phase 3 owes
it a Pydantic model per `MemoryKind`, validated on write.

**`independent_source_count` is not yet defined.** The schema has the column and
the confidence story leans on it, but "independent" is undecided — different
transcript, different day, or different surface? The choice materially changes
how fast a belief is promoted. Phase 3 must pin it down and defend it; today it
is a column with a plausible name, which is not the same as a mechanism.

**The offline default showcases the system at its worst.** The keyless provider
guarantees a reviewer is never blocked, but a character-n-gram hashing embedder
approximates lexical similarity, not semantics — precisely the capability the
product is about. So the default configuration will underperform on exactly the
retrieval cases that matter most. The resolution is not to hide it: RUN.md will
present the hosted path as the intended experience and offline as the guaranteed
fallback, and the evaluation will report the two separately. Publishing offline
numbers as "the system's quality" would be the dishonest option.

**`memory.statement` is nullable.** Required so `purge` can erase the text while
keeping the audit row, but it means every reader must handle null. Accepted, and
worth a helper rather than scattered `or ""`.

**Near-duplicate memories are not prevented by a constraint.** Deduplication is
behavioural (vector + lexical near-match at admission), because genuine
duplicates are rarely string-equal. If that logic is weak, the database will
happily store five phrasings of one preference and inflate apparent
corroboration. This is the most likely Phase 3 quality bug, and R3 in the
requirements matrix exists to catch it.

**20 tables is a lot.** Each has a justification and seven candidates were
rejected outright, but the count is at the edge of what is defensible for a
narrow submission. `db_size_sample` is the most marginal survivor — it could
have been a JSONB blob on `eval_run`.

## 4. Complexity that was refused

No queue or worker. No separate vector store. No entity-resolution subsystem. No
settings centre. No `memory_conflict` table. No `transcript_segment` table. No
caching layer. No multi-tenancy beyond a `user_id` column. Each would have been
defensible at a larger scale; none is needed at 500 records, and every one would
have added a step to the reviewer's setup path.

## 5. Does it foreclose the product position?

Checked deliberately, since this is the risk of building before Part One exists:

- No table encodes a use case.
- `MemoryKind` is one enum in one file; narrowing to one or two kinds is a migration, not a redesign.
- `tool_call` stores `tool_name` + JSONB args, so it does not know what tools exist.
- Nothing in retrieval assumes a domain — filters are status, validity, confidence, kind, surface, time.
- Consent (D7) is unresolved but supported both ways: `proposed` status is a review queue if the product wants one and invisible if it does not.

The one place a position leaked in is **D5** — no dictation-time retrieval path
exists. That follows the assignment's own observation that semantic memory "may
have little reason to affect ordinary dictation", but it is an assumption, and
if Part One disagrees a new path is needed. It is recorded as a decision rather
than buried as an omission.

## 6. Ready for Phase 3?

Yes, with three things owed immediately: a precise definition of independent
sources, typed `attributes` per memory kind, and a single writer for
`decision_event`. The Compose verification is owed before submission but does
not block Phase 3.
