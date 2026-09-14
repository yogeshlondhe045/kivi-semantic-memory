# 1. Memory is a ledger, not a profile

**Status:** accepted · **Date:** 2026-09-14

## Context

Every memory feature I looked at converges on the same shape: accumulate a
picture of the user, show it back as a list of traits, ask to be trusted. The
lines have no sources. You cannot check any of them.

Speech makes that worse rather than better. Typing is edited; speech is not.
People dictate while irritated, while walking out of a hard conversation, while
talking about a colleague who is unwell.

## Decision

Kivi records claims the person made, keeps the sentence that supports each one,
and can produce that sentence on demand. It does not infer traits from patterns.

Concretely: no memory row may exist without at least one `memory_evidence` row
quoting a dictation. The evaluation asserts this over the whole corpus.

## Consequences

Ruled out: mood detection, sentiment, predictive suggestion, any "here's what
we've noticed about you" surface. These make better demos.

Ruled in: a Memory screen where every line opens onto the recording it came
from, and a delete that means something.
