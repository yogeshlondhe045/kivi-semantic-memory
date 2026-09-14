# 5. A deterministic offline provider alongside the model

**Status:** accepted · **Date:** 2026-09-14

## Context

The declared review path should work with no credentials, and the committed
evaluation results should be something I actually produced rather than numbers I
typed. Both point the same way.

## Decision

`LlmProvider` has two implementations. `anthropic` is first class: strict tool
use, prompt caching, a cheap model for the 500-record ingestion pass and the
main model for conversation, real token and cost accounting. `local` is a
rule-based implementation of the same interface with no network.

Everything around them — storage, the policy gate, retrieval, provenance,
abstention, traces — is identical. The active provider is shown in the app,
stamped into every trace, and recorded in every evaluation result.

## Consequences

`cp .env.example .env && npm run setup && npm start` gives a working product
with no key. The evaluation runs and produces real numbers.

Honesty requirement: nothing may present a rule-based run as a model run. That
is why the provider label appears in the sidebar, the System page, every trace
row and all three evaluation artefacts.

Cost: two code paths for extraction and answer composition. The rule extractor
is narrower than a model — it finds what its patterns describe and nothing else.
The README says so plainly.
