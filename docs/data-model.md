# Data model

20 tables, implemented in `api/kivi/db/models.py` and migrated by
`api/migrations/versions/0001_initial_schema.py`. Verified against a live
Postgres 16 + pgvector 0.6 (see §5).

A memory here is **not** "text plus an embedding". It is a belief with a
lifecycle, an evidence base, a validity window and a version history — because
the assignment asks the system to change, contradict, expire, remove, retrieve
and explain memories, and none of those are possible over a text blob.

---

## 1. Tables, and why each exists

### Input

| Table | Reason to exist |
|---|---|
| `app_user` | The subject. Multi-tenancy is out of scope, but every table carries `user_id` so scoping stays a `WHERE` clause rather than a migration. |
| `transcript` | One dictation. Stores **both** `raw_asr` and `formatted_text` — they carry different information, and their divergence (`asr_agreement`) is the only available check on the formatter. `metadata_json` is a deliberate passthrough for the reviewer's log fields, whose names this system cannot know in advance. |
| `transcript_embedding` | Episodic recall over raw history, where no memory was created and none should have been ("the dictation I did around 5 PM yesterday in Slack"). |
| `ingestion_job` | So a partly failed import reports `partial` with counts instead of success with quietly missing records. |
| `ingestion_item` | Per-record status and per-stage latency — lets ingestion latency be reported as a distribution, not one averaged number. |

### Memory core

| Table | Reason to exist |
|---|---|
| `memory` | The belief. `status` + `confidence` let it be held without being acted on; `valid_from`/`valid_to` let it be true only for a while; `volatility` makes staleness a property of the claim's kind; `independent_source_count` distinguishes repetition from emphasis; `user_pinned`/`user_hidden` let the person override the system. |
| `memory_version` | Append-only history. Nothing mutates a memory without writing one, so "what did Kivi used to think, and what changed it?" is a query. |
| `memory_evidence` | The provenance backbone: belief ← span of a real transcript. A join table, not a column, because a derived belief must not lose the evidence behind it — and because it carries **contradicting** evidence too (`relation`), without which the system could not explain why it distrusts something. |
| `memory_link` | Typed edges between beliefs: supersedes, contradicts, duplicates, refines, derived_from. |
| `memory_embedding` | Separate from `memory` so re-embedding with a new model neither touches the belief row nor bumps `updated_at` (which the UI reads as "when Kivi last learned something"), and so a memory whose embedding failed is still served lexically. |
| `memory_candidate` | **Every** extraction proposal, admitted or not. This is what makes rejection a first-class inspectable outcome rather than an absence — without it, "what did Kivi deliberately ignore?" could only be answered by diffing transcripts against memories and guessing. |

### Hey Kivi and observability

| Table | Reason to exist |
|---|---|
| `hey_kivi_request` | One turn end to end. Latency is split by stage because "end-to-end latency" alone cannot tell a reviewer whether the cost is retrieval or generation, and the assignment asks for retrieval latency separately. |
| `retrieval_event` | One row per *stage*, so each arm's contribution is measurable. An arm that never contributes a surviving hit should be deleted, and this is how that becomes visible. |
| `retrieval_hit` | Every candidate considered — kept or dropped — with `included_in_context` and `exclusion_reason`. The record that answers "why did memory *not* affect this result". |
| `tool_call` | Arguments and results stored verbatim, so a reviewer can confirm a tool ran rather than being narrated by the model. |
| `decision_event` | The single append-only audit timeline. Deliberately denormalized: the typed tables are the source of truth, this is the read-optimized stream powering both the product's "why did you say that?" panel and the evaluation report, neither of which should union five tables to render a chronology. `detail_table`/`detail_id` point back at the authoritative row. |

### Evaluation

| Table | Reason to exist |
|---|---|
| `eval_case` | Cases live in the DB (not only on disk) so a result can foreign-key to the exact case *revision* it was graded against. `known_failure` lets the suite report honestly instead of being trimmed to whatever passes. |
| `eval_run` | The configuration a run executed under — provider, seed, thresholds — so results are interpretable months later. |
| `eval_result` | `request_id` is the join that makes a failure diagnosable from stored state without re-running anything. |
| `db_size_sample` | Database growth needs a before and an after, so it is sampled at named stages rather than computed at the end. |

---

## 2. Considered and rejected

Explicitly *not* built, because every table must earn its place:

