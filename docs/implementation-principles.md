# Implementation principles

> **What this document is.** The brief specifies this file as a summary of the
> engineering implications of the candidate's *own* product position. That
> position does not exist in the repository yet, and the assignment forbids AI
> from writing it. So this file does the next honest thing: it derives
> engineering principles from the **assignment's** stated constraints and from
> the shape of the data it supplies. It invents no product strategy. When
> `PRODUCT_POSITION.md` lands, this document gains a section, and the open
> decisions in `open-product-decisions.md` get resolved — nothing here is
> expected to be contradicted, because nothing here takes a product position.

---

## P1 — Most dictation is composition, not assertion

The corpus is a person writing *to other people*. "Hi Sarah, the deadline is
Tuesday" dictated into Slack is a claim addressed to Sarah; it is not evidence
about the user's preferences, habits or beliefs.

**Engineering implication.** Admission must separate *what the user said* from
*what the user believes*, as a first-class rejection reason
(`dictation_content_not_assertion`), not as a prompt aside. A pipeline that
embeds every transcript and calls it memory commits this error on nearly every
record, and does so invisibly.

## P2 — Remembering everything is the failure, not the goal

The assignment values a narrow set of convincing capabilities and asks
explicitly what the system *deliberately ignores*.

**Engineering implication.** Ignoring must be **observable**: a cheap pre-LLM
salience gate that drops records before any model call, plus a persisted
`memory_candidate` row for every proposal including the rejected ones. Absence
of a memory is not evidence of a decision; a stored rejection is.

## P3 — Evidence is the unit of truth, not model confidence

A belief's strength comes from how many independent interactions support it, how
explicitly they state it, and whether the raw ASR corroborates the formatted
text — not from a number a model emitted about its own output.

**Engineering implication.** `confidence` is computed by code (ADR-0002).
`independent_source_count` is tracked separately from `evidence_count`, because
saying something three times in one dictation is emphasis, while saying it in
three dictations is corroboration. Re-import must be idempotent, or a duplicate
import manufactures corroboration out of nothing.

## P4 — A belief that cannot be traced cannot be trusted

The assignment asks which memories and source interactions produced each answer,
and whether answers are supported by the original interactions.

**Engineering implication.** Provenance is a chain of rows, not a text field:
answer → memory → version → evidence span → transcript → raw ASR. Evidence
records contradicting observations too; keeping only supporting evidence would
leave the system unable to explain its own doubt.

## P5 — Refusing is a feature, and it must be decided before generation

"Whether it refuses to invent an answer when the history does not contain one"
is an explicit evaluation criterion.

**Engineering implication.** Abstention is a **deterministic gate over
retrieval state**, run before the model is asked to write anything, with a typed
reason code. Asking a model to be humble is not a mechanism. A second gate after
generation downgrades any answer whose claims carry no citation.

## P6 — Memory changes, so the schema is temporal from the start

Corrections, contradictions, staleness and deletion are named in the brief.

**Engineering implication.** Append-only `memory_version`; `valid_from`/`valid_to`
distinct from `created_at`; `volatility` so staleness is a property of the
claim's kind; supersession as a typed edge. Retrofitting time onto a mutable row
is a rewrite, so it is present in migration 0001.

## P7 — The person overrides the system, without administering it

"How the person remains in control without becoming the administrator of the
system."

**Engineering implication.** Control attaches to individual memories
(`user_pinned`, `user_hidden`, retract, purge), not to a settings centre. Purge
actually erases the statement and its embeddings while keeping an audit row —
"forget" must mean forget, or the control is theatre. *How* control is offered
in the interface is product-gated (D6, D7).

## P8 — The reviewer cannot ask questions

A coding agent clones the commit, follows RUN.md, and will not infer missing
setup or repair the application.

**Engineering implication.** No undocumented step may exist. Every environment
variable is declared on one settings class and mirrored in `.env.example`.
Reset must be a command, and it must be tested — which is why the
downgrade/upgrade cycle was verified in Phase 2 rather than at the end. The
system must run with **no API key at all**, or a credential mismatch becomes a
failed review.

## P9 — Failures stay visible

"A compelling interface cannot rescue memory that is careless with the truth."

**Engineering implication.** Partial imports report `partial` with counts, never
success. Extraction failures are recorded, never defaulted to something
plausible. Degraded (keyless) operation is labelled in the UI and in the
evaluation. The evaluation carries a `known_failure` flag so the suite reports
honestly instead of being trimmed to whatever passes.

## P10 — Explain, but do not expose reasoning

The brief requires inspectable decisions and forbids exposing chain-of-thought.

**Engineering implication.** Enum code plus a short note (<120 chars, asserted
by test). Engineers read the same record the user's "why?" panel reads, at
different depth — there is no hidden second story.
