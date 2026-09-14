# ADR-0005: Every decision is stored as an enum code, not as prose

**Status:** accepted · **Date:** 2026-09-14

## Context

The system must explain itself: why a memory exists, why one was rejected, why
one was not retrieved, why it abstained. The tempting implementation stores the
model's explanation as text.

Free-text reasons cannot be counted. An evaluation over them degenerates into
string matching or a second LLM call to grade the first — both unreliable, and
the latter non-reproducible.

## Decision

Nineteen Postgres ENUM types covering admission, evidence relations, link
types, exclusion reasons, abstention reasons, outcomes and lifecycle. Every
decision writes a code. A short `note` column carries one engineer-readable
sentence beside it.

Notes are concise structured justifications ("Restated 6 days later on a
different surface"), **never** chain-of-thought. A test asserts they stay under
120 characters.

## Consequences

**Good.** Evaluation metrics become `GROUP BY reason_code` — rejection
precision, abstention correctness and conflict handling are all countable. The
enum set is also a design review artifact: reading the rejection codes tells you
exactly what the system claims to ignore.

**Bad.** A new reason requires a migration. That friction is deliberate — it
forces the question of whether the taxonomy is right rather than letting reasons
proliferate as prose.