| Rejected | Why |
|---|---|
| `memory_conflict` | A conflict is `memory_link(contradicts)` plus the endpoints' statuses. A dedicated table would duplicate the endpoints and let the two representations disagree. |
| `memory_source` | That is `memory_evidence`. Two tables for one relationship. |
| `entity` / `entity_alias` | `subject_key` plus an aliases array in `attributes` is sufficient at 500 records. An entity-resolution subsystem is a project of its own and nothing here needs it yet. |
| `transcript_segment` | Character offsets into `formatted_text` locate evidence precisely without a second table to keep in sync. |
| `user_settings` | The product is not a settings centre. Control is exercised on individual memories (`user_pinned`, `user_hidden`, retract, purge). |
| `memory_access_log` | `retrieval_hit` already records every touch, with more detail. |
| A separate `abstention` table | An abstention is a property of a request, not an entity: `outcome` + `abstention_reason` on `hey_kivi_request`. |

---

## 3. Enums are the contract

Nineteen Postgres ENUM types. Every decision the pipeline makes is stored as a
code, not as prose, for one concrete reason: **the Phase 7 evaluation measures
behaviour by counting codes** rather than string-matching whatever a model
happened to write. Model prose goes in a short `note` column beside the code.

The rejection codes are where the product's "what it deliberately ignores"
lives, and they are worth reading as a set:

```
dictation_content_not_assertion   composed for a recipient, not a belief held
not_about_user                    true of someone else
transient_context                 true today, meaningless next week
low_specificity                   too vague to ever retrieve usefully
sensitive_excluded                category the product refuses to learn
not_salient                       dropped by the pre-LLM gate, no model call made
asr_unreliable                    raw and formatted disagree too much to trust
extraction_failed                 model output never validated; recorded, not guessed
```

Notes are one short engineer-readable sentence ("Restated 6 days later on a
different surface"). They are **never** model chain-of-thought — a test asserts
they stay under 120 characters.

---

## 4. Indexes

| Index | Serves |
|---|---|
| `ix_memory_embedding_hnsw`, `ix_transcript_embedding_hnsw` | HNSW / cosine ANN. HNSW rather than IVFFlat because it needs no training pass over an already-populated table — a reviewer's first ingestion is indexed correctly instead of degrading until someone remembers to `REINDEX`. |
| `ix_memory_tsv`, `ix_transcript_tsv` | GIN over **database-generated** tsvector columns, so lexical retrieval never depends on the application remembering to populate a column. |
| `ix_memory_user_status_kind`, `ix_memory_subject`, `ix_memory_validity` | The retrieval filter predicates. |
| `ix_transcript_user_occurred`, `ix_transcript_surface` | Episodic time/surface queries. |
| `ix_hit_request_included`, `ix_candidate_decision` | The inspection queries a reviewer will actually run. |

Integrity worth noting:

- `uq_transcript_external` — re-importing a corpus cannot inflate evidence counts, and therefore cannot manufacture corroboration out of nothing.
- `ck_hit_exactly_one_target` — a retrieval hit points at a memory or a transcript, never both.
- `ck_memory_conf` — confidence stays in [0,1].
- `ck_memory_link_self` — a memory cannot contradict itself.
- `fk_memory_current_version` — added post-hoc (`use_alter`) because `memory` ↔ `memory_version` is a mutual reference; enforced by the database rather than by application convention.

---

## 5. Verification performed

Against a live Postgres 16.13 + pgvector 0.6.0:

- `alembic upgrade head` from an empty database → 20 tables, 19 enums, 32 foreign keys, 3 check constraints, 2 HNSW indexes, 2 GIN indexes.
- `alembic downgrade base` → **0 tables, 0 leftover enum types**, then `upgrade head` again → 20 tables. (Native ENUMs outlive their tables, so the migration drops them explicitly; without that the reset path would break on the second cycle.)
- `api/tests/test_schema.py` — 10 passing tests that build a real provenance chain and traverse it to raw ASR, supersede a belief by correction without losing the original, record a dropped retrieval candidate with its reason, persist a rejected candidate pointing at the exact declined words, and confirm each constraint above actually rejects bad data.

Not yet verified: the Docker Compose path — Docker Hub's blob CDN is blocked in
the build environment, so no image could be pulled. `docker compose config`
validates. This is recorded as the top reproducibility risk in
`phase-2-design-review.md`.
