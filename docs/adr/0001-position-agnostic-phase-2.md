# ADR-0001: Design Phase 2 without the Part One documents

**Status:** accepted · **Date:** 2026-09-14

## Context

The operating brief states the repository contains `PRODUCT_POSITION.md` and
`PRODUCT_VISION.md` and that they are the source of truth for all product
decisions. They are absent from every branch. The assignment requires Part One
to be the candidate's own thinking and forbids generative AI from producing it.

Three options: write them (forbidden twice over), stop entirely, or build the
position-independent system and park the rest.

## Decision

Build the position-independent system. Record every product-strategic choice in
`open-product-decisions.md` with a provisional default and a named blast radius.
Default to the *least committal* option, and where no default is possible
without asserting a product thesis (use cases, tools, consent model), implement
nothing and say so.

## Consequences

**Good.** Everything the assignment demands regardless of position — ingestion,
admission, provenance, retrieval, abstention, evaluation, import, reset — is
built and verified. The interview is not poisoned by a borrowed position.

**Bad.** The submission is incomplete until Part One lands: no use cases, no
tools, no UI copy. Phases 4 and 5 are substantially gated.

**Mitigation.** Contract-first design. Memory kinds are one enum; tools are a
registry; the schema encodes no use case. Adopting the real position is
expected to be prompts, config and UI — not migrations.
