# ADR-0007: One Postgres for vectors, text, state and audit

**Status:** accepted · **Date:** 2026-09-14

## Context

The reflexive architecture pairs a relational database with a dedicated vector
store, and often adds a queue for ingestion.

## Decision

Postgres 16 + pgvector, and nothing else. Vectors (HNSW), full-text
(tsvector/GIN), relational state and the audit trail in one database. Ingestion
is a batched synchronous job with a progress table (`ingestion_job` /
`ingestion_item`) rather than a worker and a broker.

## Consequences

**Good.** A memory and its embedding are written in one transaction, so they
cannot drift apart — the failure mode of a split store, and one that produces
exactly the "careless with the truth" behaviour the assignment warns about.
Setup is one service. A reviewer inspects everything with one `psql`. Retrieval
can filter on validity window, status and confidence *in the same query* as the
ANN search, which a separate vector store makes awkward.

**Bad.** pgvector at very large scale is slower than a specialised index, and a
synchronous pipeline will not survive past roughly 10⁴ records. The corpus is
500. Both limits are documented rather than engineered around.

**Rejected.** A queue (Celery/Redis) — it would add a broker, a worker
lifecycle and a "did the worker die?" failure mode to the reviewer's setup path,
in exchange for concurrency that 500 synchronous records do not need.
