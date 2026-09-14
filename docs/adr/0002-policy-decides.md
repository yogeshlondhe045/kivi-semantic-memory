# ADR-0002: The model proposes, deterministic policy decides

**Status:** accepted · **Date:** 2026-09-14

## Context

The obvious design asks an LLM to extract a memory *and* rate its own
confidence, then stores both. It is fast to build and produces a system whose
behaviour cannot be reproduced, swept, or tested — confidence becomes a number
the model felt like emitting, and "why is this memory active?" has no answer
beyond "the model said so".

The assignment is explicit that the system must not force an answer merely
because a model produced one, and that an engineer must be able to inspect why
memory did or did not affect a result.

## Decision

Split the two. The model **proposes** typed candidates with spans. Code
**decides**:

- admission (create / reinforce / refine / supersede / reject / defer)
- `confidence`, from independent source count, explicitness, `asr_agreement`, volatility-aware decay, contradiction penalty
- promotion `proposed → active`
- conflict resolution
- the pre-generation sufficiency gate that produces abstention

Every threshold is a field on `Settings`, not a literal buried in a function.

## Consequences

**Good.** Behaviour is reproducible at a fixed seed; thresholds can be swept in
evaluation to show a precision/recall curve instead of a single asserted
number; `memory_candidate.admission_score` is stored so a sweep replays from
state without re-running the model. The offline provider becomes viable,
because only the *proposal* step needs a model.

**Bad.** A hand-written policy is less adaptive than a model judge and will have
blind spots the corpus must expose. Tuning is real work.

**Rejected alternative.** LLM-as-judge for admission — non-reproducible, and it
would have made the offline path impossible.
