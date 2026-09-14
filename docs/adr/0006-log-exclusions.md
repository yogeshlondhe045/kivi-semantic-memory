# ADR-0006: Persist retrieval candidates that were dropped, with the reason

**Status:** accepted · **Date:** 2026-09-14

## Context

The assignment asks how an engineer can inspect why memory did **or did not**
affect a result. Systems normally log the context they used. That explains a
good answer and is useless for the more common question: *Kivi knew this — why
didn't it say so?*

## Decision

Every candidate retrieval considered is persisted in `retrieval_hit` with its
score, rank, strategy, `included_in_context`, and — when excluded — an
`exclusion_reason` code: below threshold, below confidence floor, not active,
user-hidden, outside validity window, outside requested time range, wrong
surface, wrong kind, superseded, lost contradiction, rank cutoff, redundant.

## Consequences

**Good.** A miss becomes a single query. It also makes the retrieval pipeline
falsifiable: if an arm never contributes a surviving hit across the corpus, the
evaluation shows it and the arm should be deleted rather than kept for
appearances.

**Bad.** Roughly 40 rows per request. At 500 records and a few hundred eval
requests this is trivial; at production scale it would need sampling or a
retention window. Noted rather than solved, because solving it now would be
premature.
