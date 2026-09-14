# The memory model

What a memory is, how one is created, changed and removed, and what is refused
outright.

## What counts as a memory

Four types. Each row carries provenance, confidence and a status; none of them
can exist without at least one quoted sentence from a dictation.

| Type | What it is | Example | Reaches ordinary dictation? |
| --- | --- | --- | --- |
| `entity` | A name the person uses, plus the spellings the recogniser produces for it | `"Meridian" is a name they use` · variants `meridien`, `merid ian` | **Yes** — this is the only one |
| `fact` | A durable, checkable statement about their working world | `Rahul is the PM on Meridian` | No |
| `preference` | How they have said they want writing or work done | `Prefers Slack messages under four lines` | No |
| `commitment` | Something they said out loud they would do, with a deadline where one was spoken | `Promised Priya a written summary of the Kestrel incident before Thursday` | No |

Plus one thing that is not a memory:

| `episode` | A cheap per-dictation summary, always written | The floor of retrieval: even when nothing was worth promoting, *what happened* stays answerable |

The dictation/Hey Kivi boundary the task asks about falls out of that table.
Semantic memory has almost no business changing ordinary dictation — the one
exception is correcting a name the person has already used, which is verifiable
and reversible. Everything else lives in Hey Kivi, where the person has asked a
question and expects an answer that draws on their history.

## Identity: what makes two candidates "the same memory"

`dedupe_key` decides whether a new candidate reinforces an existing memory,
replaces it, or stands alone.

- `entity` — the canonical name. Seeing "Meridian" again, however it was
  spelled this time, is always more evidence for the same name.
- `fact` — **the slot, not the person.** `Rahul is the PM on Meridian` and
  `Priya is the PM on Meridian` share the key `fact::pm on meridian`, because
  "who is the PM on Meridian" has one answer at a time. Keying on the person
  instead would leave two contradictory beliefs sitting happily side by side.
- `preference` — the thing being expressed a preference about.
- `commitment` — the whole statement. Two promises about the same document are
  two promises, not one changing its mind.

## Lifecycle

```
            ┌──────────────── said again ─────────────────┐
            ▼                                             │
   candidate ──▶ POLICY GATE ──▶ created ──▶ active ──────┘
                      │              │
                      │              └──▶ pending_confirmation ──▶ (asked) ──▶ active
                      │                                                    └──▶ forgotten
                      ▼
                 rejection                contradicted ──▶ superseded (kept, linked)
              (reason recorded)
                                          user forgets ──▶ deleted + tombstone
                                                              + evidence suppressed
```

**Created.** A candidate that passes the gate is written with the sentence that
justified it.

**Reinforced.** Repetition is the strongest available evidence that something is
durable. Confidence rises asymptotically — `c + (1 − c) × 0.35` — so it
approaches certainty without ever reaching it, and never from a single source.
Each mention appends another evidence row.

**Superseded.** A different claim on the same key replaces the old one. The old
memory is *kept*, marked `superseded`, and linked from the new one. A superseded
memory is history, not a mistake to hide; the interface has a "show what
changed" view built on exactly this. An older dictation arriving late never
overwrites a newer belief.

**Held for confirmation.** Below 0.68 confidence, a memory is stored but not
used, and Kivi asks — at most four open questions at once, one card at a time.
Entities are exempt: a spelling is low-stakes, visible, and undone with one
click, and being asked twenty questions is its own kind of untrustworthy.

**Expired.** Commitments stop being live a week after their deadline, or a month
after they were made if no deadline was spoken.

**Forgotten.** Deleting a memory is permanent and does three things: it writes a
tombstone so the same claim cannot be relearned, it removes any lexicon entry it
fed, and it suppresses the dictations that supported it from being used as
grounds for a future answer. The recordings themselves stay in *Dictations* and
stay findable — the person's words are theirs. What is deleted is Kivi's belief.

## The ignore policy

The gate sits between *any* extractor and the database, and it is code, not
prompt text. A model asked nicely not to record someone's health will eventually
do it anyway; a gate enforces the boundary through a prompt change, a provider
swap, or a hostile input.

Every refusal is persisted with its reason, so "Kivi ignored the right things" is
a query rather than a claim — and a product surface (*Never kept*) rather than a
debug page.

| Reason | What it catches |
| --- | --- |
| `sensitive_category` | Health, politics, religion, sexuality and relationships, personal finances, another person's private circumstances, credentials |
| `transient` | Moods, traffic, a flaky connection, "keep this between us" — true now, misleading in a week |
| `trivial` | "Okay", "testing one two", acknowledgements |
| `low_confidence` | Below the per-type floor (0.45 entity → 0.55 preference and commitment) |
| `tombstoned` | Previously deleted by the person; re-hearing it does not bring it back |
| `malformed` | Nothing coherent to record |

A whole dictation that hits a sensitive category is withheld before extraction
runs. The transcript is still stored and still searchable — Kivi never refuses
to keep the person's own words — but it produces no memories, and its episode is
excluded from recall so it cannot leak back in as an answer.

## Schema

Five migrations in `packages/server/src/db/migrations/`.

| Table | Holds |
| --- | --- |
| `users`, `settings` | The account. Single-user product; the column exists so an imported corpus stays separate. |
| `dictations` | Raw ASR, formatted output, app, device, language, confidence, timing. De-duplicated on a content hash. |
| `ingest_batches` | One row per seed or import, with before/after database size. |
| `memories` | The four types above, with `dedupe_key`, `confidence`, `status`, `superseded_by`, `expires_at`, embedding. |
| `memory_evidence` | **Provenance.** One row per create / reinforce / contradiction, quoting the span that justified it. |
| `episodes` | One per dictation: summary, topics, entities, salience, embedding. |
| `rejections` | Everything refused, with reason code and explanation. |
| `lexicon` | Canonical spellings and observed ASR variants. The dictation channel. |
| `review_items` | Open questions for the person, capped at four. |
| `tombstones`, `suppressed_evidence` | Forgetting that survives re-ingestion. |
| `conversations`, `turns` | Hey Kivi history with citations. |
| `traces` | Why every ingestion and every answer decided as it did. |
| `db_growth_samples` | Measured growth, so the System page reports rather than estimates. |
| `*_fts` | FTS5 indexes over dictations, memories and episodes, kept current by triggers. |

## What this model refuses to be

No trait inference. No sentiment or affect. No prediction of what the person
will want. No cross-user learning. No memory without a quotable source.

Each of those would make a better demo. Each would make a worse thing to speak
into every day.
