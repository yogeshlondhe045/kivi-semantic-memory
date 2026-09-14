# 3. Abstention is decided by term coverage, not by rank score

**Status:** accepted · **Date:** 2026-09-14

## Context

The first implementation gated answers on the fused retrieval score. Asked "what
did I decide about the Zurich office lease?", it returned the five
highest-ranked memories in the database with complete confidence. Rank says
"best of what I had". It never says "this answers the question".

## Decision

Three conditions must hold before Hey Kivi speaks: something ranked; the top
result has lexical or semantic support rather than only recency; and **some
single retrieved item contains the rare words of the question**, weighted by
inverse document frequency over this user's own corpus.

Coverage is computed per candidate and maximised, never summed across the
result set — an answer needs one source that contains the question, not five
that between them mention each word once.

A proper noun the corpus has never contained is decisive on its own. An ordinary
word that happens to be absent only lowers the score.

## Consequences

Abstention went from unreliable to 6/6 on the graded cases, including questions
about categories that were refused at ingestion.

The cost is one FTS count per query term, cached. Measured p50 for a Hey Kivi
turn is well under 50 ms on 500 records.
