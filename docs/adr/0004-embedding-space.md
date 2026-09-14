# ADR-0004: One 1024-dimension embedding space per deployment

**Status:** accepted · **Date:** 2026-09-14

## Context

pgvector columns are fixed-width and an HNSW index requires a declared
dimension. Providers disagree: 384, 768, 1024, 1536, 3072. Making the column
dimensionless would forfeit the index; a column per provider multiplies the
schema.

## Decision

One embedding space per deployment, fixed at **1024** in the migration. Every
provider adapter must emit exactly 1024 dimensions; the offline embedder does so
natively, and hosted providers either request that width where the API supports
it or are projected by the adapter. `memory_embedding` and
`transcript_embedding` each record `provider`, `model` and `dim` alongside the
vector, and are unique on `(memory_id, model)` — so a future re-embedding is an
insert, not a destructive migration.

Index: HNSW with `vector_cosine_ops`, on L2-normalized vectors.

## Consequences

**Good.** Indexable, simple, and honest about the constraint. Storing the model
name means a mixed-provider database is detectable rather than silently
returning nonsense distances.

**Bad.** Switching embedding provider requires re-embedding the corpus, and
projecting a 3072-d vector to 1024 loses information. Both are acceptable at
this corpus size; the alternative (per-provider columns or an unindexed column)
is worse.

**Why HNSW over IVFFlat.** IVFFlat must be built against populated data to
choose its lists; built on an empty table it degrades silently until someone
remembers to `REINDEX`. A reviewer's first ingestion would hit exactly that.
HNSW has no training step.
