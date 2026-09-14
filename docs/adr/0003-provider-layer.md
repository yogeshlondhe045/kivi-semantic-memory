# ADR-0003: Separate chat and embedding providers; offline by default

**Status:** accepted · **Date:** 2026-09-14

## Context

A reviewing coding agent "may install declared dependencies and provide
documented model credentials" — but will not repair the application. A system
that hard-requires one vendor's key fails completely for a reviewer who has a
different one, or none.

There is also a concrete API fact: **the Claude API has no embeddings
endpoint.** A single `LLMProvider` interface with both `complete()` and
`embed()` cannot be honestly implemented for Anthropic.

## Decision

Two protocols, configured independently: `KIVI_CHAT_PROVIDER` and
`KIVI_EMBEDDING_PROVIDER`. Both default to `offline`.

The offline provider is a real implementation, not a stub: rule-based extraction
and a fixed-seed character-n-gram hashing embedder that emits 1024-d normalized
vectors with no model download and no network. Every call returns `ModelUsage`
with `degraded=True`, which surfaces as a banner in the UI and a column in the
evaluation.

## Consequences

**Good.** A reviewer with no key still gets a complete, deterministic run of
the full pipeline and evaluation. An Anthropic user can pair Claude for chat
with a different embedder without the architecture lying about it. Model usage
and cost are captured at one chokepoint.

**Bad.** Offline retrieval quality is materially worse — hashed n-grams
approximate lexical similarity, not semantics. The README must state this
plainly and the evaluation must report offline and hosted runs separately;
presenting offline numbers as the system's quality would be dishonest.

**Also.** Two providers to keep working, and a dimension-projection concern for
hosted embedders (ADR-0004).
