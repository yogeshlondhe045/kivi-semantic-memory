# Requirements matrix

Each row traces a requirement to the behaviour it demands, the schema or code
that carries it, and how it will be measured. Sources are the assignment PDF
and the operating brief.

Rows marked **⊘ product-gated** depend on `PRODUCT_POSITION.md` /
`PRODUCT_VISION.md` and are parked in `open-product-decisions.md`.

| # | Principle | User need | Required behaviour | Technical implication | Evaluation implication |
|---|---|---|---|---|---|
| R1 | Selective memory | Not to be surveilled by their own notes | Most dictations produce no memory | Salience gate before any LLM call; `memory_candidate` row per proposal | Rejection precision/recall; count of records producing zero memories; LLM calls avoided |
| R2 | Composition ≠ assertion | Kivi not to "learn" things said to other people | Content dictated *for* a recipient is not a belief | `dictation_content_not_assertion` admission code | Dedicated corpus slice of recipient-directed dictations; false-admission rate |
| R3 | Evidence-weighted belief | Confidence they can rely on | Repetition across interactions strengthens; one mention does not | `independent_source_count`; deterministic confidence; `proposed` status | Precision at each confidence band; promotion correctness |
| R4 | Idempotent ingestion | Reimport not to corrupt state | Re-running import changes nothing | `uq_transcript_external`, `content_hash` | Import twice; assert memory/evidence counts identical |
| R5 | ASR noise resistance | Not to be misquoted by a transcription error | Divergence lowers confidence | `transcript.asr_agreement` from raw vs. formatted | Corpus slice with injected ASR errors; false-memory rate |
| R6 | Memory updates | Correcting Kivi once to be enough | Later statements supersede earlier ones | `memory_version`, `memory_link(supersedes)` | Update cases: the old belief must stop being retrieved |
| R7 | Conflict handling | Not to be told a confident wrong thing | Contradictions surfaced, not silently resolved | `memory_link(contradicts)`, `conflicting_evidence` abstention | Contradiction cases; assert clarification, not a coin flip |
| R8 | Temporal validity | Stale facts not presented as current | Time-bounded beliefs expire | `valid_from`/`valid_to`, `volatility`, expiry sweep | Stale cases: assert `stale_only` or an updated answer |
| R9 | Multi-source reasoning | Answers from facts spread across dictations | Combine evidence from several transcripts | Fusion retrieval; `derived_from` links | Multi-source cases; evidence coverage ≥2 transcripts |
| R10 | Provenance | To see why Kivi said something | Every answer traces to source interactions | evidence chain; `cited_memory_ids` | Provenance cases: assert the exact transcripts behind an answer |
| R11 | Abstention | To be told "I don't know" | Refuse when history lacks the answer | Deterministic sufficiency gate; citation check | Negative cases; false-answer rate must be ~0 |
| R12 | Retrieval inspectability | (engineer) to debug a miss | Why memory was *not* used is recorded | `retrieval_hit.exclusion_reason` | Per-arm contribution; unused arms must be deleted |
| R13 | Grounded tool use | Tools acting on real state | Tools execute; results are not narrated | `tool_call` with verbatim args/results | Assert state changed, not just that text claimed it |
| R14 | Honest failure | Not to be misled by a broken run | Partial/failed states surface | `JobStatus.PARTIAL`, `extraction_failed`, `degraded` | Fault injection: assert the failure is visible |
| R15 | User control | To correct or delete without admin work | Per-memory correct/hide/retract/purge | flags + tombstone versions; purge drops embeddings | Removal cases: assert the memory stops being retrieved |
| R16 | No chain-of-thought | Explanation without exposure | Short structured reasons only | enum + `note` | Test asserts note length <120 chars |
| R17 | Reviewer reproducibility | (reviewer) one clean path | Clone → run → import → inspect → evaluate → reset | Compose primary; `.env.example`; verified reset | Phase 9 fresh-install checklist |
| R18 | Keyless operation | (reviewer) not blocked by credentials | Full pipeline runs with no API key | `offline` chat + embedding providers | Evaluation runs offline and hosted, reported separately |
| R19 | Cost & latency visibility | (reviewer) to judge practicality | Usage, latency, growth measured | `ModelUsage` chokepoint; stage latencies; `db_size_sample` | Reported per run, not asserted |
| R20 | Corpus reproducibility | (reviewer) to regenerate the data | Seeded generation + documented import | `KIVI_SEED`; generator + importer scripts | Same seed → identical corpus |
| R21 ⊘ | Chosen use cases | The 2–3 things worth doing | — | Tool registry, primary UI screen | Which cases are headline |
| R22 ⊘ | Memory kinds in play | — | Possibly narrower than three | `MemoryKind` enum | Per-kind metrics |
| R23 ⊘ | Never-remember categories | Confidence in what is off-limits | Refuse whole categories | salience gate exclusion list | Assert zero admissions from that slice |
| R24 ⊘ | Dictation vs. Hey Kivi boundary | Coherent modes | Default: memory affects Hey Kivi only | no dictation-time retrieval path exists | Assert ordinary dictation is unaffected |
| R25 ⊘ | Consent model | Trust | opt-in vs. automatic-with-undo | default `status` on admission | Whether `proposed` is user-visible |

## Coverage check

R1–R20 are position-independent and are being built now. R21–R25 are blocked.
Every row R1–R20 has both a schema element (all present in migration 0001) and a
planned measurement, so no requirement is carried as prose alone.
