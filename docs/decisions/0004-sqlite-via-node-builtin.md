# 4. SQLite through Node's built-in module

**Status:** accepted · **Date:** 2026-09-14

## Context

A coding agent clones the submitted commit and follows RUN.md literally. It will
not repair a failed native build. `better-sqlite3` fetches a prebuilt binary
from GitHub releases and falls back to node-gyp; in a restricted network that is
a coin flip, and a failed install means the submission cannot be reviewed at all.

## Decision

Use `node:sqlite`, built into Node 22.5+. Require Node 22 explicitly and check
it in a preflight script that prints the exact remedy.

## Consequences

Zero native dependencies. `npm install` cannot fail on a compiler. FTS5 is
present in the bundled build, which is what makes BM25 free.

Cost: Node 22.5 is a hard floor, and the module still prints an experimental
warning (silenced for npm scripts via `.npmrc`). The interface is small enough
that swapping in `better-sqlite3` behind `db/index.ts` would be an afternoon.
