# 2. The ignore policy is code, not prompt text

**Status:** accepted · **Date:** 2026-09-14

## Context

The obvious place to put "never record health information" is the extraction
prompt. It is also the wrong place. A model asked nicely will comply almost
always, which is a different property from complying.

## Decision

A policy gate sits between *any* extractor and the database. Both providers go
through it. The extraction prompt still asks the model to skip sensitive
material — belt and braces — but the guarantee is the gate.

Every refusal is persisted with its reason code and an explanation.

## Consequences

The guarantee survives a prompt change, a provider swap, a model upgrade, and a
deliberately hostile input.

"What it deliberately ignored" becomes a SQL query and a product surface
(*Never kept*) rather than an assertion in a README.

Cost: the gate is regex-based and will occasionally refuse something harmless.
That is the correct direction to be wrong in.
